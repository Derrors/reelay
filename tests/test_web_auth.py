"""管理接口的访问守卫：登录、会话、本机限制与跨站写请求。"""
from __future__ import annotations

import unittest
import time

from reelay.core.contracts import ParsedResource, TaskState

from tests.fakes import LOCAL, SHARE, TestClient, WebCase, message


class TestWebAuthGuard(WebCase):
    SECRETS = {"web_password": "pw"}

    def api_routes(self):
        def walk(routes):  # include_router 后的子路由挂在 original_router 下
            for route in routes:
                child = getattr(route, "original_router", None)
                yield from walk(child.routes) if child is not None else (route,)

        for route in walk(self.app.routes):
            path = getattr(route, "path", "")
            if path.startswith("/api/"):
                for method in sorted(getattr(route, "methods", set()) - {"HEAD", "OPTIONS"}):
                    yield method, path.replace("{task_id}", "x").replace("{delivery_id}", "1") \
                        .replace("{pickcode}", "abc")

    def test_every_non_public_api_route_requires_login(self):
        from reelay.web.auth import PUBLIC_PATHS
        checked = 0
        for method, path in self.api_routes():
            if path in PUBLIC_PATHS:
                continue
            response = self.client.request(method, path)
            self.assertEqual(response.status_code, 401, f"{method} {path}")
            checked += 1
        self.assertGreater(checked, 30)

    def test_login_grants_access_and_forged_or_expired_sessions_fail(self):
        self.client.cookies.set("reelay_session", "forged")
        self.assertEqual(self.client.get("/api/events").status_code, 401)
        self.client.cookies.clear()
        self.assertEqual(self.client.get("/api/auth/status").json(), {"auth_required": True, "authenticated": False})
        self.assertEqual(self.client.post("/api/auth/login", json={"password": "pw"}).status_code, 200)
        self.assertEqual(self.client.get("/api/auth/status").json()["authenticated"], True)
        self.assertEqual(self.client.get("/api/config").status_code, 200)
        # 会话存在数据库里：Web 重启（同一数据目录新建应用）后仍然有效
        from reelay.web import create_app
        cookie = self.client.cookies.get("reelay_session")
        stored = [r["token_hash"] for r in self.app.state.store._conn.execute("SELECT * FROM web_sessions")]
        self.assertEqual(len(stored), 1)
        self.assertNotIn(cookie, stored)  # 数据库里只有哈希
        with TestClient(create_app(data_dir=self.root), base_url=LOCAL) as restarted:
            restarted.cookies.set("reelay_session", cookie)
            self.assertEqual(restarted.get("/api/config").status_code, 200)
        with self.app.state.store._conn as conn:
            conn.execute("UPDATE web_sessions SET expires_at=?", (time.time() - 1,))
        self.assertEqual(self.client.get("/api/config").status_code, 401)
        self.assertEqual(list(self.app.state.store._conn.execute("SELECT * FROM web_sessions")), [])

    def test_login_is_throttled_after_repeated_failures(self):
        codes = [self.client.post("/api/auth/login", json={"password": "bad"}).status_code for _ in range(6)]
        self.assertEqual(codes, [401] * 5 + [429])
        self.assertEqual(self.client.post("/api/auth/login", json={"password": "pw"}).status_code, 429)

    def test_parse_preview_matches_pipeline_decision(self):
        self.client.post("/api/auth/login", json={"password": "pw"})
        response = self.client.post("/api/sources/test-parse",
                                    json={"text": f"📺 剧 (2026) 第3集\n{SHARE}", "urls": [SHARE]})
        body = response.json()
        self.assertEqual(response.status_code, 200, body)
        self.assertEqual(body["parsed"]["title"], "剧")
        self.assertEqual(body["decision"], "ask")  # 未配 LLM：降级结果只能人工确认，而不是被当作“非影视”跳过
        self.assertLess(body["confidence"]["score"], 0.5)

    def test_stats_counts_and_size_use_app_store(self):
        self.client.post("/api/auth/login", json={"password": "pw"})
        store = self.app.state.store
        for size in ("1.5 GB", "512 MB"):
            ctx = message(msg_id=size)
            ctx.parsed = ParsedResource(is_media=True, title="片", size=size)
            ctx.state = TaskState.DONE
            store.save_task(ctx)
        body = self.client.get("/api/stats").json()
        self.assertEqual(body["counts"], {"done": 2})
        self.assertEqual(body["estimated_done_gb"], 2.0)
        self.assertEqual(self.client.get("/api/library").status_code, 200)
        self.assertEqual(self.client.get("/api/stats").status_code, 200)  # 共享连接未被关闭

    def test_health_hides_runtime_details_without_login(self):
        self.assertEqual(self.client.get("/api/health").json(), {"ok": True, "service": "reelay"})



class TestLocalOnlyAndCrossSite(WebCase):
    """没设登录密码时：只接受本机地址的 Host（挡住局域网访问与 DNS 重绑定），跨站写请求一律拒绝。"""

    def test_non_local_hosts_are_refused_until_a_password_is_set(self):
        for host in ("192.168.1.5:28080", "nas.example", "evil.example:28080"):
            with self.subTest(host=host):
                response = self.client.get("/api/config", headers={"Host": host})
                self.assertEqual(response.status_code, 403)
                self.assertIn("设置 → 运行与安全", response.json()["detail"])
        for host in ("127.0.0.1:28080", "localhost", "[::1]:28080"):
            with self.subTest(host=host):
                self.assertEqual(self.client.get("/api/config", headers={"Host": host}).status_code, 200)
        # 容器健康检查不受限制；接口文档与其他 /api/* 一样受保护
        self.assertEqual(self.client.get("/api/health", headers={"Host": "nas.example"}).status_code, 200)
        self.assertEqual(self.client.get("/api/docs", headers={"Host": "nas.example"}).status_code, 403)
        self.assertEqual(self.client.get("/api/docs").status_code, 200)

    def test_cross_site_writes_are_refused(self):
        def validate(**headers):
            return self.client.post("/api/config/validate", json={"config": {}}, headers=headers).status_code

        self.assertEqual(validate(), 200)  # 命令行工具等不带这两个请求头
        self.assertEqual(validate(**{"Sec-Fetch-Site": "same-origin"}), 200)
        self.assertEqual(validate(**{"Sec-Fetch-Site": "cross-site"}), 403)
        self.assertEqual(validate(**{"Sec-Fetch-Site": "same-site"}), 403)  # 同一台 NAS 上其他端口的页面
        self.assertEqual(validate(Origin="http://127.0.0.1"), 200)
        self.assertEqual(validate(Origin="http://evil.example"), 403)
        self.assertEqual(self.client.get("/api/config", headers={"Sec-Fetch-Site": "cross-site"}).status_code, 200)


class TestMountedUnderPrefix(WebCase):
    SECRETS = {"web_password": "pw"}
    ROOT_PATH = "/reelay"

    def test_prefix_does_not_bypass_the_guard(self):
        self.assertEqual(self.client.get("/reelay/api/config").status_code, 401)
        self.assertEqual(self.client.post("/reelay/api/auth/login", json={"password": "pw"}).status_code, 200)
        self.assertEqual(self.client.get("/reelay/api/config").status_code, 200)


if __name__ == "__main__":
    unittest.main()
