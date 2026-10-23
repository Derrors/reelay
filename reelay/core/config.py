"""业务配置：深合并默认值、严格校验，错误配置不会静默切到默认目录。

配置文件（固定为 data/config.yaml）分两部分::

    system:   # 系统配置：保存目录、默认规则、来源、订阅、AI 服务、TMDB、Telegram、115 限流……
    plugins:  # 插件配置：plugins.<插件名> 下是 enabled 开关与该插件自己的设置

运行时内部表示把 ``system`` 平铺到顶层（``cfg["defaults"]`` 等），插件配置在
``cfg["plugins"][名称]``；只在读写文件时套上或拆掉 ``system``（见 ``from_file`` / ``to_file``）。
内置插件的默认设置与校验在插件目录的 ``settings.py``。密钥不在这里，见 ``core/secret_store.py``。
"""
from __future__ import annotations

import copy
import functools
import hashlib
import json
import math
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from . import paths

LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")
# 推理强度（OpenAI 兼容接口的 reasoning_effort）；空字符串表示不传，由模型或网关决定。
# 各家支持的取值不同（如 OpenAI 有 minimal / medium，DeepSeek 只认 low / high / xhigh / max），
# 管理台会检测当前模型支持哪些；请求被拒时去掉这个参数重发，见 ai.request
REASONING_EFFORTS = ("", "none", "minimal", "low", "medium", "high", "xhigh", "max")
DEFAULT_LLM_MODEL = "gpt-4o-mini"

_DEFAULT_CONFIG: dict[str, Any] = {
    "save_dirs": {"电影": "0", "剧集": "0", "动漫": "0", "默认": "0"},
    "forward": {"enabled": False, "target": "", "template": "{title} ({year}) {resolution}\n{link}"},
    "providers": {"source": "telegram", "notify": ["console"]},
    "defaults": {"approval_mode": "manual", "parser": "llm", "decision": "subscription",
                 "storage": "p115", "save_to": "默认", "confidence_threshold": 0.5,
                 "llm_response_format": "json_schema", "episodes_from": "share"},
    "sources": [],
    # 剧集订阅：订阅放行与缺集追更共用（每项 title/season/total/tmdbid/auto，见 core/subscriptions.py）
    "subscriptions": [],
    "approval": {"timeout_hours": 24, "on_timeout": "remind", "reminder": "digest", "max_reminders": 3},
    "schedule": {"daily_hour": 9},
    # AI 能力：人工裁决回流、转人工前的方案兜底、收件箱建议与自动审批建议
    "ai": {"examples": 3, "history": 20, "assist": True, "advice": True,
           "suggest_auto": {"min_decisions": 20, "approve_rate": 0.9, "max_correct_rate": 0.1}},
    # 同一集多个版本时的偏好（收件箱推荐版本与洗版共用），顺序即优先级
    "release": {"prefer_subtitles": ["chs", "cht"], "prefer_audio": [], "hdr": "any", "prefer_sources": []},
    # OpenAI 兼容接口；密钥在 secrets.json；reasoning_effort 留空不传
    "llm": {"base_url": "", "model": DEFAULT_LLM_MODEL, "reasoning_effort": ""},
    # TMDB：识别后的元数据补全、订阅找剧与缺集追更共用；base_url 留空用官方地址
    "tmdb": {"base_url": "", "language": "zh-CN", "timeout": 10},
    # Telegram：控制 Bot 只服务 owner_id；重连后按来源补拉停机期间的消息；断开多久告警
    "telegram": {"owner_id": "", "backfill_limit": 200, "backfill_max_days": 7, "alert_after_sec": 120},
    # 主进程：并发转存数（重启生效）与日志级别
    "runtime": {"concurrency": 3, "log_level": "INFO"},
    # 115 请求治理：各类接口的每秒请求数，触发风控后全部接口冷却的秒数
    "p115": {"open_qps": 3, "cookie_qps": 2, "share_qps": 1, "risk_cooldown_sec": 3600},
    "plugins": {},
}
# 系统配置的键（写文件时放进 system）
SYSTEM_KEYS = tuple(key for key in _DEFAULT_CONFIG if key != "plugins")


def from_file(data: dict) -> dict:
    """文件结构 → 内部表示：system 平铺到顶层。已经是内部表示（没有 system）时原样返回副本。"""
    data = copy.deepcopy(data)
    if "system" in data:
        system = data.pop("system")
        if not isinstance(system, dict):
            raise ValueError("system 必须是对象")
        conflict = (set(system) & set(data)) - {"plugins"}
        if conflict:
            raise ValueError(f"{'、'.join(sorted(conflict))} 不能同时出现在 system 内外")
        data = {**system, **data}
    if not isinstance(data.get("plugins", {}), dict):
        raise ValueError("plugins 必须是对象")
    return data


