"""电驴链接：从正文与按钮识别、交给 115 云下载前的读取（选集、大小、版本）与卡片措辞。转存流程见 test_storage。"""
from __future__ import annotations

import asyncio
import types
import unittest

from reelay.core import messages
from reelay.core.contracts import ParsedResource, PipelineContext, RawMessage, TaskState
from reelay.core.parse_rules import parse_ed2k
from reelay.providers.parser import RegexParser
from reelay.providers.source import _extract_urls
from reelay.providers.storage import describe_ed2k

DOULUO = ("ed2k://|file|斗罗大陆Ⅱ绝世唐门.2023.S01E177.WEB-DL.2160p.SDR.HEVC.EAC3.mkv|1412118844|"
          "5cb8d06865e9eec6fec50acf81818cde|/")


def context(text: str, urls=()) -> PipelineContext:
    ctx = PipelineContext(RawMessage("@bot", "1", text, list(urls)))
    ctx.source_rule = {}
    return ctx


class TestParseLinks(unittest.TestCase):
    def test_complete_links_are_read_and_truncated_ones_ignored(self):
        text = "🧲 资源链接 (点击复制)： ed2k://|file|第二次初恋(2026)"  # 正文里截断了，完整的在按钮里
        links = parse_ed2k(text, [DOULUO, DOULUO.replace("5cb8d", "5CB8D")])  # 同一个链接只算一次
        self.assertEqual(links, [{"name": "斗罗大陆Ⅱ绝世唐门.2023.S01E177.WEB-DL.2160p.SDR.HEVC.EAC3.mkv",
                                  "size": 1412118844, "hash": "5cb8d06865e9eec6fec50acf81818cde", "url": DOULUO}])
        encoded = parse_ed2k("ed2k://|file|Show%20S01E01.mkv|10|" + "a" * 32 + "|h=XYZ|/", [])
        self.assertEqual(encoded[0]["name"], "Show S01E01.mkv")
        self.assertEqual(parse_ed2k(text, []), [])

    def test_links_in_copy_and_url_buttons_are_collected(self):
        """「点击复制」按钮要复制的内容（如完整的电驴链接）与链接按钮的地址都收进来；新旧两种按钮结构都认。"""
        new = types.SimpleNamespace(text="复制", type=types.SimpleNamespace(copy_text=DOULUO), style=None)
        old = types.SimpleNamespace(text="打开", url="https://example.com/a")
        msg = types.SimpleNamespace(raw_text="消息", entities=[], reply_markup=types.SimpleNamespace(
            rows=[types.SimpleNamespace(buttons=[new]), types.SimpleNamespace(buttons=[old])]))
        self.assertEqual(_extract_urls(msg), [DOULUO, "https://example.com/a"])
        self.assertEqual(_extract_urls(types.SimpleNamespace(raw_text="x", entities=[], reply_markup=None)), [])

    def test_parser_treats_a_message_with_only_ed2k_as_a_resource(self):
        async def parse(text, urls=()):
            return await RegexParser().parse(context(text, urls))

        resource = asyncio.run(parse("🎬 斗罗大陆Ⅱ绝世唐门 (2023) 第177集", [DOULUO]))
        self.assertTrue(resource.is_media)
        self.assertEqual([x["hash"] for x in resource.ed2k], ["5cb8d06865e9eec6fec50acf81818cde"])
        self.assertEqual(resource.link_code(), "ed2k5cb8d06865e9eec6fec50acf81818cde")
        shared = asyncio.run(parse(f"剧 第1集 https://115.com/s/abc123?password=abcd {DOULUO}"))
        self.assertEqual((shared.share_code, shared.ed2k), ("abc123", []))  # 有 115 分享时只转存分享
        self.assertFalse(asyncio.run(parse("闲聊")).has_link())


class TestDescribeAndCards(unittest.TestCase):
    def resource(self, **fields):
        values = {"is_media": True, "title": "斗罗大陆Ⅱ绝世唐门", "media_type": "anime", "season": 1,
                  "episodes": [], "ed2k": parse_ed2k(DOULUO, [])}
        return ParsedResource(**{**values, **fields})

    def test_links_describe_episodes_size_and_version_without_115(self):
        result = describe_ed2k(self.resource())
        self.assertEqual((result["status"], result["kind"], result["videos"]), ("ok", "ed2k", 1))
        self.assertEqual((result["season"], result["episodes"], result["size"]), (1, [177], 1412118844))
        self.assertEqual((result["resolution"], result["release"]["source"]), ("2160p", "web-dl"))
        subtitles = self.resource(ed2k=parse_ed2k("ed2k://|file|a.ass|5|" + "b" * 32 + "|/", []))
        self.assertEqual(describe_ed2k(subtitles), {"status": "issue", "videos": 0, "note": "电驴链接里没有视频文件",
                                                     "kind": "ed2k"})

    def test_cards_say_it_goes_through_cloud_download(self):
        ctx = context("消息")
        ctx.parsed = self.resource(episodes=[177])
        ctx.meta["share"] = {**describe_ed2k(ctx.parsed), "size_text": "1.3 GB"}
        card = messages.approval_card(ctx)
        self.assertIn("🖥 分辨率：2160p", card)
        self.assertIn("📦 大小：1.3 GB", card)
        self.assertIn("由 115 云下载", card)
        ctx.state = TaskState.NEED_MANUAL
        ctx.meta["storage"] = {"phase": "receiving", "manifest": {"files": []}}
        self.assertEqual(messages.problem_reason(ctx)[0], "已交给 115 云下载，还没下完")
        ctx.meta["storage"]["auto_recheck"] = {"gave_up": True, "result": "115 云下载还没完成：进度 40%"}
        self.assertEqual(messages.problem_reason(ctx)[:2], ("115 云下载 24 小时内都没有下完", ["115 云下载还没完成：进度 40%"]))


if __name__ == "__main__":
    unittest.main()
