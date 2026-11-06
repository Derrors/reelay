"""源码部署的一键升级：把 git 克隆的源码快进到 GitHub 上最新正式版的标签，按需更新依赖、重新构建管理台，
再让主进程与管理服务原地重启。

Docker 部署不在这里升级（换镜像才是升级），管理台只提示新版本与升级命令。

能否升级由 ``blocker`` 逐项检查，不满足时给出原因：
- 不在 Docker 里，Linux / macOS；
- 管理服务由 ``python -m reelay.web`` / ``reelay-web`` 启动（退出后才能原地重启，见 web/app.py 的 main）；
- 项目目录是 git 克隆，已跟踪的文件没有本地改动。

步骤在后台线程里执行，进度存在 ``plugin_state`` 的 ``update_check/upgrade``，命令输出写 ``data/upgrade.log``：
1. ``git fetch --tags origin``（GitHub 走代理时 git 也走同一个代理），要求当前提交是 ``v<版本>`` 的祖先（只快进）；
2. 快进到这个标签；
3. ``pyproject.toml`` / ``constraints.txt`` 有变化时 ``pip install -c constraints.txt -e ".[all]"``；
4. ``web/`` 有变化时 ``npm ci``（依赖清单变了才装）并构建到 ``web/dist.next``，成功后替换 ``web/dist``；
5. 请主进程重启（它轮询到后按 SIGTERM 的流程退出，再原地重启），然后重启管理服务。

第 3、4 步失败时代码退回升级前的提交，不重启。新的管理服务启动时由 ``settle`` 核对版本，记下升级结果。
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

from .. import __version__
from . import paths, proxy, updates

log = logging.getLogger("reelay.upgrade")

STATE = "upgrade"
RESTART = "restart"
LOG_NAME = "upgrade.log"
LOG_TAIL = 40
TIMEOUTS = {"fetch": 180, "checkout": 60, "pip": 1200, "npm": 1200}
STEPS = {"fetch": "拉取新版本", "checkout": "切换代码", "pip": "更新 Python 依赖", "npm": "构建管理台", "restart": "重启"}
_LOCK = threading.Lock()

# 管理服务的 main() 设置：请服务退出并原地重启。为 None 时（如被其他方式托管）不能一键升级。
restart_hook: Callable[[], None] | None = None


class UpgradeError(Exception):
    """升级不能开始或失败的原因（中文，直接展示给用户）。"""


def _git(root: Path, *args: str, timeout: float = 30) -> str:
    result = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, timeout=timeout,
                            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
    if result.returncode:
        raise UpgradeError(f"git {args[0]} 失败：{(result.stderr or result.stdout).strip()[:200]}")
    return result.stdout.strip()


def blocker(root: Path | None = None) -> str | None:
    """不能一键升级的原因；可以时为 None。"""
    root = root or paths.PROJECT_ROOT
    if updates.in_docker():
        return "Docker 部署请拉取新镜像升级"
    if os.name != "posix":
        return "只支持在 Linux / macOS 上一键升级"
    if restart_hook is None:
        return "管理服务不是用 python -m reelay.web 启动的，升级后无法自动重启"
    if not (root / ".git").exists():
        return "项目目录不是 git 克隆的源码，无法自动升级"
    if not shutil.which("git"):
        return "没有找到 git"
    try:
        dirty = _git(root, "status", "--porcelain", "--untracked-files=no")
    except (UpgradeError, OSError, subprocess.TimeoutExpired):
        return "读取 git 状态失败"
    if dirty:
        return "源码有未提交的改动，请先提交或还原后再升级"
    return None


def status(store, data_dir: Path, root: Path | None = None) -> dict[str, Any]:
    """能否一键升级，以及最近一次升级的进度；失败时带上升级日志的末尾。"""
    reason = blocker(root)
    record = store.state_get(updates.NAME, STATE)
    if record and record.get("status") == "failed":
        record = {**record, "log": _tail(data_dir / LOG_NAME)}
    return {"supported": reason is None, "reason": reason, "state": record}


def _tail(path: Path) -> str:
    try:
        return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-LOG_TAIL:])
    except OSError:
        return ""


def start(store, target: str, *, data_dir: Path, root: Path | None = None) -> dict[str, Any]:
    """开始升级到 target（GitHub 上的最新正式版），在后台执行；返回升级记录。"""
    root = root or paths.PROJECT_ROOT
    with _LOCK:
        reason = blocker(root)
        if reason:
            raise UpgradeError(reason)
        current = store.state_get(updates.NAME, STATE) or {}
        if current.get("status") in ("running", "restarting"):
            raise UpgradeError("正在升级，请稍候")
        record = {"status": "running", "target": target, "from": __version__, "step": "fetch",
                  "started_at": time.time(), "finished_at": None, "error": None}
        store.state_set(updates.NAME, STATE, record)
    via = proxy.url_for("github")  # 配置在调用方的作用域里，后台线程里读不到
    threading.Thread(target=_run, args=(store, record, root, data_dir / LOG_NAME, via),
                     name="reelay-upgrade", daemon=True).start()
    return record


def _run(store, record: dict, root: Path, log_path: Path, via: str | None) -> None:
    def save(**change: Any) -> None:
        record.update(change)
        store.state_set(updates.NAME, STATE, record)

    old, branch = None, False
    with log_path.open("w", encoding="utf-8") as out:
        def run(args: list[str], cwd: Path, env: dict | None = None) -> None:
            out.write(f"\n$ {' '.join(args)}\n")
            out.flush()
            result = subprocess.run(args, cwd=cwd, stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                    timeout=TIMEOUTS[record["step"]], env=env)
            if result.returncode:
                raise UpgradeError(f"{STEPS[record['step']]}失败（退出码 {result.returncode}），详情见升级日志")

        try:
            tag = f"v{record['target']}"
            env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
            if via:
                env.update(https_proxy=via, http_proxy=via, HTTPS_PROXY=via, HTTP_PROXY=via)
            run(["git", "fetch", "--tags", "origin"], root, env)
            try:
                commit = _git(root, "rev-parse", "--verify", f"refs/tags/{tag}^{{commit}}")
            except UpgradeError:
                raise UpgradeError(f"远端没有 {tag} 标签") from None
            old = _git(root, "rev-parse", "HEAD")
            if subprocess.run(["git", "merge-base", "--is-ancestor", old, commit], cwd=root).returncode:
                raise UpgradeError(f"当前代码不在 {tag} 之前（有本地提交或在其他分支），请手动升级")
            changed = _git(root, "diff", "--name-only", old, commit).splitlines()
            web = root / "web"
            install_web = (bool({"web/package.json", "web/package-lock.json"} & set(changed))
                           or not (web / "node_modules").is_dir())
            build_web = (install_web or any(name.startswith("web/") for name in changed)
                         or not (web / "dist" / "index.html").is_file())
            npm = shutil.which("npm")
            if build_web and not npm:
                raise UpgradeError("新版本的管理台需要重新构建，但没有找到 npm")

            save(step="checkout")
            branch = subprocess.run(["git", "symbolic-ref", "-q", "HEAD"], cwd=root,
                                    capture_output=True).returncode == 0
            run(["git", "merge", "--ff-only", commit] if branch else ["git", "checkout", "--detach", commit], root)
            if {"pyproject.toml", "constraints.txt"} & set(changed):
                save(step="pip")
                run([sys.executable, "-m", "pip", "install", "-c", "constraints.txt", "-e", ".[all]"], root)
            if build_web:
                save(step="npm")
                if install_web:
                    run([npm, "ci"], web)
                build_env = dict(os.environ)
                base = built_base(web / "dist" / "index.html")
                if base and not build_env.get("VITE_BASE_PATH"):
                    build_env["VITE_BASE_PATH"] = base  # 沿用上次构建的子路径（反向代理在子路径下时）
                shutil.rmtree(web / "dist.next", ignore_errors=True)
                run([npm, "run", "build", "--", "--outDir", "dist.next", "--emptyOutDir"], web, build_env)
                swap_dist(web)
        except Exception as exc:  # noqa: BLE001 - 原因交给用户；不重启，旧进程照常运行
            error = _describe(exc, record["step"])
            if old and record["step"] != "fetch":
                try:
                    _git(root, *(("reset", "--keep", old) if branch else ("checkout", "--detach", old)), timeout=60)
                    error += f"；代码已退回 v{record['from']}"
                    if record["step"] == "pip":
                        error += "，Python 依赖可能已部分更新"
                except Exception:  # noqa: BLE001
                    error += "；代码没能自动退回，请在项目目录检查 git 状态"
            out.write(f"\n升级失败：{error}\n")
            log.error("升级到 %s 失败：%s", record["target"], error)
            save(status="failed", error=error, finished_at=time.time())
            return
        out.write("\n准备重启主进程与管理服务\n")
    log.info("已更新到 %s 的代码，重启主进程与管理服务", record["target"])
    save(step="restart", status="restarting")
    request_restart(store)


def _describe(exc: Exception, step: str) -> str:
    if isinstance(exc, UpgradeError):
        return str(exc)
    if isinstance(exc, subprocess.TimeoutExpired):
        return f"{STEPS[step]}超时，详情见升级日志"
    return f"{STEPS[step]}出错（{type(exc).__name__}），详情见升级日志"


def built_base(index: Path) -> str | None:
    """上次构建的子路径（VITE_BASE_PATH），从 index.html 引用的资源地址里看出来；部署在根路径时为 None。"""
    try:
        match = re.search(r'(?:src|href)="([^"]*/)assets/', index.read_text(encoding="utf-8"))
    except OSError:
        return None
    return match[1] if match and match[1] != "/" else None


def swap_dist(web: Path) -> None:
    """新构建的 dist.next 替换 dist；旧的先挪开再删，替换前出错时 dist 保持不变。"""
    current, fresh, stale = web / "dist", web / "dist.next", web / "dist.old"
    shutil.rmtree(stale, ignore_errors=True)
    if current.exists():
        current.rename(stale)
    fresh.rename(current)
    shutil.rmtree(stale, ignore_errors=True)


def request_restart(store) -> None:
    """请主进程（下次轮询时）与管理服务原地重启。"""
    store.state_set(updates.NAME, RESTART, {"requested_at": time.time()})
    if restart_hook is not None:
        restart_hook()


def restart_requested(store, since: float) -> bool:
    """主进程用：since（本进程启动时间）之后有没有请求重启。"""
    request = store.state_get(updates.NAME, RESTART) or {}
    return float(request.get("requested_at") or 0) > since


def settle(store) -> None:
    """管理服务启动时核对上一次升级：重启后版本对得上记为完成；升级中途服务退出了记为失败。"""
    record = store.state_get(updates.NAME, STATE)
    if not record or record.get("status") not in ("running", "restarting"):
        return
    if record["status"] == "running":
        record.update(status="failed", error="升级过程中管理服务退出了，请查看升级日志后重试")
    elif updates.parse_version(__version__) == updates.parse_version(record.get("target")):
        record.update(status="done", error=None)
    else:
        record.update(status="failed", error=f"重启后仍是 v{__version__}，请查看升级日志")
    record["finished_at"] = time.time()
    store.state_set(updates.NAME, STATE, record)
