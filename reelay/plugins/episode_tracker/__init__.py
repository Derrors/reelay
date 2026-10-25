"""缺集追更 —— on_saved 记录剧集进度，on_daily 巡检缺集并提醒。

已入库的集以内核的内容级去重记录为准（覆盖插件启用前的历史）；本扩展只额外记下
tmdbid 与最近更新时间。每日巡检：

- 自动跟踪（track_all）：最近 lookback_days 天有新集入库的剧，只报告「首个已入库集之后」
  的断档，以及 TMDB 已播出但尚未入库的新集——中途开始追的剧不会天天提示缺前面的集；
- 显式订阅：从第 1 集开始检查，可用 total 指定总集数（订阅在管理台「追剧订阅」、Telegram 与 MCP 里维护）。

最近一次巡检结果作为能力 ``episode_report`` 提供给内核（订阅列表、缺集问答）与日报；追更提醒上的按钮
（某部剧不用等了、不再提醒、撤销）由本扩展经 ``bot_action:<前缀>`` 处理。

配置示例::

    plugins:
      episode_tracker:
        enabled: true
        track_all: true
        lookback_days: 30
"""
from __future__ import annotations

import asyncio
import logging
import time

from ...core import runtime, tmdb, works
from ...core.contracts import PipelineContext
from ...core.media import format_episodes
from ...core.messages import callback, esc, short_key, type_icon
from ...core.registry import register

log = logging.getLogger("reelay.plugin.episode_tracker")


def missing_episodes(have: set[int], *, total: int | None, aired: int | None, from_first: bool) -> list[int]:
    upper = max([*have, total or 0, aired or 0], default=0)
    if not upper:
        return []
    start = min(have) if (from_first and have) else 1
    return sorted(set(range(start, upper + 1)) - have)


def _ago(days: int) -> str:
    if days < 60:
        return f"{days} 天"
    return f"{days // 30} 个月" if days < 365 else f"{days // 365} 年"


def _ago_range(low: int, high: int) -> str:
    """「20 天」「2~8 天」「20 天~20 年」。"""
    a, b = _ago(low), _ago(high)
    if a == b:
        return a
    (x, unit_a), (y, unit_b) = a.split(" "), b.split(" ")
    return f"{x}~{y} {unit_a}" if unit_a == unit_b else f"{a}~{b}"


# 在 Telegram 上点了「不用等了」的剧：扩展状态 muted:<片名>|Sxx → {"episodes": [...]}（这些集不再提醒）
# 或 {"all": true}（这部不再提醒）。只影响提醒，不影响订阅的自动转存。
MUTED = "muted:"
BUTTONS = 8
# 追更提醒上的按钮前缀：tm 某部剧的选项、tmi 现在缺的集不用等了、tmo 不再提醒这部、tmu 撤销、tmb 返回
BOT_ACTIONS = ("tm", "tmi", "tmo", "tmu", "tmb")


def find_series(state, digest: str) -> tuple[str, dict] | None:
    """按短键找回 (片名|Sxx, 那一季的缺集情况)：先找最近一次巡检结果；已经不再提醒的剧不在里面，
    从提醒设置里找（缺集情况为空），撤销时用。"""
    series = (state.get("last_report") or {}).get("series") or {}
    found = next(((key, info) for key, info in series.items() if short_key(key) == digest), None)
    if found:
        return found
    muted = (key[len(MUTED):] for key in state.items() if key.startswith(MUTED))
    return next(((key, {}) for key in muted if short_key(key) == digest), None)


def mute(state, key: str, episodes: list[int] | None = None) -> None:
    """episodes 为 None 时这部不再提醒，否则这些集不再提醒（之后新缺的集照常提醒）。"""
    current = state.get(MUTED + key) or {}
    if episodes is None:
        state.set(MUTED + key, {"all": True, "episodes": current.get("episodes", [])})
    else:
        state.set(MUTED + key, {**current, "episodes": sorted({*current.get("episodes", []), *episodes})})


