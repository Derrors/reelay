"""配置分组（system / plugins）、旧配置与环境变量的迁移、密钥存储与设置接口。全部在临时目录中。"""
from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from reelay.core import bootstrap, secret_store
from reelay.core.auth115 import CredentialStore
from reelay.core.config import is_legacy, load_config, normalize_config, render, to_file
from reelay.core.registry import Registry, discover

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover
    TestClient = None

LEGACY = {
    "save_dirs": {"剧集": "2", "默认": "1"},
    "defaults": {"save_to": "默认"},
    "organize": {"conflict": "skip"},
    "dir_router": {"categories": {"movie": {"电影/全部": {}}, "tv": {"剧集/全部": {}}}},
    "tmdb": {"language": "en-US"},
    "daily_report": {"narrative": False},
    "episode_tracker": {"lookback_days": 14, "subscriptions": [{"title": "剧", "season": 1}]},
    "plugins": {"strm": False},
    "audit": {"level": "info"},  # 外部插件的旧配置：认不出归属，原样保留
}


class TestFileStructure(unittest.TestCase):
    def test_legacy_file_is_converted_to_system_and_plugins(self):
        self.assertTrue(is_legacy(LEGACY))
        cfg = normalize_config(LEGACY)
        self.assertEqual(cfg["subscriptions"], [{"title": "剧", "season": 1}])
        self.assertEqual(cfg["tmdb"]["language"], "en-US")
        plugins = cfg["plugins"]
        self.assertEqual(plugins["organizer"]["conflict"], "skip")
        self.assertIn("tv", plugins["organizer"]["naming"])  # 内置插件补齐默认设置
        self.assertEqual(plugins["dir_router"]["categories"]["tv"], {"剧集/全部": {}})
        self.assertEqual(plugins["daily_report"], {"narrative": False})
        self.assertEqual(plugins["episode_tracker"], {"lookback_days": 14})
        self.assertEqual(plugins["strm"], {"enabled": False})
        for key in ("organize", "dir_router", "daily_report", "episode_tracker"):
            self.assertNotIn(key, cfg)
        self.assertEqual(cfg["audit"], {"level": "info"})

    def test_file_round_trip_keeps_the_same_config(self):
        cfg = normalize_config(LEGACY)
        data = to_file(cfg)
        self.assertEqual(set(data), {"system", "plugins", "audit"})
        self.assertIn("subscriptions", data["system"])
        self.assertNotIn("plugins", data["system"])
        self.assertFalse(is_legacy(data))
        text = render(cfg)
        self.assertTrue(text.startswith("# Reelay 配置"))
        self.assertEqual(normalize_config(yaml.safe_load(text)), cfg)

    def test_example_config_is_valid(self):
        cfg = load_config(Path(__file__).resolve().parents[1] / "config.example.yaml")
        discover().validate_config(cfg)
        self.assertEqual(cfg["plugins"]["strm"], {"enabled": False})

    def test_new_sections_are_validated(self):
        for bad in ({"llm": {"base_url": "ftp://x"}}, {"llm": {"model": " "}}, {"tmdb": {"timeout": 0}},
                    {"telegram": {"owner_id": "abc"}}, {"runtime": {"concurrency": 0}},
                    {"runtime": {"log_level": "TRACE"}}, {"p115": {"share_qps": 0}},
                    {"plugins": {"strm": {"enabled": "yes"}}}, {"plugins": {"strm": True, "x": 1}},
                    {"system": {"defaults": {}}, "defaults": {}}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                normalize_config(bad)
        cfg = normalize_config({"telegram": {"owner_id": 12345}, "llm": {"base_url": "https://api.example.com/v1/"}})
        self.assertEqual((cfg["telegram"]["owner_id"], cfg["llm"]["base_url"]), ("12345", "https://api.example.com/v1"))

    def test_plugin_switch_and_field_paths(self):
        registry = discover()
        strm = next(h for h in registry.hooks["after_save"] if h.name == "strm")
        self.assertFalse(Registry.is_enabled(strm, normalize_config({})))
        self.assertTrue(Registry.is_enabled(strm, normalize_config({"plugins": {"strm": {"enabled": True}}})))
        catalog = registry.catalog(normalize_config({}))
        daily = next(x for x in catalog["events"] if x["name"] == "daily_report")
        self.assertEqual((daily["config_section"], daily["fields"][0]["path"]),
                         ("plugins.daily_report", "plugins.daily_report.narrative"))
        with self.assertRaises(ValueError):  # 插件字段按声明的类型校验
            registry.validate_config(normalize_config({"plugins": {"daily_report": {"narrative": "no"}}}))


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


class TestBootstrap(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data = self.root / "data"
        self.data.mkdir()
        # 旧位置只在临时目录里找：真实项目根目录下的 config.yaml 绝不能被测试移走
        guard = patch.object(bootstrap.paths, "PROJECT_ROOT", self.root / "no-project-root")
        guard.start()
        self.addCleanup(guard.stop)

    def tearDown(self):
        self.temp.cleanup()

    def secrets(self):
        return secret_store.SecretStore(self.data / "secrets.json")

    def test_legacy_config_is_backed_up_and_rewritten(self):
        (self.data / "config.yaml").write_text(json.dumps(LEGACY, ensure_ascii=False), encoding="utf-8")
        bootstrap.prepare(self.data, env={})
        self.assertEqual(len(list(self.data.glob("config.yaml.bak-*"))), 1)
        data = yaml.safe_load((self.data / "config.yaml").read_text(encoding="utf-8"))
        self.assertEqual(set(data), {"system", "plugins", "audit"})
        self.assertEqual(load_config(self.data / "config.yaml"), normalize_config(LEGACY))
        bootstrap.prepare(self.data, env={})  # 已是新结构：不再备份、不再改写
        self.assertEqual(len(list(self.data.glob("config.yaml.bak-*"))), 1)

    def test_config_in_a_legacy_location_is_moved_into_data(self):
        legacy = self.root / "custom.yaml"
        legacy.write_text(json.dumps({"save_dirs": {"默认": "7"}, "defaults": {"save_to": "默认"}}), encoding="utf-8")
        bootstrap.prepare(self.data, legacy_locations=True, env={"REELAY_CONFIG_PATH": str(legacy)}, project_root=self.root)
        self.assertFalse(legacy.exists())
        self.assertTrue((self.root / "custom.yaml.migrated").exists())
        self.assertEqual(load_config(self.data / "config.yaml")["save_dirs"], {"默认": "7"})
        # 项目根目录下的旧 config.yaml
        (self.data / "config.yaml").unlink()
        (self.root / "config.yaml").write_text(json.dumps({"save_dirs": {"默认": "8"}, "defaults": {"save_to": "默认"}}),
                                               encoding="utf-8")
        bootstrap.prepare(self.data, legacy_locations=True, env={}, project_root=self.root)
        self.assertEqual(load_config(self.data / "config.yaml")["save_dirs"], {"默认": "8"})
        self.assertTrue((self.root / "config.yaml.migrated").exists())

    def test_environment_is_imported_once_and_web_changes_win(self):
        env = {"REELAY_LLM_API_KEY": "sk-old", "REELAY_LLM_MODEL": "m1", "REELAY_OWNER_ID": "42",
               "REELAY_TG_BACKFILL_MAX_DAYS": "3", "REELAY_115_SHARE_QPS": "0.5", "REELAY_WEB_PASSWORD": "pw",
               "REELAY_115_COOKIES": "UID=1;CID=2", "REELAY_STRM_SECRET": "strm"}
        bootstrap.prepare(self.data, env=env)
        cfg = load_config(self.data / "config.yaml")
        self.assertEqual((cfg["llm"]["model"], cfg["telegram"]["owner_id"], cfg["telegram"]["backfill_max_days"],
                          cfg["p115"]["share_qps"]), ("m1", "42", 3, 0.5))
        secrets = self.secrets()
        self.assertEqual(secrets.get("llm_api_key"), "sk-old")
        self.assertTrue(secrets.check_password("pw"))
        self.assertNotIn("sk-old", (self.data / "config.yaml").read_text(encoding="utf-8"))
        self.assertIn("UID=1", CredentialStore(self.data / "115_credentials.json").cookies)
        self.assertEqual((self.data / "strm.secret").read_text(), "strm")
        # 管理台清除了密钥、改了模型：.env 没变就不会再覆盖回去
        secrets.update({"llm_api_key": None})
        path = self.data / "config.yaml"
        path.write_text(path.read_text(encoding="utf-8").replace("model: m1", "model: m2"), encoding="utf-8")
        bootstrap.prepare(self.data, env=env)
        self.assertIsNone(self.secrets().get("llm_api_key"))
        self.assertEqual(load_config(path)["llm"]["model"], "m2")
        # .env 里换了新值：再导入一次
        bootstrap.prepare(self.data, env={**env, "REELAY_LLM_API_KEY": "sk-new"})
        self.assertEqual(self.secrets().get("llm_api_key"), "sk-new")

    def test_existing_credentials_and_invalid_values_are_not_overwritten(self):
        CredentialStore(self.data / "115_credentials.json").set_cookies("UID=fresh")
        bootstrap.prepare(self.data, env={"REELAY_115_COOKIES": "UID=old", "REELAY_WORKER_CONCURRENCY": "lots"})
        self.assertEqual(CredentialStore(self.data / "115_credentials.json").cookies, "UID=fresh")
        self.assertEqual(load_config(self.data / "config.yaml")["runtime"]["concurrency"], 3)
        self.assertNotIn("REELAY_WORKER_CONCURRENCY", self.secrets().imported())

    def test_broken_config_is_left_alone(self):
        (self.data / "config.yaml").write_text("[broken", encoding="utf-8")
        bootstrap.prepare(self.data, env={"REELAY_LLM_MODEL": "m", "REELAY_LLM_API_KEY": "k"})
        self.assertEqual((self.data / "config.yaml").read_text(encoding="utf-8"), "[broken")
        self.assertEqual(self.secrets().get("llm_api_key"), "k")
        self.assertNotIn("REELAY_LLM_MODEL", self.secrets().imported())

    def test_database_elsewhere_must_be_moved_by_hand(self):
        old = self.root / "old.db"
        old.write_bytes(b"")
        with self.assertRaisesRegex(bootstrap.BootstrapError, "mv"):
            bootstrap.prepare(self.data, legacy_locations=True, env={"REELAY_DB_PATH": str(old)}, project_root=self.root)
        session = self.root / "custom.session"
        session.write_text("s")
        bootstrap.prepare(self.data, legacy_locations=True, env={"REELAY_TG_SESSION": str(session)},
                          project_root=self.root)
        self.assertEqual((self.data / "userbot.session").read_text(), "s")


@unittest.skipIf(TestClient is None, "未安装 web 可选依赖")
class TestSettingsApi(unittest.TestCase):
    def setUp(self):
        from reelay import web_extras
        from reelay.web import create_app
        self.env = patch.dict(os.environ, {"REELAY_TMDB_API_KEY": "tmdb-key"}, clear=True)
        self.env.start()
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        (root / "config.yaml").write_text(json.dumps({"save_dirs": {"默认": "0"}, "defaults": {"save_to": "默认"}}),
                                          encoding="utf-8")
        web_extras._SESSIONS.clear()
        self.app = create_app(data_dir=root)
        self.client = TestClient(self.app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.temp.cleanup()
        self.env.stop()

    def test_secrets_are_set_but_never_returned(self):
        body = self.client.get("/api/config").json()
        self.assertTrue(body["secrets"]["tmdb_api_key"]["set"])  # 从环境变量导入
        self.assertFalse(body["secrets"]["llm_api_key"]["set"])
        self.assertNotIn("tmdb-key", json.dumps(body))
        self.assertTrue(body["text"].startswith("# Reelay 配置"))
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
