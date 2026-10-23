"""受限 Jinja2 文件路径模板。

支持标准 ``{{ expression }}``、``{% if %}``/``{% for %}``、注释与少量纯函数过滤器；
禁用加载器、导入/继承、宏和任意函数调用。最终产物仍执行路径安全校验。
"""
from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Any

from jinja2 import StrictUndefined, meta, nodes
from jinja2.sandbox import ImmutableSandboxedEnvironment

# 变量名与含义同 MoviePilot 的重命名格式，可直接沿用其模板。季、集等对电影为空字符串。
FILE_INFO_VARIABLES = ("videoFormat", "videoCodec", "audioCodec", "resourceType", "effect", "edition",
                       "releaseGroup", "part")
# 暂时拿不到数据、恒为空的变量也保留，免得照搬的模板校验不过
EMPTY_VARIABLES = ("en_title", "season_year", "imdbid", "doubanid", "episode_title", "episode_date",
                   "customization")
ALLOWED_VARIABLES = {
    "title", "original_title", "name", "en_name", "year", "tmdbid",
    "season", "episode", "season_episode", "fileExt", "original_name",
    *FILE_INFO_VARIABLES, *EMPTY_VARIABLES,
}

_EDGE = r"(?<![A-Za-z0-9]){}(?![A-Za-z0-9])"
# (模式, 写进文件名的写法)，按顺序取第一个命中的
_RESOLUTION = [(r"2160[pi]|4k|uhd", "2160p"), (r"1080[pi]", "1080p"), (r"720p", "720p"), (r"576p", "576p"),
               (r"480p", "480p")]
_VIDEO_CODEC = [(r"[hx]\.?265|hevc", "H265"), (r"[hx]\.?264|avc", "H264"), (r"av1", "AV1"), (r"vp9", "VP9")]
_AUDIO_CODEC = [(r"truehd(?:[ ._-]?atmos)?", "TrueHD"), (r"dts[ ._-]?hd(?:[ ._-]?ma)?", "DTS-HD MA"),
                (r"dts[ ._-]?x", "DTS-X"), (r"dts", "DTS"), (r"e-?ac-?3|ddp|dd\+|dolby[ ._-]?digital[ ._-]?plus", "DDP"),
                (r"ac-?3|dd(?:5\.?1)?", "AC3"), (r"aac", "AAC"), (r"flac", "FLAC"), (r"opus", "Opus"),
                (r"l?pcm", "LPCM"), (r"mp3", "MP3")]
_SOURCE = [(r"(?:blu-?ray[ ._-]?)?remux", "Remux"), (r"blu-?ray|bdrip", "BluRay"), (r"web-?dl", "WEB-DL"),
           (r"web-?rip", "WEBRip"), (r"hdtv", "HDTV"), (r"dvd-?rip", "DVDRip")]
_EFFECT = [(r"hdr[ ._-]?vivid", "HDR Vivid"), (r"dolby[ ._-]?vision|dovi|dv", "DV"), (r"hdr10\+|hdr10plus", "HDR10+"),
           (r"hdr10", "HDR10"), (r"hdr", "HDR"), (r"hlg", "HLG"), (r"sdr", "SDR")]
_PART = re.compile(r"(?<![A-Za-z0-9])(part[ ._-]?(?:\d{1,2}|[ab])|cd[ ._-]?\d{1,2}|dvd\d{1,2}|dis[ck][ ._-]?\d{1,2})"
                   r"(?![A-Za-z0-9])", re.IGNORECASE)
# 末尾「-组名」之前若是这些词，那是片源写法（如 WEB-DL）而不是发布组
_NOT_GROUP = {"dl", "rip", "hd", "ma", "x", "ray"}


def _first(rules: list[tuple[str, str]], text: str) -> str:
    for pattern, label in rules:
        if re.search(_EDGE.format(f"(?:{pattern})"), text, re.IGNORECASE):
            return label
    return ""


def file_info(name: str, resolution: str | None = None) -> dict[str, str]:
    """从文件名识别清晰度、编码、片源、特效、发布组与分段（同 MoviePilot 的 videoFormat 等）；识别不出为空字符串。

    videoCodec 带位深，如「H265 10bit」。文件名里没有清晰度时用识别结果的 resolution。
    """
    text = name or ""
    stem = re.sub(r"\.[A-Za-z0-9]{1,5}$", "", text)
    depth = re.search(r"(?<![A-Za-z0-9])(8|10|12)[ ._-]?bit(?![A-Za-z0-9])", text, re.IGNORECASE)
    group = re.search(r"-([A-Za-z0-9@]+)$", stem)
    part = _PART.search(stem)
    info = {
        "videoFormat": _first(_RESOLUTION, text) or _first(_RESOLUTION, resolution or "") or (resolution or ""),
        "videoCodec": " ".join(v for v in (_first(_VIDEO_CODEC, text), f"{depth.group(1)}bit" if depth else "") if v),
        "audioCodec": _first(_AUDIO_CODEC, text),
        "resourceType": _first(_SOURCE, text),
        "effect": _first(_EFFECT, text),
        "releaseGroup": group.group(1) if group and group.group(1).lower() not in _NOT_GROUP else "",
        "part": re.sub(r"[ ._-]", "", part.group(1)).upper() if part else "",
    }
    info["edition"] = " ".join(v for v in (info["resourceType"], info["effect"]) if v)
    return info


