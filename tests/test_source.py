"""Telegram 消息源：监听会话、停机补拉、断线告警、日报等补拉完成。"""
from __future__ import annotations

import asyncio
import json
import logging
import struct
import time
import types
import unittest
from unittest.mock import patch

from telethon.errors import InvalidBufferError

from reelay.core import runtime, secret_store
from reelay.core.contracts import TaskState
from reelay.core.registry import Registry
from reelay.plugins.episode_tracker import EpisodeTracker
from reelay.providers.decision import DefaultDecision
from reelay.providers.parser import RegexParser
from reelay.providers.source import TelegramUserbotSource

from tests.fakes import FakeTelegram, PipelineCase, message, tg_message


class TestTelegramListen(unittest.IsolatedAsyncioTestCase):
    """监听会话：等待凭证与登录、拒绝 Bot 会话、网络错误重试、确认身份前不放行消息、断开后重新连接。"""

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
            self.gone = None

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
            self.gone = asyncio.get_running_loop().create_future()
            await self.gone

        def drop(self, error=None):
            """连接中断：run_until_disconnected 结束，带 error 时抛出它（与关掉自动重连的 Telethon 一致）。"""
            self.connected = False
            self.gone.set_exception(error) if error else self.gone.set_result(None)

        async def disconnect(self):
            self.connected = False
            self.disconnects += 1
            if self.gone and not self.gone.done():
                self.gone.set_result(None)

    async def until(self, check, timeout=2):
        """等到 check() 为真，返回它的值。"""
        async def poll():
            while not check():
                await asyncio.sleep(0.01)
        await asyncio.wait_for(poll(), timeout)
        return check()

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
            await self.wait_for_status(source, "connected")  # 登录后接上
            listener.cancel()
            await asyncio.gather(listener, return_exceptions=True)
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
            source = TelegramUserbotSource()
            listener = asyncio.create_task(source.listen(lambda raw: None))
            await self.wait_for_status(source, "connected")
            listener.cancel()
            await asyncio.gather(listener, return_exceptions=True)
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

        async def emit(raw):
            emitted.append(raw)
        with self.fake_telethon(lambda *args, **kwargs: client), \
                secret_store.override(telegram_api_id='1', telegram_api_hash='dummy'):
            source = TelegramUserbotSource()
            source.configure(['@test'])
            listener = asyncio.create_task(source.listen(emit))
            await asyncio.wait_for(asyncio.gather(*await self.until(lambda: callbacks)), 2)
            listener.cancel()
            await asyncio.gather(listener, return_exceptions=True)
        self.assertEqual(len(emitted), 1)
        self.assertEqual(emitted[0].source, '@Test')
        self.assertEqual(emitted[0].urls, ['https://115.com/s/actual'])
        self.assertFalse(client.connected)


    async def test_lost_connection_reconnects_in_process_and_backfills(self):
        """今天的事故：代理不通 3.5 小时，恢复时 Telegram 限流（429），处理更新出错——不再让进程退出。"""
        clients, emitted = [], []

        def factory(*args, **kwargs):
            self.assertIs(kwargs.get("auto_reconnect"), False)  # 断线由监听自己重连，不交给 Telethon
            client = self.FakeClient()
            backlog = FakeTelegram([tg_message(6)] if clients else [])  # 断开期间到了一条
            client.get_entity, client.iter_messages = backlog.get_entity, backlog.iter_messages
            clients.append(client)
            return client

        async def emit(raw):
            emitted.append(raw)
        with self.fake_telethon(factory), patch("reelay.core.telegram.RECONNECT_SEC", 0.3), \
                secret_store.override(telegram_api_id="1", telegram_api_hash="dummy"):
            source = TelegramUserbotSource()
            source.configure(["@bot"])
            source.bind_watermark(lambda name: max([5, *(int(raw.msg_id) for raw in emitted)]))  # 入库后水位前进
            listener = asyncio.create_task(source.listen(emit))
            await self.wait_for_status(source, "connected")
            clients[0].drop(InvalidBufferError(struct.pack("<i", -429)))
            health = await self.until(lambda: "down_for" in source.health() and source.health())
            self.assertEqual(health["status"], "disconnected")  # 带上断开多久，告警与恢复提醒据此计时
            await self.wait_for_status(source, "connected")
            clients[1].drop()  # 心跳没有回应：run_until_disconnected 正常结束，同样重连
            await self.until(lambda: len(clients) == 3 and source.health()["status"] == "connected")
            self.assertFalse(listener.done())
            listener.cancel()
            await asyncio.gather(listener, return_exceptions=True)
        self.assertEqual([raw.msg_id for raw in emitted], ["6"])  # 重新连上后补拉了断开期间的消息
        self.assertTrue(all(client.disconnects for client in clients))

    async def test_slow_or_rate_limited_connects_are_retried(self):
        outcomes = ["hang", InvalidBufferError(struct.pack("<i", -429)), None]
        problems = []

        async def connect(client):
            step = outcomes.pop(0)
            if step == "hang":
                await asyncio.Event().wait()
            if step is not None:
                raise step

        def factory(*args, **kwargs):
            return self.FakeClient(on_connect=connect)

        with self.fake_telethon(factory), patch("reelay.core.telegram.CONNECT_TIMEOUT", 0.05), \
                patch("reelay.providers.source.RETRY_SECONDS", 0.05), \
                secret_store.override(telegram_api_id="1", telegram_api_hash="dummy"):
            source = TelegramUserbotSource()
            listener = asyncio.create_task(source.listen(lambda raw: None))
            while source.health()["status"] != "connected":
                error = source.health().get("error")
                if error and error not in problems:
                    problems.append(error)
                await asyncio.sleep(0.005)
            listener.cancel()
            await asyncio.gather(listener, return_exceptions=True)
        self.assertTrue(problems[0].startswith("连不上 Telegram：连接超时"))
        self.assertTrue(problems[1].startswith("连不上 Telegram：Invalid response buffer (HTTP code 429)"))

    def test_telethons_own_reconnect_error_is_not_logged(self):
        logger = logging.getLogger("telethon.network.mtprotosender")
        with self.assertLogs(logger, "ERROR") as logs:
            logger.error("Automatic reconnection failed %d time(s)", 0)  # 关掉自动重连后每次断线都有
            logger.error("Automatic reconnection failed %d time(s)", 3)
        self.assertEqual([r.getMessage() for r in logs.records], ["Automatic reconnection failed 3 time(s)"])

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


    async def test_outage_is_timed_from_the_disconnect_and_reported_on_recovery(self):
        from reelay.main import _source_loop

        class Recovering:
            name = "flaky"
            status = "connected"

            async def listen(self, emit):
                await asyncio.Event().wait()

            def health(self):
                return {"status": "connected"} if self.status == "connected" else {"status": "disconnected",
                                                                                  "down_for": 3600}

        source = Recovering()
        registry = Registry()
        registry.add_provider("source", source)
        pipeline = self.pipeline({"save_dirs": {"默认": "1"}, "defaults": {"save_to": "默认"},
                                  "providers": {"source": "flaky", "notify": ["console"]}}, registry)
        loop = asyncio.create_task(_source_loop(pipeline, None, poll_interval=0.02))
        await asyncio.sleep(0.1)
        source.status = "disconnected"  # 监听发现时已经断了 1 小时：不再等 alert_after（120 秒）才告警
        await asyncio.sleep(0.1)
        self.assertEqual([a["key"] for a in self.store.active_alerts()], ["source"])
        payload = json.loads(self.store._conn.execute(
            "SELECT payload FROM runtime_health WHERE component='source'").fetchone()[0])
        self.assertEqual(payload["status"], "disconnected")
        self.assertNotIn("down_for", payload)  # 每秒都在变，不写进心跳
        source.status = "connected"
        await asyncio.sleep(0.1)
        loop.cancel()
        await asyncio.gather(loop, return_exceptions=True)
        self.assertEqual(self.store.active_alerts(), [])
        recovered = [json.loads(r[0])["text"] for r in self.store._conn.execute(
            "SELECT snapshot FROM outbox WHERE kind='notice' AND event='recovered'")]
        self.assertEqual(recovered, ["✅ 消息源 flaky 已恢复连接，断开了约 1 小时"])

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
