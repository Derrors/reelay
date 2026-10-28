"""管线：解析、决策、钩子、配置热更新、置信度、选目录、元数据刷新。"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from reelay.core import confidence, runtime, secret_store, tmdb
from reelay.core.config import load_config, normalize_config
from reelay.core.contracts import Decision, ParsedResource, PipelineContext, RawMessage, TaskState
from reelay.core.health import check_worker
from reelay.core.pipeline import Pipeline
from reelay.core.registry import Registry, discover
from reelay.core.service import ManagementService
from reelay.core.store import Store
from reelay.core.worker import Worker
from reelay.providers.decision import DefaultDecision, LLMDecision
from reelay.providers.notify import ConsoleNotify
from reelay.providers.parser import LLMParser, RegexParser
from reelay.providers.source import TelegramUserbotSource

from tests.fakes import APITimeoutError, FakeAI, PipelineCase, SHARE, media, message


def context(msg_id="1"):
    return PipelineContext(RawMessage("@test", msg_id, "电影 (2026) 4K https://115.com/s/actual?password=abcd",
                                      ["https://115.com/s/actual?password=abcd"]))


def movie():
    return media(media_type="movie", title="电影", season=None, episodes=[], genres=["动画"], genre_ids=[],
                 origin_country=[], resolution="4K", confidence=0.9, reason="影视分享")


class TestPipeline(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.env = patch.dict("os.environ", {}, clear=True)
        self.env.start()
        self.temp = tempfile.TemporaryDirectory()
        self.stores = []

    def tearDown(self):
        for store in self.stores:
            store.close()
        self.temp.cleanup()
        self.env.stop()

    def store(self, name=None):
        store = Store(Path(self.temp.name) / name if name else ":memory:")
        self.stores.append(store)
        return store

    def registry(self):
        return discover()

    async def test_schema_and_hidden_url_are_sent_and_result_is_validated(self):
        parser = LLMParser()
        parser._client = FakeAI(movie())
        ctx = context()
        result = await parser.parse(ctx)
        self.assertEqual(result.genres, ["动画"])
        self.assertEqual((result.share_code, result.receive_code), ("actual", "abcd"))
        call = parser._client.calls[0]
        self.assertEqual(call["response_format"]["type"], "json_schema")
        self.assertTrue(call["response_format"]["json_schema"]["strict"])
        self.assertIn("urls", json.loads(call["messages"][1]["content"]))

    async def test_invalid_ai_values_always_fall_back_to_manual(self):
        changes = [{"is_ad": "false"}, {"is_media": "true"}, {"confidence": 2},
                   {"confidence": float("nan")}, {"confidence": True}, {"year": "2026"},
                   {"season": True}, {"episodes": ["bad"]}, {"episodes": [1, 1]},
                   {"genres": "动画"}, {"media_type": "unknown"}, {"title": None}, {"extra": 1}]
        for change in changes:
            with self.subTest(change=change):
                ctx = context()
                ctx.source_rule = {"approval_mode": "auto"}
                parser = LLMParser()
                parser._client = FakeAI({**movie(), **change})
                ctx.parsed = await parser.parse(ctx)
                self.assertTrue(ctx.meta["parse"]["fallback"])
                self.assertEqual(await DefaultDecision().decide(ctx), Decision.ASK)

    async def test_timeout_refusal_and_missing_credentials_require_approval(self):
        for client in (None, FakeAI(TimeoutError("secret must not be persisted")), FakeAI(movie(), refusal="no")):
            ctx = context()
            ctx.source_rule = {"approval_mode": "auto"}
            parser = LLMParser()
            parser._client = client
            ctx.parsed = await parser.parse(ctx)
            self.assertEqual(await DefaultDecision().decide(ctx), Decision.ASK)
            self.assertNotIn("secret", ctx.parsed.reason)

    async def test_explicit_regex_can_auto_save_and_clears_old_fallback(self):
        ctx = context()
        ctx.source_rule = {"approval_mode": "auto"}
        ctx.ns("parse")["fallback"] = True
        ctx.parsed = await RegexParser().parse(ctx)
        self.assertEqual(await DefaultDecision().decide(ctx), Decision.SAVE)

    async def test_custom_decider_cannot_auto_save_llm_fallback(self):
        class UnsafeDecision:
            name = "unsafe"
            async def decide(self, ctx):
                return Decision.SAVE
        reg = self.registry()
        reg.add_provider("decision", UnsafeDecision())
        pipeline = Pipeline(reg, self.store(), {"defaults": {"decision": "unsafe", "approval_mode": "auto"}})
        ctx = context()
        await pipeline.process_new(ctx)
        self.assertEqual(ctx.state, TaskState.PENDING_APPROVAL)

    async def test_natural_preferences_only_decide_whether_to_save_and_respect_manual(self):
        provider = LLMDecision()
        cfg = normalize_config({})
        for mode, expected in (("auto", Decision.SAVE), ("manual", Decision.ASK)):
            ctx = context()
            ctx.parsed = ParsedResource(**movie(), share_code="actual")
            ctx.source_rule = {"preferences": "只收 4K，存入动漫", "approval_mode": mode}
            provider._client = FakeAI({"decision": "save", "reason": "匹配偏好"})
            with runtime.scope(cfg):
                self.assertEqual(await provider.decide(ctx), expected)
            self.assertIsNone(ctx.save_dir_alias)  # 保存位置按「保存到」与二级分类，偏好里写了目录也不改
        request = provider._client.calls[0]
        self.assertEqual(set(request["response_format"]["json_schema"]["schema"]["properties"]), {"decision", "reason"})
        self.assertNotIn("allowed_dirs", request["messages"][1]["content"])
        provider._client = FakeAI({"decision": "save", "save_to": "动漫", "reason": ""})  # 多出字段：格式无效
        with runtime.scope(cfg):
            self.assertEqual(await provider.decide(ctx), Decision.ASK)
        self.assertIsNone(ctx.save_dir_alias)
        ctx.parsed.is_ad = True
        provider._client.calls.clear()
        with runtime.scope(cfg):
            self.assertEqual(await provider.decide(ctx), Decision.SKIP)
        self.assertEqual(provider._client.calls, [])

    async def test_natural_preferences_unavailable_cannot_auto_save(self):
        provider = LLMDecision()
        provider._client = None
        ctx = context()
        ctx.parsed = ParsedResource(**movie(), share_code="actual")
        ctx.source_rule = {"preferences": "只收动漫", "approval_mode": "auto"}
        self.assertEqual(await provider.decide(ctx), Decision.ASK)

    async def test_source_forward_overrides_and_silent_success(self):
        sent = []
        async def send(**kwargs):
            sent.append(kwargs)
        provider = ConsoleNotify()
        provider.configure(sender=send, forward_cfg={"enabled": True, "target": "global", "template": "{title}"})
        pipeline = Pipeline(self.registry(), self.store(), {
            "forward": {"enabled": True, "target": "global", "template": "{title}"},
            "sources": [{"bot": "@test", "forward": {"target": "local"}},
                        {"bot": "@off", "forward": {"enabled": False}}],
        })
        ctx = context()
        ctx.parsed = ParsedResource(**movie())
        ctx.source_rule = pipeline.rule_for("@TEST")
        await provider.notify(ctx, "identified")
        self.assertEqual(sent[-1]["chat"], "local")
        ctx.source_rule = pipeline.rule_for("@off")
        await provider.notify(ctx, "identified")
        self.assertEqual(len(sent), 1)
        await provider.notify(ctx, "organize_failed")
        self.assertEqual(len(sent), 2)

    def test_confirmation_modes_and_bot_notify_are_validated(self):
        base = {"save_dirs": {"默认": "1"}}
        cfg = normalize_config({**base, "sources": [{"bot": "@a", "approval_mode": "auto", "bot_notify": False}]})
        self.assertEqual((cfg["defaults"]["approval_mode"], cfg["defaults"]["bot_notify"]), ("manual", True))
        for defaults in ({"approval_mode": "default_pass"}, {"bot_notify": "off"}):
            with self.subTest(defaults), self.assertRaises(ValueError):
                normalize_config({**base, "defaults": defaults})

    async def test_bot_notify_off_skips_cards_and_saved_but_not_problems(self):
        pipeline = Pipeline(self.registry(), self.store(), {})
        ctx = context()
        ctx.source_rule = {"approval_mode": "auto", "bot_notify": False}
        pipeline.store.save_task(ctx)
        await pipeline.notify_all(ctx, "saved")
        await pipeline.notify_all(ctx, "pending_approval")
        self.assertEqual(pipeline.store.delivery_counts(), {})
        await pipeline.notify_all(ctx, "transfer_uncertain")
        self.assertEqual(pipeline.store.delivery_counts(), {"pending": 1})

    async def test_each_pipeline_has_its_own_coroutine_config(self):
        class Hook:
            name, stage, order = "test", "after_parse", 1
            async def run(self, ctx):
                await asyncio.sleep(0)
                ctx.ns("test")["value"] = runtime.plugin_config("test")["marker"]
                return True
        reg = Registry()
        reg.add_hook(Hook())
        a, b = Pipeline(reg, self.store(), {"plugins": {"test": {"marker": "a"}}}), \
            Pipeline(reg, self.store(), {"plugins": {"test": {"marker": "b"}}})
        ca, cb = context("a"), context("b")
        await asyncio.gather(a.run_hooks("after_parse", ca), b.run_hooks("after_parse", cb))
        self.assertEqual(ca.meta["test"]["value"], "a")
        self.assertEqual(cb.meta["test"]["value"], "b")

    async def test_live_worker_reloads_config_and_keeps_existing_task_policy(self):
        path = Path(self.temp.name) / "config.yaml"
        path.write_text(json.dumps({"defaults": {"parser": "regex", "approval_mode": "manual"}}))
        store = self.store("shared.db")
        pipeline = Pipeline(self.registry(), store, load_config(path))
        service = ManagementService(store, pipeline, config_path=str(path))
        peer = Pipeline(self.registry(), self.store("shared.db"), load_config(path))
        ManagementService(peer.store, peer, config_path=str(path))
        old = context()
        await peer.accept(old)
        old_revision = peer.revision
        worker = Worker(peer, peer.store)
        task = asyncio.create_task(worker.run())
        try:
            await service.save_config({"defaults": {"parser": "regex", "approval_mode": "auto"}, "save_dirs": {"默认": "222"}})
            async def wait_updated():
                while peer.revision == old_revision:
                    await asyncio.sleep(0.01)
            await asyncio.wait_for(wait_updated(), timeout=2)
            self.assertEqual(peer.save_dirs["默认"], "222")
            self.assertEqual(peer.store.get_task(old.task_id).source_rule["approval_mode"], "manual")
            fresh = context("2")
            await peer.accept(fresh)
            self.assertEqual(fresh.source_rule["approval_mode"], "auto")
            self.assertEqual(peer.store.health_status()["worker"]["config_revision"], pipeline.revision)
            path.write_text("[invalid")
            current = peer.revision
            self.assertFalse(peer.reload_config())
            self.assertEqual(peer.revision, current)
            self.assertIsNotNone(peer.config_error)
        finally:
            worker.stop()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_bad_config_is_rejected_before_writing_and_unknown_provider_is_not_selected(self):
        path = Path(self.temp.name) / "config.yaml"
        pipeline = Pipeline(self.registry(), self.store(), {})
        service = ManagementService(pipeline.store, pipeline, config_path=str(path))
        bad = [{"defaults": {"approval_mode": "wat"}}, {"forward": {"enabled": "false"}},
               {"defaults": {"confidence_threshold": 2}}, {"defaults": {"parser": "missing"}},
               {"plugins": {"missing": {"enabled": False}}}, {"defaults": {"filters": {"must_have": "115"}}},
               {"sources": ["wrong"]}]
        for cfg in bad:
            with self.subTest(cfg=cfg), self.assertRaises(ValueError):
                await service.save_config(cfg)
        self.assertFalse(path.exists())

    async def test_default_config_does_not_share_mutable_nested_objects(self):
        a = normalize_config({})
        a["defaults"]["parser"] = "changed"
        a["plugins"]["organizer"]["naming"]["tv"] = "changed"
        b = normalize_config({})
        self.assertEqual(b["defaults"]["parser"], "llm")
        self.assertNotEqual(b["plugins"]["organizer"]["naming"]["tv"], "changed")
        path = Path(self.temp.name) / "bad.yaml"
        path.write_text("[broken")
        with self.assertRaises(ValueError):
            load_config(path)

    async def test_patch_and_preview_reject_invalid_field_types(self):
        pipeline = Pipeline(self.registry(), self.store(), {})
        service = ManagementService(pipeline.store, pipeline, config_path=str(Path(self.temp.name) / "cfg.yaml"))
        ctx = context()
        ctx.state = TaskState.PENDING_APPROVAL
        ctx.parsed = ParsedResource(**movie())
        pipeline.store.save_task(ctx)
        for patch_value in ({"episodes": [True]}, {"is_ad": "false"}, {"year": "2026"}):
            with self.assertRaises(ValueError):
                service.patch_parsed(ctx.task_id, patch_value)
        self.assertEqual(pipeline.store.get_task(ctx.task_id).parsed.year, 2026)
        preview = pipeline.reg.action("organizer", "preview_plan")
        for payload in ({"parsed": {"season": "one"}, "landed": []}, {"parsed": movie(), "landed": "a.mkv"}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                preview(payload)

    async def test_plugins_can_be_disabled_per_task_without_dropping_registered_instances(self):
        class Hook:
            name, stage, order = "test", "after_parse", 1
            async def run(self, ctx):
                ctx.ns("test")["called"] = True
                return True
        reg = Registry()
        reg.add_hook(Hook())
        pipeline = Pipeline(reg, self.store(), {"plugins": {"test": {"enabled": False}}})
        ctx = context()
        await pipeline.run_hooks("after_parse", ctx)
        self.assertNotIn("test", ctx.meta)
        pipeline.cfg["plugins"]["test"] = {"enabled": True}
        await pipeline.run_hooks("after_parse", ctx)
        self.assertTrue(ctx.meta["test"]["called"])

    async def test_source_targets_are_dynamic_and_empty_does_not_match_everyone(self):
        source = TelegramUserbotSource()
        event = types.SimpleNamespace(chat=types.SimpleNamespace(username="Test"), sender_id=1, chat_id=1)
        self.assertFalse(source.matches(event))
        source.configure(["@test"])
        self.assertTrue(source.matches(event))
        source.configure(["@other"])
        self.assertFalse(source.matches(event))
        source.configure(["1"])
        self.assertTrue(source.matches(event))

    async def test_runtime_health_is_shared_stale_and_not_a_credentials_probe(self):
        store = self.store("health.db")
        self.assertFalse(check_worker(store.db_path))
        store.heartbeat("worker", status="running", config_revision="v1")
        store.heartbeat("source", status="connected")
        peer = self.store("health.db")
        self.assertTrue(check_worker(store.db_path))
        self.assertFalse(peer.health_status()["worker"]["stale"])
        with patch("reelay.core.health.time.time", return_value=time.time() + 60):
            self.assertFalse(check_worker(store.db_path))
            self.assertTrue(peer.health_status()["worker"]["stale"])
        store.heartbeat("worker", status="stopped")
        self.assertFalse(check_worker(store.db_path))

    async def test_storage_provider_checkpoint_survives_config_change(self):
        class Storage:
            def __init__(self, name):
                self.name, self.saves, self.checks = name, 0, 0
            async def prepare(self, ctx):
                return {"staging_cid": "1"}
            async def save(self, ctx):
                self.saves += 1
                raise asyncio.CancelledError()
            async def reconcile(self, ctx):
                self.checks += 1
                return {"storage_verified": True, "landed": [{"id": "file", "name": "film.mkv"}]}
        reg = self.registry()
        a, b = Storage("a"), Storage("b")
        reg.add_provider("storage", a)
        reg.add_provider("storage", b)
        cfg = {"defaults": {"storage": "a", "parser": "regex", "approval_mode": "auto"},
               "plugins": {"organizer": {"enabled": False}}, "providers": {"notify": []}}
        pipeline = Pipeline(reg, self.store(), cfg)
        ctx = context()
        await pipeline.process_new(ctx)
        with self.assertRaises(asyncio.CancelledError):
            await pipeline.execute_save(ctx)
        cfg["defaults"]["storage"] = "b"
        pipeline.apply_config(cfg)
        restored = pipeline.store.recover_interrupted()[0]
        restored.source_rule["storage"] = "b"
        await pipeline.execute_save(restored)
        self.assertEqual((a.saves, a.checks, b.saves, b.checks), (1, 1, 0, 0))
        self.assertEqual(restored.state, TaskState.DONE)
        self.assertTrue(pipeline.store.saved_share("actual", "0", "a"))
        self.assertFalse(pipeline.store.saved_share("actual", "0", "b"))
        self.assertFalse(pipeline.store.saved_share("actual", "0", "p115"))

    async def test_slow_listener_does_not_block_accept_and_survives_reopen(self):
        gate, started = asyncio.Event(), asyncio.Event()
        class Listener:
            name, event = "slow", "on_message"
            async def handle(self, ctx):
                started.set()
                await gate.wait()
                ctx.raw.text = "listener mutation"
        reg = Registry()
        reg.add_listener(Listener())
        path = "events.db"
        store = self.store(path)
        pipeline = Pipeline(reg, store, {})
        ctx = context()
        await asyncio.wait_for(pipeline.accept(ctx), timeout=0.2)
        self.assertFalse(started.is_set())
        store.close()
        self.stores.remove(store)
        reopened = self.store(path)
        pipeline = Pipeline(reg, reopened, {})
        dispatch = asyncio.create_task(pipeline.dispatch_deliveries())
        await asyncio.wait_for(started.wait(), timeout=0.2)
        self.assertEqual(reopened.get_task(ctx.task_id).raw.text, ctx.raw.text)
        gate.set()
        await dispatch
        self.assertEqual(reopened.delivery_counts(), {"done": 1})
        self.assertNotEqual(reopened.get_task(ctx.task_id).raw.text, "listener mutation")

    async def test_listener_failure_isolated_retry_bounded_and_does_not_repeat_success(self):
        calls = []
        class Bad:
            name, event = "bad", "on_message"
            async def handle(self, ctx):
                calls.append("bad")
                raise ValueError("private secret")
        class Good:
            name, event = "good", "on_message"
            async def handle(self, ctx):
                calls.append("good")
        reg = Registry()
        reg.add_listener(Bad())
        reg.add_listener(Good())
        store = self.store()
        pipeline = Pipeline(reg, store, {})
        await pipeline.accept(context())
        await pipeline.dispatch_deliveries()
        self.assertEqual(store.delivery_counts(), {"done": 1, "pending": 1})
        for _ in range(7):
            store._conn.execute("UPDATE outbox SET next_attempt=0 WHERE status='pending'")
            store._conn.commit()
            await pipeline.dispatch_deliveries()
        self.assertEqual(calls.count("good"), 1)
        self.assertEqual(store.delivery_counts(), {"done": 1, "failed": 1})
        failed = store.list_deliveries()[0]
        self.assertEqual(failed["error"], "ValueError")
        self.assertNotIn("snapshot", failed)
        self.assertTrue(store.retry_delivery(failed["id"]))
        self.assertFalse(store.retry_delivery(failed["id"]))
        self.assertEqual(store.delivery_counts(), {"done": 1, "pending": 1})

    async def test_task_and_event_intent_commit_or_rollback_together(self):
        class Listener:
            name, event = "event", "on_message"
            async def handle(self, ctx):
                pass
        reg = Registry()
        reg.add_listener(Listener())
        store = self.store()
        store._conn.execute("CREATE TRIGGER fail_event BEFORE INSERT ON outbox BEGIN SELECT RAISE(ABORT, 'disk failure'); END")
        pipeline = Pipeline(reg, store, {})
        with self.assertRaises(sqlite3.IntegrityError):
            await pipeline.accept(context())
        self.assertEqual(store.counts_by_state(), {})
        self.assertEqual(store.delivery_counts(), {})
        self.assertEqual(store._conn.execute("SELECT COUNT(*) FROM dedup").fetchone()[0], 0)

    async def test_delivery_lease_excludes_second_connection_and_can_expire(self):
        class Listener:
            name, event = "event", "on_message"
            async def handle(self, ctx):
                pass
        reg = Registry()
        reg.add_listener(Listener())
        store = self.store("leases.db")
        await Pipeline(reg, store, {}).accept(context())
        rows = store.claim_deliveries()
        peer = self.store("leases.db")
        self.assertEqual(peer.claim_deliveries(), [])
        with patch("reelay.core.store.time.time", return_value=time.time() + 70):
            reclaimed = peer.claim_deliveries()
        self.assertEqual(reclaimed[0]["id"], rows[0]["id"])

    async def test_ordered_notifications_and_stale_approval_are_not_resent(self):
        class Notify:
            name = "test"
            def __init__(self):
                self.events = []
            async def notify(self, ctx, event):
                self.events.append((event, ctx.state))
        reg = self.registry()
        notify = Notify()
        reg.add_provider("notify", notify)
        pipeline = Pipeline(reg, self.store(), {"defaults": {"parser": "regex"}, "providers": {"notify": ["test"]}})
        ctx = context()
        await pipeline.process_new(ctx)
        await pipeline.dispatch_deliveries()
        self.assertEqual(notify.events, [("identified", TaskState.PENDING_APPROVAL)])
        await pipeline.approve(ctx.task_id)
        await pipeline.dispatch_deliveries()
        self.assertEqual(len(notify.events), 1)
        self.assertEqual(pipeline.store.delivery_counts(), {"done": 1, "skipped": 1})

    async def test_source_selection_and_targets_change_without_restarting_worker(self):
        from reelay.main import _source_loop
        class Source:
            def __init__(self, name):
                self.name, self.targets, self.closed = name, [], False
                self.started = asyncio.Event()
            def configure(self, targets):
                self.targets = targets
            async def listen(self, emit):
                self.started.set()
                await asyncio.Event().wait()
            async def close(self):
                self.closed = True
            def health(self):
                return {"status": "connected" if self.started.is_set() else "disconnected"}
        reg = self.registry()
        a, b = Source("a"), Source("b")
        reg.add_provider("source", a)
        reg.add_provider("source", b)
        cfg = {"providers": {"source": "a"}, "sources": [{"bot": "@a"}]}
        pipeline = Pipeline(reg, self.store(), cfg)
        manager = asyncio.create_task(_source_loop(pipeline, lambda raw: None, poll_interval=0.01))
        try:
            await asyncio.wait_for(a.started.wait(), 0.5)
            cfg["sources"] = [{"bot": "@updated"}]
            pipeline.apply_config(cfg)
            async def wait_targets():
                while a.targets != ["@updated"]:
                    await asyncio.sleep(0.01)
            await asyncio.wait_for(wait_targets(), 0.5)
            cfg["providers"]["source"] = "b"
            pipeline.apply_config(cfg)
            await asyncio.wait_for(b.started.wait(), 0.5)
            self.assertTrue(a.closed)
            self.assertEqual(b.targets, ["@updated"])
        finally:
            manager.cancel()
            await asyncio.gather(manager, return_exceptions=True)
        self.assertTrue(b.closed)
        self.assertEqual(pipeline.store.health_status()["source"]["status"], "stopped")

    async def test_source_failure_health_is_not_overwritten_as_normal_shutdown(self):
        from reelay.main import _source_loop
        class Source:
            name = "bad"
            async def listen(self, emit):
                raise ValueError("private error details")
        reg = self.registry()
        reg.add_provider("source", Source())
        pipeline = Pipeline(reg, self.store(), {"providers": {"source": "bad"}})
        with self.assertRaises(ValueError):
            await _source_loop(pipeline, lambda raw: None, poll_interval=0.01)
        health = pipeline.store.health_status()["source"]
        self.assertEqual(health["status"], "failed")
        self.assertEqual(health["error"], "ValueError")

    async def test_flood_wait_is_scheduled_with_server_delay(self):
        class FloodWait(Exception):
            seconds = 120
        class Listener:
            name, event = "rate_limited", "on_message"
            async def handle(self, ctx):
                raise FloodWait()
        reg = Registry()
        reg.add_listener(Listener())
        store = self.store()
        pipeline = Pipeline(reg, store, {})
        await pipeline.accept(context())
        before = time.time()
        await pipeline.dispatch_deliveries()
        row = store.list_deliveries(status="pending")[0]
        self.assertGreaterEqual(row["next_attempt"], before + 120)
        self.assertEqual(store.claim_deliveries(), [])

    async def test_reparse_uses_new_policy_and_suppresses_old_generation_notifications(self):
        class Notify:
            name = "test"
            def __init__(self):
                self.events = []
            async def notify(self, ctx, event):
                self.events.append(event)
        reg = self.registry()
        notify = Notify()
        reg.add_provider("notify", notify)
        cfg = {"defaults": {"parser": "llm"}, "providers": {"notify": ["test"]}}
        pipeline = Pipeline(reg, self.store(), cfg)
        service = ManagementService(pipeline.store, pipeline, config_path=str(Path(self.temp.name) / "config.yaml"))
        ctx = context()
        await pipeline.process_new(ctx)
        self.assertTrue(pipeline.store.get_task(ctx.task_id).ns("parse")["fallback"])
        await service.save_config({"defaults": {"parser": "regex", "approval_mode": "auto"}, "providers": {"notify": ["test"]}})
        await service.reparse(ctx.task_id)
        fresh = pipeline.store.get_task(ctx.task_id)
        await pipeline.process_received(fresh)
        self.assertEqual(fresh.state, TaskState.QUEUED)
        self.assertFalse(fresh.ns("parse").get("fallback"))
        while await pipeline.dispatch_deliveries():
            pass
        self.assertEqual(notify.events, ["identified"])

    async def test_explicit_json_mode_still_requires_strict_local_validation(self):
        ctx = context()
        ctx.source_rule = {"approval_mode": "auto", "llm_response_format": "json_object"}
        parser = LLMParser()
        parser._client = FakeAI({**movie(), "is_ad": "false"})
        ctx.parsed = await parser.parse(ctx)
        self.assertEqual(parser._client.calls[0]["response_format"], {"type": "json_object"})
        self.assertEqual(await DefaultDecision().decide(ctx), Decision.ASK)


class TestConfidence(PipelineCase):
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
        from reelay.providers.decision import DefaultDecision, SubscriptionDecision
        registry.add_provider("decision", DefaultDecision())
        registry.add_provider("decision", SubscriptionDecision())  # 默认决策
        pipeline = self.pipeline({"save_dirs": {"默认": "1"},
                                  "defaults": {"save_to": "默认", "approval_mode": "auto",
                                               "confidence_threshold": 0.8}}, registry)
        ctx = message()
        await pipeline.process_new(ctx)
        stored = self.store.get_task(ctx.task_id)
        self.assertEqual(stored.parsed.raw_fields["parser_confidence"], 1.0)
        self.assertEqual(stored.parsed.confidence, 0.7)
        self.assertEqual(stored.state, TaskState.PENDING_APPROVAL)


class TestParserCostControls(PipelineCase):
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


class TestManualDirectoryChoice(PipelineCase):
    CFG = {"save_dirs": {"剧集": "2", "测试": "3", "电影": "4"}, "defaults": {"save_to": "测试"}}

    def setUp(self):
        super().setUp()
        from reelay.core.service import ManagementService
        self.registry = discover()
        self.pl = self.pipeline(self.CFG, self.registry)
        self.service = ManagementService(self.store, self.pl, config_path=str(self.root / "config.yaml"))
        self.registry.get_provider("parser", "llm")._client = FakeAI(
            media(title="美人余", genre_ids=[18], origin_country=["CN"]))

    async def pending(self, msg_id="1"):
        await self.pl.process_new(message(msg_id=msg_id))
        task_id = self.store.list_by_state(TaskState.PENDING_APPROVAL, 10)[0]["task_id"]
        return task_id

    async def approve_and_route(self, task_id, alias=None):
        await self.service.approve(task_id, save_dir_alias=alias)
        ctx = self.store.get_task(task_id)
        self.assertTrue(await self.pl.run_hooks("before_save", ctx))
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
        self.assertIn("📂 存到：<code>剧集/国产剧集</code>", sent[0])

    async def test_targets_show_real_drive_paths_once_recorded(self):
        self.store.state_set("kernel", "save_dir_paths", {
            "测试": {"cid": "3", "path": "/测试"}, "剧集": {"cid": "2", "path": "/测试/剧集"},
            "电影": {"cid": "999", "path": "/旧位置/电影"}})  # CID 已改的记录不用
        task_id = await self.pending()
        body = self.service.inbox()
        self.assertEqual(body["save_dir_paths"], {"测试": "/测试", "剧集": "/测试/剧集"})
        self.assertEqual((body["items"][0]["auto_dir"], body["items"][0]["auto_path"]), ("剧集/国产剧集", "/测试/剧集/国产剧集"))
        self.assertEqual(self.service.get_task(task_id)["auto_path"], "/测试/剧集/国产剧集")
        self.assertEqual(self.store.get_task(task_id).meta["library"]["preview"], "/测试/剧集/国产剧集")
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
        self.assertEqual(ctx.meta["library"]["relative_category"], "国产剧集")

    async def test_manual_choice_wins_and_skips_category_subfolder(self):
        ctx = await self.approve_and_route(await self.pending(), "测试")
        self.assertEqual(ctx.save_dir_alias, "测试")
        self.assertNotIn("relative_category", ctx.meta["library"])
        self.assertTrue(any("手动指定目录「测试」" in line for line in ctx.trail))

    async def test_manual_choice_matching_category_root_keeps_subfolder(self):
        ctx = await self.approve_and_route(await self.pending(), "剧集")
        self.assertEqual((ctx.save_dir_alias, ctx.meta["library"]["relative_category"]), ("剧集", "国产剧集"))

    async def test_llm_calls_are_reported_to_connection_status(self):
        await self.pending()
        later = time.time() + 86400  # 只在真实调用时记录，长时间没有新消息也不算过期
        with secret_store.override(llm_api_key="k"), patch("time.time", return_value=later):
            self.assertEqual(self.service.connection_status()["llm"]["status"], "connected")
        self.registry.get_provider("parser", "llm")._client = FakeAI(ValueError("bad request"))
        await self.pl.process_new(message(text=f"另一部剧 第1集 {SHARE}", msg_id="2"))
        llm = self.service.connection_status()["llm"]
        self.assertEqual((llm["status"], llm["error"]), ("failed", "ValueError"))


class TestRefreshMetadataAfterTmdbConfigured(PipelineCase):
    HIT = {"id": 294446, "name": "美人余", "first_air_date": "2026-10-09", "origin_country": ["CN"],
           "genre_ids": [18], "poster_path": "/poster.jpg", "backdrop_path": "/backdrop.jpg", "_kind": "tv", "_score": 1.1,
           "original_name": "Mei Ren Yu", "overview": " 古装爱情剧。 ", "vote_average": 7.84, "vote_count": 3}

    def setUp(self):
        super().setUp()
        secrets = secret_store.override(tmdb_api_key="k")  # 密钥在用到时读取：整个用例期间都要有
        secrets.__enter__()
        self.addCleanup(secrets.__exit__, None, None, None)
        self.pl = self.pipeline({"save_dirs": {"剧集": "2", "默认": "1"}, "defaults": {"save_to": "默认"}}, discover())

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
        with patch.object(tmdb, "best_match", return_value=(self.HIT, [self.HIT])) as match, \
                patch.object(tmdb, "details", return_value={"languages": ["国语"], "cast": ["甲", "乙"], "rating": 7.9}):
            self.assertEqual(await self.pl.refresh_metadata(), 1)
            self.assertEqual(await self.pl.refresh_metadata(), 0)  # 已补全的任务不再重复查询
        self.assertEqual(match.call_count, 1)
        ctx = self.store.get_task(task_id)
        self.assertEqual((ctx.state, ctx.parsed.raw_fields["poster_path"], ctx.parsed.raw_fields["tmdbid"]),
                         (TaskState.PENDING_APPROVAL, "/poster.jpg", 294446))
        self.assertEqual((ctx.parsed.genre_ids, ctx.parsed.origin_country), ([18], ["CN"]))
        # 通知用的原名、简介、评分（评分人数少也显示）、语言与主演
        fields = ctx.parsed.raw_fields
        self.assertEqual((fields["original_title"], fields["overview"], fields["rating"], fields["languages"], fields["cast"]),
                         ("Mei Ren Yu", "古装爱情剧。", 7.9, ["国语"], ["甲", "乙"]))
        self.assertTrue(ctx.meta["confidence"]["checks"]["tmdb"])
        self.assertEqual(ctx.meta["library"]["preview"], "剧集/国产剧集")
        self.assertTrue(any("补跑元数据补全" in line for line in ctx.trail))
        self.assertEqual(self.updated_at(task_id), before)  # 审批超时不重新计时

    async def test_ignored_work_skips_later_messages_and_pending_copies(self):
        from reelay.core.service import ManagementService
        service = ManagementService(self.store, self.pl, config_path=str(self.root / "c.yaml"))
        await service.save_config(dict(self.pl.cfg))
        first, second = self.task("美人余", msg_id="1"), self.task("美人余", msg_id="2")
        with patch.object(tmdb, "best_match", return_value=(self.HIT, [self.HIT])):
            await self.pl.refresh_metadata()
            result = await service.ignore_work(first)
            work = {"id": result["work"]["id"], "title": "美人余", "tmdbid": 294446, "kind": "tv"}
            self.assertEqual(result["work"], work)
            self.assertEqual(sorted(result["rejected"]), sorted([first, second]))  # 待审批里同一部一并忽略
            self.assertEqual(service.list_ignored_works()["items"], [work])
            self.assertNotIn("ignored_works", service.get_config())  # 用户数据在数据库里，不改配置
            await self.pl.process_new(message(msg_id="3"))  # 之后的消息：识别后直接忽略
        later = next(c for row in self.store.list_tasks() if (c := self.store.get_task(row["task_id"])).raw.msg_id == "3")
        self.assertEqual(later.state, TaskState.SKIPPED)
        self.assertIn("你已设置不再接收《美人余》", "\n".join(later.trail))
        await service.unignore_work({"title": "美人余", "tmdbid": 294446, "kind": "tv"})
        self.assertEqual(service.list_ignored_works()["items"], [])
        with self.assertRaises(ValueError):  # 不再是配置项
            normalize_config({"save_dirs": {"默认": "1"}, "ignored_works": [{"title": "x"}]})

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
        self.assertEqual(kept.parsed.raw_fields.get("backdrop_path"), "/backdrop.jpg")
        untouched = self.store.get_task(renamed)
        self.assertEqual((untouched.parsed.title, untouched.parsed.raw_fields.get("poster_path")), ("美人余 竖屏版", None))
        self.assertEqual({task_id: self.updated_at(task_id) for task_id in (same, renamed)}, before)  # 入库统计不变

    async def test_corrected_title_is_matched_against_tmdb_again(self):
        task_id = self.task("美人余3")
        ctx = self.store.get_task(task_id)
        ctx.meta["tmdb"] = {"matched": False}  # 查询过但没有可信匹配
        ctx.parsed.raw_fields["tmdbid"] = 1
        self.store.save_task(ctx)
        from reelay.core.service import ManagementService
        service = ManagementService(self.store, self.pl, config_path=str(self.root / "c.yaml"))
        with patch.object(tmdb, "best_match", return_value=(self.HIT, [self.HIT])) as match:
            await service.correct_parsed(task_id, {"title": "美人余"})
        self.assertEqual(match.call_args.args[1], "美人余")
        ctx = self.store.get_task(task_id)
        self.assertEqual((ctx.parsed.raw_fields["tmdbid"], ctx.meta["confidence"]["caps"]), (294446, []))
        self.assertEqual(self.store.feedback_rows()[-1]["label"]["changes"]["title"], ["美人余3", "美人余"])
        with patch.object(tmdb, "best_match") as match:  # 只改集数：原来的匹配仍然有效
            await service.correct_parsed(task_id, {"episodes": [24]})
        match.assert_not_called()

    async def test_snapshot_refresh_gives_up_when_task_changed_meanwhile(self):
        task_id = self.task("美人余")
        ctx = self.store.get_task(task_id)
        self.assertFalse(self.store.refresh_snapshot(ctx, updated_at=self.updated_at(task_id) - 1))
        self.assertTrue(self.store.refresh_snapshot(ctx, updated_at=self.updated_at(task_id)))

    async def test_nothing_to_refresh_without_tmdb_key(self):
        self.task("美人余")
        pipeline = self.pipeline({"save_dirs": {"默认": "1"}, "defaults": {"save_to": "默认"}}, discover())
        with secret_store.override(), patch.object(tmdb, "best_match") as match:
            self.assertEqual(await pipeline.refresh_metadata(), 0)
        match.assert_not_called()



class TestConfigSnapshots(PipelineCase):
    def test_tasks_keep_the_config_they_were_received_with(self):
        registry = discover()
        pipeline = self.pipeline({"save_dirs": {"默认": "1"}, "defaults": {"save_to": "默认"}}, registry)
        old = pipeline.revision
        ctx = message()
        pipeline.stamp_config(ctx)
        self.store.save_task(ctx)
        changed = normalize_config({"save_dirs": {"默认": "1", "剧集": "2"}, "defaults": {"save_to": "剧集"}})
        pipeline.apply_config(changed)
        restored = self.store.get_task(ctx.task_id)
        self.assertEqual(restored.meta["runtime"], {"revision": old})  # 任务快照里只有版本号，不内嵌整份配置
        self.assertEqual(pipeline.task_config(restored)["defaults"]["save_to"], "默认")
        # 重启后（新的管线、同一个数据库）仍按版本号取回当时的配置
        restarted = Pipeline(registry, Store(self.root / "t.db"), changed)
        self.addCleanup(restarted.store.close)
        self.assertEqual(restarted.task_config(restored)["save_dirs"], {"默认": "1"})
        pipeline.apply_config(changed)  # 同一版本只存一份
        self.assertEqual(self.store._conn.execute("SELECT COUNT(*) FROM config_snapshots").fetchone()[0], 2)


if __name__ == "__main__":
    unittest.main()
