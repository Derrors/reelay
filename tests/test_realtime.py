"""实时推送：有变化才广播、慢客户端不拖住观察者、推送流随登录状态结束。"""
from __future__ import annotations

import asyncio
import unittest
import unittest.mock

from reelay.core.contracts import TaskState
from reelay.core.store import Store
from reelay.web import realtime
from reelay.web.realtime import EventBus, close_all_streams, event_stream

from tests.fakes import message


class RealtimeCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.store = Store(":memory:")
        self.addCleanup(self.store.close)
        self.bus = EventBus(store=self.store)

    def changed(self) -> list[str]:
        changed, signature = self.bus._snapshot()
        self.bus._last_signature = signature
        return changed


class TestSnapshot(RealtimeCase):
    def test_first_snapshot_reports_everything_then_only_differences(self):
        self.assertEqual(self.changed(), ["counts", "deliveries", "runtime", "tasks"])
        self.assertEqual(self.changed(), [])
        ctx = message()
        ctx.state = TaskState.RECEIVED
        self.store.accept_task(ctx)
        self.assertEqual(self.changed(), ["counts", "tasks"])
        self.store.stage_notice("k", "通知", ["console"])
        self.assertEqual(self.changed(), ["deliveries"])
        self.store.raise_alert("source", "断开", ["console"])
        self.assertIn("runtime", self.changed())


class TestBroadcast(RealtimeCase):
    def test_full_queue_drops_the_oldest_frame(self):
        queue = self.bus.subscribe()
        for number in range(queue.maxsize + 1):
            self.bus._broadcast({"type": "state", "n": number})
        self.assertEqual(queue.qsize(), queue.maxsize)
        self.assertIn('"n":1', queue.get_nowait())  # 第 0 帧被丢掉，最新的还在

    def test_close_streams_ends_subscribers_even_when_their_queue_is_full(self):
        queue = self.bus.subscribe()
        for number in range(queue.maxsize):
            self.bus._broadcast({"type": "state", "n": number})
        close_all_streams()
        frames = [queue.get_nowait() for _ in range(queue.qsize())]
        self.assertIsNone(frames[-1])
        self.assertEqual(self.bus._subscribers, set())


class TestWatcher(RealtimeCase):
    async def test_broadcasts_first_state_and_changes_and_skips_the_database_when_idle(self):
        with unittest.mock.patch.object(realtime, "WATCH_INTERVAL", 0.01):
            with unittest.mock.patch.object(self.store, "counts_by_state", wraps=self.store.counts_by_state) as counts:
                self.bus.start()
                await asyncio.sleep(0.05)
                counts.assert_not_called()  # 没有客户端时不查库
                queue = self.bus.subscribe()
                first = await asyncio.wait_for(queue.get(), 1)
                self.assertIn('"changed":["counts","deliveries","runtime","tasks"]', first)
                self.store.accept_task(message())
                self.assertIn('"tasks"', await asyncio.wait_for(queue.get(), 1))
            await self.bus.stop()
        self.assertIsNone(self.bus._task)

    async def test_survives_a_failing_read(self):
        with unittest.mock.patch.object(realtime, "WATCH_INTERVAL", 0.01):
            with unittest.mock.patch.object(self.store, "counts_by_state", side_effect=[RuntimeError("busy"), {}]):
                queue = self.bus.subscribe()
                self.bus.start()
                with self.assertLogs("reelay.web.events", "WARNING"):
                    await asyncio.sleep(0.05)
                self.assertIn('"type":"state"', await asyncio.wait_for(queue.get(), 1))
            await self.bus.stop()


class TestEventStream(RealtimeCase):
    async def test_stream_announces_itself_and_ends_when_closed(self):
        stream = event_stream(self.bus)
        self.assertEqual(await anext(stream), b": connected\n\n")
        self.assertIn(b"event: hello", await anext(stream))
        self.bus._broadcast({"type": "ping"})
        self.assertIn(b"event: ping", await anext(stream))
        self.bus.close_streams()
        with self.assertRaises(StopAsyncIteration):
            await anext(stream)

    async def test_stream_ends_after_logout(self):
        allowed = [True, False]
        stream = event_stream(self.bus, lambda: allowed.pop(0))
        await anext(stream), await anext(stream)
        self.bus._broadcast({"type": "state"})
        self.assertIn(b"event: state", await anext(stream))
        self.bus._broadcast({"type": "state"})
        with self.assertRaises(StopAsyncIteration):
            await anext(stream)
        self.assertEqual(self.bus._subscribers, set())  # 结束时退订


if __name__ == "__main__":
    unittest.main()
