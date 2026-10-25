"""内核契约层：整套系统的骨架，新功能往三类扩展点上填，不改内核。

- ``TaskState``        任务状态机（接收 → 识别 → 审批 → 转存 → 整理，以及待人工、失败）
- ``ApprovalMode``     两种确认方式：人工确认、AI 处理
- ``PipelineContext``  贯穿全程的上下文，各阶段/插件读写它
- ``SourceProvider`` / ``ParserProvider`` / ``DecisionProvider``
  ``StorageProvider`` / ``NotifyProvider``   —— A 类：能力槽（可替换）
- ``Hook``             —— B 类：钩子（前后插入、可改数据、可拦截）
- ``EventListener``    —— C 类：事件（旁路响应，失败不影响主流程）
- 异常：``TransferUncertain`` / ``TransferRejected`` / ``NeedsManual`` / ``ShareUnavailable`` /
  ``AlreadyStored`` / ``StorageBusy``，Provider 用它们告诉内核该怎么处理
"""

from __future__ import annotations

import enum
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


# --------------------------------------------------------------------------- #
# 状态机 & 枚举
# --------------------------------------------------------------------------- #
class TaskState(str, enum.Enum):
    """任务状态机。任何时刻任务状态都落在库里 —— 可查(可观测) / 可恢复(重启) / 可干预(人工)。

    流转（详见 docs/architecture.md 的状态机图）::

        received → parsed → ┬ pending_approval ─(批准)→ queued
                            │                  └(忽略)→ skipped
                            ├ queued (auto：AI 处理，直接入队)
                            ├ rejected（广告 / 非影视）
                            └ skipped（不收、去重命中、不再接收的作品）
        queued → saving → stored → organizing → done
                   └ need_manual    └ need_manual
                 (结果未知)          (整理失败)
        准备或解析失败 → failed（按记录的阶段重试）
    """

    RECEIVED = "received"                    # 已落库，尚未解析
    PARSED = "parsed"                        # AI 解析完成
    PENDING_APPROVAL = "pending_approval"    # 待人工确认（manual 模式；可跨重启）
    QUEUED = "queued"                        # 待转存
    REJECTED = "rejected"                    # 判为广告 / 非影视
    SKIPPED = "skipped"                      # 你忽略 / 不收 / 去重命中
    SAVING = "saving"                        # 转存准备、发送与核验
    STORED = "stored"                        # 转存回执已落库，尚未完成整理
    ORGANIZING = "organizing"                # 整理计划已落库，执行或恢复整理
    NEED_MANUAL = "need_manual"              # 转存结果待核验或整理需人工处理
    DONE = "done"                            # 已转存并读回验证
    FAILED = "failed"                        # 失败（可重试 / 告警）

    @property
    def is_terminal(self) -> bool:
        return self in (TaskState.DONE, TaskState.REJECTED, TaskState.SKIPPED)


class ApprovalMode(str, enum.Enum):
    MANUAL = "manual"  # 人工确认：每条符合要求的资源都等你确认后才转存
    AUTO = "auto"      # AI 处理：识别与规则都通过就直接转存，拿不准的仍交给你

    @classmethod
    def parse(cls, value: str | None) -> "ApprovalMode":
        try:
            return cls(str(value))
        except ValueError:
            return cls.MANUAL  # 拿不准时默认最保守


class Decision(str, enum.Enum):
    """决策层输出：这条要怎么走。"""
    SAVE = "save"      # 直接转存
    ASK = "ask"        # 需人工确认
    SKIP = "skip"      # 忽略（含广告 / 去重命中）


# 临时错误的自动退避重试次数；用尽后任务停在 failed，等人工处理
MAX_AUTO_RETRIES = 3


class StorageBusy(RuntimeError):
    """存储服务暂时不处理请求（如 115 触发风控后的冷却期，或上一个操作还没执行完），这次请求没有执行；until 之后再试。

    不消耗自动重试次数：任务回到原阶段，到点后继续。
    """

    def __init__(self, reason: str, until: float) -> None:
        super().__init__(reason)
        self.until = until


class TransferUncertain(RuntimeError):
    """网盘写请求的结果不能确认；只允许核验，不能自动再次发送。

    response 是网盘对这次写请求的回应（存储 Provider 拿得到时填写），内核写进任务轨迹供排查。
    """

    response: str | None = None


