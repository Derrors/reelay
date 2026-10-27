"""控制 Bot：修正识别结果、列表与批量操作。"""
from __future__ import annotations

import asyncio
import struct
import time
import types
import unittest
from unittest.mock import patch

from telethon.errors import InvalidBufferError, MessageIdInvalidError

from reelay.control import manage
from reelay.control.bot import ControlBot
from reelay.core import messages, secret_store
from reelay.core.messages import parse_callback
from reelay.core.contracts import TaskState

from tests.fakes import ServiceCase, bot_for


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


class TestCorrection(ServiceCase):
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


class TestLists(ServiceCase):
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
        self.assertIn("这 <b>10</b> 条", text)
        label, data = confirm[0][0]
        self.assertEqual((label, data.split(":")[0]), ("确认批准转存", "pay"))
        later = await self.pending("11")  # 确认前又到了一条：不在这次的计划里
        text, _, _ = await bot.callback(data)
        self.assertIn("已批准，正在转存，完成后逐条通知 10 条", text)
        self.assertEqual({self.store.get_task(t.task_id).state for t in tasks}, {TaskState.QUEUED})
        self.assertEqual(self.store.get_task(later.task_id).state, TaskState.PENDING_APPROVAL)
        self.assertIn("已经过期或处理过了", (await bot.callback(data))[0])  # 同一个确认按钮不能再用
        await self.service.reject(later.task_id)
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
        item = self.subscribe({"title": "繁花", "season": 1, "tmdbid": 9})
        self.store.add_ignored_work("title:广告剧", {"title": "广告剧"})
        bot = bot_for(self.service)
        text, buttons = manage.subs_list(self.service)
        self.assertEqual(buttons, [[("🗑 取消订阅 繁花 S01", f"us:{item['id']}")]])
        text, confirm, _ = await bot.callback(f"us:{item['id']}")
        self.assertIn("取消订阅《繁花》第 1 季", text)
        text, _, _ = await bot.callback(f"usy:{item['id']}")
        self.assertIn("🗑 已取消订阅《繁花》第 1 季", text)
        self.assertEqual(self.store.subscriptions(), [])
        self.assertEqual((await bot.callback(f"usy:{item['id']}"))[2], "这条订阅已经不在了")
        # 自动结束的订阅：继续订阅
        item = self.subscribe({"title": "繁花", "season": 1, "tmdbid": 9})
        self.store.set_subscription_finished(item["id"], {"date": "2026-10-01", "episodes": 30})
        self.assertEqual(manage.subs_list(self.service)[1], [[("▶️ 继续订阅 繁花 S01", f"so:{item['id']}")]])
        await bot.callback(f"so:{item['id']}")
        self.assertIsNone(self.store.subscription(item["id"])["finished"])
        text, buttons = manage.ignored_list(self.service)
        self.assertIn("广告剧", text)
        text, _, _ = await bot.callback(buttons[0][0][1])
        self.assertIn("已恢复接收《广告剧》", text)
        self.assertEqual(self.store.ignored_works(), [])



class TestBulkPlans(ServiceCase):
    """「全部批准 / 全部忽略」先列出计划，确认时只处理计划里的任务，且只执行一次。"""

    def plan(self, action):
        text, buttons = manage.bulk_confirm(self.service, action)
        name, (plan_id,) = parse_callback(buttons[0][0][1])
        self.assertEqual(name, f"{action}y")
        return text, plan_id

    async def test_only_tasks_listed_at_confirmation_are_handled_once(self):
        first = await self.pending("1")
        text, plan_id = self.plan("pa")
        self.assertIn("<b>1</b> 条", text)
        later = await self.pending("2")  # 确认之后才到的任务不在计划里
        self.assertIn("已批准，正在转存，完成后逐条通知 1 条", await manage.bulk_run(self.service, plan_id))
        self.assertEqual(self.store.get_task(first.task_id).state, TaskState.QUEUED)
        self.assertEqual(self.store.get_task(later.task_id).state, TaskState.PENDING_APPROVAL)
        self.assertIn("已经过期或处理过了", await manage.bulk_run(self.service, plan_id))

    async def test_tasks_handled_meanwhile_are_reported_not_failed(self):
        first, _ = await self.pending("1"), await self.pending("2")
        _, plan_id = self.plan("pr")
        await self.service.approve(first.task_id)
        self.assertEqual(await manage.bulk_run(self.service, plan_id), "✅ 已忽略 1 条，1 条期间已被处理")
        self.assertEqual(self.store.get_task(first.task_id).state, TaskState.QUEUED)

    async def test_expired_plans_do_nothing(self):
        task = await self.pending("1")
        _, plan_id = self.plan("pa")
        with patch("reelay.control.manage.time.time", return_value=time.time() + manage.BULK_TTL + 1):
            self.assertIn("过期", await manage.bulk_run(self.service, plan_id))
        self.assertEqual(self.store.get_task(task.task_id).state, TaskState.PENDING_APPROVAL)



