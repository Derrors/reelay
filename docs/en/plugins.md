# Plugin Development Guide

Reelay's main workflow—monitor → identify → transfer and organize—is a fixed skeleton. All variable capabilities attach to three kinds of extension points. Add a feature by writing a plugin directory, without changing the core.

Two runnable examples:

- [`examples/plugins/webhook_notify/`](../../examples/plugins/webhook_notify/__init__.py): a webhook notification channel (Provider)
- [`examples/plugins/quality_gate/`](../../examples/plugins/quality_gate/__init__.py): block low-quality resources such as CAM/TS (Hook)

## Directory Structure

Each plugin has its own directory, with plugin code in `__init__.py`. Put supporting files—rules, templates, default data, and so on—in the same directory and read them with `Path(__file__).with_name(...)`. Built-in plugins (`reelay/plugins/`) and external plugins (`data/plugins/`) use the same structure:

```
plugins/
├── organizer/          # Library organization
│   ├── __init__.py     # Plugin code
│   ├── settings.py     # Defaults and validation (built-in plugins)
│   ├── naming.py       # Internal plugin module
│   ├── category.py
│   └── category.yaml   # Two-level category rules
└── my_plugin/
    ├── __init__.py
    └── rules.yaml
```

Settings also belong to the plugin. External plugins declare them through `ui_fields`, including defaults, and `validate_config`; see “Displaying and Validating Configuration in the Console” below. Built-in plugins can also include a `settings.py` that provides `DEFAULTS` and `normalize(section)`. The core calls these by directory when reading and saving configuration, and writes defaults into the configuration so they can be edited in the console.

`*.yaml` files in built-in plugin directories ship with the package through `package-data` in `pyproject.toml`. Add other extensions there if you use them.

## Choosing an Extension Point

| What you want to do | Extension point | Examples |
| --- | --- | --- |
| Provide a different implementation of the same capability | **Provider** (capability slot) | A new message source, notification channel, parser, or cloud drive |
| Insert processing before or after a stage, possibly modifying data or stopping the task | **Hook** | Metadata enrichment, additional filtering, destination selection, organization |
| React to an event without blocking the main workflow | **Event** | Statistics, daily reports, external indexing, auditing |

## Loading

- **Plugin directory**: place the whole directory in `data/plugins/`; Docker deployments mount `deploy/plugins` at `/app/data/plugins`. It is discovered automatically after a restart. A simple single-file plugin can also be placed there as a `*.py` file.
- **Standalone package**: declare an entry point in your package; it loads automatically after `pip install`:

  ```toml
  [project.entry-points."reelay.plugins"]
  my_plugin = "my_package.reelay_plugin"
  ```

External extension import or instantiation failures are recorded on the console's “插件” (Plugins) page without affecting built-in functionality. Extensions run with the Reelay process's privileges; load only code you trust.

## Registration

```python
from reelay.core.registry import register

@register(kind="provider", ptype="notify")   # ptype: source / parser / decision / storage / notify
class MyNotify: ...

@register(kind="hook", stage="after_parse", order=70)
class MyHook: ...

@register(kind="event", event="on_saved")
class MyListener: ...
```

The decorator only records the class. `discover()` instantiates it with a no-argument constructor. Each extension needs a globally unique `name`, used both as the Provider reference and as the `plugins.<name>` configuration key. Its switch is `plugins.<name>.enabled`, and its own settings also live under `plugins.<name>`.

## PipelineContext Throughout the Workflow

Each message has one `PipelineContext` (`reelay/core/contracts.py`), reconstructed from its database snapshot at each stage:

| Attribute | Meaning |
| --- | --- |
| `raw` | Original message: `source`, `msg_id`, `text`, and `urls`, including the actual URLs behind hyperlinks |
| `parsed` | Identification result, `ParsedResource`: title, year, type, seasons and episodes, genre IDs, countries, release information (`release`: subtitles, audio tracks, HDR, source, and more; see `reelay/core/release.py`), share code, and other fields |
| `state` / `decision` | Current state and decision: save / ask / skip |
| `save_dir_alias` / `save_dir_cid` | Destination directory |
| `source_rule` | Source rule merged with `defaults` |
| `ctx.ns(name)` | Extension-private namespace (`meta[name]`), persisted with the task |
| `ctx.log(msg)` | Write to the task trace, visible in the console |

## Hook

```python
class MyHook:
    name = "my_hook"
    stage = "after_parse"   # before_parse / after_parse / before_save / after_save
    order = 70              # Ascending within a stage; built-ins: organizer destination 10 (before_save), organization 20 (after_save), tmdb_enrich 50, share_probe 60

    async def run(self, ctx) -> bool:
        ...
        return True          # False stops processing
```