class NeedsManual(RuntimeError):
    """确定性校验不通过（如分享内集号无法识别），重试不会改变结果，需人工修正。"""


class AlreadyStored(RuntimeError):
    """分享清单中的内容已全部入库；按去重命中处理。"""


class ShareUnavailable(NeedsManual):
    """分享已不可用（过期、取消、违规），无法转存；消息是 115 给出的原因。"""


class TransferRejected(RuntimeError):
    """115 明确拒绝了转存请求，且暂存目录为空：确定没有发生转存，可以安全地重新发送。

    permanent 表示分享本身已不可用，重试也不会成功。
    """

    def __init__(self, reason: str, *, permanent: bool = False) -> None:
        super().__init__(reason)
        self.permanent = permanent


# --------------------------------------------------------------------------- #
# 贯穿全程的上下文
# --------------------------------------------------------------------------- #
@dataclass
class RawMessage:
    """从任意消息源归一化后的原始消息。"""
    source: str                       # 来源标识，如 "@some_bot"
    msg_id: str                       # 该来源内的消息唯一 id（消息级去重键）
    text: str                         # 正文
    urls: list[str] = field(default_factory=list)  # 从 entity/正文抽到的超链接真实 URL
    received_at: float = field(default_factory=time.time)
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class ParsedResource:
    """AI 感知层输出的结构化结果。字段尽量与媒体库规范对齐。"""
    is_media: bool = False            # 是不是影视资源
    is_ad: bool = False               # 是不是广告 / 噪声
    confidence: float = 0.0           # 置信度 [0,1]
    media_type: str | None = None     # movie | tv | anime | ...
    title: str | None = None
    year: int | None = None
    season: int | None = None
    episodes: list[int] = field(default_factory=list)  # 本条覆盖的集数（追更判断用）
    genres: list[str] = field(default_factory=list)
    genre_ids: list[int] = field(default_factory=list)          # TMDB 类型 ID，如 16=动画
    origin_country: list[str] = field(default_factory=list)     # 两位国家/地区代码
    resolution: str | None = None
    size: str | None = None
    # 发布版本信息：字幕、音轨、HDR、片源、编码、打包形式（见 core/release.py），同集多版本时据此择优
    release: dict[str, Any] = field(default_factory=dict)
    share_code: str | None = None     # 115 分享码（资源级去重键）
    receive_code: str | None = None   # 访问码
    reason: str | None = None         # AI 判定理由（便于回查）
    raw_fields: dict[str, Any] = field(default_factory=dict)


@dataclass
class PipelineContext:
    """一条消息从进入到存完，携带这一个对象流经所有阶段和插件。

    约定：插件的私有数据写进 ``meta[<plugin_name>]``，做命名空间隔离，互不干扰。
    """

    raw: RawMessage
    task_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    state: TaskState = TaskState.RECEIVED

    parsed: ParsedResource | None = None
    decision: Decision | None = None

    save_dir_alias: str | None = None   # 决策/路由选定的目录别名
    save_dir_cid: str | None = None      # 解析成 115 目录 cid
    result: dict[str, Any] | None = None  # 转存回执（含验证结果）

    # 来源在配置里对应的规则（审批模式 / 过滤 / 目标目录…）
    source_rule: dict[str, Any] = field(default_factory=dict)
    # 插件私有命名空间
    meta: dict[str, Any] = field(default_factory=dict)
    # 供追溯：一路走过的关键事件
    trail: list[str] = field(default_factory=list)

    def log(self, msg: str) -> None:
        self.trail.append(f"{time.strftime('%H:%M:%S')} {msg}")

    def ns(self, plugin: str) -> dict[str, Any]:
        """取某插件的私有命名空间字典。"""
        return self.meta.setdefault(plugin, {})

    def episode_pairs(self) -> list[tuple[int, int]]:
        """本任务涉及的（季, 集）。有转存清单时以清单为准（可跨多季），否则按识别结果，未写季视为第 1 季。"""
        pairs = (self.meta.get("storage", {}).get("manifest") or {}).get("pairs")
        if pairs:
            return [(int(season), int(ep)) for season, ep in pairs]
        p = self.parsed
        return [(p.season if p.season is not None else 1, ep) for ep in p.episodes] if p else []


