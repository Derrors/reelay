"""115 转存：独占暂存目录、持久化清单、完整读回核验。

prepare 只创建暂存目录并枚举分享；save 只发送一次转存请求；reconcile
只读核验。调用方必须先持久化清单和 receiving 状态，才能调用 save。

除转存外，存储 Provider 还给其他扩展提供：``library()`` 整理用的网盘接口（建目录、改名、移动、删除、
读属性）、``inspect_share()`` 只读查询分享、``browse()`` 浏览目录（管理台选目录）、``ready()`` 凭证是否就绪。
选集规则（``ShareStorage``）与 115 无关，模拟存储（``providers/dryrun.py``）共用它。

鉴权分工（混合鉴权）：
- 分享枚举（P115ShareFileSystem）与 share_receive 转存、列目录/读属性：cookie；
- 建目录、改名、移动、删除：走凭证中心（优先 OAuth open API，确定没有发出时回退 cookie webapi）。
"""
from __future__ import annotations

import json
import re
import time
from typing import Any
from urllib.error import HTTPError

from ..core import ai, assist, runtime
from ..core.auth115 import AUTH_ERRNOS, OPEN_AUTH_CODES, get_store, is_auth_error
from ..core.contracts import (AlreadyStored, NeedsManual, PipelineContext, ShareUnavailable, StorageBusy,
                              TransferRejected, TransferUncertain)
from ..core.io import run_blocking
from ..core.registry import register
from ..core.media import episode_of, is_extra, is_video, resolution_label, resolution_rank, summarize_pairs


# 电影分享中小于最大文件该比例的视频视为花絮/样片。
MOVIE_EXTRA_RATIO = 0.15
# 预告、样片、花絮不会有这么大：电影分享按文件名全像花絮时，至少这么大的仍当作正片候选。
MOVIE_MIN_SIZE = 1 << 30
# 115 接受转存后在后台陆续落地文件（实测 23 集的分享 5 秒后一个都没到），期间目录列表可能不全或不稳定：
# 核验不通过时退避重查，最多等这么久，仍不通过交给只读核验与人工。
LANDING_WAIT = 180.0
_sleep = time.sleep


