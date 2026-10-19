"""媒体库问答与来源质量的确定性统计（纯函数，数据由 Store 提供）。

- ``group_library``：已入库任务按作品汇总（清晰度、版本信息、各季已入库的集、类型与地区）；
- ``filter_library``：按类型、最低清晰度、地区、TMDB 类型 ID 与片名筛选，供「我有哪些 4K 日本动画」这类问题；
- ``missing``：缺集追更的巡检结果，没有巡检结果时按已入库的集算断档；
- ``source_quality``：按来源统计广告率、过滤率、重复率、通过率、修正率、TMDB 匹配率与版本质量。
"""
from __future__ import annotations

from typing import Any

from . import release, tmdb
from .media import resolution_rank
from .rules import MIN_RESOLUTIONS

RANK_LABELS = {4: "2160p", 3: "1080p", 2: "720p", 1: "标清"}


def group_library(snapshots: list[dict]) -> list[dict[str, Any]]:
    groups: dict[tuple, dict] = {}
    for snap in snapshots:
        p = snap.get("parsed") or {}
        if not p.get("title"):
            continue
        raw = p.get("raw_fields") or {}
        episodic = p.get("media_type") in ("tv", "anime") or bool(p.get("episodes"))
        key = ("tmdb", raw["tmdbid"]) if raw.get("tmdbid") else ("title", tmdb.normalize(p["title"]))
        group = groups.setdefault(key, {
            "title": p["title"], "year": p.get("year"), "media_type": p.get("media_type"), "tmdbid": raw.get("tmdbid"),
            "genre_ids": set(), "origin_country": set(), "best_rank": 0, "release": set(), "seasons": {},
            "save_dir": snap.get("save_dir_alias"), "files": 0})
        group["genre_ids"].update(p.get("genre_ids") or [])
        group["origin_country"].update(p.get("origin_country") or [])
        group["best_rank"] = max(group["best_rank"], resolution_rank(p.get("resolution")))
        group["release"].update(release.describe(p.get("release")))
        group["files"] += len(((snap.get("meta") or {}).get("storage") or {}).get("landed") or []) or 1
        if episodic:
            season = p.get("season") if p.get("season") is not None else 1
            group["seasons"].setdefault(season, set()).update(p.get("episodes") or [])
    out = []
    for group in groups.values():
        out.append({**group, "genre_ids": sorted(group["genre_ids"]), "origin_country": sorted(group["origin_country"]),
                    "resolution": RANK_LABELS.get(group["best_rank"]), "release": sorted(group["release"]),
                    "seasons": {str(s): sorted(eps) for s, eps in sorted(group["seasons"].items())}})
    return out


def filter_library(groups: list[dict], *, media_type: str | None = None, min_resolution: str | None = None,
                   origin_country: str | None = None, genre_id: int | None = None) -> list[dict]:
    if min_resolution is not None and min_resolution not in MIN_RESOLUTIONS:
        raise ValueError(f"最低清晰度只能是 {'、'.join(MIN_RESOLUTIONS)}")
    out = []
    for group in groups:
        kind = group["media_type"]
        if media_type == "anime":
            # 动画按 TMDB 类型 16 判断：AI 有时把日本动画识别成 tv
            if kind != "anime" and 16 not in group["genre_ids"]:
                continue
        elif media_type and kind != media_type:
            continue
        if min_resolution and group["best_rank"] < MIN_RESOLUTIONS[min_resolution]:
            continue
        if origin_country and origin_country.upper() not in group["origin_country"]:
            continue
        if genre_id is not None and genre_id not in group["genre_ids"]:
            continue
        out.append(group)
    return sorted(out, key=lambda g: g["title"])


def gaps(episodes: set[int]) -> list[int]:
    """首个已入库集之后的断档。"""
    return sorted(set(range(min(episodes), max(episodes) + 1)) - episodes) if episodes else []


