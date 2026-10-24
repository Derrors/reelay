"""日报与追更：缺集统计、日报内容与叙述、追更提醒。"""
from __future__ import annotations

import unittest
import json
from unittest.mock import patch

from reelay.core import messages, runtime, secret_store, subscriptions, tmdb
from reelay.core.config import normalize_config
from reelay.core.contracts import ParsedResource, PipelineContext, RawMessage
from reelay.core.media import format_episodes
from reelay.core.registry import Registry
from reelay.plugins.daily_report import render
from reelay.plugins.episode_tracker import EpisodeRecorder, EpisodeTracker, missing_episodes, render as tracker_render

from tests.fakes import FakeAI, PipelineCase, SHARE, ServiceCase, bot_for, done_task, message


class TestDailyAndEpisodeTracker(PipelineCase):
    def test_render_skips_quiet_days(self):
        self.assertIsNone(render({"received": 0}, "2026-10-19"))
        text = render({"received": 4, "done_count": 1, "done": [{"title": "剧"}],
                       "received_by_state": {"rejected": 2}, "pending_approval": 3}, "2026-10-19")
        self.assertIn("新收 4 条 · 入库 1 条 · 过滤 2 条", text)
        self.assertIn("📺 剧", text)
        self.assertIn("待审批 3", text)
        done = [{"title": "剧", "media_type": "tv", "pairs": [[1, 9]]}, {"title": "<片>", "media_type": "movie"},
                {"title": "剧", "media_type": "tv", "pairs": [[1, 10]]}, {"title": "片", "media_type": "movie"},
                {"title": "片", "media_type": "movie"}, {"title": "番", "media_type": "anime", "pairs": [[1, 1], [2, 1]]}]
        text = render({"received": 6, "done_count": 6, "done": done}, "2026-10-19", story="《剧》已入库")
        self.assertIn("<blockquote>《剧》已入库</blockquote>", text)
        self.assertIn("📺 剧 · S01 E09-E10\n🎬 &lt;片&gt;\n🎬 片 ×2\n🍥 番 · S01–S02 共 2 集", text)
        self.assertNotIn("待处理", text)

    async def test_daily_event_is_emitted_once_per_day(self):
        registry = Registry()
        tracker = EpisodeTracker()
        registry.add_listener(tracker)
        pipeline = self.pipeline({"save_dirs": {"默认": "1"}, "defaults": {"save_to": "默认"},
                                  "schedule": {"daily_hour": 0}}, registry)
        self.assertTrue(await pipeline.emit_daily())
        self.assertFalse(await pipeline.emit_daily())
        self.assertEqual(self.store._conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 0)
        row = self.store._conn.execute("SELECT kind, event, target FROM outbox").fetchone()
        self.assertEqual(tuple(row), ("event", "on_daily", "episode_tracker"))

    def test_missing_episode_rules(self):
        self.assertEqual(missing_episodes({20, 21, 23}, total=None, aired=None, from_first=True), [22])
        self.assertEqual(missing_episodes({20, 21}, total=None, aired=24, from_first=True), [22, 23, 24])
        self.assertEqual(missing_episodes({2, 3}, total=4, aired=None, from_first=False), [1, 4])
        self.assertEqual(format_episodes([1, 2, 3, 5]), "E01-E03、E05")

    async def test_tracker_reports_gaps_through_notice(self):
        registry = Registry()
        recorder, tracker = EpisodeRecorder(), EpisodeTracker()
        registry.add_listener(recorder)
        registry.add_listener(tracker)
        pipeline = self.pipeline(registry=registry)
        for episodes in ([20, 21], [23]):
            ctx = message()
            ctx.parsed = ParsedResource(is_media=True, title="一瓯春", season=1, episodes=episodes, share_code="s")
            ctx.save_dir_cid = "1"
            ctx.result = {"storage_verified": True}
            ctx.ns("storage")["identity"] = {"cid": "1", "share_code": f"s{episodes[0]}", "title": "一瓯春",
                                             "season": 1, "episodes": episodes}
            self.store.save_task(ctx)
            self.store.finish_transfer(ctx)
            with pipeline.scope(pipeline.cfg):
                await recorder.handle(ctx)
        with pipeline.scope(pipeline.cfg):
            await tracker.handle(message())
        notice = json.loads(self.store._conn.execute(
            "SELECT snapshot FROM outbox WHERE kind='notice'").fetchone()[0])
        self.assertTrue(notice["html"])
        self.assertIn("📺 <b>一瓯春</b> · S01\n缺 E22\n已入库 3 集", notice["text"])

    async def test_finished_season_ends_the_subscription(self):
        import time as _time
        cfg = {"save_dirs": {"默认": "1"}, "defaults": {"save_to": "默认"}}
        pipeline = self.pipeline(cfg)
        for item in ({"title": "一瓯春", "season": 1, "tmdbid": 5}, {"title": "在播剧", "season": 1, "tmdbid": 6}):
            self.store.add_subscription(subscriptions.key(item), subscriptions.normalize(item))
        for title, episodes in (("一瓯春", [1, 2, 3]), ("在播剧", [1, 2])):
            ctx = message(msg_id=title)
            ctx.parsed = ParsedResource(is_media=True, title=title, season=1, episodes=episodes, share_code=title)
            ctx.save_dir_cid, ctx.result = "1", {"storage_verified": True}
            ctx.ns("storage")["identity"] = {"cid": "1", "share_code": title, "title": title, "season": 1, "episodes": episodes}
            self.store.save_task(ctx)
            self.store.finish_transfer(ctx)
        day = lambda ago: _time.strftime("%Y-%m-%d", _time.localtime(_time.time() - ago * 86400))  # noqa: E731
        seasons = {5: [day(40), day(33), day(30)], 6: [day(3), day(2)]}  # 一瓯春 30 天前播完；在播剧前天才播完
        episodes = lambda tv_id, season, **_: [{"episode": i + 1, "air_date": d} for i, d in enumerate(seasons[tv_id])]  # noqa: E731
        details = lambda tv_id, **_: {"status": "Returning Series", "seasons": [{"season": 1, "episodes": 3}]}  # noqa: E731
        with secret_store.override(tmdb_api_key="k"), patch.object(tmdb, "season_episodes", episodes), \
                patch.object(tmdb, "tv_details", details), pipeline.scope(pipeline.cfg):
            await EpisodeTracker().handle(message())
        finished = {s["tmdbid"]: s["finished"] for s in self.store.subscriptions()}
        self.assertEqual((finished[5]["episodes"], finished[6]), (3, None))  # 在播剧最近才播完、也没完结：先不结束
        notice = json.loads(self.store._conn.execute("SELECT snapshot FROM outbox WHERE kind='notice'").fetchone()[0])
        self.assertIn("🏁 《一瓯春》第 1 季已播完，3 集全部入库，订阅已自动结束", notice["text"])
        self.assertEqual(notice["buttons"], [[["▶️ 继续订阅 一瓯春 S01", "so:1"]]])
        # 剧已完结：最后一集刚播也可以结束
        with secret_store.override(tmdb_api_key="k"), patch.object(tmdb, "season_episodes", episodes), \
                patch.object(tmdb, "tv_details", lambda tv_id, **_: {"status": "Ended", "seasons": []}), \
                pipeline.scope(pipeline.cfg):
            await EpisodeTracker().handle(message())
        self.assertTrue(all(s["finished"] for s in self.store.subscriptions()))

    def test_tracker_render_groups_overdue_episodes(self):
        text = tracker_render({
            "甲|S01": {"have": [1], "missing": [2], "aired": None, "overdue": [], "media_type": "anime"},
            "乙|S01": {"have": list(range(1, 11)), "missing": list(range(11, 25)), "aired": 24,
                      "overdue": [{"episode": 20, "days": 20}, {"episode": 21, "days": 20}, {"episode": 13, "days": 7600}]},
            "丙|S02": {"have": [1], "missing": [2, 3], "aired": 3,
                      "overdue": [{"episode": 3, "days": 2}, {"episode": 2, "days": 8}]},
        })
        self.assertTrue(text.startswith("🔔 <b>追更检查</b> · 3 部缺集，2 部逾期\n\n📺 <b>乙</b> · S01"))
        self.assertIn("🍥 <b>甲</b> · S01", text)
        self.assertIn("⏰ E02-E03 已播 2~8 天，仍未入库", text)
        self.assertIn("已入库 10 集 / 已播 24 集\n⏰ E13、E20-E21 已播 20 天~20 年，仍未入库", text)

    async def test_recorder_tracks_each_season_of_a_multi_season_pack(self):
        recorder = EpisodeRecorder()
        pipeline = self.pipeline()
        ctx = message()
        ctx.parsed = ParsedResource(is_media=True, title="毒枭", media_type="tv", episodes=[1, 2], share_code="s")
        ctx.ns("storage")["manifest"] = {"pairs": [[1, 1], [1, 2], [3, 1]]}
        with pipeline.scope(pipeline.cfg):
            await recorder.handle(ctx)
        self.assertEqual(sorted(self.store.state_items("episode_tracker")), ["series:毒枭|S01", "series:毒枭|S03"])

    def test_subscriptions_are_not_config(self):
        with self.assertRaises(ValueError):
            normalize_config({"subscriptions": [{"title": "剧"}]})
        with self.assertRaises(ValueError):
            subscriptions.normalize({"season": 1})
        self.assertEqual(subscriptions.normalize({"title": " 剧 ", "total": 12}),
                         {"title": "剧", "season": 1, "total": 12, "tmdbid": None, "auto": True})


