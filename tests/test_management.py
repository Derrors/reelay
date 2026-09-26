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
        items = self.service.organize_preview(
            parsed={
                "is_media": True,
                "media_type": "tv",
                "title": "测试剧",
                "year": 2026,
                "season": 1,
                "episodes": [1, 2],
            },
            landed=[
                {"id": "two", "name": "S01E02.mkv"},
                {"id": "one", "name": "S01E01.mp4"},
            ],
        )
        mapping = {item["src_name"]: item["target_name"] for item in items}
        self.assertTrue(mapping["S01E02.mkv"].endswith("S01E02.mkv"))
        self.assertTrue(mapping["S01E01.mp4"].endswith("S01E01.mp4"))


if __name__ == "__main__":
    unittest.main()
