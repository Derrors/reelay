"""启动前的迁移：旧位置的配置文件、旧结构的配置、.env 里的旧环境变量，以及自定义位置的会话与凭证文件。

配置改为「固定在 data/、在管理台修改」之后，主进程、Web 与登录工具启动时先调用 ``prepare()``：

1. data/config.yaml 不存在时，把旧位置（``REELAY_CONFIG_PATH``、项目根目录 config.yaml、旧 Docker 挂载的
   config/config.yaml）的配置复制过来，旧文件改名为 ``*.migrated``；
2. 配置还是旧结构时先备份原文件（``config.yaml.bak-日期``），再写成 system / plugins 的新结构；
3. 旧环境变量逐个导入：普通设置写入系统配置，密钥写入 secrets.json，115 的 cookie / token 写入凭证文件
   （凭证文件里已有的不覆盖），STRM 签名密钥写入 strm.secret。每个变量记下导入时的值摘要：值没变就不再
   导入，管理台改过或清除的设置不会被 .env 覆盖回去；.env 里换了新值会再导入一次；
4. 会话、凭证文件在旧变量指定的别处时复制到 data/；数据库在别处时不自动搬（WAL 文件同时搬不安全），
   报错并说明怎么移动。

两个进程同时启动时用文件锁串行；重复调用没有副作用。之后运行只读 data/ 下的文件，不再读这些环境变量
（只有 Web 的监听地址与端口仍从环境变量读取）。
"""
from __future__ import annotations

import contextlib
import hashlib
import logging
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Iterator

from . import paths
from .config import is_legacy, normalize_config, read_file, render
from .secret_store import SecretStore

log = logging.getLogger("reelay.bootstrap")


class BootstrapError(RuntimeError):
    """需要人工处理才能启动（如数据库在旧的自定义位置）。"""


def _number(value: str) -> int | float:
    number = float(value)
    return int(number) if number.is_integer() else number


# 旧环境变量 → (系统配置的段, 键, 转换)
CONFIG_ENV: dict[str, tuple[str, str, Callable[[str], Any]]] = {
    "REELAY_LLM_BASE_URL": ("llm", "base_url", str),
    "REELAY_LLM_MODEL": ("llm", "model", str),
    "REELAY_TMDB_BASE_URL": ("tmdb", "base_url", str),
    "REELAY_OWNER_ID": ("telegram", "owner_id", str),
    "REELAY_TG_BACKFILL_LIMIT": ("telegram", "backfill_limit", int),
    "REELAY_TG_BACKFILL_MAX_DAYS": ("telegram", "backfill_max_days", _number),
    "REELAY_SOURCE_ALERT_SEC": ("telegram", "alert_after_sec", int),
    "REELAY_WORKER_CONCURRENCY": ("runtime", "concurrency", int),
    "REELAY_LOG_LEVEL": ("runtime", "log_level", str.upper),
    "REELAY_115_OPEN_QPS": ("p115", "open_qps", _number),
    "REELAY_115_COOKIE_QPS": ("p115", "cookie_qps", _number),
    "REELAY_115_SHARE_QPS": ("p115", "share_qps", _number),
    "REELAY_115_RISK_COOLDOWN_SEC": ("p115", "risk_cooldown_sec", int),
}
# 旧环境变量 → 密钥名称
SECRET_ENV = {
    "REELAY_LLM_API_KEY": "llm_api_key",
    "REELAY_TMDB_API_KEY": "tmdb_api_key",
    "REELAY_TG_API_ID": "telegram_api_id",
    "REELAY_TG_API_HASH": "telegram_api_hash",
    "REELAY_CONTROL_BOT_TOKEN": "control_bot_token",
    "REELAY_WEB_PASSWORD": "web_password",
}
CREDENTIAL_ENV = ("REELAY_115_COOKIES", "REELAY_115_APP_ID", "REELAY_115_ACCESS_TOKEN", "REELAY_115_REFRESH_TOKEN")
# 旧环境变量 → 现在的固定文件名（在 data/ 下）
FILE_ENV = {
    "REELAY_TG_SESSION": "userbot.session",
    "REELAY_CONTROL_BOT_SESSION": "controlbot.session",
    "REELAY_115_OAUTH_FILE": "115_credentials.json",
    "REELAY_STRM_SECRET_FILE": "strm.secret",
}
# 已经写死、不再读取的变量：设置了只在启动日志里提示一次
IGNORED_ENV = ("REELAY_CONFIG_PATH", "REELAY_DB_PATH", "REELAY_LOG_FILE", "REELAY_PLUGIN_DIRS",
               "REELAY_LLM_RETRIES", "REELAY_LLM_RETRY_DELAY", "REELAY_115_AUTH_CHECK_SEC",
               "REELAY_SWEEP_INTERVAL_SEC", "REELAY_115_COOKIE_QR_APP", "REELAY_115_OAUTH_LIFETIME_SEC",
               "REELAY_115_OAUTH_REFRESH_ADVANCE_SEC")


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:16]


