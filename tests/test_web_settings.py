"""管理接口：配置按块保存、插件目录与动作、密钥、AI 接口设置。"""
from __future__ import annotations

import unittest
import json
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.error import HTTPError

import yaml

from reelay.core import secret_store
from reelay.core.config import normalize_config

from tests.fakes import LOCAL, MANUAL_REGEX, TestClient, WebCase


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


class TestConfigBlocks(WebCase):
    CONFIG = MANUAL_REGEX

    def test_add_directory_and_preserve_unrelated_config(self):
        cfg = self.client.get('/api/config').json()
        newdirs = {**cfg['config']['save_dirs'], '纪录片': '9876543210987654301'}
        response = self.client.patch('/api/config/sections', json={'revision': cfg['revision'], 'sections': {'save_dirs': newdirs}})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['config']['save_dirs']['纪录片'], '9876543210987654301')
        self.assertEqual(response.json()['config']['defaults'], cfg['config']['defaults'])
        self.assertEqual(self.client.patch('/api/config/sections', json={'revision': cfg['revision'], 'sections': {'save_dirs': newdirs}}).status_code, 409)

    def test_invalid_blocks_do_not_write(self):
        cfg = self.client.get('/api/config').json()
        before = self.config_path.read_bytes()
        for section in ({'approval': {'timeout_hours': -1}}, {'defaults': {'confidence_threshold': 2}},
                        {'save_dirs': {'bad': '-2'}}, {'plugins': {'organizer': {'naming': {'tv': '{missing}/{ext}'}}}},
                        {'plugins': {'does_not_exist': {'enabled': True}}}, {'tmdb': {'timeout': 0}}):
            response = self.client.patch('/api/config/sections', json={'revision': cfg['revision'], 'sections': section})
            self.assertEqual(response.status_code, 422, response.text)
            self.assertEqual(self.config_path.read_bytes(), before)

    def test_plugin_disable_changes_runtime_and_survives_reload(self):
        cfg = self.client.get('/api/config').json()
        res = self.client.patch('/api/config/sections', json={'revision': cfg['revision'], 'sections': {'plugins': {'tmdb_enrich': {'enabled': False}}}})
        self.assertEqual(res.status_code, 200, res.text)
        pipeline = self.app.state.pipeline
        self.assertNotIn('tmdb_enrich', [h.name for h in pipeline.reg.hooks_for('after_parse', pipeline.cfg)])
        hook = next(x for x in self.client.get('/api/config').json()['extensions']['hooks'] if x['name'] == 'tmdb_enrich')
        self.assertFalse(hook['enabled'])

    def test_catalog_is_dynamic_including_events(self):
        registry = self.app.state.pipeline.reg
        listener = SimpleNamespace(name='test_audit', event='saved', ui_label='测试审计',
            ui_fields=[{'path':'level','label':'级别','type':'string','choices':['info','error']}])
        registry.add_listener(listener)
        config = self.client.get('/api/config').json()
        self.assertIn('test_audit', [event['name'] for event in config['extensions']['events']])
        event = next(x for x in config['extensions']['events'] if x['name'] == 'test_audit')
        self.assertEqual(event['fields'][0]['path'], 'plugins.test_audit.level')  # 前端按完整路径读写
        plugins = {**config['config']['plugins'], 'test_audit': {'enabled': False, 'level': 'info'}}
        response = self.client.patch('/api/config/sections', json={'revision':config['revision'], 'sections': {'plugins': plugins}})
        self.assertEqual(response.status_code,200,response.text)
        bad = {**plugins, 'test_audit': {'level': 'verbose'}}
        config = self.client.get('/api/config').json()
        self.assertEqual(self.client.patch('/api/config/sections', json={'revision': config['revision'], 'sections': {'plugins': bad}}).status_code, 422)
        self.assertEqual(registry.listeners_for('saved',self.app.state.pipeline.cfg),[])

    def test_protected_extensions_and_config_sections(self):
        secret_store.current().update({'web_password': 'test-only'})
        for method, path in [('get','/api/config'),('patch','/api/config/sections'),('post','/api/config/validate'),
                             ('put','/api/settings/secrets')]:
            self.assertEqual(getattr(self.client,method)(path).status_code,401)

    def test_plugin_specific_config_types_and_previews(self):
        catalog = self.client.get('/api/config').json()['extensions']['hooks']
        # 选目录与整理同属「媒体库整理」：目录里只有一项，运行时机合并
        organizer = [x for x in catalog if x['name'] == 'organizer']
        self.assertEqual([(x['slot'], x['config_type'], x['actions']) for x in organizer],
                         [('before_save,after_save', 'media_library', ['preview_naming', 'category_rules', 'preview_plan', 'parse_names'])])
        self.assertNotIn('dir_router', {x['name'] for x in catalog})
        # 变量同 MoviePilot，可直接粘贴其重命名格式
        template = ("{{title}}{% if year %} ({{year}}){% endif %}/{{title}}{% if year %} ({{year}}){% endif %}"
                    "{% if part %}-{{part}}{% endif %}{% if videoFormat %} - {{videoFormat}}{% endif %}"
                    "{% if videoCodec %} - {{videoCodec}}{% endif %}{% if audioCodec %} - {{audioCodec}}{% endif %}{{fileExt}}")
        preview = self.client.post('/api/extensions/organizer/actions/preview_naming', json={'kind': 'movie', 'template': template})
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertEqual(preview.json()['path'], '示例标题 (2026)/示例标题 (2026) - 2160p - H265 10bit - AAC.mkv')
        # 二级分类随插件发布，管理台只读：按文件顺序给出路径与条件的中文说明
        media = self.client.post('/api/extensions/organizer/actions/category_rules').json()['media']
        self.assertEqual([m['media'] for m in media], ['movie', 'tv'])
        anime = media[1]['categories'][0]
        self.assertEqual((anime['path'], [(c['label'], [v['name'] for v in c['values']]) for c in anime['conditions']]),
                         ('动漫/国产动漫', [('题材', ['动画']), ('地区', ['中国大陆', '台湾', '香港'])]))
        self.assertEqual(media[0]['categories'][-1], {'path': '电影/欧美电影', 'conditions': []})
        self.assertNotIn('categories', self.client.get('/api/config').json()['config']['plugins']['organizer'])

    def test_plugin_previews_reject_unsafe_or_invalid_input(self):
        bad_template = self.client.post('/api/extensions/organizer/actions/preview_naming', json={
            'kind': 'movie', 'template': '{{ title.__class__ }}/{{ fileExt }}',
        })
        self.assertEqual(bad_template.status_code, 422)
        self.assertEqual(self.client.post('/api/extensions/organizer/actions/preview_naming', json=[]).status_code, 422)

    def test_plugin_actions_only_expose_declared_methods(self):
        # 只有 ui_actions 里声明的方法可以调用：插件的内部方法、不存在的插件都是 404
        for path in ('/api/extensions/organizer/actions/run', '/api/extensions/organizer/actions/prepare',
                     '/api/extensions/organizer/actions/__init__', '/api/extensions/missing/actions/category_rules'):
            with self.subTest(path=path):
                self.assertEqual(self.client.post(path, json={}).status_code, 404)
        secret_store.current().update({'web_password': 'test-only'})
        self.assertEqual(self.client.post('/api/extensions/organizer/actions/category_rules').status_code, 401)

    def test_directory_http_405_is_actionable_not_500(self):
        credentials = Mock(has_oauth=False)
        credentials.cookie_client.return_value.fs_files.side_effect = HTTPError('https://webapi.115.com/files',405,'blocked',{},None)
        from reelay.core.auth115 import get_store
        with patch.object(get_store(), 'cookie_client', credentials.cookie_client), patch.object(type(get_store()), 'has_oauth', property(lambda _: False)):
            response=self.client.get('/api/storage/dirs?cid=0')
        self.assertEqual(response.status_code,503)
        self.assertEqual(response.json()['code'],'upstream_restricted')
        self.assertIn('405',response.json()['detail'])

    def test_directory_bad_id_rejected(self):
        for cid in ['-1','bad','3.14']:
            self.assertEqual(self.client.get('/api/storage/dirs',params={'cid':cid}).status_code,422)


