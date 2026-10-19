"""AI 能力的管理接口：审批洞察、调用记录、规则起草、订阅、收件箱建议、来源接入与来源质量。

业务都在 ManagementService；这里只做参数解析与错误映射。AI 起草的内容（规则、来源配置）
只返回草案与改动清单，写入仍走带版本号的配置接口，由用户确认。
"""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Query
from pydantic import BaseModel

from .core.service import ConflictError, NotFoundError


def _error(exc: Exception) -> HTTPException:
    if isinstance(exc, NotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, ConflictError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(status_code=422, detail=str(exc))
    return HTTPException(status_code=500, detail=str(exc))


class SourcePayload(BaseModel):
    source: str


class CompilePayload(BaseModel):
    text: str
    scope: str = "defaults"


def register_ai(app, *, service) -> None:
    async def call(fn, *args, **kwargs) -> Any:
        try:
            result = fn(*args, **kwargs)
            if hasattr(result, "__await__"):
                result = await result
            return result
        except Exception as exc:
            raise _error(exc) from exc

    @app.get("/api/insights")
    async def insights():
        return await call(service.insights)

    @app.post("/api/insights/auto-approve")
    async def enable_auto(payload: SourcePayload):
        from .core.config import config_revision
        config = await call(service.enable_auto_approval, payload.source)
        return {"config": config, "revision": config_revision(config)}

    @app.get("/api/ai/usage")
    async def ai_usage(days: int = Query(default=30, ge=1, le=90)):
        return service.llm_usage(days)

    @app.post("/api/ai/compile-rules")
    async def compile_rules(payload: CompilePayload):
        return await call(service.compile_rules, payload.text, payload.scope)
