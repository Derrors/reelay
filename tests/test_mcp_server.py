"""Agent 工具层与 MCP 接入测试：临时 SQLite + 真实 ManagementService，不启动 Worker、不访问网络。"""

from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from reelay import agent_tools
from reelay.agent_tools import AgentTools
from reelay.control import assistant
from reelay.core import auth115, secret_store
from reelay.core.config import normalize_config
from reelay.core.contracts import ParsedResource, PipelineContext, RawMessage, TaskState
from reelay.core.pipeline import Pipeline
from reelay.core.registry import discover
from reelay.core.service import ManagementService
from reelay.core.store import Store
from reelay.mcp_server import build_server, readonly_enabled

from tests.fakes import WebCase, done_task


HAS_MCP = importlib.util.find_spec("mcp") is not None
HAS_WEB = importlib.util.find_spec("fastapi") is not None
READ_TOOLS = {"status", "list_pending", "list_tasks", "get_task", "list_save_dirs", "search_library",
              "missing_episodes", "list_subscriptions", "list_ignored", "find_show"}
WRITE_TOOLS = {"approve", "reject", "retry", "subscribe", "unsubscribe", "resume_subscription", "unignore",
               "correct", "reparse"}
SHARE_URL = "https://115.com/s/swabc123?password=abcd"


