"""AI 提议、事实源校验、人工裁决、裁决回流：偏好目录、裁决样本、示例与历史、通过率建议、
调用记录、转人工前的 AI 兜底、结构化过滤、一句话规则与版本信息。全部使用本地模拟客户端。"""
from __future__ import annotations

import json
import os
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from reelay.core import release, rules, runtime
from reelay.core.config import normalize_config
from reelay.core.contracts import Decision, NeedsManual, ParsedResource, PipelineContext, RawMessage
from reelay.core.pipeline import Pipeline
from reelay.core.registry import discover
from reelay.core.service import ConflictError, ManagementService
from reelay.core.store import Store
from reelay.core.validation import validate_resource
from reelay.plugins.dir_router import DirRouter
from reelay.plugins.organizer import Organizer
from reelay.providers.decision import DefaultDecision, LLMDecision
from reelay.providers.parser import LLMParser
from reelay.providers.storage import P115Storage, _select_video_items

SHARE = "https://115.com/s/abc123?password=abcd"


def media(**change):
    data = {"is_media": True, "is_ad": False, "confidence": 1.0, "media_type": "tv",
            "title": "剧", "year": 2026, "season": 1, "episodes": [3], "genres": [],
            "genre_ids": [18], "origin_country": ["CN"], "resolution": "1080p", "size": "2 GB", "reason": "影视"}
    data.update(change)
    return data


class FakeAI:
    """按顺序返回预置结果（最后一个重复使用）；记录请求，附带 token 用量。"""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []
        self.chat = types.SimpleNamespace(completions=self)

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        item = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(item, Exception):
            raise item
        content = item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)
        message = types.SimpleNamespace(content=content, refusal=None)
        usage = types.SimpleNamespace(prompt_tokens=100, completion_tokens=20)
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=message)], usage=usage)


def message(text=f"剧 (2026) 第3集 1080p {SHARE}", msg_id="1", source="@bot"):
    return PipelineContext(RawMessage(source, msg_id, text, [SHARE]))


class Base(unittest.IsolatedAsyncioTestCase):
    CFG = {"save_dirs": {"剧集": "2", "默认": "3", "动漫": "4", "电影": "5"}, "defaults": {"save_to": "默认"}}

    def setUp(self):
        self.env = patch.dict(os.environ, {"REELAY_LLM_RETRY_DELAY": "0"}, clear=True)
        self.env.start()
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "t.db")
        self.registry = discover()
        self.parser = self.registry.get_provider("parser", "llm")
        self.parser._client = FakeAI(media())
        self.pl = Pipeline(self.registry, self.store, normalize_config(self.CFG))
        self.config_path = Path(self.temp.name) / "config.yaml"
        self.service = ManagementService(self.store, self.pl, config_path=str(self.config_path))
        self.configure(self.CFG)

    def configure(self, cfg):
        """管理端每次读取都会按配置文件重载：测试的配置也要写进文件。"""
        self.config_path.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
        self.pl.apply_config(normalize_config(cfg))

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()
        self.env.stop()

    async def pending(self, msg_id="1", text=None, source="@bot"):
        ctx = message(text or f"剧 (2026) 第{msg_id}集 1080p {SHARE}", msg_id=msg_id, source=source)
        await self.pl.process_new(ctx)
        return self.store.get_task(ctx.task_id)


