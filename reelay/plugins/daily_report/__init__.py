"""每日日报 —— on_daily 事件监听（Event 旁路示例）。

内核每天在 schedule.daily_hour 发一次 on_daily，事件快照带过去 24 小时的汇总；
这里只负责排版（Telegram HTML）并通过 ``runtime.services().notify`` 推给所有通知渠道。没有新消息也没有
积压的日子不发送，避免无意义打扰。

配置了 AI 且 ``plugins.daily_report.narrative`` 未关闭时，标题下用引用块加一段 AI 按事实写的话（如「本周新收 3 部剧，
《X》E07 已经播出 3 天还没出现，可能需要换个来源」），下面仍附原来的数字；事实全部由代码统计，
AI 调用失败时只发数字版。
"""
from __future__ import annotations

import asyncio
import json
import time

from ...core import ai, runtime
from ...core.media import pairs_label
from ...core.messages import esc, type_icon
from ..episode_tracker import format_episodes
from ...core.contracts import PipelineContext
from ...core.registry import register

_PROMPT = (
    "你在为影视资源自动转存工具写每日简报的开头。facts 是代码统计的事实：最近 24 小时与最近 7 天新收的作品、"
    "入库与积压数量、追更巡检发现的缺集（overdue 是 TMDB 已播出多天仍未入库的集）。"
    "last_24h 与 backlog 的数字是消息条数（一条消息是一个任务，同一部剧常有好几条），要说数量时用「条」，"
    "不要说成「部」；last_7d.titles、tv、movie 才是作品部数。"
    "用 2 到 3 句、不超过 120 字的中文写出最值得注意的情况，像同事给你的简短提醒；只依据 facts，不要编造，"
    "不要罗列所有数字，没有异常时简单说一切正常。缺集可以提醒「可能需要换个来源」。片名用《》。"
    "只输出 JSON：{\"text\": 简报}。"
)


def render(summary: dict, date: str, story: str | None = None) -> str | None:
    """日报正文（Telegram HTML）：标题、AI 提醒（引用块）、24 小时数字、入库片名、积压，各段之间空一行。"""
    received = summary.get("received", 0)
    backlog = {
        "待审批": summary.get("pending_approval", 0),
        "待人工": summary.get("need_manual", 0),
        "失败": summary.get("failed", 0),
    }
    if not received and not any(backlog.values()):
        return None
    by_state = summary.get("received_by_state", {})
    filtered = by_state.get("rejected", 0) + by_state.get("skipped", 0)
    done_count = summary.get("done_count", 0)
    blocks = [f"📊 <b>Reelay 日报</b> · {esc(date)}"]
    if story:
        blocks.append(f"<blockquote>{esc(story)}</blockquote>")
    blocks.append(f"📥 <b>过去 24 小时</b>\n新收 {received} 条 · 入库 {done_count} 条 · 过滤 {filtered} 条")
    # 同一部剧常有好几条，按片名合并，列出这次入库的集
    titles: dict[str, dict] = {}
    for item in summary.get("done", []):
        group = titles.setdefault(item.get("title") or item.get("task_id"),
                                  {"media_type": item.get("media_type"), "pairs": set(), "count": 0})
        group["pairs"].update(tuple(pair) for pair in item.get("pairs") or [])
        group["count"] += 1
    if titles:
        lines = [_done_line(title, group) for title, group in list(titles.items())[:10]]
        if len(titles) > 10 or done_count > sum(group["count"] for group in titles.values()):
            lines.append(f"… 共 {done_count} 条")
        blocks.append("✅ <b>已入库</b>\n" + "\n".join(lines))
    if any(backlog.values()):
        blocks.append("📌 <b>待处理</b>\n" + " · ".join(f"{label} {count}" for label, count in backlog.items() if count))
    return "\n\n".join(blocks)


