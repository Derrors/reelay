"""来源过滤的结构化规则：按识别与 TMDB 补全后的字段确定性求值。

``filters`` 除关键词（must_have / block_keywords，解析前即可判断）外还支持：

- ``allow``：条件列表，命中任一条才收（列表为空表示不限）；
- ``deny``：条件列表，命中任一条就不收；
- ``min_resolution``：最低清晰度（480p / 720p / 1080p / 2160p）；清晰度未知时转人工确认。

条件与二级分类的写法一致：同一条件内各字段为 AND，单字段逗号分隔的多个值为 OR。
自然语言偏好由 AI 编译成这些规则，用户确认后写入配置；运行时只按规则判断。
"""
from __future__ import annotations

from typing import Any

from .category_strategy import _csv
from .media import resolution_rank

CONDITION_FIELDS = ("media_type", "genre_ids", "origin_country")
MEDIA_TYPES = {"movie", "tv", "anime"}
MIN_RESOLUTIONS = {"480p": 1, "720p": 2, "1080p": 3, "2160p": 4}


def validate_condition(cond: Any, label: str) -> None:
    if not isinstance(cond, dict) or not cond or set(cond) - set(CONDITION_FIELDS):
        raise ValueError(f"{label} 必须是只含 {'/'.join(CONDITION_FIELDS)} 的非空对象")
    for field, value in cond.items():
        if not isinstance(value, str) or not _csv(value):
            raise ValueError(f"{label}.{field} 必须是逗号分隔的非空字符串")
        values = _csv(value)
        if field == "media_type" and values - {v.upper() for v in MEDIA_TYPES}:
            raise ValueError(f"{label}.media_type 只能是 movie、tv、anime")
        if field == "genre_ids" and any(not item.isdecimal() for item in values):
            raise ValueError(f"{label}.genre_ids 必须是数字 ID")
        if field == "origin_country" and any(len(item) != 2 or not item.isalpha() for item in values):
            raise ValueError(f"{label}.origin_country 必须是两位国家/地区代码")


def validate_filters(filters: Any, label: str) -> None:
    if not isinstance(filters, dict):
        raise ValueError(f"{label} 必须是对象")
    unknown = set(filters) - {"must_have", "block_keywords", "allow", "deny", "min_resolution"}
    if unknown:
        raise ValueError(f"{label} 包含未知字段：{'、'.join(sorted(unknown))}")
    for key in ("must_have", "block_keywords"):
        values = filters.get(key, [])
        if type(values) is not list or any(type(v) is not str or not v for v in values):
            raise ValueError(f"{label}.{key} 必须是非空字符串数组")
    for key in ("allow", "deny"):
        conds = filters.get(key, [])
        if type(conds) is not list or len(conds) > 50:
            raise ValueError(f"{label}.{key} 必须是不超过 50 条的条件数组")
        for index, cond in enumerate(conds):
            validate_condition(cond, f"{label}.{key}[{index}]")
    minimum = filters.get("min_resolution")
    if minimum is not None and minimum not in MIN_RESOLUTIONS:
        raise ValueError(f"{label}.min_resolution 必须是 {'、'.join(MIN_RESOLUTIONS)} 之一")


def condition_matches(cond: dict, parsed) -> bool:
    for field, value in cond.items():
        wanted = _csv(value)
        if field == "media_type":
            actual = {str(parsed.media_type or "").upper()}
        elif field == "genre_ids":
            actual = {str(item) for item in parsed.genre_ids}
        else:
            actual = {str(item).upper() for item in parsed.origin_country}
        if not actual & wanted:
            return False
    return True


def check(filters: dict, parsed) -> tuple[str, str] | None:
    """返回 (动作, 原因)：动作为 skip（不收）或 ask（无法判断，转人工）；全部通过返回 None。"""
    allow = filters.get("allow") or []
    if allow and not any(condition_matches(cond, parsed) for cond in allow):
        return "skip", "不在收藏范围（allow）"
    for cond in filters.get("deny") or []:
        if condition_matches(cond, parsed):
            return "skip", "命中排除规则（deny）"
    minimum = filters.get("min_resolution")
    if minimum:
        rank = resolution_rank(parsed.resolution)
        if not rank:
            return "ask", f"清晰度未知，无法确认是否达到 {minimum}"
        if rank < MIN_RESOLUTIONS[minimum]:
            return "skip", f"清晰度低于 {minimum}"
    return None
