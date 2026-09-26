"""后台 Worker：分别消费 RECEIVED（解析）与 QUEUED（转存）。

监听回调只做消息级去重和落库，AI 与 115 网络请求都在 Worker 中执行，避免外部服务变慢时
阻塞 Telegram 更新接收。失败任务保留 ``retry_from``，手动或自动重试回到正确阶段。
"""

from __future__ import annotations

import asyncio
import logging

from .contracts import TaskState
from .pipeline import Pipeline
from .store import Store

log = logging.getLogger("reelay.worker")

MAX_RETRIES = 3
POLL_INTERVAL = 1.0


class Worker:
    def __init__(self, pipeline: Pipeline, store: Store, concurrency: int = 3):
        self.pipeline = pipeline
        self.store = store
        self.concurrency = max(1, concurrency)
        self.sem = asyncio.Semaphore(self.concurrency)
        self._queue: asyncio.Queue[tuple[str, TaskState]] = asyncio.Queue()
        self._seen_inflight: set[str] = set()
        self._stop = asyncio.Event()

    async def run(self, *, drain: bool = False) -> None:
        for ctx in self.store.recover_interrupted():
            await self._enqueue(ctx.task_id, ctx.state)

        producer = asyncio.create_task(self._poll(drain=drain))
        consumers = [asyncio.create_task(self._consume()) for _ in range(self.concurrency)]

        await producer
        if drain:
            await self._queue.join()
            self._stop.set()
        for consumer in consumers:
            await consumer

    def stop(self) -> None:
        self._stop.set()

    async def _enqueue(self, task_id: str, state: TaskState) -> bool:
        if task_id in self._seen_inflight:
            return False
        self._seen_inflight.add(task_id)
        await self._queue.put((task_id, state))
        return True

    async def _poll(self, *, drain: bool) -> None:
        while not self._stop.is_set():
            new = 0
            for state in (TaskState.RECEIVED, TaskState.QUEUED):
                for row in self.store.list_by_state(state, limit=100):
                    if await self._enqueue(row["task_id"], state):
                        new += 1
            if drain and new == 0 and self._queue.empty():
                return
            await asyncio.sleep(POLL_INTERVAL if not drain else 0.05)

    async def _consume(self) -> None:
        while not self._stop.is_set() or not self._queue.empty():
            try:
                task_id, expected_state = await asyncio.wait_for(
                    self._queue.get(), timeout=0.5
                )
            except asyncio.TimeoutError:
                if self._stop.is_set():
                    return
                continue
            async with self.sem:
                await self._handle(task_id, expected_state)
            self._queue.task_done()

    async def _handle(self, task_id: str, expected_state: TaskState) -> None:
        ctx = self.store.get_task(task_id)
        if not ctx or ctx.state != expected_state:
            self._seen_inflight.discard(task_id)
            return
        try:
            if expected_state == TaskState.RECEIVED:
                await self.pipeline.process_received(ctx)
            else:
                await self.pipeline.execute_save(ctx)
        except Exception as exc:
            log.warning("任务执行异常 %s: %s", task_id, exc)
            ctx.state = TaskState.FAILED
            ctx.ns("task").setdefault("retry_from", expected_state.value)
            ctx.log(f"任务执行异常：{exc}")
            self.store.save_task(ctx, error=str(exc))

        fresh = self.store.get_task(task_id)
        if fresh and fresh.state == TaskState.FAILED:
            retry_from_value = fresh.ns("task").get("retry_from", expected_state.value)
            try:
                retry_from = TaskState(retry_from_value)
            except ValueError:
                retry_from = expected_state
            count = self.store.bump_retry(task_id)
            if count <= MAX_RETRIES:
                delay = 2 ** count
                log.info("任务 %s 失败，第 %d 次退避重试（%ss 后）", task_id, count, delay)
                await asyncio.sleep(delay)
                self.store.set_state(task_id, retry_from)
            else:
                log.warning("任务 %s 超过重试上限，保持 FAILED，待人工处理", task_id)
        self._seen_inflight.discard(task_id)
