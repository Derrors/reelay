# 插件开发指南

Reelay 的主流程（监听 → 识别 → 转存整理）是固定骨架，所有可变能力都挂在三类扩展点上。加功能就是写一个插件目录，不用改内核。

两个可以直接运行的示例：

- [`examples/plugins/webhook_notify/`](../examples/plugins/webhook_notify/__init__.py)：Webhook 通知渠道（Provider）
- [`examples/plugins/quality_gate/`](../examples/plugins/quality_gate/__init__.py)：拦截 CAM/TS 等低画质资源（Hook）

## 目录结构

每个插件一个目录，插件代码写在 `__init__.py`，插件用到的其他文件（规则、模板、默认数据等）都放在同一个目录里，用 `Path(__file__).with_name(...)` 读取。内置插件（`reelay/plugins/`）与外部插件（`data/plugins/`）组织方式相同：

```
plugins/
├── organizer/          # 媒体库整理
│   ├── __init__.py     # 插件代码
│   ├── settings.py     # 默认设置与校验（内置插件）
│   ├── naming.py       # 插件内部模块
│   ├── category.py
│   └── category.yaml   # 二级分类规则
└── my_plugin/
    ├── __init__.py
    └── rules.yaml
```

插件的设置也属于插件：外部插件用 `ui_fields`（带默认值）与 `validate_config` 声明（见下文「在管理台展示与校验配置」）；内置插件还可以在目录里放 `settings.py`，提供 `DEFAULTS` 与 `normalize(section)`，配置读取与保存时由内核按目录调用，默认值会写进配置供管理台编辑。

内置插件目录里的 `*.yaml` 随安装包一起发布（`pyproject.toml` 的 `package-data`）；用其他后缀时把它加进去。

## 选哪类扩展点

| 你想做的事 | 扩展点 | 例子 |
| --- | --- | --- |
| 同一件事换一种实现 | **Provider**（能力槽） | 新消息源、新通知渠道、另一种解析器或网盘 |
| 在某个阶段前后插入处理，可能改数据或拦截 | **Hook**（钩子） | 补元数据、二次过滤、选目录、整理 |
| 某件事发生后旁路做点什么，不能挡主流程 | **Event**（事件） | 统计、日报、写外部索引、审计 |

## 加载方式

- **插件目录**：把整个目录放进 `data/plugins/`（Docker 部署把 `deploy/plugins` 挂载到 `/app/data/plugins`），重启后自动发现；只有一个文件的简单插件也可以直接放 `*.py`。
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

装饰器只记录类，`discover()` 时用无参构造函数实例化。每个扩展需要一个全局唯一的 `name`，它同时是 Provider 的引用名和配置里 `plugins.<name>` 的键：开关是 `plugins.<name>.enabled`，扩展自己的设置也放在 `plugins.<name>` 下。

## 贯穿全程的 PipelineContext

一条消息对应一个 `PipelineContext`（`reelay/core/contracts.py`），每个阶段从数据库快照重建：

| 属性 | 含义 |
| --- | --- |
| `raw` | 原始消息：`source`、`msg_id`、`text`、`urls`（含超链接真实 URL） |
| `parsed` | 识别结果 `ParsedResource`：标题、年份、类型、季集、类型 ID、国家、版本信息（`release`：字幕、音轨、HDR、片源等，见 `reelay/core/release.py`）、分享码等 |
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
    order = 60              # 同一阶段内从小到大执行；内置：organizer 选目录 10（before_save）与整理 20（after_save），tmdb_enrich 50，share_probe 60

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

`after_save` 运行时文件已经转存完成。只适用于某个存储 Provider 的整理类 Hook 应声明 `storage_provider = "p115"`，内核会拒绝把它用在其他 Provider 转存的任务上。只做附加产物（如生成媒体服务器索引文件）的 Hook 应自己捕获异常并返回 `True`，不要影响任务结果。

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
- 一个类只能订阅一个事件；需要订阅多个事件时写多个类并使用相同的 `name`（共用一个开关），参考 `reelay/plugins/episode_tracker/`。

## Provider 契约

| ptype | 需要实现 | 可选 |
| --- | --- | --- |
| `source` | `async listen(emit)`：长驻协程，每条消息 `await emit(RawMessage(...))` | `configure(targets)`、`bind_watermark(lookup)`、`health()`、`async close()` |
| `parser` | `async parse(ctx) -> ParsedResource` | — |
| `decision` | `async decide(ctx) -> Decision` | — |
| `storage` | `prepare` / `save` / `reconcile`（只读核验，绝不能重发写请求） | `discard(ctx)` |
| `notify` | `async notify(ctx, event)`；event 为 identified / pending_approval / saved / transfer_uncertain / organize_failed / failed | `async notice(text)`：系统通知（告警、汇总、日报） |

