"""独立控制台UI冒烟环境：临时数据库/配置、无凭证、无Worker、无外部写入。
运行：.venv/bin/python -m tests.smoke_console
仅供页面验收，退出即删除临时数据，端口28081。
"""
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch


def main():
    import uvicorn
    from reelay.core.auth115 import CredentialStore, reset_store
    from reelay.web import create_app
    with tempfile.TemporaryDirectory(prefix='reelay-console-smoke-') as tmp:
        root = Path(tmp)
        environment = {k: v for k, v in os.environ.items() if not k.startswith('REELAY_')}
        config = {'save_dirs': {'默认': '0'}, 'defaults': {'parser': 'regex', 'save_to': '默认'},
                  'sources': [], 'forward': {'enabled': False}, 'custom_preserved': {'test': True}}
        (root / 'config.yaml').write_text(json.dumps(config), encoding='utf-8')
        with patch.dict(os.environ, environment, clear=True), patch('reelay.web.load_env'):
            reset_store(CredentialStore(root / 'credentials.json'))
            app = create_app(data_dir=root, start_worker=False)
            uvicorn.run(app, host='127.0.0.1', port=28081)


if __name__ == '__main__':
    main()
