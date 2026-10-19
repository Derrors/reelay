"""examples/plugins 中的示例插件必须能通过外部扩展目录加载并正常工作。"""
from __future__ import annotations

import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch

from reelay.core import runtime
from reelay.core.contracts import ParsedResource, PipelineContext, RawMessage, TaskState
from reelay.core.registry import discover

EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "plugins"


class TestExamplePlugins(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        with patch.dict(os.environ, {"REELAY_PLUGIN_DIRS": str(EXAMPLES)}, clear=True):
            self.registry = discover()

    def test_examples_load_without_errors(self):
        self.assertEqual(self.registry.load_errors, [])
        self.assertIn("webhook", self.registry.providers["notify"])
        self.assertIn("quality_gate", [h.name for h in self.registry.hooks_for("after_parse", {})])

    async def test_quality_gate_blocks_cam_releases_only(self):
        gate = next(h for h in self.registry.hooks["after_parse"] if h.name == "quality_gate")
        with runtime.scope({}):
            self.assertFalse(await gate.run(PipelineContext(RawMessage("@a", "1", "新片 HDCAM 中字"))))
            self.assertTrue(await gate.run(PipelineContext(RawMessage("@a", "2", "STS 4K WEB-DL"))))

    async def test_webhook_posts_task_event_and_notice(self):
        received = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                received.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                self.send_response(204)
                self.end_headers()

            def log_message(self, *args):
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        webhook = self.registry.get_provider("notify", "webhook")
        ctx = PipelineContext(RawMessage("@a", "1", "x"))
        ctx.parsed = ParsedResource(is_media=True, title="剧", episodes=[3], share_code="secret")
        ctx.state = TaskState.DONE
        with runtime.scope({"webhook": {"url": f"http://127.0.0.1:{server.server_port}/hook"}}):
            await webhook.notify(ctx, "saved")
            await webhook.notice("日报")
        self.assertEqual([r["event"] for r in received], ["saved", "notice"])
        self.assertEqual(received[0]["title"], "剧")
        self.assertNotIn("secret", json.dumps(received))


if __name__ == "__main__":
    unittest.main()
