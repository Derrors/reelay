"""技术方案差距补齐的回归：鉴权、置信度、LLM 预过滤/缓存/重试、审批汇总、退避与鉴权暂停、
补拉与告警、TMDB 打分、日报与追更、外部扩展。全部本地模拟，不访问网络。"""
from __future__ import annotations

import asyncio
import datetime
import json
import os
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from reelay.core import confidence, tmdb
from reelay.core.config import normalize_config
from reelay.core.contracts import ParsedResource, PipelineContext, RawMessage, TaskState
from reelay.core.pipeline import Pipeline
from reelay.core.registry import Registry, discover
from reelay.core.store import Store, _dump_ctx, _load_ctx
from reelay.core.worker import Worker
from reelay.plugins.daily_report import render
from reelay.plugins.episode_tracker import EpisodeRecorder, EpisodeTracker, format_episodes, missing_episodes
from reelay.plugins.tmdb_enrich import TmdbEnrich, search_kinds
from reelay.providers.notify import ConsoleNotify
from reelay.providers.parser import LLMParser
from reelay.providers.source import TelegramUserbotSource

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover
    TestClient = None

SHARE = "https://115.com/s/abc123?password=abcd"


def media(**change):
    data = {"is_media": True, "is_ad": False, "confidence": 1.0, "media_type": "tv",
            "title": "剧", "year": 2026, "season": 1, "episodes": [3], "genres": [],
            "resolution": "4K", "size": "2 GB", "reason": "影视"}
    data.update(change)
    return data


class APITimeoutError(Exception):
    """名称与 openai SDK 的瞬时错误一致。"""


class FakeAI:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []
        self.chat = types.SimpleNamespace(completions=self)

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        item = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(item, Exception):
            raise item
        content = item if isinstance(item, str) else json.dumps(item)
        message = types.SimpleNamespace(content=content, refusal=None)
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=message)])


def message(text=f"剧 (2026) 第3集 {SHARE}", urls=(SHARE,), msg_id="1", source="@bot"):
    return PipelineContext(RawMessage(source, msg_id, text, list(urls)))


class Base(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"REELAY_LLM_RETRY_DELAY": "0"}, clear=True)
        self.env.start()
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "t.db")

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()
        self.env.stop()

    def pipeline(self, cfg=None, registry=None):
        cfg = normalize_config(cfg or {"save_dirs": {"默认": "1"}, "defaults": {"save_to": "默认"}})
        return Pipeline(registry or Registry(), self.store, cfg)


class TestSnapshotAndStore(Base):
    def test_snapshot_round_trip_keeps_every_parsed_field(self):
        ctx = message()
        ctx.parsed = ParsedResource(is_media=True, title="X", genre_ids=[16], origin_country=["JP"])
        back = _load_ctx(_dump_ctx(ctx))
        self.assertEqual((back.parsed.genre_ids, back.parsed.origin_country), ([16], ["JP"]))

    def test_watermark_is_case_insensitive_and_numeric(self):
        for msg_id in ("9", "120", "dry-1"):
            self.store.save_task(message(msg_id=msg_id, source="@Bot"))
        self.assertEqual(self.store.source_watermark("@bot"), 120)
        self.assertIsNone(self.store.source_watermark("@other"))

    def test_schema_version_is_recorded_and_old_databases_migrate(self):
        import sqlite3
        from reelay.core.store import SCHEMA_VERSION
        self.assertEqual(self.store._conn.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)
        legacy = Path(self.temp.name) / "legacy.db"
        conn = sqlite3.connect(legacy)
        conn.execute("CREATE TABLE tasks (task_id TEXT PRIMARY KEY, state TEXT NOT NULL, source TEXT, msg_id TEXT, "
                     "title TEXT, snapshot TEXT NOT NULL, error TEXT, retries INTEGER NOT NULL DEFAULT 0, "
                     "created_at REAL NOT NULL, updated_at REAL NOT NULL)")
        conn.commit()
        conn.close()
        migrated = Store(legacy)
        columns = {r["name"] for r in migrated._conn.execute("PRAGMA table_info(tasks)")}
        self.assertIn("not_before", columns)
        self.assertEqual(migrated._conn.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)
        migrated.close()

    def test_newer_database_is_refused(self):
        import sqlite3
        newer = Path(self.temp.name) / "newer.db"
        conn = sqlite3.connect(newer)
        conn.execute("PRAGMA user_version=999")
        conn.close()
        with self.assertRaises(RuntimeError):
            Store(newer)

    def test_alert_notifies_once_until_recovered(self):
        self.assertTrue(self.store.raise_alert("k", "坏了", ["console"]))
        self.assertFalse(self.store.raise_alert("k", "还是坏的", ["console"]))
        self.assertEqual(self.store.active_alerts()[0]["text"], "还是坏的")
        self.assertTrue(self.store.clear_alert("k", "好了", ["console"]))
        self.assertFalse(self.store.clear_alert("k", "好了", ["console"]))
        events = [r["event"] for r in self.store._conn.execute("SELECT event FROM outbox ORDER BY id")]
        self.assertEqual(events, ["alert", "recovered"])


class TestConfidence(Base):
    def assess(self, tmdb_ns=None, **fields):
        ctx = message()
        ctx.parsed = ParsedResource(**{"is_media": True, "share_code": "abc", "title": "剧",
                                       "media_type": "tv", "year": 2026, **fields})
        if tmdb_ns is not None:
            ctx.meta["tmdb"] = tmdb_ns
        return confidence.assess(ctx)[0]

    def test_complete_fields_score_full_and_missing_year_lowers(self):
        self.assertEqual(self.assess(), 1.0)
        self.assertEqual(self.assess(year=None), 0.7)

    def test_tmdb_unmatched_caps_below_default_threshold_but_unconfigured_is_neutral(self):
        self.assertLess(self.assess({"matched": False}), 0.5)
        self.assertEqual(self.assess({"matched": False, "note": "未配 TMDB key"}), 1.0)
        self.assertEqual(self.assess({"matched": False, "error": "URLError"}), 1.0)

    def test_movie_with_episodes_is_inconsistent(self):
        self.assertEqual(self.assess(media_type="movie", episodes=[1]), 0.7)

    async def test_pipeline_replaces_llm_self_reported_confidence(self):
        registry = Registry()
        parser = LLMParser()
        parser._client = FakeAI(media(year=None))
        registry.add_provider("parser", parser)
        from reelay.providers.decision import DefaultDecision
        registry.add_provider("decision", DefaultDecision())
        pipeline = self.pipeline({"save_dirs": {"默认": "1"},
                                  "defaults": {"save_to": "默认", "approval_mode": "auto",
                                               "confidence_threshold": 0.8}}, registry)
        ctx = message()
        await pipeline.process_new(ctx)
        stored = self.store.get_task(ctx.task_id)
        self.assertEqual(stored.parsed.raw_fields["parser_confidence"], 1.0)
        self.assertEqual(stored.parsed.confidence, 0.7)
        self.assertEqual(stored.state, TaskState.PENDING_APPROVAL)


