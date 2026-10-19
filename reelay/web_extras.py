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

from fastapi import HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .core.media import multi_season

# 会话保存在内存即可：单实例个人工具，重启后要求重新登录。token -> 过期时间
_SESSIONS: dict[str, float] = {}
# 登录失败记录：客户端地址 -> 最近失败时间；窗口内失败过多则拒绝尝试。
_FAILURES: dict[str, list[float]] = {}
LOGIN_WINDOW = 300
LOGIN_MAX_FAILURES = 5
# 设置密码后仍可匿名访问的接口：登录相关、容器健康检查，以及自带签名的 STRM 跳转。
PUBLIC_PATHS = {"/api/auth/login", "/api/auth/logout", "/api/auth/status", "/api/health"}
PUBLIC_PREFIXES = ("/api/strm/",)

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
    num = float(m.group(1))
    unit = m.group(2).upper()
    return num * {"B": 1 / 1024 ** 3, "KB": 1 / 1024 ** 2,
                  "MB": 1 / 1024, "GB": 1, "TB": 1024}.get(unit, 0.0)

_COOKIE = "reelay_session"
_SESSION_TTL = 7 * 24 * 3600


def _password() -> str | None:
    return os.getenv("REELAY_WEB_PASSWORD") or None


def auth_enabled() -> bool:
    return bool(_password())


def _session_valid(token: str | None) -> bool:
    if not token:
        return False
    expires = _SESSIONS.get(token)
    if expires is None:
        return False
    if expires < time.time():
        _SESSIONS.pop(token, None)
        return False
    return True


def is_authenticated(request: Request) -> bool:
    if not auth_enabled():
        return True
    auth = request.headers.get("authorization", "")
    bearer = auth[7:] if auth.startswith("Bearer ") else None
    return _session_valid(request.cookies.get(_COOKIE)) or _session_valid(bearer)


async def require_auth(request: Request) -> bool:
    if not is_authenticated(request):
        raise HTTPException(status_code=401, detail="未登录")
    return True


async def api_guard(request: Request) -> None:
    """应用级依赖：除公开接口外，所有 /api/* 路由都要求登录（含 SSE）。"""
    path = request.url.path
    if not path.startswith("/api/") or path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES):
        return
    await require_auth(request)


class LoginPayload(BaseModel):
    password: str


class ParseTestPayload(BaseModel):
    text: str
    urls: list[str] = Field(default_factory=list)
    source: str | None = None


class CookiePayload(BaseModel):
    cookies: str

class BatchPayload(BaseModel):
    task_ids: list[str] = Field(default_factory=list)
    action: str = ""  # approve | reject | retry
    save_dir_alias: str | None = None  # 仅 approve：统一改投到该目录


