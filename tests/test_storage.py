"""存储 Provider：转存与中断恢复、选集、目录浏览。"""
from __future__ import annotations

import asyncio
import os
import sqlite3
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from reelay.core.auth115 import CredentialStore
from reelay.core.config import normalize_config
from reelay.core.contracts import ParsedResource, PipelineContext, RawMessage, TaskState
from reelay.core.pipeline import Pipeline
from reelay.core.registry import Registry, discover
from reelay.core.service import ConflictError, ManagementService
from reelay.core.store import Store
from reelay.core.worker import Worker
from reelay.plugins.organizer import Organizer
from reelay.providers import dryrun
from reelay.providers.storage import P115Storage, StorageBrowseError, _select_video_items, browse_dirs


class Cloud:
    def __init__(self):
        self.lock = threading.RLock()
        self.items = {
            1: self.directory(1, 0, 'library'),
            2: self.directory(2, 0, 'other-library'),
        }
        self.next_id = 100
        self.shares = {}
        self.share_gone = {}  # 分享码 -> 不可用原因（如「链接已过期」）
        self.calls = []
        self.receive_mode = {}
        self.landing = {}  # 暂存目录 -> [还要被查看几次, 待落地文件, 届时消失的临时项 ID]
        self.barrier = None
        self.before_receive = None
        self.before_mutation = None
        self.fail_move_after_effect = False
        self.ignore_move = False
        self.fail_delete = None  # 'before'：请求失败未生效；'after'：已删除但响应中断；'ignore'：返回成功却没删

    @staticmethod
    def directory(fid, pid, name):
        return {'id': fid, 'parent_id': pid, 'name': name, 'is_dir': True, 'size': 0}

    def add(self, item, pid):
        with self.lock:
            self.next_id += 1
            fid = self.next_id
            self.items[fid] = {**item, 'id': fid, 'parent_id': pid, 'pickcode': f'pc{fid}'}
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

    def land(self, pid):
        """模拟 115 后台落地：转存被接受后，目录要再被查看几次文件才出现。"""
        with self.lock:
            pending = self.landing.get(pid)
            if pending:
                pending[0] -= 1
                if pending[0] <= 0:
                    _, items, temporary = self.landing.pop(pid)
                    for item in items:
                        self.add(item, pid)
                    for fid in temporary:
                        self.items.pop(fid)

    def sdk(self):
        cloud = self
        module = types.ModuleType('p115client')
        module.P115Client = lambda cookies: cloud

        class FS:
            def __init__(self, client):
                assert client is cloud

            def iterdir(self, id_or_path, /):
                cloud.land(id_or_path)
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
                cloud.fs_rename((id_or_path, name))

            def move(self, id_or_path, /, to_dir=''):
                cloud.fs_move(id_or_path, pid=to_dir)

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
                    if mode == 'background':
                        cloud.landing[to_pid] = [3, selected, []]
                        return {'state': True}
                    if mode == 'unsettled':  # 文件已到齐，但列表里暂时多一个临时目录
                        for item in selected:
                            cloud.add(item, to_pid)
                        temp = cloud.add(Cloud.directory(0, to_pid, '.receiving'), to_pid)
                        cloud.landing[to_pid] = [2, [], [temp['id']]]
                        return {'state': True}
                    for item in selected:
                        copied = cloud.add(item, to_pid)
                        if mode == 'corrupt':
                            cloud.items[copied['id']]['sha1'] = 'wrong-hash'
                    if mode == 'timeout':
                        raise TimeoutError('转存成功，但响应中断')
                    if mode == 'lost':
                        for item in list(cloud.items.values()):
                            if item['parent_id'] == to_pid:
                                cloud.items.pop(item['id'])
                        raise TimeoutError('请求超时，结果不明')
                    if mode in ('busy', 'expired', 'rejected_after_landing'):
                        error = '链接已过期' if mode == 'expired' else '操作太频繁，请稍后再试'
                        if mode == 'expired':
                            cloud.share_gone[self.code] = error
                        if mode != 'rejected_after_landing':
                            for item in list(cloud.items.values()):
                                if item['parent_id'] == to_pid:
                                    cloud.items.pop(item['id'])
                        # 与 p115client 一致：业务错误以 P115OSError(errno, 响应) 抛出
                        raise OSError(5, {'state': False, 'error': error, 'errno': 4100018})
                    return {'state': True}

        module.P115FileSystem = FS
        module.P115ShareFileSystem = ShareFS
        return module

    # 与凭证中心 cookie 写路径使用的 webapi 方法签名一致。
    def fs_mkdir(self, payload):
        pid = payload['pid']
        if not isinstance(pid, int):
            raise AssertionError('mkdir 必须传入父目录 ID')
        made = self.add(Cloud.directory(0, pid, payload['cname']), pid)
        return {'state': True, 'file_id': made['id']}

    def fs_rename(self, pair):
        fid, name = pair
        if self.before_mutation:
            self.before_mutation('rename', fid)
        with self.lock:
            self.calls.append(('rename', fid, name))
            self.items[fid]['name'] = name
        return {'state': True}

    def fs_move(self, fid, pid=0):
        if self.before_mutation:
            self.before_mutation('move', fid)
        with self.lock:
            self.calls.append(('move', fid, pid))
            if not self.ignore_move:
                self.items[fid]['parent_id'] = pid
            if self.fail_move_after_effect:
                self.fail_move_after_effect = False
                raise TimeoutError('移动成功，但响应中断')
        return {'state': True}

    def fs_delete(self, ids):
        with self.lock:
            self.calls.append(('delete', list(ids)))
            fail, self.fail_delete = self.fail_delete, None
            if fail == 'before':
                raise TimeoutError('删除请求超时')
            if fail != 'ignore':
                for fid in ids:
                    self.items.pop(int(fid), None)
            if fail == 'after':
                raise TimeoutError('删除成功，但响应中断')
        return {'state': True}

    def share_snap(self, payload):
        """只读查询分享状态（与 115 webapi 返回结构一致）。"""
        reason = self.share_gone.get(payload['share_code'])
        info = {'share_state': 7, 'forbid_reason': reason} if reason else {'share_state': 1, 'forbid_reason': ''}
        return {'state': True, 'data': {'shareinfo': info}}

    def receives(self):
        return [x for x in self.calls if x[0] == 'receive']


class CloudCredentials(CredentialStore):
    """真实凭证中心的 cookie 读写路径，只把 SDK 客户端换成内存网盘。"""

    def __init__(self, cloud, path):
        super().__init__(path)
        self._cloud = cloud

    @property
    def has_cookie(self):
        return True

    def cookie_client(self):
        return self._cloud


