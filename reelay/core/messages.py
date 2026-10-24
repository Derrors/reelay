"""发给用户的 Telegram 消息（HTML）：待审批卡片、审批后的结果、入库通知与需要处理的提醒。

通知渠道（``providers/notify.py``）与控制 Bot 的按钮回调共用这里的模板：点「转存」「忽略」后卡片原样保留，
只在末尾追加处理结果，不会把整张卡片换成一行字。正文里来自消息与识别结果的内容一律转义。

有 TMDB 图片时，待审批与入库通知以图片发送，正文作为图片说明（Telegram 限 1024 字，简介按此截短）。
正文按「图标 字段：值」逐行排列，简介放在可展开的引用块里；按钮两个一行（见文末 *_buttons）。
"""
from __future__ import annotations

import hashlib
import html
import re
from typing import Any

from . import release
from .contracts import PipelineContext
from .rules import COUNTRIES, GENRES
from .media import format_episodes, pairs_label, planned_pairs, resolution_label

# 识别不可靠的原因 → 提示（见 core/confidence.py）
_CAPS = {
    "tmdb_unmatched": "TMDB 没有可信匹配，片名可能不准",
    "parse_fallback": "AI 识别降级，片名与集数请核对",
    "no_title": "没有识别出片名",
}


def esc(value: Any) -> str:
    return html.escape(str(value), quote=False)


def plain(text: str) -> str:
    """去掉 HTML 标签（写日志用）。"""
    return html.unescape(re.sub(r"<[^>]+>", "", text))


POSTER_URL = "https://image.tmdb.org/t/p/w500{}"
BACKDROP_URL = "https://image.tmdb.org/t/p/w780{}"
OVERVIEW_LIMIT = 160


def card_image_url(ctx: PipelineContext) -> str | None:
    """通知卡片的配图：优先 TMDB 横版剧照，没有时用海报；没有匹配到 TMDB 时为 None。

    Telegram 按图片比例定气泡宽度：竖版海报的卡片窄，长图注又会把它撑宽，宽度忽大忽小；
    横图总是铺满最大宽度，所有卡片一样宽。
    """
    fields = ctx.parsed.raw_fields if ctx.parsed else {}
    for key, url in (("backdrop_path", BACKDROP_URL), ("poster_path", POSTER_URL)):
        path = fields.get(key)
        if isinstance(path, str) and path.startswith("/"):
            return url.format(path)
    return None


def _title(ctx: PipelineContext) -> str:
    p = ctx.parsed
    title = (p.title if p else None) or "（未识别片名）"
    return f"<b>{esc(title)}</b>" + (f" ({p.year})" if p and p.year else "")


def _headline(ctx: PipelineContext) -> list[str]:
    """片名（年份）· 季集，下一行是原名。"""
    p = ctx.parsed
    episodes = _episodes(ctx)
    lines = [f"{_icon(ctx)} {_title(ctx)}" + (f" · {episodes}" if episodes else "")]
    original = (p.raw_fields if p else {}).get("original_title")
    if original and p and original.casefold() != (p.title or "").casefold():
        lines.append(f"<i>{esc(original)}</i>")
    return lines


def _field(icon: str, label: str, value: str | None) -> list[str]:
    """「⭐ 评分：7.8」这样的一行；value 已转义，为空时不显示。"""
    return [f"{icon} {label}：{value}"] if value else []


def _resolution(ctx: PipelineContext) -> str | None:
    """识别结果的分辨率；消息里没写时按分享里（或已转存）视频的文件名补。"""
    p = ctx.parsed
    if p and p.resolution:
        return resolution_label(p.resolution) or p.resolution
    names = [*((ctx.meta.get("organize") or {}).get("plan") or []), *((ctx.meta.get("storage") or {}).get("landed") or [])]
    found = (ctx.meta.get("share") or {}).get("resolution") or next(
        filter(None, (resolution_label(item.get("src_name") or item.get("name")) for item in names)), None)
    return found or resolution_label(ctx.raw.text)