class TestParserCostControls(Base):
    def parser(self, *responses):
        parser = LLMParser()
        parser._client = FakeAI(*responses)
        return parser

    async def test_prefilter_skips_llm_for_messages_without_share_link(self):
        parser = self.parser(media())
        ctx = message(text="推广：极搜帮你找群组 t.me/x", urls=("https://t.me/x",))
        resource = await parser.parse(ctx)
        self.assertEqual(parser._client.calls, [])
        self.assertFalse(resource.is_media)
        self.assertEqual(ctx.ns("parse")["prefilter"], "无 115 链接")

    async def test_prefilter_applies_block_keywords_and_must_have(self):
        parser = self.parser(media())
        ctx = message()
        ctx.source_rule = {"filters": {"block_keywords": ["第3集"]}}
        await parser.parse(ctx)
        ctx2 = message()
        ctx2.source_rule = {"filters": {"must_have": ["115cdn"]}}
        await parser.parse(ctx2)
        self.assertEqual(parser._client.calls, [])

    async def test_identical_text_hits_cache_and_reparse_bypasses_it(self):
        parser = self.parser(media())
        await parser.parse(message(msg_id="1"))
        second = message(msg_id="2")
        await parser.parse(second)
        self.assertEqual(len(parser._client.calls), 1)
        self.assertEqual(second.ns("parse")["cache"], "hit")
        third = message(msg_id="3")
        third.meta["parse_request"] = {"bypass_cache": True}
        await parser.parse(third)
        self.assertEqual(len(parser._client.calls), 2)
        self.assertNotIn("parse_request", third.meta)

    async def test_transient_errors_retry_before_fallback(self):
        parser = self.parser(APITimeoutError(), APITimeoutError(), media())
        ctx = message()
        resource = await parser.parse(ctx)
        self.assertEqual(len(parser._client.calls), 3)
        self.assertNotIn("fallback", ctx.ns("parse"))
        self.assertEqual(resource.title, "剧")

    async def test_exhausted_retries_and_non_transient_errors_fall_back(self):
        parser = self.parser(APITimeoutError())
        ctx = message()
        await parser.parse(ctx)
        self.assertEqual(len(parser._client.calls), 3)
        self.assertEqual(ctx.ns("parse")["category"], "APITimeoutError")
        parser = self.parser(ValueError("bad request"))
        ctx = message()
        await parser.parse(ctx)
        self.assertEqual(len(parser._client.calls), 1)

    async def test_invalid_output_is_retried_once(self):
        parser = self.parser("not json", media())
        ctx = message()
        await parser.parse(ctx)
        self.assertEqual((len(parser._client.calls), ctx.ns("parse")["attempts"]), (2, 2))


class TestApprovalDigest(Base):
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


class TestNoticeDelivery(Base):
    async def test_notice_goes_to_providers_that_support_it(self):
        registry = Registry()
        sent = []
        console = ConsoleNotify()

        async def sender(**kwargs):
            sent.append(kwargs)

        console.configure(sender=sender)
        registry.add_provider("notify", console)
        legacy = types.SimpleNamespace(name="legacy")
        registry.add_provider("notify", legacy)
        pipeline = self.pipeline(registry=registry)
        self.store.stage_notice("x", "你好", ["console", "legacy"])
        await pipeline.dispatch_deliveries()
        self.assertEqual(sent, [{"text": "你好"}])
        self.assertEqual(self.store.delivery_counts(), {"done": 1, "skipped": 1})


class FailingStorage:
    name = "p115"

    def __init__(self, error):
        self.error = error

    async def prepare(self, ctx):
        raise RuntimeError(self.error)


class TestWorkerBackoffAndAuthPause(Base):
    def queued(self):
        ctx = message()
        ctx.parsed = ParsedResource(is_media=True, title="片", media_type="movie", share_code="abc")
        ctx.state = TaskState.QUEUED
        self.store.save_task(ctx)
        return ctx

    def worker(self, error):
        registry = Registry()
        registry.add_provider("storage", FailingStorage(error))
        return Worker(self.pipeline(registry=registry), self.store)

    async def test_failed_task_is_rescheduled_without_sleeping(self):
        worker = self.worker("网络抖动")
        ctx = self.queued()
        started = time.monotonic()
        await worker._handle(ctx.task_id, TaskState.QUEUED)
        self.assertLess(time.monotonic() - started, 1)
        row = self.store._conn.execute("SELECT state, retries, not_before FROM tasks").fetchone()
        self.assertEqual((row["state"], row["retries"]), ("queued", 1))
        self.assertGreater(row["not_before"], time.time())
        self.assertEqual(self.store.list_by_state(TaskState.QUEUED, due_only=True), [])

    async def test_auth_failure_pauses_storage_without_consuming_retries(self):
        worker = self.worker("{'state': False, 'error': '请重新登录', 'errno': 99}")
        ctx = self.queued()
        await worker._handle(ctx.task_id, TaskState.QUEUED)
        row = self.store._conn.execute("SELECT state, retries, not_before FROM tasks").fetchone()
        self.assertEqual((row["state"], row["retries"], row["not_before"]), ("queued", 0, 0))
        self.assertTrue(worker.storage_paused)
        self.assertEqual(self.store.active_alerts()[0]["key"], "p115_auth")
        # 暂停期间不领取转存任务
        await worker._handle(ctx.task_id, TaskState.QUEUED)
        self.assertEqual(self.store._conn.execute("SELECT retries FROM tasks").fetchone()[0], 0)
        worker.resume_storage()
        self.assertIsNone(worker.storage_paused)
        self.assertEqual(self.store.active_alerts(), [])

    async def test_deterministic_failures_are_not_auto_retried(self):
        worker = self.worker("x")
        ctx = self.queued()
        ctx.state = TaskState.FAILED
        ctx.ns("task").update(retry_from="queued", no_auto_retry=True)
        self.store.save_task(ctx, error="无法从文件名识别集号")
        worker._schedule_retry(ctx.task_id, TaskState.QUEUED)
        self.assertEqual(self.store.get_task(ctx.task_id).state, TaskState.FAILED)


