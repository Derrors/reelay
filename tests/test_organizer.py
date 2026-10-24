"""媒体库整理插件：选目录、二级分类、整理计划、命名模板。"""
from __future__ import annotations

import asyncio
import unittest

from reelay.core.config import normalize_config
from reelay.core.contracts import ParsedResource, PipelineContext, RawMessage
from reelay.plugins.organizer import build_plan


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
            asyncio.run(self.router.run(c))
        self.assertEqual(c.trail[0][9:], "二级分类命中「剧集/国产剧集」")
        self.assertEqual(len(c.trail), 1)
        c.parsed.media_type = "movie"
        asyncio.run(self.router.run(c))
        self.assertEqual((len(c.trail), c.save_dir_alias), (2, "电影"))

    def _layout(self, layout, **organizer):
        from reelay.core import runtime
        cfg = normalize_config({"save_dirs": {"剧集": "1", "电影": "2", "默认": "3"},
                                "plugins": {"organizer": {"layout": layout, **organizer}}})
        runtime.set_process_config(cfg)

    def _routed(self, current="默认", **kw):
        c = self._ctx(**kw)
        c.save_dir_alias = current
        asyncio.run(self.router.run(c))
        return c.save_dir_alias, c.meta["library"].get("relative_category")

    def test_media_type_layout(self):
        self._layout("media_type")
        # 文件夹名与保存目录别名相同：直接存进那个保存目录；不同：在原保存目录下建文件夹
        self.assertEqual(self._routed(media_type="tv", origin_country=["CN"]), ("剧集", ""))
        self.assertEqual(self._routed(media_type="movie"), ("电影", ""))
        self.assertEqual(self._routed(media_type="movie", genre_ids=[16]), ("默认", "动漫"))
        self.assertEqual(self._routed(media_type="anime"), ("默认", "动漫"))
        c = self._ctx(media_type="tv")
        asyncio.run(self.router.run(c))
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
        asyncio.run(self.router.run(c))
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
        from reelay.plugins.organizer import LibraryRouter
        runtime.set_process_config(normalize_config({"save_dirs": {"剧集": "1", "默认": "3"}}))
        ctx = PipelineContext(raw=RawMessage(source="@b", msg_id="1", text="x"))
        ctx.parsed = ParsedResource(is_media=True, title="T", media_type="", origin_country=["CN"])
        ctx.save_dir_alias = "默认"
        asyncio.run(LibraryRouter().run(ctx))
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
            "movie": "{{ title }} ({{ year }})/{{ title }} ({{ year }}) - {{ videoFormat }}{{ fileExt }}",
            "tv": "{{ title }} ({{ year }})/Season {{ season|pad2 }}/{{ title }} - {{ season_episode }}{{ fileExt }}",
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
        self.assertTrue(asyncio.run(self.org.run(ctx)))
        self.assertFalse(ctx.result["verified"])
        self.assertIn("error", ctx.ns("organize"))

    def test_movie_plan_naming(self):
        p = ParsedResource(is_media=True, media_type="movie", title="流浪地球2",
                           year=2023, resolution="4K")
        plan = build_plan(p, [{"id": "a", "name": "movie.mp4"}])
        self.assertEqual(plan[0]["target_dir"], "流浪地球2 (2023)")
        self.assertIn("流浪地球2 (2023) - 2160p.mp4", plan[0]["target_name"])

    def test_validate_missing_fields(self):
        self.assertIsNotNone(self.org._validate(ParsedResource(is_media=True)))  # 缺标题
        self.assertIsNotNone(self.org._validate(
            ParsedResource(is_media=True, media_type="tv", title="X")))  # 剧集缺集数
        self.assertIsNone(self.org._validate(
            ParsedResource(is_media=True, media_type="movie", title="X")))  # 电影 OK


