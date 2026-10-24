"""确定性的规则识别：通用规则、来源自己的识别规则、115 链接提取与预过滤。

规则识别（RegexParser）、AI 识别的参考结果（rule_hint）、AI 不可用时的兜底、管理台的识别规则试跑都用这里。

每个来源可在 ``sources[].parse_rules`` 里为下列字段各写一条正则（多行模式，``^``/``$`` 匹配每行首尾），
没写或在某条消息上没匹配到的字段沿用通用规则（``regex_fields``）：

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

from .media import CN_NUMBER, resolution_name, season_of

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


# --------------------------------------------------------------------------- #
# 115 链接解析（确定性代码：AI 抽候选，这里较真）
# --------------------------------------------------------------------------- #
_SHARE_RE = re.compile(r"115(?:cdn)?\.(?:com|cn)/s/([0-9a-zA-Z]+)")
_SHARE_URL_RE = re.compile(r"https?://[^\s]*?115(?:cdn)?\.(?:com|cn)/s/[0-9a-zA-Z]+[^\s]*")
_PWD_RE = re.compile(r"(?:访问码|提取码|密码|password|pwd)\s*[:：=]?\s*([0-9a-zA-Z]{4})(?![0-9a-zA-Z])", re.I)


def parse_115_url(text: str, urls: list[str]) -> tuple[str | None, str | None, str | None]:
    """从正文与超链接里提取 (share_url, share_code, receive_code)。

    兼容资源频道常用的短链域名 ``115cdn.com/s/...``；对外存储/转存仍以
    115 官方域名为规范，只保留 path 中的 share code。share_url 是消息里的那条链接本身，
    链接只出现在正文里时也只取链接，不带上整段正文。
    """
    for u in [*urls, text]:
        m = _SHARE_RE.search(u or "")
        if m:
            found = _SHARE_URL_RE.search(u or "")
            share_url = found.group(0) if found else f"https://115.com/s/{m.group(1)}"
            pm = _PWD_RE.search(text or "")
            if pm:
                pwd = pm.group(1)
            else:
                qm = re.search(r"[?&]password=([0-9a-zA-Z]{4})", u or "")
                pwd = qm.group(1) if qm else None
            return share_url, m.group(1), pwd
    return None, None, None


# --------------------------------------------------------------------------- #
# 规则解析（兜底 / 降级）
# --------------------------------------------------------------------------- #
# 只在同一行内匹配：「年份：2023」下一行的「集数：…」不能拼成「2023 集」
_SP = r"[ \t]*"
_RANGE = rf"{_SP}[-~～到至]{_SP}"
_YEAR_RE = re.compile(r"[(（]((?:19|20)\d{2})[)）]|年份" + _SP + r"[:：]" + _SP + r"((?:19|20)\d{2})")
_BARE_YEAR_RE = re.compile(r"(?<![\d.])((?:19|20)\d{2})(?![\d.]|[pPiIkK])")
# 「更新至第 8 集」「全 36 集」「共 12 集」「12 集全」：从第 1 集到这一集
_UPTO_RE = re.compile(rf"(?:更新至|更至|更新到|更到){_SP}第?{_SP}(\d{{1,4}})|(?:全|共){_SP}(\d{{1,4}}){_SP}[集话話]"
                      rf"|(\d{{1,4}}){_SP}[集话話]全")
# 写明的集与区间：S01E05、S01E01-E05、E05、EP01-EP03、第 5 集、第 1-3 集
_EPISODES_RE = re.compile(
    rf"(?i)(?<![0-9])S\d{{1,2}}[ \t._-]*EP?(\d{{1,4}})(?:{_RANGE}(?:S\d{{1,2}})?EP?(\d{{1,4}}))?(?!\d)"
    rf"|第{_SP}(\d{{1,4}})(?:{_RANGE}第?{_SP}(\d{{1,4}}))?{_SP}[集话話]"
    rf"|(?<![a-z0-9])EP?(\d{{1,4}})(?:{_RANGE}EP?(\d{{1,4}}))?(?![0-9a-z])")
# 剧集的其他写法（只用来判断是剧集，不取集数）：S01、全集、合集
_SERIES_RE = re.compile(rf"(?i)(?<![a-z0-9])S\d{{1,2}}(?![0-9])|全{_SP}\d*{_SP}集|第{_SP}{CN_NUMBER}{_SP}季")
# 「62GB」「15.12 GB」「800 MB」「1.5G」；单独的 K、M 不算（「4K」是清晰度）
_SIZE_RE = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)" + _SP + r"(?:([KMGT])i?B|([GT]))(?![a-zA-Z])", re.I)
# 片名前的字段名：「名称：」「片名：」「资源名称：」「电影：」……
_LABEL_RE = re.compile(rf"^(?:(?:资源|影片|电影|剧集|电视剧|动漫|番剧|综艺|纪录片)(?:名称|名字|名)?|名称|片名|剧名|标题){_SP}[:：]{_SP}")
# 第一行里片名在这些标记前结束：年份、季集、更新进度、清晰度、分隔符
_TITLE_END_RE = re.compile(
    rf"(?i)[(（](?:19|20)\d{{2}}[)）]|(?<![\d.])(?:19|20)\d{{2}}(?![\d.]|[pik])|第{_SP}{CN_NUMBER}{_SP}[季集话話]"
    rf"|(?<![a-z0-9])S\d{{1,2}}(?![0-9])|(?<![a-z0-9])EP?\d{{1,4}}(?![0-9a-z])|更新至|更至|更新到|[全共]{_SP}\d+{_SP}集"
    rf"|(?<![a-z0-9])(?:2160p|1080p|720p|4k|uhd)(?![a-z0-9])|[|｜]")
AD_KEYWORDS = ("招商", "广告", "投稿", "点击进群", "更多资源关注", "推广")


def _title_line(text: str) -> str:
    """片名所在的行：写明「名称：」「片名：」的行优先，否则第一个不全是话题标签与符号的行；去掉开头的 emoji 与符号。"""
    lines = [re.sub(r"^[^\w【《(（\[]+", "", line).strip() for line in (text or "").splitlines()
             if re.sub(r"#\S+|[^\w]", "", line)]
    return next((line for line in lines if _LABEL_RE.match(line)), lines[0] if lines else "")


def _title(line: str) -> str | None:
    line = _LABEL_RE.sub("", line)
    quoted = re.match(r"^(?:【([^】]+)】|《([^》]+)》)", line)
    if quoted:
        return (quoted.group(1) or quoted.group(2)).strip() or None
    cut = _TITLE_END_RE.search(line)
    title = (line[:cut.start()] if cut else line).strip()
    if " " not in title and re.search(r"[._]", title):
        title = re.sub(r"[._]+", " ", title)  # 文件名式的写法：繁花.S01E05.2160p
    # 「奥本海默 Oppenheimer」这样中英并列时取中文名
    bilingual = re.fullmatch(r"([^\x00-\x7f][^A-Za-z]*?)\s+[A-Za-z][\w :'’.&!,-]*", title)
    if bilingual:
        title = bilingual.group(1)
    return title.strip(" -·•:：/（(【[") or None


def _generic_episodes(text: str) -> list[int]:
    for line in (text or "").splitlines():
        upto = _UPTO_RE.search(line)
        if upto:
            last = int(next(group for group in upto.groups() if group))
            return list(range(1, min(last, 1000) + 1))
    found: set[int] = set()
    for match in _EPISODES_RE.finditer(text or ""):
        groups = [group for group in match.groups()]
        start = next((int(g) for g in groups[0::2] if g), None)
        end = next((int(g) for g in groups[1::2] if g), None) or start
        if start and start <= end <= start + 999 and start not in (480, 576, 720, 1080, 2160):
            found.update(range(start, end + 1))
    return sorted(found)


def regex_fields(text: str, rules: dict | None = None) -> dict:
    """按规则从正文取片名、年份、季、集、分辨率、大小；from_rules 是来源规则匹配到的字段。

    来源自己的识别规则（rules，见 core/parse_rules.py）优先，没写或没匹配到的字段沿用通用规则。
    """
    line = _title_line(text)
    year = _YEAR_RE.search(text or "") or _BARE_YEAR_RE.search(line)
    size = _SIZE_RE.search(text or "")
    fields = {
        "title": _title(line),
        "year": int(next(group for group in year.groups() if group)) if year else None,
        "season": season_of(text),
        "episodes": _generic_episodes(text),
        "resolution": resolution_name(text),
        "size": f"{size.group(1)} {(size.group(2) or size.group(3)).upper()}B" if size else None,
    }
    custom = extract(rules, text)
    fields.update(custom)
    # 看不出是剧集时不猜电影：留空，由 TMDB（剧集与电影都搜）和读取分享补上
    fields["media_type"] = "tv" if fields["season"] or fields["episodes"] or _SERIES_RE.search(text) else None
    fields["from_rules"] = list(custom)
    return fields


def prefilter_reason(ctx, share_code: str | None) -> str | None:
    """与 DefaultDecision 的硬过滤一致；命中则无论 LLM 怎么判都会被跳过。"""
    if not share_code:
        return "无 115 链接"
    filters = (ctx.source_rule or {}).get("filters", {}) or {}
    text = ctx.raw.text or ""
    must = filters.get("must_have") or []
    if must and not any(keyword in content for keyword in must for content in [text, *ctx.raw.urls]):
        return "未命中 must_have"
    if any(keyword in text for keyword in filters.get("block_keywords") or []):
        return "命中黑名单关键词"
    return None
