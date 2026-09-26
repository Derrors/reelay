"""分享选择和整理计划共用的确定性季集解析。"""
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
