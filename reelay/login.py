"""首次部署时在交互终端建立个人 Telegram 会话。"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

from .core.config import load_env


async def login() -> None:
    from telethon import TelegramClient
    load_env()
    api_id, api_hash = os.getenv("REELAY_TG_API_ID"), os.getenv("REELAY_TG_API_HASH")
    if not api_id or not api_hash:
        raise RuntimeError("请先配置个人账号的 Telegram API_ID/API_HASH")
    session = os.getenv("REELAY_TG_SESSION", "data/userbot.session")
    Path(session).parent.mkdir(parents=True, exist_ok=True)
    client = TelegramClient(session, int(api_id), api_hash)
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
