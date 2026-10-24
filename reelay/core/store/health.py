"""运行观察与告警：各组件的心跳、外部服务最近一次调用结果，以及按类别去重的告警。"""
from __future__ import annotations

import json
import time
from typing import Any

from .base import _locked


# 内容不变的心跳最多这么久写一次库：各组件每秒都在报告，每次都落盘（synchronous=FULL）会让磁盘无法休眠。
# 健康检查与管理台按 30 秒判断是否过期，10 秒一次足够。
HEARTBEAT_INTERVAL = 10.0


class HealthRepository:
    """心跳与告警。"""

    def heartbeat(self, component: str, **payload: Any) -> None:
        """记录组件状态；内容与上次相同且不到 HEARTBEAT_INTERVAL 秒时不重复写库，状态变化立即写入。"""
        data, now = json.dumps(payload, ensure_ascii=False, sort_keys=True), time.time()
        last = self._heartbeats.get(component)
        if last and last[0] == data and now - last[1] < HEARTBEAT_INTERVAL:
            return
        with self._lock, self._conn:
            self._conn.execute("INSERT INTO runtime_health VALUES (?,?,?) ON CONFLICT(component) DO UPDATE SET "
                               "payload=excluded.payload, updated_at=excluded.updated_at", (component, data, now))
        self._heartbeats[component] = (data, now)

    @_locked
    def health_status(self, *, stale_after: float = 30) -> dict[str, dict]:
        now = time.time()
        result = {}
        for row in self._conn.execute("SELECT * FROM runtime_health"):
            payload = json.loads(row["payload"])
            payload.update(updated_at=row["updated_at"], stale=now - row["updated_at"] > stale_after)
            result[row["component"]] = payload
        return result

    def raise_alert(self, key: str, text: str, targets: list[str]) -> bool:
        """告警只在状态由正常变为异常时通知一次；持续异常只更新文案。"""
        now = time.time()
        with self._lock, self._conn:
            self._conn.execute("BEGIN IMMEDIATE")
            row = self._conn.execute("SELECT active FROM alerts WHERE key=?", (key,)).fetchone()
            if row and row["active"]:
                self._conn.execute("UPDATE alerts SET text=?, updated_at=? WHERE key=?", (text, now, key))
                return False
            self._conn.execute(
                "INSERT INTO alerts VALUES (?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET "
                "active=1, text=excluded.text, since=excluded.since, updated_at=excluded.updated_at",
                (key, 1, text, now, now))
            self._stage_notice(key, f"⚠️ {text}", targets, "alert")
            return True

    def clear_alert(self, key: str, text: str, targets: list[str]) -> bool:
        now = time.time()
        with self._lock, self._conn:
            self._conn.execute("BEGIN IMMEDIATE")
            row = self._conn.execute("SELECT active FROM alerts WHERE key=?", (key,)).fetchone()
            if not row or not row["active"]:
                return False
            self._conn.execute("UPDATE alerts SET active=0, text=?, updated_at=? WHERE key=?", (text, now, key))
            self._stage_notice(key, f"✅ {text}", targets, "recovered")
            return True

    @_locked
    def active_alerts(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self._conn.execute(
            "SELECT key, text, since, updated_at FROM alerts WHERE active=1 ORDER BY since")]
