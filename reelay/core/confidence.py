"""确定性置信度：用可核对的信号打分，不采信 LLM 自报的 confidence。

实测 LLM 对绝大多数资源都自报 1.0，起不到把关作用。这里按以下信号计算：

- 标题、媒体类型、年份是否齐全，类型与集数是否自洽；
- TMDB 已启用时是否匹配到条目（启用但未匹配 → 封顶，转人工）；
- 解析降级到 regex 时封顶（降级结果本就强制人工）。

解析器原始值保留在 ``parsed.raw_fields["parser_confidence"]`` 供回查。
"""
from __future__ import annotations

from typing import Any

from .contracts import PipelineContext

WEIGHTS = {"title": 0.4, "type": 0.3, "year": 0.3}
TMDB_UNMATCHED_CAP = 0.45
FALLBACK_CAP = 0.4
NO_TITLE_CAP = 0.3


def assess(ctx: PipelineContext) -> tuple[float, dict[str, Any]]:
    p = ctx.parsed
    if not p or not p.is_media or p.is_ad or not p.has_link():
        return 0.0, {"checks": {}, "note": "非影视、广告或缺 115 分享与电驴链接"}
    episodic = p.media_type in ("tv", "anime")
    checks = {
        "title": bool(p.title and p.title.strip()),
        # 电影不应带集数；剧集集数允许为空，转存时以分享内文件名为准。
        "type": p.media_type in ("movie", "tv", "anime") and (episodic or not p.episodes),
        "year": p.year is not None,
    }
    score = sum(weight for key, weight in WEIGHTS.items() if checks[key])
    caps = []
    tmdb = ctx.meta.get("tmdb", {})
    # 只有真实查询过 TMDB 才把匹配结果当信号；未配置或网络失败保持中立。
    if "matched" in tmdb and not tmdb.get("note") and not tmdb.get("error"):
        checks["tmdb"] = bool(tmdb["matched"])
        if not checks["tmdb"]:
            caps.append(("tmdb_unmatched", TMDB_UNMATCHED_CAP))
    if ctx.meta.get("parse", {}).get("fallback"):
        caps.append(("parse_fallback", FALLBACK_CAP))
    if not checks["title"]:
        caps.append(("no_title", NO_TITLE_CAP))
    for _, cap in caps:
        score = min(score, cap)
    return round(score, 2), {"checks": checks, "caps": [name for name, _ in caps]}


def apply(ctx: PipelineContext) -> None:
    """把确定性分数写回 parsed.confidence，并记录各项信号。"""
    if not ctx.parsed:
        return
    ctx.parsed.raw_fields.setdefault("parser_confidence", ctx.parsed.confidence)
    score, detail = assess(ctx)
    ctx.parsed.confidence = score
    ctx.ns("confidence").update(score=score, **detail)
