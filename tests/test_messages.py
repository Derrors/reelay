"""消息模板、按钮编解码与通知投递（含审批汇总、系统通知）。"""
from __future__ import annotations

import json
import types
import unittest

from reelay.control.bot import ControlBot
from reelay.core import messages, release
from reelay.core.contracts import ParsedResource, PipelineContext, RawMessage, TaskState
from reelay.core.registry import Registry
from reelay.core.service import ConflictError
from reelay.providers.notify import ConsoleNotify

from tests.fakes import PipelineCase, message


def tv(**fields) -> PipelineContext:
    ctx = PipelineContext(RawMessage("@bot", "1", "x"), task_id="t1")
    values = {"is_media": True, "title": "玉簟秋", "year": 2026, "media_type": "tv", "season": 1, "episodes": [17],
              "resolution": "2160p", "size": "2.53 GB", "release": {**release.empty(), "subtitles": ["chs"], "packaging": "single"}}
    ctx.parsed = ParsedResource(**{**values, **fields})
    ctx.meta["library"] = {"preview": "/测试/剧集/国产剧集"}
    return ctx


def fake_bot(task, **service):
    """控制 Bot 的按钮逻辑，不连 Telegram：store 返回同一个任务，service 的动作按需替换。"""
    store = types.SimpleNamespace(get_task=lambda task_id: task, subscriptions=lambda: [])
    pipeline = types.SimpleNamespace(cfg={"save_dirs": {"电影": "1", "剧集": "2"}})
    bot = ControlBot.__new__(ControlBot)
    bot.service = types.SimpleNamespace(store=store, pipeline=pipeline, **service)
    return bot


