"""第二步的 AI 辅助能力：剧集订阅放行、同集版本择优、收件箱建议、新来源接入向导。全部本地模拟。"""
from __future__ import annotations

import json
import os
from unittest.mock import patch

from reelay.core import inbox, release, subscriptions
from reelay.core.contracts import Decision, ParsedResource, TaskState
from reelay.core.service import ConflictError
from reelay.providers.decision import LLMDecision, SubscriptionDecision

from tests.test_ai_feedback import SHARE, Base, FakeAI, media, message


def tv_ctx(title="剧", tmdbid=100, season=1, episodes=(3,), matched=True, msg_id="1", **fields):
    ctx = message(msg_id=msg_id)
    ctx.parsed = ParsedResource(**{**media(title=title, season=season, episodes=list(episodes)), **fields},
                                share_code="abc123")
    ctx.parsed.raw_fields["tmdbid"] = tmdbid
    ctx.meta["tmdb"] = {"matched": matched, "tmdbid": tmdbid} if matched else {"matched": False}
    ctx.source_rule = {"approval_mode": "manual"}
    return ctx


class TestSubscriptionDecision(Base):
    CFG = {**Base.CFG, "episode_tracker": {"subscriptions": [
        {"title": "剧", "season": 1, "tmdbid": 100}, {"title": "别的剧", "season": 2, "auto": False}]}}

    async def decide(self, ctx, provider=None):
        with self.pl._scope(self.pl.cfg):
            return await (provider or SubscriptionDecision()).decide(ctx)

    async def test_new_episodes_of_a_subscribed_season_skip_manual_approval(self):
        ctx = tv_ctx()
        self.assertEqual(await self.decide(ctx), Decision.SAVE)
        self.assertEqual(ctx.meta["subscription"]["tmdbid"], 100)
        self.assertTrue(any("新集自动放行" in line for line in ctx.trail))

    async def test_gates_still_apply(self):
        cases = {
            "其他季": tv_ctx(season=2),
            "TMDB 未匹配": tv_ctx(matched=False),
            "其他条目": tv_ctx(tmdbid=999),
            "置信度低": tv_ctx(confidence=0.3),
            "只提醒不放行": tv_ctx(title="别的剧", tmdbid=None, season=2),
        }
        fallback = tv_ctx()
        fallback.ns("parse")["fallback"] = True
        cases["降级"] = fallback
        for name, ctx in cases.items():
            with self.subTest(name):
                self.assertEqual(await self.decide(ctx), Decision.ASK)
        self.assertEqual(await self.decide(tv_ctx(is_ad=True)), Decision.SKIP)

    async def test_already_stored_episodes_are_not_auto_released(self):
        stored = tv_ctx(episodes=(3,))
        stored.state = TaskState.STORED
        stored.save_dir_cid = "2"
        stored.result = {"storage_verified": True}
        self.store.save_task(stored)
        self.store.finish_transfer(stored)
        self.assertEqual(await self.decide(tv_ctx(msg_id="2", episodes=(3,))), Decision.ASK)
        self.assertEqual(await self.decide(tv_ctx(msg_id="3", episodes=(3, 4))), Decision.SAVE)

    async def test_preferences_can_still_veto_a_subscription(self):
        provider = LLMDecision()
        provider._client = FakeAI({"decision": "skip", "save_to": None, "reason": "不要 1080p"})
        ctx = tv_ctx()
        ctx.source_rule["preferences"] = "只要 4K"
        self.assertEqual(await self.decide(ctx, provider), Decision.SKIP)

    def test_subscription_config_is_validated(self):
        for bad in ({"title": "剧", "tmdbid": "100"}, {"title": "剧", "auto": "yes"}, {"title": "剧", "extra": 1}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                subscriptions.validate({"episode_tracker": {"subscriptions": [bad]}})
        with self.assertRaises(ValueError):
            subscriptions.validate({"episode_tracker": {"subscriptions": [{"title": "剧", "tmdbid": 1},
                                                                           {"title": "另名", "tmdbid": 1}]}})

    def test_request_sentences_are_understood_without_ai(self):
        self.assertEqual(subscriptions.parse_request("我在追《一瓯春》第二季")["title"], "一瓯春")
        self.assertEqual(subscriptions.parse_request("我在追《一瓯春》第二季")["season"], 2)
        self.assertEqual(subscriptions.parse_request("在追 繁花 S01"), {"title": "繁花", "season": 1, "year": None})


class TestSubscriptionService(Base):
    async def pending_tv(self, msg_id, tmdbid=100, title="剧", season=1):
        ctx = tv_ctx(msg_id=msg_id, tmdbid=tmdbid, title=title, season=season, episodes=(int(msg_id),))
        ctx.state = TaskState.PENDING_APPROVAL
        self.store.save_task(ctx)
        return ctx

    async def test_add_returns_matching_pending_tasks_and_rejects_duplicates(self):
        a, b = await self.pending_tv("1"), await self.pending_tv("2")
        await self.pending_tv("3", tmdbid=7)
        result = await self.service.add_subscription({"title": "剧", "season": 1, "tmdbid": 100})
        self.assertEqual(sorted(result["pending"]), sorted([a.task_id, b.task_id]))
        self.assertEqual(self.service.get_config()["episode_tracker"]["subscriptions"],
                         [{"title": "剧", "season": 1, "tmdbid": 100}])
        with self.assertRaises(ConflictError):
            await self.service.add_subscription({"title": "剧 ", "season": 1, "tmdbid": 100})
        listed = self.service.list_subscriptions()
        self.assertEqual(listed["items"][0]["title"], "剧")
        self.assertEqual(listed["inactive"], [])
        await self.service.remove_subscription({"title": "剧", "season": 1, "tmdbid": 100})
        self.assertEqual(self.service.list_subscriptions()["items"], [])

    async def test_subscribe_from_inbox_uses_the_tasks_tmdb_entry(self):
        ctx = await self.pending_tv("1")
        result = await self.service.subscribe_from_task(ctx.task_id)
        self.assertEqual(result["subscription"], {"title": "剧", "season": 1, "tmdbid": 100})
        self.assertEqual(result["pending"], [ctx.task_id])
        movie = tv_ctx(msg_id="9", media_type="movie", episodes=())
        movie.state = TaskState.PENDING_APPROVAL
        self.store.save_task(movie)
        with self.assertRaises(ConflictError):
            await self.service.subscribe_from_task(movie.task_id)

    async def test_rule_only_sources_are_reported_as_inactive(self):
        self.configure({**self.CFG, "sources": [{"bot": "@strict", "decision": "default"}]})
        self.assertEqual(self.service.list_subscriptions()["inactive"], ["@strict"])

    async def test_suggestions_come_from_repeated_approvals(self):
        for i in range(1, 4):
            ctx = await self.pending_tv(str(i), title="繁花", tmdbid=55)
            await self.service.approve(ctx.task_id)
        suggestion = self.service.list_subscriptions()["suggestions"][0]
        self.assertEqual((suggestion["title"], suggestion["tmdbid"], suggestion["count"]), ("繁花", 55, 3))
        self.assertIn("你已批准《繁花》第 1 季 3 条（E01-E03），都是 1080p", suggestion["text"])
        await self.service.add_subscription({"title": "繁花", "season": 1, "tmdbid": 55})
        self.assertEqual(self.service.list_subscriptions()["suggestions"], [])

    async def test_resolve_maps_a_sentence_to_tmdb_candidates(self):
        self.parser._client = FakeAI({"title": "一瓯春", "season": 2, "year": None})
        hits = [{"id": 1, "name": "完全不同", "first_air_date": "2020-01-01"},
                {"id": 2, "name": "一瓯春", "first_air_date": "2025-01-01", "poster_path": "/p.jpg"}]
        details = {"seasons": [{"season": 0, "episodes": 3}, {"season": 1, "episodes": 36}, {"season": 2, "episodes": 30}]}
        with patch.dict(os.environ, {"REELAY_TMDB_API_KEY": "k"}), \
                patch("reelay.core.tmdb.search", return_value=hits), \
                patch("reelay.core.tmdb.tv_details", return_value=details):
            result = await self.service.resolve_subscription("我最近在追一瓯春第二季")
        self.assertEqual(result["query"], {"title": "一瓯春", "season": 2, "year": None})
        self.assertEqual(result["candidates"][0]["tmdbid"], 2)
        self.assertEqual([s["season"] for s in result["candidates"][0]["seasons"]], [1, 2])
        self.parser._client = None
        result = await self.service.resolve_subscription("我在追《繁花》")
        self.assertEqual((result["query"]["title"], result["candidates"]), ("繁花", []))


class TestVersionsAndAdvice(Base):
    def save_pending(self, msg_id, **fields):
        ctx = tv_ctx(msg_id=msg_id, **fields)
        ctx.state = TaskState.PENDING_APPROVAL
        self.store.save_task(ctx)
        return ctx

    def test_the_preferred_version_of_the_same_episode_is_recommended(self):
        plain = self.save_pending("1", resolution="2160p")
        subbed = self.save_pending("2", resolution="1080p", release={**release.empty(), "subtitles": ["chs"]})
        other = self.save_pending("3", episodes=(4,))
        items = {item["task_id"]: item for item in self.service.inbox()["items"]}
        self.assertEqual(items[subbed.task_id]["version"], {"count": 2, "best": True, "best_task_id": subbed.task_id})
        self.assertFalse(items[plain.task_id]["version"]["best"])
        self.assertIsNone(items[other.task_id]["version"])
        self.assertEqual(items[subbed.task_id]["release_tags"], ["简体中字"])
        self.assertEqual(items[plain.task_id]["advice"]["action"], "reject")
        self.assertEqual(items[subbed.task_id]["advice"]["action"], "approve")
        # 不偏好字幕时清晰度优先
        self.configure({**self.CFG, "release": {"prefer_subtitles": []}})
        items = {item["task_id"]: item for item in self.service.inbox()["items"]}
        self.assertTrue(items[plain.task_id]["version"]["best"])

    def test_rule_actions_cover_stored_unreliable_and_new_items(self):
        base = {"fallback": False, "caps": [], "confidence": 1.0, "version": "1080p", "subscribed": False,
                "episodes": [3], "stored_episodes": [1, 2], "new_episodes": [3]}
        self.assertEqual(inbox.action(base, 0.5), "approve")
        self.assertEqual(inbox.rule_text(base, "approve"), "已入库 E01-E02；这是 E03 的 1080p 版")
        self.assertEqual(inbox.action({**base, "new_episodes": []}, 0.5), "reject")
        self.assertEqual(inbox.action({**base, "caps": ["tmdb_unmatched"]}, 0.5), "review")
        self.assertEqual(inbox.action({**base, "versions": {"count": 2, "best": False}}, 0.5), "reject")
        self.assertIn("TMDB", inbox.rule_text({**base, "caps": ["tmdb_unmatched"]}, "review"))

    async def test_ai_phrasing_is_cached_until_the_facts_change_and_advice_is_recorded(self):
        ctx = self.save_pending("1")
        item = self.service.inbox()["items"][0]
        self.assertEqual((item["advice"]["action"], item["advice"]["source"]), ("approve", "rule"))
        self.parser._client = FakeAI({"items": [{"id": ctx.task_id, "text": "新的一集，库里还没有"}]})
        result = await self.service.phrase_advice([ctx.task_id, "missing"])
        self.assertEqual(result[ctx.task_id]["text"], "新的一集，库里还没有")
        payload = json.loads(self.parser._client.calls[0]["messages"][1]["content"])
        self.assertEqual(payload[0]["action"], "approve")
        self.assertEqual(self.service.inbox()["items"][0]["advice"]["source"], "ai")
        self.assertEqual(await self.service.phrase_advice([ctx.task_id]), {})  # 已缓存，不再调用
        self.assertEqual(len(self.parser._client.calls), 1)
        self.save_pending("2", resolution="2160p")  # 出现更好的版本：事实变化，回到规则理由
        advice = next(i for i in self.service.inbox()["items"] if i["task_id"] == ctx.task_id)["advice"]
        self.assertEqual((advice["action"], advice["source"]), ("reject", "rule"))
        await self.service.reject(ctx.task_id)
        self.assertEqual(self.store.feedback_rows()[-1]["advice"], "reject")
        self.assertEqual(self.store.decision_stats(30)["@bot"]["followed"], 1)

    async def test_advice_can_be_turned_off(self):
        self.configure({**self.CFG, "ai": {"advice": False}})
        self.save_pending("1")
        self.assertIsNone(self.service.inbox()["items"][0]["advice"])
        self.assertEqual(await self.service.phrase_advice(["x"]), {})


class TestSourceDraft(Base):
    DRAFT = {"approval_mode": "manual", "parser": "llm", "save_to": "剧集", "episodes_from": "share",
             "must_have": ["115.com"], "block_keywords": ["招商"], "summary": "剧集更新频道",
             "notes": ["偶尔有合集，先人工确认"]}

    async def test_draft_is_validated_and_tried_on_every_sample(self):
        self.parser._client = FakeAI(self.DRAFT, media(title="剧"), media(is_media=False, title=None, media_type=None))
        result = await self.service.draft_source("@new_bot", [f"剧 第3集 {SHARE}", "招商：投放广告 联系 @x", " "])
        self.assertEqual(result["draft"], {"bot": "@new_bot", "approval_mode": "manual", "parser": "llm",
                                           "save_to": "剧集", "episodes_from": "share",
                                           "filters": {"must_have": ["115.com"], "block_keywords": ["招商"]}})
        self.assertEqual([r["decision"] for r in result["results"]], ["ask", "skip"])
        self.assertEqual(result["results"][0]["title"], "剧")
        self.assertFalse(result["exists"])
        self.assertNotIn("sources", json.loads(self.config_path.read_text(encoding="utf-8")))  # 不写入配置
        purposes = {p["purpose"] for p in self.service.llm_usage()["purposes"]}
        self.assertEqual(purposes, {"source_draft", "parse"})

    async def test_bad_input_and_missing_ai_are_reported(self):
        with self.assertRaises(ValueError):
            await self.service.draft_source("@x", [])
        self.parser._client = None
        with self.assertRaises(ConflictError):
            await self.service.draft_source("@x", ["样例"])


