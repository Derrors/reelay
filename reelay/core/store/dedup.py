"""三层去重与资源占位：消息级、资源级（分享）、内容级（集）。

内容级的键是「目标前缀:规范片名|年份|TMDB|Sxx|Exx」（作品部分见 core/works.py）：同名的不同作品（如翻拍剧）
不会互相当成已入库；判断是不是同一部按 works.same。
"""
from __future__ import annotations

import re
import time

from .. import works
from ..contracts import PipelineContext
from .base import DEDUP_EPISODE, DEDUP_SHARE, _locked


class DedupRepository:
    """去重记录与转存时的资源占位。"""

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
        identity = ctx.ns("storage").get("identity") or {}
        cid = identity.get("cid")
        provider = identity.get("provider", ctx.ns("storage").get("provider", "p115"))
        prefix = _target_prefix(cid, provider)
        if not identity.get("share_code") or cid is None or (ctx.result or {}).get("storage_verified") is not True:
            raise ValueError("只有完整核验的转存回执可以提交去重记录")
        with self._lock, self._conn:
            self._write_task(ctx)
            keys = [(DEDUP_SHARE, f"{prefix}:{identity['share_code']}")]
            # 跨多季的合集按（季, 集）逐个记录；单季剧集的清单没有 pairs，按季与集数
            pairs = identity.get("pairs") or [[identity["season"], ep] for ep in identity["episodes"]]
            work = identity.get("work") or works.of(ctx.parsed, ctx.meta.get("tmdb"))
            keys += [(DEDUP_EPISODE, f"{prefix}:{self.episode_key(work, season, ep)}")
                     for season, ep in pairs if identity["title"]]
            self._conn.executemany(
                "INSERT OR IGNORE INTO dedup VALUES (?,?,?,?)",
                [(kind, key, ctx.task_id, time.time()) for kind, key in keys],
            )
            self._conn.execute("DELETE FROM resource_claims WHERE task_id=?", (ctx.task_id,))

    def saved_share(self, share: str, cid: str, provider: str = "p115") -> bool:
        return self.seen(DEDUP_SHARE, f"{_target_prefix(cid, provider)}:{share}")

    @_locked
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
    def episode_key(work: dict, season: int | None, ep: int) -> str:
        """内容级去重键（不含目标前缀）；work 见 core/works.py。"""
        return f"{works.key(work)}|S{season if season is not None else 1:02d}|E{ep:02d}"

    @_locked
    def stored_episode_pairs(self, work: dict, cid: str, provider: str = "p115") -> list[list[int]]:
        """这部作品在目标目录已入库的 [季, 集]；分享清单据此排除已入库的集。"""
        prefix = f"{_target_prefix(cid, provider)}:"
        pairs = set()
        for row in self._conn.execute("SELECT key FROM dedup WHERE kind=? AND substr(key, 1, ?)=?",
                                      (DEDUP_EPISODE, len(prefix), prefix)):
            found = split_episode_key(row["key"][len(prefix):])
            if found and works.same(found[0], work):
                pairs.add(found[1:])
        return [list(pair) for pair in sorted(pairs)]

    @_locked
    def stored_series(self) -> list[dict]:
        """所有目标目录中已入库的集，按（作品, 季）汇总：[{work, name, season, episodes}]。

        name 是入库任务的片名（展示用）；查某部作品某一季已入库哪些集用 works.episodes。
        """
        series: dict[tuple[str, int], dict] = {}
        for row in self._conn.execute("SELECT d.key, t.title FROM dedup d LEFT JOIN tasks t ON t.task_id=d.task_id "
                                      "WHERE d.kind=?", (DEDUP_EPISODE,)):
            # 目标前缀为 CID（115）或「provider:CID」
            found = split_episode_key(_TARGET_PREFIX.sub("", row["key"], count=1))
            if not found:
                continue
            work, season, ep = found
            season = season or 1  # 与追更一致：特别篇（第 0 季）并入第 1 季
            entry = series.setdefault((works.key(work), season), {"work": work, "name": row["title"] or work["title"],
                                                                  "season": season, "episodes": set()})
            entry["episodes"].add(ep)
        return list(series.values())


_TARGET_PREFIX = re.compile(r"^(?:[A-Za-z0-9_.-]+:)?\d+:")


def _target_prefix(cid: str | None, provider: str) -> str:
    return str(cid) if provider == "p115" else f"{provider}:{cid}"


def split_episode_key(body: str) -> tuple[dict, int, int] | None:
    """episode_key 的反向：（作品, 季, 集）；格式不对时为 None。"""
    head, _, tail = body.rpartition("|S")
    match = re.fullmatch(r"(\d+)\|E(\d+)", tail)
    if not head or not match:
        return None
    return works.parse_key(head), int(match.group(1)), int(match.group(2))
