"""分享选择和整理计划共用的确定性季集、花絮与画质解析。"""
from __future__ import annotations
import re


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


_EXTRA_RE = re.compile(
    r'(?i)(?<![a-z])(sample|trailer|teaser|featurette|preview|extras?|'
    r'behind[ ._-]?the[ ._-]?scenes|making[ ._-]?of)(?![a-z])|预告|花絮|片花|样片|彩蛋|特辑|幕后')


def is_extra(filename: str) -> bool:
    """样片、预告、花絮等非正片文件；不参与转存和整理。"""
    return bool(_EXTRA_RE.search(filename or ''))


_RESOLUTIONS = ((r'2160[pi]|4k|uhd', 4), (r'1080[pi]', 3), (r'720p', 2), (r'480p|576p', 1))


def resolution_rank(text: str | None) -> int:
    """画质等级：4=2160p/4K，3=1080p，2=720p，1=标清，0=未知。"""
    value = (text or '').lower()
    for pattern, rank in _RESOLUTIONS:
        if re.search(rf'(?<![a-z0-9])(?:{pattern})(?![a-z0-9])', value):
            return rank
    return 0
