"""决策器 Provider —— 结合过滤规则与审批模式，判定 save / ask / skip。

判定顺序：
1. 硬过滤：非影视资源、广告、缺 115 链接、命中黑名单、缺 must_have → SKIP
2. 低置信度（可配阈值）→ ASK（交人工兜底），避免 AI 误判直接放行
3. 按来源的审批模式：manual → ASK；auto / default_pass → SAVE
"""

from __future__ import annotations

from ..core.contracts import ApprovalMode, Decision, PipelineContext
from ..core.registry import register


CONFIDENCE_ASK_THRESHOLD = 0.5   # 低于此值即便命中，也转人工


@register(kind="provider", ptype="decision")
class DefaultDecision:
    name = "default"

    async def decide(self, ctx: PipelineContext) -> Decision:
        p = ctx.parsed
        rule = ctx.source_rule or {}

        # 1) 硬过滤
        if not p or not p.is_media or p.is_ad:
            ctx.log("过滤：非影视 / 广告")
            return Decision.SKIP
        if not p.share_code:
            ctx.log("过滤：无 115 链接")
            return Decision.SKIP

        filters = rule.get("filters", {}) or {}
        text = ctx.raw.text or ""
        must = filters.get("must_have") or []
        if must and not any(m in text for m in [*must, *ctx.raw.urls]):
            ctx.log("过滤：未命中 must_have")
            return Decision.SKIP
        blocks = filters.get("block_keywords") or []
        if any(b in text for b in blocks):
            ctx.log("过滤：命中黑名单关键词")
            return Decision.SKIP

        # 2) 低置信度转人工
        if p.confidence < CONFIDENCE_ASK_THRESHOLD:
            ctx.log(f"置信度低({p.confidence:.2f})，转人工")
            return Decision.ASK

        # 3) 审批模式
        mode = ApprovalMode.parse(rule.get("approval_mode"))
        if mode == ApprovalMode.MANUAL:
            return Decision.ASK
        return Decision.SAVE   # auto / default_pass
