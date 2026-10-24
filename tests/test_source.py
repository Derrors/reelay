"""Telegram 消息源：监听会话、停机补拉、断线告警、日报等补拉完成。"""
from __future__ import annotations

import asyncio
import json
import time
import types
import unittest
from unittest.mock import patch

from reelay.core import runtime, secret_store
from reelay.core.contracts import TaskState
from reelay.core.registry import Registry
from reelay.plugins.episode_tracker import EpisodeTracker
from reelay.providers.decision import DefaultDecision
from reelay.providers.parser import RegexParser
from reelay.providers.source import TelegramUserbotSource

from tests.fakes import FakeTelegram, PipelineCase, message, tg_message


class TestTelegramListen(unittest.IsolatedAsyncioTestCase):
    """监听会话：等待凭证与登录、拒绝 Bot 会话、网络错误重试、确认身份前不放行消息。"""

    @staticmethod
    def fake_telethon(client_factory):
        module = types.ModuleType("telethon")
        module.TelegramClient = client_factory
        module.events = types.SimpleNamespace(NewMessage=lambda **kwargs: object())
        return patch.dict("sys.modules", {"telethon": module})

    class FakeClient:
        """Telethon 客户端的替身：authorized / bot 决定会话状态，start 是交互登录，监听里不能调用。"""

        def __init__(self, *, authorized=True, bot=False, on_connect=None):
            self.authorized, self.bot, self.on_connect = authorized, bot, on_connect
            self.handler, self.connected, self.disconnects, self.listened = None, False, 0, False

        def add_event_handler(self, handler, event):
            self.handler = handler

        async def connect(self):
            self.connected = True
            if self.on_connect:
                await self.on_connect(self)

        def is_connected(self):
            return self.connected

        async def is_user_authorized(self):
            return self.authorized

        async def get_me(self):
            return types.SimpleNamespace(bot=self.bot)

        async def start(self):
            raise AssertionError("start() 会在终端里交互登录，监听不能调用")

        async def run_until_disconnected(self):
            self.listened = True

        async def disconnect(self):
            self.connected = False
            self.disconnects += 1

    async def wait_for_status(self, source, status, timeout=2):
        async def poll():
            while source.health()["status"] != status:
                await asyncio.sleep(0.01)
        await asyncio.wait_for(poll(), timeout)
        return source.health()

    async def test_source_waits_for_credentials_and_login_instead_of_exiting(self):
        clients = []

        def factory(*args, **kwargs):
            clients.append(self.FakeClient(authorized=len(clients) > 0))
            return clients[-1]

        with self.fake_telethon(factory), patch("reelay.providers.source.RETRY_SECONDS", 0.02), \
                secret_store.override(telegram_api_id=None, telegram_api_hash=None) as store:
            source = TelegramUserbotSource()
            listener = asyncio.create_task(source.listen(lambda raw: None))
            health = await self.wait_for_status(source, "not_configured")
            self.assertIn("设置 → Telegram", health["error"])
            self.assertEqual(clients, [])  # 没有凭证不建会话
            store.update({"telegram_api_id": "1", "telegram_api_hash": "dummy"})  # 填写后不用重启
            await self.wait_for_status(source, "not_logged_in")
            self.assertIn("reelay.login", source.health()["error"])
            await asyncio.wait_for(listener, 2)  # 登录后接上，监听到断开为止
        self.assertEqual(clients[0].disconnects, 1)  # 没登录的会话断开后再试
        self.assertTrue(clients[1].listened)

    async def test_bot_session_is_reported_and_never_listened(self):
        client = self.FakeClient(bot=True)
        with self.fake_telethon(lambda *args, **kwargs: client), patch("reelay.providers.source.RETRY_SECONDS", 0.02), \
                secret_store.override(telegram_api_id="1", telegram_api_hash="dummy"):
            source = TelegramUserbotSource()
            listener = asyncio.create_task(source.listen(lambda raw: None))
            health = await self.wait_for_status(source, "not_logged_in")
            listener.cancel()
            await asyncio.gather(listener, return_exceptions=True)
        self.assertIn("个人账号", health["error"])
        self.assertGreaterEqual(client.disconnects, 1)
        self.assertFalse(client.listened)

    async def test_network_errors_are_retried(self):
        attempts = []

        async def flaky(client):
            attempts.append(1)
            if len(attempts) == 1:
                raise ConnectionError("network down")

        client = self.FakeClient(on_connect=flaky)
        with self.fake_telethon(lambda *args, **kwargs: client), patch("reelay.providers.source.RETRY_SECONDS", 0.02), \
                secret_store.override(telegram_api_id="1", telegram_api_hash="dummy"):
            await asyncio.wait_for(TelegramUserbotSource().listen(lambda raw: None), 2)
        self.assertEqual(len(attempts), 2)
        self.assertTrue(client.listened)

    async def test_personal_source_waits_for_identity_and_resolves_missing_peer(self):
        emitted, callbacks = [], []

        async def deliver_early_message(client):  # 连上后、确认账号之前就到了一条消息
            event = types.SimpleNamespace(chat=None, sender_id=123, chat_id=123,
                                          message=types.SimpleNamespace(id=1, raw_text='https://115.com/s/actual', entities=[]))

            async def get_chat():
                event.chat = types.SimpleNamespace(username='Test', id=123)
                return event.chat
            event.get_chat = get_chat
            callbacks.append(asyncio.create_task(client.handler(event)))
            await asyncio.sleep(0)
            self.assertEqual(emitted, [])

        client = self.FakeClient(on_connect=deliver_early_message)

        async def run_until_disconnected():
            await asyncio.gather(*callbacks)
        client.run_until_disconnected = run_until_disconnected

        async def emit(raw):
            emitted.append(raw)
        with self.fake_telethon(lambda *args, **kwargs: client), \
                secret_store.override(telegram_api_id='1', telegram_api_hash='dummy'):
            source = TelegramUserbotSource()
            source.configure(['@test'])
            await source.listen(emit)
        self.assertEqual(len(emitted), 1)
        self.assertEqual(emitted[0].source, '@Test')
        self.assertEqual(emitted[0].urls, ['https://115.com/s/actual'])
        self.assertFalse(client.connected)


