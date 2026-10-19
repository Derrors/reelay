"""每日日报 —— on_daily 事件监听（Event 旁路示例）。

内核每天在 schedule.daily_hour 发一次 on_daily，事件快照带过去 24 小时的汇总；
这里只负责排版并通过 ``runtime.services().notify`` 推给所有通知渠道。没有新消息也没有
积压的日子不发送，避免无意义打扰。
"""
from __future__ import annotations

from ..core import runtime
from ..core.contracts import PipelineContext
from ..core.registry import register


def render(summary: dict, date: str) -> str | None:
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
    lines = [f"📊 Reelay 日报 {date}",
             f"过去 24 小时新收 {received} 条，入库 {summary.get('done_count', 0)} 条，过滤 {filtered} 条"]
    for item in summary.get("done", [])[:10]:
        lines.append(f"  ✅ {item.get('title') or item.get('task_id')}")
    if summary.get("done_count", 0) > 10:
        lines.append(f"  … 共 {summary['done_count']} 条")
    if any(backlog.values()):
        lines.append("积压：" + " · ".join(f"{label} {count}" for label, count in backlog.items()))
    return "\n".join(lines)


@register(kind="event", event="on_daily")
class DailyReport:
    name = "daily_report"
    ui_label = "每日日报"
    ui_description = "每天定时（默认 9 点）推送新收、入库与积压概况；没有动静的日子不发送。"
    event = "on_daily"

    async def handle(self, ctx: PipelineContext) -> None:
        system = ctx.meta.get("system", {})
        text = render(system.get("summary") or {}, system.get("date", ""))
        if text:
            runtime.services().notify(text, key="daily_report")
