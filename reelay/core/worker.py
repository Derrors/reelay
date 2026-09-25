"""后台 Worker —— 生产者-队列-消费者的消费端。

- 轮询 Store 里 ``QUEUED`` 的任务，投入内存队列
- 若干 consumer 协程受 ``Semaphore`` 限流并发消费，调用 ``pipeline.execute_save``
- 失败按 ``bump_retry`` 计数，在上限内指数退避重试；超限置 FAILED（等人工/告警）

监听（生产者）与本 Worker（消费者）解耦：监听只管快速落库，
即便 AI 慢 / 115 抖动 / 进程重启，队列里的任务都不丢、可重放。
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
        self.sem = asyncio.Semaphore(concurrency)
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._seen_inflight: set[str] = set()
        self._stop = asyncio.Event()

    async def run(self, *, drain: bool = False) -> None:
        """启动 worker。

        drain=True：把当前所有 QUEUED 处理完就返回（用于 dry-run 冒烟）。
        drain=False：长驻轮询。
        """
        # 重启恢复：把中断的 saving/queued 捡回
        for ctx in self.store.recover_interrupted():
            if ctx.task_id not in self._seen_inflight:
                await self._queue.put(ctx.task_id)
                self._seen_inflight.add(ctx.task_id)

        producer = asyncio.create_task(self._poll(drain=drain))
        consumers = [asyncio.create_task(self._consume()) for _ in range(self.sem._value)]

        await producer
        # drain 模式：等队列清空再停
        if drain:
            await self._queue.join()
            self._stop.set()
        for c in consumers:
            await c

    def stop(self) -> None:
        self._stop.set()

    async def _poll(self, *, drain: bool) -> None:
        while not self._stop.is_set():
            rows = self.store.list_by_state(TaskState.QUEUED, limit=100)
            new = 0
            for r in rows:
                tid = r["task_id"]
                if tid not in self._seen_inflight:
                    self._seen_inflight.add(tid)
                    await self._queue.put(tid)
                    new += 1
            if drain and new == 0 and self._queue.empty():
                return
            await asyncio.sleep(POLL_INTERVAL if not drain else 0.05)

    async def _consume(self) -> None:
        while not self._stop.is_set() or not self._queue.empty():
            try:
                tid = await asyncio.wait_for(self._queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                if self._stop.is_set():
                    return
                continue
            async with self.sem:
                await self._handle(tid)
            self._queue.task_done()

    async def _handle(self, task_id: str) -> None:
        ctx = self.store.get_task(task_id)
        if not ctx or ctx.state != TaskState.QUEUED:
            self._seen_inflight.discard(task_id)
            return
        try:
            await self.pipeline.execute_save(ctx)
        except Exception as exc:  # execute_save 内部已处理大多数，这里兜底
            log.warning("execute_save 异常 %s: %s", task_id, exc)

        # 失败重试
        fresh = self.store.get_task(task_id)
        if fresh and fresh.state == TaskState.FAILED:
            n = self.store.bump_retry(task_id)
            if n <= MAX_RETRIES:
                delay = 2 ** n
                log.info("任务 %s 失败，第 %d 次退避重试（%ss 后）", task_id, n, delay)
                await asyncio.sleep(delay)
                self.store.set_state(task_id, TaskState.QUEUED)
            else:
                log.warning("任务 %s 超过重试上限，保持 FAILED，待人工/告警", task_id)
        self._seen_inflight.discard(task_id)
