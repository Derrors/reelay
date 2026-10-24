"""追剧订阅与「不再接收」的作品：用户在管理台、控制 Bot 与 MCP 里维护的数据，不在配置文件里。

身份键（identity）由 ``core/subscriptions.py`` 与 ``core/ignored.py`` 计算，同一部作品只有一条。
"""
from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

from .base import _locked


def _subscription(row: sqlite3.Row) -> dict[str, Any]:
    return {"id": row["id"], "title": row["title"], "season": row["season"], "total": row["total"],
            "tmdbid": row["tmdbid"], "auto": bool(row["auto"]),
            "finished": json.loads(row["finished"]) if row["finished"] else None}


def _work(row: sqlite3.Row) -> dict[str, Any]:
    return {"id": row["id"], "title": row["title"], "tmdbid": row["tmdbid"], "kind": row["kind"]}


class FollowRepository:
    """订阅与不再接收的作品。"""

    # -- 订阅 ------------------------------------------------------------- #
    @_locked
    def subscriptions(self) -> list[dict[str, Any]]:
        return [_subscription(r) for r in self._conn.execute("SELECT * FROM subscriptions ORDER BY id")]

    @_locked
    def subscription(self, subscription_id: int) -> dict[str, Any] | None:
        row = self._conn.execute("SELECT * FROM subscriptions WHERE id=?", (subscription_id,)).fetchone()
        return _subscription(row) if row else None

    def add_subscription(self, identity: str, item: dict[str, Any]) -> dict[str, Any] | None:
        """新增订阅；同一部剧同一季已经订阅过时返回 None。"""
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO subscriptions (identity, title, season, total, tmdbid, auto, created_at) "
                "VALUES (?,?,?,?,?,?,?)", (identity, item["title"], item["season"], item.get("total"),
                                           item.get("tmdbid"), 1 if item.get("auto", True) else 0, time.time()))
            if not cur.rowcount:
                return None
            return _subscription(self._conn.execute("SELECT * FROM subscriptions WHERE id=?",
                                                    (cur.lastrowid,)).fetchone())

    def remove_subscription(self, subscription_id: int) -> bool:
        with self._lock, self._conn:
            return bool(self._conn.execute("DELETE FROM subscriptions WHERE id=?", (subscription_id,)).rowcount)

    def set_subscription_finished(self, subscription_id: int, record: dict[str, Any] | None) -> bool:
        """标记一季已播完且全部入库（record 为 {"date", "episodes"}），或传 None 继续订阅。返回是否有变化。"""
        with self._lock, self._conn:
            return bool(self._conn.execute(
                "UPDATE subscriptions SET finished=? WHERE id=? AND finished IS NOT ?",
                (json.dumps(record, ensure_ascii=False) if record else None, subscription_id,
                 json.dumps(record, ensure_ascii=False) if record else None)).rowcount)

    # -- 不再接收 --------------------------------------------------------- #
    @_locked
    def ignored_works(self) -> list[dict[str, Any]]:
        return [_work(r) for r in self._conn.execute("SELECT * FROM ignored_works ORDER BY id")]

    @_locked
    def ignored_work(self, work_id: int) -> dict[str, Any] | None:
        row = self._conn.execute("SELECT * FROM ignored_works WHERE id=?", (work_id,)).fetchone()
        return _work(row) if row else None

    def add_ignored_work(self, identity: str, item: dict[str, Any]) -> dict[str, Any]:
        """记下不再接收的作品；已经有了时返回原来那条。"""
        with self._lock, self._conn:
            self._conn.execute("INSERT OR IGNORE INTO ignored_works (identity, title, tmdbid, kind, created_at) "
                               "VALUES (?,?,?,?,?)",
                               (identity, item["title"], item.get("tmdbid"), item.get("kind"), time.time()))
            return _work(self._conn.execute("SELECT * FROM ignored_works WHERE identity=?", (identity,)).fetchone())

    def remove_ignored_work(self, work_id: int) -> bool:
        with self._lock, self._conn:
            return bool(self._conn.execute("DELETE FROM ignored_works WHERE id=?", (work_id,)).rowcount)