class TestTemplates(unittest.TestCase):
    def test_approval_card_lists_labeled_fields_and_where_it_goes(self):
        text = messages.approval_card(tv())
        self.assertTrue(text.startswith("📺 <b>玉簟秋</b> (2026) · S01 E17\n\n"))
        for line in ("🖥 分辨率：2160p", "💬 字幕：简体中字", "📦 大小：2.53 GB",
                     "📂 存到：<code>/测试/剧集/国产剧集</code>", "📡 来源：@bot"):
            self.assertIn(f"\n{line}", text)
        self.assertNotIn("⚠️", text)
        self.assertNotIn("单集", text)  # 打包形式从片名后的集数已经看得出来
        self.assertNotIn("集数", text)
        movie = messages.approval_card(tv(media_type="movie", season=None, episodes=[]))
        self.assertTrue(movie.startswith("🎬 "))
        self.assertTrue(movie.startswith("🎬 <b>玉簟秋</b> (2026)\n"))

    def test_unreliable_items_are_flagged_and_text_is_escaped(self):
        ctx = tv(title="A<b>&B")
        ctx.meta["confidence"] = {"caps": ["tmdb_unmatched"]}
        ctx.meta["share"] = {"status": "unavailable", "note": "分享无法访问：链接已过期"}
        ctx.meta["task"] = {"approval_reminders": 2}
        text = messages.approval_card(ctx)
        self.assertIn("<b>A&lt;b&gt;&amp;B</b>", text)
        self.assertIn("⚠️ TMDB 没有可信匹配", text)
        self.assertIn("⚠️ 分享无法访问：链接已过期", text)
        self.assertIn("⏰ 第 2 次提醒", text)
        self.assertEqual(messages.plain("<b>A&lt;b&gt;</b>"), "A<b>")

    def test_card_keeps_its_content_after_a_decision(self):
        card = messages.approval_card(tv())
        done = messages.approval_result(card, "approved", target="电影")
        self.assertTrue(done.startswith(card))
        self.assertIn("已批准</b>，存到「电影」", done)
        self.assertIn("当前：done", messages.approval_result(card, "stale", "done"))

    def test_saved_message_shows_the_organized_folder_and_files(self):
        ctx = tv(episodes=[1, 2, 3])
        ctx.meta["organize"] = {"plan": [
            {"target_dir": "国产剧集/玉簟秋 (2026)/Season 01", "target_name": f"玉簟秋 - S01E0{i}.mkv"} for i in (1, 2, 3)]}
        text = messages.saved_message(ctx, "/测试/剧集")
        self.assertTrue(text.startswith("✅ <b>已入库</b>\n\n📺 <b>玉簟秋</b> (2026) · S01 E01-E03\n"))
        self.assertIn("📂 位置：<code>/测试/剧集/国产剧集/玉簟秋 (2026)/Season 01</code>", text)
        self.assertIn("📄 文件：3 个，玉簟秋 - S01E01.mkv、玉簟秋 - S01E02.mkv 等", text)
        # 跨多季：目录取共同的上级
        ctx.meta["organize"]["plan"][2]["target_dir"] = "国产剧集/玉簟秋 (2026)/Season 02"
        self.assertIn("<code>/测试/剧集/国产剧集/玉簟秋 (2026)</code>", messages.saved_message(ctx, "/测试/剧集"))

    def test_tmdb_details_and_poster(self):
        plain = tv()
        self.assertIsNone(messages.card_image_url(plain))
        self.assertNotIn("评分", messages.approval_card(plain))  # 没有 TMDB 信息时不出现空行或空字段
        self.assertEqual(messages.link_buttons(plain), [])
        ctx = tv(genre_ids=[18, 99999, 9648, 80, 36], origin_country=["CN", "HK", "CN", "CA"],
                 release={**release.empty(), "subtitles": ["chs"], "audio": ["mandarin", "cantonese"],
                          "hdr": ["dolby_vision"], "source": "web-dl"},
                 raw_fields={"poster_path": "/p.jpg", "rating": 7, "original_title": "Yu Dian Qiu", "tmdbid": 42,
                             "languages": ["国语", "粤语"], "cast": ["甲", "乙<丙>"], "overview": "简介<很长>" * 100})
        self.assertEqual(messages.card_image_url(ctx), "https://image.tmdb.org/t/p/w500/p.jpg")  # 没有横图时用海报
        ctx.parsed.raw_fields["backdrop_path"] = "/b.jpg"
        self.assertEqual(messages.card_image_url(ctx), "https://image.tmdb.org/t/p/w780/b.jpg")
        text = messages.approval_card(ctx)
        self.assertTrue(text.startswith("📺 <b>玉簟秋</b> (2026) · S01 E17\n<i>Yu Dian Qiu</i>\n\n🌟 评分：7.0\n"
                                        "🎭 类型：剧情 · 悬疑 · 犯罪\n🌍 地区：中国大陆 · 香港 · 加拿大\n"
                                        "🗣 语言：国语 · 粤语\n👥 主演：甲、乙&lt;丙&gt;\n\n"  # 作品信息与版本信息之间空一行
                                        "🖥 分辨率：2160p · 杜比视界\n💿 片源：WEB-DL\n📦 大小：2.53 GB\n💬 字幕：简体中字\n"))
        self.assertNotIn("音轨", text)  # 与「语言」重复，不显示
        self.assertIn("📖 简介：\n<blockquote expandable>", text)
        overview = text.split("<blockquote expandable>")[1].split("</blockquote>")[0]
        self.assertTrue(overview.endswith("…") and "&lt;很长&gt;" in overview)
        self.assertLessEqual(len(messages.plain(overview)), messages.OVERVIEW_LIMIT + 1)
        self.assertIn("🎭 类型：剧情", messages.saved_message(ctx, "/测试/剧集"))
        self.assertNotIn("blockquote", messages.saved_message(ctx, "/测试/剧集"))  # 入库通知不带简介
        self.assertEqual(messages.link_buttons(ctx), [[("🎬 TMDB 详情", "https://www.themoviedb.org/tv/42")]])

    def test_resolution_falls_back_to_share_and_file_names(self):
        ctx = tv(resolution=None, size=None)
        # 没有 TMDB 信息时不在片名下多空一行；分辨率、片源、大小没标注时照样列出
        self.assertIn(" · S01 E17\n\n🖥 分辨率：未标注\n💿 片源：未标注\n📦 大小：未标注\n💬 字幕：简体中字\n",
                      messages.approval_card(ctx))
        ctx.meta["share"] = {"status": "available", "resolution": "2160p"}
        self.assertIn("🖥 分辨率：2160p", messages.approval_card(ctx))
        ctx.meta["share"] = {}
        ctx.meta["storage"] = {"landed": [{"id": "1", "name": "Show.S01E17.1080p.WEB-DL.mkv"}]}
        self.assertIn("🖥 分辨率：1080p", messages.saved_message(ctx, "/测试"))
        self.assertIn("🖥 分辨率：2160p", messages.approval_card(tv(resolution="4K")))

    def test_source_and_size_fall_back_to_files(self):
        """消息没写片源与大小：入库通知按实际转存的文件，待审批卡片按分享根目录里这次要转存的视频。"""
        ctx = tv(resolution=None, size=None, episodes=[7, 8])
        ctx.meta["share"] = {"status": "available", "resolution": "1080p", "release": {"source": "web-dl"},
                             "root": {"videos": 2, "size": 5 * 1024 ** 3}}
        card = messages.approval_card(ctx)
        self.assertIn("🖥 分辨率：1080p\n💿 片源：WEB-DL\n📦 大小：5.0 GB\n", card)
        ctx.meta["share"]["root"]["videos"] = 12  # 分享里还有别的集：总大小不是这次转存的
        self.assertIn("📦 大小：未标注", messages.approval_card(ctx))
        ctx.meta["storage"] = {"landed": [{"id": "1", "name": "Show.S01E07.BluRay.mkv", "size": 3 * 1024 ** 3},
                                          {"id": "2", "name": "Show.S01E08.BluRay.mkv", "size": 3 * 1024 ** 3}]}
        saved = messages.saved_message(ctx, "/测试")
        self.assertIn("💿 片源：蓝光", saved)  # 已转存文件的文件名优先于分享根目录
        self.assertIn("📦 大小：6.0 GB", saved)
        self.assertIn("📦 大小：2.53 GB", messages.saved_message(tv(), "/测试"))  # 消息写了的照旧

    def test_subscribe_button_only_for_matched_single_season_shows(self):
        ctx = tv(raw_fields={"tmdbid": 7})
        ctx.meta["tmdb"] = {"matched": True, "tmdbid": 7, "kind": "tv"}
        rows = messages.approval_buttons(ctx)
        self.assertEqual(rows, [[("✅ 转存", "ok:t1"), ("🚫 忽略", "no:t1")], [("📂 换目录", "dir:t1"), ("✏️ 修正", "fx:t1")],
                                [("🔔 追剧并转存", "sub:t1"), ("⛔ 不再接收", "blk:t1")],
                                [("🎬 TMDB 详情", "https://www.themoviedb.org/tv/7")]])
        self.assertNotIn(("🔔 追剧并转存", "sub:t1"),
                         [b for row in messages.approval_buttons(ctx, subscribed=True) for b in row])
        # 处理后的卡片：批准了、还没订阅的剧留一个「追剧」
        self.assertEqual(messages.result_buttons(ctx), messages.link_buttons(ctx))  # 还在待审批
        ctx.state = TaskState.QUEUED
        self.assertEqual(messages.result_buttons(ctx), [[("🔔 追剧", "sub:t1")], *messages.link_buttons(ctx)])
        self.assertEqual(messages.result_buttons(ctx, subscribed=True), messages.link_buttons(ctx))
        ctx.state = TaskState.SKIPPED
        self.assertEqual(messages.result_buttons(ctx), messages.link_buttons(ctx))
        movie = tv(media_type="movie", season=None, episodes=[], raw_fields={"tmdbid": 7})
        movie.meta["tmdb"] = {"matched": True, "tmdbid": 7, "kind": "movie"}
        self.assertEqual(messages.approval_buttons(movie)[1], [("📂 换目录", "dir:t1"), ("✏️ 修正", "fx:t1")])
        self.assertEqual(messages.approval_buttons(movie)[2][0], ("⛔ 不再接收", "blk:t1"))  # 电影没有「追剧」

    def test_problem_buttons_follow_the_task_checkpoint(self):
        ctx = tv()
        ctx.state = TaskState.NEED_MANUAL
        ctx.meta["storage"] = {"phase": "receiving", "manifest": {"files": []}}
        self.assertEqual(messages.problem_buttons(ctx), [[("🔍 核验转存", "rc:t1"), ("🗑 放弃并删除", "cf:t1:abandon")]])
        ctx.meta["storage"]["partial"] = {"landed": 18, "expected": 20, "missing": ["a", "b"]}
        ctx.parsed.raw_fields["tmdbid"] = 7
        self.assertEqual(messages.problem_buttons(ctx), [[("✅ 按已到的 18 个继续", "ap:t1"), ("🔍 核验转存", "rc:t1")],
                                                         [("🗑 放弃并删除", "cf:t1:abandon"),
                                                          ("🎬 TMDB 详情", "https://www.themoviedb.org/tv/7")]])
        del ctx.parsed.raw_fields["tmdbid"]
        ctx.meta["storage"] = {"phase": "stored", "landed": [{"id": "9"}]}
        self.assertEqual(messages.problem_buttons(ctx), [[("📂 重新整理", "ro:t1"), ("✏️ 修正", "fx:t1")]])
        ctx.meta["organize"] = {"pending_conflict": {"existing": {"name": "a.mkv"}}}
        self.assertEqual(messages.problem_buttons(ctx), [[("🗑 删除转存文件", "cf:t1:skip"), ("♻️ 覆盖保存", "cf:t1:replace")],
                                                         [("✏️ 修正", "fx:t1")]])
        failed = tv()
        failed.state = TaskState.FAILED
        self.assertEqual(messages.problem_buttons(failed), [[("🔁 重试", "rt:t1"), ("🙈 忽略", "no:t1")], [("✏️ 修正", "fx:t1")]])
        failed.meta["storage"] = {"phase": "receiving", "manifest": {"files": []}}
        self.assertNotIn([("✏️ 修正", "fx:t1")], messages.problem_buttons(failed))  # 转存结果没核验：不能修正
        text = messages.problem_message(ctx, "库里已有同一份资源", details=["库里：a.mkv", "本次：<b>.mkv"], hint="选一个")
        self.assertTrue(text.startswith(messages.approval_card(tv()).split("\n\n📂")[0]))  # 与审批卡片同样的片名与字段
        self.assertIn("📂 存到：<code>/测试/剧集/国产剧集</code>\n📡 来源：@bot\n\n⚠️ <b>需要处理</b>：库里已有同一份资源\n"
                      "<blockquote>库里：a.mkv\n本次：&lt;b&gt;.mkv</blockquote>\n👉 选一个\n🆔 任务：<code>t1</code>", text)
        rows = messages.directory_buttons("t1", ["电影", "剧集", "动漫"])
        key = messages.short_key
        self.assertEqual(rows, [[("📁 电影", f"to:t1:{key('电影')}"), ("📁 剧集", f"to:t1:{key('剧集')}")],
                                [("📁 动漫", f"to:t1:{key('动漫')}")], [("↩ 返回", "back:t1")]])
        self.assertTrue(all(len(data.encode()) <= 64 for row in rows for _, data in row))


