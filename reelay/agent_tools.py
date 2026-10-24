"""Agent 工具集：外部 Agent（MCP）与控制 Bot 的对话助手共用同一份定义与实现。

- 每个工具的名称、说明、参数与读写属性只在 ``TOOLS`` 里写一次；MCP（``mcp_server``）与对话助手
  （``control/assistant``）都从这里生成各自的工具描述，参数校验与执行也走同一套代码；
- 实现只调用 ManagementService，与协议无关，返回值均可 JSON 序列化；
- 不对外暴露分享链接与访问码：文本里的 URL 一律隐藏，解析结果的 raw_fields 只放行白名单键；
- 写操作（批准、忽略、重试、订阅）怎么确认由接入方决定：MCP 直接执行，客户端按工具标注决定是否
  逐次询问；对话助手先列成待确认的操作，用户点「执行」后才调用 ``run``。
"""

from __future__ import annotations

import inspect
import re
import time
from dataclasses import dataclass
from typing import Any

from .core.contracts import TaskState
from .core.rules import MIN_RESOLUTIONS
from .core.service import ConflictError, ManagementService, NotFoundError

# 服务层约定的业务错误；返回给模型而不是抛成协议错误。
EXPECTED = (ConflictError, NotFoundError, ValueError)
STATES = tuple(state.value for state in TaskState)
MAX_LIMIT = 100
_TRAIL_LINES = 15
# raw_fields 里的 share_url / llm_raw 可能带完整分享链接与访问码，只放行无敏感值的键。
_SAFE_RAW_FIELDS = ("tmdbid", "parser_confidence")
_HEALTH_FIELDS = ("status", "stale", "error", "config_error")
_LIBRARY_FIELDS = ("title", "year", "media_type", "tmdbid", "resolution", "release", "seasons",
                   "origin_country", "save_dir")
_URL_RE = re.compile(r"https?://\S+")


@dataclass(frozen=True)
class Param:
    name: str
    type: type  # str / int / list（字符串数组）
    description: str
    required: bool = False
    enum: tuple[str, ...] | None = None

    def schema(self) -> dict[str, Any]:
        if self.type is list:
            out: dict[str, Any] = {"type": "array", "items": {"type": "string"}}
        else:
            out = {"type": "integer" if self.type is int else "string"}
        if self.enum:
            out["enum"] = list(self.enum)
        out["description"] = self.description
        return out


@dataclass(frozen=True)
class Tool:
    name: str
    title: str
    description: str
    params: tuple[Param, ...] = ()
    write: bool = False
    destructive: bool = False  # 会发起真实 115 转存与整理（洗版时可能替换已有文件），无法从这里撤销
    open_world: bool = False  # 会访问外部服务（115、TMDB、AI 接口）

    def parameters(self) -> dict[str, Any]:
        """JSON Schema，对话助手直接作为 function 参数使用。"""
        return {"type": "object", "properties": {p.name: p.schema() for p in self.params},
                "required": [p.name for p in self.params if p.required]}


_LIMIT = Param("limit", int, "返回条数 1-100，默认 20 / Max items, 1-100, default 20")
_TASK_ID = Param("task_id", str, "任务 ID，先用查询工具找到 / Task ID from a query tool", required=True)
_TASK_IDS = Param("task_ids", list, "1-100 个任务 ID，先用查询工具找到，不要编造 / "
                                    "1-100 task IDs taken from a query tool; never invent them", required=True)

