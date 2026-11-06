"""检查更新：版本比较、查询结果缓存、Bot 提醒与管理台接口（不联网）。"""
from __future__ import annotations

import asyncio
import io
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from reelay import __version__
from reelay.core import updates, upgrade
from reelay.core.contracts import PipelineContext, RawMessage
from reelay.plugins.update_check import render

from tests.fakes import PipelineCase, ServiceCase, WebCase

RELEASE = {"tag_name": "v99.0.0", "html_url": f"{updates.RELEASES}/tag/v99.0.0", "published_at": "2026-11-06T00:00:00Z",
           "body": "### 修复\n\n- 修复 <广告> 误判。\n- 偏好判断更稳。"}
LATEST = {"version": "99.0.0", "url": RELEASE["html_url"], "notes": RELEASE["body"], "published_at": RELEASE["published_at"]}


class TestVersions(unittest.TestCase):
    def test_only_release_versions_are_compared(self):
        self.assertEqual(updates.parse_version("v1.5.2"), (1, 5, 2))
        for text in ("1.6.0-rc.1", "1.6", "latest", None):
            self.assertIsNone(updates.parse_version(text), text)
        self.assertTrue(updates.newer("1.10.0", "1.9.9"))
        self.assertFalse(updates.newer("1.5.2", "1.5.2"))
        self.assertFalse(updates.newer("1.5.1", "1.5.2"))  # 本地版本更新（开发中）不提示
        self.assertFalse(updates.newer(None, "1.5.2"))

    def test_pinned_version_comes_from_the_deployment(self):
        for value, expected in (("1.5.2", "1.5.2"), ("latest", None), ("", None)):
            with patch.dict(os.environ, {"REELAY_VERSION": value}):
                self.assertEqual(updates.pinned_version(), expected, value)

    def test_release_is_read_from_github(self):
        def reply(body):
            return patch("urllib.request.urlopen", return_value=io.BytesIO(json.dumps(body).encode()))
        with reply(RELEASE):
            self.assertEqual(updates.fetch_latest(), LATEST)
        with reply({**RELEASE, "html_url": "https://evil.example/x"}):  # 链接只认这个仓库的发布页
            self.assertEqual(updates.fetch_latest()["url"], updates.RELEASES)
        with reply({**RELEASE, "tag_name": "nightly"}), self.assertRaises(ValueError):
            updates.fetch_latest()

    def test_errors_never_carry_the_raw_exception(self):
        limited = urllib.error.HTTPError(updates.LATEST_API, 403, "rate limited", None, None)
        self.assertIn("限制了查询频率", updates.describe(limited))
        self.assertNotIn("secret", updates.describe(OSError("http://me:secret@proxy")))


class TestCheck(PipelineCase):
    def test_result_is_cached_and_failures_keep_the_last_release(self):
        t = 1_800_000_000
        with patch.object(updates, "fetch_latest", return_value=LATEST) as fetch:
            result = updates.check(self.store, now=t)
            self.assertEqual((result["latest"], result["available"], result["current"]), ("99.0.0", True, __version__))
            updates.check(self.store, now=t + updates.CHECK_INTERVAL - 1)
            self.assertEqual(fetch.call_count, 1)
            updates.check(self.store, now=t + 1, force=True)  # 管理台「检查更新」
            self.assertEqual(fetch.call_count, 2)
        with patch.object(updates, "fetch_latest", side_effect=TimeoutError()) as fetch:
            result = updates.check(self.store, force=True, now=t + 2)
            self.assertEqual((result["latest"], result["available"]), ("99.0.0", True))
            self.assertIn("超时", result["error"])
            updates.check(self.store, now=t + 2 + updates.RETRY_INTERVAL - 1)
            self.assertEqual(fetch.call_count, 1)
            updates.check(self.store, now=t + 2 + updates.RETRY_INTERVAL)  # 失败后一小时再试，不等六小时
            self.assertEqual(fetch.call_count, 2)

    def test_each_new_version_is_announced_once(self):
        result = updates.status({"release": LATEST})
        self.assertTrue(updates.take_notice(self.store, result))
        self.assertFalse(updates.take_notice(self.store, result))
        self.assertTrue(updates.take_notice(self.store, updates.status({"release": {**LATEST, "version": "99.1.0"}})))
        self.assertFalse(updates.take_notice(self.store, updates.status({"release": {**LATEST, "version": "0.0.1"}})))


