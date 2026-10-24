"""分享检查插件：转存前确认分享可用、读出季集。"""
from __future__ import annotations

import time
import types
import unittest
from unittest.mock import patch

from reelay.core import runtime
from reelay.core.contracts import Decision, ParsedResource, TaskState
from reelay.core.registry import Registry, discover
from reelay.core.service import ConflictError
from reelay.providers.decision import LLMDecision, SubscriptionDecision

from tests.fakes import FakeAI, PipelineCase, ServiceCase, message, tv_ctx


class FakeShareFS:
    def __init__(self, tree=None, error=None):
        self.tree, self.error, self.calls = tree or {}, error, 0

    def iterdir(self, cid):
        self.calls += 1
        if self.error:
            raise self.error
        return self.tree[cid]


def share_file(name, size=100):
    return {"id": name, "name": name, "size": size, "is_directory": False}


def share_dir(cid):
    return {"id": cid, "name": f"目录{cid}", "is_directory": True}


class TestShareProbe(PipelineCase):
    """消息没写集数的剧集：识别后只读列出分享，补出实际的季与集，提前标出失效与多季。"""

    def setUp(self):
        super().setUp()
        self.parsed = ParsedResource(is_media=True, title="剧", year=2026, media_type="tv", share_code="abc")

    def test_describe_share_reads_season_episodes_and_size(self):
        from reelay.providers.storage import describe_share
        fs = FakeShareFS({0: [share_dir(1), share_file("说明.txt")],
                          1: [share_file(f"Show.S01E{ep:02d}.2160p.mkv", 1024 ** 3) for ep in range(1, 11)]})
        result = describe_share(fs, self.parsed)
        self.assertEqual((result["status"], result["season"], result["episodes"], result["videos"], result["size"]),
                         ("ok", 1, list(range(1, 11)), 10, 10 * 1024 ** 3))
        self.assertEqual(self.parsed.episodes, [])  # 不改识别结果

    def test_describe_share_flags_problems_before_approval(self):
        from reelay.providers.storage import describe_share
        multi = FakeShareFS({0: [share_dir(1), share_dir(2)], 1: [share_file("A.S01E01.mkv")], 2: [share_file("A.S02E01.mkv")]})
        result = describe_share(multi, self.parsed)  # 跨多季按季转存，不再转人工
        self.assertEqual((result["status"], result["season"], result["pairs"]), ("ok", None, [[1, 1], [2, 1]]))
        bare = FakeShareFS({0: [share_file("A.S01E01.mkv"), share_file("A.S02E01.mkv"), share_file("A.E03.mkv")]})
        self.assertEqual(describe_share(bare, self.parsed)["status"], "issue")
        self.assertIn("不带季号", describe_share(bare, self.parsed)["note"])
        gone = describe_share(FakeShareFS(error=OSError(5, {"state": False, "error": "访问码错误"})), self.parsed)
        self.assertEqual(gone["status"], "unavailable")
        self.assertIn("访问码错误", gone["note"])
        with self.assertRaises(ConnectionError):  # 临时错误交给调用方稍后重试
            describe_share(FakeShareFS(error=ConnectionError("timeout")), self.parsed)
        huge = FakeShareFS({0: [share_dir(i) for i in range(1, 40)], **{i: [share_dir(100 + i)] for i in range(1, 40)}})
        self.assertEqual(describe_share(huge, self.parsed, max_dirs=5)["status"], "incomplete")
        self.assertEqual(huge.calls, 5)

    def storage_scope(self, inspect):
        """分享读取经存储 Provider：换成一个只读查询由 inspect(parsed, read) 决定的存储。"""
        storage = types.SimpleNamespace(name="p115", ready=lambda: True,
                                        inspect_share=lambda parsed, *, describe: inspect(parsed, describe))
        registry = Registry()
        registry.add_provider("storage", storage)
        return runtime.scope({}, runtime.Services(self.store, lambda: [], registry))

    async def probe(self, ctx, describe):
        from reelay.plugins import share_probe
        hook = share_probe.ShareProbe()
        inspect = lambda parsed, read: describe(parsed) if read else {"status": "available"}  # noqa: E731
        with patch.object(share_probe, "PROBE_INTERVAL", 0), self.storage_scope(inspect):
            self.assertTrue(await hook.run(ctx))
            return hook.needs_refresh(ctx)

    async def test_hook_only_reads_series_without_episodes(self):
        calls = []

        def describe(parsed):
            calls.append(parsed.title)
            return {"status": "ok", "videos": 10, "size": 71 * 1024 ** 3, "season": 1, "episodes": list(range(1, 11))}

        ctx = message()
        ctx.parsed = self.parsed
        await self.probe(ctx, describe)
        self.assertEqual((ctx.meta["share"]["episodes"], ctx.meta["share"]["size_text"]), (list(range(1, 11)), "71.0 GB"))
        self.assertTrue(any("读取分享：10 个视频" in line for line in ctx.trail))
        for change in ({"episodes": [3]}, {"media_type": "movie"}):  # 只查分享状态，不列季集
            other = message()
            other.parsed = ParsedResource(**{**self.parsed.__dict__, **change})
            await self.probe(other, describe)
            self.assertEqual(other.meta["share"]["status"], "available")
        other = message()
        other.parsed = ParsedResource(**{**self.parsed.__dict__, "share_code": None})
        await self.probe(other, describe)
        self.assertNotIn("share", other.meta)
        self.assertEqual(calls, ["剧"])

    async def test_read_errors_are_retried_on_next_startup(self):
        def describe(parsed):
            raise ConnectionError("timeout")

        ctx = message()
        ctx.parsed, ctx.state = self.parsed, TaskState.PENDING_APPROVAL
        self.assertTrue(await self.probe(ctx, describe))  # needs_refresh
        self.assertEqual(ctx.meta["share"]["status"], "error")

    async def test_inbox_shows_episodes_and_size_read_from_share(self):
        from reelay.core.service import ManagementService
        ctx = message()
        ctx.parsed, ctx.state = self.parsed, TaskState.PENDING_APPROVAL
        ctx.ns("share").update(status="ok", videos=10, season=1, episodes=list(range(1, 11)), size_text="71.1 GB")
        self.store.save_task(ctx)
        service = ManagementService(self.store, self.pipeline(), config_path=str(self.root / "c.yaml"))
        item = service.inbox()["items"][0]
        self.assertEqual((item["season"], item["episodes"], item["size"]), (1, list(range(1, 11)), "71.1 GB"))
        self.assertEqual(item["share"], {"status": "ok", "note": None, "videos": 10, "from_share": True})

    async def test_multi_season_pairs_reach_inbox_task_list_and_library(self):
        from reelay.core.service import ManagementService
        pending = message(msg_id="1")
        pending.parsed, pending.state = self.parsed, TaskState.PENDING_APPROVAL
        pending.ns("share").update(status="ok", videos=4, season=None, episodes=[1, 2],
                                   pairs=[[1, 1], [1, 2], [2, 1], [2, 2]])
        self.store.save_task(pending)
        single = message(msg_id="2")
        single.parsed = ParsedResource(is_media=True, title="单季", media_type="tv", season=1, episodes=[3], share_code="x")
        single.state = TaskState.PENDING_APPROVAL
        self.store.save_task(single)
        service = ManagementService(self.store, self.pipeline(), config_path=str(self.root / "c.yaml"))
        inbox = {i["title"]: i for i in service.inbox()["items"]}
        self.assertEqual(inbox["剧"]["episode_pairs"], [[1, 1], [1, 2], [2, 1], [2, 2]])
        self.assertIsNone(inbox["单季"]["episode_pairs"])

        done = message(msg_id="3")
        done.parsed = ParsedResource(is_media=True, title="剧", media_type="tv", episodes=[1, 2], share_code="d")
        done.state = TaskState.DONE
        done.ns("storage")["manifest"] = {"pairs": [[1, 1], [1, 2], [2, 1], [2, 2]]}
        self.store.save_task(done)
        rows = {r["task_id"]: r for r in service.list_tasks()}
        self.assertEqual(rows[done.task_id]["episode_pairs"], [[1, 1], [1, 2], [2, 1], [2, 2]])
        self.assertIsNone(rows[single.task_id]["episode_pairs"])

    async def test_pending_tasks_are_read_once_at_startup(self):
        from reelay.plugins import share_probe
        ctx = message()
        ctx.parsed, ctx.state = self.parsed, TaskState.PENDING_APPROVAL
        self.store.save_task(ctx)
        pipeline = self.pipeline(registry=discover())
        gone = lambda self, parsed, *, describe: {"status": "unavailable", "note": "分享无法访问：访问码错误"}  # noqa: E731
        with patch("reelay.providers.storage.P115Storage.ready", lambda self: True), \
                patch("reelay.providers.storage.P115Storage.inspect_share", gone), \
                patch.object(share_probe, "PROBE_INTERVAL", 0):
            self.assertEqual(await pipeline.refresh_metadata(), 1)
            self.assertEqual(await pipeline.refresh_metadata(), 0)
            stored = self.store.get_task(ctx.task_id)
            self.assertEqual(stored.meta["share"]["status"], "unavailable")
            stored.meta["share"]["version"] = share_probe.PROBE_VERSION - 1  # 按旧规则读取的结果在启动时重读
            self.store.save_task(stored)
            self.assertEqual(await pipeline.refresh_metadata(), 1)

    async def test_share_status_is_checked_before_listing_and_rechecked_when_old(self):
        from reelay.plugins import share_probe
        snaps = {"gone": {"state": True, "data": {"shareinfo": {"share_state": 7, "forbid_reason": "链接已过期"}}},
                 "late": {"state": True, "data": {"shareinfo": {"share_state": 1, "expire_time": 1}}},
                 "ok": {"state": True, "data": {"shareinfo": {"share_state": 1, "expire_time": 0}}}}
        client = types.SimpleNamespace(share_snap=lambda payload: snaps[payload["share_code"]])
        listed = []
        from reelay.providers.storage import P115Storage
        storage = P115Storage()
        storage._auth = types.SimpleNamespace(cookie_client=lambda: client)
        with patch("reelay.providers.storage.describe_share", lambda fs, parsed: listed.append(parsed) or {"status": "ok"}), \
                patch("p115client.P115ShareFileSystem", lambda *a: None, create=True):
            for code, read in (("gone", True), ("late", False)):
                parsed = ParsedResource(**{**self.parsed.__dict__, "share_code": code})
                self.assertEqual(storage.inspect_share(parsed, describe=read),
                                 {"status": "unavailable", "note": "分享无法访问：链接已过期"})
            ok = ParsedResource(**{**self.parsed.__dict__, "share_code": "ok"})
            self.assertEqual(storage.inspect_share(ok, describe=False), {"status": "available"})
            self.assertEqual(listed, [])  # 不可用的分享不再列目录；不需要季集的只查状态
            self.assertEqual(storage.inspect_share(ok, describe=True), {"status": "ok"})
        hook = share_probe.ShareProbe()
        ctx = message()
        ctx.parsed, ctx.state = ParsedResource(**{**self.parsed.__dict__, "episodes": [3]}), TaskState.PENDING_APPROVAL
        with self.storage_scope(lambda parsed, read: {"status": "available"}):
            ctx.ns("share").update(status="available", version=share_probe.PROBE_VERSION, probed_at=time.time())
            self.assertFalse(hook.needs_refresh(ctx))
            ctx.ns("share")["probed_at"] = time.time() - share_probe.CHECK_TTL - 1  # 分享会过期：太久没查就重查
            self.assertTrue(hook.needs_refresh(ctx))
            ctx.ns("share").update(status="unavailable")  # 已确认失效的不再查
            self.assertFalse(hook.needs_refresh(ctx))


