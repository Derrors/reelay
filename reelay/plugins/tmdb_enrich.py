"""TMDB 元数据匹配 —— after_parse Hook（评审 P1-04）。

从 AI 解析出的候选标题去 TMDB 搜规范条目，回填规范标题、年份、tmdbid，
让下游整理入库能建出被 Jellyfin/Emby 正确刮削的目录（写入 ``[tmdbid-xxx]`` 便于识别）。
匹配不到就标记 ``matched=False`` 并保留 AI 标题，不硬改。

- 配了 ``REELAY_TMDB_API_KEY``：走真实搜索（标准库 urllib，无需额外依赖）。
- 未配：降级放行（沿用 AI 标题），不阻断主流程。
"""

from __future__ import annotations

import json
import logging
import os
import urllib.parse
import urllib.request
import asyncio

from ..core.contracts import PipelineContext
from ..core.registry import register

log = logging.getLogger("reelay.plugin.tmdb")

TMDB_BASE = os.getenv("REELAY_TMDB_BASE_URL", "https://api.themoviedb.org/3")


@register(kind="hook", stage="after_parse", order=50)
class TmdbEnrich:
    name = "tmdb_enrich"
    ui_label = "TMDB 元数据补全"
    ui_description = "解析后补充规范标题与年份；未配置 TMDB 凭证时保留原标题。"
    ui_fields = [
        {'path': 'tmdb.language', 'label': '匹配语言', 'type': 'string', 'choices': ['zh-CN', 'zh-TW', 'en-US']},
        {'path': 'tmdb.timeout', 'label': '请求超时（秒）', 'type': 'number', 'min': 1, 'max': 60},
    ]
    stage = "after_parse"
    order = 50

    def __init__(self) -> None:
        self._dry_run = os.getenv("REELAY_DRY_RUN", "").lower() in {"1", "true", "yes"}
        self._key = None if self._dry_run else os.getenv("REELAY_TMDB_API_KEY")

    async def run(self, ctx: PipelineContext) -> bool:
        p = ctx.parsed
        if not p or not p.is_media or not p.title:
            return True

        if not self._key:
            ctx.ns("tmdb")["matched"] = False
            ctx.ns("tmdb")["note"] = "模拟模式，沿用原标题" if self._dry_run else "未配 TMDB key，沿用 AI 标题"
            return True

        kind = "tv" if (p.media_type == "tv" or p.episodes) else "movie"
        try:
            hit = await asyncio.to_thread(self._search, kind, p.title, p.year)
        except Exception as exc:
            log.warning("TMDB 查询失败：%s", exc)
            ctx.ns("tmdb")["matched"] = False
            ctx.ns("tmdb")["error"] = str(exc)
            return True  # 失败不阻断

        if not hit:
            ctx.ns("tmdb")["matched"] = False
            ctx.log(f"TMDB 未匹配到「{p.title}」，保留原标题")
            return True

        # 回填规范元数据
        canonical = hit.get("title") or hit.get("name")
        date = hit.get("release_date") or hit.get("first_air_date") or ""
        year = int(date[:4]) if date[:4].isdigit() else p.year
        if canonical:
            p.title = canonical
        if year:
            p.year = year
        ns = ctx.ns("tmdb")
        ns["matched"] = True
        ns["tmdbid"] = hit.get("id")
        p.raw_fields["tmdbid"] = hit.get("id")
        p.raw_fields.setdefault("genre", "")
        p.genre_ids = [int(value) for value in hit.get("genre_ids") or []
                       if type(value) is int and value > 0]
        countries = hit.get("origin_country") or []
        if isinstance(countries, str):
            countries = [countries]
        p.origin_country = [str(value).upper() for value in countries
                            if isinstance(value, str) and len(value) == 2]
        ctx.log(f"TMDB 匹配：{canonical} ({year}) tmdbid={hit.get('id')}")
        return True

    def _search(self, kind: str, title: str, year: int | None) -> dict | None:
        from ..core import runtime
        params = {"api_key": self._key, "query": title, "language": runtime.get('tmdb.language', 'zh-CN')}
        if year:
            params["year" if kind == "movie" else "first_air_date_year"] = year
        url = f"{TMDB_BASE}/search/{kind}?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=runtime.get('tmdb.timeout', 10)) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        results = data.get("results") or []
        return results[0] if results else None
