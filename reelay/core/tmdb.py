"""TMDB 只读客户端与候选打分（标准库实现，无额外依赖）。

搜索结果不再直接取第一条：按标题相似度（含原名）和年份打分，低于阈值视为未匹配，
保留 AI 标题而不是写入错误的规范名。结果（含未命中）进程内缓存一天。

``REELAY_TMDB_API_KEY`` 同时支持 v3 API Key 与 v4 Read Access Token（``eyJ`` 开头）。
"""
from __future__ import annotations

import json
import os
import re
import time
import unicodedata
import urllib.parse
import urllib.request
from collections import OrderedDict
from difflib import SequenceMatcher
from typing import Any

TMDB_BASE = os.getenv("REELAY_TMDB_BASE_URL", "https://api.themoviedb.org/3")
ACCEPT_SCORE = 0.8          # 年份无冲突时的最低标题相似度
ACCEPT_SCORE_YEAR = 0.6     # 年份精确一致时放宽的标题相似度
_CACHE: OrderedDict[tuple, tuple[float, Any]] = OrderedDict()
_CACHE_TTL = 24 * 3600
_CACHE_SIZE = 512


def api_key() -> str | None:
    return os.getenv("REELAY_TMDB_API_KEY") or None


def _request(path: str, params: dict[str, Any], *, timeout: float) -> dict:
    key = api_key()
    if not key:
        raise RuntimeError("未配置 REELAY_TMDB_API_KEY")
    headers = {"Accept": "application/json"}
    query = dict(params)
    if key.startswith("eyJ"):
        headers["Authorization"] = f"Bearer {key}"
    else:
        query["api_key"] = key
    url = f"{TMDB_BASE}{path}?" + urllib.parse.urlencode(query)
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _cached(key: tuple, loader):
    item = _CACHE.get(key)
    if item and time.monotonic() - item[0] < _CACHE_TTL:
        _CACHE.move_to_end(key)
        return item[1]
    value = loader()
    _CACHE[key] = (time.monotonic(), value)
    _CACHE.move_to_end(key)
    while len(_CACHE) > _CACHE_SIZE:
        _CACHE.popitem(last=False)
    return value


def normalize(title: str | None) -> str:
    text = unicodedata.normalize("NFKC", title or "").lower()
    return re.sub(r"[\W_]+", "", text)


def title_similarity(query: str, candidate: dict) -> float:
    wanted = normalize(query)
    best = 0.0
    for name in (candidate.get("title"), candidate.get("name"),
                 candidate.get("original_title"), candidate.get("original_name")):
        other = normalize(name)
        if not wanted or not other:
            continue
        if wanted == other:
            return 1.0
        ratio = SequenceMatcher(None, wanted, other).ratio()
        # 一方完整包含另一方（如带副标题）给较高分，但不视为完全一致。
        if wanted in other or other in wanted:
            ratio = max(ratio, 0.85 * min(len(wanted), len(other)) / max(len(wanted), len(other)) + 0.15)
        best = max(best, ratio)
    return best


def candidate_year(candidate: dict) -> int | None:
    date = candidate.get("release_date") or candidate.get("first_air_date") or ""
    return int(date[:4]) if date[:4].isdigit() else None


def score(query: str, year: int | None, candidate: dict) -> tuple[float, bool]:
    """返回 (综合分, 是否可接受)。年份差超过 1 年直接不接受。"""
    similarity = title_similarity(query, candidate)
    found = candidate_year(candidate)
    if year and found and abs(year - found) > 1:
        return similarity - 0.5, False
    exact_year = bool(year and found == year)
    accepted = similarity >= ACCEPT_SCORE or (exact_year and similarity >= ACCEPT_SCORE_YEAR)
    return similarity + (0.1 if exact_year else 0.0), accepted


def search(kind: str, title: str, year: int | None, *, language: str, timeout: float) -> list[dict]:
    def load():
        params = {"query": title, "language": language}
        if year:
            params["year" if kind == "movie" else "first_air_date_year"] = year
        results = _request(f"/search/{kind}", params, timeout=timeout).get("results") or []
        if not results and year:  # 年份可能差一年（跨年首播、地区上映差）：去掉年份再搜一次
            params.pop("year", None)
            params.pop("first_air_date_year", None)
            results = _request(f"/search/{kind}", params, timeout=timeout).get("results") or []
        return results[:10]
    return _cached(("search", kind, normalize(title), year, language), load)


def best_match(kind: str, title: str, year: int | None, *, language: str,
               timeout: float) -> tuple[dict | None, list[dict]]:
    """返回 (可接受的最佳候选或 None, 前 3 个候选摘要)。"""
    ranked = []
    for index, candidate in enumerate(search(kind, title, year, language=language, timeout=timeout)):
        value, accepted = score(title, year, candidate)
        ranked.append((value, -index, accepted, candidate))
    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    summary = [{"id": c.get("id"), "title": c.get("title") or c.get("name"),
                "year": candidate_year(c), "score": round(v, 2)} for v, _, _, c in ranked[:3]]
    for value, _, accepted, candidate in ranked:
        if accepted:
            return {**candidate, "_kind": kind, "_score": round(value, 2)}, summary
        break  # 只看最高分；最高分不可接受时不退而求其次
    return None, summary


def season_episodes(tv_id: int, season: int, *, language: str, timeout: float) -> list[dict]:
    """某季的集列表（含 air_date），供追更检查计算已播集数。"""
    def load():
        data = _request(f"/tv/{int(tv_id)}/season/{int(season)}", {"language": language}, timeout=timeout)
        return [{"episode": e.get("episode_number"), "air_date": e.get("air_date")}
                for e in data.get("episodes") or [] if type(e.get("episode_number")) is int]
    return _cached(("season", int(tv_id), int(season), language), load)