class TestTransferRecovery(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {}, clear=True)
        self.env.start()
        self.cloud = Cloud()
        self.sdk = patch.dict('sys.modules', {'p115client': self.cloud.sdk()})
        self.sdk.start()
        self.no_wait = patch('reelay.providers.storage.LANDING_WAIT', 0)  # 部分落地的用例不真的等待
        self.no_wait.start()
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tempdir.name) / 'tasks.db'
        self.store = Store(self.db_path)
        self.credentials = CloudCredentials(self.cloud, Path(self.tempdir.name) / 'credentials.json')
        self.storage = P115Storage()
        self.storage._auth = self.credentials
        self.organizer = Organizer()
        self.organizer._auth = self.credentials
        self.reg = Registry()
        self.reg.add_provider('storage', self.storage)
        self.reg.add_hook(self.organizer)
        self.cfg = {'save_dirs': {'默认': '1', '新目录': '2'}, 'defaults': {'save_to': '默认'}, 'providers': {'notify': []}}
        self.pipeline = Pipeline(self.reg, self.store, self.cfg)
        self.service = ManagementService(self.store, self.pipeline, config_path=Path(self.tempdir.name) / 'config.yaml')

    def tearDown(self):
        self.store.close()
        self.tempdir.cleanup()
        self.no_wait.stop()
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
        self.service = ManagementService(self.store, self.pipeline, config_path=Path(self.tempdir.name) / 'config.yaml')
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
        self.assertEqual(self.cloud.items[fid]['name'], '剧集 - S01E01 - 第 1 集.mkv')
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

    async def test_share_listing_decides_episodes_and_later_update_is_skipped(self):
        self.cloud.add_share('share', 'S01E01.mkv', 'S01E02.mkv')
        a, b = self.task(episodes=(1,)), self.task(episodes=(1, 2))
        done_a = await self.execute(a)
        self.assertEqual(done_a.parsed.episodes, [1, 2])
        self.assertEqual((await self.execute(b)).state, TaskState.SKIPPED)
        self.assertEqual([x[3] for x in self.cloud.receives()], [['500', '501']])

    def path_of(self, fid):
        parts = []
        while fid in self.cloud.items and fid != 1:  # 1 = 默认目录
            parts.append(self.cloud.items[fid]['name'])
            fid = self.cloud.items[fid]['parent_id']
        return '/'.join(reversed(parts))

    async def test_multi_season_share_is_received_and_organized_by_season(self):
        self.cloud.add_share('pack', 'Show.S01E01.mkv', 'Show.S01E02.mkv', 'Show.S02E01.mkv', 'Show.S02E02.mkv')
        ctx = self.task('pack', episodes=())
        ctx.parsed.season = None
        self.store.save_task(ctx)
        done = await self.execute(ctx)
        self.assertEqual(done.state, TaskState.DONE)
        self.assertEqual((done.parsed.season, done.parsed.episodes), (None, [1, 2]))
        self.assertEqual(sorted(self.path_of(int(x['id'])) for x in done.ns('storage')['landed']), [
            '剧集 (2026)/Season 01/剧集 - S01E01 - 第 1 集.mkv', '剧集 (2026)/Season 01/剧集 - S01E02 - 第 2 集.mkv',
            '剧集 (2026)/Season 02/剧集 - S02E01 - 第 1 集.mkv', '剧集 (2026)/Season 02/剧集 - S02E02 - 第 2 集.mkv'])
        self.assertEqual(self.store.stored_episode_pairs('剧集', '1'), [[1, 1], [1, 2], [2, 1], [2, 2]])
        self.assertTrue(any('按季分别转存与整理' in line for line in done.trail))

        # 另一个分享里 S02E02 已入库，只转存 S03E01；占位同样按（季, 集）
        self.cloud.add_share('next', 'Show.S02E02.mkv', 'Show.S03E01.mkv')
        later = self.task('next', episodes=())
        later.parsed.season = None
        self.store.save_task(later)
        done = await self.execute(later)
        self.assertEqual(done.state, TaskState.DONE)
        self.assertEqual([x[3] for x in self.cloud.receives()][-1], ['501'])
        self.assertEqual((done.parsed.season, done.parsed.episodes), (3, [1]))
        self.assertEqual(self.path_of(int(done.ns('storage')['landed'][0]['id'])),
                         '剧集 (2026)/Season 03/剧集 - S03E01 - 第 1 集.mkv')
        self.assertEqual(done.ns('storage')['content_keys'], ['p115:1:episode:剧集|S03|E01'])

    async def test_message_scope_only_receives_new_declared_episodes(self):
        self.cloud.add_share('share', 'S01E01.mkv', 'S01E02.mkv')
        a, b = self.task(episodes=(1,)), self.task(episodes=(1, 2))
        for ctx in (a, b):
            ctx.source_rule = {'episodes_from': 'message'}
            self.store.save_task(ctx)
        await self.execute(a)
        done = await self.execute(b)
        self.assertEqual(done.parsed.episodes, [2])
        self.assertEqual([x[3] for x in self.cloud.receives()], [['500'], ['501']])
        self.assertEqual(done.state, TaskState.DONE)

    async def test_unresolvable_episode_stops_before_transfer_without_auto_retry(self):
        self.cloud.add_share('share', 'video.mkv')
        failed = await self.execute(self.task())
        self.assertEqual(failed.state, TaskState.FAILED)
        self.assertTrue(failed.ns('task')['no_auto_retry'])
        self.assertEqual(self.cloud.receives(), [])
        self.assertEqual(self.store._conn.execute('SELECT COUNT(*) FROM resource_claims').fetchone()[0], 0)

    def claims(self):
        return self.store._conn.execute('SELECT COUNT(*) FROM resource_claims').fetchone()[0]

    async def test_expired_share_fails_before_creating_staging_directory(self):
        self.cloud.add_share('share', 'S01E01.mkv')
        self.cloud.share_gone['share'] = '链接已过期'
        failed = await self.execute(self.task())
        self.assertEqual(failed.state, TaskState.FAILED)
        self.assertTrue(failed.ns('task')['no_auto_retry'])
        self.assertEqual(self.store.get_task_record(failed.task_id)['error'], '分享不可用，无法转存：链接已过期')
        self.assertEqual((self.cloud.receives(), self.cloud.children(1), self.claims()), ([], [], 0))

    async def test_share_expiring_at_transfer_is_a_failure_not_an_unknown_result(self):
        self.cloud.add_share('share', 'S01E01.mkv')
        self.cloud.receive_mode['share'] = 'expired'
        failed = await self.execute(self.task())
        self.assertEqual(failed.state, TaskState.FAILED)  # 115 明确拒绝：不是「结果不明待核验」
        self.assertTrue(failed.ns('task')['no_auto_retry'])
        self.assertIn('分享不可用，无法转存：链接已过期', self.store.get_task_record(failed.task_id)['error'])
        self.assertNotIn('manifest', failed.ns('storage'))
        self.assertEqual(self.cloud.children(1), [])  # 空暂存目录已清理
        self.assertEqual(self.claims(), 0)

    async def test_transient_rejection_is_retried_and_sends_again(self):
        self.cloud.add_share('share', 'S01E01.mkv')
        self.cloud.receive_mode['share'] = 'busy'
        failed = await self.execute(self.task())
        self.assertEqual(failed.state, TaskState.FAILED)
        self.assertFalse(failed.ns('task').get('no_auto_retry'))
        self.assertEqual(failed.ns('storage')['phase'], 'ready')  # 没有发生转存，重试时重新发送
        self.assertIn('115 拒绝转存：操作太频繁', self.store.get_task_record(failed.task_id)['error'])
        self.cloud.receive_mode.pop('share')
        await self.service.retry(failed.task_id)
        done = await self.execute(failed)
        self.assertEqual(done.state, TaskState.DONE)
        self.assertEqual(len(self.cloud.receives()), 2)
        self.assertEqual(done.ns('storage')['manifest']['staging_cid'], failed.ns('storage')['manifest']['staging_cid'])

    async def test_rejection_with_files_landed_is_still_verified_not_resent(self):
        self.cloud.add_share('share', 'S01E01.mkv')
        self.cloud.receive_mode['share'] = 'rejected_after_landing'
        done = await self.execute(self.task())
        self.assertEqual(done.state, TaskState.DONE)  # 只读核验发现文件已完整落地
        self.assertEqual(len(self.cloud.receives()), 1)
        self.assertTrue(any('改为只读核验，不重发' in line for line in done.trail))

    async def test_verifying_an_empty_staging_after_the_share_expired_concludes_failure(self):
        self.cloud.add_share('share', 'S01E01.mkv')
        self.cloud.receive_mode['share'] = 'lost'
        uncertain = await self.execute(self.task())
        self.assertEqual(uncertain.state, TaskState.NEED_MANUAL)  # 分享仍有效：结果不明，只核验不重发
        await self.service.reconcile_transfer(uncertain.task_id)
        self.assertEqual((await self.execute(uncertain)).state, TaskState.NEED_MANUAL)
        self.cloud.share_gone['share'] = '链接已过期'
        await self.service.reconcile_transfer(uncertain.task_id)
        failed = await self.execute(uncertain)
        self.assertEqual(failed.state, TaskState.FAILED)
        self.assertTrue(failed.ns('task')['no_auto_retry'])
        self.assertEqual((len(self.cloud.receives()), self.cloud.children(1), self.claims()), (1, [], 0))

    async def test_failed_or_manual_tasks_can_be_ignored_only_without_files_left_on_the_drive(self):
        self.cloud.add_share('share', 'S01E01.mkv')
        self.cloud.share_gone['share'] = '链接已过期'
        gone = await self.execute(self.task())
        self.assertEqual(gone.state, TaskState.FAILED)
        await self.service.reject(gone.task_id)
        ignored = self.store.get_task(gone.task_id)
        self.assertEqual(ignored.state, TaskState.SKIPPED)
        self.assertIn('管理端忽略了这条任务', ignored.trail[-1])
        self.assertIsNone(self.store.get_task_record(gone.task_id)['error'])
        self.assertEqual(self.store._conn.execute('SELECT COUNT(*) FROM feedback').fetchone()[0], 0)  # 不当作不想要
        # 发出转存后被 115 明确拒绝：暂存目录已清理，也可以忽略
        self.cloud.share_gone.clear()
        self.cloud.add_share('b', 'S01E01.mkv')
        self.cloud.receive_mode['b'] = 'expired'
        rejected = await self.execute(self.task('b'))
        self.assertEqual(rejected.state, TaskState.FAILED)
        await self.service.reject(rejected.task_id)
        self.assertEqual(self.store.get_task(rejected.task_id).state, TaskState.SKIPPED)
        self.assertEqual(self.claims(), 0)

    async def test_ignoring_is_refused_while_transfer_is_unclear(self):
        self.cloud.add_share('c', 'S01E01.mkv')
        self.cloud.receive_mode['c'] = 'lost'  # 请求超时、结果不明
        uncertain = await self.execute(self.task('c'))
        self.assertEqual((uncertain.state, uncertain.ns('storage')['phase']), (TaskState.NEED_MANUAL, 'receiving'))
        with self.assertRaises(ConflictError):
            await self.service.reject(uncertain.task_id)
        self.assertEqual(self.store.get_task(uncertain.task_id).state, TaskState.NEED_MANUAL)

    async def test_ignoring_is_refused_when_files_already_landed(self):
        ctx, *_ = await self.conflict_task()  # 文件已转存、等待处理整理冲突
        with self.assertRaises(ConflictError):
            await self.service.reject(ctx.task_id)
        self.assertEqual(self.store.get_task(ctx.task_id).state, TaskState.NEED_MANUAL)

    async def test_waiting_task_rechecks_episodes_stored_meanwhile(self):
        self.cloud.add_share('a', 'S01E01.mkv', 'S01E02.mkv')
        self.cloud.add_share('b', 'S01E02.mkv', 'S01E03.mkv')
        a, b = self.task('a', episodes=()), self.task('b', episodes=())
        # a 先占住 E02：b 拿到清单后无法占位，释放全部占位重新排队（不持有任何键等待）。
        self.store.claim_resource(self.store.get_task(a.task_id), 'p115:1:a',
                                  content_keys=['p115:1:episode:' + Store.episode_key('剧集', 1, 2)])
        waiting = await self.execute(b)
        self.assertEqual(waiting.state, TaskState.QUEUED)
        self.assertEqual(waiting.ns('storage')['manifest']['episodes'], [2, 3])
        self.assertEqual(self.store._conn.execute(
            'SELECT COUNT(*) FROM resource_claims WHERE task_id=?', (b.task_id,)).fetchone()[0], 0)
        await self.execute(a)
        done_b = await self.execute(b)
        self.assertEqual(done_b.state, TaskState.DONE)
        self.assertEqual(done_b.parsed.episodes, [3])
        self.assertEqual([x[1] for x in self.cloud.receives()], ['a', 'b'])
        self.assertEqual(len(self.cloud.receives()[1][3]), 1)

    async def test_same_share_link_with_new_episodes_is_not_share_deduped(self):
        self.cloud.add_share('share', 'S01E01.mkv')
        await self.execute(self.task())
        self.cloud.shares['share'].append({'id': '600', 'parent_id': 0, 'name': 'S01E02.mkv',
                                           'size': 200, 'sha1': 'F' * 40, 'is_dir': False})
        done = await self.execute(self.task())
        self.assertEqual((done.state, done.parsed.episodes), (TaskState.DONE, [2]))
        self.assertEqual([x[3] for x in self.cloud.receives()], [['500'], ['600']])

    def existing_library_file(self, name='剧集 - S01E01.mkv', size=10):
        show = self.cloud.add(Cloud.directory(0, 1, '剧集 (2026)'), 1)
        season = self.cloud.add(Cloud.directory(0, show['id'], 'Season 01'), show['id'])
        return self.cloud.add({'name': name, 'size': size, 'is_dir': False}, season['id']), season['id']

    def use_conflict(self, strategy):
        self.cfg = {**self.cfg, 'plugins': {'organizer': {'conflict': strategy}}}
        self.pipeline = Pipeline(self.reg, self.store, self.cfg)

    async def test_conflict_manual_keeps_both_files_for_review(self):
        self.cloud.add_share('share', 'Show.S01E01.2160p.mkv')
        existing, _ = self.existing_library_file()
        done = await self.execute(self.task())
        self.assertEqual(done.state, TaskState.NEED_MANUAL)
        self.assertIn(existing['id'], self.cloud.items)

    async def test_conflict_suffix_keeps_both_files(self):
        self.use_conflict('suffix')
        self.cloud.add_share('share', 'Show.S01E01.2160p.mkv')
        existing, season = self.existing_library_file(name='剧集 - S01E01 - 第 1 集 - 2160p.mkv')
        done = await self.execute(self.task())
        self.assertEqual(done.state, TaskState.DONE)
        names = sorted(x['name'] for x in self.cloud.children(season))
        self.assertEqual(names, ['剧集 - S01E01 - 第 1 集 - 2160p (2).mkv', '剧集 - S01E01 - 第 1 集 - 2160p.mkv'])
        self.assertEqual(done.ns('organize')['conflicts'][0]['action'], 'suffix')

    async def test_conflict_suffix_keeps_own_name_when_versions_differ(self):
        # 文件名带清晰度后，同一集的另一个版本名字不同，仍算冲突，但不必加序号
        self.use_conflict('suffix')
        self.cloud.add_share('share', 'Show.S01E01.2160p.mkv')
        existing, season = self.existing_library_file(name='剧集 - S01E01 - 第 1 集 - 1080p.mkv')
        done = await self.execute(self.task())
        self.assertEqual(done.state, TaskState.DONE)
        names = sorted(x['name'] for x in self.cloud.children(season))
        self.assertEqual(names, ['剧集 - S01E01 - 第 1 集 - 1080p.mkv', '剧集 - S01E01 - 第 1 集 - 2160p.mkv'])
        self.assertEqual(done.ns('organize')['conflicts'][0]['action'], 'suffix')

    async def test_other_episodes_and_other_titles_are_not_conflicts(self):
        self.cloud.add_share('share', 'Show.S01E01.mkv')
        _, season = self.existing_library_file(name='剧集 - S01E02 - 第 2 集.mkv')
        self.cloud.add({'name': '别的剧 - S01E01.mkv', 'is_dir': False, 'size': 9}, season)
        self.cloud.add({'name': '剧集 - S01E01.nfo', 'is_dir': False, 'size': 1}, season)
        done = await self.execute(self.task())
        self.assertEqual(done.state, TaskState.DONE)
        self.assertEqual(done.ns('organize')['conflicts'], [])

    async def test_conflict_skip_discards_new_copy_and_finishes(self):
        self.use_conflict('skip')
        self.cloud.add_share('share', 'Show.S01E01.2160p.mkv')
        existing, season = self.existing_library_file()
        done = await self.execute(self.task())
        self.assertEqual(done.state, TaskState.DONE)
        self.assertEqual([x['id'] for x in self.cloud.children(season)], [existing['id']])
        self.assertIn(('delete', [int(done.ns('storage')['landed'][0]['id'])]), self.cloud.calls)

    async def test_conflict_upgrade_replaces_lower_quality_file(self):
        self.use_conflict('upgrade')
        self.cloud.add_share('share', 'Show.S01E01.2160p.mkv')
        existing, season = self.existing_library_file(size=500)
        self.store.state_set('organizer', f"file:{existing['id']}", {'rank': 3, 'size': 500})  # 曾入库 1080p
        done = await self.execute(self.task())
        self.assertEqual(done.state, TaskState.DONE)
        self.assertNotIn(existing['id'], self.cloud.items)
        landed = int(done.ns('storage')['landed'][0]['id'])
        self.assertEqual([x['id'] for x in self.cloud.children(season)], [landed])
        self.assertEqual(done.ns('organize')['conflicts'][0]['action'], 'upgrade')

    async def test_conflict_upgrade_keeps_better_existing_file(self):
        self.use_conflict('upgrade')
        self.cloud.add_share('share', 'Show.S01E01.720p.mkv')
        existing, season = self.existing_library_file(name='剧集 - S01E01.mkv', size=10**9)
        self.store.state_set('organizer', f"file:{existing['id']}", {'rank': 4})
        done = await self.execute(self.task())
        self.assertEqual(done.state, TaskState.DONE)
        self.assertEqual([x['id'] for x in self.cloud.children(season)], [existing['id']])

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

    async def test_files_landing_in_the_background_are_waited_for(self):
        for code, mode, waits in (('a', 'background', 2), ('b', 'unsettled', 1)):
            self.cloud.add_share(code, 'S01E01.mkv', 'S01E02.mkv')
            self.cloud.receive_mode[code] = mode
            sleeps = []
            with patch('reelay.providers.storage.LANDING_WAIT', 60), \
                    patch('reelay.providers.storage._sleep', sleeps.append):
                done = await self.execute(self.task(code, title=f'剧集{code}', episodes=(1, 2)))
            self.assertEqual(done.state, TaskState.DONE)
            self.assertEqual(len(sleeps), waits)
            self.assertFalse(any('没有确认结果' in line for line in done.trail))
        self.assertEqual(len(self.cloud.receives()), 2)
        # 等到时限仍不一致：交给只读核验，错误里写明实际数量
        self.cloud.add_share('c', 'S01E01.mkv', 'S01E02.mkv')
        self.cloud.receive_mode['c'] = 'partial'
        manual = await self.execute(self.task('c', title='剧集c', episodes=(1, 2)))
        self.assertEqual(manual.state, TaskState.NEED_MANUAL)
        self.assertIn('暂存目录已到 1/2 个文件，还差 1 个：S01E02.mkv', manual.trail[-1])

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

    async def partial_task(self):
        """分享两集，转存只到了第 1 集：转人工并记下进度。"""
        self.cloud.add_share('share', 'S01E01.mkv', 'S01E02.mkv')
        self.cloud.receive_mode['share'] = 'partial'
        ctx = self.task(episodes=(1, 2))
        manual = await self.execute(ctx)
        self.assertEqual(manual.state, TaskState.NEED_MANUAL)
        self.assertEqual(manual.ns('storage')['partial'], {'landed': 1, 'expected': 2, 'missing': ['S01E02.mkv']})
        return ctx, manual.ns('storage')['manifest']['staging_cid']

    async def test_accept_partial_organizes_what_arrived_and_leaves_the_rest_storable(self):
        ctx, _ = await self.partial_task()
        await self.service.accept_partial_transfer(ctx.task_id)
        done = await self.execute(ctx)
        self.assertEqual(done.state, TaskState.DONE)
        self.assertEqual([x['name'] for x in done.ns('storage')['landed']], ['S01E01.mkv'])
        self.assertEqual((done.parsed.episodes, done.ns('storage')['manifest']['missing']), ([1], ['S01E02.mkv']))
        self.assertTrue(any('按已到的 1 个文件继续' in line for line in done.trail))
        self.assertFalse({'partial', 'accept_partial'} & set(done.ns('storage')))
        self.assertEqual(len(self.cloud.receives()), 1)  # 没到的那集不补转存
        # 没到的第 2 集没有记为已入库：以后别的分享还能转存
        self.cloud.add_share('b', 'Show.S01E02.mkv')
        later = await self.execute(self.task('b', episodes=(2,)))
        self.assertEqual(later.state, TaskState.DONE)
        self.assertEqual(self.claims(), 0)

    async def test_accept_partial_needs_files_that_match_the_manifest(self):
        self.cloud.add_share('share', 'S01E01.mkv', 'S01E02.mkv')
        self.cloud.receive_mode['share'] = 'lost'  # 一个也没到
        ctx = self.task(episodes=(1, 2))
        self.assertEqual((await self.execute(ctx)).state, TaskState.NEED_MANUAL)
        with self.assertRaises(ConflictError):
            await self.service.accept_partial_transfer(ctx.task_id)

    async def test_abandon_deletes_the_staging_folder_and_ignores_the_task(self):
        ctx, stage = await self.partial_task()
        await self.service.abandon_transfer(ctx.task_id)
        skipped = await self.execute(ctx)
        self.assertEqual(skipped.state, TaskState.SKIPPED)
        self.assertNotIn(stage, self.cloud.items)
        self.assertEqual(self.cloud.calls.count(('delete', [stage])), 1)
        self.assertEqual((len(self.cloud.receives()), self.claims()), (1, 0))
        self.assertIn('已放弃这次转存', skipped.trail[-1])

    async def test_abandon_is_verified_and_safe_to_retry(self):
        ctx, stage = await self.partial_task()
        await self.service.abandon_transfer(ctx.task_id)
        self.cloud.fail_delete = 'ignore'  # 返回成功却没删：读回发现
        manual = await self.execute(ctx)
        self.assertEqual(manual.state, TaskState.NEED_MANUAL)
        self.assertIn('读回仍在', self.store.get_task_record(ctx.task_id)['error'])
        self.assertIn(stage, self.cloud.items)
        await self.service.abandon_transfer(ctx.task_id)
        self.cloud.fail_delete = 'after'  # 已删除但响应中断
        self.assertEqual((await self.execute(ctx)).state, TaskState.NEED_MANUAL)
        self.assertNotIn(stage, self.cloud.items)
        await self.service.abandon_transfer(ctx.task_id)
        self.assertEqual((await self.execute(ctx)).state, TaskState.SKIPPED)  # 读回已不在，不再删除
        self.assertEqual(self.cloud.calls.count(('delete', [stage])), 2)
        self.assertEqual(len(self.cloud.receives()), 1)

    async def test_partial_transfer_that_stops_changing_is_notified_early(self):
        from reelay.core.worker import RECHECK_STABLE_TIMES, RECHECK_WINDOW_SEC
        sent = []

        async def sender(**kwargs):
            sent.append(kwargs)

        from reelay.providers.notify import ConsoleNotify
        notify = ConsoleNotify()
        notify.configure(sender=sender)
        self.reg.add_provider('notify', notify)
        self.cfg = {**self.cfg, 'providers': {'notify': ['console']}}
        self.pipeline = Pipeline(self.reg, self.store, self.cfg)
        ctx, _ = await self.partial_task()
        worker, now = Worker(self.pipeline, self.store), time.time()

        def notices():
            return self.store._conn.execute("SELECT COUNT(*) FROM outbox WHERE task_id=? AND event='transfer_uncertain'",
                                            (ctx.task_id,)).fetchone()[0]
        for _ in range(RECHECK_STABLE_TIMES - 1):  # 前几次到达数没变化也先不打扰
            now += 3600
            self.assertEqual(await worker.recheck_transfers(now), 1)
            self.assertEqual((await self.execute(ctx)).state, TaskState.NEED_MANUAL)
            self.assertEqual(notices(), 0)
        now += 3600
        self.assertEqual(await worker.recheck_transfers(now), 0)  # 第 3 次仍是 1/2：提前提醒，这一轮不复查
        manual = self.store.get_task(ctx.task_id)
        self.assertEqual((manual.state, manual.ns('storage')['auto_recheck']['notified']), (TaskState.NEED_MANUAL, True))
        self.assertEqual(notices(), 1)
        await self.pipeline.dispatch_deliveries()
        self.assertIn('需要处理</b>：转存只到了 1/2 个文件，多次复查都没有变化\n<blockquote>缺 1 个文件：\nS01E02.mkv</blockquote>',
                      sent[-1]['text'])
        self.assertIn(('✅ 按已到的 1 个继续', f'ap:{ctx.task_id}'), sent[-1]['buttons'][0])
        self.assertEqual(await worker.recheck_transfers(now + 1), 1)  # 后台继续复查
        await self.execute(ctx)
        self.assertEqual(await worker.recheck_transfers(now + RECHECK_WINDOW_SEC * 2), 0)  # 24 小时到了也不再提醒
        self.assertEqual(notices(), 1)
        self.assertEqual(len(self.cloud.receives()), 1)

    async def test_late_landing_is_rechecked_automatically_and_only_notified_when_giving_up(self):
        from reelay.core.worker import RECHECK_FIRST_SEC, RECHECK_WINDOW_SEC
        self.cfg = {**self.cfg, 'providers': {'notify': ['console']}}
        self.pipeline = Pipeline(self.reg, self.store, self.cfg)
        self.cloud.add_share('share', 'S01E01.mkv', 'S01E02.mkv')
        self.cloud.receive_mode['share'] = 'partial'
        ctx = self.task(episodes=(1, 2))
        with self.assertLogs('reelay.pipeline', 'INFO') as logs:
            manual = await self.execute(ctx)
        self.assertEqual(manual.state, TaskState.NEED_MANUAL)
        self.assertTrue(any(f'任务 {ctx.task_id}' in line and '后台自动复查' in line for line in logs.output))

        def notices(task_id):
            return self.store._conn.execute("SELECT COUNT(*) FROM outbox WHERE task_id=? AND event='transfer_uncertain'",
                                            (task_id,)).fetchone()[0]
        self.assertEqual(notices(ctx.task_id), 0)  # 后台还会复查，先不提醒
        worker, now = Worker(self.pipeline, self.store), time.time()
        self.assertEqual(await worker.recheck_transfers(now), 0)  # 还没到 5 分钟
        first = now + RECHECK_FIRST_SEC + 1
        self.assertEqual(await worker.recheck_transfers(first), 1)
        self.assertEqual(self.store.get_task(ctx.task_id).state, TaskState.QUEUED)
        self.assertEqual((await self.execute(ctx)).state, TaskState.NEED_MANUAL)  # 还没到齐
        self.assertEqual(notices(ctx.task_id), 0)
        self.assertEqual(await worker.recheck_transfers(first + 1), 0)  # 间隔翻倍
        stage = manual.ns('storage')['manifest']['staging_cid']
        self.cloud.add(self.cloud.shares['share'][1], stage)  # 115 在后台落地了剩下的文件
        self.assertEqual(await worker.recheck_transfers(first + 2 * RECHECK_FIRST_SEC + 1), 1)
        with self.assertLogs('reelay.pipeline', 'INFO') as logs:
            done = await self.execute(ctx)
        self.assertEqual(done.state, TaskState.DONE)
        self.assertTrue(any(f'任务 {ctx.task_id}' in line and '已入库' in line for line in logs.output))
        self.assertEqual(len(self.cloud.receives()), 1)
        self.assertTrue(any('自动只读核验转存（第 2 次）' in line for line in done.trail))
        self.assertEqual(notices(ctx.task_id), 0)  # 复查通过，整个过程没有打扰
        # 24 小时仍未核验通过：停止自动复查，这时才通知
        self.cloud.add_share('other', 'S01E05.mkv', 'S01E06.mkv')
        self.cloud.receive_mode['other'] = 'partial'
        stuck = self.task('other', title='另一部', episodes=(5, 6))
        await self.execute(stuck)
        late = time.time() + RECHECK_WINDOW_SEC + 10
        self.assertEqual(await worker.recheck_transfers(late), 0)
        stuck = self.store.get_task(stuck.task_id)
        self.assertEqual((stuck.state, stuck.ns('storage')['auto_recheck']['gave_up']), (TaskState.NEED_MANUAL, True))
        self.assertEqual(notices(stuck.task_id), 1)
        self.assertEqual(await worker.recheck_transfers(late + RECHECK_WINDOW_SEC), 0)
        self.assertEqual(notices(stuck.task_id), 1)
        # 放弃复查后人工「核验转存」仍不通过：照常提醒
        await self.service.reconcile_transfer(stuck.task_id)
        self.assertEqual((await self.execute(stuck)).state, TaskState.NEED_MANUAL)

    async def test_expired_share_whose_declared_episodes_are_all_stored_is_a_duplicate(self):
        self.cloud.add_share('a', 'S01E01.mkv', 'S01E02.mkv')
        self.assertEqual((await self.execute(self.task('a', episodes=(1, 2)))).state, TaskState.DONE)
        self.cloud.add_share('b', 'S01E01.mkv', 'S01E02.mkv')
        self.cloud.share_gone['b'] = '链接已过期'
        duplicate = await self.execute(self.task('b', episodes=(1, 2)))
        self.assertEqual(duplicate.state, TaskState.SKIPPED)
        self.assertIn('均已入库', duplicate.trail[-1])
        self.cloud.add_share('c', 'S01E02.mkv', 'S01E03.mkv')
        self.cloud.share_gone['c'] = '链接已过期'
        failed = await self.execute(self.task('c', episodes=(2, 3)))  # E03 还没入库：仍是失败，提醒换个资源
        self.assertEqual(failed.state, TaskState.FAILED)
        self.assertEqual(self.claims(), 0)

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

    async def conflict_task(self):
        """库里已有 S01E01（旧模板命名），本次转存同一集：默认策略转人工，记下两份文件。"""
        self.cloud.add_share('share', 'Show.S01E01.2160p.mkv')
        existing, season = self.existing_library_file(size=9)
        ctx = self.task()
        manual = await self.execute(ctx)
        self.assertEqual(manual.state, TaskState.NEED_MANUAL)
        conflict = manual.ns('organize')['pending_conflict']
        landed = int(manual.ns('storage')['landed'][0]['id'])
        self.assertEqual((conflict['existing']['id'], conflict['existing']['name'], conflict['incoming']['id']),
                         (str(existing['id']), '剧集 - S01E01.mkv', str(landed)))
        self.assertNotIn(('delete', [landed]), self.cloud.calls)
        return ctx, existing, season, landed

    async def test_conflict_choice_delete_new_copy(self):
        ctx, existing, season, landed = await self.conflict_task()
        await self.service.requeue_organize(ctx.task_id, conflict='skip')
        done = await self.execute(ctx)
        self.assertEqual(done.state, TaskState.DONE)
        self.assertNotIn(landed, self.cloud.items)
        self.assertEqual([x['id'] for x in self.cloud.children(season)], [existing['id']])
        self.assertEqual(done.ns('organize')['conflicts'][-1]['action'], 'skip')
        self.assertFalse({'pending_conflict', 'resolve'} & set(done.ns('organize')))
        self.assertEqual(len(self.cloud.receives()), 1)

    async def test_conflict_choice_overwrite_library_file(self):
        ctx, existing, season, landed = await self.conflict_task()
        await self.service.requeue_organize(ctx.task_id, conflict='replace')
        done = await self.execute(ctx)
        self.assertEqual(done.state, TaskState.DONE)
        self.assertNotIn(existing['id'], self.cloud.items)
        self.assertEqual([(x['id'], x['name']) for x in self.cloud.children(season)],
                         [(landed, '剧集 - S01E01 - 第 1 集 - 2160p.mkv')])
        self.assertEqual(done.ns('organize')['conflicts'][-1]['action'], 'replace')
        self.assertIn('覆盖保存', '\n'.join(done.trail))

    async def test_conflict_choice_requires_a_pending_conflict(self):
        self.cloud.add_share('share', 'S01E01.mkv')
        self.cloud.fail_move_after_effect = True
        ctx = self.task()
        self.assertEqual((await self.execute(ctx)).state, TaskState.NEED_MANUAL)
        with self.assertRaises(ConflictError):
            await self.service.requeue_organize(ctx.task_id, conflict='replace')
        with self.assertRaises(ValueError):
            await self.service.requeue_organize(ctx.task_id, conflict='keep')

    async def test_conflict_choice_cannot_change_directory(self):
        ctx, *_ = await self.conflict_task()
        with self.assertRaises(ConflictError):  # 处理冲突时换目录，选的那份文件就不是同一个位置了
            await self.service.requeue_organize(ctx.task_id, conflict='skip', save_dir_alias='新目录')

    async def test_overwrite_with_lost_delete_response_resumes_without_deleting_again(self):
        ctx, existing, season, landed = await self.conflict_task()
        await self.service.requeue_organize(ctx.task_id, conflict='replace')
        self.cloud.fail_delete = 'after'
        manual = await self.execute(ctx)
        self.assertEqual(manual.state, TaskState.NEED_MANUAL)
        self.assertNotIn(existing['id'], self.cloud.items)  # 旧文件其实已删掉
        await self.service.requeue_organize(ctx.task_id)  # 再点「重新整理」：旧文件已不在，直接整理
        done = await self.execute(ctx)
        self.assertEqual(done.state, TaskState.DONE)
        self.assertEqual([x['id'] for x in self.cloud.children(season)], [landed])
        self.assertEqual(self.cloud.calls.count(('delete', [existing['id']])), 1)

    async def test_delete_new_copy_is_verified_and_retried_when_unclear(self):
        ctx, existing, season, landed = await self.conflict_task()
        await self.service.requeue_organize(ctx.task_id, conflict='skip')
        self.cloud.fail_delete = 'ignore'  # 返回成功却没删：读回发现
        manual = await self.execute(ctx)
        self.assertEqual(manual.state, TaskState.NEED_MANUAL)
        self.assertIn('读回', manual.ns('organize')['error'])
        self.assertIn(landed, self.cloud.items)
        await self.service.requeue_organize(ctx.task_id)  # 已决定删除：续跑时再删一次并读回
        done = await self.execute(ctx)
        self.assertEqual(done.state, TaskState.DONE)
        self.assertNotIn(landed, self.cloud.items)
        self.assertEqual([x['id'] for x in self.cloud.children(season)], [existing['id']])

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

    async def test_dry_run_storage_transfers_and_organizes_in_memory(self):
        """模拟存储：转存与整理都在内存网盘里真实执行并读回核验，不初始化 115 客户端。"""
        with patch('p115client.P115Client', side_effect=AssertionError('不允许初始化真实客户端')):
            registry = discover()
            storage = dryrun.install(registry)
            library = storage.drive.mkdir(0, '剧集')
            cfg = {**self.cfg, 'save_dirs': {'默认': str(library)}, 'defaults': {'save_to': '默认', 'storage': 'dryrun'}}
            pipeline = Pipeline(registry, self.store, cfg)
            ctx = self.task(episodes=(1, 2))
            ctx.source_rule = {'storage': 'dryrun'}
            self.store.save_task(ctx)
            await pipeline.execute_save(ctx)
        self.assertEqual(ctx.state, TaskState.DONE)
        self.assertTrue(ctx.result['storage_verified'])
        self.assertEqual(storage.drive.tree(library), [  # 默认按二级分类：保存目录下是「剧集/其他剧集」
            '剧集/', '    其他剧集/', '        剧集 (2026)/', '            Season 01/',
            '                剧集 - S01E01 - 第 1 集 - 1080p.mkv', '                剧集 - S01E02 - 第 2 集 - 1080p.mkv'])
        self.assertEqual(self.cloud.calls, [])

    async def test_worker_drains_parse_then_save_and_duplicate_tasks(self):
        from reelay.providers.decision import DefaultDecision, SubscriptionDecision
        class Parser:
            name = 'fake'
            async def parse(self, ctx):
                await asyncio.sleep(0.02)
                return ParsedResource(is_media=True, confidence=1, title='剧集', media_type='tv',
                                      season=1, episodes=[1], share_code='share')
        self.reg.add_provider('parser', Parser())
        self.reg.add_provider('decision', DefaultDecision())
        self.reg.add_provider('decision', SubscriptionDecision())  # 默认决策
        self.pipeline.cfg['defaults'].update({'parser': 'fake', 'approval_mode': 'auto'})
        self.cloud.add_share('share', 'S01E01.mkv')
        for msg_id in ('a', 'b'):
            await self.pipeline.accept(PipelineContext(RawMessage('@bot', msg_id, 'message')))
        worker = Worker(self.pipeline, self.store, concurrency=2)
        await asyncio.wait_for(worker.run(drain=True), timeout=5)
        self.assertEqual(self.store.counts_by_state(), {'done': 1, 'skipped': 1})
        self.assertEqual(len(self.cloud.receives()), 1)

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


