# 插件开发指南

Reelay 的主流程（监听 → 识别 → 转存整理）是固定骨架，所有可变能力都挂在三类扩展点上。加功能就是写一个 Python 文件，不用改内核。

两个可直接运行的示例：

- [`examples/plugins/webhook_notify.py`](../examples/plugins/webhook_notify.py)：Webhook 通知渠道（Provider）
- [`examples/plugins/quality_gate.py`](../examples/plugins/quality_gate.py)：拦截 CAM/TS 等低画质资源（Hook）

## 选哪类扩展点

| 你想做的事 | 扩展点 | 例子 |
| --- | --- | --- |
| 同一件事换一种实现 | **Provider**（能力槽） | 新消息源、新通知渠道、另一种解析器或网盘 |
| 在某个阶段前后插入处理，可能改数据或拦截 | **Hook**（钩子） | 补元数据、二次过滤、选目录、整理、生成 STRM |
| 某件事发生后旁路做点什么，不能挡主流程 | **Event**（事件） | 统计、日报、写外部索引、审计 |

## 加载方式

- **单文件或包目录**：放进 `REELAY_PLUGIN_DIRS` 指向的目录（多个用 `:` 分隔；Docker 部署为 `deploy/plugins`），重启后自动发现。
- **独立发行包**：在你的包里声明 entry point，`pip install` 后自动加载：

  ```toml
  [project.entry-points."reelay.plugins"]
  my_plugin = "my_package.reelay_plugin"
  ```

外部扩展导入或实例化失败只会记录到管理台"插件"页，不影响内置能力。扩展以 Reelay 进程权限运行，只加载你信任的代码。

## 注册

```python
from reelay.core.registry import register

@register(kind="provider", ptype="notify")   # ptype: source / parser / decision / storage / notify
class MyNotify: ...

@register(kind="hook", stage="after_parse", order=60)
class MyHook: ...

@register(kind="event", event="on_saved")
class MyListener: ...
```

装饰器只记录类，`discover()` 时用无参构造函数实例化。每个扩展需要一个全局唯一的 `name`，它同时是配置里 `plugins.<name>` 的开关名和 Provider 的引用名。

## 贯穿全程的 PipelineContext

一条消息对应一个 `PipelineContext`（`reelay/core/contracts.py`），每个阶段从数据库快照重建：

| 属性 | 含义 |
| --- | --- |
| `raw` | 原始消息：`source`、`msg_id`、`text`、`urls`（含超链接真实 URL） |
| `parsed` | 识别结果 `ParsedResource`：标题、年份、类型、季集、类型 ID、国家、分享码等 |
| `state` / `decision` | 当前状态与决策（save / ask / skip） |
| `save_dir_alias` / `save_dir_cid` | 目标目录 |
| `source_rule` | 合并了 `defaults` 的来源规则 |
| `ctx.ns(name)` | 扩展私有命名空间（`meta[name]`），随任务持久化 |
| `ctx.log(msg)` | 写入任务轨迹，管理台可见 |

## Hook

```python
class MyHook:
    name = "my_hook"
    stage = "after_parse"   # before_parse / after_parse / before_save / after_save
    order = 60              # 同一阶段内从小到大执行；内置：dir_router 10，tmdb_enrich 50，organizer 20，strm 30

    async def run(self, ctx) -> bool:
        ...
        return True          # False 表示拦截
```

返回 `False` 与抛出异常的后果因阶段而异：

| 阶段 | 返回 False | 抛出异常 |
| --- | --- | --- |
| `before_parse` / `after_parse` | 任务结束为 skipped | 记录日志后继续执行后续 Hook（失败隔离） |
| `before_save` | 任务结束为 skipped | 任务失败并按阶段重试（转存前置检查不能静默跳过） |
| `after_save` | 任务转 need_manual | 记录整理错误，任务转 need_manual |

`after_save` 运行时文件已经转存完成。只适用于某个存储 Provider 的整理类 Hook 应声明 `storage_provider = "p115"`，内核会拒绝把它用在其他 Provider 转存的任务上。只做附加产物（如 STRM）的 Hook 应自己捕获异常并返回 `True`，不要影响任务结果。

