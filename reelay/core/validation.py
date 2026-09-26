"""外部输入契约：不做 bool/string/number 的隐式强制转换。"""

from __future__ import annotations

import math
import re
from typing import Any


MEDIA_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "is_media": {"type": "boolean"},
        "is_ad": {"type": "boolean"},
        "confidence": {"type": "number"},
        "media_type": {"type": ["string", "null"], "enum": ["movie", "tv", "anime", None]},
        "title": {"type": ["string", "null"]},
        "year": {"type": ["integer", "null"]},
        "season": {"type": ["integer", "null"]},
        "episodes": {"type": "array", "items": {"type": "integer"}},
        "genres": {"type": "array", "items": {"type": "string"}},
        "resolution": {"type": ["string", "null"]},
        "size": {"type": ["string", "null"]},
        "reason": {"type": ["string", "null"]},
    },
    "additionalProperties": False,
}
MEDIA_SCHEMA["required"] = list(MEDIA_SCHEMA["properties"])


def validate_resource(data: Any, *, complete: bool = False) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError("解析结果必须是对象")
    allowed = set(MEDIA_SCHEMA["properties"]) | {"share_code", "receive_code", "raw_fields"}
    if complete:
        allowed = set(MEDIA_SCHEMA["properties"])
        if set(data) != allowed:
            raise ValueError("AI 解析结果字段缺失或包含未知字段")
    if set(data) - allowed:
        raise ValueError("解析结果包含未知字段")
    for key, value in data.items():
        if key in {"is_media", "is_ad"}:
            valid = type(value) is bool
        elif key == "confidence":
            valid = type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 1
        elif key in {"year", "season"}:
            lo, hi = (1800, 2200) if key == "year" else (0, 999)
            valid = value is None or (type(value) is int and lo <= value <= hi)
        elif key == "media_type":
            valid = value is None or (type(value) is str and value in {"movie", "tv", "anime"})
        elif key == "episodes":
            valid = (type(value) is list and len(value) <= 1000
                     and all(type(ep) is int and 1 <= ep <= 9999 for ep in value)
                     and len(set(value)) == len(value))
        elif key == "genres":
            valid = (type(value) is list and len(value) <= 30
                     and all(type(g) is str and 0 < len(g.strip()) <= 100 for g in value))
        elif key == "raw_fields":
            valid = isinstance(value, dict)
        elif key in {"share_code", "receive_code"}:
            pattern = r"[A-Za-z0-9]{1,100}" if key == "share_code" else r"[A-Za-z0-9]{4}"
            valid = value is None or (type(value) is str and re.fullmatch(pattern, value) is not None)
        else:
            valid = value is None or (type(value) is str and len(value) <= 2000)
        if not valid:
            raise ValueError(f"解析字段 {key} 类型或范围无效")
    if complete and data["is_media"] and not data["is_ad"]:
        if not data["title"] or not data["title"].strip() or data["media_type"] is None:
            raise ValueError("影视资源必须提供标题和媒体类型")
    return data
