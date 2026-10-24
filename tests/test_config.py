"""配置文件：分组结构、插件设置、首次启动的默认配置、密钥存储。"""
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


SAMPLE = {
    "system": {"save_dirs": {"剧集": "2", "默认": "1"}, "defaults": {"save_to": "默认"},
               "tmdb": {"language": "en-US"}, "sources": [{"bot": "@a"}]},
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
        self.assertIn("sources", data["system"])
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
            normalize_config({"plugins": {"organizer": {"naming": {"movie": "{{ title.__class__ }}{{ fileExt }}"}}}})

    def test_extensions_bound_to_a_storage_reject_other_storages(self):
        class LocalStorage:
            name = "local"

        registry = discover()
        registry.add_provider("storage", LocalStorage())
        cfg = normalize_config({"save_dirs": {"默认": "1"}, "sources": [{"bot": "@local", "storage": "local"}]})
        with self.assertRaisesRegex(ValueError, "媒体库整理需要存储提供 library，local 没有"):
            registry.validate_config(cfg)
        catalog = {item["name"]: item for item in registry.catalog(cfg)["hooks"]}
        self.assertEqual(catalog["organizer"]["unsupported_storage"], ["local"])  # 管理台据此提示
        cfg["plugins"]["organizer"]["enabled"] = False
        registry.validate_config(cfg)


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

    def test_updates_apply_all_or_nothing(self):
        store = secret_store.SecretStore(self.path)
        store.update({"llm_api_key": "sk-1"})
        with self.assertRaises(ValueError):
            store.update({"llm_api_key": "sk-2", "telegram_api_id": "abc"})
        self.assertEqual(store.get("llm_api_key"), "sk-1")
        with patch.object(store, "_save", side_effect=OSError("disk full")), self.assertRaises(OSError):
            store.update({"llm_api_key": "sk-3"})
        self.assertEqual(store.get("llm_api_key"), "sk-1")  # 写盘失败：内存里也回到原值

    def test_nothing_is_configured_until_an_entry_point_loads_secrets(self):
        with patch.object(secret_store, "_current", None):
            self.assertIsNone(secret_store.get("llm_api_key"))
        with secret_store.override(llm_api_key="k"):
            self.assertEqual(secret_store.get("llm_api_key"), "k")


if __name__ == "__main__":
    unittest.main()
