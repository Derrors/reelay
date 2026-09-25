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
        self._targets: list[str] = []
        self._api_id = os.getenv("REELAY_TG_API_ID")
        self._api_hash = os.getenv("REELAY_TG_API_HASH")
        self._session = os.getenv("REELAY_TG_SESSION", "data/userbot.session")

    def configure(self, targets: list[str]) -> None:
        self._targets = targets

    async def listen(self, emit: EmitFn) -> None:
        if not (self._api_id and self._api_hash):
            raise RuntimeError("未配置 REELAY_TG_API_ID / API_HASH，无法启用 telegram 源")

        from telethon import TelegramClient, events  # 延迟导入

        self._client = TelegramClient(self._session, int(self._api_id), self._api_hash)

        @self._client.on(events.NewMessage(from_users=self._targets or None))
        async def _handler(event):  # noqa: ANN001
            msg = event.message
            urls = _extract_urls(msg)
            await emit(RawMessage(
                source=_peer_name(event),
                msg_id=str(msg.id),
                text=msg.raw_text or "",
                urls=urls,
            ))

        await self._client.start()
        await self._client.run_until_disconnected()


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
