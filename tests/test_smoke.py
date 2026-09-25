"""内核关键逻辑单元测试（标准库 unittest，无三方依赖）。

覆盖评审重点：三层去重、追更增量、决策过滤、状态机落库与重启恢复。
运行：python3 -m unittest -v
"""

import asyncio
import unittest

from reelay.core.config import load_config
from reelay.core.contracts import (
    ApprovalMode, Decision, ParsedResource, PipelineContext, RawMessage, TaskState,
)
from reelay.core.pipeline import Pipeline
from reelay.core.registry import discover
from reelay.core.store import Store, DEDUP_MESSAGE, DEDUP_SHARE, DEDUP_EPISODE


def run(coro):
    return asyncio.run(coro)


class TestStore(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")

    def tearDown(self):
        self.store.close()

    def test_three_layer_dedup(self):
        s = self.store
        # 消息级
        self.assertFalse(s.seen(DEDUP_MESSAGE, "m1"))
        s.mark(DEDUP_MESSAGE, "m1")
        self.assertTrue(s.seen(DEDUP_MESSAGE, "m1"))
        # 资源级
        s.mark(DEDUP_SHARE, "shareX")
        self.assertTrue(s.seen(DEDUP_SHARE, "shareX"))
        # 空 key 不记
        s.mark(DEDUP_MESSAGE, "")
        self.assertFalse(s.seen(DEDUP_MESSAGE, ""))

    def test_missing_episodes_incremental(self):
        """追更：已入库 1-8 集，来了 1-10 集，应只返回 9、10。"""
        s = self.store
        p_old = ParsedResource(title="剧A", season=1, episodes=list(range(1, 9)))
        for ep in p_old.episodes:
            s.mark(DEDUP_EPISODE, Store.episode_key("剧A", 1, ep))
        p_new = ParsedResource(title="剧A", season=1, episodes=list(range(1, 11)))
        self.assertEqual(s.missing_episodes(p_new), [9, 10])

    def test_task_snapshot_roundtrip_and_recover(self):
        s = self.store
        ctx = PipelineContext(raw=RawMessage(source="@b", msg_id="1", text="hi"))
        ctx.parsed = ParsedResource(is_media=True, title="片", share_code="sc")
        ctx.state = TaskState.QUEUED
        s.save_task(ctx)
        # 还原
        got = s.get_task(ctx.task_id)
        self.assertEqual(got.parsed.title, "片")
        self.assertEqual(got.state, TaskState.QUEUED)
        # 重启恢复：QUEUED 应被捡回
        recovered = s.recover_interrupted()
        self.assertEqual(len(recovered), 1)


class TestDecision(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reg = discover()
        cls.cfg = load_config()

    def _ctx(self, *, is_media=True, is_ad=False, conf=0.9, share="sc",
             text="115.com/s/x", mode="auto", filters=None):
        ctx = PipelineContext(raw=RawMessage(source="@b", msg_id="1", text=text,
                                             urls=["https://115.com/s/x"]))
        ctx.parsed = ParsedResource(is_media=is_media, is_ad=is_ad, confidence=conf,
                                    share_code=share, title="T")
        ctx.source_rule = {"approval_mode": mode, "filters": filters or {}}
        return ctx

    def test_ad_skip(self):
        d = self.reg.get_provider("decision", "default")
        self.assertEqual(run(d.decide(self._ctx(is_ad=True))), Decision.SKIP)

    def test_no_link_skip(self):
        d = self.reg.get_provider("decision", "default")
        self.assertEqual(run(d.decide(self._ctx(share=None))), Decision.SKIP)

    def test_low_confidence_ask(self):
        d = self.reg.get_provider("decision", "default")
        self.assertEqual(run(d.decide(self._ctx(conf=0.2, mode="auto"))), Decision.ASK)

    def test_manual_ask_auto_save(self):
        d = self.reg.get_provider("decision", "default")
        self.assertEqual(run(d.decide(self._ctx(mode="manual"))), Decision.ASK)
        self.assertEqual(run(d.decide(self._ctx(mode="auto"))), Decision.SAVE)

    def test_block_keyword_skip(self):
        d = self.reg.get_provider("decision", "default")
        c = self._ctx(text="115.com/s/x 招商", filters={"block_keywords": ["招商"]})
        self.assertEqual(run(d.decide(c)), Decision.SKIP)


class TestApprovalMode(unittest.TestCase):
    def test_parse_fallback_manual(self):
        self.assertEqual(ApprovalMode.parse("auto"), ApprovalMode.AUTO)
        self.assertEqual(ApprovalMode.parse("garbage"), ApprovalMode.MANUAL)
        self.assertEqual(ApprovalMode.parse(None), ApprovalMode.MANUAL)


if __name__ == "__main__":
    unittest.main()
