"""不再接收的作品：在 Telegram 审批卡片点「不再接收」后，同一部作品之后的消息识别完直接忽略。

用户数据，存在数据库（``Store.ignored_works``），每项::

    {id, title: 片名, tmdbid: 12345, kind: movie}

- 有 ``tmdbid`` 时按 TMDB 条目匹配；电影与剧集的 ID 各自编号，``kind``（movie / tv）一并比较；
- 没有时按 TMDB 规范片名匹配（``tmdb.normalize``）。

在管理台「追剧订阅」页、控制 Bot 的 /ignored 与 MCP 的 unignore 里可以恢复接收。
"""
from __future__ import annotations

from typing import Any

from . import tmdb

KINDS = ("movie", "tv")


def key(item: dict) -> str:
    """一部作品的身份：有 tmdbid 时按类型与 ID，否则按 TMDB 规范片名。"""
    if item.get("tmdbid"):
        return f"tmdb:{item.get('kind') or ''}:{int(item['tmdbid'])}"
    return f"title:{tmdb.normalize(item['title'])}"


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
