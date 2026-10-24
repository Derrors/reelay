"""管理接口：总览、任务、日志、前端托管、115 登录、AI 接口、实时推送。"""
from __future__ import annotations

import asyncio
import time
import unittest
from unittest.mock import patch

from reelay.core import secret_store
from reelay.core.contracts import ParsedResource, PipelineContext, RawMessage, TaskState

from tests.fakes import MANUAL_REGEX, SHARE, TestClient, WebCase, message


class TestManagementApi(WebCase):
    CONFIG = MANUAL_REGEX

    def prepare(self, root):
        self.frontend = root / "frontend"
        self.frontend.mkdir()
        (self.frontend / "index.html").write_text("<html>Reelay app</html>", encoding="utf-8")
        (self.frontend / "app.js").write_text("safe asset", encoding="utf-8")
        self.secret = root / "private.txt"
        self.secret.write_text("MUST_NOT_BE_EXPOSED", encoding="utf-8")
        (self.frontend / "linked-secret.txt").symlink_to(self.secret)

    def test_health_dashboard_and_config(self):
        self.assertEqual(self.client.get("/api/health").json()["ok"], True)
        self.assertEqual(self.client.get("/api/dashboard").status_code, 200)
        response = self.client.get("/api/config")
        self.assertEqual(response.status_code, 200)
        self.assertIn("save_dirs", response.json()["config"])
        self.assertNotIn("REELAY_115_COOKIES", response.text)

    def test_logs_can_be_cleared(self):
        root = self.root
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
        from reelay.core.contracts import ParsedResource, TaskState
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
                self.assertEqual(self.client.post("/api/config/validate", json={"config": config}).status_code, 422)
                revision = self.client.get("/api/config").json()["revision"]
                response = self.client.patch("/api/config/sections", json={"revision": revision, "sections": config})
                self.assertEqual(response.status_code, 422)
                self.assertEqual(self.config_path.read_text(), before)
        response = self.client.patch("/api/config/sections", content=b"[invalid", headers={"Content-Type": "application/json"})
        self.assertEqual(response.status_code, 422)  # 不是合法 JSON：422 而不是 500
        self.assertEqual(self.config_path.read_text(), before)

    def test_health_reports_runtime_and_does_not_treat_credentials_as_connected(self):
        from reelay.core import auth115
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
        from reelay.core.contracts import TaskState
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
        self.assertIn("save_dirs", response.json()["config"])  # 仍是上一有效版本
        self.assertIn(str(self.config_path), response.json()["config_error"])  # 说清是哪个文件，好去修
        self.assertEqual(self.app.state.pipeline.revision, before)
        response = self.client.patch("/api/config/sections", json={"revision": response.json()["revision"],
                                                                    "sections": {"defaults": {"save_to": "默认"}}})
        self.assertEqual(response.status_code, 409)  # 不覆盖损坏的文件
        self.assertEqual(self.config_path.read_text(), "[invalid")


