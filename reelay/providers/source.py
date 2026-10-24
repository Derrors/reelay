"""消息源 Provider（SourceProvider）。

- ``TelegramUserbotSource``（name="telegram"）：用你自己的账号（Telethon）监听多个 bot。
  未配 API ID / Hash 或会话还没登录时不退出：health 报告原因并定时重试（见 _connect）。

模拟源 ``dryrun`` 在 ``providers/dryrun.py``。
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
RETRY_SECONDS = 30  # 还没配置 / 没登录 / 连不上时多久重新检查一次
LOGIN_HINT = ("监听会话还没有登录：在终端执行 python -m reelay.login（Docker 部署见 docs/quickstart.md），"
              "登录后自动接上，不用重启")


# --------------------------------------------------------------------------- #
# Telegram userbot 源
# --------------------------------------------------------------------------- #
@register(kind="provider", ptype="source")
class TelegramUserbotSource:
    name = "telegram"

    def __init__(self) -> None:
        self._client = None
        self._authorized = False
        self._problem: tuple[str, str] | None = None  # (状态, 说明)：还没配置、没登录或连不上
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
        if self._problem:
            status, error = self._problem
            return {"status": status, "error": error}
        connected = bool(self._client and self._client.is_connected())
        return {"status": "connected" if connected and self._authorized else ("connecting" if connected else "disconnected")}

    async def close(self) -> None:
        self._authorized = False
        if self._client:
            await self._client.disconnect()

    async def listen(self, emit: EmitFn) -> None:
        from telethon import events  # 延迟导入

        self.backfill_done = False
        ready = asyncio.Event()

        # 过滤在回调中读取当前目标列表，热更新无需重建会话；空列表不监听任何来源。
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
            self._client = await self._connect(lambda client: client.add_event_handler(_handler, events.NewMessage()))
            # 放行实时消息之前记下各来源的水位：实时消息会推高水位，之后再读会漏掉停机期间的消息
            marks = await self._watermarks()
            self._authorized = True
            ready.set()
            try:
                await self.backfill(emit, marks)
            except Exception as exc:  # noqa: BLE001 - 补拉失败不影响实时监听
                log.warning("历史消息补拉失败：%s", exc)
            finally:
                self.backfill_done = True
            await self._client.run_until_disconnected()
        finally:
            ready.set()
            await self.close()

    async def _connect(self, setup: Callable[[Any], None]) -> Any:
        """连上 Telegram 并确认会话登录的是个人账号，返回客户端。

        API ID / Hash 在管理台「设置 → Telegram」填写，会话用 ``python -m reelay.login`` 建立（需要交互输入，
        不能在这里做）；做不到时记下原因交给 health，每 RETRY_SECONDS 秒重新检查，补上后自动接上。
        """
        from telethon import TelegramClient  # 延迟导入

        reported = None
        while True:
            api_id, api_hash = secret_store.get("telegram_api_id"), secret_store.get("telegram_api_hash")
            if not (api_id and api_hash):
                problem = ("not_configured", "未配置 Telegram API ID / API Hash：在管理台「设置 → Telegram」中填写")
            else:
                client = TelegramClient(self._session, int(api_id), api_hash, catch_up=True)
                setup(client)
                try:
                    await client.connect()
                    if not await client.is_user_authorized():
                        problem = ("not_logged_in", LOGIN_HINT)
                    elif getattr(await client.get_me(), "bot", True):
                        problem = ("not_logged_in", "监听会话登录的是 Bot，需要个人账号："
                                                    f"删除 {paths.USERBOT_SESSION.name} 后重新执行 python -m reelay.login")
                    else:
                        self._problem = None
                        return client
                except OSError as exc:  # 含 ConnectionError：网络问题，稍后重试
                    problem = ("disconnected", f"连不上 Telegram：{exc}")
                await client.disconnect()
            self._problem = problem
            if problem != reported:
                log.warning("Telegram 监听未启动：%s", problem[1])
                reported = problem
            await asyncio.sleep(RETRY_SECONDS)

    async def _watermarks(self) -> list[tuple[Any, str, int]]:
        """每个来源的（实体, 来源标识, 水位）。首次监听（无水位）的来源不补拉；找不到的来源跳过，不影响其余来源。"""
        if not self._watermark:
            return []
        marks = []
        for target in list(self._targets):
            try:
                entity = await self._client.get_entity(int(target) if target.lstrip("-").isdigit() else target)
            except Exception as exc:  # noqa: BLE001
                log.warning("补拉：找不到来源 %s（%s），跳过", target, exc)
                continue
            source = _entity_name(entity)
            mark = self._watermark(source)
            if mark is not None:
                marks.append((entity, source, mark))
        return marks

    async def backfill(self, emit: EmitFn, marks: list[tuple[Any, str, int]] | None = None) -> int:
        """补拉每个来源水位之后、最近 max_days 天内最新的 limit 条消息，按时间顺序入库。

        marks 是监听开始时记下的水位（见 _watermarks），省略时现在读取。从最新往旧读取：停机很久时
        优先补最近的消息（较早的分享大多已失效），遇到超过 max_days 的消息即停止，上限只计入需要补拉的消息。
        一个来源读取失败只跳过这个来源。
        """
        limit = int(runtime.get("telegram.backfill_limit", 200))
        max_age = float(runtime.get("telegram.backfill_max_days", 7)) * 86400
        if limit <= 0:
            return 0
        total = 0
        for entity, source, mark in (await self._watermarks() if marks is None else marks):
            recent, truncated = [], False
            try:
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
            except Exception as exc:  # noqa: BLE001 - 一个来源读不到不影响其余来源
                log.warning("补拉 %s 失败：%s", source, exc)
                continue
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


    async def recent(self, target: str, limit: int = 10) -> list[dict]:
        """来源最近的消息（新的在前，跳过服务消息与空消息），供管理台生成识别规则。"""
        if not (self._client and self._authorized):
            raise RuntimeError("监听会话还没有连上 Telegram")
        target = target.strip()
        entity = await self._client.get_entity(int(target) if target.lstrip("-").isdigit() else target)
        out = []
        async for msg in self._client.iter_messages(entity, limit=limit * 3):
            if getattr(msg, "action", None) or not (msg.raw_text or "").strip():
                continue
            out.append({"text": msg.raw_text, "urls": _extract_urls(msg)})
            if len(out) >= limit:
                break
        return out


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
