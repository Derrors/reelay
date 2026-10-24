"""分享选择和整理计划共用的确定性季集、花絮与画质解析。"""
from __future__ import annotations
import re

VIDEO_EXT = {'.mp4', '.mkv', '.ts', '.avi', '.mov', '.flv', '.wmv', '.webm',
             '.m2ts', '.mpg', '.mpeg', '.rmvb', '.iso'}


def is_video(name: str) -> bool:
    return any((name or '').lower().endswith(ext) for ext in VIDEO_EXT)


def episode_of(filename: str) -> tuple[int | None, int | None]:
    name = filename or ''
    match = re.search(r'(?i)S(\d{1,2})[ ._\-]*E(\d{1,4})', name)
    if match:
        return int(match.group(1)), int(match.group(2))
    match = re.search(r'(?i)(?:EP?|第)[ ._\-]*(\d{1,4})[ ._\-]*(?:集)?', name)
    if match:
        return None, int(match.group(1))
    stem = re.sub(r'\.[A-Za-z0-9]{1,5}$', '', name).strip()
    return (None, int(stem)) if stem.isdigit() else (None, None)


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


_EXTRA_RE = re.compile(
    r'(?i)(?<![a-z])(sample|trailer|teaser|featurette|preview|extras?|'
    r'behind[ ._-]?the[ ._-]?scenes|making[ ._-]?of)(?![a-z])|预告|花絮|片花|样片|彩蛋|特辑|幕后')


def is_extra(filename: str) -> bool:
    """样片、预告、花絮等非正片文件；不参与转存和整理。"""
    return bool(_EXTRA_RE.search(filename or ''))


_RESOLUTIONS = ((r'2160[pi]|4k|uhd', 4), (r'1080[pi]', 3), (r'720p', 2), (r'480p|576p', 1))


RESOLUTION_LABELS = {4: "2160p", 3: "1080p", 2: "720p", 1: "标清"}


def resolution_label(text: str | None) -> str | None:
    """从文件名或消息里认出分辨率，如「2160p」；认不出为 None。"""
    return RESOLUTION_LABELS.get(resolution_rank(text))


def resolution_rank(text: str | None) -> int:
    """画质等级：4=2160p/4K，3=1080p，2=720p，1=标清，0=未知。"""
    value = (text or '').lower()
    for pattern, rank in _RESOLUTIONS:
        if re.search(rf'(?<![a-z0-9])(?:{pattern})(?![a-z0-9])', value):
            return rank
    return 0