def to_file(cfg: dict) -> dict:
    """内部表示 → 文件结构。"""
    return {"system": {key: copy.deepcopy(cfg[key]) for key in SYSTEM_KEYS if key in cfg},
            "plugins": copy.deepcopy(cfg.get("plugins", {}))}


FILE_HEADER = (
    "# Reelay 配置，由管理台「设置」与「插件」页维护，也可以直接编辑（保存后自动生效）。\n"
    "# system：系统配置；plugins.<插件名>：插件开关（enabled）与插件设置。\n"
    "# 密钥（AI / TMDB key、Telegram、控制 Bot、Web 密码）不在这里，在管理台「设置」中填写。\n"
)


def render(cfg: dict) -> str:
    import yaml
    return FILE_HEADER + yaml.safe_dump(to_file(cfg), allow_unicode=True, sort_keys=False)


def write_config(path: str | Path, cfg: dict) -> None:
    """原子写入配置文件（先写临时文件再替换），进程中断不会留下半个文件。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    temporary = Path(name)
    try:
        os.chmod(temporary, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            output.write(render(cfg))
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


def ensure_config(path: str | Path | None = None) -> None:
    """首次启动时写出默认配置；已存在时不动。各入口程序启动时调用（主进程、Web、MCP、登录与评测工具）。"""
    target = Path(path or paths.CONFIG)
    if not target.exists():
        write_config(target, normalize_config({}))


def load_env(path: str = ".env") -> None:
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def merge_config(base: dict, override: dict) -> dict:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge_config(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def normalize_config(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError("配置根节点必须是对象")
    if any(type(key) is not str for key in data):
        raise ValueError("配置根节点的键必须是字符串")
    data = from_file(data)
    unknown = set(data) - set(SYSTEM_KEYS) - {"plugins"}
    if unknown:
        raise ValueError(f"无法识别的配置项：{'、'.join(sorted(unknown))}（插件的设置放在 plugins.<插件名> 下）")
    cfg = merge_config(_DEFAULT_CONFIG, data)
    # 目录列表是完整映射；不偷偷补回已删除的 CID=0 占位目录。
    if "save_dirs" in data:
        cfg["save_dirs"] = copy.deepcopy(data["save_dirs"])
    for key in ("save_dirs", "defaults", "forward", "providers", "plugins", "approval", "schedule", "ai", "release",
                "llm", "tmdb", "telegram", "runtime", "p115"):
        if not isinstance(cfg[key], dict):
            raise ValueError(f"{key} 必须是对象")
    dirs = cfg["save_dirs"]
    if not dirs or any(type(k) is not str or not k.strip() or type(v) not in (str, int)
                       or not str(v).isdecimal() for k, v in dirs.items()):
        raise ValueError("save_dirs 必须包含非空别名与非负整数 CID")
    if len({k.strip() for k in dirs}) != len(dirs) or any(k != k.strip() for k in dirs):
        raise ValueError("save_dirs 目录别名不能重复或带首尾空格")
    if any(not re.fullmatch(r'[0-9]+', str(cid)) for cid in dirs.values()):
        raise ValueError("save_dirs CID 必须是十进制非负整数字符串")
    cfg["save_dirs"] = {alias: str(cid) for alias, cid in dirs.items()}
    for key in ("sources", "subscriptions"):
        if not isinstance(cfg[key], list):
            raise ValueError(f"{key} 必须是数组")
    _normalize_plugins(cfg["plugins"])
    prov = cfg["providers"]
    if not isinstance(prov.get("source"), str) or not prov["source"]:
        raise ValueError("providers.source 必须是 Provider 名称")
    if type(prov.get("notify")) is not list or any(type(v) is not str or not v for v in prov["notify"]):
        raise ValueError("providers.notify 必须是 Provider 名称数组")
    if len(set(prov["notify"])) != len(prov["notify"]):
        raise ValueError("providers.notify 不能重复")
    _validate_forward(cfg["forward"])
    _validate_rule(cfg["defaults"], dirs, "defaults")
    bots = set()
    for i, source in enumerate(cfg["sources"]):
        label = f"sources[{i}]"
        if not isinstance(source, dict) or type(source.get("bot")) is not str or not source["bot"].strip():
            raise ValueError(f"{label} 缺少 bot")
        bot = source["bot"].lower()
        if bot in bots:
            raise ValueError(f"{label} bot 重复")
        bots.add(bot)
        _validate_rule(merge_config(cfg["defaults"], source), dirs, label)
        if "forward" in source:
            if not isinstance(source["forward"], dict):
                raise ValueError(f"{label}.forward 必须是对象")
            _validate_forward(merge_config(cfg["forward"], source["forward"]))
    approval = cfg["approval"]
    hours = approval.get("timeout_hours")
    if type(hours) not in (int, float) or not math.isfinite(hours) or hours <= 0:
        raise ValueError("approval.timeout_hours 必须大于零")
    if type(approval.get("on_timeout")) is not str or approval["on_timeout"] not in {"remind", "skip"}:
        raise ValueError("approval.on_timeout 必须为 remind 或 skip")
    if approval.get("reminder") not in {"digest", "card"}:
        raise ValueError("approval.reminder 必须为 digest（汇总）或 card（逐条卡片）")
    if type(approval.get("max_reminders")) is not int or not 0 <= approval["max_reminders"] <= 100:
        raise ValueError("approval.max_reminders 必须是 0 到 100 的整数（0 表示不限）")
    hour = cfg["schedule"].get("daily_hour")
    if type(hour) is not int or not 0 <= hour <= 23:
        raise ValueError("schedule.daily_hour 必须是 0 到 23 的整数")
    _validate_ai(cfg["ai"])
    from .release import validate_preferences
    validate_preferences(cfg["release"])
    from .subscriptions import validate as validate_subscriptions
    validate_subscriptions(cfg)
    _validate_services(cfg)
    try:
        json.dumps(cfg, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("配置必须使用 JSON 兼容的键和值") from exc
    return cfg


def _normalize_plugins(plugins: dict) -> None:
    """plugins.<名> 是对象，enabled 可省略（按插件声明的默认状态）；内置插件按各自的 settings.py 补齐默认设置并校验。"""
    for name, section in plugins.items():
        if type(name) is not str or not name:
            raise ValueError("plugins 的键必须是插件名称")
        if not isinstance(section, dict):
            raise ValueError(f"plugins.{name} 必须是对象")
        if "enabled" in section and type(section["enabled"]) is not bool:
            raise ValueError(f"plugins.{name}.enabled 必须是布尔值")
    for name, settings in builtin_settings().items():
        plugins[name] = merge_config(settings.DEFAULTS, plugins.get(name, {}))
        settings.normalize(plugins[name])


@functools.cache
def builtin_settings() -> dict[str, Any]:
    """内置插件自己的设置模块 ``reelay/plugins/<名>/settings.py``（目录名即插件名）。

    提供 ``DEFAULTS`` 与 ``normalize(section)``。
    读文件、管理台校验时还没有注册表，所以按目录查找；没有设置模块的插件自己在代码里给默认值。
    """
    import importlib
    from .. import plugins
    root = Path(plugins.__file__).parent
    return {d.name: importlib.import_module(f"{plugins.__name__}.{d.name}.settings")
            for d in sorted(root.iterdir()) if (d / "settings.py").is_file()}


def _number(section: dict, key: str, label: str, low: float, high: float, *, integer: bool = False) -> None:
    value = section.get(key)
    kinds = (int,) if integer else (int, float)
    if type(value) not in kinds or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{label}.{key} 必须是 {low:g} 到 {high:g} 之间的{'整数' if integer else '数'}")


def _validate_services(cfg: dict) -> None:
    """AI 服务、TMDB、Telegram、主进程与 115 的设置（密钥另存，不在这里）。"""
    for section, keys in (("llm", ("base_url", "model")), ("tmdb", ("base_url", "language"))):
        for key in keys:
            if type(cfg[section].get(key)) is not str:
                raise ValueError(f"{section}.{key} 必须是字符串")
        url = cfg[section]["base_url"].strip()
        if url and not re.match(r"https?://[^/\s]+", url):
            raise ValueError(f"{section}.base_url 必须是 http(s):// 开头的地址，留空使用默认")
        cfg[section]["base_url"] = url.rstrip("/")
    if not cfg["llm"]["model"].strip():
        raise ValueError("llm.model 不能为空")
    if cfg["llm"].get("reasoning_effort") not in REASONING_EFFORTS:
        raise ValueError(f"llm.reasoning_effort 必须为 {'、'.join(e for e in REASONING_EFFORTS if e)} 之一，留空表示不传")
    _number(cfg["tmdb"], "timeout", "tmdb", 1, 60)
    telegram = cfg["telegram"]
    owner = telegram.get("owner_id")
    if type(owner) is int and owner > 0:
        telegram["owner_id"] = str(owner)
    elif type(owner) is not str or (owner.strip() and not owner.strip().isdecimal()):
        raise ValueError("telegram.owner_id 必须是数字 ID，留空表示不启用控制 Bot")
    telegram["owner_id"] = telegram["owner_id"].strip()
    _number(telegram, "backfill_limit", "telegram", 0, 5000, integer=True)
    _number(telegram, "backfill_max_days", "telegram", 0, 365)
    _number(telegram, "alert_after_sec", "telegram", 10, 86400, integer=True)
    _number(cfg["runtime"], "concurrency", "runtime", 1, 20, integer=True)
    if cfg["runtime"].get("log_level") not in LOG_LEVELS:
        raise ValueError(f"runtime.log_level 必须为 {'、'.join(LOG_LEVELS)} 之一")
    for key in ("open_qps", "cookie_qps", "share_qps"):
        _number(cfg["p115"], key, "p115", 0.1, 20)
    _number(cfg["p115"], "risk_cooldown_sec", "p115", 60, 86400, integer=True)


def _validate_rule(rule: dict, dirs: dict, label: str) -> None:
    if type(rule.get("approval_mode")) is not str or rule["approval_mode"] not in {"manual", "auto", "default_pass"}:
        raise ValueError(f"{label}.approval_mode 无效")
    if type(rule.get("save_to")) is not str or rule["save_to"] not in dirs:
        raise ValueError(f"{label}.save_to 引用了不存在的目录")
    for key in ("parser", "decision", "storage"):
        if type(rule.get(key)) is not str or not rule[key]:
            raise ValueError(f"{label}.{key} 必须是 Provider 名称")
    fmt = rule.get("llm_response_format")
    if type(fmt) is not str or fmt not in {"json_schema", "json_object"}:
        raise ValueError(f"{label}.llm_response_format 必须为 json_schema 或 json_object")
    if rule.get("episodes_from") not in {"share", "message"}:
        raise ValueError(f"{label}.episodes_from 必须为 share（以分享文件为准）或 message（只转存消息声明的集）")
    threshold = rule.get("confidence_threshold")
    if type(threshold) not in (int, float) or not math.isfinite(threshold) or not 0 <= threshold <= 1:
        raise ValueError(f"{label}.confidence_threshold 必须在 0 到 1 之间")
    if type(rule.get("preferences", "")) is not str or len(rule.get("preferences", "")) > 10000:
        raise ValueError(f"{label}.preferences 必须是长度不超过 10000 的字符串")
    from .rules import validate_filters
    validate_filters(rule.get("filters", {}), f"{label}.filters")


def _validate_ai(section: dict) -> None:
    for key, low, high in (("examples", 0, 10), ("history", 0, 100)):
        if type(section.get(key)) is not int or not low <= section[key] <= high:
            raise ValueError(f"ai.{key} 必须是 {low} 到 {high} 的整数")
    for key in ("assist", "advice"):
        if type(section.get(key)) is not bool:
            raise ValueError(f"ai.{key} 必须是布尔值")
    suggest = section.get("suggest_auto")
    if not isinstance(suggest, dict):
        raise ValueError("ai.suggest_auto 必须是对象")
    if type(suggest.get("min_decisions")) is not int or not 5 <= suggest["min_decisions"] <= 500:
        raise ValueError("ai.suggest_auto.min_decisions 必须是 5 到 500 的整数")
    for key, low in (("approve_rate", 0.5), ("max_correct_rate", 0.0)):
        value = suggest.get(key)
        if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= 1:
            raise ValueError(f"ai.suggest_auto.{key} 必须在 {low:g} 到 1 之间")


def _validate_forward(fc: dict) -> None:
    if type(fc.get("enabled")) is not bool or type(fc.get("target")) is not str or type(fc.get("template")) is not str:
        raise ValueError("forward 的 enabled/target/template 类型无效")
    if fc["enabled"] and (not fc["target"].strip() or not fc["template"].strip()):
        raise ValueError("开启转发时必须提供 target 与 template")
    from string import Formatter
    try:
        for _, field, spec, conversion in Formatter().parse(fc["template"]):
            if field is not None and (field not in {"title", "year", "resolution", "size", "link"} or spec or conversion):
                raise ValueError("forward.template 包含不支持的字段")
    except ValueError as exc:
        raise ValueError("forward.template 格式无效") from exc


def config_revision(cfg: dict) -> str:
    return hashlib.sha256(json.dumps(cfg, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12]


def read_file(path: str | Path) -> Any:
    """读取配置文件原样的数据（不转换、不校验）；文件不存在返回 None。"""
    p = Path(path)
    if not p.exists():
        return None
    import yaml
    try:
        return yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError("配置文件格式无效") from exc


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    data = read_file(path or paths.CONFIG)
    return normalize_config({} if data is None else data)
