"""MCP 工具层测试：临时 SQLite + 真实 ManagementService，不启动 Worker、不访问网络。"""

from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from reelay.core import auth115
from reelay.core.config import normalize_config
from reelay.core.contracts import ParsedResource, PipelineContext, RawMessage, TaskState
from reelay.core.pipeline import Pipeline
from reelay.core.registry import discover
from reelay.core.service import ManagementService
from reelay.core.store import Store
from reelay.mcp_server import ReelayTools, build_server, readonly_enabled

HAS_MCP = importlib.util.find_spec("mcp") is not None
READ_TOOLS = {"status", "list_tasks", "list_pending", "get_task", "list_save_dirs", "search_library"}
WRITE_TOOLS = {"approve", "reject", "retry"}
SHARE_URL = "https://115.com/s/swabc123?password=abcd"


class TestReelayTools(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        self.env = patch.dict(os.environ, {"REELAY_115_OAUTH_FILE": str(root / "creds.json")}, clear=True)
        self.env.start()
        self.creds = patch.object(auth115, "get_store",
                                  lambda: auth115.CredentialStore(root / "creds.json"))
        self.creds.start()
        raw_cfg = {"save_dirs": {"默认": "1"}, "defaults": {"save_to": "默认", "parser": "regex"},
                   "providers": {"notify": []}}
        config_path = root / "config.yaml"
        config_path.write_text(json.dumps(raw_cfg, ensure_ascii=False), encoding="utf-8")
        self.store = Store(root / "reelay.db")
        self.pipeline = Pipeline(discover(), self.store, normalize_config(raw_cfg))
        self.service = ManagementService(self.store, self.pipeline, config_path=str(config_path))
        self.tools = ReelayTools(self.service)
        self._seq = 0

    def tearDown(self):
        self.store.close()
        self.creds.stop()
        self.env.stop()
        self.tempdir.cleanup()

    def _task(self, state: TaskState, *, title: str = "测试剧", error: str | None = None,
              plan: list[dict] | None = None, trail: list[str] | None = None,
              retry_from: TaskState | None = None) -> PipelineContext:
        self._seq += 1
        ctx = PipelineContext(raw=RawMessage(source="@bot", msg_id=f"msg-{self._seq}",
                                             text=f"{title} {SHARE_URL}"))
        ctx.parsed = ParsedResource(
            is_media=True, media_type="tv", title=title, year=2026, season=1, episodes=[1],
            share_code="swabc123", receive_code="abcd",
            raw_fields={"share_url": SHARE_URL, "llm_raw": {"link": SHARE_URL}, "tmdbid": 42},
        )
        ctx.state = state
        ctx.save_dir_alias = "默认"
        if plan is not None:
            ctx.ns("organize")["plan"] = plan
        if retry_from:
            ctx.ns("task")["retry_from"] = retry_from.value
        ctx.trail = list(trail or [])
        self.store.save_task(ctx, error=error)
        return ctx

    def _state(self, ctx: PipelineContext) -> TaskState:
        return self.store.get_task(ctx.task_id).state

    def test_status_summarizes_dashboard(self):
        self._task(TaskState.PENDING_APPROVAL)
        self._task(TaskState.NEED_MANUAL)
        self._task(TaskState.FAILED, error="boom")
        self._task(TaskState.DONE)
        self.store.heartbeat("worker", status="running", pid=123, inflight=0,
                             concurrency=3, config_revision="rev")
        status = self.tools.status()
        self.assertEqual(status["pending"], 2)
        self.assertEqual(status["failed"], 1)
        self.assertEqual(status["done"], 1)
        self.assertEqual(status["counts"]["pending_approval"], 1)
        self.assertEqual(status["mode"], "not_configured")
        self.assertIn("deliveries", status)
        self.assertEqual(status["runtime"]["worker"], {"status": "running", "stale": False})
        self.assertNotIn("connections", status)

    def test_status_passes_alerts_through(self):
        alerts = [{"key": "p115_cookie", "text": "115 cookie 失效"}]
        with patch.object(self.service, "dashboard", return_value={"runtime": {}, "alerts": alerts}):
            self.assertEqual(self.tools.status()["alerts"], alerts)

    def test_list_pending_returns_human_queue_only(self):
        pending = self._task(TaskState.PENDING_APPROVAL)
        manual = self._task(TaskState.NEED_MANUAL)
        self._task(TaskState.FAILED)
        self._task(TaskState.DONE)
        items = self.tools.list_pending()["items"]
        self.assertEqual({item["task_id"] for item in items}, {pending.task_id, manual.task_id})
        self.assertEqual(len(self.tools.list_pending(limit=0)["items"]), 1)

    def test_list_tasks_filters_and_clamps(self):
        failed = self._task(TaskState.FAILED, error=f"请求失败 {SHARE_URL}")
        self._task(TaskState.DONE)
        items = self.tools.list_tasks(state="failed")["items"]
        self.assertEqual([item["task_id"] for item in items], [failed.task_id])
        self.assertNotIn("password", items[0]["error"])
        with patch.object(self.service, "list_tasks", wraps=self.service.list_tasks) as spy:
            self.tools.list_tasks(limit=1000)
        spy.assert_called_once_with(state=None, query=None, limit=100)
        result = self.tools.list_tasks(state="bogus")
        self.assertFalse(result["ok"])
        self.assertIn("pending_approval", result["error"])

    async def test_approve_queues_pending_task(self):
        ctx = self._task(TaskState.PENDING_APPROVAL)
        self.assertEqual(await self.tools.approve(ctx.task_id), {"ok": True, "state": "queued"})
        self.assertEqual(self._state(ctx), TaskState.QUEUED)

    async def test_approve_rejects_wrong_state_and_bad_input(self):
        done = self._task(TaskState.DONE)
        result = await self.tools.approve(done.task_id)
        self.assertFalse(result["ok"])
        self.assertIn("待审批", result["error"])
        self.assertEqual(self._state(done), TaskState.DONE)

        missing = await self.tools.approve("nope")
        self.assertEqual(missing, {"ok": False, "error": "任务不存在"})

        pending = self._task(TaskState.PENDING_APPROVAL)
        bad_alias = await self.tools.approve(pending.task_id, save_dir_alias="不存在")
        self.assertFalse(bad_alias["ok"])
        self.assertEqual(self._state(pending), TaskState.PENDING_APPROVAL)

    async def test_reject_skips_pending_task_once(self):
        ctx = self._task(TaskState.PENDING_APPROVAL)
        self.assertEqual(await self.tools.reject(ctx.task_id), {"ok": True, "state": "skipped"})
        self.assertFalse((await self.tools.reject(ctx.task_id))["ok"])

    async def test_retry_restores_checkpoint(self):
        ctx = self._task(TaskState.FAILED, error="boom", retry_from=TaskState.RECEIVED)
        self.assertEqual(await self.tools.retry(ctx.task_id), {"ok": True, "state": "received"})
        self.assertFalse((await self.tools.retry(ctx.task_id))["ok"])

    def test_get_task_is_trimmed_and_masked(self):
        trail = [f"00:00:{i:02d} 步骤 {i}" for i in range(19)] + [f"00:01:00 转存准备失败：{SHARE_URL}"]
        plan = [{"src_id": "f1", "src_name": "a.mkv", "size": 1, "sha1": "x",
                 "target_dir": "测试剧 (2026)/Season 01", "target_name": "测试剧 - S01E01.mkv"}]
        ctx = self._task(TaskState.NEED_MANUAL, error="整理失败", plan=plan, trail=trail)
        record = self.store.get_task(ctx.task_id)
        record.ns("storage").update(phase="stored", manifest=[{"id": "f1"}])
        self.store.save_task(record, error="整理失败")

        detail = self.tools.get_task(ctx.task_id)
        self.assertEqual(set(detail), {
            "task_id", "state", "title", "error", "retries", "source", "parsed",
            "save_dir_alias", "decision", "organize_plan", "storage_phase", "trail",
        })
        self.assertEqual(detail["state"], "need_manual")
        self.assertEqual(detail["source"], "@bot")
        self.assertEqual(detail["save_dir_alias"], "默认")
        self.assertEqual(detail["storage_phase"], "stored")
        self.assertEqual(detail["organize_plan"], [
            {"target_dir": "测试剧 (2026)/Season 01", "target_name": "测试剧 - S01E01.mkv"},
        ])
        self.assertEqual(len(detail["trail"]), 15)
        self.assertEqual(detail["trail"][0], "00:00:05 步骤 5")
        self.assertIn("<链接已隐藏>", detail["trail"][-1])
        self.assertEqual(detail["parsed"]["receive_code"], "****")
        self.assertEqual(detail["parsed"]["share_code"], "swabc123")
        self.assertEqual(detail["parsed"]["raw_fields"], {"tmdbid": 42})
        dumped = json.dumps(detail, ensure_ascii=False)
        self.assertNotIn("115.com/s/", dumped)
        self.assertNotIn("password", dumped)
        self.assertNotIn("abcd", dumped)

        self.assertEqual(self.tools.get_task("nope"), {"ok": False, "error": "任务不存在"})

    def test_search_library_returns_done_paths(self):
        plan = [
            {"src_id": "f1", "target_dir": "星际穿越 (2014)", "target_name": "星际穿越 (2014).mkv"},
            {"src_id": "f2", "target_dir": "", "target_name": "loose.mkv"},
        ]
        done = self._task(TaskState.DONE, title="星际穿越", plan=plan)
        self._task(TaskState.PENDING_APPROVAL, title="星际穿越")
        self._task(TaskState.DONE, title="其他电影")
        items = self.tools.search_library("星际")["items"]
        self.assertEqual(items, [{
            "task_id": done.task_id,
            "title": "星际穿越",
            "save_dir_alias": "默认",
            "paths": ["星际穿越 (2014)/星际穿越 (2014).mkv", "loose.mkv"],
        }])

    def test_list_save_dirs(self):
        self.assertEqual(self.tools.list_save_dirs(), {"默认": "1"})

    def test_readonly_env_flag(self):
        for value, expected in (("1", True), ("true", True), ("0", False), ("", False)):
            with self.subTest(value=value), patch.dict(os.environ, {"REELAY_MCP_READONLY": value}):
                self.assertEqual(readonly_enabled(), expected)

    @unittest.skipUnless(HAS_MCP, "未安装 agent 可选依赖")
    async def test_server_registers_all_tools(self):
        server = build_server(self.tools, readonly=False)
        tools = {tool.name: tool for tool in await server.list_tools()}
        self.assertEqual(set(tools), READ_TOOLS | WRITE_TOOLS)
        self.assertIn("115", tools["approve"].description)

        ctx = self._task(TaskState.PENDING_APPROVAL)
        await server.call_tool("approve", {"task_id": ctx.task_id})
        self.assertEqual(self._state(ctx), TaskState.QUEUED)

    @unittest.skipUnless(HAS_MCP, "未安装 agent 可选依赖")
    async def test_readonly_server_hides_write_tools(self):
        server = build_server(self.tools, readonly=True)
        names = {tool.name for tool in await server.list_tools()}
        self.assertEqual(names, READ_TOOLS)


if __name__ == "__main__":
    unittest.main()
