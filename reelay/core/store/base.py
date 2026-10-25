"""SQLite 持久层（WAL 模式）：连接、表结构与迁移、Worker 单实例锁与快照序列化。

各类数据的读写按领域放在本包的各个模块（任务、去重、Outbox、心跳与告警、扩展状态、登录会话、
AI 记录与裁决样本、来源、订阅与不再接收、配置快照、汇总查询），由 ``Store`` 组合在一起，共用一个
连接与一把可重入锁。

持久化边界：

1. **任务状态机落库**：``tasks`` 表存每个任务的完整状态与快照，进程重启后
   审批卡片仍能对上，转存和整理中断后按各自检查点恢复。
2. **三层去重**：``dedup`` 表按 (kind, key) 记录，分消息级 / 资源级 / 内容级，
   各司其职，支持追更增量（内容级按 title|season|ep 记录已入库的集）。
3. **可观测/干预的数据基础**：按状态查询、失败重试、待确认列表都从这里取数。

并发用事务、线程锁（读写都持锁）、WAL 与 busy_timeout；网盘副作用前的检查点必须提交。
Telethon 自己的 session 库与本业务库分文件，互不干扰。
"""
from __future__ import annotations

import functools
import json
import re
import sqlite3
import threading
from collections import OrderedDict
from dataclasses import asdict
from pathlib import Path
from typing import Any

from ..contracts import Decision, ParsedResource, PipelineContext, RawMessage, TaskState


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
    updated_at  REAL NOT NULL,
    not_before  REAL NOT NULL DEFAULT 0     -- 失败退避的下次执行时间；Worker 只领取到期任务
);
CREATE INDEX IF NOT EXISTS idx_tasks_state ON tasks(state);
CREATE INDEX IF NOT EXISTS idx_tasks_updated ON tasks(updated_at);

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
CREATE TABLE IF NOT EXISTS web_sessions (
    token_hash TEXT PRIMARY KEY,         -- 管理台登录会话；只存 token 的 SHA-256
    expires_at REAL NOT NULL
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
CREATE TABLE IF NOT EXISTS source_fetches (
    id INTEGER PRIMARY KEY AUTOINCREMENT, -- 管理台请主进程读取来源最近的消息（监听会话只在主进程里）
    bot TEXT NOT NULL,
    max_count INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending', -- pending | running | done | failed
    result TEXT,                         -- 消息列表（JSON）
    error TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS config_snapshots (
    revision TEXT PRIMARY KEY,           -- 配置版本号（config_revision）；任务快照只记版本号
    config TEXT NOT NULL,                -- 当时的有效配置（JSON，不含密钥）
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS subscriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    identity TEXT NOT NULL UNIQUE,       -- tmdb:<tmdbid>:S<季> 或 title:<规范片名>:S<季>
    title TEXT NOT NULL,
    season INTEGER NOT NULL,
    total INTEGER,                       -- 这一季的总集数（缺集巡检用），不知道为 NULL
    tmdbid INTEGER,
    auto INTEGER NOT NULL DEFAULT 1,     -- 1：新集自动转存；0：只巡检缺集
    finished TEXT,                       -- 一季播完且全部入库后自动结束：{"date", "episodes"}；在追为 NULL
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS ignored_works (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    identity TEXT NOT NULL UNIQUE,       -- tmdb:<kind>:<tmdbid> 或 title:<规范片名>
    title TEXT NOT NULL,
    tmdbid INTEGER,
    kind TEXT,                           -- movie | tv；没有 tmdbid 时为 NULL
    created_at REAL NOT NULL
);
"""

SYSTEM_TASK = "system"


def _episode_work_keys(conn: sqlite3.Connection) -> None:
    """v2：内容级去重键加上作品身份（年份与 TMDB ID，取自入库任务的快照），同名的不同作品不再互相当成已入库；
    转存中的资源占位改用规范片名（见 core/works.py）。"""
    from .. import tmdb, works
    target = re.compile(r"^(?:[A-Za-z0-9_.-]+:)?\d+:")
    rows = conn.execute("SELECT d.key, d.task_id, d.created_at, t.snapshot FROM dedup d "
                        "LEFT JOIN tasks t ON t.task_id=d.task_id WHERE d.kind='episode'").fetchall()
    for key, task_id, created, snapshot in rows:
        prefix = target.match(key)
        head, _, tail = key[prefix.end():].rpartition("|S") if prefix else ("", "", "")
        if not head:
            continue
        snap = json.loads(snapshot) if snapshot else {}
        parsed, matched = snap.get("parsed") or {}, (snap.get("meta") or {}).get("tmdb") or {}
        fields = parsed.get("raw_fields") or {}
        kind = matched.get("kind") or ("movie" if parsed.get("media_type") == "movie" and not parsed.get("episodes")
                                       else "tv")
        work = works.identity(head, parsed.get("year"), fields.get("tmdbid") or matched.get("tmdbid"), kind)
        new = f"{prefix.group(0)}{works.key(work)}|S{tail}"
        if new != key:
            conn.execute("DELETE FROM dedup WHERE kind='episode' AND key=?", (key,))
            conn.execute("INSERT OR IGNORE INTO dedup VALUES ('episode', ?, ?, ?)", (new, task_id, created))
    for key, task_id, created in conn.execute("SELECT resource_key, task_id, created_at FROM resource_claims "
                                              "WHERE resource_key LIKE '%:episode:%'").fetchall():
        scope, _, body = key.partition(":episode:")
        head, _, tail = body.rpartition("|S")
        new = f"{scope}:episode:{tmdb.normalize(head)}|S{tail}" if head else key
        if new != key:
            conn.execute("DELETE FROM resource_claims WHERE resource_key=?", (key,))
            conn.execute("INSERT OR IGNORE INTO resource_claims VALUES (?,?,?)", (new, task_id, created))


MIGRATIONS = (lambda conn: None, _episode_work_keys)
SCHEMA_VERSION = len(MIGRATIONS)


def _locked(method):
    """读也要持锁：整理等同步网盘操作在线程里读写扩展状态，与事件循环共用一个连接。

    sqlite3 按 SQL 文本缓存预编译语句，两个线程同时执行同一条语句会报
    「bad parameter or other API misuse」，或者读到对方的结果。
    """
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return wrapper


class Database:
    """线程安全的 SQLite 连接。共用一个连接，读写都走同一把可重入锁。

    用到 ``self._conn`` 的方法要么持锁（写用 ``with self._lock, self._conn``，读用 ``@_locked``），
    要么只在已持锁的调用方里使用（``_write_task``、``_stage_notice``）。
    """

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._worker_file = None
        self._worker_owned = False
        self._heartbeats: dict[str, tuple[str, float]] = {}  # 组件 → (上次写入的内容, 时间)，见 heartbeat
        self._config_cache: OrderedDict[str, dict] = OrderedDict()  # 配置快照缓存，见 config_snapshot
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
