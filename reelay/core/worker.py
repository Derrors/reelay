"""后台 Worker：消费 RECEIVED（解析）、QUEUED（转存）、STORED（整理）。

监听回调只做消息级去重和落库，AI 与 115 网络请求都在 Worker 中执行，避免外部服务变慢时
阻塞 Telegram 更新接收。失败任务保留 ``retry_from``，手动或自动重试回到正确阶段。

- 自动重试写入 ``not_before``，到期后才会被领取，退避期间不占用并发槽；
- 115 cookie 失效或缺失时暂停转存/整理的领取（解析照常），只告警一次；
  凭证恢复后自动继续并发送恢复通知。任务不会因鉴权失效耗尽重试次数。
"""

from __future__ import annotations

import asyncio
import logging
import os
import time

from .contracts import TaskState
from .pipeline import Pipeline
from .store import Store

log = logging.getLogger("reelay.worker")

MAX_RETRIES = 3
POLL_INTERVAL = 1.0
# 需要 115 凭证的阶段；鉴权不可用时暂停领取。
STORAGE_STATES = (TaskState.QUEUED, TaskState.STORED)
AUTH_ALERT = "p115_auth"


def is_auth_failure(message: str | None) -> bool:
    from .auth115 import AUTH_ERRNOS, _is_auth_error
    text = message or ""
    return _is_auth_error(text, AUTH_ERRNOS) or "需要配置 cookie" in text


class Worker:
    def __init__(self, pipeline: Pipeline, store: Store, concurrency: int = 3):
        self.pipeline = pipeline
        self.store = store
        self.concurrency = max(1, concurrency)
        self.sem = asyncio.Semaphore(self.concurrency)
        self._queue: asyncio.Queue[tuple[str, TaskState]] = asyncio.Queue()
        self._seen_inflight: set[str] = set()
        self._stop = asyncio.Event()
        # 非空表示转存/整理暂停及原因；只有 _monitor 和鉴权失败会修改它。
        self.storage_paused: str | None = None
        self._next_auth_check = 0.0

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
        while not self._stop.is_set():
            self.pipeline.reload_config()
            # Worker 自身心跳必须先落库，不能被外部 115 网络请求阻塞。
            self.store.heartbeat("worker", status="running", pid=os.getpid(),
                                 config_revision=self.pipeline.revision,
                                 config_error=self.pipeline.config_error,
                                 inflight=len(self._seen_inflight), concurrency=self.concurrency,
                                 paused=self.storage_paused)
            if time.time() >= self._next_auth_check:
                await self.check_auth()
            try:
                await self.pipeline.emit_daily()
            except Exception as exc:  # noqa: BLE001 - 日报失败不影响主链路
                log.warning("每日系统事件发送失败：%s", exc)
            await asyncio.sleep(1)

    async def check_auth(self) -> None:
        """探活 115 凭证并据此暂停/恢复转存；暂停期间缩短探活间隔以便尽快恢复。"""
        from .auth115 import AuthState, get_store as get_auth115_store

        auth = get_auth115_store()
        auth.reload_if_changed()
        try:
            await asyncio.to_thread(auth.refresh_oauth_if_due)
        except Exception as exc:  # noqa: BLE001
            log.warning("115 OAuth 主动续期失败：%s", exc)
        cookie_state, oauth_state = await asyncio.gather(
            asyncio.to_thread(auth.ping_cookie, True),
            asyncio.to_thread(auth.ping_oauth, True),
        )
        self.store.heartbeat("p115_oauth", status=oauth_state, error=auth.oauth_error,
                             configured=auth.has_oauth)
        if self.pipeline.cfg["defaults"].get("storage", "p115") == "p115" and not auth.has_cookie:
            self.pause_storage("未配置 115 cookie，转存与整理已暂停；请在管理台「连接状态」扫码获取 Cookie")
        elif cookie_state == AuthState.EXPIRED:
            self.pause_storage(f"115 cookie 已失效（{auth.cookie_error or '需要重新登录'}），转存与整理已暂停；"
                               "请在管理台「连接状态」重新扫码，恢复后自动继续")
        elif cookie_state == AuthState.ACTIVE:
            self.resume_storage()
        interval = 30 if self.storage_paused else max(30, int(os.getenv("REELAY_115_AUTH_CHECK_SEC", "300")))
        self._next_auth_check = time.time() + interval
        # 探活按间隔进行，带上间隔供管理台判断这条观察是否过期
        self.store.heartbeat("p115_cookie", status=cookie_state, error=auth.cookie_error, check_interval=interval)

    def pause_storage(self, reason: str) -> None:
        if not self.storage_paused:
            log.warning("暂停转存/整理：%s", reason)
        self.storage_paused = reason
        self.store.raise_alert(AUTH_ALERT, reason, self.pipeline.notify_targets())

    def resume_storage(self) -> None:
        if self.storage_paused:
            log.info("115 凭证已恢复，继续转存/整理")
        self.storage_paused = None
        self.store.clear_alert(AUTH_ALERT, "115 凭证已恢复，转存与整理继续执行", self.pipeline.notify_targets())

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
                if self.storage_paused and state in STORAGE_STATES:
                    continue
                for row in self.store.list_by_state(state, limit=100, due_only=True):
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
            return
        if self.storage_paused and expected_state in STORAGE_STATES:
            return  # 已入队但随后暂停：保持原状态，恢复后再领取
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
        self._schedule_retry(task_id, expected_state)

    def _schedule_retry(self, task_id: str, expected_state: TaskState) -> None:
        fresh = self.store.get_task(task_id)
        if not fresh or fresh.state != TaskState.FAILED:
            return
        retry_from_value = fresh.ns("task").get("retry_from", expected_state.value)
        try:
            retry_from = TaskState(retry_from_value)
        except ValueError:
            retry_from = expected_state
        error = self.store.get_task_record(task_id)["error"]
        if retry_from in STORAGE_STATES and is_auth_failure(error):
            # 鉴权失效不是任务本身的问题：回到原阶段等待恢复，不消耗重试次数。
            self.pause_storage("115 返回鉴权失败（需要重新登录），转存与整理已暂停；"
                               "请在管理台「连接状态」重新扫码，恢复后自动继续")
            self._next_auth_check = 0.0
            self.store.set_state(task_id, retry_from, expected=TaskState.FAILED, error=error)
            return
        if fresh.ns("task").get("no_auto_retry"):
            log.info("任务 %s 需人工修正，不自动重试", task_id)
            return
        count = self.store.bump_retry(task_id)
        if count <= MAX_RETRIES:
            delay = 2 ** count
            log.info("任务 %s 失败，第 %d 次退避重试（%ss 后）", task_id, count, delay)
            self.store.set_state(task_id, retry_from, expected=TaskState.FAILED,
                                 error=error, not_before=time.time() + delay)
        else:
            log.warning("任务 %s 超过重试上限，保持 FAILED，待人工处理", task_id)
