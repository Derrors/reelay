"""SQLite 持久层（WAL 模式）+ 三层去重。

这是稳定性的底座，落地了评审的三个 P0 补丁：

1. **任务状态机落库**：``tasks`` 表存每个任务的完整状态与快照，进程重启后
   ``pending_approval`` 的审批卡片仍能对上、``running`` 中断的任务能被捡回。
2. **三层去重**：``dedup`` 表按 (kind, key) 记录，分消息级 / 资源级 / 内容级，
   各司其职，支持追更增量（内容级按 title|season|ep 记录已入库的集）。
3. **可观测/干预的数据基础**：按状态查询、失败重试、待确认列表都从这里取数。

并发写用「单写协程 + WAL + busy_timeout」，规避 ``database is locked``。
Telethon 自己的 session 库与本业务库分文件，互不干扰。
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from .contracts import PipelineContext, RawMessage, ParsedResource, TaskState


# 三层去重的 kind
DEDUP_MESSAGE = "message"   # 消息级：这条 TG 消息处理过没（防重放/重启重复）
DEDUP_SHARE = "share"       # 资源级：这个分享链接转存过没（防重复转存）
DEDUP_EPISODE = "episode"   # 内容级：这一集入库过没（追更关键）


_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    task_id     TEXT PRIMARY KEY,
    state       TEXT NOT NULL,
    source      TEXT,
    msg_id      TEXT,
    title       TEXT,
    snapshot    TEXT NOT NULL,          -- PipelineContext 的 JSON 快照
    error       TEXT,
    retries     INTEGER NOT NULL DEFAULT 0,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tasks_state ON tasks(state);

CREATE TABLE IF NOT EXISTS dedup (
    kind        TEXT NOT NULL,          -- message | share | episode
    key         TEXT NOT NULL,
    task_id     TEXT,
    created_at  REAL NOT NULL,
    PRIMARY KEY (kind, key)
);
"""


