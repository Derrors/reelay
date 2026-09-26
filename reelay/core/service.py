"""统一应用服务层：Web API 与 Telegram 控制 Bot 共用这里的规则。

服务只改变任务/配置的权威状态，不直接绕过 Pipeline 操作 115。这样两个入口并发操作时，
状态检查与错误语义一致。
"""

from __future__ import annotations

import asyncio
from dataclasses import fields
from pathlib import Path
from typing import Any

from .config import load_config
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
        self._config_lock = asyncio.Lock()

    def dashboard(self) -> dict[str, Any]:
        counts = self.store.counts_by_state()
        pending = counts.get(TaskState.PENDING_APPROVAL.value, 0) + counts.get(
            TaskState.NEED_MANUAL.value, 0
        )
        active = counts.get(TaskState.RECEIVED.value, 0) + counts.get(
            TaskState.QUEUED.value, 0
        ) + counts.get(TaskState.SAVING.value, 0)
        connections = self.connection_status()
        dry_run = __import__("os").getenv("REELAY_DRY_RUN", "").lower() in {
            "1", "true", "yes"
        }
        live_ready = connections["telegram"]["configured"] and connections["p115"]["configured"]
        return {
            "counts": counts,
            "pending": pending,
            "active": active,
            "failed": counts.get(TaskState.FAILED.value, 0),
            "done": counts.get(TaskState.DONE.value, 0),
            "connections": connections,
            "simulation": dry_run,
            "mode": "simulation" if dry_run else ("live" if live_ready else "not_configured"),
        }

    def connection_status(self) -> dict[str, dict[str, Any]]:
        import os

        return {
            "telegram": {
                "configured": bool(os.getenv("REELAY_TG_API_ID") and os.getenv("REELAY_TG_API_HASH")),
                "label": "Telegram 监听",
            },
            "control_bot": {
                "configured": bool(os.getenv("REELAY_CONTROL_BOT_TOKEN") and os.getenv("REELAY_OWNER_ID")),
                "label": "控制 Bot",
            },
            "p115": {
                "configured": bool(os.getenv("REELAY_115_COOKIES")),
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

    def list_tasks(self, **filters: Any) -> list[dict[str, Any]]:
        state_value = filters.pop("state", None)
        state = TaskState(state_value) if state_value else None
        return self.store.list_tasks(state=state, **filters)

    def get_task(self, task_id: str) -> dict[str, Any]:
        record = self.store.get_task_record(task_id)
        if not record:
            raise NotFoundError("任务不存在")
        return record

    async def approve(self, task_id: str, *, save_dir_alias: str | None = None) -> None:
        ctx = self._require_context(task_id)
        if ctx.state != TaskState.PENDING_APPROVAL:
            raise ConflictError("任务不是待审批状态")
        if save_dir_alias:
            ctx.save_dir_alias = save_dir_alias
            self.store.update_task_context(ctx)
        if not await self.pipeline.approve(task_id):
            raise ConflictError("任务不是待审批状态")

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
        if not self.store.set_state(task_id, next_state):
            raise NotFoundError("任务不存在")

    async def reparse(self, task_id: str) -> None:
        ctx = self._require_context(task_id)
        if ctx.state in (TaskState.SAVING, TaskState.DONE):
            raise ConflictError("执行中或已完成任务不能重新解析")
        ctx.parsed = None
        ctx.decision = None
        ctx.result = None
        ctx.state = TaskState.RECEIVED
        ctx.ns("task")["retry_from"] = TaskState.RECEIVED.value
        ctx.log("由管理端请求重新解析")
        self.store.update_task_context(ctx)

    async def requeue_organize(self, task_id: str, *, save_dir_alias: str | None = None) -> None:
        ctx = self._require_context(task_id)
        if ctx.state != TaskState.NEED_MANUAL:
            raise ConflictError("只有待人工整理任务可以重新整理")
        if save_dir_alias:
            ctx.save_dir_alias = save_dir_alias
        if not ctx.ns("storage").get("landed"):
            raise ConflictError("任务没有已落地文件记录，不能只重新整理")
        ctx.state = TaskState.QUEUED
        ctx.ns("task")["action"] = "organize_only"
        ctx.ns("task")["retry_from"] = TaskState.QUEUED.value
        ctx.log("由管理端请求仅重新整理，不重复转存")
        self.store.update_task_context(ctx)

    def patch_parsed(self, task_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        ctx = self._require_context(task_id)
        if ctx.state in (TaskState.SAVING, TaskState.DONE):
            raise ConflictError("执行中或已完成任务不能修改解析结果")
        allowed = {field.name for field in fields(ParsedResource)}
        invalid = set(patch) - allowed
        if invalid:
            raise ValueError(f"不支持的字段：{', '.join(sorted(invalid))}")
        if ctx.parsed is None:
            ctx.parsed = ParsedResource()
        for key, value in patch.items():
            setattr(ctx.parsed, key, value)
        ctx.log("管理端修正了解析结果")
        self.store.update_task_context(ctx)
        return self.get_task(task_id)

    def organize_preview(
        self,
        *,
        parsed: dict[str, Any],
        landed: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        resource = ParsedResource(**parsed)
        return Organizer()._build_plan(resource, landed)

    def get_config(self) -> dict[str, Any]:
        return load_config(str(self.config_path))

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
                raise RuntimeError("当前未安装 PyYAML；请使用 JSON 或安装 web 依赖") from exc
        if not isinstance(config, dict):
            raise ValueError("配置根节点必须是对象")
        return await self.save_config(config)

    async def save_config(self, config: dict[str, Any]) -> dict[str, Any]:
        self.validate_config(config)
        try:
            import yaml
            rendered = yaml.safe_dump(config, allow_unicode=True, sort_keys=False)
        except ImportError:
            import json
            rendered = json.dumps(config, ensure_ascii=False, indent=2) + "\n"
        async with self._config_lock:
            target = self.config_path
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_suffix(target.suffix + ".tmp")
            temporary.write_text(rendered, encoding="utf-8")
            temporary.replace(target)
            self.pipeline.cfg = config
            self.pipeline.save_dirs = config.get("save_dirs", {})
            from . import runtime
            runtime.set_config(config)
        return config

    @staticmethod
    def validate_config(config: dict[str, Any]) -> None:
        if not isinstance(config.get("save_dirs", {}), dict):
            raise ValueError("save_dirs 必须是对象")
        sources = config.get("sources", [])
        if not isinstance(sources, list):
            raise ValueError("sources 必须是数组")
        for index, source in enumerate(sources):
            if not source.get("bot"):
                raise ValueError(f"sources[{index}] 缺少 bot")
            if source.get("approval_mode", "manual") not in {
                "manual", "auto", "default_pass"
            }:
                raise ValueError(f"sources[{index}] approval_mode 无效")
        routing = config.get("routing", [])
        if not isinstance(routing, list):
            raise ValueError("routing 必须是数组")

    def _require_context(self, task_id: str) -> PipelineContext:
        ctx = self.store.get_task(task_id)
        if not ctx:
            raise NotFoundError("任务不存在")
        return ctx
