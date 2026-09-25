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
from .core.worker import Worker
from .control.panel import ControlPanel


def _setup_log() -> None:
    logging.basicConfig(
        level=os.getenv("REELAY_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )


async def _run_dry() -> int:
    """无依赖端到端冒烟。"""
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

    # 生产者：模拟源 → process_new
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


async def _run_live() -> int:
    load_env()
    cfg = load_config()
    reg = discover()
    logging.getLogger("reelay").info("已注册：%s", reg.summary())

    store = Store(os.getenv("REELAY_DB_PATH", "data/reelay.db"))
    pipeline = Pipeline(reg, store, cfg)

    targets = [s["bot"] for s in cfg.get("sources", []) if s.get("bot")]
    source = reg.get_provider("source", "telegram")
    if hasattr(source, "configure"):
        source.configure(targets)

    async def emit(raw: RawMessage) -> None:
        await pipeline.process_new(PipelineContext(raw=raw))

    worker = Worker(pipeline, store, concurrency=int(os.getenv("REELAY_WORKER_CONCURRENCY", "3")))

    # 监听（生产者）与 worker（消费者）并发长驻
    await asyncio.gather(
        source.listen(emit),
        worker.run(drain=False),
    )
    return 0


def main() -> None:
    _setup_log()
    ap = argparse.ArgumentParser(prog="reelay")
    ap.add_argument("--dry-run", action="store_true", help="无依赖端到端冒烟")
    args = ap.parse_args()
    code = asyncio.run(_run_dry() if args.dry_run else _run_live())
    raise SystemExit(code)


if __name__ == "__main__":
    main()
