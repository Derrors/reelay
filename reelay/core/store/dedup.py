"""三层去重与资源占位：消息级、资源级（分享）、内容级（集）。"""
from __future__ import annotations

import re
import time

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
    def episode_key(title: str, season: int | None, ep: int) -> str:
        return f"{title}|S{season if season is not None else 1:02d}|E{ep:02d}"

    @_locked
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
        return [list(pair) for pair in sorted(pairs)]

    @_locked
    def stored_series(self) -> dict[tuple[str, int], set[int]]:
        """按 (标题, 季) 汇总所有目标目录中已入库的集，供追更检查使用。"""
        series: dict[tuple[str, int], set[int]] = {}
        for row in self._conn.execute("SELECT key FROM dedup WHERE kind=?", (DEDUP_EPISODE,)):
            # 键是「目标前缀:标题|Sxx|Exx」，目标前缀为 CID（115）或「provider:CID」
            body = _TARGET_PREFIX.sub("", row["key"], count=1)
            title, _, rest = body.rpartition("|S")
            pair = _episode_pair("S" + rest) if title else None
            if pair:
                season, ep = pair
                series.setdefault((title, season or 1), set()).add(ep)
        return series


_TARGET_PREFIX = re.compile(r"^(?:[A-Za-z0-9_.-]+:)?\d+:")


def _target_prefix(cid: str | None, provider: str) -> str:
    return str(cid) if provider == "p115" else f"{provider}:{cid}"


def _episode_pair(text: str) -> tuple[int, int] | None:
    """解析 episode_key 的 ``S01|E02`` 尾部。"""
    match = re.fullmatch(r"S(\d+)\|E(\d+)", text)
    return (int(match.group(1)), int(match.group(2))) if match else None