def _facts(ctx: PipelineContext) -> list[str]:
    """评分、类型、地区、语言、主演、分辨率、音轨、字幕、片源、大小，各占一行（季集在片名后）。"""
    p = ctx.parsed
    fields = p.raw_fields if p else {}
    rating = fields.get("rating")
    genres = [re.sub(r"（.*?）", "", GENRES[g]) for g in (p.genre_ids if p else []) if g in GENRES][:3]
    countries = [COUNTRIES.get(c, c) for c in dict.fromkeys(p.origin_country if p else [])][:3]
    info = (p.release if p else None) or {}
    label = release.LABELS.get
    resolution = _resolution(ctx)
    video = [*([resolution] if resolution else []), *(label(x, x) for x in info.get("hdr") or [])]
    source = [label(x, x) for x in (info.get("source"), info.get("codec")) if x]
    subtitles = [label(x, x) for x in info.get("subtitles") or []] + (
        [label(info["subtitle_format"], info["subtitle_format"])] if info.get("subtitle_format") else [])
    join = lambda items, sep=" · ": sep.join(esc(x) for x in items) or None  # noqa: E731
    strings = lambda key: [x for x in fields.get(key) or [] if isinstance(x, str)]  # noqa: E731
    return [
        *_field("🌟", "评分", f"{float(rating):.1f}" if type(rating) in (int, float) else None),
        *_field("🎭", "类型", join(genres)),
        *_field("🌍", "地区", join(countries)),
        *_field("🗣", "语言", join(strings("languages"))),
        *_field("👥", "主演", join(strings("cast")[:5], "、")),
        *_field("🖥", "分辨率", join(video) or ("未标注" if p and p.is_media else None)),
        *_field("🔊", "音轨", join([label(x, x) for x in info.get("audio") or []])),
        *_field("💬", "字幕", join(subtitles)),
        *_field("💿", "片源", join(source)),
        *_field("📦", "大小", _size(ctx)),
    ]


def _overview(ctx: PipelineContext) -> list[str]:
    text = " ".join(str((ctx.parsed.raw_fields if ctx.parsed else {}).get("overview") or "").split())
    if not text:
        return []
    if len(text) > OVERVIEW_LIMIT:
        text = text[:OVERVIEW_LIMIT].rstrip("，。、,. ") + "…"
    return ["", "📖 简介：", f"<blockquote expandable>{esc(text)}</blockquote>"]


def type_icon(media_type: str | None, episodic: bool = False) -> str:
    """类型图标：电影 🎬、动漫 🍥、其余（剧集、未识别类型）📺；带集数的「电影」按剧集算。"""
    if media_type == "anime":
        return "🍥"
    return "🎬" if media_type == "movie" and not episodic else "📺"


def _icon(ctx: PipelineContext) -> str:
    p = ctx.parsed
    return type_icon(p.media_type if p else None, bool(p and p.episodes))


def _episodes(ctx: PipelineContext) -> str | None:
    """「S01 E17」「S01 E01-E23」「S01–S03 共 30 集」；只知道季时为「S02」，电影或没有集数时返回 None。"""
    pairs = sorted(set(planned_pairs(ctx)))
    if not pairs:
        p = ctx.parsed
        return f"S{p.season:02d}" if p and p.season is not None and p.media_type != "movie" else None
    seasons = {season for season, _ in pairs}
    if len(seasons) > 1:
        return pairs_label(pairs)
    return f"S{seasons.pop():02d} {format_episodes([ep for _, ep in pairs], limit=4)}"


def _size(ctx: PipelineContext) -> str | None:
    p = ctx.parsed
    share = ctx.meta.get("share") or {}
    return esc(p.size) if p and p.size else (esc(share["size_text"]) if share.get("size_text") else None)


def approval_target(ctx: PipelineContext) -> str:
    """不手动改目录时将存入的位置（含二级分类，主进程读到网盘路径后是真实路径）。"""
    return (ctx.meta.get("library", {}).get("preview") or ctx.save_dir_alias
            or ctx.source_rule.get("save_to", "默认"))


