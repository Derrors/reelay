"""仅处理订阅：配置继承、有效订阅白名单，以及正式识别与测试预览的一致性。"""
from __future__ import annotations

import types
import unittest
from unittest.mock import AsyncMock, patch

from reelay.core.config import merge_config, normalize_config, to_file
from reelay.core.contracts import Decision, TaskState

from tests.fakes import FakeAI, ServiceCase, SHARE, message, tv_ctx


class TestSubscriptionFilterConfig(unittest.TestCase):
    def test_default_is_off_and_sources_can_inherit_or_override(self):
        self.assertFalse(normalize_config({})["defaults"]["subscriptions_only"])
        cfg = normalize_config({"defaults": {"subscriptions_only": True}, "sources": [
            {"bot": "@inherit"}, {"bot": "@off", "subscriptions_only": False},
            {"bot": ["@on", "@also_on"], "subscriptions_only": True},
        ]})
        self.assertEqual([merge_config(cfg["defaults"], source)["subscriptions_only"] for source in cfg["sources"]],
                         [True, False, True])
        self.assertNotIn("subscriptions_only", cfg["sources"][0])
        self.assertEqual(normalize_config(to_file(cfg)), cfg)

    def test_only_booleans_are_accepted_in_defaults_and_sources(self):
        for value in (None, 0, 1, "true", "false", [], {}):
            for config in ({"defaults": {"subscriptions_only": value}},
                           {"sources": [{"bot": "@bot", "subscriptions_only": value}]}):
                with self.subTest(config=config), self.assertRaisesRegex(ValueError, "subscriptions_only"):
                    normalize_config(config)


