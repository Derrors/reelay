"""发给用户的 Telegram 消息：待审批卡片、点按钮后的结果、入库通知与需要处理的提醒。"""
from __future__ import annotations

import types
import unittest

from reelay.control.bot import ControlBot
from reelay.core import messages, release
from reelay.core.contracts import ParsedResource, PipelineContext, RawMessage, TaskState
from reelay.core.service import ConflictError
from reelay.providers.notify import ConsoleNotify


def tv(**fields) -> PipelineContext:
    ctx = PipelineContext(RawMessage("@bot", "1", "x"), task_id="t1")
    values = {"is_media": True, "title": "玉簟秋", "year": 2026, "media_type": "tv", "season": 1, "episodes": [17],
              "resolution": "2160p", "size": "2.53 GB", "release": {**release.empty(), "subtitles": ["chs"], "packaging": "single"}}
    ctx.parsed = ParsedResource(**{**values, **fields})
    ctx.meta["dir_router"] = {"preview": "/测试/剧集/国产剧集"}
    return ctx


class TestTemplates(unittest.TestCase):
    def test_approval_card_shows_what_will_be_saved_and_where(self):
        text = messages.approval_card(tv())
        self.assertTrue(text.startswith("📺 <b>玉簟秋</b> (2026)"))
        for part in ("第 1 季 · E17", "2160p · 简体中字", "2.53 GB", "来源 @bot", "存到 <code>/测试/剧集/国产剧集</code>"):
            self.assertIn(part, text)
        self.assertNotIn("⚠️", text)
        self.assertNotIn("单集", text)  # 打包形式从季集那一行已经看得出来
        movie = messages.approval_card(tv(media_type="movie", season=None, episodes=[]))
        self.assertTrue(movie.startswith("🎬 "))
        self.assertNotIn("季", movie)

    def test_unreliable_items_are_flagged_and_text_is_escaped(self):
        ctx = tv(title="A<b>&B")
        ctx.meta["confidence"] = {"caps": ["tmdb_unmatched"]}
        ctx.meta["share"] = {"status": "unavailable", "note": "分享无法访问：链接已过期"}
        ctx.meta["task"] = {"approval_reminders": 2}
        text = messages.approval_card(ctx)
        self.assertIn("<b>A&lt;b&gt;&amp;B</b>", text)
        self.assertIn("⚠️ TMDB 没有可信匹配", text)
        self.assertIn("⚠️ 分享无法访问：链接已过期", text)
        self.assertIn("⏰ 第 2 次提醒", text)
        self.assertEqual(messages.plain("<b>A&lt;b&gt;</b>"), "A<b>")

    def test_card_keeps_its_content_after_a_decision(self):
        card = messages.approval_card(tv())
        done = messages.approval_result(card, "approved")
        self.assertTrue(done.startswith(card))
        self.assertIn("已批准", done)
        self.assertIn("当前：done", messages.approval_result(card, "stale", "done"))

    def test_saved_message_shows_the_organized_folder_and_files(self):
        ctx = tv(episodes=[1, 2, 3])
        ctx.meta["organize"] = {"plan": [
            {"target_dir": "国产剧集/玉簟秋 (2026)/Season 01", "target_name": f"玉簟秋 - S01E0{i}.mkv"} for i in (1, 2, 3)]}
        text = messages.saved_message(ctx, "/测试/剧集")
        self.assertIn("✅ 已入库 · <b>玉簟秋</b> (2026)", text)
        self.assertIn("<code>/测试/剧集/国产剧集/玉簟秋 (2026)/Season 01</code>", text)
        self.assertIn("📄 3 个文件：玉簟秋 - S01E01.mkv、玉簟秋 - S01E02.mkv 等", text)
        # 跨多季：目录取共同的上级
        ctx.meta["organize"]["plan"][2]["target_dir"] = "国产剧集/玉簟秋 (2026)/Season 02"
        self.assertIn("<code>/测试/剧集/国产剧集/玉簟秋 (2026)</code>", messages.saved_message(ctx, "/测试/剧集"))


class TestDelivery(unittest.IsolatedAsyncioTestCase):
    async def test_notifications_are_sent_as_html_with_buttons_in_one_row(self):
        sent = []

        async def sender(**kwargs):
            sent.append(kwargs)

        console = ConsoleNotify()
        console.configure(sender=sender)
        await console.notify(tv(), "pending_approval")
        await console.notify(tv(), "transfer_uncertain")
        self.assertTrue(all(item["html"] for item in sent))
        self.assertEqual(sent[0]["buttons"], [("✅ 转存", "ok:t1"), ("🚫 忽略", "no:t1")])
        self.assertIn("核验转存", sent[1]["text"])
        self.assertIn("<code>t1</code>", sent[1]["text"])

    async def test_bot_decision_keeps_the_card_and_reports_stale_tasks(self):
        task = tv()
        calls = []

        async def approve(task_id):
            calls.append(("approve", task_id))

        async def reject(task_id):
            raise ConflictError("任务不是待审批状态")

        store = types.SimpleNamespace(get_task=lambda task_id: task)
        bot = ControlBot.__new__(ControlBot)
        bot.service = types.SimpleNamespace(store=store, approve=approve, reject=reject)
        text = await bot.decide("ok:t1")
        self.assertEqual(calls, [("approve", "t1")])
        self.assertTrue(text.startswith(messages.approval_card(task)))
        self.assertIn("已批准", text)
        task.state = TaskState.DONE
        self.assertIn("这条已经处理过了（当前：done）", await bot.decide("no:t1"))


if __name__ == "__main__":
    unittest.main()
