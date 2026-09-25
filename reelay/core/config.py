"""配置加载。

优先读 ``config.yaml``（需装 PyYAML）；读不到就用内置最小默认，保证 dry-run
无任何三方依赖也能跑通。也做一次 ``.env`` 的轻量加载（不依赖 python-dotenv）。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any


_DEFAULT_CONFIG: dict[str, Any] = {
    "save_dirs": {"电影": "0", "剧集": "0", "动漫": "0", "默认": "0"},
    "forward": {"enabled": False, "target": "", "template": "{title} ({year}) {resolution}\n{link}"},
    "organize": {
        "naming": {
            "movie": "{title} ({year})/{title} ({year}) - {resolution}.{ext}",
            "tv": "{title} ({year})/Season {season:02d}/{title} - S{season:02d}E{ep:02d}.{ext}",
        }
    },
    "defaults": {"approval_mode": "manual", "parser": "llm", "save_to": "默认"},
    "sources": [],
}


def load_env(path: str = ".env") -> None:
    """极简 .env 加载：把 KEY=VALUE 塞进 os.environ（不覆盖已存在的）。"""
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


def load_config(path: str = "config.yaml") -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        return dict(_DEFAULT_CONFIG)
    try:
        import yaml  # 延迟导入
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:
        return dict(_DEFAULT_CONFIG)
    # 浅合并默认，避免缺键
    merged = dict(_DEFAULT_CONFIG)
    merged.update(data)
    return merged
