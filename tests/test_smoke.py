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
from reelay.core.registry import discover
from reelay.plugins.organizer import build_plan
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

    def test_stored_episode_pairs_are_scoped_to_title_and_directory(self):
        """追更：只返回同一目标目录、同一剧名已入库的 [季, 集]。"""
        s = self.store
        for ep in range(1, 9):
            s.mark(DEDUP_EPISODE, "1:" + Store.episode_key("剧A", 1, ep))
        s.mark(DEDUP_EPISODE, "2:" + Store.episode_key("剧A", 1, 9))
        s.mark(DEDUP_EPISODE, "1:" + Store.episode_key("剧AB", 1, 9))
        self.assertEqual(s.stored_episode_pairs("剧A", "1"), [[1, ep] for ep in range(1, 9)])

    def test_state_update_keeps_snapshot_consistent(self):
        s = self.store
        ctx = PipelineContext(raw=RawMessage(source="@b", msg_id="retry", text="x"))
        ctx.state = TaskState.FAILED
        s.save_task(ctx, error="boom")
        self.assertTrue(s.set_state(ctx.task_id, TaskState.QUEUED))
        self.assertEqual(s.get_task(ctx.task_id).state, TaskState.QUEUED)

    def test_saving_without_manifest_is_requeued(self):
        # 转存清单先于写请求落库：没有清单说明还没发出转存，可以安全地重新排队
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

    def test_must_have_matches_hidden_url(self):
        c = self._ctx(text="点击获取影片", filters={"must_have": ["115.com", "115.cn"]})
        self.assertEqual(run(self.reg.get_provider("decision", "default").decide(c)), Decision.SAVE)

    def test_unrelated_url_cannot_bypass_must_have(self):
        c = self._ctx(text="影片推荐", filters={"must_have": ["特定关键词"]})
        c.raw.urls = ["https://example.com/video"]
        self.assertEqual(run(self.reg.get_provider("decision", "default").decide(c)), Decision.SKIP)


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


class TestLibraryRouter(unittest.TestCase):
    def setUp(self):
        from reelay.core import runtime
        runtime.set_process_config({})
        from reelay.plugins.organizer import LibraryRouter
        self.router = LibraryRouter()

    def _ctx(self, **kw):
        ctx = PipelineContext(raw=RawMessage(source="@b", msg_id="1", text="x"))
        ctx.parsed = ParsedResource(is_media=True, title="T", **kw)
        return ctx

    def test_unchanged_route_is_logged_once(self):
        # 等待同一集的其他任务时每轮都会重新路由
        from reelay.core import runtime
        runtime.set_process_config({"save_dirs": {"剧集": "1", "电影": "2"}})
        c = self._ctx(media_type="tv", origin_country=["CN"])
        for _ in range(3):
            run(self.router.run(c))
        self.assertEqual(c.trail[0][9:], "二级分类命中「剧集/国产剧集」")
        self.assertEqual(len(c.trail), 1)
        c.parsed.media_type = "movie"
        run(self.router.run(c))
        self.assertEqual((len(c.trail), c.save_dir_alias), (2, "电影"))

    def _layout(self, layout, **organizer):
        from reelay.core import runtime
        from reelay.core.config import normalize_config
        cfg = normalize_config({"save_dirs": {"剧集": "1", "电影": "2", "默认": "3"},
                                "plugins": {"organizer": {"layout": layout, **organizer}}})
        runtime.set_process_config(cfg)

    def _routed(self, current="默认", **kw):
        c = self._ctx(**kw)
        c.save_dir_alias = current
        run(self.router.run(c))
        return c.save_dir_alias, c.meta["library"].get("relative_category")

    def test_media_type_layout(self):
        self._layout("media_type")
        # 文件夹名与保存目录别名相同：直接存进那个保存目录；不同：在原保存目录下建文件夹
        self.assertEqual(self._routed(media_type="tv", origin_country=["CN"]), ("剧集", ""))
        self.assertEqual(self._routed(media_type="movie"), ("电影", ""))
        self.assertEqual(self._routed(media_type="movie", genre_ids=[16]), ("默认", "动漫"))
        self.assertEqual(self._routed(media_type="anime"), ("默认", "动漫"))
        c = self._ctx(media_type="tv")
        run(self.router.run(c))
        self.assertEqual(c.trail[-1][9:], "按媒体类型归入「剧集」")

    def test_media_type_layout_without_anime_folder(self):
        self._layout("media_type", type_dirs={"movie": "电影", "tv": "剧集", "anime": ""})
        self.assertEqual(self._routed(media_type="anime"), ("剧集", ""))
        self.assertEqual(self._routed(media_type="movie", genre_ids=[16]), ("电影", ""))

    def test_no_layout_keeps_the_save_directory(self):
        self._layout("none")
        c = self._ctx(media_type="tv", origin_country=["CN"])
        c.save_dir_alias = "默认"
        c.ns("library")["relative_category"] = "国产剧集"  # 改为不分类前算出的结果
        run(self.router.run(c))
        self.assertEqual(c.save_dir_alias, "默认")
        self.assertNotIn("relative_category", c.meta["library"])
        self.assertEqual(self.router.preview(c), "默认")

