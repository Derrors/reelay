"""首次部署时在交互终端建立个人 Telegram 会话（API ID / Hash 先在管理台「设置」中填写）。"""
from __future__ import annotations

import asyncio

from .core import paths, secret_store
from .core.config import ensure_config


async def login() -> None:
    from telethon import TelegramClient
    ensure_config()
    store = secret_store.use(paths.SECRETS)
    api_id, api_hash = store.get("telegram_api_id"), store.get("telegram_api_hash")
    if not api_id or not api_hash:
        raise RuntimeError("请先在管理台「设置 → Telegram 与控制 Bot」中填写个人账号的 API ID 与 API Hash")
    paths.USERBOT_SESSION.parent.mkdir(parents=True, exist_ok=True)
    client = TelegramClient(str(paths.USERBOT_SESSION), int(api_id), api_hash)
    try:
        await client.start()
        account = await client.get_me()
        if not account or getattr(account, "bot", False):
            raise RuntimeError("监听会话必须登录个人账号")
        print("个人 Telegram 会话已就绪")
    finally:
        await client.disconnect()


if __name__ == "__main__":
    asyncio.run(login())