class Fixture(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        self.env = patch.dict(os.environ, {}, clear=True)
        self.env.start()
        self.creds = patch.object(auth115, "get_store",
                                  lambda: auth115.CredentialStore(root / "creds.json"))
        self.creds.start()
        raw_cfg = {"save_dirs": {"默认": "1", "动漫": "2"}, "defaults": {"save_to": "默认", "parser": "regex"},
                   "providers": {"notify": []}}
        config_path = root / "config.yaml"
        config_path.write_text(json.dumps(raw_cfg, ensure_ascii=False), encoding="utf-8")
        self.store = Store(root / "reelay.db")
        self.pipeline = Pipeline(discover(), self.store, normalize_config(raw_cfg))
        self.service = ManagementService(self.store, self.pipeline, config_path=str(config_path))
        self.tools = AgentTools(self.service)
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
            is_media=True, media_type="tv", title=title, year=2026, season=1, episodes=[self._seq],
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


class TestRegistry(unittest.TestCase):
    def test_every_tool_has_an_implementation_matching_its_params(self):
        import inspect
        for tool in agent_tools.TOOLS:
            with self.subTest(tool=tool.name):
                method = getattr(AgentTools, tool.name)
                accepted = set(inspect.signature(method).parameters) - {"self"}
                self.assertEqual(accepted, {param.name for param in tool.params})
                self.assertIn("\n", tool.description)  # 中英文各一段
        self.assertEqual({tool.name for tool in agent_tools.TOOLS}, READ_TOOLS | WRITE_TOOLS)
        self.assertEqual({tool.name for tool in agent_tools.TOOLS if tool.write}, WRITE_TOOLS)

    def test_assistant_uses_the_same_definitions(self):
        names = [item["function"]["name"] for item in assistant.TOOLS]
        self.assertEqual(names, [tool.name for tool in agent_tools.TOOLS])
        self.assertEqual(assistant.WRITE_TOOLS, WRITE_TOOLS)
        approve = next(item["function"] for item in assistant.TOOLS if item["function"]["name"] == "approve")
        self.assertEqual(approve["parameters"]["required"], ["task_ids"])
        self.assertEqual(approve["parameters"]["properties"]["task_ids"]["type"], "array")


class TestValidation(Fixture):
    def test_types_enums_and_required_params(self):
        self.assertEqual(self.tools.validate("list_tasks", {"state": "failed", "limit": "5", "extra": 1}),
                         {"state": "failed", "limit": 5})
        self.assertEqual(self.tools.validate("list_tasks", {"query": "  ", "state": None}), {})
        for name, args, message in (
            ("list_tasks", {"state": "bogus"}, "pending_approval"),
            ("list_tasks", {"limit": "many"}, "整数"),
            ("get_task", {}, "缺少参数 task_id"),
            ("approve", {"task_ids": "abc"}, "字符串数组"),
            ("approve", {"task_ids": ["nope"]}, "任务不存在"),
            ("subscribe", {"title": "剧", "season": 120}, "0 到 99"),
            ("nope", {}, "未知工具"),
            ("status", [], "参数必须是对象"),
        ):
            with self.subTest(name=name, args=args), self.assertRaisesRegex(ValueError, message):
                self.tools.validate(name, args)

    def test_write_params_are_checked_against_current_state(self):
        ctx = self._task(TaskState.PENDING_APPROVAL)
        self.assertEqual(self.tools.validate("approve", {"task_ids": [ctx.task_id, ctx.task_id]}),
                         {"task_ids": [ctx.task_id]})
        with self.assertRaisesRegex(ValueError, "目录别名不存在"):
            self.tools.validate("approve", {"task_ids": [ctx.task_id], "save_dir_alias": "不存在"})
        with self.assertRaisesRegex(ValueError, "最多 100"):
            self.tools.validate("reject", {"task_ids": [f"t{i}" for i in range(101)]})
        self.assertEqual(self.tools.validate("subscribe", {"title": " 剧 ", "season": 1, "tmdbid": 0}),
                         {"title": "剧", "season": 1})

    async def test_call_returns_business_errors_as_data(self):
        self.assertEqual(await self.tools.call("get_task", {"task_id": "nope"}),
                         {"ok": False, "error": "任务不存在"})
        result = await self.tools.call("list_tasks", {"state": "bogus"})
        self.assertFalse(result["ok"])


class TestReadTools(Fixture):
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

    def test_list_pending_returns_human_queue_with_advice(self):
        pending = self._task(TaskState.PENDING_APPROVAL)
        manual = self._task(TaskState.NEED_MANUAL, error=f"整理失败 {SHARE_URL}")
        self._task(TaskState.FAILED)
        self._task(TaskState.DONE)
        result = self.tools.list_pending()
        self.assertEqual([item["task_id"] for item in result["pending_approval"]], [pending.task_id])
        self.assertEqual([item["task_id"] for item in result["need_manual"]], [manual.task_id])
        item = result["pending_approval"][0]
        for key in ("advice", "reason", "recommended_version", "subscribed", "waiting_days", "save_dir_alias"):
            self.assertIn(key, item)
        dumped = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("115.com/s/", dumped)
        self.assertNotIn("abcd", dumped)

    def test_list_tasks_filters_and_clamps(self):
        failed = self._task(TaskState.FAILED, error=f"请求失败 {SHARE_URL}")
        self._task(TaskState.DONE)
        items = self.tools.list_tasks(state="failed")["items"]
        self.assertEqual([item["task_id"] for item in items], [failed.task_id])
        self.assertNotIn("password", items[0]["error"])
        with patch.object(self.service, "list_tasks", wraps=self.service.list_tasks) as spy:
            self.tools.list_tasks(limit=1000)
        spy.assert_called_once_with(state=None, query=None, limit=100)

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
        self.assertNotIn("receive_code", detail["parsed"])  # 分享码与访问码不交给 Agent
        self.assertNotIn("share_code", detail["parsed"])
        self.assertEqual(detail["parsed"]["raw_fields"], {"tmdbid": 42})
        dumped = json.dumps(detail, ensure_ascii=False)
        self.assertNotIn("115.com/s/", dumped)
        self.assertNotIn("password", dumped)
        self.assertNotIn("abcd", dumped)

    def test_search_library_filters_organized_works(self):
        done_task(self.store, "1", title="葬送的芙莉莲", media_type="anime", resolution="2160p", genre_ids=(16,),
                  origin_country=("JP",), episodes=(1, 2))
        done_task(self.store, "2", title="繁花", resolution="1080p")
        self._task(TaskState.PENDING_APPROVAL, title="葬送的芙莉莲")
        result = self.tools.search_library(media_type="anime", min_resolution="2160p")
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["title"], "葬送的芙莉莲")
        self.assertEqual(result["items"][0]["seasons"], {"1": [1, 2]})
        self.assertEqual(set(result["items"][0]), set(agent_tools._LIBRARY_FIELDS))
        self.assertEqual([item["title"] for item in self.tools.search_library(query="繁花")["items"]], ["繁花"])

    def test_list_save_dirs_and_subscriptions(self):
        self.assertEqual(self.tools.list_save_dirs(), {"默认": "1", "动漫": "2"})
        self.assertEqual(self.tools.list_subscriptions()["items"], [])
        self.assertIn("items", self.tools.missing_episodes())

    def test_readonly_env_flag(self):
        for value, expected in (("1", True), ("true", True), ("0", False), ("", False)):
            with self.subTest(value=value), patch.dict(os.environ, {"REELAY_MCP_READONLY": value}):
                self.assertEqual(readonly_enabled(), expected)


