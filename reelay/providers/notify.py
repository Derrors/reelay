"""通知 / 转发 Provider（NotifyProvider）。

- ``identified`` 事件：识别成功后，若配置开启 forward，立即按模板转发到目标；
- ``pending_approval`` 事件：推确认卡片；callback_data 只带 task_id；
- ``saved`` 事件：发送转存完成通知，不重复执行资源转发；
- ``notice(text, html=False, buttons=None)``：系统通知（故障告警与恢复、审批汇总、日报），不绑定任务；日报与追更是 HTML，
  审批汇总、追更等带控制 Bot 的按钮。

未配置真实发送器时写日志，便于 dry-run 与测试观察。
"""

from __future__ import annotations

import logging
from typing import Any

from ..core import messages, runtime, subscriptions
from ..core.contracts import PipelineContext
from ..core.registry import register

log = logging.getLogger("reelay.notify")


@register(kind="provider", ptype="notify")
class ConsoleNotify:
    """默认通知：打印到日志。真实渠道（控制 bot / 收藏夹）可再加一个 NotifyProvider。"""
    name = "console"

    def __init__(self) -> None:
        self._sender = None   # 真实模式下注入一个 async send(text, buttons=None, chat=None, html=False, photo=None)
        self._forward_cfg: dict[str, Any] = {}

    def configure(self, sender=None, forward_cfg: dict | None = None) -> None:
        self._sender = sender
        self._forward_cfg = forward_cfg or {}

    async def _send(self, text: str, buttons=None, photo: str | None = None) -> None:
        if self._sender:
            await self._sender(text=text, buttons=buttons, html=True, photo=photo)
        else:
            log.info("[通知] %s%s", messages.plain(text).replace("\n", " / "), f" | 按钮={buttons}" if buttons else "")

    async def notify(self, ctx: PipelineContext, event: str) -> None:
        p = ctx.parsed

        if event == "identified":
            fc = ctx.source_rule.get("forward", self._forward_cfg)
            if fc.get("enabled") and p:
                rendered = _render(fc.get("template", ""), ctx)
                if self._sender:
                    await self._sender(chat=fc.get("target"), text=rendered)
                else:
                    log.info("[识别后转发→%s] %s", fc.get("target"), rendered.replace("\n", " / "))

        elif event == "pending_approval":
            # 关键：按钮 callback_data 只带 task_id
            subscribed = subscriptions.subscribed(_subscriptions(), ctx)
            await self._send(messages.approval_card(ctx), messages.approval_buttons(ctx, subscribed=subscribed),
                             photo=messages.card_image_url(ctx))

        elif event == "saved":
            await self._send(messages.saved_message(ctx, _drive_path(ctx)), messages.link_buttons(ctx),
                             photo=messages.card_image_url(ctx))

        elif event in ("transfer_uncertain", "organize_failed", "failed"):
            # 处理办法放在按钮上（核验转存、按已到的继续、重新整理、删除转存文件 / 覆盖保存、重试）
            reason, details, hint = messages.problem_reason(ctx, event)
            await self._send(messages.problem_message(ctx, reason, details=details, hint=hint),
                             messages.problem_buttons(ctx), photo=messages.card_image_url(ctx))


    async def notice(self, text: str, html: bool = False, buttons: list | None = None) -> None:
        if self._sender:
            await self._sender(text=text, **({"html": True} if html else {}), **({"buttons": buttons} if buttons else {}))
        else:
            log.info("[系统通知] %s", (messages.plain(text) if html else text).replace("\n", " / "))


def _subscriptions() -> list[dict]:
    try:
        return runtime.services().subscriptions()
    except RuntimeError:  # 不在管线作用域里（如测试直接调用）
        return []


def _drive_path(ctx: PipelineContext) -> str:
    """保存目录在网盘里的路径（Worker 读到后记录）；不知道或目录 CID 已改时用目录别名。"""
    alias = ctx.save_dir_alias or "默认"
    try:
        saved = runtime.services().store.state_get("kernel", "save_dir_paths", {}) or {}
    except RuntimeError:  # 不在管线作用域里（如测试直接调用）
        return alias
    entry = saved.get(alias) or {}
    return entry["path"] if entry.get("path") and entry.get("cid") == str(ctx.save_dir_cid) else alias


def _render(tpl: str, ctx: PipelineContext) -> str:
    p = ctx.parsed
    data = {
        "title": (p.title if p else "") or "",
        "year": (p.year if p else "") or "",
        "resolution": (p.resolution if p else "") or "",
        "size": (p.size if p else "") or "",
        "link": (p.raw_fields.get("share_url") if p else "") or "",
    }
    try:
        return tpl.format(**data)
    except Exception:
        return tpl
