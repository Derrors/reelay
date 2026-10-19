"""业务配置：深合并默认值、严格校验，错误配置不会静默切到默认目录。"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
from pathlib import Path
from typing import Any

# 目录名带 [tmdbid=xxx]（Jellyfin/Emby 均可识别）：匹配到 TMDB 时刮削不再依赖标题猜测。
DEFAULT_NAMING = {
    "movie": "{{ title }}{% if year %} ({{ year }}){% endif %}{% if tmdb_id %} [tmdbid={{ tmdb_id }}]{% endif %}/"
             "{{ title }}{% if year %} ({{ year }}){% endif %}{% if resolution %} - {{ resolution }}{% endif %}.{{ ext }}",
    "tv": "{{ title }}{% if year %} ({{ year }}){% endif %}{% if tmdb_id %} [tmdbid={{ tmdb_id }}]{% endif %}/"
          "Season {{ season|pad2 }}/{{ title }} - S{{ season|pad2 }}E{{ episode|pad2 }}.{{ ext }}",
}
CONFLICT_STRATEGIES = {"manual", "skip", "suffix", "upgrade"}

_DEFAULT_CONFIG: dict[str, Any] = {
    "save_dirs": {"电影": "0", "剧集": "0", "动漫": "0", "默认": "0"},
    "forward": {"enabled": False, "target": "", "template": "{title} ({year}) {resolution}\n{link}"},
    "organize": {"naming": dict(DEFAULT_NAMING), "conflict": "manual"},
    "dir_router": {},
    "providers": {"source": "telegram", "notify": ["console"]},
    "plugins": {},
    "defaults": {"approval_mode": "manual", "parser": "llm", "decision": "subscription",
                 "storage": "p115", "save_to": "默认", "confidence_threshold": 0.5,
                 "llm_response_format": "json_schema", "episodes_from": "share"},
    "sources": [],
    "routing": [],
    "approval": {"timeout_hours": 24, "on_timeout": "remind", "reminder": "digest", "max_reminders": 3},
    "schedule": {"daily_hour": 9},
    # AI 能力：人工裁决回流、转人工前的方案兜底、收件箱建议与自动审批建议
    "ai": {"examples": 3, "history": 20, "assist": True, "advice": True,
           "suggest_auto": {"min_decisions": 20, "approve_rate": 0.9, "max_correct_rate": 0.1}},
    # 同一集多个版本时的偏好（收件箱推荐版本与洗版共用），顺序即优先级
    "release": {"prefer_subtitles": ["chs", "cht"], "prefer_audio": [], "hdr": "any", "prefer_sources": []},
}


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
    cfg = merge_config(_DEFAULT_CONFIG, data)
    # 目录列表是完整映射；不偷偷补回已删除的 CID=0 占位目录。
    if "save_dirs" in data:
        cfg["save_dirs"] = copy.deepcopy(data["save_dirs"])
    for key in ("save_dirs", "defaults", "forward", "providers", "plugins", "organize", "dir_router",
                "approval", "schedule", "ai", "release"):
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
    for key in ("sources", "routing"):
        if not isinstance(cfg[key], list):
            raise ValueError(f"{key} 必须是数组")
    if any(type(key) is not str or not key for key in cfg["plugins"]) or any(type(enabled) is not bool for enabled in cfg["plugins"].values()):
        raise ValueError("plugins 的值必须是布尔开关")
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
    from .category_strategy import validate_strategy
    categories = cfg["dir_router"].get("categories")
    if categories is None:
        from .category_strategy import DEFAULT_CATEGORY_STRATEGY
        cfg["dir_router"]["categories"] = copy.deepcopy(DEFAULT_CATEGORY_STRATEGY)
        categories = cfg["dir_router"]["categories"]
    validate_strategy(categories)
    for rule in cfg["routing"]:  # 旧配置只读兼容
        if not isinstance(rule, dict):
            raise ValueError("routing 规则必须是对象")
        if "default" in rule:
            target = rule["default"]
        else:
            target, when = rule.get("dir"), rule.get("when")
            if not isinstance(when, str) or not when.strip():
                raise ValueError("routing 规则缺少 when")
            for clause in when.split(" and "):
                if not re.fullmatch(r"\s*(type|media_type|title|year|season|resolution|genre|genres)\s*(==| contains )\s*.+", clause):
                    raise ValueError(f"routing 条件无效：{when}")
        if not isinstance(target, str) or target not in dirs:
            raise ValueError("routing 引用了不存在的目录别名")
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
    if cfg["organize"].get("conflict") not in CONFLICT_STRATEGIES:
        raise ValueError("organize.conflict 必须为 manual、skip、suffix 或 upgrade")
    naming = cfg["organize"].get("naming")
    if not isinstance(naming, dict) or any(type(v) is not str or not v for v in naming.values()):
        raise ValueError("organize.naming 必须包含非空模板")
    from .config_fields import validate_naming
    from .naming_template import migrate_legacy_template
    for kind, template in list(naming.items()):
        naming[kind] = migrate_legacy_template(template)
    validate_naming(naming)
    try:
        json.dumps(cfg, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("配置必须使用 JSON 兼容的键和值") from exc
    return cfg


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


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    p = Path(path or os.getenv("REELAY_CONFIG_PATH", "config.yaml"))
    if not p.exists():
        return normalize_config({})
    text = p.read_text(encoding="utf-8")
    try:
        import yaml
        data = yaml.safe_load(text)
    except ImportError:
        data = json.loads(text)
    except Exception as exc:
        raise ValueError("配置文件格式无效") from exc
    return normalize_config({} if data is None else data)
