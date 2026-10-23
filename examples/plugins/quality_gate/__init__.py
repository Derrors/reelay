"""示例插件：低画质拦截（B 类 Hook，挂在 after_parse）。

正文命中 CAM/TS/枪版 等关键词时返回 False，任务按 skipped 结束，不进入审批与转存。
关键词可在 ``plugins.quality_gate.block``（逗号分隔）覆盖。放进 ``data/plugins/`` 即生效，
``plugins.quality_gate.enabled: false`` 可关闭。
"""
from __future__ import annotations

import re

from reelay.core import runtime
from reelay.core.contracts import PipelineContext
from reelay.core.registry import register

DEFAULT_BLOCK = "CAM,HDCAM,TS,HDTS,TC,枪版,抢先版"


@register(kind="hook", stage="after_parse", order=60)
class QualityGate:
    name = "quality_gate"
    ui_label = "低画质拦截"
    ui_description = "命中 CAM/TS/枪版等关键词的资源直接跳过。"
    # 路径相对 plugins.quality_gate
    ui_fields = [{"path": "block", "label": "拦截关键词（逗号分隔）", "type": "string", "default": DEFAULT_BLOCK}]

    async def run(self, ctx: PipelineContext) -> bool:
        words = [w.strip() for w in str(runtime.plugin_config(self.name).get("block") or DEFAULT_BLOCK).split(",") if w.strip()]
        text = ctx.raw.text or ""
        for word in words:
            # 英文关键词按单词边界匹配，避免 "TS" 误伤 "STS" 之类的片名
            if re.search(rf"(?<![A-Za-z]){re.escape(word)}(?![A-Za-z])", text, re.IGNORECASE):
                ctx.ns(self.name)["blocked"] = word
                ctx.log(f"低画质拦截：命中「{word}」")
                return False
        return True
