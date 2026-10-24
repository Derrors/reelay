"""确定性的季集、花絮与画质解析：分享选择、整理计划、规则识别、修正识别与通知共用这一份。"""
from __future__ import annotations
import re

VIDEO_EXT = {'.mp4', '.mkv', '.ts', '.avi', '.mov', '.flv', '.wmv', '.webm',
             '.m2ts', '.mpg', '.mpeg', '.rmvb', '.iso'}
_EXT_RE = re.compile(r'\.[A-Za-z0-9]{1,5}$')


def is_video(name: str) -> bool:
    return any((name or '').lower().endswith(ext) for ext in VIDEO_EXT)


def strip_ext(name: str) -> str:
    """去掉文件扩展名：「繁花.S01E01.mkv」→「繁花.S01E01」。"""
    return _EXT_RE.sub('', name or '')


# --------------------------------------------------------------------------- #
# 中文数字与季号
# --------------------------------------------------------------------------- #
_CN_DIGITS = {'零': 0, '〇': 0, '一': 1, '二': 2, '两': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9}
CN_NUMBER = r'[0-9零〇一二两三四五六七八九十]+'


def cn_number(text: str) -> int | None:
    """「12」「十二」「二十三」「两」→ 整数（0–99）；认不出为 None。"""
    text = (text or '').strip()
    if text.isdigit():
        return int(text)
    if not text or any(ch not in _CN_DIGITS and ch != '十' for ch in text):
        return None
    if '十' not in text:
        return _CN_DIGITS[text] if len(text) == 1 else None
    tens, _, ones = text.partition('十')
    if len(tens) > 1 or len(ones) > 1:
        return None
    return (_CN_DIGITS[tens] if tens else 1) * 10 + (_CN_DIGITS[ones] if ones else 0)


_SEASON_RE = re.compile(rf'(?i)第\s*({CN_NUMBER})\s*季|(?<![a-z0-9])S(\d{{1,2}})(?![0-9])|Season[ ._\-]*(\d{{1,2}})(?!\d)')


def season_of(text: str) -> int | None:
    """正文或文件名里写明的季号：「第二季」「第 2 季」「S02」「Season 2」；没写为 None。"""
    match = _SEASON_RE.search(text or '')
    if not match:
        return None
    return cn_number(match.group(1)) if match.group(1) else int(match.group(2) or match.group(3))


# --------------------------------------------------------------------------- #
# 文件名里的季与集
# --------------------------------------------------------------------------- #
# S01E05、S01.EP05、S01 E05
_SE_RE = re.compile(r'(?i)(?<![0-9])S(\d{1,2})[ ._\-]*EP?(\d{1,4})(?!\d)')
# 第2季 第05集 / 第二季第5话
_CN_SE_RE = re.compile(rf'第\s*({CN_NUMBER})\s*季.*?第\s*(\d{{1,4}})\s*[集话話]')
# 只写集号：E05、EP05（前面不能紧挨字母或数字，避免把「House」里的 e 或「2021E05」当成集号）、第05集
_EP_RE = re.compile(r'(?i)(?<![a-z0-9])EP?[ ._\-]?(\d{1,4})(?![0-9])|第\s*(\d{1,4})\s*[集话話]')
# 动画常见写法：「[字幕组] 片名 - 05 [1080p]」「[05]」
_ANIME_EP_RE = re.compile(r'(?i)[\s_]-[\s_]*(\d{1,3})(?:v\d)?(?=[\s_\[(.]|$)|\[(\d{1,3})(?:v\d)?\]')
# 年份与分辨率不会是集号
_NOT_EPISODE = {480, 576, 720, 1080, 2160}


def episode_of(filename: str) -> tuple[int | None, int | None]:
    """文件名 →（季, 集）；没写季时季为 None，认不出集号时集为 None。年份与分辨率数字不当成集号。"""
    name = filename or ''
    match = _SE_RE.search(name)
    if match:
        return int(match.group(1)), int(match.group(2))
    match = _CN_SE_RE.search(name)
    if match:
        return cn_number(match.group(1)), int(match.group(2))
    season = season_of(name)
    for pattern in (_EP_RE, _ANIME_EP_RE):
        for found in pattern.finditer(name):
            episode = int(next(group for group in found.groups() if group))
            if episode and episode not in _NOT_EPISODE:
                return season, episode
    stem = strip_ext(name).strip()
    if stem.isdigit() and len(stem) <= 4:
        episode = int(stem)
        if episode and episode not in _NOT_EPISODE and not 1900 <= episode <= 2099:
            return season, episode
    return season, None


def episode_list(text: str, *, maximum: int = 2000) -> list[int]:
    """「1-5,7」「E01-E05、E07」→ [1, 2, 3, 4, 5, 7]；写法无效或超出 1–maximum 抛 ValueError。"""
    out: set[int] = set()
    for part in re.split(r'[,，、\s]+', (text or '').strip()):
        if not part:
            continue
        match = re.fullmatch(r'(?i)EP?(\d{1,4})(?:\s*[-~～到至]\s*EP?(\d{1,4}))?|(\d{1,4})(?:\s*[-~～到至]\s*(\d{1,4}))?',
                             part)
        if not match:
            raise ValueError(f'集号写法无效：{part}')
        start = int(match.group(1) or match.group(3))
        end = int(match.group(2) or match.group(4) or start)
        if not 0 < start <= end <= maximum:
            raise ValueError(f'集号无效：{part}')
        out.update(range(start, end + 1))
    return sorted(out)