Provider 由配置选择：`providers.source`、`providers.notify`，以及 `defaults`/`sources[]` 中的 `parser`、`decision`、`storage`。

内置的三个 `decision` 逐层叠加：`default` 只按规则；`subscription`（默认）在其上让订阅剧集的新集自动放行；`llm` 再按自然语言偏好筛选。自定义决策器想保留订阅放行，可以继承 `SubscriptionDecision`。无论决策器返回什么，LLM 降级的识别结果都只能转人工。

## 调用 AI

扩展需要调用 AI 时用 `reelay/core/ai.py`，与内置能力共用客户端配置、退避重试与调用记录：

```python
from reelay.core import ai

class MyListener:
    def __init__(self):
        self._client, self._model = ai.make_client()   # 未配置时为 None，测试可直接替换 _client

    async def handle(self, ctx):
        if self._client is None:
            return                                     # 按规则降级
        try:
            result, _ = await ai.complete_json(
                self._client, model=self._model, purpose="my_plugin", task_id=ctx.task_id,
                messages=[...], schema={...}, name="my_output", validate=my_validate)
        except ai.AIError:
            return                                     # 拒答、非法输出、网络错误：按规则降级
```

- `validate` 必须做本地严格校验并在不合格时抛 `ValueError`（非法输出会重试一次）；
- 每次请求记入调用记录（`purpose` 会显示在管理台「统计 → AI 调用」）；
- 送给 AI 的消息正文先用 `ai.scrub()` 去掉链接与访问码；
- AI 的输出只作提议：涉及写操作的决定交给规则校验或用户确认。

## 元数据补全约定

识别完成后，内置的 `tmdb_enrich`（`after_parse`，order 50）按标题与年份匹配 TMDB 条目并回填元数据。TMDB 的 ID 体系是 Reelay 的数据标准：类型 ID 就是 TMDB 类型 ID，命名中的 `[tmdbid=…]` 供 Jellyfin / Emby 刮削，海报按 TMDB 图片路径显示。下面的字段由内核和其他扩展直接读取；接入其他数据源（如豆瓣、Bangumi）时，请把结果对应到 TMDB 条目并写入同样的字段。

写入 `ctx.parsed`：

| 字段 | 内容 | 谁在读 |
| --- | --- | --- |
| `title` / `year` | 规范标题与上映 / 首播年份 | 去重键（`片名\|S01\|E02`，不同来源的同一部作品靠它合并）、整理路径、缺集追更 |
| `genre_ids` | TMDB 类型 ID 整数列表，整体替换 AI 的值 | 二级分类（`plugins/organizer/category.yaml`，如 16 = 动画） |
| `origin_country` | 两位大写国家/地区代码；数据源没有时保留 AI 的值，不要清空 | 二级分类 |
| `raw_fields["tmdbid"]` | TMDB 条目 ID（整数） | 命名模板变量 `tmdbid`（默认模板写入 `[tmdbid=…]`）、缺集追更查询已播集数 |
| `raw_fields["poster_path"]` | TMDB 图片路径，如 `/abc.jpg`，不是完整 URL | 管理台海报（拼接 `https://image.tmdb.org/t/p/<尺寸>`）；没有时显示文字封面 |

写入 `ctx.meta["tmdb"]`：确定性置信度在全部 `after_parse` Hook 之后计算，按这里的结果判断（`reelay/core/confidence.py`）。

| 情况 | 写入 | 对置信度的影响 |
| --- | --- | --- |
| 匹配成功 | `matched: True`、`tmdbid` | 计为通过的信号 |
| 查询成功但没有可信匹配 | `matched: False` | 封顶 0.45，标记「TMDB 未匹配」，转人工确认 |
| 没有查询（未配置凭证、模拟模式） | `matched: False`、`note: 原因` | 中立，不影响分数 |
| 查询出错（网络、限流等） | `matched: False`、`error: 异常类型名` | 中立，不影响分数 |

`candidates`、`kind`、`score` 等其余字段只供任务详情排查，内核不读取。

已有任务的补跑：`after_parse` Hook 可以实现 `needs_refresh(ctx) -> bool`。主进程启动时，内核对待审批和已入库任务逐条询问，返回 True 的补跑该 Hook（不重新调用 LLM）：待审批任务整体采用新结果并重算置信度；已入库任务的片名、路径与去重键已经落地，只在片名不变时补上 `poster_path`。补跑只更新快照，不改任务的更新时间，审批超时与入库统计不受影响。`tmdb_enrich` 对配置 key 之前识别（有 `note`）或上次查询出错（有 `error`）的任务返回 True，所以配好 TMDB 重启后，已有任务也会补上规范元数据和海报。

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

