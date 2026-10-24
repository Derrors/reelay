"""识别规则：来源规则、通用规则、115 链接、接入向导的规则草稿与预览。"""
from __future__ import annotations

import asyncio
import json
import types
import unittest
from unittest.mock import patch

from reelay.core import parse_rules
from reelay.core.config import normalize_config
from reelay.core.parse_rules import parse_115_url, regex_fields
from reelay.core.service import ConflictError
from reelay.main import _serve_fetch
from reelay.providers.parser import _regex_parse

from tests.fakes import FakeAI, SHARE, ServiceCase, media, message


# 这个来源的消息格式：第一行是频道名，片名在「作品：」后面，集数写成「第5、6期」，通用规则都认不出
POST = f"【趣看影视】每日更新\n作品：小城良方 (2026)\n更新：第5、6期\n4K 高码 1.5GB\n{SHARE}"


RULES = {"title": r"^作品：(.+?)(?:\s*\(\d{4}\))?$", "episodes": r"(\d+)[、期]"}


class TestRules(unittest.TestCase):
    def test_fields_come_from_the_rules_and_fall_back_to_generic_ones(self):
        generic = regex_fields(POST)
        self.assertEqual((generic["title"], generic["episodes"]), ("趣看影视", []))  # 通用规则认成频道名
        fields = regex_fields(POST, RULES)
        self.assertEqual((fields["title"], fields["episodes"], fields["from_rules"]),
                         ("小城良方", [5, 6], ["title", "episodes"]))
        self.assertEqual((fields["year"], fields["size"], fields["media_type"]), (2026, "1.5 GB", "tv"))
        # 规则在这条消息上没匹配到时沿用通用规则
        self.assertEqual(regex_fields("剧 (2026) 第3集", RULES)["title"], "剧")

    def test_episode_ranges_and_numbers(self):
        extract = parse_rules.extract
        self.assertEqual(extract({"episodes": r"更新至(?P<end>\d+)集"}, "更新至3集")["episodes"], [1, 2, 3])
        self.assertEqual(extract({"episodes": r"E(?P<start>\d+)(?:-E(?P<end>\d+))?"}, "E05-E07 与 E10")["episodes"],
                         [5, 6, 7, 10])
        # 几种写法用 | 连起来：每处匹配取它用上的那个分支
        either = {"episodes": r"S\d+E(\d+)|第(?P<start>\d+)-(?P<end>\d+)集"}
        self.assertEqual(extract(either, "S01E04")["episodes"], [4])
        self.assertEqual(extract(either, "第1-3集")["episodes"], [1, 2, 3])
        self.assertEqual(extract({"year": r"(\d{4})", "season": r"第(\d+)季"}, "1080 第2季"), {"season": 2})
        self.assertEqual(extract({"title": r"^片名：(.*)$"}, "片名：  "), {})

    def test_rules_are_validated_with_the_config(self):
        cfg = {"save_dirs": {"默认": "1"}, "defaults": {"save_to": "默认"}}
        normalize_config({**cfg, "sources": [{"bot": "@a", "parse_rules": RULES}]})
        for rules, message_part in (({"title": "("}, "不是有效的正则"), ({"cast": "x"}, "只能包含"),
                                    ({"episodes": r"E\d+"}, "需要捕获组"), ([], "必须是对象")):
            with self.assertRaisesRegex(ValueError, message_part):
                normalize_config({**cfg, "sources": [{"bot": "@a", "parse_rules": rules}]})


class TestGenericRules(unittest.TestCase):
    """没写来源规则时的通用规则：常见的资源消息写法。"""

    def test_common_message_formats(self):
        cases = [
            ("名称：繁花 (2023)\n第 1-3 集 4K", ("繁花", 2023, None, [1, 2, 3], "2160p")),
            ("片名：繁花\n年份：2023\n集数：更新至 10 集", ("繁花", 2023, None, list(range(1, 11)), None)),
            ("【频道】每日更新\n资源名称：漫长的季节 S01 全10集 2160p", ("漫长的季节", None, 1, list(range(1, 11)), "2160p")),
            ("【繁花】2023 国产剧 S01E05 1080p", ("繁花", 2023, 1, [5], "1080p")),
            ("#繁花 #国产剧\n繁花.S01E05.2160p.WEB-DL.H265", ("繁花", None, 1, [5], "2160p")),
            ("🎬 标题：流浪地球2 (2023)\n🏷 4K HDR", ("流浪地球2", 2023, None, [], "2160p")),
            ("电影：奥本海默 Oppenheimer (2023) REMUX", ("奥本海默", 2023, None, [], None)),
            ("繁花 第二季 第5集 HDR WEB-DL 1080p", ("繁花", None, 2, [5], "1080p")),
            ("庆余年（2024） 第2季 E01-E03", ("庆余年", 2024, 2, [1, 2, 3], None)),
        ]
        for text, expected in cases:
            with self.subTest(text=text):
                f = regex_fields(text)
                self.assertEqual((f["title"], f["year"], f["season"], f["episodes"], f["resolution"]), expected)

    def test_numbers_on_other_lines_are_not_episodes_or_sizes(self):
        f = regex_fields("片名：繁花\n年份：2023\n集数：第5集\n清晰度：4K")
        self.assertEqual((f["episodes"], f["size"]), ([5], None))
        self.assertEqual(regex_fields("繁花 62GB")["size"], "62 GB")
        self.assertEqual(regex_fields("繁花 1.5G")["size"], "1.5 GB")


    def test_share_link_is_the_link_itself_and_password_spellings(self):
        self.assertEqual(parse_115_url("https://115cdn.com/s/swabc123?password=x1y2 其他文字", []),
                         ("https://115cdn.com/s/swabc123?password=x1y2", "swabc123", "x1y2"))
        self.assertEqual(parse_115_url("链接 115.com/s/swabc123 密码：ab12", []),
                         ("https://115.com/s/swabc123", "swabc123", "ab12"))