def _response_text(response: Any, limit: int = 300) -> str:
    """115 接口回应的原文（JSON，过长时截断），写进任务轨迹。"""
    text = json.dumps(response, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[:limit] + '…'


def _await_landing(verify):
    deadline = time.monotonic() + LANDING_WAIT
    delay = 2.0
    while True:
        try:
            return verify()
        except TransferUncertain:
            if time.monotonic() + delay > deadline:
                raise
            _sleep(delay)
            delay = min(delay * 1.5, 20.0)
# 分段电影（CD1/CD2、上下部）：只能整体转存，不能只选一段。
_MULTIPART_RE = re.compile(r'(?i)(?<![a-z])(?:cd|disc|disk|part|pt)[ ._\-]*\d|[上下]部|[上下]集')


def _movie_choice_problem(videos: list[dict], chosen: str, parsed) -> str | None:
    if any(_MULTIPART_RE.search(x.get('name', '')) for x in videos):
        return '电影分享疑似分段（CD1/CD2、上下部），不能只选一个文件'
    rank = resolution_rank(chosen)
    if rank < max(resolution_rank(x.get('name', '')) for x in videos) and rank != resolution_rank(parsed.resolution):
        return f'方案选择的「{chosen}」不是清晰度最高的版本，也不是消息所写的清晰度'
    return None


def _select_video_items(video_items: list[dict], parsed, *, stored=(), scope: str = 'share',
                        hints: dict | None = None) -> dict:
    """确定本次转存的文件。分享清单是集数的事实来源，消息/LLM 给出的集数只作提示。

    返回 ``{'files', 'episodes', 'season', 'pairs'}``：每个文件带自己的季与集，``pairs`` 为
    本次转存的 ``[季, 集]``。消息指定了季时只取该季；没指定而分享跨多季时按文件名中的季号逐季
    转存，此时 ``season`` 为 None、``episodes`` 为各季集号的并集。无法确定时抛 NeedsManual（转存前
    停止），内容全部已入库时抛 AlreadyStored。``stored`` 为已入库的 ``[季, 集]``。

    ``hints`` 是 AI 在转人工前给出的方案（见 ``core/assist.py``），按同一套规则校验：只能补上文件名
    识别不出的季集、在跨多季又不能逐季转存时按消息选一季、丢掉同一季同一集清晰度不更高的多余版本、
    在电影多个正片候选中选清晰度最高（或与消息一致）的一个；与文件名冲突或校验不过照样转人工。
    """
    hints = hints or {}
    episodic = parsed.media_type in ('tv', 'anime') or bool(parsed.episodes)
    videos = [x for x in video_items if not is_extra(x.get('name', ''))]
    if not videos and not episodic:
        # 原盘、合集的文件名常列出附带内容（如「花絮中字」）：分享里总有正片，按文件名全像花絮时改按大小判断
        videos = [x for x in video_items if type(x.get('size')) is int and x['size'] >= MOVIE_MIN_SIZE]
    if not videos:
        raise NeedsManual('分享内未找到正片视频文件')
    names = {x.get('name', '') for x in videos}
    for name in [*hints.get('episodes', {}), *hints.get('drop', []), *filter(None, [hints.get('movie_file')])]:
        if name not in names:
            raise NeedsManual(f'方案引用了分享里没有的正片文件：{name}')
    if not episodic:
        sizes = [x.get('size') for x in videos]
        if all(type(size) is int and size > 0 for size in sizes):
            largest = max(sizes)
            videos = [x for x in videos if x['size'] >= largest * MOVIE_EXTRA_RATIO]
        if len(videos) > 1:
            chosen = hints.get('movie_file')
            if not chosen:
                names = '、'.join(x.get('name', '') for x in videos[:5])
                raise NeedsManual(f'电影分享包含 {len(videos)} 个正片候选，需人工选择：{names}')
            problem = _movie_choice_problem(videos, chosen, parsed)
            if problem:
                raise NeedsManual(problem)
            videos = [x for x in videos if x.get('name') == chosen]
            if not videos:
                raise NeedsManual(f'方案选择的「{chosen}」不是正片候选')
        return {'files': videos, 'episodes': [], 'season': None}

    detected = []
    for x in videos:
        name = x.get('name', '')
        file_season, ep = episode_of(name)
        if name in hints.get('episodes', {}):
            hint_season, hint_ep = hints['episodes'][name]
            if ep is not None and ep != hint_ep:
                raise NeedsManual(f'方案的集号与文件名不一致：{name}')
            if None not in (file_season, hint_season) and file_season != hint_season:
                raise NeedsManual(f'方案的季号与文件名不一致：{name}')
            file_season = file_season if file_season is not None else hint_season
            ep = hint_ep
        detected.append((x, file_season, ep))
    unknown = [x.get('name', '') for x, _, ep in detected if ep is None]
    if unknown:
        raise NeedsManual(f'无法从文件名识别集号：{"、".join(unknown[:5])}')
    marked = {s for _, s, _ in detected if s is not None}
    chosen = hints.get('season') if hints.get('season') in marked else None
    if parsed.season is not None or len(marked) <= 1 or chosen is not None:
        # 单季：消息指定的季、分享里唯一的季，或 AI 按消息选出的那一季；文件名不带季号的视为该季
        season = parsed.season if parsed.season is not None else (
            chosen if chosen is not None else (marked.pop() if marked else 1))
        picked = [(item, season, ep) for item, s, ep in detected if s in (None, season)]
    else:
        # 跨多季的合集：按文件名中的季号逐季转存；不带季号的文件无法判断属于哪一季
        bare = [item.get('name', '') for item, s, _ in detected if s is None]
        if bare:
            raise NeedsManual(f'分享包含多季 {sorted(marked)}，但有文件名不带季号：{"、".join(bare[:5])}')
        if scope == 'message' and parsed.episodes:
            raise NeedsManual(f'分享包含多季 {sorted(marked)}，消息只写了集数，需先指定季号')
        season, picked = None, detected
    groups: dict[tuple[int, int], list[dict]] = {}
    for item, s, ep in picked:
        groups.setdefault((s, ep), []).append(item)
    dropped = set(hints.get('drop', []))
    by_pair: dict[tuple[int, int], dict] = {}
    for (s, ep), items in groups.items():
        label = f'第 {ep} 集' if season is not None else f'第 {s} 季第 {ep} 集'
        kept = [x for x in items if x.get('name') not in dropped]
        if not kept:
            raise NeedsManual(f'方案丢弃了{label}的全部文件')
        if len(kept) > 1:
            raise NeedsManual(f'分享内{label}有多个文件，需人工选择')
        rank = resolution_rank(kept[0].get('name'))
        if any(resolution_rank(x.get('name')) > rank for x in items if x is not kept[0]):
            raise NeedsManual(f'方案保留的{label}版本清晰度低于被丢弃的版本')
        by_pair[(s, ep)] = kept[0]
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
            elif is_video(item.get('name', '')):
                videos.append(_file_record(item))
    result: dict[str, Any] = {'status': 'ok', 'videos': len(videos),
                              'size': sum(x['size'] for x in videos if type(x.get('size')) is int)}
    try:
        selection = _select_video_items(videos, parsed, scope='share')
        result.update(season=selection['season'], episodes=selection['episodes'], pairs=selection['pairs'])
    except NeedsManual as exc:
        result.update(status='issue', note=str(exc))
    return result


def business_error(exc: Exception) -> str | None:
    """115 明确拒绝请求时（响应 state 为 false）返回错误信息；网络错误、超时等结果不明的返回 None。"""
    for arg in getattr(exc, 'args', ()):
        if isinstance(arg, dict) and arg.get('state') is False:
            return str(arg.get('error') or arg.get('msg') or f"errno {arg.get('errno')}")[:100]
    return None


def share_problem(client, share_code: str, receive_code: str | None) -> str | None:
    """分享不可用时返回 115 给出的原因（如「链接已过期」），可用返回 None。只读，一次请求。

    列出分享内容不能说明分享可用：过期的分享仍能列出文件，转存时才被拒绝。
    """
    return share_check(client, share_code, receive_code)[0]


def share_check(client, share_code: str, receive_code: str | None) -> tuple[str | None, list[str]]:
    """（不可用的原因或 None, 分享根目录的文件名）。同一次请求顺带列出根目录前 20 项，不多调 115。"""
    try:
        resp = client.share_snap({'share_code': share_code, 'receive_code': receive_code or '', 'cid': 0, 'limit': 20})
    except Exception as exc:
        reason = business_error(exc)
        if reason is None:
            raise
        return reason, []
    if not resp.get('state'):
        return str(resp.get('error') or resp.get('msg') or '分享无法访问')[:100], []
    data = resp.get('data') or {}
    info = data.get('shareinfo') or {}
    if info.get('forbid_reason'):
        return str(info['forbid_reason'])[:100], []
    expire = str(info.get('expire_time') or '')
    if expire.isdigit() and 0 < int(expire) < time.time():
        return '链接已过期', []
    names = [str(item.get('n') or item.get('name') or '') for item in data.get('list') or [] if isinstance(item, dict)]
    return None, [name for name in names if name]


def _file_record(item: dict) -> dict:
    return {'id': str(item['id']), 'name': item.get('name', ''),
            'size': item.get('size'), 'sha1': item.get('sha1') or None,
            'is_dir': bool(item.get('is_directory') or item.get('is_dir'))}


def _landing_progress(expected: list[dict], actual: list[dict]) -> dict | None:
    """暂存目录里的文件都能和清单对上（名称、大小、可用 SHA1），只是少了几个时，返回
    ``{"landed": 已到的文件（按清单顺序）, "missing": 缺少的文件名}``；有对不上的文件或目录时返回 None。"""
    by_name = {x['name']: x for x in expected}
    if not actual or len(by_name) != len(expected) or any(x['is_dir'] for x in actual) \
            or len({x['name'] for x in actual}) != len(actual):
        return None
    for landed in actual:
        source = by_name.get(landed['name'])
        if not source or source['size'] != landed['size'] or (
                source.get('sha1') and source['sha1'].upper() != (landed.get('sha1') or '').upper()):
            return None
    arrived = {x['name']: x for x in actual}
    return {'landed': [arrived[x['name']] for x in expected if x['name'] in arrived],
            'missing': [x['name'] for x in expected if x['name'] not in arrived]}


def _verified_files(expected: list[dict], actual: list[dict]) -> list[dict]:
    """独占目录中必须恰好出现清单全部文件，数量、名称、大小和可用 SHA1 一致。

    只到了一部分（到的都对得上）时，异常带上 ``partial``（已到几个、缺哪些），管理台据此提供
    「按已到的文件继续」。
    """
    progress = _landing_progress(expected, actual) if len(actual) < len(expected) else None
    if progress and progress['missing']:
        missing = progress['missing']
        error = TransferUncertain(f'暂存目录已到 {len(actual)}/{len(expected)} 个文件，还差 {len(missing)} 个：'
                                  f'{"、".join(missing[:2])}{" 等" if len(missing) > 2 else ""}，请核验')
        error.partial = {'landed': len(actual), 'expected': len(expected), 'missing': missing}
        raise error
    if not expected or len(expected) != len(actual) or any(x['is_dir'] for x in actual):
        dirs = sum(x['is_dir'] for x in actual)
        raise TransferUncertain(f'暂存目录有 {len(actual)} 项{f"（含 {dirs} 个目录）" if dirs else ""}，'
                                f'与转存清单的 {len(expected)} 个文件不一致，请核验')
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


class ShareStorage(ai.UsesAI):  # 转人工前的 AI 兜底
    """按分享清单选定转存文件（与具体网盘无关）：规则无法确定时先让 AI 给方案，方案经同一套规则校验。"""

    @staticmethod
    def _select(ctx: PipelineContext, listing: list[dict], hints: dict | None = None) -> dict:
        return _select_video_items(listing, ctx.parsed,
                                   stored=ctx.ns('storage').get('stored_episodes') or [],
                                   scope=ctx.source_rule.get('episodes_from', 'share'), hints=hints)

    async def select(self, ctx: PipelineContext, listing: list[dict]) -> dict:
        """按规则确定转存文件；规则无法确定时先让 AI 给出方案，方案经同一套规则校验，不过再转人工。"""
        try:
            return self._select(ctx, listing)
        except NeedsManual as exc:
            if self._client is None or not runtime.get('ai.assist', True):
                raise
            problem = str(exc)
        record = ctx.ns('assist')
        record.clear()
        record['problem'] = problem
        try:
            proposal = await assist.propose_selection(self._client, self._model, ctx, listing, problem)
        except ai.AIError as err:
            record['error'] = err.category
            ctx.log(f'AI 未能给出转存方案（{err.category}），转人工')
            raise NeedsManual(problem) from None
        record['proposal'] = assist.describe(proposal)
        try:
            selection = self._select(ctx, listing, proposal)
        except NeedsManual as again:
            record.update(accepted=False, rejected=str(again))
            ctx.log(f'AI 方案未通过规则校验：{again}')
            raise NeedsManual(f'{problem}（AI 方案未通过校验：{again}）') from None
        record['accepted'] = True
        ctx.log(f'规则无法确定转存文件（{problem}），AI 方案已通过校验：{proposal["reason"]}')
        return selection


    @staticmethod
    def _receipt(manifest: dict, landed: list[dict], **extra) -> dict[str, Any]:
        return {'to_cid': manifest['to_cid'], 'staging_cid': manifest['staging_cid'],
                'requested': len(manifest['files']), 'landed': landed,
                'saved_files': [x['name'] for x in landed], 'verified': True,
                'storage_verified': True, **extra}


def drive_entry(item: dict) -> dict[str, Any]:
    """网盘目录项 → 整理用的统一结构：id（int）、name、is_dir、size、sha1、parent_id。"""
    return {'id': int(item['id']), 'name': item.get('name', ''),
            'is_dir': bool(item.get('is_directory') or item.get('is_dir')), 'size': item.get('size'),
            'sha1': item.get('sha1') or None, 'parent_id': int(item.get('parent_id') or 0)}


class P115Drive:
    """整理用的 115 网盘接口：读走 cookie 客户端，写走凭证中心的混合鉴权。所有 ID 都是 int。"""

    def __init__(self, client, auth) -> None:
        from p115client import P115FileSystem
        self._fs = P115FileSystem(client)
        self._auth = auth

    def iterdir(self, cid: int) -> list[dict]:
        return [drive_entry(x) for x in self._fs.iterdir(int(cid))]

    def get_attr(self, fid: int) -> dict:
        return drive_entry(self._fs.get_attr(int(fid), refresh=True))

    def mkdir(self, parent: int, name: str) -> int:
        return int(self._auth.mkdir(int(parent), name)['id'])

    def rename(self, fid: int, name: str) -> None:
        self._auth.rename(int(fid), name)

    def move(self, fid: int, cid: int) -> None:
        self._auth.move(int(fid), int(cid))

    def delete(self, ids: list[int]) -> None:
        self._auth.delete([int(x) for x in ids])


class StorageBrowseError(RuntimeError):
    """浏览目录失败：code 给管理台区分原因，status 是 HTTP 状态码。不把错误伪装成空目录。"""

    def __init__(self, code: str, message: str, status: int = 503):
        super().__init__(message)
        self.code, self.status = code, status


@register(kind='provider', ptype='storage')
class P115Storage(ShareStorage):
    name = 'p115'

    def __init__(self) -> None:
        # 读写都经过凭证中心：分享枚举/转存只能走 cookie，建目录走混合鉴权。
        # 测试替换 ``_auth`` 为伪凭证中心，与生产走同一条代码路径。
        self._auth = get_store()

    def _cookie_client(self):
        return self._auth.cookie_client()

    def ready(self) -> bool:
        """转存与读取分享需要的 cookie 已配置。"""
        return self._auth.has_cookie

    def dir_path(self, cid: str) -> str | None:
        """目录在网盘中的完整路径（如 /测试/剧集），供管理台显示真实去向。"""
        from p115client import P115FileSystem
        return P115FileSystem(self._cookie_client()).get_path(int(cid))

    def library(self) -> P115Drive:
        """整理用的网盘接口；没有 cookie 时抛 RuntimeError（读核验需要 cookie，写操作可走 OAuth）。"""
        client = self._cookie_client()
        if client is None:
            raise RuntimeError('整理需要 115 cookie：读核验走 cookie，写操作可走 OAuth')
        return P115Drive(client, self._auth)

    def inspect_share(self, parsed, *, describe: bool) -> dict[str, Any]:
        """只读查询分享：是否过期、取消或违规；describe 时再列出分享，按文件名得到季、集与大小（见 describe_share）。"""
        from p115client import P115ShareFileSystem
        client = self._cookie_client()
        problem, names = share_check(client, parsed.share_code, parsed.receive_code)
        if problem:
            return {'status': 'unavailable', 'note': f'分享无法访问：{problem}'}
        # 消息没写分辨率时，卡片按分享根目录里视频的文件名补（同一次请求取到，不多调 115）
        resolution = next(filter(None, (resolution_label(n) for n in names if is_video(n))), None)
        extra = {'resolution': resolution} if resolution else {}
        if not describe:
            return {'status': 'available', **extra}
        return {**describe_share(P115ShareFileSystem(client, parsed.share_code, parsed.receive_code), parsed), **extra}

    def browse(self, cid: str, offset: int = 0, limit: int = 100) -> dict[str, Any]:
        """管理台选目录：列出 cid 下的子目录（分页）与面包屑。"""
        return browse_dirs(self._auth, cid, offset, limit)

    def _require_client(self) -> None:
        if self._cookie_client() is None:
            raise RuntimeError('115 客户端不可用：分享枚举/转存需要配置 cookie（写操作可走 OAuth）')

    async def prepare(self, ctx: PipelineContext) -> dict[str, Any]:
        self._require_client()
        listing = await run_blocking(self._list_share_sync, ctx)
        selection = await self.select(ctx, listing)
        return await run_blocking(self._stage_sync, ctx, selection)

    async def discard(self, ctx: PipelineContext) -> None:
        """去重跳过时删除尚未使用的空暂存目录；非空绝不删除。"""
        stage = (ctx.ns('storage').get('manifest') or {}).get('staging_cid')
        if not isinstance(stage, int):
            return
        await run_blocking(self._discard_sync, stage)

    def _discard_sync(self, stage: int) -> None:
        from p115client import P115FileSystem
        if not list(P115FileSystem(self._cookie_client()).iterdir(stage)):
            self._auth.delete([stage])

    async def reconcile_partial(self, ctx: PipelineContext) -> dict[str, Any]:
        """人工选了「按已到的文件继续」：只读核验，暂存目录里的文件都与清单对得上就按这些文件出回执，
        回执的 missing 是没到的文件名。不发任何写请求，没到的文件不会补转存。"""
        manifest = ctx.ns('storage')['manifest']
        self._require_client()
        return await run_blocking(self._reconcile_partial_sync, manifest)

    def _reconcile_partial_sync(self, manifest: dict) -> dict[str, Any]:
        from p115client import P115FileSystem
        actual = [_file_record(x) for x in P115FileSystem(self._cookie_client()).iterdir(manifest['staging_cid'])]
        if len(actual) == len(manifest['files']):
            return {**self._receipt(manifest, _verified_files(manifest['files'], actual)), 'missing': []}
        progress = _landing_progress(manifest['files'], actual)
        if not progress:
            raise TransferUncertain('暂存目录里的文件与转存清单对不上，不能按已到的文件继续，请核验')
        return {**self._receipt(manifest, progress['landed']), 'missing': progress['missing']}

    async def abandon(self, ctx: PipelineContext) -> None:
        """人工选了「放弃这次转存」：把独占暂存目录（连同已到的文件）移进 115 回收站，并读回确认已不在。
        删除可以重复执行：结果不明时再点一次即可。"""
        manifest = ctx.ns('storage')['manifest']
        self._require_client()
        await run_blocking(self._abandon_sync, int(manifest['staging_cid']), int(manifest['to_cid']))

    def _abandon_sync(self, stage: int, parent: int) -> None:
        from p115client import P115FileSystem
        fs = P115FileSystem(self._cookie_client())
        if not any(int(x['id']) == stage for x in fs.iterdir(parent)):
            return  # 上次已删除
        self._auth.delete([stage])
        if any(int(x['id']) == stage for x in fs.iterdir(parent)):
            raise RuntimeError('删除暂存目录后读回仍在，请稍后再试')

    async def save(self, ctx: PipelineContext) -> dict[str, Any]:
        self._require_client()
        return await run_blocking(self._receive_sync, ctx)

    async def reconcile(self, ctx: PipelineContext) -> dict[str, Any]:
        self._require_client()
        return await run_blocking(self._verify_sync, ctx)

    def _verify_sync(self, ctx: PipelineContext) -> dict[str, Any]:
        """只读核验。暂存目录为空且分享已不可用时，转存确定没有发生、以后也不会发生，按失败处理。"""
        from p115client import P115FileSystem
        manifest, p = ctx.ns('storage')['manifest'], ctx.parsed
        client = self._cookie_client()
        if not list(P115FileSystem(client).iterdir(manifest['staging_cid'])):
            problem = share_problem(client, p.share_code, p.receive_code)
            if problem:
                raise TransferRejected(problem, permanent=True)
        return self._reconcile_sync(manifest)

    def _list_share_sync(self, ctx: PipelineContext) -> list[dict]:
        from p115client import P115ShareFileSystem
        p = ctx.parsed
        client = self._cookie_client()
        problem = share_problem(client, p.share_code, p.receive_code)
        if problem:
            raise ShareUnavailable(problem)
        return self._collect_share_videos(P115ShareFileSystem(client, p.share_code, p.receive_code))

    def _stage_sync(self, ctx: PipelineContext, selection: dict) -> dict[str, Any]:
        from p115client import P115FileSystem
        files = selection['files']
        if len({x['name'] for x in files}) != len(files):
            raise NeedsManual('分享内视频文件重名，需人工选择后再转存')
        fs = P115FileSystem(self._cookie_client())
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
        try:
            recv = share_fs.receive([x['id'] for x in manifest['files']],
                                    to_pid=manifest['staging_cid'])
        except StorageBusy:
            raise  # 风控冷却：请求没有发出
        except Exception as exc:
            reason = business_error(exc)
            # 115 明确拒绝且暂存目录仍为空：确定没有转存。其余情况（超时、已有文件落地）交给 reconcile 只读核验。
            if reason is None or list(P115FileSystem(client).iterdir(manifest['staging_cid'])):
                raise
            problem = share_problem(client, p.share_code, p.receive_code)
            raise TransferRejected(problem or reason, permanent=bool(problem)) from None
        try:
            receipt = _await_landing(lambda: self._reconcile_sync(manifest))
        except StorageBusy as busy:
            # 转存请求已经发出，只是核验被风控冷却挡住了：结果不明，之后只读核验
            uncertain = TransferUncertain(f'转存请求已发出，核验时 {busy}')
            uncertain.response = _response_text(recv)
            raise uncertain from None
        except TransferUncertain as uncertain:
            # 115 接受了请求，文件却迟迟没到：记下 115 当时的回应，供排查
            uncertain.response = _response_text(recv)
            raise
        receipt['received'] = recv
        return receipt

    def _reconcile_sync(self, manifest: dict) -> dict[str, Any]:
        from p115client import P115FileSystem
        client = self._cookie_client()
        actual = [_file_record(x) for x in P115FileSystem(client).iterdir(manifest['staging_cid'])]
        landed = _verified_files(manifest['files'], actual)
        return self._receipt(manifest, landed)

    @staticmethod
    def _collect_share_videos(share_fs, root: int = 0, max_depth: int = 3) -> list[dict]:
        out = []
        def walk(cid, depth):
            for item in share_fs.iterdir(cid):
                if item.get('is_directory') or item.get('is_dir'):
                    if depth >= max_depth:
                        raise NeedsManual('分享目录过深，不能完整核验视频清单')
                    walk(item['id'], depth + 1)
                elif is_video(item.get('name', '')):
                    out.append(_file_record(item))
        walk(root, 0)
        return out


def browse_dirs(credentials, cid: str, offset: int = 0, limit: int = 100) -> dict:
    """只读列出 cid 下的子目录：保留长 ID、显式分页，错误不伪装成空目录。"""
    from p115client import check_response
    mode = 'oauth' if credentials.has_oauth else 'cookie'
    try:
        client = credentials.open_client() if mode == 'oauth' else credentials.cookie_client()
        if client is None:
            raise StorageBrowseError('credentials_missing', '尚未配置 115 凭证，请在「设置 → 115 网盘」扫码登录。', 409)
        method = client.fs_files_open if mode == 'oauth' else client.fs_files
        result = check_response(method({'cid': cid, 'offset': offset, 'limit': limit,
                                        'cur': 1, 'show_dir': 1, 'record_open_time': 0}, timeout=15))
        data = result.get('data')
        if not isinstance(data, list):
            raise StorageBrowseError('invalid_response', '115 返回的目录数据格式异常，请稍后重试。', 502)
        path = result.get('path') or []
        if path and str(path[-1].get('cid', path[-1].get('file_id', path[-1].get('id', cid)))) != cid:
            raise StorageBrowseError('directory_missing', '目标目录不存在或无权访问，未回退到根目录。', 404)
        breadcrumbs = []
        for node in path:
            node_id = node.get('cid', node.get('file_id', node.get('id')))
            if node_id is None or not str(node_id).isascii() or not str(node_id).isdecimal():
                continue
            node_name = node.get('name', node.get('file_name', node.get('n')))
            breadcrumbs.append({'id': str(node_id), 'name': str(node_name or ('根目录' if str(node_id) == '0' else ''))})
        if cid == '0' and not breadcrumbs:
            breadcrumbs = [{'id': '0', 'name': '根目录'}]
        items = []
        for item in data:
            # web 列表：目录没有 fid，cid 是目录 ID。开放接口列表：fid / fn / fc（fc=0 是目录）；
            # 另一种写法 file_id / file_name / file_category 也认。
            if 'fc' in item:
                is_dir = str(item['fc']) == '0'
                fid, name = item.get('fid'), item.get('fn')
                pid = item.get('pid', cid)
            elif 'file_category' in item:
                is_dir = str(item['file_category']) == '0'
                fid, name = item.get('file_id'), item.get('file_name')
                pid = item.get('parent_id', cid)
            else:
                is_dir = not item.get('fid')
                fid, name = item.get('cid'), item.get('n')
                pid = item.get('pid', cid)
            if is_dir:
                if fid is None or not str(fid).isascii() or not str(fid).isdecimal():
                    raise StorageBrowseError('invalid_response', '115 返回了无效的目录 ID。', 502)
                items.append({'id': str(fid), 'name': str(name or ''), 'pid': str(pid)})
        total = int(result.get('count', offset + len(data)))
        next_offset = offset + len(data) if offset + len(data) < total and data else None
        return {'items': sorted(items, key=lambda x: x['name']), 'cid': cid, 'path': breadcrumbs,
                'next_offset': next_offset, 'total_entries': total, 'via': mode}
    except StorageBrowseError:
        raise
    except Exception as exc:
        msg = str(exc)
        status = getattr(exc, 'code', None) if isinstance(exc, HTTPError) else getattr(getattr(exc, 'response', None), 'status_code', None)
        if is_auth_error(msg, AUTH_ERRNOS | OPEN_AUTH_CODES):
            raise StorageBrowseError('credentials_expired', '115 登录已失效，请在「设置 → 115 网盘」重新扫码。', 409) from exc
        if status in (403, 405, 429):
            raise StorageBrowseError('upstream_restricted', f'115 拒绝了请求（HTTP {status}），不是空目录。请稍后重试，或在「设置 → 115 网盘」检查登录；不会自动重试绕过限制。') from exc
        if isinstance(exc, TimeoutError) or 'timed out' in msg.lower():
            raise StorageBrowseError('upstream_timeout', '115 目录查询超时，请稍后重试。', 504) from exc
        raise StorageBrowseError('upstream_unavailable', '115 目录服务暂不可用，请检查网络与登录后重试。', 502) from exc