The effects of returning `False` or raising an exception depend on the stage:

| Stage | Return False | Raise an exception |
| --- | --- | --- |
| `before_parse` / `after_parse` | End the task as skipped | Log the error and continue with subsequent Hooks for failure isolation |
| `before_save` | End the task as skipped | Fail the task and retry from its stage; transfer prechecks must not be silently skipped |
| `after_save` | Move the task to need_manual | Record the organization error and move the task to need_manual |

Do not use the same order as a built-in plugin in the same stage, or execution order will depend on loading order.

When `after_save` runs, the files have already been transferred. If an extension needs optional storage Provider methods, declare their names in `requires_storage`, such as the organizer's `requires_storage = ("library",)`. Saving configuration is rejected if any rule uses a storage Provider without those methods, and the “插件” (Plugins) page identifies unsupported storage Providers. Hooks that only produce supplementary output, such as media-server index files, should catch their own exceptions and return `True` so they do not affect the task outcome.

## Event

```python
class MyListener:
    name = "my_listener"
    event = "on_saved"   # on_message / on_identified / on_rejected / on_saved / on_failed / on_daily

    async def handle(self, ctx) -> None:
        ...
```

- Events are written to the Outbox in the same transaction as task state, then delivered asynchronously: at least once, a 30-second timeout per attempt, backoff retries up to 8 attempts, and generation order within each task. System alerts and recovery notifications continue retrying for up to 24 hours.
- `ctx` is a snapshot from when the event occurred. Changing it does not change the task.
- The core emits `on_daily` once a day at `schedule.daily_hour`. `ctx.meta["system"]` includes the date and a summary of the previous 24 hours, suitable for daily reports and inspections. At startup, emission waits for parsing of downtime-backfilled messages, with a maximum delay of 15 minutes.
- A class can subscribe to only one event. To subscribe to multiple events, write multiple classes with the same `name` so they share one switch; see `reelay/plugins/episode_tracker/`.

## Provider Contracts

| ptype | Required implementation | Optional |
| --- | --- | --- |
| `source` | `async listen(emit)`: a long-running coroutine that calls `await emit(RawMessage(...))` for each message | `configure(targets)`, `bind_watermark(lookup)`, `health()`, `async close()` |
| `parser` | `async parse(ctx) -> ParsedResource` | — |
| `decision` | `async decide(ctx) -> Decision` | — |
| `storage` | `prepare` / `save` / `reconcile` (read-only verification; must never resend a write request) / `abandon` (abandon an uncertain transfer, delete the staging directory, and confirm by reading back) | See below |
| `notify` | `async notify(ctx, event)`; event is identified / pending_approval / saved / transfer_uncertain / organize_failed / failed | `async notice(text, html=False, buttons=None)`: system notifications such as alerts, digests, and daily reports. Channels without `html` support receive text with tags stripped; those without `buttons` support receive only the body |

Optional storage Provider methods, defined by `StorageProvider` in `reelay/core/contracts.py`:

| Method | Purpose |
| --- | --- |
| `discard(ctx)` | Clean up an empty staging directory created by `prepare` but left unused because of deduplication |
| `reconcile_partial(ctx)` | Read-only verification that issues a receipt for files already received, after a user selects “按已到的继续” (Continue with received files) |
| `ready() -> bool` | Whether credentials are ready; treated as ready if this method is absent |
| `dir_path(cid)` | Directory path in the cloud drive, used by the console and approval cards to display the real destination |
| `library()` | Cloud-drive interface for organization: `iterdir` / `get_attr` / `mkdir` / `rename` / `move` / `delete`, with integer IDs |
| `inspect_share(parsed, describe=...)` | Read-only queries for share availability and its seasons/episodes, used by the share-probe plugin |
| `browse(cid, offset, limit)` | List subdirectories for the console's directory picker |

`save` distinguishes outcomes through exceptions. `TransferRejected` means the drive explicitly rejected the request and definitely did not transfer anything, so resending is allowed. `TransferUncertain` means the request may already have executed; all subsequent handling must be verification only. If the drive's response to that request is available, put it in the exception's `response`; the core records it in the task trace for diagnosis. `StorageBusy(reason, until)` means the drive temporarily refuses all requests or deferred this request, and this request did not execute. The task resumes at `until`, without being counted as a failure. `prepare`, `reconcile`, and optional methods also raise `StorageBusy` under the same conditions.

Configuration selects Providers through `providers.source`, `providers.notify`, and the `parser`, `decision`, and `storage` fields in `defaults` / `sources[]`.

