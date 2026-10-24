"""来源的识别规则：逐字段正则、规则识别与 AI 识别的参考、AI 起草与试跑、读取来源最近的消息。全部本地模拟。"""
from __future__ import annotations

import asyncio
import json
import types
import unittest
from unittest.mock import patch

from reelay.core import parse_rules
from reelay.core.config import normalize_config
from reelay.core.service import ConflictError
from reelay.main import _serve_fetch
from reelay.providers.parser import _regex_parse, regex_fields

from tests.test_ai_feedback import SHARE, Base, FakeAI, message

# 这个来源的消息格式：第一行是频道名，片名在「名称：」后面，集数写成 S01E05
POST = f"【趣看影视】每日更新\n名称：小城良方 (2026)\n集数：S01E05-E06\n4K 高码 1.5GB\n{SHARE}"
RULES = {"title": r"^名称：(.+?)(?:\s*\(\d{4}\))?$", "episodes": r"E(\d+)"}


class TestRules(unittest.TestCase):
    def test_fields_come_from_the_rules_and_fall_back_to_generic_ones(self):
        generic = regex_fields(POST)
        self.assertEqual(generic["title"], "【趣看影视】每日更新")  # 通用规则只认第一行
        fields = regex_fields(POST, RULES)
        self.assertEqual((fields["title"], fields["episodes"], fields["from_rules"]),
                         ("小城良方", [5, 6], ["title", "episodes"]))
        self.assertEqual((fields["year"], fields["size"], fields["media_type"]), (None, "1.5GB", "tv"))
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


class TestParsers(Base):
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


class TestDraftAndPreview(Base):
    async def test_ai_drafts_rules_and_they_are_tried_on_the_samples(self):
        self.parser._client = FakeAI({"rules": {**{key: "" for key in parse_rules.FIELDS}, **RULES}, "notes": []})
        result = await self.service.draft_parse_rules("@bot", [POST, " "])
        self.assertEqual(result["rules"], RULES)
        self.assertEqual((result["results"][0]["title"], result["results"][0]["from_rules"]),
                         ("小城良方", ["title", "episodes"]))
        payload = json.loads(self.parser._client.calls[0]["messages"][1]["content"])
        self.assertEqual(payload["samples"][0]["generic"]["title"], "【趣看影视】每日更新")
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


class TestDraftPreviews(Base):
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


class TestSourceMessages(Base):
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


if __name__ == "__main__":
    unittest.main()
