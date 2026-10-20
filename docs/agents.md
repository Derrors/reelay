# Agent 接入与控制 Bot

除了管理台，还可以用两种方式操作 Reelay：

- **Telegram 控制 Bot**：在手机上收审批卡片、点按钮，或者直接用一句话查询和操作；
- **MCP**：让 Claude 等 AI Agent 查询待审批、媒体库与缺集，并批量批准、忽略、重试或订阅。

两者用的是同一套工具（定义在 `reelay/agent_tools.py`），区别只在写操作什么时候执行：控制 Bot 先把要做的事列出来，你点「执行」才执行；MCP 由 Agent 客户端按工具标注向你确认。

## 控制 Bot

在「设置 → Telegram」填好控制 Bot 的 Token 与你的 Telegram 数字 ID，重启主进程即可。控制 Bot 只回应这个 ID。

**审批卡片**：需要你确认的资源会推送一张卡片，点「✅ 转存」或「🚫 忽略」，卡片保留原内容并在末尾写明结果；入库、需要处理和故障告警也会推送到这里。

**直接说话**（需要配置 AI 服务）：

- 「待审批里有什么」「哪些任务失败了，为什么」
- 「我有哪些 4K 的日本动画」「《X》第二季缺哪几集」
- 「把《X》这几集都批了，存到剧集」「订阅《X》第一季」

查询直接回答；批准、忽略、重试、订阅这类写操作会先列出来，点「执行」后才执行，十分钟内不点就作废。消息正文和片名只当作数据，里面写的指令不会被执行。

**命令**（不需要 AI）：`/pending` 待处理任务、`/failed` 失败任务、`/retry <任务 ID>` 重试、`/status` 状态计数、`/help` 帮助。

## MCP

### 接入方式

**HTTP**：Docker 部署，或 Agent 不在 Reelay 所在的机器上时使用。在管理台「设置 → 运行与安全 → Agent 接入」生成访问令牌（读写、只读各一个），然后：

```bash
claude mcp add --transport http reelay http://127.0.0.1:28080/mcp \
  --header "Authorization: Bearer <令牌>"
```

- `/mcp` 由 Web 进程提供，只认 MCP 访问令牌，与管理台登录密码互不相关；两个令牌都没设置时 `/mcp` 关闭。
- 只读令牌看不到写操作工具。令牌至少 24 个字符，管理台可以随机生成。
- Agent 在其他机器上时，要像管理台一样把端口开放出去，或经反向代理访问；反向代理需要放行 `Authorization` 请求头。

**stdio**：Agent 与 Reelay 在同一台机器上、直接读写 `data/` 时使用：

```bash
pip install -e ".[agent]"
claude mcp add reelay -- /path/to/reelay/.venv/bin/reelay-mcp
```

设置环境变量 `REELAY_MCP_READONLY=1` 只提供查询工具（HTTP 的读写令牌也降为只读）。

### 工具

| 工具 | 作用 | 读写 |
| --- | --- | --- |
| `status` | 运行概况：各状态任务数、运行模式、Worker / 消息源 / 115 健康状态 | 只读 |
| `list_pending` | 待审批（附建议动作与理由、推荐版本、等待天数）与待人工处理的任务 | 只读 |
| `list_tasks` / `get_task` | 按状态或关键字检索任务；任务详情与最近的处理轨迹 | 只读 |
| `list_save_dirs` | 保存目录别名，批准时可以改投到其中之一 | 只读 |
| `search_library` | 按片名、类型、最低清晰度、地区或 TMDB 类型检索已入库作品与各季的集 | 只读 |
| `missing_episodes` | 缺集巡检结果：缺哪几集、哪些已播出多天仍未入库 | 只读 |
| `list_subscriptions` / `find_show` | 已订阅的剧与订阅建议；把剧名对应到 TMDB 条目与季 | 只读 |
| `approve` | 批准待审批任务，可改投目录 | 写 · 真实转存 |
| `reject` | 忽略待审批任务 | 写 |
| `retry` | 按检查点重试失败任务 | 写 · 可能真实转存 |
| `subscribe` | 订阅一部剧的某一季，返回这一季正在待审批的任务 | 写 |

`approve`、`reject`、`retry` 接收任务 ID 数组，逐条返回结果。批准和重试只改任务状态，真实的 115 转存与整理由正在运行的主进程执行，无法从 MCP 撤销。会产生真实转存的工具带 `destructiveHint` 标注，客户端可以据此逐次确认。
