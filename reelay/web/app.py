"""组装管理服务：服务层、各资源的路由、访问守卫、统一的错误响应、实时推送、/mcp 与前端静态文件。"""
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager, nullcontext, suppress
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .. import __version__
from ..core import paths
from ..core.config import ensure_config, load_config, load_env
from ..core.contracts import StorageBusy
from ..core.pipeline import Pipeline
from ..core.registry import discover
from ..core.service import ConflictError, ManagementService, NotFoundError
from ..core.store import Store
from . import ai, auth, config, follow, library, p115, sources, system, tasks
from .realtime import EventBus, close_all_streams

log = logging.getLogger("reelay.web")


def _error_responses(app: FastAPI) -> None:
    """服务层的错误统一映射：不存在 404，状态冲突 409，输入不合格 422，网盘暂时不可用 503；
    其余错误记日志，只回一句通用说明，不把内部异常文本交给浏览器。"""
    def reply(status: int):
        async def handle(request: Request, exc: Exception) -> JSONResponse:
            return JSONResponse({"detail": str(exc)}, status_code=status)
        return handle

    for error, status in ((NotFoundError, 404), (ConflictError, 409), (ValueError, 422), (StorageBusy, 503)):
        app.add_exception_handler(error, reply(status))

    async def unexpected(request: Request, exc: Exception) -> JSONResponse:
        log.error("接口出错：%s %s", request.method, request.url.path, exc_info=exc)
        return JSONResponse({"detail": "服务器内部错误，详情见运行日志"}, status_code=500)

    app.add_exception_handler(Exception, unexpected)


def _frontend(app: FastAPI, frontend: Path) -> None:
    """生产构建（web/dist）由同一个服务托管；未命中的 /api/* 返回 404，其余路径回落到 index.html。"""
    if not frontend.exists():
        return
    if (frontend / "assets").exists():
        app.mount("/assets", StaticFiles(directory=frontend / "assets"), name="assets")
    index = (frontend / "index.html").resolve()

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str):
        if path.startswith("api/"):
            raise HTTPException(status_code=404, detail="接口不存在")
        candidate = (frontend / path).resolve()
        if not candidate.is_relative_to(frontend):
            raise HTTPException(status_code=404, detail="文件不存在")
        if path and candidate.is_file():
            return FileResponse(candidate)
        # index 必须是 frontend 内的普通文件（拒绝越界软链）
        if not index.is_relative_to(frontend) or not index.is_file() or index.is_symlink():
            raise HTTPException(status_code=404, detail="文件不存在")
        return FileResponse(index)


def create_app(*, data_dir: str | Path | None = None, frontend_dir: str | Path | None = None) -> FastAPI:
    """data_dir 默认是项目的 data/（数据库、配置、密钥、日志都在里面）；测试传临时目录。"""
    data = Path(data_dir or paths.DATA)
    ensure_config(data / paths.CONFIG_NAME)
    paths.use_data_dir(data)
    config_file = data / paths.CONFIG_NAME
    cfg = load_config(config_file)
    registry = discover()
    registry.validate_config(cfg)
    store = Store(str(data / paths.DATABASE_NAME))
    pipeline = Pipeline(registry, store, cfg)
    service = ManagementService(store, pipeline, config_path=config_file)
    bus = EventBus(store=store)
    from ..mcp_server import http_endpoint
    mcp = http_endpoint(service)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.store, app.state.pipeline, app.state.service, app.state.bus = store, pipeline, service, bus
        bus.start()
        try:
            async with mcp.run() if mcp else nullcontext():
                yield
        finally:
            await bus.stop()
            store.close()

    app = FastAPI(title="Reelay Management API", version=__version__, lifespan=lifespan,
                  docs_url="/api/docs", redoc_url=None, openapi_url="/api/openapi.json")
    # 守卫在内层、CORS 在外层（后加的在外）：开发时 Vite 跨端口访问的预检请求由 CORS 直接应答
    app.add_middleware(auth.Guard, store=store)
    app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:28641", "http://127.0.0.1:28641"],
                       allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"], allow_headers=["Content-Type"])
    _error_responses(app)
    for api in (auth.router(store, service), system.router(service, store, bus, log_file=data / paths.LOG.name),
                tasks.router(service), config.router(service, registry), p115.router(service),
                sources.router(service), follow.router(service), library.router(service), ai.router(service)):
        app.include_router(api)
    if mcp:
        # 不在 /api 下、不走管理台登录：端点自己按 MCP 访问令牌鉴权
        app.add_route("/mcp", mcp, methods=["GET", "POST", "DELETE"])
    _frontend(app, Path(frontend_dir or paths.PROJECT_ROOT / "web" / "dist").resolve())
    return app


def main() -> None:
    try:
        import uvicorn
    except ImportError as exc:
        raise RuntimeError('Web 管理服务未安装，请执行 pip install -e ".[web]"') from exc

    class Server(uvicorn.Server):
        def handle_exit(self, sig, frame) -> None:
            """开始停止时先结束 SSE 推送流，否则要等 systemd / Docker 超时强制结束。"""
            super().handle_exit(sig, frame)
            # 信号处理函数可能打断事件循环里的任意代码，交给事件循环去执行
            with suppress(RuntimeError):  # 事件循环还没启动
                asyncio.get_running_loop().call_soon_threadsafe(close_all_streams)

    load_env(str(paths.ENV_FILE))  # 监听地址、端口与信任的代理也可以写在 .env 里
    server = Server(uvicorn.Config(
        "reelay.web:create_app",
        factory=True,
        host=os.getenv("REELAY_WEB_HOST", "127.0.0.1"),
        port=int(os.getenv("REELAY_WEB_PORT", "28080")),
        # 在反向代理后面时信任它转发的客户端地址（X-Forwarded-For），登录限流才按真实客户端计数
        proxy_headers=True,
        forwarded_allow_ips=os.getenv("REELAY_FORWARDED_ALLOW_IPS") or "127.0.0.1",
        reload=False,
        # 兜底：其他长连接（如 MCP 客户端的推送流）最多再等 5 秒，之后由 uvicorn 取消
        timeout_graceful_shutdown=5,
    ))
    with suppress(KeyboardInterrupt):  # 与 uvicorn.run 一致：Ctrl+C 不打印回溯
        server.run()
    if not server.started:
        raise SystemExit(3)  # 与 uvicorn.run 一致：启动失败时非零退出，systemd 据此重启
