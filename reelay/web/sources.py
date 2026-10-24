"""来源：测试识别、新来源接入向导、来源的识别规则（取样例、AI 起草、试跑）与来源质量。

起草与建议都只返回草案，写入仍走带版本号的配置接口，由用户确认。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field


class ParseTestPayload(BaseModel):
    text: str
    urls: list[str] = Field(default_factory=list)
    source: str | None = None
    # 来源卡片上还没保存的设置（sources[] 的一项）；省略时按已保存的来源规则
    draft: dict[str, Any] | None = None


class DraftPayload(BaseModel):
    bot: str
    samples: list[str] = Field(default_factory=list)


class ParseRulesPayload(BaseModel):
    bot: str = ""
    samples: list[str] = Field(default_factory=list)
    rules: dict[str, str] = Field(default_factory=dict)


def router(service) -> APIRouter:
    api = APIRouter()

    @api.post("/api/sources/test-parse")
    async def test_parse(payload: ParseTestPayload):
        """与真实管线一致：合并默认规则 → 解析 → after_parse 扩展 → 确定性置信度 → 决策；不落库。"""
        rule = service.draft_source_rule(payload.draft) if payload.draft is not None else None
        return await service.test_parse(payload.text, payload.urls, payload.source, rule=rule)

    @api.post("/api/sources/draft")
    async def draft_source(payload: DraftPayload):
        return await service.draft_source(payload.bot, payload.samples)

    @api.get("/api/sources/messages")
    async def source_messages(bot: str, limit: int = Query(default=10, ge=1, le=20)):
        return await service.source_messages(bot, limit)

    @api.post("/api/sources/parse-rules/draft")
    async def draft_parse_rules(payload: ParseRulesPayload):
        return await service.draft_parse_rules(payload.bot, payload.samples)

    @api.post("/api/sources/parse-rules/preview")
    async def preview_parse_rules(payload: ParseRulesPayload):
        return service.preview_parse_rules(payload.rules, payload.samples)

    @api.get("/api/sources/quality")
    async def source_quality(days: int = Query(default=30, ge=1, le=365)):
        return service.source_quality(days)

    @api.post("/api/sources/quality/advice")
    async def source_quality_advice(days: int = Query(default=30, ge=1, le=365)):
        return await service.source_quality_advice(days)

    return api
