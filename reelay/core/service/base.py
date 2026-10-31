"""服务层的公共部分：错误类型、裁决样本、AI 客户端与任务操作共用的检查。"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .. import ai
from ..contracts import ParsedResource, PipelineContext, TaskState
from ..pipeline import Pipeline
from ..store import Store

log = logging.getLogger("reelay.service")


class ConflictError(RuntimeError):
    """当前状态不允许这个操作（如任务不是待审批），对应 HTTP 409。"""


class NotFoundError(RuntimeError):
    """要操作的对象不存在，对应 HTTP 404。"""


def parsed_sample(parsed: ParsedResource | None) -> dict | None:
    """写入裁决样本的识别结果：去掉分享标识，原始字段只留 tmdbid。"""
    if parsed is None:
        return None
    values = asdict(parsed)
    values.pop("share_code", None)
    values.pop("receive_code", None)
    values.pop("ed2k", None)
    raw = values.pop("raw_fields", None) or {}
    if raw.get("tmdbid"):
        values["tmdbid"] = raw["tmdbid"]
    return values


class ServiceBase:
    """各领域服务共用的状态：Store、Pipeline 与配置文件。"""

    def __init__(
        self,
        store: Store,
        pipeline: Pipeline,
        *,
        config_path: str | Path,
    ) -> None:
        self.store = store
        self.pipeline = pipeline
        self.config_path = Path(config_path)
        self.pipeline.config_path = self.config_path
        self._config_lock = asyncio.Lock()

    def _record(self, ctx: PipelineContext, kind: str, label: dict, advice: str | None = None) -> None:
        self.store.add_feedback(task_id=ctx.task_id, source=ctx.raw.source, kind=kind, text=ai.scrub(ctx.raw.text),
                                parsed=parsed_sample(ctx.parsed), label=label, advice=advice)

    # ------------------------------------------------------------------ #
    # AI 客户端（与解析器共用；测试替换解析器的 _client 即可覆盖）
    # ------------------------------------------------------------------ #
    def ai_client(self) -> tuple[Any, str]:
        parser = self.pipeline.reg.providers.get("parser", {}).get("llm")
        if parser is not None:
            return parser._client, parser._model
        return ai.make_client()

    def ai_scope(self):
        """服务层发起的 AI 调用也记入调用记录。"""
        return self.pipeline.scope(self.pipeline.cfg)

    def _require_ai(self) -> tuple[Any, str]:
        client, model = self.ai_client()
        if client is None:
            raise ConflictError("未配置 AI 接口密钥（「设置 → AI 与 TMDB」中填写），无法使用这项功能")
        return client, model

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
        from .tasks import _BUSY
        if ctx.state in _BUSY:
            raise ConflictError("排队中、执行中或已完成任务不能修改")

    def _update(self, ctx: PipelineContext, *, expected: TaskState,
                reset_retries: bool = False) -> None:
        try:
            self.store.update_task_context(ctx, expected=expected, reset_retries=reset_retries)
        except ValueError as exc:
            raise ConflictError(str(exc)) from exc
