"""收件箱：版本择优与审批建议（事实与动作由规则决定，AI 只措辞），以及按来源的通过率与自动审批建议。"""
from __future__ import annotations

import re
import asyncio
import logging
from typing import Any


from .. import ai, inbox, messages, release, subscriptions, tmdb
from ..contracts import ParsedResource, PipelineContext, TaskState
from ..media import multi_season
from .base import ServiceBase

log = logging.getLogger("reelay.service")


class ReviewMixin(ServiceBase):
    """收件箱与审批洞察。"""

    def inbox(self, state: str = TaskState.PENDING_APPROVAL.value, limit: int = 200) -> dict[str, Any]:
        """待处理收件箱：一次返回卡片需要的全部字段，前端无需逐条请求详情。"""
        if state not in (TaskState.PENDING_APPROVAL.value, TaskState.NEED_MANUAL.value):
            raise ValueError("收件箱只支持 pending_approval 与 need_manual")
        items = []
        rows = [(row, ctx) for row in self.store.list_tasks(states=[TaskState(state)], limit=limit)
                if (ctx := self.store.get_task(row["task_id"]))]
        pending = state == TaskState.PENDING_APPROVAL.value
        context = self._advice_context([ctx for _, ctx in rows]) if pending else None
        for row, ctx in rows:
            p = ctx.parsed or ParsedResource()
            meta = ctx.meta
            lines = [re.sub(r"https?://\S+", "", line).strip() for line in (ctx.raw.text or "").splitlines()]
            # 消息没写集数时，用读取分享得到的季、集与大小展示（不改识别结果，转存仍以分享为准）
            share = meta.get("share") or {}
            from_share = not p.episodes and bool(share.get("episodes"))
            pairs = share.get("pairs") if from_share else [list(pair) for pair in ctx.episode_pairs()]
            items.append({
                **{key: row[key] for key in ("task_id", "state", "source", "created_at", "updated_at", "error")},
                "actions": self.task_actions(ctx),
                "title": p.title, "year": p.year, "media_type": p.media_type,
                "season": share.get("season") if from_share else p.season,
                "episodes": share["episodes"] if from_share else p.episodes,
                "episode_pairs": multi_season(pairs),
                "resolution": p.resolution, "size": messages.size_label(ctx), "reason": p.reason,
                "share": {"status": share.get("status"), "note": share.get("note"), "videos": share.get("videos"),
                          "from_share": from_share} if share else None,
                "confidence": p.confidence,
                "confidence_caps": meta.get("confidence", {}).get("caps", []),
                "fallback": bool(meta.get("parse", {}).get("fallback")),
                "tmdb_matched": meta.get("tmdb", {}).get("matched"),
                "poster_path": p.raw_fields.get("poster_path"),
                "save_dir_alias": ctx.save_dir_alias,
                **self._auto_target(ctx if state == TaskState.PENDING_APPROVAL.value else None),
                "preview": "\n".join(line for line in lines if line)[:400],
                "storage_phase": meta.get("storage", {}).get("phase"),
                "has_landed": bool(meta.get("storage", {}).get("landed")),
                "organize_error": meta.get("organize", {}).get("error"),
                "organize_conflict": meta.get("organize", {}).get("pending_conflict"),
                "transfer_partial": meta.get("storage", {}).get("partial"),
                **(self._advice_fields(ctx, context) if pending
                   else {"explanation": self.explanation(ctx.task_id, ctx.state.value)}),
            })
        return {"items": items, "save_dirs": list(self.pipeline.cfg["save_dirs"]),
                "save_dir_paths": self.pipeline.dir_paths()}

    # ------------------------------------------------------------------ #
    # 收件箱：版本择优与审批建议（事实与动作由规则决定，AI 只措辞）
    # ------------------------------------------------------------------ #
    def _advice_context(self, contexts: list[PipelineContext] | None = None) -> dict[str, Any]:
        if contexts is None:
            contexts = [ctx for row in self.store.list_by_state(TaskState.PENDING_APPROVAL, 1000)
                        if (ctx := self.store.get_task(row["task_id"]))]
        cfg = self.pipeline.cfg
        return {"stored": self.store.stored_series(), "subs": self.store.subscriptions(),
                "versions": inbox.pick_versions([c for c in contexts if c.parsed], cfg["release"]),
                "movies": {tmdb.normalize(title) for title in self.store.library_titles("movie")},
                "cache": self.store.state_items("inbox_advice"), "enabled": cfg["ai"]["advice"]}

    def _advice_facts(self, ctx: PipelineContext, context: dict) -> tuple[dict, str, bool]:
        sub = subscriptions.match(context["subs"], ctx.parsed, ctx.meta.get("tmdb", {}),
                                  seasons=subscriptions.seasons_of(ctx), auto_only=False)
        facts = inbox.facts(ctx, stored=context["stored"], versions=context["versions"], subscribed=bool(sub),
                            movie_titles=context["movies"])
        return facts, inbox.action(facts, ctx.source_rule.get("confidence_threshold", 0.5)), bool(sub)

    def _advice_fields(self, ctx: PipelineContext, context: dict) -> dict[str, Any]:
        p = ctx.parsed or ParsedResource()
        out: dict[str, Any] = {"release": p.release, "release_tags": release.describe(p.release),
                               "version": context["versions"].get(ctx.task_id), "subscribed": False, "advice": None}
        if not ctx.parsed:
            return out
        facts, act, out["subscribed"] = self._advice_facts(ctx, context)
        if context["enabled"]:
            cached = context["cache"].get(ctx.task_id) or {}
            fresh = cached.get("fingerprint") == inbox.fingerprint(facts, act)
            out["advice"] = {"action": act, "label": inbox.ACTIONS[act],
                             "text": cached["text"] if fresh else inbox.rule_text(facts, act),
                             "source": "ai" if fresh else "rule"}
        return out

    def advice_action(self, ctx: PipelineContext) -> str | None:
        """裁决时管理台给出的建议动作，记入样本以统计「按建议处理」的比例。"""
        if not ctx.parsed or not self.pipeline.cfg["ai"]["advice"]:
            return None
        return self._advice_facts(ctx, self._advice_context())[1]

    async def phrase_advice(self, task_ids: list[str]) -> dict[str, dict]:
        """为待审批任务生成 AI 措辞的建议理由并缓存（事实变化后重新生成）；AI 不可用时返回空。"""
        client, model = self.ai_client()
        if client is None or not self.pipeline.cfg["ai"]["advice"]:
            return {}
        context = self._advice_context()
        entries = []
        for task_id in list(dict.fromkeys(task_ids))[:30]:
            ctx = self.store.get_task(task_id)
            if not ctx or ctx.state != TaskState.PENDING_APPROVAL or not ctx.parsed:
                continue
            facts, act, _ = self._advice_facts(ctx, context)
            fp = inbox.fingerprint(facts, act)
            if (context["cache"].get(task_id) or {}).get("fingerprint") != fp:
                entries.append({"id": task_id, "facts": facts, "action": act, "fingerprint": fp})
        if not entries:
            return {}
        # 分批并发：一次写 30 条理由要输出约 2000 token，实测要 80 秒以上；某一批失败只影响这一批。
        semaphore = asyncio.Semaphore(3)

        async def batch(chunk: list[dict]) -> dict[str, str]:
            async with semaphore:
                try:
                    return await inbox.phrase(client, model, [{k: e[k] for k in ("id", "facts", "action")} for e in chunk])
                except ai.AIError:
                    return {}

        size = inbox.PHRASE_BATCH
        with self.ai_scope():
            results = await asyncio.gather(*(batch(entries[i:i + size]) for i in range(0, len(entries), size)))
        texts = {task_id: text for result in results for task_id, text in result.items()}
        if not texts:
            return {}
        pending = {row["task_id"] for row in self.store.list_by_state(TaskState.PENDING_APPROVAL, 5000)}
        for task_id in set(context["cache"]) - pending:
            self.store.state_delete("inbox_advice", task_id)  # 已处理任务的缓存顺手清掉
        out = {}
        for entry in entries:
            if entry["id"] in texts:
                self.store.state_set("inbox_advice", entry["id"], {"fingerprint": entry["fingerprint"],
                                                                   "text": texts[entry["id"]]})
                out[entry["id"]] = {"action": entry["action"], "label": inbox.ACTIONS[entry["action"]],
                                    "text": texts[entry["id"]], "source": "ai"}
        return out

    # ------------------------------------------------------------------ #
    # 审批数据洞察：按来源的通过率与切换自动审批的建议
    # ------------------------------------------------------------------ #
    def insights(self) -> dict[str, Any]:
        """最近若干次人工裁决里批准占比够高、修正够少的 manual 来源，建议改为 AI 处理。

        建议只是提示：切换后自动转存仍要通过过滤规则、置信度阈值、TMDB 匹配与分享清单校验。
        """
        cfg = self.get_config()
        rule = cfg["ai"]["suggest_auto"]
        window = max(30, rule["min_decisions"])
        stats = self.store.decision_stats(window)
        names = {item["bot"].lower(): item["bot"] for item in cfg["sources"]}
        names.update({key: names.get(key, key) for key in stats if key})
        sources = []
        for key, bot in sorted(names.items()):
            item = stats.get(key, {"decisions": 0, "approved": 0, "rejected": 0, "corrected": 0,
                                   "advised": 0, "followed": 0})
            mode = self.pipeline.rule_for(bot).get("approval_mode", "manual")
            decisions = item["decisions"]
            approve_rate = item["approved"] / decisions if decisions else None
            correct_rate = item["corrected"] / decisions if decisions else None
            suggest = (mode == "manual" and decisions >= rule["min_decisions"]
                       and approve_rate >= rule["approve_rate"] and correct_rate <= rule["max_correct_rate"])
            if suggest:
                reason = f"最近 {decisions} 次确认中批准 {item['approved']} 次（{approve_rate:.0%}），修正识别 {item['corrected']} 次"
            elif mode != "manual":
                reason = "已是自动转存"
            elif decisions < rule["min_decisions"]:
                reason = f"人工确认 {decisions} 次，满 {rule['min_decisions']} 次后评估"
            elif approve_rate < rule["approve_rate"]:
                reason = f"批准率 {approve_rate:.0%}，低于 {rule['approve_rate']:.0%}"
            else:
                reason = f"修正识别占 {correct_rate:.0%}，高于 {rule['max_correct_rate']:.0%}"
            sources.append({"source": bot, "approval_mode": mode, **item, "approve_rate": approve_rate,
                            "correct_rate": correct_rate, "suggest_auto": suggest, "reason": reason})
        return {"window": window, "rule": rule, "sources": sources}

    async def enable_auto_approval(self, source: str) -> dict[str, Any]:
        """采纳建议：把来源的确认方式改为 auto（没有来源规则时新增一条）。"""
        if not isinstance(source, str) or not source.strip():
            raise ValueError("缺少来源")

        def change(cfg: dict) -> None:
            for item in cfg["sources"]:
                if item["bot"].lower() == source.strip().lower():
                    item["approval_mode"] = "auto"
                    return
            cfg["sources"].append({"bot": source.strip(), "approval_mode": "auto"})

        return await self.update_config(change)
