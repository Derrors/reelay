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
from ..core.contracts import PipelineContext, TransferUncertain
from ..core.io import run_blocking
from ..core.registry import register
from ..core.media import episode_of

VIDEO_EXT = {'.mp4', '.mkv', '.ts', '.avi', '.mov', '.flv', '.wmv', '.webm',
             '.m2ts', '.mpg', '.mpeg', '.rmvb', '.iso'}


def _is_video(name: str) -> bool:
    return any((name or '').lower().endswith(ext) for ext in VIDEO_EXT)


def _episode_of(name: str) -> int | None:
    return episode_of(name)[1]


def _select_video_items(video_items: list[dict], parsed) -> list[dict]:
    if not parsed.episodes:
        return video_items
    wanted = set(parsed.episodes)
    season = parsed.season if parsed.season is not None else 1
    matched = []
    for item in video_items:
        detected_season, ep = episode_of(item.get('name', ''))
        if ep in wanted and detected_season in (None, season):
            matched.append(item)
    missing = wanted - {episode_of(item.get('name', ''))[1] for item in matched}
    if missing:
        raise RuntimeError(f'分享内未找到待转存集数 {sorted(missing)}')
    return matched


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
        # 测试可直接注入伪 client（self._client = fake）；生产路径通过
        # _cookie_client() 从凭证中心懒加载 cookie 客户端（分享枚举/转存
        # 只能走 cookie；建目录等写操作走凭证中心的混合鉴权）。
        self._client = None
        self._init_error = None
        self._dry_run = os.getenv('REELAY_DRY_RUN', '').lower() in {'1', 'true', 'yes'}
        self._auth = get_store()

    def _cookie_client(self):
        if self._client is not None:
            return self._client
        if self._dry_run:
            return None
        return self._auth.cookie_client()

    @property
    def live(self) -> bool:
        return not self._dry_run and (self._client is not None
                                      or self._auth.has_cookie)

    def _require_client(self) -> None:
        if self._cookie_client() is None:
            raise RuntimeError('115 客户端不可用：分享枚举/转存需要配置 cookie（写操作可走 OAuth）')

    async def prepare(self, ctx: PipelineContext) -> dict[str, Any]:
        if self._dry_run:
            p = ctx.parsed
            files = [{'id': f'dry-{ctx.task_id}-{ep}', 'name': f'E{ep:02d}.mp4',
                      'size': 1, 'sha1': None, 'is_dir': False}
                     for ep in p.episodes or [1]]
            return {'dry_run': True, 'to_cid': int(ctx.save_dir_cid),
                    'staging_cid': f'dry-stage-{ctx.task_id}', 'files': files}
        self._require_client()
        return await run_blocking(self._prepare_sync, ctx)

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
        files = _select_video_items(self._collect_share_videos(share_fs), p)
        if not files:
            raise RuntimeError('分享内未找到视频文件')
        if len({x['name'] for x in files}) != len(files):
            raise RuntimeError('分享内视频文件重名，需人工选择后再转存')
        fs = P115FileSystem(client)
        parent = int(ctx.save_dir_cid)
        name = f'.reelay-{ctx.task_id}'
        matches = [x for x in fs.iterdir(parent) if x.get('name') == name]
        if matches:
            if len(matches) != 1 or not (matches[0].get('is_dir') or matches[0].get('is_directory')):
                raise RuntimeError('暂存目录名称冲突，需人工处理')
            stage = matches[0]
        else:
            stage = self._mkdir(parent, name)
        return {'to_cid': parent, 'staging_cid': int(stage['id']), 'files': files}

    def _mkdir(self, parent: int, name: str) -> dict:
        """建暂存目录。

        生产走凭证中心混合鉴权（OAuth 优先、cookie 回退）；测试注入伪
        client 时沿用伪 client 的建目录实现，保持故障注入测试可控。
        """
        if self._client is not None:
            return self._mkdir_on_fake(parent, name)
        return self._auth.mkdir(parent, name)

    def _mkdir_on_fake(self, parent: int, name: str) -> dict:
        import logging
        from p115client import P115FileSystem
        client = self._client
        try:
            resp = client.fs_mkdir({"cname": name, "pid": int(parent)})
            if isinstance(resp, dict) and resp.get("state", True) is not False:
                fid = (resp.get("file_id") or resp.get("cid")
                       or (resp.get("data") or {}).get("category_id"))
                if fid:
                    return {"id": int(fid), "name": name}
        except Exception as exc:  # noqa: BLE001
            logging.getLogger("reelay.storage").warning(
                "webapi 建目录异常，回退 app 接口：%s", exc)
        made = P115FileSystem(client).mkdir(parent, name=name)
        return {"id": int(made["id"] if isinstance(made, dict) else made),
                "name": name}

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
                        raise RuntimeError('分享目录过深，不能完整核验视频清单')
                    walk(item['id'], depth + 1)
                elif _is_video(item.get('name', '')):
                    out.append(_file_record(item))
        walk(root, 0)
        return out
