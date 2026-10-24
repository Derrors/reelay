"""TMDB 补全：搜索打分、没有 key 时降级。"""
from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

from reelay.core import runtime, secret_store, tmdb
from reelay.core.contracts import ParsedResource, PipelineContext, RawMessage
from reelay.plugins.tmdb_enrich import TmdbEnrich, search_kinds

from tests.fakes import message


REAL_DETAILS = tmdb.details  # 各用例默认把它替换成空结果（不联网）


class TestTmdbMatching(unittest.IsolatedAsyncioTestCase):
    RESULTS = [
        {"id": 1, "title": "完全不同的电影", "release_date": "2026-01-01"},
        {"id": 2, "title": "流浪地球2", "original_title": "流浪地球2", "release_date": "2023-01-22",
         "genre_ids": [878]},
    ]

    def setUp(self):
        tmdb._CACHE.clear()
        no_details = patch.object(tmdb, "details", return_value={})  # 测试不能联网
        no_details.start()
        self.addCleanup(no_details.stop)

    def test_details_reads_languages_cast_and_rating(self):
        data = {"original_language": "zh", "vote_average": 6.27,
                "spoken_languages": [{"iso_639_1": "cn"}, {"iso_639_1": "zh"}, {"iso_639_1": "xx"}],
                "credits": {"cast": [{"name": f"演员{i}", "order": 6 - i} for i in range(7)]}}
        with patch.object(tmdb, "_request", return_value=data) as request:  # 用真实的 details，只替换网络请求
            details = REAL_DETAILS("movie", 9, language="zh-CN", timeout=1)
        self.assertEqual(details, {"languages": ["国语", "粤语", "xx"], "cast": ["演员6", "演员5", "演员4", "演员3", "演员2"],
                                   "rating": 6.3})
        self.assertEqual(request.call_args.args[0], "/movie/9")

    def test_best_match_prefers_title_similarity_over_rank(self):
        with patch.object(tmdb, "_request", return_value={"results": self.RESULTS}):
            hit, candidates = tmdb.best_match("movie", "流浪地球2", 2023, language="zh-CN", timeout=1)
        self.assertEqual(hit["id"], 2)
        self.assertEqual(candidates[0]["id"], 2)

    def test_year_conflict_and_low_similarity_are_rejected(self):
        with patch.object(tmdb, "_request", return_value={"results": self.RESULTS}):
            self.assertIsNone(tmdb.best_match("movie", "流浪地球2", 2030, language="zh-CN", timeout=1)[0])
            self.assertIsNone(tmdb.best_match("movie", "毫不相干", None, language="zh-CN", timeout=1)[0])

    def test_results_are_cached(self):
        with patch.object(tmdb, "_request", return_value={"results": self.RESULTS}) as request:
            tmdb.search("movie", "流浪地球2", 2023, language="zh-CN", timeout=1)
            tmdb.search("movie", "流浪地球 2", 2023, language="zh-CN", timeout=1)
        self.assertEqual(request.call_count, 1)

    def test_base_url_from_system_config_is_used(self):
        # 系统配置 tmdb.base_url 在请求时读取，管理台改了立即生效
        seen = []

        class Resp:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return b'{"results": []}'

        with secret_store.override(tmdb_api_key="k"), runtime.scope({"tmdb": {"base_url": "https://tmdb.example/3"}}), \
                patch("urllib.request.urlopen", side_effect=lambda req, timeout: seen.append(req.full_url) or Resp()):
            tmdb.search("movie", "流浪地球2", 2023, language="zh-CN", timeout=1)
        self.assertTrue(seen[0].startswith("https://tmdb.example/3/search/movie?"))

    def test_anime_searches_tv_then_movie(self):
        self.assertEqual(search_kinds("anime", True), ["tv", "movie"])
        self.assertEqual(search_kinds("anime", False), ["movie", "tv"])
        self.assertEqual(search_kinds("movie", False), ["movie", "tv"])
        self.assertEqual(search_kinds(None, False), ["tv", "movie"])

    async def test_unknown_type_searches_tv_and_movie_and_takes_the_closer_match(self):
        from reelay.providers.parser import _regex_parse
        share = "https://115.com/s/abc123?password=abcd"
        # 降级到规则解析时看不出是剧集：不猜电影
        ctx = message(text=f"🎬 嫁金钗 (2026) DV 60FPS\n{share}")
        ctx.parsed = _regex_parse(ctx)
        self.assertEqual((ctx.parsed.title, ctx.parsed.media_type), ("嫁金钗", None))
        for text in ("某剧 S01E05 4K", "某剧 全36集", "某剧 第 3 季"):
            self.assertEqual(_regex_parse(message(text=f"{text}\n{share}")).media_type, "tv", text)
        tv_hit = {"id": 7, "name": "嫁金钗", "first_air_date": "2026-09-01", "_kind": "tv", "_score": 1.1}
        movie_hit = {"id": 8, "title": "嫁金钗", "release_date": "2026-01-01", "_kind": "movie", "_score": 0.9}
        hits = {"tv": (tv_hit, [tv_hit]), "movie": (movie_hit, [movie_hit])}
        with secret_store.override(tmdb_api_key="k"), \
                patch.object(tmdb, "best_match", side_effect=lambda kind, *a, **k: hits[kind]) as match:
            await TmdbEnrich().run(ctx)
        self.assertEqual(sorted(call.args[0] for call in match.call_args_list), ["movie", "tv"])
        self.assertEqual((ctx.parsed.media_type, ctx.meta["tmdb"]["tmdbid"]), ("tv", 7))
        # AI 判为电影、电影搜不到而剧集能匹配上：改为剧集
        ctx = message(text=f"🎬 嫁金钗 (2026)\n{share}")
        ctx.parsed = ParsedResource(is_media=True, title="嫁金钗", year=2026, media_type="movie", share_code="abc123")
        hits["movie"] = (None, [])
        with secret_store.override(tmdb_api_key="k"), patch.object(tmdb, "best_match", side_effect=lambda kind, *a, **k: hits[kind]):
            await TmdbEnrich().run(ctx)
        self.assertEqual((ctx.parsed.media_type, ctx.meta["tmdb"]["kind"]), ("tv", "tv"))
        hits["movie"] = (movie_hit, [movie_hit])
        # 只有电影能匹配上
        ctx = message(text=f"某片 (2020)\n{share}")
        ctx.parsed = _regex_parse(ctx)
        hits["tv"] = (None, [])
        with secret_store.override(tmdb_api_key="k"), patch.object(tmdb, "best_match", side_effect=lambda kind, *a, **k: hits[kind]):
            await TmdbEnrich().run(ctx)
        self.assertEqual(ctx.parsed.media_type, "movie")

    async def test_movie_match_keeps_ai_origin_country(self):
        with secret_store.override(tmdb_api_key="k"), \
                patch.object(tmdb, "_request", return_value={"results": self.RESULTS}):
            hook = TmdbEnrich()
            ctx = message()
            ctx.parsed = ParsedResource(is_media=True, title="流浪地球2", year=2023, media_type="movie",
                                        origin_country=["CN"])
            await hook.run(ctx)
        self.assertEqual((ctx.parsed.genre_ids, ctx.parsed.origin_country), ([878], ["CN"]))
        self.assertTrue(ctx.ns("tmdb")["matched"])


class TestTmdbDegrade(unittest.TestCase):
    def test_no_key_pass_through(self):
        from reelay.plugins.tmdb_enrich import TmdbEnrich
        h = TmdbEnrich()
        ctx = PipelineContext(raw=RawMessage(source="@b", msg_id="1", text="x"))
        ctx.parsed = ParsedResource(is_media=True, title="某片")
        self.assertTrue(asyncio.run(h.run(ctx)))            # 不阻断
        self.assertFalse(ctx.ns("tmdb")["matched"])  # 未匹配，沿用原标题
        self.assertEqual(ctx.parsed.title, "某片")


if __name__ == "__main__":
    unittest.main()
