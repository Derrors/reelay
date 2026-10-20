"""115 混合鉴权凭证管理。

双凭证、分工明确：

- **cookie 客户端（P115Client）**：负责分享侧与读操作——枚举分享
  （P115ShareFileSystem）、share_receive 转存、列目录/读属性等。网页 cookie
  没有 refresh_token 机制，过期需重新获取（页面粘贴或扫码），本模块提供
  健康探活与失效判定。
- **开放平台 OAuth 客户端（P115OpenClient）**：负责写操作——建目录 /
  重命名 / 移动 / 删除，走 access_token（约 2h 有效）+ refresh_token；
  p115client 在令牌失效时自动单飞刷新，刷新结果由本模块回写凭证文件，
  重启不丢。OAuth 授权通过开放平台扫码（authDeviceCode 流程）在
  Web 控制台完成，无需命令行。

混合写操作（``mkdir`` / ``rename`` / ``move`` / ``delete``）优先走 OAuth，
失败时回退到 cookie 的 webapi 路径；无论走哪条路，调用方（storage/
organizer）都会读回核验，不会因为回退产生"以为成功、实际没动"。

凭证统一持久化在一个 JSON 文件（固定为 data/115_credentials.json），同时
容纳 OAuth token 与（经 Web 录入的）cookie；不放进 config.yaml。旧版本写在
环境变量里的 cookie 与 token 在首次启动时导入该文件（见 ``core/bootstrap.py``）。
"""
from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import re
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from . import paths, runtime

log = logging.getLogger("reelay.115auth")

DEFAULT_APP_ID = 100195125  # p115client 文档示例开放应用，已实测可用
# 115 鉴权失效错误码（cookie 模式）。
AUTH_ERRNOS = {99, 401}
# 开放平台 OAuth 鉴权/令牌失效类错误码，出现时应回退 cookie 并提示续期。
OPEN_AUTH_CODES = {401, 99, 40140125, 20004, 20005}
# 开放平台扫码会话有效期（115 二维码约 5 分钟），本地稍短一点做兜底。
QR_TTL = 240
RISK_CODES = {429, 405, 990009}
RISK_MARKERS = ("访问上限", "操作频繁", "请求过于频繁", "风控", "429 Too Many Requests")
# Cookie 扫码绑定的设备：独立 TV 会话，降低与网页端/手机端既有会话互踢概率。
COOKIE_QR_APP = "tv"
# OAuth access_token 按 2 小时估算有效期，提前 15 分钟主动刷新。
OAUTH_LIFETIME_SEC = 7200
OAUTH_REFRESH_ADVANCE_SEC = 900
# 限流与风控冷却的默认值；实际值取系统配置 p115（管理台「115 连接」可改，改了立即生效）
QPS_DEFAULTS = {"open": 3.0, "cookie": 2.0, "share": 1.0}
RISK_COOLDOWN_SEC = 3600


def default_cred_file() -> Path:
    return paths.CREDENTIALS_115


class _RateGate:
    """线程安全的最小间隔限流器；同步/异步调用共享同一配额。"""

    def __init__(self, qps: float) -> None:
        self._lock = threading.Lock()
        self._next = 0.0
        self.set_qps(qps)

    def set_qps(self, qps: float) -> None:
        self.qps = qps
        self.interval = 0.0 if qps <= 0 else 1.0 / qps

    def acquire(self) -> None:
        if self.interval <= 0:
            return
        with self._lock:
            now = time.monotonic()
            wait = max(0.0, self._next - now)
            self._next = max(now, self._next) + self.interval
        if wait:
            time.sleep(wait)

    async def acquire_async(self) -> None:
        if self.interval <= 0:
            return
        with self._lock:
            now = time.monotonic()
            wait = max(0.0, self._next - now)
            self._next = max(now, self._next) + self.interval
        if wait:
            await asyncio.sleep(wait)


class AuthState:
    UNKNOWN = "unknown"      # 未配置/未探活
    ACTIVE = "active"        # 探活成功
    EXPIRED = "expired"      # 明确鉴权失效，需人工更新
    ERROR = "error"          # 网络等临时错误（可重试）


