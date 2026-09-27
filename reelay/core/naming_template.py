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

ALLOWED_VARIABLES = {
    "title", "year", "resolution", "ext", "season", "episode", "ep",
    "media_type", "genre_ids", "origin_country", "tmdb_id", "original_name",
}
ALLOWED_FILTERS = {"default", "lower", "upper", "replace", "trim", "join", "length", "pad2"}
DISALLOWED_NODES = (nodes.Extends, nodes.Include, nodes.Import, nodes.FromImport,
                    nodes.Macro, nodes.CallBlock, nodes.AssignBlock, nodes.FilterBlock, nodes.Call)


def _environment() -> ImmutableSandboxedEnvironment:
    env = ImmutableSandboxedEnvironment(undefined=StrictUndefined, autoescape=False,
                                         trim_blocks=True, lstrip_blocks=True,
                                         loader=None, enable_async=False)
    safe = {name: env.filters[name] for name in ALLOWED_FILTERS - {"pad2"} if name in env.filters}
    safe["pad2"] = lambda value: f"{int(value):02d}"
    env.filters.clear(); env.filters.update(safe)
    env.tests.clear(); env.globals.clear()
    return env


def migrate_legacy_template(source: str) -> str:
    """只迁移历史内置 format 占位符；未知单花括号占位符直接拒绝。"""
    if "{{" in source or "{%" in source or "{#" in source:
        return source
    source = re.sub(r"\{(title|year|resolution|ext)\}", r"{{ \1 }}", source)
    source = source.replace("{season:02d}", "{{ season|pad2 }}").replace(
        "{ep:02d}", "{{ episode|pad2 }}")
    if re.search(r"(?<!\{)\{[^{}]+\}(?!\})", source):
        raise ValueError("旧模板包含未知占位符，请改用 {{ variable }} Jinja2 语法")
    return source


def validate_template(source: str, label: str = "模板") -> None:
    source = migrate_legacy_template(source)
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
    render_path(source, {
        "title": "示例标题", "year": 2026, "resolution": "4K", "ext": "mkv",
        "season": 1, "episode": 2, "ep": 2, "media_type": "tv",
        "genre_ids": [16], "origin_country": ["CN"], "tmdb_id": 1,
        "original_name": "example.S01E02.mkv",
    }, label=label)


def render_path(source: str, values: dict[str, Any], *, label: str = "模板") -> str:
    source = migrate_legacy_template(source)
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
