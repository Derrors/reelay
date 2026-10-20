"""配置块、注册目录、目录浏览错误与长ID回归。全部写入临时目录。"""
import unittest
from types import SimpleNamespace
from urllib.error import HTTPError
from unittest.mock import Mock, patch

from tests import test_web_api as web_tests
from reelay.core.config import normalize_config
from reelay.web_storage import browse_dirs, StorageBrowseError


class TestConfigBlocks(unittest.TestCase):
    setUp = web_tests.TestManagementApi.setUp
    tearDown = web_tests.TestManagementApi.tearDown

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
                        {'plugins': {'does_not_exist': True}}, {'tmdb': {'timeout': 0}}):
            response = self.client.patch('/api/config/sections', json={'revision': cfg['revision'], 'sections': section})
            self.assertEqual(response.status_code, 422, response.text)
            self.assertEqual(self.config_path.read_bytes(), before)

    def test_plugin_disable_changes_runtime_and_survives_reload(self):
        cfg = self.client.get('/api/config').json()
        res = self.client.patch('/api/config/sections', json={'revision': cfg['revision'], 'sections': {'plugins': {'tmdb_enrich': False}}})
        self.assertEqual(res.status_code, 200, res.text)
        pipeline = self.app.state.pipeline
        self.assertNotIn('tmdb_enrich', [h.name for h in pipeline.reg.hooks_for('after_parse', pipeline.cfg)])
        hook = next(x for x in self.client.get('/api/extensions').json()['hooks'] if x['name'] == 'tmdb_enrich')
        self.assertFalse(hook['enabled'])

    def test_catalog_is_dynamic_including_events(self):
        registry = self.app.state.pipeline.reg
        listener = SimpleNamespace(name='test_audit', event='saved', ui_label='测试审计',
            ui_fields=[{'path':'level','label':'级别','type':'string','choices':['info','error']}])
        registry.add_listener(listener)
        res = self.client.get('/api/extensions')
        self.assertIn('test_audit', [event['name'] for event in res.json()['events']])
        config = self.client.get('/api/config').json()
        event = next(x for x in res.json()['events'] if x['name'] == 'test_audit')
        self.assertEqual(event['fields'][0]['path'], 'plugins.test_audit.level')  # 前端按完整路径读写
        plugins = {**config['config']['plugins'], 'test_audit': {'enabled': False, 'level': 'info'}}
        response = self.client.patch('/api/config/sections', json={'revision':config['revision'], 'sections': {'plugins': plugins}})
        self.assertEqual(response.status_code,200,response.text)
        bad = {**plugins, 'test_audit': {'level': 'verbose'}}
        config = self.client.get('/api/config').json()
        self.assertEqual(self.client.patch('/api/config/sections', json={'revision': config['revision'], 'sections': {'plugins': bad}}).status_code, 422)
        self.assertEqual(registry.listeners_for('saved',self.app.state.pipeline.cfg),[])

    def test_protected_extensions_and_config_sections(self):
        from reelay.core import secret_store
        secret_store.current().update({'web_password': 'test-only'})
        for method, path in [('get','/api/extensions'),('patch','/api/config/sections'),('post','/api/config/validate'),
                             ('get','/api/settings/secrets'),('put','/api/settings/secrets')]:
            self.assertEqual(getattr(self.client,method)(path).status_code,401)

    def test_plugin_specific_config_types_and_previews(self):
        catalog = self.client.get('/api/extensions').json()['hooks']
        organizer = next(x for x in catalog if x['name'] == 'organizer')
        router = next(x for x in catalog if x['name'] == 'dir_router')
        self.assertEqual(organizer['config_type'], 'naming_templates')
        self.assertEqual(router['config_type'], 'category_strategy')
        template = "{{ title }}/{{ title }}{% if resolution %} - {{ resolution }}{% endif %}.{{ ext }}"
        preview = self.client.post('/api/extensions/organizer/preview', json={'kind': 'movie', 'template': template})
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertEqual(preview.json()['path'], '示例标题/示例标题 - 4K.mkv')
        cfg = self.client.get('/api/config').json()['config']
        classified = self.client.post('/api/extensions/dir-router/preview', json={
            'categories': cfg['plugins']['dir_router']['categories'], 'media_type': 'tv',
            'genre_ids': [16], 'origin_country': ['JP'],
        })
        self.assertEqual(classified.status_code, 200, classified.text)
        self.assertEqual(classified.json()['path'], '动漫/日本番剧')

    def test_plugin_previews_reject_unsafe_or_invalid_input(self):
        bad_template = self.client.post('/api/extensions/organizer/preview', json={
            'kind': 'movie', 'template': '{{ title.__class__ }}/{{ ext }}',
        })
        self.assertEqual(bad_template.status_code, 422)
        bad_categories = {'movie': {'电影/国产电影': {}} , 'tv': {'剧集/欧美剧集': {}, '剧集/国产剧集': {'origin_country': 'CN'}}}
        response = self.client.post('/api/extensions/dir-router/preview', json={
            'categories': bad_categories, 'media_type': 'movie', 'genre_ids': [], 'origin_country': ['CN'],
        })
        self.assertEqual(response.status_code, 422)

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


class TestDirectoryReads(unittest.TestCase):
    def test_long_id_preserved_and_pagination(self):
        client=Mock()
        client.fs_files.return_value={'state':True,'count':3,'path':[{'cid':'0','name':'根目录'}],'data':[
            {'cid':'9876543210987654301','n':'影视','pid':'0'},
            {'fid':'999','cid':'0','n':'file.txt'}]}
        result=browse_dirs(SimpleNamespace(has_oauth=False,cookie_client=lambda:client),'0',0,2)
        self.assertEqual(result['items'][0]['id'],'9876543210987654301')
        self.assertEqual(result['path'][-1], {'id': '0', 'name': '根目录'})
        self.assertEqual(result['next_offset'],2)
        self.assertEqual(len(result['items']),1)

    def test_open_list_response(self):
        client=Mock()
        client.fs_files_open.return_value={'state':True,'count':1,'path':[{'file_id':'9876543210987654301','file_name':'电影'}],'data':[{'file_id':'9876543210987654301','file_name':'电影','file_category':'0','parent_id':'0'}]}
        result=browse_dirs(SimpleNamespace(has_oauth=True,open_client=lambda:client),'9876543210987654301')
        self.assertEqual(result['items'][0]['id'],'9876543210987654301')
        self.assertEqual(result['path'][-1]['name'],'电影')
        self.assertEqual(result['via'],'oauth')

    def test_expired_credentials_and_missing_directory(self):
        client=Mock()
        credentials=SimpleNamespace(has_oauth=False,cookie_client=lambda:client)
        client.fs_files.return_value={'state':False,'errno':99,'error':'请重新登录'}
        with self.assertRaises(StorageBrowseError) as caught:
            browse_dirs(credentials,'0')
        self.assertEqual(caught.exception.code,'credentials_expired')
        client.fs_files.return_value={'state':True,'data':[],'path':[{'cid':'0'}]}
        with self.assertRaises(StorageBrowseError) as caught:
            browse_dirs(credentials,'12')
        self.assertEqual(caught.exception.code,'directory_missing')

    def test_invalid_templates_rejected(self):
        for template in [
            '../{{ title }}.{{ ext }}', '{{ title.__class__ }}/{{ ext }}',
            '{{ title }}/{{ season|pad2 }}.{{ missing }}', '/{{ title }}.{{ ext }}',
            '{missing}/{ext}',
        ]:
            with self.assertRaises(ValueError):
                normalize_config({'organize':{'naming':{'tv':template}}})
