"""缺集追更 —— on_saved 记录剧集进度，on_daily 巡检缺集并提醒。

已入库的集以内核的内容级去重记录为准（覆盖插件启用前的历史）；本扩展只额外记下
tmdbid 与最近更新时间。每日巡检：

- 自动跟踪（track_all）：最近 lookback_days 天有新集入库的剧，只报告「首个已入库集之后」
  的断档，以及 TMDB 已播出但尚未入库的新集——中途开始追的剧不会天天提示缺前面的集；
- 显式订阅（subscriptions）：从第 1 集开始检查，可用 total 指定总集数。

配置示例::

    episode_tracker:
      track_all: true
      lookback_days: 30
      subscriptions:
        - {title: 一瓯春, season: 1, total: 36}
"""
from __future__ import annotations

import asyncio
import logging
import time

from ..core import runtime, tmdb
from ..core.contracts import PipelineContext
from ..core.registry import register

log = logging.getLogger("reelay.plugin.episode_tracker")


def format_episodes(episodes: list[int], limit: int = 12) -> str:
    """[1,2,3,5] → E01-E03、E05。"""
    ranges, start, prev = [], None, None
    for ep in sorted(episodes):
        if start is None:
            start = prev = ep
        elif ep == prev + 1:
            prev = ep
        else:
            ranges.append((start, prev))
            start = prev = ep
    if start is not None:
        ranges.append((start, prev))
    parts = [f"E{a:02d}" if a == b else f"E{a:02d}-E{b:02d}" for a, b in ranges]
    return "、".join(parts[:limit]) + (" 等" if len(parts) > limit else "")


def missing_episodes(have: set[int], *, total: int | None, aired: int | None, from_first: bool) -> list[int]:
    upper = max([*have, total or 0, aired or 0], default=0)
    if not upper:
        return []
    start = min(have) if (from_first and have) else 1
    return sorted(set(range(start, upper + 1)) - have)


class _Base:
    name = "episode_tracker"
    ui_label = "缺集追更"
    ui_description = "记录剧集入库进度，每日巡检断档与 TMDB 已播未入库的新集并提醒；订阅清单在配置文件中维护。"
    ui_fields = [
        {'path': 'episode_tracker.track_all', 'label': '自动跟踪所有在追剧集', 'type': 'boolean'},
        {'path': 'episode_tracker.lookback_days', 'label': '自动跟踪：最近 N 天有更新', 'type': 'number',
         'min': 1, 'max': 365},
    ]

    def validate_config(self, cfg: dict) -> None:
        section = cfg.get("episode_tracker", {})
        if not isinstance(section, dict):
            raise ValueError("episode_tracker 必须是对象")
        subscriptions = section.get("subscriptions", [])
        if not isinstance(subscriptions, list):
            raise ValueError("episode_tracker.subscriptions 必须是数组")
        for index, item in enumerate(subscriptions):
            label = f"episode_tracker.subscriptions[{index}]"
            if not isinstance(item, dict) or type(item.get("title")) is not str or not item["title"].strip():
                raise ValueError(f"{label} 必须包含 title")
            if set(item) - {"title", "season", "total"}:
                raise ValueError(f"{label} 只支持 title/season/total")
            for key, low in (("season", 0), ("total", 1)):
                if key in item and (type(item[key]) is not int or not low <= item[key] <= 9999):
                    raise ValueError(f"{label}.{key} 必须是不小于 {low} 的整数")


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
        config = runtime.get("episode_tracker", {}) or {}
        records = {key: value for key, value in state.items().items() if key.startswith("series:")}
        stored = services.store.stored_series()
        targets: dict[tuple[str, int], dict] = {}
        if config.get("track_all", True):
            since = time.time() - float(config.get("lookback_days", 30)) * 86400
            for record in records.values():
                if record.get("updated_at", 0) >= since:
                    targets[(record["title"], record["season"])] = {"tmdbid": record.get("tmdbid"), "from_first": True}
        for item in config.get("subscriptions", []):
            season = item.get("season", 1)
            record = records.get(f"series:{item['title']}|S{season:02d}", {})
            targets[(item["title"], season)] = {"tmdbid": record.get("tmdbid"), "total": item.get("total"),
                                                "from_first": False}
        today = time.strftime("%Y-%m-%d")
        report, lines = {}, []
        for (title, season), info in sorted(targets.items()):
            have = stored.get((title, season), set())
            aired = await self._aired(info.get("tmdbid"), season, today)
            missing = missing_episodes(have, total=info.get("total"), aired=aired, from_first=info["from_first"])
            if not missing:
                continue
            report[f"{title}|S{season:02d}"] = {"have": sorted(have), "missing": missing, "aired": aired}
            suffix = f"（TMDB 已播 {aired} 集）" if aired else ""
            lines.append(f"• {title} S{season:02d}：已入库 {len(have)} 集，缺 {format_episodes(missing)}{suffix}")
        state.set("last_report", {"date": today, "series": report})
        if lines:
            services.notify("📺 追更检查：\n" + "\n".join(lines[:30]), key="episode_tracker")

    @staticmethod
    async def _aired(tmdbid, season: int, today: str) -> int | None:
        if not tmdbid or not tmdb.api_key():
            return None
        try:
            episodes = await asyncio.to_thread(
                tmdb.season_episodes, int(tmdbid), season,
                language=runtime.get("tmdb.language", "zh-CN"), timeout=runtime.get("tmdb.timeout", 10))
        except Exception as exc:  # noqa: BLE001 - TMDB 不可用时只按已入库断档检查
            log.warning("TMDB 季信息获取失败：%s", exc)
            return None
        aired = [e["episode"] for e in episodes if e.get("air_date") and e["air_date"] <= today]
        return max(aired, default=0) or None
