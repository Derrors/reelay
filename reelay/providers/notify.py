"""通知 / 转发 Provider（NotifyProvider）。

- ``pending_approval`` 事件：推确认卡片；**callback_data 只带 task_id**（评审 P0-01：
  不塞 URL，避免超 64 字节 & 重启失联）。
- ``saved`` 事件：转存完成通知；若配置开启 forward，则按模板转发到目标。

dry-run 下打印到控制台；真实模式下经控制 bot / userbot 发送（此处留接口）。
"""

from __future__ import annotations

import logging
from typing import Any

from ..core.contracts import PipelineContext
from ..core.registry import register

log = logging.getLogger("reelay.notify")


@register(kind="provider", ptype="notify")
class ConsoleNotify:
    """默认通知：打印到日志。真实渠道（控制 bot / 收藏夹）可再加一个 NotifyProvider。"""
    name = "console"

    def __init__(self) -> None:
        self._sender = None   # 真实模式下注入一个 async send(chat, text, buttons=None)
        self._forward_cfg: dict[str, Any] = {}

    def configure(self, sender=None, forward_cfg: dict | None = None) -> None:
        self._sender = sender
        self._forward_cfg = forward_cfg or {}

    async def notify(self, ctx: PipelineContext, event: str) -> None:
        p = ctx.parsed
        title = (p.title if p else None) or "(未识别标题)"

        if event == "pending_approval":
            text = f"🎬 {title}  {p.size or ''}\n来源 {ctx.raw.source}\n是否转存到「{ctx.save_dir_alias or ctx.source_rule.get('save_to','默认')}」？"
            # 关键：按钮 callback_data 只带 task_id
            buttons = [("✅ 转存", f"ok:{ctx.task_id}"), ("🚫 忽略", f"no:{ctx.task_id}")]
            if self._sender:
                await self._sender(text=text, buttons=buttons)
            else:
                log.info("[待确认] %s | 按钮=%s", text.replace("\n", " / "), buttons)

        elif event == "saved":
            files = (ctx.result or {}).get("saved_files")
            log.info("[已转存] %s → %s（文件 %s）", title, ctx.save_dir_alias, files)
            # 转发（可开关）
            fc = self._forward_cfg
            if fc.get("enabled") and p:
                rendered = _render(fc.get("template", ""), ctx)
                if self._sender:
                    await self._sender(chat=fc.get("target"), text=rendered)
                else:
                    log.info("[转发→%s] %s", fc.get("target"), rendered.replace("\n", " / "))


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
