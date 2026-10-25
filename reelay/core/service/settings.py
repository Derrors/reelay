"""配置与密钥：读取、校验与保存配置（跨进程互斥、按版本号防止覆盖他人的修改），设置与清除密钥。

配置文件会被 Web 进程（设置页）与主进程（控制 Bot 里采纳建议等）写入：保存时在文件锁下核对版本号，
期间文件已被别处改过就拒绝，不会悄悄覆盖。密钥绑定在它的地址上：改了 AI 或 TMDB 的接口地址、代理地址，
原来的密钥随之清除，不会被带到新的地址去。
"""
from __future__ import annotations

import copy
import fcntl
import logging
from contextlib import contextmanager
from typing import Any

from .. import runtime, secret_store
from ..config import SYSTEM_KEYS, config_revision, load_config, normalize_config, write_config
from .base import ConflictError, ServiceBase

log = logging.getLogger("reelay.service")

# 地址（配置块, 字段）→ 发往这个地址的密钥：地址改了，密钥要重新填写
_KEY_FOR_URL = (("llm", "base_url", "llm_api_key"), ("tmdb", "base_url", "tmdb_api_key"),
                ("proxy", "url", "proxy_password"))
MIN_PASSWORD_LENGTH = 8  # Web 登录密码的最短长度（首次设置可以在局域网里的任何设备上进行）


class SettingsMixin(ServiceBase):
    """配置与密钥。"""

    def get_config(self) -> dict[str, Any]:
        self.pipeline.reload_config()
        return copy.deepcopy(self.pipeline.cfg)

    def check_config(self, config: Any) -> dict[str, Any]:
        """按保存时的规则校验（不写入），返回规整后的配置；不合格抛 ValueError。"""
        config = normalize_config(config)
        self.pipeline.reg.validate_config(config)
        return config

    @contextmanager
    def _file_lock(self):
        """跨进程互斥修改配置文件。"""
        lock = self.config_path.with_suffix(self.config_path.suffix + ".lock")
        lock.parent.mkdir(parents=True, exist_ok=True)
        with lock.open("a+b") as fh:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)

    async def save_config(self, config: dict[str, Any], *, expected_revision: str | None = None) -> dict[str, Any]:
        """校验并原子写入配置，返回 {config, revision, cleared_secrets}。

        expected_revision 是调用方读取配置时的版本号：期间配置已被别处修改时抛 ConflictError。
        改了 AI 或 TMDB 的接口地址、代理地址时，原来的密钥一并清除（cleared_secrets 列出清除了哪些）。
        """
        config = self.check_config(config)
        # 新写入的插件设置必须对应已安装的插件（防止写错名字）；删除插件后留下的旧设置不拦着保存
        unknown = set(config["plugins"]) - self.pipeline.reg.plugin_names() - set(self.pipeline.cfg["plugins"])
        if unknown:
            raise ValueError(f"plugins 包含未安装的插件：{'、'.join(sorted(unknown))}")
        async with self._config_lock:
            with self._file_lock():
                try:
                    current = load_config(self.config_path) if self.config_path.exists() else self.pipeline.cfg
                except ValueError:
                    raise ConflictError("配置文件当前无效，先修复原始文件；不会覆盖损坏的配置") from None
                if expected_revision is not None and expected_revision != config_revision(current):
                    raise ConflictError("配置已被其他页面或进程修改，请重新加载后再保存")
                write_config(self.config_path, config)
            cleared = [key for section, field, key in _KEY_FOR_URL
                       if config[section][field] != current[section][field] and secret_store.get(key)]
            if cleared:
                secret_store.current().update({key: None for key in cleared})
                log.info("地址已更改，清除了原来的密钥：%s", "、".join(cleared))
            self.pipeline.apply_config(config)
        return {"config": config, "revision": config_revision(config), "cleared_secrets": cleared}

    async def save_config_sections(self, sections: Any, revision: str) -> dict[str, Any]:
        """设置页按块保存：只替换给出的配置块（system 下的顶层项或 plugins），按读取时的版本号防止覆盖。"""
        if not isinstance(sections, dict):
            raise ValueError("sections 必须是配置块对象")
        unknown = set(sections) - {*SYSTEM_KEYS, "plugins"}
        if unknown:
            raise ValueError(f"包含未声明的配置模块：{'、'.join(sorted(unknown))}")
        if self.pipeline.config_error:
            raise ConflictError("配置文件当前无效，先修复原始文件；不会覆盖损坏的配置")
        return await self.save_config({**self.get_config(), **sections}, expected_revision=revision)

    async def update_config(self, change) -> dict[str, Any]:
        """读取当前配置、交给 change 修改后保存（按读取时的版本号，期间被别处改过就报冲突）。"""
        if self.pipeline.config_error:
            raise ConflictError("配置文件当前无效，先修复原始文件")
        cfg = self.get_config()
        revision = config_revision(cfg)
        change(cfg)
        return (await self.save_config(cfg, expected_revision=revision))["config"]

    async def test_proxy(self, *, url: str, password: str | None = None,
                         services: list[str] | None = None) -> dict[str, Any]:
        """经代理连一次各项服务（地址用表单里的，可以还没保存）。

        已保存的代理密码只发给已保存的地址：试别的地址时用 password（「这个地址的密码」），没填就不带密码。
        """
        from .. import proxy, tmdb
        from ..config import DEFAULT_LLM_BASE
        url = (url or "").strip().rstrip("/")
        if not url:
            raise ValueError("请先填写代理地址")
        proxy.parse(url)
        services = [name for name in (proxy.SERVICES if services is None else services) if name in proxy.SERVICES]
        if not services:
            raise ValueError("至少开启一项走代理的服务")
        saved = str(runtime.get("proxy.url") or "")
        password = (password or "").strip() or (secret_store.get("proxy_password") if url == saved else None)
        results = await proxy.probe(
            proxy.with_password(url, password), services, tmdb_url=tmdb.base_url(),
            llm_url=str(runtime.get("llm.base_url") or "") or DEFAULT_LLM_BASE,
            api_id=secret_store.get("telegram_api_id"), api_hash=secret_store.get("telegram_api_hash"))
        return {"results": results}

    # ------------------------------------------------------------------ #
    # 密钥：只能设置、替换或清除，读不回原值
    # ------------------------------------------------------------------ #
    @staticmethod
    def secret_status() -> dict[str, dict[str, Any]]:
        store = secret_store.current()
        status = store.status() if store is not None else {name: False for name in secret_store.NAMES}
        return {name: {"label": label, "set": status[name]} for name, label in secret_store.NAMES.items()}

    def update_secrets(self, changes: dict[str, str | None], *, current_password: str | None = None) -> dict[str, Any]:
        """changes 里值为空表示清除。已设置 Web 密码时，修改或清除它要先输入当前密码。"""
        store = secret_store.current()
        if store is None:
            raise ConflictError("当前进程没有加载密钥文件")
        if not isinstance(changes, dict) or not changes:
            raise ValueError("没有要修改的密钥")
        password = changes.get("web_password")
        if isinstance(password, str) and password.strip() and len(password.strip()) < MIN_PASSWORD_LENGTH:
            raise ValueError(f"Web 登录密码至少 {MIN_PASSWORD_LENGTH} 个字符")
        if "web_password" in changes and store.get("web_password") and not store.check_password(current_password or ""):
            raise ConflictError("当前 Web 登录密码不正确")
        store.update(changes)
        return self.secret_status()
