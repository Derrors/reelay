"""通知 / 转发 Provider（NotifyProvider）。

- ``identified`` 事件：识别成功后，若配置开启 forward，立即按模板转发到目标；
- ``pending_approval`` 事件：推确认卡片；callback_data 只带 task_id；
- ``saved`` 事件：发送转存完成通知，不重复执行资源转发；
- ``notice(text)``：系统通知（故障告警与恢复、审批汇总、日报），不绑定任务。

未配置真实发送器时写日志，便于 dry-run 与测试观察。
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

        if event == "identified":
            fc = ctx.source_rule.get("forward", self._forward_cfg)
            if fc.get("enabled") and p:
                rendered = _render(fc.get("template", ""), ctx)
                if self._sender:
                    await self._sender(chat=fc.get("target"), text=rendered)
                else:
                    log.info("[识别后转发→%s] %s", fc.get("target"), rendered.replace("\n", " / "))

        elif event == "pending_approval":
            target = (ctx.meta.get("dir_router", {}).get("preview") or ctx.save_dir_alias
                      or ctx.source_rule.get("save_to", "默认"))
            text = f"🎬 {title}  {p.size or ''}\n来源 {ctx.raw.source}\n是否转存到「{target}」？"
            # 关键：按钮 callback_data 只带 task_id
            buttons = [("✅ 转存", f"ok:{ctx.task_id}"), ("🚫 忽略", f"no:{ctx.task_id}")]
            if self._sender:
                await self._sender(text=text, buttons=buttons)
            else:
                log.info("[待确认] %s | 按钮=%s", text.replace("\n", " / "), buttons)

        elif event == "saved":
            if ctx.source_rule.get("approval_mode") == "default_pass":
                return
            files = (ctx.result or {}).get("saved_files")
            text = f"✅ 已转存：{title}\n目录：{ctx.save_dir_alias or '默认'}\n文件：{files or '已落地'}"
            if self._sender:
                await self._sender(text=text)
            else:
                log.info("[已转存] %s → %s（文件 %s）", title, ctx.save_dir_alias, files)

        elif event in ("transfer_uncertain", "organize_failed", "failed"):
            reason = {
                "transfer_uncertain": "转存结果待核验，请在管理台核验转存",
                "organize_failed": "转存已完成，整理失败，请在管理台修正后重新整理",
                "failed": "任务执行失败，请在管理台检查任务轨迹与重试状态",
            }[event]
            text = f"⚠️ {title}\n{reason}"
            if self._sender:
                await self._sender(text=text)
            else:
                log.warning("[%s] %s", event, text.replace("\n", " / "))


    async def notice(self, text: str) -> None:
        if self._sender:
            await self._sender(text=text)
        else:
            log.info("[系统通知] %s", text.replace("\n", " / "))


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