class TestSettingsApi(WebCase):
    SECRETS = {"tmdb_api_key": "tmdb-key"}

    def test_secrets_are_set_but_never_returned(self):
        body = self.client.get("/api/config").json()
        self.assertTrue(body["secrets"]["tmdb_api_key"]["set"])
        self.assertFalse(body["secrets"]["llm_api_key"]["set"])
        self.assertNotIn("tmdb-key", json.dumps(body))
        self.assertNotIn("text", body)  # 不再返回配置原文，只按分区读写
        self.assertIsInstance(body["revision"], str)
        res = self.client.put("/api/settings/secrets", json={"changes": {"llm_api_key": "sk-xyz"}})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertTrue(res.json()["llm_api_key"]["set"])
        self.assertTrue(self.client.get("/api/dashboard").json()["connections"]["llm"]["configured"])
        self.assertNotIn("sk-xyz", self.client.get("/api/config").text)
        self.assertEqual(self.client.put("/api/settings/secrets", json={"changes": {"nope": "x"}}).status_code, 422)

    def test_changing_an_api_address_clears_its_saved_key(self):
        self.client.put("/api/settings/secrets", json={"changes": {"llm_api_key": "sk-old"}})
        cfg = self.client.get("/api/config").json()
        llm = cfg["config"]["llm"]
        res = self.client.patch("/api/config/sections", json={"revision": cfg["revision"],
                                                              "sections": {"llm": {**llm, "model": "m2"}}})
        self.assertEqual(res.json()["cleared_secrets"], [])  # 地址没变：密钥保留
        res = self.client.patch("/api/config/sections", json={
            "revision": res.json()["revision"], "sections": {"llm": {**llm, "base_url": "https://other.example/v1"}}})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["cleared_secrets"], ["llm_api_key"])  # 原来的密钥不会发给新地址
        secrets = self.client.get("/api/config").json()["secrets"]
        self.assertFalse(secrets["llm_api_key"]["set"])
        self.assertTrue(secrets["tmdb_api_key"]["set"])

    def test_password_change_requires_the_current_one_and_resets_sessions(self):
        short = self.client.put("/api/settings/secrets", json={"changes": {"web_password": "short"}})
        self.assertEqual(short.status_code, 422)  # 至少 8 个字符
        self.assertEqual(self.client.put("/api/settings/secrets", json={"changes": {"web_password": "password-one"}}).status_code, 200)
        self.assertEqual(self.client.get("/api/config").status_code, 200)  # 当前页面换了新会话继续使用
        other = TestClient(self.app, base_url=LOCAL)
        self.assertEqual(other.get("/api/config").status_code, 401)
        self.assertEqual(self.client.put("/api/settings/secrets", json={"changes": {"web_password": "password-two"},
                                                                         "current_password": "bad"}).status_code, 409)
        self.assertEqual(self.client.put("/api/settings/secrets", json={"changes": {"web_password": "password-two"},
                                                                         "current_password": "password-one"}).status_code, 200)
        self.assertEqual(other.post("/api/auth/login", json={"password": "password-one"}).status_code, 401)
        self.assertEqual(other.post("/api/auth/login", json={"password": "password-two"}).status_code, 200)

    def test_system_and_plugin_sections_save_back_to_the_file(self):
        cfg = self.client.get("/api/config").json()
        plugins = {**cfg["config"]["plugins"], "daily_report": {"enabled": True, "narrative": False}}
        res = self.client.patch("/api/config/sections", json={"revision": cfg["revision"], "sections": {
            "llm": {"base_url": "https://llm.example/v1", "model": "m"}, "plugins": plugins}})
        self.assertEqual(res.status_code, 200, res.text)
        data = yaml.safe_load((self.config_path).read_text(encoding="utf-8"))
        self.assertEqual(data["system"]["llm"]["model"], "m")
        self.assertEqual(data["plugins"]["daily_report"], {"enabled": True, "narrative": False})
        cfg = self.client.get("/api/config").json()
        bad = {**cfg["config"]["plugins"], "typo_plugin": {"enabled": True}}
        res = self.client.patch("/api/config/sections", json={"revision": cfg["revision"], "sections": {"plugins": bad}})
        self.assertEqual(res.status_code, 422)
        self.assertIn("typo_plugin", res.text)


