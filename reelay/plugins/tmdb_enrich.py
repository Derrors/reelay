"""TMDB 元数据匹配 —— after_parse Hook（评审 P1-04）。

从 AI 解析出的候选标题去 TMDB 搜规范条目，按标题相似度与年份挑选候选，回填规范标题、
年份、tmdbid、类型 ID 与国家/地区，让下游分类和整理入库建出能被 Jellyfin/Emby 正确刮削的
目录（默认命名模板写入 ``[tmdbid=xxx]``）。最佳候选不可信时标记 ``matched=False`` 并保留 AI
标题，不硬改；置信度计算会据此转人工。

- 配了 TMDB API Key（管理台「设置」）：走真实搜索（标准库 urllib，无需额外依赖）。
- 未配：降级放行（沿用 AI 标题），不阻断主流程。
- anime：有季/集信息先按剧集搜，未命中再按电影搜（动画电影）。
"""

from __future__ import annotations

import asyncio
import logging
import os

from ..core import tmdb
from ..core.contracts import PipelineContext
from ..core.registry import register

log = logging.getLogger("reelay.plugin.tmdb")


def search_kinds(media_type: str | None, episodic: bool) -> list[str]:
    if media_type == "anime":
        return ["tv", "movie"] if episodic else ["movie", "tv"]
    if media_type is None and not episodic:
        return ["tv", "movie"]  # 识别不出类型（如 AI 降级）：两边都搜，取更像的
    if media_type == "tv" or episodic:
        return ["tv"]
    # 判为电影但没写集数：电影搜不到时再搜剧集。消息只写片名的剧集合集常被当成电影
    return ["movie", "tv"]


@register(kind="hook", stage="after_parse", order=50)
class TmdbEnrich:
    name = "tmdb_enrich"
    ui_label = "TMDB 元数据补全"
    ui_description = ("按标题相似度与年份匹配 TMDB 条目，补充规范标题、年份、类型与国家；未配置凭证时保留原标题。"
                      "TMDB 的 key、地址与语言在「设置 → AI 与 TMDB」中配置。")
    stage = "after_parse"
    order = 50

    def __init__(self) -> None:
        self._dry_run = os.getenv("REELAY_DRY_RUN", "").lower() in {"1", "true", "yes"}

    @property
    def _key(self) -> str | None:
        """用到时读取密钥：管理台填好 TMDB key 后不用重启。"""
        return None if self._dry_run else tmdb.api_key()

    def needs_refresh(self, ctx: PipelineContext) -> bool:
        """配置 key 之前识别、或上次查询出错的任务，启动时补跑一次。"""
        p, ns = ctx.parsed, ctx.meta.get("tmdb")
        return bool(self._key and p and p.is_media and p.title
                    and not ctx.meta.get("parse", {}).get("prefilter")
                    and (ns is None or ns.get("note") or ns.get("error")))

    async def run(self, ctx: PipelineContext) -> bool:
        p = ctx.parsed
        if not p or not p.is_media or not p.title or ctx.meta.get("parse", {}).get("prefilter"):
            return True

        ns = ctx.ns("tmdb")
        ns.clear()
        if not self._key:
            ns["matched"] = False
            ns["note"] = "模拟模式，沿用原标题" if self._dry_run else "未配 TMDB key，沿用 AI 标题"
            return True

        from ..core import runtime
        language = runtime.get("tmdb.language", "zh-CN")
        timeout = runtime.get("tmdb.timeout", 10)
        hit, candidates = None, []
        unknown = p.media_type is None
        try:
            for kind in search_kinds(p.media_type, bool(p.episodes or p.season)):
                found, found_candidates = await asyncio.to_thread(
                    tmdb.best_match, kind, p.title, p.year, language=language, timeout=timeout)
                if found and (hit is None or found["_score"] > hit["_score"]):
                    hit, candidates = found, found_candidates
                elif not candidates:
                    candidates = found_candidates
                if hit and not unknown:
                    break  # 类型已知时按顺序取第一个可信匹配；类型未知时两边都比
        except Exception as exc:
            log.warning("TMDB 查询失败：%s", exc)
            ns["matched"] = False
            ns["error"] = type(exc).__name__
            runtime.observe("tmdb", "failed", type(exc).__name__)
            return True  # 失败不阻断

        runtime.observe("tmdb", "connected")
        ns["candidates"] = candidates
        if not hit:
            ns["matched"] = False
            ctx.log(f"TMDB 无可信匹配「{p.title}」，保留原标题")
            return True

        # 回填规范元数据
        canonical = hit.get("title") or hit.get("name")
        year = tmdb.candidate_year(hit) or p.year
        if canonical:
            p.title = canonical
        if year:
            p.year = year
        ns.update(matched=True, tmdbid=hit.get("id"), kind=hit["_kind"], score=hit["_score"])
        if unknown or (p.media_type == "movie" and hit["_kind"] == "tv"):
            p.media_type = hit["_kind"]  # 类型以 TMDB 条目为准；动画由类型 ID（16）分类
            ctx.log(f"类型按 TMDB 条目定为{'剧集' if hit['_kind'] == 'tv' else '电影'}"
                    + ("，转存时以分享里的文件为准" if hit["_kind"] == "tv" else ""))
        p.raw_fields["tmdbid"] = hit.get("id")
        if hit.get("poster_path"):
            p.raw_fields["poster_path"] = hit["poster_path"]  # 管理台卡片展示海报
        p.raw_fields.setdefault("genre", "")
        p.genre_ids = [int(value) for value in hit.get("genre_ids") or []
                       if type(value) is int and value > 0]
        countries = hit.get("origin_country") or []
        if isinstance(countries, str):
            countries = [countries]
        countries = [str(value).upper() for value in countries if isinstance(value, str) and len(value) == 2]
        # 电影搜索结果没有 origin_country：保留 AI 从正文「地区」得到的值，不能清空。
        if countries:
            p.origin_country = countries
        ctx.log(f"TMDB 匹配：{canonical} ({year}) tmdbid={hit.get('id')} 相似度 {hit['_score']}")
        return True
