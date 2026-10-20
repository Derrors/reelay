# 配置参考

Reelay 的设置分四类，全部可以在管理台修改，不需要改环境变量或重启（个别项注明需要重启主进程）：

| 类别 | 存放位置 | 在哪里改 |
| --- | --- | --- |
| **系统配置** | `data/config.yaml` 的 `system:` | 管理台「设置」各页 |
| **插件配置** | `data/config.yaml` 的 `plugins.<插件名>`：`enabled` 开关与该插件的设置 | 管理台「插件」页，每个插件一张卡片 |
| **密钥** | `data/secrets.json`（权限 600，不进配置文件） | 管理台「设置」：只显示是否已设置，可替换或清除，读不回原值 |
| **固定，不可配置** | 代码常量 | 不提供配置项，见 [运行文件位置](#运行文件位置) |

管理台保存时原子写入 `data/config.yaml`；也可以直接编辑这个文件（或用管理台的原文编辑），运行中的 Worker 约 1 秒内采用新配置；配置无效时继续使用上一个有效版本并在总览报错。完整示例见 [`config.example.yaml`](../config.example.yaml)。

所有字段都有默认值，下表只列默认值和含义；`sources[]` 中的条目可覆盖 `defaults` 里的任何字段。

```yaml
system:
  save_dirs: {电影: "123", 剧集: "456", 默认: "0"}
  defaults: {approval_mode: manual, save_to: 默认}
  sources: [{bot: "@some_bot"}]
  llm: {base_url: "", model: gpt-4o-mini}
  # …其余系统配置
plugins:
  organizer: {enabled: true, conflict: manual, naming: {...}}
  strm: {enabled: false, output_dir: "", base_url: ""}
  # …其余插件
```

## 系统配置（`system`）

### 目录与来源（`save_dirs` / `sources` / `providers`）

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
| `decision` | `subscription` | `default` 只按规则；`subscription` 在规则之上让订阅剧集的新集自动放行；`llm` 再按 `preferences` 的自然语言偏好（并参考该来源近期的批准与忽略）决定是否收藏及目录，选出的目录不会被二级分类覆盖 |
| `preferences` | `""` | 例："只要 4K 或 1080p 的国产剧和日本动画，综艺不要"。想让它变成确定性规则，用管理台「用一句话生成规则」 |
| `confidence_threshold` | `0.5` | 低于该值转人工。置信度由标题/类型/年份是否齐全、TMDB 是否匹配计算，不采信 LLM 自报值 |
| `episodes_from` | `share` | `share`：以分享内文件名为准，转存未入库的所有集；`message`：只转存消息声明的集 |
| `filters.must_have` | `[]` | 正文或链接必须包含其一，否则跳过（且不调用 LLM） |
| `filters.block_keywords` | `[]` | 正文命中任一则跳过（且不调用 LLM） |
| `filters.allow` | `[]` | 条件列表，命中任一条才收；空表示不限。条件字段 `media_type`（movie/tv/anime）、`genre_ids`（TMDB 类型 ID）、`origin_country`（两位代码），写法同二级分类：同一条件内为"且"，逗号分隔为"或" |
| `filters.deny` | `[]` | 条件列表，命中任一条就不收 |
| `filters.min_resolution` | 不限 | `480p` / `720p` / `1080p` / `2160p`；低于则不收，清晰度未知时转人工 |
| `forward` | 继承全局 | 来源级转发覆盖，见下 |

无论哪种确认方式，低置信度、TMDB 已启用但无可信匹配、LLM 降级的资源都会转人工。`allow` / `deny` / `min_resolution` 在识别与 TMDB 补全之后按字段判断，管理台「用一句话生成规则」会把一句话偏好起草成这些规则，确认后才写入。

### AI 能力（`ai`）

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `ai.examples` | `3` | 识别时取同一来源最近几条人工修正作为提示词示例（0–10，0 关闭） |
| `ai.history` | `20` | `decision: llm` 时附上同一来源最近几条人工批准与忽略（0–100，0 关闭） |
| `ai.assist` | `true` | 分享清单按规则无法确定要转存哪些文件时（文件名认不出集号或季号、同一季同一集多个文件、电影多个正片候选、消息只写了集数而分享跨多季），先让 AI 给方案；方案按同一套规则校验，不过才转人工 |
| `ai.advice` | `true` | 待审批附建议动作（批准 / 忽略 / 需要你看一下）与理由；动作由规则决定，AI 只负责措辞 |
| `ai.suggest_auto.min_decisions` | `20` | 某来源人工确认满这么多次后才评估是否建议改为自动转存 |
| `ai.suggest_auto.approve_rate` | `0.9` | 批准率不低于该值 |
| `ai.suggest_auto.max_correct_rate` | `0.1` | 被修正识别结果的比例不高于该值 |

建议只是提示，切换确认方式需要你在管理台确认。

### 版本偏好（`release`）

同一集（或同一部电影）有多个版本时，待审批的推荐版本与洗版（`plugins.organizer.conflict: upgrade`）都按这里比较：偏好的字幕 → 清晰度 → HDR → 偏好的音轨 → 偏好的片源 → 体积。列表顺序即优先级。

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `release.prefer_subtitles` | `[chs, cht]` | `chs` 简体 / `cht` 繁体 / `eng` / `jpn` / `kor`；空表示不看字幕 |
| `release.prefer_audio` | `[]` | `mandarin` / `cantonese` / `english` / `japanese` / `korean` |
| `release.hdr` | `any` | `any` 不在意 / `prefer` 优先 HDR、杜比视界 / `avoid` 避开 |
| `release.prefer_sources` | `[]` | `remux` / `bluray` / `web-dl` / `webrip` / `hdtv` / `dvd` |

消息没写的项：推荐版本时按"不符合偏好"计；洗版会删除旧文件，用严格比较——库中文件已知符合偏好而新文件没写时不替换。

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

### 剧集订阅（`subscriptions`）

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `subscriptions` | `[]` | 剧集订阅 `{title, season?, total?, tmdbid?, auto?}`：缺集巡检从第 1 集开始检查；`auto`（默认 `true`）时，`decision` 为 `subscription` 或 `llm` 的来源里同一 tmdbid（没有时按 TMDB 规范片名）、同一季的新集直接转存。仍要求 TMDB 匹配、置信度达标、过滤规则无疑问、消息里的集未全部入库；消息没写季集时以读取分享（`share_probe`）的结果确认季，跨多季的合集不放行，消息没写季号时转存只取订阅的那一季。管理台「追剧订阅」与待审批「追这部剧」会写入这里 |

### AI 服务与 TMDB（`llm` / `tmdb`）

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `llm.base_url` | 空 | OpenAI 兼容接口地址，留空用 OpenAI 官方地址 |
| `llm.model` | `gpt-4o-mini` | 模型名。管理台可以从接口获取模型列表选择 |
| `llm.reasoning_effort` | 空 | 推理强度，随每次请求作为 `reasoning_effort` 发送：`none` / `minimal` / `low` / `medium` / `high`；留空不传，由模型决定。只对支持推理的模型生效，不支持的模型或网关可能报错 |
| `tmdb.base_url` | 空 | 留空用 `https://api.themoviedb.org/3`；国内该域名常被 DNS 污染，可改用官方备用域名 `https://api.tmdb.org/3` 或自己的反向代理 |
| `tmdb.language` / `tmdb.timeout` | `zh-CN` / `10` | TMDB 匹配语言与超时秒数 |

AI 接口密钥与 TMDB key 是密钥，见 [密钥](#密钥)。改了立即生效。管理台「AI 服务」可以用表单里当前的地址获取模型列表（OpenAI 兼容的 `GET /models`），并用当前的地址、模型与推理强度发一条很短的消息测试连通性，不必先保存。

### Telegram（`telegram`）

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `telegram.owner_id` | 空 | 你的 Telegram 数字 ID；控制 Bot 只服务这个 ID，留空不启用控制 Bot（重启主进程生效） |
| `telegram.backfill_limit` / `backfill_max_days` | `200` / `7` | 重连后每个来源最多补拉的消息条数（`0` 关闭）与最大消息年龄（天） |
| `telegram.alert_after_sec` | `120` | 消息源断开多久后告警 |

### 115 请求限速（`p115`）

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `p115.open_qps` / `cookie_qps` / `share_qps` | `3` / `2` / `1` | 开放平台接口、网页接口、分享与转存接口每秒最多请求数 |
| `p115.risk_cooldown_sec` | `3600` | 触发 115 风控（访问上限、操作频繁）后所有接口暂停的秒数 |

115 的登录态在「设置 → 115 网盘」扫码获得，保存在 `data/115_credentials.json`。

### 运行参数（`runtime`）

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `runtime.concurrency` | `3` | 同时处理的任务数（重启主进程生效） |
| `runtime.log_level` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR`（重启主进程生效） |

## 插件配置（`plugins`）

每个插件的设置在 `plugins.<插件名>` 下，`enabled` 是开关（不写时按插件默认：`strm` 默认关闭，其余默认开启）。没有设置项的插件（如 `tmdb_enrich`、`share_probe`、`failure_explainer`）只有开关。外部插件的设置同样放在这里，见 [插件开发指南](plugins.md)。

### 媒体库整理（`plugins.organizer`）与二级分类（`plugins.dir_router`）

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `plugins.organizer.naming.movie` / `tv` | 见示例 | 受限 Jinja2 路径模板。变量：`title` `year` `season` `episode` `resolution` `ext` `media_type` `genre_ids` `origin_country` `tmdb_id` `original_name`；过滤器：`default` `lower` `upper` `replace` `trim` `join` `length` `pad2` |
| `plugins.organizer.conflict` | `manual` | 目标已有同名不同文件时：`manual` 转人工；`skip` 保留旧文件、新文件进回收站；`suffix` 新文件加「 (2)」并存；`upgrade` 洗版——按 `release` 版本偏好比较，新文件更好则替换（旧文件进回收站），否则同 skip |
| `plugins.dir_router.categories` | MoviePilot 风格默认策略 | `movie`/`tv` → `一级/二级` 路径 → 条件（`genre_ids`、`origin_country`，逗号分隔为"或"，同一分类内多个条件为"且"，空条件为兜底且须放最后） |

一级分类名与某个 `save_dirs` 别名相同时，转存根目录跟随分类；否则在来源指定目录下创建完整的二级路径。审批时（管理台、MCP）手动选择的目录优先：与分类结果不同时直接存入该目录，不再套用二级分类子目录；不选则按分类自动存放，待审批卡片与 Telegram 审批卡片显示网盘中的实际路径（如 `/测试/剧集/国产剧集`；主进程读取到目录路径之前显示「别名/分类」）。

### 其他插件

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `plugins.share_probe.enabled` | `true` | 消息没写集数的剧集，识别后只读列出 115 分享，在待审批显示实际的季、集与大小，跨多季时显示为「S01–S03 · 30 集」，并提前标出失效链接、文件名无法识别季集等转存时会转人工的情况；同一时间只读一个分享、间隔 2 秒，单个分享最多列 20 个目录 |
| `plugins.tmdb_enrich.enabled` | `true` | 识别后匹配 TMDB 补全规范元数据与海报；TMDB 的地址、语言与 key 是系统配置 |
| `plugins.episode_tracker.track_all` | `true` | 自动跟踪最近有新集入库的剧，只报告断档和 TMDB 已播未入库的新集 |
| `plugins.episode_tracker.lookback_days` | `30` | 自动跟踪的时间窗口 |
| `plugins.daily_report.narrative` | `true` | 配置了 AI 时，日报开头附一段 AI 按统计事实写的提醒，数字版保留 |
| `plugins.strm.output_dir` | — | STRM 输出目录（本机路径），启用 `strm` 时必填 |
| `plugins.strm.base_url` | — | 媒体服务器能访问到的 Reelay 地址，如 `http://192.168.1.10:28080` |

## 密钥

在管理台「设置」中填写，保存即生效（Telegram 与控制 Bot 需重启主进程）；页面只显示「已设置 / 未设置」。

| 名称 | 在哪一页 | 说明 |
| --- | --- | --- |
| `llm_api_key` | AI 与 TMDB | AI 接口密钥；未设置时识别降级为规则匹配并且每条都转人工 |
| `tmdb_api_key` | AI 与 TMDB | TMDB v3 API Key 或 v4 Read Access Token |
| `telegram_api_id` / `telegram_api_hash` | Telegram | 个人账号的 Telegram API 凭证 |
| `control_bot_token` | Telegram | 控制 Bot 的 token |
| `web_password` | 运行与安全 | 管理台登录密码，只保存 PBKDF2 摘要。暴露到本机以外时必须设置；修改或清除要先输入当前密码，修改后其他已登录的页面需要重新登录。忘记密码时删掉 `data/secrets.json` 里的 `web_password` 一项即可恢复免登录 |

## 运行文件位置

以下位置固定，不提供配置项；备份或迁移时整体复制 `data/` 即可（Docker 为数据卷 `/app/data`）。

| 文件 | 说明 |
| --- | --- |
| `data/config.yaml` | 系统配置与插件配置 |
| `data/secrets.json` | 密钥 |
| `data/reelay.db` | SQLite 数据库（勿放在网络文件系统） |
| `data/userbot.session` / `data/controlbot.session` | 个人账号与控制 Bot 的 Telegram 会话 |
| `data/115_credentials.json` | 115 cookie 与开放平台令牌 |
| `data/strm.secret` | STRM 跳转地址的签名密钥，首次使用时生成 |
| `data/reelay.log` | 主进程日志，管理台「运行日志」读取 |
| `data/plugins/` | 外部扩展目录 |
| `data/eval/` | 本地评测样本与报告 |

以下参数也已固定：LLM 瞬时错误重试 2 次（首次间隔 2 秒）、115 凭证探活每 5 分钟（失效暂停期间每 30 秒）、审批超时扫描每小时、115 扫码绑定 TV 设备会话、OAuth 令牌按 2 小时有效期提前 15 分钟刷新。

## 环境变量

只剩启动前就要知道、或与部署方式绑定的几项：

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `REELAY_WEB_HOST` / `REELAY_WEB_PORT` | `127.0.0.1` / `28080` | 管理台监听地址 |
| `REELAY_MCP_READONLY` | 空 | 设为 `1` 时 MCP 只暴露查询工具 |
| `VITE_BASE_PATH` | `/` | 构建前端时的子路径前缀（反向代理到子路径时使用） |

## 从旧版本升级

旧版本的配置写在项目根目录的 `config.yaml`（或 `REELAY_CONFIG_PATH`、Docker 的 `/app/config/config.yaml`）与 `.env` 里。升级后主进程或管理台第一次启动时自动迁移，不需要手动操作：

- 配置文件移到 `data/config.yaml`，原文件改名为 `*.migrated`；旧结构（系统配置在顶层、插件配置在 `organize` / `dir_router` / `strm` / `daily_report` / `episode_tracker` 等顶层键、`plugins` 只是开关）先备份为 `config.yaml.bak-日期`，再改写成 `system` / `plugins` 新结构。`episode_tracker.subscriptions` 移到 `system.subscriptions`。
- `.env` 里的旧变量逐个导入：普通设置写入系统配置，密钥写入 `data/secrets.json`，`REELAY_115_COOKIES` 与开放平台令牌写入 115 凭证文件（文件里已有的不覆盖），`REELAY_STRM_SECRET` 写入 `data/strm.secret`。每个值只导入一次：之后在管理台改过或清除的不会被 `.env` 覆盖回去；`.env` 里换了新值会再导入一次。启动日志列出导入了哪些变量，之后可以从 `.env` 删掉它们。
- `REELAY_TG_SESSION`、`REELAY_CONTROL_BOT_SESSION`、`REELAY_115_OAUTH_FILE` 指向别处的文件会复制到 `data/`。`REELAY_DB_PATH` 指向别处时不自动搬（数据库与 WAL 文件要一起移动），启动会报错并给出移动命令。
- 外部插件放在 `data/plugins/`（原 `REELAY_PLUGIN_DIRS`）；它们留在配置顶层的旧设置原样保留并在启动日志提示，请移到 `plugins.<插件名>` 下。
