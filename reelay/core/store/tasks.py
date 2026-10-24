"""任务快照与状态机：每次状态变更都落库，进程重启后按快照恢复。"""
from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

from ..contracts import PipelineContext, TaskState
from ..media import multi_season
from .base import DEDUP_MESSAGE, _dump_ctx, _load_ctx, _locked


class TaskRepository:
    """任务表的读写。"""

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

    def defer_task(self, ctx: PipelineContext, *, until: float) -> None:
        """保存任务，并在 until 之前不让 Worker 领取（网盘暂时拒绝请求时用，不算失败）。"""
        with self._lock, self._conn:
            self._write_task(ctx)
            self._conn.execute("UPDATE tasks SET not_before=? WHERE task_id=?", (until, ctx.task_id))

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

    @_locked
    def get_task(self, task_id: str) -> PipelineContext | None:
        row = self._conn.execute(
            "SELECT snapshot FROM tasks WHERE task_id=?", (task_id,)
        ).fetchone()
        if not row:
            return None
        return _load_ctx(json.loads(row["snapshot"]))

    @_locked
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

    @_locked
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

    @_locked
    def get_task_record(self, task_id: str) -> dict[str, Any] | None:
        """管理端任务详情：数据库列 + 上下文快照（交给外部 Agent 时由 agent_tools 去掉分享码与访问码）。"""
        row = self._conn.execute(
            "SELECT * FROM tasks WHERE task_id=?", (task_id,)
        ).fetchone()
        if not row:
            return None
        data = dict(row)
        data["context"] = json.loads(data.pop("snapshot"))
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

    @_locked
    def counts_by_state(self) -> dict[str, int]:
        rows = self._conn.execute(
            "SELECT state, COUNT(*) c FROM tasks GROUP BY state"
        ).fetchall()
        return {r["state"]: r["c"] for r in rows}

    @_locked
    def list_recent_updated(self, limit: int = 200) -> list[sqlite3.Row]:
        """最近更新任务的轻量指纹，供 SSE 观察者判断任务是否有推进。"""
        return self._conn.execute(
            "SELECT task_id, state, updated_at FROM tasks "
            "ORDER BY updated_at DESC LIMIT ?",
            (max(1, min(int(limit), 500)),),
        ).fetchall()

    @_locked
    def recover_interrupted(self) -> list[PipelineContext]:
        """所有可执行中间态都有恢复路径。没有转存清单的 saving 还没发出转存（清单先于写请求落库），回到队列。"""
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
                    ctx.state = TaskState.QUEUED
                self.save_task(ctx)
                recovered.append(ctx)
        return recovered