class TestParser115Url(unittest.TestCase):
    def test_extract_share_and_code(self):
        from reelay.core.parse_rules import parse_115_url
        url, code, pwd = parse_115_url(
            "链接 https://115.com/s/swabc123?password=x1y2 访问码：x1y2",
            ["https://115.com/s/swabc123?password=x1y2"],
        )
        self.assertEqual(code, "swabc123")
        self.assertEqual(pwd, "x1y2")

    def test_no_115_returns_none(self):
        from reelay.core.parse_rules import parse_115_url
        url, code, pwd = parse_115_url("普通文本 http://example.com", ["http://example.com"])
        self.assertIsNone(code)


class TestParsers(ServiceCase):
    def configure_rules(self, parser="llm"):
        self.configure({**self.CFG, "sources": [{"bot": "@bot", "parser": parser, "parse_rules": RULES}]})

    async def test_regex_parser_uses_the_source_rules(self):
        ctx = message(POST)
        ctx.source_rule = {"parse_rules": RULES}
        parsed = _regex_parse(ctx)
        self.assertEqual((parsed.title, parsed.episodes), ("小城良方", [5, 6]))
        self.assertEqual(parsed.reason, "regex 规则解析（来源规则：片名、集）")
        ctx.raw.text, ctx.raw.urls = POST.replace(SHARE, ""), []
        self.assertIsNone(_regex_parse(ctx).title)  # 没有分享链接就不是资源

    async def test_llm_gets_the_rule_result_as_a_hint_and_falls_back_to_the_rules(self):
        self.configure_rules()
        ctx = message(POST)
        await self.pl.process_new(ctx)
        request = self.parser._client.calls[-1]["messages"]
        self.assertIn("rule_hint", request[0]["content"])
        hint = json.loads(request[-1]["content"])["rule_hint"]
        self.assertEqual((hint["title"], hint["episodes"]), ("小城良方", [5, 6]))
        self.parser._client = None  # AI 不可用：按来源规则识别兜底
        ctx = message(POST, msg_id="2")
        await self.pl.process_new(ctx)
        parsed = self.store.get_task(ctx.task_id).parsed
        self.assertEqual((parsed.title, parsed.episodes), ("小城良方", [5, 6]))


class TestDraftAndPreview(ServiceCase):
    async def test_ai_drafts_rules_and_they_are_tried_on_the_samples(self):
        self.parser._client = FakeAI({"rules": {**{key: "" for key in parse_rules.FIELDS}, **RULES}, "notes": []})
        result = await self.service.draft_parse_rules("@bot", [POST, " "])
        self.assertEqual(result["rules"], RULES)
        self.assertEqual((result["results"][0]["title"], result["results"][0]["from_rules"]),
                         ("小城良方", ["title", "episodes"]))
        payload = json.loads(self.parser._client.calls[0]["messages"][1]["content"])
        self.assertEqual(payload["samples"][0]["generic"]["title"], "趣看影视")
        self.assertNotIn("sources", json.loads(self.config_path.read_text(encoding="utf-8")))  # 不写入配置
        # 写错的正则只丢掉那个字段，并在提醒里说明
        self.parser._client = FakeAI({"rules": {**{key: "" for key in parse_rules.FIELDS}, "title": "(",
                                                "episodes": RULES["episodes"]}, "notes": []})
        result = await self.service.draft_parse_rules("@bot", [POST])
        self.assertEqual(result["rules"], {"episodes": RULES["episodes"]})
        self.assertIn("AI 写的片名规则无效，已丢弃", result["notes"][0])
        # 交给 AI 的样例限条数，每条只取开头
        await self.service.draft_parse_rules("@bot", [POST + "简介" * 500] * 9)
        samples = json.loads(self.parser._client.calls[-1]["messages"][1]["content"])["samples"]
        self.assertEqual((len(samples), len(samples[0]["text"])), (6, 400))
        self.parser._client = FakeAI("bad")
        with self.assertRaises(ConflictError):
            await self.service.draft_parse_rules("@bot", [POST])

    def test_preview_runs_hand_edited_rules_without_ai(self):
        self.parser._client = None
        result = self.service.preview_parse_rules({"title": RULES["title"], "episodes": " "}, [POST])
        self.assertEqual(result["rules"], {"title": RULES["title"]})
        self.assertEqual((result["results"][0]["title"], result["results"][0]["episodes"]), ("小城良方", []))
        with self.assertRaises(ValueError):
            self.service.preview_parse_rules({"title": "("}, [POST])
        with self.assertRaises(ValueError):
            self.service.preview_parse_rules({}, [])


