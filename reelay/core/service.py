"""统一应用服务层：Web API 与 Telegram 控制 Bot 共用这里的规则。

服务只改变任务/配置的权威状态，不直接绕过 Pipeline 操作 115。这样两个入口并发操作时，
状态检查与错误语义一致。
"""

from __future__ import annotations

import asyncio
import copy
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .config import normalize_config
from .validation import validate_resource
from .contracts import ParsedResource, PipelineContext, TaskState
from .pipeline import Pipeline
from .store import Store
from ..plugins.organizer import Organizer


class ConflictError(RuntimeError):
    pass


class NotFoundError(RuntimeError):
    pass


class ManagementService:
    def __init__(
        self,
        store: Store,
        pipeline: Pipeline,
        *,
        config_path: str = "config.yaml",
    ) -> None:
        self.store = store
        self.pipeline = pipeline
        self.config_path = Path(config_path)
        self.pipeline.config_path = self.config_path
        self._config_lock = asyncio.Lock()

    def dashboard(self) -> dict[str, Any]:
        counts = self.store.counts_by_state()
        pending = counts.get(TaskState.PENDING_APPROVAL.value, 0) + counts.get(
            TaskState.NEED_MANUAL.value, 0
        )
        active = sum(counts.get(state.value, 0) for state in (
            TaskState.RECEIVED, TaskState.PARSED, TaskState.QUEUED,
            TaskState.SAVING, TaskState.STORED, TaskState.ORGANIZING,
        ))
        connections = self.connection_status()
        dry_run = __import__("os").getenv("REELAY_DRY_RUN", "").lower() in {
            "1", "true", "yes"
        }
        live_ready = connections["telegram"]["configured"] and connections["p115"]["configured"]
        health = self.store.health_status()
        worker = health.get("worker", {})
        source = health.get("source", {})
        live_running = (worker.get("status") == "running" and not worker.get("stale", True)
                        and source.get("status") == "connected" and not source.get("stale", True))
        self.pipeline.reload_config()
        return {
            "counts": counts,
            "pending": pending,
            "active": active,
            "failed": counts.get(TaskState.FAILED.value, 0),
            "done": counts.get(TaskState.DONE.value, 0),
            "connections": connections,
            "runtime": health,
            "alerts": self.store.active_alerts(),
            "recent": self._recent(),
            "deliveries": self.store.delivery_counts(),
            "config_revision": self.pipeline.revision,
            "simulation": dry_run,
            "mode": "simulation" if dry_run else ("live" if live_running else ("not_running" if live_ready else "not_configured")),
        }

    def _recent(self) -> dict[str, int]:
        import time
        summary = self.store.summary_since(time.time() - 86400)
        return {"received_24h": summary["received"], "done_24h": summary["done_count"]}

    def connection_status(self) -> dict[str, dict[str, Any]]:
        import os
        import time

        connections = {
            "telegram": {
                "configured": bool(os.getenv("REELAY_TG_API_ID") and os.getenv("REELAY_TG_API_HASH")),
                "label": "Telegram 监听",
            },
            "control_bot": {
                "configured": bool(os.getenv("REELAY_CONTROL_BOT_TOKEN") and os.getenv("REELAY_OWNER_ID")),
                "label": "控制 Bot",
            },
            "p115": {
                "configured": self._p115_configured(),
                "label": "115 网盘",
            },
            "llm": {
                "configured": bool(os.getenv("REELAY_LLM_API_KEY")),
                "label": "AI 解析",
            },
            "tmdb": {
                "configured": bool(os.getenv("REELAY_TMDB_API_KEY")),
                "label": "TMDB 元数据",
            },
        }
        health = self.store.health_status()
        observed_as = {"telegram": "source", "p115": "p115_cookie"}
        for key, conn in connections.items():
            conn["status"] = "unverified" if conn["configured"] else "not_configured"
            observation = health.get(observed_as.get(key, key))
            if observation and observation.get("status") != "unknown":
                stale = observation["stale"]
                if observation.get("check_interval"):  # 定时探活：超过两个探活周期没有新结果才算过期
                    stale = time.time() - observation["updated_at"] > 2 * observation["check_interval"]
                elif observation.get("event_driven"):  # 只在真实调用时记录，显示最近一次结果
                    stale = False
                conn["status"] = "stale" if stale else observation.get("status", "unverified")
                conn["observed_at"] = observation["updated_at"]
                if observation.get("error"):
                    conn["error"] = observation["error"]
        return connections

    @staticmethod
    def _p115_configured() -> bool:
        """转存主链路依赖cookie；只有OAuth不能当成主流程就绪。"""
        from .auth115 import get_store
        return get_store().has_cookie

    def list_tasks(self, **filters: Any) -> list[dict[str, Any]]:
        """state 可以是逗号分隔的多个状态，如 "received,queued,saving"。"""
        state_value = filters.pop("state", None)
        states = [TaskState(item.strip()) for item in str(state_value).split(",") if item.strip()] \
            if state_value else None
        return self.store.list_tasks(states=states, **filters)

    def inbox(self, state: str = TaskState.PENDING_APPROVAL.value, limit: int = 200) -> dict[str, Any]:
        """待处理收件箱：一次返回卡片需要的全部字段，前端无需逐条请求详情。"""
        import re
        if state not in (TaskState.PENDING_APPROVAL.value, TaskState.NEED_MANUAL.value):
            raise ValueError("收件箱只支持 pending_approval 与 need_manual")
        items = []
        for row in self.store.list_tasks(states=[TaskState(state)], limit=limit):
            ctx = self.store.get_task(row["task_id"])
            if not ctx:
                continue
            p = ctx.parsed or ParsedResource()
            meta = ctx.meta
            lines = [re.sub(r"https?://\S+", "", line).strip() for line in (ctx.raw.text or "").splitlines()]
            items.append({
                **{key: row[key] for key in ("task_id", "state", "source", "created_at", "updated_at", "error")},
                "title": p.title, "year": p.year, "media_type": p.media_type, "season": p.season,
                "episodes": p.episodes, "resolution": p.resolution, "size": p.size, "reason": p.reason,
                "confidence": p.confidence,
                "confidence_caps": meta.get("confidence", {}).get("caps", []),
                "fallback": bool(meta.get("parse", {}).get("fallback")),
                "tmdb_matched": meta.get("tmdb", {}).get("matched"),
                "poster_path": p.raw_fields.get("poster_path"),
                "save_dir_alias": ctx.save_dir_alias,
                "auto_dir": self.pipeline.target_preview(ctx) if state == TaskState.PENDING_APPROVAL.value else None,
                "preview":"\n".join(line for line in lines if line)[:400],
                "storage_phase": meta.get("storage", {}).get("phase"),
                "has_landed": bool(meta.get("storage", {}).get("landed")),
                "organize_error": meta.get("organize", {}).get("error"),
            })
        return {"items": items, "save_dirs": list(self.pipeline.cfg["save_dirs"])}

    def get_task(self, task_id: str) -> dict[str, Any]:
        record = self.store.get_task_record(task_id)
        if not record:
            raise NotFoundError("任务不存在")
        ctx = self.store.get_task(task_id) if record["state"] == TaskState.PENDING_APPROVAL.value else None
        record["auto_dir"] = self.pipeline.target_preview(ctx) if ctx else None
        return record

    async def approve(self, task_id: str, *, save_dir_alias: str | None = None) -> None:
        """save_dir_alias 是手动指定的目录：与自动分类不一致时直接存入，不套用分类子目录。"""
        ctx = self._require_context(task_id)
        if ctx.state != TaskState.PENDING_APPROVAL:
            raise ConflictError("任务不是待审批状态")
        if save_dir_alias:
            self._validate_alias(save_dir_alias)
            ctx.save_dir_alias = save_dir_alias
            ctx.ns("dir_router")["pinned"] = save_dir_alias
        elif ctx.ns("dir_router").pop("pinned", None):
            ctx.save_dir_alias = None  # 撤销此前手动指定的目录，重新按分类
        ctx.state = TaskState.QUEUED
        ctx.log("管理端批准转存")
        self._update(ctx, expected=TaskState.PENDING_APPROVAL)

    async def reject(self, task_id: str) -> None:
        if not await self.pipeline.reject(task_id):
            raise ConflictError("任务不是待审批状态")

    async def retry(self, task_id: str) -> None:
        ctx = self._require_context(task_id)
        if ctx.state != TaskState.FAILED:
            raise ConflictError("只有失败任务可以重试")
        retry_from = ctx.ns("task").get("retry_from", TaskState.QUEUED.value)
        try:
            next_state = TaskState(retry_from)
        except ValueError:
            next_state = TaskState.QUEUED
        if next_state not in (TaskState.RECEIVED, TaskState.QUEUED, TaskState.STORED):
            raise ConflictError("任务重试阶段无效，请核验检查点")
        ctx.state = next_state
        ctx.ns("task")["generation"] = ctx.ns("task").get("generation", 0) + 1
        ctx.ns("task").pop("no_auto_retry", None)  # 人工已介入，之后的失败可再自动重试
        ctx.log("管理端请求重试")
        self._update(ctx, expected=TaskState.FAILED, reset_retries=True)

    async def reparse(self, task_id: str) -> None:
        ctx = self._require_context(task_id)
        self._require_editable(ctx)
        if ctx.ns("storage").get("manifest") or ctx.ns("storage").get("landed") \
                or ctx.ns("storage").get("phase") == "legacy_uncertain":
            raise ConflictError("已有转存检查点，不能重新解析；请核验转存或重新整理")
        previous = ctx.state
        ctx.ns("task")["generation"] = ctx.ns("task").get("generation", 0) + 1
        ctx.parsed = None
        ctx.decision = None
        ctx.result = None
        ctx.source_rule = self.pipeline._rule_for(ctx.raw.source)
        ctx.ns("runtime").update(config=copy.deepcopy(self.pipeline.cfg), revision=self.pipeline.revision)
        ctx.meta.pop("parse", None)
        ctx.meta.pop("preference", None)
        ctx.meta.pop("storage", None)  # 无远端写检查点，显式重新解析可重新选择 Provider。
        ctx.meta["parse_request"] = {"bypass_cache": True}  # 人工要求重新解析，不复用缓存结果
        ctx.state = TaskState.RECEIVED
        ctx.ns("task")["retry_from"] = TaskState.RECEIVED.value
        ctx.log("由管理端请求重新解析")
        self._update(ctx, expected=previous, reset_retries=True)

    async def requeue_organize(self, task_id: str, *, save_dir_alias: str | None = None) -> None:
        ctx = self._require_context(task_id)
        if ctx.state != TaskState.NEED_MANUAL:
            raise ConflictError("只有待人工整理任务可以重新整理")
        if save_dir_alias and save_dir_alias != ctx.save_dir_alias:
            self._validate_alias(save_dir_alias)
            ctx.save_dir_alias = save_dir_alias
            ctx.save_dir_cid = self.pipeline.cfg["save_dirs"][save_dir_alias]
            ctx.meta.pop("dir_router", None)  # 改到其他目录后不再套用分类子目录
            ctx.ns("organize").pop("plan", None)
            ctx.ns("organize").pop("base_cid", None)
        if not ctx.ns("storage").get("landed"):
            raise ConflictError("任务没有已落地文件记录，不能只重新整理")
        ctx.state = TaskState.QUEUED
        ctx.ns("task")["action"] = "organize_only"
        ctx.ns("task")["generation"] = ctx.ns("task").get("generation", 0) + 1
        ctx.ns("runtime").update(config=copy.deepcopy(self.pipeline.cfg), revision=self.pipeline.revision)
        ctx.ns("task")["retry_from"] = TaskState.QUEUED.value
        ctx.log("由管理端请求仅重新整理，不重复转存")
        self._update(ctx, expected=TaskState.NEED_MANUAL, reset_retries=True)

    async def reconcile_transfer(self, task_id: str) -> None:
        ctx = self._require_context(task_id)
        storage = ctx.ns("storage")
        if ctx.state != TaskState.NEED_MANUAL or storage.get("phase") != "receiving" \
                or not storage.get("manifest"):
            raise ConflictError("只有转存结果未知且有清单的任务可以核验")
        ctx.state = TaskState.QUEUED
        ctx.log("管理端请求只读核验转存，不重发转存请求")
        self._update(ctx, expected=TaskState.NEED_MANUAL)

    def patch_parsed(self, task_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        ctx = self._require_context(task_id)
        self._require_editable(ctx)
        storage = ctx.ns("storage")
        if storage.get("phase") == "legacy_uncertain" or (
            storage.get("manifest") and storage.get("phase") != "stored"
        ):
            raise ConflictError("转存结果未核验，不能修改解析结果")
        validate_resource(patch)
        if storage.get("landed") and set(patch) & {"share_code", "receive_code"}:
            raise ConflictError("已转存任务不能修改分享标识")
        values = asdict(ctx.parsed or ParsedResource())
        values.update(patch)
        ctx.parsed = ParsedResource(**validate_resource(values))
        ctx.ns("organize").pop("plan", None)
        ctx.log("管理端修正了解析结果")
        self._update(ctx, expected=ctx.state)
        return self.get_task(task_id)

    def organize_preview(
        self,
        *,
        parsed: dict[str, Any],
        landed: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        resource = ParsedResource(**validate_resource(parsed))
        from . import runtime
        with runtime.scope(self.pipeline.cfg):
            return Organizer()._build_plan(resource, landed)

    def get_config(self) -> dict[str, Any]:
        self.pipeline.reload_config()
        return copy.deepcopy(self.pipeline.cfg)

    def get_config_text(self) -> str:
        """返回配置原文；文件不存在时返回 JSON（合法 YAML 子集）。"""
        if self.config_path.exists():
            return self.config_path.read_text(encoding="utf-8")
        try:
            import yaml
            return yaml.safe_dump(self.get_config(), allow_unicode=True, sort_keys=False)
        except ImportError:
            import json
            return json.dumps(self.get_config(), ensure_ascii=False, indent=2)

    async def save_config_text(self, text: str) -> dict[str, Any]:
        try:
            import yaml
            config = yaml.safe_load(text) or {}
        except ImportError:
            import json
            try:
                config = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError("配置文本格式无效；未安装 PyYAML 时请使用 JSON") from exc
        except Exception as exc:
            raise ValueError("配置文本格式无效") from exc
        if not isinstance(config, dict):
            raise ValueError("配置根节点必须是对象")
        return await self.save_config(config)

    async def save_config(self, config: dict[str, Any]) -> dict[str, Any]:
        config = normalize_config(config)
        self.pipeline.reg.validate_config(config)
        try:
            import yaml
            rendered = yaml.safe_dump(config, allow_unicode=True, sort_keys=False)
        except ImportError:
            import json
            rendered = json.dumps(config, ensure_ascii=False, indent=2) + "\n"
        async with self._config_lock:
            target = self.config_path
            target.parent.mkdir(parents=True, exist_ok=True)
            import os
            import tempfile
            fd, name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
            temporary = Path(name)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as output:
                    output.write(rendered)
                    output.flush()
                    os.fsync(output.fileno())
                temporary.replace(target)
                self.pipeline.apply_config(config)
            finally:
                temporary.unlink(missing_ok=True)
        return config

    @staticmethod
    def validate_config(config: dict[str, Any]) -> None:
        normalize_config(config)

    def _require_context(self, task_id: str) -> PipelineContext:
        self.pipeline.reload_config()
        ctx = self.store.get_task(task_id)
        if not ctx:
            raise NotFoundError("任务不存在")
        return ctx

    def _validate_alias(self, alias: str) -> None:
        cid = self.pipeline.save_dirs.get(alias)
        if cid is None or not str(cid).isdecimal():
            raise ValueError(f"目标目录别名不存在或 CID 无效：{alias}")

    @staticmethod
    def _require_editable(ctx: PipelineContext) -> None:
        if ctx.state in (TaskState.RECEIVED, TaskState.PARSED, TaskState.QUEUED,
                         TaskState.SAVING, TaskState.STORED, TaskState.ORGANIZING, TaskState.DONE):
            raise ConflictError("排队中、执行中或已完成任务不能修改")

    def _update(self, ctx: PipelineContext, *, expected: TaskState,
                reset_retries: bool = False) -> None:
        try:
            self.store.update_task_context(ctx, expected=expected, reset_retries=reset_retries)
        except ValueError as exc:
            raise ConflictError(str(exc)) from exc
