"""决策 Provider 与审批模式。"""
from __future__ import annotations

import asyncio
import unittest

from reelay.core.config import normalize_config
from reelay.core.contracts import ApprovalMode, Decision, ParsedResource, PipelineContext, RawMessage
from reelay.core.registry import discover


class TestDecision(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reg = discover()
        cls.cfg = normalize_config({})  # 内置默认配置：不读本机的 data/config.yaml

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
        self.assertEqual(asyncio.run(d.decide(self._ctx(is_ad=True))), Decision.SKIP)

    def test_no_link_skip(self):
        d = self.reg.get_provider("decision", "default")
        self.assertEqual(asyncio.run(d.decide(self._ctx(share=None))), Decision.SKIP)

    def test_low_confidence_ask(self):
        d = self.reg.get_provider("decision", "default")
        self.assertEqual(asyncio.run(d.decide(self._ctx(conf=0.2, mode="auto"))), Decision.ASK)

    def test_manual_ask_auto_save(self):
        d = self.reg.get_provider("decision", "default")
        self.assertEqual(asyncio.run(d.decide(self._ctx(mode="manual"))), Decision.ASK)
        self.assertEqual(asyncio.run(d.decide(self._ctx(mode="auto"))), Decision.SAVE)

    def test_block_keyword_skip(self):
        d = self.reg.get_provider("decision", "default")
        c = self._ctx(text="115.com/s/x 招商", filters={"block_keywords": ["招商"]})
        self.assertEqual(asyncio.run(d.decide(c)), Decision.SKIP)

    def test_must_have_matches_hidden_url(self):
        c = self._ctx(text="点击获取影片", filters={"must_have": ["115.com", "115.cn"]})
        self.assertEqual(asyncio.run(self.reg.get_provider("decision", "default").decide(c)), Decision.SAVE)

    def test_unrelated_url_cannot_bypass_must_have(self):
        c = self._ctx(text="影片推荐", filters={"must_have": ["特定关键词"]})
        c.raw.urls = ["https://example.com/video"]
        self.assertEqual(asyncio.run(self.reg.get_provider("decision", "default").decide(c)), Decision.SKIP)


class TestApprovalMode(unittest.TestCase):
    def test_parse_fallback_manual(self):
        self.assertEqual(ApprovalMode.parse("auto"), ApprovalMode.AUTO)
        self.assertEqual(ApprovalMode.parse("garbage"), ApprovalMode.MANUAL)
        self.assertEqual(ApprovalMode.parse(None), ApprovalMode.MANUAL)


if __name__ == "__main__":
    unittest.main()