class Store:
    """线程安全的 SQLite 封装。所有写走同一把锁（单写），读可并发。"""

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_pragmas()
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def _init_pragmas(self) -> None:
        cur = self._conn
        cur.execute("PRAGMA journal_mode=WAL;")     # 读写不互斥，重启不丢
        cur.execute("PRAGMA synchronous=NORMAL;")
        cur.execute("PRAGMA busy_timeout=5000;")    # 遇锁最多等 5s，规避 locked
        cur.execute("PRAGMA foreign_keys=ON;")

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------------ #
    # 任务状态机
    # ------------------------------------------------------------------ #
    def save_task(self, ctx: PipelineContext, *, error: str | None = None) -> None:
        """插入或更新任务快照 —— 每次状态变更都调它，保证库里是最新状态。"""
        snap = _dump_ctx(ctx)
        parsed = ctx.parsed
        now = time.time()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO tasks (task_id, state, source, msg_id, title, snapshot,
                                   error, retries, created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(task_id) DO UPDATE SET
                    state=excluded.state,
                    title=excluded.title,
                    snapshot=excluded.snapshot,
                    error=excluded.error,
                    updated_at=excluded.updated_at
                """,
                (
                    ctx.task_id,
                    ctx.state.value,
                    ctx.raw.source,
                    ctx.raw.msg_id,
                    parsed.title if parsed else None,
                    json.dumps(snap, ensure_ascii=False),
                    error,
                    0,
                    now,
                    now,
                ),
            )
            self._conn.commit()

    def set_state(self, task_id: str, state: TaskState, *, error: str | None = None) -> bool:
        """原子更新状态列与快照，避免 Worker 从 snapshot 读到旧状态。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT snapshot FROM tasks WHERE task_id=?", (task_id,)
            ).fetchone()
            if not row:
                return False
            snapshot = json.loads(row["snapshot"])
            snapshot["state"] = state.value
            self._conn.execute(
                "UPDATE tasks SET state=?, snapshot=?, error=?, updated_at=? WHERE task_id=?",
                (
                    state.value,
                    json.dumps(snapshot, ensure_ascii=False),
                    error,
                    time.time(),
                    task_id,
                ),
            )
            self._conn.commit()
            return True

    def bump_retry(self, task_id: str) -> int:
        with self._lock:
            self._conn.execute(
                "UPDATE tasks SET retries=retries+1, updated_at=? WHERE task_id=?",
                (time.time(), task_id),
            )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT retries FROM tasks WHERE task_id=?", (task_id,)
            ).fetchone()
        return int(row["retries"]) if row else 0

    def get_task(self, task_id: str) -> PipelineContext | None:
        row = self._conn.execute(
            "SELECT snapshot FROM tasks WHERE task_id=?", (task_id,)
        ).fetchone()
        if not row:
            return None
        return _load_ctx(json.loads(row["snapshot"]))

    def list_by_state(self, state: TaskState, limit: int = 50) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT task_id, state, source, title, error, retries, updated_at "
            "FROM tasks WHERE state=? ORDER BY updated_at DESC LIMIT ?",
            (state.value, limit),
        ).fetchall()

    def list_tasks(
        self,
        *,
        state: TaskState | None = None,
        source: str | None = None,
        query: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """管理端任务列表；返回轻量摘要，不泄露完整分享链接与凭证。"""
        clauses: list[str] = []
        params: list[Any] = []
        if state:
            clauses.append("state=?")
            params.append(state.value)
        if source:
            clauses.append("source=?")
            params.append(source)
        if query:
            clauses.append("(title LIKE ? OR source LIKE ? OR task_id LIKE ? OR error LIKE ?)")
            like = f"%{query}%"
            params.extend((like, like, like, like))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        params.append(max(1, min(limit, 500)))
        rows = self._conn.execute(
            "SELECT task_id, state, source, msg_id, title, error, retries, "
            f"created_at, updated_at FROM tasks{where} ORDER BY updated_at DESC LIMIT ?",
            tuple(params),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_task_record(self, task_id: str) -> dict[str, Any] | None:
        """管理端任务详情：数据库列 + 经过脱敏的上下文快照。"""
        row = self._conn.execute(
            "SELECT * FROM tasks WHERE task_id=?", (task_id,)
        ).fetchone()
        if not row:
            return None
        data = dict(row)
        snapshot = json.loads(data.pop("snapshot"))
        parsed = snapshot.get("parsed") or {}
        if parsed.get("receive_code"):
            parsed["receive_code"] = "****"
        snapshot["parsed"] = parsed
        data["context"] = snapshot
        return data

    def update_task_context(self, ctx: PipelineContext, *, error: str | None = None) -> None:
        """管理端修改解析结果/目标目录后持久化完整上下文。"""
        self.save_task(ctx, error=error)

    def delete_task(self, task_id: str) -> bool:
        """仅用于删除误入的任务记录；不删除 115 文件。"""
        with self._lock:
            cur = self._conn.execute("DELETE FROM tasks WHERE task_id=?", (task_id,))
            self._conn.commit()
            return cur.rowcount > 0

    def counts_by_state(self) -> dict[str, int]:
        rows = self._conn.execute(
            "SELECT state, COUNT(*) c FROM tasks GROUP BY state"
        ).fetchall()
        return {r["state"]: r["c"] for r in rows}

    def recover_interrupted(self) -> list[PipelineContext]:
        """重启时把 saving/queued 原子恢复成 queued，并返回可执行快照。"""
        rows = self._conn.execute(
            "SELECT task_id FROM tasks WHERE state IN (?, ?)",
            (TaskState.SAVING.value, TaskState.QUEUED.value),
        ).fetchall()
        recovered = []
        for row in rows:
            self.set_state(row["task_id"], TaskState.QUEUED)
            ctx = self.get_task(row["task_id"])
            if ctx:
                recovered.append(ctx)
        return recovered

    # ------------------------------------------------------------------ #
    # 三层去重
    # ------------------------------------------------------------------ #
    def seen(self, kind: str, key: str) -> bool:
        if not key:
            return False
        row = self._conn.execute(
            "SELECT 1 FROM dedup WHERE kind=? AND key=?", (kind, key)
        ).fetchone()
        return row is not None

    def mark(self, kind: str, key: str, task_id: str | None = None) -> None:
        if not key:
            return
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO dedup (kind, key, task_id, created_at) VALUES (?,?,?,?)",
                (kind, key, task_id, time.time()),
            )
            self._conn.commit()

    @staticmethod
    def episode_key(title: str, season: int | None, ep: int) -> str:
        return f"{title}|S{season or 0:02d}|E{ep:02d}"

    def missing_episodes(self, parsed: ParsedResource) -> list[int]:
        """内容级去重：返回 parsed 中「尚未入库」的集数，支持追更增量。

        - 非剧集或无集数信息：返回空表（交给资源级判断）
        - 剧集：只保留 episode_key 未命中的集
        """
        if not parsed.title or not parsed.episodes:
            return []
        missing = []
        for ep in parsed.episodes:
            k = self.episode_key(parsed.title, parsed.season, ep)
            if not self.seen(DEDUP_EPISODE, k):
                missing.append(ep)
        return missing


# --------------------------------------------------------------------------- #
# ctx <-> dict 序列化（用于快照落库 / 重启还原）
# --------------------------------------------------------------------------- #
def _dump_ctx(ctx: PipelineContext) -> dict[str, Any]:
    p = ctx.parsed
    return {
        "task_id": ctx.task_id,
        "state": ctx.state.value,
        "raw": {
            "source": ctx.raw.source,
            "msg_id": ctx.raw.msg_id,
            "text": ctx.raw.text,
            "urls": ctx.raw.urls,
            "received_at": ctx.raw.received_at,
            "extra": ctx.raw.extra,
        },
        "parsed": (
            None if p is None else {
                "is_media": p.is_media, "is_ad": p.is_ad, "confidence": p.confidence,
                "media_type": p.media_type, "title": p.title, "year": p.year,
                "season": p.season, "episodes": p.episodes, "resolution": p.resolution,
                "size": p.size, "share_code": p.share_code, "receive_code": p.receive_code,
                "reason": p.reason, "raw_fields": p.raw_fields,
            }
        ),
        "decision": ctx.decision.value if ctx.decision else None,
        "save_dir_alias": ctx.save_dir_alias,
        "save_dir_cid": ctx.save_dir_cid,
        "result": ctx.result,
        "source_rule": ctx.source_rule,
        "meta": ctx.meta,
        "trail": ctx.trail,
    }


def _load_ctx(d: dict[str, Any]) -> PipelineContext:
    from .contracts import Decision  # 局部导入避免顶层循环

    raw = RawMessage(**d["raw"])
    ctx = PipelineContext(raw=raw, task_id=d["task_id"])
    ctx.state = TaskState(d["state"])
    if d.get("parsed"):
        ctx.parsed = ParsedResource(**d["parsed"])
    ctx.decision = Decision(d["decision"]) if d.get("decision") else None
    ctx.save_dir_alias = d.get("save_dir_alias")
    ctx.save_dir_cid = d.get("save_dir_cid")
    ctx.result = d.get("result")
    ctx.source_rule = d.get("source_rule", {})
    ctx.meta = d.get("meta", {})
    ctx.trail = d.get("trail", [])
    return ctx