def register_extras(app, *, project_root: Path, store, service, registry) -> None:
    # ------------------------------------------------------------------ #
    # 1) 鉴权
    # ------------------------------------------------------------------ #
    @app.post("/api/auth/login")
    async def login(payload: LoginPayload, request: Request):
        pw = _password()
        if not pw:
            return {"ok": True, "auth_required": False}
        client = request.client.host if request.client else "unknown"
        now = time.time()
        recent = [at for at in _FAILURES.get(client, []) if now - at < LOGIN_WINDOW]
        if len(recent) >= LOGIN_MAX_FAILURES:
            wait = int(LOGIN_WINDOW - (now - recent[0])) + 1
            raise HTTPException(status_code=429, detail=f"尝试过于频繁，请 {wait} 秒后再试")
        if not hmac.compare_digest(payload.password.encode(), pw.encode()):
            _FAILURES[client] = [*recent, now]
            raise HTTPException(status_code=401, detail="密码错误")
        _FAILURES.pop(client, None)
        for token, expires in list(_SESSIONS.items()):
            if expires < now:
                _SESSIONS.pop(token, None)
        token = secrets.token_urlsafe(32)
        _SESSIONS[token] = now + _SESSION_TTL
        resp = JSONResponse({"ok": True, "auth_required": True})
        resp.set_cookie(_COOKIE, token, httponly=True, samesite="lax",
                        max_age=_SESSION_TTL, path="/")
        return resp

    @app.post("/api/auth/logout")
    async def logout(request: Request):
        token = request.cookies.get(_COOKIE)
        if token:
            _SESSIONS.pop(token, None)
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(_COOKIE, path="/")
        return resp

    @app.get("/api/auth/status")
    async def auth_status(request: Request):
        # authenticated 让前端刷新后保留已登录状态，而不是每次都显示登录页。
        return {"auth_required": auth_enabled(), "authenticated": is_authenticated(request)}

    @app.get('/api/extensions')
    async def extensions_catalog():
        return registry.catalog(service.get_config())

    @app.post('/api/config/validate')
    async def validate_config_payload(request: Request):
        from .core.config import normalize_config
        try:
            payload = await request.json()
            cfg = normalize_config(payload.get('config'))
            registry.validate_config(cfg)
            return {'ok': True}
        except (ValueError, TypeError, AttributeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post('/api/extensions/organizer/preview')
    async def organizer_template_preview(request: Request):
        from .core.naming_template import validate_template, render_path
        payload = await request.json()
        template = payload.get('template') if isinstance(payload, dict) else None
        kind = payload.get('kind', 'movie') if isinstance(payload, dict) else 'movie'
        try:
            validate_template(template, f'{kind}命名模板')
            values = {"title": "示例标题", "year": 2026, "resolution": "4K", "ext": "mkv",
                      "season": 1, "episode": 2, "ep": 2, "media_type": kind,
                      "genre_ids": [16], "origin_country": ["CN"], "tmdb_id": 123,
                      "original_name": "sample.S01E02.mkv"}
            return {'path': render_path(template, values, label=f'{kind}命名模板')}
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post('/api/extensions/dir-router/preview')
    async def dir_router_preview(request: Request):
        from .core.category_strategy import classify, validate_strategy
        payload = await request.json()
        try:
            strategy = payload.get('categories')
            validate_strategy(strategy)
            path = classify(strategy, payload.get('media_type', ''), payload.get('genre_ids', []),
                            payload.get('origin_country', []))
            return {'path': path}
        except (ValueError, TypeError, AttributeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.patch('/api/config/sections')
    async def save_config_sections(request: Request):
        from .core.config import config_revision
        from copy import deepcopy
        payload = await request.json()
        if not isinstance(payload, dict) or not isinstance(payload.get('sections'), dict):
            raise HTTPException(status_code=422, detail='sections 必须是配置块对象')
        async with section_lock:
            cfg = service.get_config()
            if service.pipeline.config_error:
                raise HTTPException(status_code=409, detail='配置文件当前无效，先修复原始文件；不会覆盖损坏配置。')
            if payload.get('revision') != config_revision(cfg):
                raise HTTPException(status_code=409, detail='配置已被其他页面或进程修改，请重新加载后再保存。')
            allowed = {'save_dirs', 'defaults', 'sources', 'forward', 'organize', 'dir_router', 'routing', 'approval', 'providers', 'plugins'}
            catalog = registry.catalog(cfg)
            objects = [x for rows in catalog['providers'].values() for x in rows] + catalog['hooks'] + catalog['events']
            allowed.update(f['path'].split('.')[0] for x in objects for f in x['fields'])
            if set(payload['sections']) - allowed:
                raise HTTPException(status_code=422, detail='包含未声明的配置模块')
            updated = deepcopy(cfg)
            updated.update(payload['sections'])
            try:
                saved = await service.save_config(updated)
            except (ValueError, KeyError) as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            return {'config': saved, 'revision': config_revision(saved)}

    import asyncio
    section_lock = asyncio.Lock()

    # ------------------------------------------------------------------ #
    # 2) 115 混合鉴权：状态 / cookie 录入 / 开放平台扫码授权 / 探活
    # ------------------------------------------------------------------ #
    from .core.auth115 import get_store


    @app.get("/api/auth115/status")
    async def auth115_status():
        return get_store().status()

    @app.post("/api/auth115/ping")
    async def auth115_ping():
        store = get_store()
        # 在线程中执行，避免阻塞事件循环
        import asyncio
        cookie_state, oauth_state = await asyncio.gather(
            asyncio.to_thread(store.ping_cookie, True),
            asyncio.to_thread(store.ping_oauth, True),
        )
        return {"cookie": cookie_state, "oauth": oauth_state, "status": store.status()}

    @app.put("/api/auth115/cookie")
    async def auth115_set_cookie(payload: CookiePayload):
        cookies = (payload.cookies or "").strip()
        if not cookies:
            raise HTTPException(status_code=422, detail="cookie 不能为空")
        if "UID" not in cookies.upper() or "CID" not in cookies.upper():
            raise HTTPException(status_code=422,
                                detail="不像有效的 115 cookie（需包含 UID/CID）")
        store = get_store()
        store.set_cookies(cookies)
        state = await __import__("asyncio").to_thread(store.ping_cookie, True)
        if state == "expired":
            raise HTTPException(status_code=409, detail="cookie 已保存，但探活提示已失效，请重新获取")
        return {"ok": True, "state": state}

    @app.post("/api/auth115/cookie/qr")
    async def auth115_cookie_qr_start():
        try:
            return await asyncio.to_thread(get_store().cookie_qr_start)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=f"申请 Cookie 登录二维码失败：{exc}") from exc

    @app.get("/api/auth115/cookie/qr")
    async def auth115_cookie_qr_status(uid: str = Query(...)):
        try:
            return await asyncio.to_thread(get_store().cookie_qr_status, uid)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=f"查询 Cookie 扫码状态失败：{exc}") from exc

    @app.get("/api/auth115/cookie/qr/image")
    async def auth115_cookie_qr_image(uid: str = Query(...)):
        from fastapi import Response
        store = get_store()

        def _fetch() -> bytes:
            import io
            import qrcode
            from qrcode.image.svg import SvgPathImage
            session = store.cookie_qr_session(uid)
            url = session.get("qrcode_url") or f"https://115.com/scan/dg-{uid}"
            image = qrcode.make(url, image_factory=SvgPathImage)
            output = io.BytesIO()
            image.save(output)
            return output.getvalue()

        try:
            png = await asyncio.to_thread(_fetch)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=f"获取 Cookie 二维码图片失败：{exc}") from exc
        return Response(content=png, media_type="image/svg+xml",
                        headers={"Cache-Control": "no-store"})

    @app.post("/api/auth115/oauth/qr")
    async def auth115_qr_start():
        store = get_store()
        try:
            return await __import__("asyncio").to_thread(store.qr_start)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=f"申请授权二维码失败：{exc}") from exc

    @app.get("/api/auth115/oauth/qr")
    async def auth115_qr_status(uid: str = Query(...)):
        store = get_store()
        try:
            return await __import__("asyncio").to_thread(store.qr_status, uid)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=f"查询扫码状态失败：{exc}") from exc

    @app.get("/api/auth115/oauth/qr/image")
    async def auth115_qr_image(uid: str = Query(...)):
        """返回授权二维码 PNG：后端向 115 拉取，避免前端跨域与外链跳转。"""
        import asyncio
        from fastapi import Response
        store = get_store()

        def _fetch() -> bytes:
            store.qr_session(uid)  # 校验会话存在且未过期
            from p115client import P115OpenClient
            data = P115OpenClient.login_qrcode({"uid": uid}, app="web")
            if not isinstance(data, (bytes, bytearray)):
                raise RuntimeError("二维码接口未返回图片")
            return bytes(data)

        try:
            png = await asyncio.to_thread(_fetch)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=f"获取二维码图片失败：{exc}") from exc
        return Response(content=png, media_type="image/png",
                        headers={"Cache-Control": "no-store"})

    @app.delete("/api/auth115/oauth")
    async def auth115_oauth_clear():
        get_store().clear_oauth()
        return {"ok": True}

    # ------------------------------------------------------------------ #
    # 3) 运行日志查看
    # ------------------------------------------------------------------ #
    def _log_path() -> Path:
        return Path(os.getenv("REELAY_LOG_FILE", str(project_root / "data" / "reelay.log")))

    @app.get("/api/logs")
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
    @app.get("/api/storage/dirs")
    async def storage_dirs(cid: str = Query(default='0', pattern=r'^[0-9]+$'),
                           offset: int = Query(default=0, ge=0),
                           limit: int = Query(default=100, ge=1, le=1000)):
        import asyncio
        from .web_storage import browse_dirs, StorageBrowseError
        try:
            return await asyncio.to_thread(browse_dirs, get_store(), cid, offset, limit)
        except StorageBrowseError as exc:
            return JSONResponse(status_code=exc.status, content={'detail': str(exc), 'code': exc.code})

    # ------------------------------------------------------------------ #
    # 5) 源管理：列出 + 立即解析测试（不入库、不转存）
    # ------------------------------------------------------------------ #
    @app.get("/api/sources")
    async def list_sources():
        return {"sources": service.get_config().get("sources", []),
                "save_dirs": service.get_config().get("save_dirs", {})}

    @app.post("/api/sources/test-parse")
    async def test_parse(payload: ParseTestPayload):
        """与真实管线一致：合并默认规则 → 解析 → after_parse 扩展 → 确定性置信度 → 决策；不落库。"""
        from reelay.core import confidence
        from reelay.core.contracts import RawMessage, PipelineContext
        pipeline = service.pipeline
        cfg = service.get_config()
        raw = RawMessage(source=payload.source or "@test", msg_id="web-preview",
                         text=payload.text, urls=payload.urls)
        ctx = PipelineContext(raw=raw)
        ctx.source_rule = pipeline._rule_for(raw.source)
        ctx.ns("runtime").update(config=cfg)
        parser = registry.get_provider("parser", ctx.source_rule.get("parser", cfg["defaults"]["parser"]))
        with pipeline._scope(ctx):
            ctx.parsed = await parser.parse(ctx)
        await pipeline._run_hooks("after_parse", ctx)
        confidence.apply(ctx)
        decider = registry.get_provider("decision", ctx.source_rule.get("decision", cfg["defaults"]["decision"]))
        with pipeline._scope(ctx):
            decision = await decider.decide(ctx)
        return {"parsed": ctx.parsed.__dict__ if ctx.parsed else None,
                "decision": getattr(decision, "value", str(decision)),
                "confidence": ctx.meta.get("confidence"), "trail": ctx.trail}

    # ------------------------------------------------------------------ #
    # 6) 任务高级检索：片名/剧集聚合 + 体积 + 支持批量操作的扩展筛选
    # ------------------------------------------------------------------ #
    @app.get("/api/tasks/advanced")
    async def advanced_tasks(q: str | None = None, media_type: str | None = None,
                             limit: int = Query(default=200, ge=1, le=1000)):
        conn = store._conn  # 与应用同一个 Store；不要按环境变量另开连接
        where, args = ["state='done'"], []
        if q:
            where.append("title LIKE ?")
            args.append(f"%{q}%")
        if media_type:
            where.append("json_extract(snapshot,'$.parsed.media_type')=?")
            args.append(media_type)
        rows = conn.execute(
            f"SELECT task_id,title,state,snapshot,updated_at FROM tasks "
            f"WHERE {' AND '.join(where)} ORDER BY updated_at DESC LIMIT ?",
            (*args, limit)).fetchall()
        groups: dict[str, dict] = {}
        for r in rows:
            snap = json.loads(r["snapshot"])
            p = snap.get("parsed") or {}
            key = p.get("title") or "(未识别)"
            g = groups.setdefault(key, {"title": key, "media_type": p.get("media_type"),
                                        "year": p.get("year"), "episodes": [], "pairs": set(), "tasks": 0,
                                        "bytes": 0, "poster_path": None, "save_dir_alias": None,
                                        "files": [], "updated_at": r["updated_at"]})
            g["poster_path"] = g["poster_path"] or (p.get("raw_fields") or {}).get("poster_path")
            g["save_dir_alias"] = g["save_dir_alias"] or snap.get("save_dir_alias")
            plan = ((snap.get("meta") or {}).get("organize") or {}).get("plan") or []
            g["files"] = (g["files"] + ["/".join(x for x in (i.get("target_dir"), i.get("target_name")) if x)
                                        for i in plan])[:100]
            eps = p.get("episodes") or []
            g["episodes"] = sorted(set(g["episodes"] + [int(e) for e in eps if isinstance(e, int)]))
            # 跨多季的合集按（季, 集）计数，否则各季的同号集会被当成一集
            manifest = ((snap.get("meta") or {}).get("storage") or {}).get("manifest") or {}
            season = p.get("season") if p.get("season") is not None else 1
            g["pairs"] |= {(int(s), int(e)) for s, e in manifest.get("pairs") or []} or {
                (season, int(e)) for e in eps if isinstance(e, int)}
            g["tasks"] += 1
            size = p.get("size")
            g["bytes"] += int(_parse_size_gb(size) * 1024 ** 3)
        for g in groups.values():
            g["episode_pairs"] = multi_season(sorted(g.pop("pairs")))
        return {
            "groups": sorted(groups.values(), key=lambda x: -x["tasks"]),
            "total_done": len(rows),
            "estimated_gb": round(sum(g["bytes"] for g in groups.values()) / 1024 ** 3, 2),
        }

    # ------------------------------------------------------------------ #
    # 4b) 115 文件详情（核对已转存文件：名称/大小/sha1/父目录）
    # ------------------------------------------------------------------ #
    @app.get("/api/storage/file")
    async def storage_file(fid: int):
        from p115client import P115FileSystem
        client = get_store().cookie_client()
        if client is None:
            raise HTTPException(status_code=409, detail="未配置 115 cookie（查询文件需要 cookie）")
        fs = P115FileSystem(client)
        try:
            attr = fs.get_attr(int(fid), refresh=True)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=404, detail=f"未找到文件：{exc}") from exc
        return {k: attr.get(k) for k in
                ("id", "name", "size", "sha1", "parent_id", "is_dir", "pickcode",
                 "ctime", "mtime")}


    @app.post("/api/tasks/batch")
    async def batch_actions(payload: BatchPayload | None = None):
        payload = payload or BatchPayload()
        if payload.action not in ("approve", "reject", "retry"):
            raise HTTPException(status_code=422, detail="action 必须是 approve/reject/retry")
        done, failed = 0, []
        for tid in payload.task_ids:
            try:
                if payload.action == "approve":
                    await service.approve(tid, save_dir_alias=payload.save_dir_alias)
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
    @app.get("/api/stats")
    async def stats(days: int = Query(default=30, ge=1, le=365)):
        # 任务按天/状态聚合由 SQL 直接统计；复用应用的 Store 连接，不为每个请求新开连接。
        conn = store._conn
        since = time.time() - days * 86400
        daily = conn.execute(
            "SELECT substr(datetime(updated_at,'unixepoch','localtime'),1,10) d, state, "
            "count(*) n FROM tasks WHERE updated_at>=? GROUP BY d,state ORDER BY d",
            (since,)).fetchall()
        by_title = conn.execute(
            "SELECT title, state, count(*) n FROM tasks WHERE title IS NOT NULL "
            "GROUP BY title ORDER BY n DESC LIMIT 15").fetchall()
        # 解析出的 size 是 "15.12 GB" 这类文本，按单位换算后再求和。
        sizes = conn.execute(
            "SELECT json_extract(snapshot,'$.parsed.size') FROM tasks "
            "WHERE state='done' AND json_extract(snapshot,'$.parsed.size') IS NOT NULL").fetchall()
        return {
            "counts": store.counts_by_state(),
            "daily": [dict(r) for r in daily],
            "top_titles": [dict(r) for r in by_title],
            "estimated_done_gb": round(sum(_parse_size_gb(row[0]) for row in sizes), 2),
        }
