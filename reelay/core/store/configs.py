"""配置快照：任务接收时记下当时的配置版本号，配置本身按版本号只存一份。"""
from __future__ import annotations

import json
import time
from collections import OrderedDict
from typing import Any

from .base import _locked

# 进程内缓存最近用到的快照：投递与恢复时同一版本会反复读取
_CACHE_SIZE = 32


class ConfigSnapshotRepository:
    """按版本号保存的配置快照。"""

    def put_config_snapshot(self, revision: str, config: dict[str, Any]) -> None:
        """记下这个版本的配置（已有时不变）。"""
        with self._lock, self._conn:
            self._conn.execute("INSERT OR IGNORE INTO config_snapshots VALUES (?,?,?)",
                               (revision, json.dumps(config, ensure_ascii=False, sort_keys=True), time.time()))
        self._config_cache[revision] = config

    @_locked
    def config_snapshot(self, revision: str | None) -> dict[str, Any] | None:
        """这个版本的配置；没有记录时为 None。"""
        if not revision:
            return None
        cache: OrderedDict = self._config_cache
        if revision in cache:
            cache.move_to_end(revision)
            return cache[revision]
        row = self._conn.execute("SELECT config FROM config_snapshots WHERE revision=?", (revision,)).fetchone()
        if not row:
            return None
        cache[revision] = json.loads(row["config"])
        while len(cache) > _CACHE_SIZE:
            cache.popitem(last=False)
        return cache[revision]