class TestStorageEpisodeSelection(unittest.TestCase):
    ITEMS = [
        {"id": "1", "name": "Show.S01E08.mkv"},
        {"id": "2", "name": "Show.S01E09.mkv"},
        {"id": "3", "name": "Show.S01E10.mkv"},
    ]

    def test_share_listing_is_source_of_truth_and_skips_stored(self):
        parsed = ParsedResource(media_type="tv", episodes=[10])  # 消息只提示最新一集
        selected = _select_video_items(self.ITEMS, parsed, stored=[[1, 8]])
        self.assertEqual([item["id"] for item in selected["files"]], ["2", "3"])
        self.assertEqual((selected["episodes"], selected["season"]), ([9, 10], 1))

    def test_empty_llm_episodes_still_select_from_filenames(self):
        selected = _select_video_items(self.ITEMS, ParsedResource(media_type="tv"))
        self.assertEqual(selected["episodes"], [8, 9, 10])

    def test_message_scope_restricts_to_declared_episodes(self):
        parsed = ParsedResource(media_type="tv", episodes=[9, 10])
        selected = _select_video_items(self.ITEMS, parsed, scope="message")
        self.assertEqual([item["id"] for item in selected["files"]], ["2", "3"])

    def test_message_scope_requires_all_declared_episodes(self):
        from reelay.core.contracts import NeedsManual
        parsed = ParsedResource(media_type="tv", episodes=[9, 10])
        with self.assertRaises(NeedsManual):
            _select_video_items([{"id": "2", "name": "Show.S01E09.mkv"}], parsed, scope="message")

    def test_ambiguous_incremental_share_fails_closed(self):
        from reelay.core.contracts import NeedsManual
        parsed = ParsedResource(media_type="tv", episodes=[9])
        with self.assertRaises(NeedsManual):
            _select_video_items([{"id": "1", "name": "video.mkv"}], parsed)

    def test_all_stored_raises_already_stored(self):
        from reelay.core.contracts import AlreadyStored
        with self.assertRaises(AlreadyStored):
            _select_video_items(self.ITEMS, ParsedResource(media_type="tv"), stored=[[1, 8], [1, 9], [1, 10]])

    def test_extras_are_dropped_before_episode_detection(self):
        items = [*self.ITEMS[:1], {"id": "9", "name": "预告片.mp4"}, {"id": "8", "name": "Show.Sample.mkv"}]
        selected = _select_video_items(items, ParsedResource(media_type="tv"))
        self.assertEqual([item["id"] for item in selected["files"]], ["1"])

    def test_movie_drops_small_extras_and_rejects_multiple_mains(self):
        from reelay.core.contracts import NeedsManual
        movie = ParsedResource(media_type="movie")
        items = [{"id": "m", "name": "Film.2160p.mkv", "size": 20_000},
                 {"id": "f", "name": "Featurette.mkv", "size": 900},
                 {"id": "x", "name": "Bonus.mkv", "size": 1_000}]
        self.assertEqual([x["id"] for x in _select_video_items(items, movie)["files"]], ["m"])
        with self.assertRaises(NeedsManual):
            _select_video_items([{"id": "a", "name": "Film.CD1.mkv", "size": 9_000},
                                 {"id": "b", "name": "Film.CD2.mkv", "size": 8_000}], movie)

    def test_disc_named_after_its_bundled_extras_is_still_the_movie(self):
        # 原盘文件名列出附带内容：「花絮中字」
        from reelay.core.contracts import NeedsManual
        disc = {"id": "d", "name": "[Film 2018][原盘 简繁字幕 花絮中字][40.91G].iso", "size": 40 << 30}
        trailer = {"id": "t", "name": "Film 预告片.mp4", "size": 80 << 20}
        movie = ParsedResource(media_type="movie")
        self.assertEqual([x["id"] for x in _select_video_items([disc, trailer], movie)["files"]], ["d"])
        with self.assertRaises(NeedsManual):  # 只有预告片
            _select_video_items([trailer], movie)
        with self.assertRaises(NeedsManual):  # 剧集分享里只有花絮时仍转人工
            _select_video_items([{**disc, "name": "Show 花絮 E01.mkv"}], ParsedResource(media_type="tv"))

    MULTI = [{"id": "11", "name": "Show.S01E01.mkv"}, {"id": "12", "name": "Show.S01E02.mkv"},
             {"id": "21", "name": "Show.S02E01.mkv"}, {"id": "22", "name": "Show.S02E02.mkv"}]

    def test_multi_season_share_is_received_season_by_season(self):
        selected = _select_video_items(self.MULTI, ParsedResource(media_type="tv"), stored=[[1, 1]])
        self.assertEqual([(x["id"], x["season"], x["episode"]) for x in selected["files"]],
                         [("12", 1, 2), ("21", 2, 1), ("22", 2, 2)])
        self.assertEqual((selected["season"], selected["episodes"], selected["pairs"]),
                         (None, [1, 2], [[1, 2], [2, 1], [2, 2]]))

    def test_message_season_still_restricts_multi_season_share(self):
        selected = _select_video_items(self.MULTI, ParsedResource(media_type="tv", season=2))
        self.assertEqual(([x["id"] for x in selected["files"]], selected["season"], selected["pairs"]),
                         (["21", "22"], 2, [[2, 1], [2, 2]]))

    def test_ambiguous_multi_season_shares_still_stop_before_transfer(self):
        from reelay.core.contracts import AlreadyStored, NeedsManual
        cases = {
            "有文件名不带季号": [*self.MULTI, {"id": "9", "name": "Show.E03.mkv"}],
            "第 2 季第 1 集有多个文件": [*self.MULTI, {"id": "23", "name": "Show.S02E01.1080p.mkv"}],
        }
        for reason, items in cases.items():
            with self.subTest(reason), self.assertRaisesRegex(NeedsManual, reason):
                _select_video_items(items, ParsedResource(media_type="tv"))
        with self.assertRaisesRegex(NeedsManual, "消息只写了集数"):
            _select_video_items(self.MULTI, ParsedResource(media_type="tv", episodes=[1]), scope="message")
        with self.assertRaisesRegex(AlreadyStored, "2 季共 4 集"):
            _select_video_items(self.MULTI, ParsedResource(media_type="tv"), stored=[[1, 1], [1, 2], [2, 1], [2, 2]])


