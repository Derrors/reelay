"""目录路由 DirRouter —— before_save Hook（方案 Stage 3）。

按 ``config.routing`` 规则，用 AI 解析出的 type / resolution / genre 等字段，
自上而下匹配，算出该资源该进哪个目录别名，写回 ctx.save_dir_alias。
命中 default 兜底。规则可配置、可扩展。

为安全**不使用 eval**：用受控的迷你条件求值器，只支持
``字段 == 值`` / ``字段 contains 值``，以及用 ``and`` 连接的多个条件。
"""

from __future__ import annotations

import logging

from ..core.contracts import PipelineContext
from ..core.registry import register
from ..core import runtime

log = logging.getLogger("reelay.plugin.dir_router")


def _field_value(parsed, field: str):
    field = field.strip()
    if not parsed:
        return None
    if field == "genre":
        # genre 可能在 raw_fields 里；容错取
        return parsed.raw_fields.get("genre") or parsed.raw_fields.get("genres") or ""
    return getattr(parsed, field, None)


def _match_condition(cond: str, parsed) -> bool:
    """求值单条 when 表达式，支持 and 连接。"""
    for clause in cond.split(" and "):
        clause = clause.strip()
        if not clause:
            continue
        if " contains " in clause:
            field, val = clause.split(" contains ", 1)
            actual = _field_value(parsed, field)
            val = val.strip().strip("'\"")
            if val not in (str(actual) if actual is not None else ""):
                return False
        elif "==" in clause:
            field, val = clause.split("==", 1)
            actual = _field_value(parsed, field)
            val = val.strip().strip("'\"")
            if str(actual) != val:
                return False
        else:
            # 不认识的子句 → 视为不匹配，避免误命中
            return False
    return True


@register(kind="hook", stage="before_save", order=10)
class DirRouter:
    stage = "before_save"
    order = 10

    async def run(self, ctx: PipelineContext) -> bool:
        # 已被上游（决策/规则）显式指定目录则尊重之
        if ctx.save_dir_alias:
            return True

        rules = runtime.get("routing", []) or []
        for rule in rules:
            if "default" in rule:
                default_alias = rule["default"]
                continue
            when = rule.get("when")
            target = rule.get("dir")
            if when and target and _match_condition(when, ctx.parsed):
                ctx.save_dir_alias = target
                ctx.log(f"DirRouter 命中规则 [{when}] → 目录「{target}」")
                return True

        # 未命中任何规则：用 default 或来源规则的 save_to
        fallback = locals().get("default_alias") or ctx.source_rule.get("save_to") or "默认"
        ctx.save_dir_alias = fallback
        ctx.log(f"DirRouter 未命中，用兜底目录「{fallback}」")
        return True
