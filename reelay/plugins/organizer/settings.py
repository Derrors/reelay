"""媒体库整理的设置（``plugins.organizer``）：默认值与校验。

规范化配置时插件还没有加载，``core/config.py`` 按目录找到内置插件的 ``settings.py`` 调用这里：

- ``DEFAULTS``：未配置时补齐的默认设置（与用户设置深合并）；
- ``normalize(section)``：校验并就地规范化。

二级分类规则不在配置里：随插件发布（同目录的 ``category.yaml``），管理台只读。
"""
from __future__ import annotations

from typing import Any

from .category import DEFAULT_TYPE_DIRS, LAYOUTS, validate_type_dirs
from .naming import validate_template

# 变量同 MoviePilot。目录名带 [tmdbid=xxx]（Jellyfin/Emby 均可识别）：匹配到 TMDB 时刮削不再依赖标题猜测；
# 文件名带清晰度与编码，如「死刑将至 - S01E25 - 第 25 集 - 2160p H265 10bit AAC.mp4」。
_FOLDER = "{{title}}{% if year %} ({{year}}){% endif %}{% if tmdbid %} [tmdbid={{tmdbid}}]{% endif %}"
_MEDIA_INFO = ("{% if videoFormat %} - {{videoFormat}}{% endif %}{% if videoCodec %} {{videoCodec}}{% endif %}"
               "{% if audioCodec %} {{audioCodec}}{% endif %}")
DEFAULT_NAMING = {
    "movie": _FOLDER + "/{{title}}{% if year %} ({{year}}){% endif %}{% if part %}-{{part}}{% endif %}"
             + _MEDIA_INFO + "{{fileExt}}",
    "tv": _FOLDER + "/Season {{season|pad2}}/{{title}} - {{season_episode}}{% if part %}-{{part}}{% endif %}"
          " - 第 {{episode}} 集" + _MEDIA_INFO + "{{fileExt}}",
}
CONFLICT_STRATEGIES = {"manual", "skip", "suffix", "upgrade"}

DEFAULTS: dict[str, Any] = {
    "naming": dict(DEFAULT_NAMING),
    "conflict": "manual",
    "layout": "category",
    "type_dirs": dict(DEFAULT_TYPE_DIRS),
}


def normalize(section: dict) -> None:
    if section.get("conflict") not in CONFLICT_STRATEGIES:
        raise ValueError("plugins.organizer.conflict 必须为 manual、skip、suffix 或 upgrade")
    naming = section.get("naming")
    if not isinstance(naming, dict) or any(type(v) is not str or not v for v in naming.values()):
        raise ValueError("plugins.organizer.naming 必须包含非空模板")
    if not {"movie", "tv"} <= set(naming):
        raise ValueError("plugins.organizer.naming 必须包含 movie 和 tv Jinja2 模板")
    for kind, template in naming.items():
        validate_template(template, f"plugins.organizer.naming.{kind}")
    if section.get("layout") not in LAYOUTS:
        raise ValueError("plugins.organizer.layout 必须为 category（二级分类）、media_type（按媒体类型）或 none（不分类）")
    validate_type_dirs(section["type_dirs"])