class FakeTelegram:
    def __init__(self, messages):
        self.messages = messages
        self.requests = []

    async def get_entity(self, ref):
        return types.SimpleNamespace(username="Bot", id=7)

    async def iter_messages(self, entity, limit=None, *, min_id=0, reverse=False):
        """与 Telethon 一致：默认从新到旧返回，limit 计入所有读到的消息。"""
        self.requests.append(min_id)
        ordered = sorted(self.messages, key=lambda m: m.id, reverse=not reverse)
        returned = 0
        for msg in ordered:
            if msg.id <= min_id:
                continue
            if limit is not None and returned >= limit:
                return
            returned += 1
            yield msg


def tg_message(msg_id, *, days=0, action=None):
    date = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
    return types.SimpleNamespace(id=msg_id, raw_text=f"消息 {msg_id} {SHARE}", entities=[], date=date,
                                 action=action)


class TestSourceBackfillAndAlerts(Base):
    async def test_backfill_emits_messages_after_watermark_only(self):
        source = TelegramUserbotSource()
        source.configure(["@bot"])
        source._client = FakeTelegram([tg_message(5, days=40), tg_message(6, days=30), tg_message(7, action="pin"),
                                       tg_message(8), tg_message(9)])
        source.bind_watermark(lambda name: 5 if name == "@Bot" else None)
        emitted = []

        async def emit(raw):
            emitted.append(raw)

        self.assertEqual(await source.backfill(emit), 2)
        self.assertEqual([raw.msg_id for raw in emitted], ["8", "9"])  # 按时间顺序入库
        self.assertTrue(all(raw.source == "@Bot" and raw.extra["backfill"] for raw in emitted))

    async def test_long_downtime_backfills_newest_messages_within_window(self):
        """停机很久时（水位之后先是大量过期消息），上限只计入窗口内最新的消息。"""
        source = TelegramUserbotSource()
        source.configure(["@bot"])
        old = [tg_message(i, days=10) for i in range(11, 411)]
        recent = [tg_message(i) for i in range(411, 416)]
        source._client = FakeTelegram(old + recent)
        source.bind_watermark(lambda name: 10)
        emitted = []

        async def emit(raw):
            emitted.append(raw.msg_id)

        with patch.dict(os.environ, {"REELAY_TG_BACKFILL_LIMIT": "3"}):
            self.assertEqual(await source.backfill(emit), 3)
        self.assertEqual(emitted, ["413", "414", "415"])
        emitted.clear()
        self.assertEqual(await source.backfill(emit), 5)  # 默认上限 200：只补 7 天内的 5 条
        self.assertEqual(emitted, [str(i) for i in range(411, 416)])

    async def test_backfilled_tasks_skip_per_task_approval_cards(self):
        from reelay.providers.decision import DefaultDecision
        from reelay.providers.parser import RegexParser
        registry = Registry()
        registry.add_provider("parser", RegexParser())
        registry.add_provider("decision", DefaultDecision())
        pipeline = self.pipeline({"save_dirs": {"默认": "1"}, "providers": {"source": "telegram", "notify": ["console"]},
                                  "defaults": {"save_to": "默认", "parser": "regex"}}, registry)
        live, old = message(msg_id="1"), message(msg_id="2")
        old.raw.extra["backfill"] = True
        for ctx in (live, old):
            await pipeline.process_new(ctx)
            self.assertEqual(self.store.get_task(ctx.task_id).state, TaskState.PENDING_APPROVAL)
        cards = [r["task_id"] for r in self.store._conn.execute(
            "SELECT task_id FROM outbox WHERE event='pending_approval'")]
        self.assertEqual(cards, [live.task_id])

    async def test_source_loop_sends_one_backfill_summary(self):
        from reelay.main import _source_loop

        class Backfilling:
            name = "fake"
            backfilled = 0
            backfill_done = False

            async def listen(self, emit):
                self.backfilled += 37
                self.backfill_done = True
                await asyncio.Event().wait()

            def health(self):
                return {"status": "connected"}

        registry = Registry()
        registry.add_provider("source", Backfilling())
        pipeline = self.pipeline({"save_dirs": {"默认": "1"}, "defaults": {"save_to": "默认"},
                                  "providers": {"source": "fake", "notify": ["console"]}}, registry)
        pipeline.hold_daily(900)
        self.assertFalse(pipeline.sources_ready)
        loop = asyncio.create_task(_source_loop(pipeline, None, poll_interval=0.05))
        await asyncio.sleep(0.3)
        loop.cancel()
        await asyncio.gather(loop, return_exceptions=True)
        notices = [json.loads(r[0])["text"] for r in self.store._conn.execute(
            "SELECT snapshot FROM outbox WHERE kind='notice' AND event='notice'")]
        self.assertEqual(len(notices), 1)
        self.assertIn("37 条消息", notices[0])
        self.assertTrue(pipeline.sources_ready)

    async def test_first_listen_does_not_import_history(self):
        source = TelegramUserbotSource()
        source.configure(["@bot"])
        source._client = FakeTelegram([tg_message(1)])
        source.bind_watermark(lambda name: None)
        self.assertEqual(await source.backfill(lambda raw: None), 0)

    async def test_source_loop_alerts_after_prolonged_disconnect(self):
        from reelay.main import _source_loop

        class Disconnected:
            name = "flaky"

            async def listen(self, emit):
                await asyncio.Event().wait()

            def health(self):
                return {"status": "disconnected"}

        registry = Registry()
        registry.add_provider("source", Disconnected())
        pipeline = self.pipeline({"save_dirs": {"默认": "1"}, "defaults": {"save_to": "默认"},
                                  "providers": {"source": "flaky", "notify": ["console"]}}, registry)
        with patch.dict(os.environ, {"REELAY_SOURCE_ALERT_SEC": "1"}):
            loop = asyncio.create_task(_source_loop(pipeline, None, poll_interval=0.05))
            await asyncio.sleep(1.3)
            loop.cancel()
            await asyncio.gather(loop, return_exceptions=True)
        self.assertEqual([a["key"] for a in self.store.active_alerts()], ["source"])


