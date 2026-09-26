"""Web 控制台增强 API：鉴权 / 日志 / 115 浏览 / 源测试解析 / 统计。

刻意与核心管线解耦：只读为主，写操作（源管理）复用 ManagementService，
避免把控制台需求渗进内核。
"""
from __future__ import annotations

import hmac
import json
import os
import re
import secrets
import time
from pathlib import Path
from typing import Any

from fastapi import Depends, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

# 会话保存在内存即可：单实例个人工具，重启后要求重新登录。
_SESSIONS: set[str] = set()

def _parse_size_gb(value: Any) -> float:
    """把 '1.46 GB' / '800 MB' / 字节数换算成 GB。"""
    if value in (None, ""):
        return 0.0
    if isinstance(value, (int, float)):
        v = float(value)
        return v / 1024 ** 3 if v > 100000 else v
    m = re.match(r"\s*([0-9.]+)\s*([KMGT]?B|GB|MB|KB|TB)", str(value), re.I)
    if not m:
        return 0.0
    num = float(m.group(1)); unit = m.group(2).upper()
    return num * {"B": 1 / 1024 ** 3, "KB": 1 / 1024 ** 2,
                  "MB": 1 / 1024, "GB": 1, "TB": 1024}.get(unit, 0.0)

_COOKIE = "reelay_session"
_SESSION_TTL = 7 * 24 * 3600


def _password() -> str | None:
    return os.getenv("REELAY_WEB_PASSWORD") or None


def auth_enabled() -> bool:
    return bool(_password())


class LoginPayload(BaseModel):
    password: str


class ParseTestPayload(BaseModel):
    text: str
    urls: list[str] = Field(default_factory=list)
    source: str | None = None


