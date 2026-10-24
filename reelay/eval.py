"""本地评测：用人工裁决样本检验当前提示词与模型，改提示词或换模型前先跑一遍对比。

样本来自数据库里的人工裁决（``feedback`` 表）：修正（correct）给出标准答案；批准（approve）表示
当时的识别结果被接受，作为较弱的标准答案；同一任务以最近的修正为准。忽略（reject）不代表识别错误，不参与。

样本含真实频道的消息正文（已去掉链接与访问码），**只能留在本地 data/ 目录**（已在 .gitignore 中），
不要提交到仓库或贴到公开 issue。

用法::

    python -m reelay.eval export                   # 导出 data/eval/samples.jsonl
    python -m reelay.eval run --limit 50           # 用当前提示词与模型跑一遍，报告写入 data/eval/
    python -m reelay.eval run --model gpt-4.1-mini # 换模型对比
    python -m reelay.eval compare data/eval/report-A.json data/eval/report-B.json
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import time
from pathlib import Path
from typing import Any

from .core import ai, paths, runtime, secret_store, tmdb
from .core.config import ensure_config, load_config
from .core.contracts import PipelineContext, RawMessage
from .core.store import Store

EVAL_DIR = paths.EVAL
FIELDS = ("is_media", "media_type", "title", "year", "season", "episodes")


def export_samples(store: Store) -> list[dict]:
    latest: dict[str, dict] = {}
    for row in store.feedback_rows(kinds=("approve", "correct")):
        if not row["text"]:
            continue
        if row["kind"] == "correct":
            expected = row["label"].get("result")
        else:
            if row["task_id"] in latest and latest[row["task_id"]]["kind"] == "correct":
                continue  # 修正后的批准沿用修正结果
            expected = {key: value for key, value in (row["parsed"] or {}).items() if key in FIELDS}
        if not isinstance(expected, dict):
            continue
        latest[row["task_id"]] = {"id": row["task_id"], "source": row["source"], "kind": row["kind"],
                                  "text": row["text"], "expected": expected, "created_at": row["created_at"]}
    return sorted(latest.values(), key=lambda item: item["created_at"])


def _same(field: str, expected: Any, actual: Any) -> bool:
    if field == "title":
        return tmdb.normalize(expected) == tmdb.normalize(actual)
    if field == "episodes":
        return sorted(expected or []) == sorted(actual or [])
    return expected == actual


def score(samples: list[dict], outputs: dict[str, Any]) -> dict:
    fields = {field: {"total": 0, "correct": 0} for field in FIELDS}
    mismatches, failures = [], {}
    for sample in samples:
        got = outputs.get(sample["id"])
        if isinstance(got, str):
            failures[got] = failures.get(got, 0) + 1
            continue
        for field in FIELDS:
            if field not in sample["expected"]:
                continue
            fields[field]["total"] += 1
            if _same(field, sample["expected"][field], got.get(field)):
                fields[field]["correct"] += 1
            else:
                mismatches.append({"id": sample["id"], "field": field, "expected": sample["expected"][field],
                                   "got": got.get(field)})
    for item in fields.values():
        item["accuracy"] = round(item["correct"] / item["total"], 4) if item["total"] else None
    return {"fields": fields, "mismatches": mismatches, "failures": failures}


async def run(samples: list[dict], *, client, model: str, examples: int = 3, concurrency: int = 4) -> dict:
    """逐条调用解析器（不经过预过滤与缓存）；示例取同一来源其他样本中的修正，与线上一致。"""
    from .providers.parser import LLMParser, rule_hint
    parser = LLMParser()
    parser._client, parser._model = client, model
    semaphore = asyncio.Semaphore(concurrency)
    outputs: dict[str, Any] = {}
    started = time.monotonic()

    async def one(sample: dict) -> None:
        shots = [{"id": other["id"], "text": other["text"], "expected": other["expected"]}
                 for other in reversed(samples)
                 if other["kind"] == "correct" and other["source"] == sample["source"] and other["id"] != sample["id"]
                 ][:examples]
        ctx = PipelineContext(RawMessage(sample["source"] or "eval", sample["id"], sample["text"], []))
        async with semaphore:
            try:
                outputs[sample["id"]] = await parser._complete(ctx, list(reversed(shots)), rule_hint(ctx))
            except ai.AIError as exc:
                outputs[sample["id"]] = exc.category

    await asyncio.gather(*(one(sample) for sample in samples))
    from .providers.parser import _SYSTEM_PROMPT
    return {"model": model, "prompt": hashlib.sha256(_SYSTEM_PROMPT.encode()).hexdigest()[:12],
            "samples": len(samples), "examples": examples, "seconds": round(time.monotonic() - started, 1),
            "created_at": time.time(), **score(samples, outputs)}


def print_report(report: dict) -> None:
    print(f"模型 {report['model']} · 提示词 {report['prompt']} · 样本 {report['samples']} 条 · "
          f"用时 {report['seconds']} 秒")
    for field, item in report["fields"].items():
        if item["total"]:
            print(f"  {field:<10} {item['correct']:>4}/{item['total']:<4} {item['accuracy']:.1%}")
    if report["failures"]:
        print("  调用失败：" + "、".join(f"{k} ×{v}" for k, v in report["failures"].items()))
    for item in report["mismatches"][:20]:
        print(f"  ✗ {item['id']} {item['field']}：期望 {item['expected']!r}，得到 {item['got']!r}")
    if len(report["mismatches"]) > 20:
        print(f"  … 另有 {len(report['mismatches']) - 20} 处不一致，见报告文件")


def compare(old: dict, new: dict) -> list[str]:
    lines = [f"{old['model']}/{old['prompt']} → {new['model']}/{new['prompt']}"]
    for field in FIELDS:
        a, b = old["fields"].get(field, {}), new["fields"].get(field, {})
        if a.get("accuracy") is not None and b.get("accuracy") is not None:
            lines.append(f"  {field:<10} {a['accuracy']:.1%} → {b['accuracy']:.1%} ({b['accuracy'] - a['accuracy']:+.1%})")
    before = {(m["id"], m["field"]) for m in old["mismatches"]}
    after = {(m["id"], m["field"]) for m in new["mismatches"]}
    lines.append(f"  新增错误 {len(after - before)} 处，修好 {len(before - after)} 处")
    return lines


def _load_samples(args) -> list[dict]:
    if getattr(args, "dataset", None):  # 只有 run 有 --dataset
        return [json.loads(line) for line in Path(args.dataset).read_text(encoding="utf-8").splitlines() if line.strip()]
    store = Store(args.db)
    try:
        return export_samples(store)
    finally:
        store.close()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="reelay.eval", description="用人工裁决样本评测识别提示词与模型（只在本地运行）")
    sub = parser.add_subparsers(dest="command", required=True)
    db = str(paths.DATABASE)
    exp = sub.add_parser("export", help="从数据库导出样本")
    exp.add_argument("--db", default=db)
    exp.add_argument("--out", default=str(EVAL_DIR / "samples.jsonl"))
    rn = sub.add_parser("run", help="用当前提示词与模型评测")
    rn.add_argument("--db", default=db)
    rn.add_argument("--dataset", help="使用导出的 jsonl 样本，而不是直接读数据库")
    rn.add_argument("--limit", type=int, default=0, help="只评测最近的 N 条")
    rn.add_argument("--model", help="覆盖系统配置里的模型（llm.model）")
    rn.add_argument("--examples", type=int, default=3, help="每条附带的同来源修正示例数（0 关闭）")
    cmp = sub.add_parser("compare", help="对比两份报告")
    cmp.add_argument("old")
    cmp.add_argument("new")
    args = parser.parse_args(argv)

    if args.command == "compare":
        old, new = (json.loads(Path(p).read_text(encoding="utf-8")) for p in (args.old, args.new))
        print("\n".join(compare(old, new)))
        return
    samples = _load_samples(args)
    if args.command == "export":
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("".join(json.dumps(s, ensure_ascii=False) + "\n" for s in samples), encoding="utf-8")
        print(f"已导出 {len(samples)} 条样本到 {out}（含真实消息正文，只能留在本地）")
        return
    if args.limit:
        samples = samples[-args.limit:]
    if not samples:
        raise SystemExit("没有可用样本：在管理台批准或修正一些识别结果后再评测")
    ensure_config()
    secret_store.use(paths.SECRETS)
    runtime.set_process_config(load_config())  # AI 接口地址与模型取系统配置
    client, model = ai.make_client()
    if client is None:
        raise SystemExit("未配置 AI 接口密钥（管理台「设置 → AI 与 TMDB」中填写），无法评测")
    report = asyncio.run(run(samples, client=client, model=args.model or model, examples=args.examples))
    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    path = EVAL_DIR / f"report-{time.strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print_report(report)
    print(f"报告：{path}")


if __name__ == "__main__":
    main()
