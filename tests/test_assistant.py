"""AI 助手：对话调用管理工具、Bot 上的助手入口。"""
from __future__ import annotations

import unittest
import json
import types

from reelay.agent_tools import AgentTools
from reelay.control.assistant import Assistant
from reelay.control.bot import ControlBot
from reelay.core.contracts import TaskState

from tests.fakes import ServiceCase, done_task, tv_ctx


class FakeToolAI:
    """按顺序返回预置的回复：{\"tools\": [(名称, 参数)]} 表示调用工具，{\"text\": ...} 表示直接回答。"""

    def __init__(self, *steps):
        self.steps = list(steps)
        self.calls = []
        self.chat = types.SimpleNamespace(completions=self)

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        step = self.steps.pop(0) if len(self.steps) > 1 else self.steps[0]
        calls = [types.SimpleNamespace(id=f"call{i}", function=types.SimpleNamespace(
            name=name, arguments=json.dumps(args, ensure_ascii=False))) for i, (name, args) in enumerate(step.get("tools", []))]
        message = types.SimpleNamespace(content=step.get("text"), tool_calls=calls or None)
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=message)],
                                     usage=types.SimpleNamespace(prompt_tokens=10, completion_tokens=5))


class TestAssistant(ServiceCase):
    def setUp(self):
        super().setUp()
        self.assistant = Assistant(self.service)

    def pending(self, msg_id, **fields):
        ctx = tv_ctx(msg_id=msg_id, **fields)
        ctx.state = TaskState.PENDING_APPROVAL
        self.store.save_task(ctx)
        return ctx

    async def test_questions_are_answered_with_query_tools(self):
        done_task(self.store, "1", title="葬送的芙莉莲", media_type="anime", resolution="2160p", genre_ids=(16,),
                  origin_country=("JP",))
        done_task(self.store, "2", title="繁花", resolution="2160p")
        self.parser._client = FakeToolAI(
            {"tools": [("search_library", {"media_type": "anime", "min_resolution": "2160p", "origin_country": "JP"})]},
            {"text": "你有 1 部 4K 日本动画：《葬送的芙莉莲》"})
        reply = await self.assistant.handle("我有哪些 4K 日本动画？")
        self.assertEqual((reply.text, reply.plan_id), ("你有 1 部 4K 日本动画：《葬送的芙莉莲》", None))
        tool_message = self.parser._client.calls[1]["messages"][-1]
        self.assertEqual(tool_message["role"], "tool")
        self.assertEqual([item["title"] for item in json.loads(tool_message["content"])["items"]], ["葬送的芙莉莲"])
        usage = self.service.llm_usage()["purposes"][0]
        self.assertEqual((usage["purpose"], usage["calls"], usage["prompt_tokens"]), ("assistant", 2, 20))

    async def test_write_actions_wait_for_confirmation(self):
        first, second = self.pending("1"), self.pending("2", episodes=(4,))
        self.parser._client = FakeToolAI(
            {"tools": [("list_pending", {})]},
            {"tools": [("approve", {"task_ids": [first.task_id, second.task_id], "save_dir_alias": "动漫"})]},
            {"text": "这两集我列好了，请确认。"})
        reply = await self.assistant.handle("把《剧》这几集都批了，存到动漫")
        self.assertTrue(reply.plan_id)
        self.assertIn("转存：《剧》E03、《剧》E04（存到「动漫」）", reply.text)
        self.assertEqual(self.store.get_task(first.task_id).state, TaskState.PENDING_APPROVAL)  # 还没执行
        self.assertEqual(await self.assistant.execute(reply.plan_id), "✅ 已转存 2 条")
        self.assertEqual(self.store.get_task(first.task_id).state, TaskState.QUEUED)
        self.assertEqual(self.store.feedback_rows()[0]["label"]["save_dir_alias"], "动漫")
        self.assertIn("已过期或已处理", await self.assistant.execute(reply.plan_id))

    async def test_invalid_write_requests_are_reported_back_to_the_model(self):
        self.parser._client = FakeToolAI({"tools": [("reject", {"task_ids": ["nope"]})]}, {"text": "没有找到这个任务"})
        reply = await self.assistant.handle("忽略 nope")
        self.assertIsNone(reply.plan_id)
        result = json.loads(self.parser._client.calls[1]["messages"][-1]["content"])
        self.assertEqual(result["ok"], False)
        self.assertIn("任务不存在", result["error"])

    async def test_subscribe_through_chat_and_cancel(self):
        self.parser._client = FakeToolAI({"tools": [("subscribe", {"title": "剧", "season": 1, "tmdbid": 100})]},
                                         {"text": "请确认订阅。"})
        reply = await self.assistant.handle("我在追《剧》")
        self.assertIn("订阅《剧》第 1 季", reply.text)
        self.assertTrue(self.assistant.cancel(reply.plan_id))
        self.assertEqual(self.service.list_subscriptions()["items"], [])
        self.parser._client = FakeToolAI({"tools": [("subscribe", {"title": "剧", "season": 1, "tmdbid": 100})]},
                                         {"text": "请确认订阅。"})
        reply = await self.assistant.handle("我在追《剧》")
        self.pending("1")
        result = await self.assistant.execute(reply.plan_id)
        self.assertIn("已订阅《剧》第 1 季，待审批里还有 1 条属于这部剧", result)

    async def test_bot_attaches_confirmation_buttons_and_degrades_without_ai(self):
        self.pending("1")
        bot = ControlBot(self.service)
        bot.assistant = self.assistant
        task_id = self.store.list_by_state(TaskState.PENDING_APPROVAL, 1)[0]["task_id"]
        self.parser._client = FakeToolAI({"tools": [("reject", {"task_ids": [task_id]})]}, {"text": "请确认"})
        text, buttons = await bot.chat("忽略它")
        self.assertEqual([label for label, _ in buttons], ["✅ 执行", "取消"])
        self.assertEqual(await bot.confirm(buttons[1][1]), "已取消，没有执行任何操作。")
        self.assertEqual(self.store.get_task(task_id).state, TaskState.PENDING_APPROVAL)
        self.parser._client = None
        text, buttons = await bot.chat("在吗")
        self.assertIn("未配置 AI", text)
        self.assertIsNone(buttons)


class TestAssistantTools(ServiceCase):
    async def test_new_write_tools(self):
        task = await self.pending("6")
        tools = AgentTools(self.service)
        result = await tools.call("correct", {"task_id": task.task_id, "title": "繁花", "episodes": "1-2,5"})
        self.assertTrue(result["ok"])
        self.assertEqual(self.store.get_task(task.task_id).parsed.episodes, [1, 2, 5])
        self.assertFalse((await tools.call("correct", {"task_id": task.task_id}))["ok"])
        self.assertFalse((await tools.call("correct", {"task_id": task.task_id, "episodes": "a-b"}))["ok"])
        self.subscribe({"title": "繁花", "season": 1})
        self.assertTrue((await tools.call("unsubscribe", {"title": "繁花", "season": 1}))["ok"])
        self.assertEqual(self.store.subscriptions(), [])
        self.assertTrue((await tools.call("reparse", {"task_ids": [task.task_id]}))["ok"])


if __name__ == "__main__":
    unittest.main()
