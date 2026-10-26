"""控制 Bot 发出的审批卡片：任务在管理台、对话助手等别处处理后，Bot 据此把卡片改成处理结果。"""
from __future__ import annotations

import time

from ..contracts import TaskState
from .base import _locked


class CardRepository:
    """审批卡片（会话 + 消息 ID → 任务）；只有主进程里的控制 Bot 读写。"""

    def card_add(self, task_id: str, chat: str, message_id: int) -> None:
        with self._lock, self._conn:
            self._conn.execute("INSERT OR REPLACE INTO bot_cards (chat, message_id, task_id, created_at) VALUES (?,?,?,?)",
                               (chat, message_id, task_id, time.time()))

    @_locked
    def cards_settled(self, limit: int = 50) -> list[dict]:
        """任务已经不在待审批（或已删除）的卡片，先发出的在前。"""
        return [dict(row) for row in self._conn.execute(
            """SELECT c.chat, c.message_id, c.task_id FROM bot_cards c LEFT JOIN tasks t ON t.task_id=c.task_id
            WHERE t.state IS NULL OR t.state != ? ORDER BY c.created_at LIMIT ?""",
            (TaskState.PENDING_APPROVAL.value, limit))]

    def card_drop(self, chat: str, message_id: int, *, settled_only: bool = False) -> None:
        """不再跟踪这张卡片；settled_only 时只在它的任务已经不在待审批时才删（点了卡片上的按钮之后）。"""
        with self._lock, self._conn:
            self._conn.execute(
                "DELETE FROM bot_cards WHERE chat=? AND message_id=?" + (
                    " AND NOT EXISTS (SELECT 1 FROM tasks t WHERE t.task_id=bot_cards.task_id AND t.state=?)"
                    if settled_only else ""),
                (chat, message_id, *([TaskState.PENDING_APPROVAL.value] if settled_only else [])))