class TestDailyWaitsForStartupBackfill(Base):
    CFG = {"save_dirs": {"默认": "1"}, "defaults": {"save_to": "默认"}, "schedule": {"daily_hour": 0}}

    def daily_summary(self):
        row = self.store._conn.execute("SELECT snapshot FROM outbox WHERE event='on_daily'").fetchone()
        return json.loads(row[0])["meta"]["system"]["summary"]

    async def test_daily_report_waits_until_backfilled_messages_are_parsed(self):
        registry = Registry()
        registry.add_listener(EpisodeTracker())
        pipeline = self.pipeline(self.CFG, registry)
        now = time.time()
        pipeline.hold_daily(900, now=now)
        self.assertFalse(await pipeline.emit_daily(now=now + 1))  # 消息源还在补拉
        pipeline.sources_ready = True
        ctx = await pipeline.accept(message())
        self.assertFalse(await pipeline.emit_daily(now=now + 2))  # 补拉的消息还没解析
        self.store.set_state(ctx.task_id, TaskState.PENDING_APPROVAL, expected=TaskState.RECEIVED)
        self.assertTrue(await pipeline.emit_daily(now=now + 3))
        self.assertEqual(self.daily_summary()["received"], 1)

    async def test_daily_report_is_not_held_forever_when_source_never_connects(self):
        registry = Registry()
        registry.add_listener(EpisodeTracker())
        pipeline = self.pipeline(self.CFG, registry)
        now = time.time()
        pipeline.hold_daily(900, now=now)
        self.assertFalse(await pipeline.emit_daily(now=now + 60))
        self.assertTrue(await pipeline.emit_daily(now=now + 901))

    async def test_daily_report_is_not_held_without_startup_hold(self):
        registry = Registry()
        registry.add_listener(EpisodeTracker())
        pipeline = self.pipeline(self.CFG, registry)
        await pipeline.accept(message())
        self.assertTrue(await pipeline.emit_daily())


class TestManualDirectoryChoice(Base):
    CFG = {"save_dirs": {"剧集": "2", "测试": "3", "电影": "4"}, "defaults": {"save_to": "测试"}}

    def setUp(self):
        super().setUp()
        from reelay.core.service import ManagementService
        self.registry = discover()
        self.pl = self.pipeline(self.CFG, self.registry)
        self.service = ManagementService(self.store, self.pl, config_path=str(Path(self.temp.name) / "config.yaml"))
        self.registry.get_provider("parser", "llm")._client = FakeAI(
            media(title="美人余", genre_ids=[18], origin_country=["CN"]))

    async def pending(self, msg_id="1"):
        await self.pl.process_new(message(msg_id=msg_id))
        task_id = self.store.list_by_state(TaskState.PENDING_APPROVAL, 10)[0]["task_id"]
        return task_id

    async def approve_and_route(self, task_id, alias=None):
        await self.service.approve(task_id, save_dir_alias=alias)
        ctx = self.store.get_task(task_id)
        self.assertTrue(await self.pl._run_hooks("before_save", ctx))
        return ctx

    async def test_inbox_and_approval_card_show_the_category_target(self):
        task_id = await self.pending()
        item = self.service.inbox()["items"][0]
        self.assertEqual((item["save_dir_alias"], item["auto_dir"]), (None, "剧集/国产剧集"))
        self.assertEqual(self.service.get_task(task_id)["auto_dir"], "剧集/国产剧集")
        sent = []

        async def sender(**kwargs):
            sent.append(kwargs["text"])

        console = ConsoleNotify()
        console.configure(sender=sender)
        await console.notify(self.store.get_task(task_id), "pending_approval")
        self.assertIn("是否转存到「剧集/国产剧集」", sent[0])

    async def test_targets_show_real_drive_paths_once_recorded(self):
        self.store.state_set("kernel", "save_dir_paths", {
            "测试": {"cid": "3", "path": "/测试"}, "剧集": {"cid": "2", "path": "/测试/剧集"},
            "电影": {"cid": "999", "path": "/旧位置/电影"}})  # CID 已改的记录不用
        task_id = await self.pending()
        body = self.service.inbox()
        self.assertEqual(body["save_dir_paths"], {"测试": "/测试", "剧集": "/测试/剧集"})
        self.assertEqual((body["items"][0]["auto_dir"], body["items"][0]["auto_path"]), ("剧集/国产剧集", "/测试/剧集/国产剧集"))
        self.assertEqual(self.service.get_task(task_id)["auto_path"], "/测试/剧集/国产剧集")
        self.assertEqual(self.store.get_task(task_id).meta["dir_router"]["preview"], "/测试/剧集/国产剧集")
        self.assertEqual(self.pl.display_target("测试/其他/综艺节目"), "/测试/其他/综艺节目")
        self.assertEqual((self.pl.display_target("测试"), self.pl.display_target("电影/欧美电影")), ("/测试", "电影/欧美电影"))

    async def test_worker_records_drive_paths_and_tolerates_failures(self):
        class Storage:
            name = "p115"
            fail = False

            def dir_path(self, cid):
                if self.fail:
                    raise ConnectionError("down")
                return {"2": "/测试/剧集", "3": "/测试", "4": "/测试/电影"}[cid]

        registry = Registry()
        storage = Storage()
        registry.add_provider("storage", storage)
        pipeline = self.pipeline(self.CFG, registry)
        worker = Worker(pipeline, self.store)
        storage.fail = True
        await worker.record_dir_paths()  # 失败只影响展示，不抛出
        self.assertEqual(pipeline.dir_paths(), {})
        self.assertGreater(worker._next_dir_paths, time.time())
        storage.fail = False
        await worker.record_dir_paths()
        self.assertEqual(pipeline.dir_paths(), {"剧集": "/测试/剧集", "测试": "/测试", "电影": "/测试/电影"})
        self.assertEqual(worker._dir_paths_revision, pipeline.revision)

    async def test_approval_without_choice_follows_category(self):
        ctx = await self.approve_and_route(await self.pending())
        self.assertEqual(ctx.save_dir_alias, "剧集")
        self.assertEqual(ctx.meta["dir_router"]["relative_category"], "国产剧集")

    async def test_manual_choice_wins_and_skips_category_subfolder(self):
        ctx = await self.approve_and_route(await self.pending(), "测试")
        self.assertEqual(ctx.save_dir_alias, "测试")
        self.assertNotIn("relative_category", ctx.meta["dir_router"])
        self.assertTrue(any("手动指定目录「测试」" in line for line in ctx.trail))

    async def test_manual_choice_matching_category_root_keeps_subfolder(self):
        ctx = await self.approve_and_route(await self.pending(), "剧集")
        self.assertEqual((ctx.save_dir_alias, ctx.meta["dir_router"]["relative_category"]), ("剧集", "国产剧集"))

    async def test_llm_calls_are_reported_to_connection_status(self):
        await self.pending()
        later = time.time() + 86400  # 只在真实调用时记录，长时间没有新消息也不算过期
        with patch.dict(os.environ, {"REELAY_LLM_API_KEY": "k"}), patch("time.time", return_value=later):
            self.assertEqual(self.service.connection_status()["llm"]["status"], "connected")
        self.registry.get_provider("parser", "llm")._client = FakeAI(ValueError("bad request"))
        await self.pl.process_new(message(text=f"另一部剧 第1集 {SHARE}", msg_id="2"))
        llm = self.service.connection_status()["llm"]
        self.assertEqual((llm["status"], llm["error"]), ("failed", "ValueError"))


