"""在 Telegram 上完成日常操作：修正识别、重新调出卡片、一键批准、订阅与不再接收的管理、追更提醒的按钮。

不连 Telegram：直接调用控制 Bot 的按钮与消息处理逻辑，服务层与数据库是真的。
"""
from __future__ import annotations

import json
import unittest

from reelay.agent_tools import AgentTools
from reelay.control import manage
from reelay.control.bot import ControlBot
from reelay.core import messages, runtime, subscriptions
from reelay.core.contracts import ParsedResource, TaskState
from reelay.plugins import episode_tracker
from reelay.plugins.episode_tracker import EpisodeRecorder, EpisodeTracker

from tests.test_ai_feedback import SHARE, Base, message


class TestParseCorrection(unittest.TestCase):
    def test_phrases(self):
        parse = manage.parse_correction
        self.assertEqual(parse("繁花 2023 剧集 S01E03"),
                         {"title": "繁花", "year": 2023, "media_type": "tv", "season": 1, "episodes": [3]})
        self.assertEqual(parse("片名 繁花"), {"title": "繁花"})
        self.assertEqual(parse("第 2 季 E01-E05"), {"season": 2, "episodes": [1, 2, 3, 4, 5]})
        self.assertEqual(parse("《Doctor X：白色黑手党时代》 第1-5集"),
                         {"title": "Doctor X：白色黑手党时代", "episodes": [1, 2, 3, 4, 5]})
        self.assertEqual(parse("类型：电影"), {"media_type": "movie", "season": None, "episodes": []})
        self.assertEqual(parse("Doctor X 4K"), {"title": "Doctor X", "resolution": "2160p"})
        self.assertEqual(parse("  "), {})
        with self.assertRaises(ValueError):
            parse("第 9-3 集")


def bot_for(service) -> ControlBot:
    bot = ControlBot.__new__(ControlBot)
    bot.service, bot._correcting = service, {}
    return bot


class TestCorrection(Base):
    async def test_correct_a_pending_task_from_telegram(self):
        task = await self.pending("3")
        bot = bot_for(self.service)
        self.assertIn(("✏️ 修正", f"fx:{task.task_id}"), [b for row in messages.approval_buttons(task) for b in row])
        prompt, buttons, _, toast = await bot.open(f"fx:{task.task_id}", chat="c")
        self.assertIn(task.task_id, prompt)
        self.assertEqual(buttons, messages.correction_buttons(task.task_id))
        # 回复了提示，或点「修正」后直接发的下一条消息，都当作修正内容
        self.assertEqual(bot.correction_target("x", messages.plain(prompt)), task.task_id)
        self.assertEqual(bot.correction_target("c", None), task.task_id)
        self.assertIsNone(bot.correction_target("c", None))  # 只算一次
        text, buttons, _ = await bot.correct(task.task_id, "片名 繁花 第 2 季 E05")
        parsed = self.store.get_task(task.task_id).parsed
        self.assertEqual((parsed.title, parsed.season, parsed.episodes), ("繁花", 2, [5]))
        self.assertIn("✏️ <b>已修正</b>：片名：剧 → 繁花；季：1 → 2；集：E03 → E05", text)
        self.assertEqual(buttons, messages.approval_buttons(self.store.get_task(task.task_id)))
        self.assertIn("没看出要改什么", (await bot.correct(task.task_id, "…"))[0])
        await self.service.approve(task.task_id)  # 已经批准、排队转存：不能再改
        self.assertIn("没有修改：排队中", (await bot.correct(task.task_id, "片名 甲"))[0])
        self.assertEqual((await bot.open(f"fx:{task.task_id}"))[3], "现在不能修正（当前：queued）")

    async def test_failed_task_can_be_corrected_then_retried(self):
        task = await self.pending("4")
        task.state = TaskState.FAILED
        task.trail.append("10:00:00 转存前校验未通过，需人工处理：无法从文件名识别集号：a.mp4")
        self.store.save_task(task)
        text, buttons, _ = messages.task_card(task)
        self.assertIn("先点「修正」，再重试", text)
        self.assertIn([("✏️ 修正", f"fx:{task.task_id}")], buttons)
        await bot_for(self.service).correct(task.task_id, "第 4 集")
        self.assertEqual(self.store.get_task(task.task_id).parsed.episodes, [4])
        await self.service.retry(task.task_id)

    async def test_reparse_button(self):
        task = await self.pending("5")
        text, _, _ = await bot_for(self.service).callback(f"rp:{task.task_id}", "提示")
        self.assertIn("已重新识别", text)
        self.assertEqual(self.store.get_task(task.task_id).state, TaskState.RECEIVED)


