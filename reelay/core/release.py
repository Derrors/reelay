"""发布版本信息：字幕、音轨、HDR、片源、编码与打包形式。

LLM 在识别时一并抽取（strict schema + 本地校验）；规则从正文再抽一遍补上 LLM 漏掉的明确标记。
同一集多个版本时由 ``compare`` 按用户偏好（``release`` 配置）择优：收件箱推荐版本与整理时的洗版
用同一套比较。比较依次看：偏好的字幕语言 → 清晰度 → HDR 偏好 → 偏好的音轨 → 偏好的片源 → 体积。

消息没写的项（如没提字幕）有两种处理：推荐版本时视为不符合偏好，明确符合偏好的版本优先；
洗版会删除旧文件，用严格模式 —— 旧文件已知符合偏好而新文件未知时不替换，未知的一方不算更好。
"""
from __future__ import annotations

import re
from typing import Any

from .media import resolution_rank

SUBTITLE_LANGS = ("chs", "cht", "eng", "jpn", "kor")
SUBTITLE_FORMATS = ("embedded", "external", "hardsub")
AUDIO_LANGS = ("mandarin", "cantonese", "english", "japanese", "korean")
HDR_FORMATS = ("hdr10", "hdr10plus", "dolby_vision", "hlg")
SOURCES = ("remux", "bluray", "web-dl", "webrip", "hdtv", "dvd")
CODECS = ("h265", "h264", "av1")
PACKAGING = ("single", "batch", "season", "complete")
HDR_PREFERENCES = ("any", "prefer", "avoid")

LABELS = {
    "chs": "简体中字", "cht": "繁体中字", "eng": "英字", "jpn": "日字", "kor": "韩字",
    "embedded": "内封字幕", "external": "外挂字幕", "hardsub": "内嵌字幕",
    "mandarin": "国语", "cantonese": "粤语", "english": "英语", "japanese": "日语", "korean": "韩语",
    "hdr10": "HDR10", "hdr10plus": "HDR10+", "dolby_vision": "杜比视界", "hlg": "HLG",
    "remux": "Remux", "bluray": "蓝光", "web-dl": "WEB-DL", "webrip": "WEBRip", "hdtv": "HDTV", "dvd": "DVD",
    "h265": "H.265", "h264": "H.264", "av1": "AV1",
    "single": "单集", "batch": "多集合集", "season": "整季", "complete": "全集",
}

SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "subtitles": {"type": "array", "items": {"type": "string", "enum": list(SUBTITLE_LANGS)}},
        "subtitle_format": {"type": ["string", "null"], "enum": [*SUBTITLE_FORMATS, None]},
        "audio": {"type": "array", "items": {"type": "string", "enum": list(AUDIO_LANGS)}},
        "hdr": {"type": "array", "items": {"type": "string", "enum": list(HDR_FORMATS)}},
        "source": {"type": ["string", "null"], "enum": [*SOURCES, None]},
        "codec": {"type": ["string", "null"], "enum": [*CODECS, None]},
        "packaging": {"type": ["string", "null"], "enum": [*PACKAGING, None]},
        "group": {"type": ["string", "null"]},
    },
    "required": ["subtitles", "subtitle_format", "audio", "hdr", "source", "codec", "packaging", "group"],
}
_LISTS = {"subtitles": SUBTITLE_LANGS, "audio": AUDIO_LANGS, "hdr": HDR_FORMATS}
_ONE_OF = {"subtitle_format": SUBTITLE_FORMATS, "source": SOURCES, "codec": CODECS, "packaging": PACKAGING}

PROMPT_FIELDS = (
    "release(发布版本信息对象：subtitles 字幕语言数组 chs简体/cht繁体/eng/jpn/kor，"
    "subtitle_format 字幕形式 embedded内封/external外挂/hardsub内嵌硬字幕，"
    "audio 音轨语言数组 mandarin国语/cantonese粤语/english/japanese/korean，hdr 数组 hdr10/hdr10plus/dolby_vision/hlg，"
    "source 片源 remux/bluray/web-dl/webrip/hdtv/dvd，codec h265/h264/av1，"
    "packaging 打包形式 single单集/batch多集合集/season整季/complete全集完结，group 发布组或字幕组；"
    "消息没写的项给空数组或 null)"
)


def empty() -> dict[str, Any]:
    return {"subtitles": [], "subtitle_format": None, "audio": [], "hdr": [], "source": None,
            "codec": None, "packaging": None, "group": None}


