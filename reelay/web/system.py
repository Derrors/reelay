"""运行概况：健康检查、总览、实时推送、运行日志与通知投递。"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from .auth import is_authenticated
from .realtime import event_stream


def router(service, store, bus, *, log_file: Path) -> APIRouter:
    api = APIRouter()

    @api.get("/api/health")
    async def health(request: Request):
        # 容器健康检查无需登录；运行细节（含错误摘要）只给已登录用户。
        if not is_authenticated(request, store):
            return {"ok": True, "service": "reelay"}
        return {"ok": True, "service": "reelay", "runtime": store.health_status(),
                "deliveries": store.delivery_counts()}

    @api.get("/api/dashboard")
    async def dashboard():
        return service.dashboard()

    @api.get("/api/events")
    async def events(request: Request):
        """SSE：状态变化时推送 {type:'state', changed:[...]}；退出登录后推送流随之结束。

        反向代理需要关闭这个路径的缓冲（响应已带 X-Accel-Buffering: no）。
        """
        headers = {"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"}
        return StreamingResponse(event_stream(bus, lambda: is_authenticated(request, store)),
                                 media_type="text/event-stream", headers=headers)

    @api.get("/api/logs")
    async def logs(limit: int = Query(default=400, ge=1, le=5000), q: str | None = None):
        if not log_file.is_file():
            return {"lines": [], "path": str(log_file), "exists": False}
        # 只取末尾，避免大文件全读
        with log_file.open("rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(size - min(size, 200_000))
            lines = fh.read().decode("utf-8", errors="replace").splitlines()[-limit:]
        if q:
            lines = [line for line in lines if q.lower() in line.lower()]
        return {"lines": lines, "path": str(log_file), "exists": True}

    @api.post("/api/logs/clear")
    async def clear_logs():
        """清空运行日志：当前文件截断为空，删除轮转留下的 reelay.log.1 … .N。

        主进程以追加方式写日志，截断后从文件开头接着写，不需要重启。
        """
        freed = 0
        if log_file.is_file():
            freed += log_file.stat().st_size
            with log_file.open("r+b") as fh:
                fh.truncate(0)
        for old in log_file.parent.glob(f"{log_file.name}.*"):
            if old.suffix[1:].isdecimal() and old.is_file():
                freed += old.stat().st_size
                old.unlink()
        return {"ok": True, "freed": freed}

    @api.get("/api/deliveries")
    async def deliveries(status: str = "failed", limit: int = Query(default=100, ge=1, le=500)):
        return {"items": store.list_deliveries(status=status, limit=limit)}

    @api.post("/api/deliveries/{delivery_id}/retry")
    async def retry_delivery(delivery_id: int):
        if not store.retry_delivery(delivery_id):
            raise HTTPException(status_code=409, detail="只有投递失败项可以重试")
        return {"ok": True}

    return api
