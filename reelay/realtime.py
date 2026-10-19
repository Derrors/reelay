"""实时事件推送（SSE）。

设计要点：

* web 与 worker 在 compose 部署下是两个进程，只共享同一个 SQLite，无法走内存
  事件总线。这里在 web 进程内放**唯一一个**观察者，定时对 SQLite 做轻量 diff，
  把 N 个浏览器标签的轮询收敛成「服务端 1 次查询」。
* 有变化才向所有 /api/events 客户端广播；无变化发 SSE keepalive 注释保活。
* 浏览器端 EventSource 原生支持断线自动重连，后端不维护历史，客户端重连后应先
  重新拉一次快照（前端在 onopen 时处理），因此这里只做「变化通知」而非消息队列。
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import Any, AsyncIterator

# 观察者轮询间隔：这是服务端对 SQLite 的唯一固定查询，成本极低。
WATCH_INTERVAL = 1.5
# 无事件时保活间隔；反向代理通常 30~60s 空闲断流，15s 足够安全。
KEEPALIVE_INTERVAL = 15.0
# 初始快照里只取最近更新的若干条，供前端判断任务列表是否需要刷新。
RECENT_LIMIT = 200


class EventBus:
    """进程内 SSE 订阅者集合 + 唯一观察者。"""

    def __init__(self, *, store, service) -> None:
        self._store = store
        self._service = service
        self._subscribers: set[asyncio.Queue[str | None]] = set()
        self._task: asyncio.Task | None = None
        self._last_signature: tuple | None = None
        self._last_keepalive = 0.0

    # ------------------------------------------------------------------ #
    # 订阅
    # ------------------------------------------------------------------ #
    def subscribe(self) -> asyncio.Queue[str | None]:
        queue: asyncio.Queue[str | None] = asyncio.Queue(maxsize=64)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[str | None]) -> None:
        self._subscribers.discard(queue)

    def _broadcast(self, payload: dict[str, Any]) -> None:
        import json
        event = payload.get("type", "message")
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        frame = f"event: {event}\ndata: {data}\n\n"
        for queue in list(self._subscribers):
            try:
                # 满了说明消费端卡顿：丢弃最旧的非致命帧，保证观察者不被拖死。
                if queue.full():
                    with contextlib.suppress(Exception):
                        queue.get_nowait()
                queue.put_nowait(frame)
            except Exception:
                self._subscribers.discard(queue)

    # ------------------------------------------------------------------ #
    # 观察者生命周期
    # ------------------------------------------------------------------ #
    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._watch(), name="reelay-sse-watch")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        for queue in list(self._subscribers):
            with contextlib.suppress(Exception):
                queue.put_nowait(None)
        self._subscribers.clear()

    async def _watch(self) -> None:
        """定时 diff；首个状态总是广播一次，让重连客户端立刻对齐。"""
        first = True
        while True:
            try:
                changed, signature = await asyncio.to_thread(self._snapshot)
                now = time.monotonic()
                if first or changed:
                    self._broadcast({"type": "state", "changed": changed, "ts": int(time.time())})
                    first = False
                elif now - self._last_keepalive >= KEEPALIVE_INTERVAL and self._subscribers:
                    self._broadcast({"type": "ping", "ts": int(time.time())})
                    self._last_keepalive = now
                self._last_signature = signature
            except asyncio.CancelledError:
                raise
            except Exception:
                # 观察者不能因为单次读库异常退出；下一轮自愈。
                pass
            await asyncio.sleep(WATCH_INTERVAL)

    # ------------------------------------------------------------------ #
    # SQLite 轻量快照（在线程池中执行，避免阻塞事件循环）
    # ------------------------------------------------------------------ #
    def _snapshot(self) -> tuple[list[str], tuple]:
        store = self._store
        counts = store.counts_by_state()
        deliveries = store.delivery_counts()
        # 心跳状态（worker/source 是否存活、配置版本）是仪表盘关键信息。
        health = store.health_status()
        worker = health.get("worker", {})
        source = health.get("source", {}) if isinstance(health, dict) else {}
        # 最近更新任务的 (id, state, updated_at, error) 指纹：任何任务推进都会变。
        try:
            rows = store.list_recent_updated(RECENT_LIMIT) if hasattr(store, "list_recent_updated") else []
        except Exception:
            rows = []
        recent = tuple((r["task_id"], r["state"], round(float(r["updated_at"]), 1)) for r in rows)
        alerts = tuple((a["key"], a["updated_at"]) for a in store.active_alerts())

        signature = (
            tuple(sorted(counts.items())),
            tuple(sorted(deliveries.items())),
            worker.get("status"), bool(worker.get("stale")),
            source.get("status"), bool(source.get("stale")),
            recent, alerts,
        )

        changed: list[str] = []
        if self._last_signature is None:
            changed = ["counts", "deliveries", "runtime", "tasks"]
            return changed, signature

        prev_counts, prev_deliveries, pw, pws, ps, pss, prev_recent, prev_alerts = self._last_signature
        cur_counts, cur_deliveries, cw, cws, cs, css, cur_recent, cur_alerts = signature
        if cur_counts != prev_counts:
            changed.append("counts")
        if cur_deliveries != prev_deliveries:
            changed.append("deliveries")
        if (cw, cws, cs, css, cur_alerts) != (pw, pws, ps, pss, prev_alerts):
            changed.append("runtime")
        if cur_recent != prev_recent:
            changed.append("tasks")
        return changed, signature


async def event_stream(bus: EventBus) -> AsyncIterator[bytes]:
    """SSE 响应体生成器。"""
    queue = bus.subscribe()
    try:
        yield b": connected\n\n"
        # 新连接立即通知前端去拉一次快照对齐（首帧可能早于首个广播）。
        yield b"event: hello\ndata: {}\n\n"
        while True:
            frame = await queue.get()
            if frame is None:
                break
            yield frame.encode("utf-8")
    finally:
        bus.unsubscribe(queue)
