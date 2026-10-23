"""第二步的对话与运维能力：控制 Bot 对话助手、媒体库问答、来源质量、失败解释、日报叙述化。全部本地模拟。"""
from __future__ import annotations

import json
import os
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from reelay.control.assistant import Assistant
from reelay.control.bot import ControlBot
from reelay.core import library
from reelay.core.contracts import ParsedResource, PipelineContext, RawMessage, TaskState
from reelay.plugins.episode_tracker import overdue_episodes

from tests.test_ai_assistance import tv_ctx
from tests.test_ai_feedback import Base, FakeAI, media, message


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


def done_task(store, msg_id, *, title, media_type="tv", resolution="1080p", episodes=(1,), season=1, genre_ids=(18,),
              origin_country=("CN",), tmdbid=None):
    ctx = message(msg_id=msg_id)
    ctx.parsed = ParsedResource(**{**media(title=title, media_type=media_type, resolution=resolution,
                                           episodes=list(episodes), season=season if episodes else None,
                                           genre_ids=list(genre_ids), origin_country=list(origin_country))},
                                share_code=f"s{msg_id}")
    if tmdbid:
        ctx.parsed.raw_fields["tmdbid"] = tmdbid
    ctx.save_dir_cid, ctx.save_dir_alias = "2", "剧集"
    ctx.ns("storage")["identity"] = {"cid": "2", "share_code": f"s{msg_id}", "title": title,
                                     "season": ctx.parsed.season, "episodes": list(episodes)}
    ctx.state = TaskState.STORED
    ctx.result = {"storage_verified": True}
    store.save_task(ctx)
    store.finish_transfer(ctx)
    ctx.state = TaskState.DONE
    store.save_task(ctx)
    return ctx


class TestAssistant(Base):
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


class TestLibraryAndQuality(Base):
    async def test_library_filters_and_missing_episodes(self):
        done_task(self.store, "1", title="葬送的芙莉莲", media_type="tv", resolution="2160p", genre_ids=(16,),
                  origin_country=("JP",), episodes=(1, 2, 4))
        done_task(self.store, "2", title="电影", media_type="movie", resolution="1080p", episodes=(),
                  origin_country=("US",))
        anime = self.service.library_query(media_type="anime", min_resolution="2160p", origin_country="jp")
        self.assertEqual([item["title"] for item in anime["items"]], ["葬送的芙莉莲"])
        self.assertEqual(anime["items"][0]["seasons"], {"1": [1, 2, 4]})
        self.assertEqual(self.service.library_query(min_resolution="2160p", media_type="movie")["total"], 0)
        with self.assertRaises(ValueError):
            self.service.library_query(min_resolution="8k")
        missing = self.service.missing_episodes("芙莉莲")["items"]
        self.assertEqual(missing[0]["missing"], [3])
        self.assertIn("没有 TMDB 播出信息", missing[0]["note"])

    def test_overdue_episodes_skip_the_first_day(self):
        dates = {7: "2026-10-16", 8: "2026-10-18", 9: "2026-10-19"}
        self.assertEqual(overdue_episodes([7, 8, 9], dates, "2026-10-19"), [{"episode": 7, "days": 3}])

    async def test_source_quality_stats_and_ai_advice(self):
        for i in range(12):
            ad = message(text=f"推广：进群领资源 {i}", msg_id=f"ad{i}", source="@noisy")
            ad.parsed = ParsedResource(is_ad=True)
            ad.state = TaskState.REJECTED
            self.store.save_task(ad)
        done_task(self.store, "1", title="剧")
        for i in range(10):  # 确认前就过期的分享：单独统计，也不作为被拒样例交给 AI
            gone = message(text=f"剧 第{i}集 已过期", msg_id=f"gone{i}", source="@slow")
            gone.parsed = ParsedResource(is_media=True, title="剧", media_type="tv")
            gone.ns("share").update(status="unavailable", note="分享无法访问：链接已过期")
            gone.state = TaskState.SKIPPED
            self.store.save_task(gone)
        stats = {item["source"]: item for item in self.service.source_quality()["sources"]}
        self.assertEqual((stats["@slow"]["unavailable"], stats["@slow"]["unavailable_rate"]), (10, 1.0))
        self.assertIn("分享常在确认前失效", stats["@slow"]["flags"])
        self.assertEqual((stats["@noisy"]["received"], stats["@noisy"]["ad_rate"]), (12, 1.0))
        self.assertIn("广告占一半以上", stats["@noisy"]["flags"])
        self.assertEqual(stats["@bot"]["resolution"], "1080p")
        self.store.add_feedback(task_id="x", source="@noisy", kind="approve", text="资源 剧 第3集", parsed={}, label={})
        self.parser._client = FakeAI({"items": [{"source": "@noisy", "summary": "几乎都是推广", "suggestions": ["加屏蔽词"],
                                                 "add_block_keywords": ["进群", "剧"]}]})
        advice = await self.service.source_quality_advice()
        self.assertEqual(advice["items"][0]["dropped_keywords"], ["剧"])  # 批准过的消息里出现过，不能屏蔽
        self.assertEqual(advice["changes"], [{"section": "来源 @noisy", "kind": "add", "text": "屏蔽关键词：进群"}])
        noisy = next(s for s in advice["sections"]["sources"] if s["bot"] == "@noisy")
        self.assertEqual(noisy["filters"]["block_keywords"], ["进群"])
        payload = json.loads(self.parser._client.calls[0]["messages"][1]["content"])
        self.assertTrue(all("<链接>" in s or "http" not in s for s in payload[0]["rejected_samples"]))
        slow = next(item for item in payload if item["source"] == "@slow")
        self.assertEqual(slow["rejected_samples"], [])

    def test_rates_are_none_without_data(self):
        self.assertEqual(library.source_quality([], {}), [])


