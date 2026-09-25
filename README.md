# Reelay

> **Reel**（影视胶片） + **Relay**（接力转发）——监听 Telegram 资源 Bot 的消息，用 AI 识别影视资源与 115 网盘链接，经可配置的审批后转存进网盘、并按媒体库规范整理入库。

这是一个 **AI Native + 全流程可扩展** 的自托管系统的**内核骨架**。当前版本落地了架构评审确认的 MVP 范围，核心是一条"稳定内核 + 三类扩展点"的管线。

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
├── providers/      # 内置能力槽实现（含 dry-run 占位实现）
├── plugins/        # 用户插件（Hook/Event），目录自动发现
├── control/        # 控制 bot（可观测与人工干预）
└── main.py         # 入口
```

## 状态

内核骨架 + dry-run 冒烟可跑通。真实的 TG 监听 / 115 转存 / LLM 解析以「未安装依赖则降级」方式接入，见各 Provider。

> 本项目仅作技术实现探讨。userbot 与网盘操作请遵守相关平台规则及当地法律法规；凭证妥善保管。
