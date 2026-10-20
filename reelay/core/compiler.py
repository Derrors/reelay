"""把一句话的偏好编译成确定性配置：AI 起草，配置校验，用户确认后才写入。

AI 给出某个范围（默认规则或某个来源）完整的新过滤规则，以及需要时的新二级分类与版本偏好；
代码把它套到当前配置的副本上，按正式配置的校验规则校验（不过时把错误交回 AI 重写一次），
再逐项算出改动清单。管理台展示改动，用户确认后经配置接口（带版本号）写入。运行时只按规则判断，
不再为每条消息调用 AI。
"""
from __future__ import annotations

import copy
import json
from typing import Any

from . import ai, release
from .category_strategy import _csv
from .config import config_revision, normalize_config
from .rules import CONDITION_FIELDS, MIN_RESOLUTIONS

# 常用 TMDB 类型 ID，供 AI 写规则时查表
GENRES = {
    16: "动画", 99: "纪录", 10764: "真人秀", 10767: "脱口秀", 18: "剧情", 35: "喜剧", 80: "犯罪", 9648: "悬疑",
    28: "动作（电影）", 12: "冒险（电影）", 878: "科幻（电影）", 14: "奇幻（电影）", 27: "恐怖（电影）",
    53: "惊悚（电影）", 10749: "爱情", 36: "历史", 10752: "战争（电影）", 10402: "音乐", 10751: "家庭",
    10759: "动作冒险（剧集）", 10765: "科幻奇幻（剧集）", 10768: "战争政治（剧集）", 10762: "儿童（剧集）",
    10766: "肥皂剧", 10763: "新闻", 10770: "电视电影", 37: "西部",
}

_NULLABLE_STR = {"type": ["string", "null"]}
_CONDITION = {"type": "object", "additionalProperties": False,
              "properties": {field: _NULLABLE_STR for field in CONDITION_FIELDS},
              "required": list(CONDITION_FIELDS)}
_RELEASE_PREFS = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "prefer_subtitles": {"type": "array", "items": {"type": "string", "enum": list(release.SUBTITLE_LANGS)}},
        "prefer_audio": {"type": "array", "items": {"type": "string", "enum": list(release.AUDIO_LANGS)}},
        "hdr": {"type": "string", "enum": list(release.HDR_PREFERENCES)},
        "prefer_sources": {"type": "array", "items": {"type": "string", "enum": list(release.SOURCES)}},
    },
    "required": ["prefer_subtitles", "prefer_audio", "hdr", "prefer_sources"],
}
SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "filters": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "must_have": {"type": "array", "items": {"type": "string"}},
                "block_keywords": {"type": "array", "items": {"type": "string"}},
                "allow": {"type": "array", "items": _CONDITION},
                "deny": {"type": "array", "items": _CONDITION},
                "min_resolution": {"type": ["string", "null"], "enum": [*MIN_RESOLUTIONS, None]},
            },
            "required": ["must_have", "block_keywords", "allow", "deny", "min_resolution"],
        },
        "categories": {"anyOf": [{"type": "null"}, {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {"media": {"type": "string", "enum": ["movie", "tv"]}, "path": {"type": "string"},
                           "genre_ids": _NULLABLE_STR, "origin_country": _NULLABLE_STR},
            "required": ["media", "path", "genre_ids", "origin_country"]}}]},
        "release": {"anyOf": [{"type": "null"}, _RELEASE_PREFS]},
        "summary": {"type": "string"},
        "unsupported": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["filters", "categories", "release", "summary", "unsupported"],
}

_PROMPT = (
    "你把用户用一句话描述的收藏偏好改写成 Reelay 的确定性规则。current 是当前规则，request 是用户的要求。"
    "规则说明：filters.must_have 消息必须包含其一的关键词；filters.block_keywords 消息含有任一就不收；"
    "filters.allow 条件列表，命中任一条才收（空表示不限）；filters.deny 命中任一条就不收；"
    "条件字段 media_type（movie/tv/anime）、genre_ids（TMDB 类型 ID）、origin_country（两位大写国家/地区代码），"
    "同一条件内各字段同时满足，字段值用英文逗号分隔表示「或」，不需要的字段填 null；"
    "filters.min_resolution 最低清晰度（480p/720p/1080p/2160p 或 null）。"
    "categories 是二级分类（媒体类型 movie/tv 下按顺序匹配的「一级/二级」路径，条件为空的一条是兜底且必须放在该类型最后），"
    "不需要改分类时填 null；需要改时给出完整的新列表。"
    "release 是同一集多个版本时的偏好（prefer_subtitles 字幕语言 chs/cht/eng/jpn/kor、prefer_audio 音轨 "
    "mandarin/cantonese/english/japanese/korean、hdr any/prefer/avoid、prefer_sources 片源，列表顺序即优先级），"
    "不需要改时填 null。filters 必须给出改动后的完整内容：保留与要求无关的现有规则，只改用户提到的部分。"
    "无法用这些规则表达的要求写进 unsupported。summary 用一句话说明改了什么。只输出 JSON。"
)