@unittest.skipIf(TestClient is None, "未安装 web 可选依赖")
class TestConsoleApi(WebCase):
    """新管理台用到的聚合接口：收件箱、带媒体字段的任务列表、扩展目录去重。"""
    CONFIG = {"save_dirs": {"默认": "1", "剧集": "2"}, "defaults": {"save_to": "默认"}}

    def setUp(self):
        super().setUp()
        store = self.app.state.store
        for index, state in enumerate((TaskState.PENDING_APPROVAL, TaskState.PENDING_APPROVAL,
                                       TaskState.NEED_MANUAL, TaskState.QUEUED, TaskState.DONE)):
            ctx = message(msg_id=str(index), text=f"剧 第{index}集\n{SHARE}")
            ctx.parsed = ParsedResource(is_media=True, title=f"剧{index}", media_type="tv", season=1,
                                        episodes=[index], resolution="4K", size="1 GB", share_code="abc",
                                        raw_fields={"poster_path": f"/p{index}.jpg"})
            ctx.state = state
            ctx.ns("confidence").update(score=0.4, caps=["tmdb_unmatched"])
            store.save_task(ctx, error="同名冲突" if state == TaskState.NEED_MANUAL else None)

    def test_inbox_returns_card_fields_without_links(self):
        body = self.client.get("/api/inbox").json()
        self.assertEqual(body["save_dirs"], ["默认", "剧集"])
        self.assertEqual(len(body["items"]), 2)
        item = body["items"][0]
        self.assertEqual((item["media_type"], item["resolution"], item["confidence_caps"]), ("tv", "4K", ["tmdb_unmatched"]))
        self.assertTrue(item["poster_path"].startswith("/p"))
        self.assertEqual((item["save_dir_alias"], item["auto_dir"]), (None, "剧集/其他剧集"))  # 没有地区的剧集归入兜底分类
        self.assertNotIn("http", item["preview"])
        manual = self.client.get("/api/inbox?state=need_manual").json()["items"]
        self.assertEqual([m["error"] for m in manual], ["同名冲突"])
        self.assertEqual(self.client.get("/api/inbox?state=done").status_code, 422)

    def test_task_list_supports_multiple_states_paging_and_media_fields(self):
        items = self.client.get("/api/tasks?state=pending_approval,queued&limit=2").json()["items"]
        self.assertEqual(len(items), 2)
        self.assertTrue({i["state"] for i in items} <= {"pending_approval", "queued"})
        self.assertEqual(items[0]["episodes"], [int(items[0]["title"][-1])])
        rest = self.client.get("/api/tasks?state=pending_approval,queued&limit=2&offset=2").json()["items"]
        self.assertEqual(len(rest), 1)

    def test_batch_approve_can_redirect_target_directory(self):
        ids = [i["task_id"] for i in self.client.get("/api/inbox").json()["items"]]
        result = self.client.post("/api/tasks/batch", json={"task_ids": ids, "action": "approve",
                                                             "save_dir_alias": "剧集"}).json()
        self.assertEqual(result["done"], 2)
        store = self.app.state.store
        self.assertEqual({store.get_task(i).save_dir_alias for i in ids}, {"剧集"})

    def test_library_counts_multi_season_pack_by_season_and_episode(self):
        store = self.app.state.store
        for msg_id, pairs in (("m1", [[1, 1], [1, 2], [2, 1], [2, 2]]), ("m2", [[3, 1]])):
            ctx = message(msg_id=msg_id)
            ctx.parsed = ParsedResource(is_media=True, title="多季剧", media_type="tv", share_code="abc",
                                        season=None if len(pairs) > 1 else 3, episodes=sorted({e for _, e in pairs}))
            ctx.state = TaskState.DONE
            ctx.ns("storage")["manifest"] = {"pairs": pairs}
            store.save_task(ctx)
        group = next(g for g in self.client.get("/api/library").json()["groups"] if g["title"] == "多季剧")
        self.assertEqual(group["episode_pairs"], [[1, 1], [1, 2], [2, 1], [2, 2], [3, 1]])
        single = next(g for g in self.client.get("/api/library").json()["groups"] if g["title"] == "剧4")
        self.assertIsNone(single["episode_pairs"])

    def test_library_skips_files_replaced_by_overwrite_or_upgrade(self):
        store = self.app.state.store

        def done(msg_id, title, landed, media_type="movie", size=None, episodes=(), conflicts=()):
            ctx = message(msg_id=msg_id)
            ctx.parsed = ParsedResource(is_media=True, title=title, media_type=media_type, share_code="abc",
                                        season=1 if episodes else None, episodes=list(episodes), size=size)
            ctx.state = TaskState.DONE
            ctx.ns("storage")["landed"] = [{"id": fid, "name": f"{fid}.mkv", "size": gb * 1024 ** 3} for fid, gb in landed]
            ctx.ns("organize").update(conflicts=list(conflicts), plan=[
                {"src_id": fid, "target_dir": title, "target_name": f"{title} {fid}.mkv"} for fid, _ in landed])
            store.save_task(ctx)

        done("r1", "空枪", [("10", 33)], size="33 GB")
        done("r2", "空枪", [("11", 32)], size="32 GB",
             conflicts=[{"target": "空枪/空枪.mkv", "action": "replace", "existing_id": "10"}])
        done("r3", "合集剧", [("20", 1), ("21", 2)], media_type="tv", size="3 GB", episodes=(1, 2))
        done("r4", "合集剧", [("22", 4)], media_type="tv", size="4 GB", episodes=(2,),
             conflicts=[{"target": "合集剧/E02.mkv", "action": "upgrade", "existing_id": "21"},
                        {"target": "x", "action": "skip", "existing_id": "20"}])  # 保留旧文件的不算替换
        self.assertEqual(store.replaced_file_ids(), {"10", "21"})
        groups = {g["title"]: g for g in self.client.get("/api/library").json()["groups"]}
        self.assertEqual((groups["空枪"]["tasks"], groups["空枪"]["files"], round(groups["空枪"]["bytes"] / 1024 ** 3)),
                         (1, ["空枪/空枪 11.mkv"], 32))
        self.assertEqual((groups["合集剧"]["tasks"], sorted(groups["合集剧"]["files"]), round(groups["合集剧"]["bytes"] / 1024 ** 3)),
                         (2, ["合集剧/合集剧 20.mkv", "合集剧/合集剧 22.mkv"], 5))
        self.assertEqual(groups["合集剧"]["episodes"], [1, 2])
        items = {g["title"]: g for g in self.app.state.service.library_query()["items"]}
        self.assertEqual((items["空枪"]["file_count"], items["合集剧"]["file_count"]), (1, 2))

    def test_dashboard_has_recent_counts_and_catalog_merges_multi_event_extensions(self):
        self.assertEqual(self.client.get("/api/dashboard").json()["recent"]["received_24h"], 5)
        events = self.client.get("/api/config").json()["extensions"]["events"]
        tracker = [e for e in events if e["name"] == "episode_tracker"]
        self.assertEqual(len(tracker), 1)
        self.assertEqual(set(tracker[0]["slot"].split(",")), {"on_saved", "on_daily"})


