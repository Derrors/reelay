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
    # 阶段 2：识别（AI 感知层） + 决策
    # ------------------------------------------------------------------ #
    async def process_new(self, ctx: PipelineContext) -> None:
        """从「收到消息」到「决定是否入队」。由监听回调触发。"""
        # 层1 消息级去重
        if self.store.seen(DEDUP_MESSAGE, ctx.raw.msg_id):
            ctx.state = TaskState.SKIPPED
            ctx.log("消息级去重命中，跳过")
            self.store.save_task(ctx)
            return
        self.store.mark(DEDUP_MESSAGE, ctx.raw.msg_id, ctx.task_id)

        ctx.source_rule = self._rule_for(ctx.raw.source)
        self.store.save_task(ctx)  # RECEIVED
        await self._emit("on_message", ctx)

        await self._run_hooks("before_parse", ctx)

        # —— AI 解析 ——
        parser_name = ctx.source_rule.get("parser", self.cfg.get("defaults", {}).get("parser", "llm"))
        parser = self.reg.get_provider("parser", parser_name)
        ctx.parsed = await parser.parse(ctx)
        ctx.state = TaskState.PARSED
        ctx.log(f"解析完成 is_media={ctx.parsed.is_media} is_ad={ctx.parsed.is_ad} "
                f"conf={ctx.parsed.confidence:.2f}")
        self.store.save_task(ctx)

        if not await self._run_hooks("after_parse", ctx):
            ctx.state = TaskState.SKIPPED
            self.store.save_task(ctx)
            return

        # —— 决策 ——
        decider = self.reg.get_provider("decision", "default")
        ctx.decision = await decider.decide(ctx)
        ctx.log(f"决策 = {ctx.decision.value}")

        if ctx.decision == Decision.SKIP:
            ctx.state = TaskState.REJECTED if (ctx.parsed and ctx.parsed.is_ad) else TaskState.SKIPPED
            self.store.save_task(ctx)
            await self._emit("on_rejected", ctx)
            return

        # 层2 资源级去重（已转存过的分享，若无新增集则跳过）
        share = ctx.parsed.share_code if ctx.parsed else None
        if share and self.store.seen(DEDUP_SHARE, share):
            missing = self.store.missing_episodes(ctx.parsed)
            if not missing:
                ctx.state = TaskState.SKIPPED
                ctx.log("资源级去重命中且无新增集，跳过")
                self.store.save_task(ctx)
                return
            ctx.log(f"资源已转存过，但有新增集 {missing}（追更增量）")

        await self._emit("on_identified", ctx)

        # 按审批模式决定：入队 or 待确认
        if ctx.decision == Decision.ASK:
            ctx.state = TaskState.PENDING_APPROVAL
            ctx.log("转人工确认（审批态已落库，可跨重启）")
            self.store.save_task(ctx)
            # 通知渠道推确认卡片（callback_data 只带 task_id）
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
