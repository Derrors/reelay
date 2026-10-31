"""管线编排 —— 内核最核心的部分。

把一条 ``PipelineContext`` 按「监听 → 识别 → 转存整理」推到底，沿途：

- 在每个挂载点跑 Hook（**失败隔离**：单个 Hook 抛异常只记日志，不拖垮主流程；
  返回 ``False`` 则中止后续、当前任务按 skip 处理）
- 在关键时刻发 Event（**旁路**：监听器异常被吞掉，绝不影响主链路）
- 每次状态变更都落库（可观测 / 可恢复 / 可干预）

真正的耗时执行（解析、转存）由 Provider 完成；这里只负责编排与状态机推进。
"""

from __future__ import annotations

import asyncio
import copy
import inspect
import json
import logging
import math
import time
from pathlib import Path
from typing import Any

from .contracts import (
    AlreadyStored,
    Decision,
    NeedsManual,
    PipelineContext,
    RawMessage,
    ShareUnavailable,
    StorageBusy,
    TaskState,
    TransferRejected,
    TransferUncertain,
)
from .registry import Registry
from .store import Store, _load_ctx
from .config import config_revision, find_source, load_config, merge_config, normalize_config
from .media import pairs_label, summarize_pairs
from . import confidence, ignored, messages, runtime, works

log = logging.getLogger("reelay.pipeline")
LATE_ALERT_SEC = 120  # 告警晚于发生这么久才送达时，注明发生的时间


