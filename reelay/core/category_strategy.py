"""MoviePilot 风格二级分类策略：媒体类型 → 分类路径 → 条件。

同一分类中的条件为 AND；单字段逗号分隔值为 OR；空条件分类是该媒体类型的兜底。
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

ALLOWED_MEDIA = {"movie", "tv"}
ALLOWED_FIELDS = {"genre_ids", "origin_country"}

DEFAULT_CATEGORY_STRATEGY: dict[str, dict[str, dict[str, str]]] = {
    "movie": {
        "动漫/动画电影": {"genre_ids": "16"},
        "电影/港台电影": {"origin_country": "TW,HK"},
        "电影/国产电影": {"origin_country": "CN"},
        "电影/日韩电影": {"origin_country": "JP,KP,KR"},
        "电影/欧美电影": {},
    },
    "tv": {
        "动漫/国产动漫": {"genre_ids": "16", "origin_country": "CN,TW,HK"},
        "动漫/欧美动漫": {"genre_ids": "16", "origin_country": "US,FR,GB,DE,ES,IT,NL,PT,RU,UK"},
        "动漫/日本番剧": {"genre_ids": "16", "origin_country": "JP"},
        "其他/纪录影片": {"genre_ids": "99"},
        "其他/综艺节目": {"genre_ids": "10764,10767"},
        "剧集/港台剧集": {"origin_country": "TW,HK"},
        "剧集/国产剧集": {"origin_country": "CN"},
        "剧集/日韩剧集": {"origin_country": "JP,KP,KR"},
        "剧集/南亚剧集": {"origin_country": "TH,IN,SG"},
        "剧集/欧美剧集": {"origin_country": "US,FR,GB,DE,ES,IT,NL,PT,RU,UK"},
    },
}


def _csv(value: str) -> set[str]:
    return {item.strip().upper() for item in value.split(",") if item.strip()}


def validate_strategy(strategy: Any) -> None:
    if not isinstance(strategy, Mapping) or set(strategy) - ALLOWED_MEDIA:
        raise ValueError("plugins.dir_router.categories 仅允许 movie 和 tv 两个媒体类型")
    for media_type, categories in strategy.items():
        if not isinstance(categories, Mapping) or not categories:
            raise ValueError(f"{media_type} 分类必须是非空对象")
        fallback_seen = False
        for index, (path, conditions) in enumerate(categories.items()):
            if not isinstance(path, str) or not path.strip() or path != path.strip() or path.startswith("/"):
                raise ValueError(f"{media_type} 分类路径无效")
            parts = path.split("/")
            if len(parts) != 2 or any(not p or p in (".", "..") for p in parts):
                raise ValueError(f"{media_type}.{path} 必须是“一级/二级”路径")
            if not isinstance(conditions, Mapping) or set(conditions) - ALLOWED_FIELDS:
                raise ValueError(f"{media_type}.{path} 只支持 genre_ids/origin_country")
            if not conditions:
                if fallback_seen or index != len(categories) - 1:
                    raise ValueError(f"{media_type} 兜底分类必须唯一且放在最后")
                fallback_seen = True
            for field, value in conditions.items():
                if not isinstance(value, str) or not _csv(value):
                    raise ValueError(f"{media_type}.{path}.{field} 必须是逗号分隔的非空字符串")
                if field == "genre_ids" and any(not item.isdecimal() for item in _csv(value)):
                    raise ValueError(f"{media_type}.{path}.genre_ids 必须是数字ID")
                if field == "origin_country" and any(len(item) != 2 or not item.isalpha() for item in _csv(value)):
                    raise ValueError(f"{media_type}.{path}.origin_country 必须是两位国家/地区代码")


def classify(strategy: Mapping[str, Any], media_type: str, genre_ids: list[int],
             origin_country: list[str]) -> str | None:
    media = "tv" if media_type in ("tv", "anime") else media_type
    categories = strategy.get(media, {})
    actual_genres = {str(item) for item in genre_ids}
    actual_countries = {item.upper() for item in origin_country}
    fallback = None
    for path, conditions in categories.items():
        if not conditions:
            fallback = path
            continue
        if "genre_ids" in conditions and not (actual_genres & _csv(conditions["genre_ids"])):
            continue
        if "origin_country" in conditions and not (actual_countries & _csv(conditions["origin_country"])):
            continue
        return path
    return fallback