class TestRefreshMetadataAfterTmdbConfigured(Base):
    HIT = {"id": 294446, "name": "美人余", "first_air_date": "2026-10-09", "origin_country": ["CN"],
           "genre_ids": [18], "poster_path": "/poster.jpg", "_kind": "tv", "_score": 1.1}

    def setUp(self):
        super().setUp()
        with patch.dict(os.environ, {"REELAY_TMDB_API_KEY": "k"}):
            registry = discover()
        self.pl = self.pipeline({"save_dirs": {"剧集": "2", "默认": "1"}, "defaults": {"save_to": "默认"}}, registry)

    def task(self, title, state=TaskState.PENDING_APPROVAL, msg_id="1"):
        """配置 TMDB 之前识别的任务：meta["tmdb"] 记着未查询的原因。"""
        ctx = message(msg_id=msg_id)
        ctx.parsed = ParsedResource(is_media=True, title=title, year=2026, media_type="tv", season=1,
                                    episodes=[23], share_code="abc", confidence=1.0)
        ctx.ns("tmdb").update(matched=False, note="未配 TMDB key，沿用 AI 标题")
        ctx.state = state
        self.store.save_task(ctx)
        return ctx.task_id

    def updated_at(self, task_id):
        return self.store._conn.execute("SELECT updated_at FROM tasks WHERE task_id=?", (task_id,)).fetchone()[0]

    async def test_pending_task_gets_tmdb_metadata_and_poster_without_llm(self):
        task_id = self.task("美人余")
        before = self.updated_at(task_id)
        with patch.object(tmdb, "best_match", return_value=(self.HIT, [self.HIT])) as match:
            self.assertEqual(await self.pl.refresh_metadata(), 1)
            self.assertEqual(await self.pl.refresh_metadata(), 0)  # 已补全的任务不再重复查询
        self.assertEqual(match.call_count, 1)
        ctx = self.store.get_task(task_id)
        self.assertEqual((ctx.state, ctx.parsed.raw_fields["poster_path"], ctx.parsed.raw_fields["tmdbid"]),
                         (TaskState.PENDING_APPROVAL, "/poster.jpg", 294446))
        self.assertEqual((ctx.parsed.genre_ids, ctx.parsed.origin_country), ([18], ["CN"]))
        self.assertTrue(ctx.meta["confidence"]["checks"]["tmdb"])
        self.assertEqual(ctx.meta["dir_router"]["preview"], "剧集/国产剧集")
        self.assertTrue(any("补跑元数据补全" in line for line in ctx.trail))
        self.assertEqual(self.updated_at(task_id), before)  # 审批超时不重新计时

    async def test_unmatched_pending_task_is_capped_for_manual_review(self):
        task_id = self.task("星河彼岸的回声")
        with patch.object(tmdb, "best_match", return_value=(None, [])) as match:
            self.assertEqual(await self.pl.refresh_metadata(), 1)
            self.assertEqual(await self.pl.refresh_metadata(), 0)
        self.assertEqual(match.call_count, 1)
        confidence_meta = self.store.get_task(task_id).meta["confidence"]
        self.assertEqual((confidence_meta["score"], confidence_meta["caps"]), (0.45, ["tmdb_unmatched"]))

    async def test_done_task_only_gets_poster_when_title_is_unchanged(self):
        same = self.task("美人余", TaskState.DONE, msg_id="1")
        renamed = self.task("美人余 竖屏版", TaskState.DONE, msg_id="2")
        before = {task_id: self.updated_at(task_id) for task_id in (same, renamed)}
        with patch.object(tmdb, "best_match", return_value=(self.HIT, [self.HIT])) as match:
            self.assertEqual(await self.pl.refresh_metadata(), 2)
            self.assertEqual(await self.pl.refresh_metadata(), 0)
        self.assertEqual(match.call_count, 2)
        kept = self.store.get_task(same)
        self.assertEqual((kept.parsed.title, kept.parsed.raw_fields.get("poster_path")), ("美人余", "/poster.jpg"))
        untouched = self.store.get_task(renamed)
        self.assertEqual((untouched.parsed.title, untouched.parsed.raw_fields.get("poster_path")), ("美人余 竖屏版", None))
        self.assertEqual({task_id: self.updated_at(task_id) for task_id in (same, renamed)}, before)  # 入库统计不变

    async def test_snapshot_refresh_gives_up_when_task_changed_meanwhile(self):
        task_id = self.task("美人余")
        ctx = self.store.get_task(task_id)
        self.assertFalse(self.store.refresh_snapshot(ctx, updated_at=self.updated_at(task_id) - 1))
        self.assertTrue(self.store.refresh_snapshot(ctx, updated_at=self.updated_at(task_id)))

    async def test_nothing_to_refresh_without_tmdb_key(self):
        self.task("美人余")
        pipeline = self.pipeline({"save_dirs": {"默认": "1"}, "defaults": {"save_to": "默认"}}, discover())
        with patch.object(tmdb, "best_match") as match:
            self.assertEqual(await pipeline.refresh_metadata(), 0)
        match.assert_not_called()


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