def _is_auth_error(msg: str, codes: set[int]) -> bool:
    text = msg or ""
    if any(f"[{n}]" in text or f"errno: {n}" in text or f"errno': {n}" in text
           or f"'code': {n}" in text or f'"code": {n}' in text for n in codes):
        return True
    return "请重新登录" in text


class CredentialStore:
    """线程安全地持有并持久化 115 凭证（cookie + OAuth token）。"""

    def __init__(self, cred_file: str | Path | None = None) -> None:
        self.path = Path(cred_file or default_cred_file())
        # 兼容旧版本的 data/115_oauth.json：新文件不存在但旧文件在时，
        # 首次保存自动迁移到新路径（统一容纳 cookie + OAuth）。
        self._legacy_path = self.path.parent / "115_oauth.json"
        self._lock = threading.RLock()
        self._refresh_lock = threading.Lock()
        self._data: dict[str, Any] = self._load()
        self._file_mtime = self.path.stat().st_mtime if self.path.is_file() else 0.0
        # MoviePilot 风格的请求治理：文件 API 与分享 API 分级限流，触发风控后全通道冷却。
        self._gates = {lane: _RateGate(self._qps(lane)) for lane in QPS_DEFAULTS}
        self._cooldown_lock = threading.Lock()
        self._cooldown_until = 0.0
        self._cooldown_reason = ""
        # cookie 探活状态
        self.cookie_state = AuthState.UNKNOWN
        self.cookie_error = ""
        self.cookie_checked_at = 0.0
        # OAuth 探活状态
        self.oauth_state = AuthState.UNKNOWN
        self.oauth_error = ""
        self.oauth_checked_at = 0.0
        self._cookie_client = None
        self._open_client = None
        # 进行中的开放平台扫码会话：uid -> {sign,time,app_id,created_at}
        self._qr_pending: dict[str, dict[str, Any]] = {}
        # 进行中的 Cookie 扫码会话：uid -> {sign,time,app,created_at}
        self._cookie_qr_pending: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------------ #
    # 请求治理：带锁刷新、分级限流、全局风控冷却
    # ------------------------------------------------------------------ #
    @staticmethod
    def _request_lane(url: str) -> str:
        path = urlsplit(str(url)).path.lower()
        if path.startswith("/open/"):
            return "open"
        if "/share/" in path or path.startswith("/share"):
            return "share"
        return "cookie"

    @staticmethod
    def _risk_reason(value: Any) -> str:
        if isinstance(value, dict):
            code = value.get("code", value.get("errno", value.get("status")))
            message = str(value.get("message") or value.get("msg") or value.get("error") or "")
            try:
                numeric_code = int(code) if code is not None else None
            except (TypeError, ValueError):
                numeric_code = None
            if numeric_code in RISK_CODES or any(mark in message for mark in RISK_MARKERS):
                return f"code={code}; {message}"[:200]
            return ""
        text = str(value or "")
        code_hit = any(re.search(rf"(?<!\d){code}(?!\d)", text) for code in RISK_CODES)
        if code_hit or any(mark in text for mark in RISK_MARKERS):
            return text[:200]
        return ""

    @staticmethod
    def _qps(lane: str) -> float:
        return float(runtime.get(f"p115.{lane}_qps") or QPS_DEFAULTS[lane])

    @property
    def _cooldown_sec(self) -> int:
        return max(1, int(runtime.get("p115.risk_cooldown_sec") or RISK_COOLDOWN_SEC))

    def _wait_for_request(self, lane: str, async_: bool = False):
        gate = self._gates[lane]
        if isinstance(gate, _RateGate) and gate.qps != self._qps(lane):
            gate.set_qps(self._qps(lane))
        with self._cooldown_lock:
            wait = max(0.0, self._cooldown_until - time.time())
        if async_:
            async def wait_async() -> None:
                if wait:
                    await asyncio.sleep(wait)
                await self._gates[lane].acquire_async()
            return wait_async()
        if wait:
            time.sleep(wait)
        self._gates[lane].acquire()
        return None

    def _enter_cooldown(self, reason: str) -> None:
        until = time.time() + self._cooldown_sec
        with self._cooldown_lock:
            self._cooldown_until = max(self._cooldown_until, until)
            self._cooldown_reason = reason[:200]
        log.warning("115 触发风控，所有接口冷却 %s 秒：%s", self._cooldown_sec, reason[:120])

    @contextmanager
    def _refresh_file_lock(self):
        """跨进程互斥刷新，避免两个进程同时消费同一个 refresh_token。"""
        lock_path = self.path.with_suffix(self.path.suffix + ".refresh.lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+b") as fh:
            try:
                import fcntl
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            except ImportError:
                pass
            try:
                yield
            finally:
                try:
                    import fcntl
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
                except ImportError:
                    pass

    def _after_request(self, result: Any) -> Any:
        reason = self._risk_reason(result)
        if reason:
            self._enter_cooldown(reason)
        return result

    def _managed_client_class(self, base_cls):
        store = self

        class ManagedClient(base_cls):
            def request(self, /, url, method="GET", payload=None, *, async_=False, **kwargs):
                lane = store._request_lane(url)
                if async_:
                    async def run():
                        await store._wait_for_request(lane, True)
                        try:
                            result = super(ManagedClient, self).request(
                                url, method, payload, async_=True, **kwargs)
                            if inspect.isawaitable(result):
                                result = await result
                            return store._after_request(result)
                        except Exception as exc:
                            reason = store._risk_reason(exc)
                            if reason:
                                store._enter_cooldown(reason)
                            raise
                    return run()
                store._wait_for_request(lane, False)
                try:
                    return store._after_request(super().request(
                        url, method, payload, async_=False, **kwargs))
                except Exception as exc:
                    reason = store._risk_reason(exc)
                    if reason:
                        store._enter_cooldown(reason)
                    raise

        return ManagedClient

    # ------------------------------------------------------------------ #
    # 持久化
    # ------------------------------------------------------------------ #
    def _load(self) -> dict[str, Any]:
        path = self.path
        if not path.is_file() and self._legacy_path.is_file():
            path = self._legacy_path  # 只读迁移源，_save 时写入新路径
        if not path.is_file():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception as exc:  # noqa: BLE001
            log.warning("读取 115 凭证文件失败：%s", exc)
            return {}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)
        self._file_mtime = self.path.stat().st_mtime

    def reload_if_changed(self) -> bool:
        """跨进程热加载：Web 扫码落 token 后，Worker 无需重启即可采用。"""
        try:
            mtime = self.path.stat().st_mtime
        except FileNotFoundError:
            return False
        with self._lock:
            if mtime <= self._file_mtime:
                return False
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return False
            self._data = data
            self._file_mtime = mtime
            self._cookie_client = None
            self._open_client = None
            return True

    # ------------------------------------------------------------------ #
    # cookie
    # ------------------------------------------------------------------ #
    @property
    def cookies(self) -> str:
        """当前生效 cookie（凭证文件）。"""
        self.reload_if_changed()
        return str(self._data.get("cookies") or "")

    @property
    def has_cookie(self) -> bool:
        return bool(self.cookies)

    @property
    def cookie_from_file(self) -> bool:
        return bool(self._data.get("cookies"))

    def set_cookies(self, cookies: str) -> None:
        """写入新的 cookie（Web 录入），持久化并热更新运行态。"""
        cookies = (cookies or "").strip()
        with self._lock:
            if cookies:
                self._data["cookies"] = cookies
                self._data["cookie_updated_at"] = int(time.time())
            else:
                self._data.pop("cookies", None)
                self._data.pop("cookie_updated_at", None)
            self._save()
            self._cookie_client = None
            self.cookie_state, self.cookie_error, self.cookie_checked_at = (
                AuthState.UNKNOWN, "", 0.0)

    def cookie_client(self):
        """返回 cookie 客户端（未配置返回 None），懒加载、可热重建。"""
        cookies = self.cookies
        if not cookies:
            return None
        with self._lock:
            if self._cookie_client is None:
                from p115client import P115Client
                ManagedCookieClient = self._managed_client_class(P115Client)
                self._cookie_client = ManagedCookieClient(cookies)
            return self._cookie_client

    def reset_cookie_client(self) -> None:
        with self._lock:
            self._cookie_client = None

    def ping_cookie(self, force: bool = False) -> str:
        """用最轻量的只读 webapi 探活，更新并返回 cookie 鉴权状态。"""
        if not force and time.time() - self.cookie_checked_at < 600 \
                and self.cookie_state != AuthState.UNKNOWN:
            return self.cookie_state
        client = self.cookie_client()
        if client is None:
            self.cookie_state, self.cookie_error = AuthState.UNKNOWN, "未配置 cookie"
            self.cookie_checked_at = 0.0
            return self.cookie_state
        try:
            resp = client.fs_files({"cid": 0, "limit": 1, "offset": 0})
            if isinstance(resp, dict) and resp.get("state", True) is False:
                raise RuntimeError(str(resp))
            self.cookie_state, self.cookie_error = AuthState.ACTIVE, ""
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)
            if _is_auth_error(msg, AUTH_ERRNOS):
                self.cookie_state, self.cookie_error = AuthState.EXPIRED, msg[:200]
            else:
                self.cookie_state, self.cookie_error = AuthState.ERROR, msg[:200]
        self.cookie_checked_at = time.time()
        return self.cookie_state

    # ------------------------------------------------------------------ #
    # OAuth token
    # ------------------------------------------------------------------ #
    @property
    def app_id(self) -> int:
        return int(self._data.get("app_id") or DEFAULT_APP_ID)

    @property
    def access_token(self) -> str:
        self.reload_if_changed()
        return str(self._data.get("access_token") or "")

    @property
    def refresh_token(self) -> str:
        self.reload_if_changed()
        return str(self._data.get("refresh_token") or "")

    @property
    def has_oauth(self) -> bool:
        return bool(self.access_token or self.refresh_token)

    def save_tokens(self, access_token: str, refresh_token: str,
                    app_id: int | None = None) -> None:
        with self._lock:
            self._data["access_token"] = access_token
            self._data["refresh_token"] = refresh_token
            self._data["app_id"] = int(app_id or self.app_id)
            now = int(time.time())
            self._data["oauth_updated_at"] = now
            self._data["oauth_expires_at"] = now + OAUTH_LIFETIME_SEC
            self._save()
            self._open_client = None
            self.oauth_state, self.oauth_error, self.oauth_checked_at = (
                AuthState.UNKNOWN, "", 0.0)

    def clear_oauth(self) -> None:
        with self._lock:
            for key in ("access_token", "refresh_token", "app_id", "oauth_updated_at",
                        "oauth_expires_at"):
                self._data.pop(key, None)
            self._save()
            self._open_client = None
            self.oauth_state, self.oauth_error, self.oauth_checked_at = (
                AuthState.UNKNOWN, "", 0.0)

    def open_client(self):
        """返回可用的 P115OpenClient（无 token 返回 None）。

        用子类包装 refresh，token 刷新成功后回写磁盘；构造时禁止命令行
        二维码交互（授权一律走 Web 开放平台扫码流程）。
        """
        if not self.has_oauth:
            return None
        with self._lock:
            if self._open_client is not None:
                return self._open_client
            from p115client import P115OpenClient

            store = self
            base_access = self.access_token
            base_refresh = self.refresh_token
            app_id = self.app_id
            ManagedBase = self._managed_client_class(P115OpenClient)

            class _ManagedOpenClient(ManagedBase):
                def refresh_access_token(self, /, async_=False, **kw):  # noqa: ANN001
                    if async_:
                        async def refresh_async():
                            data = super(_ManagedOpenClient, self).refresh_access_token(
                                async_=True, **kw)
                            if inspect.isawaitable(data):
                                data = await data
                            store.save_tokens(data["access_token"], data["refresh_token"])
                            log.info("115 OAuth access_token 已自动续期并回写")
                            return data
                        return refresh_async()
                    with store._refresh_lock:
                        with store._refresh_file_lock():
                            store.reload_if_changed()
                            # 另一个线程/进程已轮换 token 时直接采用最新值，不再重复刷新旧 refresh_token。
                            if store.access_token and store.access_token != self.access_token:
                                self.access_token = store.access_token
                                self.refresh_token = store.refresh_token
                                return {"access_token": self.access_token,
                                        "refresh_token": self.refresh_token}
                            data = super().refresh_access_token(async_=False, **kw)
                            store.save_tokens(data["access_token"], data["refresh_token"])
                            log.info("115 OAuth access_token 已自动续期并回写")
                            return data

            client = _ManagedOpenClient(base_access, base_refresh, app_id,
                                        console_qrcode=False)
            if client.access_token and client.access_token != base_access:
                self.save_tokens(client.access_token, client.refresh_token, app_id)
            self._open_client = client
            return client

    def refresh_oauth_if_due(self, force: bool = False) -> bool:
        """到期前主动续期；refresh_token 轮换后由 managed client 立即回写。"""
        self.reload_if_changed()
        if not self.has_oauth:
            return False
        expires = float(self._data.get("oauth_expires_at") or 0)
        advance = OAUTH_REFRESH_ADVANCE_SEC
        if not force and expires and time.time() < expires - advance:
            return False
        client = self.open_client()
        if client is None:
            return False
        client.refresh_access_token()
        return True

    def ping_oauth(self, force: bool = False) -> str:
        """对开放平台做一次最轻量探活（列根目录 1 条）。"""
        if not force and time.time() - self.oauth_checked_at < 600 \
                and self.oauth_state != AuthState.UNKNOWN:
            return self.oauth_state
        try:
            client = self.open_client()
            if client is None:
                self.oauth_state, self.oauth_error = AuthState.UNKNOWN, "未配置 OAuth"
                self.oauth_checked_at = 0.0
                return self.oauth_state
            resp = client.fs_files_open({"cid": 0, "limit": 1})
            if isinstance(resp, dict) and resp.get("state", True) is False:
                raise RuntimeError(str(resp))
            self.oauth_state, self.oauth_error = AuthState.ACTIVE, ""
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)
            if _is_auth_error(msg, OPEN_AUTH_CODES):
                self.oauth_state, self.oauth_error = AuthState.EXPIRED, msg[:200]
            else:
                self.oauth_state, self.oauth_error = AuthState.ERROR, msg[:200]
        self.oauth_checked_at = time.time()
        return self.oauth_state

    # ------------------------------------------------------------------ #
    # Cookie 扫码登录：绑定独立设备会话，自动换取并保存 UID/CID/SEID/KID
    # ------------------------------------------------------------------ #
    def cookie_qr_start(self, app: str | None = None) -> dict[str, Any]:
        from p115client import P115Client

        # 默认绑定独立 TV 会话，降低与网页端/手机端既有会话互踢概率。
        device = (app or COOKIE_QR_APP).strip() or COOKIE_QR_APP
        # 二维码 token 固定从 web 入口申请；确认后再绑定到独立 device 会话。
        # 这与 p115client.login_with_qrcode 的实现一致。
        resp = P115Client.login_qrcode_token()
        data = (resp or {}).get("data") or {}
        uid = data.get("uid")
        if not uid:
            raise RuntimeError(f"申请 115 Cookie 二维码失败：{resp}")
        qrcode_url = data.get("qrcode") or f"https://115.com/scan/dg-{uid}"
        with self._lock:
            self._gc_cookie_qr()
            self._cookie_qr_pending[uid] = {
                "sign": data.get("sign"), "time": data.get("time"),
                "app": device, "qrcode_url": qrcode_url,
                "created_at": time.time(),
            }
        return {"uid": uid, "qrcode_url": qrcode_url, "app": device,
                "expires_at": int(time.time()) + QR_TTL}

    def cookie_qr_session(self, uid: str) -> dict[str, Any]:
        with self._lock:
            self._gc_cookie_qr()
            session = self._cookie_qr_pending.get(uid)
        if not session:
            raise KeyError("Cookie 扫码会话不存在或已过期，请重新生成二维码")
        return session

    def cookie_qr_status(self, uid: str) -> dict[str, Any]:
        from p115client import P115Client

        session = self.cookie_qr_session(uid)
        resp = P115Client.login_qrcode_scan_status({
            "uid": uid, "time": session["time"], "sign": session["sign"]})
        data = (resp or {}).get("data") or {}
        status = data.get("status")
        if status is None:
            raise RuntimeError(f"115 未返回 Cookie 扫码状态：{resp}")
        if status == 2:
            result = P115Client.login_qrcode_scan_result(uid, app=session["app"])
            cookies = ((result or {}).get("data") or {}).get("cookie")
            if isinstance(cookies, dict):
                cookies = "; ".join(f"{key}={value}" for key, value in cookies.items())
            if not isinstance(cookies, str) or "UID=" not in cookies.upper() or "CID=" not in cookies.upper():
                raise RuntimeError("扫码已确认但未返回完整 Cookie")
            self.set_cookies(cookies)
            with self._lock:
                self._cookie_qr_pending.pop(uid, None)
            state = self.ping_cookie(force=True)
            return {"status": 2, "message": "Cookie 已自动保存并验证",
                    "authenticated": True, "state": state}
        messages = {0: "等待扫码", 1: "已扫码，等待在手机上确认",
                    -1: "二维码已过期", -2: "已取消登录"}
        if status in (-1, -2):
            with self._lock:
                self._cookie_qr_pending.pop(uid, None)
        return {"status": status, "message": messages.get(status, "未知状态"),
                "authenticated": False}

    def _gc_cookie_qr(self) -> None:
        now = time.time()
        expired = [uid for uid, s in self._cookie_qr_pending.items()
                   if now - float(s.get("created_at", 0)) > QR_TTL]
        for uid in expired:
            self._cookie_qr_pending.pop(uid, None)

    # ------------------------------------------------------------------ #
    # 开放平台扫码授权（authDeviceCode / PKCE）
    # ------------------------------------------------------------------ #
    def qr_start(self, app_id: int | None = None) -> dict[str, Any]:
        """申请开放平台登录二维码，返回 {uid, qrcode_url, app_id, expires_at}。"""
        from p115client import P115OpenClient

        aid = int(app_id or self.app_id)
        resp = P115OpenClient.login_qrcode_token_open(aid)
        if not isinstance(resp, dict) or resp.get("code"):
            raise RuntimeError(f"申请 115 授权二维码失败：{resp}")
        data = resp.get("data") or {}
        uid = data.get("uid")
        if not uid:
            raise RuntimeError(f"授权二维码响应缺少 uid：{resp}")
        qrcode_url = data.get("qrcode") or f"https://115.com/scan/dg-{uid}"
        with self._lock:
            self._gc_qr()
            self._qr_pending[uid] = {
                "sign": data.get("sign"),
                "time": data.get("time"),
                "app_id": aid,
                "created_at": time.time(),
            }
        return {"uid": uid, "qrcode_url": qrcode_url, "app_id": aid,
                "expires_at": int(time.time()) + QR_TTL}

    def qr_session(self, uid: str) -> dict[str, Any]:
        """返回未过期的扫码会话；不存在/已过期抛 KeyError（供图片接口校验）。"""
        with self._lock:
            self._gc_qr()
            session = self._qr_pending.get(uid)
        if not session:
            raise KeyError("扫码会话不存在或已过期，请重新生成二维码")
        return session

    def qr_status(self, uid: str) -> dict[str, Any]:
        """轮询扫码状态；用户确认登录(status=2)后换取并保存 token。

        返回 {status: 0|1|2|-1|-2, message, authenticated?}。
        """
        from p115client import P115OpenClient

        with self._lock:
            self._gc_qr()
            session = self._qr_pending.get(uid)
        if not session:
            raise KeyError("扫码会话不存在或已过期，请重新生成二维码")
        resp = P115OpenClient.login_qrcode_scan_status({
            "uid": uid, "time": session["time"], "sign": session["sign"]})
        data = (resp or {}).get("data") or {}
        status = data.get("status")
        if status == 2:
            token_resp = P115OpenClient.login_qrcode_access_token_open(uid)
            tokens = (token_resp or {}).get("data") or {}
            access = tokens.get("access_token")
            refresh = tokens.get("refresh_token")
            if not access or not refresh:
                raise RuntimeError(f"扫码已确认但换取令牌失败：{token_resp}")
            self.save_tokens(access, refresh, session["app_id"])
            with self._lock:
                self._qr_pending.pop(uid, None)
            return {"status": 2, "message": "已授权", "authenticated": True}
        messages = {0: "等待扫码", 1: "已扫码，等待在手机上确认",
                    -1: "二维码已过期", -2: "已取消授权"}
        if status in (-1, -2):
            with self._lock:
                self._qr_pending.pop(uid, None)
        return {"status": status, "message": messages.get(status, "未知状态"),
                "authenticated": False}

    def _gc_qr(self) -> None:
        now = time.time()
        expired = [uid for uid, s in self._qr_pending.items()
                   if now - float(s.get("created_at", 0)) > QR_TTL]
        for uid in expired:
            self._qr_pending.pop(uid, None)

    # ------------------------------------------------------------------ #
    # 混合写操作：优先 OAuth open API，失败回退 cookie webapi
    # ------------------------------------------------------------------ #
    def mkdir(self, parent: int, name: str) -> dict[str, Any]:
        """在 parent 下建目录，返回 {"id": int, "name": str}。幂等由调用方保证。"""
        open_client = self._try_open_client()
        if open_client is not None:
            try:
                resp = open_client.fs_mkdir_open(
                    {"file_name": name, "pid": int(parent)})
                fid = self._extract_id(resp)
                if fid:
                    return {"id": int(fid), "name": name}
                log.warning("OAuth 建目录未返回 id，回退 cookie：%s", resp)
            except Exception as exc:  # noqa: BLE001
                log.warning("OAuth 建目录失败，回退 cookie：%s", exc)
        return self._cookie_mkdir(parent, name)

    def rename(self, fid: int, name: str) -> None:
        open_client = self._try_open_client()
        if open_client is not None:
            try:
                open_client.fs_rename_open({"file_id": int(fid), "file_name": name})
                return
            except Exception as exc:  # noqa: BLE001
                log.warning("OAuth 重命名失败，回退 cookie：%s", exc)
        client = self.cookie_client()
        if client is None:
            raise RuntimeError("重命名失败：OAuth 与 cookie 均不可用")
        if hasattr(client, "fs_rename"):
            client.fs_rename((int(fid), name))
        else:
            from p115client import P115FileSystem
            P115FileSystem(client).rename(int(fid), name=name)

    def move(self, fid: int, pid: int) -> None:
        open_client = self._try_open_client()
        if open_client is not None:
            try:
                open_client.fs_move_open({"file_ids": int(fid), "to_cid": int(pid)})
                return
            except Exception as exc:  # noqa: BLE001
                log.warning("OAuth 移动失败，回退 cookie：%s", exc)
        client = self.cookie_client()
        if client is None:
            raise RuntimeError("移动失败：OAuth 与 cookie 均不可用")
        if hasattr(client, "fs_move"):
            client.fs_move(int(fid), pid=int(pid))
        else:
            from p115client import P115FileSystem
            P115FileSystem(client).move(int(fid), to_dir=int(pid))

    def delete(self, fids: list[int]) -> None:
        open_client = self._try_open_client()
        if open_client is not None:
            try:
                open_client.fs_delete_open([int(x) for x in fids])
                return
            except Exception as exc:  # noqa: BLE001
                log.warning("OAuth 删除失败，回退 cookie：%s", exc)
        client = self.cookie_client()
        if client is None:
            raise RuntimeError("删除失败：OAuth 与 cookie 均不可用")
        client.fs_delete([int(x) for x in fids])

    def _try_open_client(self):
        """返回可用 open client；构造失败（如刷新失败）不致命，回退 cookie。"""
        if not self.has_oauth:
            return None
        try:
            return self.open_client()
        except Exception as exc:  # noqa: BLE001
            log.warning("OAuth 客户端不可用，本次写操作回退 cookie：%s", exc)
            return None

    @staticmethod
    def _extract_id(resp: Any) -> int | None:
        if isinstance(resp, (int, str)) and str(resp).isdecimal():
            return int(resp)
        if not isinstance(resp, dict):
            return None
        for key in ("file_id", "cid", "id"):
            value = resp.get(key)
            if value is not None and str(value).isdecimal():
                return int(value)
        data = resp.get("data")
        if isinstance(data, dict):
            for key in ("file_id", "cid", "id", "category_id"):
                value = data.get(key)
                if value is not None and str(value).isdecimal():
                    return int(value)
        return None

    def _cookie_mkdir(self, parent: int, name: str) -> dict[str, Any]:
        """cookie 建目录：先 webapi /files/add，失败回退 SDK app 路径。"""
        client = self.cookie_client()
        if client is None:
            raise RuntimeError("建目录失败：OAuth 与 cookie 均不可用")
        try:
            resp = client.fs_mkdir({"cname": name, "pid": int(parent)})
            if isinstance(resp, dict) and resp.get("state", True) is not False:
                fid = (resp.get("file_id") or resp.get("cid")
                       or (resp.get("data") or {}).get("category_id"))
                if fid:
                    return {"id": int(fid), "name": name}
        except Exception as exc:  # noqa: BLE001
            log.warning("webapi 建目录异常，回退 app 接口：%s", exc)
        from p115client import P115FileSystem
        made = P115FileSystem(client).mkdir(int(parent), name=name)
        return {"id": int(made["id"] if isinstance(made, dict) else made),
                "name": name}

    # ------------------------------------------------------------------ #
    def status(self) -> dict[str, Any]:
        return {
            "cookie": {
                "state": self.cookie_state,
                "configured": self.has_cookie,
                "source": "file" if self.cookie_from_file else "none",
                "error": self.cookie_error,
                "checked_at": self.cookie_checked_at or None,
            },
            "oauth": {
                "state": self.oauth_state,
                "configured": self.has_oauth,
                "has_access_token": bool(self.access_token),
                "has_refresh_token": bool(self.refresh_token),
                "app_id": self.app_id if self.has_oauth else None,
                "updated_at": self._data.get("oauth_updated_at"),
                "expires_at": self._data.get("oauth_expires_at"),
                "expires_in": max(0, int(float(self._data.get("oauth_expires_at") or 0) - time.time())) if self._data.get("oauth_expires_at") else None,
                "error": self.oauth_error,
                "checked_at": self.oauth_checked_at or None,
            },
            "traffic": {
                "cooling_down": self._cooldown_until > time.time(),
                "cooldown_until": self._cooldown_until or None,
                "cooldown_remaining": max(0, int(self._cooldown_until - time.time())),
                "cooldown_reason": self._cooldown_reason,
                "open_qps": self._qps("open"),
                "cookie_qps": self._qps("cookie"),
                "share_qps": self._qps("share"),
            },
            "ready": self.has_cookie or self.has_oauth,
            "writes_via": "oauth" if self.has_oauth else (
                "cookie" if self.has_cookie else "none"),
        }


# 进程级单例
_store: CredentialStore | None = None
_store_lock = threading.Lock()


def get_store() -> CredentialStore:
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = CredentialStore()
    return _store


def reset_store(store: CredentialStore | None = None) -> CredentialStore:
    """替换进程级单例（主要给测试做隔离）；传 None 则惰性重建默认实例。"""
    global _store
    with _store_lock:
        _store = store
    return get_store()
