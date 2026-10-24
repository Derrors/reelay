"""AI 能力：审批洞察与「改为 AI 处理」、调用记录、AI 服务设置（模型列表、连通性、推理强度）与一句话生成规则。

起草的规则只返回草案与改动清单，写入仍走带版本号的配置接口，由用户确认。
"""
from __future__ import annotations

from fastapi import APIRouter, Query
from pydantic import BaseModel

from ..core.config import config_revision


class SourcePayload(BaseModel):
    source: str


class CompilePayload(BaseModel):
    text: str
    scope: str = "defaults"


class LlmPayload(BaseModel):
    # 都可以省略：省略时用已保存的配置。地址与已保存的不同时要带上这个地址的密钥（api_key）
    base_url: str | None = None
    model: str | None = None
    reasoning_effort: str | None = None
    api_key: str | None = None


def router(service) -> APIRouter:
    api = APIRouter()

    @api.get("/api/insights")
    async def insights():
        return service.insights()

    @api.post("/api/insights/auto-approve")
    async def enable_auto(payload: SourcePayload):
        config = await service.enable_auto_approval(payload.source)
        return {"config": config, "revision": config_revision(config)}

    @api.get("/api/ai/usage")
    async def usage(days: int = Query(default=30, ge=1, le=90)):
        return service.llm_usage(days)

    @api.post("/api/ai/models")
    async def models(payload: LlmPayload):
        return await service.llm_models(payload.base_url, payload.api_key)

    @api.get("/api/ai/efforts")
    async def efforts(base_url: str | None = None, model: str | None = None):
        return service.llm_efforts(base_url, model)

    @api.post("/api/ai/efforts")
    async def probe_efforts(payload: LlmPayload):
        return await service.probe_llm_efforts(payload.base_url, payload.model, payload.api_key)

    @api.post("/api/ai/test")
    async def test(payload: LlmPayload):
        return await service.llm_test(base_url=payload.base_url, model=payload.model,
                                      reasoning_effort=payload.reasoning_effort, api_key=payload.api_key)

    @api.post("/api/ai/compile-rules")
    async def compile_rules(payload: CompilePayload):
        return await service.compile_rules(payload.text, payload.scope)

    return api
