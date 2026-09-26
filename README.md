# Reelay

> **Reel**
>
> （影视胶片） +
>
> **Relay**
>
> （接力转发）—— 监听 Telegram 资源 Bot 的消息，用 AI 识别影视资源与 115 网盘链接，经可配置的审批后转存进网盘、并按媒体库规范整理入库。

这是一个 **AI Native + 全流程可扩展** 的自托管系统。当前版本提供主流程各阶段的实现与本地模拟验证，真实账号联调尚未完成，核心是一条 "稳定内核 + 三类扩展点" 的管线。

## 设计原则



1. **AI 负责理解判断，代码负责执行验证** —— AI 读消息、抽字段、判断存不存；真正调 115、写目录、去重必须是确定性代码，且转存后读回验证。

2. **稳定内核 + 可插拔外围** —— 主流程（监听→识别→转存）是固定骨架，能力全是挂在扩展点上的插件。

3. **单一 Context 贯穿全程** —— 一条消息带着 `PipelineContext` 流经所有阶段，可追溯、可回放。

4. **插件失败隔离** —— 通知和旁路事件先落库，独立投递与退避重试；转存前置检查与整理失败会阻止当前任务完成。

## 三类扩展点



| 类型               | 何时用           | 例子                 |
| ---------------- | ------------- | ------------------ |
| **Provider** 能力槽 | 一件事的另一种实现     | 消息源、解析器、网盘执行器、通知渠道 |
| **Hook** 钩子      | 在阶段前后插入、可改数据  | 补元数据、二次过滤、选目录、改名整理 |
| **Event** 事件     | 发生后旁路响应，不挡主流程 | 统计、推日报、写媒体库索引      |

## 主流程与任务状态机



```
监听接入 → AI 感知层识别 → 转存 + 整理
   Stage1        Stage2          Stage3
```

任务状态机（评审补丁：审批态落库、可重启、可干预）：



```
received → parsed → pending_approval → queued → saving → stored → organizing → done
                └→ queued / rejected / skipped    └→ need_manual       └→ need_manual
解析或转存准备失败 → failed → 从记录的阶段重试
```

## 关键设计（来自架构评审）



* **审批态落库**：manual 模式下待确认任务持久化为 `pending_approval`，callback\_data 只带短 id，进程重启后卡片仍可点。

* **三层去重**：消息级 (source+msg\_id) / 资源级 (存储 Provider+目标 CID+share\_code) / 内容级 (存储 Provider+目标 CID+title+season+ep)。消息记录与任务同时提交；执行转存前重新检查去重，支持排队期间追更。

* **可观测与干预**：控制 bot 提供 `/status`、`/failed`（重试）、`/pending`。

## 快速开始

运行环境为 Python 3.12+，与固定的 115 SDK 版本保持一致。


```
# 1. 骨架 dry-run（无需任何三方依赖，验证内核跑通）
python3 -m reelay.main --dry-run

# 2. 接入真实能力
pip install -e ".[full]"
cp .env.example .env            # 填 TG / 115 / LLM 凭证
cp config.example.yaml config.yaml
python3 -m reelay.main
```

### Web 管理控制台

```bash
# 后端管理 API（默认仅监听 127.0.0.1:28080，不启动任务 Worker）
pip install -e ".[web]"
python3 -m reelay.web

# 前端开发服务（另开终端）
cd web
npm install
npm run dev -- --port 28641
```

打开 `http://localhost:28641`。Web 与 Telegram 控制 Bot 共用同一应用服务层和 SQLite 任务状态；控制台提供运行总览、任务审批/重试/重新解析、识别字段纠错、配置维护和整理计划预览。配置保存后，运行中的 Worker 会重载有效配置，监听目标与消息源随之更新。管理台分别展示保存版本和 Worker 实际采用版本；环境凭证与并发数修改需要重启。

> Web 默认不启动第二个 Worker。当前支持 macOS/Linux 单机运行，数据库文件锁阻止两个进程消费同一数据库。仅本机访问不含内置登录；如需暴露到局域网或公网，请先增加访问控制。

## 目录结构