def approval_card(ctx: PipelineContext, target: str | None = None) -> str:
    """待审批卡片。target 是将存入的位置（如「/测试/剧集/国产剧集」），省略时按任务推算。"""
    target = target or approval_target(ctx)
    meta = ctx.meta
    lines = [*_headline(ctx), "", *_facts(ctx), *_overview(ctx), ""]
    lines += [*_field("📂", "存到", f"<code>{esc(target)}</code>"), *_field("📡", "来源", esc(ctx.raw.source))]
    warnings = [_CAPS[cap] for cap in (meta.get("confidence") or {}).get("caps", []) if cap in _CAPS]
    share = meta.get("share") or {}
    if share.get("status") in ("unavailable", "issue") and share.get("note"):
        warnings.append(share["note"])
    if warnings:
        lines += ["", *(f"⚠️ {esc(w)}" for w in warnings)]
    reminders = (meta.get("task") or {}).get("approval_reminders", 0)
    if reminders:
        lines += ["", f"⏰ 第 {reminders} 次提醒：这条资源还在等你确认"]
    return "\n".join(lines)


def with_result(card: str, result: str) -> str:
    """点按钮后的卡片：原内容不变，末尾加一行处理结果（result 已是 HTML）。"""
    return f"{card}\n\n{result}"


def approval_result(card: str, outcome: str, state: str | None = None, target: str | None = None) -> str:
    """审批后的卡片。outcome：approved / rejected / stale；target 是手动选的保存目录。"""
    result = {
        "approved": f"✅ <b>已批准</b>{f'，存到「{esc(target)}」' if target else ''}，正在转存，完成后另行通知",
        "rejected": "🚫 <b>已忽略</b>",
        "stale": f"ℹ️ 这条已经处理过了{f'（当前：{esc(state)}）' if state else ''}",
    }[outcome]
    return with_result(card, result)


def _saved_location(ctx: PipelineContext, root: str) -> tuple[str, list[str]]:
    """整理后的目录（多个目录时取共同的上级）与文件名。root 是保存目录在网盘里的路径。"""
    plan = (ctx.meta.get("organize") or {}).get("plan") or []
    dirs = [item["target_dir"].strip("/") for item in plan if item.get("target_dir")]
    names = [item["target_name"] for item in plan if item.get("target_name")]
    if not names:
        names = [str(x) for x in (ctx.result or {}).get("saved_files") or []]
    common = dirs[0].split("/") if dirs else []
    for d in dirs[1:]:
        parts = d.split("/")
        size = 0
        while size < min(len(common), len(parts)) and common[size] == parts[size]:
            size += 1
        common = common[:size]
    return "/".join(filter(None, [root.rstrip("/"), *common])) or root, names


def saved_message(ctx: PipelineContext, root: str) -> str:
    """入库通知。root 是保存目录在网盘里的路径（未知时为目录别名）。"""
    folder, names = _saved_location(ctx, root)
    lines = ["✅ <b>已入库</b>", "", *_headline(ctx), "", *_facts(ctx), ""]
    lines += _field("📂", "位置", f"<code>{esc(folder)}</code>")
    if len(names) == 1:
        lines += _field("📄", "文件", esc(names[0]))
    elif names:
        shown = "、".join(esc(n) for n in names[:2])
        lines += _field("📄", "文件", f"{len(names)} 个，{shown}{' 等' if len(names) > 2 else ''}")
    return "\n".join(lines)


def problem_message(ctx: PipelineContext, reason: str, *, details: list[str] | None = None,
                    hint: str | None = None) -> str:
    """转存或整理停下来、需要用户处理的提醒，排版与审批卡片一致：片名、字段、存到与来源，
    再写原因（details 是逐行列出的文件名等，放在引用块里；hint 是怎么处理）。处理办法见按钮（problem_buttons）。"""
    lines = [*_headline(ctx), "", *_facts(ctx), ""]
    lines += [*_field("📂", "存到", f"<code>{esc(approval_target(ctx))}</code>"), *_field("📡", "来源", esc(ctx.raw.source))]
    lines += ["", f"⚠️ <b>需要处理</b>：{esc(reason)}"]
    if details:
        lines.append(f"<blockquote>{esc(chr(10).join(details))}</blockquote>")
    if hint:
        lines.append(f"👉 {esc(hint)}")
    return "\n".join([*lines, *_field("🆔", "任务", f"<code>{esc(ctx.task_id)}</code>")])