The three built-in `decision` Providers build on each other: `default` uses rules only; `subscription`, the default, adds automatic approval for new episodes of subscribed series; `llm` additionally filters by natural-language preferences. To retain subscription auto-approval in a custom decision Provider, inherit `SubscriptionDecision`. When `subscriptions_only` is enabled, available since **v1.5.0**, the core checks active subscriptions first and ignores unmatched resources without calling the decision Provider. Production identification and test previews behave identically. If a decision Provider returns `save` for an LLM-fallback result, the core still changes it to manual review.

## Calling AI

Use `reelay/core/ai.py` when an extension needs AI, sharing client configuration, backoff retries, and call records with built-in features. After inheriting `ai.UsesAI`, `self._client` / `self._model` are resolved on use from the current configuration and secrets. Console changes take effect immediately; `_client` is None when unconfigured. Assign these attributes directly in tests to replace them:

```python
from reelay.core import ai

class MyListener(ai.UsesAI):
    async def handle(self, ctx):
        if self._client is None:
            return                                     # Fall back to rules
        try:
            result, _ = await ai.complete_json(
                self._client, model=self._model, purpose="my_plugin", task_id=ctx.task_id,
                messages=[...], schema={...}, name="my_output", validate=my_validate)
        except ai.AIError:
            return                                     # Refusal, invalid output, or network error: fall back to rules
```

- `validate` must perform strict local validation and raise `ValueError` for invalid output, which is retried once.
- Every request is recorded; `purpose` appears under “统计 → AI 调用” (Statistics → AI calls) in the console.
- Run message text through `ai.scrub()` before sending it to AI to remove links and access codes.
- AI output is a proposal only. Decisions involving writes must pass rule validation or user confirmation.

## Metadata Enrichment Conventions

After identification, the built-in `tmdb_enrich` Hook (`after_parse`, order 50) matches a TMDB entry by title and year and fills in metadata. TMDB's ID system is Reelay's data standard: genre IDs are TMDB genre IDs, `[tmdbid=…]` in names supports Jellyfin / Emby metadata scraping, and posters use TMDB image paths. The core and other extensions read the following fields directly. When adding another data source, such as Douban or Bangumi, map its results to TMDB entries and populate these same fields.

Write to `ctx.parsed`:

| Field | Content | Consumers |
| --- | --- | --- |
| `title` / `year` | Normalized title and release / premiere year | Work identity without a TMDB ID, using normalized title + year (`reelay/core/works.py`) to merge the same work across sources; organization paths; episode tracking |
| `genre_ids` | Integer list of TMDB genre IDs, replacing the AI's values entirely | Two-level classification (`plugins/organizer/category.yaml`, such as 16 = Animation) |
| `origin_country` | Uppercase two-letter country/region codes; keep the AI's values if the source has none, rather than clearing them | Two-level classification |
| `raw_fields["tmdbid"]` | TMDB entry ID, an integer | Work identity, so library deduplication, subscriptions, and episode tracking distinguish titles with the same name; the `tmdbid` naming variable, included as `[tmdbid=…]` by default; aired-episode queries for episode tracking |
| `raw_fields["poster_path"]` | TMDB image path such as `/abc.jpg`, not a full URL | Console posters, combined with `https://image.tmdb.org/t/p/<尺寸>`, where `<尺寸>` is the image size; a text cover is shown if absent |

Write to `ctx.meta["tmdb"]`. Deterministic confidence is calculated after all `after_parse` Hooks and uses these results (`reelay/core/confidence.py`):

| Situation | Write | Effect on confidence |
| --- | --- | --- |
| Successful match | `matched: True`, `tmdbid` | Counts as a passed signal |
| Successful query but no trustworthy match | `matched: False` | Cap at 0.45, mark “TMDB 未匹配” (No TMDB match), and request manual confirmation |
| No query because credentials are not configured | `matched: False`, `note: 原因`, where `原因` is the reason | Neutral; does not affect the score |
| Query error, such as a network error or rate limit | `matched: False`, `error: 异常类型名`, where `异常类型名` is the exception type name | Neutral; does not affect the score |

Other fields, such as `candidates`, `kind`, and `score`, are only for diagnosis in task details; the core does not read them.

Refreshing existing tasks: an `after_parse` Hook may implement `needs_refresh(ctx) -> bool`. At main-process startup, the core asks each Hook about pending approvals and stored tasks, rerunning those that return True without calling the LLM again. Pending approvals adopt the entire new result and recalculate confidence. Stored tasks already have fixed titles, paths, and deduplication keys, so only `poster_path` is filled in, and only when the title is unchanged. Refreshing updates the snapshot without changing the task's update time, leaving approval timeouts and library statistics unaffected. `tmdb_enrich` returns True for tasks identified before its key was configured, which have a `note`, or whose previous query failed, which have an `error`. Configure TMDB and restart to enrich existing tasks with normalized metadata and posters.

