"""115 转存：独占暂存目录、持久化清单、完整读回核验。

prepare 只创建暂存目录并枚举分享；save 只发送一次转存请求；reconcile
只读核验。调用方必须先持久化清单和 receiving 状态，才能调用 save。

鉴权分工（混合鉴权）：
- 分享枚举（P115ShareFileSystem）与 share_receive 转存、列目录/读属性：cookie；
- 建暂存目录：走凭证中心 ``CredentialStore.mkdir``（优先 OAuth open API，
  失败回退 cookie webapi）。
"""
from __future__ import annotations

import os
from typing import Any

from ..core.auth115 import get_store
from ..core.contracts import AlreadyStored, NeedsManual, PipelineContext, TransferUncertain
from ..core.io import run_blocking
from ..core.registry import register
from ..core.media import episode_of, is_extra, summarize_pairs

VIDEO_EXT = {'.mp4', '.mkv', '.ts', '.avi', '.mov', '.flv', '.wmv', '.webm',
             '.m2ts', '.mpg', '.mpeg', '.rmvb', '.iso'}


def _is_video(name: str) -> bool:
    return any((name or '').lower().endswith(ext) for ext in VIDEO_EXT)


# 电影分享中小于最大文件该比例的视频视为花絮/样片。
MOVIE_EXTRA_RATIO = 0.15


def _select_video_items(video_items: list[dict], parsed, *, stored=(), scope: str = 'share') -> dict:
    """确定本次转存的文件。分享清单是集数的事实来源，消息/LLM 给出的集数只作提示。

    返回 ``{'files', 'episodes', 'season', 'pairs'}``：每个文件带自己的季与集，``pairs`` 为
    本次转存的 ``[季, 集]``。消息指定了季时只取该季；没指定而分享跨多季时按文件名中的季号逐季
    转存，此时 ``season`` 为 None、``episodes`` 为各季集号的并集。无法确定时抛 NeedsManual（转存前
    停止），内容全部已入库时抛 AlreadyStored。``stored`` 为已入库的 ``[季, 集]``。
    """
    videos = [x for x in video_items if not is_extra(x.get('name', ''))]
    if not videos:
        raise NeedsManual('分享内未找到正片视频文件')
    episodic = parsed.media_type in ('tv', 'anime') or bool(parsed.episodes)
    if not episodic:
        sizes = [x.get('size') for x in videos]
        if all(type(size) is int and size > 0 for size in sizes):
            largest = max(sizes)
            videos = [x for x in videos if x['size'] >= largest * MOVIE_EXTRA_RATIO]
        if len(videos) > 1:
            names = '、'.join(x.get('name', '') for x in videos[:5])
            raise NeedsManual(f'电影分享包含 {len(videos)} 个正片候选，需人工选择：{names}')
        return {'files': videos, 'episodes': [], 'season': None}

    detected = [(x, *episode_of(x.get('name', ''))) for x in videos]
    unknown = [x.get('name', '') for x, _, ep in detected if ep is None]
    if unknown:
        raise NeedsManual(f'无法从文件名识别集号：{"、".join(unknown[:5])}')
    marked = {s for _, s, _ in detected if s is not None}
    if parsed.season is not None or len(marked) <= 1:
        # 单季：消息指定的季，或分享里唯一的季；文件名不带季号的视为该季
        season = parsed.season if parsed.season is not None else (marked.pop() if marked else 1)
        picked = [(item, season, ep) for item, s, ep in detected if s in (None, season)]
    else:
        # 跨多季的合集：按文件名中的季号逐季转存；不带季号的文件无法判断属于哪一季
        bare = [item.get('name', '') for item, s, _ in detected if s is None]
        if bare:
            raise NeedsManual(f'分享包含多季 {sorted(marked)}，但有文件名不带季号：{"、".join(bare[:5])}')
        if scope == 'message' and parsed.episodes:
            raise NeedsManual(f'分享包含多季 {sorted(marked)}，消息只写了集数，需先指定季号')
        season, picked = None, detected
    by_pair: dict[tuple[int, int], dict] = {}
    for item, s, ep in picked:
        if (s, ep) in by_pair:
            raise NeedsManual(f'分享内第 {ep} 集有多个文件，需人工选择' if season is not None
                              else f'分享内第 {s} 季第 {ep} 集有多个文件，需人工选择')
        by_pair[(s, ep)] = item
    if not by_pair:
        raise NeedsManual(f'分享内没有第 {season} 季的视频')
    if scope == 'message' and parsed.episodes:
        missing = set(parsed.episodes) - {ep for _, ep in by_pair}
        if missing:
            raise NeedsManual(f'分享内未找到消息声明的集数 {sorted(missing)}')
        by_pair = {(season, ep): by_pair[(season, ep)] for ep in parsed.episodes}
    wanted = sorted(set(by_pair) - {(s, ep) for s, ep in stored})
    if not wanted:
        raise AlreadyStored(f'分享内第 {season} 季的 {len(by_pair)} 集均已入库' if season is not None
                            else f'分享内 {len({s for s, _ in by_pair})} 季共 {len(by_pair)} 集均已入库')
    files = [{**by_pair[pair], 'season': pair[0], 'episode': pair[1]} for pair in wanted]
    season, episodes = summarize_pairs(wanted)
    return {'files': files, 'episodes': episodes, 'season': season, 'pairs': [list(pair) for pair in wanted]}


