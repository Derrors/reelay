"""消息源 Provider（SourceProvider）。

- ``TelegramUserbotSource``（name="telegram"）：用你自己的账号（Telethon）监听多个 bot。
  未装 telethon / 未配 api_id 时不可用。
- ``DryRunSource``（name="dryrun"）：内置模拟源，吐几条样例消息用于骨架冒烟，
  不依赖任何三方库与网络。
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable

from ..core import paths, runtime, secret_store
from ..core.contracts import RawMessage
from ..core.registry import register


EmitFn = Callable[[RawMessage], Awaitable[None]]
log = logging.getLogger("reelay.source")


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
        self._session = str(paths.USERBOT_SESSION)
        self._watermark: Callable[[str], int | None] | None = None
        self.backfilled = 0
        self.backfill_done = False  # 本次监听的启动补拉已结束（含失败），日报据此决定何时发送

    def configure(self, targets: list[str]) -> None:
        self._targets = [target.strip().lower() for target in targets]

    def bind_watermark(self, lookup: Callable[[str], int | None]) -> None:
        """注入「来源 → 已接收最大消息 ID」查询；未注入则不补拉。"""
        self._watermark = lookup

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
        # 在管理台「设置」填写；改了要重启主进程（会话在启动时建立）
        self._api_id = secret_store.get("telegram_api_id")
        self._api_hash = secret_store.get("telegram_api_hash")
        if not (self._api_id and self._api_hash):
            raise RuntimeError("未配置 Telegram API ID / API Hash（管理台「设置」中填写），无法启用 telegram 源")

        from telethon import TelegramClient, events  # 延迟导入

        self._client = TelegramClient(self._session, int(self._api_id), self._api_hash, catch_up=True)
        self.backfill_done = False
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
                    "监听会话（data/userbot.session）必须登录个人 Telegram 账号，不能使用控制 Bot 会话"
                )
            self._authorized = True
            ready.set()
            try:
                await self.backfill(emit)
            except Exception as exc:  # noqa: BLE001 - 补拉失败不影响实时监听
                log.warning("历史消息补拉失败：%s", exc)
            finally:
                self.backfill_done = True
            await self._client.run_until_disconnected()
        finally:
            ready.set()
            await self.close()


    async def backfill(self, emit: EmitFn) -> int:
        """补拉每个来源水位之后、最近 max_days 天内最新的 limit 条消息，按时间顺序入库。

        从最新往旧读取：停机很久时优先补最近的消息（较早的分享大多已失效），遇到超过
        max_days 的消息即停止，上限只计入需要补拉的消息。首次监听（无水位）不导入历史。
        """
        limit = int(runtime.get("telegram.backfill_limit", 200))
        max_age = float(runtime.get("telegram.backfill_max_days", 7)) * 86400
        if not self._watermark or limit <= 0:
            return 0
        total = 0
        for target in list(self._targets):
            ref = int(target) if target.lstrip("-").isdigit() else target
            entity = await self._client.get_entity(ref)
            source = _entity_name(entity)
            mark = self._watermark(source)
            if mark is None:
                continue
            recent, truncated = [], False
            async for msg in self._client.iter_messages(entity, min_id=mark):
                date = getattr(msg, "date", None)
                if date is not None and time.time() - date.timestamp() > max_age:
                    break  # 消息按时间倒序，之后的都更旧
                if getattr(msg, "action", None):
                    continue  # 服务消息（置顶、入群等）
                if len(recent) >= limit:
                    truncated = True
                    break
                recent.append(msg)
            for msg in reversed(recent):
                await emit(RawMessage(source=source, msg_id=str(msg.id), text=msg.raw_text or "",
                                      urls=_extract_urls(msg), extra={"backfill": True}))
            if recent:
                log.info("补拉 %s 水位 %s 之后的 %d 条消息", source, mark, len(recent))
            if truncated:
                log.warning("%s 停机期间的消息超过补拉上限 %d 条，更早的消息未补拉", source, limit)
            total += len(recent)
        self.backfilled += total
        return total


def _entity_name(entity: Any) -> str:
    """与实时事件的来源标识一致：有用户名用 @username，否则用数字 ID。"""
    uname = getattr(entity, "username", None)
    return f"@{uname}" if uname else str(getattr(entity, "id", "unknown"))


def _extract_urls(msg: Any) -> list[str]:
    """从消息 entity 里取超链接真实 URL（"点击跳转"这种藏在 text_link 里）。"""
    urls: list[str] = []
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
        return _entity_name(event.chat) if event.chat is not None else "unknown"
    except Exception:
        return "unknown"