class TestNotice(unittest.TestCase):
    def result(self, **change):
        return {**updates.status({"release": LATEST}), "docker": True, "pinned": None, **change}

    def test_notice_lists_the_changes_and_how_to_upgrade(self):
        text = render(self.result())
        self.assertTrue(text.startswith(f"🆕 <b>Reelay 99.0.0 已发布</b>\n当前版本 {__version__}\n\n"
                                        "• 修复 &lt;广告&gt; 误判。\n• 偏好判断更稳。"))
        self.assertIn("执行 <code>docker compose pull &amp;&amp; docker compose up -d</code>", text)
        self.assertIn(f'<a href="{RELEASE["html_url"]}">查看更新说明</a>', text)
        self.assertIn("先把 .env 里的 REELAY_VERSION 改成 99.0.0", render(self.result(pinned="1.5.2")))
        self.assertIn("拉取最新代码后重新安装并重启", render(self.result(docker=False)))


class TestUpdatePlugin(ServiceCase):
    def setUp(self):
        super().setUp()
        self.plugin = next(x for x in self.registry.listeners["on_daily"] if x.name == updates.NAME)

    async def daily(self) -> list[str]:
        ctx = PipelineContext(raw=RawMessage(source="system", msg_id="daily", text=""), task_id="daily-x")
        with patch.object(updates, "fetch_latest", return_value=LATEST), self.pl.scope(self.pl.cfg):
            await self.plugin.handle(ctx)
        return [json.loads(row[0])["text"] for row in self.store._conn.execute(
            "SELECT snapshot FROM outbox WHERE kind='notice' ORDER BY id")]

    async def test_bot_announces_a_new_version_once(self):
        self.assertEqual(len(await self.daily()), 1)
        self.store.state_set(updates.NAME, "latest", {})  # 第二天重新查询，仍是同一个版本
        self.assertEqual(len(await self.daily()), 1)

    async def test_bot_notice_can_be_turned_off(self):
        self.configure({**self.CFG, "plugins": {updates.NAME: {"notify": False}}})
        self.assertEqual(await self.daily(), [])
        self.assertEqual(self.store.state_get(updates.NAME, "latest")["release"]["version"], "99.0.0")  # 仍会检查

    async def test_turning_the_plugin_off_stops_checking(self):
        self.configure({**self.CFG, "plugins": {updates.NAME: {"enabled": False}}})
        with patch.object(updates, "fetch_latest") as fetch:
            result = await self.service.update_status(force=True)
        fetch.assert_not_called()
        self.assertEqual((result["enabled"], result["available"], result["current"]), (False, False, __version__))


class TestUpdateApi(WebCase):
    def test_console_reads_and_rechecks_the_latest_release(self):
        with patch.object(updates, "fetch_latest", return_value=LATEST) as fetch:
            data = self.client.get("/api/update").json()
            self.assertEqual((data["enabled"], data["latest"], data["available"]), (True, "99.0.0", True))
            self.client.get("/api/update")
            self.assertEqual(fetch.call_count, 1)  # 六小时内用缓存
            self.client.post("/api/update/check")
            self.assertEqual(fetch.call_count, 2)


PATH = os.environ.get("PATH", "")