class TestDailyNarrative(ServiceCase):
    def setUp(self):
        super().setUp()
        self.report = next(x for x in self.registry.listeners["on_daily"] if x.name == "daily_report")

    async def send(self):
        ctx = PipelineContext(raw=RawMessage(source="system", msg_id="daily", text=""), task_id="daily-x")
        ctx.meta["system"] = {"date": "2026-10-19", "summary": {"received": 3, "done_count": 1, "done": [{"title": "剧"}],
                                                               "pending_approval": 2}}
        with self.pl.scope(self.pl.cfg):
            await self.report.handle(ctx)
        rows = self.store._conn.execute("SELECT snapshot FROM outbox WHERE kind='notice' ORDER BY id").fetchall()
        return json.loads(rows[-1][0])["text"]

    async def test_story_comes_first_and_numbers_stay(self):
        done_task(self.store, "1", title="剧")
        self.report._client = FakeAI({"text": "本周新收 1 部剧，《剧》已入库。"})
        text = await self.send()
        self.assertTrue(text.startswith("📊 <b>Reelay 日报</b> · 2026-10-19\n\n<blockquote>本周新收 1 部剧，《剧》已入库。</blockquote>"
                                        "\n\n📥 <b>过去 24 小时</b>\n新收 3 条"))
        facts = json.loads(self.report._client.calls[0]["messages"][1]["content"])["facts"]
        self.assertEqual(facts["last_7d"]["top"][0]["title"], "剧")
        self.report._client = FakeAI("bad")
        self.assertTrue((await self.send()).startswith("📊 <b>Reelay 日报</b> · 2026-10-19\n\n📥 <b>过去 24 小时</b>"))
        self.configure({**self.CFG, "plugins": {"daily_report": {"narrative": False}}})
        self.report._client = FakeAI({"text": "x"})
        await self.send()
        self.assertEqual(self.report._client.calls, [])


