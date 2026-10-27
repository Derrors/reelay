"""网络代理：配置校验、各项服务按开关走代理、代理密码绑定地址，以及管理台「测试代理」。

经代理的请求发给本机的假 HTTP 代理（FakeProxy），用真实的 httpx2 / OpenAI 客户端，不联网。
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import socket
import threading
import unittest
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from reelay.core import ai, proxy, runtime, secret_store, tmdb
from reelay.core.config import normalize_config

from tests.fakes import WebCase


def with_proxy(url: str, **switches) -> dict:
    return normalize_config({"system": {"proxy": {"url": url, **switches}}})


class FakeProxy:
    """只认绝对地址请求（GET http://host/path）的 HTTP 代理：记下请求与 Proxy-Authorization，回固定的 JSON。"""

    def __init__(self, body: dict, status: int = 200):
        seen = self.seen = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                seen.append((self.path, self.headers.get("Proxy-Authorization")))
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def closed_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def basic(user: str, password: str) -> str:
    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()


class TestProxyConfig(unittest.TestCase):
    def test_defaults_to_no_proxy(self):
        cfg = normalize_config({})
        self.assertEqual(cfg["proxy"], {"url": "", "telegram": True, "tmdb": True, "ai": True})
        self.assertIsNone(proxy.url_for("tmdb", cfg))
        self.assertIsNone(proxy.telethon(cfg))

    def test_address_is_validated_and_normalized(self):
        self.assertEqual(with_proxy(" socks5://127.0.0.1:1080/ ")["proxy"]["url"], "socks5://127.0.0.1:1080")
        for url, message in (("ftp://h:1", "http:// 或 socks5://"), ("127.0.0.1:7890", "http:// 或 socks5://"),
                             ("http://h", "端口"), ("http://h:99999", "端口"), ("http://u:p@h:1", "代理密码"),
                             ("http://h:1/path", "只写到端口")):
            with self.subTest(url=url), self.assertRaisesRegex(ValueError, message):
                with_proxy(url)
        with self.assertRaisesRegex(ValueError, "proxy.tmdb"):
            with_proxy("http://h:1", tmdb="yes")

    def test_each_service_follows_its_switch(self):
        cfg = with_proxy("http://h:7890", ai=False)
        with secret_store.override(proxy_password=None):
            self.assertEqual(proxy.url_for("tmdb", cfg), "http://h:7890")
            self.assertIsNone(proxy.url_for("ai", cfg))
            with runtime.scope(cfg):  # 省略 cfg 时读当前有效配置
                self.assertEqual(proxy.url_for("telegram"), "http://h:7890")

    def test_saved_password_is_added_only_with_a_username(self):
        with secret_store.override(proxy_password="p@ss:word"):
            self.assertEqual(proxy.url_for("tmdb", with_proxy("http://h:1")), "http://h:1")
            url = proxy.url_for("tmdb", with_proxy("socks5h://me@h:1080"))
            self.assertEqual(url, "socks5h://me:p%40ss%3Aword@h:1080")
            self.assertEqual(proxy.telethon(with_proxy("socks5h://me@h:1080")), {
                "proxy_type": "socks5", "addr": "h", "port": 1080, "username": "me", "password": "p@ss:word",
                "rdns": True})
            self.assertEqual(proxy.telethon(with_proxy("http://h:7890"))["proxy_type"], "http")