class TestLists(Base):
    async def test_pending_list_reopens_cards_and_approves_everything(self):
        tasks = [await self.pending(str(i)) for i in range(1, 11)]
        bot = bot_for(self.service)
        text, buttons = manage.pending_list(self.service)
        self.assertIn("待审批 10", text)
        self.assertEqual(buttons[0], [("📺 剧 S01 E03", f"cd:{tasks[0].task_id}")])
        self.assertEqual(buttons[-2], [("下一页 ➡️", "pl:8")])
        self.assertEqual(buttons[-1], [("✅ 全部批准（10）", "pa"), ("🚫 全部忽略（10）", "pr")])
        self.assertEqual(len((await bot.callback("pl:8"))[1]), 2 + 2)  # 第二页 2 条 + 翻页 + 一键操作
        self.assertTrue(all(len(data.encode()) <= 64 for row in buttons for _, data in row))
        card, card_buttons, _, _ = await bot.open(f"cd:{tasks[0].task_id}")
        self.assertEqual(card, messages.approval_card(tasks[0]))
        text, confirm, _ = await bot.callback("pa")
        self.assertIn("全部 <b>10</b> 条", text)
        self.assertEqual(confirm[0][0], ("确认批准转存", "pay"))
        text, _, _ = await bot.callback("pay")
        self.assertIn("已批准，正在转存，完成后逐条通知 10 条", text)
        self.assertEqual({self.store.get_task(t.task_id).state for t in tasks}, {TaskState.QUEUED})
        text, buttons = manage.pending_list(self.service)
        self.assertTrue(text.startswith("📋 <b>待处理</b>\n\n✅ 没有需要你确认或处理的资源"))
        self.assertEqual(buttons, [[("📊 运行概况", "st")]])

    async def test_status_and_help(self):
        await self.pending("1")
        text, buttons = manage.status_view(self.service)
        self.assertTrue(text.startswith("📊 <b>运行概况</b>"))
        self.assertIn("📌 <b>待处理</b>\n待审批 1 · 待人工 0 · 失败 0", text)
        self.assertEqual(buttons, [[("📋 待处理 1", "pl:0")], [("🔄 刷新", "st")]])
        self.assertEqual((await bot_for(self.service).callback("st"))[0], text)
        text, _ = manage.help_view(self.service)
        self.assertIn("/pending　待审批与待人工", text)
        self.assertNotIn("/help", text)

    async def test_digest_and_backfill_notices_carry_the_button(self):
        sent = []

        async def sender(**kwargs):
            sent.append(kwargs)

        console = self.registry.get_provider("notify", "console")
        console.configure(sender=sender)
        self.addCleanup(console.configure)
        self.store.stage_notice("approval_digest", "⏳ 2 条资源等待审批", ["console"], buttons=messages.PENDING_BUTTONS)
        await self.pl.dispatch_deliveries()
        self.assertEqual(sent[-1]["buttons"], [[["📋 查看待审批", "pl:0"]]])

    async def test_subscriptions_and_ignored_works(self):
        item = {"title": "繁花", "season": 1, "tmdbid": 9}
        self.configure({**self.CFG, "subscriptions": [item], "ignored_works": [{"title": "广告剧"}]})
        bot = bot_for(self.service)
        text, buttons = manage.subs_list(self.service)
        key = subscriptions.button_key(item)
        self.assertEqual(buttons, [[("🗑 取消订阅 繁花 S01", f"us:{key}")]])
        text, confirm, _ = await bot.callback(f"us:{key}")
        self.assertIn("取消订阅《繁花》第 1 季", text)
        text, _, _ = await bot.callback(f"usy:{key}")
        self.assertIn("🗑 已取消订阅《繁花》第 1 季", text)
        self.assertEqual(self.service.get_config()["subscriptions"], [])
        self.assertEqual((await bot.callback(f"usy:{key}"))[2], "这条订阅已经不在了")
        # 自动结束的订阅：继续订阅
        self.configure({**self.CFG, "subscriptions": [item], "ignored_works": [{"title": "广告剧"}]})
        self.store.state_set(*subscriptions.FINISHED, {subscriptions.key(item): {"date": "2026-10-01", "episodes": 30}})
        self.assertEqual(manage.subs_list(self.service)[1], [[("▶️ 继续订阅 繁花 S01", f"so:{key}")]])
        await bot.callback(f"so:{key}")
        self.assertEqual(self.store.state_get(*subscriptions.FINISHED), {})
        text, buttons = manage.ignored_list(self.service)
        self.assertIn("广告剧", text)
        text, _, _ = await bot.callback(buttons[0][0][1])
        self.assertIn("已恢复接收《广告剧》", text)
        self.assertEqual(self.service.get_config()["ignored_works"], [])