class TestSubscriptionFilter(ServiceCase):
    SUBSCRIPTIONS = [
        {"title": "剧", "season": 1, "tmdbid": 100},
        {"title": "只提醒的剧", "season": 1, "tmdbid": 200, "auto": False},
        {"title": "按片名的剧", "season": 1},
    ]

    async def decide(self, ctx, **rule):
        ctx.source_rule.update({"subscriptions_only": True, **rule})
        return await self.pl.decide(ctx)

    async def test_unsubscribed_is_skipped_for_every_decider_and_approval_mode(self):
        ai_decider = self.registry.get_provider("decision", "llm")
        ai_decider._client = FakeAI({"decision": "save", "reason": "不应调用"})
        for decider in ("default", "subscription", "llm"):
            for mode in ("manual", "auto"):
                with self.subTest(decider=decider, mode=mode):
                    ctx = tv_ctx(tmdbid=999)
                    self.assertEqual(await self.decide(ctx, decision=decider, approval_mode=mode,
                                                       preferences="全部收藏"), Decision.SKIP)
                    self.assertIn("仅处理订阅", ctx.trail[-1])
        self.assertEqual(ai_decider._client.calls, [])

    async def test_subscribed_resources_keep_existing_approval_and_safety_checks(self):
        for decider, mode, expected in (("default", "manual", Decision.ASK), ("default", "auto", Decision.SAVE),
                                        ("subscription", "manual", Decision.SAVE), ("llm", "manual", Decision.SAVE)):
            with self.subTest(decider=decider, mode=mode):
                self.assertEqual(await self.decide(tv_ctx(), decision=decider, approval_mode=mode), expected)
        self.assertEqual(await self.decide(tv_ctx(confidence=0.2)), Decision.ASK)
        fallback = tv_ctx()
        fallback.ns("parse")["fallback"] = True
        self.assertEqual(await self.decide(fallback, approval_mode="auto"), Decision.ASK)
        self.assertEqual(await self.decide(tv_ctx(), filters={"block_keywords": ["剧"]}), Decision.SKIP)

    async def test_wrong_season_unmatched_identity_and_movies_are_skipped(self):
        for ctx in (tv_ctx(season=2), tv_ctx(matched=False), tv_ctx(tmdbid=999),
                    tv_ctx(media_type="movie", season=None, episodes=()), tv_ctx(season=None, episodes=())):
            with self.subTest(parsed=ctx.parsed):
                self.assertEqual(await self.decide(ctx), Decision.SKIP)
        fallback = tv_ctx(matched=False)
        fallback.ns("parse")["fallback"] = True
        self.assertEqual(await self.decide(fallback), Decision.SKIP)
        empty = tv_ctx()
        empty.parsed = None
        self.assertEqual(await self.decide(empty), Decision.SKIP)

    async def test_reminder_only_and_canonical_title_subscriptions_are_still_subscribed(self):
        self.assertEqual(await self.decide(tv_ctx(title="只提醒的剧", tmdbid=200)), Decision.ASK)
        self.assertEqual(await self.decide(tv_ctx(title="按片名的剧", tmdbid=300)), Decision.SAVE)
        self.assertEqual(await self.decide(tv_ctx(title="按片名的剧", tmdbid=300, matched=False)), Decision.SKIP)

    async def test_finished_and_removed_subscriptions_take_effect_on_the_next_decision(self):
        item = self.store.subscriptions()[0]
        self.assertEqual(await self.decide(tv_ctx()), Decision.SAVE)
        self.store.set_subscription_finished(item["id"], {"date": "2026-11-01", "episodes": 30})
        self.assertEqual(await self.decide(tv_ctx()), Decision.SKIP)
        self.store.set_subscription_finished(item["id"], None)
        self.assertEqual(await self.decide(tv_ctx()), Decision.SAVE)
        self.store.remove_subscription(item["id"])
        self.assertEqual(await self.decide(tv_ctx()), Decision.SKIP)

    async def test_unknown_message_season_is_pinned_even_with_auto_approval(self):
        ctx = tv_ctx(season=None)
        self.assertEqual(await self.decide(ctx, approval_mode="auto"), Decision.SAVE)
        self.assertEqual(ctx.parsed.season, 1)
        self.assertTrue(any("转存范围限定为第 1 季" in entry for entry in ctx.trail))

    async def test_multi_season_resources_must_have_every_selected_season_subscribed(self):
        def multi():
            ctx = tv_ctx(season=None, episodes=())
            ctx.ns("share").update(status="ok", pairs=[[1, 3], [2, 3]])
            return ctx

        self.assertEqual(await self.decide(multi(), approval_mode="auto"), Decision.SKIP)
        self.subscribe({"title": "剧", "season": 2, "tmdbid": 100})
        ctx = multi()
        self.assertEqual(await self.decide(ctx, approval_mode="auto"), Decision.SAVE)
        self.assertIsNone(ctx.parsed.season)
        # 消息已明确只取第 1 季时，分享里有其他季不会扩大本次转存范围。
        selected = multi()
        selected.parsed.season = 1
        self.store.remove_subscription(self.store.subscriptions()[-1]["id"])
        self.assertEqual(await self.decide(selected, approval_mode="auto"), Decision.SAVE)

    async def test_off_preserves_unsubscribed_manual_and_auto_behavior(self):
        self.assertEqual(await self.decide(tv_ctx(tmdbid=999), subscriptions_only=False), Decision.ASK)
        self.assertEqual(await self.decide(tv_ctx(tmdbid=999), subscriptions_only=False, approval_mode="auto"),
                         Decision.SAVE)

    def configure_preview(self):
        self.configure({**self.CFG, "defaults": {**self.CFG["defaults"], "parser": "regex", "approval_mode": "auto",
                                                "subscriptions_only": True},
                        "sources": [{"bot": "@all", "subscriptions_only": False}],
                        "plugins": {"tmdb_enrich": {"enabled": False}}})

        async def identify(ctx):
            ctx.ns("tmdb").update(matched=True, tmdbid=100 if ctx.parsed.title == "剧" else 999)
            return True

        self.registry.add_hook(types.SimpleNamespace(name="test_identity", stage="after_parse", order=55, run=identify))

    async def test_preview_and_pipeline_share_the_filter_and_source_override(self):
        self.configure_preview()
        for index, (title, source, decision, state) in enumerate((
                ("其他剧", "@bot", "skip", TaskState.SKIPPED), ("剧", "@bot", "save", TaskState.QUEUED),
                ("其他剧", "@all", "save", TaskState.QUEUED))):
            with self.subTest(title=title, source=source):
                text = f"{title} (2026) S01E03 1080p {SHARE}"
                before = len(self.store.list_tasks())
                preview = await self.service.test_parse(text, [], source)
                self.assertEqual(preview["decision"], decision)
                self.assertEqual(len(self.store.list_tasks()), before)  # 预览不创建任务
                ctx = message(text, urls=[], source=source, msg_id=str(index))
                await self.pl.process_new(ctx)
                saved = self.store.get_task(ctx.task_id)
                self.assertEqual((saved.decision.value, saved.state), (decision, state))

    async def test_ed2k_preview_and_pipeline_use_the_same_subscription_filter(self):
        self.configure_preview()
        storage = self.registry.get_provider("storage", "p115")
        # 只读电驴链接里的文件名与大小；不调用 115，不下载。
        with patch.object(storage, "ready", return_value=True):
            for index, (title, expected) in enumerate((("其他剧", "skip"), ("剧", "save"))):
                with self.subTest(title=title):
                    link = f"ed2k://|file|{title}.2026.S01E03.1080p.mkv|2048|0123456789abcdef0123456789abcdef|/"
                    text = f"{title} (2026) S01E03 1080p\n{link}"
                    preview = await self.service.test_parse(text, [], "@bot")
                    self.assertEqual(preview["decision"], expected)
                    self.assertEqual(len(preview["parsed"]["ed2k"]), 1)
                    self.assertIsNone(preview["parsed"]["share_code"])
                    ctx = message(text, urls=[], msg_id=str(index))
                    await self.pl.process_new(ctx)
                    saved = self.store.get_task(ctx.task_id)
                    self.assertEqual(saved.decision.value, expected)
                    self.assertEqual(saved.state, TaskState.SKIPPED if expected == "skip" else TaskState.QUEUED)

    async def test_unsubscribed_does_not_trigger_forwarding_or_approval_notifications(self):
        self.configure_preview()
        ctx = message(f"其他剧 (2026) S01E03 1080p {SHARE}")
        with patch.object(self.pl, "emit", wraps=self.pl.emit) as emit, \
                patch.object(self.pl, "notify_all", new_callable=AsyncMock) as notify:
            await self.pl.process_new(ctx)
        self.assertEqual(ctx.state, TaskState.SKIPPED)
        self.assertNotIn("on_identified", [call.args[0] for call in emit.call_args_list])
        notify.assert_not_awaited()

    async def test_config_changes_only_affect_newly_received_tasks(self):
        self.configure_preview()
        text = f"其他剧 (2026) S01E03 1080p {SHARE}"
        before = message(text, msg_id="before")
        await self.pl.accept(before)
        cfg = {**self.pl.cfg, "defaults": {**self.pl.cfg["defaults"], "subscriptions_only": False}}
        self.configure(cfg)
        await self.pl.process_received(self.store.get_task(before.task_id))
        self.assertEqual(self.store.get_task(before.task_id).state, TaskState.SKIPPED)
        after = message(text, msg_id="after")
        await self.pl.process_new(after)
        self.assertEqual(self.store.get_task(after.task_id).state, TaskState.QUEUED)

    async def test_unsaved_preview_and_custom_deciders_cannot_bypass_the_filter(self):
        self.configure_preview()
        calls = []

        async def unsafe(ctx):
            calls.append(ctx.task_id)
            return Decision.SAVE

        self.registry.add_provider("decision", types.SimpleNamespace(name="unsafe", decide=unsafe))
        draft = {"bot": "@all", "subscriptions_only": True, "decision": "unsafe", "parser": "regex"}
        text = f"其他剧 (2026) S01E03 1080p {SHARE}"
        result = await self.service.test_parse(text, [], "@all", rule=self.service.draft_source_rule(draft))
        self.assertEqual(result["decision"], "skip")
        self.assertEqual(await self.decide(tv_ctx(tmdbid=999), decision="unsafe"), Decision.SKIP)
        self.assertEqual(calls, [])
        self.assertFalse(self.pl.rule_for("@all")["subscriptions_only"])  # 草案没有写入配置


if __name__ == "__main__":
    unittest.main()
