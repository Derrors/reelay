"""telegra.ph 页面链接：消息本身没有链接时，读取附带的页面取出分享链接。"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from reelay.plugins import telegraph_links
from reelay.plugins.telegraph_links import TelegraphLinks, page_links, page_paths

from tests.fakes import MANUAL_REGEX, SHARE, PipelineCase, ServiceCase, message

PAGE = "https://telegra.ph/%E5%89%A7-2026-11-13"
POST = "🎥 剧 (2026)\n1080P WEB-DL\n🔍 搜索：@example_bot"


def page(*nodes) -> dict:
    return {"ok": True, "result": {"title": "剧 (2026)", "content": list(nodes)}}


def link(href: str, text: str = "查看链接") -> dict:
    return {"tag": "a", "attrs": {"href": href}, "children": [text]}


class TestPageParsing(unittest.TestCase):
    def test_page_paths_are_decoded_deduplicated_and_skip_images(self):
        urls = [PAGE, "https://telegra.ph/剧-2026-11-13", "https://graph.org/合集-11-13?x=1",
                "https://telegra.ph/file/abc.jpg", "https://example.com/web/#/login"]
        self.assertEqual(page_paths("另见 https://telegra.ph/其他-11-13", urls),
                         ["剧-2026-11-13", "合集-11-13", "其他-11-13"])
        self.assertEqual(page_paths("没有页面", ["https://t.me/example_channel"]), [])

    def test_first_share_and_ed2k_links_are_taken_from_the_page(self):
        content = [{"tag": "p", "children": [link("https://t.me/example_channel", "频道")]},
                   {"tag": "p", "children": [link("https://115cdn.com/s/swabc123?password=wxyz#")]},
                   {"tag": "p", "children": [link("https://115.com/s/other999")]},
                   {"tag": "pre", "children": ["ed2k://|file|剧.S01E01.mkv|1024|" + "a" * 32 + "|/"]}]
        self.assertEqual(page_links(content), ["https://115cdn.com/s/swabc123?password=wxyz#",
                                               "ed2k://|file|剧.S01E01.mkv|1024|" + "a" * 32 + "|/"])

    def test_access_code_written_in_the_page_text_goes_with_the_link(self):
        content = [{"tag": "p", "children": [link("https://115.com/s/swabc123")]},
                   {"tag": "p", "children": ["访问码：k9x2"]}]
        self.assertEqual(page_links(content), ["https://115.com/s/swabc123?password=k9x2"])

    def test_pages_without_links(self):
        self.assertEqual(page_links([{"tag": "p", "children": ["📀 包含影片：剧 / 剧 2"]},
                                     link("/%E5%89%A7-2026-11-13")]), [])


class TestHook(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        patcher = patch.object(telegraph_links, "RETRY_DELAY", 0)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def run_hook(self, ctx, pages: dict):
        requested = []

        def fetch(path):
            requested.append(path)
            result = pages[path]
            if isinstance(result, Exception):
                raise result
            return result

        with patch.object(telegraph_links, "fetch_links", side_effect=fetch):
            self.assertTrue(await TelegraphLinks().run(ctx))
        return requested

    async def test_links_from_the_page_are_added_to_the_message(self):
        ctx = message(POST, urls=["https://telegra.ph/合集-11-13", PAGE])
        requested = await self.run_hook(ctx, {"合集-11-13": [], "剧-2026-11-13": [SHARE]})
        self.assertEqual(requested, ["合集-11-13", "剧-2026-11-13"])  # 只读消息直接带的页面
        self.assertEqual(ctx.raw.urls[-1], SHARE)
        self.assertIn("从 telegra.ph 页面取到 1 条链接", ctx.trail[-1])
        self.assertEqual(ctx.meta["telegraph_links"]["links"], 1)

    async def test_messages_that_already_have_a_link_are_left_alone(self):
        for urls in ([SHARE, PAGE], [PAGE, "ed2k://|file|剧.mkv|1024|" + "b" * 32 + "|/"]):
            with self.subTest(urls=urls):
                ctx = message(POST, urls=urls)
                self.assertEqual(await self.run_hook(ctx, {}), [])
                self.assertEqual(ctx.raw.urls, urls)

    async def test_at_most_three_pages_are_read(self):
        urls = [f"https://telegra.ph/p{i}" for i in range(5)]
        requested = await self.run_hook(message(POST, urls=urls), {f"p{i}": [] for i in range(5)})
        self.assertEqual(requested, ["p0", "p1", "p2"])

    async def test_a_failed_page_is_retried_once_then_noted_in_the_trail(self):
        attempts = iter([OSError("reset"), [SHARE]])
        ctx = message(POST, urls=[PAGE])
        with patch.object(telegraph_links, "fetch_links", side_effect=lambda path: (_ for _ in ()).throw(e)
                          if isinstance(e := next(attempts), Exception) else e):
            await TelegraphLinks().run(ctx)
        self.assertEqual(ctx.raw.urls[-1], SHARE)
        ctx = message(POST, urls=[PAGE])
        with self.assertLogs("reelay.plugin.telegraph_links", "WARNING"):
            requested = await self.run_hook(ctx, {"剧-2026-11-13": TimeoutError("timed out")})
        self.assertEqual(len(requested), 2)
        self.assertEqual(ctx.raw.urls, [PAGE])
        self.assertIn("读取 telegra.ph 页面失败", ctx.trail[-1])


class TestPipeline(PipelineCase):
    async def test_message_with_only_a_page_is_identified_and_kept(self):
        from reelay.core.registry import discover
        pipeline = self.pipeline(MANUAL_REGEX, discover(plugin_dirs=[]))
        ctx = message(POST, urls=[PAGE, "https://example.com/web/#/login?code=x"])
        with patch.object(telegraph_links, "fetch_links", return_value=[SHARE]):
            await pipeline.process_new(ctx)
        stored = self.store.get_task(ctx.task_id)
        self.assertEqual(stored.parsed.share_code, "abc123")
        self.assertEqual(stored.parsed.receive_code, "abcd")
        self.assertEqual(stored.state.value, "pending_approval")
        self.assertIn(SHARE, stored.raw.urls)  # 落库后重新识别、核对都能看到这条链接

    async def test_unreadable_page_is_skipped_as_before(self):
        from reelay.core.registry import discover
        pipeline = self.pipeline(MANUAL_REGEX, discover(plugin_dirs=[]))
        ctx = message(POST, urls=[PAGE])
        with patch.object(telegraph_links, "RETRY_DELAY", 0), self.assertLogs("reelay.plugin.telegraph_links"):
            await pipeline.process_new(ctx)  # 测试里读取页面默认离线
        stored = self.store.get_task(ctx.task_id)
        self.assertIn(stored.state.value, ("skipped", "rejected"))  # 与读取页面之前一样：没有链接就不收
        self.assertTrue(any("读取 telegra.ph 页面失败" in line for line in stored.trail))  # 轨迹里写明原因


class TestPreview(ServiceCase):
    async def test_parse_preview_reads_the_page_too(self):
        with patch.object(telegraph_links, "fetch_links", return_value=[SHARE]):
            result = await self.service.test_parse(f"{POST}\n{PAGE}", [], "@bot")
        self.assertEqual(result["parsed"]["share_code"], "abc123")
        self.assertNotEqual(result["decision"], "skip")


if __name__ == "__main__":
    unittest.main()
