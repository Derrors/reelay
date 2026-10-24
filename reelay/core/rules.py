"""来源过滤的结构化规则：按识别与 TMDB 补全后的字段确定性求值。

``filters`` 除关键词（must_have / block_keywords，解析前即可判断）外还支持：

- ``allow``：条件列表，命中任一条才收（列表为空表示不限）；
- ``deny``：条件列表，命中任一条就不收；
- ``min_resolution``：最低清晰度（480p / 720p / 1080p / 2160p）；清晰度未知时转人工确认。

条件与媒体库整理的二级分类写法一致：同一条件内各字段为 AND，单字段逗号分隔的多个值为 OR。
自然语言偏好由 AI 编译成这些规则，用户确认后写入配置；运行时只按规则判断。
"""
from __future__ import annotations

from typing import Any

from .media import resolution_rank

# TMDB 类型 ID → 中文名（一句话生成规则、媒体库整理的分类展示共用）
GENRES = {
    16: "动画", 99: "纪录", 10764: "真人秀", 10767: "脱口秀", 18: "剧情", 35: "喜剧", 80: "犯罪", 9648: "悬疑",
    28: "动作（电影）", 12: "冒险（电影）", 878: "科幻（电影）", 14: "奇幻（电影）", 27: "恐怖（电影）",
    53: "惊悚（电影）", 10749: "爱情", 36: "历史", 10752: "战争（电影）", 10402: "音乐", 10751: "家庭",
    10759: "动作冒险（剧集）", 10765: "科幻奇幻（剧集）", 10768: "战争政治（剧集）", 10762: "儿童（剧集）",
    10766: "肥皂剧", 10763: "新闻", 10770: "电视电影", 37: "西部",
}
# 两位国家/地区代码 → 中文名（通知、分类展示共用）
COUNTRIES = {
    "CN": "中国大陆", "HK": "香港", "TW": "台湾", "JP": "日本", "KR": "韩国", "KP": "朝鲜", "TH": "泰国",
    "IN": "印度", "SG": "新加坡", "US": "美国", "GB": "英国", "UK": "英国", "FR": "法国", "DE": "德国",
    "ES": "西班牙", "IT": "意大利", "NL": "荷兰", "PT": "葡萄牙", "RU": "俄罗斯",
    "CA": "加拿大", "AU": "澳大利亚", "NZ": "新西兰", "IE": "爱尔兰", "BE": "比利时", "SE": "瑞典", "DK": "丹麦",
    "NO": "挪威", "FI": "芬兰", "PL": "波兰", "MX": "墨西哥", "BR": "巴西", "AR": "阿根廷", "TR": "土耳其",
    "MY": "马来西亚", "PH": "菲律宾", "VN": "越南", "ID": "印度尼西亚",
}

def tv_category(genre_ids: list[int] | None, media_type: str | None = None) -> str:
    """剧集类条目的细分：动漫、综艺、纪录片、少儿，其余为电视剧（按 TMDB 题材 ID）。"""
    genres = set(genre_ids or [])
    if media_type == "anime" or 16 in genres:
        return "动漫"
    if genres & {10764, 10767}:
        return "综艺"
    if 99 in genres:
        return "纪录片"
    if 10762 in genres:
        return "少儿"
    return "电视剧"


# TMDB 语言代码（ISO 639-1；cn 是 TMDB 对粤语的写法）→ 中文名
LANGUAGES = {
    "zh": "国语", "cn": "粤语", "en": "英语", "ja": "日语", "ko": "韩语", "th": "泰语", "fr": "法语", "de": "德语",
    "es": "西班牙语", "it": "意大利语", "ru": "俄语", "pt": "葡萄牙语", "hi": "印地语", "ar": "阿拉伯语",
    "tr": "土耳其语", "vi": "越南语", "id": "印尼语", "ms": "马来语", "nl": "荷兰语", "sv": "瑞典语",
}