class TestSourceBackfillAndAlerts(PipelineCase):
    async def test_backfill_emits_messages_after_watermark_only(self):
        source = TelegramUserbotSource()
        source.configure(["@bot"])
        source._client = FakeTelegram([tg_message(5, days=40), tg_message(6, days=30), tg_message(7, action="pin"),
                                       tg_message(8), tg_message(9)])
        source.bind_watermark(lambda name: 5 if name == "@Bot" else None)
        emitted = []

        async def emit(raw):
            emitted.append(raw)

        self.assertEqual(await source.backfill(emit), 2)
        self.assertEqual([raw.msg_id for raw in emitted], ["8", "9"])  # 按时间顺序入库
        self.assertTrue(all(raw.source == "@Bot" and raw.extra["backfill"] for raw in emitted))

    async def test_backfill_uses_watermarks_taken_before_live_messages(self):
        source = TelegramUserbotSource()
        source.configure(["@bot"])
        source._client = FakeTelegram([tg_message(6), tg_message(7), tg_message(8)])
        marks = {"@Bot": 5}
        source.bind_watermark(marks.get)
        snapshot = await source._watermarks()
        marks["@Bot"] = 8  # 放行后实时消息 8 先入库、推高了水位：停机期间的 6、7 仍要补
        emitted = []

        async def emit(raw):
            emitted.append(raw.msg_id)

        self.assertEqual(await source.backfill(emit, snapshot), 3)
        self.assertEqual(emitted, ["6", "7", "8"])

    async def test_one_failing_source_does_not_stop_the_others(self):
        class Partly(FakeTelegram):
            async def get_entity(self, ref):
                if ref == "@gone":
                    raise ValueError('No user has "gone" as username')
                return types.SimpleNamespace(username=ref.lstrip("@").title(), id=len(ref))

            async def iter_messages(self, entity, limit=None, *, min_id=0, reverse=False):
                if entity.username == "Broken":
                    raise ConnectionError("timeout")
                async for msg in FakeTelegram.iter_messages(self, entity, limit, min_id=min_id, reverse=reverse):
                    yield msg

        source = TelegramUserbotSource()
        source.configure(["@gone", "@broken", "@bot"])
        source._client = Partly([tg_message(6), tg_message(7)])
        source.bind_watermark(lambda name: 5)
        emitted = []

        async def emit(raw):
            emitted.append(raw.source)

        with self.assertLogs("reelay.source", "WARNING"):
            self.assertEqual(await source.backfill(emit), 2)
        self.assertEqual(emitted, ["@Bot", "@Bot"])

    async def test_long_downtime_backfills_newest_messages_within_window(self):
        """停机很久时（水位之后先是大量过期消息），上限只计入窗口内最新的消息。"""
        source = TelegramUserbotSource()
        source.configure(["@bot"])
        old = [tg_message(i, days=10) for i in range(11, 411)]
        recent = [tg_message(i) for i in range(411, 416)]
        source._client = FakeTelegram(old + recent)
        source.bind_watermark(lambda name: 10)
        emitted = []

        async def emit(raw):
            emitted.append(raw.msg_id)

        with runtime.scope({"telegram": {"backfill_limit": 3, "backfill_max_days": 7}}):
            self.assertEqual(await source.backfill(emit), 3)
        self.assertEqual(emitted, ["413", "414", "415"])
        emitted.clear()
        self.assertEqual(await source.backfill(emit), 5)  # 默认上限 200：只补 7 天内的 5 条
        self.assertEqual(emitted, [str(i) for i in range(411, 416)])

    async def test_backfilled_tasks_skip_per_task_approval_cards(self):
        from reelay.providers.decision import SubscriptionDecision
        registry = Registry()
        registry.add_provider("parser", RegexParser())
        registry.add_provider("decision", DefaultDecision())
        registry.add_provider("decision", SubscriptionDecision())  # 默认决策
        pipeline = self.pipeline({"save_dirs": {"默认": "1"}, "providers": {"source": "telegram", "notify": ["console"]},
                                  "defaults": {"save_to": "默认", "parser": "regex"}}, registry)
        live, old = message(msg_id="1"), message(msg_id="2")
        old.raw.extra["backfill"] = True
        for ctx in (live, old):
            await pipeline.process_new(ctx)
            self.assertEqual(self.store.get_task(ctx.task_id).state, TaskState.PENDING_APPROVAL)
        cards = [r["task_id"] for r in self.store._conn.execute(
            "SELECT task_id FROM outbox WHERE event='pending_approval'")]
        self.assertEqual(cards, [live.task_id])

    async def test_source_loop_sends_one_backfill_summary(self):
        from reelay.main import _source_loop

        class Backfilling:
            name = "fake"
            backfilled = 0
            backfill_done = False

            async def listen(self, emit):
                self.backfilled += 37
                self.backfill_done = True
                await asyncio.Event().wait()

            def health(self):
                return {"status": "connected"}

        registry = Registry()
        registry.add_provider("source", Backfilling())
        pipeline = self.pipeline({"save_dirs": {"默认": "1"}, "defaults": {"save_to": "默认"},
                                  "providers": {"source": "fake", "notify": ["console"]}}, registry)
        pipeline.hold_daily(900)
        self.assertFalse(pipeline.sources_ready)
        loop = asyncio.create_task(_source_loop(pipeline, None, poll_interval=0.05))
        await asyncio.sleep(0.3)
        loop.cancel()
        await asyncio.gather(loop, return_exceptions=True)
        notices = [json.loads(r[0])["text"] for r in self.store._conn.execute(
            "SELECT snapshot FROM outbox WHERE kind='notice' AND event='notice'")]
        self.assertEqual(len(notices), 1)
        self.assertIn("37 条消息", notices[0])
        self.assertTrue(pipeline.sources_ready)

    async def test_first_listen_does_not_import_history(self):
        source = TelegramUserbotSource()
        source.configure(["@bot"])
        source._client = FakeTelegram([tg_message(1)])
        source.bind_watermark(lambda name: None)
        self.assertEqual(await source.backfill(lambda raw: None), 0)

    async def test_source_loop_alerts_after_prolonged_disconnect(self):
        from reelay.main import _source_loop

        class Disconnected:
            name = "flaky"

            async def listen(self, emit):
                await asyncio.Event().wait()

            def health(self):
                return {"status": "disconnected"}

        registry = Registry()
        registry.add_provider("source", Disconnected())
        pipeline = self.pipeline({"save_dirs": {"默认": "1"}, "defaults": {"save_to": "默认"},
                                  "providers": {"source": "flaky", "notify": ["console"]}}, registry)
        pipeline.cfg["telegram"]["alert_after_sec"] = 1  # 校验下限是 10 秒，测试直接改有效配置
        loop = asyncio.create_task(_source_loop(pipeline, None, poll_interval=0.05))
        await asyncio.sleep(1.3)
        loop.cancel()
        await asyncio.gather(loop, return_exceptions=True)
        self.assertEqual([a["key"] for a in self.store.active_alerts()], ["source"])


