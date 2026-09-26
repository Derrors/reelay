"""FastAPI 管理接口的最小集成测试。"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover
    TestClient = None


@unittest.skipIf(TestClient is None, "未安装 web 可选依赖")
class TestManagementApi(unittest.TestCase):
    def setUp(self):
        from reelay.web import create_app

        self.env = patch.dict(os.environ, {}, clear=True)
        self.env.start()
        self.env_loader = patch('reelay.web.load_env')
        self.env_loader.start()

        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        self.frontend = root / "frontend"
        self.frontend.mkdir()
        (self.frontend / "index.html").write_text("<html>Reelay app</html>", encoding="utf-8")
        (self.frontend / "app.js").write_text("safe asset", encoding="utf-8")
        self.secret = root / "private.txt"
        self.secret.write_text("MUST_NOT_BE_EXPOSED", encoding="utf-8")
        (self.frontend / "linked-secret.txt").symlink_to(self.secret)
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
            frontend_dir=self.frontend,
        )
        self.client = TestClient(self.app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.tempdir.cleanup()
        self.env_loader.stop()
        self.env.stop()

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

    def test_spa_routes_and_assets_still_work(self):
        self.assertEqual(self.client.get('/app.js').text, 'safe asset')
        self.assertIn('Reelay app', self.client.get('/tasks/123').text)

    def test_traversal_and_symlinks_cannot_read_files_outside_frontend(self):
        for path in (
            '/%2e%2e/private.txt', '/%2e%2e%2fprivate.txt',
            '/nested/%2e%2e/%2e%2e/private.txt', '/linked-secret.txt',
            '/%2F' + str(self.secret).lstrip('/'),
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 404)
                self.assertNotIn('MUST_NOT_BE_EXPOSED', response.text)

    def test_spa_index_symlink_cannot_bypass_boundary_check(self):
        (self.frontend / 'index.html').unlink()
        (self.frontend / 'index.html').symlink_to(self.secret)
        response = self.client.get('/tasks')
        self.assertEqual(response.status_code, 404)
        self.assertNotIn('MUST_NOT_BE_EXPOSED', response.text)

    def test_reconcile_preserves_manifest_and_only_queues_readback(self):
        from reelay.core.contracts import ParsedResource, PipelineContext, RawMessage, TaskState
        ctx = PipelineContext(RawMessage('@test', '1', 'test'))
        ctx.parsed = ParsedResource(is_media=True, share_code='share')
        ctx.state = TaskState.NEED_MANUAL
        storage = ctx.ns('storage')
        storage.update({'phase': 'receiving', 'manifest': {'staging_cid': 123, 'files': []}})
        self.app.state.store.save_task(ctx)
        response = self.client.post(f'/api/tasks/{ctx.task_id}/reconcile')
        self.assertEqual(response.status_code, 200)
        restored = self.app.state.store.get_task(ctx.task_id)
        self.assertEqual(restored.state, TaskState.QUEUED)
        self.assertEqual(restored.ns('storage')['phase'], 'receiving')
        self.assertEqual(restored.ns('storage')['manifest'], storage['manifest'])
        self.assertEqual(self.client.post(f'/api/tasks/{ctx.task_id}/reparse').status_code, 409)


    def test_config_validation_returns_422_without_replacing_valid_file(self):
        before = self.config_path.read_text()
        for config in ({"defaults": {"approval_mode": []}}, {"defaults": {"confidence_threshold": 3}},
                       {"defaults": {"parser": "unknown"}}, {"approval": {"on_timeout": []}}):
            with self.subTest(config=config):
                response = self.client.put("/api/config", json={"config": config})
                self.assertEqual(response.status_code, 422)
                self.assertEqual(self.config_path.read_text(), before)
        response = self.client.put("/api/config/text", json={"text": "[invalid"})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.config_path.read_text(), before)

    def test_health_reports_runtime_and_does_not_treat_credentials_as_connected(self):
        with patch.dict(os.environ, {"REELAY_TG_API_ID": "1", "REELAY_TG_API_HASH": "dummy", "REELAY_115_COOKIES": "dummy"}):
            dashboard = self.client.get("/api/dashboard").json()
            self.assertEqual(dashboard["mode"], "not_running")
            self.assertEqual(dashboard["connections"]["p115"]["status"], "unverified")
            self.app.state.store.heartbeat("worker", status="running", config_revision=self.app.state.pipeline.revision)
            self.app.state.store.heartbeat("source", status="connected")
            dashboard = self.client.get("/api/dashboard").json()
            self.assertEqual(dashboard["mode"], "live")
            self.assertEqual(dashboard["connections"]["telegram"]["status"], "connected")
            self.assertEqual(dashboard["connections"]["p115"]["status"], "unverified")
        self.assertIn("worker", self.client.get("/api/health").json()["runtime"])

    def test_failed_delivery_can_be_requeued_without_mutating_resource_task(self):
        from reelay.core.contracts import PipelineContext, RawMessage, TaskState
        store = self.app.state.store
        ctx = PipelineContext(RawMessage("@test", "outbox", "text"))
        ctx.state = TaskState.DONE
        ctx.ns("deliveries")["notification"] = {"kind": "notify", "event": "saved", "target": "console"}
        store.save_task(ctx)
        row = store.claim_deliveries()[0]
        for _ in range(8):
            store.finish_delivery(row["id"], error="TimeoutError")
        response = self.client.get("/api/deliveries?status=failed")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("snapshot", response.text)
        self.assertEqual(response.json()["items"][0]["attempts"], 8)
        self.assertEqual(self.client.post(f"/api/deliveries/{row['id']}/retry").status_code, 200)
        self.assertEqual(self.client.post(f"/api/deliveries/{row['id']}/retry").status_code, 409)
        self.assertEqual(store.get_task(ctx.task_id).state, TaskState.DONE)
        self.assertEqual(store.delivery_counts(), {"pending": 1})

    def test_invalid_runtime_config_remains_editable_and_keeps_last_valid_version(self):
        before = self.app.state.pipeline.revision
        self.config_path.write_text("[invalid")
        response = self.client.get("/api/config")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["text"], "[invalid")
        self.assertEqual(self.app.state.pipeline.revision, before)
        self.assertIsNotNone(self.app.state.pipeline.config_error)


if __name__ == "__main__":
    unittest.main()