def problem_reason(ctx: PipelineContext, event: str | None = None) -> tuple[str, list[str], str]:
    """需要处理的提醒：（一句话原因, 逐行列出的细节, 怎么处理）。

    event 是通知事件（transfer_uncertain / organize_failed / failed）；从 /pending 等处重新调出卡片时省略，
    按任务当前的检查点推断。
    """
    if event is None:
        storage = ctx.meta.get("storage") or {}
        event = ("transfer_uncertain" if storage.get("phase") == "receiving" and storage.get("manifest")
                 else "organize_failed" if storage.get("landed") else "failed")
    storage, organize = ctx.ns("storage"), ctx.ns("organize")
    if event == "transfer_uncertain":
        partial = storage.get("partial")
        if partial:
            missing = partial["missing"]
            return (f"转存只到了 {partial['landed']}/{partial['expected']} 个文件，多次复查都没有变化",
                    [f"缺 {len(missing)} 个文件：", *missing[:5], *([f"…等 {len(missing)} 个"] if len(missing) > 5 else [])],
                    "可以按已到的文件继续入库，或放弃并删除")
        if (storage.get("auto_recheck") or {}).get("gave_up"):
            return "后台 24 小时的自动复查都没有核验通过", [], "系统不会自动重发；到网盘确认后点「核验转存」，或放弃并删除"
        return "转存请求发出后没有确认结果", [], "系统不会自动重发；确认网盘情况后点「核验转存」"
    if event == "organize_failed":
        conflict = organize.get("pending_conflict")
        if conflict:
            return ("库里已有同一份资源",
                    [f"库里：{(conflict.get('existing') or {}).get('name')}",
                     f"本次：{(conflict.get('incoming') or {}).get('name')}"],
                    "选择删除本次转存的文件，或覆盖保存")
        error = organize.get("error")
        return "文件已经转存，整理没有完成", [error] if error else [], "识别结果有误时先点「修正」，再点「重新整理」"
    last = ctx.trail[-1].split(" ", 1)[-1] if ctx.trail else ""
    if correctable(ctx):  # 转存前停下的（如认不出集号、分享跨多季）多半要先改识别结果，直接重试结果不变
        return "任务执行失败", [last] if last else [], "识别结果有误时先点「修正」，再重试；不需要了就忽略"
    return "任务执行失败", [last] if last else [], "可以重试，不需要了就忽略"


def correctable(ctx: PipelineContext) -> bool:
    """能不能修正识别结果（与管理台一致）：排队、执行中与已完成的不行，转存结果还没核验的也不行。"""
    from .contracts import TaskState
    storage = ctx.meta.get("storage") or {}
    busy = ctx.state in (TaskState.RECEIVED, TaskState.PARSED, TaskState.QUEUED, TaskState.SAVING,
                         TaskState.STORED, TaskState.ORGANIZING, TaskState.DONE, TaskState.SKIPPED,
                         TaskState.REJECTED)
    return not busy and not (storage.get("manifest") and storage.get("phase") != "stored")


def task_card(ctx: PipelineContext, *, subscribed: bool = False) -> tuple[str, Buttons, str | None]:
    """重新调出一条任务的卡片（/pending、/failed 列表里点开、修正之后）：(正文, 按钮, 配图)。

    待审批的是审批卡片，待人工与失败的是需要处理的提醒，其余状态只有卡片加一行当前状态。
    """
    from .contracts import TaskState
    if ctx.state == TaskState.PENDING_APPROVAL:
        return approval_card(ctx), approval_buttons(ctx, subscribed=subscribed), card_image_url(ctx)
    if ctx.state in (TaskState.NEED_MANUAL, TaskState.FAILED):
        reason, details, hint = problem_reason(ctx)
        return problem_message(ctx, reason, details=details, hint=hint), problem_buttons(ctx), card_image_url(ctx)
    return (with_result(approval_card(ctx), f"ℹ️ 这条已经处理过了（当前：{esc(ctx.state.value)}）"),
            link_buttons(ctx), card_image_url(ctx))


CORRECTION_MARK = "✏️ 修正识别 · 任务 "


