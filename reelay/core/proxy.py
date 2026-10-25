"""网络代理：Telegram、TMDB 与 AI 服务可以经 HTTP 或 SOCKS5 代理连接（系统配置 ``proxy``）。

115 的服务器在国内，始终直连。没给某项服务开代理时它按原来的方式连接：TMDB 与 AI 仍会使用环境变量
HTTP(S)_PROXY，Telegram 直连。

代理密码是密钥（``proxy_password``），不写进地址；和 AI 密钥一样绑定在代理地址上，改了地址就清除
（见 service.settings）。地址里只写用户名，用到时再拼上密码。

- ``url_for(service)``：这项服务要用的代理地址（带密码），请求时读取，管理台改了立即生效；
- ``telethon()``：Telethon 的 proxy 参数（需要 python-socks），客户端创建时读取；
- ``http_client(url)``：TMDB 走代理时用的 httpx2 客户端，标准库不支持 SOCKS；
- ``probe(...)``：管理台「测试代理」，经代理连一次各项服务。
"""
from __future__ import annotations

import asyncio
import threading
import time
import urllib.parse
from typing import Any

from . import runtime, secret_store

SERVICES = {"telegram": "Telegram", "tmdb": "TMDB", "ai": "AI 服务"}
SCHEMES = ("http", "socks5", "socks5h")
PROBE_TIMEOUT = 15
_CLIENTS: dict[str, Any] = {}
_LOCK = threading.Lock()  # TMDB 查询在工作线程里并发执行（asyncio.to_thread）


def validate(section: dict) -> None:
    """系统配置 proxy：地址可以留空（不使用代理），三项服务各一个开关。"""
    if type(section.get("url")) is not str:
        raise ValueError("proxy.url 必须是字符串")
    section["url"] = section["url"].strip().rstrip("/")
    if section["url"]:
        parse(section["url"])
    for name in SERVICES:
        if type(section.get(name)) is not bool:
            raise ValueError(f"proxy.{name} 必须是 true 或 false")


def parse(url: str) -> urllib.parse.SplitResult:
    parts = urllib.parse.urlsplit(url)
    if parts.scheme.lower() not in SCHEMES:
        raise ValueError("proxy.url 必须以 http:// 或 socks5:// 开头，如 http://127.0.0.1:7890")
    try:
        port = parts.port
    except ValueError:
        raise ValueError("proxy.url 的端口无效") from None
    if not parts.hostname or not port:
        raise ValueError("proxy.url 要写明地址和端口，如 http://127.0.0.1:7890")
    if parts.password is not None:
        raise ValueError("代理密码不要写在 proxy.url 里，在「网络代理」的代理密码中填写")
    if parts.path or parts.query or parts.fragment:
        raise ValueError("proxy.url 只写到端口为止，如 http://127.0.0.1:7890")
    return parts


def with_password(url: str, password: str | None) -> str:
    """地址里写了用户名时拼上密码；没写用户名时密码用不上。"""
    parts = urllib.parse.urlsplit(url)
    if not password or not parts.username:
        return url
    netloc = f"{parts.username}:{urllib.parse.quote(password, safe='')}@{parts.netloc.rsplit('@', 1)[1]}"
    return parts._replace(netloc=netloc).geturl()


def url_for(service: str, cfg: dict | None = None) -> str | None:
    """这项服务要用的代理地址（带上已保存的密码）；没配置代理或这项没开时为 None。"""
    section = cfg.get("proxy") if cfg is not None else runtime.get("proxy")
    if not isinstance(section, dict) or not section.get("url") or not section.get(service):
        return None
    return with_password(section["url"], secret_store.get("proxy_password"))


def telethon(cfg: dict | None = None) -> dict | None:
    """Telethon 的 proxy 参数；Telegram 没开代理时为 None（直连）。"""
    url = url_for("telegram", cfg)
    return _telethon_proxy(url) if url else None


