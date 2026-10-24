"""运行时共享上下文。

Hook / EventListener 的 ``run(ctx)`` 签名里只有单条任务的 ctx，拿不到全局配置。
Pipeline 用 ContextVar 注入当前任务的配置和内核服务，协程和 to_thread 互相隔离；
作用域之外读到的是进程配置（``set_process_config``）。

扩展通过 ``services()`` 使用内核能力，而不是直接持有 Store：

- ``services().notify(text)``：给所有通知渠道发一条系统通知（经 Outbox 持久化投递）；
- ``services().state(name)``：按扩展名称隔离的持久化 KV。
"""

from __future__ import annotations

from typing import Any
from contextlib import contextmanager
from contextvars import ContextVar

_CONFIG: dict[str, Any] = {}
# 进程当前的有效配置（Pipeline 设置）：不在任务作用域里的调用（如 Web 发起的 TMDB 查询）读系统配置用
_PROCESS: dict[str, Any] = {}
_SCOPED: ContextVar[dict[str, Any] | None] = ContextVar("reelay_config", default=None)
_SERVICES: ContextVar["Services | None"] = ContextVar("reelay_services", default=None)


class PluginState:
    """单个扩展的持久化状态；值必须可 JSON 序列化。"""

    def __init__(self, store, plugin: str) -> None:
        self._store, self._plugin = store, plugin

    def get(self, key: str, default: Any = None) -> Any:
        return self._store.state_get(self._plugin, key, default)

    def set(self, key: str, value: Any) -> None:
        self._store.state_set(self._plugin, key, value)

    def delete(self, key: str) -> None:
        self._store.state_delete(self._plugin, key)

    def items(self) -> dict[str, Any]:
        return self._store.state_items(self._plugin)


class Services:
    """内核暴露给扩展的最小能力集。"""

    def __init__(self, store, notify_targets) -> None:
        self.store = store
        self._targets = notify_targets

    def notify(self, text: str, *, key: str = "plugin", html: bool = False, buttons: list | None = None) -> None:
        targets = self._targets()
        if targets:
            self.store.stage_notice(key, text, targets, html=html, buttons=buttons)

    def state(self, plugin: str) -> PluginState:
        return PluginState(self.store, plugin)


@contextmanager
def scope(cfg: dict[str, Any], services: Services | None = None):
    """配置属于当前协程/应用；to_thread 也会继承它。"""
    token = _SCOPED.set(cfg)
    service_token = _SERVICES.set(services) if services is not None else None
    try:
        yield
    finally:
        if service_token is not None:
            _SERVICES.reset(service_token)
        _SCOPED.reset(token)


def services() -> Services:
    current = _SERVICES.get()
    if current is None:
        raise RuntimeError("当前不在 Pipeline 作用域内，无法使用内核服务")
    return current


def observe(component: str, status: str, error: str = "") -> None:
    """记录一次真实调用的结果（如 LLM、TMDB），管理台「连接状态」显示最近一次结果。

    这类观察只在实际调用时产生，不会因为一段时间没有调用而过期；不在管线作用域内时忽略。
    """
    current = _SERVICES.get()
    if current is not None:
        current.store.heartbeat(component, status=status, error=error, event_driven=True)


def set_process_config(cfg: dict[str, Any]) -> None:
    global _PROCESS
    _PROCESS = cfg or {}


def get_config() -> dict[str, Any]:
    scoped = _SCOPED.get()
    if scoped is not None:
        return scoped
    return _PROCESS


def plugin_config(name: str, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """插件自己的设置 ``plugins.<name>``（不含 enabled）；cfg 省略时用当前作用域的配置。"""
    section = ((get_config() if cfg is None else cfg).get("plugins") or {}).get(name) or {}
    return {key: value for key, value in section.items() if key != "enabled"} if isinstance(section, dict) else {}


def get(path: str, default: Any = None) -> Any:
    """点路径取配置，如 get('plugins.organizer.naming.tv')。"""
    cur: Any = get_config()
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return default
    return cur
