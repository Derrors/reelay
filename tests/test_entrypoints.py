"""入口程序：主进程、管理服务与命令行。"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class TestEntrypoints(unittest.TestCase):
    """主进程启动顺序：先写出默认配置（首次启动），再按数据目录加载密钥与凭证。"""

    def test_main_writes_default_config_before_using_data_dir(self):
        from reelay import main as entry
        calls = []

        def fake_run(coro):
            coro.close()
            return 0

        with patch("sys.argv", ["reelay"]), \
                patch.object(entry, "_setup_log", lambda **kw: calls.append(("log", kw["to_file"]))), \
                patch.object(entry, "ensure_config", lambda: calls.append(("ensure_config",))), \
                patch.object(entry.paths, "use_data_dir", lambda d: calls.append(("use", d))), \
                patch.object(entry.asyncio, "run", fake_run), self.assertRaises(SystemExit):
            entry.main()
        from reelay.core import paths
        self.assertEqual(calls, [("log", True), ("ensure_config",), ("use", paths.DATA)])

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


if __name__ == "__main__":
    unittest.main()