class TestPreferredDirectory(Base):
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
            await DirRouter().run(ctx)
        return ctx

    async def test_category_no_longer_overrides_the_preferred_directory(self):
        ctx, provider = self.decided("默认")
        with runtime.scope(self.pl.cfg):
            self.assertEqual(await provider.decide(ctx), Decision.SAVE)
        self.assertEqual(self.pl.target_preview(ctx), "默认")  # 审批卡片与管理台显示 AI 选出的目录
        await self.route(ctx)
        self.assertEqual(ctx.save_dir_alias, "默认")
        self.assertNotIn("relative_category", ctx.meta["dir_router"])
        self.assertTrue(any("AI 按偏好选择目录「默认」" in line for line in ctx.trail))

    async def test_preferred_directory_matching_category_root_keeps_subfolder(self):
        ctx, provider = self.decided("动漫")
        with runtime.scope(self.pl.cfg):
            await provider.decide(ctx)
        await self.route(ctx)
        self.assertEqual((ctx.save_dir_alias, ctx.meta["dir_router"]["relative_category"]), ("动漫", "日本番剧"))

    async def test_manual_choice_beats_preferred_directory(self):
        ctx, provider = self.decided("默认")
        with runtime.scope(self.pl.cfg):
            await provider.decide(ctx)
        ctx.ns("dir_router")["pinned"] = "电影"
        await self.route(ctx)
        self.assertEqual(ctx.save_dir_alias, "电影")

    async def test_a_new_decision_drops_the_previous_preferred_directory(self):
        ctx, provider = self.decided("默认")
        with runtime.scope(self.pl.cfg):
            await provider.decide(ctx)
            provider._client = FakeAI({"decision": "ask", "save_to": None, "reason": "拿不准"})
            await provider.decide(ctx)
        self.assertNotIn("preferred", ctx.meta["dir_router"])


class TestFeedbackSamples(Base):
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
        with self.pl._scope(self.pl.cfg):
            await provider.decide(ctx)
        payload = json.loads(provider._client.calls[0]["messages"][1]["content"])
        self.assertEqual([item["decision"] for item in payload["history"]], ["reject", "approve"])
        self.assertEqual(payload["history"][1]["save_to"], "剧集")


class TestAutoApprovalSuggestion(Base):
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


class TestLLMCallLog(Base):
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


class TestAssistBeforeManual(Base):
    def ctx(self, **fields):
        ctx = message()
        ctx.parsed = ParsedResource(**{**media(episodes=[], season=None), **fields}, share_code="abc123")
        ctx.source_rule = {}
        return ctx

    async def select(self, ctx, listing, proposal):
        storage = P115Storage()
        storage._client = FakeAI({"season": None, "episodes": [], "drop": [], "movie_file": None,
                                  "reason": "依据文件名", **proposal})
        with self.pl._scope(self.pl.cfg):
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
        with self.pl._scope(self.pl.cfg), self.assertRaises(NeedsManual):
            await storage.select(ctx, listing)
        self.assertEqual(ctx.meta["assist"]["error"], "invalid")
        self.configure({**self.CFG, "ai": {"assist": False}})
        storage._client = FakeAI({"season": None, "episodes": [], "drop": [], "movie_file": None, "reason": ""})
        with self.pl._scope(self.pl.cfg), self.assertRaises(NeedsManual):
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
            plan = Organizer()._build_plan(p, landed, mapping={"剧 一.mp4": (1, 1), "剧 二.mp4": (1, 2)})
        self.assertEqual([x["target_name"] for x in plan], ["剧 - S01E01.mp4", "剧 - S01E02.mp4"])


class TestStructuredFilters(Base):
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


