"""媒体库整理的目录方式（``plugins.organizer.layout``）：整理时把资源放进哪个文件夹。

- ``category``：MoviePilot 风格二级分类，规则在同目录的 ``category.yaml``（不可在管理台修改）：
  媒体类型 → 分类路径 → 条件。同一分类中的条件为 AND；单字段逗号分隔值为 OR；按顺序取第一个命中的分类，
  没有条件的分类遇到即命中（放在最后就是兜底）。
- ``media_type``：按媒体类型放进一层文件夹（电影 / 剧集 / 动漫）。
- ``none``：不分类，直接放在保存目录下。
"""
from __future__ import annotations

import functools
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ...core.rules import COUNTRIES, GENRES, _csv

CATEGORY_FILE = Path(__file__).with_name("category.yaml")

LAYOUTS = ("category", "media_type", "none")
# 按媒体类型分目录时的文件夹名；anime 留空表示动漫不单独分，按电影 / 剧集归入
DEFAULT_TYPE_DIRS = {"movie": "电影", "tv": "剧集", "anime": "动漫"}
ANIMATION_GENRE = "16"

ALLOWED_MEDIA = {"movie", "tv"}
# 识别结果里有的字段；MoviePilot 的 original_language / production_countries 这里没有数据
ALLOWED_FIELDS = {"genre_ids", "origin_country", "release_year"}
FIELD_LABELS = {"genre_ids": "题材", "origin_country": "地区", "release_year": "年份"}



def _expand(value: str) -> set[str]:
    """条件值：逗号分隔为「或」；``2000-2010`` 这样前后都是数字的写法展开为范围（同 MoviePilot）。"""
    values = set()
    for item in _csv(value):
        start, dash, stop = item.partition("-")
        if dash and start.isdecimal() and stop.isdecimal() and int(start) <= int(stop):
            values.update(str(year) for year in range(int(start), int(stop) + 1))
        else:
            values.add(item)
    return values


def validate_strategy(strategy: Any) -> None:
    """分类文件的结构：movie / tv → 「一级/二级」路径 → 条件（没有条件的分类接住其余作品）。"""
    if not isinstance(strategy, Mapping) or not strategy or set(strategy) - ALLOWED_MEDIA:
        raise ValueError("分类规则只能有 movie 和 tv 两个媒体类型")
    for media_type, categories in strategy.items():
        if not isinstance(categories, Mapping) or not categories:
            raise ValueError(f"{media_type} 分类必须是非空对象")
        for path, conditions in categories.items():
            parts = path.split("/") if isinstance(path, str) else []
            if len(parts) != 2 or any(not p or p != p.strip() or p in (".", "..") for p in parts):
                raise ValueError(f"{media_type}.{path} 必须是「一级/二级」路径")
            if conditions is None:
                continue
            if not isinstance(conditions, Mapping) or set(conditions) - ALLOWED_FIELDS:
                raise ValueError(f"{media_type}.{path} 只支持 {'、'.join(sorted(ALLOWED_FIELDS))}")
            for field, value in conditions.items():
                if type(value) not in (str, int) or not _expand(str(value)):
                    raise ValueError(f"{media_type}.{path}.{field} 必须是逗号分隔的非空值")


@functools.cache
def categories() -> dict[str, dict[str, dict[str, str]]]:
    """二级分类规则：随插件发布的 category.yaml，进程内只读一次。"""
    return load_strategy(CATEGORY_FILE)


def load_strategy(path: str | Path) -> dict[str, dict[str, dict[str, str]]]:
    """读取分类文件（YAML，可带注释）；没有条件的分类读成空对象。"""
    import yaml
    with Path(path).open(encoding="utf-8") as f:
        strategy = yaml.safe_load(f)
    validate_strategy(strategy)
    return {media: {cat: {k: str(v) for k, v in (cond or {}).items()} for cat, cond in items.items()}
            for media, items in strategy.items()}


def classify(strategy: Mapping[str, Any], media_type: str, genre_ids: list[int],
             origin_country: list[str], year: int | None = None) -> str | None:
    """与 MoviePilot 相同：按顺序取第一个满足全部条件的分类，没有条件的分类遇到即命中；都不满足返回 None。

    动漫按 tv 匹配（TMDB 只有电影与剧集两种类型，动漫、纪录片、综艺都靠题材区分）。
    作品缺少某个条件要用的信息（如没有地区）时，该分类不命中。
    """
    media = "tv" if media_type in ("tv", "anime") else media_type
    info = {"genre_ids": {str(item) for item in genre_ids or []},
            "origin_country": {str(item).upper() for item in origin_country or []},
            "release_year": {str(year)} if year else set()}
    for path, conditions in (strategy.get(media) or {}).items():
        if all(info[field] & _expand(str(value)) for field, value in (conditions or {}).items()):
            return path
    return None


def describe(strategy: Mapping[str, Any]) -> list[dict[str, Any]]:
    """分类规则的展示形式：每个媒体类型下按顺序的 {path, conditions: [{field, label, values: [{code, name}]}]}。"""
    def name(field: str, code: str) -> str:
        if field == "genre_ids":
            return GENRES.get(int(code), code) if code.isdecimal() else code
        return COUNTRIES.get(code, code) if field == "origin_country" else code
    def values(field: str, value: str) -> list[dict[str, str]]:
        # 同名的代码合并成一项（如 GB 与 UK 都是英国），原始代码留在 code 里
        merged: dict[str, list[str]] = {}
        for code in str(value).replace(" ", "").upper().split(","):
            if code:
                merged.setdefault(name(field, code), []).append(code)
        return [{"code": ",".join(codes), "name": label} for label, codes in merged.items()]
    return [{"media": media, "categories": [
        {"path": path, "conditions": [
            {"field": field, "label": FIELD_LABELS[field], "values": values(field, value)}
            for field, value in (conditions or {}).items()]}
        for path, conditions in (strategy.get(media) or {}).items()]}
        for media in ("movie", "tv") if media in strategy]


def validate_type_dirs(dirs: Any) -> None:
    if not isinstance(dirs, Mapping) or set(dirs) != set(DEFAULT_TYPE_DIRS):
        raise ValueError("plugins.organizer.type_dirs 必须包含 movie、tv、anime 三项")
    for kind, name in dirs.items():
        if type(name) is not str or (not name and kind != "anime"):
            raise ValueError(f"plugins.organizer.type_dirs.{kind} 必须是非空的文件夹名")
        if name and (name != name.strip() or name in (".", "..") or re.search(r'[\\/:*?"<>|]', name)):
            raise ValueError(f"plugins.organizer.type_dirs.{kind} 必须是单层文件夹名，不能含 / \\ : * ? \" < > |")


def type_dir(dirs: Mapping[str, str], media_type: str, genre_ids: list[int]) -> str | None:
    """按媒体类型选文件夹：动画（类型为 anime 或题材含动画）优先归入动漫，未设动漫文件夹时按电影 / 剧集。"""
    if dirs.get("anime") and (media_type == "anime" or ANIMATION_GENRE in {str(g) for g in genre_ids or []}):
        return dirs["anime"]
    if media_type == "movie":
        return dirs.get("movie")
    if media_type in ("tv", "anime"):
        return dirs.get("tv")
    return None
