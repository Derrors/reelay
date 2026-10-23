"""FastAPI 管理接口的最小集成测试。"""

from __future__ import annotations

import json
import os
import tempfile
import time
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
            }, ensure_ascii=False),
            encoding="utf-8",
        )
        self.app = create_app(
            data_dir=root,
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

    def test_logs_can_be_cleared(self):
        root = Path(self.tempdir.name)
        (root / "reelay.log").write_text("2026-10-23 10:00:00 INFO reelay | 一行\n", encoding="utf-8")
        (root / "reelay.log.1").write_text("旧日志\n", encoding="utf-8")
        (root / "reelay.log.2").write_text("更旧\n", encoding="utf-8")
        (root / "reelay.log.bak").write_text("不是轮转文件\n", encoding="utf-8")
        self.assertEqual(len(self.client.get("/api/logs").json()["lines"]), 1)
        response = self.client.post("/api/logs/clear")
        self.assertEqual(response.status_code, 200)
        self.assertGreater(response.json()["freed"], 0)
        self.assertEqual(self.client.get("/api/logs").json(), {"lines": [], "path": str(root / "reelay.log"), "exists": True})
        self.assertFalse((root / "reelay.log.1").exists() or (root / "reelay.log.2").exists())
        self.assertTrue((root / "reelay.log.bak").exists())

    def test_organize_preview(self):
        response = self.client.post("/api/extensions/organizer/actions/preview_plan", json={
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
        self.assertEqual(response.json()["items"][0]["target_name"], "测试剧 - S01E01 - 第 1 集.mkv")

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
        self.assertEqual(self.client.post(f'/api/tasks/{ctx.task_id}/reorganize', json={'conflict': 'keep'}).status_code, 422)


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
        from reelay.core import auth115, secret_store
        secret_store.current().update({"telegram_api_id": "1", "telegram_api_hash": "dummy"})
        auth115.get_store().set_cookies("UID=1;CID=2;SEID=3")
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

    def test_dashboard_reports_115_cookie_probe_within_its_check_interval(self):
        store = self.app.state.store

        def p115_status():
            return self.client.get("/api/dashboard").json()["connections"]["p115"]["status"]

        store.heartbeat("p115_cookie", status="unknown", error="未配置 cookie", check_interval=300)
        self.assertIn(p115_status(), {"unverified", "not_configured"})
        store.heartbeat("p115_cookie", status="active", error="", check_interval=300)
        with patch("time.time", return_value=time.time() + 120):  # 普通心跳 30 秒即过期，探活按自身间隔
            self.assertEqual(p115_status(), "active")
        with patch("time.time", return_value=time.time() + 601):
            self.assertEqual(p115_status(), "stale")
        store.heartbeat("p115_cookie", status="expired", error="登录失效", check_interval=30)
        self.assertEqual(p115_status(), "expired")

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


@unittest.skipIf(TestClient is None, "未安装 web 可选依赖")
class TestAuth115Api(unittest.TestCase):
    """115 混合鉴权 Web API：状态、cookie 录入校验、开放平台扫码流程。"""

    def setUp(self):
        from reelay.core import auth115
        from reelay.web import create_app

        self.auth115 = auth115
        self.env = patch.dict(os.environ, {}, clear=True)
        self.env.start()
        self.env_loader = patch('reelay.web.load_env')
        self.env_loader.start()

        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        (root / "frontend").mkdir()
        (root / "frontend" / "index.html").write_text("<html>x</html>", encoding="utf-8")
        self.cred_path = root / "creds.json"
        self.cred_store = auth115.CredentialStore(self.cred_path)
        self.singleton_patch = patch.object(auth115, "get_store",
                                            lambda: self.cred_store)
        self.singleton_patch.start()

        (root / "config.yaml").write_text(json.dumps({
            "save_dirs": {"默认": "0"},
            "defaults": {"approval_mode": "manual", "parser": "regex", "save_to": "默认"},
            "sources": [],
        }), encoding="utf-8")
        self.app = create_app(
            data_dir=root,
            start_worker=False,
            frontend_dir=root / "frontend",
        )
        self.client = TestClient(self.app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.singleton_patch.stop()
        self.tempdir.cleanup()
        self.env_loader.stop()
        self.env.stop()

    def test_status_empty(self):
        data = self.client.get("/api/auth115/status").json()
        self.assertFalse(data["cookie"]["configured"])
        self.assertFalse(data["oauth"]["configured"])
        self.assertEqual(data["writes_via"], "none")

    def test_cookie_rejects_garbage(self):
        resp = self.client.put("/api/auth115/cookie", json={"cookies": "not-a-cookie"})
        self.assertEqual(resp.status_code, 422)

    def test_cookie_qr_start_status_and_image(self):
        png_bytes = b"\x89PNG\r\n\x1a\nfake-cookie"

        class FakeP115Cookie:
            @staticmethod
            def login_qrcode_token():
                return {"state": 1, "data": {"uid": "cu9", "time": 1, "sign": "s",
                                             "qrcode": "https://115.com/scan/dg-cu9"}}

            @staticmethod
            def login_qrcode_scan_status(payload):
                return {"state": 1, "data": {"status": 1}}

            @staticmethod
            def login_qrcode_scan_result(uid, app):
                return {"state": 1, "data": {"cookie": {
                    "UID": "1_A1_2", "CID": "c", "SEID": "s", "KID": "k",
                }}}

            @staticmethod
            def login_qrcode(payload, app="web"):
                return png_bytes

        fake = type("M", (), {"P115Client": FakeP115Cookie})()
        with patch.dict("sys.modules", {"p115client": fake}):
            start = self.client.post("/api/auth115/cookie/qr")
            self.assertEqual(start.status_code, 200, start.text)
            uid = start.json()["uid"]
            image = self.client.get(f"/api/auth115/cookie/qr/image?uid={uid}")
            self.assertEqual(image.status_code, 200)
            self.assertEqual(image.headers["content-type"], "image/svg+xml")
            self.assertIn(b"<svg", image.content)
            waiting = self.client.get(f"/api/auth115/cookie/qr?uid={uid}")
            self.assertEqual(waiting.json()["status"], 1)
            FakeP115Cookie.login_qrcode_scan_status = staticmethod(
                lambda payload: {"state": 1, "data": {"status": 2}})
            with patch.object(self.cred_store, "ping_cookie", return_value="active"):
                done = self.client.get(f"/api/auth115/cookie/qr?uid={uid}")
            self.assertTrue(done.json()["authenticated"])
            self.assertTrue(self.cred_store.has_cookie)

    def test_qr_start_then_scan_confirmed_saves_tokens(self):
        store = self.cred_store

        class FakeP115Open:
            @staticmethod
            def login_qrcode_token_open(app_id):
                return {"code": 0, "data": {"uid": "u9", "time": 1, "sign": "s",
                                            "qrcode": "https://115.com/scan/dg-u9"}}

            @staticmethod
            def login_qrcode_scan_status(payload):
                return {"data": {"status": 2}}

            @staticmethod
            def login_qrcode_access_token_open(uid):
                return {"data": {"access_token": "wa", "refresh_token": "wr"}}

        fake = type("M", (), {"P115OpenClient": FakeP115Open})()
        with patch.dict("sys.modules", {"p115client": fake}):
            r1 = self.client.post("/api/auth115/oauth/qr")
            self.assertEqual(r1.status_code, 200, r1.text)
            uid = r1.json()["uid"]
            r2 = self.client.get(f"/api/auth115/oauth/qr?uid={uid}")
            self.assertEqual(r2.status_code, 200)
            self.assertTrue(r2.json()["authenticated"])
        self.assertTrue(store.has_oauth)
        self.assertTrue(self.client.get("/api/auth115/status").json()
                        ["oauth"]["configured"])
        self.assertEqual(self.client.delete("/api/auth115/oauth").status_code, 200)
        self.assertFalse(store.has_oauth)

    def test_qr_image_encodes_the_open_platform_link(self):
        # 授权二维码必须是开放平台返回的 qrcode 链接；网页登录的二维码图片 App 扫了提示参数错误
        import io
        import qrcode
        from qrcode.image.svg import SvgPathImage

        class FakeP115Open:
            @staticmethod
            def login_qrcode_token_open(app_id):
                return {"code": 0, "data": {"uid": "u8", "time": 1, "sign": "s",
                                            "qrcode": "https://115.com/scan/dg-u8"}}

            @staticmethod
            def login_qrcode(payload, app="web"):
                raise AssertionError("不能用网页登录的二维码图片")

            @staticmethod
            def login_qrcode_scan_status(payload):
                return {"state": 1, "code": 0, "data": {}}  # 还没扫码时 115 返回空的 data

        expected = io.BytesIO()
        qrcode.make("https://115.com/scan/dg-u8", image_factory=SvgPathImage).save(expected)
        fake = type("M", (), {"P115OpenClient": FakeP115Open})()
        with patch.dict("sys.modules", {"p115client": fake}):
            uid = self.client.post("/api/auth115/oauth/qr").json()["uid"]
            r2 = self.client.get(f"/api/auth115/oauth/qr/image?uid={uid}")
            self.assertEqual(r2.status_code, 200, r2.text)
            self.assertEqual(r2.headers["content-type"], "image/svg+xml")
            self.assertEqual(r2.content, expected.getvalue())
            status = self.client.get(f"/api/auth115/oauth/qr?uid={uid}").json()
            self.assertEqual((status["status"], status["message"]), (0, "等待扫码"))
        # 未知 uid 返回 404
        r3 = self.client.get("/api/auth115/oauth/qr/image?uid=nope")
        self.assertEqual(r3.status_code, 404)


class TestRealtimeShutdown(unittest.IsolatedAsyncioTestCase):
    async def test_close_all_streams_ends_open_sse_even_when_queue_is_full(self):
        # 停止时不结束推送流，uvicorn 会一直等这条长连接，直到 systemd / Docker 超时强杀
        import asyncio
        from reelay.realtime import EventBus, close_all_streams, event_stream
        bus = EventBus(store=None)
        stream = event_stream(bus)
        self.assertEqual(await anext(stream), b": connected\n\n")
        await anext(stream)  # hello
        for _ in range(64):  # 浏览器卡住，队列积满
            bus._broadcast({"type": "ping"})
        close_all_streams()

        async def drain():
            return [frame async for frame in stream]
        frames = await asyncio.wait_for(drain(), timeout=2)
        self.assertEqual(len(frames), 63)  # 丢掉最旧的一帧，放下结束标记
        self.assertEqual(bus._subscribers, set())


if __name__ == "__main__":
    unittest.main()
