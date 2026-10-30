"""事务 Outbox：通知与事件与任务状态同一事务写入，投递器独立重试。"""
from __future__ import annotations

import json
import time

from ..contracts import PipelineContext
from .base import SYSTEM_TASK, _dump_ctx, _locked

MAX_ATTEMPTS = 8  # 一般投递最多尝试几次（退避到 5 分钟，约 7 分钟内放弃）
# 告警与恢复通知多半在 Telegram 断开时产生，那时 Bot 也发不出去：不按次数放弃，24 小时内每分钟重试，恢复后补发
URGENT_EVENTS = ("alert", "recovered")
URGENT_RETRY_SEC = 60
URGENT_WINDOW_SEC = 24 * 3600


class OutboxRepository:
    """通知、事件与系统通知的投递记录。"""

    def claim_deliveries(self, limit: int = 20) -> list[dict]:
        """有限批量租约；同一任务/接收器按事件生成顺序投递。"""
        now = time.time()
        with self._lock, self._conn:
            self._conn.execute("UPDATE outbox SET status='pending' WHERE status='delivering' AND lease_until<=?", (now,))
            rows = self._conn.execute(
                """SELECT o.* FROM outbox o WHERE status='pending' AND next_attempt<=?
                AND NOT EXISTS (SELECT 1 FROM outbox prev WHERE prev.task_id=o.task_id
                    AND prev.kind=o.kind AND prev.target=o.target AND prev.id<o.id
                    AND prev.status IN ('pending','delivering')) ORDER BY id LIMIT ?""", (now, limit)
            ).fetchall()
            claimed = []
            for row in rows:
                cur = self._conn.execute("UPDATE outbox SET status='delivering', lease_until=?, updated_at=? WHERE id=? AND status='pending'",
                                         (now + 60, now, row["id"]))
                if cur.rowcount:
                    claimed.append(dict(row))
            return claimed

    def finish_delivery(self, delivery_id: int, *, error: str | None = None,
                        retry_after: float | None = None, skipped: bool = False) -> None:
        with self._lock, self._conn:
            row = self._conn.execute("SELECT attempts, kind, event, created_at FROM outbox WHERE id=?",
                                     (delivery_id,)).fetchone()
            if not row:
                return
            attempts = row["attempts"] + (0 if skipped else 1)
            now = time.time()
            urgent = row["kind"] == "notice" and row["event"] in URGENT_EVENTS
            if urgent:
                give_up = now - row["created_at"] >= URGENT_WINDOW_SEC
                backoff = min(2 ** attempts, URGENT_RETRY_SEC)
            else:
                give_up, backoff = attempts >= MAX_ATTEMPTS, min(2 ** attempts, 300)
            status = "skipped" if skipped else ("done" if error is None else ("failed" if give_up else "pending"))
            delay = retry_after if retry_after is not None else backoff
            self._conn.execute("UPDATE outbox SET status=?, attempts=?, error=?, next_attempt=?, lease_until=0, updated_at=? WHERE id=?",
                               (status, attempts, error, now + delay, now, delivery_id))

    @_locked
    def delivery_counts(self) -> dict[str, int]:
        return {r["status"]: r["n"] for r in self._conn.execute("SELECT status, COUNT(*) n FROM outbox GROUP BY status")}

    @_locked
    def list_deliveries(self, *, status: str = "failed", limit: int = 100) -> list[dict]:
        if status not in {"pending", "delivering", "failed", "done", "skipped"}:
            raise ValueError("投递状态无效")
        return [dict(row) for row in self._conn.execute(
            """SELECT id, task_id, kind, event, target, status, attempts, error,
            next_attempt, updated_at FROM outbox WHERE status=? ORDER BY id DESC LIMIT ?""", (status, limit))]

    def prune_deliveries(self, *, done_before: float, failed_before: float) -> int:
        """清理已投递或已跳过、以及很早就投递失败的记录（每行带一份完整快照，不清理会一直变大）。"""
        with self._lock, self._conn:
            return self._conn.execute(
                "DELETE FROM outbox WHERE (status IN ('done','skipped') AND updated_at<?) "
                "OR (status='failed' AND updated_at<?)", (done_before, failed_before)).rowcount

    def retry_delivery(self, delivery_id: int) -> bool:
        with self._lock, self._conn:
            cur = self._conn.execute("UPDATE outbox SET status='pending', attempts=0, next_attempt=0, error=NULL WHERE id=? AND status='failed'", (delivery_id,))
            return bool(cur.rowcount)

    def _stage_notice(self, key: str, text: str, targets: list[str], event: str, html: bool = False,
                      buttons: list | None = None) -> None:
        now = time.time()
        snapshot = json.dumps({"key": key, "text": text, **({"html": True} if html else {}),
                               **({"buttons": buttons} if buttons else {})}, ensure_ascii=False)
        for target in targets:
            self._conn.execute(
                """INSERT INTO outbox (delivery_key, task_id, kind, event, target, snapshot, created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?)""",
                (f"notice:{key}:{target}:{time.time_ns()}", SYSTEM_TASK, "notice", event,
                 target, snapshot, now, now),
            )

    def stage_notice(self, key: str, text: str, targets: list[str], *, event: str = "notice",
                     html: bool = False, buttons: list | None = None) -> None:
        """html=True 时 text 是 Telegram HTML（见 core/messages.py），不支持 HTML 的渠道收到去掉标签的文字。
        buttons 是控制 Bot 的按钮行（见 core/messages.py 的 Buttons），不支持按钮的渠道只收到正文。"""
        with self._lock, self._conn:
            self._stage_notice(key, text, targets, event, html, buttons)

    def stage_system_event(self, ctx: PipelineContext) -> None:
        """系统事件（如日报）只写投递意图，不在任务表里出现。"""
        snapshot = json.dumps(_dump_ctx(ctx), ensure_ascii=False)
        now = time.time()
        with self._lock, self._conn:
            for key, delivery in ctx.meta.get("deliveries", {}).items():
                self._conn.execute(
                    """INSERT OR IGNORE INTO outbox
                    (delivery_key, task_id, kind, event, target, snapshot, created_at, updated_at)
                    VALUES (?,?,?,?,?,?,?,?)""",
                    (f"{ctx.task_id}:{key}", ctx.task_id, delivery["kind"], delivery["event"],
                     delivery["target"], snapshot, now, now))
