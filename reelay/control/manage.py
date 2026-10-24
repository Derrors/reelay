"""控制 Bot 里不依赖 AI 的管理操作：修正识别结果的文字解析，以及 /pending、/failed、/subs、/ignored 列表。

列表都是「正文 + 按钮」：每条一个按钮，点开重新发出那条任务的卡片（``cd``），或直接取消订阅、恢复接收；
按钮数据不超过 64 字节，订阅与不再接收的作品用短哈希指代（``short_key``），点的时候再按当前配置找回。
"""
from __future__ import annotations

import hashlib
import re
from typing import Any

from ..core import ignored, messages, subscriptions
from ..core.contracts import TaskState
from ..core.media import pairs_label, planned_pairs
from ..plugins.episode_tracker import format_episodes

PAGE = 8
MAX_EPISODE = 2000

# --------------------------------------------------------------------------- #
# 修正识别结果：回复「修正」提示的一句话 → 要改的字段
# --------------------------------------------------------------------------- #
_TYPES = {"电影": "movie", "剧集": "tv", "电视剧": "tv", "连续剧": "tv", "动漫": "anime", "动画": "anime", "番剧": "anime"}
_RESOLUTIONS = {"4k": "2160p", "2160p": "2160p", "1080p": "1080p", "720p": "720p", "480p": "480p"}
_RANGE = r"\s*(?:-|~|～|到|至)\s*"


def _episodes(start: str, end: str | None) -> list[int]:
    a, b = int(start), int(end or start)
    if not 0 < a <= b <= MAX_EPISODE:
        raise ValueError(f"集数 {a}-{b} 无效")
    return list(range(a, b + 1))


def parse_correction(text: str) -> dict[str, Any]:
    """「繁花 2023 剧集 S01E03」「片名 繁花」「第 2 季 E01-E05」→ 要改的字段；没写的不改。

    逐项认出季、集、年份、类型、分辨率后，剩下的文字是片名（也可以用「片名 X」或《X》写明）。
    写成电影时清空季与集。认不出任何一项时返回空字典。
    """
    rest = " ".join((text or "").split())
    patch: dict[str, Any] = {}
    episodes: list[int] = []

    def take(pattern: str, handle) -> None:
        nonlocal rest
        for match in list(re.finditer(pattern, rest, flags=re.I)):
            handle(match)
        rest = re.sub(pattern, " ", rest, flags=re.I)

    title = re.search(r"《(.+?)》", rest)
    if title:
        patch["title"] = title.group(1).strip()
        rest = rest.replace(title.group(0), " ")
    take(r"(?<![a-z])S(\d{1,2})\s*E(\d{1,4})(?:" + _RANGE + r"E?(\d{1,4}))?(?!\d)",
         lambda m: (patch.__setitem__("season", int(m.group(1))), episodes.extend(_episodes(m.group(2), m.group(3)))))
    take(r"第\s*(\d{1,2})\s*季|(?<![a-z])S(\d{1,2})(?![\da-z])|季\s*[:：]?\s*(\d{1,2})(?!\d)",
         lambda m: patch.__setitem__("season", int(next(g for g in m.groups() if g))))
    take(r"第\s*(\d{1,4})(?:" + _RANGE + r"(\d{1,4}))?\s*集|(?<![a-z])EP?(\d{1,4})(?:" + _RANGE + r"E?P?(\d{1,4}))?(?!\d)",
         lambda m: episodes.extend(_episodes(m.group(1) or m.group(3), m.group(2) or m.group(4))))
    take(r"集数?\s*[:：]?\s*(\d{1,4})(?:" + _RANGE + r"(\d{1,4}))?(?!\d)",
         lambda m: episodes.extend(_episodes(m.group(1), m.group(2))))
    take(r"年份\s*[:：]?\s*(\d{4})|(?<!\d)(\d{4})\s*年|(?<![\d.])((?:19|20)\d{2})(?![\dpk])",
         lambda m: patch.__setitem__("year", int(next(g for g in m.groups() if g))))
    take(r"(?<![\da-z])(4k|2160p|1080p|720p|480p)(?![\da-z])",
         lambda m: patch.__setitem__("resolution", _RESOLUTIONS[m.group(1).lower()]))
    take(r"类型\s*[:：]?", lambda m: None)
    take(r"(?:^|(?<=\s))(" + "|".join(_TYPES) + r")(?=\s|$)",
         lambda m: patch.__setitem__("media_type", _TYPES[m.group(1)]))
    rest = re.sub(r"(?:片名|标题|名称)\s*[:：]?", " ", rest)
    leftover = rest.strip(" ,，、·|:：-")
    if re.search(r"\w", leftover) and "title" not in patch:
        patch["title"] = " ".join(leftover.split())
    if episodes:
        patch["episodes"] = sorted(set(episodes))
    if patch.get("media_type") == "movie":
        patch.setdefault("season", None)
        patch.setdefault("episodes", [])
    elif ("episodes" in patch or "season" in patch) and "media_type" not in patch:
        pass  # 类型不写就不改：写了季集的电影会在校验时报错，提示用户写明类型
    return patch