def _clean_condition(cond: dict) -> dict:
    return {field: ",".join(sorted(_csv(value))) if field != "media_type" else ",".join(
        sorted(item.lower() for item in _csv(value)))
            for field, value in cond.items() if value is not None and str(value).strip()}


def validate(data: Any) -> dict:
    if not isinstance(data, dict) or set(data) != set(SCHEMA["properties"]):
        raise ValueError("规则草案字段缺失或包含未知字段")
    filters = data["filters"]
    if not isinstance(filters, dict) or set(filters) != set(SCHEMA["properties"]["filters"]["properties"]):
        raise ValueError("filters 字段不完整")
    out_filters: dict[str, Any] = {
        "must_have": ai.string_list(filters["must_have"], label="must_have") if filters["must_have"] else [],
        "block_keywords": (ai.string_list(filters["block_keywords"], limit=200, label="block_keywords")
                           if filters["block_keywords"] else []),
    }
    for key in ("allow", "deny"):
        if type(filters[key]) is not list:
            raise ValueError(f"{key} 必须是数组")
        conds = [_clean_condition(item) for item in filters[key] if isinstance(item, dict)]
        out_filters[key] = [cond for cond in conds if cond]
    out_filters["min_resolution"] = filters["min_resolution"]
    categories = None
    if data["categories"] is not None:
        if type(data["categories"]) is not list:
            raise ValueError("categories 必须是数组或 null")
        categories = {}
        for item in data["categories"]:
            if not isinstance(item, dict) or item.get("media") not in ("movie", "tv") or type(item.get("path")) is not str:
                raise ValueError("categories 项格式无效")
            path = item["path"].strip()
            if path in categories.setdefault(item["media"], {}):
                raise ValueError(f"分类路径重复：{path}")
            categories[item["media"]][path] = {
                key: ",".join(item[key].split(",")).replace(" ", "") for key in ("genre_ids", "origin_country")
                if type(item.get(key)) is str and item[key].strip()}
    prefs = data["release"]
    if prefs is not None:
        release.validate_preferences(prefs)
    return {"filters": out_filters, "categories": categories, "release": prefs,
            "summary": ai.text_field(data["summary"], limit=500, label="summary"),
            "unsupported": ai.string_list(data["unsupported"], limit=20, item_limit=300, label="unsupported")
            if data["unsupported"] else []}


def _scope_filters(cfg: dict, scope: str) -> tuple[dict | None, dict]:
    """返回 (来源条目或 None, 该范围当前生效的完整过滤规则)。"""
    base = {"must_have": [], "block_keywords": [], "allow": [], "deny": [], "min_resolution": None}
    defaults = {**base, **copy.deepcopy(cfg["defaults"].get("filters") or {})}
    if scope == "defaults":
        return None, defaults
    for item in cfg["sources"]:
        if item["bot"].lower() == scope.lower():
            return item, {**defaults, **copy.deepcopy(item.get("filters") or {})}
    raise ValueError(f"来源不存在：{scope}")


def apply(cfg: dict, scope: str, proposal: dict) -> dict:
    candidate = copy.deepcopy(cfg)
    entry, _ = _scope_filters(candidate, scope)
    if entry is None:
        candidate["defaults"]["filters"] = {key: value for key, value in proposal["filters"].items()
                                            if value not in (None, [])}
    else:
        # 来源的过滤规则逐项覆盖默认规则：空列表与 null 也要写明，否则会继承回默认值。
        entry["filters"] = dict(proposal["filters"])
    if proposal["categories"] is not None:
        candidate["plugins"]["dir_router"]["categories"] = proposal["categories"]
    if proposal["release"] is not None:
        candidate["release"] = proposal["release"]
    return candidate


def _condition_text(cond: dict) -> str:
    labels = {"media_type": "类型", "genre_ids": "类型 ID", "origin_country": "地区"}
    parts = []
    for field, value in cond.items():
        if field == "genre_ids":
            value = "、".join(f"{GENRES.get(int(v), v)}({v})" if v.isdecimal() else v for v in value.split(","))
        parts.append(f"{labels[field]} {value}")
    return " 且 ".join(parts) or "（空条件）"