class TestDefaultNaming(unittest.TestCase):
    NAME = "[死刑将至].Death.is.Coming.2026.S01E25.2160p.WEB-DL.60Fps.HDRVivid.H265.10bit.AAC-GROUP.mp4"

    def setUp(self):
        from reelay.core import runtime
        runtime.set_process_config({})

    def test_file_info_matches_moviepilot_variables(self):
        from reelay.plugins.organizer.naming import file_info
        self.assertEqual(file_info(self.NAME), {
            "videoFormat": "2160p", "videoCodec": "H265 10bit", "audioCodec": "AAC", "resourceType": "WEB-DL",
            "effect": "HDR Vivid", "edition": "WEB-DL HDR Vivid", "releaseGroup": "GROUP", "part": ""})
        self.assertEqual(file_info("Movie.2023.Part.2.1080p.WEB-DL.mkv")["part"], "PART2")
        self.assertEqual(file_info("Movie.2023.1080p.WEB-DL.mkv")["releaseGroup"], "")  # WEB-DL 不是发布组
        self.assertEqual(file_info("S01E01.mkv", "4K")["videoFormat"], "2160p")
        self.assertEqual(file_info("S01E01.mkv")["videoCodec"], "")

    def test_all_moviepilot_variables_render(self):
        from reelay.core import runtime
        from reelay.plugins.organizer.naming import ALLOWED_VARIABLES
        keys = sorted(ALLOWED_VARIABLES)
        template = ";".join(f"{{{{{key}}}}}" for key in keys) + "/x{{fileExt}}"
        runtime.set_process_config({"plugins": {"organizer": {"naming": {"tv": template}}}})
        p = ParsedResource(is_media=True, media_type="tv", title="死刑将至", year=2026, season=1, episodes=[25],
                           raw_fields={"tmdbid": 42})
        rendered = build_plan(p, [{"id": "a", "name": self.NAME}])[0]["target_dir"].split(";")
        values = dict(zip(keys, rendered, strict=True))
        self.assertEqual({k: values[k] for k in ("name", "en_name", "tmdbid", "season", "episode", "season_episode", "fileExt")},
                         {"name": "死刑将至", "en_name": "Death is Coming", "tmdbid": "42", "season": "1", "episode": "25",
                          "season_episode": "S01E25", "fileExt": ".mp4"})
        self.assertEqual(values["en_title"], "")

    def test_default_template_matches_release_names(self):
        p = ParsedResource(is_media=True, media_type="tv", title="死刑将至", season=1, episodes=[25])
        plan = build_plan(p, [{"id": "a", "name": self.NAME}])
        self.assertEqual(plan[0]["target_dir"], "死刑将至/Season 01")  # 缺年份时不留空括号
        p.year, p.raw_fields = 2026, {"tmdbid": 42}
        self.assertEqual(build_plan(p, [{"id": "a", "name": self.NAME}])[0]["target_dir"],
                         "死刑将至 (2026) [tmdbid=42]/Season 01")
        p.year, p.raw_fields = None, {}
        self.assertEqual(plan[0]["target_name"], "死刑将至 - S01E25 - 第 25 集 - 2160p H265 10bit AAC.mp4")
        p = ParsedResource(is_media=True, media_type="movie", title="流浪地球2", year=2023)
        plan = build_plan(p, [{"id": "a", "name": "The.Wandering.Earth.II.2023.1080p.BluRay.x264.DTS.mkv"}])
        self.assertEqual(plan[0]["target_dir"], "流浪地球2 (2023)")
        self.assertEqual(plan[0]["target_name"], "流浪地球2 (2023) - 1080p H264 DTS.mkv")

    def test_guess_from_file_names(self):
        from reelay.plugins.organizer import guess_from_names
        self.assertEqual(guess_from_names([self.NAME]),
                         {"title": "死刑将至", "year": 2026, "media_type": "tv", "season": 1, "episodes": [25]})
        self.assertEqual(guess_from_names(["[字幕组] 某番 S01E03.mkv"])["title"], "某番")
        self.assertEqual(guess_from_names(["【某字幕组】进击的巨人 第02集.mp4"])["title"], "进击的巨人")
        self.assertEqual(guess_from_names(["The.Wandering.Earth.II.2023.1080p.mkv"])["media_type"], "movie")

    def test_parse_names_uses_tmdb_like_the_real_pipeline(self):
        from unittest.mock import patch
        from reelay.core import tmdb
        from reelay.plugins.organizer import Organizer
        movie = {"id": 842675, "title": "The Wandering Earth II", "release_date": "2023-01-22"}

        def best_match(kind, query, year, *, language, timeout):
            if query == "死刑将至":
                return {"id": 326382, "name": "死刑将至", "first_air_date": "2026-01-01", "_kind": kind}, []
            if query == "The Wandering Earth II" and kind == "movie" and language == "en-US":
                return {**movie, "_kind": kind}, []  # 按中文搜到的是译名，只有英文结果能比上
            return None, []

        def search(kind, query, year, *, language, timeout):
            return [{**movie, "title": "流浪地球2"}] if kind == "movie" else []

        parse = Organizer().parse_names
        with patch.object(tmdb, "api_key", return_value="k"), patch.object(tmdb, "best_match", best_match), \
                patch.object(tmdb, "search", search):
            tv = asyncio.run(parse({"names": [self.NAME]}))
            self.assertEqual((tv["title"], tv["tmdbid"], tv["tmdb"], tv["episodes"]), ("死刑将至", 326382, "matched", [25]))
            film = asyncio.run(parse({"names": ["The.Wandering.Earth.II.2023.1080p.BluRay.x264.mkv"]}))
            self.assertEqual((film["title"], film["year"], film["tmdbid"], film["media_type"]),
                             ("流浪地球2", 2023, 842675, "movie"))
            self.assertEqual(asyncio.run(parse({"names": ["Unknown.Show.S01E01.mkv"]}))["tmdb"], "unmatched")
        with patch.object(tmdb, "api_key", return_value=None):
            self.assertEqual(asyncio.run(parse({"names": [self.NAME]}))["tmdb"], "unconfigured")
        with patch.object(tmdb, "api_key", return_value="k"), \
                patch.object(tmdb, "best_match", side_effect=TimeoutError):
            failed = asyncio.run(parse({"names": [self.NAME]}))
            self.assertEqual((failed["title"], failed["tmdb"]), ("死刑将至", "failed"))  # 查询失败仍给出文件名猜测


if __name__ == "__main__":
    unittest.main()