def register_extras(app, *, project_root: Path, store, service, registry) -> None:
    # ------------------------------------------------------------------ #
    # 1) 鉴权
    # ------------------------------------------------------------------ #
    @app.post("/api/auth/login")
    async def login(payload: LoginPayload):
        pw = _password()
        if not pw:
            return {"ok": True, "auth_required": False}
        if not hmac.compare_digest(payload.password, pw):
            raise HTTPException(status_code=401, detail="密码错误")
        token = secrets.token_urlsafe(32)
        _SESSIONS.add(token)
        resp = JSONResponse({"ok": True, "auth_required": True})
        resp.set_cookie(_COOKIE, token, httponly=True, samesite="lax",
                        max_age=_SESSION_TTL, path="/")
        return resp

    @app.post("/api/auth/logout")
    async def logout(request: Request):
        token = request.cookies.get(_COOKIE)
        if token:
            _SESSIONS.discard(token)
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(_COOKIE, path="/")
        return resp

    @app.get("/api/auth/status")
    async def auth_status():
        return {"auth_required": auth_enabled()}

    async def require_auth(request: Request):
        if not auth_enabled():
            return True
        token = request.cookies.get(_COOKIE)
        auth = request.headers.get("authorization", "")
        bearer = auth[7:] if auth.startswith("Bearer ") else None
        if (token and token in _SESSIONS) or (bearer and bearer in _SESSIONS):
            return True
        raise HTTPException(status_code=401, detail="未登录")

    # ------------------------------------------------------------------ #
    # 3) 运行日志查看
    # ------------------------------------------------------------------ #
    def _log_path() -> Path:
        return Path(os.getenv("REELAY_LOG_FILE", str(project_root / "data" / "reelay.log")))

    @app.get("/api/logs", dependencies=[Depends(require_auth)])
    async def get_logs(limit: int = Query(default=400, ge=1, le=5000),
                       q: str | None = None):
        path = _log_path()
        if not path.is_file():
            return {"lines": [], "path": str(path), "exists": False}
        # 只取末尾，避免大文件全读
        with path.open("rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            block = min(size, 200_000)
            fh.seek(size - block)
            raw = fh.read().decode("utf-8", errors="replace").splitlines()
        lines = raw[-limit:]
        if q:
            lines = [ln for ln in lines if q.lower() in ln.lower()]
        return {"lines": lines[-limit:], "path": str(path), "exists": True}

    # ------------------------------------------------------------------ #
    # 4) 115 网盘浏览（只读）
    # ------------------------------------------------------------------ #
    @app.get("/api/storage/dirs", dependencies=[Depends(require_auth)])
    async def storage_dirs(cid: int = Query(default=0)):
        from p115client import P115Client, P115FileSystem
        cookies = os.getenv("REELAY_115_COOKIES")
        if not cookies:
            raise HTTPException(status_code=409, detail="未配置 115 cookies")
        client = P115Client(cookies)
        fs = P115FileSystem(client)
        items = []
        for it in fs.iterdir(int(cid)):
            if it.get("is_dir") or it.get("is_directory"):
                items.append({"id": int(it["id"]), "name": it.get("name"),
                              "pid": it.get("parent_id")})
        return {"items": sorted(items, key=lambda x: str(x["name"]))}

    # ------------------------------------------------------------------ #
    # 5) 源管理：列出 + 立即解析测试（不入库、不转存）
    # ------------------------------------------------------------------ #
    @app.get("/api/sources", dependencies=[Depends(require_auth)])
    async def list_sources():
        return {"sources": service.get_config().get("sources", []),
                "save_dirs": service.get_config().get("save_dirs", {})}

    @app.post("/api/sources/test-parse", dependencies=[Depends(require_auth)])
    async def test_parse(payload: ParseTestPayload):
        import asyncio
        from reelay.core.contracts import RawMessage, PipelineContext
        cfg = service.get_config()
        source_rule = None
        for s in cfg.get("sources", []):
            if payload.source and s.get("bot", "").lower() == payload.source.lower():
                source_rule = s
                break
        source_rule = source_rule or {"approval_mode": "manual",
                                      "save_to": cfg.get("defaults", {}).get("save_to")}
        raw = RawMessage(source=payload.source or "@test", msg_id="web-preview",
                         text=payload.text, urls=payload.urls)
        ctx = PipelineContext(raw=raw)
        ctx.source_rule = source_rule
        parser = registry.get_provider(
            "parser", source_rule.get("parser", cfg.get("defaults", {}).get("parser", "llm")))
        parsed = await parser.parse(ctx)
        decider = registry.get_provider("decision",
                                        source_rule.get("decision", "default"))
        decision = await decider.decide(ctx)
        return {"parsed": parsed.__dict__ if parsed else None,
                "decision": getattr(decision, "value", str(decision))}

    # ------------------------------------------------------------------ #
    # 6) 任务高级检索：片名/剧集聚合 + 体积 + 支持批量操作的扩展筛选
    # ------------------------------------------------------------------ #
    @app.get("/api/tasks/advanced", dependencies=[Depends(require_auth)])
    async def advanced_tasks(q: str | None = None, media_type: str | None = None,
                             limit: int = Query(default=200, ge=1, le=1000)):
        import sqlite3
        db = os.getenv("REELAY_DB_PATH", str(project_root / "data" / "reelay.db"))
        conn = sqlite3.connect(db); conn.row_factory = sqlite3.Row
        where, args = ["state='done'"], []
        if q:
            where.append("title LIKE ?"); args.append(f"%{q}%")
        if media_type:
            where.append("json_extract(snapshot,'$.parsed.media_type')=?"); args.append(media_type)
        rows = conn.execute(
            f"SELECT task_id,title,state,snapshot,updated_at FROM tasks "
            f"WHERE {' AND '.join(where)} ORDER BY updated_at DESC LIMIT ?",
            (*args, limit)).fetchall()
        groups: dict[str, dict] = {}
        total_bytes = 0
        for r in rows:
            snap = json.loads(r["snapshot"]); p = snap.get("parsed") or {}
            key = p.get("title") or "(未识别)"
            g = groups.setdefault(key, {"title": key, "media_type": p.get("media_type"),
                                        "year": p.get("year"), "episodes": [], "tasks": 0,
                                        "bytes": 0})
            eps = p.get("episodes") or []
            g["episodes"] = sorted(set(g["episodes"] + [int(e) for e in eps if isinstance(e, int)]))
            g["tasks"] += 1
            size = p.get("size")
            g["bytes"] += int(_parse_size_gb(size) * 1024 ** 3)
        return {
            "groups": sorted(groups.values(), key=lambda x: -x["tasks"]),
            "total_done": len(rows),
            "estimated_gb": round(sum(g["bytes"] for g in groups.values()) / 1024 ** 3, 2),
        }

    # ------------------------------------------------------------------ #
    # 4b) 115 文件详情（核对已转存文件：名称/大小/sha1/父目录）
    # ------------------------------------------------------------------ #
    @app.get("/api/storage/file", dependencies=[Depends(require_auth)])
    async def storage_file(fid: int):
        from p115client import P115Client, P115FileSystem
        cookies = os.getenv("REELAY_115_COOKIES")
        if not cookies:
            raise HTTPException(status_code=409, detail="未配置 115 cookies")
        fs = P115FileSystem(P115Client(cookies))
        try:
            attr = fs.get_attr(int(fid), refresh=True)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=404, detail=f"未找到文件：{exc}")
        return {k: attr.get(k) for k in
                ("id", "name", "size", "sha1", "parent_id", "is_dir", "pickcode",
                 "ctime", "mtime")}

    class BatchPayload(BaseModel):
        task_ids: list[str] = Field(default_factory=list)
        action: str = ""  # approve | reject | retry

    @app.post("/api/tasks/batch", dependencies=[Depends(require_auth)])
    async def batch_actions(payload: BatchPayload | None = None):
        payload = payload or BatchPayload()
        if payload.action not in ("approve", "reject", "retry"):
            raise HTTPException(status_code=422, detail="action 必须是 approve/reject/retry")
        done, failed = 0, []
        for tid in payload.task_ids:
            try:
                if payload.action == "approve":
                    await service.approve(tid)
                elif payload.action == "reject":
                    await service.reject(tid)
                elif payload.action == "retry":
                    await service.retry(tid)
                else:
                    raise ValueError(f"不支持的批量动作：{payload.action}")
                done += 1
            except Exception as exc:  # noqa: BLE001
                failed.append({"task_id": tid, "error": str(exc)})
        return {"done": done, "failed": failed}

    # ------------------------------------------------------------------ #
    # 7) 统计聚合（供图表）
    # ------------------------------------------------------------------ #
    @app.get("/api/stats", dependencies=[Depends(require_auth)])
    async def stats(days: int = Query(default=30, ge=1, le=365)):
        rows = store.dashboard() if hasattr(store, "dashboard") else {}
        # 任务按天/状态聚合由 SQL 直接统计
        import sqlite3
        db = store.db_path if hasattr(store, "db_path") else os.getenv(
            "REELAY_DB_PATH", str(project_root / "data" / "reelay.db"))
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        since = time.time() - days * 86400
        daily = conn.execute(
            "SELECT substr(datetime(updated_at,'unixepoch','localtime'),1,10) d, state, "
            "count(*) n FROM tasks WHERE updated_at>=? GROUP BY d,state ORDER BY d",
            (since,)).fetchall()
        by_title = conn.execute(
            "SELECT title, state, count(*) n FROM tasks WHERE title IS NOT NULL "
            "GROUP BY title ORDER BY n DESC LIMIT 15").fetchall()
        total_bytes = conn.execute(
            "SELECT COALESCE(SUM(CAST(json_extract(snapshot,'$.parsed.size') AS REAL)),0) "
            "FROM tasks WHERE state='done' AND json_extract(snapshot,'$.parsed.size') IS NOT NULL"
        ).fetchone()[0]
        conn.close()
        return {
            "counts": rows.get("counts", {}),
            "daily": [dict(r) for r in daily],
            "top_titles": [dict(r) for r in by_title],
            "estimated_done_gb": round(float(total_bytes or 0) / (1024 ** 3), 2),
        }