class TestFailureExplainer(Base):
    def setUp(self):
        super().setUp()
        self.explainer = next(x for x in self.registry.listeners["on_failed"] if x.name == "failure_explainer")
        self.explainer._client = None

    async def run_on(self, ctx):
        with self.pl._scope(self.pl.cfg):
            await self.explainer.handle(ctx)
        return self.store.state_get("failure_explainer", f"task:{ctx.task_id}")

    def notices(self):
        return [json.loads(r[0])["text"] for r in self.store._conn.execute(
            "SELECT snapshot FROM outbox WHERE kind='notice' AND snapshot LIKE '%failure_explainer%'")]

    async def test_unknown_transfer_result_points_to_reconcile_and_notifies_once(self):
        ctx = tv_ctx()
        ctx.state = TaskState.NEED_MANUAL
        ctx.ns("storage").update(phase="receiving", manifest={"files": [{"name": "a.mkv"}]})
        self.store.save_task(ctx, error="转存回执未确认全部文件落地")
        explanation = await self.run_on(ctx)
        self.assertEqual((explanation["action"], explanation["label"], explanation["source"]),
                         ("reconcile", "核验转存", "rule"))
        await self.run_on(ctx)
        self.assertEqual(len(self.notices()), 1)
        self.assertIn("建议：核验转存", self.notices()[0])
        self.assertEqual(self.service.get_task(ctx.task_id)["explanation"]["action"], "reconcile")
        self.assertEqual(self.service.inbox("need_manual")["items"][0]["explanation"]["action"], "reconcile")

    async def test_library_conflict_points_to_delete_or_overwrite(self):
        ctx = tv_ctx()
        ctx.state = TaskState.NEED_MANUAL
        ctx.ns("storage").update(phase="stored", landed=[{"id": "9", "name": "a.mkv"}])
        ctx.ns("organize").update(error="库里已有「剧 - S01E01.mkv」，请选择删除转存文件或覆盖保存", pending_conflict={
            "target_dir": "剧 (2026)/Season 01", "target_name": "剧 - S01E01.mkv",
            "existing": {"id": "1", "name": "剧 - S01E01.mkv", "size": 9},
            "incoming": {"id": "9", "name": "a.mkv", "size": 10}})
        self.store.save_task(ctx)
        explanation = await self.run_on(ctx)
        self.assertEqual((explanation["action"], explanation["label"]), ("resolve_conflict", "删除转存文件或覆盖保存"))
        self.assertIn("库里已有「剧 - S01E01.mkv」", explanation["text"])
        item = self.service.inbox("need_manual")["items"][0]
        self.assertEqual(item["organize_conflict"]["existing"]["name"], "剧 - S01E01.mkv")

    async def test_failures_that_will_retry_stay_quiet(self):
        ctx = tv_ctx()
        ctx.state = TaskState.QUEUED  # Worker 已安排自动重试
        self.store.save_task(ctx)
        self.assertIsNone(await self.run_on(ctx))
        ctx.state = TaskState.FAILED
        self.store.save_task(ctx, error="转存准备失败：ConnectionError")
        self.assertEqual((await self.run_on(ctx))["action"], "retry")
        self.assertEqual(self.notices(), [])
        for _ in range(4):
            self.store.bump_retry(ctx.task_id)
        self.assertIn("自动重试 4 次后仍失败", (await self.run_on(ctx))["text"])
        self.assertEqual(len(self.notices()), 1)

    async def test_every_failure_reaches_the_explainer_through_the_outbox(self):
        ctx = tv_ctx()
        ctx.state = TaskState.QUEUED
        self.store.save_task(ctx)
        await self.pl._fail(ctx, "转存准备失败：ConnectionError", retry_from=TaskState.QUEUED)
        await self.pl.dispatch_deliveries()
        self.assertEqual(self.store.state_get("failure_explainer", f"task:{ctx.task_id}")["action"], "retry")
        self.assertEqual(self.notices(), [])  # 还会自动重试，不打扰
        # 同一轮里再次失败（如自动重试后）：事件仍要送到，失败解释据此更新并通知
        again = self.store.get_task(ctx.task_id)
        await self.pl._fail(again, "转存前校验未通过，需人工处理：无法从文件名识别集号：a.mp4",
                            retry_from=TaskState.QUEUED, auto_retry=False)
        await self.pl.dispatch_deliveries()
        explanation = self.store.state_get("failure_explainer", f"task:{ctx.task_id}")
        self.assertEqual((explanation["action"], explanation["state"]), ("fix_and_retry", "failed"))
        self.assertEqual(len(self.notices()), 1)
        self.assertEqual(self.service.get_task(ctx.task_id)["explanation"]["action"], "fix_and_retry")
        # 解释与当前状态不符时不显示（如任务已被重新排队）
        self.store.set_state(ctx.task_id, TaskState.QUEUED, expected=TaskState.FAILED)
        self.store.set_state(ctx.task_id, TaskState.NEED_MANUAL, expected=TaskState.QUEUED)
        self.assertIsNone(self.service.get_task(ctx.task_id)["explanation"])

    async def test_expired_shares_are_explained_as_gone(self):
        ctx = tv_ctx()
        ctx.state = TaskState.FAILED
        ctx.ns("task")["no_auto_retry"] = True
        self.store.save_task(ctx, error="分享不可用，无法转存：链接已过期")
        explanation = await self.run_on(ctx)
        self.assertEqual((explanation["action"], explanation["text"]), ("share_gone", "分享不可用，无法转存：链接已过期"))
        self.assertIn("建议：忽略这条任务", self.notices()[0])

    async def test_shares_that_need_picking_files_are_not_sent_to_fix_the_parse(self):
        ctx = tv_ctx()
        ctx.state = TaskState.FAILED
        ctx.ns("task")["no_auto_retry"] = True
        self.store.save_task(ctx, error="转存前校验未通过，需人工处理：电影分享包含 2 个正片候选，需人工选择：a.iso、b.iso")
        explanation = await self.run_on(ctx)
        self.assertEqual((explanation["action"], explanation["label"]), ("manual_transfer", "到网盘挑选文件手动转存"))

    async def test_ai_phrases_the_reason_but_not_the_action(self):
        ctx = tv_ctx()
        ctx.state = TaskState.FAILED
        ctx.ns("task")["no_auto_retry"] = True
        self.store.save_task(ctx, error="转存前校验未通过，需人工处理：无法从文件名识别集号：a.mp4")
        self.explainer._client = FakeAI({"text": "分享里的文件名看不出是第几集"})
        explanation = await self.run_on(ctx)
        self.assertEqual((explanation["action"], explanation["text"], explanation["source"]),
                         ("fix_and_retry", "分享里的文件名看不出是第几集", "ai"))
        payload = json.loads(self.explainer._client.calls[0]["messages"][1]["content"])
        self.assertEqual(payload["action"], "修正识别结果后重试")
        self.explainer._client = FakeAI("not json")
        ctx.ns("task")["generation"] = 1
        self.store.save_task(ctx, error="无法从文件名识别集号：a.mp4")
        self.assertEqual((await self.run_on(ctx))["source"], "rule")


