"""同步网盘调用取消时等线程退出，避免旧线程和恢复 Worker 同时修改网盘。"""
from __future__ import annotations

import asyncio
from contextlib import suppress


async def run_blocking(function, *args):
    task = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        with suppress(Exception):
            await task
        raise
