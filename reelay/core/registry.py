"""插件注册机 + 目录自动发现。

「加功能不改内核」的落点：任何 Provider / Hook / EventListener 用 ``@register``
声明，放进 ``reelay/plugins/`` 目录即被自动发现并注册；删文件或配置关掉即卸载。
内核只认接口、不认具体实现。

内置包之外还会加载：

- ``REELAY_PLUGIN_DIRS``（``os.pathsep`` 分隔）中的 ``*.py`` 文件或包目录，
  容器部署挂载目录即可加扩展，无需重建镜像；
- 已安装发行包声明的 ``reelay.plugins`` entry point。

外部扩展导入失败只记录到 ``Registry.load_errors``，不影响内置能力。
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import logging
import os
import pkgutil
import sys
from pathlib import Path
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
        # 外部扩展的导入错误：[{"source": 路径或 entry point, "error": 摘要}]
        self.load_errors: list[dict[str, str]] = []

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

    @staticmethod
    def is_enabled(instance: Any, config: dict | None) -> bool:
        """未显式配置时按扩展声明的 ``default_enabled``（默认 True）。"""
        toggles = (config or {}).get("plugins", {})
        return toggles.get(Registry.extension_name(instance), getattr(instance, "default_enabled", True))

    def _enabled(self, instances: list, config: dict | None) -> list:
        return [obj for obj in instances if self.is_enabled(obj, config)]

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
        from .config_fields import validate_declared_field
        objects = [obj for entries in self.providers.values() for obj in entries.values()]
        objects += [obj for groups in (self.hooks, self.listeners) for entries in groups.values() for obj in entries]
        for obj in objects:
            for field in getattr(obj, 'ui_fields', []):
                validate_declared_field(cfg, field)
            # 扩展自己的复杂配置（数组、对象）由扩展校验；只校验已启用的扩展。
            if hasattr(obj, 'validate_config') and self.is_enabled(obj, cfg):
                obj.validate_config(cfg)
        extensions = {self.extension_name(obj) for groups in (self.hooks, self.listeners)
                      for items in groups.values() for obj in items}
        if set(cfg["plugins"]) - extensions:
            raise ValueError("plugins 包含未注册的扩展名称")
        if "organizer" in extensions and cfg["plugins"].get("organizer", True):
            if any(rule.get("storage", cfg["defaults"]["storage"]) != "p115"
                   for rule in [cfg["defaults"], *cfg["sources"]]):
                raise ValueError("organizer 仅支持 p115；其他存储需关闭它并注册对应的整理扩展")

    def catalog(self, config: dict) -> dict[str, Any]:
        """目录直接来自已加载注册表；新扩展声明 ui_fields 即可获得配置表单。"""
        def describe(obj, kind, slot):
            name = self.extension_name(obj)
            return {
                'name': name, 'kind': kind, 'slot': slot,
                'label': getattr(obj, 'ui_label', name),
                'description': getattr(obj, 'ui_description', (obj.__doc__ or '').strip().split('\n')[0]),
                'enabled': self.is_enabled(obj, config),
                'order': getattr(obj, 'order', None),
                'fields': getattr(obj, 'ui_fields', []),
                'config_section': getattr(obj, 'ui_section', None),
                'config_type': getattr(obj, 'ui_config_type', None),
                'required_storage': getattr(obj, 'storage_provider', None),
            }
        return {
            'providers': {kind: [describe(obj, 'provider', kind) for obj in entries.values()]
                          for kind, entries in self.providers.items()},
            'hooks': [describe(obj, 'hook', slot) for slot, entries in self.hooks.items() for obj in entries],
            'events': _merge_by_name([describe(obj, 'event', slot) for slot, entries in self.listeners.items()
                                      for obj in entries]),
            'load_errors': list(self.load_errors),
        }

    def summary(self) -> dict[str, Any]:
        return {
            "providers": {k: list(v) for k, v in self.providers.items()},
            "hooks": {k: len(v) for k, v in self.hooks.items()},
            "listeners": {k: len(v) for k, v in self.listeners.items()},
        }


# 装饰器只记录类；客户端在 discover 时按当时的运行环境实例化。
_DECLARATIONS: list[tuple[str, dict[str, Any], type]] = []
# 外部扩展的模块名：只有它们的构造失败会被隔离。
_EXTERNAL_MODULES: set[str] = set()


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


def _merge_by_name(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """同名扩展共用一个开关，在目录里只展示一项，slot 合并为逗号分隔。"""
    merged: dict[str, dict[str, Any]] = {}
    for item in items:
        if item['name'] in merged:
            merged[item['name']]['slot'] += f",{item['slot']}"
        else:
            merged[item['name']] = dict(item)
    return list(merged.values())


def _is_under(module: str, names: set[str]) -> bool:
    return any(module == name or module.startswith(name + ".") for name in names)


def _instantiate(registry: Registry, errors: list[dict[str, str]] | None = None,
                 active: set[str] | None = None) -> None:
    """active 为本次配置下的外部模块；之前其他配置加载过的外部扩展不会被实例化。"""
    for kind, kw, cls in _DECLARATIONS:
        if _is_under(cls.__module__, _EXTERNAL_MODULES) and not _is_under(cls.__module__, active or set()):
            continue
        try:
            obj = cls()
        except Exception as exc:  # noqa: BLE001
            # 内置扩展构造失败是缺陷，照常抛出；外部扩展失败隔离。
            if not _is_under(cls.__module__, _EXTERNAL_MODULES):
                raise
            log.exception("外部扩展实例化失败：%s", cls.__name__)
            if errors is not None:
                errors.append({"source": f"{cls.__module__}.{cls.__name__}",
                               "error": f"{type(exc).__name__}: {exc}"[:300]})
            continue
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


def _load_external(errors: list[dict[str, str]]) -> set[str]:
    """导入外部扩展目录与 entry point，返回本次配置涉及的模块名；同一文件在进程内只执行一次。"""
    active: set[str] = set()
    for directory in filter(None, os.getenv("REELAY_PLUGIN_DIRS", "").split(os.pathsep)):
        root = Path(directory).expanduser()
        if not root.is_dir():
            errors.append({"source": str(root), "error": "扩展目录不存在"})
            continue
        for path in sorted(root.iterdir()):
            if path.name.startswith(("_", ".")):
                continue
            target = path / "__init__.py" if path.is_dir() else path
            if not target.is_file() or target.suffix != ".py":
                continue
            digest = hashlib.sha1(str(path.resolve()).encode()).hexdigest()[:8]
            module_name = f"reelay_ext_{digest}_{path.stem}"
            if module_name in sys.modules:
                active.add(module_name)
                continue
            try:
                spec = importlib.util.spec_from_file_location(
                    module_name, target,
                    submodule_search_locations=[str(path)] if path.is_dir() else None)
                module = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = module
                _EXTERNAL_MODULES.add(module_name)
                spec.loader.exec_module(module)
                active.add(module_name)
            except Exception as exc:  # noqa: BLE001 - 外部扩展失败隔离
                sys.modules.pop(module_name, None)
                log.exception("外部扩展加载失败：%s", path)
                errors.append({"source": str(path), "error": f"{type(exc).__name__}: {exc}"[:300]})
    try:
        from importlib.metadata import entry_points
        points = entry_points(group="reelay.plugins")
    except Exception:  # noqa: BLE001
        points = []
    for point in points:
        try:
            _EXTERNAL_MODULES.add(point.module)
            point.load()
            active.add(point.module)
        except Exception as exc:  # noqa: BLE001
            log.exception("entry point 扩展加载失败：%s", point.name)
            errors.append({"source": f"entry_point:{point.name}", "error": f"{type(exc).__name__}: {exc}"[:300]})
    return active


def discover(packages: tuple[str, ...] = ("reelay.providers", "reelay.plugins")) -> Registry:
    """导入内置包与外部扩展，触发 ``@register``，再统一实例化。"""
    for pkg_name in packages:
        try:
            pkg = importlib.import_module(pkg_name)
        except ModuleNotFoundError:
            continue
        for mod in pkgutil.iter_modules(pkg.__path__):
            if mod.name.startswith("_"):
                continue
            importlib.import_module(f"{pkg_name}.{mod.name}")
    errors: list[dict[str, str]] = []
    active = _load_external(errors)
    registry = Registry()
    _instantiate(registry, errors, active)
    registry.load_errors = errors
    return registry
