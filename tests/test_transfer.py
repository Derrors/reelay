"""转存与整理的故障注入测试：SDK 假实现严格区分文件 ID 和路径，不访问网盘。"""
from __future__ import annotations

import asyncio
import os
import sqlite3
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from reelay.core.contracts import ParsedResource, PipelineContext, RawMessage, TaskState
from reelay.core.pipeline import Pipeline
from reelay.core.registry import Registry, discover
from reelay.core.service import ConflictError, ManagementService
from reelay.core.store import DEDUP_MESSAGE, Store
from reelay.core.worker import Worker
from reelay.plugins.organizer import Organizer
from reelay.providers.storage import P115Storage, _select_video_items


class Cloud:
    def __init__(self):
        self.lock = threading.RLock()
        self.items = {
            1: self.directory(1, 0, 'library'),
            2: self.directory(2, 0, 'other-library'),
        }
        self.next_id = 100
        self.shares = {}
        self.calls = []
        self.receive_mode = {}
        self.barrier = None
        self.before_receive = None
        self.before_mutation = None
        self.fail_move_after_effect = False
        self.ignore_move = False

    @staticmethod
    def directory(fid, pid, name):
        return {'id': fid, 'parent_id': pid, 'name': name, 'is_dir': True, 'size': 0}

    def add(self, item, pid):
        with self.lock:
            self.next_id += 1
            fid = self.next_id
            self.items[fid] = {**item, 'id': fid, 'parent_id': pid}
            return dict(self.items[fid])

    def add_share(self, code, *names):
        self.shares[code] = [
            {'id': str(500 + i), 'parent_id': 0, 'name': name, 'size': 123 + i,
             'sha1': f'{i + 1:040X}', 'is_dir': False}
            for i, name in enumerate(names)
        ]

    def children(self, pid):
        if not isinstance(pid, int):
            raise AssertionError('SDK 的字符串参数是路径；文件 ID 必须传 int')
        with self.lock:
            return [dict(x) for x in self.items.values() if x['parent_id'] == pid]

    def sdk(self):
        cloud = self
        module = types.ModuleType('p115client')
        module.P115Client = lambda cookies: cloud

        class FS:
            def __init__(self, client):
                assert client is cloud

            def iterdir(self, id_or_path, /):
                return iter(cloud.children(id_or_path))

            # 与已核对的 p115client SDK 一致：第一个参数是必需的位置参数。
            def mkdir(self, id_or_path, /, name, pid=None):
                if not isinstance(id_or_path, int):
                    raise AssertionError('mkdir 必须传入父目录 ID')
                return cloud.add(Cloud.directory(0, id_or_path, name), id_or_path)

            def get_attr(self, id_or_path, /, *, refresh=False):
                assert isinstance(id_or_path, int) and refresh
                with cloud.lock:
                    return dict(cloud.items[id_or_path])

            def rename(self, id_or_path, /, name):
                if cloud.before_mutation:
                    cloud.before_mutation('rename', id_or_path)
                with cloud.lock:
                    cloud.calls.append(('rename', id_or_path, name))
                    cloud.items[id_or_path]['name'] = name

            def move(self, id_or_path, /, to_dir=''):
                if cloud.before_mutation:
                    cloud.before_mutation('move', id_or_path)
                with cloud.lock:
                    cloud.calls.append(('move', id_or_path, to_dir))
                    if not cloud.ignore_move:
                        cloud.items[id_or_path]['parent_id'] = to_dir
                    if cloud.fail_move_after_effect:
                        cloud.fail_move_after_effect = False
                        raise TimeoutError('移动成功，但响应中断')

        class ShareFS:
            def __init__(self, client, share_code, receive_code=None):
                assert client is cloud
                self.code = share_code

            def iterdir(self, id_or_path=0, /):
                return iter([dict(x) for x in cloud.shares[self.code]])

            def receive(self, ids, /, to_pid=0):
                if cloud.before_receive:
                    cloud.before_receive(self.code, to_pid)
                if cloud.barrier:
                    cloud.barrier.wait(timeout=5)
                with cloud.lock:
                    cloud.calls.append(('receive', self.code, to_pid, list(ids)))
                    selected = [x for x in cloud.shares[self.code] if x['id'] in ids]
                    mode = cloud.receive_mode.get(self.code)
                    if mode == 'partial':
                        selected = selected[:1]
                    for item in selected:
                        copied = cloud.add(item, to_pid)
                        if mode == 'corrupt':
                            cloud.items[copied['id']]['sha1'] = 'wrong-hash'
                    if mode == 'timeout':
                        raise TimeoutError('转存成功，但响应中断')
                    return {'state': True}

        module.P115FileSystem = FS
        module.P115ShareFileSystem = ShareFS
        return module

    def receives(self):
        return [x for x in self.calls if x[0] == 'receive']


