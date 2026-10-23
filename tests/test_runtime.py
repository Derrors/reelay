"""配置、AI 契约和持久化旁路的回归验证；全部使用本地模拟客户端。"""
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

from reelay.core import runtime, secret_store
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


def context(msg_id="1"):
    return PipelineContext(RawMessage("@test", msg_id, "电影 (2026) 4K https://115.com/s/actual?password=abcd",
                                      ["https://115.com/s/actual?password=abcd"]))


def media():
    return {"is_media": True, "is_ad": False, "confidence": 0.9, "media_type": "movie",
            "title": "电影", "year": 2026, "season": None, "episodes": [], "genres": ["动画"],
            "resolution": "4K", "size": "2 GB", "reason": "影视分享"}


class FakeAI:
    def __init__(self, data=None, error=None, refusal=None):
        self.data, self.error, self.refusal = data, error, refusal
        self.calls = []
        self.chat = types.SimpleNamespace(completions=self)

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        message = types.SimpleNamespace(content=json.dumps(self.data), refusal=self.refusal)
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=message)])


class RuntimeTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.env = patch.dict("os.environ", {"REELAY_DRY_RUN": "1"})
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
        parser._client = FakeAI(media())
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
                parser._client = FakeAI({**media(), **change})
                ctx.parsed = await parser.parse(ctx)
                self.assertTrue(ctx.meta["parse"]["fallback"])
                self.assertEqual(await DefaultDecision().decide(ctx), Decision.ASK)

    async def test_timeout_refusal_and_missing_credentials_require_approval(self):
        for client in (None, FakeAI(error=TimeoutError("secret must not be persisted")), FakeAI(media(), refusal="no")):
            ctx = context()
            ctx.source_rule = {"approval_mode": "default_pass"}
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

    async def test_natural_preferences_choose_only_allowed_aliases_and_respect_manual(self):
        provider = LLMDecision()
        cfg = normalize_config({})
        for mode, expected in (("auto", Decision.SAVE), ("manual", Decision.ASK)):
            ctx = context()
            ctx.parsed = ParsedResource(**media(), share_code="actual")
            ctx.source_rule = {"preferences": "只收 4K，存入动漫", "approval_mode": mode}
            provider._client = FakeAI({"decision": "save", "save_to": "动漫", "reason": "匹配偏好"})
            with runtime.scope(cfg):
                self.assertEqual(await provider.decide(ctx), expected)
            self.assertEqual(ctx.save_dir_alias, "动漫")
        provider._client = FakeAI({"decision": "save", "save_to": "未授权目录", "reason": ""})
        with runtime.scope(cfg):
            self.assertEqual(await provider.decide(ctx), Decision.ASK)
        self.assertEqual(ctx.save_dir_alias, "动漫")
        ctx.parsed.is_ad = True
        provider._client.calls.clear()
        with runtime.scope(cfg):
            self.assertEqual(await provider.decide(ctx), Decision.SKIP)
        self.assertEqual(provider._client.calls, [])

    async def test_natural_preferences_unavailable_cannot_auto_save(self):
        provider = LLMDecision()
        provider._client = None
        ctx = context()
        ctx.parsed = ParsedResource(**media(), share_code="actual")
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
        ctx.parsed = ParsedResource(**media())
        ctx.source_rule = pipeline._rule_for("@TEST")
        await provider.notify(ctx, "identified")
        self.assertEqual(sent[-1]["chat"], "local")
        ctx.source_rule = pipeline._rule_for("@off")
        await provider.notify(ctx, "identified")
        self.assertEqual(len(sent), 1)
        ctx.source_rule["approval_mode"] = "default_pass"
        await provider.notify(ctx, "saved")
        self.assertEqual(len(sent), 1)
        await provider.notify(ctx, "organize_failed")
        self.assertEqual(len(sent), 2)

    async def test_silent_pipeline_does_not_enqueue_saved_notification(self):
        pipeline = Pipeline(self.registry(), self.store(), {})
        ctx = context()
        ctx.source_rule = {"approval_mode": "default_pass"}
        pipeline.store.save_task(ctx)
        await pipeline._notify_all(ctx, "saved")
        self.assertEqual(pipeline.store.delivery_counts(), {})
        await pipeline._notify_all(ctx, "transfer_uncertain")
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
        await asyncio.gather(a._run_hooks("after_parse", ca), b._run_hooks("after_parse", cb))
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
        ctx.parsed = ParsedResource(**media())
        pipeline.store.save_task(ctx)
        for patch_value in ({"episodes": [True]}, {"is_ad": "false"}, {"year": "2026"}):
            with self.assertRaises(ValueError):
                service.patch_parsed(ctx.task_id, patch_value)
        self.assertEqual(pipeline.store.get_task(ctx.task_id).parsed.year, 2026)
        preview = pipeline.reg.action("organizer", "preview_plan")
        for payload in ({"parsed": {"season": "one"}, "landed": []}, {"parsed": media(), "landed": "a.mkv"}):
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
        await pipeline._run_hooks("after_parse", ctx)
        self.assertNotIn("test", ctx.meta)
        pipeline.cfg["plugins"]["test"] = {"enabled": True}
        await pipeline._run_hooks("after_parse", ctx)
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
        parser._client = FakeAI({**media(), "is_ad": "false"})
        ctx.parsed = await parser.parse(ctx)
        self.assertEqual(parser._client.calls[0]["response_format"], {"type": "json_object"})
        self.assertEqual(await DefaultDecision().decide(ctx), Decision.ASK)

    async def test_personal_source_rejects_bot_and_disconnects_failed_start(self):
        module = types.ModuleType("telethon")
        class Client:
            def __init__(self, *args, **kwargs):
                self.disconnected, self.forever = False, False
            def on(self, event):
                return lambda fn: fn
            async def start(self):
                return self
            async def get_me(self):
                return types.SimpleNamespace(bot=True)
            async def run_until_disconnected(self):
                self.forever = True
            async def disconnect(self):
                self.disconnected = True
        client = Client()
        module.TelegramClient = lambda *args, **kwargs: client
        module.events = types.SimpleNamespace(NewMessage=lambda **kwargs: object())
        with patch.dict("sys.modules", {"telethon": module}), secret_store.override(telegram_api_id="1", telegram_api_hash="dummy"):
            source = TelegramUserbotSource()
            with self.assertRaisesRegex(RuntimeError, "个人"):
                await source.listen(lambda raw: None)
        self.assertTrue(client.disconnected)
        self.assertFalse(client.forever)

    async def test_personal_source_waits_for_identity_and_resolves_missing_peer(self):
        module = types.ModuleType("telethon")
        emitted = []
        class Client:
            def __init__(self):
                self.handler, self.callbacks = None, []
                self.disconnected = False
            def on(self, event):
                def register(fn):
                    self.handler = fn
                    return fn
                return register
            async def start(self):
                event = types.SimpleNamespace(chat=None, sender_id=123, chat_id=123,
                                              message=types.SimpleNamespace(id=1, raw_text='https://115.com/s/actual', entities=[]))
                async def get_chat():
                    event.chat = types.SimpleNamespace(username='Test', id=123)
                    return event.chat
                event.get_chat = get_chat
                self.callbacks.append(asyncio.create_task(self.handler(event)))
                await asyncio.sleep(0)
                self_before_identity = len(emitted)
                assert self_before_identity == 0
                return self
            async def get_me(self):
                return types.SimpleNamespace(bot=False)
            async def run_until_disconnected(self):
                await asyncio.gather(*self.callbacks)
            async def disconnect(self):
                self.disconnected = True
        client = Client()
        module.TelegramClient = lambda *args, **kwargs: client
        module.events = types.SimpleNamespace(NewMessage=lambda **kwargs: object())
        async def emit(raw):
            emitted.append(raw)
        with patch.dict('sys.modules', {'telethon': module}), secret_store.override(telegram_api_id='1', telegram_api_hash='dummy'):
            source = TelegramUserbotSource()
            source.configure(['@test'])
            await source.listen(emit)
        self.assertEqual(len(emitted), 1)
        self.assertEqual(emitted[0].source, '@Test')
        self.assertEqual(emitted[0].urls, ['https://115.com/s/actual'])
        self.assertTrue(client.disconnected)
