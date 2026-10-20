"""发给用户的 Telegram 消息（HTML）：待审批卡片、审批后的结果、入库通知与需要处理的提醒。

通知渠道（``providers/notify.py``）与控制 Bot 的按钮回调共用这里的模板：点「转存」「忽略」后卡片原样保留，
只在末尾追加处理结果，不会把整张卡片换成一行字。正文里来自消息与识别结果的内容一律转义。
"""
from __future__ import annotations

import html
import re
from typing import Any

from . import release
from .contracts import PipelineContext
from .media import pairs_label, planned_pairs

# 识别不可靠的原因 → 提示（见 core/confidence.py）
_CAPS = {
    "tmdb_unmatched": "TMDB 没有可信匹配，片名可能不准",
    "parse_fallback": "AI 识别降级，片名与集数请核对",
    "no_title": "没有识别出片名",
}


def esc(value: Any) -> str:
    return html.escape(str(value), quote=False)


def plain(text: str) -> str:
    """去掉 HTML 标签（写日志用）。"""
    return html.unescape(re.sub(r"<[^>]+>", "", text))


def _title(ctx: PipelineContext) -> str:
    p = ctx.parsed
    title = (p.title if p else None) or "（未识别片名）"
    return f"<b>{esc(title)}</b>" + (f" ({p.year})" if p and p.year else "")


def _icon(ctx: PipelineContext) -> str:
    p = ctx.parsed
    return "🎬" if p and p.media_type == "movie" and not p.episodes else "📺"


def _episodes(ctx: PipelineContext) -> str | None:
    """「第 1 季 · E17」「第 1 季 · E01-E23」「S01–S03 共 30 集」；电影或没有集数时返回 None。"""
    pairs = sorted(set(planned_pairs(ctx)))
    if not pairs:
        p = ctx.parsed
        return f"第 {p.season} 季" if p and p.season is not None and p.media_type != "movie" else None
    seasons = {season for season, _ in pairs}
    if len(seasons) > 1:
        return pairs_label(pairs)
    from ..plugins.episode_tracker import format_episodes
    return f"第 {seasons.pop()} 季 · {format_episodes([ep for _, ep in pairs], limit=4)}"


def _quality(ctx: PipelineContext) -> str | None:
    p = ctx.parsed
    if not p:
        return None
    # 打包形式（单集、合集）从季集那一行已经看得出来，不再重复
    tags = release.describe({k: v for k, v in (p.release or {}).items() if k != "packaging"})
    parts = ([p.resolution] if p.resolution else []) + tags[:3]
    return " · ".join(esc(x) for x in parts) or None


def _size(ctx: PipelineContext) -> str | None:
    p = ctx.parsed
    share = ctx.meta.get("share") or {}
    return esc(p.size) if p and p.size else (esc(share["size_text"]) if share.get("size_text") else None)


def _lines(*pairs: tuple[str, str | None]) -> list[str]:
    return [f"{icon} {value}" for icon, value in pairs if value]


def approval_target(ctx: PipelineContext) -> str:
    """不手动改目录时将存入的位置（含二级分类，主进程读到网盘路径后是真实路径）。"""
    return (ctx.meta.get("dir_router", {}).get("preview") or ctx.save_dir_alias
            or ctx.source_rule.get("save_to", "默认"))


def approval_card(ctx: PipelineContext, target: str | None = None) -> str:
    """待审批卡片。target 是将存入的位置（如「/测试/剧集/国产剧集」），省略时按任务推算。"""
    target = target or approval_target(ctx)
    meta = ctx.meta
    lines = [f"{_icon(ctx)} {_title(ctx)}"]
    lines += _lines(("🗂", _episodes(ctx)), ("🎞", _quality(ctx)), ("📦", _size(ctx)))
    lines += ["", *_lines(("📡", f"来源 {esc(ctx.raw.source)}"), ("📂", f"存到 <code>{esc(target)}</code>"))]
    warnings = [_CAPS[cap] for cap in (meta.get("confidence") or {}).get("caps", []) if cap in _CAPS]
    share = meta.get("share") or {}
    if share.get("status") in ("unavailable", "issue") and share.get("note"):
        warnings.append(share["note"])
    if warnings:
        lines += ["", *(f"⚠️ {esc(w)}" for w in warnings)]
    reminders = (meta.get("task") or {}).get("approval_reminders", 0)
    if reminders:
        lines += ["", f"⏰ 第 {reminders} 次提醒：这条资源还在等你确认"]
    return "\n".join(lines)


def approval_result(card: str, outcome: str, state: str | None = None) -> str:
    """点按钮后的卡片：原内容加一行结果。outcome：approved / rejected / stale。"""
    result = {
        "approved": "✅ <b>已批准</b>，正在转存，完成后另行通知",
        "rejected": "🚫 <b>已忽略</b>",
        "stale": f"ℹ️ 这条已经处理过了{f'（当前：{esc(state)}）' if state else ''}",
    }[outcome]
    return f"{card}\n\n{result}"


def _saved_location(ctx: PipelineContext, root: str) -> tuple[str, list[str]]:
    """整理后的目录（多个目录时取共同的上级）与文件名。root 是保存目录在网盘里的路径。"""
    plan = (ctx.meta.get("organize") or {}).get("plan") or []
    dirs = [item["target_dir"].strip("/") for item in plan if item.get("target_dir")]
    names = [item["target_name"] for item in plan if item.get("target_name")]
    if not names:
        names = [str(x) for x in (ctx.result or {}).get("saved_files") or []]
    common = dirs[0].split("/") if dirs else []
    for d in dirs[1:]:
        parts = d.split("/")
        size = 0
        while size < min(len(common), len(parts)) and common[size] == parts[size]:
            size += 1
        common = common[:size]
    return "/".join(filter(None, [root.rstrip("/"), *common])) or root, names


def saved_message(ctx: PipelineContext, root: str) -> str:
    """入库通知。root 是保存目录在网盘里的路径（未知时为目录别名）。"""
    folder, names = _saved_location(ctx, root)
    lines = [f"✅ 已入库 · {_title(ctx)}"]
    lines += _lines(("🗂", _episodes(ctx)), ("🎞", _quality(ctx)), ("📂", f"<code>{esc(folder)}</code>"))
    if len(names) == 1:
        lines.append(f"📄 {esc(names[0])}")
    elif names:
        shown = "、".join(esc(n) for n in names[:2])
        lines.append(f"📄 {len(names)} 个文件：{shown}{' 等' if len(names) > 2 else ''}")
    return "\n".join(lines)


def problem_message(ctx: PipelineContext, reason: str) -> str:
    """转存或整理停下来、需要用户处理的提醒。"""
    return "\n".join([f"⚠️ <b>需要处理</b> · {_title(ctx)}", esc(reason), f"<code>{esc(ctx.task_id)}</code>"])
