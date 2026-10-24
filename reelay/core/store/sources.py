"""来源：每个来源已接收的消息水位与最近消息，以及管理台请主进程读取来源消息的请求。"""
from __future__ import annotations

import json
import time
from typing import Any

from .base import _locked


class SourceRepository:
    """来源水位与读取请求。"""

    @_locked
    def source_watermark(self, source: str) -> int | None:
        """某来源已接收的最大消息 ID；重连后从这里补拉，首次监听返回 None。"""
        row = self._conn.execute(
            "SELECT MAX(CAST(msg_id AS INTEGER)) m FROM tasks WHERE lower(source)=lower(?) "
            "AND msg_id GLOB '[0-9]*' AND msg_id NOT GLOB '*[^0-9]*'", (source,)
        ).fetchone()
        return int(row["m"]) if row and row["m"] is not None else None

    @_locked
    def source_messages(self, source: str, limit: int) -> list[dict[str, Any]]:
        """某来源最近收到的消息（正文去重，新的在前），读取不到频道时给识别规则当样例。"""
        out, seen = [], set()
        for r in self._conn.execute("SELECT snapshot FROM tasks WHERE lower(source)=lower(?) "
                                    "ORDER BY created_at DESC LIMIT ?", (source, limit * 5)):
            raw = json.loads(r["snapshot"]).get("raw") or {}
            text = (raw.get("text") or "").strip()
            if text and text not in seen:
                seen.add(text)
                out.append({"text": text, "urls": raw.get("urls") or []})
            if len(out) >= limit:
                break
        return out

    def request_source_fetch(self, bot: str, count: int) -> int:
        now = time.time()
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM source_fetches WHERE created_at<?", (now - 3600,))
            return self._conn.execute(
                "INSERT INTO source_fetches (bot, max_count, created_at, updated_at) VALUES (?,?,?,?)",
                (bot, count, now, now)).lastrowid

    def claim_source_fetches(self) -> list[dict[str, Any]]:
        """主进程领取待处理的读取请求；一分钟前的请求管理台早已放弃等待，不再处理。"""
        now = time.time()
        with self._lock, self._conn:
            rows = [dict(r) for r in self._conn.execute(
                "SELECT id, bot, max_count FROM source_fetches WHERE status='pending' AND created_at>=?", (now - 60,))]
            self._conn.executemany("UPDATE source_fetches SET status='running', updated_at=? WHERE id=?",
                                   [(now, r["id"]) for r in rows])
            return rows

    def finish_source_fetch(self, fetch_id: int, *, messages: list[dict] | None = None,
                            error: str | None = None) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE source_fetches SET status=?, result=?, error=?, updated_at=? WHERE id=?",
                ("failed" if error else "done", None if error else json.dumps(messages or [], ensure_ascii=False),
                 error, time.time(), fetch_id))

    @_locked
    def source_fetch(self, fetch_id: int) -> dict[str, Any] | None:
        row = self._conn.execute("SELECT status, result, error FROM source_fetches WHERE id=?", (fetch_id,)).fetchone()
        if not row:
            return None
        return {"status": row["status"], "error": row["error"],
                "messages": json.loads(row["result"]) if row["result"] else []}