class TestCardSync(ServiceCase):
    """审批卡片在别处（管理台、批量操作、「不再接收」……）处理后，Bot 把卡片改成处理结果。"""

    def bot(self):
        sent, edits = [], []

        class Client:
            fail = None

            async def send_message(self, chat, text, **kwargs):
                sent.append(text)
                return types.SimpleNamespace(id=len(sent))

            async def edit_message(self, chat, message_id, text, **kwargs):
                if self.fail:
                    raise self.fail
                edits.append((chat, message_id, text, kwargs["buttons"]))

        bot = bot_for(self.service)
        bot._client, bot._owner = Client(), "42"
        bot._Button = types.SimpleNamespace(inline=lambda label, data: (label, data.decode()), url=lambda label, url: (label, url))
        return bot, edits

    async def test_cards_follow_tasks_handled_elsewhere(self):
        first, second, third = [await self.pending(str(i)) for i in (1, 2, 3)]
        bot, edits = self.bot()
        for task in (first, second, third):
            await bot.send(messages.approval_card(task), messages.approval_buttons(task), html=True, card=task.task_id)
        await bot.send("不是审批卡片", html=True)
        self.assertEqual(await bot.sync_cards(), 0)  # 都还在待审批
        await self.service.approve(first.task_id)  # 在管理台批准
        self.assertEqual(await bot.sync_cards(), 1)
        chat, message_id, text, buttons = edits[-1]
        self.assertEqual((chat, message_id), (42, 1))
        self.assertEqual(text, messages.approval_result(messages.approval_card(self.store.get_task(first.task_id)),
                                                        "approved"))
        self.assertIsNone(buttons)  # 审批按钮去掉了
        self.assertEqual(await bot.sync_cards(), 0)  # 只改一次
        await self.service.ignore_work(second.task_id)  # 「不再接收」：同一部作品的其他卡片一并改掉
        self.assertEqual(await bot.sync_cards(), 2)
        self.assertEqual([m for _, m, _, _ in edits[-2:]], [2, 3])
        self.assertTrue(all(text.endswith("⛔ <b>已忽略</b>，之后《剧》的消息不再处理") for _, _, text, _ in edits[-2:]))

    async def test_cards_answered_on_telegram_or_gone_are_dropped(self):
        first, second = await self.pending("1"), await self.pending("2")
        bot, edits = self.bot()
        await bot.send("卡片", html=True, card=first.task_id)
        await bot.send("卡片", html=True, card=second.task_id)
        self.store.card_drop("42", 1, settled_only=True)  # 点了卡片上的按钮，但任务还在待审批：继续跟踪
        await self.service.approve(first.task_id)
        self.store.card_drop("42", 1, settled_only=True)  # 卡片上点了「转存」：结果已写在卡片上
        await self.service.reject(second.task_id)
        bot._client.fail = ConnectionError("断开")
        with self.assertRaises(ConnectionError):  # 连不上 Telegram：下次再改
            await bot.sync_cards()
        bot._client.fail = MessageIdInvalidError(request=None)  # 卡片被删了：不再重试
        self.assertEqual(await bot.sync_cards(), 0)
        bot._client.fail = None
        self.assertEqual(await bot.sync_cards(), 0)
        self.assertEqual(edits, [])


