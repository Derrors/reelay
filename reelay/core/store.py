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
from dataclasses import asdict
import threading
import time
from pathlib import Path
from typing import Any

from .contracts import PipelineContext, RawMessage, ParsedResource, TaskState
from .media import multi_season


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
CREATE TABLE IF NOT EXISTS alerts (
    key TEXT PRIMARY KEY,                -- 告警类别，如 p115_auth / source
    active INTEGER NOT NULL,
    text TEXT NOT NULL,
    since REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS plugin_state (
    plugin TEXT NOT NULL,                -- 扩展名称，状态按扩展隔离
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (plugin, key)
);
CREATE TABLE IF NOT EXISTS llm_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    purpose TEXT NOT NULL,               -- 用途：parse / preference / assist / compile ...
    model TEXT,
    task_id TEXT,
    outcome TEXT NOT NULL,               -- ok | invalid（未通过校验）| refused | error
    error TEXT,
    duration_ms INTEGER NOT NULL,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_llm_calls_created ON llm_calls(created_at);
CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    source TEXT,
    kind TEXT NOT NULL,                  -- approve | reject | correct
    text TEXT,                           -- 消息正文（已去掉链接与访问码），供提示词示例与本地评测
    parsed TEXT,                         -- 裁决时的识别结果（JSON）
    label TEXT NOT NULL,                 -- 人工裁决（JSON）：修正前后的字段、选择的目录等
    advice TEXT,                         -- 裁决时管理台给出的建议动作；没有建议为 NULL
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_feedback_source ON feedback(source, kind, id);
"""

# 系统通知（告警、审批汇总、日报）没有对应任务，投递行使用固定的伪任务 ID。
SYSTEM_TASK = "system"


def _add_task_not_before(conn: sqlite3.Connection) -> None:
    """v2：失败退避的下次执行时间；Worker 只领取到期任务，不在消费者里睡眠。"""
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(tasks)")}
    if "not_before" not in columns:
        conn.execute("ALTER TABLE tasks ADD COLUMN not_before REAL NOT NULL DEFAULT 0")


# 迁移按顺序追加、只增不改；第 n 项把数据库从版本 n-1 升到 n。v1 是 _SCHEMA 建立的基线。
MIGRATIONS = (lambda conn: None, _add_task_not_before)
SCHEMA_VERSION = len(MIGRATIONS)


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
        self._migrate()
        self._conn.commit()

    def _migrate(self) -> None:
        """按 PRAGMA user_version 顺序执行迁移；新表由 _SCHEMA 的 IF NOT EXISTS 创建。"""
        version = self._conn.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise RuntimeError(
                f"数据库结构版本 {version} 高于当前程序支持的 {SCHEMA_VERSION}，请升级 Reelay 后再打开")
        for target, migrate in enumerate(MIGRATIONS, start=1):
            if version < target:
                migrate(self._conn)
        if version != SCHEMA_VERSION:
            self._conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

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
            # 跨多季的合集按（季, 集）逐个记录；旧记录没有 pairs，按单季的季与集
            pairs = identity.get("pairs") or [[identity["season"], ep] for ep in identity["episodes"]]
            keys += [
                (DEDUP_EPISODE, f"{prefix}:{self.episode_key(identity['title'] or '', season, ep)}")
                for season, ep in pairs if identity["title"]
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
                  expected: TaskState | None = None, not_before: float = 0) -> bool:
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
                "UPDATE tasks SET state=?, snapshot=?, error=?, updated_at=?, not_before=? WHERE task_id=?",
                (
                    state.value,
                    json.dumps(snapshot, ensure_ascii=False),
                    error,
                    time.time(),
                    not_before,
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

    def list_by_state(self, state: TaskState, limit: int = 50, *,
                      due_only: bool = False) -> list[sqlite3.Row]:
        """due_only=True 时跳过仍在退避期内的任务。"""
        due = " AND not_before<=?" if due_only else ""
        params = (state.value, time.time(), limit) if due_only else (state.value, limit)
        return self._conn.execute(
            "SELECT task_id, state, source, title, error, retries, updated_at, created_at "
            f"FROM tasks WHERE state=?{due} ORDER BY updated_at ASC LIMIT ?",
            params,
        ).fetchall()

    def list_tasks(
        self,
        *,
        state: TaskState | None = None,
        states: list[TaskState] | None = None,
        source: str | None = None,
        query: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """管理端任务列表；返回轻量摘要（含展示用的媒体字段），不泄露完整分享链接与凭证。"""
        clauses: list[str] = []
        params: list[Any] = []
        wanted = [*(states or []), *([state] if state else [])]
        if wanted:
            clauses.append(f"state IN ({','.join('?' * len(wanted))})")
            params.extend(item.value for item in wanted)
        if source:
            clauses.append("source=?")
            params.append(source)
        if query:
            clauses.append("(title LIKE ? OR source LIKE ? OR task_id LIKE ? OR error LIKE ?)")
            like = f"%{query}%"
            params.extend((like, like, like, like))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        params.extend((max(1, min(limit, 500)), max(0, offset)))
        rows = self._conn.execute(
            "SELECT task_id, state, source, msg_id, title, error, retries, created_at, updated_at, "
            "json_extract(snapshot,'$.parsed.media_type') AS media_type, "
            "json_extract(snapshot,'$.parsed.year') AS year, "
            "json_extract(snapshot,'$.parsed.season') AS season, "
            "json_extract(snapshot,'$.parsed.episodes') AS episodes, "
            "json_extract(snapshot,'$.parsed.resolution') AS resolution, "
            "json_extract(snapshot,'$.parsed.size') AS size, "
            "json_extract(snapshot,'$.parsed.raw_fields.poster_path') AS poster_path, "
            "json_extract(snapshot,'$.save_dir_alias') AS save_dir_alias, "
            "json_extract(snapshot,'$.meta.storage.manifest.pairs') AS episode_pairs "
            f"FROM tasks{where} ORDER BY updated_at DESC LIMIT ? OFFSET ?",
            tuple(params),
        ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item["episodes"] = json.loads(item["episodes"]) if item["episodes"] else []
            # 跨多季的合集附带（季, 集），界面显示「S01–S03 · 30 集」
            item["episode_pairs"] = multi_season(json.loads(item["episode_pairs"]) if item["episode_pairs"] else [])
            items.append(item)
        return items

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
                self._conn.execute("UPDATE tasks SET retries=0, not_before=0 WHERE task_id=?", (ctx.task_id,))

    def refresh_snapshot(self, ctx: PipelineContext, *, updated_at: float) -> bool:
        """只替换片名与快照，不改状态和更新时间（审批超时计时、入库统计照旧）。

        updated_at 是读取快照时的值：期间任务被任何其他写入改过则放弃，返回 False。
        """
        with self._lock, self._conn:
            cur = self._conn.execute(
                "UPDATE tasks SET title=?, snapshot=? WHERE task_id=? AND state=? AND updated_at=?",
                (ctx.parsed.title if ctx.parsed else None, json.dumps(_dump_ctx(ctx), ensure_ascii=False),
                 ctx.task_id, ctx.state.value, updated_at))
            return cur.rowcount == 1

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

    def stored_episode_pairs(self, title: str, cid: str, provider: str = "p115") -> list[list[int]]:
        """某剧在目标目录已入库的 [季, 集]；分享清单据此排除已入库的集。"""
        prefix = f"{_target_prefix(cid, provider)}:{title}|"
        pairs = set()
        for row in self._conn.execute(
                "SELECT key, task_id FROM dedup WHERE kind=? AND substr(key, 1, ?)=?",
                (DEDUP_EPISODE, len(prefix), prefix)):
            parsed = _episode_pair(row["key"][len(prefix):])
            if parsed:
                pairs.add(parsed)
        if provider == "p115":  # 旧版全局键：仅当原任务能确认同一目标目录时生效
            legacy = f"{title}|"
            for row in self._conn.execute(
                    "SELECT key, task_id FROM dedup WHERE kind=? AND substr(key, 1, ?)=?",
                    (DEDUP_EPISODE, len(legacy), legacy)):
                parsed = _episode_pair(row["key"][len(legacy):])
                ctx = self.get_task(row["task_id"]) if parsed and row["task_id"] else None
                if ctx and ctx.save_dir_cid == cid:
                    season, ep = parsed
                    pairs.add((season or 1, ep))
        return [list(pair) for pair in sorted(pairs)]

    def stored_series(self) -> dict[tuple[str, int], set[int]]:
        """按 (标题, 季) 汇总所有目标目录中已入库的集，供追更检查使用。"""
        series: dict[tuple[str, int], set[int]] = {}
        for row in self._conn.execute("SELECT key FROM dedup WHERE kind=?", (DEDUP_EPISODE,)):
            key = row["key"]
            head, sep, tail = key.partition(":")
            body = tail if sep and head.isdecimal() else key
            title, _, rest = body.rpartition("|S")
            pair = _episode_pair("S" + rest) if title else None
            if pair:
                season, ep = pair
                series.setdefault((title, season or 1), set()).add(ep)
        return series

    def library_titles(self, media_type: str) -> list[str]:
        """已入库任务的片名（按识别出的媒体类型）。"""
        return [r["title"] for r in self._conn.execute(
            "SELECT DISTINCT title FROM tasks WHERE state=? AND title IS NOT NULL "
            "AND json_extract(snapshot,'$.parsed.media_type')=?", (TaskState.DONE.value, media_type))]

    def titles_for_tmdbid(self, tmdbid: int) -> list[str]:
        """同一 TMDB 条目入库时用过的片名（规范名可能随语言设置变化）。"""
        return [r["title"] for r in self._conn.execute(
            "SELECT DISTINCT title FROM tasks WHERE title IS NOT NULL "
            "AND json_extract(snapshot,'$.parsed.raw_fields.tmdbid')=?", (int(tmdbid),))]

    def source_watermark(self, source: str) -> int | None:
        """某来源已接收的最大消息 ID；重连后从这里补拉，首次监听返回 None。"""
        row = self._conn.execute(
            "SELECT MAX(CAST(msg_id AS INTEGER)) m FROM tasks WHERE lower(source)=lower(?) "
            "AND msg_id GLOB '[0-9]*' AND msg_id NOT GLOB '*[^0-9]*'", (source,)
        ).fetchone()
        return int(row["m"]) if row and row["m"] is not None else None

    def summary_since(self, since: float) -> dict[str, Any]:
        """日报数据：时间窗口内新收与完成的任务，以及当前待处理积压。"""
        received = {r["state"]: r["c"] for r in self._conn.execute(
            "SELECT state, COUNT(*) c FROM tasks WHERE created_at>=? GROUP BY state", (since,))}
        done = [dict(r) for r in self._conn.execute(
            "SELECT task_id, title, source FROM tasks WHERE state=? AND updated_at>=? "
            "ORDER BY updated_at DESC LIMIT 20", (TaskState.DONE.value, since))]
        current = self.counts_by_state()
        return {
            "since": since,
            "received": sum(received.values()),
            "received_by_state": received,
            "done": done,
            "done_count": self._conn.execute(
                "SELECT COUNT(*) FROM tasks WHERE state=? AND updated_at>=?",
                (TaskState.DONE.value, since)).fetchone()[0],
            "pending_approval": current.get(TaskState.PENDING_APPROVAL.value, 0),
            "need_manual": current.get(TaskState.NEED_MANUAL.value, 0),
            "failed": current.get(TaskState.FAILED.value, 0),
        }

    # ------------------------------------------------------------------ #
    # 系统通知与告警（与任务状态同样走 Outbox，进程退出也不会丢）
    # ------------------------------------------------------------------ #
    def _stage_notice(self, key: str, text: str, targets: list[str], event: str) -> None:
        now = time.time()
        snapshot = json.dumps({"key": key, "text": text}, ensure_ascii=False)
        for target in targets:
            self._conn.execute(
                """INSERT INTO outbox (delivery_key, task_id, kind, event, target, snapshot, created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?)""",
                (f"notice:{key}:{target}:{time.time_ns()}", SYSTEM_TASK, "notice", event,
                 target, snapshot, now, now),
            )

    def stage_notice(self, key: str, text: str, targets: list[str], *, event: str = "notice") -> None:
        with self._lock, self._conn:
            self._stage_notice(key, text, targets, event)

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

    def active_alerts(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self._conn.execute(
            "SELECT key, text, since, updated_at FROM alerts WHERE active=1 ORDER BY since")]

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

    # ------------------------------------------------------------------ #
    # 扩展私有状态（按扩展名称隔离的小型 KV）
    # ------------------------------------------------------------------ #
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

    def state_items(self, plugin: str) -> dict[str, Any]:
        return {r["key"]: json.loads(r["value"]) for r in self._conn.execute(
            "SELECT key, value FROM plugin_state WHERE plugin=? ORDER BY key", (plugin,))}

    # ------------------------------------------------------------------ #
    # AI 调用记录（用途、模型、耗时、token 与是否通过校验；不存提示词与输出正文）
    # ------------------------------------------------------------------ #
    def record_llm_call(self, *, purpose: str, model: str | None, task_id: str | None, outcome: str,
                        error: str | None, duration_ms: int, prompt_tokens: int | None,
                        completion_tokens: int | None) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO llm_calls (purpose, model, task_id, outcome, error, duration_ms, prompt_tokens, "
                "completion_tokens, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (purpose, model, task_id, outcome, error, duration_ms, prompt_tokens, completion_tokens, time.time()))

    def llm_usage(self, since: float) -> dict[str, Any]:
        """按用途汇总调用次数、各结果数、平均耗时与 token；另给出按天的次数。"""
        purposes = [dict(r) for r in self._conn.execute(
            """SELECT purpose, COUNT(*) calls,
                      SUM(outcome='ok') ok, SUM(outcome='invalid') invalid,
                      SUM(outcome='refused') refused, SUM(outcome='error') error,
                      CAST(AVG(duration_ms) AS INTEGER) avg_ms,
                      COALESCE(SUM(prompt_tokens), 0) prompt_tokens,
                      COALESCE(SUM(completion_tokens), 0) completion_tokens
               FROM llm_calls WHERE created_at>=? GROUP BY purpose ORDER BY calls DESC""", (since,))]
        daily = [dict(r) for r in self._conn.execute(
            """SELECT substr(datetime(created_at,'unixepoch','localtime'),1,10) d, COUNT(*) calls,
                      COALESCE(SUM(prompt_tokens), 0) + COALESCE(SUM(completion_tokens), 0) tokens
               FROM llm_calls WHERE created_at>=? GROUP BY d ORDER BY d""", (since,))]
        models = [r["model"] for r in self._conn.execute(
            "SELECT DISTINCT model FROM llm_calls WHERE created_at>=? AND model IS NOT NULL", (since,))]
        total = {key: sum(item[key] or 0 for item in purposes)
                 for key in ("calls", "ok", "invalid", "refused", "error", "prompt_tokens", "completion_tokens")}
        return {"since": since, "total": total, "purposes": purposes, "daily": daily, "models": models}

    def prune_llm_calls(self, before: float) -> int:
        with self._lock, self._conn:
            return self._conn.execute("DELETE FROM llm_calls WHERE created_at<?", (before,)).rowcount

    # ------------------------------------------------------------------ #
    # 人工裁决样本：批准、忽略与修正，供提示词示例、偏好参考、通过率统计与本地评测
    # ------------------------------------------------------------------ #
    def add_feedback(self, *, task_id: str, source: str | None, kind: str, text: str | None,
                     parsed: dict | None, label: dict, advice: str | None = None) -> int:
        if kind not in ("approve", "reject", "correct"):
            raise ValueError("裁决类型无效")
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO feedback (task_id, source, kind, text, parsed, label, advice, created_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (task_id, source, kind, text, json.dumps(parsed, ensure_ascii=False) if parsed is not None else None,
                 json.dumps(label, ensure_ascii=False), advice, time.time()))
            return int(cur.lastrowid)

    def recent_feedback(self, *, source: str | None, kinds: tuple[str, ...], limit: int,
                        exclude_task: str | None = None) -> list[dict[str, Any]]:
        """某来源最近的裁决样本，每个任务只取最新一条；source 为 None 时不限来源。"""
        if limit <= 0:
            return []
        clauses = [f"kind IN ({','.join('?' * len(kinds))})"]
        params: list[Any] = list(kinds)
        if source is not None:
            clauses.append("lower(source)=lower(?)")
            params.append(source)
        if exclude_task:
            clauses.append("task_id<>?")
            params.append(exclude_task)
        rows = self._conn.execute(
            f"SELECT * FROM feedback WHERE {' AND '.join(clauses)} ORDER BY id DESC LIMIT ?",
            (*params, max(limit * 5, 50))).fetchall()
        seen, out = set(), []
        for row in rows:
            if row["task_id"] in seen:
                continue
            seen.add(row["task_id"])
            out.append(_feedback_row(row))
            if len(out) >= limit:
                break
        return out

    def feedback_rows(self, *, kinds: tuple[str, ...] = ("approve", "reject", "correct")) -> list[dict[str, Any]]:
        return [_feedback_row(r) for r in self._conn.execute(
            f"SELECT * FROM feedback WHERE kind IN ({','.join('?' * len(kinds))}) ORDER BY id", kinds)]

    def decision_stats(self, window: int) -> dict[str, dict[str, Any]]:
        """按来源统计最近 window 次人工批准 / 忽略（同一任务只算最新一次），以及其中被修正过识别结果的任务数
        与「按建议处理」的次数。"""
        latest: dict[str, dict[str, sqlite3.Row]] = {}
        for row in self._conn.execute(
                "SELECT id, task_id, source, kind, advice FROM feedback WHERE kind IN ('approve','reject') "
                "ORDER BY id DESC LIMIT 20000"):
            source = (row["source"] or "").lower()
            tasks = latest.setdefault(source, {})
            if row["task_id"] not in tasks and len(tasks) < window:
                tasks[row["task_id"]] = row
        corrected = {r["task_id"] for r in self._conn.execute("SELECT DISTINCT task_id FROM feedback WHERE kind='correct'")}
        stats = {}
        for source, rows in latest.items():
            advised = [r for r in rows.values() if r["advice"] in ("approve", "reject")]
            stats[source] = {
                "decisions": len(rows),
                "approved": sum(r["kind"] == "approve" for r in rows.values()),
                "rejected": sum(r["kind"] == "reject" for r in rows.values()),
                "corrected": sum(task in corrected for task in rows),
                "advised": len(advised),
                "followed": sum(r["advice"] == r["kind"] for r in advised),
            }
        return stats


def _feedback_row(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item["parsed"] = json.loads(item["parsed"]) if item["parsed"] else None
    item["label"] = json.loads(item["label"])
    return item


def _episode_pair(text: str) -> tuple[int, int] | None:
    """解析 episode_key 的 ``S01|E02`` 尾部。"""
    import re
    match = re.fullmatch(r"S(\d+)\|E(\d+)", text)
    return (int(match.group(1)), int(match.group(2))) if match else None


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
        # 完整序列化：Worker 每个阶段都从快照重建，漏字段会让下游 Hook 读不到上游结果。
        "parsed": None if p is None else asdict(p),
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