TOOLS: tuple[Tool, ...] = (
    Tool("status", "运行概况",
         "Reelay 运行概况：各状态任务数、待处理/执行中/失败/完成数、运行模式、通知投递与 worker/消息源/115 健康状态。\n"
         "Overview: task counts by state, pending/active/failed/done totals, run mode "
         "(live / not_running / not_configured / simulation), delivery counts, and "
         "worker / source / 115 auth health (stale=true means no recent heartbeat)."),
    Tool("list_pending", "待处理任务",
         "列出需要人处理的任务：pending_approval（待审批，附建议动作与理由、同集多版本中的推荐版本、是否已订阅、"
         "等待天数）与 need_manual（转存结果待核验或整理失败，附问题说明）。\n"
         "Tasks waiting for a human: pending_approval (with suggested action and reason, the recommended "
         "version among duplicates, subscription flag, days waiting; approve/reject them) and need_manual "
         "(transfer result or organize step needs attention, with the problem).",
         (_LIMIT,)),
    Tool("list_tasks", "检索任务",
         "按状态或关键字列出任务，最近更新在前；query 匹配片名、来源、任务 ID 或错误信息。\n"
         "List tasks, newest first, optionally filtered by state; query matches title/source/task_id/error.",
         (Param("state", str, "任务状态 / Task state", enum=STATES),
          Param("query", str, "关键字 / Keyword"), _LIMIT)),
    Tool("get_task", "任务详情",
         "任务详情：识别结果（访问码已脱敏）、目标目录别名、决策、整理计划的目标路径、转存阶段与最近 15 条轨迹。\n"
         "Task details: parsed resource (receive code masked), save_dir_alias, decision, organize plan "
         "target paths, storage phase, and the last 15 trail lines.",
         (_TASK_ID,)),
    Tool("list_save_dirs", "保存目录",
         "可用的保存目录别名及其 115 目录 CID，approve 的 save_dir_alias 取这里的键。\n"
         "Map of save-dir alias to 115 folder CID; use an alias as approve's save_dir_alias."),
    Tool("search_library", "检索媒体库",
         "查询已入库的作品，可按片名、类型（anime 按 TMDB 动画类型判断）、最低清晰度、两位国家/地区代码与 "
         "TMDB 类型 ID 筛选；返回各季已入库的集，可用来判断是否已有某资源。\n"
         "Search the organized library by title, media type (anime is decided by the TMDB animation genre), "
         "minimum resolution, two-letter origin country and TMDB genre id; returns stored episodes per season, "
         "useful to check whether something is already in the library.",
         (Param("query", str, "片名关键字 / Title keyword"),
          Param("media_type", str, "类型 / Media type", enum=("movie", "tv", "anime")),
          Param("min_resolution", str, "最低清晰度 / Minimum resolution", enum=tuple(MIN_RESOLUTIONS)),
          Param("origin_country", str, "两位国家/地区代码，如 JP / Two-letter country code, e.g. JP"),
          Param("genre_id", int, "TMDB 类型 ID / TMDB genre id"), _LIMIT)),
    Tool("missing_episodes", "缺集情况",
         "缺集情况：每日追更巡检的结果（缺哪几集、TMDB 已播几集、哪几集播出多天仍未入库）。\n"
         "Missing episodes from the daily tracker: gaps, episodes aired on TMDB, and episodes aired "
         "for days but still not in the library.",
         (Param("title", str, "只看这部剧 / Limit to this show"),)),
    Tool("list_subscriptions", "追剧订阅",
         "已订阅的剧（新集自动转存）及从批准记录发现的订阅建议。\n"
         "Subscribed shows (new episodes are transferred automatically) and suggestions derived from approvals."),
    Tool("list_ignored", "不再接收的作品",
         "「不再接收」的作品：之后的消息识别完直接忽略。unignore 的参数取这里的 title / tmdbid / kind。\n"
         "Works the user chose to stop receiving; later messages for them are skipped. Use an item's "
         "title / tmdbid / kind as unignore's arguments."),
    Tool("find_show", "查找剧集",
         "把剧名或一句话对应到 TMDB 条目与季，订阅前先用它确认 tmdbid。\n"
         "Resolve a show name or sentence to TMDB candidates with their seasons; use it to get the tmdbid "
         "before subscribing.",
         (Param("text", str, "剧名或一句话 / Show name or sentence", required=True),), open_world=True),
    Tool("approve", "批准转存",
         "批准待审批（pending_approval）任务开始转存，可选改投到 list_save_dirs 中的另一个目录别名；"
         "不传则按自动分类存放，改投到与分类不同的目录时不再套用分类子目录。"
         "批准后由正在运行的 Reelay worker 执行真实的 115 转存与整理，无法从这里撤销。\n"
         "Approve pending_approval tasks, optionally overriding the target save-dir alias (omit it to keep "
         "automatic categorization; an override skips category subfolders). This queues a REAL 115 cloud "
         "transfer that the running Reelay worker executes; it cannot be undone from here. "
         "Returns {ok, results: [{task_id, ok, state | error}]}.",
         (_TASK_IDS, Param("save_dir_alias", str, "目录别名 / Save-dir alias")),
         write=True, destructive=True, open_world=True),
    Tool("reject", "忽略",
         "忽略待审批任务：标记为 skipped，不会转存。\n"
         "Reject pending_approval tasks: they are marked skipped and nothing is transferred. "
         "Returns {ok, results: [{task_id, ok, state | error}]}.",
         (_TASK_IDS,), write=True),
    Tool("retry", "重试",
         "重试失败（failed）任务：按记录的检查点重新排队，worker 会再次执行，可能再次发起真实 115 转存。\n"
         "Retry failed tasks from their recorded checkpoint. The running worker picks them up again and may "
         "perform a real 115 transfer. Returns {ok, results: [{task_id, ok, state | error}]}.",
         (_TASK_IDS,), write=True, destructive=True, open_world=True),
    Tool("subscribe", "订阅剧集",
         "订阅一部剧的某一季，后续新集自动转存；tmdbid 先用 find_show 确认。返回待审批里属于这一季的任务 ID，"
         "可以接着 approve。\n"
         "Subscribe to one season of a show so new episodes are transferred automatically; get the tmdbid "
         "from find_show first. Returns pending task IDs of that season, which you may approve next.",
         (Param("title", str, "片名 / Title", required=True),
          Param("season", int, "季号 0-99 / Season number 0-99", required=True),
          Param("tmdbid", int, "TMDB 剧集 ID / TMDB tv id")),
         write=True),
)
_SUB = (Param("title", str, "片名，取 list_subscriptions 里的写法 / Title as in list_subscriptions", required=True),
        Param("season", int, "季号 0-99 / Season number 0-99", required=True),
        Param("tmdbid", int, "TMDB 剧集 ID / TMDB tv id"))