def correction_prompt(ctx: PipelineContext) -> str:
    """点「修正」后发出的提示（HTML）：回复这条消息写要改的项。任务 ID 写在第一行，回复时据此找到任务。"""
    p = ctx.parsed
    episodes = _episodes(ctx)
    kind = {"movie": "电影", "tv": "剧集", "anime": "动漫"}.get(p.media_type if p else None, "未识别")
    current = " · ".join(filter(None, [f"《{esc(p.title)}》" if p and p.title else "（未识别片名）",
                                      str(p.year) if p and p.year else None, kind, esc(episodes) if episodes else None]))
    return "\n".join([
        f"<b>{CORRECTION_MARK}</b><code>{esc(ctx.task_id)}</code>",
        f"现在：{current}",
        "",
        "回复这条消息，写要改的项，没写的不变，例如：",
        "<code>片名 繁花</code>",
        "<code>第 2 季 E01-E05</code>",
        "<code>繁花 2023 剧集 S01E03</code>",
        "可以写片名、年份、类型（电影 / 剧集 / 动漫）、季、集、分辨率；也可以点「重新识别」让系统再识别一遍。",
    ])


# --------------------------------------------------------------------------- #
# 按钮：每行一组 (文字, 数据)；数据以 https:// 开头时是链接按钮，否则是回调数据（不超过 64 字节，
# 只带任务 ID 与短参数）。回调由控制 Bot 处理（control/bot.py）。
# --------------------------------------------------------------------------- #
Buttons = list[list[tuple[str, str]]]

# --------------------------------------------------------------------------- #
# 控制 Bot 的按钮数据：「动作:参数:参数」，Telegram 限 64 字节；拼接与解析都用这里的函数
# --------------------------------------------------------------------------- #
CALLBACK_LIMIT = 64


def callback(action: str, *args: object) -> str:
    """按钮数据。参数里不能有冒号；超过 64 字节时抛 ValueError（长内容用 short_key）。"""
    parts = [action, *(str(arg) for arg in args)]
    data = ":".join(parts)
    if any(":" in part for part in parts) or len(data.encode("utf-8")) > CALLBACK_LIMIT:
        raise ValueError(f"按钮数据无效或超过 {CALLBACK_LIMIT} 字节：{data[:80]}")
    return data


def parse_callback(data: str) -> tuple[str, list[str]]:
    """按钮数据 →（动作, 参数列表）；参数缺省时为空列表。"""
    action, *args = (data or "").split(":")
    return action, args


def short_key(text: str) -> str:
    """按钮里指代一条较长内容（目录别名、订阅建议、一部剧一季）的 10 位短键，点的时候再按当前内容找回。"""
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:10]

# 审批汇总、补拉通知下方：打开待处理列表（control/manage.py 的 pending_list）
PENDING_BUTTONS: Buttons = [[("📋 查看待审批", callback("pl", 0))]]


def link_buttons(ctx: PipelineContext) -> Buttons:
    """匹配到 TMDB 时的「TMDB 详情」链接。"""
    p = ctx.parsed
    tmdbid = (p.raw_fields if p else {}).get("tmdbid")
    if not tmdbid:
        return []
    kind = (ctx.meta.get("tmdb") or {}).get("kind") or ("movie" if p.media_type == "movie" else "tv")
    return [[("🎬 TMDB 详情", f"https://www.themoviedb.org/{kind}/{int(tmdbid)}")]]


def approval_buttons(ctx: PipelineContext, *, subscribed: bool = False) -> Buttons:
    """转存、忽略、换目录、修正、追剧（可订阅且还没订阅的剧）、不再接收、TMDB 详情，两个一行。"""
    from .subscriptions import subscribable
    tid = ctx.task_id
    items = [("✅ 转存", callback("ok", tid)), ("🚫 忽略", callback("no", tid)), ("📂 换目录", callback("dir", tid)),
             ("✏️ 修正", callback("fx", tid))]
    if subscribable(ctx) and not subscribed:
        items.append(("🔔 追剧", callback("sub", tid)))
    items.append(("⛔ 不再接收", callback("blk", tid)))
    items += [button for row in link_buttons(ctx) for button in row]
    return [items[i:i + 2] for i in range(0, len(items), 2)]


