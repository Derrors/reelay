"""运行时共享上下文。

Hook / EventListener 的 ``run(ctx)`` 签名里只有单条任务的 ctx，拿不到全局配置。
Pipeline 用 ContextVar 注入当前任务的配置，协程和 to_thread 互相隔离。
set_config 仅保留给独立插件调用的兼容入口，不用于应用之间共享配置。
"""

from __future__ import annotations

from typing import Any
from contextlib import contextmanager
from contextvars import ContextVar

_CONFIG: dict[str, Any] = {}
_SCOPED: ContextVar[dict[str, Any] | None] = ContextVar("reelay_config", default=None)


@contextmanager
def scope(cfg: dict[str, Any]):
    """配置属于当前协程/应用；to_thread 也会继承它。"""
    token = _SCOPED.set(cfg)
    try:
        yield
    finally:
        _SCOPED.reset(token)


def set_config(cfg: dict[str, Any]) -> None:
    global _CONFIG
    _CONFIG = cfg or {}


def get_config() -> dict[str, Any]:
    scoped = _SCOPED.get()
    return _CONFIG if scoped is None else scoped


def get(path: str, default: Any = None) -> Any:
    """点路径取配置，如 get('organize.naming.tv')。"""
    cur: Any = get_config()
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return default
    return cur