TOOLS += (
    Tool("unsubscribe", "取消订阅",
         "取消某部剧某一季的订阅：之后的新集不再自动转存，也不再巡检缺集。参数取 list_subscriptions 里的值。\n"
         "Unsubscribe from one season: new episodes are no longer transferred automatically or checked for gaps. "
         "Use the values from list_subscriptions.", _SUB, write=True),
    Tool("resume_subscription", "继续订阅",
         "已完结（一季播完且全部入库后自动结束）的订阅继续订阅，如出了特别篇或补了集。参数取 list_subscriptions 里的值。\n"
         "Resume a subscription that ended automatically when the season finished (e.g. a special was added). "
         "Use the values from list_subscriptions.", _SUB, write=True),
    Tool("unignore", "恢复接收",
         "恢复接收一部「不再接收」的作品，参数取 list_ignored 里的值。\n"
         "Start receiving a work again; use the values from list_ignored.",
         (Param("title", str, "片名 / Title", required=True), Param("tmdbid", int, "TMDB ID"),
          Param("kind", str, "TMDB 类型 / TMDB kind", enum=("movie", "tv"))), write=True),
    Tool("correct", "修正识别结果",
         "修正一条待审批、待人工或失败任务的识别结果，只传要改的字段；待审批的随后按新片名重新核对 TMDB。"
         "失败任务改完后通常还要 retry。\n"
         "Correct the parsed result of a pending_approval, need_manual or failed task; pass only fields to change. "
         "Pending tasks are re-matched against TMDB afterwards; failed tasks usually need retry next.",
         (_TASK_ID, Param("title", str, "片名 / Title"), Param("year", int, "年份 / Year"),
          Param("media_type", str, "类型 / Media type", enum=("movie", "tv", "anime")),
          Param("season", int, "季号 0-99 / Season number 0-99"),
          Param("episodes", str, "集号，如 1-5,7 / Episodes, e.g. 1-5,7"),
          Param("resolution", str, "分辨率，如 1080p / Resolution, e.g. 1080p")), write=True),
    Tool("reparse", "重新识别",
         "让系统重新识别这些任务的消息（不用缓存），待审批的识别完会重新推送卡片。已有转存检查点的任务不行。\n"
         "Re-run recognition for these tasks without the cache; pending ones get a fresh approval card. "
         "Not allowed once a transfer checkpoint exists.", (_TASK_IDS,), write=True),
)
TOOL_MAP = {tool.name: tool for tool in TOOLS}


