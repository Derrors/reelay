"""Reelay 入口。

用法::

    python3 -m reelay.main --dry-run     # 无依赖冒烟：模拟源 → 解析 → 决策 → 转存(占位) → 状态
    python3 -m reelay.main               # 正常运行：真实源 + worker 长驻

--dry-run 用内置 DryRunSource 与 115 占位实现，端到端验证内核骨架，不碰网络与凭证。
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os

from .core.config import load_config, load_env
from .core.contracts import PipelineContext, RawMessage, TaskState
from .core.pipeline import Pipeline
from .core.registry import discover
from .core.store import Store
from .core.service import ManagementService
from .core.worker import Worker
from .control.panel import ControlPanel


def _setup_log() -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    log_file = os.getenv("REELAY_LOG_FILE")
    if log_file:
        path = __import__("pathlib").Path(log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(path, encoding="utf-8"))
    logging.basicConfig(
        level=os.getenv("REELAY_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
        handlers=handlers,
    )


async def _run_dry() -> int:
    """无依赖端到端冒烟。"""
    os.environ["REELAY_DRY_RUN"] = "1"
    cfg = load_config()
    # dry-run 用规则解析器（无需 LLM key），并给两个样例源配好规则
    cfg["defaults"]["parser"] = "regex"
    cfg["sources"] = [
        {"bot": "@example_tv_bot", "approval_mode": "manual", "save_to": "剧集",
         "filters": {"must_have": ["115."], "block_keywords": ["招商", "广告", "更多资源关注"]}},
        {"bot": "@example_movie_bot", "approval_mode": "auto", "save_to": "电影",
         "filters": {"must_have": ["115."], "block_keywords": ["招商", "广告"]}},
    ]

    reg = discover()
    log = logging.getLogger("reelay.dry")
    log.info("已注册：%s", reg.summary())

    store = Store(":memory:")
    pipeline = Pipeline(reg, store, cfg)
    panel = ControlPanel(store, pipeline)

    # dry-run 直接推进到审批/队列，便于在一次冒烟中验证完整路径
    source = reg.get_provider("source", "dryrun")

    async def emit(raw: RawMessage) -> None:
        await pipeline.process_new(PipelineContext(raw=raw))

    await source.listen(emit)

    # 模拟人工：把所有 pending_approval 自动确认，演示完整转存链路
    for r in store.list_by_state(TaskState.PENDING_APPROVAL, 100):
        log.info("模拟人工确认：%s（%s）", r["task_id"], r["title"])
        await pipeline.approve(r["task_id"])

    # 消费者：把队列排干
    worker = Worker(pipeline, store, concurrency=2)
    await worker.run(drain=True)

    # 汇报
    print("\n================= DRY-RUN 结果 =================")
    print("状态计数：", panel.status()["counts"])
    print("\n各任务轨迹：")
    for st in (TaskState.DONE, TaskState.REJECTED, TaskState.SKIPPED,
               TaskState.NEED_MANUAL, TaskState.FAILED):
        for r in store.list_by_state(st, 100):
            ctx = store.get_task(r["task_id"])
            print(f"\n[{st.value}] {r['title'] or r['task_id']}  (来源 {ctx.raw.source})")
            for t in ctx.trail:
                print(f"    · {t}")

    counts = panel.status()["counts"]
    done = counts.get("done", 0)
    rejected = counts.get("rejected", 0)
    ok = done >= 2 and rejected >= 1     # 2 条资源应转存成功、1 条广告应被拒
    print("\n冒烟判定：", "✅ PASS" if ok else "❌ FAIL",
          f"(done={done}, rejected={rejected})")
    print("================================================\n")
    store.close()
    return 0 if ok else 1


async def _source_loop(pipeline: Pipeline, emit, *, poll_interval: float = 1) -> None:
    """监听来源、目标清单都跟随有效配置；切换来源先关闭旧会话。

    断开超过 REELAY_SOURCE_ALERT_SEC（默认 120 秒）告警一次，恢复后发送恢复通知；
    监听意外停止时先落告警再退出，由容器重启拉起，告警经 Outbox 在新进程中送达。
    """
    import time
    alert_after = max(1, int(os.getenv("REELAY_SOURCE_ALERT_SEC", "120")))
    while True:
        name = pipeline.cfg["providers"]["source"]
        source = pipeline.reg.get_provider("source", name)
        if hasattr(source, "configure"):
            source.configure([item["bot"] for item in pipeline.cfg["sources"]])
        if hasattr(source, "bind_watermark"):
            source.bind_watermark(pipeline.store.source_watermark)
        listener = asyncio.create_task(source.listen(emit))
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
                if getattr(source, "backfilled", 0) > backfilled:
                    count, backfilled = source.backfilled - backfilled, source.backfilled
                    pipeline.store.stage_notice(
                        "backfill", f"📥 已补拉停机期间的 {count} 条消息。补拉的消息不逐条推送确认卡片，"
                        "需要确认的资源请在管理台「待审批」中处理。", pipeline.notify_targets())
                health = source.health() if hasattr(source, "health") else {"status": "unobserved"}
                pipeline.store.heartbeat("source", provider=name, config_revision=pipeline.revision, **health)
                if health.get("status") in ("connected", "unobserved"):
                    down_since = None
                    pipeline.store.clear_alert("source", f"消息源 {name} 已恢复连接", pipeline.notify_targets())
                elif down_since is None:
                    down_since = time.monotonic()
                elif time.monotonic() - down_since >= alert_after:
                    pipeline.store.raise_alert(
                        "source", f"消息源 {name} 已断开超过 {alert_after // 60 or 1} 分钟，期间消息将在重连后补拉",
                        pipeline.notify_targets())
                await asyncio.sleep(poll_interval)
        except Exception as exc:
            status, error = "failed", type(exc).__name__
            pipeline.store.raise_alert(
                "source", f"消息源 {name} 意外停止（{error}），进程将退出并由容器重启",
                pipeline.notify_targets())
            raise
        finally:
            listener.cancel()
            await asyncio.gather(listener, return_exceptions=True)
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
    store = Store(os.getenv("REELAY_DB_PATH", "data/reelay.db"))
    pipeline = Pipeline(reg, store, cfg)
    service = ManagementService(store, pipeline, config_path=os.getenv("REELAY_CONFIG_PATH", "config.yaml"))
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

        worker = Worker(pipeline, store, concurrency=int(os.getenv("REELAY_WORKER_CONCURRENCY", "3")))

        async def sweep_loop() -> None:
            interval = max(1, int(os.getenv("REELAY_SWEEP_INTERVAL_SEC", "3600")))
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
    if not args.dry_run:
        load_env()  # 须早于 _setup_log，否则 .env 中的 REELAY_LOG_FILE / REELAY_LOG_LEVEL 不生效
    _setup_log()
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