class TestCategoryStrategy(unittest.TestCase):
    def test_shipped_category_file(self):
        from reelay.plugins.organizer.category import classify
        from reelay.plugins.organizer import categories
        rules = categories()
        for args, expected in (
            (("tv", [16], ["JP"]), "动漫/日本番剧"),
            (("anime", [16], ["CN"]), "动漫/国产动漫"),  # 动漫按剧集匹配
            (("tv", [99], ["CN"]), "其他/纪录影片"),  # 题材优先于地区：按文件顺序
            (("tv", [18], ["KR"]), "剧集/日韩剧集"),
            (("movie", [16], ["US"]), "动漫/动画电影"),
            (("movie", [18], ["US"]), "电影/欧美电影"),
            (("movie", [], []), "电影/欧美电影"),  # 没有条件的分类接住其余电影
            (("tv", [18], ["BR"]), "剧集/其他剧集"),  # 剧集的兜底
            (("tv", [], []), "剧集/其他剧集"),  # 缺少地区与题材信息时，带条件的分类都不命中
            (("", [18], ["CN"]), None),  # 识别不出媒体类型：不归入任何分类
        ):
            with self.subTest(args=args):
                self.assertEqual(classify(rules, *args), expected)

    def test_matching_follows_moviepilot(self):
        from reelay.plugins.organizer.category import classify
        # 按顺序取第一个命中的；没有条件的分类遇到即命中，后面的不再看
        rules = {"tv": {"剧集/全部": {}, "剧集/国产剧集": {"origin_country": "CN"}}}
        self.assertEqual(classify(rules, "tv", [], ["CN"]), "剧集/全部")
        # 同一分类内的条件都要满足；年份可写范围
        rules = {"movie": {"电影/老电影": {"release_year": "1900-1999", "origin_country": "cn"}, "电影/其他": {}}}
        self.assertEqual(classify(rules, "movie", [], ["CN"], 1994), "电影/老电影")
        self.assertEqual(classify(rules, "movie", [], ["US"], 1994), "电影/其他")
        self.assertEqual(classify(rules, "movie", [], ["CN"], None), "电影/其他")

    def test_category_without_match_uses_the_save_directory(self):
        from reelay.core import runtime
        from reelay.core.config import normalize_config
        from reelay.plugins.organizer import LibraryRouter
        runtime.set_process_config(normalize_config({"save_dirs": {"剧集": "1", "默认": "3"}}))
        ctx = PipelineContext(raw=RawMessage(source="@b", msg_id="1", text="x"))
        ctx.parsed = ParsedResource(is_media=True, title="T", media_type="", origin_country=["CN"])
        ctx.save_dir_alias = "默认"
        run(LibraryRouter().run(ctx))
        self.assertEqual((ctx.save_dir_alias, ctx.meta["library"].get("relative_category")), ("默认", None))
        self.assertEqual(ctx.trail[-1][9:], "无二级分类元数据，用根目录「默认」")

    def test_invalid_category_file_is_rejected(self):
        from reelay.plugins.organizer.category import validate_strategy
        for strategy in ({"music": {"音乐/全部": {}}}, {"tv": {"只有一级": {}}},
                         {"tv": {"剧集/英语": {"original_language": "en"}}}, {"tv": {"剧集/空": {"genre_ids": ","}}}):
            with self.subTest(strategy=strategy), self.assertRaises(ValueError):
                validate_strategy(strategy)