def _clamp(limit: int) -> int:
    return max(1, min(int(limit), MAX_LIMIT))


def scrub(text: str | None) -> str | None:
    """异常与提示文本可能带含访问码的请求 URL，对外一律隐藏。"""
    return _URL_RE.sub("<链接已隐藏>", text) if text else text


def _brief(item: dict[str, Any]) -> dict[str, Any]:
    return {**item, "error": scrub(item.get("error"))}


def _safe_parsed(parsed: dict[str, Any] | None) -> dict[str, Any] | None:
    if not parsed:
        return None
    parsed = dict(parsed)
    raw = parsed.pop("raw_fields", None) or {}
    kept = {key: raw[key] for key in _SAFE_RAW_FIELDS if key in raw}
    if kept:
        parsed["raw_fields"] = kept
    return parsed


def _plan(context: dict[str, Any]) -> list[dict[str, Any]]:
    plan = ((context.get("meta") or {}).get("organize") or {}).get("plan") or []
    return [{"target_dir": item.get("target_dir"), "target_name": item.get("target_name")}
            for item in plan]


def _episode_list(text: str) -> list[int]:
    """「1-5,7」→ [1, 2, 3, 4, 5, 7]。"""
    out: set[int] = set()
    for part in re.split(r"[,，、\s]+", text.strip()):
        match = re.fullmatch(r"[Ee]?(\d{1,4})(?:\s*[-~到至]\s*[Ee]?(\d{1,4}))?", part)
        if not part:
            continue
        if not match:
            raise ValueError(f"集号写法无效：{part}")
        start, end = int(match.group(1)), int(match.group(2) or match.group(1))
        if not 0 < start <= end <= 2000:
            raise ValueError(f"集号无效：{part}")
        out.update(range(start, end + 1))
    return sorted(out)


def _coerce(param: Param, value: Any) -> Any:
    if param.type is int:
        if type(value) is str and value.strip().lstrip("-").isdecimal():
            value = int(value)
        if type(value) is not int:
            raise ValueError(f"{param.name} 必须是整数")
    elif param.type is list:
        if type(value) is not list or any(type(item) is not str for item in value):
            raise ValueError(f"{param.name} 必须是字符串数组")
    elif type(value) is not str:
        raise ValueError(f"{param.name} 必须是字符串")
    if param.enum and value not in param.enum:
        raise ValueError(f"{param.name} 只能是：{', '.join(param.enum)}")
    return value