# 只读描述分享时最多列出的目录数：逐集一个目录的大合集要几百次请求，容易触发 115 风控。
DESCRIBE_MAX_DIRS = 20
# 115 表示分享本身不可用（而非临时网络错误）的提示语。
_SHARE_GONE = ('访问码', '提取码', '取消', '不存在', '过期', '失效', '删除', '违规', '禁止')


def describe_share(share_fs, parsed, *, max_dirs: int = DESCRIBE_MAX_DIRS) -> dict[str, Any]:
    """只读描述分享内容，供待审批展示：视频数、总大小、季与集，以及转存时会转人工的原因。

    status：``ok`` 已确定季与集；``issue`` 能读取但转存时需人工（note 为原因）；``unavailable``
    分享失效或访问码错误；``incomplete`` 目录太多未读完。临时错误直接抛出，由调用方稍后重试。
    与转存使用同一套选集规则，但不改识别结果：转存时仍以那时的分享清单为准。
    """
    videos: list[dict] = []
    queue, listed = [(0, 0)], 0
    while queue:
        cid, depth = queue.pop(0)
        if listed >= max_dirs:
            return {'status': 'incomplete', 'videos': len(videos),
                    'note': f'分享内容较多，只读取了前 {max_dirs} 个目录'}
        try:
            entries = list(share_fs.iterdir(cid))
        except Exception as exc:
            detail = next((a.get('error') or a.get('message') for a in getattr(exc, 'args', ())
                           if isinstance(a, dict)), None) or str(exc)
            if listed == 0 and any(word in detail for word in _SHARE_GONE):
                return {'status': 'unavailable', 'note': f'分享无法访问：{detail[:60]}'}
            raise
        listed += 1
        for item in entries:
            if item.get('is_directory') or item.get('is_dir'):
                if depth >= 3:
                    return {'status': 'issue', 'videos': len(videos), 'note': '分享目录过深，不能完整核验视频清单'}
                queue.append((item['id'], depth + 1))
            elif _is_video(item.get('name', '')):
                videos.append(_file_record(item))
    result: dict[str, Any] = {'status': 'ok', 'videos': len(videos),
                              'size': sum(x['size'] for x in videos if type(x.get('size')) is int)}
    try:
        selection = _select_video_items(videos, parsed, scope='share')
        result.update(season=selection['season'], episodes=selection['episodes'], pairs=selection['pairs'])
    except NeedsManual as exc:
        result.update(status='issue', note=str(exc))
    return result