```
reelay/
├── core/           # 内核：契约、状态机、持久层、去重、管线、注册机
├── providers/      # 内置能力槽实现（真实实现 + 未配凭证时降级）
├── plugins/        # 用户插件（Hook/Event），目录自动发现
├── control/        # 控制 bot（可观测与人工干预）
└── main.py         # 入口
```

## 已实现能力

已实现的功能如下；dry-run 验证模拟主流程，不能替代真实账号验收：



* **Stage 1 监听**：`TelegramUserbotSource` 用你的账号（Telethon）同时监听多个 bot；从消息 entity 抽超链接真实 URL。

* **Stage 2 识别**：`LLMParser`（OpenAI 兼容，JSON schema 约束）判类 + 抽字段，输出再经本地严格类型与范围校验；未配 key、超时、拒绝或校验失败时降级 `RegexParser` 并强制人工确认。显式选择 `regex` 可按常规审批策略自动执行。115 链接解析交确定性代码；`DefaultDecision` 按过滤规则与审批模式决策，`LLMDecision` 可按自然语言偏好选择是否收藏和目录，但不能覆盖硬过滤、低置信度或人工审批。

* **元数据匹配**：`TmdbEnrich`（after\_parse Hook）查 TMDB 回填规范标题 / 年份 /tmdbid，未配 key 降级放行。

* **Stage 3 转存整理**：


  * `DirRouter`（before\_save）按 `routing` 规则选目录别名；

  * `P115Storage` 列举待转存视频，创建 `.reelay-{task_id}` 独占暂存目录；文件清单和发送检查点落库后才调用 `receive`。按名称、数量、大小及可用 SHA1 完整核验，分别记录源文件与落地文件 ID；

  * `Organizer`（after\_save）先持久化整理计划，再按文件 ID 执行 `mkdir/rename/move`，读回最终目录和名称。中断后按实际位置续跑；字段不全或重名冲突转 `need_manual`，保留源文件。

* **通知 / 转发**：`ConsoleNotify` 推确认卡片（callback 只带 task\_id）与结果通知；全局 `forward` 与来源级覆盖按模板转发；`default_pass` 不发成功通知，人工审批与故障告警仍保留。通知和 Event 均持久化投递，不阻塞接收和转存。

* **控制 bot**：`/status`、`/failed`、`/pending`、`/retry <id>` 与审批按钮回调（仅服务 `REELAY_OWNER_ID`）。

* **稳定性**：SQLite (WAL) 持久队列、三层去重（支持追更增量）、重启恢复、退避重试、审批超时清扫、Semaphore 控制任务并发、独立事件投递与 Worker 心跳。Semaphore 不是请求级速率限制。

* **Web 管理台**：React 控制台 + FastAPI API，支持状态总览、任务筛选与详情、人工审批/忽略、失败重试、重新解析、识别字段修正、核验转存、仅重新整理、配置管理和整理计划预览。排队中和执行中的任务禁止编辑；已有转存检查点的任务不能重新解析。

## 转存与恢复边界

`StorageProvider` 分为 `prepare`、`save`、`reconcile`：准备清单、发送转存、只读核验。`receiving` 必须在调用网盘前提交。转存回执、去重记录和释放资源占位在同一事务内提交，之后整理独立恢复。SQLite 使用 WAL + FULL 同步；同一分享或同一集的未核验转存会持续占位，防止其他消息绕过检查点。

115 未提供本项目可用的请求幂等键，发送前后发生中断时可能无法确认是否已执行。因此 `receiving` 恢复后只核验独占目录，绝不自动重发；空目录、部分落地、内容不符都进入 `need_manual`。管理台“核验转存”（`POST /api/tasks/{id}/reconcile`）只重新读回，完整核验后才进入整理。“重新整理”只使用已有文件 ID，不再次转存。更换目标目录时重新解析别名对应的 CID。

暂存目录保留用于核验，整理成功后为空。分享中不同路径的视频重名或集数不完整时，在转存前停止。整理的同名不同 ID 冲突会保留文件并转人工，同 ID 已完成的步骤可以重放。SDK 固定为 `p115client==0.0.9.6.5.1`，升级时需重新检查 SDK 接口及故障测试。