class TestAuth115Api(WebCase):
    """115 混合鉴权 Web API：状态、cookie 录入校验、开放平台扫码流程。"""

    def prepare(self, root):
        from reelay.core import auth115
        self.auth115 = auth115
        self.cred_store = auth115.CredentialStore(root / "creds.json")
        singleton = patch.object(auth115, "get_store", lambda: self.cred_store)
        singleton.start()
        self.addCleanup(singleton.stop)

    def test_status_empty(self):
        data = self.client.get("/api/auth115/status").json()
        self.assertFalse(data["cookie"]["configured"])
        self.assertFalse(data["oauth"]["configured"])
        self.assertEqual(data["writes_via"], "none")

    def test_cookie_rejects_garbage(self):
        resp = self.client.put("/api/auth115/cookie", json={"cookies": "not-a-cookie"})
        self.assertEqual(resp.status_code, 422)

    def test_cookie_is_probed_before_it_replaces_the_saved_one(self):
        from reelay.core.auth115 import AuthState
        cookies = "UID=1_A1_2;CID=c;SEID=s"
        for state, error, status in ((AuthState.EXPIRED, "", 409), (AuthState.ERROR, "timeout", 409)):
            with self.subTest(state=state), patch.object(self.cred_store, "probe_cookies", return_value=(state, error)):
                self.assertEqual(self.client.put("/api/auth115/cookie", json={"cookies": cookies}).status_code, status)
            self.assertFalse(self.cred_store.has_cookie)  # 失效或验证不了的 cookie 不保存
        with patch.object(self.cred_store, "probe_cookies", return_value=(AuthState.ACTIVE, "")), \
                patch.object(self.cred_store, "ping_cookie", return_value=AuthState.ACTIVE):
            response = self.client.put("/api/auth115/cookie", json={"cookies": cookies})
        self.assertEqual(response.json(), {"ok": True, "state": AuthState.ACTIVE})
        self.assertTrue(self.cred_store.has_cookie)

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


class TestAiRoutes(WebCase):
    """HTTP 层：新接口已注册、参数与错误码正确（不访问网络，未配置 AI）。"""

    def test_routes_answer_without_ai(self):
        for path in ("/api/insights", "/api/ai/usage", "/api/subscriptions", "/api/sources/quality",
                     "/api/library?media_type=anime", "/api/stats"):
            self.assertEqual(self.client.get(path).status_code, 200, path)
        self.assertEqual(self.client.post("/api/ai/compile-rules", json={"text": "国产剧"}).status_code, 409)
        self.assertEqual(self.client.post("/api/sources/draft", json={"bot": "@x", "samples": ["a"]}).status_code, 409)
        self.assertEqual(self.client.post("/api/inbox/advice", json={"task_ids": ["x"]}).json(), {"items": {}})
        added = self.client.post("/api/subscriptions", json={"title": "剧", "season": 2})
        self.assertEqual({k: added.json()["subscription"][k] for k in ("title", "season")}, {"title": "剧", "season": 2})
        self.assertEqual(self.client.post("/api/subscriptions", json={"title": "剧", "season": 2}).status_code, 409)
        self.assertEqual(self.client.post("/api/subscriptions/remove", json={"title": "剧", "season": 2}).status_code, 200)
        self.assertEqual(self.client.post("/api/subscriptions/from-task/missing").status_code, 404)
        self.assertEqual(self.client.post("/api/insights/auto-approve", json={"source": "@new"}).json()["config"]["sources"],
                         [{"bot": "@new", "approval_mode": "auto"}])


class TestRealtimeShutdown(unittest.IsolatedAsyncioTestCase):
    async def test_close_all_streams_ends_open_sse_even_when_queue_is_full(self):
        # 停止时不结束推送流，uvicorn 会一直等这条长连接，直到 systemd / Docker 超时强杀
        from reelay.web.realtime import EventBus, close_all_streams, event_stream
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
