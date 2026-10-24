"""发布版本信息：抽取、校验、同集版本择优与收件箱建议。"""
from __future__ import annotations

import json
import unittest

from reelay.core import inbox, release
from reelay.core.config import normalize_config
from reelay.core.contracts import TaskState
from reelay.core.validation import validate_resource
from reelay.providers.parser import LLMParser

from tests.fakes import FakeAI, SHARE, ServiceCase, media, message, tv_ctx


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
        self.assertEqual(release.compare(v("1080p", subtitles=["chs"]), v("2160p")), 1)  # 推荐：未写字幕不算符合偏好
        self.assertEqual(release.compare(v("1080p"), v("2160p", subtitles=["eng"])), -1)
        # 洗版：库中文件已知是中字、新文件没写字幕时不替换；新文件明确中字而库中未知时按清晰度比
        self.assertEqual(release.compare(v("2160p"), v("1080p", subtitles=["chs"]), strict=True), 0)
        self.assertEqual(release.compare(v("2160p", subtitles=["chs"]), v("1080p"), strict=True), 1)
        self.assertEqual(release.compare(v("720p", subtitles=["chs"]), v("1080p"), strict=True), -1)
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
        without_release = media()  # 不带 release 字段
        validate_resource(dict(without_release), complete=True)
        with self.assertRaises(ValueError):
            validate_resource({"release": {"audio": ["klingon"]}})
        with self.assertRaises(ValueError):
            normalize_config({"release": {"hdr": "always"}})


class TestVersionsAndAdvice(ServiceCase):
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


if __name__ == "__main__":
    unittest.main()
