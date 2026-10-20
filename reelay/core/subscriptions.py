"""剧集订阅：用户在追的剧，后续新集由订阅决策（``SubscriptionDecision``）自动放行。

订阅是系统配置 ``subscriptions``（缺集追更也用它），每项::

    {title: 剧名, season: 1, total: 36, tmdbid: 12345, auto: true}

- ``tmdbid``：TMDB 条目 ID；有它时按 ID 匹配，没有时按 TMDB 规范片名匹配；
- ``auto``：是否自动放行新集（默认 true；设为 false 只做缺集追更提醒）。

自动放行只是把「待你确认」改为「直接转存」：过滤规则、置信度、TMDB 匹配、分享清单校验与
去重照常生效，任何一项不过都仍交给你。放行只针对订阅的那一季：消息与读取分享都不能确认只涉及
这一季（如跨多季的合集）时不放行；消息没写季号时把季记为订阅的那一季，转存时只取该季。
"""
from __future__ import annotations

import re
from typing import Any

from . import tmdb
from .media import planned_pairs

FIELDS = ("title", "season", "total", "tmdbid", "auto")


def validate(cfg: dict) -> None:
    subscriptions = cfg.get("subscriptions", [])
    if not isinstance(subscriptions, list):
        raise ValueError("subscriptions 必须是数组")
    seen = set()
    for index, item in enumerate(subscriptions):
        label = f"subscriptions[{index}]"
        if not isinstance(item, dict) or type(item.get("title")) is not str or not item["title"].strip():
            raise ValueError(f"{label} 必须包含 title")
        if set(item) - set(FIELDS):
            raise ValueError(f"{label} 只支持 {'/'.join(FIELDS)}")
        for key, low in (("season", 0), ("total", 1), ("tmdbid", 1)):
            if key in item and item[key] is not None and (type(item[key]) is not int or not low <= item[key] <= 10 ** 9):
                raise ValueError(f"{label}.{key} 必须是不小于 {low} 的整数")
        if "auto" in item and type(item["auto"]) is not bool:
            raise ValueError(f"{label}.auto 必须是布尔值")
        key = identity(item)
        if key in seen:
            raise ValueError(f"{label} 与前面的订阅重复")
        seen.add(key)


def items(cfg: dict) -> list[dict[str, Any]]:
    out = []
    for item in cfg.get("subscriptions", []) or []:
        if isinstance(item, dict) and isinstance(item.get("title"), str):
            out.append({"title": item["title"].strip(), "season": item.get("season") if item.get("season") is not None else 1,
                        "total": item.get("total"), "tmdbid": item.get("tmdbid"), "auto": item.get("auto", True)})
    return out


def identity(item: dict) -> tuple:
    season = item.get("season") if item.get("season") is not None else 1
    return ("tmdb", item["tmdbid"], season) if item.get("tmdbid") else ("title", tmdb.normalize(item["title"]), season)


def seasons_of(ctx) -> set[int]:
    """任务涉及的季（跨多季的合集有多个）；消息与分享都没写集数时按识别结果的季，未写视为第 1 季。"""
    seasons = {season for season, _ in planned_pairs(ctx)}
    if seasons:
        return seasons
    return {ctx.parsed.season if ctx.parsed and ctx.parsed.season is not None else 1}


def match(subscriptions: list[dict], parsed, tmdb_ns: dict, *, seasons: set[int] | None = None,
          auto_only: bool = True) -> dict | None:
    """TMDB 匹配成功的剧集资源对应的订阅（任一季命中即可）；只认 TMDB 规范结果，不认 AI 自己给的片名。

    seasons 默认取识别结果的季（未写视为第 1 季）。
    """
    if not parsed or not tmdb_ns.get("matched") or not (parsed.media_type in ("tv", "anime") or parsed.episodes):
        return None
    if seasons is None:
        seasons = {parsed.season if parsed.season is not None else 1}
    found = tmdb_ns.get("tmdbid") or parsed.raw_fields.get("tmdbid")
    for item in subscriptions:
        if (auto_only and not item["auto"]) or item["season"] not in seasons:
            continue
        if item["tmdbid"] and item["tmdbid"] == found:
            return item
        if not item["tmdbid"] and tmdb.normalize(item["title"]) == tmdb.normalize(parsed.title):
            return item
    return None


_CN_NUMBERS = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def _season_number(text: str) -> int | None:
    if text.isdigit():
        return int(text)
    if text in _CN_NUMBERS:
        return _CN_NUMBERS[text]
    if text.startswith("十") and text[1:] in _CN_NUMBERS:
        return 10 + _CN_NUMBERS[text[1:]]
    return None


def parse_request(text: str) -> dict[str, Any]:
    """不调用 AI 时的兜底：从「我在追《X》第二季」这类句子里取片名与季号。"""
    season = None
    match_season = re.search(r"第\s*([0-9一二两三四五六七八九十]+)\s*季|S(\d{1,2})(?!\d)", text, re.I)
    if match_season:
        season = _season_number(match_season.group(1) or match_season.group(2))
    quoted = re.search(r"《([^》]+)》", text)
    if quoted:
        title = quoted.group(1)
    else:
        title = re.sub(r"第\s*[0-9一二两三四五六七八九十]+\s*季|S\d{1,2}(?!\d)", "", text, flags=re.I)
        title = re.sub(r"^(我|最近|正)?(在)?(追|看|订阅)(一下)?", "", title.strip())
    return {"title": title.strip(" ，。,.!！"), "season": season, "year": None}
