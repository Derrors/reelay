"""管理台登录会话：只存 token 的 SHA-256，Web 重启后保持登录。"""
from __future__ import annotations

import time

from .base import _locked


class SessionRepository:
    """管理台登录会话。"""

    def web_session_add(self, token_hash: str, expires_at: float) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM web_sessions WHERE expires_at < ?", (time.time(),))
            self._conn.execute("INSERT OR REPLACE INTO web_sessions VALUES (?,?)", (token_hash, expires_at))

    @_locked
    def web_session_expires(self, token_hash: str) -> float | None:
        row = self._conn.execute(
            "SELECT expires_at FROM web_sessions WHERE token_hash=?", (token_hash,)).fetchone()
        return row["expires_at"] if row else None

    def web_session_drop(self, token_hash: str | None = None) -> None:
        """删除一个会话；不传则删除全部（修改或清除密码时）。"""
        with self._lock, self._conn:
            if token_hash is None:
                self._conn.execute("DELETE FROM web_sessions")
            else:
                self._conn.execute("DELETE FROM web_sessions WHERE token_hash=?", (token_hash,))
