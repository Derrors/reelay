# Changelog

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/)，格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

## [Unreleased]

### 新增

- 管理台重新设计：浅色侧栏与重新分组的导航（工作台 / 媒体 / 系统 / 工具），完整的深色模式（默认跟随系统），手机端顶栏、抽屉与底部标签栏。
- 「待审批」收件箱：海报、季集、清晰度、置信度与原因一目了然，逐条或多选批量转存 / 忽略，可改投目录；「待人工」集中处理结果不明或整理失败的任务。
- 总览、任务、媒体库（海报墙）、统计、设置、插件与各工具页重做；任务详情改为侧边抽屉。
- TMDB 匹配成功时保存海报并在管理台展示，没有海报时生成配色稳定的文字封面。
- 接口：`GET /api/inbox`；任务列表支持多状态、分页并返回媒体字段；批量批准可指定目录；总览返回近 24 小时数据。

### 修复

- 批量批准 / 忽略与粘贴 cookie 接口的请求体从未被解析，调用总是失败。
- 每个页面各开一条 SSE 连接，多标签页或前进后退时可能占满浏览器连接数导致页面卡住；现在每个标签页共用一条，页面进入后台缓存时释放。
- 插件页同一扩展（缺集追更）重复显示；统计页颜色在深色模式下难以辨认。
- 长时间停机后重启只按时间正序补拉，补拉上限被最旧的消息占满，停机期间的新消息全部漏掉；现在从最新的消息往回补拉窗口内的消息，补拉的任务不再逐条推送确认卡片，改为一条汇总通知。
- 本地直接运行时 `.env` 里的 `REELAY_LOG_FILE` / `REELAY_LOG_LEVEL` 与 `REELAY_WEB_HOST` / `REELAY_WEB_PORT` 不生效。
- 总览的「115 网盘」始终显示「已填写，待验证」，现在显示实际探活结果。

## [0.2.1] - 2026-10-19

0.2.0 全新安装后无法使用 115 转存，请直接使用本版本。

### 修复

- 全新安装（含 Docker 镜像）时 `p115client` 无法导入：其传递依赖 `python-concurrenttools` 0.1.9 移除了 `threadpool_map`，现固定为 0.1.8。
- 管理台关闭时 SSE 观察线程可能与数据库关闭竞争同一连接导致进程崩溃（Linux 上表现为段错误），改为在事件循环线程读取。

### 变更

- CI 使用的 GitHub Actions 升级到最新大版本。

## [0.2.0] - 2026-10-19

首个公开版本。

### 新增

- 确定性置信度：由标题、类型、年份、TMDB 匹配等信号计算，替代 LLM 自报值。
- LLM 成本控制：调用前确定性预过滤、相同正文缓存、瞬时错误退避重试、非法输出重试一次。
- 分享清单作为集数的事实来源：剔除样片/预告/花絮，排除已入库的集；新增 `episodes_from`。
- 整理冲突策略 `organize.conflict`：manual / skip / suffix / upgrade（洗版）。
- TMDB 候选按标题相似度与年份打分，anime 先剧集后电影，结果缓存；支持 v4 Token；默认命名模板写入 `[tmdbid=…]`。
- 审批超时汇总提醒（`approval.reminder` / `max_reminders`）。
- 115 凭证缺失或失效时暂停转存与整理并告警，恢复后自动继续；消息源断线告警；重连后按消息 ID 水位补拉。
- 系统通知与告警通道、每日 `on_daily` 事件；内置日报、缺集追更、STRM 生成扩展。
- 外部插件目录 `REELAY_PLUGIN_DIRS` 与 `reelay.plugins` entry point；扩展可默认关闭、自校验配置，并通过 `runtime.services()` 发送通知与读写私有状态。
- MCP server（`python -m reelay.mcp_server`），支持只读模式。
- Web：SSE 实时推送、115 目录选择器、运行总览展示进行中的告警。
- 版本化数据库迁移（`PRAGMA user_version`），拒绝打开更新版本创建的数据库。
- CI（后端测试与 ruff、前端 typecheck/eslint/vitest/build、Docker 构建冒烟）、pre-commit、示例插件、完整文档与免责声明。

### 变更

- 失败重试改为写入下次执行时间，退避期间不再占用并发槽。
- 前端改用标准 Vite 构建（`web/dist`），支持 `VITE_BASE_PATH` 子路径部署；删除未使用的组件与依赖，统计页按需加载。
- 依赖统一由 `pyproject.toml` 声明，新增 `agent`、`dev` 可选依赖。

### 修复

- 任务快照漏存 `genre_ids` / `origin_country`，导致二级分类在 Worker 中不生效。
- 来源解析预览未把解析结果交给决策，结果恒为"跳过"。
- 统计接口的任务计数恒为空、已完成体积估算错误；统计与高级检索不再另开且不关闭数据库连接。
- 设置管理台密码后刷新页面总要重新登录。
- 电影的 TMDB 匹配会清空 AI 从正文得到的国家/地区。

### 安全

- 设置 `REELAY_WEB_PASSWORD` 后，所有 `/api/*` 接口（含 SSE）统一要求有效会话；会话服务端过期，登录失败限流；健康检查不再向未登录者暴露运行细节。

## [0.1.0] - 2026-09-27

未公开发布的初版：Telegram userbot 监听、LLM 解析、三种审批模式、115 转存与读回核验、媒体库整理、Web 管理台、控制 Bot 与 Docker 部署。

[0.2.1]: https://github.com/Derrors/reelay/releases/tag/v0.2.1
[0.2.0]: https://github.com/Derrors/reelay/releases/tag/v0.2.0
[0.1.0]: https://github.com/Derrors/reelay/commits/v0.2.0