class TestCompileRules(Base):
    DRAFT = {
        "filters": {"must_have": [], "block_keywords": ["广告"], "allow": [
            {"media_type": "tv", "genre_ids": None, "origin_country": "CN"},
            {"media_type": None, "genre_ids": "16", "origin_country": "JP"}],
            "deny": [{"media_type": None, "genre_ids": "10764,10767", "origin_country": None}],
            "min_resolution": "1080p"},
        "categories": None,
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
        bad["categories"] = [{"media": "tv", "path": "只有一级", "genre_ids": None, "origin_country": None}]
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


class TestReleaseInfo(unittest.IsolatedAsyncioTestCase):
    def test_rule_extraction_reads_explicit_markers_only(self):
        value = release.from_text("某剧 S01E03 2160p WEB-DL H265 HDR10+ DV 内封简繁 国粤双语")
        self.assertEqual(value["subtitles"], ["chs", "cht"])
        self.assertEqual(value["audio"], ["mandarin", "cantonese"])
        self.assertEqual(value["hdr"], ["hdr10plus", "dolby_vision"])
        self.assertEqual((value["source"], value["codec"], value["subtitle_format"]), ("web-dl", "h265", "embedded"))
        self.assertEqual(release.from_text("DVD 合集")["source"], "dvd")
        self.assertEqual(release.from_text("ADVENTURE 1080p")["hdr"], [])

    def test_comparison_follows_preferences_and_skips_unknown(self):
        def v(res, size=None, **rel):
            return {"resolution": res, "size": size, "release": {**release.empty(), **rel}}
        self.assertEqual(release.compare(v("1080p", subtitles=["chs"]), v("2160p", subtitles=["eng"])), 1)
        self.assertEqual(release.compare(v("1080p"), v("2160p", subtitles=["eng"])), -1)  # 字幕未知不参与
        self.assertEqual(release.compare(v("2160p", hdr=["hdr10"]), v("2160p"), {"hdr": "avoid"}), -1)
        self.assertEqual(release.compare(v("1080p", 5), v("1080p", 3)), 1)
        self.assertEqual(release.compare(v("1080p"), v("1080p")), 0)
        self.assertEqual(release.size_bytes("1.5 GB"), int(1.5 * 1024 ** 3))

    async def test_llm_release_is_validated_and_completed_from_text(self):
        parser = LLMParser()
        llm_release = {**release.empty(), "subtitles": ["chs"]}
        parser._client = FakeAI(media(release=llm_release))
        ctx = message(text=f"剧 第3集 2160p HDR 中字 {SHARE}")
        result = await parser.parse(ctx)
        self.assertEqual(result.release["subtitles"], ["chs"])
        self.assertEqual(result.release["hdr"], ["hdr10"])
        schema = parser._client.calls[0]["response_format"]["json_schema"]["schema"]
        self.assertIn("release", schema["required"])
        bad = LLMParser()
        bad._client = FakeAI(media(release={**release.empty(), "hdr": ["8k"]}))
        ctx = message()
        await bad.parse(ctx)
        self.assertTrue(ctx.meta["parse"]["fallback"])

    def test_release_is_optional_for_older_models_and_validated_on_patch(self):
        legacy = media()
        validate_resource(dict(legacy), complete=True)
        with self.assertRaises(ValueError):
            validate_resource({"release": {"audio": ["klingon"]}})
        with self.assertRaises(ValueError):
            normalize_config({"release": {"hdr": "always"}})


class TestLocalEval(Base):
    async def test_export_prefers_corrections_and_run_scores_each_field(self):
        from reelay import eval as evaluation
        first, second = await self.pending("1"), await self.pending("2")
        self.service.patch_parsed(first.task_id, {"title": "规范剧名"})
        await self.service.approve(first.task_id)
        await self.service.approve(second.task_id)
        await self.service.reject((await self.pending("3")).task_id)
        samples = evaluation.export_samples(self.store)
        self.assertEqual([(s["id"], s["kind"]) for s in samples], [(first.task_id, "correct"), (second.task_id, "approve")])
        self.assertEqual(samples[0]["expected"]["title"], "规范剧名")
        self.assertNotIn("115.com", json.dumps(samples, ensure_ascii=False))

        client = FakeAI(media(title="剧"), media(title="剧", episodes=[3]))
        report = await evaluation.run(samples, client=client, model="m", examples=3)
        self.assertEqual(report["fields"]["title"], {"total": 2, "correct": 1, "accuracy": 0.5})
        self.assertEqual(report["mismatches"][0]["field"], "title")
        # 同来源其他样本的修正作为示例，自己的修正不会作为自己的示例
        sizes = {json.loads(call["messages"][-1]["content"])["text"]: len(call["messages"]) for call in client.calls}
        self.assertEqual((sizes[samples[0]["text"]], sizes[samples[1]["text"]]), (2, 4))
        better = {**report, "model": "n", "fields": {**report["fields"], "title": {"total": 2, "correct": 2, "accuracy": 1.0}},
                  "mismatches": []}
        lines = evaluation.compare(report, better)
        self.assertIn("50.0% → 100.0% (+50.0%)", "\n".join(lines))
        self.assertIn("修好 1 处", lines[-1])


class TestStoreHousekeeping(unittest.TestCase):
    def test_old_llm_calls_are_pruned(self):
        store = Store(":memory:")
        store.record_llm_call(purpose="parse", model="m", task_id=None, outcome="ok", error=None,
                              duration_ms=5, prompt_tokens=1, completion_tokens=1)
        self.assertEqual(store.prune_llm_calls(0), 0)
        self.assertEqual(store.prune_llm_calls(float("inf")), 1)
        store.close()


if __name__ == "__main__":
    unittest.main()