数据库升级仅增加资源占位、投递队列与健康状态表，不删除历史任务。`parsed` 中断重新解析，`organizing` 中断继续整理；旧版 `saving` 没有核验清单时进入人工处理，不能自动重放，也不能通过重新解析绕过。旧版去重键在能确认原目标 CID 时继续生效。

真实模式缺网盘依赖或凭证会报错。`--dry-run` 使用内存数据库；即使环境中存在凭证，115、整理、LLM 与 TMDB 也走模拟或规则路径。


## 配置与审批契约

配置文件支持深层默认合并和类型校验，语法错误不会静默恢复到 CID=0。Provider 名称与扩展开关在保存和启动时校验。`providers.source` 选择消息源，`providers.notify` 选择通知渠道；`defaults` 和每个来源可选择 `parser`、`decision`、`storage`；`plugins` 按扩展名称启停 Hook/Event。

```yaml
providers:
  source: telegram
  notify: [console]
defaults:
  parser: llm
  llm_response_format: json_schema
  decision: default   # 设为 llm 并填 preferences 可启用自然语言偏好
  storage: p115
  confidence_threshold: 0.5
  preferences: ""
plugins:
  tmdb_enrich: true
  dir_router: true
  organizer: true
```

默认使用严格 JSON Schema；不支持该格式的兼容接口可显式设置 `llm_response_format: json_object`，本地字段校验和失败转人工仍生效，不会在错误后悄悄切换格式。

任务接收时保存审批、过滤、转发、路由、命名和扩展开关快照。配置更新用于新任务；显式“重新解析”使用当前策略，“重新整理”使用当前命名和扩展配置。目录别名在尚无转存检查点时解析为当前 CID；有检查点后绑定原 Provider、CID、清单与文件 ID。其他存储 Provider 需要对应的整理扩展，不能复用操作 115 的 Organizer。

| 模式 | 高置信度资源 | 成功通知 | 低置信度 / AI 降级 |
| --- | --- | --- | --- |
| manual | 待人工审批 | 有 | 待人工审批 |
| auto | 自动入队 | 有 | 待人工审批 |
| default_pass | 自动入队 | 静默 | 待人工审批 |

显式开启的资源转发独立于成功通知开关。`auto` 尚不提供已转存文件的自动撤销，不会删除账号内的文件。路由支持 `type`/`media_type`、`genre`/`genres`；AI 解析提供 genres，缺少依据时为空。

通知与事件使用事务 Outbox：接收、审批入队和完成状态与对应投递意图共同提交，任务状态不等待发送器或监听器。每次投递使用独立快照，超时 30 秒；失败退避，最多 8 次后保持失败，可在管理台重新入队。支持 Telegram FloodWait 的服务器等待时间；同一任务/接收器维持事件生成顺序。已经完成的审批和旧轮次通知不再推送。投递按至少一次语义恢复，发送后回执丢失可能重复通知；重试投递不会重新执行转存。

投递管理 API：`GET /api/deliveries?status=failed`、`POST /api/deliveries/{id}/retry`。`/api/health` 展示服务自身和共享的 Worker 状态，不能证明凭证有效或真实转存验收完成。

## 部署与后续验收

提供前端多阶段构建的 [Dockerfile](Dockerfile)、单 Worker/独立 Web 的 [compose.yaml](compose.yaml)，以及 [Docker 部署说明](docs/deployment.md)。真实账号联调、应用层历史水位补拉、请求级令牌桶、115 认证失效暂停与登录态续期、自动撤销仍待后续开发和验收。

## 状态

本地验证命令：`python3 -m unittest discover -s tests -v`、`python3 -m reelay.main --dry-run`，以及前端 `npm run typecheck`、`npm run lint:eslint`、`npm run build`。回归覆盖目录穿越、并发转存、重复排队、进程中断、部分落地、回执丢失、整理冲突及检查点恢复。回归还覆盖 AI 非法输出/降级、自然语言偏好、跨进程配置重载、Provider 绑定、来源切换、投递失败与租约恢复。真实账号的 TG/115 端到端联调尚未执行。

> 本项目仅作技术实现探讨。userbot 与网盘操作请遵守相关平台规则及当地法律法规；凭证妥善保管。
