# 配置参考

Reelay 有两类配置：

- **业务配置** `config.yaml`（路径由 `REELAY_CONFIG_PATH` 指定）：来源、目录、审批、整理、扩展。可在管理台修改，保存后运行中的 Worker 约 1 秒内采用；配置无效时继续使用上一个有效版本并在总览报错。完整示例见 [`config.example.yaml`](../config.example.yaml)。
- **环境变量** `.env`：凭证与运行参数，启动时读取，修改后需重启。

所有字段都有默认值，下表只列默认值和含义；`sources[]` 中的条目可覆盖 `defaults` 里的任何字段。

## config.yaml

### 目录与来源

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `save_dirs` | — | 目录别名 → 115 目录 CID（十进制字符串）。必须至少有一个；`0` 表示根目录 |
| `sources[].bot` | — | 要监听的 `@username` 或数字 ID，不区分大小写 |
| `sources[].*` | 同 `defaults` | 来源级覆盖：确认方式、保存目录、解析器、过滤规则、偏好等 |
| `providers.source` | `telegram` | 消息源 Provider |
| `providers.notify` | `[console]` | 通知渠道列表；`console` 在配置了控制 Bot 时发到 Telegram，否则写日志 |

### 识别与决策（`defaults` / `sources[]`）

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `approval_mode` | `manual` | `manual` 人工确认；`auto` 自动转存并通知；`default_pass` 自动转存且不发成功通知 |
| `save_to` | `默认` | 默认保存到的目录别名 |
| `parser` | `llm` | `llm` 或 `regex`。LLM 不可用、超时重试后仍失败或输出非法时降级 regex 并强制人工确认 |
| `llm_response_format` | `json_schema` | 不支持结构化输出的兼容接口改为 `json_object`（仍做本地严格校验） |
| `decision` | `default` | `llm` 时按 `preferences` 的自然语言偏好决定是否收藏及目录 |
| `preferences` | `""` | 例："只要 4K 或 1080p 的国产剧和日本动画，综艺不要" |
| `confidence_threshold` | `0.5` | 低于该值转人工。置信度由标题/类型/年份是否齐全、TMDB 是否匹配计算，不采信 LLM 自报值 |
| `episodes_from` | `share` | `share`：以分享内文件名为准，转存未入库的所有集；`message`：只转存消息声明的集 |
| `filters.must_have` | `[]` | 正文或链接必须包含其一，否则跳过（且不调用 LLM） |
| `filters.block_keywords` | `[]` | 正文命中任一则跳过（且不调用 LLM） |
| `forward` | 继承全局 | 来源级转发覆盖，见下 |

无论哪种确认方式，低置信度、TMDB 已启用但无可信匹配、LLM 降级的资源都会转人工。

### 转发、审批提醒、定时

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `forward.enabled` | `false` | 识别成功后按模板转发 |
| `forward.target` | `""` | 转发目标（控制 Bot 需能向其发消息） |
| `forward.template` | 见示例 | 可用字段 `{title}` `{year}` `{resolution}` `{size}` `{link}` |
| `approval.timeout_hours` | `24` | 待审批超过该时长视为超时 |
| `approval.on_timeout` | `remind` | `remind` 提醒；`skip` 超时直接忽略 |
| `approval.reminder` | `digest` | `digest` 把所有超时任务合并成一条汇总；`card` 逐条重推确认卡片 |
| `approval.max_reminders` | `3` | 每条最多提醒次数，之后不再提醒但仍保留在待审批；`0` 不限 |
| `schedule.daily_hour` | `9` | 每日系统事件（日报、追更巡检）发送的本地小时；进程启动时会先等停机期间补拉的消息解析完（最多 15 分钟） |

### 整理入库

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `organize.naming.movie` / `tv` | 见示例 | 受限 Jinja2 路径模板。变量：`title` `year` `season` `episode` `resolution` `ext` `media_type` `genre_ids` `origin_country` `tmdb_id` `original_name`；过滤器：`default` `lower` `upper` `replace` `trim` `join` `length` `pad2` |
| `organize.conflict` | `manual` | 目标已有同名不同文件时：`manual` 转人工；`skip` 保留旧文件、新文件进回收站；`suffix` 新文件加「 (2)」并存；`upgrade` 洗版——按画质再按大小比较，新文件更好则替换（旧文件进回收站），否则同 skip |
| `dir_router.categories` | MoviePilot 风格默认策略 | `movie`/`tv` → `一级/二级` 路径 → 条件（`genre_ids`、`origin_country`，逗号分隔为"或"，同一分类内多个条件为"且"，空条件为兜底且须放最后） |

