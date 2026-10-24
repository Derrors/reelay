"""人工裁决回流：偏好目录、裁决样本与示例、自动转存建议、调用记录、转人工前的 AI 兜底。"""
from __future__ import annotations

import unittest
import json

from reelay.core import runtime
from reelay.core.contracts import Decision, NeedsManual, ParsedResource
from reelay.core.service import ConflictError
from reelay.core.validation import validate_resource
from reelay.plugins.organizer import LibraryRouter, build_plan
from reelay.providers.decision import LLMDecision
from reelay.providers.storage import P115Storage, _select_video_items

from tests.fakes import FakeAI, ServiceCase, media, message


class TestPreferredDirectory(ServiceCase):
    def decided(self, save_to):
        ctx = message()
        ctx.parsed = ParsedResource(**{**media(media_type="anime", genre_ids=[16], origin_country=["JP"])},
                                    share_code="abc123")
        ctx.source_rule = {"preferences": "动画都放默认目录", "approval_mode": "auto"}
        provider = LLMDecision()
        provider._client = FakeAI({"decision": "save", "save_to": save_to, "reason": "偏好"})
        return ctx, provider

    async def route(self, ctx):
        with runtime.scope(self.pl.cfg):
            await LibraryRouter().run(ctx)
        return ctx

    async def test_category_no_longer_overrides_the_preferred_directory(self):
        ctx, provider = self.decided("默认")
        with runtime.scope(self.pl.cfg):
            self.assertEqual(await provider.decide(ctx), Decision.SAVE)
        self.assertEqual(self.pl.target_preview(ctx), "默认")  # 审批卡片与管理台显示 AI 选出的目录
        await self.route(ctx)
        self.assertEqual(ctx.save_dir_alias, "默认")
        self.assertNotIn("relative_category", ctx.meta["library"])
        self.assertTrue(any("AI 按偏好选择目录「默认」" in line for line in ctx.trail))

    async def test_preferred_directory_matching_category_root_keeps_subfolder(self):
        ctx, provider = self.decided("动漫")
        with runtime.scope(self.pl.cfg):
            await provider.decide(ctx)
        await self.route(ctx)
        self.assertEqual((ctx.save_dir_alias, ctx.meta["library"]["relative_category"]), ("动漫", "日本番剧"))

    async def test_manual_choice_beats_preferred_directory(self):
        ctx, provider = self.decided("默认")
        with runtime.scope(self.pl.cfg):
            await provider.decide(ctx)
        ctx.ns("library")["pinned"] = "电影"
        await self.route(ctx)
        self.assertEqual(ctx.save_dir_alias, "电影")

    async def test_a_new_decision_drops_the_previous_preferred_directory(self):
        ctx, provider = self.decided("默认")
        with runtime.scope(self.pl.cfg):
            await provider.decide(ctx)
            provider._client = FakeAI({"decision": "ask", "save_to": None, "reason": "拿不准"})
            await provider.decide(ctx)
        self.assertNotIn("preferred", ctx.meta["library"])