class Pipeline:
    """持久化状态在 Store 中，进程内按目标目录串行整理。"""

    def __init__(self, registry: Registry, store: Store, config: dict[str, Any]):
        self.reg = registry
        self.store = store
        self.cfg = normalize_config(config)
        runtime.set_process_config(self.cfg)
        self.revision = config_revision(self.cfg)
        self.store.put_config_snapshot(self.revision, self.cfg)
        self.config_path: Path | None = None
        self.config_error: str | None = None
        self.save_dirs: dict[str, str] = self.cfg["save_dirs"]
        self._organize_locks: dict[str, asyncio.Lock] = {}
        self.services = runtime.Services(store, self.notify_targets, registry)
        # 启动补拉期间推迟日报（见 hold_daily）；消息源完成补拉后由主进程置为 True
        self.sources_ready = True
        self._daily_hold_until: float | None = None

    def notify_targets(self) -> list[str]:
        return list(self.cfg.get("providers", {}).get("notify", ["console"]))

    def scope(self, ctx_or_cfg=None):
        """内核服务与配置的作用域：传任务时用任务接收时的配置，省略时用当前配置。"""
        cfg = self.task_config(ctx_or_cfg) if isinstance(ctx_or_cfg, PipelineContext) else (ctx_or_cfg or self.cfg)
        return runtime.scope(cfg, self.services)

    def query(self, capability: str, *args, **kwargs):
        """调用某个已启用扩展声明的能力（见 Registry.capability），不在任务作用域里时也可以用。"""
        with self.scope():
            return self.services.query(capability, *args, **kwargs)

    def apply_config(self, config: dict) -> None:
        cfg = normalize_config(config)
        self.reg.validate_config(cfg)
        self.cfg, self.save_dirs = cfg, cfg["save_dirs"]
        runtime.set_process_config(cfg)
        self.revision = config_revision(cfg)
        self.store.put_config_snapshot(self.revision, cfg)
        self.config_error = None

    def reload_config(self) -> bool:
        if self.config_path is None:
            return False
        try:
            # 删除运行配置不等于授权恢复到内置 CID=0。
            if not self.config_path.exists():
                raise ValueError("运行配置文件不存在")
            cfg = load_config(self.config_path)
            revision = config_revision(cfg)
            if revision == self.revision:
                self.config_error = None
                return False
            self.apply_config(cfg)
            return True
        except Exception as exc:  # noqa: BLE001 - 原因交给管理台显示，用户据此修改文件
            self.config_error = f"配置文件 {self.config_path} 无效，继续使用上一有效版本：{str(exc)[:300]}"
            return False

    def task_config(self, ctx: PipelineContext) -> dict:
        """任务接收时的配置（按快照里的配置版本号取）；没有记录时用当前配置。"""
        revision = ctx.meta.get("runtime", {}).get("revision")
        if revision is None or revision == self.revision:
            return self.cfg
        return self.store.config_snapshot(revision) or self.cfg

    def stamp_config(self, ctx: PipelineContext) -> None:
        """任务从此按当前配置执行（接收、重新识别、重新整理时）。"""
        ctx.ns("runtime")["revision"] = self.revision

    async def refresh_metadata(self) -> int:
        """补跑元数据补全（如配置 TMDB 之前识别的任务），不重新调用 LLM；返回更新的任务数。

        由声明 ``needs_refresh(ctx)`` 的 after_parse Hook 决定哪些任务需要补跑。待审批任务整体
        采用新结果并重算置信度；已入库任务的片名、路径与去重键都已落地，只在片名不变时补海报。
        """
        refreshed = 0
        for state in (TaskState.PENDING_APPROVAL, TaskState.DONE):
            for row in self.store.list_by_state(state, 100_000):
                if await self._refresh_task(row["task_id"], state, row["updated_at"]):
                    refreshed += 1
        return refreshed

    async def refresh_task(self, task_id: str) -> bool:
        """人工修正识别结果后为待审批任务补跑元数据补全：修正后的片名同样要经过 TMDB 等事实源核对。"""
        record = self.store.get_task_record(task_id)
        if not record or record["state"] != TaskState.PENDING_APPROVAL.value:
            return False
        return await self._refresh_task(task_id, TaskState.PENDING_APPROVAL, record["updated_at"])

    async def _refresh_task(self, task_id: str, state: TaskState, updated_at: float) -> bool:
        ctx = self.store.get_task(task_id)
        if not ctx or not ctx.parsed:
            return False
        with self.scope(ctx):
            hooks = [hook for hook in self.reg.hooks_for("after_parse", self.cfg)
                     if hasattr(hook, "needs_refresh") and hook.needs_refresh(ctx)]
        if not hooks:
            return False
        fresh = copy.deepcopy(ctx)
        fresh.log("补跑元数据补全（未重新调用 LLM）")
        for hook in hooks:
            try:
                with self.scope(fresh):
                    await hook.run(fresh)
            except Exception as exc:  # 与识别阶段一样失败隔离
                log.warning("补跑 %s 失败：%s", self.reg.extension_name(hook), exc)
        if state == TaskState.PENDING_APPROVAL:
            confidence.apply(fresh)
            fresh.ns("library")["preview"] = self.display_target(self.target_preview(fresh))
            ctx = fresh
        else:
            if fresh.parsed.title == ctx.parsed.title:
                for key in ("poster_path", "backdrop_path"):
                    if fresh.parsed.raw_fields.get(key):
                        ctx.parsed.raw_fields[key] = fresh.parsed.raw_fields[key]
            ctx.meta, ctx.trail = fresh.meta, fresh.trail
        return self.store.refresh_snapshot(ctx, updated_at=updated_at)

    def dir_paths(self) -> dict[str, str]:
        """各目录别名在网盘中的路径（Worker 读取后记录）；目录 CID 已改过的记录不用。"""
        saved = self.store.state_get("kernel", "save_dir_paths", {}) or {}
        return {alias: item["path"] for alias, item in saved.items()
                if str(self.cfg["save_dirs"].get(alias)) == item.get("cid")}

    def display_target(self, label: str) -> str:
        """把「别名/分类」换成网盘真实路径，如「剧集/国产剧集」→「/测试/剧集/国产剧集」；未知时原样返回。"""
        alias, _, rest = label.partition("/")
        root = self.dir_paths().get(alias)
        if not root:
            return label
        return f"{root.rstrip('/')}/{rest}" if rest else root

    def target_preview(self, ctx: PipelineContext) -> str:
        """不手动改目录时任务将存入的位置（含二级分类），供审批卡片与管理台展示。"""
        with self.scope(ctx):
            for hook in self.reg.hooks_for("before_save", self.task_config(ctx)):
                if hasattr(hook, "preview"):
                    return hook.preview(ctx)
        return ctx.save_dir_alias or ctx.source_rule.get("save_to") or "默认"

    # ------------------------------------------------------------------ #
    # Hook / Event 基础设施
    # ------------------------------------------------------------------ #
    async def run_hooks(self, stage: str, ctx: PipelineContext) -> bool:
        """跑某挂载点的所有 Hook。返回 False 表示被拦截。"""
        for hook in self.reg.hooks_for(stage, self.task_config(ctx)):
            try:
                if stage == "after_save" and getattr(hook, "requires_storage", None):
                    storage = self.reg.get_provider("storage", ctx.ns("storage").get("provider", "p115"))
                    missing = self.reg.missing_storage(hook, storage)
                    if missing:
                        raise ValueError(f"任务的存储 Provider 不提供 {missing}，无法整理")
                if stage == "after_save" and hasattr(hook, "prepare"):
                    with self.scope(ctx):
                        await hook.prepare(ctx)
                    self.store.save_task(ctx)  # 整理计划必须先落库
                with self.scope(ctx):
                    ok = await hook.run(ctx)
                if ok is False:
                    ctx.log(f"hook 拦截 @{stage}: {hook.__class__.__name__}")
                    if stage == "after_save" and ctx.result is not None:
                        ctx.result["verified"] = False
                    return False
            except StorageBusy:
                raise  # 网盘暂时拒绝一切请求：不是扩展出错，交给 Worker 到点再执行
            except Exception as exc:  # 失败隔离
                log.warning("hook 异常 @%s %s: %s（已隔离）", stage, hook.__class__.__name__, exc)
                ctx.log(f"hook 异常 @{stage}: {exc}")
                if stage == "before_save":
                    raise RuntimeError(f"转存前置检查失败：{exc}") from exc
                if stage == "after_save":
                    ctx.ns("organize")["error"] = str(exc)
                    if ctx.result is not None:
                        ctx.result["verified"] = False
                    return False
        return True

    def _stage(self, kind: str, event: str, target: str, ctx: PipelineContext) -> None:
        # 投递键在同一轮内去重：审批提醒按提醒次数区分；on_failed 事件按失败次数区分，自动重试后的
        # 每次失败都送到监听方（如失败解释要在重试用尽时通知）。通知渠道的「失败」消息每轮只发一次。
        task = ctx.meta.get("task", {})
        sequence = (task.get("approval_reminders", 0) if event == "pending_approval"
                    else task.get("failures", 0) if kind == "event" and event == "on_failed" else 0)
        generation = task.get("generation", 0)
        key = f"{generation}:{kind}:{event}:{target}:{sequence}"
        ctx.ns("deliveries")[key] = {"kind": kind, "event": event, "target": target}

    async def emit(self, event: str, ctx: PipelineContext, *, persist: bool = True) -> None:
        if event == "on_failed":
            ctx.ns("task")["failures"] = ctx.ns("task").get("failures", 0) + 1
        if event in ("on_failed", "on_saved"):
            # 任务结果也写进运行日志：排查时不必去数据库里翻轨迹；原因是轨迹的最后一行（去掉时间）
            title = (ctx.parsed.title if ctx.parsed else None) or "未识别片名"
            reason = ctx.trail[-1][9:] if ctx.trail else ""
            if event == "on_saved":
                log.info("任务 %s《%s》已入库", ctx.task_id, title)
            else:
                log.warning("任务 %s《%s》%s：%s", ctx.task_id, title,
                            "待人工处理" if ctx.state == TaskState.NEED_MANUAL else "失败", reason[:200])
        for listener in self.reg.listeners_for(event, self.task_config(ctx)):
            self._stage("event", event, self.reg.extension_name(listener), ctx)
        if persist:
            self.store.save_task(ctx)

    async def dispatch_deliveries(self) -> int:
        rows = self.store.claim_deliveries()

        async def deliver(row: dict) -> None:
            try:
                if row["kind"] == "notice":
                    # 系统通知（告警、审批汇总、日报）不绑定任务；渠道不支持则跳过。
                    obj = self.reg.get_provider("notify", row["target"])
                    if not hasattr(obj, "notice"):
                        self.store.finish_delivery(row["id"], skipped=True)
                        return
                    notice = json.loads(row["snapshot"])
                    if row["event"] == "alert" and time.time() - row["created_at"] >= LATE_ALERT_SEC:
                        # 断线期间发不出、恢复后补发的告警：写明发生的时间，不当成新的故障
                        notice["text"] += (f"\n（{time.strftime('%m-%d %H:%M', time.localtime(row['created_at']))} "
                                           "发生，当时发不出，恢复后补发）")
                    accepts = inspect.signature(obj.notice).parameters
                    extra = {"buttons": notice["buttons"]} if notice.get("buttons") and "buttons" in accepts else {}
                    if not notice.get("html"):
                        callback = obj.notice(notice["text"], **extra)
                    elif "html" in accepts:
                        callback = obj.notice(notice["text"], html=True, **extra)
                    else:  # 渠道只收纯文字
                        callback = obj.notice(messages.plain(notice["text"]), **extra)
                    cfg = self.cfg
                else:
                    ctx = _load_ctx(json.loads(row["snapshot"]))
                    cfg = self.task_config(ctx)
                    # 已处理的审批不再推过时按钮；接收器看到的是独立事件快照。
                    if row["kind"] == "notify":
                        current = self.store.get_task(ctx.task_id)
                        stale_generation = (not current or current.meta.get("task", {}).get("generation", 0)
                                            != ctx.meta.get("task", {}).get("generation", 0))
                        stale_approval = row["event"] == "pending_approval" and current and current.state != TaskState.PENDING_APPROVAL
                        if stale_generation or stale_approval:
                            self.store.finish_delivery(row["id"], skipped=True)
                            return
                        obj = self.reg.get_provider("notify", row["target"])
                        callback = obj.notify(ctx, row["event"])
                    else:
                        obj = next(obj for obj in self.reg.listeners_for(row["event"])
                                   if self.reg.extension_name(obj) == row["target"])
                        callback = obj.handle(ctx)
                with self.scope(cfg):
                    await asyncio.wait_for(callback, timeout=30)
            except Exception as exc:
                category = type(exc).__name__
                seconds = getattr(exc, "seconds", None)
                delay = min(seconds, 86400) if type(seconds) in (int, float) and math.isfinite(seconds) and seconds > 0 else None
                self.store.finish_delivery(row["id"], error=category, retry_after=delay)
                log.warning("旁路投递失败 %s/%s，独立退避重试：%s", row["kind"], row["target"], category)
            else:
                self.store.finish_delivery(row["id"])

        await asyncio.gather(*(deliver(row) for row in rows))
        return len(rows)

    # ------------------------------------------------------------------ #
    # 阶段 1：快速接收（监听回调只去重+落库，不等待 AI）
    # ------------------------------------------------------------------ #
    async def accept(self, ctx: PipelineContext) -> PipelineContext | None:
        """快速接收新消息并落为 RECEIVED；返回 None 表示消息级重复。"""
        ctx.source_rule = self.rule_for(ctx.raw.source)
        self.stamp_config(ctx)
        await self.emit("on_message", ctx, persist=False)
        ctx.state = TaskState.RECEIVED
        ctx.log("消息已接收并落库")
        if not self.store.accept_task(ctx):
            return None
        return ctx

    # ------------------------------------------------------------------ #
    # 阶段 2：识别（由 Worker 消费 RECEIVED）
    # ------------------------------------------------------------------ #
    async def process_new(self, ctx: PipelineContext) -> None:
        """接收后立即解析（不经 Worker 队列）；dry-run 与单元测试使用。"""
        accepted = await self.accept(ctx)
        if accepted:
            await self.process_received(accepted)

    async def process_received(self, ctx: PipelineContext) -> None:
        """从 RECEIVED 推进到 rejected / pending_approval / queued。"""
        if ctx.state != TaskState.RECEIVED:
            return
        if not await self.run_hooks("before_parse", ctx):
            ctx.state = TaskState.SKIPPED
            self.store.save_task(ctx)
            return

        parser_name = ctx.source_rule.get(
            "parser", self.cfg.get("defaults", {}).get("parser", "llm")
        )
        parser = self.reg.get_provider("parser", parser_name)
        try:
            with self.scope(ctx):
                ctx.parsed = await parser.parse(ctx)
        except Exception as exc:
            ctx.state = TaskState.FAILED
            ctx.ns("task")["retry_from"] = TaskState.RECEIVED.value
            ctx.log(f"解析失败：{exc}")
            await self.emit("on_failed", ctx, persist=False)
            await self.notify_all(ctx, "failed", persist=False)
            self.store.save_task(ctx, error=str(exc))
            return

        ctx.state = TaskState.PARSED
        ctx.log(
            f"解析完成 is_media={ctx.parsed.is_media} is_ad={ctx.parsed.is_ad} "
            f"conf={ctx.parsed.confidence:.2f}"
        )
        self.store.save_task(ctx)

        if not await self.run_hooks("after_parse", ctx):
            ctx.state = TaskState.SKIPPED
            self.store.save_task(ctx)
            return

        # 决策只看确定性信号算出的置信度；TMDB 等补全 Hook 已在上面跑完。
        confidence.apply(ctx)
        blocked = ignored.match(self.store.ignored_works(), ctx.parsed, ctx.meta.get("tmdb", {}))
        if blocked:
            ctx.decision = Decision.SKIP
            ctx.log(f"你已设置不再接收《{blocked['title']}》，直接忽略")
        else:
            decider = self.reg.get_provider("decision",
                                            ctx.source_rule.get("decision", self.cfg["defaults"]["decision"]))
            with self.scope(ctx):
                ctx.decision = await decider.decide(ctx)
        if ctx.decision == Decision.SAVE and ctx.meta.get("parse", {}).get("fallback"):
            ctx.decision = Decision.ASK
        ctx.log(f"决策 = {ctx.decision.value}")

        if ctx.decision == Decision.SKIP:
            ctx.state = (
                TaskState.REJECTED
                if (ctx.parsed and ctx.parsed.is_ad)
                else TaskState.SKIPPED
            )
            await self.emit("on_rejected", ctx, persist=False)
            self.store.save_task(ctx)
            return

        if ctx.decision == Decision.ASK:
            ctx.state = TaskState.PENDING_APPROVAL
            # 审批卡片展示实际去向（网盘真实路径）
            ctx.ns("library")["preview"] = self.display_target(self.target_preview(ctx))
            ctx.log("转人工确认（审批态已落库，可跨重启）")
        else:
            ctx.state = TaskState.QUEUED
        await self.emit("on_identified", ctx, persist=False)
        await self.notify_all(ctx, "identified", persist=False)
        # 补拉的历史消息不逐条推送确认卡片：长时间停机后会一次补进上百条，刷屏且多已过时；
        # 它们照常进入管理台「待审批」，超时后由审批汇总提醒覆盖。
        if ctx.decision == Decision.ASK and not ctx.raw.extra.get("backfill"):
            await self.notify_all(ctx, "pending_approval", persist=False)
        self.store.save_task(ctx)

    # ------------------------------------------------------------------ #
    # 审批回调（控制 bot / 收藏夹点击后调用）
    # ------------------------------------------------------------------ #
    async def approve(self, task_id: str) -> bool:
        ctx = self.store.get_task(task_id)
        if not ctx or ctx.state != TaskState.PENDING_APPROVAL:
            return False
        return self.store.set_state(task_id, TaskState.QUEUED, expected=TaskState.PENDING_APPROVAL)

    async def reject(self, task_id: str) -> bool:
        ctx = self.store.get_task(task_id)
        if not ctx or ctx.state != TaskState.PENDING_APPROVAL:
            return False
        return self.store.set_state(task_id, TaskState.SKIPPED, expected=TaskState.PENDING_APPROVAL)

    # ------------------------------------------------------------------ #
    # 阶段 3：转存 + 整理（由 worker 从队列取出后调用）
    # ------------------------------------------------------------------ #
    def _resolve_directory(self, ctx: PipelineContext) -> None:
        alias = ctx.save_dir_alias or ctx.source_rule.get("save_to") \
            or self.cfg.get("defaults", {}).get("save_to", "默认")
        if alias not in self.save_dirs:
            raise ValueError(f"目标目录别名不存在：{alias}")
        cid = str(self.save_dirs[alias])
        if not cid.isdecimal():
            raise ValueError(f"目标目录 CID 无效：{alias}")
        ctx.save_dir_alias, ctx.save_dir_cid = alias, cid

    async def execute_save(self, ctx: PipelineContext) -> None:
        """转存和整理分别有持久化边界；未知写结果仅核验，绝不重发。"""
        if ctx.state in (TaskState.STORED, TaskState.ORGANIZING):
            await self._organize(ctx)
            return
        if ctx.state != TaskState.QUEUED:
            return
        ns = ctx.ns("storage")
        if ctx.ns("task").get("action") == "organize_only":
            self._resolve_directory(ctx)
            ctx.ns("task").pop("action")
            ctx.ns("task")["retry_from"] = TaskState.STORED.value
            ctx.state = TaskState.STORED
            self.store.save_task(ctx)
            await self._organize(ctx)
            return

        provider_name = ns.get("provider") or ctx.source_rule.get("storage", self.cfg["defaults"]["storage"])
        storage = self.reg.get_provider("storage", provider_name)
        ns["provider"] = provider_name
        if ns.get("abandon") and ns.get("phase") == "receiving":
            await self._abandon(ctx, storage)
            return
        if not ns.get("manifest"):
            if not await self.run_hooks("before_save", ctx):
                ctx.state = TaskState.SKIPPED
                self.store.save_task(ctx)
                return
            self._resolve_directory(ctx)
            ctx.state = TaskState.SAVING
            ns["phase"] = "preparing"
            # 资源键：115 分享码；只有电驴链接时按链接的哈希（见 ParsedResource.link_code）
            key = f"{storage.name}:{ctx.save_dir_cid}:{ctx.parsed.link_code()}"
            # 先按消息提示的集数占位，尽早串行化同一集的不同分享；清单确定后再按实际集数占位。
            if not self.store.claim_resource(ctx, key, content_keys=self._content_keys(storage.name, ctx)):
                ctx.state = TaskState.QUEUED
                self.store.save_task(ctx)  # 移到轮询末尾，避免等待资源的任务阻塞新任务
                return
            # 剧集分享链接常在追更中原地增加新集：剧集按集去重，其他按分享去重。
            if not self._episodic(ctx) and self.store.saved_share(
                    ctx.parsed.link_code(), ctx.save_dir_cid, provider_name):
                ctx.state = TaskState.SKIPPED
                ctx.log("执行前去重命中，分享已转存到该目录")
                self.store.save_task(ctx)
                return
            # work 是作品身份（TMDB ID、规范片名与年份）：同名的不同作品不会互相当成已入库
            ns["identity"] = {
                "provider": provider_name,
                "cid": ctx.save_dir_cid, "share_code": ctx.parsed.link_code(),
                "title": ctx.parsed.title, "season": ctx.parsed.season,
                "episodes": list(ctx.parsed.episodes), "work": works.of(ctx.parsed, ctx.meta.get("tmdb")),
            }
            ns["stored_episodes"] = (self.store.stored_episode_pairs(ns["identity"]["work"], ctx.save_dir_cid,
                                                                     provider_name) if ctx.parsed.title else [])
            try:
                with self.scope(ctx):
                    manifest = await storage.prepare(ctx)
            except AlreadyStored as exc:
                ctx.state = TaskState.SKIPPED
                ctx.log(f"执行前去重命中：{exc}")
                self.store.save_task(ctx)
                return
            except ShareUnavailable as exc:
                # 读不到分享就无法按清单去重；消息写明的集都已入库时，这条只是重复消息，不算失败
                declared = ctx.episode_pairs()
                stored = {tuple(pair) for pair in ns.get("stored_episodes") or []}
                if self._episodic(ctx) and declared and stored.issuperset(declared):
                    ctx.state = TaskState.SKIPPED
                    ctx.log(f"执行前去重命中：分享不可用（{exc}），但消息写明的 {pairs_label(declared)} 均已入库")
                    self.store.save_task(ctx)
                    return
                await self._fail(ctx, f"分享不可用，无法转存：{exc}", retry_from=TaskState.QUEUED, auto_retry=False)
                return
            except NeedsManual as exc:
                # 确定性校验失败：重试不会改变结果，不自动重试，修正后由人工重试。
                await self._fail(ctx, f"转存前校验未通过，需人工处理：{exc}", retry_from=TaskState.QUEUED,
                                 auto_retry=False)
                return
            except StorageBusy:
                raise
            except Exception as exc:
                await self._fail(ctx, f"转存准备失败：{exc}", retry_from=TaskState.QUEUED)
                return
            finally:
                ns.pop("stored_episodes", None)
            self._adopt_manifest(ctx, manifest)
            ns["manifest"] = manifest
            ns["phase"] = "ready"
            ns["content_keys"] = self._content_keys(storage.name, ctx)
            self.store.save_task(ctx)
            if not self.store.claim_resource(ctx, key, content_keys=ns["content_keys"]):
                # 释放全部占位再排队，避免两个任务各持一集、互相等待。
                self.store.release_resource(ctx.task_id)
                ctx.state = TaskState.QUEUED
                ctx.log("分享中的部分集正由其他任务转存，稍后重新核对")
                self.store.save_task(ctx)
                return
        else:
            ctx.state = TaskState.SAVING
            # 占位键按当前格式重新生成（集数已按清单确定），升级前开始的任务与新任务互相可见
            if ns.get("manifest"):
                ns["content_keys"] = self._content_keys(storage.name, ctx)
            if not self.store.claim_resource(ctx, ns["resource_key"],
                                             content_keys=ns.get("content_keys") or ns.get("claim_keys")):
                ctx.state = TaskState.QUEUED
                self.store.save_task(ctx)
                return
            # 尚未发送：等待期间其他任务可能已入库同一分享或部分集，发送前重新核对。
            if ns["phase"] == "ready" and not await self._recheck_manifest(ctx, storage, provider_name):
                return

        auto_recheck = ns.pop("auto_recheck_run", False)  # 这一轮是 Worker 的后台自动复查
        sending = ns["phase"] == "ready"
        try:
            if sending:
                ns["phase"] = "receiving"
                self.store.save_task(ctx)  # 必须在远端写请求之前提交
                try:
                    with self.scope(ctx):
                        receipt = await storage.save(ctx)
                except (TransferRejected, StorageBusy):
                    raise
                except Exception as error:
                    ctx.log(f"转存请求没有确认结果（{type(error).__name__}），改为只读核验，不重发")
                    if isinstance(error, TransferUncertain) and error.response:
                        ctx.log(f"网盘对这次转存请求的回应：{error.response}")
                    with self.scope(ctx):
                        receipt = await storage.reconcile(ctx)
            elif ns["phase"] == "receiving" and ns.get("accept_partial") and hasattr(storage, "reconcile_partial"):
                with self.scope(ctx):
                    receipt = await storage.reconcile_partial(ctx)
                self._accept_partial(ctx, receipt)
            elif ns["phase"] == "receiving":
                with self.scope(ctx):
                    receipt = await storage.reconcile(ctx)
            else:
                raise TransferUncertain("转存检查点状态异常，需人工核验")
            if receipt.get("storage_verified") is not True or not receipt.get("landed"):
                raise TransferUncertain("转存回执未确认全部文件落地")
        except StorageBusy:
            if sending:
                ns["phase"] = "ready"  # save() 抛 StorageBusy 表示转存请求没有发出，回到发送前
            raise
        except TransferRejected as rejected:
            await self._transfer_rejected(ctx, storage, rejected)
            return
        except Exception as exc:
            ctx.state = TaskState.NEED_MANUAL
            # 只到了一部分（到的都对得上）：记下进度，管理台可选「按已到的文件继续」
            partial = getattr(exc, "partial", None)
            if partial:
                ns["partial"] = partial
            else:
                ns.pop("partial", None)
            ctx.log(f"转存结果待核验，不自动重发：{exc}")
            if not self.auto_rechecks(ctx):
                await self.notify_uncertain(ctx)
            elif auto_recheck:
                log.info("任务 %s 自动复查仍未核验通过：%s", ctx.task_id, exc)
            else:
                # 115 落地慢时多数几分钟内就能复查通过，这期间不提醒；24 小时仍未通过时 Worker 再通知
                log.info("任务 %s《%s》转存待核验，后台自动复查，24 小时内仍未通过再通知：%s",
                         ctx.task_id, ctx.parsed.title or "未识别片名", exc)
            self.store.save_task(ctx, error=str(exc))
            return  # 保留资源占位，防止另一条消息绕过核验再次转存

        ctx.result = receipt
        ns["landed"] = receipt["landed"]
        ns["phase"] = "stored"
        ctx.state = TaskState.STORED
        ctx.ns("task")["retry_from"] = TaskState.STORED.value
        ctx.log("全部转存文件已核验，回执持久化后进入整理")
        self.store.finish_transfer(ctx)
        await self._organize(ctx)

    def _accept_partial(self, ctx: PipelineContext, receipt: dict) -> None:
        """按已到的文件继续：清单只留到了的文件，集数、去重身份随之缩小，没到的集不记为已入库。"""
        ns = ctx.ns("storage")
        missing = receipt.get("missing") or []
        ns.pop("accept_partial", None)
        ns.pop("partial", None)
        if not missing:
            return
        manifest = ns["manifest"]
        arrived = {x["name"] for x in receipt["landed"]}
        manifest["missing"] = missing
        manifest["files"] = [f for f in manifest["files"] if f["name"] in arrived]
        if manifest.get("episodes"):
            pairs = sorted({(f.get("season"), f["episode"]) for f in manifest["files"] if type(f.get("episode")) is int})
            seasons = {season for season, _ in pairs}
            manifest["episodes"] = sorted({ep for _, ep in pairs})
            manifest["season"] = seasons.pop() if len(seasons) == 1 else None
            if manifest.get("pairs") and manifest["season"] is None:
                manifest["pairs"] = [list(pair) for pair in pairs]  # 仍跨多季
            elif manifest.pop("pairs", None) is not None:
                ns.get("identity", {}).pop("pairs", None)  # 只剩一季：按季与集数记录
            self._adopt_manifest(ctx, manifest)
        ctx.log(f"按已到的 {len(arrived)} 个文件继续；没到的 {len(missing)} 个不转存、不记为已入库："
                f"{'、'.join(missing[:3])}{' 等' if len(missing) > 3 else ''}")

    def defer(self, ctx: PipelineContext, busy: StorageBusy) -> None:
        """网盘暂时拒绝一切请求（如 115 风控冷却），这次请求没有发出：回到所在阶段，到点后 Worker 再领取。

        不算失败、不消耗自动重试次数、不发提醒。转存还没发出时放开资源占位，让其他任务也能排队。
        """
        ns = ctx.ns("storage")
        if ctx.state in (TaskState.STORED, TaskState.ORGANIZING) or ns.get("phase") == "stored":
            ctx.state = TaskState.STORED
        elif ctx.state in (TaskState.RECEIVED, TaskState.PARSED):
            ctx.state = TaskState.RECEIVED
        else:
            ctx.state = TaskState.QUEUED
            if ns.get("phase") == "preparing":
                for key in ("phase", "identity"):
                    ns.pop(key, None)
                self.store.release_resource(ctx.task_id)
        resume = time.strftime("%H:%M", time.localtime(busy.until))
        ctx.log(f"{busy}，{resume} 后自动继续")
        self.store.defer_task(ctx, until=busy.until)

    async def _abandon(self, ctx: PipelineContext, storage) -> None:
        """放弃这次转存：删除暂存目录（进回收站）并读回确认，然后忽略任务、释放占位。"""
        ns = ctx.ns("storage")
        if not hasattr(storage, "abandon"):
            ctx.state = TaskState.NEED_MANUAL
            ns.pop("abandon", None)
            ctx.log(f"存储 {storage.name} 不支持放弃转存，请到网盘里手动删除暂存目录")
            self.store.save_task(ctx, error="存储不支持放弃转存")
            return
        try:
            with self.scope(ctx):
                await storage.abandon(ctx)
        except StorageBusy:
            raise  # 网盘暂时推迟了删除（风控冷却、上一个操作还没执行完）：Worker 到点再执行这次放弃
        except Exception as exc:  # noqa: BLE001 - 删除结果不明：保留决定，再点一次会先读回再删
            ctx.state = TaskState.NEED_MANUAL
            ctx.log(f"删除暂存目录没有完成：{exc}")
            self.store.save_task(ctx, error=f"删除暂存目录没有完成：{exc}")
            return
        for key in ("abandon", "partial", "accept_partial", "auto_recheck"):
            ns.pop(key, None)
        ns["phase"] = "abandoned"
        ctx.state = TaskState.SKIPPED
        ctx.log("已放弃这次转存：暂存目录连同已到的文件移进回收站，不再处理")
        self.store.save_task(ctx)
        self.store.release_resource(ctx.task_id)

    async def _organize(self, ctx: PipelineContext) -> None:
        cid = ctx.save_dir_cid
        async with self._organize_locks.setdefault(cid, asyncio.Lock()):
            if ctx.ns("storage").get("phase") == "stored":
                ctx.state = TaskState.STORED
                self.store.finish_transfer(ctx)
            ctx.state = TaskState.ORGANIZING
            ctx.result = dict(ctx.result or {})
            ctx.result["verified"] = True
            self.store.save_task(ctx)
            ok = await self.run_hooks("after_save", ctx)
            verified = ok and ctx.result.get("verified") is True
            ctx.state = TaskState.DONE if verified else TaskState.NEED_MANUAL
            ctx.log("整理完成且已读回核验" if verified else "转存已完成，整理需人工处理")
            await self.notify_all(ctx, "saved" if verified else "organize_failed", persist=False)
            await self.emit("on_saved" if verified else "on_failed", ctx, persist=False)
            self.store.save_task(ctx)

    @staticmethod
    def auto_rechecks(ctx: PipelineContext) -> bool:
        """转存结果待核验的任务是否由 Worker 后台自动复查（已发出转存、有清单、还没放弃复查）。"""
        ns = ctx.ns("storage")
        return (ns.get("phase") == "receiving" and bool(ns.get("manifest"))
                and not (ns.get("auto_recheck") or {}).get("gave_up"))

    async def notify_uncertain(self, ctx: PipelineContext) -> None:
        """提醒转存结果待人工核验（通知渠道与 on_failed 事件）；由调用方保存任务。"""
        await self.notify_all(ctx, "transfer_uncertain", persist=False)
        await self.emit("on_failed", ctx, persist=False)

    @staticmethod
    def _episodic(ctx: PipelineContext) -> bool:
        p = ctx.parsed
        return bool(p.title) and (p.media_type in ("tv", "anime") or bool(p.episodes))

    @staticmethod
    def _content_keys(storage_name: str, ctx: PipelineContext) -> list[str]:
        title = ctx.parsed.title
        return [f"{storage_name}:{ctx.save_dir_cid}:episode:{works.claim_key(title, season, ep)}"
                for season, ep in ctx.episode_pairs() if title]

    @staticmethod
    def _adopt_manifest(ctx: PipelineContext, manifest: dict) -> None:
        """分享清单是集数的事实来源；记录与消息提示的差异，去重身份随之更新。

        跨多季时识别结果的季记为 None、集为各季集号的并集；按季区分的（季, 集）以清单的 pairs 为准。
        """
        if not manifest.get("episodes"):
            return
        p = ctx.parsed
        if manifest.get("season") is None and manifest.get("pairs"):
            ctx.log(f"分享跨多季（{pairs_label(manifest['pairs'])}），按季分别转存与整理")
        elif p.episodes and sorted(p.episodes) != manifest["episodes"]:
            ctx.log(f"集数以分享文件为准：消息提示 {p.episodes} → 本次转存 {manifest['episodes']}")
        p.episodes = list(manifest["episodes"])
        p.season = manifest["season"]
        if p.media_type is None:
            p.media_type = "tv"
        identity = ctx.ns("storage").get("identity")
        if identity is not None:
            identity.update(episodes=list(p.episodes), season=p.season)
            if manifest.get("pairs"):
                identity["pairs"] = [list(pair) for pair in manifest["pairs"]]

    async def _recheck_manifest(self, ctx: PipelineContext, storage, provider_name: str) -> bool:
        ns = ctx.ns("storage")
        manifest = ns["manifest"]
        reason = None
        if not manifest.get("episodes") and self.store.saved_share(
                ctx.parsed.link_code(), ctx.save_dir_cid, provider_name):
            reason = "分享已转存到该目录"
        elif manifest.get("episodes") and ctx.parsed.title:
            work = ns.get("identity", {}).get("work") or works.of(ctx.parsed, ctx.meta.get("tmdb"))
            stored = {tuple(pair) for pair in self.store.stored_episode_pairs(work, ctx.save_dir_cid, provider_name)}
            remaining = [f for f in manifest["files"] if (f.get("season"), f.get("episode")) not in stored]
            if not remaining:
                reason = "分享内的集数均已由其他任务入库"
            elif len(remaining) < len(manifest["files"]):
                manifest["files"] = remaining
                manifest["pairs"] = [[f["season"], f["episode"]] for f in remaining]
                manifest["season"], manifest["episodes"] = summarize_pairs(manifest["pairs"])
                self._adopt_manifest(ctx, manifest)
                ctx.log("部分集已由其他任务入库，本次只转存 "
                        + (str(manifest["episodes"]) if manifest["season"] is not None else pairs_label(manifest["pairs"])))
                self.store.save_task(ctx)
        if reason is None:
            return True
        ctx.state = TaskState.SKIPPED
        ctx.log(f"执行前去重命中：{reason}")
        if hasattr(storage, "discard"):
            try:
                with self.scope(ctx):
                    await storage.discard(ctx)
            except Exception as exc:  # noqa: BLE001 - 空暂存目录残留不影响去重结果
                log.warning("清理未使用的暂存目录失败：%s", exc)
        self.store.save_task(ctx)
        return False

    async def _transfer_rejected(self, ctx: PipelineContext, storage, exc: TransferRejected) -> None:
        """115 明确拒绝且暂存目录为空：没有发生转存。分享已不可用时清掉暂存目录、不再重试；
        其他原因（如请求过于频繁）回到发送前，退避后重新发送。"""
        ns = ctx.ns("storage")
        if not exc.permanent:
            ns["phase"] = "ready"
            await self._fail(ctx, f"115 拒绝转存：{exc}，稍后重试" if ctx.parsed.share_code else f"{exc}，稍后重试",
                             retry_from=TaskState.QUEUED)
            return
        if hasattr(storage, "discard"):
            try:
                with self.scope(ctx):
                    await storage.discard(ctx)
            except Exception as error:  # noqa: BLE001 - 空暂存目录残留不影响结果
                log.warning("清理未使用的暂存目录失败：%s", error)
        for key in ("manifest", "phase", "identity", "content_keys"):
            ns.pop(key, None)
        await self._fail(ctx, f"分享不可用，无法转存：{exc}" if ctx.parsed.share_code else f"无法转存：{exc}",
                         retry_from=TaskState.QUEUED, auto_retry=False)

    async def _fail(self, ctx: PipelineContext, message: str, *, retry_from: TaskState,
                    auto_retry: bool = True) -> None:
        ctx.state = TaskState.FAILED
        task = ctx.ns("task")
        task["retry_from"] = retry_from.value
        if auto_retry:
            task.pop("no_auto_retry", None)
        else:
            task["no_auto_retry"] = True
        ctx.log(message)
        await self.emit("on_failed", ctx, persist=False)
        await self.notify_all(ctx, "failed", persist=False)
        self.store.save_task(ctx, error=message)

    # ------------------------------------------------------------------ #
    # 工具
    # ------------------------------------------------------------------ #
    async def notify_all(self, ctx: PipelineContext, event: str, *, persist: bool = True) -> None:
        # 「Bot 通知」关闭时不推送审批卡片与入库通知（在管理台处理）；需要处理的提醒照常发送。
        if event in ("pending_approval", "saved") and not ctx.source_rule.get("bot_notify", True):
            return
        names = self.task_config(ctx).get("providers", {}).get("notify", ["console"])
        for name in names:
            self._stage("notify", event, name, ctx)
        if persist:
            self.store.save_task(ctx)

    def rule_for(self, source: str) -> dict[str, Any]:
        merged = copy.deepcopy(self.cfg.get("defaults", {}))
        merged["forward"] = copy.deepcopy(self.cfg.get("forward", {}))
        rule = find_source(self.cfg.get("sources", []), source)  # 一张卡片可以写多个来源，共用它的设置
        return merge_config(merged, rule) if rule else merged

    # ------------------------------------------------------------------ #
    # 审批超时清扫：待审批久未处理的兜底
    # ------------------------------------------------------------------ #
    async def sweep_approval_timeouts(self) -> int:
        """把超时未处理的 pending_approval 按策略处理，返回处理条数。

        approval.timeout_hours（默认 24）+ on_timeout：
        - skip：置 SKIPPED；
        - remind：reminder=digest（默认）合并成一条汇总通知，reminder=card 逐条重推确认卡片；
          每条最多提醒 max_reminders 次（0 表示不限），之后停止提醒但保留在待审批列表。
        """
        ap = self.cfg.get("approval", {}) or {}
        hours = float(ap.get("timeout_hours", 24))
        action = ap.get("on_timeout", "remind")
        style = ap.get("reminder", "digest")
        limit = int(ap.get("max_reminders", 3))
        now = time.time()
        deadline = now - hours * 3600
        handled, reminded, exhausted = 0, [], 0
        for r in self.store.list_by_state(TaskState.PENDING_APPROVAL, 500):
            if r["updated_at"] > deadline:
                continue
            ctx = self.store.get_task(r["task_id"])
            if not ctx or ctx.state != TaskState.PENDING_APPROVAL:
                continue
            if action == "skip":
                if not self.store.set_state(ctx.task_id, TaskState.SKIPPED, expected=TaskState.PENDING_APPROVAL):
                    continue
                handled += 1
                continue
            count = ctx.ns("task").get("approval_reminders", 0)
            if limit and count >= limit:
                exhausted += 1
                continue
            ctx.log(f"审批超时（>{hours:g}h），第 {count + 1} 次提醒")
            ctx.ns("task")["approval_reminders"] = count + 1
            if style == "card":
                await self.notify_all(ctx, "pending_approval", persist=False)
            try:
                self.store.update_task_context(ctx, expected=TaskState.PENDING_APPROVAL)
            except ValueError:
                continue
            reminded.append((ctx, r["created_at"]))
            handled += 1
        if style == "digest" and reminded:
            self.store.stage_notice("approval_digest", self._approval_digest(reminded, exhausted, now),
                                    self.notify_targets(), event="digest", buttons=messages.PENDING_BUTTONS)
        return handled

    @staticmethod
    def _approval_digest(items: list, exhausted: int, now: float) -> str:
        # 追更会产生大量同名任务：按标题与来源合并，显示条数和最久等待天数。
        groups: dict[tuple[str, str], list[float]] = {}
        for ctx, created_at in items:
            title = (ctx.parsed.title if ctx.parsed else None) or ctx.raw.text[:20]
            groups.setdefault((title, ctx.raw.source), []).append(created_at)
        lines = [f"⏳ {len(items)} 条资源等待审批："]
        ordered = sorted(groups.items(), key=lambda item: min(item[1]))
        for (title, source), created in ordered[:15]:
            days = max(0, int((now - min(created)) // 86400))
            count = f" ×{len(created)}" if len(created) > 1 else ""
            lines.append(f"• {title}{count}（{source}，最久 {days} 天）")
        if len(ordered) > 15:
            lines.append(f"… 另有 {len(ordered) - 15} 部")
        if exhausted:
            lines.append(f"另有 {exhausted} 条已达提醒上限，不再提醒")
        lines.append("点下方按钮逐条查看，或一键全部批准、忽略。")
        return "\n".join(lines)

    # ------------------------------------------------------------------ #
    # 每日系统事件：日报、追更巡检等扩展订阅 on_daily
    # ------------------------------------------------------------------ #
    def hold_daily(self, seconds: float, now: float | None = None) -> None:
        """启动时调用：消息源补拉完、补拉的消息解析完之前不发日报，否则会少算停机期间的
        消息；最多推迟 seconds 秒，消息源迟迟连不上时照常发送。"""
        self.sources_ready = False
        self._daily_hold_until = (time.time() if now is None else now) + seconds

    def _daily_on_hold(self, now: float) -> bool:
        if self._daily_hold_until is None or now >= self._daily_hold_until:
            return False
        if self.sources_ready and not self.store.counts_by_state().get(TaskState.RECEIVED.value, 0):
            self._daily_hold_until = None  # 启动补拉已全部处理，之后不再检查
            return False
        return True

    async def emit_daily(self, now: float | None = None) -> bool:
        """到达 schedule.daily_hour 后每天发一次 on_daily；返回本次是否发出。"""
        now = time.time() if now is None else now
        local = time.localtime(now)
        today = time.strftime("%Y-%m-%d", local)
        hour = int(self.cfg.get("schedule", {}).get("daily_hour", 9))
        if local.tm_hour < hour or self.store.state_get("kernel", "daily_event") == today:
            return False
        if self._daily_on_hold(now):
            return False
        ctx = PipelineContext(raw=RawMessage(source="system", msg_id=f"daily-{today}", text=""),
                              task_id=f"daily-{today}")
        ctx.meta["system"] = {"event": "on_daily", "date": today,
                              "summary": self.store.summary_since(now - 86400)}
        self.stamp_config(ctx)
        await self.emit("on_daily", ctx, persist=False)
        self.store.stage_system_event(ctx)
        self.store.state_set("kernel", "daily_event", today)
        self.store.prune_llm_calls(now - 90 * 86400)  # AI 调用记录保留 90 天
        # 已投递的通知与事件保留 7 天（排查用），投递失败的保留 30 天
        self.store.prune_deliveries(done_before=now - 7 * 86400, failed_before=now - 30 * 86400)
        return True
