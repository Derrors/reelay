# Reelay

> **Reel**（影视胶片） + **Relay**（接力转发）——监听 Telegram 资源 Bot 的消息，用 AI 识别影视资源与 115 网盘链接，经可配置的审批后转存进网盘、并按媒体库规范整理入库。

这是一个 **AI Native + 全流程可扩展** 的自托管系统。当前版本按架构评审确认的范围落地了主流程各阶段的真实能力，核心是一条"稳定内核 + 三类扩展点"的管线。

## 设计原则

1. **AI 负责理解判断，代码负责执行验证** —— AI 读消息、抽字段、判断存不存；真正调 115、写目录、去重必须是确定性代码，且转存后读回验证。
2. **稳定内核 + 可插拔外围** —— 主流程（监听→识别→转存）是固定骨架，能力全是挂在扩展点上的插件。
3. **单一 Context 贯穿全程** —— 一条消息带着 `PipelineContext` 流经所有阶段，可追溯、可回放。
4. **插件失败隔离** —— 任何插件出错都不拖垮主流程。

## 三类扩展点

| 类型 | 何时用 | 例子 |
|---|---|---|
| **Provider** 能力槽 | 一件事的另一种实现 | 消息源、解析器、网盘执行器、通知渠道 |
| **Hook** 钩子 | 在阶段前后插入、可改数据 | 补元数据、二次过滤、选目录、改名整理 |
| **Event** 事件 | 发生后旁路响应，不挡主流程 | 统计、推日报、写媒体库索引 |

## 主流程与任务状态机

```
监听接入 → AI 感知层识别 → 转存 + 整理
   Stage1        Stage2          Stage3
```

任务状态机（评审补丁：审批态落库、可重启、可干预）：

```
received → parsed → [pending_approval|queued|rejected] → saving → [done|need_manual|failed]
```

## 关键设计（来自架构评审）

- **审批态落库**：manual 模式下待确认任务持久化为 `pending_approval`，callback_data 只带短 id，进程重启后卡片仍可点。
- **三层去重**：消息级(msg_id) / 资源级(share_code) / 内容级(title+season+ep)，各司其职，支持追更增量。
- **可观测与干预**：控制 bot 提供 `/status`、`/failed`（重试）、`/pending`。

## 快速开始

```bash
# 1. 骨架 dry-run（无需任何三方依赖，验证内核跑通）
python3 -m reelay.main --dry-run

# 2. 接入真实能力
pip install -e ".[full]"
cp .env.example .env            # 填 TG / 115 / LLM 凭证
cp config.example.yaml config.yaml
python3 -m reelay.main
```

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

按「完整技术方案」落地的功能（均可通过 dry-run 端到端验证，接入真实凭证即生效）：

- **Stage 1 监听**：`TelegramUserbotSource` 用你的账号（Telethon）同时监听多个 bot；从消息 entity 抽超链接真实 URL。
- **Stage 2 识别**：`LLMParser`（OpenAI 兼容，JSON schema 约束）判类+抽字段，未配 key 自动降级 `RegexParser`；115 链接解析交确定性代码；`DefaultDecision` 按过滤规则+审批模式判 save/ask/skip。
- **元数据匹配**：`TmdbEnrich`（after_parse Hook）查 TMDB 回填规范标题/年份/tmdbid，未配 key 降级放行。
- **Stage 3 转存整理**：
  - `DirRouter`（before_save）按 `routing` 规则选目录别名；
  - `P115Storage` 用 `P115ShareFileSystem` 列举分享内视频文件（过滤广告 txt）→ `receive` 转存 → **读回验证**（对比目标目录前后快照）；
  - `Organizer`（after_save）按 `naming` 模板在 115 内 `mkdir/rename/move` 整理成「剧名(年份)/Season NN/SxxEyy」，字段不全或整理失败转 `need_manual`，重名跳过不覆盖。
- **通知/转发**：`ConsoleNotify` 推确认卡片（callback 只带 task_id）与结果通知；开启 `forward` 则按模板转发到目标。
- **控制 bot**：`/status`、`/failed`、`/pending`、`/retry <id>` 与审批按钮回调（仅服务 `REELAY_OWNER_ID`）。
- **稳定性**：SQLite(WAL) 持久队列、三层去重（支持追更增量）、重启恢复、退避重试、审批超时清扫、Semaphore 限流。

## 状态

主流程各阶段功能已实现：`python3 -m reelay.main --dry-run` 端到端 PASS，19 个单元测试全过。真实的 TG 监听 / 115 转存整理 / LLM 解析 / TMDB / 控制 bot 均以「未装依赖或未配凭证则降级」方式接入——装上 `.[full]` 依赖、填好 `.env` 与 `config.yaml` 即从占位切换为真实执行。

> 本项目仅作技术实现探讨。userbot 与网盘操作请遵守相关平台规则及当地法律法规；凭证妥善保管。
