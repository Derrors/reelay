"""Worker：失败退避、115 鉴权失效与风控时暂停。"""
from __future__ import annotations

import asyncio
import json
import unittest
import time
from unittest.mock import patch

from reelay.core.config import normalize_config
from reelay.core.contracts import ParsedResource, StorageBusy, TaskState
from reelay.core.registry import Registry
from reelay.core.worker import COOLDOWN_ALERT, Worker

from tests.fakes import PipelineCase, message


class FailingStorage:
    name = "p115"

    def __init__(self, error):
        self.error = error

    async def prepare(self, ctx):
        raise RuntimeError(self.error)


class TestWorkerBackoffAndAuthPause(PipelineCase):
    def queued(self):
        ctx = message()
        ctx.parsed = ParsedResource(is_media=True, title="片", media_type="movie", share_code="abc")
        ctx.state = TaskState.QUEUED
        self.store.save_task(ctx)
        return ctx

    def worker(self, error):
        registry = Registry()
        registry.add_provider("storage", FailingStorage(error))
        return Worker(self.pipeline(registry=registry), self.store)

    async def test_failed_task_is_rescheduled_without_sleeping(self):
        worker = self.worker("网络抖动")
        ctx = self.queued()
        started = time.monotonic()
        await worker._handle(ctx.task_id, TaskState.QUEUED)
        self.assertLess(time.monotonic() - started, 1)
        row = self.store._conn.execute("SELECT state, retries, not_before FROM tasks").fetchone()
        self.assertEqual((row["state"], row["retries"]), ("queued", 1))
        self.assertGreater(row["not_before"], time.time())
        self.assertEqual(self.store.list_by_state(TaskState.QUEUED, due_only=True), [])

    async def test_auth_failure_pauses_storage_without_consuming_retries(self):
        worker = self.worker("{'state': False, 'error': '请重新登录', 'errno': 99}")
        ctx = self.queued()
        await worker._handle(ctx.task_id, TaskState.QUEUED)
        row = self.store._conn.execute("SELECT state, retries, not_before FROM tasks").fetchone()
        self.assertEqual((row["state"], row["retries"], row["not_before"]), ("queued", 0, 0))
        self.assertTrue(worker.storage_paused)
        self.assertEqual(self.store.active_alerts()[0]["key"], "p115_auth")
        # 暂停期间不领取转存任务
        await worker._handle(ctx.task_id, TaskState.QUEUED)
        self.assertEqual(self.store._conn.execute("SELECT retries FROM tasks").fetchone()[0], 0)
        worker.resume_storage()
        self.assertIsNone(worker.storage_paused)
        self.assertEqual(self.store.active_alerts(), [])

    async def test_deterministic_failures_are_not_auto_retried(self):
        worker = self.worker("x")
        ctx = self.queued()
        ctx.state = TaskState.FAILED
        ctx.ns("task").update(retry_from="queued", no_auto_retry=True)
        self.store.save_task(ctx, error="无法从文件名识别集号")
        worker._schedule_retry(ctx.task_id, TaskState.QUEUED)
        self.assertEqual(self.store.get_task(ctx.task_id).state, TaskState.FAILED)



class BusyStorage:
    name = "p115"

    def __init__(self, until):
        self.until = until

    async def prepare(self, ctx):
        raise StorageBusy("115 触发风控，冷却中", self.until)


class TestStorageBusy(PipelineCase):
    async def test_busy_storage_defers_the_task_without_failing_or_retrying(self):
        until = time.time() + 600
        registry = Registry()
        registry.add_provider("storage", BusyStorage(until))
        worker = Worker(self.pipeline(registry=registry), self.store)
        ctx = message()
        ctx.parsed = ParsedResource(is_media=True, title="片", media_type="movie", share_code="abc")
        ctx.state = TaskState.QUEUED
        self.store.save_task(ctx)
        await worker._handle(ctx.task_id, TaskState.QUEUED)
        row = self.store._conn.execute("SELECT state, retries, not_before, error FROM tasks").fetchone()
        self.assertEqual((row["state"], row["retries"], row["error"]), ("queued", 0, None))
        self.assertAlmostEqual(row["not_before"], until, places=3)  # 冷却结束才再领取
        self.assertEqual(self.store.list_by_state(TaskState.QUEUED, due_only=True), [])
        self.assertIn("自动继续", self.store.get_task(ctx.task_id).trail[-1])
        self.assertEqual(self.store.active_alerts(), [])  # 不算失败，不发提醒
        self.assertEqual(self.store.delivery_counts(), {})

    async def test_risk_cooldown_pauses_storage_until_it_ends(self):
        from reelay.core import auth115
        credentials = auth115.CredentialStore()
        worker = Worker(self.pipeline(), self.store)
        with patch.object(auth115, "get_store", lambda: credentials):
            credentials._enter_cooldown("操作太频繁")
            worker.check_cooldown()
            self.assertIn("风控", worker.storage_paused)
            self.assertEqual(self.store.active_alerts()[0]["key"], COOLDOWN_ALERT)
            credentials._cooldown_until = 0
            worker.check_cooldown()
        self.assertIsNone(worker.storage_paused)
        self.assertEqual(self.store.active_alerts(), [])


    async def test_cooldown_survives_a_restart(self):
        from reelay.core import auth115
        before = auth115.CredentialStore()
        with patch.object(auth115, "get_store", lambda: before):
            before._enter_cooldown("操作太频繁")
            Worker(self.pipeline(), self.store).check_cooldown()
        until = before.cooldown()[0]
        after = auth115.CredentialStore()  # 主进程重启：冷却只在内存里，新进程的凭证中心没有冷却
        restarted = Worker(self.pipeline(), self.store)
        with patch.object(auth115, "get_store", lambda: after):
            restarted.check_cooldown()
            self.assertIn("风控", restarted.storage_paused)  # 接着冷却
            self.assertAlmostEqual(after.cooldown()[0], until, places=3)
            self.assertEqual(self.store.active_alerts()[0]["key"], COOLDOWN_ALERT)
            after._cooldown_until = 0
            restarted.check_cooldown()
        self.assertIsNone(restarted.storage_paused)
        self.assertEqual(self.store.active_alerts(), [])

    async def test_stale_cooldown_alert_is_cleared_on_start(self):
        # 上次冷却期间重启、冷却没有存下来：告警一直显示生效中，下次真的触发风控也不会再提醒
        self.store.raise_alert(COOLDOWN_ALERT, "115 触发风控，转存与整理暂停到 20:19", ["console"])
        from reelay.core import auth115
        with patch.object(auth115, "get_store", lambda: auth115.CredentialStore()):
            Worker(self.pipeline(), self.store).check_cooldown()
        self.assertEqual(self.store.active_alerts(), [])
        recovered = [json.loads(r[0])["text"] for r in self.store._conn.execute(
            "SELECT snapshot FROM outbox WHERE event='recovered'")]
        self.assertEqual(recovered, ["✅ 115 风控冷却结束，转存与整理继续执行"])