class TestDraftPreviews(ServiceCase):
    """来源卡片的「测试识别」与媒体库整理的「预览整理效果」按还没保存的设置跑。"""

    async def test_recognition_test_uses_the_unsaved_source_settings(self):
        draft = {"bot": "@bot", "parser": "regex", "parse_rules": RULES}
        result = await self.service.test_parse(POST, [SHARE], "@bot", rule=self.service.draft_source_rule(draft))
        self.assertEqual((result["parsed"]["title"], result["parsed"]["episodes"]), ("小城良方", [5, 6]))
        self.assertNotIn("sources", json.loads(self.config_path.read_text(encoding="utf-8")))  # 不写入配置
        for bad in ({"bot": "@bot", "parse_rules": {"title": "("}}, {"bot": " "}, None):
            with self.assertRaises(ValueError):
                self.service.draft_source_rule(bad)

    def test_naming_preview_uses_the_unsaved_template(self):
        from reelay.core import runtime
        preview = self.pl.reg.action("organizer", "preview_plan")
        payload = {"parsed": {"is_media": True, "media_type": "tv", "title": "剧", "season": 1, "episodes": [2]},
                   "landed": [{"id": "1", "name": "S01E02.mkv"}]}
        with runtime.scope(self.pl.cfg):
            saved = preview(payload)["items"][0]["target_name"]
            draft = preview({**payload, "naming": {"tv": "{{title}}/{{title}}.{{season_episode}}{{fileExt}}"}})
            self.assertEqual(draft["items"][0]["target_name"], "剧.S01E02.mkv")
            self.assertNotEqual(saved, "剧.S01E02.mkv")
            with self.assertRaises(ValueError):
                preview({**payload, "naming": {"tv": "{{ title"}})


class TestSourceMessages(ServiceCase):
    async def test_main_process_reads_the_channel_when_it_is_connected(self):
        self.store.heartbeat("source", status="connected")

        class Source:
            async def recent(self, bot, limit):
                return [{"text": f"{bot} 最近的消息", "urls": []}][:limit]

        async def main_process():
            while not (requests := self.store.claim_source_fetches()):
                await asyncio.sleep(0.05)
            await _serve_fetch(self.store, Source(), requests[0])

        result, _ = await asyncio.gather(self.service.source_messages("@bot", 5), main_process())
        self.assertEqual(result, {"messages": [{"text": "@bot 最近的消息", "urls": []}], "origin": "telegram",
                                  "problem": None})

    async def test_falls_back_to_received_messages(self):
        self.store.heartbeat("source", status="connected")

        async def main_process():
            while not (requests := self.store.claim_source_fetches()):
                await asyncio.sleep(0.05)
            await _serve_fetch(self.store, types.SimpleNamespace(), requests[0])  # 消息源不支持读取

        await self.pending("1")
        await self.pending("2")
        result, _ = await asyncio.gather(self.service.source_messages("@BOT", 5), main_process())
        self.assertEqual((result["origin"], len(result["messages"])), ("history", 2))
        self.assertEqual(result["problem"], "当前消息源不支持读取历史消息")
        self.assertTrue(result["messages"][0]["text"].startswith("剧 (2026) 第2集"))  # 新的在前
        # 主进程没在运行：不发请求，直接用已收到的消息；一条都没有时请用户手动粘贴
        self.store.heartbeat("source", status="disconnected")
        with patch.object(self.store, "request_source_fetch") as request:
            self.assertEqual((await self.service.source_messages("@bot"))["origin"], "history")
            request.assert_not_called()
            with self.assertRaisesRegex(ConflictError, "手动粘贴"):
                await self.service.source_messages("@other")


class TestSourceDraft(ServiceCase):
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


if __name__ == "__main__":
    unittest.main()