class TestReconnect(ServiceCase):
    """控制 Bot 与 Telegram 断开后在进程里重新连接；启动时连不上也不退出。"""

    async def until(self, check, timeout=2):
        async def poll():
            while not check():
                await asyncio.sleep(0.005)
        await asyncio.wait_for(poll(), timeout)

    async def test_bot_reconnects_instead_of_exiting(self):
        clients, failures = [], [ConnectionError("blocked")]  # 启动时代理不通

        class Client:
            def __init__(self, *args, **kwargs):
                self.options, self.connected, self.gone = kwargs, False, None
                clients.append(self)

            def on(self, event):
                return lambda handler: handler

            async def start(self, bot_token):
                if failures:
                    raise failures.pop(0)
                self.connected = True

            async def __call__(self, request):  # 命令菜单
                return None

            def is_connected(self):
                return self.connected

            async def run_until_disconnected(self):
                self.gone = asyncio.get_running_loop().create_future()
                await self.gone

            def drop(self, error=None):
                self.connected = False
                self.gone.set_exception(error) if error else self.gone.set_result(None)

            async def disconnect(self):
                self.connected = False
                if self.gone and not self.gone.done():
                    self.gone.set_result(None)

        module = types.ModuleType("telethon")
        module.TelegramClient, module.Button = Client, None
        module.events = types.SimpleNamespace(NewMessage=lambda **kwargs: None, CallbackQuery=lambda **kwargs: None)
        module.functions = types.SimpleNamespace(bots=types.SimpleNamespace(SetBotCommandsRequest=lambda **kw: kw))
        module.types = types.SimpleNamespace(BotCommandScopeDefault=lambda: None, BotCommand=lambda **kw: kw)
        bot = bot_for(self.service)
        bot._token, bot._owner, bot._problem = "123:abc", "42", None
        throttled = lambda: InvalidBufferError(struct.pack("<i", -429))  # noqa: E731
        with patch.dict("sys.modules", {"telethon": module}), patch("reelay.core.telegram.RECONNECT_SEC", 0.01), \
                patch("reelay.control.bot.RETRY_SECONDS", 0.01):
            await bot.start()  # 启动时连不上：不退出，稍后自己连
            self.assertEqual(bot.health(), {"status": "disconnected", "error": "连不上 Telegram：blocked"})
            runner = asyncio.create_task(bot.run_forever())
            await self.until(lambda: bot.health()["status"] == "connected")
            self.assertIs(clients[-1].options["auto_reconnect"], False)  # 断线由 Bot 自己重连
            failures.append(throttled())  # 恢复时被 Telegram 限流：稍后再连
            clients[-1].drop(throttled())
            await self.until(lambda: bot.health()["status"] == "connected" and not failures)
            clients[-1].drop()  # 心跳没有回应：run_until_disconnected 正常结束，同样重连
            await self.until(lambda: len(clients) == 5 and bot.health()["status"] == "connected")
            self.assertFalse(runner.done())
            failures.append(ValueError("Bot Token 失效"))  # 不是网络的问题：照常抛出
            clients[-1].drop()
            with self.assertRaises(ValueError):
                await asyncio.wait_for(runner, 2)

class TestHealth(ServiceCase):
    def test_unconfigured_bot_is_not_reported_as_disconnected(self):
        with secret_store.override():
            health = ControlBot(self.service).health()
        self.assertEqual(health["status"], "not_configured")  # 总览显示「未配置」，不是红色的连接中断
        self.assertIn("设置 → Telegram", health["error"])
        self.configure({**self.CFG, "telegram": {"owner_id": "42"}})
        with secret_store.override(control_bot_token="123:abc"):
            self.assertEqual(ControlBot(self.service).health(), {"status": "disconnected"})  # 配好了但还没连上


if __name__ == "__main__":
    unittest.main()
