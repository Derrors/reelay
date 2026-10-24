"""不再接收的作品：在 Telegram 审批卡片点「不再接收」后，同一部作品之后的消息识别完直接忽略。

系统配置 ``ignored_works``，每项::

    {title: 片名, tmdbid: 12345, kind: movie}

- 有 ``tmdbid`` 时按 TMDB 条目匹配；电影与剧集的 ID 各自编号，``kind``（movie / tv）一并比较；
- 没有时按 TMDB 规范片名匹配（``tmdb.normalize``）。

在管理台「追剧订阅」页可以恢复接收。
"""
from __future__ import annotations

from typing import Any

from . import tmdb

FIELDS = ("title", "tmdbid", "kind")
KINDS = ("movie", "tv")


def validate(cfg: dict) -> None:
    works = cfg.get("ignored_works", [])
    if not isinstance(works, list):
        raise ValueError("ignored_works 必须是数组")
    seen = set()
    for index, item in enumerate(works):
        label = f"ignored_works[{index}]"
        if not isinstance(item, dict) or type(item.get("title")) is not str or not item["title"].strip():
            raise ValueError(f"{label} 必须包含 title")
        if set(item) - set(FIELDS):
            raise ValueError(f"{label} 只支持 {'/'.join(FIELDS)}")
        if item.get("tmdbid") is not None and (type(item["tmdbid"]) is not int or not 1 <= item["tmdbid"] <= 10 ** 9):
            raise ValueError(f"{label}.tmdbid 必须是正整数")
        if item.get("kind") is not None and item["kind"] not in KINDS:
            raise ValueError(f"{label}.kind 只能是 movie 或 tv")
        key = identity(item)
        if key in seen:
            raise ValueError(f"{label} 与前面的条目重复")
        seen.add(key)


def items(cfg: dict) -> list[dict[str, Any]]:
    return [{"title": item["title"].strip(), "tmdbid": item.get("tmdbid"), "kind": item.get("kind")}
            for item in cfg.get("ignored_works", []) or []
            if isinstance(item, dict) and isinstance(item.get("title"), str)]


def identity(item: dict) -> tuple:
    if item.get("tmdbid"):
        return ("tmdb", item.get("kind"), item["tmdbid"])
    return ("title", tmdb.normalize(item["title"]))


def entry_for(parsed, tmdb_ns: dict) -> dict[str, Any]:
    """任务对应的条目：匹配到 TMDB 时带编号与类型，否则只有片名。"""
    if not parsed or not parsed.title:
        raise ValueError("这条资源没有识别出片名")
    entry: dict[str, Any] = {"title": parsed.title}
    tmdbid = tmdb_ns.get("tmdbid") or parsed.raw_fields.get("tmdbid")
    if tmdbid:
        entry["tmdbid"] = int(tmdbid)
        kind = tmdb_ns.get("kind") or ("movie" if parsed.media_type == "movie" else "tv")
        entry["kind"] = kind if kind in KINDS else "tv"
    return entry


def match(works: list[dict], parsed, tmdb_ns: dict) -> dict | None:
    """识别结果属于哪一项不再接收的作品；没有时为 None。"""
    if not works or not parsed or not parsed.title:
        return None
    tmdbid = tmdb_ns.get("tmdbid") or parsed.raw_fields.get("tmdbid")
    kind = tmdb_ns.get("kind")
    title = tmdb.normalize(parsed.title)
    for item in works:
        if item["tmdbid"]:
            if tmdbid and int(tmdbid) == item["tmdbid"] and (not kind or not item["kind"] or kind == item["kind"]):
                return item
        elif tmdb.normalize(item["title"]) == title:
            return item
    return None
