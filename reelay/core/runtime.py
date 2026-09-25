"""运行时共享上下文。

Hook / EventListener 的 ``run(ctx)`` 签名里只有单条任务的 ctx，拿不到全局配置。
这里提供一个进程级的全局配置引用，由启动时（Pipeline 初始化）注入，
插件按需只读地取用（如 DirRouter 读 routing、Organizer 读 naming 模板）。
"""

from __future__ import annotations

from typing import Any

_CONFIG: dict[str, Any] = {}


def set_config(cfg: dict[str, Any]) -> None:
    global _CONFIG
    _CONFIG = cfg or {}


def get_config() -> dict[str, Any]:
    return _CONFIG


def get(path: str, default: Any = None) -> Any:
    """点路径取配置，如 get('organize.naming.tv')。"""
    cur: Any = _CONFIG
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return default
    return cur