## Event

```python
class MyListener:
    name = "my_listener"
    event = "on_saved"   # on_message / on_identified / on_rejected / on_saved / on_failed / on_daily

    async def handle(self, ctx) -> None:
        ...
```

- 事件与任务状态在同一事务内写入 Outbox，异步投递：至少一次、单次 30 秒超时、失败退避重试（最多 8 次）、同一任务内按生成顺序。
- `ctx` 是事件发生时的快照，修改它不会影响任务。
- `on_daily` 由内核每天在 `schedule.daily_hour` 发出一次，`ctx.meta["system"]` 里有日期和过去 24 小时汇总，适合日报、巡检类扩展。
- 一个类只能订阅一个事件；需要订阅多个事件时写多个类并使用相同的 `name`（共用一个开关），参考 `reelay/plugins/episode_tracker.py`。

## Provider 契约

| ptype | 需要实现 | 可选 |
| --- | --- | --- |
| `source` | `async listen(emit)`：长驻协程，每条消息 `await emit(RawMessage(...))` | `configure(targets)`、`bind_watermark(lookup)`、`health()`、`async close()` |
| `parser` | `async parse(ctx) -> ParsedResource` | — |
| `decision` | `async decide(ctx) -> Decision` | — |
| `storage` | `prepare` / `save` / `reconcile`（只读核验，绝不能重发写请求） | `discard(ctx)` |
| `notify` | `async notify(ctx, event)`；event 为 identified / pending_approval / saved / transfer_uncertain / organize_failed / failed | `async notice(text)`：系统通知（告警、汇总、日报） |

Provider 由配置选择：`providers.source`、`providers.notify`，以及 `defaults`/`sources[]` 中的 `parser`、`decision`、`storage`。

## 读取配置与使用内核服务

```python
from reelay.core import runtime

url = runtime.get("webhook.url")                      # 点路径读取当前任务采用的配置
runtime.services().notify("📦 备份完成", key="my_plugin")  # 发系统通知到所有通知渠道（持久化投递）
state = runtime.services().state("my_plugin")          # 按扩展隔离的持久化 KV，值需可 JSON 序列化
state.set("last_run", "2026-10-19")
```

`runtime.get` 读到的是任务接收时的配置快照，配置变更只影响新任务。`runtime.get` 与 `services()` 在内核调用 Hook、Event、通知渠道，以及解析/决策/存储 Provider 的方法时可用（包括其中的 `asyncio.to_thread`）；消息源的 `listen` 是长驻协程，不在任务作用域内。

同步阻塞调用（HTTP、SDK）请放进 `asyncio.to_thread`，不要阻塞事件循环。

## 在管理台展示与校验配置

```python
class MyHook:
    name = "my_hook"
    ui_label = "我的扩展"
    ui_description = "一句话说明，显示在插件列表"
    ui_fields = [   # 自动生成配置表单，并在保存时按类型校验
        {"path": "my_hook.threshold", "label": "阈值", "type": "number", "min": 0, "max": 1},
        {"path": "my_hook.mode", "label": "模式", "type": "string", "choices": ["a", "b"]},
        {"path": "my_hook.enabled_x", "label": "开关", "type": "boolean"},
    ]
    default_enabled = False   # 未在 plugins 中显式配置时默认关闭（适合有副作用的扩展）

    def validate_config(self, cfg: dict) -> None:
        """扩展启用时，配置保存与启动都会调用；抛 ValueError 拒绝无效配置。"""
```

数组、对象等复杂配置在 `validate_config` 中自行校验，用户通过配置文件维护。

## 测试

用 `REELAY_PLUGIN_DIRS` 加载你的扩展并直接调用，参考 [`tests/test_examples.py`](../tests/test_examples.py)：

```python
with patch.dict(os.environ, {"REELAY_PLUGIN_DIRS": "path/to/plugins"}, clear=True):
    registry = discover()
assert registry.load_errors == []
hook = next(h for h in registry.hooks["after_parse"] if h.name == "my_hook")
with runtime.scope({"my_hook": {"threshold": 0.5}}):
    assert await hook.run(ctx) is True
```