class TestFeedbackSamples(ServiceCase):
    async def test_approve_reject_and_correction_are_recorded_without_links(self):
        first, second = await self.pending("1"), await self.pending("2")
        await self.service.approve(first.task_id, save_dir_alias="剧集")
        await self.service.reject(second.task_id)
        third = await self.pending("3")
        self.service.patch_parsed(third.task_id, {"title": "正确的剧名", "season": 2})
        rows = self.store.feedback_rows()
        self.assertEqual([r["kind"] for r in rows], ["approve", "reject", "correct"])
        self.assertEqual(rows[0]["label"]["save_dir_alias"], "剧集")
        self.assertNotIn("115.com", rows[0]["text"])
        self.assertNotIn("abcd", rows[0]["text"])
        self.assertNotIn("share_code", rows[0]["parsed"])
        correct = rows[2]["label"]
        self.assertEqual(correct["changes"]["title"], ["剧", "正确的剧名"])
        self.assertEqual(correct["result"]["season"], 2)
        validate_resource(dict(correct["result"]), complete=True)  # 可直接作为示例与评测答案

    async def test_failed_transitions_and_unchanged_patches_record_nothing(self):
        ctx = await self.pending()
        await self.service.approve(ctx.task_id)
        with self.assertRaises(ConflictError):
            await self.service.approve(ctx.task_id)
        with self.assertRaises(ConflictError):
            await self.service.reject(ctx.task_id)
        self.assertEqual(len(self.store.feedback_rows()), 1)

    async def test_corrections_become_examples_for_the_same_source_only(self):
        ctx = await self.pending("1")
        self.service.patch_parsed(ctx.task_id, {"title": "规范剧名"})
        self.parser._client = FakeAI(media())
        await self.pending("2")
        messages = self.parser._client.calls[-1]["messages"]
        self.assertEqual([m["role"] for m in messages], ["system", "user", "assistant", "user"])
        self.assertIn("人工修正", messages[0]["content"])
        self.assertEqual(json.loads(messages[2]["content"])["title"], "规范剧名")
        self.assertNotIn("115.com", messages[1]["content"])
        await self.pending("3", source="@other")
        self.assertEqual(len(self.parser._client.calls[-1]["messages"]), 2)

    async def test_examples_can_be_disabled(self):
        ctx = await self.pending("1")
        self.service.patch_parsed(ctx.task_id, {"title": "规范剧名"})
        self.configure({**self.CFG, "ai": {"examples": 0}})
        await self.pending("2")
        self.assertEqual(len(self.parser._client.calls[-1]["messages"]), 2)

    async def test_preference_decision_receives_recent_decisions(self):
        approved, rejected = await self.pending("1"), await self.pending("2")
        await self.service.approve(approved.task_id, save_dir_alias="剧集")
        await self.service.reject(rejected.task_id)
        provider = LLMDecision()
        provider._client = FakeAI({"decision": "ask", "save_to": None, "reason": "看记录"})
        ctx = message(msg_id="9")
        ctx.parsed = ParsedResource(**media(), share_code="abc123")
        ctx.source_rule = {"preferences": "只收国产剧", "approval_mode": "auto"}
        with self.pl.scope(self.pl.cfg):
            await provider.decide(ctx)
        payload = json.loads(provider._client.calls[0]["messages"][1]["content"])
        self.assertEqual([item["decision"] for item in payload["history"]], ["reject", "approve"])
        self.assertEqual(payload["history"][1]["save_to"], "剧集")


class TestAutoApprovalSuggestion(ServiceCase):
    def record(self, source, kind, count, start=0):
        for i in range(count):
            self.store.add_feedback(task_id=f"{source}-{start + i}", source=source, kind=kind, text="t",
                                    parsed={}, label={})

    async def test_suggests_auto_only_with_enough_high_rate_decisions(self):
        cfg = {**self.CFG, "sources": [{"bot": "@good"}, {"bot": "@mixed"}, {"bot": "@auto", "approval_mode": "auto"}],
               "ai": {"suggest_auto": {"min_decisions": 10}}}
        self.configure(cfg)
        self.record("@good", "approve", 19)
        self.record("@good", "reject", 1, start=19)
        self.record("@mixed", "approve", 6)
        self.record("@mixed", "reject", 6, start=6)
        self.record("@auto", "approve", 30)
        self.record("@new", "approve", 3)
        by_source = {item["source"]: item for item in self.service.insights()["sources"]}
        self.assertTrue(by_source["@good"]["suggest_auto"])
        self.assertIn("95%", by_source["@good"]["reason"])
        self.assertFalse(by_source["@mixed"]["suggest_auto"])
        self.assertFalse(by_source["@auto"]["suggest_auto"])
        self.assertFalse(by_source["@new"]["suggest_auto"])
        saved = await self.service.enable_auto_approval("@GOOD")
        self.assertEqual(next(s for s in saved["sources"] if s["bot"] == "@good")["approval_mode"], "auto")
        saved = await self.service.enable_auto_approval("@new")
        self.assertIn({"bot": "@new", "approval_mode": "auto"}, saved["sources"])

    async def test_frequent_corrections_block_the_suggestion(self):
        cfg = {**self.CFG, "ai": {"suggest_auto": {"min_decisions": 10}}}
        self.configure(cfg)
        self.record("@bot", "approve", 10)
        for i in range(3):
            self.store.add_feedback(task_id=f"@bot-{i}", source="@bot", kind="correct", text="t", parsed={},
                                    label={"changes": {}})
        item = next(s for s in self.service.insights()["sources"] if s["source"] == "@bot")
        self.assertFalse(item["suggest_auto"])
        self.assertIn("修正识别", item["reason"])


