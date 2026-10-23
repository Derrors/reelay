"""配置分组（system / plugins）、首次启动的默认配置、密钥存储与设置接口。全部在临时目录中。"""
from __future__ import annotations

import json
import os
import stat
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from reelay.core import secret_store
from reelay.core.config import ensure_config, load_config, normalize_config, render, to_file
from reelay.core.registry import Registry, discover

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover
    TestClient = None

SAMPLE = {
    "system": {"save_dirs": {"剧集": "2", "默认": "1"}, "defaults": {"save_to": "默认"},
               "tmdb": {"language": "en-US"}, "subscriptions": [{"title": "剧", "season": 1}]},
    "plugins": {"organizer": {"conflict": "skip"}, "daily_report": {"enabled": True, "narrative": False}},
}


class TestFileStructure(unittest.TestCase):
    def test_layout_settings_are_validated(self):
        for organizer, message in (({"layout": "flat"}, "layout"),
                                   ({"type_dirs": {"movie": "电影/外语", "tv": "剧集", "anime": ""}}, "单层文件夹名"),
                                   ({"type_dirs": {"movie": "", "tv": "剧集", "anime": ""}}, "非空")):
            with self.subTest(organizer=organizer), self.assertRaisesRegex(ValueError, message):
                normalize_config({"plugins": {"organizer": organizer}})

    def test_file_round_trip_keeps_the_same_config(self):
        cfg = normalize_config(SAMPLE)
        self.assertEqual((cfg["tmdb"]["language"], cfg["plugins"]["organizer"]["conflict"]), ("en-US", "skip"))
        self.assertIn("tv", cfg["plugins"]["organizer"]["naming"])  # 内置插件补齐默认设置
        data = to_file(cfg)
        self.assertEqual(set(data), {"system", "plugins"})
        with self.assertRaisesRegex(ValueError, "无法识别的配置项：audit"):
            normalize_config({**SAMPLE, "audit": {"level": "info"}})
        self.assertIn("subscriptions", data["system"])
        self.assertNotIn("plugins", data["system"])
        text = render(cfg)
        self.assertTrue(text.startswith("# Reelay 配置"))
        self.assertEqual(normalize_config(yaml.safe_load(text)), cfg)

    def test_example_config_is_valid(self):
        cfg = load_config(Path(__file__).resolve().parents[1] / "config.example.yaml")
        discover().validate_config(cfg)

    def test_new_sections_are_validated(self):
        for bad in ({"llm": {"base_url": "ftp://x"}}, {"llm": {"model": " "}}, {"tmdb": {"timeout": 0}},
                    {"telegram": {"owner_id": "abc"}}, {"runtime": {"concurrency": 0}},
                    {"runtime": {"log_level": "TRACE"}}, {"p115": {"share_qps": 0}},
                    {"plugins": {"organizer": {"enabled": "yes"}}}, {"plugins": {"organizer": True, "x": 1}},
                    {"system": {"defaults": {}}, "defaults": {}}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                normalize_config(bad)
        cfg = normalize_config({"telegram": {"owner_id": 12345}, "llm": {"base_url": "https://api.example.com/v1/"}})
        self.assertEqual((cfg["telegram"]["owner_id"], cfg["llm"]["base_url"]), ("12345", "https://api.example.com/v1"))

    def test_plugin_switch_and_field_paths(self):
        registry = discover()
        opt_in = types.SimpleNamespace(name="opt_in", default_enabled=False)  # 默认关闭的扩展
        self.assertFalse(Registry.is_enabled(opt_in, normalize_config({})))
        self.assertTrue(Registry.is_enabled(opt_in, normalize_config({"plugins": {"opt_in": {"enabled": True}}})))
        catalog = registry.catalog(normalize_config({}))
        daily = next(x for x in catalog["events"] if x["name"] == "daily_report")
        self.assertEqual((daily["config_section"], daily["fields"][0]["path"]),
                         ("plugins.daily_report", "plugins.daily_report.narrative"))
        with self.assertRaises(ValueError):  # 插件字段按声明的类型校验
            registry.validate_config(normalize_config({"plugins": {"daily_report": {"narrative": "no"}}}))


class TestPluginSettings(unittest.TestCase):
    def test_builtin_plugin_settings_live_in_the_plugin_directory(self):
        # 内置插件的默认设置与校验在 reelay/plugins/<名>/settings.py，core 按目录找到它们
        from reelay.core.config import builtin_settings
        from reelay.plugins.organizer import settings
        self.assertIs(builtin_settings()["organizer"], settings)
        organizer = normalize_config({})["plugins"]["organizer"]
        self.assertEqual(organizer, settings.DEFAULTS)
        with self.assertRaisesRegex(ValueError, "conflict"):
            normalize_config({"plugins": {"organizer": {"conflict": "keep"}}})
        with self.assertRaisesRegex(ValueError, "naming.movie"):
            normalize_config({"plugins": {"organizer": {"naming": {"movie": "{{ title.__class__ }}.{{ ext }}"}}}})

    def test_extensions_bound_to_a_storage_reject_other_storages(self):
        class LocalStorage:
            name = "local"

        registry = discover()
        registry.add_provider("storage", LocalStorage())
        cfg = normalize_config({"save_dirs": {"默认": "1"}, "sources": [{"bot": "@local", "storage": "local"}]})
        with self.assertRaisesRegex(ValueError, "organizer 仅支持 p115"):
            registry.validate_config(cfg)
        cfg["plugins"]["organizer"]["enabled"] = False
        registry.validate_config(cfg)


class TestSecretStore(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "secrets.json"

    def tearDown(self):
        self.temp.cleanup()

    def test_values_are_private_reloaded_and_password_is_hashed(self):
        store = secret_store.SecretStore(self.path)
        store.update({"llm_api_key": "sk-1", "web_password": "pw"})
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        raw = json.loads(self.path.read_text())
        self.assertNotIn("pw", raw["values"]["web_password"])
        self.assertTrue(store.check_password("pw"))
        self.assertFalse(store.check_password("other"))
        # 另一个进程写入后按修改时间重新读取
        other = secret_store.SecretStore(self.path)
        os.utime(self.path, (1, 1))
        other.update({"llm_api_key": None})
        self.assertIsNone(store.get("llm_api_key"))
        self.assertEqual(store.status()["web_password"], True)
        with self.assertRaises(ValueError):
            store.update({"unknown": "x"})
        with self.assertRaises(ValueError):
            store.update({"telegram_api_id": "abc"})

    def test_nothing_is_configured_until_an_entry_point_loads_secrets(self):
        with patch.object(secret_store, "_current", None):
            self.assertIsNone(secret_store.get("llm_api_key"))
        with secret_store.override(llm_api_key="k"):
            self.assertEqual(secret_store.get("llm_api_key"), "k")


class TestEnsureConfig(unittest.TestCase):
    def test_default_config_is_written_once(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "data" / "config.yaml"
            ensure_config(path)
            self.assertEqual(load_config(path), normalize_config({}))
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            path.write_text(render(normalize_config(SAMPLE)), encoding="utf-8")
            ensure_config(path)  # 已存在：不覆盖
            self.assertEqual(load_config(path)["tmdb"]["language"], "en-US")


class TestSettingsApi(unittest.TestCase):
    def setUp(self):
        from reelay.web import create_app
        self.env = patch.dict(os.environ, {}, clear=True)
        self.env.start()
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        secret_store.SecretStore(root / "secrets.json").update({"tmdb_api_key": "tmdb-key"})
        (root / "config.yaml").write_text(json.dumps({"save_dirs": {"默认": "0"}, "defaults": {"save_to": "默认"}}),
                                          encoding="utf-8")
        self.app = create_app(data_dir=root)
        self.client = TestClient(self.app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.temp.cleanup()
        self.env.stop()

    def test_secrets_are_set_but_never_returned(self):
        body = self.client.get("/api/config").json()
        self.assertTrue(body["secrets"]["tmdb_api_key"]["set"])
        self.assertFalse(body["secrets"]["llm_api_key"]["set"])
        self.assertNotIn("tmdb-key", json.dumps(body))
        self.assertEqual(body["text"], (Path(self.temp.name) / "config.yaml").read_text(encoding="utf-8"))  # 配置原文
        res = self.client.put("/api/settings/secrets", json={"changes": {"llm_api_key": "sk-xyz"}})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertTrue(res.json()["llm_api_key"]["set"])
        self.assertTrue(self.client.get("/api/dashboard").json()["connections"]["llm"]["configured"])
        self.assertNotIn("sk-xyz", self.client.get("/api/config").text)
        self.assertEqual(self.client.put("/api/settings/secrets", json={"changes": {"nope": "x"}}).status_code, 422)

    def test_password_change_requires_the_current_one_and_resets_sessions(self):
        self.assertEqual(self.client.put("/api/settings/secrets", json={"changes": {"web_password": "one"}}).status_code, 200)
        self.assertEqual(self.client.get("/api/config").status_code, 200)  # 当前页面换了新会话继续使用
        other = TestClient(self.app)
        self.assertEqual(other.get("/api/config").status_code, 401)
        self.assertEqual(self.client.put("/api/settings/secrets", json={"changes": {"web_password": "two"},
                                                                         "current_password": "bad"}).status_code, 409)
        self.assertEqual(self.client.put("/api/settings/secrets", json={"changes": {"web_password": "two"},
                                                                         "current_password": "one"}).status_code, 200)
        self.assertEqual(other.post("/api/auth/login", json={"password": "one"}).status_code, 401)
        self.assertEqual(other.post("/api/auth/login", json={"password": "two"}).status_code, 200)

    def test_system_and_plugin_sections_save_back_to_the_file(self):
        cfg = self.client.get("/api/config").json()
        plugins = {**cfg["config"]["plugins"], "daily_report": {"enabled": True, "narrative": False}}
        res = self.client.patch("/api/config/sections", json={"revision": cfg["revision"], "sections": {
            "llm": {"base_url": "https://llm.example/v1", "model": "m"}, "plugins": plugins}})
        self.assertEqual(res.status_code, 200, res.text)
        data = yaml.safe_load(self.client.get("/api/config").json()["text"].split("\n", 3)[-1])
        self.assertEqual(data["system"]["llm"]["model"], "m")
        self.assertEqual(data["plugins"]["daily_report"], {"enabled": True, "narrative": False})
        cfg = self.client.get("/api/config").json()
        bad = {**cfg["config"]["plugins"], "typo_plugin": {"enabled": True}}
        res = self.client.patch("/api/config/sections", json={"revision": cfg["revision"], "sections": {"plugins": bad}})
        self.assertEqual(res.status_code, 422)
        self.assertIn("typo_plugin", res.text)


class FakeModels:
    def __init__(self, ids):
        self.ids = ids

    async def list(self):
        return type("Page", (), {"data": [type("M", (), {"id": i})() for i in self.ids]})()


class EffortRejected(Exception):
    """与 openai.BadRequestError 一样带 status_code。"""
    status_code = 400


class FakeLLM:
    """OpenAI 兼容客户端的替身：记下请求参数；error 不为空时抛出；accepts 之外的推理强度按 HTTP 400 拒绝。"""

    def __init__(self, ids=(), error=None, accepts=None):
        self.models, self.calls, self.error, self.accepts = FakeModels(list(ids)), [], error, accepts
        self.chat = type("Chat", (), {"completions": self})()

    async def create(self, **params):
        self.calls.append(params)
        if self.error:
            raise self.error
        effort = params.get("reasoning_effort")
        if effort and self.accepts is not None and effort not in self.accepts:
            raise EffortRejected("reasoning_effort must be low, high, xhigh, max")
        message = type("Msg", (), {"content": "正常", "refusal": None})()
        usage = type("Usage", (), {"prompt_tokens": 12, "completion_tokens": 30})()
        return type("Resp", (), {"choices": [type("C", (), {"message": message})()], "usage": usage, "model": params["model"]})()


@unittest.skipIf(TestClient is None, "未安装 web 可选依赖")
class TestLlmSettings(unittest.TestCase):
    setUp = TestSettingsApi.setUp  # 只复用环境，不重复跑设置接口的用例
    tearDown = TestSettingsApi.tearDown

    def test_model_list_and_connection_test_use_the_form_values(self):
        from reelay.core import ai
        self.assertEqual(self.client.post("/api/ai/models", json={}).status_code, 409)  # 还没有密钥
        self.client.put("/api/settings/secrets", json={"changes": {"llm_api_key": "sk-secret"}})
        fake, used = FakeLLM(["gpt-b", "gpt-a", "gpt-a"]), []
        with patch.object(ai, "client_for", lambda url: used.append(url) or fake):
            res = self.client.post("/api/ai/models", json={"base_url": "https://llm.example/v1/"})
            self.assertEqual(res.json(), {"models": ["gpt-a", "gpt-b"]})
            self.assertEqual(used, ["https://llm.example/v1"])
            self.assertEqual(self.client.post("/api/ai/models", json={"base_url": "ftp://x"}).status_code, 422)
            res = self.client.post("/api/ai/test", json={"model": "gpt-a", "reasoning_effort": "low"}).json()
            self.assertEqual((res["ok"], res["reply"], res["completion_tokens"]), (True, "正常", 30))
            self.assertEqual((fake.calls[-1]["model"], fake.calls[-1]["reasoning_effort"]), ("gpt-a", "low"))
            self.client.post("/api/ai/test", json={"model": "gpt-a", "reasoning_effort": ""})
            self.assertNotIn("reasoning_effort", fake.calls[-1])  # 留空不传
            self.assertEqual(self.client.post("/api/ai/test", json={"reasoning_effort": "ultra"}).status_code, 422)
            fake.error = RuntimeError("401 bad key sk-secret")
            res = self.client.post("/api/ai/test", json={"model": "gpt-a"}).json()
            self.assertFalse(res["ok"])
            self.assertNotIn("sk-secret", res["error"])

    def test_reasoning_effort_is_sent_on_every_request_when_set(self):
        import asyncio
        from reelay.core import ai, runtime
        with self.assertRaises(ValueError):
            normalize_config({"llm": {"reasoning_effort": "ultra"}})
        fake = FakeLLM()
        with runtime.scope(normalize_config({"llm": {"reasoning_effort": "high"}})):
            asyncio.run(ai.request(fake, model="m", messages=[]))
        with runtime.scope(normalize_config({})):
            asyncio.run(ai.request(fake, model="m", messages=[]))
        self.assertEqual(fake.calls[0]["reasoning_effort"], "high")
        self.assertNotIn("reasoning_effort", fake.calls[1])

    def test_rejected_reasoning_effort_does_not_break_requests(self):
        import asyncio
        from reelay.core import ai, runtime
        fake = FakeLLM(accepts={"low", "high"})
        with patch.object(ai, "_REJECTED_EFFORTS", set()), \
                runtime.scope(normalize_config({"llm": {"reasoning_effort": "medium"}})):
            asyncio.run(ai.request(fake, model="m", messages=[]))  # 被拒后去掉参数立即重发
            asyncio.run(ai.request(fake, model="m", messages=[]))  # 之后不再先失败一次
        self.assertEqual([c.get("reasoning_effort") for c in fake.calls], ["medium", None, None])
        other = FakeLLM(error=EffortRejected("bad request: reasoning"))
        with self.assertRaises(EffortRejected):  # 调用方显式指定的强度被拒：照常报错
            asyncio.run(ai.request(other, model="m", messages=[], reasoning_effort="high"))

    def test_supported_efforts_are_probed_and_remembered(self):
        from reelay.core import ai
        self.client.put("/api/settings/secrets", json={"changes": {"llm_api_key": "sk-secret"}})
        self.assertIsNone(self.client.get("/api/ai/efforts", params={"model": "m"}).json()["efforts"])
        fake = FakeLLM(accepts={"low", "high", "xhigh", "max"})
        with patch.object(ai, "client_for", lambda url: fake):
            res = self.client.post("/api/ai/efforts", json={"model": "m"}).json()
        expected = {"none": "unsupported", "minimal": "unsupported", "low": "supported", "medium": "unsupported",
                    "high": "supported", "xhigh": "supported", "max": "supported"}
        self.assertEqual(res["efforts"], expected)
        self.assertIn("reasoning_effort", res["note"])
        self.assertEqual(self.client.get("/api/ai/efforts", params={"model": "m"}).json()["efforts"], expected)


if __name__ == "__main__":
    unittest.main()