section = runtime.plugin_config("my_plugin")           # 本扩展的设置 plugins.my_plugin（不含 enabled）
language = runtime.get("tmdb.language")                # 点路径读取系统配置
runtime.services().notify("📦 备份完成", key="my_plugin")  # 发系统通知到所有通知渠道（持久化投递）
state = runtime.services().state("my_plugin")          # 按扩展隔离的持久化 KV，值需可 JSON 序列化
state.set("last_run", "2026-10-19")
runtime.observe("my_api", "failed", "TimeoutError")    # 记录一次外部调用结果（connected / failed）
```

`runtime.observe` 记录的是最近一次真实调用的结果，不会因一段时间没有调用而过期，可在 `GET /api/health` 的 `runtime` 中查看；内置的 LLM 解析与 TMDB 补全用它驱动管理台「AI 解析」「TMDB 元数据」的连接状态。

`runtime.get` / `runtime.plugin_config` 读到的是任务接收时的配置快照，配置变更只影响新任务；不在任务作用域里时读进程当前的有效配置。`services()` 在内核调用 Hook、Event、通知渠道，以及解析/决策/存储 Provider 的方法时可用（包括其中的 `asyncio.to_thread`）；消息源的 `listen` 是长驻协程，不在任务作用域内。

同步阻塞调用（HTTP、SDK）请放进 `asyncio.to_thread`，不要阻塞事件循环。

## 在管理台展示与校验配置

```python
class MyHook:
    name = "my_hook"
    ui_label = "我的扩展"
    ui_description = "一句话说明，显示在插件列表"
    ui_fields = [   # 自动生成配置表单，并在保存时按类型校验；path 相对 plugins.my_hook
        {"path": "threshold", "label": "阈值", "type": "number", "min": 0, "max": 1, "default": 0.5},
        {"path": "mode", "label": "模式", "type": "string", "choices": ["a", "b"]},
        {"path": "strict", "label": "严格模式", "type": "boolean"},
    ]
    default_enabled = False   # 未设置 plugins.my_hook.enabled 时默认关闭（适合有副作用的扩展）

    def validate_config(self, cfg: dict) -> None:
        """扩展启用时，配置保存与启动都会调用；抛 ValueError 拒绝无效配置。"""
        section = runtime.plugin_config(self.name, cfg)
```

对应的配置文件：

```yaml
plugins:
  my_hook:
    enabled: true
    threshold: 0.7
    mode: a
```

`default` 是未填写时扩展实际使用的值，只用于在表单里提示。数组、对象等复杂配置在 `validate_config` 中自行校验，用户通过配置文件维护。扩展不要读环境变量：需要的地址、开关写成 `ui_fields`；需要的文件放在 `reelay.core.paths.DATA` 下。

### 插件动作

管理台需要向插件要数据或让插件算点什么（如预览命名模板、展示内置规则）时，插件把普通方法列进 `ui_actions`，内核统一提供 `POST /api/extensions/<插件名>/actions/<动作名>`：

```python
class MyHook:
    ui_actions = ("preview",)

    def preview(self, payload: dict) -> dict:
        """payload 是请求体（JSON 对象，可以为空）；返回 JSON 对象。"""
        if not payload.get("text"):
            raise ValueError("text 不能为空")   # ValueError / TypeError → 422，消息原样显示给用户
        return {"result": payload["text"].upper()}
```

- 只有列进 `ui_actions` 的方法可以调用，插件的其他方法从接口上取不到；插件未安装或没有这个动作时返回 404。
- 鉴权由内核完成（与其他管理接口相同）；方法里可以用 `runtime.get` 读当前配置。
- 插件不需要依赖 FastAPI。方法在 Web 进程的事件循环里执行，要快；耗时的写成 `async def`。
- 插件目录里会列出每个插件的 `actions`。

内置插件可以有专用的界面，放在前端的 `web/src/plugins/<插件名>/`：设置编辑器由后端声明 `ui_config_type`，并登记到 `web/src/plugins/index.ts` 的 `PLUGIN_EDITORS`；只服务于这个插件的页面（如媒体库整理的「整理预览」）也放在这里，由路由引用；组件需要后端数据时调用 `api.extensionAction(插件名, 动作名, payload)`。前端是整体构建后发布的，外部插件没有专用编辑器，用 `ui_fields` 生成表单。

## 测试

用 `discover(plugin_dirs=[...])` 加载你的扩展并直接调用，参考 [`tests/test_examples.py`](../tests/test_examples.py)：

```python
registry = discover(plugin_dirs=["path/to/plugins"])
assert registry.load_errors == []
hook = next(h for h in registry.hooks["after_parse"] if h.name == "my_hook")
with runtime.scope({"plugins": {"my_hook": {"threshold": 0.5}}}):
    assert await hook.run(ctx) is True
```