There are two ways to add another data source:

- **Supplement**: use an order greater than 50 and query only when TMDB did not match. On a match, write the fields above and replace all of `meta["tmdb"]`; leftover `note` / `error` values would still make the result neutral.
- **Replace**: disable `tmdb_enrich` under `plugins` and have your Hook populate the same fields using the same conventions.

```python
import asyncio

from reelay.core import runtime
from reelay.core.registry import register


@register(kind="hook", stage="after_parse", order=55)
class DoubanFallback:
    name = "douban_fallback"

    async def run(self, ctx) -> bool:
        p = ctx.parsed
        if not p or not p.is_media or ctx.meta.get("tmdb", {}).get("matched"):
            return True
        hit = await asyncio.to_thread(lookup_via_douban, p.title, p.year)  # Your implementation: find the corresponding TMDB entry through Douban
        runtime.observe("douban", "connected")
        if hit:
            p.title, p.year = hit["title"], hit["year"]
            p.raw_fields["tmdbid"] = hit["tmdbid"]
            # Also fill in genre_ids, origin_country, and raw_fields["poster_path"] when available
            ns = ctx.ns("tmdb")
            ns.clear()
            ns.update(matched=True, tmdbid=hit["tmdbid"], source="douban")
        return True
```

If a result cannot be mapped to a TMDB entry, do not write only the source's own ID. Deduplication, classification, naming, and episode tracking all use TMDB entries; treat this case as “no trustworthy match.”

## Reading Configuration and Using Core Services

```python
from reelay.core import runtime

section = runtime.plugin_config("my_plugin")           # This extension's plugins.my_plugin settings, excluding enabled
language = runtime.get("tmdb.language")                # Read system configuration by dotted path
runtime.services().notify("\U0001f4e6 备份完成", key="my_plugin")  # “Backup complete”: durable system notification to all channels
state = runtime.services().state("my_plugin")          # Extension-isolated persistent KV; values must be JSON-serializable
state.set("last_run", "2026-10-19")
runtime.observe("my_api", "failed", "TimeoutError")    # Record an external call outcome: connected / failed
```

`runtime.observe` records the outcome of the latest actual call and does not expire simply because no calls have occurred for a while. Results are available in `runtime` from `GET /api/health`. Built-in LLM parsing and TMDB enrichment use it to populate “AI 解析” (AI parsing) and “TMDB 元数据” (TMDB metadata) under “总览 → 运行状态” (Overview → Runtime status) in the console.

`runtime.get` / `runtime.plugin_config` read the configuration from when the task was received, according to its recorded configuration version. Changes affect new tasks only. Outside task scope, they read the process's currently effective configuration. `services()` is available when the core calls Hooks, Events, notification channels, and parser/decision/storage Provider methods, including inside their `asyncio.to_thread` calls. A source's `listen` is a long-running coroutine outside task scope.

Methods provided by `services()`:

| Method | Purpose |
| --- | --- |
| `notify(text, key=, html=, buttons=)` | Send a durable system notification to all notification channels |
| `state(name)` | Extension-isolated persistent KV: `get` / `set` / `delete` / `items` |
| `storage(name)` | Storage Provider named name, for example to obtain `library()` in the organizer |
| `query(capability, *args)` | Call a capability declared by another extension; see below |
| `subscriptions()` / `finish_subscription(id, record)` | Series subscriptions; mark a subscription as finished once airing is complete and every episode is in the library |
| `ignored_works()` | Titles marked as no longer accepted |
| `stored_series()` | Stored episodes grouped by (work, season): `[{work, name, season, episodes}]`. Query a work's stored episodes for a season with `works.episodes(stored, works.identity(片名, 年份, tmdbid), 季)`, where the placeholders are title, year, and season |
| `task(task_id)` / `task_record(task_id)` | Task snapshot (`PipelineContext`) / task row, including state, error, and update time |
| `title_media_types()` / `titles_since(since)` | Title → media type; titles newly received after a given time |
| `recent_feedback(source=, kinds=, limit=)` | Recent manual decisions—approvals, ignores, corrections—with links and access codes removed from the text |

Do not use direct module imports to couple extensions to one another or the core to individual extensions. An extension declares what it can answer in `provides`, a mapping of capability name → method name. The core and other extensions call it through `services().query(能力名, ...)`, replacing `能力名` with the capability name. A disabled or absent extension returns None, and callers handle the capability as unavailable. Built-in capabilities:

