"""密钥：AI 接口 / TMDB 的 key、Telegram API、控制 Bot Token、Web 登录密码与 MCP 访问令牌。

存放在 data/secrets.json（权限 600），不进配置文件，配置原文与接口返回里都看不到；管理台只能
设置、替换或清除，读不回原值。Web 登录密码只存 PBKDF2 摘要。

文件按修改时间热加载：Web 进程改了，主进程下次用到时就是新值。只有入口程序（主进程、Web、
登录与评测工具）会加载密钥文件；没有加载时一律视为未配置，测试不会读到本机的真实密钥。
"""
from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import os
import secrets
import tempfile
import threading
from pathlib import Path
from typing import Any, Iterator

# 名称 → 管理台显示的名称
NAMES = {
    "llm_api_key": "AI 接口密钥",
    "tmdb_api_key": "TMDB API Key",
    "telegram_api_id": "Telegram API ID",
    "telegram_api_hash": "Telegram API Hash",
    "control_bot_token": "控制 Bot Token",
    "web_password": "Web 登录密码",
    "mcp_token": "MCP 访问令牌（读写）",
    "mcp_readonly_token": "MCP 访问令牌（只读）",
}
# 外部 Agent 凭它们经 Web 的 /mcp 操作 Reelay，至少 24 个字符，两个不能相同。
TOKEN_NAMES = ("mcp_token", "mcp_readonly_token")
MIN_TOKEN_LENGTH = 24
_PBKDF2_ROUNDS = 200_000


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), _PBKDF2_ROUNDS).hex()
    return f"pbkdf2_sha256${_PBKDF2_ROUNDS}${salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, rounds, salt, digest = stored.split("$")
        if scheme != "pbkdf2_sha256":
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(rounds)).hex()
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, digest)


class SecretStore:
    """path 为 None 时只在内存中保存（测试用）。"""

    def __init__(self, path: str | Path | None, values: dict[str, str] | None = None) -> None:
        self.path = Path(path) if path is not None else None
        self._lock = threading.RLock()
        self._data: dict[str, Any] = {"values": dict(values or {})}
        self._mtime: float | None = None
        self._reload()

    def _reload(self) -> None:
        if self.path is None:
            return
        try:
            mtime = self.path.stat().st_mtime
        except FileNotFoundError:
            self._data, self._mtime = {"values": {}}, None
            return
        if mtime == self._mtime:
            return
        data = json.loads(self.path.read_text(encoding="utf-8") or "{}")
        if not isinstance(data, dict):
            raise ValueError(f"{self.path} 格式无效")
        self._data = {"values": {k: v for k, v in (data.get("values") or {}).items() if isinstance(v, str) and v}}
        self._mtime = mtime

    def _save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=self.path.parent)
        temporary = Path(name)
        try:
            os.chmod(temporary, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as output:
                json.dump(self._data, output, ensure_ascii=False, indent=2)
                output.flush()
                os.fsync(output.fileno())
            temporary.replace(self.path)
            self._mtime = self.path.stat().st_mtime
        finally:
            temporary.unlink(missing_ok=True)

    def get(self, name: str) -> str | None:
        with self._lock:
            self._reload()
            return self._data["values"].get(name) or None

    def status(self) -> dict[str, bool]:
        with self._lock:
            self._reload()
            return {name: bool(self._data["values"].get(name)) for name in NAMES}

    def update(self, changes: dict[str, str | None]) -> None:
        """设置或清除（值为空）；web_password 传明文，保存摘要。全部校验通过才一起生效，有一项不合格就都不改。"""
        unknown = set(changes) - set(NAMES)
        if unknown:
            raise ValueError(f"未知的密钥：{'、'.join(sorted(unknown))}")
        with self._lock:
            self._reload()
            values = dict(self._data["values"])
            for name, value in changes.items():
                if value is not None and type(value) is not str:
                    raise ValueError(f"{NAMES[name]}必须是字符串")
                value = (value or "").strip()
                if name == "telegram_api_id" and value and not value.isdecimal():
                    raise ValueError("Telegram API ID 必须是数字")
                if name in TOKEN_NAMES and value:
                    if len(value) < MIN_TOKEN_LENGTH:
                        raise ValueError(f"{NAMES[name]}至少 {MIN_TOKEN_LENGTH} 个字符")
                    other = next(item for item in TOKEN_NAMES if item != name)
                    paired = (changes[other] or "").strip() if other in changes else values.get(other)
                    if value == paired:
                        raise ValueError("读写与只读的 MCP 访问令牌不能相同")
                if value:
                    values[name] = hash_password(value) if name == "web_password" else value
                else:
                    values.pop(name, None)
            previous, self._data = self._data, {**self._data, "values": values}
            try:
                self._save()
            except BaseException:
                self._data = previous
                raise

    def check_password(self, password: str) -> bool:
        stored = self.get("web_password")
        return bool(stored) and verify_password(password, stored)


_current: SecretStore | None = None


def use(store: SecretStore | str | Path | None) -> SecretStore | None:
    """设定当前进程使用的密钥存储（入口程序调用）；传 None 视为未配置任何密钥。"""
    global _current
    _current = store if isinstance(store, SecretStore) or store is None else SecretStore(store)
    return _current


def current() -> SecretStore | None:
    return _current


def get(name: str) -> str | None:
    return _current.get(name) if _current is not None else None


@contextlib.contextmanager
def override(**values: str) -> Iterator[SecretStore]:
    """测试用：临时换成只在内存中的密钥。"""
    previous = _current
    store = use(SecretStore(None, {k: v for k, v in values.items() if v}))
    try:
        yield store
    finally:
        use(previous)
