"""MCP 服务：让外部 Agent（Claude Code / Claude Desktop）通过 stdio 操作 Reelay。

与 Web 管理台同一模型：本进程只读写 SQLite 中的任务状态，不启动 Worker；
批准/重试后由正在运行的 Worker 领取并执行真实的 115 转存。
设置 ``REELAY_MCP_READONLY=1`` 时不注册任何写操作工具。

接入示例（Claude Code）::

    claude mcp add reelay -- /path/to/reelay/.venv/bin/reelay-mcp
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from .core.config import load_config, load_env
from .core.contracts import TaskState
from .core.pipeline import Pipeline
from .core.registry import discover
from .core.service import ConflictError, ManagementService, NotFoundError
from .core.store import Store

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# 服务层约定的业务错误；返回给 Agent 而不是抛成协议错误。
_EXPECTED = (ConflictError, NotFoundError, ValueError)
_STATES = tuple(state.value for state in TaskState)
_MAX_LIMIT = 100
_TRAIL_LINES = 15
# raw_fields 里的 share_url / llm_raw 可能带完整分享链接与访问码，只放行无敏感值的键。
_SAFE_RAW_FIELDS = ("tmdbid", "parser_confidence")
_HEALTH_FIELDS = ("status", "stale", "error", "config_error")
_URL_RE = re.compile(r"https?://\S+")

_INSTRUCTIONS = """\
Reelay 监听 Telegram 资源 Bot，解析 115 分享并在审批后转存、整理入库。
Reelay watches Telegram resource bots, parses 115 shares, and transfers them after approval.
建议流程 / Suggested flow: status → list_pending → get_task → approve / reject.
approve / retry 只修改任务状态，由正在运行的 Reelay worker 执行真实 115 转存。
approve / retry only change task state; the running Reelay worker performs the real 115 transfer.
"""


def _failure(exc: Exception) -> dict[str, Any]:
    return {"ok": False, "error": str(exc)}


def _clamp(limit: int) -> int:
    return max(1, min(int(limit), _MAX_LIMIT))


def _scrub(text: str | None) -> str | None:
    """异常文本可能带含访问码的请求 URL，对外一律隐藏。"""
    return _URL_RE.sub("<链接已隐藏>", text) if text else text


def _brief(item: dict[str, Any]) -> dict[str, Any]:
    return {**item, "error": _scrub(item.get("error"))}


def _safe_parsed(parsed: dict[str, Any] | None) -> dict[str, Any] | None:
    if not parsed:
        return None
    parsed = dict(parsed)
    raw = parsed.pop("raw_fields", None) or {}
    kept = {key: raw[key] for key in _SAFE_RAW_FIELDS if key in raw}
    if kept:
        parsed["raw_fields"] = kept
    return parsed


def _plan(context: dict[str, Any]) -> list[dict[str, Any]]:
    plan = ((context.get("meta") or {}).get("organize") or {}).get("plan") or []
    return [{"target_dir": item.get("target_dir"), "target_name": item.get("target_name")}
            for item in plan]


class ReelayTools:
    """MCP 工具的业务实现；与协议无关，返回值均可 JSON 序列化。"""

    def __init__(self, service: ManagementService) -> None:
        self.service = service
        self.store = service.store

    def status(self) -> dict[str, Any]:
        data = self.service.dashboard()
        summary = {key: data.get(key) for key in (
            "mode", "counts", "pending", "active", "failed", "done", "deliveries",
        )}
        summary["runtime"] = {
            name: {key: _scrub(item[key]) if isinstance(item[key], str) else item[key]
                   for key in _HEALTH_FIELDS if item.get(key) not in (None, "")}
            for name, item in (data.get("runtime") or {}).items()
        }
        if "alerts" in data:
            summary["alerts"] = data["alerts"]
        return summary

    def list_tasks(self, state: str | None = None, query: str | None = None,
                   limit: int = 20) -> dict[str, Any]:
        if state and state not in _STATES:
            return {"ok": False, "error": f"未知状态：{state}；可选：{', '.join(_STATES)}"}
        items = self.service.list_tasks(state=state or None, query=query or None,
                                        limit=_clamp(limit))
        return {"items": [_brief(item) for item in items]}

    def list_pending(self, limit: int = 20) -> dict[str, Any]:
        limit = _clamp(limit)
        items = [item for state in (TaskState.PENDING_APPROVAL, TaskState.NEED_MANUAL)
                 for item in self.service.list_tasks(state=state.value, limit=limit)]
        items.sort(key=lambda item: item["updated_at"], reverse=True)
        return {"items": [_brief(item) for item in items[:limit]]}

    def get_task(self, task_id: str) -> dict[str, Any]:
        try:
            record = self.service.get_task(task_id)
        except NotFoundError as exc:
            return _failure(exc)
        context = record.get("context") or {}
        meta = context.get("meta") or {}
        return {
            "task_id": record["task_id"],
            "state": record["state"],
            "title": record.get("title"),
            "error": _scrub(record.get("error")),
            "retries": record.get("retries"),
            "source": record.get("source"),
            "parsed": _safe_parsed(context.get("parsed")),
            "save_dir_alias": context.get("save_dir_alias"),
            "decision": context.get("decision"),
            "organize_plan": _plan(context),
            "storage_phase": (meta.get("storage") or {}).get("phase"),
            "trail": [_scrub(line) for line in (context.get("trail") or [])[-_TRAIL_LINES:]],
        }

    async def approve(self, task_id: str, save_dir_alias: str | None = None) -> dict[str, Any]:
        try:
            await self.service.approve(task_id, save_dir_alias=save_dir_alias or None)
        except _EXPECTED as exc:
            return _failure(exc)
        return self._done(task_id)

    async def reject(self, task_id: str) -> dict[str, Any]:
        try:
            await self.service.reject(task_id)
        except _EXPECTED as exc:
            return _failure(exc)
        return self._done(task_id)

    async def retry(self, task_id: str) -> dict[str, Any]:
        try:
            await self.service.retry(task_id)
        except _EXPECTED as exc:
            return _failure(exc)
        return self._done(task_id)

    def list_save_dirs(self) -> dict[str, str]:
        return {alias: str(cid) for alias, cid in self.service.get_config()["save_dirs"].items()}

    def search_library(self, query: str, limit: int = 20) -> dict[str, Any]:
        items = self.service.list_tasks(state=TaskState.DONE.value, query=query or None,
                                        limit=_clamp(limit))
        results = []
        for item in items:
            try:
                context = self.service.get_task(item["task_id"]).get("context") or {}
            except NotFoundError:  # 列表与详情之间被删除
                continue
            results.append({
                "task_id": item["task_id"],
                "title": item.get("title"),
                "save_dir_alias": context.get("save_dir_alias"),
                "paths": ["/".join(part for part in (entry["target_dir"], entry["target_name"]) if part)
                          for entry in _plan(context)],
            })
        return {"items": results}

    def _done(self, task_id: str) -> dict[str, Any]:
        ctx = self.store.get_task(task_id)
        return {"ok": True, "state": ctx.state.value if ctx else None}


def _server_class():
    try:  # mcp 2.x 将 FastMCP 更名为 MCPServer
        from mcp.server.mcpserver import MCPServer
        return MCPServer
    except ImportError:
        pass
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        raise RuntimeError('MCP SDK 不可用，请执行 pip install -e ".[agent]"') from exc
    return FastMCP


def build_server(tools: ReelayTools, *, readonly: bool):
    """注册工具；全部声明为 async，SQLite 访问留在事件循环线程，不进线程池。"""
    # SDK 构造时会按 log_level 配置根日志；默认 WARNING，避免 stderr 被 INFO 刷屏。
    level = os.getenv("REELAY_LOG_LEVEL", "WARNING").upper()
    level = level if level in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"} else "WARNING"
    server = _server_class()("reelay", instructions=_INSTRUCTIONS, log_level=level)

    async def status() -> dict[str, Any]:
        """Reelay 运行概况：各状态任务数、待处理/执行中/失败/完成数、运行模式、通知投递与 worker/消息源/115 健康状态。
        Overview: task counts by state, pending/active/failed/done totals, run mode
        (live / not_running / not_configured / simulation), delivery counts, and
        worker / source / 115 auth health (stale=true means no recent heartbeat)."""
        return tools.status()

    async def list_tasks(state: str | None = None, query: str | None = None,
                         limit: int = 20) -> dict[str, Any]:
        """按状态/关键字列出任务，最近更新在前；query 匹配标题、来源、任务 ID 或错误信息。
        List tasks, newest first. Optional state filter, one of: received, parsed,
        pending_approval, queued, rejected, skipped, saving, stored, organizing,
        need_manual, done, failed. query matches title/source/task_id/error. limit 1-100."""
        return tools.list_tasks(state=state, query=query, limit=limit)

    async def list_pending(limit: int = 20) -> dict[str, Any]:
        """列出需要人处理的任务：pending_approval（待审批）与 need_manual（待人工核验/整理）。
        List tasks waiting for a human: pending_approval (approve/reject them) and
        need_manual (transfer result or organize step needs manual attention). limit 1-100."""
        return tools.list_pending(limit=limit)

    async def get_task(task_id: str) -> dict[str, Any]:
        """查看任务详情：解析结果（访问码已脱敏）、目标目录别名、决策、整理计划目标路径、转存阶段与最近 15 条轨迹。
        Task details: parsed resource (receive code masked), save_dir_alias, decision,
        organize plan target paths, storage phase, and the last 15 trail lines."""
        return tools.get_task(task_id)

    async def list_save_dirs() -> dict[str, str]:
        """列出可用的转存目录别名及其 115 目录 CID，approve 的 save_dir_alias 取这里的键。
        Map of save-dir alias to 115 folder CID; use an alias as approve's save_dir_alias."""
        return tools.list_save_dirs()

    async def search_library(query: str, limit: int = 20) -> dict[str, Any]:
        """在已入库（done）任务中按片名搜索，返回目录别名与整理后的文件路径，可用于判断是否已有某资源。
        Search completed (done) tasks by title; returns save_dir_alias and organized
        file paths (relative to the save dir). Useful to check whether something is
        already in the library. limit 1-100."""
        return tools.search_library(query, limit=limit)

    for fn in (status, list_tasks, list_pending, get_task, list_save_dirs, search_library):
        server.add_tool(fn)
    if readonly:
        return server

    async def approve(task_id: str, save_dir_alias: str | None = None) -> dict[str, Any]:
        """批准待审批（pending_approval）任务，可选改投到 list_save_dirs 中的另一个目录别名。
        注意：批准后任务进入 queued，正在运行的 Reelay worker 会执行真实的 115 转存与整理，此处无法撤销。
        Approve a pending_approval task, optionally overriding the target save-dir alias.
        This queues a REAL 115 cloud transfer that the running Reelay worker executes;
        it cannot be undone from here. Returns {ok, state} or {ok: false, error}."""
        return await tools.approve(task_id, save_dir_alias=save_dir_alias)

    async def reject(task_id: str) -> dict[str, Any]:
        """拒绝待审批任务：标记为 skipped，不会转存。
        Reject a pending_approval task: it is marked skipped and nothing is transferred.
        Returns {ok, state} or {ok: false, error}."""
        return await tools.reject(task_id)

    async def retry(task_id: str) -> dict[str, Any]:
        """重试失败（failed）任务：按记录的检查点重新排队，worker 会再次执行，可能再次发起真实 115 转存。
        Retry a failed task from its recorded checkpoint. The running worker picks it up
        again and may perform a real 115 transfer. Returns {ok, state} or {ok: false, error}."""
        return await tools.retry(task_id)

    for fn in (approve, reject, retry):
        server.add_tool(fn)
    return server


def create_service(*, db_path: str | None = None, config_path: str | None = None) -> ManagementService:
    """与 web.create_app 相同的装配方式，但不创建 Worker。"""
    load_env(str(PROJECT_ROOT / ".env"))
    config_file = config_path or os.getenv("REELAY_CONFIG_PATH", str(PROJECT_ROOT / "config.yaml"))
    cfg = load_config(config_file)
    registry = discover()
    registry.validate_config(cfg)
    store = Store(db_path or os.getenv("REELAY_DB_PATH", str(PROJECT_ROOT / "data/reelay.db")))
    pipeline = Pipeline(registry, store, cfg)
    return ManagementService(store, pipeline, config_path=config_file)


def readonly_enabled() -> bool:
    return os.getenv("REELAY_MCP_READONLY", "").lower() in {"1", "true", "yes"}


def main() -> None:
    service = create_service()
    server = build_server(ReelayTools(service), readonly=readonly_enabled())
    try:
        server.run()  # stdio；stdout 专用于协议，日志只能走 stderr
    finally:
        service.store.close()


if __name__ == "__main__":
    main()