def _resolve(value: str, root: Path) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    for base in (Path.cwd(), root):
        if (base / path).exists():
            return (base / path).resolve()
    return (Path.cwd() / path).resolve()


def _same(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return False


@contextlib.contextmanager
def _locked(data_dir: Path) -> Iterator[None]:
    data_dir.mkdir(parents=True, exist_ok=True)
    try:
        import fcntl
    except ImportError:  # pragma: no cover - Windows 不加锁
        yield
        return
    with open(data_dir / ".bootstrap.lock", "w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _write_atomic(path: Path, text: str, mode: int | None = None) -> None:
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        if mode is not None:
            os.chmod(temporary, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            output.write(text)
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _move_legacy_config(config: Path, env: dict[str, str], root: Path) -> None:
    if config.exists():
        return
    candidates = [_resolve(env["REELAY_CONFIG_PATH"], root)] if env.get("REELAY_CONFIG_PATH") else []
    candidates += [root / paths.CONFIG_NAME, root / "config" / paths.CONFIG_NAME]
    for source in candidates:
        if source.is_file() and not _same(source, config):
            shutil.copy2(source, config)
            moved = source.with_name(source.name + ".migrated")
            try:
                source.rename(moved)
                log.warning("配置文件已移到 %s（原文件改名为 %s）", config, moved)
            except OSError:  # 只读挂载等：保留原文件，之后不再读取它
                log.warning("配置文件已复制到 %s；原文件 %s 之后不再读取，可以删除", config, source)
            return


def _copy_files(data_dir: Path, env: dict[str, str], root: Path) -> None:
    for name, filename in FILE_ENV.items():
        if not env.get(name):
            continue
        source, target = _resolve(env[name], root), data_dir / filename
        if source.is_file() and not target.exists() and not _same(source, target):
            shutil.copy2(source, target)
            log.warning("%s 指向的 %s 已复制到 %s", name, source, target)
    if env.get("REELAY_DB_PATH"):
        source, target = _resolve(env["REELAY_DB_PATH"], root), data_dir / paths.DATABASE_NAME
        if source.is_file() and not _same(source, target):
            if target.exists():
                log.warning("数据库固定为 %s，不再使用 REELAY_DB_PATH 指向的 %s", target, source)
            else:
                raise BootstrapError(
                    f"数据库现在固定放在 {target}，旧数据库在 {source}。请先停止 Reelay，把数据库及同名的 "
                    f"-wal、-shm 文件一起移过去（如 mv {source}* {target.parent}/，文件名改为 {paths.DATABASE_NAME}），"
                    "再从环境变量里删掉 REELAY_DB_PATH 后启动。")


def _apply_config(config: Path, env: dict[str, str], imported: dict[str, str]) -> dict[str, str]:
    """把旧结构改写成新结构并导入普通设置；返回本次导入的变量摘要。"""
    raw = read_file(config) if config.exists() else None
    pending = {name: env[name] for name in CONFIG_ENV if env.get(name) and imported.get(name) != _digest(env[name])}
    legacy = is_legacy(raw)
    if not pending and not legacy and raw is not None:
        return {}
    data = normalize_config({} if raw is None else raw)
    marks = {}
    for name, value in pending.items():
        section, key, convert = CONFIG_ENV[name]
        try:
            candidate = {**data, section: {**data[section], key: convert(value.strip())}}
            data = normalize_config(candidate)
        except (ValueError, TypeError) as exc:
            log.warning("环境变量 %s 的值无效，没有导入：%s", name, exc)
            continue
        marks[name] = _digest(value)
    if legacy and config.exists():
        backup = config.with_name(f"{config.name}.bak-{time.strftime('%Y%m%d-%H%M%S')}")
        shutil.copy2(config, backup)
        log.warning("配置文件改为 system / plugins 新结构，原文件备份为 %s", backup)
    _write_atomic(config, render(data))
    return marks


def _apply_secrets(secrets: SecretStore, env: dict[str, str], imported: dict[str, str]) -> dict[str, str]:
    changes, marks = {}, {}
    for name, secret in SECRET_ENV.items():
        value = env.get(name, "").strip()
        if value and imported.get(name) != _digest(value):
            changes[secret], marks[name] = value, _digest(value)
    if changes:
        secrets.update(changes)
    return marks


def _apply_credentials(data_dir: Path, env: dict[str, str], imported: dict[str, str]) -> dict[str, str]:
    pending = {name: env[name].strip() for name in CREDENTIAL_ENV
               if env.get(name, "").strip() and imported.get(name) != _digest(env[name].strip())}
    if not pending:
        return {}
    from .auth115 import CredentialStore
    store = CredentialStore(data_dir / "115_credentials.json")
    if pending.get("REELAY_115_COOKIES") and not store.cookie_from_file:
        store.set_cookies(pending["REELAY_115_COOKIES"])
        log.warning("REELAY_115_COOKIES 已导入 115 凭证文件")
    access, refresh = pending.get("REELAY_115_ACCESS_TOKEN", ""), pending.get("REELAY_115_REFRESH_TOKEN", "")
    if (access or refresh) and not store.has_oauth:
        app_id = pending.get("REELAY_115_APP_ID")
        store.save_tokens(access, refresh, int(app_id) if app_id and app_id.isdecimal() else None)
        log.warning("REELAY_115_ACCESS_TOKEN / REFRESH_TOKEN 已导入 115 凭证文件")
    return {name: _digest(value) for name, value in pending.items()}


def _apply_strm(data_dir: Path, env: dict[str, str], imported: dict[str, str]) -> dict[str, str]:
    value = env.get("REELAY_STRM_SECRET", "").strip()
    if not value or imported.get("REELAY_STRM_SECRET") == _digest(value):
        return {}
    target = data_dir / "strm.secret"
    if not target.exists():  # 已生成过的密钥不换：换了媒体服务器里已有的 .strm 会失效
        _write_atomic(target, value, 0o600)
        log.warning("REELAY_STRM_SECRET 已写入 %s", target)
    return {"REELAY_STRM_SECRET": _digest(value)}


def prepare(data_dir: str | Path | None = None, *, legacy_locations: bool | None = None,
            env: dict[str, str] | None = None, project_root: str | Path | None = None) -> None:
    """启动前调用。legacy_locations 默认只在使用默认 data/ 时查找旧位置的配置；旧位置在 project_root（默认项目根目录）下。
    测试必须传临时的 project_root：否则会把开发机上真实的 config.yaml 移走。"""
    data_dir = Path(data_dir or paths.DATA)
    root = Path(project_root or paths.PROJECT_ROOT)
    env = dict(os.environ if env is None else env)
    if legacy_locations is None:
        legacy_locations = _same(data_dir, paths.DATA)
    config = data_dir / paths.CONFIG_NAME
    with _locked(data_dir):
        if legacy_locations:
            _move_legacy_config(config, env, root)
            _copy_files(data_dir, env, root)
        secrets = SecretStore(data_dir / paths.SECRETS_NAME)
        imported = secrets.imported()
        marks: dict[str, str] = {}
        try:
            marks.update(_apply_config(config, env, imported))
        except ValueError as exc:
            # 配置文件本身无效：不覆盖它，修好后下次启动再导入；Web 原文编辑可以修复
            log.error("配置文件 %s 无效，暂不迁移与导入环境变量：%s", config, exc)
        marks.update(_apply_secrets(secrets, env, imported))
        marks.update(_apply_credentials(data_dir, env, imported))
        marks.update(_apply_strm(data_dir, env, imported))
        if marks:
            secrets.mark_imported(marks)
            log.warning("已从环境变量导入：%s。之后以管理台的设置为准，可以从 .env 中删掉这些变量",
                        "、".join(sorted(marks)))
        ignored = [name for name in IGNORED_ENV if env.get(name)]
        if ignored:
            log.warning("这些环境变量已不再使用（路径与参数固定）：%s", "、".join(ignored))
