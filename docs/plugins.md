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
- `on_daily` 由内核每天在 `schedule.daily_hour` 发出一次，`ctx.meta["system"]` 里有日期和过去 24 小时汇总，适合日报、巡检类扩展；进程启动时会等停机期间补拉的消息解析完再发（最多推迟 15 分钟）。
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

## 元数据补全约定

识别完成后，内置的 `tmdb_enrich`（`after_parse`，order 50）按标题与年份匹配 TMDB 条目并回填元数据。TMDB 的 ID 体系是 Reelay 的数据标准：类型 ID 就是 TMDB 类型 ID，命名中的 `[tmdbid=…]` 供 Jellyfin / Emby 刮削，海报按 TMDB 图片路径显示。下面的字段由内核和其他扩展直接读取；接入其他数据源（如豆瓣、Bangumi）时，请把结果对应到 TMDB 条目并写入同样的字段。

写入 `ctx.parsed`：

| 字段 | 内容 | 谁在读 |
| --- | --- | --- |
| `title` / `year` | 规范标题与上映 / 首播年份 | 去重键（`片名\|S01\|E02`，不同来源的同一部作品靠它合并）、整理路径、缺集追更 |
| `genre_ids` | TMDB 类型 ID 整数列表，整体替换 AI 的值 | 二级分类 `dir_router.categories`（如 16 = 动画）、命名模板 |
| `origin_country` | 两位大写国家/地区代码；数据源没有时保留 AI 的值，不要清空 | 二级分类、命名模板 |
| `raw_fields["tmdbid"]` | TMDB 条目 ID（整数） | 命名模板变量 `tmdb_id`（默认模板写入 `[tmdbid=…]`）、缺集追更查询已播集数 |
| `raw_fields["poster_path"]` | TMDB 图片路径，如 `/abc.jpg`，不是完整 URL | 管理台海报（拼接 `https://image.tmdb.org/t/p/<尺寸>`）；没有时显示文字封面 |

写入 `ctx.meta["tmdb"]`：确定性置信度在全部 `after_parse` Hook 之后计算，按这里的结果判断（`reelay/core/confidence.py`）。

| 情况 | 写入 | 对置信度的影响 |
| --- | --- | --- |
| 匹配成功 | `matched: True`、`tmdbid` | 计为通过的信号 |
| 查询成功但没有可信匹配 | `matched: False` | 封顶 0.45，标记「TMDB 未匹配」，转人工确认 |
| 没有查询（未配置凭证、模拟模式） | `matched: False`、`note: 原因` | 中立，不影响分数 |
| 查询出错（网络、限流等） | `matched: False`、`error: 异常类型名` | 中立，不影响分数 |

`candidates`、`kind`、`score` 等其余字段只供任务详情排查，内核不读取。

接入其他数据源有两种方式：

- **补充**：order 大于 50，只在 TMDB 没有匹配时查询；命中后写入上表字段，并整体替换 `meta["tmdb"]`——残留的 `note` / `error` 会让结果仍按中立处理。
- **替代**：在 `plugins` 中关闭 `tmdb_enrich`，由你的 Hook 按同样约定写入。

```python
import asyncio

from reelay.core import runtime
from reelay.core.registry import register


@register(kind="hook", stage="after_parse", order=60)
class DoubanFallback:
    name = "douban_fallback"

    async def run(self, ctx) -> bool:
        p = ctx.parsed
        if not p or not p.is_media or ctx.meta.get("tmdb", {}).get("matched"):
            return True
        hit = await asyncio.to_thread(lookup_via_douban, p.title, p.year)  # 你的实现：经豆瓣找到对应的 TMDB 条目
        runtime.observe("douban", "connected")
        if hit:
            p.title, p.year = hit["title"], hit["year"]
            p.raw_fields["tmdbid"] = hit["tmdbid"]
            # 能拿到时同样回填 genre_ids、origin_country、raw_fields["poster_path"]
            ns = ctx.ns("tmdb")
            ns.clear()
            ns.update(matched=True, tmdbid=hit["tmdbid"], source="douban")
        return True
```

对应不到 TMDB 条目的结果不要只写自有 ID：去重、分类、命名与追更都以 TMDB 条目为准，这种情况按「没有可信匹配」处理。

## 读取配置与使用内核服务

```python
from reelay.core import runtime

url = runtime.get("webhook.url")                      # 点路径读取当前任务采用的配置
runtime.services().notify("📦 备份完成", key="my_plugin")  # 发系统通知到所有通知渠道（持久化投递）
state = runtime.services().state("my_plugin")          # 按扩展隔离的持久化 KV，值需可 JSON 序列化
state.set("last_run", "2026-10-19")
runtime.observe("my_api", "failed", "TimeoutError")    # 记录一次外部调用结果（connected / failed）
```

`runtime.observe` 记录的是最近一次真实调用的结果，不会因一段时间没有调用而过期，可在 `GET /api/health` 的 `runtime` 中查看；内置的 LLM 解析与 TMDB 补全用它驱动管理台「AI 解析」「TMDB 元数据」的连接状态。

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
