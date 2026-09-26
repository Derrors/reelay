"""容器 Worker 存活检查：只读 SQLite，不调用外部服务或加载凭证。"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path


def check_worker(db_path: str | Path, *, max_age: float = 30) -> bool:
    path = Path(db_path).resolve()
    try:
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=3) as conn:
            row = conn.execute("SELECT payload, updated_at FROM runtime_health WHERE component='worker'").fetchone()
            if not row or time.time() - row[1] > max_age or json.loads(row[0]).get("status") != "running":
                return False
            source = conn.execute("SELECT payload, updated_at FROM runtime_health WHERE component='source'").fetchone()
            return bool(source and time.time() - source[1] <= max_age
                        and json.loads(source[0]).get("status") == "connected")
    except (sqlite3.Error, OSError, ValueError):
        return False


if __name__ == "__main__":
    raise SystemExit(0 if check_worker(os.getenv("REELAY_DB_PATH", "data/reelay.db")) else 1)
