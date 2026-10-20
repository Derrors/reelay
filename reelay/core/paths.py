"""运行文件的固定位置：全部在项目根目录的 data/ 下（Docker 镜像里是 /app/data），不提供配置项。

数据库、配置、密钥、会话、凭证与日志放在同一个目录，备份或迁移时整体复制 data/ 即可；
Docker 部署只需挂载这一个数据卷。测试与工具通过函数参数传入临时目录，不改这里的常量。
"""
from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA = PROJECT_ROOT / "data"
ENV_FILE = PROJECT_ROOT / ".env"

CONFIG_NAME = "config.yaml"
DATABASE_NAME = "reelay.db"
SECRETS_NAME = "secrets.json"

CONFIG = DATA / CONFIG_NAME
DATABASE = DATA / DATABASE_NAME
SECRETS = DATA / SECRETS_NAME
USERBOT_SESSION = DATA / "userbot.session"
CONTROL_BOT_SESSION = DATA / "controlbot.session"
CREDENTIALS_115 = DATA / "115_credentials.json"
LOG = DATA / "reelay.log"
PLUGINS = DATA / "plugins"
EVAL = DATA / "eval"


def use_data_dir(data_dir: Path) -> None:
    """入口程序按数据目录设定进程级的文件位置：密钥与 115 凭证。须在 discover() 之前调用。"""
    from . import auth115, secret_store
    secret_store.use(data_dir / SECRETS_NAME)
    auth115.reset_store(auth115.CredentialStore(data_dir / CREDENTIALS_115.name))
