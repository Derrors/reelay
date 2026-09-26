"""插件注册机 + 目录自动发现。

「加功能不改内核」的落点：任何 Provider / Hook / EventListener 用 ``@register``
声明，放进 ``reelay/plugins/`` 目录即被自动发现并注册；删文件或配置关掉即卸载。
内核只认接口、不认具体实现。
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
from typing import Any, Callable

log = logging.getLogger("reelay.registry")


class Registry:
    """每个应用实例独立的注册表，避免共享客户端与模拟模式。"""

    def __init__(self) -> None:
        # Provider 按「种类 -> {name: instance}」存，运行时按配置选用哪个
        self.providers: dict[str, dict[str, Any]] = {}
        # Hook 按 stage 分组，组内按 order 升序
        self.hooks: dict[str, list[Any]] = {}
        # EventListener 按事件名分组
        self.listeners: dict[str, list[Any]] = {}

    # -- 注册入口（供 @register 调用）------------------------------------ #
    def add_provider(self, kind: str, instance: Any) -> None:
        name = getattr(instance, "name", instance.__class__.__name__)
        self.providers.setdefault(kind, {})[name] = instance
        log.debug("provider registered: %s/%s", kind, name)

    def add_hook(self, instance: Any) -> None:
        stage = instance.stage
        self.hooks.setdefault(stage, []).append(instance)
        self.hooks[stage].sort(key=lambda h: getattr(h, "order", 100))
        log.debug("hook registered: %s (order=%s)", stage, getattr(instance, "order", 100))

    def add_listener(self, instance: Any) -> None:
        self.listeners.setdefault(instance.event, []).append(instance)
        log.debug("listener registered: %s", instance.event)

    # -- 查询 ----------------------------------------------------------- #
    def get_provider(self, kind: str, name: str) -> Any:
        try:
            return self.providers[kind][name]
        except KeyError as exc:
            raise KeyError(
                f"未找到 provider {kind}/{name}；已注册: "
                f"{list(self.providers.get(kind, {}))}"
            ) from exc

    def hooks_for(self, stage: str, config: dict | None = None) -> list[Any]:
        return self._enabled(self.hooks.get(stage, []), config)

    def listeners_for(self, event: str, config: dict | None = None) -> list[Any]:
        return self._enabled(self.listeners.get(event, []), config)

    @staticmethod
    def extension_name(instance: Any) -> str:
        return getattr(instance, "name", instance.__class__.__name__)

    def _enabled(self, instances: list, config: dict | None) -> list:
        toggles = (config or {}).get("plugins", {})
        return [obj for obj in instances if toggles.get(self.extension_name(obj), True)]

    def validate_config(self, cfg: dict) -> None:
        try:
            self.get_provider("source", cfg["providers"]["source"])
            for name in cfg["providers"]["notify"]:
                self.get_provider("notify", name)
            for rule in [cfg["defaults"], *cfg["sources"]]:
                for kind in ("parser", "decision", "storage"):
                    self.get_provider(kind, rule.get(kind, cfg["defaults"][kind]))
        except KeyError as exc:
            raise ValueError(str(exc)) from exc
        extensions = {self.extension_name(obj) for groups in (self.hooks, self.listeners)
                      for items in groups.values() for obj in items}
        if set(cfg["plugins"]) - extensions:
            raise ValueError("plugins 包含未注册的扩展名称")
        if "organizer" in extensions and cfg["plugins"].get("organizer", True):
            if any(rule.get("storage", cfg["defaults"]["storage"]) != "p115"
                   for rule in [cfg["defaults"], *cfg["sources"]]):
                raise ValueError("organizer 仅支持 p115；其他存储需关闭它并注册对应的整理扩展")

    def summary(self) -> dict[str, Any]:
        return {
            "providers": {k: list(v) for k, v in self.providers.items()},
            "hooks": {k: len(v) for k, v in self.hooks.items()},
            "listeners": {k: len(v) for k, v in self.listeners.items()},
        }


# 装饰器只记录类；客户端在 discover 时按当时的运行环境实例化。
_DECLARATIONS: list[tuple[str, dict[str, Any], type]] = []


def register(kind: str, **kw: Any) -> Callable[[type], type]:
    """声明式注册。

    - ``kind="provider"``：需再给 ``ptype``（source/parser/decision/storage/notify）
    - ``kind="hook"``：需给 ``stage`` 与可选 ``order``
    - ``kind="event"``：需给 ``event``
    这些参数也可以直接作为类属性写在类里；装饰器参数优先。
    """

    def deco(cls: type) -> type:
        _DECLARATIONS.append((kind, kw, cls))
        return cls

    return deco


def _instantiate(registry: Registry) -> None:
    for kind, kw, cls in _DECLARATIONS:
        obj = cls()
        # 允许装饰器参数覆盖/补齐类属性
        for k, v in kw.items():
            if k not in ("ptype",):
                setattr(obj, k, v)
        if kind == "provider":
            registry.add_provider(kw["ptype"], obj)
        elif kind == "hook":
            registry.add_hook(obj)
        elif kind == "event":
            registry.add_listener(obj)


def discover(packages: tuple[str, ...] = ("reelay.providers", "reelay.plugins")) -> Registry:
    """导入指定包下所有模块，触发 ``@register``，再统一实例化。"""
    for pkg_name in packages:
        try:
            pkg = importlib.import_module(pkg_name)
        except ModuleNotFoundError:
            continue
        for mod in pkgutil.iter_modules(pkg.__path__):
            if mod.name.startswith("_"):
                continue
            importlib.import_module(f"{pkg_name}.{mod.name}")
    registry = Registry()
    _instantiate(registry)
    return registry
