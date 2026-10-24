"""管理台的访问控制：登录会话、所有 /api/* 请求的守卫，以及登录、退出与密钥接口。

守卫（``Guard``，ASGI 中间件）对每个 /api/* 请求（含接口文档与实时推送）依次检查：

1. 还没设置登录密码时只接受本机地址（Host 为 localhost / 127.0.0.1 / ::1），挡住经局域网访问与 DNS 重绑定；
2. 写请求（非 GET/HEAD/OPTIONS）必须来自管理台自己的页面（``Sec-Fetch-Site`` 为 same-origin，或 Origin 与
   Host 一致），挡住同一台 NAS 上其他端口、同一域名下其他子站点发起的跨站请求；
3. 设置了密码时，除登录、登录状态与健康检查外都要求登录。

登录失败按客户端地址计数：5 分钟内 5 次错误后暂时拒绝。在反向代理后面时，用 ``REELAY_FORWARDED_ALLOW_IPS``
信任代理转发的真实地址（见 app.main），否则所有人共用代理的地址。
"""
from __future__ import annotations

import asyncio
import hashlib
import secrets
import time
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..core import secret_store

COOKIE = "reelay_session"
SESSION_TTL = 7 * 24 * 3600
LOGIN_WINDOW = 300
LOGIN_MAX_FAILURES = 5
# 失败记录最多保留这么多个客户端地址，超过时丢掉最早的，不会无限增长
LOGIN_MAX_CLIENTS = 10000
# 设置密码后仍可匿名访问的接口：登录相关与容器健康检查。
PUBLIC_PATHS = {"/api/auth/login", "/api/auth/logout", "/api/auth/status", "/api/health"}
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def session_key(token: str) -> str:
    """会话存在数据库里（Web 重启后已打开的页面不用重新登录），只存 token 的 SHA-256。"""
    return hashlib.sha256(token.encode()).hexdigest()


def auth_enabled() -> bool:
    """设置了 Web 登录密码（管理台「设置 → 运行与安全」）才要求登录。"""
    return bool(secret_store.get("web_password"))


def _session_valid(store, token: str | None) -> bool:
    if not token:
        return False
    expires = store.web_session_expires(session_key(token))
    if expires is None:
        return False
    if expires < time.time():
        store.web_session_drop(session_key(token))
        return False
    return True


def is_authenticated(request: Request, store) -> bool:
    if not auth_enabled():
        return True
    auth = request.headers.get("authorization", "")
    bearer = auth[7:] if auth.startswith("Bearer ") else None
    return _session_valid(store, request.cookies.get(COOKIE)) or _session_valid(store, bearer)


def _host(value: str) -> str:
    """Host 请求头去掉端口：「127.0.0.1:28080」→「127.0.0.1」，「[::1]:28080」→「::1」。"""
    return urlsplit(f"//{value}").hostname or ""


def _cross_site(request: Request) -> bool:
    site = request.headers.get("sec-fetch-site")
    if site:
        return site not in ("same-origin", "none")
    origin = request.headers.get("origin")
    return bool(origin) and urlsplit(origin).netloc != request.headers.get("host")


class Guard:
    """所有 /api/* 请求的守卫（见模块说明）；路径按挂载前缀（root_path）之后的部分判断。"""

    def __init__(self, app, *, store) -> None:
        self.app, self.store = app, store

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "http":
            path = scope["path"]
            root = scope.get("root_path") or ""
            if root and path.startswith(root):
                path = path[len(root):]
            if path.startswith("/api/"):
                problem = self.check(Request(scope), path)
                if problem:
                    status, detail = problem
                    return await JSONResponse({"detail": detail}, status_code=status)(scope, receive, send)
        await self.app(scope, receive, send)

    def check(self, request: Request, path: str) -> tuple[int, str] | None:
        if path == "/api/health":
            return None  # 容器健康检查；不带运行细节（见 system.health）
        if not auth_enabled() and _host(request.headers.get("host", "")) not in LOCAL_HOSTS:
            return 403, ("还没有设置登录密码，管理台只能在本机打开（127.0.0.1 或 localhost）。"
                         "请先在本机打开管理台，在「设置 → 运行与安全」设置登录密码，再从其他设备访问。")
        if request.method not in SAFE_METHODS and _cross_site(request):
            return 403, "拒绝来自其他网站的请求"
        if path not in PUBLIC_PATHS and not is_authenticated(request, self.store):
            return 401, "未登录"
        return None


class LoginPayload(BaseModel):
    password: str


class SecretsPayload(BaseModel):
    # 名称 → 新值；空字符串或 null 表示清除
    changes: dict[str, str | None]
    current_password: str | None = None


def router(store, service) -> APIRouter:
    api = APIRouter()
    failures: dict[str, list[float]] = {}  # 客户端地址 → 最近的失败时间

    def new_session(body: dict) -> JSONResponse:
        token = secrets.token_urlsafe(32)
        store.web_session_add(session_key(token), time.time() + SESSION_TTL)
        resp = JSONResponse(body)
        resp.set_cookie(COOKIE, token, httponly=True, samesite="lax", max_age=SESSION_TTL, path="/")
        return resp

    @api.post("/api/auth/login")
    async def login(payload: LoginPayload, request: Request):
        if not auth_enabled():
            return {"ok": True, "auth_required": False}
        client = request.client.host if request.client else "unknown"
        now = time.time()
        recent = [at for at in failures.get(client, []) if now - at < LOGIN_WINDOW]
        if len(recent) >= LOGIN_MAX_FAILURES:
            wait = int(LOGIN_WINDOW - (now - recent[0])) + 1
            raise HTTPException(status_code=429, detail=f"尝试过于频繁，请 {wait} 秒后再试")
        # 密码摘要要算 20 万轮：放到线程里，不拖住其他请求
        if not await asyncio.to_thread(secret_store.current().check_password, payload.password):
            failures[client] = [*recent, now]
            for stale in [key for key, times in failures.items() if not times or now - times[-1] >= LOGIN_WINDOW]:
                failures.pop(stale, None)
            while len(failures) > LOGIN_MAX_CLIENTS:
                failures.pop(next(iter(failures)))
            raise HTTPException(status_code=401, detail="密码错误")
        failures.pop(client, None)
        return new_session({"ok": True, "auth_required": True})

    @api.post("/api/auth/logout")
    async def logout(request: Request):
        token = request.cookies.get(COOKIE)
        if token:
            store.web_session_drop(session_key(token))
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(COOKIE, path="/")
        return resp

    @api.get("/api/auth/status")
    async def auth_status(request: Request):
        # authenticated 让前端刷新后保留已登录状态，而不是每次都显示登录页。
        return {"auth_required": auth_enabled(), "authenticated": is_authenticated(request, store)}

    @api.put("/api/settings/secrets")
    async def update_secrets(payload: SecretsPayload):
        status = service.update_secrets(payload.changes, current_password=payload.current_password)
        if "web_password" not in payload.changes:
            return status
        # 改了或清除了 Web 密码：其他已登录的会话全部失效，当前页面换一个新会话继续使用
        store.web_session_drop()
        return new_session(status) if auth_enabled() else status

    return api
