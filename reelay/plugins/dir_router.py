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
        ns = ctx.ns("dir_router")
        target = route(ctx.parsed, ctx.source_rule, current=ctx.save_dir_alias, pinned=ns.get("pinned"),
                       preferred=ns.get("preferred"))
        if target["alias"]:
            ctx.save_dir_alias = target["alias"]
        ns.pop("category_path", None)
        ns.pop("relative_category", None)
        if target["category_path"]:
            ns["category_path"] = target["category_path"]
            ns["relative_category"] = target["relative_category"]
        # 等待同一集的其他任务时每轮都会重新路由，结果不变就不再写轨迹
        if target["note"] and target["note"] != ns.get("note"):
            ctx.log(target["note"])
        ns["note"] = target["note"]
        return True

    def preview(self, ctx: PipelineContext) -> str:
        """待审批任务不改目录时将存入的位置，如「剧集/国产剧集」；含 AI 按偏好选出的目录。"""
        target = route(ctx.parsed, ctx.source_rule, preferred=ctx.ns("dir_router").get("preferred"))
        alias = target["alias"] or ctx.source_rule.get("save_to") or "默认"
        return f"{alias}/{target['relative_category']}" if target["relative_category"] else alias


def route(p, source_rule: dict, *, current: str | None = None, pinned: str | None = None,
          preferred: str | None = None) -> dict:
    """按当前作用域的配置算出目标目录，不修改任务。

    返回 alias（None 表示沿用任务/来源目录）、category_path / relative_category（二级分类）
    与写入轨迹的 note。指定的目录与分类结果不一致时直接存进该目录、不套用分类子目录：
    两者不一致通常说明分类本身就不对。人工指定（pinned）优先于 AI 按偏好选出的目录（preferred）。
    """
    chosen = pinned or preferred
    if chosen:
        auto = route(p, source_rule)
        if chosen == (auto["alias"] or source_rule.get("save_to") or "默认"):
            return auto
        who = "手动指定目录" if pinned else "AI 按偏好选择目录"
        return {"alias": chosen, "category_path": None, "relative_category": "",
                "note": f"{who}「{chosen}」，不套用分类子目录"}
    result = {"alias": None, "category_path": None, "relative_category": "", "note": None}
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
                return {**result, "alias": rule["dir"]}
        return {**result, "alias": default_alias or source_rule.get("save_to") or "默认"}
    strategy = strategy or DEFAULT_CATEGORY_STRATEGY
    selected = classify(strategy, p.media_type if p else "", p.genre_ids if p else [],
                        p.origin_country if p else [])
    if selected:
        root = selected.split("/", 1)[0]
        aliases = runtime.get("save_dirs", {}) or {}
        # 如果用户确实建立了一级目录映射，转存根目录跟随分类；否则保留来源指定目录，
        # Organizer 会在其下创建完整二级路径，避免无效默认配置阻断旧部署。
        if root in aliases:
            return {**result, "alias": root, "category_path": selected,
                    "relative_category": selected.split("/", 1)[1], "note": f"二级分类命中「{selected}」"}
        return {**result, "alias": current, "category_path": selected, "relative_category": selected,
                "note": f"二级分类命中「{selected}」"}

    # 旧 routing 只用于已有配置迁移期；新配置不再由 Web 页面编辑它。
    default_alias = None
    for rule in runtime.get("routing", []) or []:
        if "default" in rule:
            default_alias = rule["default"]
        elif rule.get("when") and rule.get("dir") and _match_condition(rule["when"], p):
            return {**result, "alias": rule["dir"], "note": f"兼容路由命中 [{rule['when']}] → 「{rule['dir']}」"}
    alias = current or default_alias or source_rule.get("save_to") or "默认"
    return {**result, "alias": alias, "note": f"无二级分类元数据，用根目录「{alias}」"}
