"""结构化过滤与一句话规则。"""
from __future__ import annotations

import unittest
import json

from reelay.core import rules
from reelay.core.config import normalize_config
from reelay.core.contracts import Decision, ParsedResource
from reelay.core.service import ConflictError
from reelay.providers.decision import DefaultDecision

from tests.fakes import FakeAI, ServiceCase, media, message


class TestStructuredFilters(ServiceCase):
    async def decide(self, filters, **fields):
        ctx = message()
        ctx.parsed = ParsedResource(**{**media(), **fields}, share_code="abc123")
        ctx.source_rule = {"approval_mode": "auto", "filters": filters}
        return await DefaultDecision().decide(ctx), ctx

    async def test_allow_deny_and_minimum_resolution(self):
        allow = {"allow": [{"media_type": "tv", "origin_country": "CN"}, {"genre_ids": "16", "origin_country": "JP"}]}
        self.assertEqual((await self.decide(allow))[0], Decision.SAVE)
        self.assertEqual((await self.decide(allow, origin_country=["US"]))[0], Decision.SKIP)
        self.assertEqual((await self.decide(allow, media_type="anime", genre_ids=[16], origin_country=["JP"]))[0],
                         Decision.SAVE)
        self.assertEqual((await self.decide({"deny": [{"genre_ids": "10764,10767"}]}, genre_ids=[10764]))[0],
                         Decision.SKIP)
        self.assertEqual((await self.decide({"min_resolution": "2160p"}))[0], Decision.SKIP)
        decision, ctx = await self.decide({"min_resolution": "1080p"}, resolution=None)
        self.assertEqual(decision, Decision.ASK)
        self.assertTrue(any("清晰度未知" in line for line in ctx.trail))

    def test_invalid_filters_are_rejected_by_config(self):
        for filters in ({"allow": [{"genre_ids": "动画"}]}, {"deny": [{}]}, {"min_resolution": "4k"},
                        {"allow": [{"media_type": "music"}]}, {"unknown": []}):
            with self.subTest(filters=filters), self.assertRaises(ValueError):
                normalize_config({**self.CFG, "defaults": {"save_to": "默认", "filters": filters}})
        rules.validate_filters({"allow": [{"origin_country": "cn,jp"}], "min_resolution": "720p"}, "ok")


class TestCompileRules(ServiceCase):
    DRAFT = {
        "filters": {"must_have": [], "block_keywords": ["广告"], "allow": [
            {"media_type": "tv", "genre_ids": None, "origin_country": "CN"},
            {"media_type": None, "genre_ids": "16", "origin_country": "JP"}],
            "deny": [{"media_type": None, "genre_ids": "10764,10767", "origin_country": None}],
            "min_resolution": "1080p"},
        "release": {"prefer_subtitles": ["chs"], "prefer_audio": ["mandarin"], "hdr": "any", "prefer_sources": []},
        "summary": "只收国产剧和日本动画，不要综艺，至少 1080p",
        "unsupported": [],
    }

    async def test_draft_is_validated_and_returned_as_a_reviewable_diff(self):
        self.parser._client = FakeAI(self.DRAFT)
        before = self.config_path.read_text(encoding="utf-8")
        result = await self.service.compile_rules("只要 1080p 以上的国产剧和日本动画，综艺不要，优先国语中字")
        texts = [c["text"] for c in result["changes"]]
        self.assertIn("屏蔽关键词：广告", texts)
        self.assertIn("只收：类型 tv 且 地区 CN", texts)
        self.assertIn("不收：类型 ID 真人秀(10764)、脱口秀(10767)", texts)
        self.assertIn("最低清晰度：不限 → 1080p", texts)
        self.assertTrue(any(c["section"] == "版本偏好" for c in result["changes"]))
        self.assertEqual(set(result["sections"]), {"defaults", "release"})
        self.assertEqual(self.config_path.read_text(encoding="utf-8"), before)  # 只起草，不写入
        saved = normalize_config({**self.pl.cfg, **result["sections"]})
        self.assertEqual(saved["defaults"]["filters"]["min_resolution"], "1080p")
        self.assertEqual(self.service.llm_usage()["purposes"][0]["purpose"], "compile")

    async def test_invalid_draft_is_sent_back_once_with_the_error(self):
        bad = json.loads(json.dumps(self.DRAFT))
        bad["filters"]["allow"] = [{"media_type": None, "genre_ids": "动画", "origin_country": None}]
        self.parser._client = FakeAI(bad, self.DRAFT)
        result = await self.service.compile_rules("国产剧")
        self.assertIn("未通过配置校验", self.parser._client.calls[1]["messages"][-1]["content"])
        self.assertTrue(result["changes"])
        self.parser._client = FakeAI(bad)
        with self.assertRaises(ValueError):
            await self.service.compile_rules("国产剧")

    async def test_source_scope_writes_explicit_overrides(self):
        cfg = {**self.CFG, "defaults": {"save_to": "默认", "filters": {"block_keywords": ["广告"]}},
               "sources": [{"bot": "@bot"}]}
        self.configure(cfg)
        draft = json.loads(json.dumps(self.DRAFT))
        draft["filters"].update(block_keywords=[], allow=[], deny=[], min_resolution=None)
        draft["release"] = None
        self.parser._client = FakeAI(draft)
        result = await self.service.compile_rules("这个来源不屏蔽任何词", scope="@bot")
        self.assertEqual(result["sections"]["sources"][0]["filters"]["block_keywords"], [])
        self.assertEqual(result["changes"], [{"section": "来源 @bot", "kind": "remove", "text": "屏蔽关键词：广告"}])
        with self.assertRaises(ValueError):
            await self.service.compile_rules("x", scope="@missing")

    async def test_without_ai_the_feature_is_unavailable(self):
        self.parser._client = None
        with self.assertRaises(ConflictError) as raised:
            await self.service.compile_rules("国产剧")
        self.assertIn("未配置 AI", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
