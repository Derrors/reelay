"""控制 Bot —— 可观测与人工干预（评审 P0-03）。

用你自己的 Telegram Bot（``REELAY_CONTROL_BOT_TOKEN``）作为控制入口，提供：

- 命令：``/status``（队列积压、各状态计数、在线状态）、``/failed``（失败列表+重试）、
  ``/pending``（待确认/待整理）、``/retry <task_id>``
- 审批按钮回调：``ok:<task_id>`` / ``no:<task_id>``（callback_data 只带短 id，评审 P0-01）
- 对外暴露 ``send(...)``：给 NotifyProvider 用来推确认卡片（带按钮）与结果通知

未配置 token（dry-run / 未接入）时，本模块不启动，通知走 ConsoleNotify 打印。
消息只发给 ``REELAY_OWNER_ID``（你自己的数字 id），不广播。
"""

from __future__ import annotations

import logging
import os

from ..core.contracts import TaskState
from ..core.service import ConflictError, ManagementService, NotFoundError

log = logging.getLogger("reelay.control.bot")


class ControlBot:
    def __init__(self, service: ManagementService):
        self.service = service
        self._token = os.getenv("REELAY_CONTROL_BOT_TOKEN")
        self._owner = os.getenv("REELAY_OWNER_ID")
        self._client = None
        self._Button = None

    @property
    def enabled(self) -> bool:
        return bool(self._token and self._owner)

    async def start(self) -> None:
        if not self.enabled:
            log.info("控制 bot 未配置（缺 token/owner），跳过；通知走控制台")
            return
        from telethon import TelegramClient, events, Button  # 延迟导入
        self._Button = Button

        api_id = int(os.getenv("REELAY_TG_API_ID", "0"))
        api_hash = os.getenv("REELAY_TG_API_HASH", "")
        self._client = TelegramClient("data/controlbot.session", api_id, api_hash)

        self._register_handlers(events)
        await self._client.start(bot_token=self._token)
        log.info("控制 bot 已上线")

    def _register_handlers(self, events) -> None:
        c = self._client

        @c.on(events.NewMessage(pattern=r"/status"))
        async def _status(e):
            if not self._is_owner(e):
                return
            counts = self.service.dashboard()["counts"]
            body = "\n".join(f"  {k}: {v}" for k, v in sorted(counts.items())) or "  (空)"
            await e.reply(f"📊 状态计数：\n{body}")

        @c.on(events.NewMessage(pattern=r"/failed"))
        async def _failed(e):
            if not self._is_owner(e):
                return
            rows = self.service.list_tasks(state=TaskState.FAILED.value, limit=20)
            if not rows:
                await e.reply("✅ 无失败任务")
                return
            lines = [f"• `{r['task_id']}` {r['title'] or ''} (retries={r['retries']})\n  {r['error'] or ''}"
                     for r in rows]
            await e.reply("❌ 失败任务：\n" + "\n".join(lines) + "\n\n用 /retry <task_id> 重试")

        @c.on(events.NewMessage(pattern=r"/pending"))
        async def _pending(e):
            if not self._is_owner(e):
                return
            rows = []
            for state in (TaskState.PENDING_APPROVAL, TaskState.NEED_MANUAL):
                rows.extend(self.service.list_tasks(state=state.value, limit=20))
            if not rows:
                await e.reply("✅ 无待处理任务")
                return
            lines = [f"• [{r['state']}] `{r['task_id']}` {r['title'] or ''}" for r in rows]
            await e.reply("⏳ 待处理：\n" + "\n".join(lines))

        @c.on(events.NewMessage(pattern=r"/retry (\w+)"))
        async def _retry(e):
            if not self._is_owner(e):
                return
            tid = e.pattern_match.group(1)
            try:
                await self.service.retry(tid)
                await e.reply("♻️ 已重新入队")
            except (ConflictError, NotFoundError):
                await e.reply("未找到可重试的失败任务")

        @c.on(events.CallbackQuery())
        async def _cb(e):
            if str(e.sender_id) != str(self._owner):
                return
            data = (e.data or b"").decode()
            if data.startswith("ok:"):
                try:
                    await self.service.approve(data[3:])
                    await e.edit("✅ 已确认，加入转存队列")
                except (ConflictError, NotFoundError):
                    await e.edit("任务状态已变化")
            elif data.startswith("no:"):
                try:
                    await self.service.reject(data[3:])
                    await e.edit("🚫 已忽略")
                except (ConflictError, NotFoundError):
                    await e.edit("任务状态已变化")

    def _is_owner(self, event) -> bool:
        return str(getattr(event, "sender_id", "")) == str(self._owner)

    async def send(self, text: str, buttons=None, chat=None) -> None:
        """供 NotifyProvider 调用：把消息发给 owner（或指定 chat）。"""
        if not self._client:
            log.info("[控制bot未启用] %s", text.replace("\n", " / "))
            return
        tg_buttons = None
        if buttons and self._Button:
            tg_buttons = [[self._Button.inline(label, data.encode())] for label, data in buttons]
        await self._client.send_message(chat or int(self._owner), text, buttons=tg_buttons)

    async def run_forever(self) -> None:
        """保持控制 bot 长驻（在 gather 里与监听/worker 并发）。"""
        if self._client:
            await self._client.run_until_disconnected()
