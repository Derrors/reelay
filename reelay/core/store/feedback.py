"""AI 调用记录与人工裁决样本：供调用统计、提示词示例、偏好参考、通过率统计与本地评测。"""
from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

from .base import _locked


class FeedbackRepository:
    """AI 调用记录与人工裁决样本。"""

    def record_llm_call(self, *, purpose: str, model: str | None, task_id: str | None, outcome: str,
                        error: str | None, duration_ms: int, prompt_tokens: int | None,
                        completion_tokens: int | None) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO llm_calls (purpose, model, task_id, outcome, error, duration_ms, prompt_tokens, "
                "completion_tokens, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (purpose, model, task_id, outcome, error, duration_ms, prompt_tokens, completion_tokens, time.time()))

    @_locked
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

    @_locked
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

    @_locked
    def feedback_rows(self, *, kinds: tuple[str, ...] = ("approve", "reject", "correct")) -> list[dict[str, Any]]:
        return [_feedback_row(r) for r in self._conn.execute(
            f"SELECT * FROM feedback WHERE kind IN ({','.join('?' * len(kinds))}) ORDER BY id", kinds)]

    @_locked
    def decision_stats(self, window: int, groups: dict[str, str] | None = None) -> dict[str, dict[str, Any]]:
        """按来源统计最近 window 次人工批准 / 忽略（同一任务只算最新一次），以及其中被修正过识别结果的任务数
        与「按建议处理」的次数。groups 把来源（小写）归到一组（如同一张来源卡片），按组统计。"""
        latest: dict[str, dict[str, sqlite3.Row]] = {}
        for row in self._conn.execute(
                "SELECT id, task_id, source, kind, advice FROM feedback WHERE kind IN ('approve','reject') "
                "ORDER BY id DESC LIMIT 20000"):
            source = (row["source"] or "").lower()
            source = (groups or {}).get(source, source)
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