_LABELS = {"title": "片名", "year": "年份", "media_type": "类型", "season": "季", "episodes": "集",
           "resolution": "分辨率"}
_KINDS = {"movie": "电影", "tv": "剧集", "anime": "动漫"}


def describe_changes(before, after) -> str:
    """修正前后的识别结果 → 「片名：甲 → 乙；季：1 → 2」。"""
    def show(key: str, value: Any) -> str:
        if value in (None, "", []):
            return "空"
        if key == "media_type":
            return _KINDS.get(value, value)
        if key == "episodes":
            return format_episodes(value)
        return str(value)
    parts = [f"{_LABELS[key]}：{show(key, getattr(before, key, None))} → {show(key, getattr(after, key, None))}"
             for key in _LABELS if getattr(before, key, None) != getattr(after, key, None)]
    return "；".join(parts) or "没有变化"


# --------------------------------------------------------------------------- #
# 列表
# --------------------------------------------------------------------------- #
def short_key(*parts: Any) -> str:
    """按钮里指代一条订阅或作品的短键（配置里的顺序会变，不能用序号）。"""
    return hashlib.sha1(repr(parts).encode("utf-8")).hexdigest()[:10]


def _task_label(ctx) -> str:
    """列表按钮上的一条任务：类型图标、片名、季集。"""
    p = ctx.parsed
    title = (p.title if p else None) or ((ctx.raw.text or "").strip().splitlines() or ["（未识别）"])[0][:20]
    pairs = sorted(set(planned_pairs(ctx)))
    seasons = {season for season, _ in pairs}
    if len(seasons) > 1:
        episodes = pairs_label(pairs)
    elif pairs:
        episodes = f"S{seasons.pop():02d} {format_episodes([e for _, e in pairs], limit=2)}"
    else:
        episodes = ""
    icon = messages.type_icon(p.media_type if p else None, bool(pairs))
    return f"{icon} {title} {episodes}".strip()[:48]


def _pager(prefix: str, offset: int, total: int) -> list[tuple[str, str]]:
    row = []
    if offset > 0:
        row.append(("⬅️ 上一页", f"{prefix}:{max(0, offset - PAGE)}"))
    if offset + PAGE < total:
        row.append(("下一页 ➡️", f"{prefix}:{offset + PAGE}"))
    return row


def _counts(service) -> dict[str, int]:
    counts = service.store.counts_by_state()
    return {"pending": counts.get(TaskState.PENDING_APPROVAL.value, 0) + counts.get(TaskState.NEED_MANUAL.value, 0),
            "failed": counts.get(TaskState.FAILED.value, 0)}


def _nav(service, *skip: str) -> list:
    """列表底部互相跳转的一行按钮：待处理、失败（有才显示数字）与运行概况。"""
    counts = _counts(service)
    pending = f"📋 待处理 {counts['pending']}" if counts["pending"] else "📋 待处理"
    items = [("pending", pending, "pl:0"),
             ("failed", f"❌ 失败 {counts['failed']}", "fl:0"), ("status", "📊 运行概况", "st")]
    row = [(label, data) for key, label, data in items if key not in skip and (key != "failed" or counts["failed"])]
    return [row] if row else []