def missing(report: dict, stored: dict[tuple[str, int], set[int]], title: str | None = None) -> list[dict]:
    wanted = tmdb.normalize(title) if title else None
    items = []
    for key, info in (report.get("series") or {}).items():
        name, _, season = key.rpartition("|S")
        if wanted and wanted not in tmdb.normalize(name):
            continue
        items.append({"title": name, "season": int(season), "have": len(info.get("have") or []),
                      "missing": info.get("missing") or [], "aired": info.get("aired"),
                      "overdue": info.get("overdue") or []})
    if items or not wanted:
        return items
    for (name, season), episodes in stored.items():  # 没有巡检结果：按已入库的集算断档
        if wanted in tmdb.normalize(name):
            items.append({"title": name, "season": season, "have": len(episodes), "missing": gaps(episodes),
                          "aired": None, "overdue": [], "note": "没有 TMDB 播出信息，只看已入库集之间的断档"})
    return items


def source_quality(snapshots: list[dict], decisions: dict[str, dict]) -> list[dict[str, Any]]:
    by_source: dict[str, dict] = {}
    for snap in snapshots:
        source = (snap.get("source") or "").lower()
        item = by_source.setdefault(source, {"source": snap.get("source"), "received": 0, "ads": 0, "filtered": 0,
                                             "duplicates": 0, "done": 0, "media": 0, "tmdb_checked": 0,
                                             "tmdb_matched": 0, "fallback": 0, "ranks": []})
        item["received"] += 1
        meta, parsed, state = snap.get("meta") or {}, snap.get("parsed") or {}, snap["state"]
        trail = " ".join(snap.get("trail") or [])
        if state == "rejected" or parsed.get("is_ad"):
            item["ads"] += 1
        elif state == "skipped" and "去重命中" in trail:
            item["duplicates"] += 1
        elif state == "skipped" and ((meta.get("parse") or {}).get("prefilter") or "过滤：" in trail):
            item["filtered"] += 1
        if parsed.get("is_media") and not parsed.get("is_ad"):
            item["media"] += 1
            if (meta.get("parse") or {}).get("fallback"):
                item["fallback"] += 1
            tmdb_ns = meta.get("tmdb") or {}
            if "matched" in tmdb_ns and not tmdb_ns.get("note") and not tmdb_ns.get("error"):
                item["tmdb_checked"] += 1
                item["tmdb_matched"] += bool(tmdb_ns["matched"])
            if state == "done":
                item["ranks"].append(resolution_rank(parsed.get("resolution")))
        if state == "done":
            item["done"] += 1
    out = []
    for key, item in by_source.items():
        human = decisions.get(key, {})
        received, ranks = item.pop("received"), [r for r in item.pop("ranks") if r]
        rate = lambda n, d: round(n / d, 3) if d else None  # noqa: E731
        stats = {**item, "received": received,
                 "ad_rate": rate(item["ads"], received), "filter_rate": rate(item["filtered"], received),
                 "duplicate_rate": rate(item["duplicates"], received),
                 "tmdb_match_rate": rate(item["tmdb_matched"], item["tmdb_checked"]),
                 "fallback_rate": rate(item["fallback"], item["media"]),
                 "decisions": human.get("decisions", 0), "approve_rate": rate(human.get("approved", 0), human.get("decisions", 0)),
                 "correct_rate": rate(human.get("corrected", 0), human.get("decisions", 0)),
                 "resolution": RANK_LABELS.get(round(sum(ranks) / len(ranks))) if ranks else None}
        flags = []
        if received >= 10 and stats["ad_rate"] >= 0.5:
            flags.append("广告占一半以上")
        if received >= 10 and stats["duplicate_rate"] >= 0.3:
            flags.append("重复资源较多")
        if stats["decisions"] >= 10 and stats["approve_rate"] < 0.5:
            flags.append("确认时多半被忽略")
        if stats["tmdb_checked"] >= 10 and stats["tmdb_match_rate"] < 0.7:
            flags.append("TMDB 匹配率低，片名常不规范")
        if stats["media"] >= 10 and stats["fallback_rate"] >= 0.2:
            flags.append("AI 识别经常降级")
        out.append({**stats, "flags": flags})
    return sorted(out, key=lambda item: -item["received"])
