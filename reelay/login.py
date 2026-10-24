"""在交互终端建立个人 Telegram 会话（API ID / Hash 先在管理台「设置 → Telegram」中填写）。

主进程在会话还没登录时等待并定时重试，这里登录成功后会自动接上，不用重启。
"""
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
        raise SystemExit("请先在管理台「设置 → Telegram」中填写个人账号的 API ID 与 API Hash，再执行登录")
    paths.USERBOT_SESSION.parent.mkdir(parents=True, exist_ok=True)
    client = TelegramClient(str(paths.USERBOT_SESSION), int(api_id), api_hash)
    try:
        await client.start()
        account = await client.get_me()
        if not account or getattr(account, "bot", False):
            raise SystemExit(f"这个会话登录的是 Bot，监听需要个人账号：删除 {paths.USERBOT_SESSION} 后重新执行登录")
        name = f"@{account.username}" if getattr(account, "username", None) else str(account.id)
        print(f"个人 Telegram 会话已就绪（{name}），主进程会自动接上")
    finally:
        await client.disconnect()


def main() -> None:
    try:
        asyncio.run(login())
    except KeyboardInterrupt:
        raise SystemExit(130) from None


if __name__ == "__main__":
    main()