class TestDelivery(unittest.IsolatedAsyncioTestCase):
    async def test_notifications_are_sent_as_html_with_button_rows(self):
        sent = []

        async def sender(**kwargs):
            sent.append(kwargs)

        console = ConsoleNotify()
        console.configure(sender=sender)
        await console.notify(tv(raw_fields={"poster_path": "/p.jpg", "tmdbid": 7}), "pending_approval")
        uncertain = tv()
        uncertain.state = TaskState.NEED_MANUAL
        uncertain.meta["storage"] = {"phase": "receiving", "manifest": {"files": []}}
        await console.notify(uncertain, "transfer_uncertain")
        self.assertTrue(all(item["html"] for item in sent))
        self.assertEqual(sent[0]["photo"], "https://image.tmdb.org/t/p/w500/p.jpg")
        self.assertEqual(sent[0]["buttons"], [[("✅ 转存", "ok:t1"), ("🚫 忽略", "no:t1")],
                                              [("📂 换目录", "dir:t1"), ("✏️ 修正", "fx:t1")],
                                              [("⛔ 不再接收", "blk:t1"), ("🎬 TMDB 详情", "https://www.themoviedb.org/tv/7")]])
        self.assertIn("核验转存", sent[1]["text"])
        self.assertIn("<code>t1</code>", sent[1]["text"])
        self.assertEqual(sent[1]["buttons"], [[("🔍 核验转存", "rc:t1"), ("🗑 放弃并删除", "cf:t1:abandon")]])

    async def test_bot_sends_posters_and_falls_back_to_text(self):
        calls = []

        class Client:
            fail = False

            async def send_file(self, chat, file, **kwargs):
                if self.fail:
                    raise ValueError("bad image")
                calls.append(("photo", file, kwargs["caption"]))

            async def send_message(self, chat, text, **kwargs):
                calls.append(("text", None, text))

        bot = ControlBot.__new__(ControlBot)
        bot._client, bot._owner, bot._Button = Client(), "1", None
        await bot.send("卡片", html=True, photo="https://x/p.jpg")
        await bot.send("长" * 2000, html=True, photo="https://x/p.jpg")  # 超过图片说明的长度上限
        bot._client.fail = True
        await bot.send("卡片", html=True, photo="https://x/p.jpg")
        await bot.send("没有海报", html=True)
        self.assertEqual([kind for kind, _, _ in calls], ["photo", "text", "text", "text"])
        self.assertEqual(calls[0][1:], ("https://x/p.jpg", "卡片"))

    def test_button_rows_and_links(self):
        bot = ControlBot.__new__(ControlBot)
        bot._Button = types.SimpleNamespace(inline=lambda label, data: ("inline", label, data),
                                            url=lambda label, url: ("url", label, url))
        self.assertEqual(bot._buttons([("执行", "do:1"), ("取消", "cancel:1")]),
                         [[("inline", "执行", b"do:1"), ("inline", "取消", b"cancel:1")]])
        self.assertEqual(bot._buttons([[("转存", "ok:1")], [("TMDB", "https://www.themoviedb.org/tv/1")]]),
                         [[("inline", "转存", b"ok:1")], [("url", "TMDB", "https://www.themoviedb.org/tv/1")]])

    async def test_approval_buttons_pick_a_directory_and_keep_the_card(self):
        task = tv(raw_fields={"tmdbid": 7})
        task.state = TaskState.PENDING_APPROVAL
        calls = []

        async def approve(task_id, save_dir_alias=None):
            calls.append(("approve", task_id, save_dir_alias))

        async def reject(task_id):
            raise ConflictError("任务不是待审批状态")

        bot = fake_bot(task, approve=approve, reject=reject)
        text, rows, _ = await bot.callback("dir:t1", "卡片")
        self.assertIsNone(text)  # 只换按钮
        key = messages.short_key
        self.assertEqual(rows[0], [("📁 电影", f"to:t1:{key('电影')}"), ("📁 剧集", f"to:t1:{key('剧集')}")])
        self.assertEqual((await bot.callback("back:t1", "卡片"))[1], messages.approval_buttons(task))
        # 目录按别名的短键回调：配置里目录增删、换顺序后，按钮仍指向原来那个目录
        bot.service.pipeline.cfg["save_dirs"] = {"新目录": "9", "剧集": "2", "电影": "1"}
        text, rows, _ = await bot.callback(f"to:t1:{key('剧集')}", "卡片")
        self.assertEqual(calls, [("approve", "t1", "剧集")])
        self.assertTrue(text.startswith("卡片\n\n✅ <b>已批准</b>，存到「剧集」"))
        self.assertEqual(rows, messages.link_buttons(task))  # 处理后只留链接
        text = await bot.decide("ok:t1")
        self.assertTrue(text.startswith(messages.approval_card(task)))
        self.assertIn("已批准", text)
        self.assertIn("这条已经处理过了", await bot.decide("no:t1"))
        task.state = TaskState.DONE
        self.assertIn("这条已经处理过了（当前：done）", (await bot.callback("ok:t1", "卡片"))[0])

    async def test_subscribe_and_ignore_work_buttons(self):
        task = tv(raw_fields={"tmdbid": 7})
        task.state = TaskState.PENDING_APPROVAL
        task.meta["tmdb"] = {"matched": True, "tmdbid": 7, "kind": "tv"}
        calls, subs = [], []
        show = {"title": "玉簟秋", "season": 1, "tmdbid": 7, "auto": True}

        async def subscribe_from_task(task_id):
            calls.append(("sub", task_id))
            subs.append(show)
            return {"subscription": show, "pending": [task_id, "t2"]}

        async def approve(task_id, save_dir_alias=None):
            calls.append(("approve", task_id))
            task.state = TaskState.QUEUED

        async def ignore_work(task_id):
            calls.append(("ignore", task_id))
            return {"work": {"title": "玉簟秋", "tmdbid": 7, "kind": "tv"}, "rejected": [task_id, "t2", "t3"]}

        bot = fake_bot(task, subscribe_from_task=subscribe_from_task, approve=approve, ignore_work=ignore_work)
        bot.service.store.subscriptions = lambda: subs
        # 待审批时「追剧并转存」：订阅这一季，这一集一并转存
        text, rows, toast = await bot.callback("sub:t1", "卡片")
        self.assertEqual(calls, [("sub", "t1"), ("approve", "t1")])
        self.assertEqual(text, "卡片\n\n🔔 已订阅《玉簟秋》第 1 季，之后的新集自动转存；待审批里还有 1 条这部剧，可以用 /pending 处理\n"
                               "✅ <b>已批准</b>，正在转存，完成后另行通知")
        self.assertEqual(rows, messages.link_buttons(task))
        self.assertEqual(toast, "已订阅，开始转存")
        # 先点了「转存」：批准后的卡片上还有「追剧」，点了只订阅
        subs.clear()
        task.state = TaskState.PENDING_APPROVAL
        text, rows, _ = await bot.callback("ok:t1", "卡片")
        calls.clear()
        self.assertEqual(rows, [[("🔔 追剧", "sub:t1")], *messages.link_buttons(task)])
        text, rows, toast = await bot.callback("sub:t1", "卡片")
        self.assertEqual(calls, [("sub", "t1")])
        self.assertTrue(text.startswith("卡片\n\n🔔 已订阅《玉簟秋》第 1 季"))
        self.assertNotIn("已批准", text)
        self.assertEqual((rows, toast), (messages.link_buttons(task), "已订阅"))
        # 这一季已经订阅过（如同一部剧的另一张卡片）：只转存
        task.state = TaskState.PENDING_APPROVAL
        calls.clear()
        text, rows, toast = await bot.callback("sub:t1", "卡片")
        self.assertEqual(calls, [("approve", "t1")])
        self.assertEqual(text, "卡片\n\n✅ <b>已批准</b>，正在转存，完成后另行通知")
        self.assertEqual(toast, "开始转存")
        # 已经忽略的：不再订阅
        task.state = TaskState.SKIPPED
        self.assertIn("这条已经处理过了（当前：skipped）", (await bot.callback("sub:t1", "卡片"))[0])
        task.state = TaskState.PENDING_APPROVAL
        calls.clear()
        text, rows, toast = await bot.callback("blk:t1", "卡片")
        self.assertIsNone(text)
        self.assertEqual(rows, [[("⚠️ 确认不再接收", "bly:t1"), ("↩ 返回", "back:t1")]])
        self.assertIn("恢复", toast)
        self.assertEqual(calls, [])  # 第一次点只出确认
        text, rows, _ = await bot.callback("bly:t1", "卡片")
        self.assertEqual(calls[-1], ("ignore", "t1"))
        self.assertIn("之后《玉簟秋》的消息不再处理；待审批里另有 2 条一并忽略", text)
        self.assertEqual(rows, messages.link_buttons(task))

    async def test_problem_buttons_confirm_before_deleting(self):
        task = tv()
        task.state = TaskState.NEED_MANUAL
        task.meta["storage"] = {"phase": "stored", "landed": [{"id": "9"}]}
        task.meta["organize"] = {"pending_conflict": {"existing": {"name": "a.mkv"}}}
        calls = []

        async def requeue_organize(task_id, conflict=None):
            calls.append((task_id, conflict))

        async def reconcile_transfer(task_id):
            raise ConflictError("只有转存结果未知且有清单的任务可以核验")

        bot = fake_bot(task, requeue_organize=requeue_organize, reconcile_transfer=reconcile_transfer)
        text, rows, toast = await bot.callback("cf:t1:replace", "提醒")
        self.assertIsNone(text)
        self.assertEqual(rows, [[("⚠️ 确认覆盖保存", "cy:t1:replace"), ("↩ 返回", "pb:t1")]])
        self.assertIn("回收站", toast)
        self.assertEqual(calls, [])  # 第一次点只出确认
        self.assertEqual((await bot.callback("pb:t1", "提醒"))[1], messages.problem_buttons(task))
        text, rows, _ = await bot.callback("cy:t1:replace", "提醒")
        self.assertEqual(calls, [("t1", "replace")])
        self.assertTrue(text.startswith("提醒\n\n♻️ 已排队覆盖保存"))
        self.assertEqual(rows, [])
        self.assertIn("ℹ️ 只有转存结果未知", (await bot.callback("rc:t1", "提醒"))[0])
        self.assertEqual((await bot.callback("cy:t1:keep", "提醒"))[2], "这个按钮已失效")


