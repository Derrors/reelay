# Configuration Reference

[Documentation home](README.md) · [User guide](user-guide.md) · [FAQ](faq.md)

If you are unsure which settings to choose, start with [Choose Your Processing Mode](user-guide.md#choose-your-processing-mode). This page is a field reference, not a checklist you need to fill out from top to bottom.

All settings can be changed in the web console and take effect in about 1 second after saving, except for the few marked as requiring a main-process restart. This is the full field reference; look up the relevant section when you need it rather than reading every field for everyday use. The console itself is currently in Chinese, so key UI labels are retained below with English explanations.

- [System configuration](#system-configuration-system): [Directories and sources](#directories-and-sources-save_dirs-sources-providers) · [Identification and decisions](#identification-and-decisions-defaults-sources) · [AI capabilities](#ai-capabilities-ai) · [Release preferences](#release-preferences-release) · [Forwarding, approval reminders, and scheduling](#forwarding-approval-reminders-and-scheduling) · [AI services and TMDB](#ai-services-and-tmdb-llm-tmdb) · [Telegram](#telegram-telegram) · [115 request limits](#115-request-limits-p115) · [Network proxy](#network-proxy-proxy) · [Runtime settings](#runtime-settings-runtime)
- [Plugin configuration](#plugin-configuration-plugins) · [Subscriptions and Ignored Titles](#subscriptions-and-ignored-titles) · [Secrets](#secrets) · [Runtime File Locations](#runtime-file-locations) · [Environment Variables](#environment-variables)

Settings fall into four categories:

| Category | Location | Where to edit |
| --- | --- | --- |
| **System configuration** | `system:` in `data/config.yaml` | The web console's “设置” (Settings) pages |
| **Plugin configuration** | `plugins.<插件名>` in `data/config.yaml`: the `enabled` switch and the plugin's own settings; `<插件名>` is the plugin name | The console's “插件” (Plugins) page, with one card per plugin |
| **Secrets** | `data/secrets.json`, with mode 600 permissions; not included in the configuration file | The console's “设置” (Settings) pages show only whether a secret is set; you can replace or clear it, but cannot read back its value |
| **Fixed, not configurable** | Code constants | No settings provided; see [Runtime File Locations](#runtime-file-locations) |

Each settings page saves separately, writing `data/config.yaml` atomically. You can also edit this file directly; the running Worker adopts valid changes in about 1 second. Saving checks the configuration version loaded by the page. If another page or process changed it in the meantime, the save is rejected; reload before editing again. If the file becomes invalid, Reelay keeps using the last valid version and shows the faulty file and reason on the overview and settings pages. Saving through the console is blocked during this time, so it will not overwrite your file, and resumes once you fix it. See [`config.example.yaml`](../../config.example.yaml) for a complete example.

Every field has a default. The tables below list defaults and their meanings; entries in `sources[]` can override any field in `defaults`.

```yaml
system:
  save_dirs: {电影: "123", 剧集: "456", 默认: "0"}
  defaults: {approval_mode: manual, save_to: 默认}
  sources: [{bot: "@some_bot"}]
  llm: {base_url: "", model: gpt-4o-mini}
  # …other system configuration
plugins:
  organizer: {enabled: true, conflict: manual, naming: {...}}
  daily_report: {enabled: true, narrative: true}
  # …other plugins
```

## System Configuration (`system`)

### Directories and Sources (`save_dirs`, `sources`, `providers`)

| Field | Default | Description |
| --- | --- | --- |
| `save_dirs` | — | Directory alias → 115 directory CID, as a decimal string. At least one is required; `0` is the root directory |
| `sources[].bot` | — | The `@username` or numeric ID to monitor, case-insensitive. It can also be a list, such as `["@a", "@b"]`; all listed sources share the card's settings. Each source may appear in only one card |
| `sources[].*` | Same as `defaults` | Per-source overrides for approval mode, destination directory, parser, filters, preferences, and other settings |
| `providers.source` | `telegram` | Message-source Provider |
| `providers.notify` | `[console]` | List of notification channels; `console` sends to Telegram when a control Bot is configured, otherwise to the log |

### Identification and Decisions (`defaults`, `sources[]`)

| Field | Default | Description |
| --- | --- | --- |
| `approval_mode` | `manual` | Approval mode: `manual` requires your confirmation for every eligible resource; `auto` (“AI 处理”, AI processing) saves directly when identification and rules pass. Uncertain cases, including low confidence, no TMDB match, or LLM fallback, still require manual review |
| `bot_notify` | `true` | Bot notifications, independent of approval mode: send approval cards that can be acted on in Telegram, plus library-addition notifications. When disabled, handle approvals under “待审批” (Pending approvals) in the console. Transfer and organization problem alerts are still sent |
| `subscriptions_only` | `false` | “仅处理订阅” (Subscriptions only): ignore resources that do not match an active subscription instead of sending them to pending approvals. Set the global default under “基础 → 默认处理方式” (Basics → Default processing mode); source cards can override it. An unset source value inherits the global setting |
| `save_to` | `默认` | Default destination-directory alias; `默认` means Default |
| `parser` | `llm` | `llm` or `regex`. `llm` supplies rule-based identification results to AI as reference. If the LLM is unavailable, still fails after timeout retries, or returns invalid output, fall back to regex and force manual confirmation |
| `parse_rules` | None | Per-source Python regular expressions, in multiline mode, for `title`, `year`, `season`, `episodes`, `resolution`, and `size`, using the first capture group. `episodes` combines all matches: use one capture group for a single episode, or named groups `start` and `end` for a range; `end` alone means episode 1 through end. Missing or unmatched fields use the generic rules. “识别规则” (Identification rules) on a source card can read recent channel messages for AI-assisted drafting and test runs |
| `llm_response_format` | `json_schema` | Use `json_object` for compatible APIs without structured-output support; strict local validation still applies |
| `decision` | `subscription` | Decision strategy: `default` uses rules only; `subscription` adds automatic approval for new episodes of subscribed series; `llm` additionally uses the natural-language `preferences`, with recent approvals and ignores from the same source, to decide whether to save. All three decide only whether to proceed; destinations are determined by `save_to` and category rules. Reliably identified, TMDB-matched messages whose episodes are all in the library are skipped directly |
| `preferences` | `""` | For example, “Only Chinese TV series and Japanese anime in 4K or 1080p; no variety shows.” To turn this into deterministic rules, use “用一句话生成规则” (Generate rules from one sentence) in the console |
| `confidence_threshold` | `0.5` | Below this value, request manual review. Confidence is calculated from the presence of title, type, and year and whether TMDB matched, not from the LLM's self-reported score |
| `episodes_from` | `share` | `share`: use filenames in the share and save all episodes not already in the library; `message`: save only episodes stated in the message |
| `filters.must_have` | `[]` | The text or links must contain at least one listed term; otherwise skip without calling the LLM |
| `filters.block_keywords` | `[]` | Skip if the text contains any listed term, without calling the LLM |
| `filters.allow` | `[]` | List of conditions; at least one must match. Empty means unrestricted. Fields are `media_type` (movie/tv/anime), `genre_ids` (TMDB genre IDs), and `origin_country` (two-letter codes). Syntax matches category rules: fields within one condition are ANDed, comma-separated values are ORed |
| `filters.deny` | `[]` | List of conditions; skip if any matches |
| `filters.min_resolution` | Unrestricted | `480p` / `720p` / `1080p` / `2160p`; skip lower resolutions and request manual review if resolution is unknown |
| `forward` | Inherit global | Per-source forwarding override; see below |

After passing ignored-title and filter checks, resources with low confidence, no trustworthy match when TMDB is enabled, or an LLM fallback require manual review regardless of approval mode. `allow`, `deny`, and `min_resolution` evaluate fields after identification and TMDB enrichment. The console's “用一句话生成规则” (Generate rules from one sentence) drafts these rules from a natural-language preference and saves them only after your confirmation.

**Version note**: “仅处理订阅” (Subscriptions only) is supported from **v1.5.0** onward. Earlier versions do not have this setting. See the [User guide](user-guide.md#subscriptions-only) for examples and the [Changelog](../../CHANGELOG.md) for release history.

**Subscriptions only** differs from “规则 + 订阅放行” (Rules + subscription auto-approval): the latter makes new subscribed episodes easier to save automatically but does not exclude unsubscribed titles. `subscriptions_only: true` instead applies a hard filter first, regardless of approval mode or decision strategy.

- The work must be identified through TMDB and match a subscribed season. Resources with no subscription, a canceled or completed subscription, or no confirmed match to an active subscription are ignored directly. If TMDB is not configured or matching fails, they do not enter pending approvals either.
- A subscription set to “仅提醒、不自动放行” (Reminders only, no automatic approval) is still active. After a match, the existing filters, confidence checks, deduplication, and approval rules continue to apply; matching does not mean automatic saving in every case.
- A multi-season collection not narrowed to one season requires an active subscription for every season, or the whole message is ignored. If only one season is confirmed, transfers are limited to that season.
- Reelay still needs to identify the message and perform configured TMDB matching and share probing before it can determine whether the resource is subscribed. Ignored messages retain their task records and reasons but are not forwarded and do not produce identification or approval notifications.
- Omitting the field on a source inherits the global value; explicit `false` overrides global `true`. Changes affect newly received tasks only: they do not clear existing pending approvals or stop transfers already underway. Adding a subscription does not automatically replay previously ignored messages.

### AI Capabilities (`ai`)

| Field | Default | Description |
| --- | --- | --- |
| `ai.examples` | `3` | Number of recent manual corrections from the same source to include as identification prompt examples, from 0–10; 0 disables this |
| `ai.history` | `20` | For `decision: llm`, number of recent manual approvals and ignores from the same source to include, from 0–100; 0 disables this. Ignores apply only to the same title. Ignores due to expired shares or all stated episodes already being in the library are excluded. The AI's decision and reason appear in the task trace |
| `ai.assist` | `true` | When rules cannot determine which share files to save—unrecognized episode or season numbers, multiple files for the same episode, multiple main-feature candidates, or a message with only episode numbers and a multi-season share—ask AI for a proposal first. Validate it with the same rules, and request manual intervention only if it fails |
| `ai.advice` | `true` | Attach a suggested action—approve, ignore, or review—and reason to pending approvals. Rules choose the action; AI supplies only the wording |
| `ai.suggest_auto.min_decisions` | `20` | Minimum manual decisions for a source before assessing whether to suggest “改为 AI 处理” (Switch to AI processing) |
| `ai.suggest_auto.approve_rate` | `0.9` | Approval rate must be at least this value |
| `ai.suggest_auto.max_correct_rate` | `0.1` | Fraction of identification results corrected must be no higher than this value |

Suggestions are advisory only. Changing the approval mode requires your confirmation in the console.

### Release Preferences (`release`)

When multiple versions of an episode or movie exist, both pending-approval recommendations and upgrades (`plugins.organizer.conflict: upgrade`) compare them in this order: preferred subtitles → resolution → HDR → preferred audio → preferred source → size. List order determines priority.

| Field | Default | Description |
| --- | --- | --- |
| `release.prefer_subtitles` | `[chs, cht]` | `chs` Simplified Chinese / `cht` Traditional Chinese / `eng` / `jpn` / `kor`; an empty list ignores subtitles |
| `release.prefer_audio` | `[]` | `mandarin` / `cantonese` / `english` / `japanese` / `korean` |
| `release.hdr` | `any` | `any`: no preference; `prefer`: favor HDR and Dolby Vision; `avoid`: avoid them |
| `release.prefer_sources` | `[]` | `remux` / `bluray` / `web-dl` / `webrip` / `hdtv` / `dvd` |

For recommendations, information missing from the message counts as not meeting a preference. Upgrades delete old files, so their comparison is stricter: if the library file is known to meet a preference but the new file does not state that information, it will not replace the old one.

### Forwarding, Approval Reminders, and Scheduling

| Field | Default | Description |
| --- | --- | --- |
| `forward.enabled` | `false` | Forward successfully identified resources using a template |
| `forward.target` | `""` | Forwarding destination; the control Bot must be able to send messages there |
| `forward.template` | See example | Available fields: `{title}` `{year}` `{resolution}` `{size}` `{link}` |
| `approval.timeout_hours` | `24` | Pending approvals older than this many hours are considered overdue |
| `approval.on_timeout` | `remind` | `remind`: send reminders; `skip`: ignore overdue tasks directly |
| `approval.reminder` | `digest` | `digest`: combine all overdue tasks into one summary; `card`: resend individual confirmation cards |
| `approval.max_reminders` | `3` | Maximum reminders per task. Afterward, the task stays pending but receives no further reminders; `0` means unlimited |
| `schedule.daily_hour` | `9` | Local hour for daily system events, such as reports and episode inspections. At startup, wait for parsing of downtime-backfilled messages first, for up to 15 minutes |

### AI Services and TMDB (`llm`, `tmdb`)

| Field | Default | Description |
| --- | --- | --- |
| `llm.base_url` | Empty | OpenAI-compatible API endpoint; leave empty for the official OpenAI endpoint |
| `llm.model` | `gpt-4o-mini` | Model name. On opening the console, it fetches the API's model list using the saved key, and only listed models can be selected. Direct configuration-file edits are not restricted by this list |
| `llm.reasoning_effort` | Empty | Reasoning effort, sent as `reasoning_effort` with every request: `none` / `minimal` / `low` / `medium` / `high` / `xhigh` / `max`. Empty omits the parameter and lets the model decide. Providers support different values; for example, DeepSeek accepts only `low` / `high` / `xhigh` / `max`. The console detects the current model's supported values and disables the others. If a request is rejected because of this parameter, retry automatically without it rather than failing identification |
| `tmdb.base_url` | Empty | Empty uses `https://api.themoviedb.org/3`. DNS interference with this domain is common on networks in China; configure a [network proxy](#network-proxy-proxy), use the official alternative `https://api.tmdb.org/3`, or use your own reverse proxy |
| `tmdb.language` / `tmdb.timeout` | `zh-CN` / `10` | TMDB matching language and timeout in seconds |

AI API keys and TMDB keys are [secrets](#secrets); changes take effect immediately. Under “设置 → AI 与 TMDB” (Settings → AI and TMDB), you can fetch models from the endpoint currently entered in the form using OpenAI-compatible `GET /models`, and test connectivity with a very short message using the current endpoint, model, and reasoning effort, without saving first.

Saved keys are sent only to saved endpoints. If the endpoint in the form differs from the saved one, you must also enter a key for that endpoint before fetching models or testing. Saving a change to `llm.base_url` or `tmdb.base_url` clears the corresponding old key, which must be entered again.

### Telegram (`telegram`)

| Field | Default | Description |
| --- | --- | --- |
| `telegram.owner_id` | Empty | Your numeric Telegram ID. The control Bot serves only this ID; leave empty to disable it. Requires a main-process restart |
| `telegram.backfill_limit` / `backfill_max_days` | `200` / `7` | Maximum messages per source to backfill after startup or reconnection, and maximum message age in days; `0` disables backfilling. A newly monitored source does not import its history |
| `telegram.alert_after_sec` | `120` | How long the message source must be disconnected before an alert, including when Telegram is not yet configured or logged in; measured from the moment of disconnection |

### 115 Request Limits (`p115`)

| Field | Default | Description |
| --- | --- | --- |
| `p115.open_qps` / `cookie_qps` / `share_qps` | `3` / `2` / `1` | Maximum requests per second for open-platform, web, and share/transfer APIs, respectively |
| `p115.risk_cooldown_sec` | `3600` | Seconds to pause all APIs after 115 protection is triggered by access limits or excessive operations. Transfers and organization pause during the cooldown and resume automatically afterward, without failures or retry consumption. The cooldown survives main-process restarts. If 115 says a previous operation is still running, such as permitting only one deletion at a time, this is not treated as a protection cooldown; only that operation retries automatically after 30 seconds |

Obtain a 115 login by scanning the QR code under “设置 → 115 网盘” (Settings → 115 Drive). Credentials are stored in `data/115_credentials.json`.

### Network Proxy (`proxy`)

If your network cannot reach Telegram, TMDB, the AI service, or GitHub, connect them through an HTTP or SOCKS5 proxy. HTTP or mixed ports from clients such as Clash, mihomo, or v2rayN work. 115 servers are in China and always use a direct connection.

| Field | Default | Description |
| --- | --- | --- |
| `proxy.url` | Empty | Proxy URL, such as `http://192.168.1.2:7890` or `socks5://127.0.0.1:1080`; empty disables the proxy. For authentication, use `http://用户名@地址:端口`, replacing the placeholders with username, address, and port. Store the password in the `proxy_password` secret, not in the URL. In Docker, `127.0.0.1` is the container itself; if the proxy runs on the host or another LAN device, use that device's LAN address |
| `proxy.telegram` | `true` | Proxy the listener, control Bot, and terminal login (`python -m reelay.login`) |
| `proxy.tmdb` | `true` | Proxy TMDB |
| `proxy.ai` | `true` | Proxy the AI service; can be disabled for a locally reachable provider or local model |
| `proxy.github` | `true` | Proxy GitHub requests made when checking for new versions (plugin `update_check`) |

TMDB, AI, and GitHub changes take effect immediately after saving. Telegram requires a main-process restart; if it is not connected yet, the next retry, every 30 seconds, uses the new settings. Services not using this proxy retain their previous connection behavior: TMDB, AI, and GitHub still honor `HTTP_PROXY` / `HTTPS_PROXY` environment variables, while Telegram connects directly.

“测试代理” (Test proxy) under “设置 → 网络代理” (Settings → Network proxy) uses the form's current address to connect once through the proxy to each enabled service, without saving first. For Telegram it only connects to a data center and does not log in; any response from TMDB, the AI service, or GitHub counts as reachable. As with AI keys, a saved proxy password is sent only to the saved address, and saving a different `proxy.url` clears the password.

### Runtime Settings (`runtime`)

| Field | Default | Description |
| --- | --- | --- |
| `runtime.concurrency` | `3` | Number of tasks processed concurrently; requires a main-process restart |
| `runtime.log_level` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR`; requires a main-process restart |
| `runtime.task_retention_days` | `0` | Days to keep skipped and rejected tasks: `0` keeps them forever, otherwise 30–3650. They are deleted during the daily check; their trails and source statistics are gone afterwards. Stored tasks and dedup records are not affected |

## Plugin Configuration (`plugins`)

Each plugin's settings live under `plugins.<插件名>`, where `<插件名>` is its name. `enabled` is its switch; if omitted, the plugin's declared default is used. All built-in plugins are enabled by default. Plugins without other settings, such as `tmdb_enrich`, `share_probe`, and `failure_explainer`, have only this switch. External plugins store their settings here too; see the [Plugin development guide](plugins.md).

### Library Organization (`plugins.organizer`)

Before transfer, the directory layout determines the destination. After transfer, naming templates drive directory creation, renaming, and moving, followed by read-back verification. Both steps share one switch.

| Field | Default | Description |
| --- | --- | --- |
| `plugins.organizer.layout` | `category` | Directory layout: `category` uses two-level category rules, described below; `media_type` groups by media type using `type_dirs`; `none` skips classification and stores directly in the source's destination directory |
| `plugins.organizer.type_dirs` | `{movie: 电影, tv: 剧集, anime: 动漫}` | Single-level folder names for media-type layout. Titles identified as anime or with the Animation genre (16) go into `anime`. If `anime` is empty, do not separate them; place them under movie or TV instead. The defaults mean Movies, TV series, and Anime |
| `plugins.organizer.naming.movie` / `tv` | See example | Restricted Jinja2 path templates, with variables matching MoviePilot's renaming format: `title` `original_title` `name` `en_name` `year` `tmdbid` `season` `episode` `season_episode` `part` `videoFormat` `videoCodec` `audioCodec` `resourceType` `effect` `edition` `releaseGroup` `fileExt` (including the dot) `original_name` (original filename). Season and episode fields are empty for movies. `en_title` `season_year` `imdbid` `doubanid` `episode_title` `episode_date` `customization` are currently empty. Filters: `default` `lower` `upper` `replace` `trim` `join` `length` `pad2` |
| `plugins.organizer.conflict` | `manual` | When a file for the same episode or movie already exists: `manual` requests intervention so you can delete this transfer's files or overwrite and save in the console; `skip` keeps the old files and sends the new ones to the recycle bin; `suffix` keeps both by appending “ (2)” to the new name; `upgrade` compares using `release` preferences, replacing old files with better new versions and moving old files to the recycle bin, or behaving like `skip` otherwise |

Two-level category rules ship with the plugin in [`reelay/plugins/organizer/category.yaml`](../../reelay/plugins/organizer/category.yaml), not in the configuration file; the console displays them read-only. Matching follows MoviePilot: use `movie` for TMDB movies and `tv` for series, including anime, documentaries, and variety shows. Take the first category, from top to bottom, whose conditions all match. Condition fields are `genre_ids` (TMDB genre IDs), `origin_country` (two-letter country/region codes), and `release_year` (a year or range such as `2000-2010`). Comma-separated values mean OR. A category without conditions always matches and serves as a fallback when placed last. A category does not match if the work lacks data required by its conditions. If no category matches, store directly in the save directory.

If the classification result—the first level of a two-level category, or the media-type folder—matches a `save_dirs` alias, that alias determines the transfer root. Otherwise, create the full path below the source's configured directory. A directory manually selected during approval in the console or MCP takes precedence: if it differs from the classification result, store directly there without adding category subdirectories. If no directory is selected manually, classification applies automatically. Pending-approval cards and Telegram approval cards show the actual drive path, such as `/测试/剧集/国产剧集`; before the main process has read the directory path, they show “alias/category” instead.

### Other Plugins

| Field | Default | Description |
| --- | --- | --- |
| `plugins.share_probe.enabled` | `true` | Messages with only ed2k links do not access 115; infer seasons, episodes, sizes, and releases from the links' filenames and sizes. After identification, inspect 115 shares read-only: expired, canceled, or policy-violating shares are marked “分享无法访问” (Share inaccessible) in pending approvals with a suggestion to ignore. For series whose message omits episode numbers, list share contents to show actual seasons, episodes, and size, for example “S01–S03 · 30 集” (30 episodes), and flag issues likely to require manual intervention during transfer, such as unrecognizable season/episode filenames. Read one share at a time, at least 2 seconds apart, listing at most 20 directories per share; do not read during 115 protection cooldowns. If the message omits resolution, source, or size, the same query supplements them from root-directory videos using filenames or 115's detected resolution |
| `plugins.tmdb_enrich.enabled` | `true` | Match TMDB after identification to fill in normalized metadata and posters. TMDB endpoint, language, and key are system-level settings |
| `plugins.episode_tracker.track_all` | `true` | Automatically track series with recently added episodes; report only gaps and episodes aired according to TMDB but not yet in the library |
| `plugins.episode_tracker.lookback_days` | `30` | Lookback window for automatic tracking |
| `plugins.daily_report.narrative` | `true` | When AI is configured, prepend a short AI-written reminder based on the report's factual statistics; keep the numerical report too |
| `plugins.update_check.enabled` | `true` | Check for new versions: query the latest GitHub release (at most every 6 hours) and, when one is available, show a one-time notice in the top-right corner and an entry in the sidebar; the “版本更新” (Updates) page has the release notes and upgrade instructions. Source deployments can upgrade with one click after confirming (see [Deployment](deployment.md#versions-upgrades-and-rollbacks)); Docker deployments pull the new image themselves. Nothing is upgraded automatically. When disabled, GitHub is not contacted |
| `plugins.update_check.notify` | `true` | Check once a day at the daily report time and have the control Bot announce each new version once |

## Subscriptions and Ignored Titles

Subscriptions and titles marked “不再接收” (Stop receiving) are user data in the database (`data/reelay.db`), not configuration. Manage them through “追剧订阅” (Series subscriptions) in the console, Telegram approval-card actions “追剧并转存” (Subscribe and save) and “不再接收” (Stop receiving), the `/subs` and `/ignored` commands, or MCP. Changes do not alter the configuration version.

- **Series subscriptions**: title, season, optional total episode count, optional TMDB ID, and whether to auto-approve. Missing-episode inspections start at episode 1. With auto-approval enabled, new episodes of the same tmdbid, or TMDB-normalized title when no ID is present, and the same season save directly from sources using `decision: subscription` or `llm`. A TMDB match, sufficient confidence, unambiguous filter results, and at least one message episode not already in the library are still required. If the message omits season/episode information, confirm the season by inspecting the share (`share_probe`); multi-season collections are not auto-approved. If the message omits a season number, transfer only the subscribed season. A subscription ends automatically once airing is complete and all episodes are in the library.
- **Ignored titles**: title, optional TMDB ID, and media type (movie / TV). After identification, messages with the same tmdbid and type, or the TMDB-normalized title if there is no tmdbid, are ignored directly.

## Secrets

Enter secrets on the console's “设置” (Settings) pages; they apply when saved. Pages display only “已设置 / 未设置” (Set / Not set). Changing the control Bot token requires a main-process restart. Telegram API ID / Hash changes are adopted automatically if the listener has not connected yet; changing them after it connects requires a restart.

| Name | Page | Description |
| --- | --- | --- |
| `llm_api_key` | “AI 与 TMDB” (AI and TMDB) | AI API key. Without it, identification falls back to rules and every item requires manual review. Cleared automatically when `llm.base_url` changes |
| `tmdb_api_key` | “AI 与 TMDB” (AI and TMDB) | TMDB v3 API Key or v4 Read Access Token. Cleared automatically when `tmdb.base_url` changes |
| `telegram_api_id` / `telegram_api_hash` | Telegram | Telegram API credentials for your personal account |
| `control_bot_token` | Telegram | Control Bot token |
| `proxy_password` | “网络代理” (Network proxy) | Proxy authentication password, used only when `proxy.url` includes a username. Cleared automatically when `proxy.url` changes |
| `web_password` | “运行与安全” (Runtime and security) | Console login password, at least 8 characters, stored only as a PBKDF2 hash. Set it the first time you open the console; a LAN device opening it by IP can also do this. Before it is set, endpoints other than initial setup accept only local addresses. Changing or clearing it requires the current password first; changing it forces other logged-in pages to sign in again. Sessions are stored in the database, last 7 days, and survive Web restarts. If you forget the password, delete only the `web_password` entry from `data/secrets.json`, then reopen the console to set a new one |
| `mcp_token` / `mcp_readonly_token` | “运行与安全” (Runtime and security) | MCP access tokens, read-write / read-only. External agents access `/mcp` with `Authorization: Bearer <令牌>`, replacing `<令牌>` with their token. Read-only tokens do not expose write tools. Tokens must be at least 24 characters and distinct; the console can generate them randomly. If neither is set, `/mcp` is disabled. Independent of the Web login password |

## Runtime File Locations

These locations are fixed and have no configuration options. For backups or migration, copy all of `data/` together; in Docker it is the volume mounted at `/app/data`.

| File | Description |
| --- | --- |
| `data/config.yaml` | System and plugin configuration |
| `data/secrets.json` | Secrets |
| `data/reelay.db` | SQLite database; do not place it on a network filesystem |
| `data/userbot.session` / `data/controlbot.session` | Telegram sessions for the personal account and control Bot |
| `data/115_credentials.json` | 115 cookies and open-platform tokens |
| `data/reelay.log` | Main-process log, read by “运行日志” (Runtime logs) in the console; rotates above 10 MB into `reelay.log.1` through `.5` |
| `data/plugins/` | External extensions directory |
| `data/eval/` | Local evaluation samples and reports |

These parameters are fixed too: 2 retries for transient LLM errors, starting with a 2-second delay; 115 credential health checks every 5 minutes, or every 30 seconds while paused due to invalid credentials; hourly approval-timeout scans; 115 QR login binds a TV-device session; and OAuth tokens with a 2-hour lifetime refresh 15 minutes before expiry.

## Environment Variables

Only a few values need to be known before startup or depend on the deployment method. Put them in `.env`; see `.env.example`.

| Variable | Default | Description |
| --- | --- | --- |
| `REELAY_VERSION` | `latest` | Docker Compose only: image version, such as `1.0.0` |
| `REELAY_WEB_HOST` / `REELAY_WEB_PORT` | `127.0.0.1` / `28080` | Console bind address for local execution. For Docker, change `ports` in `compose.yaml` |
| `REELAY_FORWARDED_ALLOW_IPS` | `127.0.0.1` | Reverse proxies whose forwarded client addresses (`X-Forwarded-For`) the console trusts. Comma-separated; `*` trusts all. Failed-login rate limits use the real client address. In Docker, use the reverse proxy's address on the container network |
| `REELAY_MCP_READONLY` | Empty | Set to `1` to expose only MCP query tools, for both stdio and HTTP; the HTTP read-write token becomes read-only too |
| `REELAY_LOG_LEVEL` | `WARNING` | MCP stdio only (`python -m reelay.mcp_server`): log level for stderr. The main process uses `runtime.log_level` in system configuration |
| `VITE_BASE_PATH` | `/` | Frontend subpath prefix at build time, for reverse proxies serving a subpath |