def unmute(state, key: str) -> None:
    state.delete(MUTED + key)


def buttons(report: dict[str, dict]) -> list:
    """每部剧一个「🔕」按钮（顺序与 render 一致），点开可以忽略现在缺的集或不再提醒这部。"""
    ordered = sorted(report, key=lambda key: not report[key]["overdue"])
    rows = []
    for key in ordered[:BUTTONS]:
        title, _, season = key.rpartition("|")
        rows.append([(f"🔕 {title} {season}"[:40], callback("tm", short_key(key)))])
    return rows


def render(report: dict[str, dict], limit: int = 20) -> str:
    """追更提醒（Telegram HTML）：每部剧一段——类型图标、片名与季、缺哪些集、入库与播出进度、已播多日仍未入库的集
    （合并成一句，写播出天数的范围）。
    有逾期集的剧排在前面。"""
    overdue_count = sum(1 for info in report.values() if info["overdue"])
    head = f"🔔 <b>追更检查</b> · {len(report)} 部缺集" + (f"，{overdue_count} 部逾期" if overdue_count else "")
    blocks = [head]
    ordered = sorted(report.items(), key=lambda item: not item[1]["overdue"])
    for key, info in ordered[:limit]:
        title, _, season = key.rpartition("|")
        progress = f"已入库 {len(info['have'])} 集" + (f" / 已播 {info['aired']} 集" if info["aired"] else "")
        lines = [f"{type_icon(info.get('media_type'), True)} <b>{esc(title)}</b> · {season}",
                 f"缺 {format_episodes(info['missing'])}", progress]
        if info["overdue"]:
            days = [item["days"] for item in info["overdue"]]
            episodes = format_episodes([item["episode"] for item in info["overdue"]], limit=4)
            lines.append(f"⏰ {episodes} 已播 {_ago_range(min(days), max(days))}，仍未入库")
        blocks.append("\n".join(lines))
    if len(ordered) > limit:
        blocks.append(f"… 另有 {len(ordered) - limit} 部")
    return "\n\n".join(blocks)


class _Base:
    name = "episode_tracker"
    ui_label = "缺集追更"
    ui_description = "记录剧集入库进度，每日巡检断档与 TMDB 已播未入库的新集并提醒；订阅的剧在「追剧订阅」中维护。"
    provides = {"episode_report": "report", **{f"bot_action:{action}": "bot_action" for action in BOT_ACTIONS}}
    ui_fields = [
        {'path': 'track_all', 'label': '自动跟踪所有在追剧集', 'type': 'boolean', 'default': True},
        {'path': 'lookback_days', 'label': '自动跟踪：最近 N 天有更新', 'type': 'number',
         'min': 1, 'max': 365, 'default': 30},
    ]


    @staticmethod
    def report() -> dict:
        """最近一次巡检结果：{"date", "series": {片名|Sxx: {have, missing, aired, overdue, media_type}}}。"""
        return runtime.services().state(_Base.name).get("last_report") or {}

    @staticmethod
    def bot_action(action: str, digest: str, original: str = "") -> tuple[str | None, list | None, str | None]:
        """追更提醒上某部剧的按钮：选项只换按钮，执行后在提醒末尾加一行结果。返回 (正文, 按钮, 顶部提示)。"""
        from ...core.messages import with_result
        state = runtime.services().state(_Base.name)
        found = find_series(state, digest)
        if not found:
            return None, None, "这条提醒已经过期，等明天的巡检"
        key, info = found
        title, _, season = key.rpartition("|")
        if not info and action not in ("tmu", "tmb"):
            return None, None, "这部剧已经不再提醒"
        if action == "tmb":
            return None, buttons(state.get("last_report", {}).get("series") or {}), None
        if action == "tm":
            missing = format_episodes(info["missing"], limit=3)
            return None, [[(f"🙈 {missing} 不用等了"[:40], callback("tmi", digest))],
                          [(f"🔕 不再提醒 {title}"[:40], callback("tmo", digest)), ("↩ 返回", callback("tmb", digest))]], None
        if action == "tmu":
            unmute(state, key)
            text = with_result(original, f"↩ 《{esc(title)}》{season} 照常提醒") if original else None
            return text, buttons(state.get("last_report", {}).get("series") or {}), "已撤销"
        if action == "tmi":
            mute(state, key, info["missing"])
            done = f"🙈 《{title}》{season} 现在缺的 {format_episodes(info['missing'])} 不再提醒，之后新缺的集照常提醒"
        else:
            mute(state, key)
            done = f"🔕 《{title}》{season} 不再提醒缺集（订阅的自动转存不受影响）"
        undo = [[("↩ 撤销", callback("tmu", digest))]]
        return (with_result(original, esc(done)) if original else None), undo, done[:190]