def diff(cfg: dict, candidate: dict, scope: str) -> list[dict]:
    """逐项列出改动：{section, kind: add/remove/change, text}。"""
    label = "默认规则" if scope == "defaults" else f"来源 {scope}"
    changes: list[dict] = []
    _, before = _scope_filters(cfg, scope)
    _, after = _scope_filters(candidate, scope)
    for key, name in (("must_have", "必须包含"), ("block_keywords", "屏蔽关键词")):
        for word in after[key]:
            if word not in before[key]:
                changes.append({"section": label, "kind": "add", "text": f"{name}：{word}"})
        for word in before[key]:
            if word not in after[key]:
                changes.append({"section": label, "kind": "remove", "text": f"{name}：{word}"})
    for key, name in (("allow", "只收"), ("deny", "不收")):
        old = [_clean_condition(cond) for cond in before[key]]
        new = [_clean_condition(cond) for cond in after[key]]
        for cond in new:
            if cond not in old:
                changes.append({"section": label, "kind": "add", "text": f"{name}：{_condition_text(cond)}"})
        for cond in old:
            if cond not in new:
                changes.append({"section": label, "kind": "remove", "text": f"{name}：{_condition_text(cond)}"})
    if before["min_resolution"] != after["min_resolution"]:
        changes.append({"section": label, "kind": "change",
                        "text": f"最低清晰度：{before['min_resolution'] or '不限'} → {after['min_resolution'] or '不限'}"})
    old_cats, new_cats = cfg["plugins"]["dir_router"]["categories"], candidate["plugins"]["dir_router"]["categories"]
    for media in ("movie", "tv"):
        name = "电影分类" if media == "movie" else "剧集分类"
        old, new = old_cats.get(media, {}), new_cats.get(media, {})
        for path, cond in new.items():
            if path not in old:
                changes.append({"section": name, "kind": "add", "text": f"{path}：{_condition_text(cond)}"})
            elif _clean_condition(old[path]) != _clean_condition(cond):
                changes.append({"section": name, "kind": "change",
                                "text": f"{path}：{_condition_text(old[path])} → {_condition_text(cond)}"})
        for path in old:
            if path not in new:
                changes.append({"section": name, "kind": "remove", "text": path})
        if [p for p in new if p in old] != [p for p in old if p in new]:
            changes.append({"section": name, "kind": "change", "text": "调整了匹配顺序"})
    for key, name in (("prefer_subtitles", "优先字幕"), ("prefer_audio", "优先音轨"), ("hdr", "HDR"),
                      ("prefer_sources", "优先片源")):
        old, new = cfg["release"].get(key), candidate["release"].get(key)
        if old != new:
            show = (lambda v: "、".join(release.LABELS.get(x, x) for x in v) or "不限") if isinstance(new, list) else (
                lambda v: {"any": "不限", "prefer": "优先 HDR", "avoid": "避开 HDR"}.get(v, v))
            changes.append({"section": "版本偏好", "kind": "change", "text": f"{name}：{show(old)} → {show(new)}"})
    return changes


async def compile_rules(client, model: str, *, cfg: dict, scope: str, request: str, check) -> dict:
    """返回 {summary, unsupported, changes, sections, revision}；check(candidate) 做完整配置校验。"""
    request = ai.text_field(request, limit=1000, label="偏好描述")
    if not request:
        raise ValueError("请用一句话描述偏好")
    _, current = _scope_filters(cfg, scope)
    categories = [{"media": media, "path": path, "genre_ids": cond.get("genre_ids"),
                   "origin_country": cond.get("origin_country")}
                  for media, items in cfg["plugins"]["dir_router"]["categories"].items() for path, cond in items.items()]
    payload = {"request": request, "scope": scope,
               "current": {"filters": current, "categories": categories, "release": cfg["release"]},
               "genres": GENRES}
    messages = [{"role": "system", "content": _PROMPT},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]
    for attempt in range(2):
        # 要写出整套过滤与分类规则，输出长：实测一句三条偏好要 80 秒
        proposal, _ = await ai.complete_json(client, model=model, purpose="compile", messages=messages,
                                             schema=SCHEMA, name="rule_draft", validate=validate, timeout=150)
        candidate = apply(cfg, scope, proposal)
        try:
            candidate = normalize_config(candidate)
            check(candidate)
            break
        except ValueError as exc:
            if attempt:
                raise ValueError(f"AI 起草的规则未通过配置校验：{exc}") from None
            messages += [{"role": "assistant", "content": json.dumps(proposal, ensure_ascii=False)},
                         {"role": "user", "content": f"上面的规则未通过配置校验：{exc}。请修正后重新输出完整 JSON。"}]
    sections: dict[str, Any] = {"defaults" if scope == "defaults" else "sources":
                                candidate["defaults"] if scope == "defaults" else candidate["sources"]}
    if candidate["plugins"]["dir_router"] != cfg["plugins"]["dir_router"]:
        sections["plugins"] = candidate["plugins"]
    if candidate["release"] != cfg["release"]:
        sections["release"] = candidate["release"]
    return {"summary": proposal["summary"], "unsupported": proposal["unsupported"],
            "changes": diff(cfg, candidate, scope), "sections": sections, "revision": config_revision(cfg)}