def _file_record(item: dict) -> dict:
    return {'id': str(item['id']), 'name': item.get('name', ''),
            'size': item.get('size'), 'sha1': item.get('sha1') or None,
            'is_dir': bool(item.get('is_directory') or item.get('is_dir'))}


def _verified_files(expected: list[dict], actual: list[dict]) -> list[dict]:
    """独占目录中必须恰好出现清单全部文件，数量、名称、大小和可用 SHA1 一致。"""
    if not expected or len(expected) != len(actual) or any(x['is_dir'] for x in actual):
        raise TransferUncertain('暂存目录的文件数量或类型与转存清单不一致，请核验')
    if len({x['name'] for x in expected}) != len(expected):
        raise TransferUncertain('转存清单存在重名文件，无法确定对应关系')
    by_name = {x['name']: x for x in actual}
    if len(by_name) != len(actual):
        raise TransferUncertain('暂存目录存在重名文件，请核验')
    for source in expected:
        landed = by_name.get(source['name'])
        if not landed or landed['size'] != source['size']:
            raise TransferUncertain('落地文件名称或大小与清单不一致，请核验')
        if source.get('sha1') and source['sha1'].upper() != (landed.get('sha1') or '').upper():
            raise TransferUncertain('落地文件 SHA1 与清单不一致，请核验')
    return [by_name[x['name']] for x in expected]


