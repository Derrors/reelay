"""版本更新检查：查询 GitHub 上最新的正式版，与当前版本比较。源码部署的一键升级见 ``core/upgrade.py``。

管理台侧栏与控制 Bot 的提醒共用一份结果，存在 ``plugin_state`` 的 ``update_check`` 下，最多每 6 小时联网
查询一次（失败后 1 小时再试）；管理台「检查更新」立即查询。开关是插件 ``update_check``
（见 ``plugins/update_check``），关掉后两处都不再联网。

升级按部署方式执行（Docker：``docker compose pull && docker compose up -d``）。``.env`` 里固定了
``REELAY_VERSION`` 时拉取不会升级，要先改版本号，状态里的 ``pinned`` 用来提示这一点。
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .. import __version__
from . import proxy

NAME = "update_check"  # 插件名，也是状态的命名空间
REPO = "Derrors/reelay"
RELEASES = f"https://github.com/{REPO}/releases"
LATEST_API = f"https://api.github.com/repos/{REPO}/releases/latest"
CHECK_INTERVAL = 6 * 3600
RETRY_INTERVAL = 3600
TIMEOUT = 10
NOTES_LIMIT = 20_000
_LOCK = threading.Lock()  # 几个页面同时打开时只查一次


def parse_version(text: Any) -> tuple[int, int, int] | None:
    """「1.5.2」「v1.5.2」→ (1, 5, 2)；预发布版本（带 -）与其他写法为 None。"""
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", str(text or "").strip())
    return (int(match[1]), int(match[2]), int(match[3])) if match else None


def newer(latest: str | None, current: str = __version__) -> bool:
    a, b = parse_version(latest), parse_version(current)
    return bool(a and b and a > b)


def pinned_version() -> str | None:
    """部署时固定的镜像版本（.env 的 REELAY_VERSION）；写 latest 或没写时为 None。"""
    value = os.getenv("REELAY_VERSION", "").strip()
    return value if value and value.lower() != "latest" else None


def in_docker() -> bool:
    return Path("/.dockerenv").exists()


def fetch_latest(timeout: float = TIMEOUT) -> dict[str, Any]:
    """GitHub 上最新的正式版（不含预发布）。网络或接口出错时抛异常。"""
    headers = {"Accept": "application/vnd.github+json", "User-Agent": f"reelay/{__version__}"}
    via = proxy.url_for("github")
    if via:
        response = proxy.http_client(via).get(LATEST_API, headers=headers, timeout=timeout)
        if response.is_error:
            raise urllib.error.HTTPError(LATEST_API, response.status_code, response.reason_phrase, None, None)
        data = response.json()
    else:
        with urllib.request.urlopen(urllib.request.Request(LATEST_API, headers=headers), timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    version = parse_version(data.get("tag_name")) if isinstance(data, dict) else None
    if not version:
        raise ValueError("最新版本号无法识别")
    url = data.get("html_url")
    return {"version": ".".join(map(str, version)),
            "url": url if isinstance(url, str) and url.startswith(RELEASES) else RELEASES,
            "notes": str(data.get("body") or "").strip()[:NOTES_LIMIT],
            "published_at": data.get("published_at") if isinstance(data.get("published_at"), str) else None}


def describe(exc: Exception) -> str:
    """出错原因的简短说明；不带异常原文（可能含代理地址与密码）。"""
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code in (403, 429):
            return "GitHub 限制了查询频率，稍后自动重试"
        return f"GitHub 返回 {exc.code}"
    if isinstance(exc, TimeoutError) or "Timeout" in type(exc).__name__:
        return "连接 GitHub 超时，可以在「设置 → 网络代理」让 GitHub 走代理"
    if isinstance(exc, ValueError):
        return str(exc)
    return f"连不上 GitHub（{type(exc).__name__}），可以在「设置 → 网络代理」让 GitHub 走代理"


def check(store, *, force: bool = False, now: float | None = None) -> dict[str, Any]:
    """返回检查结果；到期（或 force）时先联网查询。查询失败时保留上次查到的版本。"""
    with _LOCK:
        now = time.time() if now is None else now
        state = store.state_get(NAME, "latest") or {}
        wait = RETRY_INTERVAL if state.get("error") else CHECK_INTERVAL
        if force or now - float(state.get("checked_at") or 0) >= wait:
            try:
                state = {"release": fetch_latest(), "checked_at": now, "error": None}
            except Exception as exc:  # noqa: BLE001 - 检查更新失败不影响其他功能
                state = {**state, "checked_at": now, "error": describe(exc)}
            store.state_set(NAME, "latest", state)
    return status(state)


def cached(store) -> dict[str, Any]:
    """不联网，只读上次的结果。"""
    return status(store.state_get(NAME, "latest") or {})


def status(state: dict) -> dict[str, Any]:
    release = state.get("release") or {}
    latest = release.get("version")
    return {"current": __version__, "latest": latest, "available": newer(latest),
            "url": release.get("url") or RELEASES, "notes": release.get("notes") or "",
            "published_at": release.get("published_at"), "checked_at": state.get("checked_at"),
            "error": state.get("error"), "pinned": pinned_version(), "docker": in_docker()}


def take_notice(store, result: dict[str, Any]) -> bool:
    """这个新版本还没提醒过时记下并返回 True：每个版本只提醒一次。"""
    if not result["available"] or store.state_get(NAME, "notified") == result["latest"]:
        return False
    store.state_set(NAME, "notified", result["latest"])
    return True
