"""SQLite 持久层（WAL 模式）+ 三层去重。

持久化边界：

1. **任务状态机落库**：``tasks`` 表存每个任务的完整状态与快照，进程重启后
   审批卡片仍能对上，转存和整理中断后按各自检查点恢复。
2. **三层去重**：``dedup`` 表按 (kind, key) 记录，分消息级 / 资源级 / 内容级，
   各司其职，支持追更增量（内容级按 title|season|ep 记录已入库的集）。
3. **可观测/干预的数据基础**：按状态查询、失败重试、待确认列表都从这里取数。

并发写用事务、线程锁、WAL 与 busy_timeout；网盘副作用前的检查点必须提交。
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

CREATE TABLE IF NOT EXISTS resource_claims (
    resource_key TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(task_id),
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    delivery_key TEXT NOT NULL UNIQUE,
    task_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    event TEXT NOT NULL,
    target TEXT NOT NULL,
    snapshot TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt REAL NOT NULL DEFAULT 0,
    lease_until REAL NOT NULL DEFAULT 0,
    error TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_outbox_ready ON outbox(status, next_attempt);
CREATE TABLE IF NOT EXISTS runtime_health (
    component TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    updated_at REAL NOT NULL
);
"""


class Store:
    """线程安全的 SQLite 封装。所有写走同一把锁（单写），读可并发。"""

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._worker_file = None
        self._worker_owned = False
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_pragmas()
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def _init_pragmas(self) -> None:
        cur = self._conn
        cur.execute("PRAGMA journal_mode=WAL;")     # 读写不互斥，重启不丢
        cur.execute("PRAGMA synchronous=FULL;")     # 检查点落盘后才允许网盘写入
        cur.execute("PRAGMA busy_timeout=5000;")    # 遇锁最多等 5s，规避 locked
        cur.execute("PRAGMA foreign_keys=ON;")

    def close(self) -> None:
        with self._lock:
            self.release_worker()
            self._conn.close()

    # ------------------------------------------------------------------ #
    # 任务状态机
    # ------------------------------------------------------------------ #
    def save_task(self, ctx: PipelineContext, *, error: str | None = None) -> None:
        """插入或更新任务快照 —— 每次状态变更都调它，保证库里是最新状态。"""
        with self._lock, self._conn:
            self._write_task(ctx, error=error)

    def _write_task(self, ctx: PipelineContext, *, error: str | None = None) -> None:
        """事务内写入；调用方负责提交，不能在这里提前 commit。"""
        snap = _dump_ctx(ctx)
        parsed = ctx.parsed
        now = time.time()
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
        # 投递意图与权威任务状态同一事务提交，崩溃不会丢旁路事件。
        for key, delivery in ctx.meta.get("deliveries", {}).items():
            self._conn.execute(
                """INSERT OR IGNORE INTO outbox
                (delivery_key, task_id, kind, event, target, snapshot, created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?)""",
                (f"{ctx.task_id}:{key}", ctx.task_id, delivery["kind"], delivery["event"],
                 delivery["target"], json.dumps(snap, ensure_ascii=False), now, now),
            )
        # 尚未发送的准备阶段结束时，状态和释放占位一起提交，不能留下终态占位。
        if ctx.state in (TaskState.RECEIVED, TaskState.SKIPPED, TaskState.REJECTED, TaskState.FAILED) \
                and ctx.meta.get("storage", {}).get("phase") in (None, "preparing", "ready"):
            self._conn.execute("DELETE FROM resource_claims WHERE task_id=?", (ctx.task_id,))

    def accept_task(self, ctx: PipelineContext) -> bool:
        """消息唯一键和任务快照一起提交；任一失败都回滚。"""
        key = f"{ctx.raw.source}:{ctx.raw.msg_id}"
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO dedup VALUES (?,?,?,?)",
                (DEDUP_MESSAGE, key, ctx.task_id, time.time()),
            )
            if not cur.rowcount:
                return False
            self._write_task(ctx)
        return True

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
            row = self._conn.execute("SELECT attempts FROM outbox WHERE id=?", (delivery_id,)).fetchone()
            if not row:
                return
            attempts = row["attempts"] + (0 if skipped else 1)
            status = "skipped" if skipped else ("done" if error is None else ("failed" if attempts >= 8 else "pending"))
            now = time.time()
            delay = retry_after if retry_after is not None else min(2 ** attempts, 300)
            self._conn.execute("UPDATE outbox SET status=?, attempts=?, error=?, next_attempt=?, lease_until=0, updated_at=? WHERE id=?",
                               (status, attempts, error, now + delay, now, delivery_id))

    def delivery_counts(self) -> dict[str, int]:
        return {r["status"]: r["n"] for r in self._conn.execute("SELECT status, COUNT(*) n FROM outbox GROUP BY status")}

    def list_deliveries(self, *, status: str = "failed", limit: int = 100) -> list[dict]:
        if status not in {"pending", "delivering", "failed", "done", "skipped"}:
            raise ValueError("投递状态无效")
        return [dict(row) for row in self._conn.execute(
            """SELECT id, task_id, kind, event, target, status, attempts, error,
            next_attempt, updated_at FROM outbox WHERE status=? ORDER BY id DESC LIMIT ?""", (status, limit))]

    def retry_delivery(self, delivery_id: int) -> bool:
        with self._lock, self._conn:
            cur = self._conn.execute("UPDATE outbox SET status='pending', attempts=0, next_attempt=0, error=NULL WHERE id=? AND status='failed'", (delivery_id,))
            return bool(cur.rowcount)

    def heartbeat(self, component: str, **payload: Any) -> None:
        with self._lock, self._conn:
            self._conn.execute("INSERT INTO runtime_health VALUES (?,?,?) ON CONFLICT(component) DO UPDATE SET payload=excluded.payload, updated_at=excluded.updated_at",
                               (component, json.dumps(payload, ensure_ascii=False), time.time()))

    def health_status(self, *, stale_after: float = 30) -> dict[str, dict]:
        now = time.time()
        result = {}
        for row in self._conn.execute("SELECT * FROM runtime_health"):
            payload = json.loads(row["payload"])
            payload.update(updated_at=row["updated_at"], stale=now - row["updated_at"] > stale_after)
            result[row["component"]] = payload
        return result

    def claim_resource(self, ctx: PipelineContext, key: str, *,
                       content_keys: list[str] | None = None) -> bool:
        """分享和剧集一起占位，防止不同分享重复入库；崩溃后所有权保留。"""
        keys = sorted(set([key, *(content_keys or [])]))
        with self._lock, self._conn:
            self._conn.execute("BEGIN IMMEDIATE")
            for resource_key in keys:
                row = self._conn.execute(
                    "SELECT task_id FROM resource_claims WHERE resource_key=?", (resource_key,)
                ).fetchone()
                if row and row["task_id"] != ctx.task_id:
                    return False
            self._conn.executemany(
                "INSERT OR IGNORE INTO resource_claims VALUES (?,?,?)",
                [(resource_key, ctx.task_id, time.time()) for resource_key in keys],
            )
            ctx.ns("storage")["resource_key"] = key
            ctx.ns("storage")["claim_keys"] = keys
            self._write_task(ctx)
        return True

    def release_resource(self, task_id: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM resource_claims WHERE task_id=?", (task_id,))

    def finish_transfer(self, ctx: PipelineContext) -> None:
        """转存回执、去重记录和释放占位原子提交，整理可以独立恢复。"""
        p = ctx.parsed
        identity = ctx.ns("storage").get("identity") or {
            "cid": ctx.save_dir_cid, "share_code": p.share_code,
            "title": p.title, "season": p.season, "episodes": p.episodes,
        }
        cid = identity["cid"]
        provider = identity.get("provider", ctx.ns("storage").get("provider", "p115"))
        prefix = _target_prefix(cid, provider)
        if not identity["share_code"] or cid is None or (ctx.result or {}).get("storage_verified") is not True:
            raise ValueError("只有完整核验的转存回执可以提交去重记录")
        with self._lock, self._conn:
            self._write_task(ctx)
            keys = [(DEDUP_SHARE, f"{prefix}:{identity['share_code']}")]
            keys += [
                (DEDUP_EPISODE, f"{prefix}:{self.episode_key(identity['title'] or '', identity['season'], ep)}")
                for ep in identity["episodes"] if identity["title"]
            ]
            self._conn.executemany(
                "INSERT OR IGNORE INTO dedup VALUES (?,?,?,?)",
                [(kind, key, ctx.task_id, time.time()) for kind, key in keys],
            )
            self._conn.execute("DELETE FROM resource_claims WHERE task_id=?", (ctx.task_id,))

    def saved_share(self, share: str, cid: str, provider: str = "p115") -> bool:
        return self.seen(DEDUP_SHARE, f"{_target_prefix(cid, provider)}:{share}") or (
            provider == "p115" and self._legacy_seen(DEDUP_SHARE, share, cid))

    def _legacy_seen(self, kind: str, key: str, cid: str) -> bool:
        """旧版全局去重键只在关联任务能确认目标目录时复用。"""
        row = self._conn.execute(
            "SELECT task_id FROM dedup WHERE kind=? AND key=?", (kind, key)
        ).fetchone()
        ctx = self.get_task(row["task_id"]) if row and row["task_id"] else None
        return bool(ctx and ctx.save_dir_cid == cid)

    def acquire_worker(self) -> None:
        """单机服务只允许一个 Worker；进程退出时内核自动释放文件锁。"""
        import fcntl
        with self._lock:
            if self._worker_owned:
                raise RuntimeError("该数据库已有正在运行的 Worker")
            if self.db_path != ":memory:":
                stream = open(str(Path(self.db_path).resolve()) + ".worker.lock", "a")
                try:
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    stream.close()
                    raise RuntimeError("该数据库已有正在运行的 Worker") from None
                self._worker_file = stream
            self._worker_owned = True

    def release_worker(self) -> None:
        with self._lock:
            if self._worker_file:
                self._worker_file.close()
                self._worker_file = None
            self._worker_owned = False

    def set_state(self, task_id: str, state: TaskState, *, error: str | None = None,
                  expected: TaskState | None = None) -> bool:
        """原子更新状态列与快照，避免 Worker 从 snapshot 读到旧状态。"""
        with self._lock, self._conn:
            self._conn.execute("BEGIN IMMEDIATE")
            row = self._conn.execute(
                "SELECT snapshot, state FROM tasks WHERE task_id=?", (task_id,)
            ).fetchone()
            if not row:
                return False
            if expected is not None and row["state"] != expected.value:
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
            "FROM tasks WHERE state=? ORDER BY updated_at ASC LIMIT ?",
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

    def update_task_context(self, ctx: PipelineContext, *, error: str | None = None,
                            expected: TaskState | None = None, reset_retries: bool = False) -> None:
        """管理端修改解析结果/目标目录后持久化完整上下文。"""
        with self._lock, self._conn:
            self._conn.execute("BEGIN IMMEDIATE")
            row = self._conn.execute("SELECT state FROM tasks WHERE task_id=?", (ctx.task_id,)).fetchone()
            if expected is not None and (not row or row["state"] != expected.value):
                raise ValueError("任务状态已变化，请刷新后重试")
            self._write_task(ctx, error=error)
            if reset_retries:
                self._conn.execute("UPDATE tasks SET retries=0 WHERE task_id=?", (ctx.task_id,))

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

    def list_recent_updated(self, limit: int = 200) -> list[sqlite3.Row]:
        """最近更新任务的轻量指纹，供 SSE 观察者判断任务是否有推进。"""
        return self._conn.execute(
            "SELECT task_id, state, updated_at FROM tasks "
            "ORDER BY updated_at DESC LIMIT ?",
            (max(1, min(int(limit), 500)),),
        ).fetchall()

    def recover_interrupted(self) -> list[PipelineContext]:
        """所有可执行中间态都有恢复路径；旧版未知转存不能盲目重放。"""
        rows = self._conn.execute(
            "SELECT task_id FROM tasks WHERE state IN (?,?,?,?,?,?)",
            tuple(s.value for s in (
                TaskState.RECEIVED, TaskState.PARSED, TaskState.QUEUED,
                TaskState.SAVING, TaskState.STORED, TaskState.ORGANIZING,
            )),
        ).fetchall()
        recovered = []
        for row in rows:
            ctx = self.get_task(row["task_id"])
            if ctx:
                if ctx.state == TaskState.PARSED:
                    ctx.state = TaskState.RECEIVED
                elif ctx.state == TaskState.ORGANIZING:
                    ctx.state = TaskState.STORED
                elif ctx.state == TaskState.SAVING:
                    if ctx.ns("storage").get("manifest") or ctx.ns("storage").get("phase") == "preparing":
                        ctx.state = TaskState.QUEUED
                    else:
                        ctx.state = TaskState.NEED_MANUAL
                        ctx.ns("storage")["phase"] = "legacy_uncertain"
                        ctx.log("旧版转存中断且没有核验清单，请人工确认网盘结果")
                self.save_task(ctx)
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
        return f"{title}|S{season if season is not None else 1:02d}|E{ep:02d}"

    def missing_episodes(self, parsed: ParsedResource, cid: str | None = None, provider: str = "p115") -> list[int]:
        """内容级去重：返回 parsed 中「尚未入库」的集数，支持追更增量。

        - 非剧集或无集数信息：返回空表（交给资源级判断）
        - 剧集：只保留 episode_key 未命中的集
        """
        if not parsed.title or not parsed.episodes:
            return []
        missing = []
        for ep in parsed.episodes:
            k = self.episode_key(parsed.title, parsed.season, ep)
            if cid is not None:
                k = f"{_target_prefix(cid, provider)}:{k}"
            legacy_key = f"{parsed.title}|S{parsed.season or 0:02d}|E{ep:02d}"
            if not self.seen(DEDUP_EPISODE, k) and not (
                provider == "p115" and cid is not None and self._legacy_seen(DEDUP_EPISODE, legacy_key, cid)
            ):
                missing.append(ep)
        return missing


# --------------------------------------------------------------------------- #
# ctx <-> dict 序列化（用于快照落库 / 重启还原）
# --------------------------------------------------------------------------- #
def _target_prefix(cid: str | None, provider: str) -> str:
    return str(cid) if provider == "p115" else f"{provider}:{cid}"


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
                "season": p.season, "episodes": p.episodes, "genres": p.genres, "resolution": p.resolution,
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