def validate(value: Any, label: str = "release") -> dict[str, Any]:
    """校验并补齐为完整结构；未知字段、非法枚举值都拒绝。"""
    if not isinstance(value, dict) or set(value) - set(SCHEMA["properties"]):
        raise ValueError(f"{label} 必须是只含 {'/'.join(SCHEMA['properties'])} 的对象")
    out = empty()
    for key, allowed in _LISTS.items():
        items = value.get(key, [])
        if type(items) is not list or any(item not in allowed for item in items) or len(set(items)) != len(items):
            raise ValueError(f"{label}.{key} 只能包含 {'、'.join(allowed)}，且不能重复")
        out[key] = list(items)
    for key, allowed in _ONE_OF.items():
        item = value.get(key)
        if item is not None and item not in allowed:
            raise ValueError(f"{label}.{key} 只能是 {'、'.join(allowed)}")
        out[key] = item
    group = value.get("group")
    if group is not None and (type(group) is not str or len(group) > 100):
        raise ValueError(f"{label}.group 必须是不超过 100 字的字符串")
    out["group"] = group.strip() or None if group else None
    return out


# --------------------------------------------------------------------------- #
# 规则抽取：只认明确的标记
# --------------------------------------------------------------------------- #
_TOKEN = r"(?<![A-Za-z0-9])(?:{})(?![A-Za-z0-9])"
_RULES: list[tuple[str, str, re.Pattern]] = [
    ("subtitles", "chs", re.compile(r"简繁|简中|简体|中字|中文字幕|简日|简英|中英(?:双|字)|双语字幕")),
    ("subtitles", "cht", re.compile(r"简繁|繁中|繁体|繁體|繁日|繁英")),
    ("subtitles", "eng", re.compile(r"中英(?:双|字)|简英|繁英|英字|英文字幕|双语字幕")),
    ("audio", "mandarin", re.compile(r"国语|國語|国配|普通话|国粤|國粵")),
    ("audio", "cantonese", re.compile(r"粤语|粵語|粤配|国粤|國粵")),
    ("audio", "english", re.compile(r"英语音轨|英配|英语原声")),
    ("audio", "japanese", re.compile(r"日语|日配|日语原声")),
    ("audio", "korean", re.compile(r"韩语|韓語")),
    ("hdr", "hdr10plus", re.compile(_TOKEN.format(r"HDR10\+|HDR10Plus"), re.I)),
    ("hdr", "hdr10", re.compile(_TOKEN.format(r"HDR10(?!\+)|HDR(?!10)"), re.I)),
    ("hdr", "dolby_vision", re.compile(_TOKEN.format(r"DV|DoVi|Dolby[ .]?Vision") + r"|杜比视界", re.I)),
    ("hdr", "hlg", re.compile(_TOKEN.format(r"HLG"), re.I)),
]
_SINGLE: list[tuple[str, str, re.Pattern]] = [
    ("subtitle_format", "embedded", re.compile(r"内封")),
    ("subtitle_format", "external", re.compile(r"外挂")),
    ("subtitle_format", "hardsub", re.compile(r"内嵌|硬字幕")),
    ("source", "remux", re.compile(_TOKEN.format(r"REMUX"), re.I)),
    ("source", "bluray", re.compile(_TOKEN.format(r"Blu-?Ray|BDRip|BD") + r"|蓝光|原盘", re.I)),
    ("source", "web-dl", re.compile(_TOKEN.format(r"WEB-?DL"), re.I)),
    ("source", "webrip", re.compile(_TOKEN.format(r"WEB-?Rip"), re.I)),
    ("source", "hdtv", re.compile(_TOKEN.format(r"HDTV"), re.I)),
    ("source", "dvd", re.compile(_TOKEN.format(r"DVD(?:Rip)?"), re.I)),
    ("codec", "h265", re.compile(_TOKEN.format(r"H\.?265|HEVC|x265"), re.I)),
    ("codec", "h264", re.compile(_TOKEN.format(r"H\.?264|AVC|x264"), re.I)),
    ("codec", "av1", re.compile(_TOKEN.format(r"AV1"), re.I)),
    ("packaging", "complete", re.compile(r"全集|完结|全\s*\d+\s*集")),
    ("packaging", "season", re.compile(r"整季|全季")),
    ("packaging", "batch", re.compile(r"合集|打包")),
]


def from_text(text: str | None) -> dict[str, Any]:
    value = empty()
    content = text or ""
    for key, item, pattern in _RULES:
        if item not in value[key] and pattern.search(content):
            value[key].append(item)
    for key, item, pattern in _SINGLE:
        if value[key] is None and pattern.search(content):
            value[key] = item
    return value


def fill(primary: dict | None, fallback: dict) -> dict[str, Any]:
    """LLM 结果为主，规则只补 LLM 留空的项。"""
    value = dict(primary or empty())
    for key in SCHEMA["properties"]:
        if not value.get(key) and fallback.get(key):
            value[key] = fallback[key]
    return value