class TestTransferRecovery(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {}, clear=True)
        self.env.start()
        self.cloud = Cloud()
        self.sdk = patch.dict('sys.modules', {'p115client': self.cloud.sdk()})
        self.sdk.start()
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tempdir.name) / 'tasks.db'
        self.store = Store(self.db_path)
        self.storage = P115Storage()
        self.storage._client = self.cloud
        self.organizer = Organizer()
        self.organizer._client = self.cloud
        self.reg = Registry()
        self.reg.add_provider('storage', self.storage)
        self.reg.add_hook(self.organizer)
        self.cfg = {'save_dirs': {'默认': '1', '新目录': '2'}, 'defaults': {'save_to': '默认'}, 'providers': {'notify': []}}
        self.pipeline = Pipeline(self.reg, self.store, self.cfg)
        self.service = ManagementService(self.store, self.pipeline)

    def tearDown(self):
        self.store.close()
        self.tempdir.cleanup()
        self.sdk.stop()
        self.env.stop()

    def task(self, code='share', *, title='剧集', episodes=(1,), alias='默认'):
        ctx = PipelineContext(RawMessage('@bot', str(len(self.store.list_tasks())), 'message'))
        ctx.parsed = ParsedResource(is_media=True, media_type='tv', title=title,
                                    year=2026, season=1, episodes=list(episodes), share_code=code)
        ctx.save_dir_alias = alias
        ctx.state = TaskState.QUEUED
        self.store.save_task(ctx)
        return ctx

    async def execute(self, ctx):
        await self.pipeline.execute_save(self.store.get_task(ctx.task_id))
        return self.store.get_task(ctx.task_id)

    def reopen(self):
        self.store.close()
        self.store = Store(self.db_path)
        self.pipeline = Pipeline(self.reg, self.store, self.cfg)
        self.service = ManagementService(self.store, self.pipeline)
        self.store.recover_interrupted()

    async def test_manifest_and_plan_are_committed_before_remote_writes(self):
        self.cloud.add_share('share', 'Show.S01E01.mkv')
        ctx = self.task()
        def before_receive(code, stage):
            stored = self.store.get_task(ctx.task_id)
            self.assertEqual(stored.state, TaskState.SAVING)
            self.assertEqual(stored.ns('storage')['phase'], 'receiving')
            self.assertEqual(stored.ns('storage')['manifest']['staging_cid'], stage)
        def before_mutation(operation, fid):
            stored = self.store.get_task(ctx.task_id)
            self.assertEqual(stored.state, TaskState.ORGANIZING)
            self.assertEqual(int(stored.ns('organize')['plan'][0]['src_id']), fid)
            self.assertTrue(stored.result['storage_verified'])
        self.cloud.before_receive = before_receive
        self.cloud.before_mutation = before_mutation
        done = await self.execute(ctx)
        self.assertEqual(done.state, TaskState.DONE)
        self.assertTrue(done.ns('organize')['applied'])
        fid = int(done.ns('storage')['landed'][0]['id'])
        self.assertEqual(self.cloud.items[fid]['name'], '剧集 - S01E01.mkv')
        self.assertEqual(self.cloud.children(done.ns('storage')['manifest']['staging_cid']), [])

    async def test_concurrent_shares_own_separate_staging_directories(self):
        self.cloud.add_share('a', 'Alpha.S01E01.mkv')
        self.cloud.add_share('b', 'Beta.S01E01.mp4')
        self.cloud.barrier = threading.Barrier(2)
        a, b = self.task('a', title='Alpha'), self.task('b', title='Beta')
        done_a, done_b = await asyncio.gather(self.execute(a), self.execute(b))
        self.assertEqual([done_a.state, done_b.state], [TaskState.DONE, TaskState.DONE])
        self.assertNotEqual(done_a.result['staging_cid'], done_b.result['staging_cid'])
        self.assertEqual(done_a.result['saved_files'], ['Alpha.S01E01.mkv'])
        self.assertEqual(done_b.result['saved_files'], ['Beta.S01E01.mp4'])
        self.assertNotEqual(done_a.result['landed'][0]['id'], done_b.result['landed'][0]['id'])

    async def test_queued_duplicate_share_is_rechecked_before_execution(self):
        self.cloud.add_share('share', 'S01E01.mkv')
        a, b = self.task(), self.task()
        await asyncio.gather(self.execute(a), self.execute(b))
        done_b = await self.execute(b)
        self.assertEqual(done_b.state, TaskState.SKIPPED)
        self.assertEqual(len(self.cloud.receives()), 1)

    async def test_different_shares_of_same_episode_are_serialized(self):
        self.cloud.add_share('a', 'S01E01.mkv')
        self.cloud.add_share('b', 'S01E01.mkv')
        a, b = self.task('a'), self.task('b')
        await asyncio.gather(self.execute(a), self.execute(b))
        self.assertEqual((await self.execute(b)).state, TaskState.SKIPPED)
        self.assertEqual(len(self.cloud.receives()), 1)

    async def test_queued_update_only_receives_new_episodes(self):
        self.cloud.add_share('share', 'S01E01.mkv', 'S01E02.mkv')
        a, b = self.task(episodes=(1,)), self.task(episodes=(1, 2))
        await self.execute(a)
        done = await self.execute(b)
        self.assertEqual(done.parsed.episodes, [2])
        self.assertEqual([x[3] for x in self.cloud.receives()], [['500'], ['501']])
        self.assertEqual(done.state, TaskState.DONE)

    async def test_dedup_is_scoped_to_target_directory(self):
        self.cloud.add_share('share', 'S01E01.mkv')
        await self.execute(self.task())
        done = await self.execute(self.task(alias='新目录'))
        self.assertEqual(done.state, TaskState.DONE)
        self.assertEqual(len(self.cloud.receives()), 2)
        self.assertEqual(done.result['to_cid'], 2)

    async def test_timeout_after_receive_is_reconciled_without_resending(self):
        self.cloud.add_share('share', 'S01E01.mkv')
        self.cloud.receive_mode['share'] = 'timeout'
        done = await self.execute(self.task())
        self.assertEqual(done.state, TaskState.DONE)
        self.assertEqual(len(self.cloud.receives()), 1)

    async def test_restart_after_receive_before_receipt_uses_read_only_reconciliation(self):
        self.cloud.add_share('share', 'S01E01.mkv')
        ctx = self.task()
        original_save = self.storage.save
        async def crash_after_receive(context):
            await original_save(context)
            raise asyncio.CancelledError('模拟进程在保存回执前退出')
        self.storage.save = crash_after_receive
        with self.assertRaises(asyncio.CancelledError):
            await self.execute(ctx)
        self.assertEqual(self.store.get_task(ctx.task_id).ns('storage')['phase'], 'receiving')
        self.reopen()
        done = await self.execute(ctx)
        self.assertEqual(done.state, TaskState.DONE)
        self.assertEqual(len(self.cloud.receives()), 1)

    async def test_receipt_dedup_and_claim_release_roll_back_as_one_unit(self):
        self.cloud.add_share('share', 'S01E01.mkv')
        ctx = self.task()
        self.store._conn.execute("CREATE TRIGGER fail_receipt BEFORE INSERT ON dedup WHEN NEW.kind='share' BEGIN SELECT RAISE(ABORT, 'disk failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            await self.execute(ctx)
        persisted = self.store.get_task(ctx.task_id)
        self.assertEqual(persisted.state, TaskState.SAVING)
        self.assertEqual(persisted.ns('storage')['phase'], 'receiving')
        self.assertIsNone(persisted.result)
        self.assertFalse(self.store.saved_share('share', '1'))
        self.assertGreater(self.store._conn.execute('SELECT COUNT(*) FROM resource_claims').fetchone()[0], 0)
        self.store._conn.execute('DROP TRIGGER fail_receipt')
        self.reopen()
        self.assertEqual((await self.execute(ctx)).state, TaskState.DONE)
        self.assertEqual(len(self.cloud.receives()), 1)

    async def test_partial_transfer_retains_claim_and_manual_recheck_never_resends(self):
        self.cloud.add_share('share', 'S01E01.mkv', 'S01E02.mkv')
        self.cloud.receive_mode['share'] = 'partial'
        ctx = self.task(episodes=(1, 2))
        manual = await self.execute(ctx)
        self.assertEqual(manual.state, TaskState.NEED_MANUAL)
        self.assertIsNone(manual.result)
        with self.assertRaises(ConflictError):
            await self.service.reparse(ctx.task_id)
        with self.assertRaises(ConflictError):
            self.service.patch_parsed(ctx.task_id, {'title': '改名'})
        other = self.task(episodes=(1, 2))
        self.assertEqual((await self.execute(other)).state, TaskState.QUEUED)
        await self.service.reconcile_transfer(ctx.task_id)
        self.assertEqual((await self.execute(ctx)).state, TaskState.NEED_MANUAL)
        stage = manual.ns('storage')['manifest']['staging_cid']
        self.cloud.add(self.cloud.shares['share'][1], stage)  # 模拟人工补齐或远端延迟落地
        await self.service.reconcile_transfer(ctx.task_id)
        self.assertEqual((await self.execute(ctx)).state, TaskState.DONE)
        self.assertEqual((await self.execute(other)).state, TaskState.SKIPPED)
        self.assertEqual(len(self.cloud.receives()), 1)

    async def test_corrupt_landed_content_is_not_committed_as_success(self):
        self.cloud.add_share('share', 'S01E01.mkv')
        self.cloud.receive_mode['share'] = 'corrupt'
        manual = await self.execute(self.task())
        self.assertEqual(manual.state, TaskState.NEED_MANUAL)
        self.assertFalse(self.store.saved_share('share', '1'))
        self.assertFalse(any(x[0] == 'rename' for x in self.cloud.calls))

    async def test_restart_with_persisted_organizing_plan_does_not_receive_again(self):
        self.cloud.add_share('share', 'S01E01.mkv')
        ctx = self.task()
        original_run = self.organizer.run
        async def crash_before_organize(context):
            raise asyncio.CancelledError('模拟整理阶段进程退出')
        self.organizer.run = crash_before_organize
        with self.assertRaises(asyncio.CancelledError):
            await self.execute(ctx)
        stored = self.store.get_task(ctx.task_id)
        self.assertEqual(stored.state, TaskState.ORGANIZING)
        self.assertTrue(stored.ns('organize')['plan'])
        self.organizer.run = original_run
        self.reopen()
        self.assertEqual((await self.execute(ctx)).state, TaskState.DONE)
        self.assertEqual(len(self.cloud.receives()), 1)

    async def test_partial_organizing_replay_uses_actual_file_id_and_location(self):
        self.cloud.add_share('share', 'S01E01.mkv')
        self.cloud.fail_move_after_effect = True
        ctx = self.task()
        manual = await self.execute(ctx)
        self.assertEqual(manual.state, TaskState.NEED_MANUAL)
        self.assertTrue(manual.result['storage_verified'])
        self.assertFalse(manual.result['verified'])
        await self.service.requeue_organize(ctx.task_id)
        self.assertEqual((await self.execute(ctx)).state, TaskState.DONE)
        self.assertEqual(len(self.cloud.receives()), 1)
        self.assertEqual(len([x for x in self.cloud.calls if x[0] == 'rename']), 1)
        self.assertEqual(len([x for x in self.cloud.calls if x[0] == 'move']), 1)

    async def test_name_conflict_keeps_both_files_and_requires_manual(self):
        self.cloud.add_share('share', 'S01E01.mkv')
        folder = self.cloud.add(Cloud.directory(0, 1, '剧集 (2026)'), 1)
        season = self.cloud.add(Cloud.directory(0, folder['id'], 'Season 01'), folder['id'])
        existing = self.cloud.add({'name': '剧集 - S01E01.mkv', 'is_dir': False, 'size': 9}, season['id'])
        manual = await self.execute(self.task())
        self.assertEqual(manual.state, TaskState.NEED_MANUAL)
        fid = int(manual.result['landed'][0]['id'])
        self.assertEqual(self.cloud.items[fid]['name'], 'S01E01.mkv')
        self.assertEqual(self.cloud.items[fid]['parent_id'], manual.result['staging_cid'])
        self.assertEqual(self.cloud.items[existing['id']]['size'], 9)

    async def test_false_success_from_move_is_detected_by_readback(self):
        self.cloud.add_share('share', 'S01E01.mkv')
        self.cloud.ignore_move = True
        manual = await self.execute(self.task())
        self.assertEqual(manual.state, TaskState.NEED_MANUAL)
        self.assertIn('读回', manual.ns('organize')['error'])

    async def test_reorganize_resolves_changed_alias_to_new_cid(self):
        self.cloud.add_share('share', 'S01E01.mkv')
        self.cloud.fail_move_after_effect = True
        ctx = self.task()
        await self.execute(ctx)
        await self.service.requeue_organize(ctx.task_id, save_dir_alias='新目录')
        done = await self.execute(ctx)
        self.assertEqual(done.save_dir_cid, '2')
        fid = int(done.result['landed'][0]['id'])
        season = self.cloud.items[self.cloud.items[fid]['parent_id']]
        title = self.cloud.items[season['parent_id']]
        self.assertEqual(title['parent_id'], 2)
        self.assertEqual(done.state, TaskState.DONE)
        self.assertEqual(len(self.cloud.receives()), 1)

    async def test_dry_run_with_cookies_never_initializes_or_calls_live_client(self):
        with patch.dict(os.environ, {'REELAY_DRY_RUN': 'true', 'REELAY_115_COOKIES': 'fake',
                                     'REELAY_LLM_API_KEY': 'fake', 'REELAY_TMDB_API_KEY': 'fake'}):
            with patch('p115client.P115Client', side_effect=AssertionError('不允许初始化真实客户端')):
                registry = discover()
                pipeline = Pipeline(registry, self.store, self.cfg)
                ctx = self.task()
                self.assertIsNone(registry.get_provider('parser', 'llm')._client)
                with patch('urllib.request.urlopen') as request:
                    await pipeline._run_hooks('after_parse', ctx)
                    request.assert_not_called()
                await pipeline.execute_save(ctx)
        self.assertEqual(ctx.state, TaskState.DONE)
        self.assertTrue(ctx.result['dry_run'])
        self.assertEqual(self.cloud.calls, [])

    async def test_worker_drains_parse_then_save_and_duplicate_tasks(self):
        from reelay.providers.decision import DefaultDecision
        class Parser:
            name = 'fake'
            async def parse(self, ctx):
                await asyncio.sleep(0.02)
                return ParsedResource(is_media=True, confidence=1, title='剧集', media_type='tv',
                                      season=1, episodes=[1], share_code='share')
        self.reg.add_provider('parser', Parser())
        self.reg.add_provider('decision', DefaultDecision())
        self.pipeline.cfg['defaults'].update({'parser': 'fake', 'approval_mode': 'auto'})
        self.cloud.add_share('share', 'S01E01.mkv')
        for msg_id in ('a', 'b'):
            await self.pipeline.accept(PipelineContext(RawMessage('@bot', msg_id, 'message')))
        worker = Worker(self.pipeline, self.store, concurrency=2)
        await asyncio.wait_for(worker.run(drain=True), timeout=5)
        self.assertEqual(self.store.counts_by_state(), {'done': 1, 'skipped': 1})
        self.assertEqual(len(self.cloud.receives()), 1)

    async def test_legacy_unknown_transfer_cannot_be_reparsed_or_replayed(self):
        ctx = self.task()
        ctx.state = TaskState.SAVING
        self.store.save_task(ctx)
        self.reopen()
        self.assertEqual(self.store.get_task(ctx.task_id).state, TaskState.NEED_MANUAL)
        with self.assertRaises(ConflictError):
            await self.service.reparse(ctx.task_id)
        with self.assertRaises(ConflictError):
            self.service.patch_parsed(ctx.task_id, {'title': '改名'})
        self.store.set_state(ctx.task_id, TaskState.QUEUED)  # 即使错误入队也不能发送
        self.assertEqual((await self.execute(ctx)).state, TaskState.NEED_MANUAL)
        self.assertEqual(self.cloud.calls, [])

    async def test_waiting_for_claim_does_not_starve_other_queued_tasks(self):
        self.cloud.add_share('share', 'S01E01.mkv', 'S01E02.mkv')
        self.cloud.add_share('other', 'Other.S01E01.mkv')
        self.cloud.receive_mode['share'] = 'partial'
        await self.execute(self.task(episodes=(1, 2)))
        blocked = self.task(episodes=(1, 2))
        runnable = self.task('other', title='Other')
        self.assertEqual(self.store.list_by_state(TaskState.QUEUED, 1)[0]['task_id'], blocked.task_id)
        await self.execute(blocked)
        self.assertEqual(self.store.list_by_state(TaskState.QUEUED, 1)[0]['task_id'], runnable.task_id)
        self.assertEqual((await self.execute(runnable)).state, TaskState.DONE)

    async def test_consumer_failure_stops_worker_and_releases_database_lock(self):
        ctx = self.task()
        worker = Worker(self.pipeline, self.store)
        async def broken_consumer(*args):
            raise RuntimeError('模拟持久层不可用')
        worker._handle = broken_consumer
        with self.assertRaisesRegex(RuntimeError, '持久层'):
            await asyncio.wait_for(worker.run(), timeout=1)
        self.assertEqual(self.store.get_task(ctx.task_id).state, TaskState.QUEUED)
        other_store = Store(self.db_path)
        try:
            other_store.acquire_worker()
        finally:
            other_store.close()

    async def test_cancellation_waits_for_inflight_remote_thread(self):
        from reelay.core.io import run_blocking
        started, release = threading.Event(), threading.Event()
        completed = []
        def remote_operation():
            started.set()
            release.wait(timeout=2)
            completed.append(True)
        operation = asyncio.create_task(run_blocking(remote_operation))
        try:
            await asyncio.wait_for(asyncio.to_thread(started.wait), timeout=1)
            operation.cancel()
            await asyncio.sleep(0)
            self.assertFalse(operation.done())
        finally:
            release.set()
        with self.assertRaises(asyncio.CancelledError):
            await operation
        self.assertEqual(completed, [True])


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
        self.assertEqual(_select_video_items(items, p), [items[1]])
        self.assertEqual(Store.episode_key('Show', None, 1), Store.episode_key('Show', 1, 1))


if __name__ == '__main__':
    unittest.main()