class TestWriteTools(Fixture):
    async def test_approve_reports_each_task(self):
        first, second = self._task(TaskState.PENDING_APPROVAL), self._task(TaskState.PENDING_APPROVAL)
        done = self._task(TaskState.DONE)
        result = await self.tools.call("approve", {"task_ids": [first.task_id, done.task_id, second.task_id],
                                                   "save_dir_alias": "动漫"})
        self.assertFalse(result["ok"])
        self.assertEqual([(item["task_id"], item["ok"]) for item in result["results"]],
                         [(first.task_id, True), (done.task_id, False), (second.task_id, True)])
        self.assertEqual(result["results"][0]["state"], "queued")
        self.assertIn("待审批", result["results"][1]["error"])
        self.assertEqual(self._state(first), TaskState.QUEUED)
        self.assertEqual(self.store.get_task(first.task_id).save_dir_alias, "动漫")
        self.assertEqual(self._state(done), TaskState.DONE)

    async def test_reject_and_retry(self):
        pending = self._task(TaskState.PENDING_APPROVAL)
        self.assertEqual(await self.tools.call("reject", {"task_ids": [pending.task_id]}),
                         {"ok": True, "results": [{"task_id": pending.task_id, "ok": True, "state": "skipped"}]})
        self.assertFalse((await self.tools.call("reject", {"task_ids": [pending.task_id]}))["ok"])
        failed = self._task(TaskState.FAILED, error="boom", retry_from=TaskState.RECEIVED)
        result = await self.tools.call("retry", {"task_ids": [failed.task_id]})
        self.assertEqual(result["results"][0]["state"], "received")

    async def test_subscribe_lists_pending_tasks_of_the_season(self):
        pending = self._task(TaskState.PENDING_APPROVAL, title="剧")
        record = self.store.get_task(pending.task_id)
        record.ns("tmdb").update(matched=True, tmdbid=42)  # 订阅只认 TMDB 匹配成功的资源
        self.store.save_task(record)
        result = await self.tools.call("subscribe", {"title": "剧", "season": 1, "tmdbid": 42})
        self.assertTrue(result["ok"])
        self.assertEqual(result["pending"], [pending.task_id])
        self.assertEqual([item["title"] for item in self.tools.list_subscriptions()["items"]], ["剧"])
        again = await self.tools.call("subscribe", {"title": "剧", "season": 1, "tmdbid": 42})
        self.assertFalse(again["ok"])
        self.assertIn("已经订阅过", again["error"])


@unittest.skipUnless(HAS_MCP, "未安装 agent 可选依赖")
class TestMcpServer(Fixture):
    async def test_server_registers_all_tools_with_hints(self):
        server = build_server(self.tools, readonly=False)
        tools = {tool.name: tool for tool in await server.list_tools()}
        self.assertEqual(set(tools), READ_TOOLS | WRITE_TOOLS)
        self.assertIn("115", tools["approve"].description)
        hints = {name: tool.annotations.model_dump(by_alias=True, exclude_none=True) for name, tool in tools.items()}
        self.assertTrue(hints["status"]["readOnlyHint"])
        self.assertNotIn("destructiveHint", hints["status"])
        self.assertEqual((hints["approve"]["readOnlyHint"], hints["approve"]["destructiveHint"]), (False, True))
        self.assertFalse(hints["reject"]["destructiveHint"])
        self.assertTrue(hints["find_show"]["openWorldHint"])
        self.assertEqual(hints["approve"]["title"], "批准转存")
        schemas = {name: tool.model_dump(by_alias=True)["inputSchema"] for name, tool in tools.items()}
        self.assertEqual(schemas["list_tasks"].get("required", []), [])
        self.assertIn("pending_approval", json.dumps(schemas["list_tasks"]["properties"]["state"]))
        self.assertEqual(schemas["get_task"]["required"], ["task_id"])

        ctx = self._task(TaskState.PENDING_APPROVAL)
        await server.call_tool("approve", {"task_ids": [ctx.task_id]})
        self.assertEqual(self._state(ctx), TaskState.QUEUED)

    async def test_readonly_server_hides_write_tools(self):
        server = build_server(self.tools, readonly=True)
        names = {tool.name for tool in await server.list_tools()}
        self.assertEqual(names, READ_TOOLS)


