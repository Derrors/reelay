"""媒体库与统计。"""
from __future__ import annotations

from fastapi import APIRouter, Query


def router(service) -> APIRouter:
    api = APIRouter()

    @api.get("/api/library")
    async def library(q: str | None = None, media_type: str | None = None):
        """已入库作品（不限条数，按作品汇总后返回），与 MCP 的 search_library 同一套汇总。"""
        return service.library(query=q, media_type=media_type)

    @api.get("/api/stats")
    async def stats(days: int = Query(default=30, ge=1, le=365)):
        return service.stats(days)

    return api
