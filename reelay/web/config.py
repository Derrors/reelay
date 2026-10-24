"""配置：读取、按块保存（带版本号）、校验，以及插件声明的动作（如命名预览）。"""
from __future__ import annotations

import inspect
import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..core import runtime
from ..core.config import config_revision


class SectionsPayload(BaseModel):
    revision: str
    sections: dict[str, Any]


class ConfigPayload(BaseModel):
    config: Any


def router(service, registry) -> APIRouter:
    api = APIRouter()

    @api.get("/api/config")
    async def get_config():
        config = service.get_config()
        return {
            "config": config,
            "revision": config_revision(config),
            "config_error": service.pipeline.config_error,
            "connections": service.connection_status(),
            "extensions": registry.catalog(config),
            "secrets": service.secret_status(),
            # 各目录别名在网盘里的路径（主进程读到后记录），管理台显示目录名不用再查 115
            "save_dir_paths": service.pipeline.dir_paths(),
        }

    @api.patch("/api/config/sections")
    async def save_sections(payload: SectionsPayload):
        return await service.save_config_sections(payload.sections, payload.revision)

    @api.post("/api/config/validate")
    async def validate(payload: ConfigPayload):
        service.check_config(payload.config)
        return {"ok": True}

    @api.post("/api/extensions/{name}/actions/{action}")
    async def extension_action(name: str, action: str, request: Request):
        """扩展在 ui_actions 里声明的动作（如命名模板预览）：请求与返回都是 JSON 对象。

        扩展不接触 Web 框架，ValueError / TypeError 视为输入有误（422）。
        扩展未安装或没有声明这个动作时返回 404，删除插件目录不会让接口报错。
        """
        method = registry.action(name, action)
        if method is None:
            raise HTTPException(status_code=404, detail=f"扩展 {name} 没有动作 {action}")
        body = await request.body()
        try:
            payload = json.loads(body) if body.strip() else {}
        except ValueError:
            payload = None
        if not isinstance(payload, dict):
            raise HTTPException(status_code=422, detail="请求体必须是 JSON 对象")
        try:
            with runtime.scope(service.pipeline.cfg):
                result = method(payload)
                if inspect.isawaitable(result):
                    result = await result
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if not isinstance(result, dict):
            raise HTTPException(status_code=500, detail=f"扩展 {name} 的动作 {action} 必须返回对象")
        return result

    return api