class TestOrganizerPlan(unittest.TestCase):
    def setUp(self):
        from reelay.core import runtime
        runtime.set_process_config({"plugins": {"organizer": {"naming": {
            "movie": "{{ title }} ({{ year }})/{{ title }} ({{ year }}) - {{ resolution }}.{{ ext }}",
            "tv": "{{ title }} ({{ year }})/Season {{ season|pad2 }}/{{ title }} - S{{ season|pad2 }}E{{ episode|pad2 }}.{{ ext }}",
        }}}})
        from reelay.plugins.organizer import Organizer
        self.org = Organizer()

    def test_tv_plan_naming(self):
        p = ParsedResource(is_media=True, media_type="tv", title="法医秦明", year=2026,
                           season=1, episodes=[1, 2])
        landed = [{"id": "a", "name": "01.mp4"}, {"id": "b", "name": "02.mkv"}]
        plan = build_plan(p, landed)
        self.assertEqual(plan[0]["target_dir"], "法医秦明 (2026)/Season 01")
        self.assertEqual(plan[0]["target_name"], "法医秦明 - S01E01.mp4")
        self.assertEqual(plan[1]["target_name"], "法医秦明 - S01E02.mkv")

    def test_tv_plan_uses_filename_episode_not_api_order(self):
        p = ParsedResource(is_media=True, media_type="tv", title="法医秦明", year=2026,
                           season=1, episodes=[1, 2])
        landed = [{"id": "b", "name": "S01E02.mkv"}, {"id": "a", "name": "S01E01.mp4"}]
        plan = build_plan(p, landed)
        mapping = {item["src_name"]: item["target_name"] for item in plan}
        self.assertEqual(mapping["S01E02.mkv"], "法医秦明 - S01E02.mkv")
        self.assertEqual(mapping["S01E01.mp4"], "法医秦明 - S01E01.mp4")

    def test_tv_plan_rejects_ambiguous_episode(self):
        p = ParsedResource(is_media=True, media_type="tv", title="法医秦明", year=2026,
                           season=1, episodes=[1])
        with self.assertRaises(ValueError):
            build_plan(p, [{"id": "a", "name": "unknown.mp4"}])

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
        plan = build_plan(p, [{"id": "a", "name": "movie.mp4"}])
        self.assertEqual(plan[0]["target_dir"], "流浪地球2 (2023)")
        self.assertIn("流浪地球2 (2023) - 4K.mp4", plan[0]["target_name"])

    def test_validate_missing_fields(self):
        self.assertIsNotNone(self.org._validate(ParsedResource(is_media=True)))  # 缺标题
        self.assertIsNotNone(self.org._validate(
            ParsedResource(is_media=True, media_type="tv", title="X")))  # 剧集缺集数
        self.assertIsNone(self.org._validate(
            ParsedResource(is_media=True, media_type="movie", title="X")))  # 电影 OK


