"""SQLite 持久层：``Store`` 把各领域的仓储组合在同一个连接上，见 ``base.py``。"""
from __future__ import annotations

import time  # noqa: F401 - 测试按 reelay.core.store.time 打补丁

from .base import (DEDUP_EPISODE, DEDUP_MESSAGE, DEDUP_SHARE, MIGRATIONS, SCHEMA_VERSION, SYSTEM_TASK, Database,
                   _dump_ctx, _load_ctx)
from .cards import CardRepository
from .configs import ConfigSnapshotRepository
from .dedup import DedupRepository
from .feedback import FeedbackRepository
from .follow import FollowRepository
from .health import HealthRepository
from .outbox import OutboxRepository
from .reports import ReportRepository
from .sessions import SessionRepository
from .sources import SourceRepository
from .state import PluginStateRepository
from .tasks import TaskRepository

__all__ = ["Store", "DEDUP_EPISODE", "DEDUP_MESSAGE", "DEDUP_SHARE", "MIGRATIONS", "SCHEMA_VERSION", "SYSTEM_TASK",
           "_dump_ctx", "_load_ctx"]


class Store(TaskRepository, DedupRepository, OutboxRepository, HealthRepository, PluginStateRepository,
            SessionRepository, FeedbackRepository, SourceRepository, FollowRepository, ConfigSnapshotRepository,
            ReportRepository, CardRepository, Database):
    """线程安全的 SQLite 封装：各领域的读写方法来自对应的仓储，连接与锁来自 ``Database``。"""