@register(kind='provider', ptype='storage')
class P115Storage:
    name = 'p115'

    def __init__(self) -> None:
        # 读写都经过凭证中心：分享枚举/转存只能走 cookie，建目录走混合鉴权。
        # 测试替换 ``_auth`` 为伪凭证中心，与生产走同一条代码路径。
        self._dry_run = os.getenv('REELAY_DRY_RUN', '').lower() in {'1', 'true', 'yes'}
        self._auth = get_store()

    def _cookie_client(self):
        return None if self._dry_run else self._auth.cookie_client()

    def dir_path(self, cid: str) -> str | None:
        """目录在网盘中的完整路径（如 /测试/剧集），供管理台显示真实去向；模拟模式返回 None。"""
        if self._dry_run:
            return None
        from p115client import P115FileSystem
        return P115FileSystem(self._cookie_client()).get_path(int(cid))

    @property
    def live(self) -> bool:
        return not self._dry_run and self._auth.has_cookie

    def _require_client(self) -> None:
        if self._cookie_client() is None:
            raise RuntimeError('115 客户端不可用：分享枚举/转存需要配置 cookie（写操作可走 OAuth）')

    async def prepare(self, ctx: PipelineContext) -> dict[str, Any]:
        if self._dry_run:
            p = ctx.parsed
            episodic = p.media_type in ('tv', 'anime') or bool(p.episodes)
            names = ([f'S{p.season if p.season is not None else 1:02d}E{ep:02d}.mp4' for ep in p.episodes or [1]]
                     if episodic else ['movie.mp4'])
            listing = [{'id': f'dry-{ctx.task_id}-{i}', 'name': name, 'size': 1, 'sha1': None, 'is_dir': False}
                       for i, name in enumerate(names)]
            selection = self._select(ctx, listing)
            return {'dry_run': True, 'to_cid': int(ctx.save_dir_cid),
                    'staging_cid': f'dry-stage-{ctx.task_id}', **selection}
        self._require_client()
        return await run_blocking(self._prepare_sync, ctx)

    @staticmethod
    def _select(ctx: PipelineContext, listing: list[dict]) -> dict:
        return _select_video_items(listing, ctx.parsed,
                                   stored=ctx.ns('storage').get('stored_episodes') or [],
                                   scope=ctx.source_rule.get('episodes_from', 'share'))

    async def discard(self, ctx: PipelineContext) -> None:
        """去重跳过时删除尚未使用的空暂存目录；非空绝不删除。"""
        stage = (ctx.ns('storage').get('manifest') or {}).get('staging_cid')
        if self._dry_run or not isinstance(stage, int):
            return
        await run_blocking(self._discard_sync, stage)

    def _discard_sync(self, stage: int) -> None:
        from p115client import P115FileSystem
        if not list(P115FileSystem(self._cookie_client()).iterdir(stage)):
            self._auth.delete([stage])

    async def save(self, ctx: PipelineContext) -> dict[str, Any]:
        manifest = ctx.ns('storage')['manifest']
        if self._dry_run:
            return self._receipt(manifest, manifest['files'], dry_run=True)
        self._require_client()
        return await run_blocking(self._receive_sync, ctx)

    async def reconcile(self, ctx: PipelineContext) -> dict[str, Any]:
        manifest = ctx.ns('storage')['manifest']
        if self._dry_run:
            return self._receipt(manifest, manifest['files'], dry_run=True)
        self._require_client()
        return await run_blocking(self._reconcile_sync, manifest)

    def _prepare_sync(self, ctx: PipelineContext) -> dict[str, Any]:
        from p115client import P115FileSystem, P115ShareFileSystem
        p = ctx.parsed
        client = self._cookie_client()
        share_fs = P115ShareFileSystem(client, p.share_code, p.receive_code)
        selection = self._select(ctx, self._collect_share_videos(share_fs))
        files = selection['files']
        if len({x['name'] for x in files}) != len(files):
            raise NeedsManual('分享内视频文件重名，需人工选择后再转存')
        fs = P115FileSystem(client)
        parent = int(ctx.save_dir_cid)
        name = f'.reelay-{ctx.task_id}'
        matches = [x for x in fs.iterdir(parent) if x.get('name') == name]
        if matches:
            if len(matches) != 1 or not (matches[0].get('is_dir') or matches[0].get('is_directory')):
                raise RuntimeError('暂存目录名称冲突，需人工处理')
            stage = matches[0]
        else:
            stage = self._auth.mkdir(parent, name)
        return {'to_cid': parent, 'staging_cid': int(stage['id']), **selection}

    def _receive_sync(self, ctx: PipelineContext) -> dict[str, Any]:
        from p115client import P115FileSystem, P115ShareFileSystem
        client = self._cookie_client()
        manifest = ctx.ns('storage')['manifest']
        # 独占目录非空时只能核验，绝不追加一份转存。
        if list(P115FileSystem(client).iterdir(manifest['staging_cid'])):
            return self._reconcile_sync(manifest)
        p = ctx.parsed
        share_fs = P115ShareFileSystem(client, p.share_code, p.receive_code)
        # SDK check_response 检查业务状态；异常结果交给 reconcile，不能自动重发。
        recv = share_fs.receive([x['id'] for x in manifest['files']],
                                to_pid=manifest['staging_cid'])
        receipt = self._reconcile_sync(manifest)
        receipt['received'] = recv
        return receipt

    def _reconcile_sync(self, manifest: dict) -> dict[str, Any]:
        from p115client import P115FileSystem
        client = self._cookie_client()
        actual = [_file_record(x) for x in P115FileSystem(client).iterdir(manifest['staging_cid'])]
        landed = _verified_files(manifest['files'], actual)
        return self._receipt(manifest, landed)

    @staticmethod
    def _receipt(manifest: dict, landed: list[dict], **extra) -> dict[str, Any]:
        return {'to_cid': manifest['to_cid'], 'staging_cid': manifest['staging_cid'],
                'requested': len(manifest['files']), 'landed': landed,
                'saved_files': [x['name'] for x in landed], 'verified': True,
                'storage_verified': True, **extra}

    @staticmethod
    def _collect_share_videos(share_fs, root: int = 0, max_depth: int = 3) -> list[dict]:
        out = []
        def walk(cid, depth):
            for item in share_fs.iterdir(cid):
                if item.get('is_directory') or item.get('is_dir'):
                    if depth >= max_depth:
                        raise NeedsManual('分享目录过深，不能完整核验视频清单')
                    walk(item['id'], depth + 1)
                elif _is_video(item.get('name', '')):
                    out.append(_file_record(item))
        walk(root, 0)
        return out
