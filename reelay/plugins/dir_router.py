"""目录路由 Hook：MoviePilot 风格二级分类策略。

保留 legacy routing 的只读兼容，配置新策略后优先用 dir_router.categories。
分类结果是相对路径，转存仍落到稳定的根目录别名，Organizer 再创建二级目录。
"""
from __future__ import annotations

import logging

from ..core import runtime
from ..core.category_strategy import DEFAULT_CATEGORY_STRATEGY, classify
from ..core.contracts import PipelineContext
from ..core.registry import register

log = logging.getLogger("reelay.plugin.dir_router")


def _field_value(parsed, field: str):
    if not parsed:
        return None
    field = field.strip()
    if field == "type":
        field = "media_type"
    if field == "genre":
        return parsed.genres or parsed.raw_fields.get("genre") or parsed.raw_fields.get("genres") or ""
    return getattr(parsed, field, None)


def _match_condition(cond: str, parsed) -> bool:
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
            if str(actual) != val.strip().strip("'\""):
                return False
        else:
            return False
    return True


@register(kind="hook", stage="before_save", order=10)
class DirRouter:
    name = "dir_router"
    ui_label = "二级分类路由"
    ui_description = "参照 MoviePilot 按媒体类型、类型ID与国家/地区匹配二级目录。"
    ui_section = "dir_router"
    ui_config_type = "category_strategy"
    stage = "before_save"
    order = 10

    async def run(self, ctx: PipelineContext) -> bool:
        p = ctx.parsed
        config = runtime.get("dir_router", {}) or {}
        legacy = runtime.get("routing", []) or []
        strategy = config.get("categories")
        # 旧任务/测试若只有 legacy routing，保持旧语义；规范化后的新配置一定有 categories。
        if not strategy and legacy:
            default_alias = None
            for rule in legacy:
                if "default" in rule:
                    default_alias = rule["default"]
                elif rule.get("when") and rule.get("dir") and _match_condition(rule["when"], p):
                    ctx.save_dir_alias = rule["dir"]
                    return True
            ctx.save_dir_alias = default_alias or ctx.source_rule.get("save_to") or "默认"
            return True
        strategy = strategy or DEFAULT_CATEGORY_STRATEGY
        selected = classify(strategy, p.media_type if p else "", p.genre_ids if p else [],
                            p.origin_country if p else [])
        if selected:
            ctx.ns("dir_router")["category_path"] = selected
            root = selected.split("/", 1)[0]
            aliases = runtime.get("save_dirs", {}) or {}
            # 如果用户确实建立了一级目录映射，转存根目录跟随分类；否则保留来源指定目录，
            # Organizer 会在其下创建完整二级路径，避免无效默认配置阻断旧部署。
            if root in aliases:
                ctx.save_dir_alias = root
                ctx.ns("dir_router")["relative_category"] = selected.split("/", 1)[1]
            else:
                ctx.ns("dir_router")["relative_category"] = selected
            ctx.log(f"二级分类命中「{selected}」")
            return True

        # 旧 routing 只用于已有配置迁移期；新配置不再由 Web 页面编辑它。
        default_alias = None
        for rule in runtime.get("routing", []) or []:
            if "default" in rule:
                default_alias = rule["default"]
            elif rule.get("when") and rule.get("dir") and _match_condition(rule["when"], p):
                ctx.save_dir_alias = rule["dir"]
                ctx.log(f"兼容路由命中 [{rule['when']}] → 「{rule['dir']}」")
                return True
        ctx.save_dir_alias = ctx.save_dir_alias or default_alias or ctx.source_rule.get("save_to") or "默认"
        ctx.log(f"无二级分类元数据，用根目录「{ctx.save_dir_alias}」")
        return True