class TestShareProbe(Base):
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
        self.assertEqual(describe_share(multi, self.parsed)["status"], "issue")
        self.assertIn("多季", describe_share(multi, self.parsed)["note"])
        gone = describe_share(FakeShareFS(error=OSError(5, {"state": False, "error": "访问码错误"})), self.parsed)
        self.assertEqual(gone["status"], "unavailable")
        self.assertIn("访问码错误", gone["note"])
        with self.assertRaises(ConnectionError):  # 临时错误交给调用方稍后重试
            describe_share(FakeShareFS(error=ConnectionError("timeout")), self.parsed)
        huge = FakeShareFS({0: [share_dir(i) for i in range(1, 40)], **{i: [share_dir(100 + i)] for i in range(1, 40)}})
        self.assertEqual(describe_share(huge, self.parsed, max_dirs=5)["status"], "incomplete")
        self.assertEqual(huge.calls, 5)

    async def probe(self, ctx, describe):
        from reelay.plugins import share_probe
        hook = share_probe.ShareProbe()
        with patch.object(share_probe, "get_store", return_value=types.SimpleNamespace(has_cookie=True)), \
                patch.object(share_probe, "PROBE_INTERVAL", 0), \
                patch.object(share_probe.ShareProbe, "_describe", staticmethod(describe)):
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
        for change in ({"episodes": [3]}, {"media_type": "movie"}, {"share_code": None}):
            other = message()
            other.parsed = ParsedResource(**{**self.parsed.__dict__, **change})
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
        service = ManagementService(self.store, self.pipeline(), config_path=str(Path(self.temp.name) / "c.yaml"))
        item = service.inbox()["items"][0]
        self.assertEqual((item["season"], item["episodes"], item["size"]), (1, list(range(1, 11)), "71.1 GB"))
        self.assertEqual(item["share"], {"status": "ok", "note": None, "videos": 10, "from_share": True})

    async def test_pending_tasks_are_read_once_at_startup(self):
        from reelay.plugins import share_probe
        ctx = message()
        ctx.parsed, ctx.state = self.parsed, TaskState.PENDING_APPROVAL
        self.store.save_task(ctx)
        pipeline = self.pipeline(registry=discover())
        describe = staticmethod(lambda parsed: {"status": "unavailable", "note": "分享无法访问：访问码错误"})
        with patch.object(share_probe, "get_store", return_value=types.SimpleNamespace(has_cookie=True)), \
                patch.object(share_probe, "PROBE_INTERVAL", 0), patch.object(share_probe.ShareProbe, "_describe", describe):
            self.assertEqual(await pipeline.refresh_metadata(), 1)
            self.assertEqual(await pipeline.refresh_metadata(), 0)
        self.assertEqual(self.store.get_task(ctx.task_id).meta["share"]["status"], "unavailable")


class TestTmdbMatching(unittest.IsolatedAsyncioTestCase):
    RESULTS = [
        {"id": 1, "title": "完全不同的电影", "release_date": "2026-01-01"},
        {"id": 2, "title": "流浪地球2", "original_title": "流浪地球2", "release_date": "2023-01-22",
         "genre_ids": [878]},
    ]

    def setUp(self):
        tmdb._CACHE.clear()

    def test_best_match_prefers_title_similarity_over_rank(self):
        with patch.object(tmdb, "_request", return_value={"results": self.RESULTS}):
            hit, candidates = tmdb.best_match("movie", "流浪地球2", 2023, language="zh-CN", timeout=1)
        self.assertEqual(hit["id"], 2)
        self.assertEqual(candidates[0]["id"], 2)

    def test_year_conflict_and_low_similarity_are_rejected(self):
        with patch.object(tmdb, "_request", return_value={"results": self.RESULTS}):
            self.assertIsNone(tmdb.best_match("movie", "流浪地球2", 2030, language="zh-CN", timeout=1)[0])
            self.assertIsNone(tmdb.best_match("movie", "毫不相干", None, language="zh-CN", timeout=1)[0])

    def test_results_are_cached(self):
        with patch.object(tmdb, "_request", return_value={"results": self.RESULTS}) as request:
            tmdb.search("movie", "流浪地球2", 2023, language="zh-CN", timeout=1)
            tmdb.search("movie", "流浪地球 2", 2023, language="zh-CN", timeout=1)
        self.assertEqual(request.call_count, 1)

    def test_anime_searches_tv_then_movie(self):
        self.assertEqual(search_kinds("anime", True), ["tv", "movie"])
        self.assertEqual(search_kinds("anime", False), ["movie", "tv"])
        self.assertEqual(search_kinds("movie", False), ["movie"])

    async def test_movie_match_keeps_ai_origin_country(self):
        with patch.dict(os.environ, {"REELAY_TMDB_API_KEY": "k"}, clear=True), \
                patch.object(tmdb, "_request", return_value={"results": self.RESULTS}):
            hook = TmdbEnrich()
            ctx = message()
            ctx.parsed = ParsedResource(is_media=True, title="流浪地球2", year=2023, media_type="movie",
                                        origin_country=["CN"])
            await hook.run(ctx)
        self.assertEqual((ctx.parsed.genre_ids, ctx.parsed.origin_country), ([878], ["CN"]))
        self.assertTrue(ctx.ns("tmdb")["matched"])


class TestDailyAndEpisodeTracker(Base):
    def test_render_skips_quiet_days(self):
        self.assertIsNone(render({"received": 0}, "2026-10-19"))
        text = render({"received": 4, "done_count": 1, "done": [{"title": "剧"}],
                       "received_by_state": {"rejected": 2}, "pending_approval": 3}, "2026-10-19")
        self.assertIn("新收 4 条，入库 1 条，过滤 2 条", text)
        self.assertIn("待审批 3", text)

    async def test_daily_event_is_emitted_once_per_day(self):
        registry = Registry()
        tracker = EpisodeTracker()
        registry.add_listener(tracker)
        pipeline = self.pipeline({"save_dirs": {"默认": "1"}, "defaults": {"save_to": "默认"},
                                  "schedule": {"daily_hour": 0}}, registry)
        self.assertTrue(await pipeline.emit_daily())
        self.assertFalse(await pipeline.emit_daily())
        self.assertEqual(self.store._conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 0)
        row = self.store._conn.execute("SELECT kind, event, target FROM outbox").fetchone()
        self.assertEqual(tuple(row), ("event", "on_daily", "episode_tracker"))

    def test_missing_episode_rules(self):
        self.assertEqual(missing_episodes({20, 21, 23}, total=None, aired=None, from_first=True), [22])
        self.assertEqual(missing_episodes({20, 21}, total=None, aired=24, from_first=True), [22, 23, 24])
        self.assertEqual(missing_episodes({2, 3}, total=4, aired=None, from_first=False), [1, 4])
        self.assertEqual(format_episodes([1, 2, 3, 5]), "E01-E03、E05")

    async def test_tracker_reports_gaps_through_notice(self):
        registry = Registry()
        recorder, tracker = EpisodeRecorder(), EpisodeTracker()
        registry.add_listener(recorder)
        registry.add_listener(tracker)
        pipeline = self.pipeline(registry=registry)
        for episodes in ([20, 21], [23]):
            ctx = message()
            ctx.parsed = ParsedResource(is_media=True, title="一瓯春", season=1, episodes=episodes, share_code="s")
            ctx.save_dir_cid = "1"
            ctx.result = {"storage_verified": True}
            ctx.ns("storage")["identity"] = {"cid": "1", "share_code": f"s{episodes[0]}", "title": "一瓯春",
                                             "season": 1, "episodes": episodes}
            self.store.save_task(ctx)
            self.store.finish_transfer(ctx)
            with pipeline._scope(pipeline.cfg):
                await recorder.handle(ctx)
        with pipeline._scope(pipeline.cfg):
            await tracker.handle(message())
        text = json.loads(self.store._conn.execute(
            "SELECT snapshot FROM outbox WHERE kind='notice'").fetchone()[0])["text"]
        self.assertIn("一瓯春 S01：已入库 3 集，缺 E22", text)

    def test_subscription_config_is_validated(self):
        with self.assertRaises(ValueError):
            EpisodeTracker().validate_config({"episode_tracker": {"subscriptions": [{"season": 1}]}})
        EpisodeTracker().validate_config({"episode_tracker": {"subscriptions": [{"title": "剧", "total": 12}]}})


