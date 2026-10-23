"""管理服务/API 的业务级测试，不启动网络服务。"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from reelay.core.config import load_config
from reelay.core.contracts import ParsedResource, PipelineContext, RawMessage, TaskState
from reelay.core.pipeline import Pipeline
from reelay.core.registry import discover
from reelay.core.service import ConflictError, ManagementService
from reelay.core.store import Store


def run(coro):
    return asyncio.run(coro)


class TestManagementService(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = Store(":memory:")
        self.pipeline = Pipeline(discover(), self.store, load_config("missing.yaml"))
        self.pipeline.save_dirs = {**self.pipeline.save_dirs, "电影4K": "42"}
        self.config_path = Path(self.tempdir.name) / "config.yaml"
        self.service = ManagementService(
            self.store, self.pipeline, config_path=str(self.config_path)
        )

    def tearDown(self):
        self.store.close()
        self.tempdir.cleanup()

    def _task(
        self,
        state: TaskState,
        *,
        retry_from: TaskState | None = None,
        landed: list[dict] | None = None,
    ):
        ctx = PipelineContext(
            raw=RawMessage(source="@bot", msg_id=f"msg-{state.value}", text="source")
        )
        ctx.parsed = ParsedResource(
            is_media=True, media_type="tv", title="测试剧", year=2026,
            season=1, episodes=[1], share_code="share",
        )
        ctx.state = state
        if retry_from:
            ctx.ns("task")["retry_from"] = retry_from.value
        if landed is not None:
            ctx.ns("storage")["landed"] = landed
            ctx.result = {"verified": False, "landed": landed}
        self.store.save_task(ctx, error="boom" if state == TaskState.FAILED else None)
        return ctx

    def test_retry_restores_recorded_stage(self):
        ctx = self._task(TaskState.FAILED, retry_from=TaskState.RECEIVED)
        run(self.service.retry(ctx.task_id))
        self.assertEqual(self.store.get_task(ctx.task_id).state, TaskState.RECEIVED)

    def test_approve_reject_conflict(self):
        ctx = self._task(TaskState.PENDING_APPROVAL)
        run(self.service.approve(ctx.task_id))
        self.assertEqual(self.store.get_task(ctx.task_id).state, TaskState.QUEUED)
        with self.assertRaises(ConflictError):
            run(self.service.reject(ctx.task_id))

    def test_approve_can_change_target_directory(self):
        ctx = self._task(TaskState.PENDING_APPROVAL)
        run(self.service.approve(ctx.task_id, save_dir_alias="电影4K"))
        restored = self.store.get_task(ctx.task_id)
        self.assertEqual(restored.state, TaskState.QUEUED)
        self.assertEqual(restored.save_dir_alias, "电影4K")

    def test_unknown_directory_does_not_silently_use_default(self):
        ctx = self._task(TaskState.PENDING_APPROVAL)
        with self.assertRaises(ValueError):
            run(self.service.approve(ctx.task_id, save_dir_alias="不存在的目录"))
        self.assertEqual(self.store.get_task(ctx.task_id).state, TaskState.PENDING_APPROVAL)

    def test_active_tasks_cannot_be_overwritten_by_management(self):
        for state in (TaskState.RECEIVED, TaskState.PARSED, TaskState.QUEUED,
                      TaskState.SAVING, TaskState.STORED, TaskState.ORGANIZING):
            ctx = self._task(state)
            with self.subTest(state=state):
                with self.assertRaises(ConflictError):
                    self.service.patch_parsed(ctx.task_id, {"title": "编辑"})
                with self.assertRaises(ConflictError):
                    run(self.service.reparse(ctx.task_id))

    def test_dashboard_counts_transfer_and_organizing_as_active(self):
        self._task(TaskState.STORED)
        self._task(TaskState.ORGANIZING)
        self.assertEqual(self.service.dashboard()["active"], 2)

    def test_manual_retry_resets_exhausted_retry_budget(self):
        ctx = self._task(TaskState.FAILED, retry_from=TaskState.RECEIVED)
        for _ in range(4):
            self.store.bump_retry(ctx.task_id)
        run(self.service.retry(ctx.task_id))
        self.assertEqual(self.store.get_task_record(ctx.task_id)["retries"], 0)

    def test_patch_parsed_and_task_detail(self):
        ctx = self._task(TaskState.NEED_MANUAL)
        detail = self.service.patch_parsed(ctx.task_id, {"title": "修正剧名", "episodes": [1, 2]})
        self.assertEqual(detail["context"]["parsed"]["title"], "修正剧名")
        self.assertEqual(detail["context"]["parsed"]["episodes"], [1, 2])

    def test_reorganize_need_manual_only(self):
        ctx = self._task(
            TaskState.NEED_MANUAL,
            landed=[{"id": "one", "name": "S01E01.mp4"}],
        )
        run(self.service.requeue_organize(ctx.task_id, save_dir_alias="剧集"))
        restored = self.store.get_task(ctx.task_id)
        self.assertEqual(restored.state, TaskState.QUEUED)
        self.assertEqual(restored.save_dir_alias, "剧集")
        self.assertEqual(restored.ns("task")["action"], "organize_only")

    def test_config_text_roundtrip(self):
        text = '{"save_dirs":{"默认":"123"},"defaults":{"approval_mode":"manual"},"sources":[]}'
        config = run(self.service.save_config_text(text))
        self.assertEqual(config["save_dirs"]["默认"], "123")
        self.assertIn("save_dirs", self.service.get_config_text())

    def test_organize_preview_respects_filename_episode(self):
        # 「工具 → 整理预览」调用媒体库整理插件的动作 preview_plan
        from reelay.core import runtime
        preview = self.service.pipeline.reg.action("organizer", "preview_plan")
        with runtime.scope(self.service.pipeline.cfg):
            items = preview({
                "parsed": {"is_media": True, "media_type": "tv", "title": "测试剧", "year": 2026,
                           "season": 1, "episodes": [1, 2]},
                "landed": [{"id": "two", "name": "S01E02.mkv"}, {"id": "one", "name": "S01E01.mp4"}],
            })["items"]
        mapping = {item["src_name"]: item["target_name"] for item in items}
        self.assertEqual(mapping["S01E02.mkv"], "测试剧 - S01E02 - 第 2 集.mkv")
        self.assertEqual(mapping["S01E01.mp4"], "测试剧 - S01E01 - 第 1 集.mp4")


if __name__ == "__main__":
    unittest.main()