def sample_values(kind: str = "tv") -> dict[str, Any]:
    """校验与预览模板用的示例影片（所有变量都有值）。"""
    tv = kind == "tv"
    name = "Sample.Title.2026.S01E02.2160p.WEB-DL.H265.10bit.AAC-GROUP.mkv" if tv else \
        "Sample.Title.2026.2160p.WEB-DL.H265.10bit.AAC-GROUP.mkv"
    return {
        **{key: "" for key in EMPTY_VARIABLES}, **file_info(name),
        "title": "示例标题", "original_title": "Sample Title", "name": "示例标题", "en_name": "Sample Title",
        "year": "2026", "tmdbid": "123", "season": 1 if tv else "", "episode": 2 if tv else "",
        "season_episode": "S01E02" if tv else "", "fileExt": ".mkv", "original_name": name,
    }


ALLOWED_FILTERS = {"default", "lower", "upper", "replace", "trim", "join", "length", "pad2"}
DISALLOWED_NODES = (nodes.Extends, nodes.Include, nodes.Import, nodes.FromImport,
                    nodes.Macro, nodes.CallBlock, nodes.AssignBlock, nodes.FilterBlock, nodes.Call)


def _environment() -> ImmutableSandboxedEnvironment:
    env = ImmutableSandboxedEnvironment(undefined=StrictUndefined, autoescape=False,
                                         trim_blocks=True, lstrip_blocks=True,
                                         loader=None, enable_async=False)
    safe = {name: env.filters[name] for name in ALLOWED_FILTERS - {"pad2"} if name in env.filters}
    safe["pad2"] = lambda value: f"{int(value):02d}" if str(value).isdigit() else value  # 电影的季集为空
    env.filters.clear()
    env.filters.update(safe)
    env.tests.clear()
    env.globals.clear()
    return env


def validate_template(source: str, label: str = "模板") -> None:
    if isinstance(source, str) and re.search(r"(?<!\{)\{\s*[A-Za-z_]\w*(?::[^{}]*)?\s*\}(?!\})", source):
        raise ValueError(f"{label}使用了单花括号占位符，请改用 Jinja2 写法，如 {{{{ title }}}}")
    if not isinstance(source, str) or not source.strip() or len(source) > 4000:
        raise ValueError(f"{label}必须是长度不超过4000的非空Jinja2模板")
    env = _environment()
    try:
        tree = env.parse(source)
    except Exception as exc:
        raise ValueError(f"{label} Jinja2语法错误：{exc}") from exc
    unknown = meta.find_undeclared_variables(tree) - ALLOWED_VARIABLES
    if unknown:
        raise ValueError(f"{label}包含未知变量：{', '.join(sorted(unknown))}")
    for node in tree.find_all(DISALLOWED_NODES):
        raise ValueError(f"{label}不允许使用 {node.__class__.__name__}，可使用变量、过滤器、if和for")
    for node in tree.find_all(nodes.Filter):
        if node.name not in ALLOWED_FILTERS:
            raise ValueError(f"{label}不允许过滤器 {node.name}")
    # 编译并用完整示例渲染，可同时捕获 StrictUndefined 与过滤器参数错误。
    for kind in ("movie", "tv"):
        render_path(source, sample_values(kind), label=label)


def render_path(source: str, values: dict[str, Any], *, label: str = "模板") -> str:
    try:
        rendered = _environment().from_string(source).render(**values).strip()
    except Exception as exc:
        raise ValueError(f"{label}渲染失败：{exc}") from exc
    if not rendered or len(rendered) > 2000 or "\\" in rendered or rendered.startswith("/"):
        raise ValueError(f"{label}渲染出了空路径、绝对路径或过长路径")
    parts = list(PurePosixPath(rendered).parts)
    if not parts or any(part in ("", ".", "..") for part in parts):
        raise ValueError(f"{label}渲染结果包含无效路径片段")
    if any(re.search(r'[\\:*?"<>|\x00-\x1f]', part) or len(part.encode('utf-8')) > 255 for part in parts):
        raise ValueError(f"{label}渲染结果包含非法文件名字符或超长片段")
    return "/".join(parts)
