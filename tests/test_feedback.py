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

from tests.fakes import APITimeoutError, FakeAI, ServiceCase, done_task, media, message


class TestPreferenceKeepsTheDirectory(ServiceCase):
    """AI 按偏好判断只决定放不放行：保存位置按来源的「保存到」与二级分类，偏好里写了目录也不改。"""

    async def test_preferences_never_change_where_it_is_saved(self):
        ctx = message()
        ctx.parsed = ParsedResource(**{**media(media_type="anime", genre_ids=[16], origin_country=["JP"])},
                                    share_code="abc123")
        ctx.source_rule = {"preferences": "动画都放默认目录", "approval_mode": "auto"}
        provider = LLMDecision()
        provider._client = FakeAI({"decision": "save", "reason": "偏好"})
        with runtime.scope(self.pl.cfg):
            self.assertEqual(await provider.decide(ctx), Decision.SAVE)
        self.assertEqual(self.pl.target_preview(ctx), "动漫/日本番剧")  # 审批卡片与管理台显示的去向
        with runtime.scope(self.pl.cfg):
            await LibraryRouter().run(ctx)
        self.assertEqual((ctx.save_dir_alias, ctx.meta["library"]["relative_category"]), ("动漫", "日本番剧"))


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
        provider._client = FakeAI({"decision": "ask", "reason": "看记录"})
        ctx = message(msg_id="9")
        ctx.parsed = ParsedResource(**media(), share_code="abc123")
        ctx.source_rule = {"preferences": "只收国产剧", "approval_mode": "auto"}
        with self.pl.scope(self.pl.cfg):
            await provider.decide(ctx)
        payload = json.loads(provider._client.calls[0]["messages"][1]["content"])
        self.assertEqual([item["decision"] for item in payload["history"]], ["reject", "approve"])
        self.assertNotIn("save_to", payload["history"][1])  # 目录不归 AI 管，批准时选的目录也不交给它
        self.assertNotIn("allowed_dirs", payload)


    async def test_rejecting_episodes_already_in_the_library_is_not_a_sample(self):
        """消息里的集都已入库时忽略（重复），不当作不想要这部剧，否则偏好判断会据此交给你确认。"""
        done_task(self.store, "s", title="剧", episodes=(3,))
        duplicate = await self.pending("1")  # 剧 S01E03，已入库
        await self.service.reject(duplicate.task_id)
        self.assertEqual(self.store.feedback_rows(), [])
        self.parser._client = FakeAI(media(episodes=[3, 4]))
        partly_new = await self.pending("2")  # E04 还没入库：忽略仍记为样本
        await self.service.reject(partly_new.task_id)
        self.assertEqual([r["kind"] for r in self.store.feedback_rows()], ["reject"])

    async def test_preference_verdicts_are_written_to_the_trail(self):
        """处理过程里看得到 AI 按偏好判断的结论、目录和理由；AI 不可用或出错时也写明。"""
        async def decide(reply, approval_mode="manual"):
            provider = LLMDecision()
            provider._client = FakeAI(reply) if reply is not None else None
            ctx = message(msg_id="9")
            ctx.parsed = ParsedResource(**media(), share_code="abc123")
            ctx.source_rule = {"preferences": "只收国产剧", "approval_mode": approval_mode}
            with self.pl.scope(self.pl.cfg):
                decision = await provider.decide(ctx)
            return decision, ctx.trail[-1].split(" ", 1)[1]

        self.assertEqual(await decide({"decision": "save", "reason": "国产剧"}, "auto"),
                         (Decision.SAVE, "AI 按偏好判断：转存（国产剧）"))
        self.assertEqual(await decide({"decision": "save", "reason": "长" * 200}),  # 人工确认的来源
                         (Decision.ASK, f"AI 按偏好判断：转存（{'长' * 150}…）；这条本来就要等你确认，仍交给你确认"))
        self.assertEqual(await decide(APITimeoutError("slow")),
                         (Decision.ASK, "按偏好判断时 AI 没有给出结果（APITimeoutError），交给你确认"))
        self.assertEqual(await decide(None), (Decision.ASK, "AI 不可用，没能按偏好判断，交给你确认"))

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

    async def test_sources_sharing_a_card_are_counted_and_switched_together(self):
        """一张卡片上的多个来源共用设置：确认统计合起来算，「改为 AI 处理」改的是整张卡片。"""
        self.configure({**self.CFG, "sources": [{"bot": ["@a", "@b"]}, {"bot": "@c"}],
                        "ai": {"suggest_auto": {"min_decisions": 10}}})
        self.record("@a", "approve", 6)
        self.record("@b", "approve", 6)  # 各自只有 6 次，合起来 12 次
        by_source = {item["source"]: item for item in self.service.insights()["sources"]}
        self.assertEqual(set(by_source), {"@a", "@c"})
        self.assertEqual((by_source["@a"]["bots"], by_source["@a"]["decisions"]), (["@a", "@b"], 12))
        self.assertTrue(by_source["@a"]["suggest_auto"])
        saved = await self.service.enable_auto_approval("@B")
        self.assertEqual(saved["sources"][0], {"bot": ["@a", "@b"], "approval_mode": "auto"})
        # 卡片上的每个来源收到的消息都按这张卡片的设置处理
        self.assertEqual([self.pl.rule_for(bot)["approval_mode"] for bot in ("@A", "@b", "@c")],
                         ["auto", "auto", "manual"])

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
