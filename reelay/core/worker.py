"""后台 Worker：消费 RECEIVED（解析）、QUEUED（转存）、STORED（整理）。

监听回调只做消息级去重和落库，AI 与 115 网络请求都在 Worker 中执行，避免外部服务变慢时
阻塞 Telegram 更新接收。失败任务保留 ``retry_from``，手动或自动重试回到正确阶段。

- 自动重试写入 ``not_before``，到期后才会被领取，退避期间不占用并发槽；
- 115 cookie 失效或缺失时暂停转存/整理的领取（解析照常），只告警一次；
  凭证恢复后自动继续并发送恢复通知。任务不会因鉴权失效耗尽重试次数；
- 115 触发风控冷却时同样暂停转存/整理，冷却结束自动继续；冷却期间被拒的请求没有发出，任务回到原阶段等待；
- 转存请求已发出、暂存目录还没到齐而转为待人工的任务，后台按退避间隔自动只读核验（见 ``recheck_transfers``）：
  115 在后台落地文件有时要十几分钟，到齐后自动入库，并释放它占着的集，排在后面的任务随之继续；
  连续几次核验结果都没有变化（比如一个文件都没到）时提前提醒一次，后台仍继续复查。
"""

from __future__ import annotations

import asyncio
import logging
import os
import time

from .auth115 import is_auth_failure
from .contracts import MAX_AUTO_RETRIES, StorageBusy, TaskState
from .pipeline import Pipeline
from .store import Store

log = logging.getLogger("reelay.worker")

POLL_INTERVAL = 1.0
# 需要 115 凭证的阶段；鉴权不可用时暂停领取。
STORAGE_STATES = (TaskState.QUEUED, TaskState.STORED)
AUTH_ALERT = "p115_auth"
COOLDOWN_ALERT = "p115_cooldown"
# 115 凭证探活间隔（秒）；凭证失效暂停转存期间固定 30 秒探活，恢复后自动继续
AUTH_CHECK_SEC = 300
# 转存结果待核验的任务：首次在转为待人工 5 分钟后自动复查，之后间隔翻倍（最长 1 小时），持续 24 小时
RECHECK_FIRST_SEC = 300
RECHECK_MAX_INTERVAL_SEC = 3600
RECHECK_WINDOW_SEC = 86400
# 只到了一部分、连续这么多次复查到达数都没变：多半不会再到，不等 24 小时，提前提醒
RECHECK_STABLE_TIMES = 3
RECHECK_SCAN_SEC = 60


