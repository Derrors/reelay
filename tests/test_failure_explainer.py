"""失败解释插件。"""
from __future__ import annotations

import unittest
import json

from reelay.core.contracts import TaskState

from tests.fakes import FakeAI, ServiceCase, tv_ctx


class TestFailureExplainer(ServiceCase):
    def setUp(self):
        super().setUp()
        self.explainer = next(x for x in self.registry.listeners["on_failed"] if x.name == "failure_explainer")
        self.explainer._client = None

    async def run_on(self, ctx):
        with self.pl.scope(self.pl.cfg):
            await self.explainer.handle(ctx)
        return self.store.state_get("failure_explainer", f"task:{ctx.task_id}")

    def notices(self):
        return [json.loads(r[0])["text"] for r in self.store._conn.execute(
            "SELECT snapshot FROM outbox WHERE kind='notice' AND snapshot LIKE '%failure_explainer%'")]

    async def test_unknown_transfer_result_points_to_reconcile_and_notifies_once(self):
        ctx = tv_ctx()
        ctx.state = TaskState.NEED_MANUAL
        ctx.ns("storage").update(phase="receiving", manifest={"files": [{"name": "a.mkv"}]})
        self.store.save_task(ctx, error="转存回执未确认全部文件落地")
        explanation = await self.run_on(ctx)
        self.assertEqual((explanation["action"], explanation["label"], explanation["source"]),
                         ("reconcile", "核验转存", "rule"))
        await self.run_on(ctx)
        self.assertEqual(len(self.notices()), 1)
        self.assertIn("建议：核验转存", self.notices()[0])
        self.assertEqual(self.service.get_task(ctx.task_id)["explanation"]["action"], "reconcile")
        self.assertEqual(self.service.inbox("need_manual")["items"][0]["explanation"]["action"], "reconcile")

    async def test_library_conflict_points_to_delete_or_overwrite(self):
        ctx = tv_ctx()
        ctx.state = TaskState.NEED_MANUAL
        ctx.ns("storage").update(phase="stored", landed=[{"id": "9", "name": "a.mkv"}])
        ctx.ns("organize").update(error="库里已有「剧 - S01E01.mkv」，请选择删除转存文件或覆盖保存", pending_conflict={
            "target_dir": "剧 (2026)/Season 01", "target_name": "剧 - S01E01.mkv",
            "existing": {"id": "1", "name": "剧 - S01E01.mkv", "size": 9},
            "incoming": {"id": "9", "name": "a.mkv", "size": 10}})
        self.store.save_task(ctx)
        explanation = await self.run_on(ctx)
        self.assertEqual((explanation["action"], explanation["label"]), ("resolve_conflict", "删除转存文件或覆盖保存"))
        self.assertIn("库里已有「剧 - S01E01.mkv」", explanation["text"])
        item = self.service.inbox("need_manual")["items"][0]
        self.assertEqual(item["organize_conflict"]["existing"]["name"], "剧 - S01E01.mkv")

    async def test_failures_that_will_retry_stay_quiet(self):
        ctx = tv_ctx()
        ctx.state = TaskState.QUEUED  # Worker 已安排自动重试
        self.store.save_task(ctx)
        self.assertIsNone(await self.run_on(ctx))
        ctx.state = TaskState.FAILED
        self.store.save_task(ctx, error="转存准备失败：ConnectionError")
        self.assertEqual((await self.run_on(ctx))["action"], "retry")
        self.assertEqual(self.notices(), [])
        for _ in range(4):
            self.store.bump_retry(ctx.task_id)
        self.assertIn("自动重试 4 次后仍失败", (await self.run_on(ctx))["text"])
        self.assertEqual(len(self.notices()), 1)

    async def test_every_failure_reaches_the_explainer_through_the_outbox(self):
        ctx = tv_ctx()
        ctx.state = TaskState.QUEUED
        self.store.save_task(ctx)
        await self.pl._fail(ctx, "转存准备失败：ConnectionError", retry_from=TaskState.QUEUED)
        await self.pl.dispatch_deliveries()
        self.assertEqual(self.store.state_get("failure_explainer", f"task:{ctx.task_id}")["action"], "retry")
        self.assertEqual(self.notices(), [])  # 还会自动重试，不打扰
        # 同一轮里再次失败（如自动重试后）：事件仍要送到，失败解释据此更新并通知
        again = self.store.get_task(ctx.task_id)
        await self.pl._fail(again, "转存前校验未通过，需人工处理：无法从文件名识别集号：a.mp4",
                            retry_from=TaskState.QUEUED, auto_retry=False)
        await self.pl.dispatch_deliveries()
        explanation = self.store.state_get("failure_explainer", f"task:{ctx.task_id}")
        self.assertEqual((explanation["action"], explanation["state"]), ("fix_and_retry", "failed"))
        self.assertEqual(len(self.notices()), 1)
        self.assertEqual(self.service.get_task(ctx.task_id)["explanation"]["action"], "fix_and_retry")
        # 解释与当前状态不符时不显示（如任务已被重新排队）
        self.store.set_state(ctx.task_id, TaskState.QUEUED, expected=TaskState.FAILED)
        self.store.set_state(ctx.task_id, TaskState.NEED_MANUAL, expected=TaskState.QUEUED)
        self.assertIsNone(self.service.get_task(ctx.task_id)["explanation"])

    async def test_expired_shares_are_explained_as_gone(self):
        ctx = tv_ctx()
        ctx.state = TaskState.FAILED
        ctx.ns("task")["no_auto_retry"] = True
        self.store.save_task(ctx, error="分享不可用，无法转存：链接已过期")
        explanation = await self.run_on(ctx)
        self.assertEqual((explanation["action"], explanation["text"]), ("share_gone", "分享不可用，无法转存：链接已过期"))
        self.assertIn("建议：忽略这条任务", self.notices()[0])

    async def test_shares_that_need_picking_files_are_not_sent_to_fix_the_parse(self):
        ctx = tv_ctx()
        ctx.state = TaskState.FAILED
        ctx.ns("task")["no_auto_retry"] = True
        self.store.save_task(ctx, error="转存前校验未通过，需人工处理：电影分享包含 2 个正片候选，需人工选择：a.iso、b.iso")
        explanation = await self.run_on(ctx)
        self.assertEqual((explanation["action"], explanation["label"]), ("manual_transfer", "到网盘挑选文件手动转存"))

    async def test_ai_phrases_the_reason_but_not_the_action(self):
        ctx = tv_ctx()
        ctx.state = TaskState.FAILED
        ctx.ns("task")["no_auto_retry"] = True
        self.store.save_task(ctx, error="转存前校验未通过，需人工处理：无法从文件名识别集号：a.mp4")
        self.explainer._client = FakeAI({"text": "分享里的文件名看不出是第几集"})
        explanation = await self.run_on(ctx)
        self.assertEqual((explanation["action"], explanation["text"], explanation["source"]),
                         ("fix_and_retry", "分享里的文件名看不出是第几集", "ai"))
        payload = json.loads(self.explainer._client.calls[0]["messages"][1]["content"])
        self.assertEqual(payload["action"], "修正识别结果后重试")
        self.explainer._client = FakeAI("not json")
        ctx.ns("task")["generation"] = 1
        self.store.save_task(ctx, error="无法从文件名识别集号：a.mp4")
        self.assertEqual((await self.run_on(ctx))["source"], "rule")


if __name__ == "__main__":
    unittest.main()