class TestTracker(ServiceCase):
    async def report(self):
        recorder, tracker = EpisodeRecorder(), EpisodeTracker()
        for episodes in ([20, 21], [23]):
            ctx = message(f"一瓯春 第{episodes[0]}集 {SHARE}", msg_id=str(episodes[0]))
            ctx.parsed = ParsedResource(is_media=True, title="一瓯春", season=1, episodes=episodes, share_code="s")
            ctx.save_dir_cid = "1"
            ctx.result = {"storage_verified": True}
            ctx.ns("storage")["identity"] = {"cid": "1", "share_code": f"s{episodes[0]}", "title": "一瓯春",
                                             "season": 1, "episodes": episodes}
            self.store.save_task(ctx)
            self.store.finish_transfer(ctx)
            with self.pl.scope(self.pl.cfg):
                await recorder.handle(ctx)
        self.store._conn.execute("DELETE FROM outbox")
        with self.pl.scope(self.pl.cfg):
            await tracker.handle(message())
        row = self.store._conn.execute("SELECT snapshot FROM outbox WHERE kind='notice'").fetchone()
        return json.loads(row[0]) if row else None

    async def test_dismiss_missing_episodes_from_the_reminder(self):
        notice = await self.report()
        digest = messages.short_key("一瓯春|S01")
        self.assertEqual(notice["buttons"], [[["🔕 一瓯春 S01", f"tm:{digest}"]]])
        bot = bot_for(self.service)
        _, options, _ = await bot.callback(f"tm:{digest}", "提醒")
        self.assertEqual(options[0], [("🙈 E22 不用等了", f"tmi:{digest}")])
        text, undo, _ = await bot.callback(f"tmi:{digest}", "提醒")
        self.assertIn("E22 不再提醒", text)
        self.assertIsNone(await self.report())  # 只缺 E22，已忽略：不再提醒
        text, buttons, _ = await bot.callback(f"tmu:{digest}", "提醒")  # 这时最新的提醒里已经没有这部剧
        self.assertIn("照常提醒", text)
        self.assertIsNotNone(await self.report())
        await bot.callback(f"tmo:{digest}", "提醒")
        self.assertIsNone(await self.report())
        state = runtime.PluginState(self.store, "episode_tracker")
        self.assertEqual(state.get("muted:一瓯春|S01"), {"all": True, "episodes": []})


if __name__ == "__main__":
    unittest.main()
