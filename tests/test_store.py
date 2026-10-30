"""数据库：任务快照、去重、调度与恢复、跨线程访问、清理。"""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from reelay.core import works
from reelay.core.contracts import ParsedResource, PipelineContext, RawMessage, TaskState
from reelay.core.registry import discover
from reelay.core.store.health import HEARTBEAT_INTERVAL
from reelay.core.store import DEDUP_EPISODE, DEDUP_MESSAGE, DEDUP_SHARE, Store, _dump_ctx, _load_ctx
from reelay.providers.storage import _select_video_items

from tests.fakes import PipelineCase, message


class TestStore(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")

    def tearDown(self):
        self.store.close()

    def test_three_layer_dedup(self):
        s = self.store
        # 消息级
        self.assertFalse(s.seen(DEDUP_MESSAGE, "m1"))
        s.mark(DEDUP_MESSAGE, "m1")
        self.assertTrue(s.seen(DEDUP_MESSAGE, "m1"))
        # 资源级
        s.mark(DEDUP_SHARE, "shareX")
        self.assertTrue(s.seen(DEDUP_SHARE, "shareX"))
        # 空 key 不记
        s.mark(DEDUP_MESSAGE, "")
        self.assertFalse(s.seen(DEDUP_MESSAGE, ""))

    def test_stored_episode_pairs_are_scoped_to_title_and_directory(self):
        """追更：只返回同一目标目录、同一剧名已入库的 [季, 集]。"""
        s = self.store
        for ep in range(1, 9):
            s.mark(DEDUP_EPISODE, "1:" + Store.episode_key(works.identity("剧A"), 1, ep))
        s.mark(DEDUP_EPISODE, "2:" + Store.episode_key(works.identity("剧A"), 1, 9))
        s.mark(DEDUP_EPISODE, "1:" + Store.episode_key(works.identity("剧AB"), 1, 9))
        self.assertEqual(s.stored_episode_pairs(works.identity("剧A"), "1"), [[1, ep] for ep in range(1, 9)])

    def test_state_update_keeps_snapshot_consistent(self):
        s = self.store
        ctx = PipelineContext(raw=RawMessage(source="@b", msg_id="retry", text="x"))
        ctx.state = TaskState.FAILED
        s.save_task(ctx, error="boom")
        self.assertTrue(s.set_state(ctx.task_id, TaskState.QUEUED))
        self.assertEqual(s.get_task(ctx.task_id).state, TaskState.QUEUED)

    def test_saving_without_manifest_is_requeued(self):
        # 转存清单先于写请求落库：没有清单说明还没发出转存，可以安全地重新排队
        s = self.store
        ctx = PipelineContext(raw=RawMessage(source="@b", msg_id="resume", text="x"))
        ctx.state = TaskState.SAVING
        s.save_task(ctx)
        recovered = s.recover_interrupted()
        self.assertEqual(recovered[0].state, TaskState.QUEUED)
        self.assertEqual(s.get_task(ctx.task_id).state, TaskState.QUEUED)

    def test_task_snapshot_roundtrip_and_recover(self):
        s = self.store
        ctx = PipelineContext(raw=RawMessage(source="@b", msg_id="1", text="hi"))
        ctx.parsed = ParsedResource(is_media=True, title="片", share_code="sc")
        ctx.state = TaskState.QUEUED
        s.save_task(ctx)
        # 还原
        got = s.get_task(ctx.task_id)
        self.assertEqual(got.parsed.title, "片")
        self.assertEqual(got.state, TaskState.QUEUED)
        # 重启恢复：QUEUED 应被捡回
        recovered = s.recover_interrupted()
        self.assertEqual(len(recovered), 1)


class TestSnapshotAndStore(PipelineCase):
    def test_snapshot_round_trip_keeps_every_parsed_field(self):
        ctx = message()
        ctx.parsed = ParsedResource(is_media=True, title="X", genre_ids=[16], origin_country=["JP"])
        back = _load_ctx(_dump_ctx(ctx))
        self.assertEqual((back.parsed.genre_ids, back.parsed.origin_country), ([16], ["JP"]))

    def test_watermark_is_case_insensitive_and_numeric(self):
        for msg_id in ("9", "120", "dry-1"):
            self.store.save_task(message(msg_id=msg_id, source="@Bot"))
        self.assertEqual(self.store.source_watermark("@bot"), 120)
        self.assertIsNone(self.store.source_watermark("@other"))

    def test_schema_version_is_recorded(self):
        from reelay.core.store import SCHEMA_VERSION
        self.assertEqual(self.store._conn.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)

    def test_newer_database_is_refused(self):
        import sqlite3
        newer = self.root / "newer.db"
        conn = sqlite3.connect(newer)
        conn.execute("PRAGMA user_version=999")
        conn.close()
        with self.assertRaises(RuntimeError):
            Store(newer)

    def test_alert_notifies_once_until_recovered(self):
        self.assertTrue(self.store.raise_alert("k", "坏了", ["console"]))
        self.assertFalse(self.store.raise_alert("k", "还是坏的", ["console"]))
        self.assertEqual(self.store.active_alerts()[0]["text"], "还是坏的")
        self.assertTrue(self.store.clear_alert("k", "好了", ["console"]))
        self.assertFalse(self.store.clear_alert("k", "好了", ["console"]))
        events = [r["event"] for r in self.store._conn.execute("SELECT event FROM outbox ORDER BY id")]
        self.assertEqual(events, ["alert", "recovered"])


class TestPersistenceBoundaries(unittest.TestCase):
    def test_installed_sdk_supports_the_required_filesystem_signatures(self):
        import inspect
        try:
            from p115client import P115FileSystem
        except ImportError:
            self.skipTest('未安装 115 可选依赖')
        for method, args, kwargs in (
            ('mkdir', (1,), {'name': 'library'}),
            ('get_attr', (123,), {'refresh': True}),
            ('rename', (123,), {'name': 'new-name'}),
            ('move', (123,), {'to_dir': 1}),
        ):
            with self.subTest(method=method):
                inspect.signature(getattr(P115FileSystem, method)).bind(object(), *args, **kwargs)

    def test_message_and_task_insertion_roll_back_together(self):
        store = Store(':memory:')
        try:
            store._conn.execute("CREATE TRIGGER fail_task BEFORE INSERT ON tasks BEGIN SELECT RAISE(ABORT, 'disk failure'); END")
            ctx = PipelineContext(RawMessage('@bot', '1', 'message'))
            with self.assertRaises(sqlite3.IntegrityError):
                store.accept_task(ctx)
            self.assertFalse(store.seen(DEDUP_MESSAGE, '@bot:1'))
            store._conn.execute('DROP TRIGGER fail_task')
            self.assertTrue(store.accept_task(ctx))
            self.assertFalse(store.accept_task(ctx))
            self.assertEqual(len(store.list_tasks()), 1)
        finally:
            store.close()

    def test_skipping_prepared_task_releases_claim_in_the_same_transaction(self):
        store = Store(':memory:')
        try:
            ctx = PipelineContext(RawMessage('@bot', 'skip', 'message'))
            ctx.state = TaskState.SAVING
            ctx.ns('storage')['phase'] = 'preparing'
            store.save_task(ctx)
            self.assertTrue(store.claim_resource(ctx, 'p115:1:share'))
            store._conn.execute("CREATE TRIGGER fail_update BEFORE UPDATE ON tasks BEGIN SELECT RAISE(ABORT, 'disk failure'); END")
            ctx.state = TaskState.SKIPPED
            with self.assertRaises(sqlite3.IntegrityError):
                store.save_task(ctx)
            self.assertEqual(store.get_task(ctx.task_id).state, TaskState.SAVING)
            self.assertEqual(store._conn.execute('SELECT COUNT(*) FROM resource_claims').fetchone()[0], 1)
            store._conn.execute('DROP TRIGGER fail_update')
            store.save_task(ctx)
            self.assertEqual(store._conn.execute('SELECT COUNT(*) FROM resource_claims').fetchone()[0], 0)
        finally:
            store.close()

    def test_parsed_and_preparing_tasks_are_recovered(self):
        store = Store(':memory:')
        try:
            parsed = PipelineContext(RawMessage('@bot', 'parsed', 'message'))
            parsed.state = TaskState.PARSED
            preparing = PipelineContext(RawMessage('@bot', 'preparing', 'message'))
            preparing.state = TaskState.SAVING
            preparing.ns('storage')['phase'] = 'preparing'
            for ctx in (parsed, preparing):
                store.save_task(ctx)
            store.recover_interrupted()
            self.assertEqual(store.get_task(parsed.task_id).state, TaskState.RECEIVED)
            self.assertEqual(store.get_task(preparing.task_id).state, TaskState.QUEUED)
        finally:
            store.close()

    def test_two_workers_cannot_own_the_same_database(self):
        with tempfile.TemporaryDirectory() as directory:
            a, b = Store(Path(directory) / 'tasks.db'), Store(Path(directory) / 'tasks.db')
            try:
                a.acquire_worker()
                with self.assertRaises(RuntimeError):
                    b.acquire_worker()
                a.release_worker()
                b.acquire_worker()
                with self.assertRaises(RuntimeError):
                    b.acquire_worker()
            finally:
                a.close()
                b.close()

    def test_registry_instances_do_not_share_provider_clients(self):
        with patch.dict(os.environ, {}, clear=True):
            a, b = discover(), discover()
        self.assertIsNot(a, b)
        self.assertIsNot(a.get_provider('storage', 'p115'), b.get_provider('storage', 'p115'))

    def test_episode_selection_distinguishes_seasons_including_specials(self):
        items = [{'id': 'one', 'name': 'S01E01.mkv'}, {'id': 'special', 'name': 'S00E01.mkv'}]
        p = ParsedResource(episodes=[1], season=0)
        self.assertEqual([x['id'] for x in _select_video_items(items, p)['files']], ['special'])
        show = works.identity('Show')
        self.assertEqual(Store.episode_key(show, None, 1), Store.episode_key(show, 1, 1))


class TestStoreThreading(unittest.TestCase):
    def test_plugin_state_from_organize_thread_does_not_race_event_loop(self):
        # 整理在线程里读写扩展状态；事件循环同时执行同一条语句曾报 API misuse 或读到对方的结果
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / 'reelay.db')
            try:
                for i in range(50):
                    store.state_set('organizer', f'file:{i}', {'rank': i})
                errors, stop = [], threading.Event()

                def organize():
                    i = 0
                    while not stop.is_set():
                        try:
                            if store.state_get('organizer', f'file:{i % 50}') is None:
                                raise AssertionError('读到了不属于自己的结果')
                            store.state_set('organizer', f'file:{i % 50}', {'rank': i})
                        except Exception as exc:
                            errors.append(exc)
                            stop.set()
                        i += 1

                thread = threading.Thread(target=organize)
                thread.start()
                deadline = time.monotonic() + 1.5
                try:
                    while time.monotonic() < deadline and not stop.is_set():
                        store.state_get('inbox_advice', 'missing')
                        store.heartbeat('worker', status='running')
                        store.get_task('missing')
                finally:
                    stop.set()
                    thread.join()
                self.assertEqual(errors, [])
            finally:
                store.close()

    def test_store_methods_using_the_connection_hold_the_lock(self):
        import ast
        import inspect
        from reelay.core import store as store_module
        tree = ast.parse(inspect.getsource(store_module.Store))
        # 构造时还没有其他线程；_write_task / _stage_notice 只在已持锁的事务里调用
        exempt = {'__init__', '_migrate', '_init_pragmas', '_write_task', '_stage_notice'}
        unlocked = []
        for node in tree.body[0].body:
            if not isinstance(node, ast.FunctionDef) or node.name in exempt:
                continue
            source = ast.unparse(node)
            decorated = any(isinstance(d, ast.Name) and d.id == '_locked' for d in node.decorator_list)
            if 'self._conn' in source and not decorated and 'self._lock' not in source:
                unlocked.append(node.name)
        self.assertEqual(unlocked, [])