class TestSourceUpgrade(PipelineCase):
    """源码部署的一键升级：在临时目录里用本地仓库当远端，实际跑 git。"""

    def setUp(self):
        super().setUp()
        env = patch.dict(os.environ, {"PATH": PATH, "HOME": str(self.root)})  # isolate() 清空了环境变量
        env.start()
        self.addCleanup(env.stop)
        self.restarts = []
        for patcher in (patch.object(upgrade, "restart_hook", lambda: self.restarts.append(1)),
                        patch.object(updates, "in_docker", return_value=False)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.remote = self.root / "remote"
        self.remote.mkdir()
        self.git(self.remote, "init", "-q", "-b", "main")
        (self.remote / "reelay.txt").write_text("1.5.2\n", encoding="utf-8")
        (self.remote / "web").mkdir()
        (self.remote / "web" / "app.ts").write_text("old\n", encoding="utf-8")
        self.commit(self.remote, "v1.5.2")
        self.code = self.root / "code"
        self.git(self.root, "clone", "-q", str(self.remote), str(self.code))
        (self.code / "web" / "node_modules").mkdir()  # 已构建过的管理台（不在版本库里）
        (self.code / "web" / "dist").mkdir()
        (self.code / "web" / "dist" / "index.html").write_text("built", encoding="utf-8")

    def git(self, cwd, *args):
        return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args], cwd=cwd,
                              check=True, capture_output=True, text=True).stdout.strip()

    def commit(self, repo, tag=None):
        self.git(repo, "add", "-A")
        self.git(repo, "commit", "-q", "-m", tag or "change")
        if tag:
            self.git(repo, "tag", tag)

    def release(self, files: dict[str, str]):
        for name, text in files.items():
            path = self.remote / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        self.commit(self.remote, "v99.0.0")
        return self.git(self.remote, "rev-parse", "HEAD")

    def upgrade(self) -> dict:
        upgrade.start(self.store, "99.0.0", data_dir=self.root, root=self.code)
        for thread in threading.enumerate():
            if thread.name == "reelay-upgrade":
                thread.join(30)
        return self.store.state_get(updates.NAME, upgrade.STATE)

    def head(self):
        return self.git(self.code, "rev-parse", "HEAD")

    def test_fast_forwards_to_the_release_tag_and_restarts(self):
        target = self.release({"reelay.txt": "99.0.0\n"})
        before = time.time() - 1
        record = self.upgrade()
        self.assertEqual((record["status"], record["step"], record["error"]), ("restarting", "restart", None))
        self.assertEqual(self.head(), target)
        self.assertEqual(self.git(self.code, "branch", "--show-current"), "main")  # 仍在原来的分支上
        self.assertEqual(self.restarts, [1])
        self.assertTrue(upgrade.restart_requested(self.store, before))  # 主进程也随之重启
        self.assertIn("git fetch --tags origin", (self.root / upgrade.LOG_NAME).read_text(encoding="utf-8"))

    def test_failed_dependency_update_restores_the_code_and_does_not_restart(self):
        self.release({"pyproject.toml": "[project]\n"})
        before = self.head()
        with patch.object(upgrade.sys, "executable", shutil.which("false")):
            record = self.upgrade()
        self.assertEqual((record["status"], record["step"]), ("failed", "pip"))
        self.assertIn(f"代码已退回 v{__version__}", record["error"])
        self.assertEqual((self.head(), self.restarts), (before, []))
        self.assertIn("升级失败", upgrade.status(self.store, self.root, self.code)["state"]["log"])

    def test_web_changes_need_npm(self):
        self.release({"web/app.ts": "new\n"})
        before = self.head()
        which = shutil.which
        with patch.object(upgrade.shutil, "which", side_effect=lambda name: None if name == "npm" else which(name)):
            record = self.upgrade()
        self.assertEqual(record["status"], "failed")
        self.assertIn("没有找到 npm", record["error"])
        self.assertEqual(self.head(), before)

    def test_local_commits_are_never_merged(self):
        self.release({"reelay.txt": "99.0.0\n"})
        (self.code / "mine.txt").write_text("local", encoding="utf-8")
        self.commit(self.code)
        before = self.head()
        record = self.upgrade()
        self.assertIn("当前代码不在 v99.0.0 之前", record["error"])
        self.assertEqual((self.head(), self.restarts), (before, []))

    def test_preconditions(self):
        self.assertIsNone(upgrade.blocker(self.code))
        (self.code / "reelay.txt").write_text("edited", encoding="utf-8")
        self.assertIn("未提交的改动", upgrade.blocker(self.code))
        with self.assertRaises(upgrade.UpgradeError):
            upgrade.start(self.store, "99.0.0", data_dir=self.root, root=self.code)
        self.assertIn("不是 git 克隆", upgrade.blocker(self.remote / "web"))
        with patch.object(upgrade, "restart_hook", None):
            self.assertIn("python -m reelay.web", upgrade.blocker(self.code))
        with patch.object(updates, "in_docker", return_value=True):
            self.assertIn("拉取新镜像", upgrade.blocker(self.code))

    def test_restart_settles_the_result(self):
        record = {"status": "restarting", "target": __version__, "from": "1.0.0"}
        self.store.state_set(updates.NAME, upgrade.STATE, record)
        upgrade.settle(self.store)
        self.assertEqual(self.store.state_get(updates.NAME, upgrade.STATE)["status"], "done")
        self.store.state_set(updates.NAME, upgrade.STATE, {**record, "target": "99.0.0"})
        upgrade.settle(self.store)
        self.assertIn(f"重启后仍是 v{__version__}", self.store.state_get(updates.NAME, upgrade.STATE)["error"])
        self.store.state_set(updates.NAME, upgrade.STATE, {**record, "status": "running"})
        upgrade.settle(self.store)
        self.assertIn("管理服务退出", self.store.state_get(updates.NAME, upgrade.STATE)["error"])


