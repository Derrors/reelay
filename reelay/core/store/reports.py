"""只读统计：日报、媒体库、来源质量、统计页与追更用到的汇总查询。"""
from __future__ import annotations

import json
from typing import Any

from ..contracts import TaskState
from ..media import planned_pairs
from .base import _load_ctx, _locked


class ReportRepository:
    """汇总查询。"""

    @_locked
    def done_snapshots(self, query: str | None = None, media_type: str | None = None) -> list[dict[str, Any]]:
        """已入库任务的快照与入库时间（媒体库用），最近入库在前；不限条数，按作品汇总后才是展示的单位。"""
        where, args = "state=?", [TaskState.DONE.value]
        if query:
            where += " AND title LIKE ?"
            args.append(f"%{query}%")
        if media_type:
            where += " AND json_extract(snapshot,'$.parsed.media_type')=?"
            args.append(media_type)
        out = []
        for r in self._conn.execute(f"SELECT snapshot, updated_at FROM tasks WHERE {where} ORDER BY updated_at DESC",
                                    args):
            snap = json.loads(r["snapshot"])
            snap["updated_at"] = r["updated_at"]
            out.append(snap)
        return out

    @_locked
    def task_stats(self, since: float) -> dict[str, Any]:
        """统计页：时间窗口内按天、按状态的任务数与任务最多的片名，以及已入库任务记下的体积（文本，如「15.1 GB」）。"""
        daily = [dict(r) for r in self._conn.execute(
            "SELECT substr(datetime(updated_at,'unixepoch','localtime'),1,10) d, state, COUNT(*) n FROM tasks "
            "WHERE updated_at>=? GROUP BY d, state ORDER BY d", (since,))]
        top = [dict(r) for r in self._conn.execute(
            "SELECT title, COUNT(*) count, SUM(state='done') done FROM tasks WHERE updated_at>=? AND title<>'' "
            "GROUP BY title ORDER BY count DESC LIMIT 15", (since,))]
        sizes = [r[0] for r in self._conn.execute(
            "SELECT json_extract(snapshot,'$.parsed.size') FROM tasks WHERE state='done' "
            "AND json_extract(snapshot,'$.parsed.size') IS NOT NULL")]
        return {"daily": daily, "top_titles": top, "done_sizes": sizes}

    @_locked
    def snapshots_since(self, since: float, limit: int = 20000) -> list[dict[str, Any]]:
        """时间窗口内新收任务的快照与当前状态（来源质量统计用）。"""
        out = []
        for r in self._conn.execute(
                "SELECT state, source, snapshot FROM tasks WHERE created_at>=? AND task_id NOT LIKE 'daily-%' "
                "ORDER BY created_at DESC LIMIT ?", (since, limit)):
            snap = json.loads(r["snapshot"])
            snap["state"], snap["source"] = r["state"], r["source"]
            out.append(snap)
        return out

    @_locked
    def titles_since(self, since: float) -> list[dict[str, Any]]:
        """时间窗口内收到的影视资源按片名汇总（不含广告与被过滤的），供日报写「本周新收几部剧」。"""
        return [dict(r) for r in self._conn.execute(
            "SELECT title, json_extract(snapshot,'$.parsed.media_type') media_type, COUNT(*) count, "
            "SUM(state='done') done FROM tasks WHERE created_at>=? AND title IS NOT NULL "
            "AND state NOT IN ('rejected','skipped') GROUP BY title ORDER BY count DESC", (since,))]

    @_locked
    def replaced_file_ids(self) -> set[str]:
        """已入库后又被覆盖保存或洗版替换掉（进了回收站）的文件 ID，取自替换方任务记下的整理冲突。"""
        return {str(r[0]) for r in self._conn.execute(
            "SELECT json_extract(c.value,'$.existing_id') FROM tasks, "
            "json_each(tasks.snapshot,'$.meta.organize.conflicts') c "
            "WHERE tasks.state=? AND json_extract(c.value,'$.action') IN ('replace','upgrade')",
            (TaskState.DONE.value,)) if r[0] is not None}

    @_locked
    def title_media_types(self) -> dict[str, str]:
        """已入库片名 → 识别出的媒体类型（追更提醒显示类型图标用）。"""
        return {r["title"]: r["media_type"] for r in self._conn.execute(
            "SELECT title, json_extract(snapshot,'$.parsed.media_type') media_type FROM tasks "
            "WHERE state=? AND title IS NOT NULL AND media_type IS NOT NULL ORDER BY updated_at",
            (TaskState.DONE.value,))}

    @_locked
    def library_titles(self, media_type: str) -> list[str]:
        """已入库任务的片名（按识别出的媒体类型）。"""
        return [r["title"] for r in self._conn.execute(
            "SELECT DISTINCT title FROM tasks WHERE state=? AND title IS NOT NULL "
            "AND json_extract(snapshot,'$.parsed.media_type')=?", (TaskState.DONE.value, media_type))]

    @_locked
    def work_genres(self, tmdbid: int | None, titles: list[str]) -> tuple[list[int], str | None] | None:
        """最近一条属于这部作品（同一 tmdbid，没有时同片名）的任务记下的（TMDB 题材 ID, 媒体类型）。"""
        if tmdbid:
            where, args = "json_extract(snapshot,'$.parsed.raw_fields.tmdbid')=?", [int(tmdbid)]
        elif titles:
            where, args = f"title IN ({','.join('?' * len(titles))})", list(titles)
        else:
            return None
        row = self._conn.execute(
            f"SELECT json_extract(snapshot,'$.parsed.genre_ids') g, json_extract(snapshot,'$.parsed.media_type') m "
            f"FROM tasks WHERE {where} ORDER BY updated_at DESC LIMIT 1", args).fetchone()
        return (json.loads(row["g"] or "[]"), row["m"]) if row else None

    @_locked
    def titles_for_tmdbid(self, tmdbid: int) -> list[str]:
        """同一 TMDB 条目入库时用过的片名（规范名可能随语言设置变化）。"""
        return [r["title"] for r in self._conn.execute(
            "SELECT DISTINCT title FROM tasks WHERE title IS NOT NULL "
            "AND json_extract(snapshot,'$.parsed.raw_fields.tmdbid')=?", (int(tmdbid),))]

    @_locked
    def summary_since(self, since: float) -> dict[str, Any]:
        """日报数据：时间窗口内新收与完成的任务，以及当前待处理积压。"""
        received = {r["state"]: r["c"] for r in self._conn.execute(
            "SELECT state, COUNT(*) c FROM tasks WHERE created_at>=? GROUP BY state", (since,))}
        done = []
        for r in self._conn.execute(
                "SELECT task_id, title, source, snapshot FROM tasks WHERE state=? AND updated_at>=? "
                "ORDER BY updated_at DESC LIMIT 20", (TaskState.DONE.value, since)):
            ctx = _load_ctx(json.loads(r["snapshot"]))
            done.append({"task_id": r["task_id"], "title": r["title"], "source": r["source"],
                         "media_type": ctx.parsed.media_type if ctx.parsed else None,
                         "pairs": [list(pair) for pair in planned_pairs(ctx)]})
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
