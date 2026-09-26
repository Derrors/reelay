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
import json
from pathlib import Path
import logging
from typing import Any

from .contracts import (
    Decision,
    PipelineContext,
    TaskState,
    TransferUncertain,
)
from .registry import Registry
from .store import Store, _load_ctx
from .config import config_revision, load_config, merge_config, normalize_config
from . import runtime

log = logging.getLogger("reelay.pipeline")


class Pipeline:
    """持久化状态在 Store 中，进程内按目标目录串行整理。"""

    def __init__(self, registry: Registry, store: Store, config: dict[str, Any]):
        self.reg = registry
        self.store = store
        self.cfg = normalize_config(config)
        self.revision = config_revision(self.cfg)
        self.config_path: Path | None = None
        self.config_error: str | None = None
        self.save_dirs: dict[str, str] = self.cfg["save_dirs"]
        self._organize_locks: dict[str, asyncio.Lock] = {}

    def apply_config(self, config: dict) -> None:
        cfg = normalize_config(config)
        self.reg.validate_config(cfg)
        self.cfg, self.save_dirs = cfg, cfg["save_dirs"]
        self.revision = config_revision(cfg)
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
        except Exception:
            self.config_error = "配置更新无效，继续使用上一有效版本"
            return False

    def task_config(self, ctx: PipelineContext) -> dict:
        return ctx.meta.get("runtime", {}).get("config", self.cfg)

    # ------------------------------------------------------------------ #
    # Hook / Event 基础设施
    # ------------------------------------------------------------------ #
    async def _run_hooks(self, stage: str, ctx: PipelineContext) -> bool:
        """跑某挂载点的所有 Hook。返回 False 表示被拦截。"""
        for hook in self.reg.hooks_for(stage, self.task_config(ctx)):
            try:
                bound = getattr(hook, "storage_provider", None)
                if stage == "after_save" and bound and bound != ctx.ns("storage").get("provider", "p115"):
                    raise ValueError("整理扩展与任务的存储 Provider 不匹配")
                if stage == "after_save" and hasattr(hook, "prepare"):
                    with runtime.scope(self.task_config(ctx)):
                        await hook.prepare(ctx)
                    self.store.save_task(ctx)  # 整理计划必须先落库
                with runtime.scope(self.task_config(ctx)):
                    ok = await hook.run(ctx)
                if ok is False:
                    ctx.log(f"hook 拦截 @{stage}: {hook.__class__.__name__}")
                    if stage == "after_save" and ctx.result is not None:
                        ctx.result["verified"] = False
                    return False
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
        sequence = ctx.meta.get("task", {}).get("approval_reminders", 0) if event == "pending_approval" else 0
        generation = ctx.meta.get("task", {}).get("generation", 0)
        key = f"{generation}:{kind}:{event}:{target}:{sequence}"
        ctx.ns("deliveries")[key] = {"kind": kind, "event": event, "target": target}

    async def _emit(self, event: str, ctx: PipelineContext, *, persist: bool = True) -> None:
        for listener in self.reg.listeners_for(event, self.task_config(ctx)):
            self._stage("event", event, self.reg.extension_name(listener), ctx)
        if persist:
            self.store.save_task(ctx)

    async def dispatch_deliveries(self) -> int:
        rows = self.store.claim_deliveries()

        async def deliver(row: dict) -> None:
            ctx = _load_ctx(json.loads(row["snapshot"]))
            try:
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
                with runtime.scope(self.task_config(ctx)):
                    await asyncio.wait_for(callback, timeout=30)
            except Exception as exc:
                category = type(exc).__name__
                import math
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
        ctx.source_rule = self._rule_for(ctx.raw.source)
        ctx.ns("runtime").update(config=copy.deepcopy(self.cfg), revision=self.revision)
        await self._emit("on_message", ctx, persist=False)
        ctx.state = TaskState.RECEIVED
        ctx.log("消息已接收并落库")
        if not self.store.accept_task(ctx):
            return None
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
            await self._emit("on_failed", ctx, persist=False)
            await self._notify_all(ctx, "failed", persist=False)
            self.store.save_task(ctx, error=str(exc))
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

        decider = self.reg.get_provider("decision", ctx.source_rule.get("decision", self.cfg["defaults"]["decision"]))
        with runtime.scope(self.task_config(ctx)):
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
            await self._emit("on_rejected", ctx, persist=False)
            self.store.save_task(ctx)
            return

        if ctx.decision == Decision.ASK:
            ctx.state = TaskState.PENDING_APPROVAL
            ctx.log("转人工确认（审批态已落库，可跨重启）")
        else:
            ctx.state = TaskState.QUEUED
        await self._emit("on_identified", ctx, persist=False)
        await self._notify_all(ctx, "identified", persist=False)
        if ctx.decision == Decision.ASK:
            await self._notify_all(ctx, "pending_approval", persist=False)
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
        if ns.get("phase") == "legacy_uncertain":
            ctx.state = TaskState.NEED_MANUAL
            self.store.save_task(ctx, error="旧版转存结果未知，不能重新发送")
            return
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
        if not ns.get("manifest"):
            if not await self._run_hooks("before_save", ctx):
                ctx.state = TaskState.SKIPPED
                self.store.save_task(ctx)
                return
            self._resolve_directory(ctx)
            ctx.state = TaskState.SAVING
            ns["phase"] = "preparing"
            key = f"{storage.name}:{ctx.save_dir_cid}:{ctx.parsed.share_code}"
            content_keys = [
                f"{storage.name}:{ctx.save_dir_cid}:episode:"
                f"{self.store.episode_key(ctx.parsed.title or '', ctx.parsed.season, ep)}"
                for ep in ctx.parsed.episodes if ctx.parsed.title
            ]
            if not self.store.claim_resource(ctx, key, content_keys=content_keys):
                ctx.state = TaskState.QUEUED
                self.store.save_task(ctx)  # 移到轮询末尾，避免等待资源的任务阻塞新任务
                return

            # 已排队任务也必须在获得执行权后重新核对，支持相同分享的追更。
            duplicate = self.store.saved_share(ctx.parsed.share_code, ctx.save_dir_cid, provider_name)
            if ctx.parsed.episodes and ctx.parsed.title:
                missing = self.store.missing_episodes(ctx.parsed, ctx.save_dir_cid, provider_name)
                duplicate = not missing
                if missing:
                    ctx.parsed.episodes = missing
            if duplicate:
                ctx.state = TaskState.SKIPPED
                ctx.log("执行前去重命中，资源已转存到该目录")
                self.store.save_task(ctx)
                return
            ns["identity"] = {
                "provider": provider_name,
                "cid": ctx.save_dir_cid, "share_code": ctx.parsed.share_code,
                "title": ctx.parsed.title, "season": ctx.parsed.season,
                "episodes": list(ctx.parsed.episodes),
            }
            try:
                ns["manifest"] = await storage.prepare(ctx)
                ns["phase"] = "ready"
                self.store.save_task(ctx)
            except Exception as exc:
                ctx.state = TaskState.FAILED
                ctx.ns("task")["retry_from"] = TaskState.QUEUED.value
                ctx.log(f"转存准备失败：{exc}")
                await self._emit("on_failed", ctx, persist=False)
                await self._notify_all(ctx, "failed", persist=False)
                self.store.save_task(ctx, error=str(exc))
                return
        else:
            ctx.state = TaskState.SAVING
            if not self.store.claim_resource(ctx, ns["resource_key"], content_keys=ns.get("claim_keys")):
                ctx.state = TaskState.QUEUED
                self.store.save_task(ctx)
                return

        try:
            if ns["phase"] == "ready":
                ns["phase"] = "receiving"
                self.store.save_task(ctx)  # 必须在远端写请求之前提交
                try:
                    receipt = await storage.save(ctx)
                except Exception:
                    receipt = await storage.reconcile(ctx)
            elif ns["phase"] == "receiving":
                receipt = await storage.reconcile(ctx)
            else:
                raise TransferUncertain("转存检查点状态异常，需人工核验")
            if receipt.get("storage_verified") is not True or not receipt.get("landed"):
                raise TransferUncertain("转存回执未确认全部文件落地")
        except Exception as exc:
            ctx.state = TaskState.NEED_MANUAL
            ctx.log(f"转存结果待核验，不自动重发：{exc}")
            await self._notify_all(ctx, "transfer_uncertain", persist=False)
            await self._emit("on_failed", ctx, persist=False)
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
            ok = await self._run_hooks("after_save", ctx)
            verified = ok and ctx.result.get("verified") is True
            ctx.state = TaskState.DONE if verified else TaskState.NEED_MANUAL
            ctx.log(("[dry-run] 模拟转存整理完成" if ctx.result.get("dry_run") else "整理完成且已读回核验")
                    if verified else "转存已完成，整理需人工处理")
            await self._notify_all(ctx, "saved" if verified else "organize_failed", persist=False)
            await self._emit("on_saved" if verified else "on_failed", ctx, persist=False)
            self.store.save_task(ctx)

    # ------------------------------------------------------------------ #
    # 工具
    # ------------------------------------------------------------------ #
    async def _notify_all(self, ctx: PipelineContext, event: str, *, persist: bool = True) -> None:
        # 静默模式只抑制常规成功通知；低置信度审批和故障仍可干预。
        if event == "saved" and ctx.source_rule.get("approval_mode") == "default_pass":
            return
        names = self.task_config(ctx).get("providers", {}).get("notify", ["console"])
        for name in names:
            self._stage("notify", event, name, ctx)
        if persist:
            self.store.save_task(ctx)

    def _rule_for(self, source: str) -> dict[str, Any]:
        merged = copy.deepcopy(self.cfg.get("defaults", {}))
        merged["forward"] = copy.deepcopy(self.cfg.get("forward", {}))
        for rule in self.cfg.get("sources", []):
            if rule.get("bot", "").lower() == source.lower():
                return merge_config(merged, rule)
        return merged

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
            if not ctx or ctx.state != TaskState.PENDING_APPROVAL:
                continue
            if action == "skip":
                if not self.store.set_state(ctx.task_id, TaskState.SKIPPED, expected=TaskState.PENDING_APPROVAL):
                    continue
            else:
                ctx.log(f"审批超时（>{hours}h），重推提醒")
                ctx.ns("task")["approval_reminders"] = ctx.ns("task").get("approval_reminders", 0) + 1
                await self._notify_all(ctx, "pending_approval", persist=False)
                try:
                    self.store.update_task_context(ctx, expected=TaskState.PENDING_APPROVAL)
                except ValueError:
                    continue
            handled += 1
        return handled
