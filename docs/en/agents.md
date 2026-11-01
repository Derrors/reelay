# Agent Access and the Control Bot

[Documentation home](README.md) · [User guide](user-guide.md) · [FAQ](faq.md)

In addition to the web console, there are two ways to operate Reelay:

- **Telegram control Bot**: receive approval cards on your phone, use their buttons, or ask questions and request actions in natural language.
- **MCP**: let AI agents such as Claude query pending approvals, your library, and missing episodes, and approve, ignore, retry, or subscribe in batches.

**The Bot's natural-language assistant and MCP** share the management tools defined in `reelay/agent_tools.py`, but the control Bot also has separate card and button features. Natural-language write requests produce a plan first; nothing runs until you click “执行” (Execute). MCP read and write tools can run directly: whether you are asked to confirm again depends on the agent client, and Reelay does not add a separate confirmation before each call. Start with a read-only token when connecting an agent for the first time.

The web console and Bot interface are currently in Chinese. The Chinese labels below help you find the corresponding controls; this English documentation does not imply an English application interface.

## Control Bot

Under “设置 → Telegram” (Settings → Telegram), enter the control Bot token and your numeric Telegram ID. Open your own Bot in Telegram and send `/start`, then restart the main process. The control Bot responds only to this ID; it is separate from the personal Telegram account used to monitor sources.

**Cards and buttons** (no AI required):

- Resources that need your confirmation arrive as approval cards with actions to save, ignore, change the directory, follow a series, or stop receiving a title. The card keeps its original content and appends the outcome.
- If identification is wrong, click “修正” (Correct), then send the fields you want to change, for example “片名 繁花” (title: Blossoms Shanghai), “第 2 季 E01-E05” (season 2, E01–E05), or “繁花 2023 剧集 S01E03” (Blossoms Shanghai, 2023, TV series, S01E03). Omitted fields stay unchanged. You can also click “重新识别” (Re-identify). Corrected pending approvals are checked against TMDB again and sent back as new cards.
- When saving or organization stops, a “需要处理” (Action required) alert offers the applicable actions: verify the transfer, continue with the files received, reorganize, delete the transferred files / overwrite and save, retry, ignore, or correct identification.
- Approval digests and downtime-backfill notifications include a “查看待审批” (View pending approvals) button. Episode-tracking reminders have a mute button for each series: mark the currently missing episodes as no longer needed, or stop reminders for that series; both actions can be undone. When a subscription ends automatically, you can resume it with one click.
- Library-addition notifications and failure alerts are also sent here.

**Natural-language conversation** (requires an AI service):

- “待审批里有什么” — What is awaiting approval? “哪些任务失败了，为什么” — Which tasks failed, and why?
- “我有哪些 4K 的日本动画” — Which Japanese anime do I have in 4K? “《X》第二季缺哪几集” — Which episodes of season 2 of X are missing?
- “把《X》这几集都批了，存到剧集” — Approve these episodes of X and save them to 剧集. “订阅《X》第一季” — Subscribe to season 1 of X. “不追《X》了” — Unsubscribe from X. “把这条的片名改成《Y》” — Change this item's title to Y.

Queries return answers directly. Natural-language requests to approve, ignore, retry, subscribe, unsubscribe, correct identification, or perform other writes are listed as a plan first and run only after you click “执行” (Execute). The plan expires if you do not click within ten minutes. This plan-confirmation flow does not mean that ordinary card buttons and direct commands all follow the same process. The model is instructed to treat resource text and titles as data, but this is not an absolute defense against prompt injection. Always check the targets and actions before execution.

**Commands** (the command entry points themselves do not depend on AI; common commands appear in the `/` menu. `/retry` can be entered directly but is not listed in the menu. `/subs 片名` queries TMDB and may use the configured AI to help parse the request):