class TestApprovalDigest(PipelineCase):
    def pending(self, count, *, reminders=0):
        ids = []
        for index in range(count):
            ctx = message(msg_id=str(index))
            ctx.parsed = ParsedResource(is_media=True, title=f"片{index}")
            ctx.state = TaskState.PENDING_APPROVAL
            ctx.ns("task")["approval_reminders"] = reminders
            self.store.save_task(ctx)
            ids.append(ctx.task_id)
        self.store._conn.execute("UPDATE tasks SET updated_at=updated_at-90000")
        self.store._conn.commit()
        return ids

    def outbox(self):
        return [dict(r) for r in self.store._conn.execute("SELECT kind, event, snapshot FROM outbox")]

    async def test_overdue_tasks_are_merged_into_one_digest(self):
        self.pending(3)
        pipeline = self.pipeline()
        self.assertEqual(await pipeline.sweep_approval_timeouts(), 3)
        rows = self.outbox()
        self.assertEqual([(r["kind"], r["event"]) for r in rows], [("notice", "digest")])
        self.assertIn("3 条资源等待审批", json.loads(rows[0]["snapshot"])["text"])
        # 刚提醒过的任务不会在下一轮再次计入
        self.assertEqual(await pipeline.sweep_approval_timeouts(), 0)

    async def test_reminder_limit_stops_reminding_but_keeps_pending(self):
        ids = self.pending(2, reminders=3)
        self.assertEqual(await self.pipeline().sweep_approval_timeouts(), 0)
        self.assertEqual(self.outbox(), [])
        self.assertEqual(self.store.get_task(ids[0]).state, TaskState.PENDING_APPROVAL)

    async def test_card_style_keeps_per_task_cards(self):
        self.pending(2)
        pipeline = self.pipeline({"save_dirs": {"默认": "1"}, "defaults": {"save_to": "默认"},
                                  "approval": {"reminder": "card"}})
        await pipeline.sweep_approval_timeouts()
        self.assertEqual([r["event"] for r in self.outbox()], ["pending_approval", "pending_approval"])


class TestNoticeDelivery(PipelineCase):
    async def test_notice_goes_to_providers_that_support_it(self):
        registry = Registry()
        sent = []
        console = ConsoleNotify()

        async def sender(**kwargs):
            sent.append(kwargs)

        console.configure(sender=sender)
        registry.add_provider("notify", console)
        plain = types.SimpleNamespace(name="plain")  # 没有 notice 方法的通知渠道
        registry.add_provider("notify", plain)
        pipeline = self.pipeline(registry=registry)
        self.store.stage_notice("x", "你好", ["console", "plain"])
        await pipeline.dispatch_deliveries()
        self.assertEqual(sent, [{"text": "你好"}])
        self.assertEqual(self.store.delivery_counts(), {"done": 1, "skipped": 1})
        self.store.stage_notice("y", "<b>日报</b>", ["console"], html=True)
        await pipeline.dispatch_deliveries()
        self.assertEqual(sent[-1], {"text": "<b>日报</b>", "html": True})


if __name__ == "__main__":
    unittest.main()