class TestShareCheck(unittest.TestCase):
    def test_one_request_returns_status_and_root_file_names(self):
        from reelay.providers.storage import share_check
        snaps = []
        client = types.SimpleNamespace(share_snap=lambda payload: snaps.append(payload) or {
            "state": True, "data": {"shareinfo": {"forbid_reason": ""},
                                     "list": [{"n": "Movie.2026.2160p.WEB-DL.mkv"}, {"n": "Movie.2026.nfo"}, {}]}})
        self.assertEqual(share_check(client, "abc", None), (None, ["Movie.2026.2160p.WEB-DL.mkv", "Movie.2026.nfo"]))
        self.assertEqual(len(snaps), 1)
        gone = types.SimpleNamespace(share_snap=lambda payload: {"state": True, "data": {"shareinfo": {"forbid_reason": "已取消"}}})
        self.assertEqual(share_check(gone, "abc", None), ("已取消", []))


class TestMultiSeasonAndShareProbe(ServiceCase):
    """与「跨多季的合集按季转存」「消息没写集数时读取分享」配合：订阅放行、建议、推荐版本与「追这部剧」。"""
    SUBSCRIPTIONS = [{"title": "剧", "season": 2, "tmdbid": 100}]

    def probed(self, pairs, msg_id="1", status="ok", **fields):
        """消息没写季集、由 share_probe 读出（季, 集）的剧集。"""
        ctx = tv_ctx(msg_id=msg_id, season=None, episodes=(), **fields)
        seasons = {s for s, _ in pairs}
        ctx.meta["share"] = {"status": status, "season": seasons.pop() if len(seasons) == 1 else None,
                             "episodes": sorted({e for _, e in pairs}), "pairs": [list(p) for p in pairs]}
        return ctx

    def stored(self, pairs, msg_id="s", title="剧"):
        ctx = tv_ctx(msg_id=msg_id, title=title, season=None, episodes=sorted({e for _, e in pairs}))
        ctx.save_dir_cid, ctx.result = "2", {"storage_verified": True}
        ctx.ns("storage").update(manifest={"pairs": [list(p) for p in pairs]}, identity={
            "cid": "2", "share_code": f"s{msg_id}", "title": title, "season": None,
            "episodes": sorted({e for _, e in pairs}), "pairs": [list(p) for p in pairs]})
        ctx.state = TaskState.STORED
        self.store.save_task(ctx)
        self.store.finish_transfer(ctx)
        ctx.state = TaskState.DONE
        self.store.save_task(ctx)

    async def decide(self, ctx, provider=None):
        with self.pl.scope(self.pl.cfg):
            return await (provider or SubscriptionDecision()).decide(ctx)

    async def test_subscription_uses_the_share_season_and_pins_it(self):
        ctx = self.probed([(2, 1), (2, 2)])
        self.assertEqual(await self.decide(ctx), Decision.SAVE)
        self.assertEqual(ctx.parsed.season, 2)  # 转存时只取订阅的这一季
        self.assertTrue(any("记为第 2 季" in line for line in ctx.trail))

    async def test_multi_season_packs_and_unknown_seasons_are_not_released(self):
        multi = self.probed([(1, 1), (2, 1)])
        self.assertEqual(await self.decide(multi), Decision.ASK)
        self.assertIsNone(multi.parsed.season)
        self.assertTrue(any("跨多季（S01–S02 共 2 集）" in line for line in multi.trail))
        unknown = self.probed([(2, 1)], status="error")  # 读取分享出错：不知道是哪一季
        self.assertEqual(await self.decide(unknown), Decision.ASK)
        self.assertTrue(any("无法确认只涉及第 2 季" in line for line in unknown.trail))
        # 只写了集数的消息按第 1 季，与订阅的第 2 季不符
        self.assertEqual(await self.decide(tv_ctx(season=None, episodes=(3,))), Decision.ASK)

    async def test_preferences_veto_withdraws_the_pinned_season(self):
        provider = LLMDecision()
        provider._client = FakeAI({"decision": "skip", "save_to": None, "reason": "不要"})
        ctx = self.probed([(2, 1)])
        ctx.source_rule["preferences"] = "只要 4K"
        self.assertEqual(await self.decide(ctx, provider), Decision.SKIP)
        self.assertIsNone(ctx.parsed.season)
        self.assertNotIn("subscription", ctx.meta)
        # 重新判断时不沿用上次的放行记录（否则会误撤回识别出的季号）
        stale = tv_ctx(season=3, episodes=(1,))
        stale.meta["subscription"] = {"pinned": True}
        self.assertEqual(await self.decide(stale, LLMDecision()), Decision.ASK)
        self.assertEqual(stale.parsed.season, 3)

    async def test_advice_and_versions_follow_share_pairs(self):
        self.stored([(1, 1), (1, 2)])
        first = self.probed([(1, 1), (1, 2), (2, 1)], msg_id="1")
        second = self.probed([(1, 1), (1, 2), (2, 1)], msg_id="2", resolution="2160p")
        other = self.probed([(1, 1), (1, 2)], msg_id="3")
        for ctx in (first, second, other):
            ctx.state = TaskState.PENDING_APPROVAL
            self.store.save_task(ctx)
        items = {item["task_id"]: item for item in self.service.inbox()["items"]}
        self.assertEqual(items[second.task_id]["version"]["count"], 2)  # 同样的季集才算同一内容的不同版本
        self.assertIsNone(items[other.task_id]["version"])
        self.assertEqual(items[second.task_id]["advice"]["text"],
                         "已入库 S01 E01-E02；这是 S02 E01（2160p）；同一集 2 个版本中最符合偏好；在你的订阅里")
        self.assertEqual(items[first.task_id]["advice"]["action"], "reject")
        self.assertEqual(items[other.task_id]["advice"]["action"], "reject")  # 分享里的集都已入库
        self.assertTrue(items[second.task_id]["subscribed"])  # 涉及的第 2 季在订阅里

    async def test_follow_this_show_uses_the_share_season(self):
        single = self.probed([(3, 1)], msg_id="1", tmdbid=7)
        multi = self.probed([(1, 1), (2, 1)], msg_id="2", tmdbid=8)
        for ctx in (single, multi):
            ctx.state = TaskState.PENDING_APPROVAL
            self.store.save_task(ctx)
        result = await self.service.subscribe_from_task(single.task_id)
        self.assertEqual((result["subscription"]["season"], result["pending"]), (3, [single.task_id]))
        with self.assertRaises(ConflictError):
            await self.service.subscribe_from_task(multi.task_id)

    def test_library_groups_multi_season_packs_by_season(self):
        self.stored([(1, 1), (1, 2), (2, 1)])
        item = self.service.library_query()["items"][0]
        self.assertEqual(item["seasons"], {"1": [1, 2], "2": [1]})


if __name__ == "__main__":
    unittest.main()
