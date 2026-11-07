"""入口程序：主进程、管理服务与命令行。"""
from __future__ import annotations

import asyncio
import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class TestEntrypoints(unittest.TestCase):
    """启动顺序：先写出默认配置（首次启动），再按数据目录加载密钥与凭证。"""

    def test_open_service_writes_default_config_before_using_data_dir(self):
        from reelay.core import bootstrap, paths
        calls = []
        with tempfile.TemporaryDirectory() as root:
            data = Path(root)
            with patch.object(bootstrap, "ensure_config", lambda path: calls.append(("ensure_config", path))), \
                    patch.object(bootstrap.paths, "use_data_dir", lambda d: calls.append(("use", d))), \
                    patch.object(bootstrap, "load_config", side_effect=StopIteration), self.assertRaises(StopIteration):
                bootstrap.open_service(data)
        self.assertEqual(calls, [("ensure_config", data / paths.CONFIG_NAME), ("use", data)])

    def test_web_main_reads_host_and_port_from_env_file(self):
        try:
            import uvicorn  # noqa: F401
        except ImportError:  # pragma: no cover
            self.skipTest("未安装 web 可选依赖")
        from reelay import web
        from reelay.core import paths
        configs = []

        def fake_run(server):
            configs.append(server.config)
            server.started = True

        with tempfile.TemporaryDirectory() as root:
            Path(root, ".env").write_text("REELAY_WEB_HOST=0.0.0.0\nREELAY_WEB_PORT=28181\n"
                                          "REELAY_FORWARDED_ALLOW_IPS=172.18.0.1\n", encoding="utf-8")
            with patch.dict(os.environ, {}, clear=True), patch.object(paths, "ENV_FILE", Path(root) / ".env"), \
                    patch("uvicorn.Server.run", fake_run):
                web.main()
        self.assertEqual((configs[0].host, configs[0].port), ("0.0.0.0", 28181))
        self.assertEqual(configs[0].forwarded_allow_ips, "172.18.0.1")  # 反向代理转发的客户端地址可信


class TestMainProcess(unittest.IsolatedAsyncioTestCase):
    """主进程的常驻循环：升级后原地重启，任何常驻服务意外退出都不能留下空转进程。"""

    def setUp(self):
        env = patch.dict(os.environ, {}, clear=True)
        env.start()
        self.addCleanup(env.stop)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        from reelay.core.bootstrap import open_service
        self.service = open_service(temp.name)

    async def run_live(self, *, source=None, requested=False):
        from reelay import main as entry

        class IdleWorker:
            def __init__(self, *args, **kwargs):
                pass

            async def run(self):
                await asyncio.Event().wait()

        async def idle_source(*args, **kwargs):
            await asyncio.Event().wait()

        with patch.object(entry, "open_service", return_value=self.service), \
                patch.object(entry, "Worker", IdleWorker), \
                patch.object(entry, "_source_loop", source or idle_source), \
                patch.object(entry.upgrade, "restart_requested", return_value=requested), \
                patch.object(self.service.store, "close") as close:
            try:
                return await asyncio.wait_for(entry._run_live(), 5)
            finally:
                close.assert_called_once()  # 无论怎么退出都关闭数据库
                self.addCleanup(self.service.store.close)

    async def test_restart_request_returns_the_restart_code(self):
        from reelay import main as entry
        self.assertEqual(await self.run_live(requested=True), entry.RESTART)

    async def test_a_service_stopping_unexpectedly_ends_the_process(self):
        async def stops_at_once(*args, **kwargs):
            return None

        with self.assertRaisesRegex(RuntimeError, "常驻服务意外停止"):
            await self.run_live(source=stops_at_once)

    async def test_a_crashing_service_propagates_its_error(self):
        async def crashes(*args, **kwargs):
            raise OSError("boom")

        with self.assertRaisesRegex(OSError, "boom"):
            await self.run_live(source=crashes)


class TestDryRun(unittest.IsolatedAsyncioTestCase):
    async def test_dry_run_passes_its_smoke_check(self):
        from reelay import main as entry
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(await entry._run_dry(), 0)
        self.assertIn("PASS", output.getvalue())


if __name__ == "__main__":
    unittest.main()
