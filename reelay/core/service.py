"""统一应用服务层：Web API 与 Telegram 控制 Bot 共用这里的规则。

服务只改变任务/配置的权威状态，不直接绕过 Pipeline 操作 115。这样两个入口并发操作时，
状态检查与错误语义一致。

人工裁决（批准、忽略、修正识别结果）在这里统一记成带标签的样本（``feedback`` 表），
供解析示例、偏好决策、通过率统计与本地评测使用：AI 提议，事实源校验，人工裁决，裁决结果反馈给 AI。
"""

from __future__ import annotations

import asyncio
import copy
from dataclasses import asdict
from pathlib import Path
from typing import Any

from . import ai
from .config import normalize_config
from .validation import MEDIA_SCHEMA, validate_resource
from .contracts import ParsedResource, PipelineContext, TaskState
from .media import multi_season
from .pipeline import Pipeline
from .store import Store
from ..plugins.organizer import Organizer


class ConflictError(RuntimeError):
    pass


class NotFoundError(RuntimeError):
    pass


def parsed_sample(parsed: ParsedResource | None) -> dict | None:
    """写入裁决样本的识别结果：去掉分享标识，原始字段只留 tmdbid。"""
    if parsed is None:
        return None
    values = asdict(parsed)
    values.pop("share_code", None)
    values.pop("receive_code", None)
    raw = values.pop("raw_fields", None) or {}
    if raw.get("tmdbid"):
        values["tmdbid"] = raw["tmdbid"]
    return values