class TestStorageEpisodeSelection(unittest.TestCase):
    ITEMS = [
        {"id": "1", "name": "Show.S01E08.mkv"},
        {"id": "2", "name": "Show.S01E09.mkv"},
        {"id": "3", "name": "Show.S01E10.mkv"},
    ]

    def test_share_listing_is_source_of_truth_and_skips_stored(self):
        from reelay.providers.storage import _select_video_items
        parsed = ParsedResource(media_type="tv", episodes=[10])  # 消息只提示最新一集
        selected = _select_video_items(self.ITEMS, parsed, stored=[[1, 8]])
        self.assertEqual([item["id"] for item in selected["files"]], ["2", "3"])
        self.assertEqual((selected["episodes"], selected["season"]), ([9, 10], 1))

    def test_empty_llm_episodes_still_select_from_filenames(self):
        from reelay.providers.storage import _select_video_items
        selected = _select_video_items(self.ITEMS, ParsedResource(media_type="tv"))
        self.assertEqual(selected["episodes"], [8, 9, 10])

    def test_message_scope_restricts_to_declared_episodes(self):
        from reelay.providers.storage import _select_video_items
        parsed = ParsedResource(media_type="tv", episodes=[9, 10])
        selected = _select_video_items(self.ITEMS, parsed, scope="message")
        self.assertEqual([item["id"] for item in selected["files"]], ["2", "3"])

    def test_message_scope_requires_all_declared_episodes(self):
        from reelay.core.contracts import NeedsManual
        from reelay.providers.storage import _select_video_items
        parsed = ParsedResource(media_type="tv", episodes=[9, 10])
        with self.assertRaises(NeedsManual):
            _select_video_items([{"id": "2", "name": "Show.S01E09.mkv"}], parsed, scope="message")

    def test_ambiguous_incremental_share_fails_closed(self):
        from reelay.core.contracts import NeedsManual
        from reelay.providers.storage import _select_video_items
        parsed = ParsedResource(media_type="tv", episodes=[9])
        with self.assertRaises(NeedsManual):
            _select_video_items([{"id": "1", "name": "video.mkv"}], parsed)

    def test_all_stored_raises_already_stored(self):
        from reelay.core.contracts import AlreadyStored
        from reelay.providers.storage import _select_video_items
        with self.assertRaises(AlreadyStored):
            _select_video_items(self.ITEMS, ParsedResource(media_type="tv"), stored=[[1, 8], [1, 9], [1, 10]])

    def test_extras_are_dropped_before_episode_detection(self):
        from reelay.providers.storage import _select_video_items
        items = [*self.ITEMS[:1], {"id": "9", "name": "预告片.mp4"}, {"id": "8", "name": "Show.Sample.mkv"}]
        selected = _select_video_items(items, ParsedResource(media_type="tv"))
        self.assertEqual([item["id"] for item in selected["files"]], ["1"])

    def test_movie_drops_small_extras_and_rejects_multiple_mains(self):
        from reelay.core.contracts import NeedsManual
        from reelay.providers.storage import _select_video_items
        movie = ParsedResource(media_type="movie")
        items = [{"id": "m", "name": "Film.2160p.mkv", "size": 20_000},
                 {"id": "f", "name": "Featurette.mkv", "size": 900},
                 {"id": "x", "name": "Bonus.mkv", "size": 1_000}]
        self.assertEqual([x["id"] for x in _select_video_items(items, movie)["files"]], ["m"])
        with self.assertRaises(NeedsManual):
            _select_video_items([{"id": "a", "name": "Film.CD1.mkv", "size": 9_000},
                                 {"id": "b", "name": "Film.CD2.mkv", "size": 8_000}], movie)

    def test_disc_named_after_its_bundled_extras_is_still_the_movie(self):
        # 原盘文件名列出附带内容：「花絮中字」
        from reelay.core.contracts import NeedsManual
        from reelay.providers.storage import _select_video_items
        disc = {"id": "d", "name": "[Film 2018][原盘 简繁字幕 花絮中字][40.91G].iso", "size": 40 << 30}
        trailer = {"id": "t", "name": "Film 预告片.mp4", "size": 80 << 20}
        movie = ParsedResource(media_type="movie")
        self.assertEqual([x["id"] for x in _select_video_items([disc, trailer], movie)["files"]], ["d"])
        with self.assertRaises(NeedsManual):  # 只有预告片
            _select_video_items([trailer], movie)
        with self.assertRaises(NeedsManual):  # 剧集分享里只有花絮时仍转人工
            _select_video_items([{**disc, "name": "Show 花絮 E01.mkv"}], ParsedResource(media_type="tv"))

    MULTI = [{"id": "11", "name": "Show.S01E01.mkv"}, {"id": "12", "name": "Show.S01E02.mkv"},
             {"id": "21", "name": "Show.S02E01.mkv"}, {"id": "22", "name": "Show.S02E02.mkv"}]

    def test_multi_season_share_is_received_season_by_season(self):
        from reelay.providers.storage import _select_video_items
        selected = _select_video_items(self.MULTI, ParsedResource(media_type="tv"), stored=[[1, 1]])
        self.assertEqual([(x["id"], x["season"], x["episode"]) for x in selected["files"]],
                         [("12", 1, 2), ("21", 2, 1), ("22", 2, 2)])
        self.assertEqual((selected["season"], selected["episodes"], selected["pairs"]),
                         (None, [1, 2], [[1, 2], [2, 1], [2, 2]]))

    def test_message_season_still_restricts_multi_season_share(self):
        from reelay.providers.storage import _select_video_items
        selected = _select_video_items(self.MULTI, ParsedResource(media_type="tv", season=2))
        self.assertEqual(([x["id"] for x in selected["files"]], selected["season"], selected["pairs"]),
                         (["21", "22"], 2, [[2, 1], [2, 2]]))

    def test_ambiguous_multi_season_shares_still_stop_before_transfer(self):
        from reelay.core.contracts import AlreadyStored, NeedsManual
        from reelay.providers.storage import _select_video_items
        cases = {
            "有文件名不带季号": [*self.MULTI, {"id": "9", "name": "Show.E03.mkv"}],
            "第 2 季第 1 集有多个文件": [*self.MULTI, {"id": "23", "name": "Show.S02E01.1080p.mkv"}],
        }
        for reason, items in cases.items():
            with self.subTest(reason), self.assertRaisesRegex(NeedsManual, reason):
                _select_video_items(items, ParsedResource(media_type="tv"))
        with self.assertRaisesRegex(NeedsManual, "消息只写了集数"):
            _select_video_items(self.MULTI, ParsedResource(media_type="tv", episodes=[1]), scope="message")
        with self.assertRaisesRegex(AlreadyStored, "2 季共 4 集"):
            _select_video_items(self.MULTI, ParsedResource(media_type="tv"), stored=[[1, 1], [1, 2], [2, 1], [2, 2]])


class TestTelegramUserbotGuard(unittest.TestCase):
    def test_source_contains_bot_account_guard(self):
        from pathlib import Path
        source = Path("reelay/providers/source.py").read_text(encoding="utf-8")
        self.assertIn('getattr(account, "bot", False)', source)
        self.assertIn("不能使用控制 Bot 会话", source)


class TestTmdbDegrade(unittest.TestCase):
    def test_no_key_pass_through(self):
        from reelay.plugins.tmdb_enrich import TmdbEnrich
        h = TmdbEnrich()
        ctx = PipelineContext(raw=RawMessage(source="@b", msg_id="1", text="x"))
        ctx.parsed = ParsedResource(is_media=True, title="某片")
        self.assertTrue(run(h.run(ctx)))            # 不阻断
        self.assertFalse(ctx.ns("tmdb")["matched"])  # 未匹配，沿用原标题
        self.assertEqual(ctx.parsed.title, "某片")


if __name__ == "__main__":
    unittest.main()