CONDITION_FIELDS = ("media_type", "genre_ids", "origin_country")
MEDIA_TYPES = {"movie", "tv", "anime"}
# 可选的最低清晰度 → 画质等级（与 core.media 的等级一致）
MIN_RESOLUTIONS = {name: resolution_rank(name) for name in ("480p", "720p", "1080p", "2160p")}


def csv_set(value: str) -> set[str]:
    """条件里逗号分隔的取值，如 "16,10762" → {"16", "10762"}（统一大写）。"""
    return {item.strip().upper() for item in value.split(",") if item.strip()}


def validate_condition(cond: Any, label: str) -> None:
    if not isinstance(cond, dict) or not cond or set(cond) - set(CONDITION_FIELDS):
        raise ValueError(f"{label} 必须是只含 {'/'.join(CONDITION_FIELDS)} 的非空对象")
    for field, value in cond.items():
        if not isinstance(value, str) or not csv_set(value):
            raise ValueError(f"{label}.{field} 必须是逗号分隔的非空字符串")
        values = csv_set(value)
        if field == "media_type" and values - {v.upper() for v in MEDIA_TYPES}:
            raise ValueError(f"{label}.media_type 只能是 movie、tv、anime")
        if field == "genre_ids" and any(not item.isdecimal() for item in values):
            raise ValueError(f"{label}.genre_ids 必须是数字 ID")
        if field == "origin_country" and any(len(item) != 2 or not item.isalpha() for item in values):
            raise ValueError(f"{label}.origin_country 必须是两位国家/地区代码")


def validate_filters(filters: Any, label: str) -> None:
    if not isinstance(filters, dict):
        raise ValueError(f"{label} 必须是对象")
    unknown = set(filters) - {"must_have", "block_keywords", "allow", "deny", "min_resolution"}
    if unknown:
        raise ValueError(f"{label} 包含未知字段：{'、'.join(sorted(unknown))}")
    for key in ("must_have", "block_keywords"):
        values = filters.get(key, [])
        if type(values) is not list or any(type(v) is not str or not v for v in values):
            raise ValueError(f"{label}.{key} 必须是非空字符串数组")
    for key in ("allow", "deny"):
        conds = filters.get(key, [])
        if type(conds) is not list or len(conds) > 50:
            raise ValueError(f"{label}.{key} 必须是不超过 50 条的条件数组")
        for index, cond in enumerate(conds):
            validate_condition(cond, f"{label}.{key}[{index}]")
    minimum = filters.get("min_resolution")
    if minimum is not None and minimum not in MIN_RESOLUTIONS:
        raise ValueError(f"{label}.min_resolution 必须是 {'、'.join(MIN_RESOLUTIONS)} 之一")


def condition_matches(cond: dict, parsed) -> bool:
    for field, value in cond.items():
        wanted = csv_set(value)
        if field == "media_type":
            actual = {str(parsed.media_type or "").upper()}
        elif field == "genre_ids":
            actual = {str(item) for item in parsed.genre_ids}
        else:
            actual = {str(item).upper() for item in parsed.origin_country}
        if not actual & wanted:
            return False
    return True


def check(filters: dict, parsed) -> tuple[str, str] | None:
    """返回 (动作, 原因)：动作为 skip（不收）或 ask（无法判断，转人工）；全部通过返回 None。"""
    allow = filters.get("allow") or []
    if allow and not any(condition_matches(cond, parsed) for cond in allow):
        return "skip", "不在收藏范围（allow）"
    for cond in filters.get("deny") or []:
        if condition_matches(cond, parsed):
            return "skip", "命中排除规则（deny）"
    minimum = filters.get("min_resolution")
    if minimum:
        rank = resolution_rank(parsed.resolution)
        if not rank:
            return "ask", f"清晰度未知，无法确认是否达到 {minimum}"
        if rank < MIN_RESOLUTIONS[minimum]:
            return "skip", f"清晰度低于 {minimum}"
    return None
