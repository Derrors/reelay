"""STRM 播放地址签名。

.strm 文件里写的是 Reelay 的 302 跳转地址；媒体服务器播放时不会带登录态，所以跳转接口
不走会话鉴权，改用 HMAC 签名防止任意 pickcode 被遍历。密钥首次使用时生成并持久化到
``data/strm.secret``（与数据库同在数据卷内）；旧版本 ``REELAY_STRM_SECRET`` 设置的密钥在首次启动时导入该文件。
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import threading
from pathlib import Path

from . import paths

PICKCODE_RE = re.compile(r"[A-Za-z0-9]{1,64}")
_lock = threading.Lock()
_secret: bytes | None = None


SECRET_FILE = paths.STRM_SECRET


def _secret_file() -> Path:
    return SECRET_FILE


def use_secret_file(path: Path) -> None:
    """入口程序按数据目录设定密钥文件（测试传临时目录）。"""
    global SECRET_FILE, _secret
    with _lock:
        SECRET_FILE, _secret = Path(path), None


def secret() -> bytes:
    global _secret
    with _lock:
        if _secret is None:
            path = _secret_file()
            if path.is_file():
                _secret = path.read_text(encoding="utf-8").strip().encode()
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                value = secrets.token_hex(32)
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as output:
                    output.write(value)
                _secret = value.encode()
        return _secret


def sign(pickcode: str) -> str:
    return hmac.new(secret(), pickcode.encode(), hashlib.sha256).hexdigest()[:32]


def verify(pickcode: str, signature: str) -> bool:
    return bool(PICKCODE_RE.fullmatch(pickcode or "")) and hmac.compare_digest(sign(pickcode), signature or "")


def play_url(base_url: str, pickcode: str) -> str:
    return f"{base_url.rstrip('/')}/api/strm/p115/{pickcode}?sig={sign(pickcode)}"