class Worker:
    def __init__(self, pipeline: Pipeline, store: Store, concurrency: int = 3):
        self.pipeline = pipeline
        self.store = store
        self.concurrency = max(1, concurrency)
        self.sem = asyncio.Semaphore(self.concurrency)
        self._queue: asyncio.Queue[tuple[str, TaskState]] = asyncio.Queue()
        self._seen_inflight: set[str] = set()
        self._stop = asyncio.Event()
        # 转存/整理暂停的原因：auth（115 凭证失效）、cooldown（115 风控冷却）；只有 _monitor 与鉴权失败会修改。
        self._paused: dict[str, str] = {}
        self._next_auth_check = 0.0
        self._dir_paths_revision: str | None = None  # 已记录目录路径的配置版本
        self._next_dir_paths = 0.0
        self._next_recheck = 0.0

    @property
    def storage_paused(self) -> str | None:
        """转存与整理暂停时的原因（多个原因用分号连接）；没有暂停时为 None。"""
        return "；".join(self._paused.values()) or None

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
            tasks = [producer, *consumers, dispatcher]
            if not drain:
                tasks += [asyncio.create_task(self._monitor()), asyncio.create_task(self._refresh_metadata())]
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

    async def record_dir_paths(self) -> None:
        """记录各目录别名在网盘中的路径，管理台与审批卡片据此显示真实去向。

        启动和配置变更后各读一次；失败时 60 秒后重试，期间页面退回显示别名。
        """
        revision, cfg = self.pipeline.revision, self.pipeline.cfg
        paths = {}
        try:
            storage = self.pipeline.reg.get_provider("storage", cfg["defaults"].get("storage", "p115"))
            for alias, cid in cfg["save_dirs"].items() if hasattr(storage, "dir_path") else ():
                path = await asyncio.to_thread(storage.dir_path, str(cid))
                if path:
                    paths[alias] = {"cid": str(cid), "path": path}
        except Exception as exc:  # noqa: BLE001 - 只影响展示
            if self._next_dir_paths == 0.0:
                log.warning("读取保存目录的网盘路径失败，页面暂时显示目录别名：%s", exc)
            self._next_dir_paths = time.time() + 60
            return
        self.store.state_set("kernel", "save_dir_paths", paths)
        self._dir_paths_revision, self._next_dir_paths = revision, 0.0

    async def _refresh_metadata(self) -> None:
        """启动时补跑一次元数据补全：刚配好 TMDB 时，已有任务也能补上规范元数据与海报。"""
        try:
            count = await self.pipeline.refresh_metadata()
        except Exception as exc:  # noqa: BLE001 - 补全失败不影响主链路
            log.warning("补全已有任务的元数据失败：%s", exc)
            return
        if count:
            log.info("已为 %d 条已有任务补全元数据", count)

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
            self.check_cooldown()
            if time.time() >= self._next_auth_check and "cooldown" not in self._paused:
                await self.check_auth()
            if self._dir_paths_revision != self.pipeline.revision and time.time() >= self._next_dir_paths:
                await self.record_dir_paths()
            if not self.storage_paused and time.time() >= self._next_recheck:
                self._next_recheck = time.time() + RECHECK_SCAN_SEC
                try:
                    await self.recheck_transfers()
                except Exception as exc:  # noqa: BLE001 - 复查失败不影响主链路
                    log.warning("自动复查待核验转存失败：%s", exc)
            try:
                await self.pipeline.emit_daily()
            except Exception as exc:  # noqa: BLE001 - 日报失败不影响主链路
                log.warning("每日系统事件发送失败：%s", exc)
            await asyncio.sleep(1)

    async def recheck_transfers(self, now: float | None = None) -> int:
        """转存请求已发出、核验时暂存目录没到齐的待人工任务：到点后放回队列做一次只读核验（不重发转存）。

        复查记录在 storage.auto_recheck（since / count / next_at，以及 result / stable：上次的核验结果、
        连续几次一样）。复查期间不提醒；连续 RECHECK_STABLE_TIMES 次核验结果都一样时（只到了一部分、一个文件
        都没到、到的文件对不上），等下去多半不会变，提前提醒一次（notified），之后照常复查；超过 24 小时仍未
        核验通过就不再自动复查，没提醒过的这时才通知。
        返回本次放回队列的任务数。
        """
        now = time.time() if now is None else now
        queued = 0
        for row in self.store.list_by_state(TaskState.NEED_MANUAL, 500):
            ctx = self.store.get_task(row["task_id"])
            ns = ctx.ns("storage") if ctx else {}
            if not ctx or ns.get("phase") != "receiving" or not ns.get("manifest"):
                continue
            record = ns.get("auto_recheck") or {}
            since = record.get("since", row["updated_at"])
            if record.get("gave_up") or now < record.get("next_at", since + RECHECK_FIRST_SEC):
                continue
            if now - since > RECHECK_WINDOW_SEC:
                ns["auto_recheck"] = {**record, "since": since, "gave_up": True}
                ctx.log("24 小时内自动复查都没有核验通过，停止自动复查，请到网盘确认后处理")
                log.warning("任务 %s 自动复查 24 小时仍未核验通过，留给人工处理", ctx.task_id)
                if not record.get("notified"):
                    await self.pipeline.notify_uncertain(ctx)  # 复查期间没有提醒过，现在才通知
                try:
                    self.store.update_task_context(ctx, expected=TaskState.NEED_MANUAL, error=row["error"])
                except ValueError:
                    pass  # 期间被人工处理了
                continue
            result = row["error"] or ""  # 上一次核验的结果，如「暂存目录有 0 项……」「已到 1/2 个文件……」
            if not record.get("notified"):
                stable = record.get("stable", 0) + 1 if record.get("result") == result else 1
                record = {**record, "since": since, "result": result, "stable": stable}
                ns["auto_recheck"] = record
            if not record.get("notified") and record["stable"] >= RECHECK_STABLE_TIMES:
                ns["auto_recheck"] = {**record, "notified": True}
                ctx.log(f"连续 {record['stable']} 次核验结果都没有变化，先提醒你处理，后台仍继续复查")
                log.info("任务 %s 转存连续 %d 次核验结果没有变化，提前提醒：%s", ctx.task_id, record["stable"], result)
                await self.pipeline.notify_uncertain(ctx)
                try:
                    self.store.update_task_context(ctx, expected=TaskState.NEED_MANUAL, error=row["error"])
                except ValueError:
                    pass  # 期间被人工处理了
                continue
            count = record.get("count", 0) + 1
            ns["auto_recheck"] = {**record, "since": since, "count": count,
                                  "next_at": now + min(RECHECK_FIRST_SEC * 2 ** count, RECHECK_MAX_INTERVAL_SEC)}
            ns["auto_recheck_run"] = True
            ctx.state = TaskState.QUEUED
            ctx.log(f"自动只读核验转存（第 {count} 次），不重发转存请求")
            try:
                self.store.update_task_context(ctx, expected=TaskState.NEED_MANUAL)
            except ValueError:
                continue  # 期间被人工处理了
            log.info("任务 %s 转存待核验，第 %d 次自动复查", ctx.task_id, count)
            queued += 1
        return queued

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
            self.pause_storage("未配置 115 cookie，转存与整理已暂停；请在管理台「设置 → 115 网盘」扫码登录")
        elif cookie_state == AuthState.EXPIRED:
            self.pause_storage(f"115 cookie 已失效（{auth.cookie_error or '需要重新登录'}），转存与整理已暂停；"
                               "请在管理台「设置 → 115 网盘」重新扫码，恢复后自动继续")
        elif cookie_state == AuthState.ACTIVE:
            self.resume_storage()
        interval = 30 if self.storage_paused else AUTH_CHECK_SEC
        self._next_auth_check = time.time() + interval
        # 探活按间隔进行，带上间隔供管理台判断这条观察是否过期
        self.store.heartbeat("p115_cookie", status=cookie_state, error=auth.cookie_error, check_interval=interval)

    def pause_storage(self, reason: str, kind: str = "auth") -> None:
        if kind not in self._paused:
            log.warning("暂停转存/整理：%s", reason)
        self._paused[kind] = reason
        self.store.raise_alert(AUTH_ALERT if kind == "auth" else COOLDOWN_ALERT, reason, self.pipeline.notify_targets())

    def resume_storage(self, kind: str = "auth") -> None:
        if self._paused.pop(kind, None):
            log.info("%s，继续转存/整理", "115 凭证已恢复" if kind == "auth" else "115 风控冷却结束")
        text = "115 凭证已恢复，转存与整理继续执行" if kind == "auth" else "115 风控冷却结束，转存与整理继续执行"
        self.store.clear_alert(AUTH_ALERT if kind == "auth" else COOLDOWN_ALERT, text, self.pipeline.notify_targets())

    def check_cooldown(self) -> None:
        """115 风控冷却期间暂停转存与整理（请求本来也会被拒），结束后自动继续。"""
        from .auth115 import get_store
        cooling = get_store().cooldown()
        if cooling:
            until, reason = cooling
            self.pause_storage(f"115 触发风控（{reason[:60]}），转存与整理暂停到 {time.strftime('%H:%M', time.localtime(until))}，"
                               "之后自动继续", kind="cooldown")
        elif "cooldown" in self._paused:
            self.resume_storage("cooldown")

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
        except StorageBusy as busy:
            # 网盘暂时拒绝一切请求，这次请求没有发出：回到原阶段，到点再领取，不算失败
            self.pipeline.defer(ctx, busy)
            return
        except Exception as exc:
            log.warning("任务执行异常 %s: %s", task_id, exc)
            ctx.state = TaskState.FAILED
            ctx.ns("task").setdefault("retry_from", expected_state.value)
            ctx.log(f"任务执行异常：{exc}")
            await self.pipeline.emit("on_failed", ctx, persist=False)
            await self.pipeline.notify_all(ctx, "failed", persist=False)
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
                               "请在管理台「设置 → 115 网盘」重新扫码，恢复后自动继续")
            self._next_auth_check = 0.0
            self.store.set_state(task_id, retry_from, expected=TaskState.FAILED, error=error)
            return
        if fresh.ns("task").get("no_auto_retry"):
            log.info("任务 %s 需人工修正，不自动重试", task_id)
            return
        count = self.store.bump_retry(task_id)
        if count <= MAX_AUTO_RETRIES:
            delay = 2 ** count
            log.info("任务 %s 失败，第 %d 次退避重试（%ss 后）", task_id, count, delay)
            self.store.set_state(task_id, retry_from, expected=TaskState.FAILED,
                                 error=error, not_before=time.time() + delay)
        else:
            log.warning("任务 %s 超过重试上限，保持 FAILED，待人工处理", task_id)
