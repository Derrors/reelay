"""管线编排 —— 内核最核心的部分。

把一条 ``PipelineContext`` 按「监听 → 识别 → 转存整理」推到底，沿途：

- 在每个挂载点跑 Hook（**失败隔离**：单个 Hook 抛异常只记日志，不拖垮主流程；
  返回 ``False`` 则中止后续、当前任务按 skip 处理）
- 在关键时刻发 Event（**旁路**：监听器异常被吞掉，绝不影响主链路）
- 每次状态变更都落库（可观测 / 可恢复 / 可干预）

真正的耗时执行（解析、转存）由 Provider 完成；这里只负责编排与状态机推进。
"""

from __future__ import annotations

import logging
from typing import Any

from .contracts import (
    Decision,
    PipelineContext,
    TaskState,
)
from .registry import Registry
from .store import (
    Store,
    DEDUP_MESSAGE,
    DEDUP_SHARE,
    DEDUP_EPISODE,
)

log = logging.getLogger("reelay.pipeline")


class Pipeline:
    """无状态编排器；所有状态都在 Store 里。"""

    def __init__(self, registry: Registry, store: Store, config: dict[str, Any]):
        self.reg = registry
        self.store = store
        self.cfg = config
        self.save_dirs: dict[str, str] = config.get("save_dirs", {})
        # 注入全局配置，供 Hook（DirRouter / Organizer 等）只读取用
        from . import runtime
        runtime.set_config(config)

    # ------------------------------------------------------------------ #
    # Hook / Event 基础设施
    # ------------------------------------------------------------------ #
    async def _run_hooks(self, stage: str, ctx: PipelineContext) -> bool:
        """跑某挂载点的所有 Hook。返回 False 表示被拦截。"""
        for hook in self.reg.hooks_for(stage):
            try:
                ok = await hook.run(ctx)
                if ok is False:
                    ctx.log(f"hook 拦截 @{stage}: {hook.__class__.__name__}")
                    return False
            except Exception as exc:  # 失败隔离
                log.warning("hook 异常 @%s %s: %s（已隔离）", stage, hook.__class__.__name__, exc)
                ctx.log(f"hook 异常 @{stage}: {exc}")
        return True

    async def _emit(self, event: str, ctx: PipelineContext) -> None:
        """发事件。监听器异常一律吞掉，绝不影响主流程。"""
        for lis in self.reg.listeners_for(event):
            try:
                await lis.handle(ctx)
            except Exception as exc:
                log.warning("listener 异常 %s %s: %s（已忽略）", event, lis.__class__.__name__, exc)

    # ------------------------------------------------------------------ #
    # 阶段 1：快速接收（监听回调只去重+落库，不等待 AI）
    # ------------------------------------------------------------------ #
    async def accept(self, ctx: PipelineContext) -> PipelineContext | None:
        """快速接收新消息并落为 RECEIVED；返回 None 表示消息级重复。"""
        message_key = f"{ctx.raw.source}:{ctx.raw.msg_id}"
        if self.store.seen(DEDUP_MESSAGE, message_key):
            return None
        self.store.mark(DEDUP_MESSAGE, message_key, ctx.task_id)
        ctx.source_rule = self._rule_for(ctx.raw.source)
        ctx.state = TaskState.RECEIVED
        ctx.log("消息已接收并落库")
        self.store.save_task(ctx)
        await self._emit("on_message", ctx)
        return ctx

    # ------------------------------------------------------------------ #
    # 阶段 2：识别（由 Worker 消费 RECEIVED）
    # ------------------------------------------------------------------ #
    async def process_new(self, ctx: PipelineContext) -> None:
        """兼容入口：接收后立即解析；dry-run 与单元测试使用。"""
        accepted = await self.accept(ctx)
        if accepted:
            await self.process_received(accepted)

    async def process_received(self, ctx: PipelineContext) -> None:
        """从 RECEIVED 推进到 rejected / pending_approval / queued。"""
        if ctx.state != TaskState.RECEIVED:
            return
        if not await self._run_hooks("before_parse", ctx):
            ctx.state = TaskState.SKIPPED
            self.store.save_task(ctx)
            return

        parser_name = ctx.source_rule.get(
            "parser", self.cfg.get("defaults", {}).get("parser", "llm")
        )
        parser = self.reg.get_provider("parser", parser_name)
        try:
            ctx.parsed = await parser.parse(ctx)
        except Exception as exc:
            ctx.state = TaskState.FAILED
            ctx.ns("task")["retry_from"] = TaskState.RECEIVED.value
            ctx.log(f"解析失败：{exc}")
            self.store.save_task(ctx, error=str(exc))
            await self._emit("on_failed", ctx)
            return

        ctx.state = TaskState.PARSED
        ctx.log(
            f"解析完成 is_media={ctx.parsed.is_media} is_ad={ctx.parsed.is_ad} "
            f"conf={ctx.parsed.confidence:.2f}"
        )
        self.store.save_task(ctx)

        if not await self._run_hooks("after_parse", ctx):
            ctx.state = TaskState.SKIPPED
            self.store.save_task(ctx)
            return

        decider = self.reg.get_provider("decision", "default")
        ctx.decision = await decider.decide(ctx)
        ctx.log(f"决策 = {ctx.decision.value}")

        if ctx.decision == Decision.SKIP:
            ctx.state = (
                TaskState.REJECTED
                if (ctx.parsed and ctx.parsed.is_ad)
                else TaskState.SKIPPED
            )
            self.store.save_task(ctx)
            await self._emit("on_rejected", ctx)
            return

        share = ctx.parsed.share_code if ctx.parsed else None
        if share and self.store.seen(DEDUP_SHARE, share):
            missing = self.store.missing_episodes(ctx.parsed)
            if not missing:
                ctx.state = TaskState.SKIPPED
                ctx.log("资源级去重命中且无新增集，跳过")
                self.store.save_task(ctx)
                return
            ctx.parsed.episodes = missing
            ctx.log(f"资源已转存过，仅处理新增集 {missing}")

        await self._emit("on_identified", ctx)
        await self._notify_all(ctx, "identified")

        if ctx.decision == Decision.ASK:
            ctx.state = TaskState.PENDING_APPROVAL
            ctx.log("转人工确认（审批态已落库，可跨重启）")
            self.store.save_task(ctx)
            await self._notify_all(ctx, "pending_approval")
        else:
            ctx.state = TaskState.QUEUED
            self.store.save_task(ctx)

    # ------------------------------------------------------------------ #
    # 审批回调（控制 bot / 收藏夹点击后调用）
    # ------------------------------------------------------------------ #
    async def approve(self, task_id: str) -> bool:
        ctx = self.store.get_task(task_id)
        if not ctx or ctx.state != TaskState.PENDING_APPROVAL:
            return False
        ctx.state = TaskState.QUEUED
        ctx.log("人工确认通过")
        self.store.save_task(ctx)
        return True

    async def reject(self, task_id: str) -> bool:
        ctx = self.store.get_task(task_id)
        if not ctx or ctx.state != TaskState.PENDING_APPROVAL:
            return False
        ctx.state = TaskState.SKIPPED
        ctx.log("人工忽略")
        self.store.save_task(ctx)
        return True

    # ------------------------------------------------------------------ #
    # 阶段 3：转存 + 整理（由 worker 从队列取出后调用）
    # ------------------------------------------------------------------ #
    async def execute_save(self, ctx: PipelineContext) -> None:
        ctx.state = TaskState.SAVING
        self.store.save_task(ctx)

        action = ctx.ns("task").pop("action", None)
        if action == "organize_only":
            ctx.result = dict(ctx.result or {})
            ctx.result["verified"] = True
            await self._run_hooks("after_save", ctx)
            verified = bool((ctx.result or {}).get("verified", True))
            ctx.state = TaskState.DONE if verified else TaskState.NEED_MANUAL
            ctx.log("重新整理完成" if verified else "重新整理仍需人工处理")
            self.store.save_task(ctx)
            await self._notify_all(ctx, "saved")
            await self._emit("on_saved", ctx)
            return

        # before_save：DirRouter 选目录、查配额等
        await self._run_hooks("before_save", ctx)

        # 目录别名 -> cid
        alias = ctx.save_dir_alias or ctx.source_rule.get("save_to") \
            or self.cfg.get("defaults", {}).get("save_to", "默认")
        ctx.save_dir_alias = alias
        ctx.save_dir_cid = self.save_dirs.get(alias) or self.save_dirs.get("默认")

        try:
            storage = self.reg.get_provider("storage", "p115")
            ctx.result = await storage.save(ctx)
        except Exception as exc:
            ctx.state = TaskState.FAILED
            ctx.log(f"转存失败：{exc}")
            self.store.save_task(ctx, error=str(exc))
            await self._emit("on_failed", ctx)
            return

        # after_save：改名 + 整理入库 + 读回验证
        await self._run_hooks("after_save", ctx)

        # 资源级 / 内容级去重标记
        if ctx.parsed and ctx.parsed.share_code:
            self.store.mark(DEDUP_SHARE, ctx.parsed.share_code, ctx.task_id)
        if ctx.parsed and ctx.parsed.title and ctx.parsed.episodes:
            for ep in ctx.parsed.episodes:
                self.store.mark(
                    DEDUP_EPISODE,
                    Store.episode_key(ctx.parsed.title, ctx.parsed.season, ep),
                    ctx.task_id,
                )

        # 读回验证结果决定终态
        verified = bool((ctx.result or {}).get("verified", True))
        ctx.state = TaskState.DONE if verified else TaskState.NEED_MANUAL
        ctx.log("转存完成且已验证" if verified else "转存了但验证不通过，转人工")
        self.store.save_task(ctx)

        await self._notify_all(ctx, "saved")
        await self._emit("on_saved", ctx)

    # ------------------------------------------------------------------ #
    # 工具
    # ------------------------------------------------------------------ #
    async def _notify_all(self, ctx: PipelineContext, event: str) -> None:
        for name, prov in self.reg.providers.get("notify", {}).items():
            try:
                await prov.notify(ctx, event)
            except Exception as exc:
                log.warning("notify 异常 %s: %s", name, exc)

    def _rule_for(self, source: str) -> dict[str, Any]:
        for s in self.cfg.get("sources", []):
            if s.get("bot") == source:
                merged = dict(self.cfg.get("defaults", {}))
                merged.update(s)
                return merged
        return dict(self.cfg.get("defaults", {}))

    # ------------------------------------------------------------------ #
    # 审批超时清扫（评审 P0-01：manual 卡片久未处理的兜底）
    # ------------------------------------------------------------------ #
    async def sweep_approval_timeouts(self) -> int:
        """把超时未处理的 pending_approval 按策略处理。

        config.approval.timeout_hours（默认 24）+ on_timeout（remind|skip，默认 remind）。
        remind：重推一次确认通知；skip：置 SKIPPED。返回处理条数。
        """
        import time
        ap = self.cfg.get("approval", {}) or {}
        hours = float(ap.get("timeout_hours", 24))
        action = ap.get("on_timeout", "remind")
        deadline = time.time() - hours * 3600
        handled = 0
        for r in self.store.list_by_state(TaskState.PENDING_APPROVAL, 200):
            if r["updated_at"] > deadline:
                continue
            ctx = self.store.get_task(r["task_id"])
            if not ctx:
                continue
            if action == "skip":
                ctx.state = TaskState.SKIPPED
                ctx.log(f"审批超时（>{hours}h），按策略忽略")
                self.store.save_task(ctx)
            else:
                ctx.log(f"审批超时（>{hours}h），重推提醒")
                self.store.set_state(ctx.task_id, TaskState.PENDING_APPROVAL)  # 刷新 updated_at
                await self._notify_all(ctx, "pending_approval")
            handled += 1
        return handled