# --------------------------------------------------------------------------- #
# A 类：能力槽 Provider（可替换）
# --------------------------------------------------------------------------- #
@runtime_checkable
class SourceProvider(Protocol):
    """消息源：监听并产出 RawMessage。"""
    name: str

    async def listen(self, emit) -> None:
        """开始监听；每收到一条消息就 ``await emit(raw_message)``。长驻协程。"""
        ...


@runtime_checkable
class ParserProvider(Protocol):
    """解析器：把 RawMessage 理解成 ParsedResource（默认实现 = LLM）。"""
    name: str

    async def parse(self, ctx: PipelineContext) -> ParsedResource:
        ...


@runtime_checkable
class DecisionProvider(Protocol):
    """决策器：结合审批模式与偏好，判定 save / ask / skip。"""
    name: str

    async def decide(self, ctx: PipelineContext) -> Decision:
        ...


@runtime_checkable
class StorageProvider(Protocol):
    """网盘执行器：确定性代码，负责真正转存 + 读回验证。"""
    name: str

    async def prepare(self, ctx: PipelineContext) -> dict[str, Any]:
        """建立本任务独占的暂存目录并返回文件清单；不执行转存。"""
        ...

    async def save(self, ctx: PipelineContext) -> dict[str, Any]:
        """按照已持久化的 storage.manifest 执行一次转存并核验完整清单。"""
        ...

    async def reconcile(self, ctx: PipelineContext) -> dict[str, Any]:
        """只读核验中断或结果未知的转存，绝不能再次发送转存请求。"""
        ...

    async def abandon(self, ctx: PipelineContext) -> None:
        """放弃结果不明的转存：删除暂存目录（连同已到的文件）并读回确认；可以重复执行。"""
        ...

    # 可选：
    # - ``discard(ctx)``：清理 prepare 建立、但因去重而未使用的空暂存目录；
    # - ``reconcile_partial(ctx)``：只读核验，按已经到了的文件出回执（人工选「按已到的文件继续」）；
    # - ``ready() -> bool``：凭证是否就绪；``dir_path(cid)``：目录在网盘里的路径（管理台显示真实去向）；
    # - ``library()``：整理用的网盘接口（iterdir / get_attr / mkdir / rename / move / delete，ID 为 int）；
    # - ``inspect_share(parsed, describe=...)``：只读查询分享是否可用、有哪些季集（share_probe 用）；
    # - ``browse(cid, offset, limit)``：列出子目录（管理台选目录用）。


@runtime_checkable
class NotifyProvider(Protocol):
    """通知 / 转发渠道。"""
    name: str

    async def notify(self, ctx: PipelineContext, event: str) -> None:
        ...


# --------------------------------------------------------------------------- #
# B 类：钩子 Hook（前后插入、可改数据、可拦截）
# --------------------------------------------------------------------------- #
@runtime_checkable
class Hook(Protocol):
    """在某个阶段前后插入的中间件。

    - ``stage``：挂载点，如 ``after_parse`` / ``before_save`` / ``after_save``
    - ``order``：同一挂载点内的执行顺序，小的先执行
    - ``run``：返回 ``False`` 可中止后续（拦截）；抛异常会被隔离（不拖垮主流程）
    """
    stage: str
    order: int

    async def run(self, ctx: PipelineContext) -> bool:
        ...


# --------------------------------------------------------------------------- #
# C 类：事件 EventListener（旁路，失败不影响主流程）
# --------------------------------------------------------------------------- #
@runtime_checkable
class EventListener(Protocol):
    """订阅某个事件，异步响应。慢或失败都不影响主链路。"""
    event: str

    async def handle(self, ctx: PipelineContext) -> None:
        ...


# 已知的挂载点 / 事件名，集中声明便于校验与查阅
HOOK_STAGES = (
    "before_parse",
    "after_parse",
    "before_save",
    "after_save",
)

EVENTS = (
    "on_message",       # 收到新消息
    "on_identified",    # 识别出资源
    "on_rejected",      # 判为广告 / 噪声
    "on_saved",         # 转存完成
    "on_failed",        # 失败
    "on_daily",         # 每日一次的系统事件（ctx.meta["system"] 带汇总数据），供日报/巡检类扩展订阅
)