class TestBuildHelpers(unittest.TestCase):
    def test_rebuild_keeps_the_sub_path_and_replaces_dist(self):
        with tempfile.TemporaryDirectory() as temp:
            web = Path(temp)
            index = web / "dist" / "index.html"
            index.parent.mkdir()
            index.write_text('<script type="module" src="/reelay/assets/index-1.js"></script>', encoding="utf-8")
            self.assertEqual(upgrade.built_base(index), "/reelay/")
            index.write_text('<script src="/assets/index-1.js"></script>', encoding="utf-8")
            self.assertIsNone(upgrade.built_base(index))
            (web / "dist.next").mkdir()
            (web / "dist.next" / "index.html").write_text("new", encoding="utf-8")
            upgrade.swap_dist(web)
            self.assertEqual(index.read_text(encoding="utf-8"), "new")
            self.assertEqual(sorted(p.name for p in web.iterdir()), ["dist"])


class TestWorkerRestart(PipelineCase):
    async def test_worker_restarts_after_an_upgrade_request(self):
        from reelay import main
        since = time.time()
        with patch.object(main, "RESTART_POLL_SEC", 0.01), patch.object(upgrade, "restart_hook", None):
            watch = asyncio.create_task(main._restart_watch(self.store, since))
            await asyncio.sleep(0.05)
            self.assertFalse(watch.done())
            upgrade.request_restart(self.store)
            await asyncio.wait_for(watch, 1)


class TestUpgradeApi(WebCase):
    def test_docker_deployments_only_check(self):
        with patch.object(updates, "fetch_latest", return_value=LATEST), \
                patch.object(updates, "in_docker", return_value=True), \
                patch.object(upgrade, "restart_hook", lambda: None):
            data = self.client.get("/api/update").json()
            self.assertEqual((data["upgrade"]["supported"], data["upgrade"]["reason"]),
                             (False, "Docker 部署请拉取新镜像升级"))
            response = self.client.post("/api/update/apply")
        self.assertEqual((response.status_code, response.json()["detail"]), (409, "Docker 部署请拉取新镜像升级"))