class TestTokens(unittest.TestCase):
    def test_mcp_tokens_must_be_long_and_distinct(self):
        store = secret_store.SecretStore(None)
        with self.assertRaisesRegex(ValueError, "至少 24 个字符"):
            store.update({"mcp_token": "short"})
        store.update({"mcp_token": "w" * 24})
        with self.assertRaisesRegex(ValueError, "不能相同"):
            store.update({"mcp_readonly_token": "w" * 24})
        with self.assertRaisesRegex(ValueError, "不能相同"):
            store.update({"mcp_token": "x" * 24, "mcp_readonly_token": "x" * 24})
        store.update({"mcp_token": None, "mcp_readonly_token": "w" * 24})
        self.assertIsNone(store.get("mcp_token"))


@unittest.skipUnless(HAS_MCP and HAS_WEB, "未安装 agent / web 可选依赖")
class TestHttpEndpoint(WebCase):
    WRITE, READ = "w" * 32, "r" * 32
    HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    CONFIG = {"save_dirs": {"默认": "1"}, "defaults": {"parser": "regex", "save_to": "默认"}}

    def rpc(self, token: str | None, method: str, params: dict | None = None, **headers):
        auth = {"Authorization": f"Bearer {token}"} if token else {}
        body = {"jsonrpc": "2.0", "id": 1, "method": method, **({"params": params} if params else {})}
        return self.client.post("/mcp", json=body, headers={**self.HEADERS, **auth, **headers})

    def tools(self, token: str, **headers) -> set[str]:
        init = self.rpc(token, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                              "clientInfo": {"name": "test", "version": "0"}}, **headers)
        self.assertEqual(init.status_code, 200, init.text)
        version = init.json()["result"]["protocolVersion"]
        listed = self.rpc(token, "tools/list", **{"mcp-protocol-version": version}, **headers)
        self.assertEqual(listed.status_code, 200, listed.text)
        return {tool["name"] for tool in listed.json()["result"]["tools"]}

    def test_endpoint_is_off_until_a_token_is_set(self):
        response = self.rpc(None, "initialize")
        self.assertEqual(response.status_code, 404)
        self.assertIn("MCP 访问令牌", response.json()["detail"])

    def test_tokens_select_scope(self):
        secret_store.current().update({"mcp_token": self.WRITE, "mcp_readonly_token": self.READ})
        self.assertEqual(self.rpc(None, "initialize").status_code, 401)
        self.assertEqual(self.rpc("wrong" * 8, "initialize").status_code, 401)
        # 经局域网地址或反代访问时 Host 不是 127.0.0.1，也要能用
        self.assertEqual(self.tools(self.WRITE, Host="nas.lan:28080"), READ_TOOLS | WRITE_TOOLS)
        self.assertEqual(self.tools(self.READ), READ_TOOLS)
        with patch.dict(os.environ, {"REELAY_MCP_READONLY": "1"}):
            self.assertEqual(self.tools(self.WRITE), READ_TOOLS)

    def test_web_login_does_not_grant_mcp_access(self):
        secret_store.current().update({"mcp_token": self.WRITE, "web_password": "pw"})
        login = self.client.post("/api/auth/login", json={"password": "pw"})
        self.assertEqual(login.status_code, 200)
        self.assertEqual(self.rpc(None, "initialize").status_code, 401)


if __name__ == "__main__":
    unittest.main()
