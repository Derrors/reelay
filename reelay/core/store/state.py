"""扩展私有状态：按扩展名称隔离的小型 KV（runtime.PluginState 的存储）。"""
from __future__ import annotations

import json
import time
from typing import Any

from .base import _locked


class PluginStateRepository:
    """扩展私有状态。"""

    @_locked
    def state_get(self, plugin: str, key: str, default: Any = None) -> Any:
        row = self._conn.execute(
            "SELECT value FROM plugin_state WHERE plugin=? AND key=?", (plugin, key)).fetchone()
        return json.loads(row["value"]) if row else default

    def state_set(self, plugin: str, key: str, value: Any) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO plugin_state VALUES (?,?,?,?) ON CONFLICT(plugin, key) DO UPDATE SET "
                "value=excluded.value, updated_at=excluded.updated_at",
                (plugin, key, json.dumps(value, ensure_ascii=False), time.time()))

    def state_delete(self, plugin: str, key: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM plugin_state WHERE plugin=? AND key=?", (plugin, key))

    @_locked
    def state_items(self, plugin: str) -> dict[str, Any]:
        return {r["key"]: json.loads(r["value"]) for r in self._conn.execute(
            "SELECT key, value FROM plugin_state WHERE plugin=? ORDER BY key", (plugin,))}