class TestStoreHousekeeping(unittest.TestCase):
    def test_old_llm_calls_are_pruned(self):
        store = Store(":memory:")
        store.record_llm_call(purpose="parse", model="m", task_id=None, outcome="ok", error=None,
                              duration_ms=5, prompt_tokens=1, completion_tokens=1)
        self.assertEqual(store.prune_llm_calls(0), 0)
        self.assertEqual(store.prune_llm_calls(float("inf")), 1)
        store.close()



class TestHousekeeping(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        self.addCleanup(self.store.close)

    def test_unchanged_heartbeats_are_written_at_most_every_interval(self):
        clock = "reelay.core.store.health.time.time"

        def beat(at, **payload):
            with patch(clock, return_value=at):
                self.store.heartbeat("worker", **payload)
            return self.store.health_status()["worker"]["updated_at"]

        self.assertEqual(beat(1000.0, status="running"), 1000.0)
        self.assertEqual(beat(1005.0, status="running"), 1000.0)  # 内容没变、不到间隔：不写库
        self.assertEqual(beat(1006.0, status="stopped"), 1006.0)  # 状态变化立即写入
        self.assertEqual(beat(1006.0 + HEARTBEAT_INTERVAL, status="stopped"), 1006.0 + HEARTBEAT_INTERVAL)

    def test_stats_top_titles_follow_the_requested_window(self):
        now = time.time()
        for msg_id, title, age in (("1", "新剧", 0), ("2", "新剧", 0), ("3", "旧剧", 40 * 86400)):
            ctx = PipelineContext(RawMessage("@b", msg_id, "x"))
            ctx.parsed = ParsedResource(is_media=True, title=title)
            ctx.state = TaskState.DONE
            self.store.save_task(ctx)
            with self.store._conn:
                self.store._conn.execute("UPDATE tasks SET updated_at=? WHERE task_id=?", (now - age, ctx.task_id))
        stats = self.store.task_stats(now - 30 * 86400)
        self.assertEqual([(t["title"], t["count"], t["done"]) for t in stats["top_titles"]], [("新剧", 2, 2)])
        self.assertEqual(len(stats["done_sizes"]), 0)

    def test_old_deliveries_are_pruned(self):
        now = time.time()
        rows = {"recent-done": ("done", now), "old-done": ("done", now - 8 * 86400),
                "old-skipped": ("skipped", now - 8 * 86400), "recent-failed": ("failed", now - 8 * 86400),
                "old-failed": ("failed", now - 31 * 86400), "old-pending": ("pending", now - 31 * 86400)}
        for key, (status, updated) in rows.items():
            self.store.stage_notice(key, key, ["console"])
            with self.store._conn:
                self.store._conn.execute("UPDATE outbox SET status=?, updated_at=? WHERE snapshot LIKE ?",
                                         (status, updated, f'%"{key}"%'))
        removed = self.store.prune_deliveries(done_before=now - 7 * 86400, failed_before=now - 30 * 86400)
        self.assertEqual(removed, 3)
        left = {json.loads(row["snapshot"])["key"] for row in self.store._conn.execute("SELECT snapshot FROM outbox")}
        self.assertEqual(left, {"recent-done", "recent-failed", "old-pending"})  # 还没投递的不清理



class TestAlertsWaitForTelegram(PipelineCase):
    """断线告警在 Telegram 断开时发不出：不按次数放弃，恢复后补发并写明发生时间，恢复通知排在它后面。"""

    def rows(self):
        return [dict(r) for r in self.store._conn.execute("SELECT id, event, status, attempts FROM outbox ORDER BY id")]

    def test_alerts_keep_retrying_while_other_notices_give_up(self):
        self.store.raise_alert("source", "连不上 Telegram", ["console"])
        self.store.stage_notice("daily", "日报", ["other"])
        alert, daily = [row["id"] for row in self.rows()]
        for _ in range(20):  # 断了很久：一般通知 8 次就放弃，告警继续等
            self.store.finish_delivery(alert, error="ConnectionError")
            self.store.finish_delivery(daily, error="ConnectionError")
        self.assertEqual([(r["status"], r["attempts"]) for r in self.rows()], [("pending", 20), ("failed", 20)])
        next_attempt = self.store._conn.execute("SELECT next_attempt FROM outbox WHERE id=?", (alert,)).fetchone()[0]
        self.assertLessEqual(next_attempt - time.time(), 61)  # 恢复后一分钟内补发
        with self.store._conn:  # 超过 24 小时仍发不出才放弃
            self.store._conn.execute("UPDATE outbox SET created_at=? WHERE id=?", (time.time() - 86401, alert))
        self.store.finish_delivery(alert, error="ConnectionError")
        self.assertEqual(self.rows()[0]["status"], "failed")

    async def test_late_alert_is_marked_and_sent_before_the_recovery(self):
        sent = []

        async def sender(**kwargs):
            sent.append(kwargs["text"])

        pipeline = self.pipeline(registry=discover())
        console = pipeline.reg.get_provider("notify", "console")
        console.configure(sender=sender)
        self.addCleanup(console.configure)
        self.store.raise_alert("source", "连不上 Telegram", ["console"])
        with self.store._conn:  # 断线时发出的告警，13 分钟后才连上
            self.store._conn.execute("UPDATE outbox SET created_at=created_at-780")
        self.store.clear_alert("source", "已恢复连接，断开了约 13 分钟", ["console"])
        while await pipeline.dispatch_deliveries():
            pass
        self.assertEqual(len(sent), 2)
        self.assertTrue(sent[0].startswith("⚠️ 连不上 Telegram\n（") and sent[0].endswith("发生，当时发不出，恢复后补发）"))
        self.assertEqual(sent[1], "✅ 已恢复连接，断开了约 13 分钟")
        self.store.raise_alert("source", "又断了", ["console"])  # 及时送达的告警不加说明
        await pipeline.dispatch_deliveries()
        self.assertEqual(sent[-1], "⚠️ 又断了")

class TestWorkIdentity(unittest.TestCase):
    """内容级去重按作品区分：同名的不同作品（如翻拍剧）不会互相当成已入库。"""

    def setUp(self):
        self.store = Store(":memory:")
        self.addCleanup(self.store.close)

    def test_same_title_works_are_told_apart_by_tmdb_id_then_year(self):
        self.store.mark(DEDUP_EPISODE, "1:" + Store.episode_key(works.identity("幕府将军", 1980, 1001), 1, 1))

        def stored(*args, **kwargs):
            return self.store.stored_episode_pairs(works.identity(*args, **kwargs), "1")

        self.assertEqual(stored("幕府将军", 2024, 126308), [])  # 翻拍：TMDB ID 不同
        self.assertEqual(stored("Shogun", 1980, 1001), [[1, 1]])  # 同一部，片名改过也算
        self.assertEqual(stored("幕府将军", 2024), [])  # TMDB 没匹配上：年份不同
        self.assertEqual(stored("幕府 将军！", 1980), [[1, 1]])  # 规范片名与年份相同
        self.assertEqual(stored("幕府将军"), [[1, 1]])  # 不知道年份：按同一部，宁可确认也不重复转存
        self.assertEqual(stored("幕府将军", 1980, 1001, kind="movie"), [])  # 同号的电影是另一部
        series = self.store.stored_series()
        self.assertEqual(works.episodes(series, works.identity("幕府将军", tmdbid=1001), 1), {1})
        self.assertEqual(works.episodes(series, works.identity("幕府将军", tmdbid=126308), 1), set())

    def test_old_episode_keys_are_migrated_with_work_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "reelay.db"
            store = Store(path)
            ctx = PipelineContext(RawMessage("@b", "1", "x"))
            ctx.parsed = ParsedResource(is_media=True, title="幕府将军", year=1980, media_type="tv", season=1,
                                        episodes=[1], raw_fields={"tmdbid": 1001})
            ctx.state = TaskState.DONE
            store.save_task(ctx)
            store.mark(DEDUP_EPISODE, "1:幕府将军|S01|E01", ctx.task_id)  # 1.0.x 的格式：只有片名
            store.mark(DEDUP_EPISODE, "dryrun:5:没有任务的剧|S00|E02", "gone")
            with store._conn:
                store._conn.execute("INSERT INTO resource_claims VALUES (?,?,?)",
                                    ("p115:1:episode:幕府 将军|S01|E02", ctx.task_id, 0))
                store._conn.execute("PRAGMA user_version=1")
            store.close()
            store = Store(path)  # 打开时自动迁移
            try:
                keys = sorted(r[0] for r in store._conn.execute("SELECT key FROM dedup WHERE kind='episode'"))
                self.assertEqual(keys, ["1:幕府将军|1980|tv:1001|S01|E01", "dryrun:5:没有任务的剧|||S00|E02"])
                claims = [r[0] for r in store._conn.execute("SELECT resource_key FROM resource_claims")]
                self.assertEqual(claims, ["p115:1:episode:幕府将军|S01|E02"])
                self.assertEqual(store._conn.execute("PRAGMA user_version").fetchone()[0], 2)
                self.assertEqual(store.stored_episode_pairs(works.identity("幕府将军", 2024, 126308), "1"), [])
                self.assertEqual(store.stored_episode_pairs(works.identity("幕府将军", 1980), "1"), [[1, 1]])
                self.assertEqual(store.stored_episode_pairs(works.identity("没有任务的剧", 2001), "5", "dryrun"),
                                 [[0, 2]])  # 找不到任务：身份未知，按同一部处理
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
