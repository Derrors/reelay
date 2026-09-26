"""FastAPI 管理接口的最小集成测试。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover
    TestClient = None


@unittest.skipIf(TestClient is None, "未安装 web 可选依赖")
class TestManagementApi(unittest.TestCase):
    def setUp(self):
        from reelay.web import create_app

        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        self.config_path = root / "config.yaml"
        self.config_path.write_text(
            json.dumps({
                "save_dirs": {"默认": "0"},
                "defaults": {"approval_mode": "manual", "parser": "regex", "save_to": "默认"},
                "sources": [],
                "routing": [],
            }, ensure_ascii=False),
            encoding="utf-8",
        )
        self.app = create_app(
            db_path=str(root / "reelay.db"),
            config_path=str(self.config_path),
            start_worker=False,
        )
        self.client = TestClient(self.app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.tempdir.cleanup()

    def test_health_dashboard_and_config(self):
        self.assertEqual(self.client.get("/api/health").json()["ok"], True)
        self.assertEqual(self.client.get("/api/dashboard").status_code, 200)
        response = self.client.get("/api/config")
        self.assertEqual(response.status_code, 200)
        self.assertIn("save_dirs", response.json()["config"])
        self.assertNotIn("REELAY_115_COOKIES", response.text)

    def test_organize_preview(self):
        response = self.client.post("/api/organize/preview", json={
            "parsed": {
                "is_media": True,
                "media_type": "tv",
                "title": "测试剧",
                "year": 2026,
                "season": 1,
                "episodes": [1],
            },
            "landed": [{"id": "one", "name": "S01E01.mkv"}],
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["items"][0]["target_name"], "测试剧 - S01E01.mkv")


if __name__ == "__main__":
    unittest.main()