class TestExternalPlugins(unittest.TestCase):
    def test_plugin_dir_is_loaded_and_broken_plugins_are_isolated(self):
        with tempfile.TemporaryDirectory() as root:
            Path(root, "audit_hook.py").write_text(
                "from reelay.core.registry import register\n"
                "@register(kind='hook', stage='after_parse', order=99)\n"
                "class AuditHook:\n"
                "    name = 'audit_hook'\n"
                "    async def run(self, ctx):\n"
                "        return True\n", encoding="utf-8")
            Path(root, "broken.py").write_text("raise RuntimeError('boom')\n", encoding="utf-8")
            with patch.dict(os.environ, {"REELAY_PLUGIN_DIRS": root}, clear=True):
                registry = discover()
                again = discover()
        names = [registry.extension_name(h) for h in registry.hooks.get("after_parse", [])]
        self.assertIn("audit_hook", names)
        self.assertEqual(sum(1 for h in again.hooks["after_parse"] if h.name == "audit_hook"), 1)
        self.assertTrue(any("broken.py" in e["source"] for e in registry.load_errors))
        with patch.dict(os.environ, {}, clear=True):
            plain = discover()
        self.assertNotIn("audit_hook", [h.name for h in plain.hooks.get("after_parse", [])])

    def test_opt_in_extensions_default_disabled(self):
        with patch.dict(os.environ, {}, clear=True):
            registry = discover()
        names = [registry.extension_name(h) for h in registry.hooks_for("after_save", {})]
        self.assertNotIn("strm", names)
        self.assertIn("strm", [registry.extension_name(h)
                               for h in registry.hooks_for("after_save", {"plugins": {"strm": True}})])


class TestEntrypointsLoadEnvFirst(unittest.TestCase):
    """本地直接运行时，.env 里的日志与监听配置必须在被读取之前加载。"""

    def test_main_loads_env_before_setting_up_logging(self):
        from reelay import main as entry
        seen = {}

        def fake_run(coro):
            coro.close()
            return 0

        with tempfile.TemporaryDirectory() as root:
            Path(root, ".env").write_text("REELAY_LOG_FILE=data/reelay.log\nREELAY_LOG_LEVEL=DEBUG\n",
                                          encoding="utf-8")
            cwd = os.getcwd()
            os.chdir(root)
            try:
                with patch.dict(os.environ, {}, clear=True), patch("sys.argv", ["reelay"]), \
                        patch.object(entry, "_setup_log", lambda: seen.update(os.environ)), \
                        patch.object(entry.asyncio, "run", fake_run), self.assertRaises(SystemExit):
                    entry.main()
            finally:
                os.chdir(cwd)
        self.assertEqual(seen.get("REELAY_LOG_FILE"), "data/reelay.log")
        self.assertEqual(seen.get("REELAY_LOG_LEVEL"), "DEBUG")

    def test_web_main_reads_host_and_port_from_env_file(self):
        try:
            import uvicorn  # noqa: F401
        except ImportError:  # pragma: no cover
            self.skipTest("未安装 web 可选依赖")
        from reelay import web
        with tempfile.TemporaryDirectory() as root:
            Path(root, ".env").write_text("REELAY_WEB_HOST=0.0.0.0\nREELAY_WEB_PORT=28181\n", encoding="utf-8")
            with patch.dict(os.environ, {}, clear=True), patch.object(web, "PROJECT_ROOT", Path(root)), \
                    patch("uvicorn.run") as run:
                web.main()
        self.assertEqual((run.call_args.kwargs["host"], run.call_args.kwargs["port"]), ("0.0.0.0", 28181))


@unittest.skipIf(TestClient is None, "未安装 web 可选依赖")
class TestWebAuthGuard(unittest.TestCase):
    def setUp(self):
        from reelay import web_extras
        from reelay.web import create_app
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.env = patch.dict(os.environ, {"REELAY_WEB_PASSWORD": "pw", "REELAY_STRM_SECRET": "s"}, clear=True)
        self.env.start()
        self.loader = patch("reelay.web.load_env")
        self.loader.start()
        web_extras._SESSIONS.clear()
        web_extras._FAILURES.clear()
        (root / "config.yaml").write_text(json.dumps({"save_dirs": {"默认": "0"}, "defaults": {"save_to": "默认"}}),
                                          encoding="utf-8")
        self.app = create_app(db_path=str(root / "t.db"), config_path=str(root / "config.yaml"))
        self.client = TestClient(self.app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.loader.stop()
        self.env.stop()
        self.temp.cleanup()

    def api_routes(self):
        for route in self.app.routes:
            path = getattr(route, "path", "")
            if path.startswith("/api/"):
                for method in sorted(getattr(route, "methods", set()) - {"HEAD", "OPTIONS"}):
                    yield method, path.replace("{task_id}", "x").replace("{delivery_id}", "1") \
                        .replace("{pickcode}", "abc")

    def test_every_non_public_api_route_requires_login(self):
        from reelay.web_extras import PUBLIC_PATHS, PUBLIC_PREFIXES
        checked = 0
        for method, path in self.api_routes():
            if path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES):
                continue
            response = self.client.request(method, path)
            self.assertEqual(response.status_code, 401, f"{method} {path}")
            checked += 1
        self.assertGreater(checked, 30)

    def test_login_grants_access_and_forged_or_expired_sessions_fail(self):
        from reelay import web_extras
        self.assertEqual(self.client.get("/api/events", cookies={"reelay_session": "forged"}).status_code, 401)
        self.assertEqual(self.client.get("/api/auth/status").json(), {"auth_required": True, "authenticated": False})
        self.assertEqual(self.client.post("/api/auth/login", json={"password": "pw"}).status_code, 200)
        self.assertEqual(self.client.get("/api/auth/status").json()["authenticated"], True)
        self.assertEqual(self.client.get("/api/config").status_code, 200)
        for token in web_extras._SESSIONS:
            web_extras._SESSIONS[token] = time.time() - 1
        self.assertEqual(self.client.get("/api/config").status_code, 401)

    def test_login_is_throttled_after_repeated_failures(self):
        codes = [self.client.post("/api/auth/login", json={"password": "bad"}).status_code for _ in range(6)]
        self.assertEqual(codes, [401] * 5 + [429])
        self.assertEqual(self.client.post("/api/auth/login", json={"password": "pw"}).status_code, 429)

    def test_parse_preview_matches_pipeline_decision(self):
        self.client.post("/api/auth/login", json={"password": "pw"})
        response = self.client.post("/api/sources/test-parse",
                                    json={"text": f"📺 剧 (2026) 第3集\n{SHARE}", "urls": [SHARE]})
        body = response.json()
        self.assertEqual(response.status_code, 200, body)
        self.assertEqual(body["parsed"]["title"], "剧")
        self.assertEqual(body["decision"], "ask")  # 未配 LLM：降级结果只能人工确认，而不是被当作“非影视”跳过
        self.assertLess(body["confidence"]["score"], 0.5)

    def test_stats_counts_and_size_use_app_store(self):
        self.client.post("/api/auth/login", json={"password": "pw"})
        store = self.app.state.store
        for size in ("1.5 GB", "512 MB"):
            ctx = message(msg_id=size)
            ctx.parsed = ParsedResource(is_media=True, title="片", size=size)
            ctx.state = TaskState.DONE
            store.save_task(ctx)
        body = self.client.get("/api/stats").json()
        self.assertEqual(body["counts"], {"done": 2})
        self.assertEqual(body["estimated_done_gb"], 2.0)
        self.assertEqual(self.client.get("/api/tasks/advanced").status_code, 200)
        self.assertEqual(self.client.get("/api/stats").status_code, 200)  # 共享连接未被关闭

    def test_health_hides_runtime_details_without_login(self):
        self.assertEqual(self.client.get("/api/health").json(), {"ok": True, "service": "reelay"})

    def test_strm_redirect_requires_valid_signature(self):
        from reelay.core.strm import sign
        self.assertEqual(self.client.get("/api/strm/p115/abc?sig=bad", follow_redirects=False).status_code, 403)
        fake = types.SimpleNamespace(download_url=lambda pickcode, user_agent=None: f"https://cdn/{pickcode}")
        with patch("reelay.core.auth115.CredentialStore.cookie_client", return_value=fake):
            response = self.client.get(f"/api/strm/p115/abc?sig={sign('abc')}", follow_redirects=False)
        self.assertEqual((response.status_code, response.headers["location"]), (302, "https://cdn/abc"))


