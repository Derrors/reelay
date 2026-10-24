"""剧集订阅：订阅放行、订阅管理、订阅建议。"""
from __future__ import annotations

import unittest
import json
from unittest.mock import patch

from reelay.control import manage
from reelay.core import messages, secret_store, subscriptions
from reelay.core.contracts import Decision, TaskState
from reelay.core.service import ConflictError
from reelay.providers.decision import LLMDecision, SubscriptionDecision

from tests.fakes import FakeAI, ServiceCase, bot_for, tv_ctx


class TestSubscriptionDecision(ServiceCase):
    SUBSCRIPTIONS = [{"title": "剧", "season": 1, "tmdbid": 100}, {"title": "别的剧", "season": 2, "auto": False}]

    async def decide(self, ctx, provider=None):
        with self.pl.scope(self.pl.cfg):
            return await (provider or SubscriptionDecision()).decide(ctx)

    async def test_new_episodes_of_a_subscribed_season_skip_manual_approval(self):
        ctx = tv_ctx()
        self.assertEqual(await self.decide(ctx), Decision.SAVE)
        self.assertEqual(ctx.meta["subscription"]["tmdbid"], 100)
        self.assertTrue(any("新集自动放行" in line for line in ctx.trail))

    async def test_finished_subscription_no_longer_auto_releases_and_can_be_reopened(self):
        first = self.store.subscriptions()[0]
        self.store.set_subscription_finished(first["id"], {"date": "2026-10-01", "episodes": 30})
        self.assertEqual(await self.decide(tv_ctx()), Decision.ASK)
        service = self.service
        listed = {i["title"]: i for i in service.list_subscriptions()["items"]}
        self.assertEqual((listed["剧"]["finished"]["episodes"], listed["剧"]["category"]), (30, "电视剧"))
        service.reopen_subscription({"title": "剧", "season": 1, "tmdbid": 100})
        self.assertEqual(await self.decide(tv_ctx(msg_id="2")), Decision.SAVE)

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
        stored.ns("storage")["identity"] = {"cid": "2", "share_code": stored.parsed.share_code,
                                            "title": stored.parsed.title, "season": 1, "episodes": [3]}
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

    async def test_subscription_input_is_validated_and_duplicates_are_rejected(self):
        for bad in ({"title": "剧", "tmdbid": "100"}, {"title": "剧", "auto": "yes"}, {"title": " "}, {"season": 1}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                await self.service.add_subscription(bad)
        # 同一 tmdbid 同一季只有一条，片名写法不同也算同一部
        with self.assertRaises(ConflictError):
            await self.service.add_subscription({"title": "另名", "season": 1, "tmdbid": 100})

    def test_request_sentences_are_understood_without_ai(self):
        self.assertEqual(subscriptions.parse_request("我在追《一瓯春》第二季")["title"], "一瓯春")
        self.assertEqual(subscriptions.parse_request("我在追《一瓯春》第二季")["season"], 2)
        self.assertEqual(subscriptions.parse_request("在追 繁花 S01"), {"title": "繁花", "season": 1, "year": None})


class TestSubscriptionService(ServiceCase):
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
        self.assertEqual([(s["title"], s["season"], s["tmdbid"], s["auto"]) for s in self.store.subscriptions()],
                         [("剧", 1, 100, True)])
        self.assertNotIn("subscriptions", self.service.get_config())  # 订阅不在配置里
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
        self.assertEqual({k: result["subscription"][k] for k in ("title", "season", "tmdbid")},
                         {"title": "剧", "season": 1, "tmdbid": 100})
        self.assertEqual(result["pending"], [ctx.task_id])
        movie = tv_ctx(msg_id="9", media_type="movie", episodes=())
        movie.state = TaskState.PENDING_APPROVAL
        self.store.save_task(movie)
        with self.assertRaises(ConflictError):
            await self.service.subscribe_from_task(movie.task_id)

    async def test_rule_only_sources_are_reported_as_inactive(self):
        self.configure({**self.CFG, "sources": [{"bot": "@strict", "decision": "default"}]})
        self.assertEqual(self.service.list_subscriptions()["inactive"], ["@strict"])
        # defaults 只是来源没写时的兜底：来源都写了订阅放行就不提示
        self.configure({**self.CFG, "defaults": {**self.CFG["defaults"], "decision": "default"},
                        "sources": [{"bot": "@a", "decision": "subscription"}]})
        self.assertEqual(self.service.list_subscriptions()["inactive"], [])

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
        with secret_store.override(tmdb_api_key="k"), \
                patch("reelay.core.tmdb.search", return_value=hits), \
                patch("reelay.core.tmdb.tv_details", return_value=details):
            result = await self.service.resolve_subscription("我最近在追一瓯春第二季")
        self.assertEqual(result["query"], {"title": "一瓯春", "season": 2, "year": None})
        self.assertEqual(result["candidates"][0]["tmdbid"], 2)
        self.assertEqual([s["season"] for s in result["candidates"][0]["seasons"]], [1, 2])
        self.parser._client = None
        result = await self.service.resolve_subscription("我在追《繁花》")
        self.assertEqual((result["query"]["title"], result["candidates"]), ("繁花", []))


class TestSubscriptionSuggestions(ServiceCase):
    def notices(self):
        return [json.loads(r[0]) for r in self.store._conn.execute(
            "SELECT snapshot FROM outbox WHERE kind='notice' AND delivery_key LIKE 'notice:subscription_suggestion:%'")]

    async def test_asked_once_on_the_third_approval_then_handled_in_telegram(self):
        tasks = [await self.pending(str(i)) for i in range(1, 5)]
        for task in tasks[:2]:
            await self.service.approve(task.task_id)
        self.assertEqual(self.notices(), [])
        await self.service.approve(tasks[2].task_id)
        await self.service.approve(tasks[3].task_id)  # 第 4 条不再问
        [notice] = self.notices()
        self.assertTrue(notice["html"])
        self.assertIn("你已批准《剧》第 1 季 3 条", notice["text"])
        key = messages.short_key(subscriptions.key({"title": "剧", "season": 1}))
        self.assertEqual(notice["buttons"], [[["🔔 订阅第 1 季", f"ss:{key}"], ["🙅 不用了", f"sd:{key}"]]])
        # /subs 里也能看到
        text, buttons = manage.subs_list(self.service)
        self.assertIn("💡 <b>订阅建议</b>", text)
        self.assertEqual(buttons, [[("🔔 订阅 剧 S01", f"ss:{key}:l"), ("🙅 不用了", f"sd:{key}:l")]])
        bot = bot_for(self.service)
        text, _, _ = await bot.callback(f"ss:{key}", "建议")
        self.assertTrue(text.startswith("建议\n\n🔔 <b>已订阅</b>《剧》第 1 季"))
        self.assertEqual([(s["title"], s["season"]) for s in self.store.subscriptions()], [("剧", 1)])
        self.assertEqual((await bot.callback(f"ss:{key}", "建议"))[2], "这条建议已经处理过了")

    async def test_dismissed_suggestions_stay_hidden(self):
        for i in range(1, 4):
            await self.service.approve((await self.pending(str(i))).task_id)
        key = messages.short_key(subscriptions.key({"title": "剧", "season": 1}))
        text, buttons, _ = await bot_for(self.service).callback(f"sd:{key}:l")
        self.assertTrue(text.startswith("🙅 不再建议订阅《剧》第 1 季"))
        self.assertEqual(self.service.subscription_suggestions(), [])
        self.assertNotIn("订阅建议", text)


if __name__ == "__main__":
    unittest.main()
