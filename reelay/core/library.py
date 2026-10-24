"""媒体库问答与来源质量的确定性统计（纯函数，数据由 Store 提供）。

- ``group_library``：已入库任务按作品汇总（清晰度、版本信息、各季已入库的集、类型与地区）；
- ``filter_library``：按类型、最低清晰度、地区、TMDB 类型 ID 与片名筛选，供「我有哪些 4K 日本动画」这类问题；
- ``missing``：缺集追更的巡检结果，没有巡检结果时按已入库的集算断档；
- ``source_quality``：按来源统计广告率、过滤率、重复率、通过率、修正率、TMDB 匹配率与版本质量。
"""
from __future__ import annotations

from typing import Any

from . import release, tmdb
from .media import RESOLUTION_LABELS, multi_season, resolution_rank
from .rules import MIN_RESOLUTIONS



def kept_files(snap: dict, replaced: set[str]) -> tuple[list[dict], bool]:
    """（这次入库还在网盘里的文件, 是否已全部被之后的版本替换）。replaced 见 Store.replaced_file_ids。"""
    landed = ((snap.get("meta") or {}).get("storage") or {}).get("landed") or []
    kept = [f for f in landed if str(f.get("id")) not in replaced]
    return kept, bool(landed) and not kept


def group_library(snapshots: list[dict], replaced: set[str] = frozenset()) -> list[dict[str, Any]]:
    """已入库任务按作品汇总，最近入库的作品在前；管理台媒体库、MCP 与对话助手共用。

    同一 tmdbid（没有时同一规范片名）是一部作品。replaced 是被覆盖保存或洗版替换掉的文件 ID：
    不再计入，全部被替换的任务整条跳过。体积优先用还在网盘里的文件大小，没有时按消息写的大小估算。
    """
    groups: dict[tuple, dict] = {}
    for snap in snapshots:
        p = snap.get("parsed") or {}
        if not p.get("title"):
            continue
        kept, gone = kept_files(snap, replaced)
        if gone:
            continue
        meta = snap.get("meta") or {}
        storage = meta.get("storage") or {}
        raw = p.get("raw_fields") or {}
        episodic = p.get("media_type") in ("tv", "anime") or bool(p.get("episodes"))
        key = ("tmdb", raw["tmdbid"]) if raw.get("tmdbid") else ("title", tmdb.normalize(p["title"]))
        group = groups.setdefault(key, {
            "title": p["title"], "year": p.get("year"), "media_type": p.get("media_type"), "tmdbid": raw.get("tmdbid"),
            "genre_ids": set(), "origin_country": set(), "best_rank": 0, "release": set(), "seasons": {},
            "save_dir_alias": snap.get("save_dir_alias"), "poster_path": None, "tasks": 0, "file_count": 0,
            "bytes": 0, "files": [], "updated_at": snap.get("updated_at") or 0})
        group["genre_ids"].update(p.get("genre_ids") or [])
        group["origin_country"].update(p.get("origin_country") or [])
        group["best_rank"] = max(group["best_rank"], resolution_rank(p.get("resolution")))
        group["release"].update(release.describe(p.get("release")))
        group["poster_path"] = group["poster_path"] or raw.get("poster_path")
        group["tasks"] += 1
        group["file_count"] += len(kept) or 1
        sizes = [f.get("size") for f in kept]
        group["bytes"] += (sum(sizes) if sizes and all(type(x) is int for x in sizes)
                           else release.size_bytes(p.get("size")) or 0)
        plan = [item for item in (meta.get("organize") or {}).get("plan") or [] if str(item.get("src_id")) not in replaced]
        group["files"] = (group["files"] + ["/".join(x for x in (item.get("target_dir"), item.get("target_name")) if x)
                                            for item in plan])[:100]
        group["updated_at"] = max(group["updated_at"], snap.get("updated_at") or 0)
        if episodic:
            # 跨多季的合集识别结果没有季、集为各季的并集，按转存清单的（季, 集）汇总
            pairs = (storage.get("manifest") or {}).get("pairs") or [
                [p.get("season") if p.get("season") is not None else 1, ep] for ep in p.get("episodes") or []]
            if not pairs and p.get("season") is not None:
                group["seasons"].setdefault(p["season"], set())
            for season, ep in pairs:
                group["seasons"].setdefault(int(season), set()).add(int(ep))
    out = []
    for group in groups.values():
        pairs = sorted((season, ep) for season, eps in group["seasons"].items() for ep in eps)
        out.append({**group, "genre_ids": sorted(group["genre_ids"]), "origin_country": sorted(group["origin_country"]),
                    "resolution": RESOLUTION_LABELS.get(group["best_rank"]), "release": sorted(group["release"]),
                    "episodes": sorted({ep for _, ep in pairs}), "episode_pairs": multi_season(pairs),
                    "seasons": {str(s): sorted(eps) for s, eps in sorted(group["seasons"].items())}})
    return sorted(out, key=lambda g: -g["updated_at"])


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
                                             "duplicates": 0, "done": 0, "media": 0, "unavailable": 0,
                                             "tmdb_checked": 0, "tmdb_matched": 0, "fallback": 0, "ranks": []})
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
            if (meta.get("share") or {}).get("status") == "unavailable":
                item["unavailable"] += 1  # 转存前分享已过期或取消，多是确认得太晚
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
                 "unavailable_rate": rate(item["unavailable"], item["media"]),
                 "decisions": human.get("decisions", 0), "approve_rate": rate(human.get("approved", 0), human.get("decisions", 0)),
                 "correct_rate": rate(human.get("corrected", 0), human.get("decisions", 0)),
                 "resolution": RESOLUTION_LABELS.get(round(sum(ranks) / len(ranks))) if ranks else None}
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
        if stats["media"] >= 10 and stats["unavailable_rate"] >= 0.3:
            flags.append("分享常在确认前失效")
        out.append({**stats, "flags": flags})
    return sorted(out, key=lambda item: -item["received"])
