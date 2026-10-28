"""媒体库：聚合查询与来源质量。"""
from __future__ import annotations

import unittest
import json

from reelay.core import library
from reelay.core.contracts import ParsedResource, TaskState
from reelay.plugins.episode_tracker import overdue_episodes

from tests.fakes import FakeAI, ServiceCase, done_task, message


class TestLibraryAndQuality(ServiceCase):
    async def test_library_filters_and_missing_episodes(self):
        done_task(self.store, "1", title="葬送的芙莉莲", media_type="tv", resolution="2160p", genre_ids=(16,),
                  origin_country=("JP",), episodes=(1, 2, 4))
        done_task(self.store, "2", title="电影", media_type="movie", resolution="1080p", episodes=(),
                  origin_country=("US",))
        anime = self.service.library_query(media_type="anime", min_resolution="2160p", origin_country="jp")
        self.assertEqual([item["title"] for item in anime["items"]], ["葬送的芙莉莲"])
        self.assertEqual(anime["items"][0]["seasons"], {"1": [1, 2, 4]})
        self.assertEqual(self.service.library_query(min_resolution="2160p", media_type="movie")["total"], 0)
        with self.assertRaises(ValueError):
            self.service.library_query(min_resolution="8k")
        missing = self.service.missing_episodes("芙莉莲")["items"]
        self.assertEqual(missing[0]["missing"], [3])
        self.assertIn("没有 TMDB 播出信息", missing[0]["note"])

    def test_overdue_episodes_skip_the_first_day(self):
        dates = {7: "2026-10-16", 8: "2026-10-18", 9: "2026-10-19"}
        self.assertEqual(overdue_episodes([7, 8, 9], dates, "2026-10-19"), [{"episode": 7, "days": 3}])

    async def test_source_quality_stats_and_ai_advice(self):
        for i in range(12):
            ad = message(text=f"推广：进群领资源 {i}", msg_id=f"ad{i}", source="@noisy")
            ad.parsed = ParsedResource(is_ad=True)
            ad.state = TaskState.REJECTED
            self.store.save_task(ad)
        done_task(self.store, "1", title="剧")
        for i in range(10):  # 确认前就过期的分享：单独统计，也不作为被拒样例交给 AI
            gone = message(text=f"剧 第{i}集 已过期", msg_id=f"gone{i}", source="@slow")
            gone.parsed = ParsedResource(is_media=True, title="剧", media_type="tv")
            gone.ns("share").update(status="unavailable", note="分享无法访问：链接已过期")
            gone.state = TaskState.SKIPPED
            self.store.save_task(gone)
        stats = {item["source"]: item for item in self.service.source_quality()["sources"]}
        self.assertEqual((stats["@slow"]["unavailable"], stats["@slow"]["unavailable_rate"]), (10, 1.0))
        self.assertIn("分享常在确认前失效", stats["@slow"]["flags"])
        self.assertEqual((stats["@noisy"]["received"], stats["@noisy"]["ad_rate"]), (12, 1.0))
        self.assertIn("广告占一半以上", stats["@noisy"]["flags"])
        self.assertEqual(stats["@bot"]["resolution"], "1080p")
        self.store.add_feedback(task_id="x", source="@noisy", kind="approve", text="资源 剧 第3集", parsed={}, label={})
        self.parser._client = FakeAI({"items": [{"source": "@noisy", "summary": "几乎都是推广", "suggestions": ["加屏蔽词"],
                                                 "add_block_keywords": ["进群", "剧"]}]})
        advice = await self.service.source_quality_advice()
        self.assertEqual(advice["items"][0]["dropped_keywords"], ["剧"])  # 批准过的消息里出现过，不能屏蔽
        self.assertEqual(advice["changes"], [{"section": "来源 @noisy", "kind": "add", "text": "屏蔽关键词：进群"}])
        noisy = next(s for s in advice["sections"]["sources"] if s["bot"] == "@noisy")
        self.assertEqual(noisy["filters"]["block_keywords"], ["进群"])
        payload = json.loads(self.parser._client.calls[0]["messages"][1]["content"])
        self.assertTrue(all("<链接>" in s or "http" not in s for s in payload[0]["rejected_samples"]))
        slow = next(item for item in payload if item["source"] == "@slow")
        self.assertEqual(slow["rejected_samples"], [])
        # 有的模型（如经中转的 Gemini）不包一层 {"items": …}，直接回数组
        self.parser._client = FakeAI([{"source": "@noisy", "summary": "几乎都是推广", "suggestions": [],
                                       "add_block_keywords": ["进群"]}])
        self.assertEqual((await self.service.source_quality_advice())["changes"], advice["changes"])

    def test_rates_are_none_without_data(self):
        self.assertEqual(library.source_quality([], {}), [])


if __name__ == "__main__":
    unittest.main()