| Capability | Provider | Method and result |
| --- | --- | --- |
| `task_explanation` | Failure explainer | `(task_id) -> dict \| None`: the most recent reason and suggested actions for this task, displayed in task details |
| `episode_report` | Episode tracker | `() -> dict`: latest missing-episode inspection results, used by daily reports and the library and subscriptions pages |
| `bot_action:<动作>` | Episode tracker | `(action, digest, original) -> (正文, 按钮, 提示)`: the plugin's own control Bot buttons; `<动作>` is the action, and the return values are body, buttons, and hint |

```python
class MyReport:
    name = "my_report"
    provides = {"weekly_summary": "summary"}

    def summary(self) -> dict:
        ...

# In another extension
summary = runtime.services().query("weekly_summary") or {}
```

Put synchronous blocking calls, such as HTTP or SDK calls, in `asyncio.to_thread` rather than blocking the event loop.

## Displaying and Validating Configuration in the Console

The example keeps Chinese user-facing labels, as used in the current console: “我的扩展” means My extension, “阈值” Threshold, “模式” Mode, and “严格模式” Strict mode.

```python
class MyHook:
    name = "my_hook"
    ui_label = "我的扩展"
    ui_description = "一句话说明，显示在插件列表"
    ui_fields = [   # Generate a form and validate types on save; paths are relative to plugins.my_hook
        {"path": "threshold", "label": "阈值", "type": "number", "min": 0, "max": 1, "default": 0.5},
        {"path": "mode", "label": "模式", "type": "string", "choices": ["a", "b"]},
        {"path": "strict", "label": "严格模式", "type": "boolean"},
    ]
    default_enabled = False   # Disabled if plugins.my_hook.enabled is unset; suitable for extensions with side effects

    def validate_config(self, cfg: dict) -> None:
        """Called on configuration save and startup when enabled; raise ValueError to reject invalid configuration."""
        section = runtime.plugin_config(self.name, cfg)
```

`ui_description` supplies the one-line description shown in the plugin list. The corresponding configuration file:

```yaml
plugins:
  my_hook:
    enabled: true
    threshold: 0.7
    mode: a
```

`default` is the value the extension actually uses when the field is unset; the form uses it only as a hint. Validate complex configuration, such as arrays and objects, yourself in `validate_config`; users maintain it through the configuration file. Extensions should not read environment variables: declare required endpoints and switches in `ui_fields`, and place required files under `reelay.core.paths.DATA`.

### Plugin Actions

When the console needs data or a computation from a plugin, such as a naming-template preview or built-in rules, the plugin lists ordinary methods in `ui_actions`. The core exposes them through `POST /api/extensions/<插件名>/actions/<动作名>`, where the placeholders are the plugin and action names:

```python
class MyHook:
    ui_actions = ("preview",)

    def preview(self, payload: dict) -> dict:
        """payload is the JSON request object, which may be empty; return a JSON object."""
        if not payload.get("text"):
            raise ValueError("text 不能为空")   # “text must not be empty”; ValueError / TypeError → 422, with the message shown unchanged
        return {"result": payload["text"].upper()}
```

- Only methods listed in `ui_actions` can be called; other plugin methods are inaccessible through this endpoint. Return 404 if the plugin is not installed or the action does not exist.
- The core handles authentication, just as for other management endpoints. Methods can use `runtime.get` to read current configuration.
- Plugins do not need a FastAPI dependency. Methods execute in the Web process's event loop and must be fast; use `async def` for time-consuming work.
- The plugin catalog lists each plugin's `actions`.

Built-in plugins may have dedicated UI under the frontend's `web/src/plugins/<插件名>/`, where `<插件名>` is the plugin name. A settings editor is declared by backend `ui_config_type` and registered in `PLUGIN_EDITORS` in `web/src/plugins/index.ts`. UI used only by that plugin, such as the organizer settings' “预览整理效果” (Preview organization) dialog, also belongs there. Components call `api.extensionAction(插件名, 动作名, payload)` for backend data, using the plugin and action names. The frontend is built and released as a whole; external plugins do not have dedicated editors and use forms generated from `ui_fields`.

## Testing

Load your extension with `discover(plugin_dirs=[...])` and call it directly. See [`tests/test_examples.py`](../../tests/test_examples.py):

```python
registry = discover(plugin_dirs=["path/to/plugins"])
assert registry.load_errors == []
hook = next(h for h in registry.hooks["after_parse"] if h.name == "my_hook")
with runtime.scope({"plugins": {"my_hook": {"threshold": 0.5}}}):
    assert await hook.run(ctx) is True
```