if __name__ == "__main__":
    unittest.main()


@unittest.skipIf(TestClient is None, "未安装 web 可选依赖")
class TestConsoleApi(unittest.TestCase):
    """新管理台用到的聚合接口：收件箱、带媒体字段的任务列表、扩展目录去重。"""

    def setUp(self):
        from reelay.web import create_app
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.env = patch.dict(os.environ, {"REELAY_115_OAUTH_FILE": str(root / "cred.json")}, clear=True)
        self.env.start()
        self.loader = patch("reelay.web.load_env")
        self.loader.start()
        (root / "config.yaml").write_text(json.dumps({"save_dirs": {"默认": "1", "剧集": "2"},
                                                      "defaults": {"save_to": "默认"}}), encoding="utf-8")
        self.app = create_app(db_path=str(root / "t.db"), config_path=str(root / "config.yaml"))
        self.client = TestClient(self.app)
        self.client.__enter__()
        store = self.app.state.store
        for index, state in enumerate((TaskState.PENDING_APPROVAL, TaskState.PENDING_APPROVAL,
                                       TaskState.NEED_MANUAL, TaskState.QUEUED, TaskState.DONE)):
            ctx = message(msg_id=str(index), text=f"剧 第{index}集\n{SHARE}")
            ctx.parsed = ParsedResource(is_media=True, title=f"剧{index}", media_type="tv", season=1,
                                        episodes=[index], resolution="4K", size="1 GB", share_code="abc",
                                        raw_fields={"poster_path": f"/p{index}.jpg"})
            ctx.state = state
            ctx.ns("confidence").update(score=0.4, caps=["tmdb_unmatched"])
            store.save_task(ctx, error="同名冲突" if state == TaskState.NEED_MANUAL else None)

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.loader.stop()
        self.env.stop()
        self.temp.cleanup()

    def test_inbox_returns_card_fields_without_links(self):
        body = self.client.get("/api/inbox").json()
        self.assertEqual(body["save_dirs"], ["默认", "剧集"])
        self.assertEqual(len(body["items"]), 2)
        item = body["items"][0]
        self.assertEqual((item["media_type"], item["resolution"], item["confidence_caps"]), ("tv", "4K", ["tmdb_unmatched"]))
        self.assertTrue(item["poster_path"].startswith("/p"))
        self.assertEqual((item["save_dir_alias"], item["auto_dir"]), (None, "默认"))
        self.assertNotIn("http", item["preview"])
        manual = self.client.get("/api/inbox?state=need_manual").json()["items"]
        self.assertEqual([m["error"] for m in manual], ["同名冲突"])
        self.assertEqual(self.client.get("/api/inbox?state=done").status_code, 422)

    def test_task_list_supports_multiple_states_paging_and_media_fields(self):
        items = self.client.get("/api/tasks?state=pending_approval,queued&limit=2").json()["items"]
        self.assertEqual(len(items), 2)
        self.assertTrue({i["state"] for i in items} <= {"pending_approval", "queued"})
        self.assertEqual(items[0]["episodes"], [int(items[0]["title"][-1])])
        rest = self.client.get("/api/tasks?state=pending_approval,queued&limit=2&offset=2").json()["items"]
        self.assertEqual(len(rest), 1)

    def test_batch_approve_can_redirect_target_directory(self):
        ids = [i["task_id"] for i in self.client.get("/api/inbox").json()["items"]]
        result = self.client.post("/api/tasks/batch", json={"task_ids": ids, "action": "approve",
                                                             "save_dir_alias": "剧集"}).json()
        self.assertEqual(result["done"], 2)
        store = self.app.state.store
        self.assertEqual({store.get_task(i).save_dir_alias for i in ids}, {"剧集"})

    def test_dashboard_has_recent_counts_and_catalog_merges_multi_event_extensions(self):
        self.assertEqual(self.client.get("/api/dashboard").json()["recent"]["received_24h"], 5)
        events = self.client.get("/api/extensions").json()["events"]
        tracker = [e for e in events if e["name"] == "episode_tracker"]
        self.assertEqual(len(tracker), 1)
        self.assertEqual(set(tracker[0]["slot"].split(",")), {"on_saved", "on_daily"})