class TestSubscriptionSuggestions(Base):
    def notices(self):
        return [json.loads(r[0]) for r in self.store._conn.execute(
            "SELECT snapshot FROM outbox WHERE kind='notice' AND delivery_key LIKE 'notice:subscription_suggestion:%'")]

    async def test_asked_once_on_the_third_approval_then_handled_in_telegram(self):
        tasks = [await self.pending(str(i)) for i in range(1, 5)]
        for task in tasks[:2]:
            await self.service.approve(task.task_id)
        self.assertEqual(self.notices(), [])
        await self.service.approve(tasks[2].task_id)
        await self.service.approve(tasks[3].task_id)  # 第 4 条不再问
        [notice] = self.notices()
        self.assertTrue(notice["html"])
        self.assertIn("你已批准《剧》第 1 季 3 条", notice["text"])
        item = {"title": "剧", "season": 1, "tmdbid": None}
        key = subscriptions.button_key(item)
        self.assertEqual(notice["buttons"], [[["🔔 订阅第 1 季", f"ss:{key}"], ["🙅 不用了", f"sd:{key}"]]])
        # /subs 里也能看到
        text, buttons = manage.subs_list(self.service)
        self.assertIn("💡 <b>订阅建议</b>", text)
        self.assertEqual(buttons, [[("🔔 订阅 剧 S01", f"ss:{key}:l"), ("🙅 不用了", f"sd:{key}:l")]])
        bot = bot_for(self.service)
        text, _, _ = await bot.callback(f"ss:{key}", "建议")
        self.assertTrue(text.startswith("建议\n\n🔔 <b>已订阅</b>《剧》第 1 季"))
        self.assertEqual(self.service.get_config()["subscriptions"], [{"title": "剧", "season": 1}])
        self.assertEqual((await bot.callback(f"ss:{key}", "建议"))[2], "这条建议已经处理过了")

    async def test_dismissed_suggestions_stay_hidden(self):
        for i in range(1, 4):
            await self.service.approve((await self.pending(str(i))).task_id)
        key = subscriptions.button_key({"title": "剧", "season": 1, "tmdbid": None})
        text, buttons, _ = await bot_for(self.service).callback(f"sd:{key}:l")
        self.assertTrue(text.startswith("🙅 不再建议订阅《剧》第 1 季"))
        self.assertEqual(self.service.subscription_suggestions(), [])
        self.assertNotIn("订阅建议", text)


class TestTracker(Base):
    async def report(self):
        recorder, tracker = EpisodeRecorder(), EpisodeTracker()
        for episodes in ([20, 21], [23]):
            ctx = message(f"一瓯春 第{episodes[0]}集 {SHARE}", msg_id=str(episodes[0]))
            ctx.parsed = ParsedResource(is_media=True, title="一瓯春", season=1, episodes=episodes, share_code="s")
            ctx.save_dir_cid = "1"
            ctx.result = {"storage_verified": True}
            ctx.ns("storage")["identity"] = {"cid": "1", "share_code": f"s{episodes[0]}", "title": "一瓯春",
                                             "season": 1, "episodes": episodes}
            self.store.save_task(ctx)
            self.store.finish_transfer(ctx)
            with self.pl._scope(self.pl.cfg):
                await recorder.handle(ctx)
        self.store._conn.execute("DELETE FROM outbox")
        with self.pl._scope(self.pl.cfg):
            await tracker.handle(message())
        row = self.store._conn.execute("SELECT snapshot FROM outbox WHERE kind='notice'").fetchone()
        return json.loads(row[0]) if row else None

    async def test_dismiss_missing_episodes_from_the_reminder(self):
        notice = await self.report()
        digest = episode_tracker.series_hash("一瓯春|S01")
        self.assertEqual(notice["buttons"], [[["🔕 一瓯春 S01", f"tm:{digest}"]]])
        bot = bot_for(self.service)
        _, options, _ = await bot.callback(f"tm:{digest}", "提醒")
        self.assertEqual(options[0], [("🙈 E22 不用等了", f"tmi:{digest}")])
        text, undo, _ = await bot.callback(f"tmi:{digest}", "提醒")
        self.assertIn("E22 不再提醒", text)
        self.assertIsNone(await self.report())  # 只缺 E22，已忽略：不再提醒
        text, buttons, _ = await bot.callback(f"tmu:{digest}", "提醒")  # 这时最新的提醒里已经没有这部剧
        self.assertIn("照常提醒", text)
        self.assertIsNotNone(await self.report())
        await bot.callback(f"tmo:{digest}", "提醒")
        self.assertIsNone(await self.report())
        state = runtime.PluginState(self.store, "episode_tracker")
        self.assertEqual(state.get("muted:一瓯春|S01"), {"all": True, "episodes": []})


class TestAssistantTools(Base):
    async def test_new_write_tools(self):
        task = await self.pending("6")
        tools = AgentTools(self.service)
        result = await tools.call("correct", {"task_id": task.task_id, "title": "繁花", "episodes": "1-2,5"})
        self.assertTrue(result["ok"])
        self.assertEqual(self.store.get_task(task.task_id).parsed.episodes, [1, 2, 5])
        self.assertFalse((await tools.call("correct", {"task_id": task.task_id}))["ok"])
        self.assertFalse((await tools.call("correct", {"task_id": task.task_id, "episodes": "a-b"}))["ok"])
        self.configure({**self.CFG, "subscriptions": [{"title": "繁花", "season": 1}]})
        self.assertTrue((await tools.call("unsubscribe", {"title": "繁花", "season": 1}))["ok"])
        self.assertEqual(self.service.get_config()["subscriptions"], [])
        self.assertTrue((await tools.call("reparse", {"task_ids": [task.task_id]}))["ok"])


if __name__ == "__main__":
    unittest.main()