| Command | Purpose |
| --- | --- |
| `/pending` | Pending approvals and tasks needing manual intervention, each with a button to open its card; approve all or ignore all in one action, with confirmation first |
| `/failed` | Failed tasks; open a task to correct, retry, or ignore it |
| `/subs` | Series subscriptions: episodes already in the library and episodes still missing. `/subs 片名` (optionally with a season, such as `/subs 嫁金钗 第二季`) finds the series on TMDB; select a season to subscribe. If matching tasks are already awaiting approval, you can save them at the same time. You can cancel subscriptions. Series that have finished airing and ended automatically are not listed; use the completion notification to resume them. Subscription suggestions are also listed, with options to subscribe or choose “不用了” (No thanks) |
| `/ignored` | Titles marked “不再接收” (Stop receiving); you can restore them |
| `/status` · `/retry <任务 ID>` · `/help` | Status counts, retry a task, and help; replace `<任务 ID>` with the task ID |

If your 115 login expires, scan the QR code again in the web console.

## MCP

### Connection Methods

**HTTP**: use this for Docker deployments or when the agent runs on another machine. In the web console, generate access tokens under “设置 → 运行与安全 → Agent 接入” (Settings → Runtime and security → Agent access): one read-write token and one read-only token. Then run the following, replacing `<令牌>` with your token:

```bash
claude mcp add --transport http reelay http://127.0.0.1:28080/mcp \
  --header "Authorization: Bearer <令牌>"
```

- `/mcp` is served by the Web process and accepts only MCP access tokens, independently of the web console's login password. If neither token is set, `/mcp` is disabled.
- The read-only token does not expose write tools. Tokens must be at least 24 characters long; the web console can generate them randomly.
- If the agent runs on another machine, expose the port or use a reverse proxy, just as for the web console. The reverse proxy must pass through the `Authorization` header.

**stdio**: use this when the agent runs on the same machine as Reelay and accesses `data/` directly:

```bash
pip install -e ".[agent]"
claude mcp add reelay -- /path/to/reelay/.venv/bin/reelay-mcp
```

Set the environment variable `REELAY_MCP_READONLY=1` to expose query tools only. This also makes the HTTP read-write token read-only.

### Tools

| Tool | Purpose | Access |
| --- | --- | --- |
| `status` | Runtime overview: task counts by state, operating mode, and Worker / message source / 115 health | Read-only |
| `list_pending` | Pending approvals, including suggested actions and reasons, recommended versions, and days waiting; also tasks needing manual intervention | Read-only |
| `list_tasks` / `get_task` | Find tasks by state or keyword; get task details and recent processing traces | Read-only |
| `list_save_dirs` | Save-directory aliases; an approval can redirect a task to one of them | Read-only |
| `search_library` | Search library titles and episodes by title, media type, minimum resolution, region, or TMDB genre | Read-only |
| `missing_episodes` | Episode-inspection results: missing episodes and episodes that aired days ago but are still absent from the library | Read-only |
| `list_subscriptions` / `find_show` | Subscribed series, including whether each subscription ended automatically, and subscription suggestions; resolve a series name to a TMDB entry and seasons | Read-only |
| `list_ignored` | Titles marked “不再接收” (Stop receiving) | Read-only |
| `approve` | Approve pending tasks, optionally changing the destination directory | Write · real transfers |
| `reject` | Ignore pending tasks | Write |
| `retry` | Retry failed tasks from their checkpoints | Write · may cause real transfers |
| `subscribe` | Subscribe to one season of a series; returns pending tasks for that season | Write |
| `unsubscribe` / `resume_subscription` | Cancel a subscription; resume one that ended automatically | Write |
| `unignore` | Resume receiving a title | Write |
| `correct` | Correct identification for tasks awaiting approval, needing manual intervention, or failed: title, year, media type, season, episodes, or resolution | Write |
| `reparse` | Re-identify without the cache; unavailable for tasks that already have a transfer checkpoint | Write |

`approve`, `reject`, `retry`, and `reparse` accept arrays of task IDs, with 1–100 IDs per call. They return a result for each task and flag tasks that were handled in the meantime. Approval and retry only change task state; the running main process performs the actual 115 transfers and organization, which cannot be undone through MCP. Tools that can cause real transfers carry a `destructiveHint` annotation. Clients may use it to ask for confirmation on each call, but the annotation itself does not force them to ask.

Read-only means these write operations are unavailable, not that there are no external requests: title searches and similar queries may still access TMDB or call the configured AI. The current MCP tools do not cover every web-console feature. For example, scanning the QR code again, changing settings, verifying transfers, continuing with partial results, reorganizing, and overwriting conflicts still require the web console or the corresponding Bot buttons.