class TestDirectoryReads(unittest.TestCase):
    def test_long_id_preserved_and_pagination(self):
        client=Mock()
        client.fs_files.return_value={'state':True,'count':3,'path':[{'cid':'0','name':'根目录'}],'data':[
            {'cid':'9876543210987654301','n':'影视','pid':'0'},
            {'fid':'999','cid':'0','n':'file.txt'}]}
        result=browse_dirs(SimpleNamespace(has_oauth=False,cookie_client=lambda:client),'0',0,2)
        self.assertEqual(result['items'][0]['id'],'9876543210987654301')
        self.assertEqual(result['path'][-1], {'id': '0', 'name': '根目录'})
        self.assertEqual(result['next_offset'],2)
        self.assertEqual(len(result['items']),1)

    def test_open_list_response(self):
        client=Mock()
        client.fs_files_open.return_value={'state':True,'count':1,'path':[{'file_id':'9876543210987654301','file_name':'电影'}],'data':[{'file_id':'9876543210987654301','file_name':'电影','file_category':'0','parent_id':'0'}]}
        result=browse_dirs(SimpleNamespace(has_oauth=True,open_client=lambda:client),'9876543210987654301')
        self.assertEqual(result['items'][0]['id'],'9876543210987654301')
        self.assertEqual(result['path'][-1]['name'],'电影')
        self.assertEqual(result['via'],'oauth')

    def test_open_list_uses_short_field_names(self):
        # 开放接口实际返回的字段：fid / fn / fc（0 目录、1 文件）/ pid；path 是 cid / name
        client=Mock()
        client.fs_files_open.return_value={'state':True,'count':3,'path':[{'cid':'0','name':'根目录'},{'cid':'42','name':'电影'}],'data':[
            {'fid':'101','fn':'国产电影','fc':'0','pid':'42'},{'fid':'102','fn':'欧美电影','fc':'0','pid':'42'},
            {'fid':'103','fn':'a.mkv','fc':'1','pid':'42'}]}
        result=browse_dirs(SimpleNamespace(has_oauth=True,open_client=lambda:client),'42')
        self.assertEqual([(i['id'],i['name'],i['pid']) for i in result['items']],[('101','国产电影','42'),('102','欧美电影','42')])
        self.assertEqual(result['path'],[{'id':'0','name':'根目录'},{'id':'42','name':'电影'}])

    def test_expired_credentials_and_missing_directory(self):
        client=Mock()
        credentials=SimpleNamespace(has_oauth=False,cookie_client=lambda:client)
        client.fs_files.return_value={'state':False,'errno':99,'error':'请重新登录'}
        with self.assertRaises(StorageBrowseError) as caught:
            browse_dirs(credentials,'0')
        self.assertEqual(caught.exception.code,'credentials_expired')
        client.fs_files.return_value={'state':True,'data':[],'path':[{'cid':'0'}]}
        with self.assertRaises(StorageBrowseError) as caught:
            browse_dirs(credentials,'12')
        self.assertEqual(caught.exception.code,'directory_missing')

    def test_invalid_templates_rejected(self):
        for template in [
            '../{{ title }}{{ fileExt }}', '{{ title.__class__ }}/{{ fileExt }}',
            '{{ title }}/{{ season|pad2 }}.{{ missing }}', '/{{ title }}{{ fileExt }}',
            '{missing}/{ext}', '{{ title }}.{{ ext }}',  # 旧变量名不再支持
        ]:
            with self.assertRaises(ValueError):
                normalize_config({'plugins': {'organizer': {'naming': {'tv': template}}}})


if __name__ == "__main__":
    unittest.main()
