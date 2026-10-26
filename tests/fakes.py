"""测试共用的替身与夹具：模拟的 AI 客户端与 Telegram、样例消息、管线 / 管理服务 / 管理接口的基类。

全部只在内存或临时目录里工作，不访问网络。
"""
from __future__ import annotations

import asyncio
import datetime
import json
import os
import tempfile
import types
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from reelay.control.bot import ControlBot
from reelay.core import secret_store, subscriptions, tmdb
from reelay.core.config import normalize_config
from reelay.core.contracts import ParsedResource, PipelineContext, RawMessage, TaskState
from reelay.core.pipeline import Pipeline
from reelay.core.registry import Registry, discover
from reelay.core.service import ManagementService
from reelay.core.store import Store

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover
    TestClient = None

SHARE = "https://115.com/s/abc123?password=abcd"
# 没设登录密码时管理台只接受本机地址：测试客户端按本机访问
LOCAL = "http://127.0.0.1"
needs_web = unittest.skipIf(TestClient is None, "未安装 web 可选依赖")
# 人工确认 + 正则解析（不调用 AI）的最小配置
MANUAL_REGEX = {"save_dirs": {"默认": "0"}, "sources": [],
                "defaults": {"approval_mode": "manual", "parser": "regex", "save_to": "默认"}}


def media(**change: Any) -> dict:
    """LLM 解析返回的一条剧集识别结果。"""
    data = {"is_media": True, "is_ad": False, "confidence": 1.0, "media_type": "tv",
            "title": "剧", "year": 2026, "season": 1, "episodes": [3], "genres": [],
            "genre_ids": [18], "origin_country": ["CN"], "resolution": "1080p", "size": "2 GB", "reason": "影视"}
    data.update(change)
    return data


def message(text: str = f"剧 (2026) 第3集 1080p {SHARE}", urls=(SHARE,), msg_id: str = "1",
            source: str = "@bot") -> PipelineContext:
    return PipelineContext(RawMessage(source, msg_id, text, list(urls)))


def tv_ctx(title="剧", tmdbid=100, season=1, episodes=(3,), matched=True, msg_id="1", **fields) -> PipelineContext:
    """一条已识别、已匹配 TMDB 的剧集任务（还没落库）。"""
    ctx = message(msg_id=msg_id)
    ctx.parsed = ParsedResource(**{**media(title=title, season=season, episodes=list(episodes)), **fields},
                                share_code="abc123")
    ctx.parsed.raw_fields["tmdbid"] = tmdbid
    ctx.meta["tmdb"] = {"matched": matched, "tmdbid": tmdbid} if matched else {"matched": False}
    ctx.source_rule = {"approval_mode": "manual"}
    return ctx


def done_task(store, msg_id, *, title, media_type="tv", resolution="1080p", episodes=(1,), season=1, genre_ids=(18,),
              origin_country=("CN",), tmdbid=None) -> PipelineContext:
    """一条已转存并核验入库的任务（写进 store）。"""
    ctx = message(msg_id=msg_id)
    ctx.parsed = ParsedResource(**{**media(title=title, media_type=media_type, resolution=resolution,
                                           episodes=list(episodes), season=season if episodes else None,
                                           genre_ids=list(genre_ids), origin_country=list(origin_country))},
                                share_code=f"s{msg_id}")
    if tmdbid:
        ctx.parsed.raw_fields["tmdbid"] = tmdbid
    ctx.save_dir_cid, ctx.save_dir_alias = "2", "剧集"
    ctx.ns("storage")["identity"] = {"cid": "2", "share_code": f"s{msg_id}", "title": title,
                                     "season": ctx.parsed.season, "episodes": list(episodes)}
    ctx.state = TaskState.STORED
    ctx.result = {"storage_verified": True}
    store.save_task(ctx)
    store.finish_transfer(ctx)
    ctx.state = TaskState.DONE
    store.save_task(ctx)
    return ctx


def bot_for(service) -> ControlBot:
    """不连 Telegram 的控制 Bot：只用来调用它的按钮与命令处理。"""
    bot = ControlBot.__new__(ControlBot)
    bot.service, bot._correcting, bot._card_lock = service, {}, asyncio.Lock()
    return bot


class APITimeoutError(Exception):
    """名称与 openai SDK 的瞬时错误一致。"""


class FakeAI:
    """OpenAI 兼容客户端的替身：按顺序返回预置结果（最后一个重复使用，异常则抛出），记录请求。"""

    def __init__(self, *responses: Any, refusal: str | None = None):
        self.responses, self.refusal = list(responses), refusal
        self.calls: list[dict] = []
        self.chat = types.SimpleNamespace(completions=self)

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        item = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(item, Exception):
            raise item
        content = item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)
        message = types.SimpleNamespace(content=content, refusal=self.refusal)
        usage = types.SimpleNamespace(prompt_tokens=100, completion_tokens=20)
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=message)], usage=usage)


