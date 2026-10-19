"""AI 能力的管理接口：审批洞察、调用记录、规则起草、订阅、收件箱建议、来源接入与来源质量。

业务都在 ManagementService；这里只做参数解析与错误映射。AI 起草的内容（规则、来源配置）
只返回草案与改动清单，写入仍走带版本号的配置接口，由用户确认。
"""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Query
from pydantic import BaseModel, Field

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


class TextPayload(BaseModel):
    text: str


class SubscriptionPayload(BaseModel):
    title: str
    season: int | None = None
    tmdbid: int | None = None
    total: int | None = None
    auto: bool = True


class TaskIdsPayload(BaseModel):
    task_ids: list[str] = Field(default_factory=list)


class DraftPayload(BaseModel):
    bot: str
    samples: list[str] = Field(default_factory=list)


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

    # 剧集订阅
    @app.get("/api/subscriptions")
    async def subscriptions():
        return await call(service.list_subscriptions)

    @app.post("/api/subscriptions/resolve")
    async def resolve_subscription(payload: TextPayload):
        return await call(service.resolve_subscription, payload.text)

    @app.post("/api/subscriptions")
    async def add_subscription(payload: SubscriptionPayload):
        return await call(service.add_subscription, payload.model_dump())

    @app.post("/api/subscriptions/from-task/{task_id}")
    async def subscribe_from_task(task_id: str):
        return await call(service.subscribe_from_task, task_id)

    @app.post("/api/subscriptions/remove")
    async def remove_subscription(payload: SubscriptionPayload):
        await call(service.remove_subscription, payload.model_dump())
        return {"ok": True}

    # 收件箱建议：先返回规则写的理由，前端再请求 AI 措辞（按事实指纹缓存）
    @app.post("/api/inbox/advice")
    async def inbox_advice(payload: TaskIdsPayload):
        return {"items": await call(service.phrase_advice, payload.task_ids)}

    # 新来源接入向导
    @app.post("/api/sources/draft")
    async def draft_source(payload: DraftPayload):
        return await call(service.draft_source, payload.bot, payload.samples)

    # 来源质量：规则统计 + AI 调整建议（建议的规则改动需用户确认后经配置接口写入）
    @app.get("/api/sources/quality")
    async def source_quality(days: int = Query(default=30, ge=1, le=365)):
        return await call(service.source_quality, days)

    @app.post("/api/sources/quality/advice")
    async def source_quality_advice(days: int = Query(default=30, ge=1, le=365)):
        return await call(service.source_quality_advice, days)

    # 媒体库问答（控制 Bot 的查询工具同样使用）
    @app.get("/api/library/query")
    async def library_query(q: str | None = None, media_type: str | None = None,
                            min_resolution: str | None = None, origin_country: str | None = None,
                            genre_id: int | None = None, limit: int = Query(default=30, ge=1, le=100)):
        return await call(service.library_query, query=q, media_type=media_type, min_resolution=min_resolution,
                          origin_country=origin_country, genre_id=genre_id, limit=limit)

    @app.get("/api/library/missing")
    async def library_missing(title: str | None = None):
        return await call(service.missing_episodes, title)