def _view(head: str, *blocks: str) -> str:
    """列表的统一排版：粗体标题，各段之间空一行。"""
    return "\n\n".join([head, *[block for block in blocks if block]])


def pending_list(service, offset: int = 0) -> tuple[str, list]:
    """待审批与待人工：每条一个按钮点开卡片；待审批可以一键全部批准或忽略（先确认）。"""
    store = service.store
    rows = [*store.list_by_state(TaskState.PENDING_APPROVAL, 500), *store.list_by_state(TaskState.NEED_MANUAL, 500)]
    if not rows:
        return (_view("📋 <b>待处理</b>", "✅ 没有需要你确认或处理的资源。\n新资源到了会推送审批卡片。"),
                _nav(service, "pending"))
    approval = sum(1 for r in rows if r["state"] == TaskState.PENDING_APPROVAL.value)
    manual = len(rows) - approval
    offset = max(0, min(offset, (len(rows) - 1) // PAGE * PAGE))
    page = [store.get_task(r["task_id"]) for r in rows[offset:offset + PAGE]]
    head = "📋 <b>待处理</b> · " + " · ".join(filter(None, [f"待审批 {approval}" if approval else "",
                                                          f"待人工 {manual}" if manual else ""]))
    hint = "点下方一条打开卡片处理" + ("；⚠️ 是转存或整理停下来、需要你处理的" if manual else "") + "。"
    if len(rows) > PAGE:
        hint += f"\n第 {offset + 1}–{offset + len(page)} 条，共 {len(rows)} 条。"
    buttons = [[(("⚠️ " if ctx.state == TaskState.NEED_MANUAL else "") + _task_label(ctx), f"cd:{ctx.task_id}")]
               for ctx in page if ctx]
    pager = _pager("pl", offset, len(rows))
    if pager:
        buttons.append(pager)
    if approval:
        buttons.append([(f"✅ 全部批准（{approval}）", "pa"), (f"🚫 全部忽略（{approval}）", "pr")])
    return _view(head, hint), buttons


def bulk_confirm(service, action: str) -> tuple[str, list]:
    count = len(service.store.list_by_state(TaskState.PENDING_APPROVAL, 500))
    verb = "批准转存" if action == "pa" else "忽略"
    return (f"⚠️ 确认{verb}待审批里的全部 <b>{count}</b> 条？",
            [[(f"确认{verb}", f"{action}y"), ("↩ 返回", "pl:0")]])


async def bulk_run(service, action: str) -> str:
    done, failed = 0, 0
    for row in service.store.list_by_state(TaskState.PENDING_APPROVAL, 500):
        try:
            await (service.approve(row["task_id"]) if action == "pay" else service.reject(row["task_id"]))
            done += 1
        except Exception:  # noqa: BLE001 - 期间已被处理的跳过
            failed += 1
    verb = "批准，正在转存，完成后逐条通知" if action == "pay" else "忽略"
    return f"✅ 已{verb} {done} 条" + (f"，{failed} 条期间已被处理" if failed else "")


def failed_list(service, offset: int = 0) -> tuple[str, list]:
    store = service.store
    rows = store.list_by_state(TaskState.FAILED, 500)
    if not rows:
        return (_view("❌ <b>失败任务</b>", "✅ 没有失败任务。\n转存或整理出错时会推送提醒。"),
                _nav(service, "failed"))
    offset = max(0, min(offset, (len(rows) - 1) // PAGE * PAGE))
    page = [store.get_task(r["task_id"]) for r in rows[offset:offset + PAGE]]
    hint = "点下方一条查看原因，可以修正、重试或忽略。"
    if len(rows) > PAGE:
        hint += f"\n第 {offset + 1}–{offset + len(page)} 条，共 {len(rows)} 条。"
    buttons = [[(_task_label(ctx), f"cd:{ctx.task_id}")] for ctx in page if ctx]
    pager = _pager("fl", offset, len(rows))
    if pager:
        buttons.append(pager)
    return _view(f"❌ <b>失败任务</b> · {len(rows)} 条", hint), buttons


def find_subscription(service, key: str) -> dict | None:
    finished = service.store.state_get(*subscriptions.FINISHED, {}) or {}
    return next((item for item in subscriptions.items(service.get_config(), finished)
                 if subscriptions.button_key(item) == key), None)


def find_suggestion(service, key: str) -> dict | None:
    return next((item for item in service.subscription_suggestions() if subscriptions.button_key(item) == key), None)


def _suggestions(suggestions: list[dict]) -> tuple[str, list]:
    if not suggestions:
        return "", []
    lines = "\n".join(f"• {messages.esc(item['text'])}" for item in suggestions)
    return (f"💡 <b>订阅建议</b>\n{lines}",
            [row for item in suggestions for row in messages.suggestion_buttons(item, in_list=True)])


def subs_list(service) -> tuple[str, list]:
    """订阅：每部一段（类型图标、片名与季、入库与缺集），下方每部一个取消或继续订阅的按钮；
    最后是还没处理的订阅建议，可以订阅或点「不用了」。"""
    data = service.list_subscriptions()
    items = data["items"]
    suggested, suggestion_buttons = _suggestions(data["suggestions"])
    if not items:
        empty = "还没有订阅。\n在审批卡片上点「🔔 追剧」，或对我说「我在追《X》第二季」。"
        return (_view("🔔 <b>追剧订阅</b>", empty, suggested),
                suggestion_buttons or _nav(service))
    blocks, buttons = [], []
    for item in items[:20]:
        icon = "🍥" if item.get("category") == "动漫" else "📺"
        if item.get("finished"):
            state = "🏁 已播完，订阅已结束"
        elif item.get("missing"):
            state = f"缺 {format_episodes(item['missing'], limit=3)}"
        else:
            state = "不缺集"
        blocks.append(f"{icon} <b>{messages.esc(item['title'])}</b> · S{item['season']:02d}\n"
                      f"已入库 {len(item['have'])} 集 · {state}")
        name = f"{item['title']} S{item['season']:02d}"
        key = subscriptions.button_key(item)
        buttons.append([(f"▶️ 继续订阅 {name}"[:40], f"so:{key}")] if item.get("finished")
                       else [(f"🗑 取消订阅 {name}"[:40], f"us:{key}")])
    if len(items) > 20:
        blocks.append(f"… 另有 {len(items) - 20} 部")
    return _view(f"🔔 <b>追剧订阅</b> · {len(items)} 部", *blocks, suggested), buttons + suggestion_buttons


def ignored_key(item: dict) -> str:
    return short_key("ign", ignored.identity(item))


def find_ignored(service, key: str) -> dict | None:
    return next((item for item in ignored.items(service.get_config()) if ignored_key(item) == key), None)


def ignored_list(service) -> tuple[str, list]:
    items = ignored.items(service.get_config())
    if not items:
        return (_view("⛔ <b>不再接收</b>", "没有不再接收的作品。\n审批卡片上点「⛔ 不再接收」后，那部作品之后的消息会直接忽略。"),
                _nav(service))
    lines = [f"• 《{messages.esc(item['title'])}》" for item in items[:20]]
    if len(items) > 20:
        lines.append(f"… 另有 {len(items) - 20} 部")
    return (_view(f"⛔ <b>不再接收</b> · {len(items)} 部", "这些作品之后的消息会直接忽略，点下方按钮恢复。", "\n".join(lines)),
            [[(f"↩️ 恢复接收 {item['title']}"[:40], f"ui:{ignored_key(item)}")] for item in items[:20]])


# --------------------------------------------------------------------------- #
# /status 与 /help
# --------------------------------------------------------------------------- #
_OK = {"connected", "active", "running"}
_PROBLEM = {"failed": "异常", "stale": "没有响应", "disconnected": "已断开", "connecting": "连接中",
            "expired": "已失效", "stopped": "已停止"}


def _health(name: str, status: str, error: str | None = None) -> tuple[str, str | None]:
    """（「🟢 监听」这样的一项, 有问题时的说明）。"""
    if status in _OK:
        return f"🟢 {name}", None
    if status == "not_configured":
        return f"⚫ {name}", f"{name}：未配置"
    if status == "unverified":
        return f"⚪ {name}", None
    detail = _PROBLEM.get(status, status)
    return f"🔴 {name}", f"{name}：{detail}" + (f"（{error}）" if error else "")


def status_view(service) -> tuple[str, list]:
    """运行概况：各部分是否在线、过去 24 小时、待处理与累计数字、正在生效的告警。"""
    data = service.dashboard()
    connections, runtime = data["connections"], data["runtime"]
    worker = runtime.get("worker", {})
    worker_status = "stale" if worker.get("stale", True) else worker.get("status", "unknown")
    parts = [_health("监听", connections["telegram"]["status"], connections["telegram"].get("error")),
             _health("Worker", worker_status, worker.get("error")),
             _health("115", connections["p115"]["status"], connections["p115"].get("error")),
             _health("AI", connections["llm"]["status"]), _health("TMDB", connections["tmdb"]["status"])]
    health = " · ".join(item for item, _ in parts)
    problems = [problem for _, problem in parts if problem]
    if problems:
        health += "\n" + "\n".join(problems)
    counts, recent = data["counts"], data.get("recent") or {}
    backlog = [("待审批", counts.get(TaskState.PENDING_APPROVAL.value, 0)),
               ("待人工", counts.get(TaskState.NEED_MANUAL.value, 0)), ("失败", data["failed"]),
               ("进行中", data["active"])]
    totals = [("已入库", data["done"]), ("已忽略", counts.get(TaskState.SKIPPED.value, 0)),
              ("已过滤", counts.get(TaskState.REJECTED.value, 0))]
    alerts = "\n".join(f"• {messages.esc(alert['text'])}" for alert in data.get("alerts") or [])
    head = "📊 <b>运行概况</b>" + (" · 模拟模式" if data.get("mode") == "simulation" else "")
    text = _view(head, health,
                 f"📥 <b>过去 24 小时</b>\n新收 {recent.get('received_24h', 0)} 条 · 入库 {recent.get('done_24h', 0)} 条",
                 "📌 <b>待处理</b>\n" + " · ".join(f"{label} {count}" for label, count in backlog),
                 "📚 <b>累计</b>\n" + " · ".join(f"{label} {count}" for label, count in totals),
                 f"⚠️ <b>告警</b>\n{alerts}" if alerts else "")
    return text, [*_nav(service, "status"), [("🔄 刷新", "st")]]


def help_view(service) -> tuple[str, list]:
    ai = service.connection_status()["llm"]["configured"]
    commands = "\n".join(f"/{name}　{text}" for name, text in COMMAND_MENU if name != "help")
    examples = "\n".join(["待审批里有什么？", "我有哪些 4K 日本动画？", "《X》缺哪几集？", "把《X》这几集都批了，存到动漫",
                          "我在追《X》第二季"])
    text = _view(
        "🤖 <b>Reelay 控制 Bot</b>",
        "资源到了推送审批卡片，点按钮就能转存、忽略、换目录或追剧。",
        f"📋 <b>命令</b>\n{commands}",
        "✏️ <b>识别错了</b>\n点卡片上的「✏️ 修正」，再发一句要改的内容，如 <code>片名 繁花 第 2 季</code>。",
        "💬 <b>直接说话</b>" + ("" if ai else "（要先在管理台配置 AI）")
        + f"\n<blockquote>{messages.esc(examples)}</blockquote>\n写操作会先列出来，点「执行」后才执行。",
    )
    return text, [[("📋 待处理", "pl:0"), ("🔔 追剧订阅", "sl"), ("📊 运行概况", "st")]]


# 命令菜单（control/bot.py 启动时登记，输入 / 时列出）与 /help 共用
COMMAND_MENU = [("pending", "待审批与待人工"), ("failed", "失败任务"), ("subs", "追剧订阅"),
                ("ignored", "不再接收的作品"), ("status", "运行概况"), ("help", "能做什么")]
