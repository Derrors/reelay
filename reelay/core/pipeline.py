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
    AlreadyStored,
    Decision,
    NeedsManual,
    PipelineContext,
    RawMessage,
    TaskState,
    TransferUncertain,
)
from .registry import Registry
from .store import Store, _load_ctx
from .config import config_revision, load_config, merge_config, normalize_config
from . import confidence, runtime

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
        self.services = runtime.Services(store, self.notify_targets)

    def notify_targets(self) -> list[str]:
        return list(self.cfg.get("providers", {}).get("notify", ["console"]))

    def _scope(self, ctx_or_cfg):
        cfg = self.task_config(ctx_or_cfg) if isinstance(ctx_or_cfg, PipelineContext) else ctx_or_cfg
        return runtime.scope(cfg, self.services)

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
                    with self._scope(ctx):
                        await hook.prepare(ctx)
                    self.store.save_task(ctx)  # 整理计划必须先落库
                with self._scope(ctx):
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
            try:
                if row["kind"] == "notice":
                    # 系统通知（告警、审批汇总、日报）不绑定任务；渠道不支持则跳过。
                    obj = self.reg.get_provider("notify", row["target"])
                    if not hasattr(obj, "notice"):
                        self.store.finish_delivery(row["id"], skipped=True)
                        return
                    callback = obj.notice(json.loads(row["snapshot"])["text"])
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
                with self._scope(cfg):
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
            with self._scope(ctx):
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

        # 决策只看确定性信号算出的置信度；TMDB 等补全 Hook 已在上面跑完。
        confidence.apply(ctx)
        decider = self.reg.get_provider("decision", ctx.source_rule.get("decision", self.cfg["defaults"]["decision"]))
        with self._scope(ctx):
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
            # 先按消息提示的集数占位，尽早串行化同一集的不同分享；清单确定后再按实际集数占位。
            if not self.store.claim_resource(ctx, key, content_keys=self._content_keys(storage.name, ctx)):
                ctx.state = TaskState.QUEUED
                self.store.save_task(ctx)  # 移到轮询末尾，避免等待资源的任务阻塞新任务
                return
            # 剧集分享链接常在追更中原地增加新集：剧集按集去重，其他按分享去重。
            if not self._episodic(ctx) and self.store.saved_share(
                    ctx.parsed.share_code, ctx.save_dir_cid, provider_name):
                ctx.state = TaskState.SKIPPED
                ctx.log("执行前去重命中，分享已转存到该目录")
                self.store.save_task(ctx)
                return
            ns["identity"] = {
                "provider": provider_name,
                "cid": ctx.save_dir_cid, "share_code": ctx.parsed.share_code,
                "title": ctx.parsed.title, "season": ctx.parsed.season,
                "episodes": list(ctx.parsed.episodes),
            }
            ns["stored_episodes"] = (self.store.stored_episode_pairs(ctx.parsed.title, ctx.save_dir_cid, provider_name)
                                     if ctx.parsed.title else [])
            try:
                with self._scope(ctx):
                    manifest = await storage.prepare(ctx)
            except AlreadyStored as exc:
                ctx.state = TaskState.SKIPPED
                ctx.log(f"执行前去重命中：{exc}")
                self.store.save_task(ctx)
                return
            except NeedsManual as exc:
                # 确定性校验失败：重试不会改变结果，不自动重试，修正后由人工重试。
                await self._fail(ctx, f"转存前校验未通过，需人工处理：{exc}", retry_from=TaskState.QUEUED,
                                 auto_retry=False)
                return
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
            if not self.store.claim_resource(ctx, ns["resource_key"],
                                             content_keys=ns.get("content_keys") or ns.get("claim_keys")):
                ctx.state = TaskState.QUEUED
                self.store.save_task(ctx)
                return
            # 尚未发送：等待期间其他任务可能已入库同一分享或部分集，发送前重新核对。
            if ns["phase"] == "ready" and not await self._recheck_manifest(ctx, storage, provider_name):
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

    @staticmethod
    def _episodic(ctx: PipelineContext) -> bool:
        p = ctx.parsed
        return bool(p.title) and (p.media_type in ("tv", "anime") or bool(p.episodes))

    def _content_keys(self, storage_name: str, ctx: PipelineContext) -> list[str]:
        p = ctx.parsed
        return [f"{storage_name}:{ctx.save_dir_cid}:episode:{self.store.episode_key(p.title or '', p.season, ep)}"
                for ep in p.episodes if p.title]

    @staticmethod
    def _adopt_manifest(ctx: PipelineContext, manifest: dict) -> None:
        """分享清单是集数的事实来源；记录与消息提示的差异，去重身份随之更新。"""
        if not manifest.get("episodes"):
            return
        p = ctx.parsed
        if p.episodes and sorted(p.episodes) != manifest["episodes"]:
            ctx.log(f"集数以分享文件为准：消息提示 {p.episodes} → 本次转存 {manifest['episodes']}")
        p.episodes = list(manifest["episodes"])
        p.season = manifest["season"]
        if p.media_type is None:
            p.media_type = "tv"
        identity = ctx.ns("storage").get("identity")
        if identity is not None:
            identity.update(episodes=list(p.episodes), season=p.season)

    async def _recheck_manifest(self, ctx: PipelineContext, storage, provider_name: str) -> bool:
        ns = ctx.ns("storage")
        manifest = ns["manifest"]
        reason = None
        if not manifest.get("episodes") and self.store.saved_share(
                ctx.parsed.share_code, ctx.save_dir_cid, provider_name):
            reason = "分享已转存到该目录"
        elif manifest.get("episodes") and ctx.parsed.title:
            stored = {tuple(pair) for pair in self.store.stored_episode_pairs(
                ctx.parsed.title, ctx.save_dir_cid, provider_name)}
            remaining = [f for f in manifest["files"] if (f.get("season"), f.get("episode")) not in stored]
            if not remaining:
                reason = "分享内的集数均已由其他任务入库"
            elif len(remaining) < len(manifest["files"]):
                manifest["files"] = remaining
                manifest["episodes"] = [f["episode"] for f in remaining]
                self._adopt_manifest(ctx, manifest)
                ctx.log(f"部分集已由其他任务入库，本次只转存 {manifest['episodes']}")
                self.store.save_task(ctx)
        if reason is None:
            return True
        ctx.state = TaskState.SKIPPED
        ctx.log(f"执行前去重命中：{reason}")
        if hasattr(storage, "discard"):
            try:
                with self._scope(ctx):
                    await storage.discard(ctx)
            except Exception as exc:  # noqa: BLE001 - 空暂存目录残留不影响去重结果
                log.warning("清理未使用的暂存目录失败：%s", exc)
        self.store.save_task(ctx)
        return False

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
        await self._emit("on_failed", ctx, persist=False)
        await self._notify_all(ctx, "failed", persist=False)
        self.store.save_task(ctx, error=message)

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
        """把超时未处理的 pending_approval 按策略处理，返回处理条数。

        approval.timeout_hours（默认 24）+ on_timeout：
        - skip：置 SKIPPED；
        - remind：reminder=digest（默认）合并成一条汇总通知，reminder=card 逐条重推确认卡片；
          每条最多提醒 max_reminders 次（0 表示不限），之后停止提醒但保留在待审批列表。
        """
        import time
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
                await self._notify_all(ctx, "pending_approval", persist=False)
            try:
                self.store.update_task_context(ctx, expected=TaskState.PENDING_APPROVAL)
            except ValueError:
                continue
            reminded.append((ctx, r["created_at"]))
            handled += 1
        if style == "digest" and reminded:
            self.store.stage_notice("approval_digest", self._approval_digest(reminded, exhausted, now),
                                    self.notify_targets(), event="digest")
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
        lines.append("请在管理台「任务」中批量批准或忽略，或用 /pending 查看。")
        return "\n".join(lines)

    # ------------------------------------------------------------------ #
    # 每日系统事件：日报、追更巡检等扩展订阅 on_daily
    # ------------------------------------------------------------------ #
    async def emit_daily(self, now: float | None = None) -> bool:
        """到达 schedule.daily_hour 后每天发一次 on_daily；返回本次是否发出。"""
        import time
        now = time.time() if now is None else now
        local = time.localtime(now)
        today = time.strftime("%Y-%m-%d", local)
        hour = int(self.cfg.get("schedule", {}).get("daily_hour", 9))
        if local.tm_hour < hour or self.store.state_get("kernel", "daily_event") == today:
            return False
        ctx = PipelineContext(raw=RawMessage(source="system", msg_id=f"daily-{today}", text=""),
                              task_id=f"daily-{today}")
        ctx.meta["system"] = {"event": "on_daily", "date": today,
                              "summary": self.store.summary_since(now - 86400)}
        ctx.ns("runtime").update(config=copy.deepcopy(self.cfg), revision=self.revision)
        await self._emit("on_daily", ctx, persist=False)
        self.store.stage_system_event(ctx)
        self.store.state_set("kernel", "daily_event", today)
        return True
