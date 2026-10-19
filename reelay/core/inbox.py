"""收件箱的版本择优与审批建议。

- 同一集（或同一部电影）有多个待审批版本时，按 ``release`` 偏好用 ``release.compare`` 选出推荐版本；
- 每条待审批附一条建议：事实（已入库哪些集、是否有更好的版本、是否订阅、识别是否可靠）由代码查出，
  建议动作（批准 / 忽略 / 需要你看一下）也由规则决定；AI 只负责把这些事实写成一句通顺的理由，
  不可用时用模板句子。用户接受或推翻建议的记录随裁决样本保存。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from . import ai, release, tmdb
from .contracts import PipelineContext

ACTIONS = {"approve": "建议批准", "reject": "建议忽略", "review": "需要你看一下"}


def version_key(ctx: PipelineContext) -> tuple | None:
    p = ctx.parsed
    if not p or not p.title:
        return None
    show = ("tmdb", p.raw_fields.get("tmdbid")) if p.raw_fields.get("tmdbid") else ("title", tmdb.normalize(p.title))
    if p.media_type == "movie" and not p.episodes:
        return (*show, "movie")
    return (*show, "tv", p.season if p.season is not None else 1, tuple(sorted(p.episodes)))


def candidate(ctx: PipelineContext) -> dict:
    p = ctx.parsed
    return {"resolution": p.resolution, "release": p.release, "size": release.size_bytes(p.size)}


def pick_versions(contexts: list[PipelineContext], prefs: dict) -> dict[str, dict]:
    """task_id → {count, best, best_task_id}；只有一个版本的不出现在结果里。"""
    groups: dict[tuple, list[PipelineContext]] = {}
    for ctx in contexts:
        key = version_key(ctx)
        if key:
            groups.setdefault(key, []).append(ctx)
    result = {}
    for items in groups.values():
        if len(items) < 2:
            continue
        items = sorted(items, key=lambda c: c.raw.received_at)
        best = items[0]
        for ctx in items[1:]:
            if release.compare(candidate(ctx), candidate(best), prefs) > 0:
                best = ctx
        for ctx in items:
            result[ctx.task_id] = {"count": len(items), "best": ctx is best, "best_task_id": best.task_id}
    return result


def _episodes_text(episodes: list[int]) -> str:
    from ..plugins.episode_tracker import format_episodes
    return format_episodes(episodes, limit=4)


def version_label(p) -> str:
    parts = [p.resolution] if p.resolution else []
    parts += release.describe(p.release)[:3]
    return " ".join(parts)


def facts(ctx: PipelineContext, *, stored: dict, versions: dict, subscribed: bool, movie_titles: set) -> dict[str, Any]:
    p = ctx.parsed
    meta = ctx.meta
    season = p.season if p.season is not None else 1
    episodic = p.media_type in ("tv", "anime") or bool(p.episodes)
    have = sorted(stored.get((p.title, season), set())) if episodic and p.title else []
    out: dict[str, Any] = {
        "title": p.title, "media_type": p.media_type, "season": season if episodic else None,
        "episodes": list(p.episodes), "version": version_label(p),
        "confidence": p.confidence, "caps": meta.get("confidence", {}).get("caps", []),
        "fallback": bool(meta.get("parse", {}).get("fallback")), "tmdb_matched": meta.get("tmdb", {}).get("matched"),
        "subscribed": subscribed,
    }
    if episodic:
        out["stored_episodes"] = have
        out["new_episodes"] = [e for e in p.episodes if e not in have]
    else:
        out["in_library"] = tmdb.normalize(p.title) in movie_titles if p.title else False
    version = versions.get(ctx.task_id)
    if version:
        out["versions"] = {"count": version["count"], "best": version["best"]}
    return out


def action(f: dict, threshold: float) -> str:
    if f["fallback"] or "no_title" in f["caps"] or "tmdb_unmatched" in f["caps"] or f["confidence"] < threshold:
        return "review"
    if f.get("episodes") and not f.get("new_episodes"):
        return "reject"
    if f.get("versions") and not f["versions"]["best"]:
        return "reject"
    if f.get("in_library"):
        return "review"
    return "approve"


def rule_text(f: dict, act: str) -> str:
    """不调用 AI 时的建议理由，只陈述事实。"""
    if act == "review":
        if f["fallback"]:
            return "AI 识别降级，片名与集数需要你核对"
        if "tmdb_unmatched" in f["caps"]:
            return "TMDB 没有可信匹配，片名可能不准"
        if "no_title" in f["caps"]:
            return "没有识别出片名"
        if f.get("in_library"):
            return "库里已有这部电影，看看是否要换成这个版本"
        return f"置信度 {f['confidence']:.0%}，低于阈值"
    parts = []
    if f.get("stored_episodes"):
        parts.append(f"已入库 {_episodes_text(f['stored_episodes'])}")
    if act == "reject":
        if f.get("episodes") and not f.get("new_episodes"):
            parts.append(f"{_episodes_text(f['episodes'])} 都已入库")
        else:
            parts.append(f"同一集还有 {f['versions']['count'] - 1} 个版本，另一个更符合偏好")
    else:
        if f.get("new_episodes"):
            parts.append(f"这是 {_episodes_text(f['new_episodes'])}" + (f" 的 {f['version']} 版" if f["version"] else ""))
        else:
            parts.append("库里还没有" + (f"，这是 {f['version']} 版" if f["version"] else ""))
        if f.get("versions"):
            parts.append(f"同一集 {f['versions']['count']} 个版本中最符合偏好")
        if f["subscribed"]:
            parts.append("在你的订阅里")
    return "；".join(parts)


def fingerprint(f: dict, act: str) -> str:
    return hashlib.sha256(json.dumps([f, act], ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]


_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {"items": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "properties": {"id": {"type": "string"}, "text": {"type": "string"}},
        "required": ["id", "text"]}}},
    "required": ["items"],
}
_PROMPT = (
    "你在为影视资源的待审批列表写建议理由。每项给出了代码查出的事实（facts）和已经决定的建议动作（action："
    "approve 批准 / reject 忽略 / review 需要用户看一下）。用一句不超过 40 字的中文写出理由，只依据 facts，"
    "不要编造，不要改变或质疑 action，不要以「建议」开头。集数写成 E01、E01-E03 的形式。"
    "只输出 JSON：{\"items\": [{\"id\": 任务 ID, \"text\": 理由}]}。"
)


async def phrase(client, model: str, entries: list[dict]) -> dict[str, str]:
    """entries: [{id, facts, action}] → {id: 理由}；AI 只措辞，失败抛 AIError。"""
    ids = {entry["id"] for entry in entries}

    def validate(data):
        if not isinstance(data, dict) or set(data) != {"items"} or type(data["items"]) is not list:
            raise ValueError("格式无效")
        out = {}
        for item in data["items"]:
            if not isinstance(item, dict) or item.get("id") not in ids:
                raise ValueError("包含未知任务")
            out[item["id"]] = ai.text_field(item.get("text"), limit=80, label="理由")
        return out

    result, _ = await ai.complete_json(
        client, model=model, purpose="advice",
        messages=[{"role": "system", "content": _PROMPT},
                  {"role": "user", "content": json.dumps(entries, ensure_ascii=False)}],
        schema=_SCHEMA, name="inbox_advice", validate=validate)
    return result