class TestLlmSettings(WebCase):
    SECRETS = {"tmdb_api_key": "tmdb-key"}

    def test_model_list_and_connection_test_use_the_form_values(self):
        from reelay.core import ai
        self.assertEqual(self.client.post("/api/ai/models", json={}).status_code, 409)  # 还没有密钥
        self.client.put("/api/settings/secrets", json={"changes": {"llm_api_key": "sk-secret"}})
        fake, used = FakeLLM(["gpt-b", "gpt-a", "gpt-a"]), []
        with patch.object(ai, "client_for", lambda url, key=None: used.append((url, key)) or fake):
            # 已保存的密钥只发给已保存的地址：换了地址又没填密钥时不发请求
            res = self.client.post("/api/ai/models", json={"base_url": "https://llm.example/v1/"})
            self.assertEqual(res.status_code, 409)
            self.assertEqual(used, [])
            res = self.client.post("/api/ai/models", json={"base_url": "https://llm.example/v1/", "api_key": "sk-form"})
            self.assertEqual(res.json(), {"models": ["gpt-a", "gpt-b"]})
            self.assertEqual(used, [("https://llm.example/v1", "sk-form")])
            self.assertEqual(self.client.post("/api/ai/models", json={"base_url": "ftp://x", "api_key": "k"}).status_code, 422)
            self.assertEqual(self.client.post("/api/ai/models", json={}).status_code, 200)
            self.assertEqual(used[-1], ("", "sk-secret"))  # 已保存的地址（留空即官方地址）用已保存的密钥
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
        with patch.object(ai, "client_for", lambda url, key=None: fake):
            res = self.client.post("/api/ai/efforts", json={"model": "m"}).json()
        expected = {"none": "unsupported", "minimal": "unsupported", "low": "supported", "medium": "unsupported",
                    "high": "supported", "xhigh": "supported", "max": "supported"}
        self.assertEqual(res["efforts"], expected)
        self.assertIn("reasoning_effort", res["note"])
        self.assertEqual(self.client.get("/api/ai/efforts", params={"model": "m"}).json()["efforts"], expected)


if __name__ == "__main__":
    unittest.main()
