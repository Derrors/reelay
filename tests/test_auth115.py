"""tests for core.auth115 credential store hybrid auth (no network)."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from reelay.core import auth115, runtime


class FakeOpenClient:
    last_instances: list["FakeOpenClient"] = []
    fail: Exception | None = None
    calls: list[tuple] = []

    def __init__(self, access_token="", refresh_token="", app_id=0, console_qrcode=True):
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.app_id = app_id
        FakeOpenClient.last_instances.append(self)

    def fs_mkdir_open(self, payload):
        FakeOpenClient.calls.append(("mkdir", payload))
        if FakeOpenClient.fail:
            raise FakeOpenClient.fail
        return {"file_id": 777, "file_name": payload["file_name"]}

    def fs_rename_open(self, payload):
        FakeOpenClient.calls.append(("rename", payload))
        if FakeOpenClient.fail:
            raise FakeOpenClient.fail

    def fs_move_open(self, payload, pid=0):
        FakeOpenClient.calls.append(("move", payload))
        if FakeOpenClient.fail:
            raise FakeOpenClient.fail

    def fs_delete_open(self, payload):
        FakeOpenClient.calls.append(("delete", list(payload)))
        if FakeOpenClient.fail:
            raise FakeOpenClient.fail

    def fs_files_open(self, payload, **kwargs):
        if FakeOpenClient.fail:
            raise FakeOpenClient.fail
        return {"data": [{"id": 0}]}

    def refresh_access_token(self, async_=False, **kw):
        return {"access_token": "new-a", "refresh_token": "new-r"}


class FakeCookieClient:
    def __init__(self, cookies):
        self.cookies = cookies
        self.mkdirs: list[dict] = []
        self.renames: list = []
        self.moves: list = []
        self.deletes: list = []

    def fs_mkdir(self, payload):
        self.mkdirs.append(payload)
        return {"file_id": 999, "state": True}

    def fs_rename(self, payload):
        self.renames.append(payload)

    def fs_move(self, payload, pid=None):
        self.moves.append((payload, pid))

    def fs_delete(self, payload):
        self.deletes.append(list(payload))

    def fs_files(self, payload, **kwargs):
        return {"data": [{"id": 0}]}


def install_fake_p115(store: auth115.CredentialStore) -> tuple[FakeCookieClient, FakeOpenClient]:
    cookie = FakeCookieClient("UID=1;CID=2")
    open_client = FakeOpenClient("a", "r", 100195125)

    from p115client import check_response
    fake_module = type("M", (), {
        "check_response": staticmethod(check_response),
        "P115Client": lambda c: cookie,
        "P115OpenClient": FakeOpenClient,
    })()
    patcher = patch.dict("sys.modules", {"p115client": fake_module})
    patcher.start()
    store._cookie_client_obj = cookie  # type: ignore[attr-defined]
    store._open_client_obj = open_client  # type: ignore[attr-defined]

    orig_cookie = store.cookie_client
    orig_open = store.open_client
    store.cookie_client = lambda: cookie  # type: ignore[assignment]
    store.open_client = lambda: open_client  # type: ignore[assignment]
    store._teardown = patcher.stop  # type: ignore[attr-defined]
    store._orig_cookie = orig_cookie  # type: ignore[attr-defined]
    store._orig_open = orig_open  # type: ignore[attr-defined]
    return cookie, open_client


class TestCredentialStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cred = Path(self.tmp.name) / "creds.json"
        self.env = patch.dict(os.environ, {}, clear=True)
        self.env.start()
        FakeOpenClient.fail = None
        FakeOpenClient.calls = []
        FakeOpenClient.last_instances = []
        self.store = auth115.CredentialStore(self.cred)

    def tearDown(self):
        if hasattr(self.store, "_teardown"):
            self.store.cookie_client = self.store._orig_cookie  # type: ignore[attr-defined]
            self.store.open_client = self.store._orig_open  # type: ignore[attr-defined]
            self.store._teardown()  # type: ignore[attr-defined]
        self.tmp.cleanup()
        self.env.stop()

    def test_empty_store_status(self):
        s = self.store.status()
        self.assertFalse(s["cookie"]["configured"])
        self.assertFalse(s["oauth"]["configured"])
        self.assertEqual(s["writes_via"], "none")

    def test_cookie_persisted_and_loaded(self):
        self.store.set_cookies("UID=1; CID=2; SEID=3; KID=4")
        data = json.loads(self.cred.read_text())
        self.assertIn("UID=1", data["cookies"])
        # 新实例从同一文件读到 cookie
        again = auth115.CredentialStore(self.cred)
        self.assertTrue(again.has_cookie)
        self.assertEqual(again.cookie_state, auth115.AuthState.UNKNOWN)

    def test_env_cookie_is_not_read_at_runtime(self):
        # 115 凭证只在凭证文件里（管理台录入），不读环境变量
        with patch.dict(os.environ, {"REELAY_115_COOKIES": "UID=9;CID=8"}):
            store = auth115.CredentialStore(self.cred)
            self.assertFalse(store.has_cookie)

    def test_oauth_tokens_persisted(self):
        self.store.save_tokens("a1", "r1", 100195125)
        self.assertTrue(self.store.has_oauth)
        again = auth115.CredentialStore(self.cred)
        self.assertEqual(again.access_token, "a1")
        self.assertEqual(again.refresh_token, "r1")
        again.clear_oauth()
        self.assertFalse(auth115.CredentialStore(self.cred).has_oauth)

    def test_ping_states_active_expired_error(self):
        install_fake_p115(self.store)
        self.store.set_cookies("UID=1;CID=2")
        self.assertEqual(self.store.ping_cookie(force=True), auth115.AuthState.ACTIVE)
        # 模拟失效
        client = self.store.cookie_client()
        def boom(_, **kwargs):
            raise RuntimeError("errno: 99 请重新登录")
        client.fs_files = boom
        self.assertEqual(self.store.ping_cookie(force=True), auth115.AuthState.EXPIRED)
        def oops(_, **kwargs):
            raise TimeoutError("network down")
        client.fs_files = oops
        self.assertEqual(self.store.ping_cookie(force=True), auth115.AuthState.ERROR)

    # ---------- 混合写操作：OAuth 优先，失败回退 cookie ---------- #
    def test_mkdir_prefers_oauth(self):
        cookie, open_client = install_fake_p115(self.store)
        self.store.save_tokens("a", "r", 100195125)
        made = self.store.mkdir(1, "x")
        self.assertEqual(made, {"id": 777, "name": "x"})
        self.assertEqual(cookie.mkdirs, [])
        self.assertEqual(FakeOpenClient.calls[0][0], "mkdir")

    def test_writes_fallback_to_cookie_when_oauth_fails(self):
        cookie, _ = install_fake_p115(self.store)
        self.store.save_tokens("a", "r", 100195125)
        FakeOpenClient.fail = RuntimeError("[40140125] token invalid")
        made = self.store.mkdir(1, "x")
        self.assertEqual(made["id"], 999)
        self.assertEqual(cookie.mkdirs, [{"cname": "x", "pid": 1}])

        self.store.rename(5, "n.mkv")
        self.assertEqual(cookie.renames, [(5, "n.mkv")])
        self.store.move(5, 9)
        self.assertEqual(cookie.moves, [(5, 9)])
        self.store.delete([5])
        self.assertEqual(cookie.deletes, [[5]])

    def test_writes_use_cookie_without_oauth(self):
        cookie, _ = install_fake_p115(self.store)
        self.assertFalse(self.store.has_oauth)
        made = self.store.mkdir(1, "y")
        self.assertEqual(made["id"], 999)
        self.store.rename(1, "a.mkv")
        self.store.move(1, 2)
        self.store.delete([1])
        self.assertEqual(len(cookie.mkdirs), 1)
        self.assertEqual(len(cookie.renames), 1)
        self.assertEqual(len(cookie.moves), 1)
        self.assertEqual(len(cookie.deletes), 1)

    def test_write_without_any_credential_raises(self):
        with self.assertRaises(RuntimeError):
            self.store.mkdir(1, "z")
        with self.assertRaises(RuntimeError):
            self.store.rename(1, "z")
        with self.assertRaises(RuntimeError):
            self.store.move(1, 2)
        with self.assertRaises(RuntimeError):
            self.store.delete([1])

    def test_oauth_ping_active_and_expired(self):
        _, open_client = install_fake_p115(self.store)
        self.store.save_tokens("a", "r", 100195125)
        self.assertEqual(self.store.ping_oauth(force=True), auth115.AuthState.ACTIVE)
        FakeOpenClient.fail = RuntimeError("[40140125]")
        self.assertEqual(self.store.ping_oauth(force=True), auth115.AuthState.EXPIRED)

    def test_qr_start_and_status_without_network_using_fakes(self):
        # 用假的开放平台模块驱动 qr_start / qr_status 状态机
        store = self.store

        class FakeP115Open:
            @staticmethod
            def login_qrcode_token_open(app_id):
                return {"code": 0, "data": {"uid": "u1", "time": 1, "sign": "s",
                                            "qrcode": "https://115.com/scan/dg-u1"}}

            @staticmethod
            def login_qrcode_scan_status(payload):
                return {"data": {"status": 1}}

            @staticmethod
            def login_qrcode_access_token_open(uid):
                return {"data": {"access_token": "qa", "refresh_token": "qr"}}

        fake = type("M", (), {"P115OpenClient": FakeP115Open})()
        with patch.dict("sys.modules", {"p115client": fake}):
            info = store.qr_start()
            self.assertEqual(info["uid"], "u1")
            r = store.qr_status("u1")
            self.assertEqual(r["status"], 1)
            self.assertFalse(r["authenticated"])
            # status=2 时落 token
            FakeP115Open.login_qrcode_scan_status = staticmethod(
                lambda payload: {"data": {"status": 2}})
            r2 = store.qr_status("u1")
            self.assertTrue(r2["authenticated"])
            self.assertTrue(store.has_oauth)

    def test_cookie_qr_start_and_status_persists_cookie(self):
        store = self.store

        class FakeP115Cookie:
            @staticmethod
            def login_qrcode_token():
                return {"state": 1, "data": {"uid": "cu1", "time": 1, "sign": "s"}}

            @staticmethod
            def login_qrcode_scan_status(payload):
                return {"state": 1, "data": {"status": 2}}

            @staticmethod
            def login_qrcode_scan_result(uid, app):
                self.assertEqual((uid, app), ("cu1", "tv"))
                return {"state": 1, "data": {"cookie": {
                    "UID": "1_A1_2", "CID": "c", "SEID": "s", "KID": "k",
                }}}

        fake = type("M", (), {"P115Client": FakeP115Cookie})()
        with patch.dict("sys.modules", {"p115client": fake}), \
                patch.object(store, "ping_cookie", return_value=auth115.AuthState.ACTIVE):
            info = store.cookie_qr_start()
            self.assertEqual(info["app"], "tv")
            result = store.cookie_qr_status("cu1")
            self.assertTrue(result["authenticated"])
            self.assertIn("UID=1_A1_2", store.cookies)
            self.assertEqual(self.cred.stat().st_mode & 0o777, 0o600)

    def test_managed_client_applies_lane_gate_and_enters_cooldown(self):
        class Gate:
            qps = auth115.QPS_DEFAULTS["open"]

            def __init__(self):
                self.calls = 0

            def acquire(self):
                self.calls += 1

        class Base:
            def request(self, url, method="GET", payload=None, *, async_=False, **kwargs):
                return {"code": 429, "message": "操作频繁"}

        gate = Gate()
        self.store._gates["open"] = gate
        managed = self.store._managed_client_class(Base)()
        before = auth115.time.time()
        result = managed.request("https://proapi.115.com/open/ufile/files")
        self.assertEqual(result["code"], 429)
        self.assertEqual(gate.calls, 1)
        self.assertGreaterEqual(self.store._cooldown_until, before + 1)

    def test_rate_limits_follow_system_config_without_restart(self):
        self.assertIsNone(auth115.CredentialStore().path)  # 没指定文件时只在内存里，不读本机的真实凭证
        self.assertEqual(self.store.status()["traffic"]["share_qps"], 1.0)
        with runtime.scope({"p115": {"share_qps": 0.5, "risk_cooldown_sec": 60}}):
            self.assertEqual(self.store.status()["traffic"]["share_qps"], 0.5)
            self.assertEqual(self.store._cooldown_sec, 60)
            self.store._wait_for_request("share")  # 限流器按新的速率工作
            self.assertEqual(self.store._gates["share"].interval, 2.0)

    def test_rate_lane_and_risk_cooldown(self):
        self.assertEqual(self.store._request_lane("https://proapi.115.com/open/ufile/files"), "open")
        self.assertEqual(self.store._request_lane("https://webapi.115.com/share/receive"), "share")
        self.assertEqual(self.store._request_lane("https://webapi.115.com/files"), "cookie")
        before = auth115.time.time()
        self.store._after_request({"code": 429, "message": "操作频繁"})
        self.assertGreaterEqual(self.store._cooldown_until, before + 1)
        self.assertTrue(self.store.status()["traffic"]["cooling_down"])

    def test_qr_expired_session_removed(self):
        store = self.store

        class FakeP115Open:
            @staticmethod
            def login_qrcode_token_open(app_id):
                return {"code": 0, "data": {"uid": "u2", "time": 1, "sign": "s",
                                            "qrcode": "https://115.com/scan/dg-u2"}}

            @staticmethod
            def login_qrcode_scan_status(payload):
                return {"data": {"status": -1}}

        fake = type("M", (), {"P115OpenClient": FakeP115Open})()
        with patch.dict("sys.modules", {"p115client": fake}):
            store.qr_start()
            r = store.qr_status("u2")
            self.assertEqual(r["status"], -1)
            with self.assertRaises(KeyError):
                store.qr_status("u2")


if __name__ == "__main__":
    unittest.main()
