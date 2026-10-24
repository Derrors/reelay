"""Reelay 入口。

用法::

    python -m reelay.main --dry-run     # 模拟运行：样例消息 → 识别 → 审批 → 转存 → 整理，全在内存里
    python -m reelay.main               # 正常运行：Telegram 监听 + Worker + 控制 Bot 长驻

--dry-run 用内置默认配置、模拟消息源与内存网盘（见 ``providers/dryrun.py``），不读本机的配置与密钥，
不访问网络，走的是与正常运行相同的识别、选集、核验与整理逻辑。

运行文件都在 data/ 下（见 ``core/paths.py``）；配置与密钥在管理台修改。首次启动时写出默认配置。
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import time

from .core import messages, paths
from .core.config import ensure_config, load_config, normalize_config
from .core.contracts import PipelineContext, RawMessage, TaskState
from .core.pipeline import Pipeline
from .core.registry import discover
from .core.store import Store
from .core.service import ManagementService
from .core.worker import Worker


# 审批超时扫描间隔（秒）
SWEEP_INTERVAL_SEC = 3600


# 运行日志按大小轮转：单个文件最多 10 MB，保留 5 个旧文件（reelay.log.1 … .5）
LOG_MAX_BYTES = 10 * 1024 * 1024
LOG_BACKUPS = 5
# 第三方库的 INFO 日志（Telegram 每次拉取更新、每个 HTTP 请求）量大且没有排查价值，只记警告以上
QUIET_LOGGERS = ("telethon", "httpx", "httpx2", "httpcore", "openai")


def _setup_log(*, to_file: bool) -> None:
    """日志级别取系统配置 runtime.log_level（重启生效）；正常运行时同时写入 data/reelay.log 供管理台查看。"""
    from logging.handlers import RotatingFileHandler
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    level = "INFO"
    if to_file:
        paths.LOG.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(RotatingFileHandler(paths.LOG, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUPS,
                                            encoding="utf-8"))
        try:
            level = load_config()["runtime"]["log_level"]
        except ValueError:
            pass  # 配置无效时启动流程会报出具体原因
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handlers,
    )
    for name in QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


async def _run_dry() -> int:
    """模拟运行：内置默认配置 + 模拟消息源 + 内存网盘，打印每条任务的轨迹与整理后的媒体库。"""
    from .providers import dryrun
    reg = discover()
    log = logging.getLogger("reelay.dry")
    storage = dryrun.install(reg)
    dirs = {alias: str(storage.drive.mkdir(0, alias)) for alias in ("电影", "剧集", "动漫")}
    cfg = normalize_config({
        "save_dirs": {**dirs, "默认": "0"},
        "providers": {"source": "dryrun", "notify": ["console"]},
        "defaults": {"parser": "regex", "storage": "dryrun"},
        "sources": [
            {"bot": "@example_tv_bot", "approval_mode": "manual", "save_to": "剧集",
             "filters": {"must_have": ["115."], "block_keywords": ["招商", "广告", "更多资源关注"]}},
            {"bot": "@example_movie_bot", "approval_mode": "auto", "save_to": "电影",
             "filters": {"must_have": ["115."], "block_keywords": ["招商", "广告"]}},
        ],
    })
    reg.validate_config(cfg)
    store = Store(":memory:")
    pipeline = Pipeline(reg, store, cfg)

    async def emit(raw: RawMessage) -> None:
        await pipeline.process_new(PipelineContext(raw=raw))

    await reg.get_provider("source", "dryrun").listen(emit)
    # 模拟人工：把所有 pending_approval 都批准，演示完整转存链路
    for r in store.list_by_state(TaskState.PENDING_APPROVAL, 100):
        log.info("模拟人工确认：%s（%s）", r["task_id"], r["title"])
        await pipeline.approve(r["task_id"])
    # 消费者：把队列排干
    await Worker(pipeline, store, concurrency=2).run(drain=True)

    counts = store.counts_by_state()
    print("\n================= 模拟运行结果 =================")
    print("状态计数：", counts)
    print("\n各任务轨迹：")
    for st in (TaskState.DONE, TaskState.REJECTED, TaskState.SKIPPED, TaskState.NEED_MANUAL, TaskState.FAILED):
        for r in store.list_by_state(st, 100):
            ctx = store.get_task(r["task_id"])
            print(f"\n[{st.value}] {r['title'] or r['task_id']}  (来源 {ctx.raw.source})")
            for t in ctx.trail:
                print(f"    · {t}")
    print("\n整理后的网盘（内存里）：")
    print("\n".join(f"    {line}" for line in storage.drive.tree()) or "    （空）")
    done, rejected = counts.get("done", 0), counts.get("rejected", 0)
    ok = done >= 2 and rejected >= 1     # 2 条资源应转存成功、1 条广告应被拒
    print("\n冒烟判定：", "✅ PASS" if ok else "❌ FAIL", f"(done={done}, rejected={rejected})")
    print("================================================\n")
    store.close()
    return 0 if ok else 1


async def _serve_fetch(store: Store, source, request: dict) -> None:
    """管理台「获取最近消息」：监听会话只在主进程里，由这里代为读取并写回数据库。"""
    try:
        if not hasattr(source, "recent"):
            raise RuntimeError("当前消息源不支持读取历史消息")
        messages = await asyncio.wait_for(source.recent(request["bot"], request["max_count"]), 20)
    except asyncio.CancelledError:
        store.finish_source_fetch(request["id"], error="主进程正在切换消息源")
        raise
    except Exception as exc:  # noqa: BLE001 - 读取失败交给管理台改用已收到的消息
        store.finish_source_fetch(request["id"], error=str(exc)[:200] or type(exc).__name__)
        return
    store.finish_source_fetch(request["id"], messages=messages)


async def _source_loop(pipeline: Pipeline, emit, *, poll_interval: float = 1) -> None:
    """监听来源、目标清单都跟随有效配置；切换来源先关闭旧会话。

    断开（含还没配置或登录 Telegram）超过 telegram.alert_after_sec（默认 120 秒）告警一次，恢复后发送恢复通知；
    监听意外停止时先落告警再退出，由容器重启拉起，告警经 Outbox 在新进程中送达。
    """
    while True:
        alert_after = int(pipeline.cfg["telegram"]["alert_after_sec"])
        name = pipeline.cfg["providers"]["source"]
        source = pipeline.reg.get_provider("source", name)
        if hasattr(source, "configure"):
            source.configure([item["bot"] for item in pipeline.cfg["sources"]])
        if hasattr(source, "bind_watermark"):
            source.bind_watermark(pipeline.store.source_watermark)
        listener = asyncio.create_task(source.listen(emit))
        fetches: set[asyncio.Task] = set()  # 管理台请求读取的最近消息，见 _serve_fetch
        status, error = "stopped", None
        down_since = time.monotonic()
        backfilled = getattr(source, "backfilled", 0)
        try:
            while pipeline.cfg["providers"]["source"] == name:
                if listener.done():
                    await listener
                    raise RuntimeError("消息源意外停止")
                if hasattr(source, "configure"):
                    source.configure([item["bot"] for item in pipeline.cfg["sources"]])
                pipeline.sources_ready = getattr(source, "backfill_done", True)
                if getattr(source, "backfilled", 0) > backfilled:
                    count, backfilled = source.backfilled - backfilled, source.backfilled
                    pipeline.store.stage_notice(
                        "backfill", f"📥 已补拉停机期间的 {count} 条消息。补拉的消息不逐条推送确认卡片，"
                        "点下方按钮查看需要确认的资源。", pipeline.notify_targets(), buttons=messages.PENDING_BUTTONS)
                health = source.health() if hasattr(source, "health") else {"status": "unobserved"}
                pipeline.store.heartbeat("source", provider=name, config_revision=pipeline.revision, **health)
                if health.get("status") in ("connected", "unobserved"):
                    down_since = None
                    pipeline.store.clear_alert("source", f"消息源 {name} 已恢复连接", pipeline.notify_targets())
                    for request in pipeline.store.claim_source_fetches():
                        fetch = asyncio.create_task(_serve_fetch(pipeline.store, source, request))
                        fetches.add(fetch)
                        fetch.add_done_callback(fetches.discard)
                elif down_since is None:
                    down_since = time.monotonic()
                elif time.monotonic() - down_since >= alert_after:
                    problem = health.get("error") or (f"消息源 {name} 已断开超过 {alert_after // 60 or 1} 分钟，"
                                                      "期间消息将在重连后补拉")
                    pipeline.store.raise_alert("source", problem, pipeline.notify_targets())
                await asyncio.sleep(poll_interval)
        except Exception as exc:
            status, error = "failed", type(exc).__name__
            pipeline.store.raise_alert(
                "source", f"消息源 {name} 意外停止（{error}），进程将退出并由容器重启",
                pipeline.notify_targets())
            raise
        finally:
            for task in (listener, *fetches):
                task.cancel()
            await asyncio.gather(listener, *fetches, return_exceptions=True)
            try:
                if hasattr(source, "close"):
                    await source.close()
            except Exception as exc:
                status, error = "failed", type(exc).__name__
                raise
            finally:
                pipeline.store.heartbeat("source", provider=name, status=status, error=error)


async def _run_live() -> int:
    cfg = load_config()
    reg = discover()
    reg.validate_config(cfg)
    log = logging.getLogger("reelay")
    log.info("已注册：%s", reg.summary())
    for name in reg.unclaimed_sections(cfg):
        log.warning("配置 %s 没有对应的插件，已忽略", name)
    store = Store(str(paths.DATABASE))
    pipeline = Pipeline(reg, store, cfg)
    service = ManagementService(store, pipeline, config_path=paths.CONFIG)
    from .control.bot import ControlBot
    bot = ControlBot(service)
    tasks = []
    try:
        await bot.start()
        console = reg.providers.get("notify", {}).get("console")
        if console and hasattr(console, "configure"):
            console.configure(sender=bot.send if bot.enabled else None)

        async def emit(raw: RawMessage) -> None:
            await pipeline.accept(PipelineContext(raw=raw))

        worker = Worker(pipeline, store, concurrency=cfg["runtime"]["concurrency"])
        pipeline.hold_daily(900)  # 日报等启动补拉的消息解析完再发，最多等 15 分钟

        async def sweep_loop() -> None:
            interval = SWEEP_INTERVAL_SEC
            while True:
                await pipeline.sweep_approval_timeouts()
                store.heartbeat("control_bot", **bot.health())
                # 每秒记录连接观察，审批扫描仍按配置间隔进行。
                for _ in range(interval):
                    await asyncio.sleep(1)
                    store.heartbeat("control_bot", **bot.health())

        tasks = [asyncio.create_task(coro) for coro in (
            _source_loop(pipeline, emit), worker.run(), sweep_loop(),
        )]
        if bot.enabled:
            tasks.append(asyncio.create_task(bot.run_forever()))
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            await task
        raise RuntimeError("常驻服务意外停止")
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        try:
            await bot.close()
        finally:
            store.close()


def main() -> None:
    ap = argparse.ArgumentParser(prog="reelay")
    ap.add_argument("--dry-run", action="store_true", help="无依赖端到端冒烟")
    args = ap.parse_args()
    _setup_log(to_file=not args.dry_run)
    if not args.dry_run:
        ensure_config()
        paths.use_data_dir(paths.DATA)
    async def run_with_signals():
        import signal
        loop = asyncio.get_running_loop()
        task = asyncio.current_task()
        loop.add_signal_handler(signal.SIGTERM, task.cancel)
        try:
            return await (_run_dry() if args.dry_run else _run_live())
        except asyncio.CancelledError:
            return 0
        finally:
            loop.remove_signal_handler(signal.SIGTERM)
    try:
        code = asyncio.run(run_with_signals())
    except KeyboardInterrupt:
        code = 0
    raise SystemExit(code)


if __name__ == "__main__":
    main()
