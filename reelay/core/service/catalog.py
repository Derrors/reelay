"""媒体库：已入库作品的汇总（管理台媒体库页、MCP 与对话助手共用同一套汇总）、缺集情况与统计。"""
from __future__ import annotations

import time
from typing import Any

from .. import library, release
from .base import ServiceBase

# 管理台媒体库页每部作品需要的字段
_WEB_FIELDS = ("title", "media_type", "year", "tmdbid", "episodes", "episode_pairs", "tasks", "bytes", "poster_path",
               "save_dir_alias", "files", "updated_at", "resolution", "release", "origin_country", "genre_ids")


class CatalogMixin(ServiceBase):
    """媒体库查询与统计。"""

    def _groups(self, query: str | None = None, media_type: str | None = None) -> list[dict[str, Any]]:
        """全部已入库任务按作品汇总（不限条数），最近入库的作品在前。"""
        return library.group_library(self.store.done_snapshots(query or None, media_type or None),
                                     self.store.replaced_file_ids())

    def library(self, *, query: str | None = None, media_type: str | None = None) -> dict[str, Any]:
        """管理台媒体库：每部作品的季集、体积、海报与整理后的文件。"""
        groups = [{key: group[key] for key in _WEB_FIELDS} for group in self._groups(query, media_type)]
        return {"groups": groups, "total_done": sum(group["tasks"] for group in groups),
                "estimated_gb": round(sum(group["bytes"] for group in groups) / 1024 ** 3, 2)}

    def library_query(self, *, query: str | None = None, media_type: str | None = None,
                      min_resolution: str | None = None, origin_country: str | None = None,
                      genre_id: int | None = None, limit: int = 30) -> dict[str, Any]:
        """媒体库问答（MCP、对话助手）：按类型（动漫按 TMDB 动画题材判断）、最低清晰度、地区与题材筛选。"""
        found = library.filter_library(self._groups(query), media_type=media_type, min_resolution=min_resolution,
                                       origin_country=origin_country, genre_id=genre_id)
        return {"total": len(found), "items": found[:max(1, min(limit, 100))]}

    def missing_episodes(self, title: str | None = None) -> dict[str, Any]:
        report = self.pipeline.query("episode_report") or {}
        return {"checked_on": report.get("date"),
                "items": library.missing(report, self.store.stored_series(), title or None)}

    def stats(self, days: int = 30) -> dict[str, Any]:
        """统计页：各状态任务数与已入库的估算体积（全部），最近 days 天按天的任务数与任务最多的片名。"""
        data = self.store.task_stats(time.time() - days * 86400)
        sizes = [release.size_bytes(value) or 0 for value in data.pop("done_sizes")]
        return {"counts": self.store.counts_by_state(), **data,
                "estimated_done_gb": round(sum(sizes) / 1024 ** 3, 2)}
