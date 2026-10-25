"""作品身份：两条记录是不是同一部作品（入库去重、已入库集数的汇总用）。

与媒体库、订阅、「不再接收」的约定一致：匹配到 TMDB 时按类型与 TMDB ID，没有时按规范片名，再用年份区分同名作品：

- 两边都有 TMDB ID：ID 相同才是同一部（片名改过也算同一部）；
- 否则规范片名相同，且年份相同或有一边不知道年份。

两边都不知道 TMDB ID 与年份时按同一部处理：宁可转人工确认，也不重复转存。
"""
from __future__ import annotations

import re
from typing import Any

from . import tmdb

_TMDB = re.compile(r"(tv|movie):\d+")


def identity(title: str | None, year: Any = None, tmdbid: Any = None, kind: str = "tv") -> dict[str, Any]:
    """{title: 规范片名, year, tmdb: 「tv:123」}；不知道的为 None。"""
    year = int(year) if isinstance(year, int) or (isinstance(year, str) and year.isdigit()) else None
    tmdbid = int(tmdbid) if isinstance(tmdbid, int) or (isinstance(tmdbid, str) and tmdbid.isdigit()) else None
    return {"title": tmdb.normalize(title), "year": year or None,
            "tmdb": f"{kind if kind in ('tv', 'movie') else 'tv'}:{tmdbid}" if tmdbid else None}


def of(parsed, tmdb_ns: dict | None = None) -> dict[str, Any]:
    """识别结果对应的作品；tmdb_ns 是任务的 meta["tmdb"]（TMDB 补全写入的匹配结果）。"""
    tmdb_ns = tmdb_ns or {}
    fields = parsed.raw_fields or {}
    kind = tmdb_ns.get("kind") or ("movie" if parsed.media_type == "movie" and not parsed.episodes else "tv")
    return identity(parsed.title, parsed.year, fields.get("tmdbid") or tmdb_ns.get("tmdbid"), kind)


def same(a: dict, b: dict) -> bool:
    if a.get("tmdb") and b.get("tmdb"):
        return a["tmdb"] == b["tmdb"]
    return a["title"] == b["title"] and (not a.get("year") or not b.get("year") or a["year"] == b["year"])


def key(work: dict) -> str:
    """去重键里的作品部分：「规范片名|年份|TMDB」，不知道的留空。规范片名里没有「|」。"""
    return f"{work['title']}|{work.get('year') or ''}|{work.get('tmdb') or ''}"


def parse_key(text: str) -> dict[str, Any]:
    """key() 的反向；只有片名的旧格式按「年份与 TMDB 都不知道」处理。"""
    parts = text.split("|")
    if len(parts) == 3 and (not parts[1] or parts[1].isdigit()) and (not parts[2] or _TMDB.fullmatch(parts[2])):
        return {"title": parts[0], "year": int(parts[1]) if parts[1] else None, "tmdb": parts[2] or None}
    return {"title": tmdb.normalize(text), "year": None, "tmdb": None}


def claim_key(title: str | None, season: int | None, ep: int) -> str:
    """转存时同一集的资源占位：只按规范片名，同名的不同作品依次转存，身份一时判断不准也不会同时转存同一集。"""
    return f"{tmdb.normalize(title)}|S{season if season is not None else 1:02d}|E{ep:02d}"


def episodes(stored: list[dict], work: dict, season: int) -> set[int]:
    """已入库汇总（Store.stored_series）里属于这部作品这一季的集。"""
    return set().union(*(entry["episodes"] for entry in stored
                         if entry["season"] == season and same(entry["work"], work)))
