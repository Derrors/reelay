"""消息源 Provider（SourceProvider）。

- ``TelegramUserbotSource``（name="telegram"）：用你自己的账号（Telethon）监听多个 bot。
  未装 telethon / 未配 api_id 时不可用。
- ``DryRunSource``（name="dryrun"）：内置模拟源，吐几条样例消息用于骨架冒烟，
  不依赖任何三方库与网络。
"""

from __future__ import annotations

import asyncio
import os
from typing import Any, Awaitable, Callable

from ..core.contracts import RawMessage
from ..core.registry import register


EmitFn = Callable[[RawMessage], Awaitable[None]]


# --------------------------------------------------------------------------- #
# Dry-run 模拟源
# --------------------------------------------------------------------------- #
_SAMPLES = [
    # 一条正常剧集资源
    {
        "source": "@example_tv_bot",
        "text": (
            "📺 法医秦明之龙番往事 (2026) 第1季 更新至第8集 ✨4K WEB-DL\n"
            "🌍 地区： China\n💾 大小： 15.12 GB\n🔗 链接： https://115.com/s/swabc123?password=x1y2"
        ),
        "urls": ["https://115.com/s/swabc123?password=x1y2"],
    },
    # 一条广告（应被过滤）
    {
        "source": "@example_tv_bot",
        "text": "🔥 招商广告：更多资源关注本频道，点击进群 👉 t.me/xxx",
        "urls": ["https://t.me/xxx"],
    },
    # 一条电影资源
    {
        "source": "@example_movie_bot",
        "text": "🎬 流浪地球2 (2023) 4K HDR\n💾 62 GB\n🔗 https://115.com/s/mv2gala?password=abcd",
        "urls": ["https://115.com/s/mv2gala?password=abcd"],
    },
]


@register(kind="provider", ptype="source")
class DryRunSource:
    name = "dryrun"

    async def listen(self, emit: EmitFn) -> None:
        for i, s in enumerate(_SAMPLES):
            await emit(RawMessage(
                source=s["source"],
                msg_id=f"dry-{i}",
                text=s["text"],
                urls=s["urls"],
            ))
            await asyncio.sleep(0.05)
        # dry-run 源发完即结束（真实源是长驻的）


# --------------------------------------------------------------------------- #
# Telegram userbot 源
# --------------------------------------------------------------------------- #
@register(kind="provider", ptype="source")
class TelegramUserbotSource:
    name = "telegram"

    def __init__(self) -> None:
        self._client = None
        self._authorized = False
        self._targets: list[str] = []
        self._api_id = os.getenv("REELAY_TG_API_ID")
        self._api_hash = os.getenv("REELAY_TG_API_HASH")
        self._session = os.getenv("REELAY_TG_SESSION", "data/userbot.session")

    def configure(self, targets: list[str]) -> None:
        self._targets = [target.strip().lower() for target in targets]

    def matches(self, event: Any) -> bool:
        identities = {_peer_name(event).lower(), str(getattr(event, "sender_id", "")),
                      str(getattr(event, "chat_id", ""))}
        return bool(set(self._targets) & identities)

    def health(self) -> dict:
        connected = bool(self._client and self._client.is_connected())
        return {"status": "connected" if connected and self._authorized else ("connecting" if connected else "disconnected")}

    async def close(self) -> None:
        self._authorized = False
        if self._client:
            await self._client.disconnect()

    async def listen(self, emit: EmitFn) -> None:
        if not (self._api_id and self._api_hash):
            raise RuntimeError("未配置 REELAY_TG_API_ID / API_HASH，无法启用 telegram 源")

        from telethon import TelegramClient, events  # 延迟导入

        self._client = TelegramClient(self._session, int(self._api_id), self._api_hash, catch_up=True)
        ready = asyncio.Event()

        # 过滤在回调中读取当前目标列表，热更新无需重建会话；空列表不监听任何来源。
        @self._client.on(events.NewMessage())
        async def _handler(event):  # noqa: ANN001
            await ready.wait()
            if not self._authorized:
                return
            if _peer_name(event) == "unknown" and hasattr(event, "get_chat"):
                await event.get_chat()  # 少数短更新没有实体缓存时，补取来源标识。
            if not self.matches(event):
                return
            msg = event.message
            urls = _extract_urls(msg)
            await emit(RawMessage(
                source=_peer_name(event),
                msg_id=str(msg.id),
                text=msg.raw_text or "",
                urls=urls,
            ))

        try:
            await self._client.start()
            account = await self._client.get_me()
            if not account or getattr(account, "bot", False):
                raise RuntimeError(
                    "REELAY_TG_SESSION 必须登录个人 Telegram 账号，不能使用控制 Bot 会话"
                )
            self._authorized = True
            ready.set()
            await self._client.run_until_disconnected()
        finally:
            ready.set()
            await self.close()


def _extract_urls(msg: Any) -> list[str]:
    """从消息 entity 里取超链接真实 URL（"点击跳转"这种藏在 text_link 里）。"""
    urls: list[str] = []
    try:
        for _, url in (msg.get_entities_text() or []):
            pass
    except Exception:
        pass
    try:
        from telethon.tl.types import MessageEntityTextUrl
        for ent in (msg.entities or []):
            if isinstance(ent, MessageEntityTextUrl) and ent.url:
                urls.append(ent.url)
    except Exception:
        pass
    # 兜底：正文里的明文链接
    import re
    urls += re.findall(r"https?://[^\s]+", msg.raw_text or "")
    return list(dict.fromkeys(urls))


def _peer_name(event: Any) -> str:
    try:
        chat = event.chat
        uname = getattr(chat, "username", None)
        return f"@{uname}" if uname else str(getattr(chat, "id", "unknown"))
    except Exception:
        return "unknown"