def block_confirm_buttons(ctx: PipelineContext) -> Buttons:
    """「不再接收」会影响之后的消息：先点一次出确认，再点才执行。"""
    return [[("⚠️ 确认不再接收", callback("bly", ctx.task_id)), ("↩ 返回", callback("back", ctx.task_id))]]


def suggestion_message(item: dict) -> str:
    """订阅建议（见 ManagementService.subscription_suggestions）。"""
    return f"💡 <b>订阅建议</b>\n\n{esc(item['text'])}"


def suggestion_buttons(item: dict, *, in_list: bool = False) -> Buttons:
    """订阅建议的「订阅 / 不用了」；in_list 时是 /subs 列表里的按钮，处理后重新列出订阅。"""
    args = (short_key(item["key"]), *(["l"] if in_list else []))
    label = f"🔔 订阅 {item['title']} S{item['season']:02d}"[:40] if in_list else f"🔔 订阅第 {item['season']} 季"
    return [[(label, callback("ss", *args)), ("🙅 不用了", callback("sd", *args))]]


def correction_buttons(task_id: str) -> Buttons:
    """修正提示下方：让系统重新识别，或不改了。"""
    return [[("🔄 重新识别", callback("rp", task_id)), ("✖️ 不改了", callback("fxn", task_id))]]


def directory_buttons(task_id: str, aliases: list[str]) -> Buttons:
    """「换目录」展开后的保存目录（按别名的短键回调：别名可能很长，目录增删后序号会变），两个一行，最后是返回。"""
    items = [(f"📁 {alias}", callback("to", task_id, short_key(alias))) for alias in aliases]
    return [items[i:i + 2] for i in range(0, len(items), 2)] + [[("↩ 返回", callback("back", task_id))]]


def problem_buttons(ctx: PipelineContext) -> Buttons:
    """需要处理的任务能直接做的操作，按任务当前的检查点决定（与管理台一致）。"""
    from .contracts import TaskState
    tid, storage = ctx.task_id, ctx.meta.get("storage") or {}
    if storage.get("phase") == "receiving" and storage.get("manifest"):
        # 只到了一部分时可按已到的继续；也可以放弃（删除暂存目录）
        partial = storage.get("partial")
        items = [*([(f"✅ 按已到的 {partial['landed']} 个继续", callback("ap", tid))] if partial else []),
                 ("🔍 核验转存", callback("rc", tid)), ("🗑 放弃并删除", callback("cf", tid, "abandon"))]
    elif storage.get("landed"):
        items = ([("🗑 删除转存文件", callback("cf", tid, "skip")), ("♻️ 覆盖保存", callback("cf", tid, "replace"))]
                 if (ctx.meta.get("organize") or {}).get("pending_conflict") else [("📂 重新整理", callback("ro", tid))])
    elif ctx.state == TaskState.FAILED:
        items = [("🔁 重试", callback("rt", tid)), ("🙈 忽略", callback("no", tid))]
    else:
        items = [("🙈 忽略", callback("no", tid))] if ctx.state == TaskState.NEED_MANUAL else []
    if correctable(ctx):
        items.append(("✏️ 修正", callback("fx", tid)))
    items += [button for row in link_buttons(ctx) for button in row]
    return [items[i:i + 2] for i in range(0, len(items), 2)]  # 与审批卡片一样两个一行


CONFLICT_CHOICES = {
    "skip": ("删除转存文件", "保留库里的文件，本次转存的这份移进回收站"),
    "replace": ("覆盖保存", "库里原有的文件移进回收站，换成本次转存的这份"),
    "abandon": ("放弃并删除", "暂存目录连同已到的文件移进回收站，任务改为忽略"),
}


def confirm_buttons(task_id: str, choice: str) -> Buttons:
    """删除转存文件 / 覆盖保存 / 放弃并删除会删文件：先点一次出确认，再点才执行。"""
    return [[(f"⚠️ 确认{CONFLICT_CHOICES[choice][0]}", callback("cy", task_id, choice)),
             ("↩ 返回", callback("pb", task_id))]]