class TestRequestsUseTheProxy(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        env = patch.dict(os.environ, {}, clear=True)  # 不受本机的 HTTP(S)_PROXY 影响
        env.start()
        self.addCleanup(env.stop)
        tmdb._CACHE.clear()

    def fake_proxy(self, body: dict, status: int = 200) -> FakeProxy:
        server = FakeProxy(body, status)
        self.addCleanup(server.close)
        return server

    def test_tmdb_goes_through_the_proxy_with_its_password(self):
        server = self.fake_proxy({"results": [{"id": 7, "title": "流浪地球2"}]})
        cfg = with_proxy(server.url.replace("//", "//me@"))
        cfg["tmdb"]["base_url"] = "http://tmdb.test/3"
        with secret_store.override(tmdb_api_key="k", proxy_password="pw"), runtime.scope(cfg):
            results = tmdb.search("movie", "流浪地球2", None, language="zh-CN", timeout=5)
        self.assertEqual(results[0]["id"], 7)
        path, auth = server.seen[0]
        self.assertTrue(path.startswith("http://tmdb.test/3/search/movie?"))
        self.assertEqual(auth, basic("me", "pw"))

    def test_tmdb_errors_through_the_proxy_do_not_reveal_the_key(self):
        server = self.fake_proxy({}, status=401)
        cfg = with_proxy(server.url)
        cfg["tmdb"]["base_url"] = "http://tmdb.test/3"
        with secret_store.override(tmdb_api_key="tmdb-secret"), runtime.scope(cfg), \
                self.assertRaises(urllib.error.HTTPError) as caught:
            tmdb.search("movie", "x", None, language="zh-CN", timeout=5)
        self.assertEqual(caught.exception.code, 401)  # 与直连时同样的异常
        self.assertNotIn("tmdb-secret", f"{caught.exception} {caught.exception.url}")

    async def test_ai_client_goes_through_the_proxy_and_is_rebuilt_when_it_changes(self):
        server = self.fake_proxy({"object": "list", "data": [{"id": "m1", "object": "model"}]})
        cfg = with_proxy(server.url)
        cfg["llm"]["base_url"] = "http://llm.test/v1"
        with secret_store.override(llm_api_key="sk-test"), runtime.scope(cfg):
            client, _ = ai.make_client()
            page = await client.models.list()
            self.assertEqual([m.id for m in page.data], ["m1"])
            self.assertEqual(server.seen[0][0], "http://llm.test/v1/models")
            self.assertIs(ai.make_client()[0], client)
            cfg["proxy"]["ai"] = False  # 关掉 AI 的代理：换一个直连的客户端
            self.assertIsNot(ai.make_client()[0], client)

    async def test_probe_reports_each_service(self):
        server = self.fake_proxy({}, status=401)  # 有回应就算连得上，哪怕未授权
        results = await proxy.probe(server.url, ["tmdb", "ai"], tmdb_url="http://tmdb.test/3",
                                    llm_url="http://llm.test/v1", api_id=None, api_hash=None)
        self.assertEqual([(r["service"], r["ok"]) for r in results], [("tmdb", True), ("ai", True)])
        self.assertEqual({path for path, _ in server.seen},
                         {"http://tmdb.test/3/configuration", "http://llm.test/v1/models"})

    async def test_probe_reports_an_unreachable_proxy_without_its_password(self):
        port = closed_port()
        results = await proxy.probe(f"http://me:hunter2@127.0.0.1:{port}", ["telegram", "tmdb", "ai"],
                                    tmdb_url="http://tmdb.test/3", llm_url="http://llm.test/v1",
                                    api_id=None, api_hash=None)
        self.assertEqual(len(results), 3)
        for item in results:
            self.assertFalse(item["ok"])
            self.assertIn(f"连不上代理 127.0.0.1:{port}", item["error"])
            self.assertNotIn("hunter2", item["error"])


class TestTelegramClientsUseTheProxy(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    async def connected(source):
        while source.health()["status"] != "connected":
            await asyncio.sleep(0.01)

    async def test_listener_reads_the_proxy_on_each_attempt(self):
        from reelay.providers.source import TelegramUserbotSource
        from tests.test_source import TestTelegramListen

        cfg = normalize_config({})
        previous = runtime._PROCESS
        runtime.set_process_config(cfg)
        self.addCleanup(runtime.set_process_config, previous)
        proxies = []

        def factory(*args, proxy=None, **kwargs):
            proxies.append(proxy)

            async def offline(client):
                if proxy is None:
                    raise ConnectionError("blocked")
            return TestTelegramListen.FakeClient(on_connect=offline)

        source = TelegramUserbotSource()
        with TestTelegramListen.fake_telethon(factory), patch("reelay.providers.source.RETRY_SECONDS", 0.02), \
                secret_store.override(telegram_api_id="1", telegram_api_hash="dummy", proxy_password=None):
            listener = asyncio.create_task(source.listen(lambda raw: None))
            while "error" not in source.health():
                await asyncio.sleep(0.01)
            self.assertEqual(source.health()["status"], "disconnected")
            self.assertIn("网络代理", source.health()["error"])  # 直连不通时提示配置代理
            cfg["proxy"]["url"] = "socks5://127.0.0.1:1080"  # 管理台保存后，下次重试就用上
            await asyncio.wait_for(self.connected(source), 2)
            listener.cancel()
            await asyncio.gather(listener, return_exceptions=True)
        self.assertIsNone(proxies[0])
        self.assertEqual(proxies[-1]["addr"], "127.0.0.1")


class TestProxySettingsApi(WebCase):
    SECRETS = {"proxy_password": "old-pw"}
    CONFIG = {**WebCase.CONFIG, "proxy": {"url": "http://me@10.0.0.2:7890"}}

    def save_proxy(self, **changes):
        cfg = self.client.get("/api/config").json()
        res = self.client.patch("/api/config/sections", json={
            "revision": cfg["revision"], "sections": {"proxy": {**cfg["config"]["proxy"], **changes}}})
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()

    def test_password_is_a_secret_bound_to_the_address(self):
        body = self.client.get("/api/config").json()
        self.assertTrue(body["secrets"]["proxy_password"]["set"])
        self.assertNotIn("old-pw", json.dumps(body))
        self.assertEqual(self.save_proxy(ai=False)["cleared_secrets"], [])  # 地址没变：密码保留
        self.assertEqual(self.save_proxy(url="http://me@10.0.0.3:7890")["cleared_secrets"], ["proxy_password"])
        self.assertFalse(self.client.get("/api/config").json()["secrets"]["proxy_password"]["set"])

    def test_saved_password_is_only_sent_to_the_saved_address(self):
        calls = []

        async def probe(url, services, **kwargs):
            calls.append((url, services))
            return [{"service": s, "ok": True} for s in services]

        with patch.object(proxy, "probe", probe):
            res = self.client.post("/api/proxy/test", json={"url": "http://me@10.0.0.2:7890/"})
            self.assertEqual(res.status_code, 200, res.text)
            self.assertEqual(calls[-1], ("http://me:old-pw@10.0.0.2:7890", ["telegram", "tmdb", "ai"]))
            self.client.post("/api/proxy/test", json={"url": "http://me@10.0.0.9:7890", "services": ["tmdb"]})
            self.assertEqual(calls[-1], ("http://me@10.0.0.9:7890", ["tmdb"]))  # 换了地址：不带已保存的密码
            self.client.post("/api/proxy/test", json={"url": "http://me@10.0.0.9:7890", "password": "new"})
            self.assertEqual(calls[-1][0], "http://me:new@10.0.0.9:7890")
            for payload in ({"url": ""}, {"url": "ftp://h:1"}, {"url": "http://h:1", "services": []}):
                self.assertEqual(self.client.post("/api/proxy/test", json=payload).status_code, 422, payload)
        self.assertEqual(len(calls), 3)


if __name__ == "__main__":
    unittest.main()