class TestLLMCallLog(ServiceCase):
    async def test_every_request_is_recorded_with_purpose_tokens_and_outcome(self):
        class APITimeoutError(Exception):
            pass

        self.parser._client = FakeAI(APITimeoutError(), "not json", media())
        await self.pending("1")
        usage = self.service.llm_usage()
        parse = next(p for p in usage["purposes"] if p["purpose"] == "parse")
        self.assertEqual((parse["calls"], parse["ok"], parse["invalid"], parse["error"]), (3, 1, 1, 1))
        self.assertEqual(parse["prompt_tokens"], 200)  # 失败的请求没有用量
        self.assertEqual(usage["total"]["calls"], 3)


class TestAssistBeforeManual(ServiceCase):
    def ctx(self, **fields):
        ctx = message()
        ctx.parsed = ParsedResource(**{**media(episodes=[], season=None), **fields}, share_code="abc123")
        ctx.source_rule = {}
        return ctx

    async def select(self, ctx, listing, proposal):
        storage = P115Storage()
        storage._client = FakeAI({"season": None, "episodes": [], "drop": [], "movie_file": None,
                                  "reason": "依据文件名", **proposal})
        with self.pl.scope(self.pl.cfg):
            return await storage.select(ctx, listing), storage

    async def test_unrecognised_filenames_are_mapped_by_ai_and_validated(self):
        listing = [{"id": "1", "name": "剧 一.mp4"}, {"id": "2", "name": "剧 二.mp4"}, {"id": "3", "name": "剧.E03.mp4"}]
        ctx = self.ctx()
        selection, storage = await self.select(ctx, listing, {"episodes": [
            {"name": "剧 一.mp4", "season": 1, "episode": 1}, {"name": "剧 二.mp4", "season": None, "episode": 2}]})
        self.assertEqual(selection["episodes"], [1, 2, 3])
        self.assertTrue(ctx.meta["assist"]["accepted"])
        payload = json.loads(storage._client.calls[0]["messages"][1]["content"])
        self.assertIn("无法从文件名识别集号", payload["problem"])
        self.assertNotIn("115.com", payload["message"])
        self.assertEqual(self.service.llm_usage()["purposes"][0]["purpose"], "assist")

    async def test_proposals_that_contradict_filenames_still_need_manual(self):
        listing = [{"id": "1", "name": "剧 一.mp4"}, {"id": "3", "name": "剧.E03.mp4"}]
        ctx = self.ctx()
        with self.assertRaises(NeedsManual) as raised:
            await self.select(ctx, listing, {"episodes": [
                {"name": "剧 一.mp4", "season": 1, "episode": 1}, {"name": "剧.E03.mp4", "season": 1, "episode": 4}]})
        self.assertIn("AI 方案未通过校验", str(raised.exception))
        self.assertFalse(ctx.meta["assist"]["accepted"])

    async def test_duplicate_versions_keep_the_sharper_file_only(self):
        listing = [{"id": "a", "name": "剧.E01.1080p.mkv"}, {"id": "b", "name": "剧.E01.2160p.mkv"}]
        selection, _ = await self.select(self.ctx(), listing, {"drop": ["剧.E01.1080p.mkv"]})
        self.assertEqual([x["id"] for x in selection["files"]], ["b"])
        with self.assertRaises(NeedsManual):
            await self.select(self.ctx(), listing, {"drop": ["剧.E01.2160p.mkv"]})

    async def test_movie_candidates_and_multi_season_shares(self):
        movie = self.ctx(media_type="movie", episodes=[], resolution="2160p")
        listing = [{"id": "a", "name": "Film.2160p.mkv", "size": 9000}, {"id": "b", "name": "Film.1080p.mkv", "size": 8000}]
        selection, _ = await self.select(movie, listing, {"movie_file": "Film.2160p.mkv"})
        self.assertEqual([x["id"] for x in selection["files"]], ["a"])
        with self.assertRaises(NeedsManual):
            await self.select(self.ctx(media_type="movie", episodes=[]), [
                {"id": "a", "name": "Film.CD1.mkv", "size": 9000}, {"id": "b", "name": "Film.CD2.mkv", "size": 8000}],
                {"movie_file": "Film.CD1.mkv"})
        # 跨多季的合集规则自己就能逐季转存，不调用 AI，AI 给的季也不会让它只转一季
        seasons = [{"id": "1", "name": "S01E01.mkv"}, {"id": "2", "name": "S02E01.mkv"}]
        selection, storage = await self.select(self.ctx(), seasons, {"season": 2})
        self.assertEqual(([x["id"] for x in selection["files"]], storage._client.calls), (["1", "2"], []))
        # 消息只写了集数、按消息取集时规则无法确定是哪一季：AI 按消息选季
        scoped = self.ctx(episodes=[1])
        scoped.source_rule = {"episodes_from": "message"}
        selection, _ = await self.select(scoped, seasons, {"season": 2})
        self.assertEqual(([x["id"] for x in selection["files"]], selection["pairs"]), (["2"], [[2, 1]]))
        # 有文件不带季号时，AI 补上季号后逐季转存
        bare = [*seasons, {"id": "3", "name": "第2集.mkv"}]
        selection, _ = await self.select(self.ctx(), bare, {"episodes": [{"name": "第2集.mkv", "season": 2, "episode": 2}]})
        self.assertEqual(selection["pairs"], [[1, 1], [2, 1], [2, 2]])

    async def test_ai_failures_and_disabled_assist_fall_back_to_manual(self):
        listing = [{"id": "1", "name": "剧 一.mp4"}]
        storage = P115Storage()
        storage._client = FakeAI("not json")
        ctx = self.ctx()
        with self.pl.scope(self.pl.cfg), self.assertRaises(NeedsManual):
            await storage.select(ctx, listing)
        self.assertEqual(ctx.meta["assist"]["error"], "invalid")
        self.configure({**self.CFG, "ai": {"assist": False}})
        storage._client = FakeAI({"season": None, "episodes": [], "drop": [], "movie_file": None, "reason": ""})
        with self.pl.scope(self.pl.cfg), self.assertRaises(NeedsManual):
            await storage.select(self.ctx(), listing)
        self.assertEqual(storage._client.calls, [])

    def test_unknown_files_in_a_proposal_are_rejected(self):
        with self.assertRaises(NeedsManual):
            _select_video_items([{"id": "1", "name": "a.mp4"}], ParsedResource(media_type="tv"),
                                hints={"episodes": {"b.mp4": (1, 1)}})

    def test_organizer_reuses_the_validated_manifest_mapping(self):
        p = ParsedResource(is_media=True, media_type="tv", title="剧", season=1, episodes=[1, 2])
        landed = [{"id": "1", "name": "剧 一.mp4"}, {"id": "2", "name": "剧 二.mp4"}]
        with runtime.scope(self.pl.cfg):
            plan = build_plan(p, landed, mapping={"剧 一.mp4": (1, 1), "剧 二.mp4": (1, 2)})
        self.assertEqual([x["target_name"] for x in plan], ["剧 - S01E01 - 第 1 集.mp4", "剧 - S01E02 - 第 2 集.mp4"])


if __name__ == "__main__":
    unittest.main()