class AgentTools:
    """工具实现；方法名与 ``TOOLS`` 中的工具名一一对应。"""

    def __init__(self, service: ManagementService) -> None:
        self.service = service
        self.store = service.store

    # -- 调度 ---------------------------------------------------------------- #
    def validate(self, name: str, args: dict[str, Any] | None) -> dict[str, Any]:
        """按参数定义检查并规整参数（未知参数忽略）；写操作还检查任务与目录别名是否存在。无效抛 ValueError。"""
        tool = TOOL_MAP.get(name)
        if tool is None:
            raise ValueError(f"未知工具：{name}")
        args = {} if args is None else args
        if not isinstance(args, dict):
            raise ValueError("参数必须是对象")
        out: dict[str, Any] = {}
        for param in tool.params:
            value = args.get(param.name)
            if isinstance(value, str):
                value = value.strip()
            if value is None or value == "" or value == []:
                if param.required:
                    raise ValueError(f"缺少参数 {param.name}")
                continue
            out[param.name] = _coerce(param, value)
        if "task_ids" in out:
            ids = list(dict.fromkeys(out["task_ids"]))
            if len(ids) > MAX_LIMIT:
                raise ValueError(f"task_ids 最多 {MAX_LIMIT} 个")
            missing = [task_id for task_id in ids if not self.store.get_task(task_id)]
            if missing:
                raise ValueError(f"任务不存在：{'、'.join(missing[:5])}")
            out["task_ids"] = ids
        alias = out.get("save_dir_alias")
        if alias and alias not in self.service.get_config()["save_dirs"]:
            raise ValueError(f"目录别名不存在：{alias}")
        if "season" in out and not 0 <= out["season"] <= 99:
            raise ValueError("季号必须在 0 到 99 之间")
        if out.get("tmdbid") is not None and out["tmdbid"] <= 0:
            out.pop("tmdbid")
        if name == "correct":
            if "episodes" in out:
                _episode_list(out["episodes"])  # 提议时就报出写法错误，不等用户点「执行」
            if not self.store.get_task(out["task_id"]):
                raise ValueError(f"任务不存在：{out['task_id']}")
            if set(out) == {"task_id"}:
                raise ValueError("没有要修改的字段")
        return out

    async def run(self, name: str, kwargs: dict[str, Any]) -> Any:
        """执行已经 ``validate`` 过的参数（对话助手在用户确认后直接调用）。"""
        if name not in TOOL_MAP:
            raise ValueError(f"未知工具：{name}")
        result = getattr(self, name)(**kwargs)
        return await result if inspect.isawaitable(result) else result

    async def call(self, name: str, args: dict[str, Any] | None = None) -> Any:
        """校验并执行；业务错误（参数无效、找不到、状态不对）返回 {ok: false, error}，交给调用方的模型处理。"""
        try:
            return await self.run(name, self.validate(name, args))
        except EXPECTED as exc:
            return {"ok": False, "error": scrub(str(exc))[:300]}

    # -- 查询 ---------------------------------------------------------------- #
    def status(self) -> dict[str, Any]:
        data = self.service.dashboard()
        summary = {key: data.get(key) for key in (
            "mode", "counts", "pending", "active", "failed", "done", "deliveries",
        )}
        summary["runtime"] = {
            name: {key: scrub(item[key]) if isinstance(item[key], str) else item[key]
                   for key in _HEALTH_FIELDS if item.get(key) not in (None, "")}
            for name, item in (data.get("runtime") or {}).items()
        }
        if "alerts" in data:
            summary["alerts"] = data["alerts"]
        return summary

    def list_pending(self, limit: int = 20) -> dict[str, Any]:
        limit = _clamp(limit)
        now = time.time()
        pending = self.service.inbox(TaskState.PENDING_APPROVAL.value, limit)["items"]
        manual = self.service.inbox(TaskState.NEED_MANUAL.value, limit)["items"]
        return {"pending_approval": [{
            "task_id": i["task_id"], "title": i["title"], "media_type": i["media_type"], "season": i["season"],
            "episodes": i["episodes"], "episode_pairs": i.get("episode_pairs"), "resolution": i["resolution"],
            "release": i.get("release_tags"), "source": i["source"], "save_dir_alias": i.get("save_dir_alias"),
            "waiting_days": round((now - i["created_at"]) / 86400, 1),
            "advice": (i.get("advice") or {}).get("action"), "reason": (i.get("advice") or {}).get("text"),
            "recommended_version": (i.get("version") or {}).get("best"), "subscribed": i.get("subscribed"),
        } for i in pending], "need_manual": [{
            "task_id": i["task_id"], "title": i["title"],
            "problem": scrub((i.get("explanation") or {}).get("text") or i.get("organize_error") or i.get("error")),
        } for i in manual]}

    def list_tasks(self, state: str | None = None, query: str | None = None,
                   limit: int = 20) -> dict[str, Any]:
        items = self.service.list_tasks(state=state, query=query, limit=_clamp(limit))
        return {"items": [_brief(item) for item in items]}

    def get_task(self, task_id: str) -> dict[str, Any]:
        record = self.service.get_task(task_id)
        context = record.get("context") or {}
        meta = context.get("meta") or {}
        return {
            "task_id": record["task_id"],
            "state": record["state"],
            "title": record.get("title"),
            "error": scrub(record.get("error")),
            "retries": record.get("retries"),
            "source": record.get("source"),
            "parsed": _safe_parsed(context.get("parsed")),
            "save_dir_alias": context.get("save_dir_alias"),
            "decision": context.get("decision"),
            "organize_plan": _plan(context),
            "storage_phase": (meta.get("storage") or {}).get("phase"),
            "trail": [scrub(line) for line in (context.get("trail") or [])[-_TRAIL_LINES:]],
        }

    def list_save_dirs(self) -> dict[str, str]:
        return {alias: str(cid) for alias, cid in self.service.get_config()["save_dirs"].items()}

    def search_library(self, query: str | None = None, media_type: str | None = None,
                       min_resolution: str | None = None, origin_country: str | None = None,
                       genre_id: int | None = None, limit: int = 20) -> dict[str, Any]:
        result = self.service.library_query(query=query, media_type=media_type, min_resolution=min_resolution,
                                            origin_country=origin_country, genre_id=genre_id, limit=_clamp(limit))
        return {"total": result["total"],
                "items": [{key: item.get(key) for key in _LIBRARY_FIELDS} for item in result["items"]]}

    def missing_episodes(self, title: str | None = None) -> dict[str, Any]:
        return self.service.missing_episodes(title)

    def list_subscriptions(self) -> dict[str, Any]:
        data = self.service.list_subscriptions()
        return {"items": [{key: item[key] for key in ("title", "season", "tmdbid", "auto", "missing")}
                          | {"have": len(item["have"]), "finished": bool(item.get("finished"))} for item in data["items"]],
                "suggestions": [item["text"] for item in data["suggestions"]], "inactive": data["inactive"]}

    def list_ignored(self) -> dict[str, Any]:
        return self.service.list_ignored_works()

    async def find_show(self, text: str) -> dict[str, Any]:
        return await self.service.resolve_subscription(text)

    # -- 写操作 -------------------------------------------------------------- #
    async def approve(self, task_ids: list[str], save_dir_alias: str | None = None) -> dict[str, Any]:
        return await self._each(task_ids, lambda task_id: self.service.approve(task_id, save_dir_alias=save_dir_alias))

    async def reject(self, task_ids: list[str]) -> dict[str, Any]:
        return await self._each(task_ids, self.service.reject)

    async def retry(self, task_ids: list[str]) -> dict[str, Any]:
        return await self._each(task_ids, self.service.retry)

    async def subscribe(self, title: str, season: int, tmdbid: int | None = None) -> dict[str, Any]:
        result = await self.service.add_subscription({"title": title, "season": season, "tmdbid": tmdbid})
        return {"ok": True, **result}

    async def unsubscribe(self, title: str, season: int, tmdbid: int | None = None) -> dict[str, Any]:
        await self.service.remove_subscription({"title": title, "season": season, "tmdbid": tmdbid})
        return {"ok": True}

    async def resume_subscription(self, title: str, season: int, tmdbid: int | None = None) -> dict[str, Any]:
        self.service.reopen_subscription({"title": title, "season": season, "tmdbid": tmdbid})
        return {"ok": True}

    async def unignore(self, title: str, tmdbid: int | None = None, kind: str | None = None) -> dict[str, Any]:
        await self.service.unignore_work({"title": title, "tmdbid": tmdbid, "kind": kind})
        return {"ok": True}

    async def correct(self, task_id: str, title: str | None = None, year: int | None = None,
                      media_type: str | None = None, season: int | None = None, episodes: str | None = None,
                      resolution: str | None = None) -> dict[str, Any]:
        patch = {key: value for key, value in (("title", title), ("year", year), ("media_type", media_type),
                                               ("season", season), ("resolution", resolution)) if value is not None}
        if episodes is not None:
            patch["episodes"] = _episode_list(episodes)
        if not patch:
            raise ValueError("没有要修改的字段")
        record = await self.service.correct_parsed(task_id, patch)
        return {"ok": True, "parsed": _safe_parsed((record.get("context") or {}).get("parsed"))}

    async def reparse(self, task_ids: list[str]) -> dict[str, Any]:
        return await self._each(task_ids, self.service.reparse)

    async def _each(self, task_ids: list[str], action) -> dict[str, Any]:
        """逐条执行并逐条报告，一条失败不影响其余任务。"""
        results = []
        for task_id in task_ids:
            try:
                await action(task_id)
            except Exception as exc:  # noqa: BLE001 - 逐条报告
                results.append({"task_id": task_id, "ok": False, "error": scrub(str(exc))})
                continue
            ctx = self.store.get_task(task_id)
            results.append({"task_id": task_id, "ok": True, "state": ctx.state.value if ctx else None})
        return {"ok": all(item["ok"] for item in results), "results": results}