def _done_line(title: str, group: dict) -> str:
    """「📺 小城良方 · S01 E09-E10」「🎬 空枪 ×2」；跨多季时写「S01–S03 共 30 集」。"""
    pairs = sorted(group["pairs"])
    line = f"{type_icon(group['media_type'], bool(pairs))} {esc(title)}"
    seasons = {season for season, _ in pairs}
    if len(seasons) == 1:
        return f"{line} · S{seasons.pop():02d} {format_episodes([ep for _, ep in pairs], limit=4)}"
    if seasons:
        return f"{line} · {pairs_label(pairs)}"
    return line + (f" ×{group['count']}" if group["count"] > 1 else "")


def facts(summary: dict, week: list[dict], tracker: dict) -> dict:
    series = tracker.get("series") or {}
    return {
        "last_24h": {"received": summary.get("received", 0), "done": summary.get("done_count", 0),
                     "done_titles": [item.get("title") for item in summary.get("done", [])[:10] if item.get("title")]},
        "backlog": {"pending_approval": summary.get("pending_approval", 0), "need_manual": summary.get("need_manual", 0),
                    "failed": summary.get("failed", 0)},
        "last_7d": {"titles": len(week), "tv": sum(1 for w in week if w["media_type"] in ("tv", "anime")),
                    "movie": sum(1 for w in week if w["media_type"] == "movie"),
                    "top": [{"title": w["title"], "items": w["count"], "done": w["done"]} for w in week[:8]]},
        "missing": [{"title": key.rpartition("|S")[0], "season": int(key.rpartition("|S")[2]),
                     "missing": info.get("missing", [])[:10], "overdue": info.get("overdue", [])[:3]}
                    for key, info in list(series.items())[:10]],
    }


@register(kind="event", event="on_daily")
class DailyReport(ai.UsesAI):
    name = "daily_report"
    ui_label = "每日日报"
    ui_description = "每天定时（默认 9 点）推送新收、入库与积压概况；配置了 AI 时开头附一段 AI 按事实写的提醒。没有动静的日子不发送。"
    ui_fields = [
        {'path': 'narrative', 'label': '开头附 AI 写的一段提醒', 'type': 'boolean', 'default': True},
    ]
    event = "on_daily"

    async def handle(self, ctx: PipelineContext) -> None:
        system = ctx.meta.get("system", {})
        summary = system.get("summary") or {}
        date = system.get("date", "")
        if not render(summary, date):
            return
        story = None
        if self._client is not None and runtime.plugin_config(self.name).get("narrative", True):
            story = await self._narrate(summary, date)
        runtime.services().notify(render(summary, date, story), key="daily_report", html=True)

    async def _narrate(self, summary: dict, date: str) -> str | None:
        store = runtime.services().store
        week = store.titles_since(time.time() - 7 * 86400)
        tracker = store.state_get("episode_tracker", "last_report") or {}
        if tracker.get("date", "") < time.strftime("%Y-%m-%d", time.localtime(time.time() - 86400)):
            tracker = {}  # 追更巡检与日报同时触发，最多用前一天的巡检结果

        def validate(data):
            if not isinstance(data, dict) or set(data) != {"text"}:
                raise ValueError("格式无效")
            value = ai.text_field(data["text"], limit=300, label="简报")
            if not value:
                raise ValueError("简报为空")
            return value

        try:
            story, _ = await asyncio.wait_for(ai.complete_json(
                self._client, model=self._model, purpose="daily", invalid_retries=0, timeout=12,
                schema={"type": "object", "additionalProperties": False,
                        "properties": {"text": {"type": "string"}}, "required": ["text"]},
                name="daily_story", validate=validate,
                messages=[{"role": "system", "content": _PROMPT},
                          {"role": "user", "content": json.dumps({"date": date, "facts": facts(summary, week, tracker)},
                                                                 ensure_ascii=False)}]), timeout=20)
        except (ai.AIError, asyncio.TimeoutError):
            return None
        return story