一级分类名与某个 `save_dirs` 别名相同时，转存根目录跟随分类；否则在来源指定目录下创建完整的二级路径。审批时（管理台、MCP）手动选择的目录优先：与分类结果不同时直接存入该目录，不再套用二级分类子目录；不选则按分类自动存放，待审批卡片会显示实际去向。

### 扩展

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `plugins.<name>` | 扩展自身声明 | 按名称启停 Hook/Event；`strm` 默认关闭，其余默认开启 |
| `tmdb.language` / `tmdb.timeout` | `zh-CN` / `10` | TMDB 匹配语言与超时秒数 |
| `episode_tracker.track_all` | `true` | 自动跟踪最近有新集入库的剧，只报告断档和 TMDB 已播未入库的新集 |
| `episode_tracker.lookback_days` | `30` | 自动跟踪的时间窗口 |
| `episode_tracker.subscriptions` | `[]` | 显式订阅 `{title, season?, total?}`，从第 1 集开始检查 |
| `strm.output_dir` | — | STRM 输出目录（本机路径），启用 `strm` 时必填 |
| `strm.base_url` | — | 媒体服务器能访问到的 Reelay 地址，如 `http://192.168.1.10:28080` |

## 环境变量

### 凭证

| 变量 | 说明 |
| --- | --- |
| `REELAY_TG_API_ID` / `REELAY_TG_API_HASH` | 个人账号的 Telegram API 凭证 |
| `REELAY_TG_SESSION` | 个人账号 session 文件，默认 `data/userbot.session` |
| `REELAY_CONTROL_BOT_TOKEN` / `REELAY_OWNER_ID` | 控制 Bot token 与唯一可操作它的用户 ID |
| `REELAY_CONTROL_BOT_SESSION` | 控制 Bot session 文件，默认 `data/controlbot.session` |
| `REELAY_115_COOKIES` | 115 cookie 兜底来源；推荐在管理台扫码，保存到凭证文件 |
| `REELAY_115_OAUTH_FILE` | 115 凭证文件，默认 `data/115_credentials.json`（权限 600） |
| `REELAY_LLM_BASE_URL` / `REELAY_LLM_API_KEY` / `REELAY_LLM_MODEL` | OpenAI 兼容接口，模型默认 `gpt-4o-mini` |
| `REELAY_TMDB_API_KEY` | TMDB v3 API Key 或 v4 Read Access Token |
| `REELAY_WEB_PASSWORD` | 管理台密码；暴露到本机以外时必须设置 |
| `REELAY_STRM_SECRET` | STRM 签名密钥；留空则自动生成并保存到 `data/strm.secret` |

### 运行参数

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `REELAY_DB_PATH` | `data/reelay.db` | SQLite 数据库（勿放在网络文件系统） |
| `REELAY_CONFIG_PATH` | `config.yaml` | 业务配置文件 |
| `REELAY_WORKER_CONCURRENCY` | `3` | 同时处理的任务数 |
| `REELAY_LOG_LEVEL` / `REELAY_LOG_FILE` | `INFO` / 空 | 日志级别与落盘文件（管理台"运行日志"读取该文件） |
| `REELAY_WEB_HOST` / `REELAY_WEB_PORT` | `127.0.0.1` / `28080` | 管理台监听地址 |
| `REELAY_PLUGIN_DIRS` | 空 | 外部扩展目录，多个用 `:` 分隔 |
| `REELAY_TG_BACKFILL_LIMIT` / `REELAY_TG_BACKFILL_MAX_DAYS` | `200` / `7` | 重连后每个来源最多补拉条数（`0` 关闭）与最大消息年龄 |
| `REELAY_SOURCE_ALERT_SEC` | `120` | 消息源断开多久后告警 |
| `REELAY_SWEEP_INTERVAL_SEC` | `3600` | 审批超时扫描间隔 |
| `REELAY_LLM_RETRIES` / `REELAY_LLM_RETRY_DELAY` | `2` / `2` | LLM 瞬时错误的重试次数与首次间隔（秒） |
| `REELAY_115_AUTH_CHECK_SEC` | `300` | 115 凭证探活间隔；凭证失效暂停期间固定 30 秒 |
| `REELAY_115_OPEN_QPS` / `REELAY_115_COOKIE_QPS` / `REELAY_115_SHARE_QPS` | `3` / `2` / `1` | 115 各类接口的请求速率上限 |
| `REELAY_115_RISK_COOLDOWN_SEC` | `3600` | 触发 115 风控后所有接口的冷却时间 |
| `REELAY_115_COOKIE_QR_APP` | `tv` | 扫码登录绑定的设备类型，独立设备会话可减少与网页端互踢 |
| `REELAY_MCP_READONLY` | 空 | 设为 `1` 时 MCP 只暴露查询工具 |
| `VITE_BASE_PATH` | `/` | 构建前端时的子路径前缀（反向代理到子路径时使用） |
