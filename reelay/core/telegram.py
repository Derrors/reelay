"""Telethon 客户端的共用约定：断线后由 Reelay 自己重新连接，不交给 Telethon 自动重连。

Telethon 自动重连时，经透明代理（TUN）的连接在代理上游不通时也能「连上」，随即被重置，于是无休止地重连：
期间 ``is_connected()`` 一直为真，看不出已经断了；每次重连后它还会排一个请求，网络恢复时这些请求一齐发出，
被 Telegram 限流（HTTP 429），处理更新的循环随之出错。关掉自动重连后，连接被重置、或 Telethon 自己的心跳
ping 没有回应时，``run_until_disconnected()`` 立即结束，由监听与控制 Bot 用新的客户端重新连接。
"""
from __future__ import annotations

import logging

CLIENT_OPTIONS = {"auto_reconnect": False}  # 监听与控制 Bot 的 TelegramClient 都带上
CONNECT_TIMEOUT = 60  # 一次连接（含确认登录）最多等多久
RECONNECT_SEC = 5     # 断线后过多久重新连接；还连不上时按各自的重试间隔再试


def transient(exc: BaseException) -> bool:
    """网络或 Telegram 那边的临时问题（连不上、超时、连接被重置、限流、服务端出错）：稍后重试即可。"""
    if isinstance(exc, (OSError, EOFError)):  # 含 ConnectionError、TimeoutError 与连接被关闭时的 IncompleteReadError
        return True
    from telethon.errors import FloodError, InvalidBufferError, ServerError  # 延迟导入
    return isinstance(exc, (FloodError, InvalidBufferError, ServerError))


def describe(exc: BaseException) -> str:
    """连不上的原因（给 health 与日志）。"""
    return str(exc) or ("连接超时" if isinstance(exc, TimeoutError) else type(exc).__name__)


class _OwnReconnect(logging.Filter):
    """关掉自动重连后，Telethon 每次断线都以 ERROR 记一条「自动重连失败 0 次」；重连由 Reelay 负责，不记这条。"""

    def filter(self, record: logging.LogRecord) -> bool:
        return not (record.msg == "Automatic reconnection failed %d time(s)" and record.args == (0,))


logging.getLogger("telethon.network.mtprotosender").addFilter(_OwnReconnect())
