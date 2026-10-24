"""总览：运行概况、连接状态、任务列表与任务详情。"""
from __future__ import annotations

import time
import logging
from typing import Any


from ... import __version__
from .. import secret_store
from ..contracts import PipelineContext, TaskState
from .base import NotFoundError, ServiceBase

log = logging.getLogger("reelay.service")


class OverviewMixin(ServiceBase):
    """运行概况与任务查询。"""

    def dashboard(self) -> dict[str, Any]:
        counts = self.store.counts_by_state()
        pending = counts.get(TaskState.PENDING_APPROVAL.value, 0) + counts.get(
            TaskState.NEED_MANUAL.value, 0
        )
        active = sum(counts.get(state.value, 0) for state in (
            TaskState.RECEIVED, TaskState.PARSED, TaskState.QUEUED,
            TaskState.SAVING, TaskState.STORED, TaskState.ORGANIZING,
        ))
        connections = self.connection_status()
        live_ready = connections["telegram"]["configured"] and connections["p115"]["configured"]
        health = self.store.health_status()
        worker = health.get("worker", {})
        source = health.get("source", {})
        live_running = (worker.get("status") == "running" and not worker.get("stale", True)
                        and source.get("status") == "connected" and not source.get("stale", True))
        self.pipeline.reload_config()
        return {
            "counts": counts,
            "pending": pending,
            "active": active,
            "failed": counts.get(TaskState.FAILED.value, 0),
            "done": counts.get(TaskState.DONE.value, 0),
            "connections": connections,
            "runtime": health,
            "alerts": self.store.active_alerts(),
            "recent": self._recent(),
            "deliveries": self.store.delivery_counts(),
            "config_revision": self.pipeline.revision,
            "mode": "live" if live_running else ("not_running" if live_ready else "not_configured"),
            "version": __version__,
        }

    def _recent(self) -> dict[str, int]:
        summary = self.store.summary_since(time.time() - 86400)
        return {"received_24h": summary["received"], "done_24h": summary["done_count"]}

    def connection_status(self) -> dict[str, dict[str, Any]]:

        connections = {
            "telegram": {
                "configured": bool(secret_store.get("telegram_api_id") and secret_store.get("telegram_api_hash")),
                "label": "Telegram 监听",
            },
            "control_bot": {
                "configured": bool(secret_store.get("control_bot_token") and self.pipeline.cfg["telegram"]["owner_id"]),
                "label": "控制 Bot",
            },
            "p115": {
                "configured": self._p115_configured(),
                "label": "115 网盘",
            },
            "llm": {
                "configured": bool(secret_store.get("llm_api_key")),
                "label": "AI 解析",
            },
            "tmdb": {
                "configured": bool(secret_store.get("tmdb_api_key")),
                "label": "TMDB 元数据",
            },
        }
        health = self.store.health_status()
        observed_as = {"telegram": "source", "p115": "p115_cookie"}
        for key, conn in connections.items():
            conn["status"] = "unverified" if conn["configured"] else "not_configured"
            observation = health.get(observed_as.get(key, key))
            if observation and observation.get("status") != "unknown":
                stale = observation["stale"]
                if observation.get("check_interval"):  # 定时探活：超过两个探活周期没有新结果才算过期
                    stale = time.time() - observation["updated_at"] > 2 * observation["check_interval"]
                elif observation.get("event_driven"):  # 只在真实调用时记录，显示最近一次结果
                    stale = False
                conn["status"] = "stale" if stale else observation.get("status", "unverified")
                conn["observed_at"] = observation["updated_at"]
                if observation.get("error"):
                    conn["error"] = observation["error"]
        return connections

    @staticmethod
    def _p115_configured() -> bool:
        """转存主链路依赖cookie；只有OAuth不能当成主流程就绪。"""
        from ..auth115 import get_store
        return get_store().has_cookie

    def list_tasks(self, **filters: Any) -> list[dict[str, Any]]:
        """state 可以是逗号分隔的多个状态，如 "received,queued,saving"。"""
        state_value = filters.pop("state", None)
        states = [TaskState(item.strip()) for item in str(state_value).split(",") if item.strip()] \
            if state_value else None
        return self.store.list_tasks(states=states, **filters)

    def explanation(self, task_id: str, state: str | None = None) -> dict | None:
        """失败解释扩展写下的原因与建议操作（见 plugins/failure_explainer/）；与任务当前状态不符的旧解释不返回。"""
        found = self.pipeline.query("task_explanation", task_id)
        return found if found and (state is None or found.get("state") == state) else None

    def _auto_target(self, ctx: PipelineContext | None) -> dict[str, str | None]:
        """待审批任务不改目录时的去向：auto_dir 是「别名/分类」，auto_path 是网盘真实路径（未知时为 None）。"""
        if not ctx:
            return {"auto_dir": None, "auto_path": None}
        label = self.pipeline.target_preview(ctx)
        path = self.pipeline.display_target(label)
        return {"auto_dir": label, "auto_path": path if path != label else None}

    def get_task(self, task_id: str) -> dict[str, Any]:
        record = self.store.get_task_record(task_id)
        if not record:
            raise NotFoundError("任务不存在")
        ctx = self.store.get_task(task_id)
        record["actions"] = self.task_actions(ctx)
        record.update(self._auto_target(ctx if record["state"] == TaskState.PENDING_APPROVAL.value else None))
        if record["state"] in (TaskState.FAILED.value, TaskState.NEED_MANUAL.value):
            record["explanation"] = self.explanation(task_id, record["state"])
        return record
