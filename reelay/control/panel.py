"""控制面板逻辑（与具体渠道解耦）。

把「查状态 / 看失败 / 重试 / 看待确认 / 审批」做成纯函数式的命令，
控制 bot 或 CLI 都可复用。真实的 Telegram 控制 bot 只需把命令路由到这里。
"""

from __future__ import annotations

from typing import Any

from ..core.contracts import TaskState
from ..core.pipeline import Pipeline
from ..core.store import Store


class ControlPanel:
    def __init__(self, store: Store, pipeline: Pipeline):
        self.store = store
        self.pipeline = pipeline

    def status(self) -> dict[str, Any]:
        """/status —— 队列积压与各状态计数。"""
        return {"counts": self.store.counts_by_state()}

    def failed(self, limit: int = 20) -> list[dict[str, Any]]:
        """/failed —— 失败任务列表。"""
        return [
            {"task_id": r["task_id"], "title": r["title"], "error": r["error"], "retries": r["retries"]}
            for r in self.store.list_by_state(TaskState.FAILED, limit)
        ]

    def pending(self, limit: int = 20) -> list[dict[str, Any]]:
        """/pending —— 待人工确认 / 待整理列表。"""
        out = []
        for st in (TaskState.PENDING_APPROVAL, TaskState.NEED_MANUAL):
            for r in self.store.list_by_state(st, limit):
                out.append({"task_id": r["task_id"], "state": r["state"], "title": r["title"]})
        return out

    def retry(self, task_id: str) -> bool:
        """把一个 FAILED 任务重新入队。"""
        ctx = self.store.get_task(task_id)
        if not ctx or ctx.state != TaskState.FAILED:
            return False
        self.store.set_state(task_id, TaskState.QUEUED)
        return True

    async def approve(self, task_id: str) -> bool:
        return await self.pipeline.approve(task_id)

    async def reject(self, task_id: str) -> bool:
        return await self.pipeline.reject(task_id)