class TestTaskInterval(PipelineCase):
    """p115.task_interval_sec：相邻两个任务开始转存/整理至少隔几秒，所有并发共用。"""

    def queued(self, n=1):
        tasks = []
        for i in range(n):
            ctx = message(msg_id=f"m{i}")
            ctx.parsed = ParsedResource(is_media=True, title=f"片{i}", media_type="movie", share_code=f"s{i}")
            ctx.state = TaskState.QUEUED
            self.store.save_task(ctx)
            tasks.append(ctx)
        return tasks

    def worker(self, interval, concurrency=3):
        worker = Worker(self.pipeline(), self.store, concurrency=concurrency)
        worker.pipeline.cfg["p115"]["task_interval_sec"] = interval
        self.started = []

        async def execute_save(ctx):
            self.started.append((ctx.task_id, time.monotonic()))
            await asyncio.sleep(0.01)
            ctx.state = TaskState.DONE
            self.store.save_task(ctx)

        worker.pipeline.execute_save = execute_save
        return worker

    async def test_default_keeps_tasks_back_to_back(self):
        worker = self.worker(0)
        first, second = self.queued(2)
        self.assertEqual(normalize_config({})["p115"]["task_interval_sec"], 0)
        await worker._handle(first.task_id, TaskState.QUEUED)
        await worker._handle(second.task_id, TaskState.QUEUED)
        self.assertEqual(len(self.started), 2)

    async def test_second_task_waits_without_changing_state_and_config_applies_at_once(self):
        worker = self.worker(60)
        first, second = self.queued(2)
        await worker._handle(first.task_id, TaskState.QUEUED)
        await worker._handle(second.task_id, TaskState.QUEUED)
        self.assertEqual([t for t, _ in self.started], [first.task_id])
        row = self.store.get_task_record(second.task_id)
        self.assertEqual((row["state"], row["retries"]), ("queued", 0))  # 没开始，不算失败
        self.assertGreater(worker.spacing_wait(), 50)
        worker.pipeline.cfg["p115"]["task_interval_sec"] = 0  # 改小立即生效
        await worker._handle(second.task_id, TaskState.QUEUED)
        self.assertEqual(len(self.started), 2)

    async def test_poll_holds_storage_tasks_but_not_parsing(self):
        worker = self.worker(60)
        first, second = self.queued(2)
        received = message(msg_id="new")
        self.store.save_task(received)
        await worker._handle(first.task_id, TaskState.QUEUED)
        poll = asyncio.create_task(worker._poll(drain=False))
        await asyncio.sleep(0.05)
        worker.stop()
        await poll
        queued = []
        while not worker._queue.empty():
            queued.append(worker._queue.get_nowait())
        self.assertEqual(queued, [(received.task_id, TaskState.RECEIVED)])  # 转存等间隔，解析照常入队

    async def test_interval_is_shared_across_consumers(self):
        worker = self.worker(0.3, concurrency=3)
        self.queued(3)
        await asyncio.wait_for(worker.run(drain=True), timeout=5)
        self.assertEqual(len(self.started), 3)  # 排空模式等间隔走完，不提前退出
        times = [t for _, t in self.started]
        gaps = [b - a for a, b in zip(times, times[1:], strict=False)]
        self.assertTrue(all(gap >= 0.29 for gap in gaps), gaps)

    def test_interval_validation(self):
        self.assertEqual(normalize_config({"p115": {"task_interval_sec": 8}})["p115"]["task_interval_sec"], 8)
        for bad in (-1, 3601, "5", True):
            with self.assertRaises(ValueError):
                normalize_config({"p115": {"task_interval_sec": bad}})


if __name__ == "__main__":
    unittest.main()