class TestDailyWaitsForStartupBackfill(PipelineCase):
    CFG = {"save_dirs": {"默认": "1"}, "defaults": {"save_to": "默认"}, "schedule": {"daily_hour": 0}}

    def daily_summary(self):
        row = self.store._conn.execute("SELECT snapshot FROM outbox WHERE event='on_daily'").fetchone()
        return json.loads(row[0])["meta"]["system"]["summary"]

    async def test_daily_report_waits_until_backfilled_messages_are_parsed(self):
        registry = Registry()
        registry.add_listener(EpisodeTracker())
        pipeline = self.pipeline(self.CFG, registry)
        now = time.time()
        pipeline.hold_daily(900, now=now)
        self.assertFalse(await pipeline.emit_daily(now=now + 1))  # 消息源还在补拉
        pipeline.sources_ready = True
        ctx = await pipeline.accept(message())
        self.assertFalse(await pipeline.emit_daily(now=now + 2))  # 补拉的消息还没解析
        self.store.set_state(ctx.task_id, TaskState.PENDING_APPROVAL, expected=TaskState.RECEIVED)
        self.assertTrue(await pipeline.emit_daily(now=now + 3))
        self.assertEqual(self.daily_summary()["received"], 1)

    async def test_daily_report_is_not_held_forever_when_source_never_connects(self):
        registry = Registry()
        registry.add_listener(EpisodeTracker())
        pipeline = self.pipeline(self.CFG, registry)
        now = time.time()
        pipeline.hold_daily(900, now=now)
        self.assertFalse(await pipeline.emit_daily(now=now + 60))
        self.assertTrue(await pipeline.emit_daily(now=now + 901))

    async def test_daily_report_is_not_held_without_startup_hold(self):
        registry = Registry()
        registry.add_listener(EpisodeTracker())
        pipeline = self.pipeline(self.CFG, registry)
        await pipeline.accept(message())
        self.assertTrue(await pipeline.emit_daily())


if __name__ == "__main__":
    unittest.main()