def _telethon_proxy(url: str) -> dict:
    parts = urllib.parse.urlsplit(url)
    unquote = lambda value: urllib.parse.unquote(value) if value else None  # noqa: E731
    return {"proxy_type": "http" if parts.scheme.lower() == "http" else "socks5", "addr": parts.hostname,
            "port": parts.port, "username": unquote(parts.username), "password": unquote(parts.password),
            "rdns": True}


def http_client(url: str) -> Any:
    """经代理请求的同步 httpx2 客户端（随 AI SDK 安装）；同一个代理复用连接。"""
    with _LOCK:
        client = _CLIENTS.get(url)
        if client is None:
            import httpx2  # 延迟导入
            _CLIENTS.clear()  # 换了代理，旧客户端不再使用
            client = _CLIENTS[url] = httpx2.Client(proxy=url)
        return client


# --------------------------------------------------------------------------- #
# 管理台「测试代理」
# --------------------------------------------------------------------------- #
async def probe(url: str, services: list[str], *, tmdb_url: str, llm_url: str,
                api_id: str | None, api_hash: str | None) -> list[dict[str, Any]]:
    """先确认代理本身连得上，再经代理逐项连一次（并发）：Telegram 用 Telethon 连数据中心（不登录），
    TMDB 与 AI 发一个 HTTP 请求，服务器有回应（哪怕是未授权）就算连得上。url 已带密码；错误说明里去掉密码再返回。"""
    services = [s for s in services if s in SERVICES]
    result = lambda service, error, started: {  # noqa: E731
        "service": service, "label": SERVICES[service], "ok": error is None, "error": error,
        "seconds": round(time.monotonic() - started, 1)}
    parts = urllib.parse.urlsplit(url)
    started = time.monotonic()
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(parts.hostname, parts.port), PROBE_TIMEOUT)
        writer.close()
    except (OSError, asyncio.TimeoutError) as exc:
        error = f"连不上代理 {parts.hostname}:{parts.port}（{_describe(exc, url)}）"
        return [result(service, error, started) for service in services]

    async def one(service: str) -> dict[str, Any]:
        started = time.monotonic()
        try:
            if service == "telegram":
                await _probe_telegram(url, api_id, api_hash)
            else:
                target = f"{tmdb_url}/configuration" if service == "tmdb" else f"{llm_url}/models"
                await _probe_http(url, target)
            error = None
        except Exception as exc:  # noqa: BLE001 - 原因交给用户判断（代理认证、规则或节点）
            error = _describe(exc, url)
        return result(service, error, started)
    return list(await asyncio.gather(*(one(s) for s in services)))


async def _probe_http(proxy_url: str, target: str) -> None:
    import httpx2  # 延迟导入
    async with httpx2.AsyncClient(proxy=proxy_url, timeout=PROBE_TIMEOUT) as client:
        await client.get(target)


async def _probe_telegram(proxy_url: str, api_id: str | None, api_hash: str | None) -> None:
    from telethon import TelegramClient  # 延迟导入
    from telethon.errors import RPCError
    from telethon.sessions import StringSession
    # 还没填 API ID 时用占位值：Telegram 回了「API ID 无效」也说明经代理连上了
    client = TelegramClient(StringSession(), int(api_id or 1), api_hash or "0" * 32, proxy=_telethon_proxy(proxy_url),
                            timeout=PROBE_TIMEOUT, connection_retries=0, receive_updates=False)
    try:
        await asyncio.wait_for(client.connect(), PROBE_TIMEOUT * 2)
    except RPCError:
        pass
    except ConnectionError:  # Telethon 只说连接失败，具体原因记在它的日志里
        raise ConnectionError("经代理没能连上 Telegram，检查代理的认证、规则或节点") from None
    finally:
        await client.disconnect()


def _describe(exc: Exception, url: str) -> str:
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)) or "Timeout" in type(exc).__name__:
        text = "连接超时"
    elif type(exc) is ConnectionError:  # 上面 _probe_telegram 写好的说明
        text = str(exc)
    else:
        text = f"{type(exc).__name__}：{exc}" if str(exc) else type(exc).__name__
    password = urllib.parse.urlsplit(url).password
    for secret in {password, urllib.parse.unquote(password)} if password else ():
        text = text.replace(secret, "***")
    return text[:300]