class TestAiRoutes(unittest.TestCase):
    """HTTP 层：新接口已注册、参数与错误码正确（不访问网络，未配置 AI）。"""

    def setUp(self):
        try:
            from fastapi.testclient import TestClient
        except ImportError:  # pragma: no cover
            self.skipTest("未安装 web 可选依赖")
        from reelay.web import create_app
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.env = patch.dict(os.environ, {}, clear=True)
        self.env.start()
        self.loader = patch("reelay.web.load_env")
        self.loader.start()
        (root / "config.yaml").write_text(json.dumps({"save_dirs": {"默认": "0"}, "defaults": {"save_to": "默认"}}),
                                          encoding="utf-8")
        self.client = TestClient(create_app(data_dir=root))
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.loader.stop()
        self.env.stop()
        self.temp.cleanup()

    def test_routes_answer_without_ai(self):
        for path in ("/api/insights", "/api/ai/usage", "/api/subscriptions", "/api/sources/quality",
                     "/api/library/query?media_type=anime", "/api/library/missing"):
            self.assertEqual(self.client.get(path).status_code, 200, path)
        self.assertEqual(self.client.get("/api/library/query?min_resolution=8k").status_code, 422)
        self.assertEqual(self.client.post("/api/ai/compile-rules", json={"text": "国产剧"}).status_code, 409)
        self.assertEqual(self.client.post("/api/sources/draft", json={"bot": "@x", "samples": ["a"]}).status_code, 409)
        self.assertEqual(self.client.post("/api/inbox/advice", json={"task_ids": ["x"]}).json(), {"items": {}})
        added = self.client.post("/api/subscriptions", json={"title": "剧", "season": 2})
        self.assertEqual(added.json()["subscription"], {"title": "剧", "season": 2})
        self.assertEqual(self.client.post("/api/subscriptions", json={"title": "剧", "season": 2}).status_code, 409)
        self.assertEqual(self.client.post("/api/subscriptions/remove", json={"title": "剧", "season": 2}).status_code, 200)
        self.assertEqual(self.client.post("/api/subscriptions/from-task/missing").status_code, 404)
        self.assertEqual(self.client.post("/api/insights/auto-approve", json={"source": "@new"}).json()["config"]["sources"],
                         [{"bot": "@new", "approval_mode": "auto"}])


