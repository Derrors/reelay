"""来源的识别规则：按字段写的正则，规则识别（RegexParser）与 AI 识别的参考结果共用。

每个来源可在 ``sources[].parse_rules`` 里为下列字段各写一条正则（多行模式，``^``/``$`` 匹配每行首尾），
没写或在某条消息上没匹配到的字段沿用通用规则（见 ``providers/parser.py``）：

- title / year / season / resolution / size：取第一处匹配的第一个捕获组，没有捕获组时取整个匹配；
- episodes：在正文里逐处匹配并合并。这处匹配用上了命名组 ``end`` 时是区间（有 ``start`` 时从 start 起，
  没有时从 1 起，如「更新至 10 集」表示前 10 集都在），否则取第一个匹配到的捕获组为单集；
  可以用 ``|`` 写几种写法，如 ``S\\d+E(\\d+)|第(?P<start>\\d+)-(?P<end>\\d+)集``。

配置示例::

    sources:
      - bot: "@resource_bot"
        parser: regex
        parse_rules:
          title: '^名称：(.+?)\\s*(?:\\(\\d{4}\\))?$'
          episodes: 'S\\d+E(\\d+)'
"""
from __future__ import annotations

import functools
import re
from typing import Any

FIELDS = ("title", "year", "season", "episodes", "resolution", "size")
LABELS = {"title": "片名", "year": "年份", "season": "季", "episodes": "集", "resolution": "分辨率", "size": "大小"}
PATTERN_LIMIT = 500
MAX_EPISODE = 2000


def validate(rules: Any, label: str = "parse_rules") -> None:
    if not isinstance(rules, dict):
        raise ValueError(f"{label} 必须是对象")
    for field, pattern in rules.items():
        if field not in FIELDS:
            raise ValueError(f"{label} 只能包含 {'、'.join(FIELDS)}")
        if type(pattern) is not str or not pattern.strip() or len(pattern) > PATTERN_LIMIT:
            raise ValueError(f"{label}.{field} 必须是不超过 {PATTERN_LIMIT} 字的非空正则")
        try:
            compiled = re.compile(pattern, re.M)
        except re.error as exc:
            raise ValueError(f"{label}.{field}（{LABELS[field]}）不是有效的正则：{exc}") from None
        if field == "episodes" and not compiled.groups:
            raise ValueError(f"{label}.episodes 需要捕获组：单集用一个捕获组，区间用命名组 start 与 end")
        if field == "episodes" and "start" in compiled.groupindex and "end" not in compiled.groupindex:
            raise ValueError(f"{label}.episodes 有命名组 start 时也要有 end")


@functools.lru_cache(maxsize=256)
def _compile(pattern: str) -> re.Pattern:
    return re.compile(pattern, re.M)


def _first(match: re.Match) -> str:
    return (match.group(1) if match.re.groups else match.group(0)) or ""


def _number(value: str) -> int | None:
    digits = re.sub(r"\D", "", value or "")
    return int(digits) if digits and len(digits) <= 6 else None


def _episodes(pattern: re.Pattern, text: str) -> list[int]:
    found: set[int] = set()
    named = pattern.groupindex
    for match in pattern.finditer(text):
        end = _number(match.group("end") or "") if "end" in named else None
        if end:
            if "start" in named:  # 有 start 组却没取到（区间写法只出现了一个数字）时只算这一集
                start = _number(match.group("start") or "") or end
            else:
                start = 1
            if start <= end <= MAX_EPISODE:
                found.update(range(start, end + 1))
            continue
        # 几种写法用 | 连起来时，这处匹配用的是哪个分支，就取那个分支里的捕获组
        ep = next((_number(value) for value in match.groups() if value), None)
        if ep and ep <= MAX_EPISODE:
            found.add(ep)
    return sorted(found)


def extract(rules: dict | None, text: str) -> dict[str, Any]:
    """按来源规则从正文提取字段；只返回匹配到的字段（值已转成 ParsedResource 的类型）。"""
    out: dict[str, Any] = {}
    for field in FIELDS:
        pattern = (rules or {}).get(field)
        if not pattern:
            continue
        try:
            compiled = _compile(pattern)
        except re.error:
            continue  # 配置校验会拦下，这里只防手改文件
        if field == "episodes":
            episodes = _episodes(compiled, text)
            if episodes:
                out[field] = episodes
            continue
        match = compiled.search(text)
        if not match:
            continue
        value = " ".join(_first(match).split()).strip(" -·•|:：（(")
        if field in ("year", "season"):
            number = _number(value)
            if number is not None and (field == "season" or 1900 <= number <= 2100):
                out[field] = number
        elif value:
            out[field] = value[:200]
    return out
