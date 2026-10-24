"""本地评测：样本导出、逐字段打分、报告对比。"""
from __future__ import annotations

import unittest
import json
from unittest.mock import patch

from tests.fakes import FakeAI, ServiceCase, media


class TestLocalEval(ServiceCase):
    async def test_export_prefers_corrections_and_run_scores_each_field(self):
        from reelay import eval as evaluation
        first, second = await self.pending("1"), await self.pending("2")
        self.service.patch_parsed(first.task_id, {"title": "规范剧名"})
        await self.service.approve(first.task_id)
        await self.service.approve(second.task_id)
        await self.service.reject((await self.pending("3")).task_id)
        samples = evaluation.export_samples(self.store)
        self.assertEqual([(s["id"], s["kind"]) for s in samples], [(first.task_id, "correct"), (second.task_id, "approve")])
        self.assertEqual(samples[0]["expected"]["title"], "规范剧名")
        self.assertNotIn("115.com", json.dumps(samples, ensure_ascii=False))
        out = self.root / "eval" / "samples.jsonl"
        with patch("builtins.print"):  # 命令行导出：不读配置与密钥
            evaluation.main(["export", "--db", str(self.root / "t.db"), "--out", str(out)])
        self.assertEqual([json.loads(line)["id"] for line in out.read_text(encoding="utf-8").splitlines()],
                         [s["id"] for s in samples])

        client = FakeAI(media(title="剧"), media(title="剧", episodes=[3]))
        report = await evaluation.run(samples, client=client, model="m", examples=3)
        self.assertEqual(report["fields"]["title"], {"total": 2, "correct": 1, "accuracy": 0.5})
        self.assertEqual(report["mismatches"][0]["field"], "title")
        # 同来源其他样本的修正作为示例，自己的修正不会作为自己的示例
        sizes = {json.loads(call["messages"][-1]["content"])["text"]: len(call["messages"]) for call in client.calls}
        self.assertEqual((sizes[samples[0]["text"]], sizes[samples[1]["text"]]), (2, 4))
        better = {**report, "model": "n", "fields": {**report["fields"], "title": {"total": 2, "correct": 2, "accuracy": 1.0}},
                  "mismatches": []}
        lines = evaluation.compare(report, better)
        self.assertIn("50.0% → 100.0% (+50.0%)", "\n".join(lines))
        self.assertIn("修好 1 处", lines[-1])


if __name__ == "__main__":
    unittest.main()
