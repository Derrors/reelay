"""内核关键逻辑单元测试（标准库 unittest，无三方依赖）。

覆盖评审重点：三层去重、追更增量、决策过滤、状态机落库与重启恢复。
运行：python3 -m unittest -v
"""

import asyncio
import unittest

from reelay.core.config import load_config
from reelay.core.contracts import (
    ApprovalMode, Decision, ParsedResource, PipelineContext, RawMessage, TaskState,
)
from reelay.core.pipeline import Pipeline
from reelay.core.registry import discover
from reelay.core.store import Store, DEDUP_MESSAGE, DEDUP_SHARE, DEDUP_EPISODE


def run(coro):
    return asyncio.run(coro)


class TestStore(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")

    def tearDown(self):
        self.store.close()

    def test_three_layer_dedup(self):
        s = self.store
        # 消息级
        self.assertFalse(s.seen(DEDUP_MESSAGE, "m1"))
        s.mark(DEDUP_MESSAGE, "m1")
        self.assertTrue(s.seen(DEDUP_MESSAGE, "m1"))
        # 资源级
        s.mark(DEDUP_SHARE, "shareX")
        self.assertTrue(s.seen(DEDUP_SHARE, "shareX"))
        # 空 key 不记
        s.mark(DEDUP_MESSAGE, "")
        self.assertFalse(s.seen(DEDUP_MESSAGE, ""))

    def test_missing_episodes_incremental(self):
        """追更：已入库 1-8 集，来了 1-10 集，应只返回 9、10。"""
        s = self.store
        p_old = ParsedResource(title="剧A", season=1, episodes=list(range(1, 9)))
        for ep in p_old.episodes:
            s.mark(DEDUP_EPISODE, Store.episode_key("剧A", 1, ep))
        p_new = ParsedResource(title="剧A", season=1, episodes=list(range(1, 11)))
        self.assertEqual(s.missing_episodes(p_new), [9, 10])

    def test_state_update_keeps_snapshot_consistent(self):
        s = self.store
        ctx = PipelineContext(raw=RawMessage(source="@b", msg_id="retry", text="x"))
        ctx.state = TaskState.FAILED
        s.save_task(ctx, error="boom")
        self.assertTrue(s.set_state(ctx.task_id, TaskState.QUEUED))
        self.assertEqual(s.get_task(ctx.task_id).state, TaskState.QUEUED)

    def test_recover_saving_as_queued(self):
        s = self.store
        ctx = PipelineContext(raw=RawMessage(source="@b", msg_id="resume", text="x"))
        ctx.state = TaskState.SAVING
        s.save_task(ctx)
        recovered = s.recover_interrupted()
        self.assertEqual(recovered[0].state, TaskState.QUEUED)
        self.assertEqual(s.get_task(ctx.task_id).state, TaskState.QUEUED)

    def test_task_snapshot_roundtrip_and_recover(self):
        s = self.store
        ctx = PipelineContext(raw=RawMessage(source="@b", msg_id="1", text="hi"))
        ctx.parsed = ParsedResource(is_media=True, title="片", share_code="sc")
        ctx.state = TaskState.QUEUED
        s.save_task(ctx)
        # 还原
        got = s.get_task(ctx.task_id)
        self.assertEqual(got.parsed.title, "片")
        self.assertEqual(got.state, TaskState.QUEUED)
        # 重启恢复：QUEUED 应被捡回
        recovered = s.recover_interrupted()
        self.assertEqual(len(recovered), 1)


class TestDecision(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reg = discover()
        cls.cfg = load_config()

    def _ctx(self, *, is_media=True, is_ad=False, conf=0.9, share="sc",
             text="115.com/s/x", mode="auto", filters=None):
        ctx = PipelineContext(raw=RawMessage(source="@b", msg_id="1", text=text,
                                             urls=["https://115.com/s/x"]))
        ctx.parsed = ParsedResource(is_media=is_media, is_ad=is_ad, confidence=conf,
                                    share_code=share, title="T")
        ctx.source_rule = {"approval_mode": mode, "filters": filters or {}}
        return ctx

    def test_ad_skip(self):
        d = self.reg.get_provider("decision", "default")
        self.assertEqual(run(d.decide(self._ctx(is_ad=True))), Decision.SKIP)

    def test_no_link_skip(self):
        d = self.reg.get_provider("decision", "default")
        self.assertEqual(run(d.decide(self._ctx(share=None))), Decision.SKIP)

    def test_low_confidence_ask(self):
        d = self.reg.get_provider("decision", "default")
        self.assertEqual(run(d.decide(self._ctx(conf=0.2, mode="auto"))), Decision.ASK)

    def test_manual_ask_auto_save(self):
        d = self.reg.get_provider("decision", "default")
        self.assertEqual(run(d.decide(self._ctx(mode="manual"))), Decision.ASK)
        self.assertEqual(run(d.decide(self._ctx(mode="auto"))), Decision.SAVE)

    def test_block_keyword_skip(self):
        d = self.reg.get_provider("decision", "default")
        c = self._ctx(text="115.com/s/x 招商", filters={"block_keywords": ["招商"]})
        self.assertEqual(run(d.decide(c)), Decision.SKIP)


class TestApprovalMode(unittest.TestCase):
    def test_parse_fallback_manual(self):
        self.assertEqual(ApprovalMode.parse("auto"), ApprovalMode.AUTO)
        self.assertEqual(ApprovalMode.parse("garbage"), ApprovalMode.MANUAL)
        self.assertEqual(ApprovalMode.parse(None), ApprovalMode.MANUAL)


class TestParser115Url(unittest.TestCase):
    def test_extract_share_and_code(self):
        from reelay.providers.parser import parse_115_url
        url, code, pwd = parse_115_url(
            "链接 https://115.com/s/swabc123?password=x1y2 访问码：x1y2",
            ["https://115.com/s/swabc123?password=x1y2"],
        )
        self.assertEqual(code, "swabc123")
        self.assertEqual(pwd, "x1y2")

    def test_no_115_returns_none(self):
        from reelay.providers.parser import parse_115_url
        url, code, pwd = parse_115_url("普通文本 http://example.com", ["http://example.com"])
        self.assertIsNone(code)


class TestDirRouter(unittest.TestCase):
    def setUp(self):
        from reelay.core import runtime
        runtime.set_config({
            "routing": [
                {"when": "media_type == movie and resolution == 4K", "dir": "电影"},
                {"when": "media_type == tv", "dir": "剧集"},
                {"when": "genre contains 动画", "dir": "动漫"},
                {"default": "默认"},
            ]
        })
        from reelay.plugins.dir_router import DirRouter
        self.router = DirRouter()

    def _ctx(self, **kw):
        ctx = PipelineContext(raw=RawMessage(source="@b", msg_id="1", text="x"))
        ctx.parsed = ParsedResource(is_media=True, title="T", **kw)
        return ctx

    def test_movie_4k(self):
        c = self._ctx(media_type="movie", resolution="4K")
        run(self.router.run(c))
        self.assertEqual(c.save_dir_alias, "电影")

    def test_tv(self):
        c = self._ctx(media_type="tv")
        run(self.router.run(c))
        self.assertEqual(c.save_dir_alias, "剧集")

    def test_genre_contains(self):
        c = self._ctx(media_type="movie", resolution="1080p")
        c.parsed.raw_fields["genre"] = "动画/奇幻"
        run(self.router.run(c))
        self.assertEqual(c.save_dir_alias, "动漫")

    def test_default_fallback(self):
        c = self._ctx(media_type="movie", resolution="1080p")
        run(self.router.run(c))
        self.assertEqual(c.save_dir_alias, "默认")


class TestOrganizerPlan(unittest.TestCase):
    def setUp(self):
        from reelay.core import runtime
        runtime.set_config({"organize": {"naming": {
            "movie": "{title} ({year})/{title} ({year}) - {resolution}.{ext}",
            "tv": "{title} ({year})/Season {season:02d}/{title} - S{season:02d}E{ep:02d}.{ext}",
        }}})
        from reelay.plugins.organizer import Organizer
        self.org = Organizer()

    def test_tv_plan_naming(self):
        p = ParsedResource(is_media=True, media_type="tv", title="法医秦明", year=2026,
                           season=1, episodes=[1, 2])
        landed = [{"id": "a", "name": "01.mp4"}, {"id": "b", "name": "02.mkv"}]
        plan = self.org._build_plan(p, landed)
        self.assertEqual(plan[0]["target_dir"], "法医秦明 (2026)/Season 01")
        self.assertEqual(plan[0]["target_name"], "法医秦明 - S01E01.mp4")
        self.assertEqual(plan[1]["target_name"], "法医秦明 - S01E02.mkv")

    def test_tv_plan_uses_filename_episode_not_api_order(self):
        p = ParsedResource(is_media=True, media_type="tv", title="法医秦明", year=2026,
                           season=1, episodes=[1, 2])
        landed = [{"id": "b", "name": "S01E02.mkv"}, {"id": "a", "name": "S01E01.mp4"}]
        plan = self.org._build_plan(p, landed)
        mapping = {item["src_name"]: item["target_name"] for item in plan}
        self.assertEqual(mapping["S01E02.mkv"], "法医秦明 - S01E02.mkv")
        self.assertEqual(mapping["S01E01.mp4"], "法医秦明 - S01E01.mp4")

    def test_tv_plan_rejects_ambiguous_episode(self):
        p = ParsedResource(is_media=True, media_type="tv", title="法医秦明", year=2026,
                           season=1, episodes=[1])
        with self.assertRaises(ValueError):
            self.org._build_plan(p, [{"id": "a", "name": "unknown.mp4"}])

    def test_tv_run_marks_manual_when_filename_ambiguous(self):
        p = ParsedResource(is_media=True, media_type="tv", title="法医秦明", year=2026,
                           season=1, episodes=[1])
        ctx = PipelineContext(raw=RawMessage(source="@b", msg_id="ambiguous", text="x"))
        ctx.parsed = p
        ctx.result = {"verified": True}
        ctx.ns("storage")["landed"] = [{"id": "a", "name": "unknown.mp4"}]
        self.assertTrue(run(self.org.run(ctx)))
        self.assertFalse(ctx.result["verified"])
        self.assertIn("error", ctx.ns("organize"))

    def test_movie_plan_naming(self):
        p = ParsedResource(is_media=True, media_type="movie", title="流浪地球2",
                           year=2023, resolution="4K")
        plan = self.org._build_plan(p, [{"id": "a", "name": "movie.mp4"}])
        self.assertEqual(plan[0]["target_dir"], "流浪地球2 (2023)")
        self.assertIn("流浪地球2 (2023) - 4K.mp4", plan[0]["target_name"])

    def test_validate_missing_fields(self):
        self.assertIsNotNone(self.org._validate(ParsedResource(is_media=True)))  # 缺标题
        self.assertIsNotNone(self.org._validate(
            ParsedResource(is_media=True, media_type="tv", title="X")))  # 剧集缺集数
        self.assertIsNone(self.org._validate(
            ParsedResource(is_media=True, media_type="movie", title="X")))  # 电影 OK


class TestStorageEpisodeSelection(unittest.TestCase):
    def test_selects_only_missing_episodes(self):
        from reelay.providers.storage import _select_video_items
        parsed = ParsedResource(media_type="tv", episodes=[9, 10])
        items = [
            {"id": "1", "name": "Show.S01E08.mkv"},
            {"id": "2", "name": "Show.S01E09.mkv"},
            {"id": "3", "name": "Show.S01E10.mkv"},
        ]
        selected = _select_video_items(items, parsed)
        self.assertEqual([item["id"] for item in selected], ["2", "3"])

    def test_incremental_share_requires_all_missing_episodes(self):
        from reelay.providers.storage import _select_video_items
        parsed = ParsedResource(media_type="tv", episodes=[9, 10])
        with self.assertRaises(RuntimeError):
            _select_video_items([{"id": "2", "name": "Show.S01E09.mkv"}], parsed)

    def test_ambiguous_incremental_share_fails_closed(self):
        from reelay.providers.storage import _select_video_items
        parsed = ParsedResource(media_type="tv", episodes=[9])
        with self.assertRaises(RuntimeError):
            _select_video_items([{"id": "1", "name": "video.mkv"}], parsed)


class TestTmdbDegrade(unittest.TestCase):
    def test_no_key_pass_through(self):
        import os
        os.environ.pop("REELAY_TMDB_API_KEY", None)
        from reelay.plugins.tmdb_enrich import TmdbEnrich
        h = TmdbEnrich()
        ctx = PipelineContext(raw=RawMessage(source="@b", msg_id="1", text="x"))
        ctx.parsed = ParsedResource(is_media=True, title="某片")
        self.assertTrue(run(h.run(ctx)))            # 不阻断
        self.assertFalse(ctx.ns("tmdb")["matched"])  # 未匹配，沿用原标题
        self.assertEqual(ctx.parsed.title, "某片")


if __name__ == "__main__":
    unittest.main()