class TestDailyNarrative(Base):
    def setUp(self):
        super().setUp()
        self.report = next(x for x in self.registry.listeners["on_daily"] if x.name == "daily_report")

    async def send(self):
        ctx = PipelineContext(raw=RawMessage(source="system", msg_id="daily", text=""), task_id="daily-x")
        ctx.meta["system"] = {"date": "2026-10-19", "summary": {"received": 3, "done_count": 1, "done": [{"title": "剧"}],
                                                               "pending_approval": 2}}
        with self.pl._scope(self.pl.cfg):
            await self.report.handle(ctx)
        rows = self.store._conn.execute("SELECT snapshot FROM outbox WHERE kind='notice' ORDER BY id").fetchall()
        return json.loads(rows[-1][0])["text"]

    async def test_story_comes_first_and_numbers_stay(self):
        done_task(self.store, "1", title="剧")
        self.report._client = FakeAI({"text": "本周新收 1 部剧，《剧》已入库。"})
        text = await self.send()
        self.assertTrue(text.startswith("📊 Reelay 日报 2026-10-19\n本周新收 1 部剧，《剧》已入库。\n\n过去 24 小时新收 3 条"))
        facts = json.loads(self.report._client.calls[0]["messages"][1]["content"])["facts"]
        self.assertEqual(facts["last_7d"]["top"][0]["title"], "剧")
        self.report._client = FakeAI("bad")
        self.assertTrue((await self.send()).startswith("📊 Reelay 日报 2026-10-19\n过去 24 小时"))
        self.configure({**self.CFG, "plugins": {"daily_report": {"narrative": False}}})
        self.report._client = FakeAI({"text": "x"})
        await self.send()
        self.assertEqual(self.report._client.calls, [])
