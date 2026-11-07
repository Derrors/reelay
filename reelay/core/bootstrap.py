"""入口程序共用的装配：按数据目录打开配置、注册表、数据库、管线与管理服务。"""
from __future__ import annotations

from pathlib import Path

from . import paths
from .config import ensure_config, load_config
from .pipeline import Pipeline
from .registry import discover
from .service import ManagementService
from .store import Store


def open_service(data_dir: str | Path | None = None) -> ManagementService:
    """首次启动时先写出默认配置，再按数据目录加载密钥与凭证；data_dir 默认是项目的 data/，测试传临时目录。"""
    data = Path(data_dir or paths.DATA)
    config_file = data / paths.CONFIG_NAME
    ensure_config(config_file)
    paths.use_data_dir(data)
    cfg = load_config(config_file)
    registry = discover()
    registry.validate_config(cfg)
    store = Store(str(data / paths.DATABASE_NAME))
    return ManagementService(store, Pipeline(registry, store, cfg), config_path=config_file)
