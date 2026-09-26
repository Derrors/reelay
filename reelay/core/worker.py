"""后台 Worker：消费 RECEIVED（解析）、QUEUED（转存）、STORED（整理）。

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
        self.store.acquire_worker()
        tasks = []
        try:
            for ctx in self.store.recover_interrupted():
                if ctx.state in (TaskState.RECEIVED, TaskState.QUEUED, TaskState.STORED):
                    await self._enqueue(ctx.task_id, ctx.state)
            async def produce():
                await self._poll(drain=drain)
                if drain:
                    await self._queue.join()
                    self._stop.set()

            producer = asyncio.create_task(produce())
            consumers = [asyncio.create_task(self._consume()) for _ in range(self.concurrency)]
            dispatcher = asyncio.create_task(self._dispatch(drain=drain))
            monitor = asyncio.create_task(self._monitor()) if not drain else None
            tasks = [producer, *consumers, dispatcher, *([monitor] if monitor else [])]
            await asyncio.gather(*tasks)  # 持久层故障不能让消费者悄悄退出、留下空转进程
        finally:
            self._stop.set()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            try:
                self.store.heartbeat("worker", status="stopped", config_revision=self.pipeline.revision)
            finally:
                self.store.release_worker()

    async def _dispatch(self, *, drain: bool) -> None:
        while not self._stop.is_set():
            await self.pipeline.dispatch_deliveries()
            await asyncio.sleep(0.05 if drain else 0.5)
        if drain:
            while await self.pipeline.dispatch_deliveries():
                pass  # 只排空当前可投递项；退避中的项留待下次运行。

    async def _monitor(self) -> None:
        import os
        while not self._stop.is_set():
            self.pipeline.reload_config()
            self.store.heartbeat("worker", status="running", pid=os.getpid(),
                                 config_revision=self.pipeline.revision,
                                 config_error=self.pipeline.config_error,
                                 inflight=len(self._seen_inflight), concurrency=self.concurrency)
            await asyncio.sleep(1)

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
            for state in (TaskState.STORED, TaskState.RECEIVED, TaskState.QUEUED):
                for row in self.store.list_by_state(state, limit=100):
                    await self._enqueue(row["task_id"], state)
            if drain and not self._seen_inflight and self._queue.empty():
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
            try:
                async with self.sem:
                    await self._handle(task_id, expected_state)
            finally:
                self._seen_inflight.discard(task_id)
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
            await self.pipeline._emit("on_failed", ctx, persist=False)
            await self.pipeline._notify_all(ctx, "failed", persist=False)
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
                self.store.set_state(task_id, retry_from, expected=TaskState.FAILED)
            else:
                log.warning("任务 %s 超过重试上限，保持 FAILED，待人工处理", task_id)
        self._seen_inflight.discard(task_id)