def format_episodes(episodes: list[int], limit: int = 12) -> str:
    """[1,2,3,5] → E01-E03、E05。"""
    ranges, start, prev = [], None, None
    for ep in sorted(episodes):
        if start is None:
            start = prev = ep
        elif ep == prev + 1:
            prev = ep
        else:
            ranges.append((start, prev))
            start = prev = ep
    if start is not None:
        ranges.append((start, prev))
    parts = [f'E{a:02d}' if a == b else f'E{a:02d}-E{b:02d}' for a, b in ranges]
    return '、'.join(parts[:limit]) + (' 等' if len(parts) > limit else '')


def summarize_pairs(pairs) -> tuple[int | None, list[int]]:
    """（季, 集）列表 →（唯一的季，跨多季时为 None；去重排序的集号）。"""
    seasons = {season for season, _ in pairs}
    return (seasons.pop() if len(seasons) == 1 else None), sorted({ep for _, ep in pairs})


def multi_season(pairs) -> list[list[int]] | None:
    """只在跨多季时返回（季, 集）列表，供界面显示「S01–S03 · 30 集」；单季返回 None，沿用季与集。"""
    pairs = [list(pair) for pair in pairs or []]
    return pairs if len({season for season, _ in pairs}) > 1 else None


def planned_pairs(ctx) -> list[tuple[int, int]]:
    """任务涉及的（季, 集）：有转存清单时以清单为准；消息没写集数时用读取分享的结果（share_probe）；
    否则按识别结果，未写季视为第 1 季。消息与分享都没给出集数时返回空列表。"""
    pairs = ctx.episode_pairs()
    if pairs:
        return pairs
    share = ctx.meta.get("share") or {}
    if share.get("status") == "ok" and share.get("pairs"):
        return [(int(season), int(ep)) for season, ep in share["pairs"]]
    return []


def pairs_label(pairs) -> str:
    """跨多季的（季, 集）→「S01–S03 共 30 集」；季不连续时用顿号分隔。"""
    seasons = sorted({season for season, _ in pairs})
    if not seasons:
        return ''
    contiguous = seasons == list(range(seasons[0], seasons[-1] + 1))
    head = (f'S{seasons[0]:02d}–S{seasons[-1]:02d}' if contiguous and len(seasons) > 1
            else '、'.join(f'S{s:02d}' for s in seasons))
    return f'{head} 共 {len(set(map(tuple, pairs)))} 集'


def pairs_text(pairs, *, with_season: bool = False) -> str:
    """单季写成「E01-E03」（with_season 时为「S02 E01-E03」），跨多季写成「S01–S03 共 30 集」。"""
    pairs = sorted({tuple(pair) for pair in pairs})
    seasons = {season for season, _ in pairs}
    if len(seasons) > 1:
        return pairs_label(pairs)
    text = format_episodes([ep for _, ep in pairs], limit=4)
    return f'S{seasons.pop():02d} {text}' if with_season and seasons else text


_EXTRA_RE = re.compile(
    r'(?i)(?<![a-z])(sample|trailer|teaser|featurette|preview|extras?|'
    r'behind[ ._-]?the[ ._-]?scenes|making[ ._-]?of)(?![a-z])|预告|花絮|片花|样片|彩蛋|特辑|幕后')


def is_extra(filename: str) -> bool:
    """样片、预告、花絮等非正片文件；不参与转存和整理。"""
    return bool(_EXTRA_RE.search(filename or ''))


# --------------------------------------------------------------------------- #
# 清晰度
# --------------------------------------------------------------------------- #
# （写法, 规范名, 等级）：等级 4=2160p/4K，3=1080p，2=720p，1=标清
_RESOLUTIONS = ((r'2160[pi]|4k|uhd', '2160p', 4), (r'1080[pi]', '1080p', 3), (r'720p', '720p', 2),
                (r'576p', '576p', 1), (r'480p', '480p', 1))
RESOLUTION_LABELS = {4: '2160p', 3: '1080p', 2: '720p', 1: '标清'}


def _resolution(text: str | None) -> tuple[str, int] | None:
    value = (text or '').lower()
    for pattern, name, rank in _RESOLUTIONS:
        if re.search(rf'(?<![a-z0-9])(?:{pattern})(?![a-z0-9])', value):
            return name, rank
    return None


def resolution_name(text: str | None) -> str | None:
    """文件名或消息里写的清晰度的规范写法：「4K」→「2160p」，「1080i」→「1080p」；认不出为 None。"""
    found = _resolution(text)
    return found[0] if found else None


def resolution_rank(text: str | None) -> int:
    """画质等级：4=2160p/4K，3=1080p，2=720p，1=标清，0=未知。"""
    found = _resolution(text)
    return found[1] if found else 0


def resolution_label(text: str | None) -> str | None:
    """展示用的清晰度，如「2160p」「标清」；认不出为 None。"""
    return RESOLUTION_LABELS.get(resolution_rank(text))