def describe(value: dict | None) -> list[str]:
    """展示用的中文标签，如 ["简体中字", "内封字幕", "国语", "杜比视界", "WEB-DL"]。"""
    value = value or {}
    out = [LABELS[item] for key in ("subtitles",) for item in value.get(key) or []]
    out += [LABELS[value[key]] for key in ("subtitle_format",) if value.get(key)]
    out += [LABELS[item] for key in ("audio", "hdr") for item in value.get(key) or []]
    out += [LABELS[value[key]] for key in ("source", "codec", "packaging") if value.get(key)]
    return out


# --------------------------------------------------------------------------- #
# 用户偏好与版本比较
# --------------------------------------------------------------------------- #
DEFAULT_PREFERENCES: dict[str, Any] = {"prefer_subtitles": ["chs", "cht"], "prefer_audio": [], "hdr": "any",
                                       "prefer_sources": []}


def validate_preferences(prefs: Any) -> None:
    if not isinstance(prefs, dict) or set(prefs) - set(DEFAULT_PREFERENCES):
        raise ValueError(f"release 只支持 {'、'.join(DEFAULT_PREFERENCES)}")
    for key, allowed in (("prefer_subtitles", SUBTITLE_LANGS), ("prefer_audio", AUDIO_LANGS),
                         ("prefer_sources", SOURCES)):
        items = prefs.get(key, [])
        if type(items) is not list or any(item not in allowed for item in items) or len(set(items)) != len(items):
            raise ValueError(f"release.{key} 只能包含 {'、'.join(allowed)}，且不能重复（顺序即优先级）")
    if prefs.get("hdr", "any") not in HDR_PREFERENCES:
        raise ValueError("release.hdr 必须为 any、prefer 或 avoid")


def _rank_in(values: list[str], preferred: list[str]) -> int | None:
    """偏好列表里的名次分（越靠前越高，没有命中为 0）；values 为空表示未知，返回 None。"""
    if not values:
        return None
    hits = [len(preferred) - preferred.index(v) for v in values if v in preferred]
    return max(hits, default=0)


def _resolution(item: dict) -> int:
    return item["rank"] if type(item.get("rank")) is int else resolution_rank(item.get("resolution"))


def compare(a: dict, b: dict, prefs: dict | None = None, *, strict: bool = False) -> int:
    """a、b 为 ``{"resolution" 或 "rank", "release", "size"}``（size 为字节数或 None）；
    1 表示 a 更好，-1 表示 b 更好，0 表示无法区分。

    strict（洗版用，a 是新文件、b 是库中文件）：某项 b 已知而 a 未知时直接返回 0（不能确认新文件不更差），
    a 已知而 b 未知时这一项跳过；非严格时未知按「不符合偏好」计。
    """
    prefs = {**DEFAULT_PREFERENCES, **(prefs or {})}
    ra, rb = a.get("release") or {}, b.get("release") or {}

    def sign(x, y) -> int:
        return (x > y) - (x < y)

    def ranked(key: str, preferred: list[str]):
        values = [(r.get(key) if isinstance(r.get(key), list) else [r[key]] if r.get(key) else []) for r in (ra, rb)]
        return tuple(_rank_in(v, preferred) for v in values)

    steps = []
    if prefs["prefer_subtitles"]:
        steps.append(ranked("subtitles", prefs["prefer_subtitles"]))
    steps.append((_resolution(a), _resolution(b)))
    if prefs["hdr"] != "any":
        want = prefs["hdr"] == "prefer"
        steps.append(tuple((bool(r.get("hdr")) == want) if (r.get("hdr") or not strict) else None for r in (ra, rb)))
    if prefs["prefer_audio"]:
        steps.append(ranked("audio", prefs["prefer_audio"]))
    if prefs["prefer_sources"]:
        steps.append(ranked("source", prefs["prefer_sources"]))
    steps.append((a.get("size"), b.get("size")))
    for x, y in steps:
        if strict and y is not None and x is None:
            return 0
        if x is None or y is None:
            if strict:
                continue
            x, y = x or 0, y or 0
        result = sign(x, y)
        if result:
            return result
    return 0


def size_bytes(value: Any) -> int | None:
    """把 '1.46 GB' / '800 MB' / 字节数换算成字节；无法识别返回 None。"""
    if value in (None, ""):
        return None
    if type(value) in (int, float):
        return int(value)
    match = re.match(r"\s*([0-9.]+)\s*(TB|GB|MB|KB|T|G|M|K|B)?", str(value), re.I)
    if not match:
        return None
    try:
        number = float(match.group(1))
    except ValueError:
        return None
    unit = (match.group(2) or "B").upper().rstrip("B") or "B"
    return int(number * {"B": 1, "K": 1024, "M": 1024 ** 2, "G": 1024 ** 3, "T": 1024 ** 4}[unit])
