"""第二步的 AI 辅助能力：剧集订阅放行、同集版本择优、收件箱建议、新来源接入向导。全部本地模拟。"""
from __future__ import annotations

import json
from unittest.mock import patch

from reelay.core import inbox, release, secret_store, subscriptions
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
    CFG = {**Base.CFG, "subscriptions": [
        {"title": "剧", "season": 1, "tmdbid": 100}, {"title": "别的剧", "season": 2, "auto": False}]}

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

    def test_subscription_config_is_validated(self):
        for bad in ({"title": "剧", "tmdbid": "100"}, {"title": "剧", "auto": "yes"}, {"title": "剧", "extra": 1}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                subscriptions.validate({"subscriptions": [bad]})
        with self.assertRaises(ValueError):
            subscriptions.validate({"subscriptions": [{"title": "剧", "tmdbid": 1}, {"title": "另名", "tmdbid": 1}]})

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
        self.assertEqual(self.service.get_config()["subscriptions"],
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
                "seasons": [1], "episodes": [[1, 3]], "stored_episodes": [[1, 1], [1, 2]], "new_episodes": [[1, 3]]}
        self.assertEqual(inbox.action(base, 0.5), "approve")
        self.assertEqual(inbox.rule_text(base, "approve"), "已入库 E01-E02；这是 E03（1080p）")
        self.assertEqual(inbox.action({**base, "new_episodes": []}, 0.5), "reject")
        self.assertEqual(inbox.action({**base, "caps": ["tmdb_unmatched"]}, 0.5), "review")
        self.assertEqual(inbox.action({**base, "versions": {"count": 2, "best": False}}, 0.5), "reject")
        self.assertIn("TMDB", inbox.rule_text({**base, "caps": ["tmdb_unmatched"]}, "review"))
        multi = {**base, "seasons": [1, 2], "episodes": [[1, 3], [2, 1], [2, 2]], "new_episodes": [[2, 1], [2, 2]]}
        self.assertEqual(inbox.rule_text(multi, "approve"), "已入库 S01 E01-E02；这是 S02 E01-E02（1080p）")
        both = {**multi, "new_episodes": [[1, 3], [2, 1]]}
        self.assertEqual(inbox.rule_text(both, "approve"), "已入库 S01 E01-E02；这是 S01–S02 共 2 集（1080p）")

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

    def test_unavailable_shares_are_advised_to_be_ignored(self):
        ctx = tv_ctx(msg_id="1")
        ctx.ns("share").update(status="unavailable", note="分享无法访问：链接已过期")
        ctx.state = TaskState.PENDING_APPROVAL
        self.store.save_task(ctx)
        item = self.service.inbox()["items"][0]
        self.assertEqual((item["advice"]["action"], item["advice"]["text"]),
                         ("reject", "分享无法访问：链接已过期，无法转存"))
        self.assertEqual(item["share"]["status"], "unavailable")

    async def test_ignoring_an_unavailable_share_is_not_a_preference_sample(self):
        gone = tv_ctx(msg_id="1")
        gone.ns("share").update(status="unavailable", note="分享无法访问：链接已过期")
        gone.state = TaskState.PENDING_APPROVAL
        self.store.save_task(gone)
        unwanted = self.save_pending("2", episodes=(4,))
        await self.service.reject(gone.task_id)
        await self.service.reject(unwanted.task_id)
        self.assertEqual(self.store.get_task(gone.task_id).state, TaskState.SKIPPED)
        self.assertEqual([row["task_id"] for row in self.store.feedback_rows()], [unwanted.task_id])

    def test_unavailable_shares_do_not_outrank_usable_versions(self):
        usable = self.save_pending("1", resolution="1080p")
        gone = tv_ctx(msg_id="2", resolution="2160p")
        gone.ns("share").update(status="unavailable", note="分享无法访问：访问码错误")
        gone.state = TaskState.PENDING_APPROVAL
        self.store.save_task(gone)
        items = {item["task_id"]: item for item in self.service.inbox()["items"]}
        self.assertIsNone(items[usable.task_id]["version"])
        self.assertEqual(items[usable.task_id]["advice"]["action"], "approve")
        self.assertEqual(items[gone.task_id]["advice"]["action"], "reject")

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

    async def test_draft_filters_are_checked_against_messages_you_approved(self):
        # 样例全是电影时 AI 可能把「🎬」当必含词，会挡掉这个来源的剧集消息
        for i, text in enumerate((f"🎬 电影 (2026) 2160p {SHARE}", f"📺 剧 S01E03 {SHARE}")):
            past = message(text=text, msg_id=str(i), source="@new_bot")
            self.store.save_task(past)
            self.store.add_feedback(task_id=past.task_id, source="@new_bot", kind="approve", text=text, parsed={}, label={})
        self.parser._client = FakeAI({**self.DRAFT, "must_have": ["🎬"]}, media(title="电影", media_type="movie"))
        result = await self.service.draft_source("@new_bot", [f"🎬 电影 (2026) 2160p {SHARE}"])
        self.assertEqual(result["approved_checked"], 2)
        self.assertEqual([(b["reason"], b["preview"]) for b in result["blocked_approved"]],
                         [("未命中 must_have", "📺 剧 S01E03 <链接>")])
        # 必含词写的是链接域名时也按原始消息（含链接）核对，不会误报
        self.parser._client = FakeAI(self.DRAFT, media(title="电影", media_type="movie"))
        result = await self.service.draft_source("@new_bot", [f"🎬 电影 (2026) 2160p {SHARE}"])
        self.assertEqual(result["blocked_approved"], [])

    async def test_bad_input_and_missing_ai_are_reported(self):
        with self.assertRaises(ValueError):
            await self.service.draft_source("@x", [])
        self.parser._client = None
        with self.assertRaises(ConflictError):
            await self.service.draft_source("@x", ["样例"])




class TestMultiSeasonAndShareProbe(Base):
    """与「跨多季的合集按季转存」「消息没写集数时读取分享」配合：订阅放行、建议、推荐版本与「追这部剧」。"""
    CFG = {**Base.CFG, "subscriptions": [{"title": "剧", "season": 2, "tmdbid": 100}]}

    def probed(self, pairs, msg_id="1", status="ok", **fields):
        """消息没写季集、由 share_probe 读出（季, 集）的剧集。"""
        ctx = tv_ctx(msg_id=msg_id, season=None, episodes=(), **fields)
        seasons = {s for s, _ in pairs}
        ctx.meta["share"] = {"status": status, "season": seasons.pop() if len(seasons) == 1 else None,
                             "episodes": sorted({e for _, e in pairs}), "pairs": [list(p) for p in pairs]}
        return ctx

    def stored(self, pairs, msg_id="s", title="剧"):
        ctx = tv_ctx(msg_id=msg_id, title=title, season=None, episodes=sorted({e for _, e in pairs}))
        ctx.save_dir_cid, ctx.result = "2", {"storage_verified": True}
        ctx.ns("storage").update(manifest={"pairs": [list(p) for p in pairs]}, identity={
            "cid": "2", "share_code": f"s{msg_id}", "title": title, "season": None,
            "episodes": sorted({e for _, e in pairs}), "pairs": [list(p) for p in pairs]})
        ctx.state = TaskState.STORED
        self.store.save_task(ctx)
        self.store.finish_transfer(ctx)
        ctx.state = TaskState.DONE
        self.store.save_task(ctx)

    async def decide(self, ctx, provider=None):
        with self.pl._scope(self.pl.cfg):
            return await (provider or SubscriptionDecision()).decide(ctx)

    async def test_subscription_uses_the_share_season_and_pins_it(self):
        ctx = self.probed([(2, 1), (2, 2)])
        self.assertEqual(await self.decide(ctx), Decision.SAVE)
        self.assertEqual(ctx.parsed.season, 2)  # 转存时只取订阅的这一季
        self.assertTrue(any("记为第 2 季" in line for line in ctx.trail))

    async def test_multi_season_packs_and_unknown_seasons_are_not_released(self):
        multi = self.probed([(1, 1), (2, 1)])
        self.assertEqual(await self.decide(multi), Decision.ASK)
        self.assertIsNone(multi.parsed.season)
        self.assertTrue(any("跨多季（S01–S02 共 2 集）" in line for line in multi.trail))
        unknown = self.probed([(2, 1)], status="error")  # 读取分享出错：不知道是哪一季
        self.assertEqual(await self.decide(unknown), Decision.ASK)
        self.assertTrue(any("无法确认只涉及第 2 季" in line for line in unknown.trail))
        # 只写了集数的消息按第 1 季，与订阅的第 2 季不符
        self.assertEqual(await self.decide(tv_ctx(season=None, episodes=(3,))), Decision.ASK)

    async def test_preferences_veto_withdraws_the_pinned_season(self):
        provider = LLMDecision()
        provider._client = FakeAI({"decision": "skip", "save_to": None, "reason": "不要"})
        ctx = self.probed([(2, 1)])
        ctx.source_rule["preferences"] = "只要 4K"
        self.assertEqual(await self.decide(ctx, provider), Decision.SKIP)
        self.assertIsNone(ctx.parsed.season)
        self.assertNotIn("subscription", ctx.meta)
        # 重新判断时不沿用上次的放行记录（否则会误撤回识别出的季号）
        stale = tv_ctx(season=3, episodes=(1,))
        stale.meta["subscription"] = {"pinned": True}
        self.assertEqual(await self.decide(stale, LLMDecision()), Decision.ASK)
        self.assertEqual(stale.parsed.season, 3)

    async def test_advice_and_versions_follow_share_pairs(self):
        self.stored([(1, 1), (1, 2)])
        first = self.probed([(1, 1), (1, 2), (2, 1)], msg_id="1")
        second = self.probed([(1, 1), (1, 2), (2, 1)], msg_id="2", resolution="2160p")
        other = self.probed([(1, 1), (1, 2)], msg_id="3")
        for ctx in (first, second, other):
            ctx.state = TaskState.PENDING_APPROVAL
            self.store.save_task(ctx)
        items = {item["task_id"]: item for item in self.service.inbox()["items"]}
        self.assertEqual(items[second.task_id]["version"]["count"], 2)  # 同样的季集才算同一内容的不同版本
        self.assertIsNone(items[other.task_id]["version"])
        self.assertEqual(items[second.task_id]["advice"]["text"],
                         "已入库 S01 E01-E02；这是 S02 E01（2160p）；同一集 2 个版本中最符合偏好；在你的订阅里")
        self.assertEqual(items[first.task_id]["advice"]["action"], "reject")
        self.assertEqual(items[other.task_id]["advice"]["action"], "reject")  # 分享里的集都已入库
        self.assertTrue(items[second.task_id]["subscribed"])  # 涉及的第 2 季在订阅里

    async def test_follow_this_show_uses_the_share_season(self):
        single = self.probed([(3, 1)], msg_id="1", tmdbid=7)
        multi = self.probed([(1, 1), (2, 1)], msg_id="2", tmdbid=8)
        for ctx in (single, multi):
            ctx.state = TaskState.PENDING_APPROVAL
            self.store.save_task(ctx)
        result = await self.service.subscribe_from_task(single.task_id)
        self.assertEqual((result["subscription"]["season"], result["pending"]), (3, [single.task_id]))
        with self.assertRaises(ConflictError):
            await self.service.subscribe_from_task(multi.task_id)

    def test_library_groups_multi_season_packs_by_season(self):
        self.stored([(1, 1), (1, 2), (2, 1)])
        item = self.service.library_query()["items"][0]
        self.assertEqual(item["seasons"], {"1": [1, 2], "2": [1]})