class _Isolated:
    """清空环境变量；AI 瞬时错误重试不真的等待；TMDB 条目详情默认返回空（不联网）。"""

    def isolate(self) -> Path:
        env = patch.dict(os.environ, {}, clear=True)
        env.start()
        self.addCleanup(env.stop)
        for patcher in (patch("reelay.core.ai.RETRY_DELAY", 0), patch.object(tmdb, "details", return_value={})):
            patcher.start()
            self.addCleanup(patcher.stop)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        return Path(temp.name)


class PipelineCase(_Isolated, unittest.IsolatedAsyncioTestCase):
    """临时数据库 + 按需组装的管线（注册目录由用例给出，默认为空）。"""

    def setUp(self):
        self.root = self.isolate()
        self.store = Store(self.root / "t.db")
        self.addCleanup(self.store.close)

    def pipeline(self, cfg: dict | None = None, registry: Registry | None = None) -> Pipeline:
        cfg = normalize_config(cfg or {"save_dirs": {"默认": "1"}, "defaults": {"save_to": "默认"}})
        return Pipeline(registry or Registry(), self.store, cfg)


class ServiceCase(_Isolated, unittest.IsolatedAsyncioTestCase):
    """完整的内置注册目录、管线与管理服务；LLM 解析器换成 FakeAI，配置同时写进文件（管理端会按文件重载）。"""

    CFG = {"save_dirs": {"剧集": "2", "默认": "3", "动漫": "4", "电影": "5"}, "defaults": {"save_to": "默认"}}
    SUBSCRIPTIONS: list[dict] = []  # 用例开始前已有的订阅（用户数据在数据库里，不在配置里）

    def setUp(self):
        self.root = self.isolate()
        self.store = Store(self.root / "t.db")
        self.addCleanup(self.store.close)
        self.registry = discover()
        self.parser = self.registry.get_provider("parser", "llm")
        self.parser._client = FakeAI(media())
        self.pl = Pipeline(self.registry, self.store, normalize_config(self.CFG))
        self.config_path = self.root / "config.yaml"
        self.service = ManagementService(self.store, self.pl, config_path=str(self.config_path))
        self.configure(self.CFG)
        for item in self.SUBSCRIPTIONS:
            self.subscribe(item)

    def subscribe(self, item: dict) -> dict:
        entry = subscriptions.normalize(item)
        return self.store.add_subscription(subscriptions.key(entry), entry)

    def configure(self, cfg: dict) -> None:
        self.config_path.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
        self.pl.apply_config(normalize_config(cfg))

    async def pending(self, msg_id: str = "1", text: str | None = None, source: str = "@bot"):
        ctx = message(text or f"剧 (2026) 第{msg_id}集 1080p {SHARE}", msg_id=msg_id, source=source)
        await self.pl.process_new(ctx)
        return self.store.get_task(ctx.task_id)


@needs_web
class WebCase(unittest.TestCase):
    """临时数据目录里的管理服务（create_app）+ 按本机地址访问的测试客户端。

    CONFIG 写进 config.yaml，SECRETS 写进 secrets.json；prepare(root) 在建应用之前做额外准备
    （如在 root/frontend 放前端文件）。ROOT_PATH 模拟挂在反向代理的路径前缀下。
    """

    CONFIG: dict = {"save_dirs": {"默认": "0"}, "defaults": {"save_to": "默认"}}
    SECRETS: dict = {}
    ROOT_PATH = ""

    def setUp(self):
        from reelay.web import create_app
        env = patch.dict(os.environ, {}, clear=True)
        env.start()
        self.addCleanup(env.stop)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.config_path = self.root / "config.yaml"
        self.config_path.write_text(json.dumps(self.CONFIG, ensure_ascii=False), encoding="utf-8")
        if self.SECRETS:
            secret_store.SecretStore(self.root / "secrets.json").update(dict(self.SECRETS))
        self.prepare(self.root)
        self.app = create_app(data_dir=self.root, frontend_dir=self.root / "frontend")
        self.client = TestClient(self.app, base_url=LOCAL, root_path=self.ROOT_PATH, client=("127.0.0.1", 50000))
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def prepare(self, root: Path) -> None:
        pass


# --------------------------------------------------------------------------- #
# Telegram
# --------------------------------------------------------------------------- #
class FakeTelegram:
    """已连上的 Telethon 客户端：get_entity 与 iter_messages（与 Telethon 一致：默认从新到旧）。"""

    def __init__(self, messages):
        self.messages = messages
        self.requests = []

    async def get_entity(self, ref):
        return types.SimpleNamespace(username="Bot", id=7)

    async def iter_messages(self, entity, limit=None, *, min_id=0, reverse=False):
        """limit 计入所有读到的消息。"""
        self.requests.append(min_id)
        ordered = sorted(self.messages, key=lambda m: m.id, reverse=not reverse)
        returned = 0
        for msg in ordered:
            if msg.id <= min_id:
                continue
            if limit is not None and returned >= limit:
                return
            returned += 1
            yield msg


def tg_message(msg_id: int, *, days: float = 0, action=None):
    date = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
    return types.SimpleNamespace(id=msg_id, raw_text=f"消息 {msg_id} {SHARE}", entities=[], date=date,
                                 action=action)
