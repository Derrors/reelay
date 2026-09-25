"""示例插件：after_parse 阶段的「元数据匹配」钩子（评审 P1-04 的落点）。

演示"加功能 = 写一个文件"：无需改内核，放进 plugins/ 即生效。

真实实现应拿 AI 抽到的候选标题去查 TMDB/TVDB，回填规范标题、年份、tmdbid，
匹配不到就把资源标记为「待人工」而非硬建目录。这里给结构占位，默认放行。
"""

from __future__ import annotations

import logging

from ..core.contracts import PipelineContext
from ..core.registry import register

log = logging.getLogger("reelay.plugin.tmdb")


@register(kind="hook", stage="after_parse", order=50)
class TmdbEnrich:
    stage = "after_parse"
    order = 50

    async def run(self, ctx: PipelineContext) -> bool:
        p = ctx.parsed
        if not p or not p.is_media or not p.title:
            return True  # 非影视，放行，不做元数据匹配

        # --- 占位：真实实现在这里查 TMDB ---
        # info = await tmdb.search(p.title, year=p.year)
        # if not info: ctx.ns("tmdb")["matched"] = False; return True
        # p.title = info.canonical_title; p.year = info.year
        # ctx.ns("tmdb")["tmdbid"] = info.id
        ctx.ns("tmdb")["matched"] = False
        ctx.ns("tmdb")["note"] = "示例占位：未接 TMDB，沿用 AI 标题"
        log.debug("tmdb enrich(占位) title=%s", p.title)
        return True
