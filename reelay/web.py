"""FastAPI 管理服务。

开发期通过 Vite 代理访问 ``/api``；生产构建后若 ``web/dist/output`` 存在，会由同一个
Python 服务托管 SPA。默认只绑定本机，部署到局域网/公网时应在外层加访问控制。
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

try:
    from fastapi import FastAPI, HTTPException, Query
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles
    from pydantic import BaseModel, Field
except ImportError as exc:  # pragma: no cover - 仅在未装 web 依赖时触发
    raise RuntimeError('Web 管理服务未安装，请执行 pip install -e ".[web]"') from exc

from .core.config import load_config, load_env
from .core.contracts import TaskState
from .core.pipeline import Pipeline
from .core.registry import discover
from .core.service import ConflictError, ManagementService, NotFoundError
from .core.store import Store
from .core.worker import Worker

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ActionPayload(BaseModel):
    save_dir_alias: str | None = None


class ParsePatchPayload(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)


class OrganizePreviewPayload(BaseModel):
    parsed: dict[str, Any]
    landed: list[dict[str, Any]] = Field(default_factory=list)


class ConfigPayload(BaseModel):
    config: dict[str, Any]


class ConfigTextPayload(BaseModel):
    text: str


def _error(exc: Exception) -> HTTPException:
    if isinstance(exc, NotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, ConflictError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(status_code=422, detail=str(exc))
    return HTTPException(status_code=500, detail=str(exc))


def create_app(
    *,
    db_path: str | None = None,
    config_path: str | None = None,
    start_worker: bool = False,
    frontend_dir: str | Path | None = None,
) -> FastAPI:
    load_env(str(PROJECT_ROOT / ".env"))
    config_file = config_path or os.getenv("REELAY_CONFIG_PATH", str(PROJECT_ROOT / "config.yaml"))
    cfg = load_config(config_file)
    registry = discover()
    registry.validate_config(cfg)
    store = Store(db_path or os.getenv("REELAY_DB_PATH", str(PROJECT_ROOT / "data/reelay.db")))
    pipeline = Pipeline(registry, store, cfg)
    service = ManagementService(store, pipeline, config_path=config_file)
    worker = Worker(
        pipeline,
        store,
        concurrency=int(os.getenv("REELAY_WORKER_CONCURRENCY", "3")),
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.store = store
        app.state.pipeline = pipeline
        app.state.service = service
        app.state.worker = worker
        worker_task = None
        if start_worker:
            import asyncio
            worker_task = asyncio.create_task(worker.run(drain=False))
        try:
            yield
        finally:
            worker.stop()
            if worker_task:
                worker_task.cancel()
                from contextlib import suppress
                import asyncio
                with suppress(asyncio.CancelledError):
                    await worker_task
            store.close()

    app = FastAPI(title="Reelay Management API", version="0.2.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:28641", "http://127.0.0.1:28641"],
        allow_methods=["GET", "POST", "PUT", "PATCH"],
        allow_headers=["Content-Type"],
    )

    @app.get("/api/health")
    async def health():
        return {"ok": True, "service": "reelay", "runtime": store.health_status(),
                "deliveries": store.delivery_counts()}

    @app.get("/api/dashboard")
    async def dashboard():
        return service.dashboard()

    @app.get("/api/tasks")
    async def tasks(
        state: str | None = None,
        source: str | None = None,
        query: str | None = None,
        limit: int = Query(default=100, ge=1, le=500),
    ):
        try:
            return {"items": service.list_tasks(state=state, source=source, query=query, limit=limit)}
        except Exception as exc:
            raise _error(exc)

    @app.get("/api/deliveries")
    async def deliveries(status: str = "failed", limit: int = Query(default=100, ge=1, le=500)):
        try:
            return {"items": store.list_deliveries(status=status, limit=limit)}
        except ValueError as exc:
            raise _error(exc)

    @app.post("/api/deliveries/{delivery_id}/retry")
    async def retry_delivery(delivery_id: int):
        if not store.retry_delivery(delivery_id):
            raise HTTPException(status_code=409, detail="只有投递失败项可以重试")
        return {"ok": True}

    @app.get("/api/tasks/{task_id}")
    async def task_detail(task_id: str):
        try:
            return service.get_task(task_id)
        except Exception as exc:
            raise _error(exc)

    @app.post("/api/tasks/{task_id}/approve")
    async def approve(task_id: str, payload: ActionPayload | None = None):
        try:
            await service.approve(
                task_id,
                save_dir_alias=payload.save_dir_alias if payload else None,
            )
            return {"ok": True}
        except Exception as exc:
            raise _error(exc)

    @app.post("/api/tasks/{task_id}/reject")
    async def reject(task_id: str):
        try:
            await service.reject(task_id)
            return {"ok": True}
        except Exception as exc:
            raise _error(exc)

    @app.post("/api/tasks/{task_id}/retry")
    async def retry(task_id: str):
        try:
            await service.retry(task_id)
            return {"ok": True}
        except Exception as exc:
            raise _error(exc)

    @app.post("/api/tasks/{task_id}/reparse")
    async def reparse(task_id: str):
        try:
            await service.reparse(task_id)
            return {"ok": True}
        except Exception as exc:
            raise _error(exc)

    @app.post("/api/tasks/{task_id}/reorganize")
    async def reorganize(task_id: str, payload: ActionPayload):
        try:
            await service.requeue_organize(task_id, save_dir_alias=payload.save_dir_alias)
            return {"ok": True}
        except Exception as exc:
            raise _error(exc)

    @app.post("/api/tasks/{task_id}/reconcile")
    async def reconcile(task_id: str):
        try:
            await service.reconcile_transfer(task_id)
            return {"ok": True}
        except Exception as exc:
            raise _error(exc)

    @app.patch("/api/tasks/{task_id}/parsed")
    async def patch_parsed(task_id: str, payload: ParsePatchPayload):
        try:
            return service.patch_parsed(task_id, payload.values)
        except Exception as exc:
            raise _error(exc)

    @app.post("/api/organize/preview")
    async def organize_preview(payload: OrganizePreviewPayload):
        try:
            return {"items": service.organize_preview(parsed=payload.parsed, landed=payload.landed)}
        except Exception as exc:
            raise _error(exc)

    @app.get("/api/config")
    async def get_config():
        return {
            "config": service.get_config(),
            "text": service.get_config_text(),
            "connections": service.connection_status(),
            "config_error": pipeline.config_error,
        }

    @app.put("/api/config")
    async def put_config(payload: ConfigPayload):
        try:
            return {"config": await service.save_config(payload.config)}
        except Exception as exc:
            raise _error(exc)

    @app.put("/api/config/text")
    async def put_config_text(payload: ConfigTextPayload):
        try:
            config = await service.save_config_text(payload.text)
            return {"config": config, "text": service.get_config_text()}
        except Exception as exc:
            raise _error(exc)

    frontend = Path(frontend_dir or PROJECT_ROOT / "web" / "dist" / "output").resolve()
    frontend_assets = PROJECT_ROOT / "web" / "dist" / "output_resource" / "assets"
    if frontend.exists():
        if frontend_assets.exists():
            app.mount("/assets", StaticFiles(directory=frontend_assets), name="assets")

        @app.get("/{path:path}")
        async def spa(path: str):
            candidate = (frontend / path).resolve()
            if not candidate.is_relative_to(frontend):
                raise HTTPException(status_code=404, detail="文件不存在")
            if path and candidate.is_file():
                return FileResponse(candidate)
            index = (frontend / "index.html").resolve()
            if not index.is_relative_to(frontend) or not index.is_file():
                raise HTTPException(status_code=404, detail="文件不存在")
            return FileResponse(index)

    return app


def main() -> None:
    try:
        import uvicorn
    except ImportError as exc:
        raise RuntimeError('Web 管理服务未安装，请执行 pip install -e ".[web]"') from exc
    uvicorn.run(
        "reelay.web:create_app",
        factory=True,
        host=os.getenv("REELAY_WEB_HOST", "127.0.0.1"),
        port=int(os.getenv("REELAY_WEB_PORT", "28080")),
        reload=False,
    )


if __name__ == "__main__":
    main()