class ManagementService:
    def __init__(
        self,
        store: Store,
        pipeline: Pipeline,
        *,
        config_path: str = "config.yaml",
    ) -> None:
        self.store = store
        self.pipeline = pipeline
        self.config_path = Path(config_path)
        self.pipeline.config_path = self.config_path
        self._config_lock = asyncio.Lock()

    def dashboard(self) -> dict[str, Any]:
        counts = self.store.counts_by_state()
        pending = counts.get(TaskState.PENDING_APPROVAL.value, 0) + counts.get(
            TaskState.NEED_MANUAL.value, 0
        )
        active = sum(counts.get(state.value, 0) for state in (
            TaskState.RECEIVED, TaskState.PARSED, TaskState.QUEUED,
            TaskState.SAVING, TaskState.STORED, TaskState.ORGANIZING,
        ))
        connections = self.connection_status()
        dry_run = __import__("os").getenv("REELAY_DRY_RUN", "").lower() in {
            "1", "true", "yes"
        }
        live_ready = connections["telegram"]["configured"] and connections["p115"]["configured"]
        health = self.store.health_status()
        worker = health.get("worker", {})
        source = health.get("source", {})
        live_running = (worker.get("status") == "running" and not worker.get("stale", True)
                        and source.get("status") == "connected" and not source.get("stale", True))
        self.pipeline.reload_config()
        return {
            "counts": counts,
            "pending": pending,
            "active": active,
            "failed": counts.get(TaskState.FAILED.value, 0),
            "done": counts.get(TaskState.DONE.value, 0),
            "connections": connections,
            "runtime": health,
            "alerts": self.store.active_alerts(),
            "recent": self._recent(),
            "deliveries": self.store.delivery_counts(),
            "config_revision": self.pipeline.revision,
            "simulation": dry_run,
            "mode": "simulation" if dry_run else ("live" if live_running else ("not_running" if live_ready else "not_configured")),
        }

    def _recent(self) -> dict[str, int]:
        import time
        summary = self.store.summary_since(time.time() - 86400)
        return {"received_24h": summary["received"], "done_24h": summary["done_count"]}

    def connection_status(self) -> dict[str, dict[str, Any]]:
        import os
        import time

        connections = {
            "telegram": {
                "configured": bool(os.getenv("REELAY_TG_API_ID") and os.getenv("REELAY_TG_API_HASH")),
                "label": "Telegram 监听",
            },
            "control_bot": {
                "configured": bool(os.getenv("REELAY_CONTROL_BOT_TOKEN") and os.getenv("REELAY_OWNER_ID")),
                "label": "控制 Bot",
            },
            "p115": {
                "configured": self._p115_configured(),
                "label": "115 网盘",
            },
            "llm": {
                "configured": bool(os.getenv("REELAY_LLM_API_KEY")),
                "label": "AI 解析",
            },
            "tmdb": {
                "configured": bool(os.getenv("REELAY_TMDB_API_KEY")),
                "label": "TMDB 元数据",
            },
        }
        health = self.store.health_status()
        observed_as = {"telegram": "source", "p115": "p115_cookie"}
        for key, conn in connections.items():
            conn["status"] = "unverified" if conn["configured"] else "not_configured"
            observation = health.get(observed_as.get(key, key))
            if observation and observation.get("status") != "unknown":
                stale = observation["stale"]
                if observation.get("check_interval"):  # 定时探活：超过两个探活周期没有新结果才算过期
                    stale = time.time() - observation["updated_at"] > 2 * observation["check_interval"]
                elif observation.get("event_driven"):  # 只在真实调用时记录，显示最近一次结果
                    stale = False
                conn["status"] = "stale" if stale else observation.get("status", "unverified")
                conn["observed_at"] = observation["updated_at"]
                if observation.get("error"):
                    conn["error"] = observation["error"]
        return connections

    @staticmethod
    def _p115_configured() -> bool:
        """转存主链路依赖cookie；只有OAuth不能当成主流程就绪。"""
        from .auth115 import get_store
        return get_store().has_cookie

    def list_tasks(self, **filters: Any) -> list[dict[str, Any]]:
        """state 可以是逗号分隔的多个状态，如 "received,queued,saving"。"""
        state_value = filters.pop("state", None)
        states = [TaskState(item.strip()) for item in str(state_value).split(",") if item.strip()] \
            if state_value else None
        return self.store.list_tasks(states=states, **filters)

    def inbox(self, state: str = TaskState.PENDING_APPROVAL.value, limit: int = 200) -> dict[str, Any]:
        """待处理收件箱：一次返回卡片需要的全部字段，前端无需逐条请求详情。"""
        import re
        if state not in (TaskState.PENDING_APPROVAL.value, TaskState.NEED_MANUAL.value):
            raise ValueError("收件箱只支持 pending_approval 与 need_manual")
        items = []
        for row in self.store.list_tasks(states=[TaskState(state)], limit=limit):
            ctx = self.store.get_task(row["task_id"])
            if not ctx:
                continue
            p = ctx.parsed or ParsedResource()
            meta = ctx.meta
            lines = [re.sub(r"https?://\S+", "", line).strip() for line in (ctx.raw.text or "").splitlines()]
            # 消息没写集数时，用读取分享得到的季、集与大小展示（不改识别结果，转存仍以分享为准）
            share = meta.get("share") or {}
            from_share = not p.episodes and bool(share.get("episodes"))
            pairs = share.get("pairs") if from_share else [list(pair) for pair in ctx.episode_pairs()]
            items.append({
                **{key: row[key] for key in ("task_id", "state", "source", "created_at", "updated_at", "error")},
                "title": p.title, "year": p.year, "media_type": p.media_type,
                "season": share.get("season") if from_share else p.season,
                "episodes": share["episodes"] if from_share else p.episodes,
                "episode_pairs": multi_season(pairs),
                "resolution": p.resolution, "size": p.size or share.get("size_text"), "reason": p.reason,
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
            })
        return {"items": items, "save_dirs": list(self.pipeline.cfg["save_dirs"]),
                "save_dir_paths": self.pipeline.dir_paths()}

    def _auto_target(self, ctx: PipelineContext | None) -> dict[str, str | None]:
        """待审批任务不改目录时的去向：auto_dir 是「别名/分类」，auto_path 是网盘真实路径（未知时为 None）。"""
        if not ctx:
            return {"auto_dir": None, "auto_path": None}
        label = self.pipeline.target_preview(ctx)
        path = self.pipeline.display_target(label)
        return {"auto_dir": label, "auto_path": path if path != label else None}

    def get_task(self, task_id: str) -> dict[str, Any]:
        record = self.store.get_task_record(task_id)
        if not record:
            raise NotFoundError("任务不存在")
        ctx = self.store.get_task(task_id) if record["state"] == TaskState.PENDING_APPROVAL.value else None
        record.update(self._auto_target(ctx))
        return record

    async def approve(self, task_id: str, *, save_dir_alias: str | None = None) -> None:
        """save_dir_alias 是手动指定的目录：与自动分类不一致时直接存入，不套用分类子目录。"""
        ctx = self._require_context(task_id)
        if ctx.state != TaskState.PENDING_APPROVAL:
            raise ConflictError("任务不是待审批状态")
        advice = self.advice_action(ctx)
        target = self.pipeline.target_preview(ctx)
        if save_dir_alias:
            self._validate_alias(save_dir_alias)
            ctx.save_dir_alias = save_dir_alias
            ctx.ns("dir_router")["pinned"] = save_dir_alias
        elif ctx.ns("dir_router").pop("pinned", None):
            ctx.save_dir_alias = None  # 撤销此前手动指定的目录，重新按分类
        ctx.state = TaskState.QUEUED
        ctx.log("管理端批准转存")
        self._update(ctx, expected=TaskState.PENDING_APPROVAL)
        self._record(ctx, "approve", {"save_dir_alias": save_dir_alias or None, "target": target}, advice)

    async def reject(self, task_id: str) -> None:
        ctx = self.store.get_task(task_id)
        advice = self.advice_action(ctx) if ctx and ctx.state == TaskState.PENDING_APPROVAL else None
        if not await self.pipeline.reject(task_id):
            raise ConflictError("任务不是待审批状态")
        self._record(ctx, "reject", {}, advice)

    def advice_action(self, ctx: PipelineContext) -> str | None:
        """裁决时管理台给出的建议动作，记入样本以统计「按建议处理」的比例。"""
        return None

    def _record(self, ctx: PipelineContext, kind: str, label: dict, advice: str | None = None) -> None:
        self.store.add_feedback(task_id=ctx.task_id, source=ctx.raw.source, kind=kind, text=ai.scrub(ctx.raw.text),
                                parsed=parsed_sample(ctx.parsed), label=label, advice=advice)

    async def retry(self, task_id: str) -> None:
        ctx = self._require_context(task_id)
        if ctx.state != TaskState.FAILED:
            raise ConflictError("只有失败任务可以重试")
        retry_from = ctx.ns("task").get("retry_from", TaskState.QUEUED.value)
        try:
            next_state = TaskState(retry_from)
        except ValueError:
            next_state = TaskState.QUEUED
        if next_state not in (TaskState.RECEIVED, TaskState.QUEUED, TaskState.STORED):
            raise ConflictError("任务重试阶段无效，请核验检查点")
        ctx.state = next_state
        ctx.ns("task")["generation"] = ctx.ns("task").get("generation", 0) + 1
        ctx.ns("task").pop("no_auto_retry", None)  # 人工已介入，之后的失败可再自动重试
        ctx.log("管理端请求重试")
        self._update(ctx, expected=TaskState.FAILED, reset_retries=True)

    async def reparse(self, task_id: str) -> None:
        ctx = self._require_context(task_id)
        self._require_editable(ctx)
        if ctx.ns("storage").get("manifest") or ctx.ns("storage").get("landed") \
                or ctx.ns("storage").get("phase") == "legacy_uncertain":
            raise ConflictError("已有转存检查点，不能重新解析；请核验转存或重新整理")
        previous = ctx.state
        ctx.ns("task")["generation"] = ctx.ns("task").get("generation", 0) + 1
        ctx.parsed = None
        ctx.decision = None
        ctx.result = None
        ctx.source_rule = self.pipeline._rule_for(ctx.raw.source)
        ctx.ns("runtime").update(config=copy.deepcopy(self.pipeline.cfg), revision=self.pipeline.revision)
        ctx.meta.pop("parse", None)
        ctx.meta.pop("preference", None)
        ctx.meta.get("dir_router", {}).pop("preferred", None)
        ctx.meta.pop("storage", None)  # 无远端写检查点，显式重新解析可重新选择 Provider。
        ctx.meta["parse_request"] = {"bypass_cache": True}  # 人工要求重新解析，不复用缓存结果
        ctx.state = TaskState.RECEIVED
        ctx.ns("task")["retry_from"] = TaskState.RECEIVED.value
        ctx.log("由管理端请求重新解析")
        self._update(ctx, expected=previous, reset_retries=True)

    async def requeue_organize(self, task_id: str, *, save_dir_alias: str | None = None) -> None:
        ctx = self._require_context(task_id)
        if ctx.state != TaskState.NEED_MANUAL:
            raise ConflictError("只有待人工整理任务可以重新整理")
        if save_dir_alias and save_dir_alias != ctx.save_dir_alias:
            self._validate_alias(save_dir_alias)
            ctx.save_dir_alias = save_dir_alias
            ctx.save_dir_cid = self.pipeline.cfg["save_dirs"][save_dir_alias]
            ctx.meta.pop("dir_router", None)  # 改到其他目录后不再套用分类子目录
            ctx.ns("organize").pop("plan", None)
            ctx.ns("organize").pop("base_cid", None)
        if not ctx.ns("storage").get("landed"):
            raise ConflictError("任务没有已落地文件记录，不能只重新整理")
        ctx.state = TaskState.QUEUED
        ctx.ns("task")["action"] = "organize_only"
        ctx.ns("task")["generation"] = ctx.ns("task").get("generation", 0) + 1
        ctx.ns("runtime").update(config=copy.deepcopy(self.pipeline.cfg), revision=self.pipeline.revision)
        ctx.ns("task")["retry_from"] = TaskState.QUEUED.value
        ctx.log("由管理端请求仅重新整理，不重复转存")
        self._update(ctx, expected=TaskState.NEED_MANUAL, reset_retries=True)

    async def reconcile_transfer(self, task_id: str) -> None:
        ctx = self._require_context(task_id)
        storage = ctx.ns("storage")
        if ctx.state != TaskState.NEED_MANUAL or storage.get("phase") != "receiving" \
                or not storage.get("manifest"):
            raise ConflictError("只有转存结果未知且有清单的任务可以核验")
        ctx.state = TaskState.QUEUED
        ctx.log("管理端请求只读核验转存，不重发转存请求")
        self._update(ctx, expected=TaskState.NEED_MANUAL)

    def patch_parsed(self, task_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        ctx = self._require_context(task_id)
        self._require_editable(ctx)
        storage = ctx.ns("storage")
        if storage.get("phase") == "legacy_uncertain" or (
            storage.get("manifest") and storage.get("phase") != "stored"
        ):
            raise ConflictError("转存结果未核验，不能修改解析结果")
        validate_resource(patch)
        if storage.get("landed") and set(patch) & {"share_code", "receive_code"}:
            raise ConflictError("已转存任务不能修改分享标识")
        before = asdict(ctx.parsed or ParsedResource())
        values = dict(before)
        values.update(patch)
        ctx.parsed = ParsedResource(**validate_resource(values))
        ctx.ns("organize").pop("plan", None)
        ctx.log("管理端修正了解析结果")
        self._update(ctx, expected=ctx.state)
        after = asdict(ctx.parsed)
        changes = {key: [before[key], after[key]] for key in MEDIA_SCHEMA["properties"]
                   if key in after and before.get(key) != after[key]}
        if changes:
            # result 是修正后的完整识别结果，作为同来源后续解析的示例与本地评测的标准答案。
            self._record(ctx, "correct", {"changes": changes, "result": {
                key: after[key] for key in MEDIA_SCHEMA["properties"] if key in after}})
        return self.get_task(task_id)

    # ------------------------------------------------------------------ #
    # 审批数据洞察：按来源的通过率与切换自动审批的建议
    # ------------------------------------------------------------------ #
    def insights(self) -> dict[str, Any]:
        """最近若干次人工裁决里批准占比够高、修正够少的 manual 来源，建议改为自动转存。

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
            mode = self.pipeline._rule_for(bot).get("approval_mode", "manual")
            decisions = item["decisions"]
            approve_rate = item["approved"] / decisions if decisions else None
            correct_rate = item["corrected"] / decisions if decisions else None
            suggest = (mode == "manual" and decisions >= rule["min_decisions"]
                       and approve_rate >= rule["approve_rate"] and correct_rate <= rule["max_correct_rate"])
            if suggest:
                reason = (f"最近 {decisions} 次确认中批准 {item['approved']} 次（{approve_rate:.0%}），"
                          f"修正识别 {item['corrected']} 次，可以考虑改为自动转存")
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
        cfg = self.get_config()
        if self.pipeline.config_error:
            raise ConflictError("配置文件当前无效，先修复原始文件")
        for item in cfg["sources"]:
            if item["bot"].lower() == source.strip().lower():
                item["approval_mode"] = "auto"
                break
        else:
            cfg["sources"].append({"bot": source.strip(), "approval_mode": "auto"})
        return await self.save_config(cfg)

    # ------------------------------------------------------------------ #
    # AI 客户端（与解析器共用；测试替换解析器的 _client 即可覆盖）
    # ------------------------------------------------------------------ #
    def ai_client(self) -> tuple[Any, str]:
        parser = self.pipeline.reg.providers.get("parser", {}).get("llm")
        if parser is not None:
            return parser._client, parser._model
        return ai.make_client()

    def ai_scope(self):
        """服务层发起的 AI 调用也记入调用记录。"""
        return self.pipeline._scope(self.pipeline.cfg)

    def llm_usage(self, days: int = 30) -> dict[str, Any]:
        import time
        return self.store.llm_usage(time.time() - days * 86400)

    def _require_ai(self) -> tuple[Any, str]:
        client, model = self.ai_client()
        if client is None:
            raise ConflictError("未配置 AI（REELAY_LLM_API_KEY），无法使用这项功能")
        return client, model

    # ------------------------------------------------------------------ #
    # 一句话偏好 → 确定性规则（AI 起草，配置校验，用户确认后经配置接口写入）
    # ------------------------------------------------------------------ #
    async def compile_rules(self, text: str, scope: str = "defaults") -> dict[str, Any]:
        from .compiler import compile_rules
        client, model = self._require_ai()
        cfg = self.get_config()
        if self.pipeline.config_error:
            raise ConflictError("配置文件当前无效，先修复原始文件")
        with self.ai_scope():
            try:
                return await compile_rules(client, model, cfg=cfg, scope=scope or "defaults", request=text,
                                           check=self.pipeline.reg.validate_config)
            except ai.AIError as exc:
                raise ConflictError(f"AI 起草规则失败（{exc.category}），请稍后重试") from None

    def organize_preview(
        self,
        *,
        parsed: dict[str, Any],
        landed: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        resource = ParsedResource(**validate_resource(parsed))
        from . import runtime
        with runtime.scope(self.pipeline.cfg):
            return Organizer()._build_plan(resource, landed)

    def get_config(self) -> dict[str, Any]:
        self.pipeline.reload_config()
        return copy.deepcopy(self.pipeline.cfg)

    def get_config_text(self) -> str:
        """返回配置原文；文件不存在时返回 JSON（合法 YAML 子集）。"""
        if self.config_path.exists():
            return self.config_path.read_text(encoding="utf-8")
        try:
            import yaml
            return yaml.safe_dump(self.get_config(), allow_unicode=True, sort_keys=False)
        except ImportError:
            import json
            return json.dumps(self.get_config(), ensure_ascii=False, indent=2)

    async def save_config_text(self, text: str) -> dict[str, Any]:
        try:
            import yaml
            config = yaml.safe_load(text) or {}
        except ImportError:
            import json
            try:
                config = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError("配置文本格式无效；未安装 PyYAML 时请使用 JSON") from exc
        except Exception as exc:
            raise ValueError("配置文本格式无效") from exc
        if not isinstance(config, dict):
            raise ValueError("配置根节点必须是对象")
        return await self.save_config(config)

    async def save_config(self, config: dict[str, Any]) -> dict[str, Any]:
        config = normalize_config(config)
        self.pipeline.reg.validate_config(config)
        try:
            import yaml
            rendered = yaml.safe_dump(config, allow_unicode=True, sort_keys=False)
        except ImportError:
            import json
            rendered = json.dumps(config, ensure_ascii=False, indent=2) + "\n"
        async with self._config_lock:
            target = self.config_path
            target.parent.mkdir(parents=True, exist_ok=True)
            import os
            import tempfile
            fd, name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
            temporary = Path(name)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as output:
                    output.write(rendered)
                    output.flush()
                    os.fsync(output.fileno())
                temporary.replace(target)
                self.pipeline.apply_config(config)
            finally:
                temporary.unlink(missing_ok=True)
        return config

    @staticmethod
    def validate_config(config: dict[str, Any]) -> None:
        normalize_config(config)

    def _require_context(self, task_id: str) -> PipelineContext:
        self.pipeline.reload_config()
        ctx = self.store.get_task(task_id)
        if not ctx:
            raise NotFoundError("任务不存在")
        return ctx

    def _validate_alias(self, alias: str) -> None:
        cid = self.pipeline.save_dirs.get(alias)
        if cid is None or not str(cid).isdecimal():
            raise ValueError(f"目标目录别名不存在或 CID 无效：{alias}")

    @staticmethod
    def _require_editable(ctx: PipelineContext) -> None:
        if ctx.state in (TaskState.RECEIVED, TaskState.PARSED, TaskState.QUEUED,
                         TaskState.SAVING, TaskState.STORED, TaskState.ORGANIZING, TaskState.DONE):
            raise ConflictError("排队中、执行中或已完成任务不能修改")

    def _update(self, ctx: PipelineContext, *, expected: TaskState,
                reset_retries: bool = False) -> None:
        try:
            self.store.update_task_context(ctx, expected=expected, reset_retries=reset_retries)
        except ValueError as exc:
            raise ConflictError(str(exc)) from exc
