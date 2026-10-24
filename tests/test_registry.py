"""插件注册目录：外部扩展的发现与隔离。"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from reelay.core.config import normalize_config
from reelay.core.registry import discover


class TestExternalPlugins(unittest.TestCase):
    def test_plugin_dir_is_loaded_and_broken_plugins_are_isolated(self):
        with tempfile.TemporaryDirectory() as root:
            Path(root, "audit_hook.py").write_text(
                "from reelay.core.registry import register\n"
                "@register(kind='hook', stage='after_parse', order=99)\n"
                "class AuditHook:\n"
                "    name = 'audit_hook'\n"
                "    async def run(self, ctx):\n"
                "        return True\n", encoding="utf-8")
            Path(root, "broken.py").write_text("raise RuntimeError('boom')\n", encoding="utf-8")
            registry = discover(plugin_dirs=[root])
            again = discover(plugin_dirs=[root])
        names = [registry.extension_name(h) for h in registry.hooks.get("after_parse", [])]
        self.assertIn("audit_hook", names)
        self.assertEqual(sum(1 for h in again.hooks["after_parse"] if h.name == "audit_hook"), 1)
        self.assertTrue(any("broken.py" in e["source"] for e in registry.load_errors))
        plain = discover(plugin_dirs=[])
        self.assertNotIn("audit_hook", [h.name for h in plain.hooks.get("after_parse", [])])



class TestBuiltinCatalog(unittest.TestCase):
    def test_dry_run_providers_are_not_selectable_in_normal_runs(self):
        # 模拟存储的「转存」只写内存：正式运行的注册目录里没有它，管理台选不到
        registry = discover(plugin_dirs=[])
        self.assertNotIn("dryrun", registry.providers["source"])
        self.assertNotIn("dryrun", registry.providers["storage"])

    def test_catalog_lists_storages_an_extension_cannot_work_with(self):
        class LocalCopy:  # 只会转存、没有整理用的网盘接口（library）
            name = "local"

        registry = discover(plugin_dirs=[])
        registry.add_provider("storage", LocalCopy())
        hooks = registry.catalog(normalize_config({}))["hooks"]
        organizer = next(x for x in hooks if x["name"] == "organizer")
        self.assertEqual(organizer["unsupported_storage"], ["local"])
        with self.assertRaisesRegex(ValueError, "需要存储提供 library"):
            registry.validate_config(normalize_config({"defaults": {"storage": "local"}}))


if __name__ == "__main__":
    unittest.main()