@register(kind="event", event="on_saved")
class EpisodeRecorder(_Base):
    event = "on_saved"

    async def handle(self, ctx: PipelineContext) -> None:
        p = ctx.parsed
        if not p or not p.title:
            return
        state = runtime.services().state(self.name)
        for season in sorted({season for season, _ in ctx.episode_pairs()}):  # 跨多季的合集每季各记一条
            key = f"series:{p.title}|S{season:02d}"
            record = state.get(key, {})
            record.update(title=p.title, season=season, updated_at=time.time(),
                          tmdbid=p.raw_fields.get("tmdbid") or record.get("tmdbid"))
            state.set(key, record)


@register(kind="event", event="on_daily")
class EpisodeTracker(_Base):
    event = "on_daily"

    async def handle(self, ctx: PipelineContext) -> None:
        services = runtime.services()
        state = services.state(self.name)
        config = runtime.plugin_config(self.name)
        everything = state.items()
        records = {key: value for key, value in everything.items() if key.startswith("series:")}
        muted = {key[len(MUTED):]: value for key, value in everything.items() if key.startswith(MUTED)}
        stored = services.stored_series()
        targets: dict[tuple[str, int], dict] = {}
        if config.get("track_all", True):
            since = time.time() - float(config.get("lookback_days", 30)) * 86400
            for record in records.values():
                if record.get("updated_at", 0) >= since:
                    targets[(record["title"], record["season"])] = {"tmdbid": record.get("tmdbid"), "from_first": True}
        today = time.strftime("%Y-%m-%d")
        ended = []
        for item in services.subscriptions():
            if item["finished"]:
                continue  # 已完结：不再巡检
            have = works.episodes(stored, works.identity(item["title"], tmdbid=item["tmdbid"]), item["season"])
            episodes = await self._season_complete(item, have, today)
            if episodes:
                services.finish_subscription(item["id"], {"date": today, "episodes": episodes})
                ended.append(item | {"episodes": episodes})
                continue
            season = item["season"]
            record = records.get(f"series:{item['title']}|S{season:02d}", {})
            targets[(item["title"], season)] = {"tmdbid": item["tmdbid"] or record.get("tmdbid"),
                                                "total": item["total"], "from_first": False}
        if ended:
            lines = [f"🏁 《{e['title']}》第 {e['season']} 季已播完，{e['episodes']} 集全部入库，订阅已自动结束" for e in ended]
            services.notify("\n".join(lines) + "\n不再自动转存这一季；出了特别篇或补了集时可以继续订阅。",
                            key="subscription_finished",
                            buttons=[[(f"▶️ 继续订阅 {e['title']} S{e['season']:02d}"[:40],
                                       callback("so", e["id"]))] for e in ended[:BUTTONS]])
        media_types = services.title_media_types()
        report = {}
        for (title, season), info in sorted(targets.items()):
            silence = muted.get(f"{title}|S{season:02d}") or {}
            if silence.get("all"):
                continue
            have = works.episodes(stored, works.identity(title, tmdbid=info.get("tmdbid")), season)
            aired, air_dates = await self._aired(info.get("tmdbid"), season, today)
            missing = missing_episodes(have, total=info.get("total"), aired=aired, from_first=info["from_first"])
            missing = [ep for ep in missing if ep not in set(silence.get("episodes", []))]
            if not missing:
                continue
            overdue = overdue_episodes(missing, air_dates, today)
            report[f"{title}|S{season:02d}"] = {"have": sorted(have), "missing": missing, "aired": aired,
                                                "overdue": overdue, "media_type": media_types.get(title)}
        state.set("last_report", {"date": today, "series": report})
        if report:
            services.notify(render(report), key="episode_tracker", html=True, buttons=buttons(report))

    @staticmethod
    async def _season_complete(item: dict, have: set[int], today: str) -> int | None:
        """订阅的这一季是否已播完且全部入库；是则返回集数，否则 None。

        有 tmdbid 时要求 TMDB 上该季每一集都已播出、都已入库，并且剧已完结 / 被砍、已有下一季，或最后
        一集播出满 14 天（防止 TMDB 还没登记完后面的集就提前结束）。没有 tmdbid 时只按订阅写的总集数判断。
        """
        total = item.get("total") or 0
        if not item.get("tmdbid") or not tmdb.api_key():
            return total if total and set(range(1, total + 1)) <= have else None
        language, timeout = runtime.get("tmdb.language", "zh-CN"), runtime.get("tmdb.timeout", 10)
        try:
            episodes = await asyncio.to_thread(tmdb.season_episodes, int(item["tmdbid"]), item["season"],
                                               language=language, timeout=timeout)
            dates = [e.get("air_date") for e in episodes]
            if not episodes or not all(dates) or max(dates) > today:
                return None  # 还有没播的集，或 TMDB 没写播出日期
            count = max(total, max(e["episode"] for e in episodes))
            if not set(range(1, count + 1)) <= have:
                return None
            details = await asyncio.to_thread(tmdb.tv_details, int(item["tmdbid"]), language=language, timeout=timeout)
        except Exception as exc:  # noqa: BLE001 - TMDB 不可用时这次不判断，明天再看
            log.warning("TMDB 季信息获取失败：%s", exc)
            return None
        later = any(s["season"] > item["season"] and s.get("episodes") for s in details.get("seasons") or [])
        settled = time.strftime("%Y-%m-%d", time.localtime(time.time() - 14 * 86400)) >= max(dates)
        return count if details.get("status") in ("Ended", "Canceled") or later or settled else None

    @staticmethod
    async def _aired(tmdbid, season: int, today: str) -> tuple[int | None, dict[int, str]]:
        """返回 (TMDB 已播出的最大集号, 已播出各集的播出日期)。"""
        if not tmdbid or not tmdb.api_key():
            return None, {}
        try:
            episodes = await asyncio.to_thread(
                tmdb.season_episodes, int(tmdbid), season,
                language=runtime.get("tmdb.language", "zh-CN"), timeout=runtime.get("tmdb.timeout", 10))
        except Exception as exc:  # noqa: BLE001 - TMDB 不可用时只按已入库断档检查
            log.warning("TMDB 季信息获取失败：%s", exc)
            return None, {}
        aired = {e["episode"]: e["air_date"] for e in episodes if e.get("air_date") and e["air_date"] <= today}
        return max(aired, default=0) or None, aired


def overdue_episodes(missing: list[int], air_dates: dict[int, str], today: str, *, grace: int = 1) -> list[dict]:
    """已经播出超过 grace 天仍未入库的集（播出当天、次日的资源常常还没出现），最近播出的在前。"""
    import datetime
    now = datetime.date.fromisoformat(today)
    out = []
    for episode in missing:
        try:
            days = (now - datetime.date.fromisoformat(air_dates[episode])).days
        except (KeyError, ValueError):
            continue
        if days > grace:
            out.append({"episode": episode, "days": days})
    return sorted(out, key=lambda item: item["days"])
