"""分享读取 —— after_parse Hook。

识别出的 115 分享都先只读查询分享状态：过期、取消或违规的分享在待审批标为「分享无法访问」，审批建议
改为忽略（这类分享仍能列出文件，转存时才会被 115 拒绝）。消息没写集数的剧集再只读列出分享，按文件名
得到实际的季、集与总大小，并提前标出多季合集、文件名无法识别等转存时会转人工的情况。结果只记在
``meta["share"]``，不改识别结果：转存时仍以那时的分享为准（追更的分享会原地增加新集）。

为避免触发 115 风控：同一时间只读一个分享，两次读取至少间隔 ``PROBE_INTERVAL`` 秒，单个分享最多
列出 ``DESCRIBE_MAX_DIRS`` 个目录。读取出错不影响识别；启动时为仍待审批、还没读过、上次出错或
超过 ``CHECK_TTL`` 没有查过状态的条目补读（已确认不可用的不再查）。
"""
from __future__ import annotations

import asyncio
import logging
import os
import time

from ...core.auth115 import get_store
from ...core.contracts import PipelineContext, TaskState
from ...core.media import is_video, pairs_label, resolution_label
from ...core.registry import register
from ..episode_tracker import format_episodes

log = logging.getLogger("reelay.plugin.share_probe")

PROBE_INTERVAL = 2.0
# 选集规则变化时加一，启动时重读按旧规则得出的结果（2：跨多季的合集按季转存，不再转人工；
# 3：所有分享都查询是否过期）
PROBE_VERSION = 3
# 分享状态会随时间变化（过期）：待审批条目超过这么久没查过就在启动时重查
CHECK_TTL = 12 * 3600


def size_text(size: int) -> str:
    gb = size / 1024 ** 3
    return f"{gb:.1f} GB" if gb >= 1 else f"{size / 1024 ** 2:.0f} MB"


@register(kind="hook", stage="after_parse", order=60)
class ShareProbe:
    name = "share_probe"
    ui_label = "分享读取"
    ui_description = "识别后只读查询 115 分享是否过期或失效；消息没写集数的剧集再列出分享，补出实际的季、集与大小。"
    stage = "after_parse"
    order = 60

    def __init__(self) -> None:
        self._dry_run = os.getenv("REELAY_DRY_RUN", "").lower() in {"1", "true", "yes"}
        self._lock = asyncio.Lock()
        self._last = 0.0

    @staticmethod
    def _checkable(ctx: PipelineContext) -> bool:
        p = ctx.parsed
        return bool(p and p.is_media and not p.is_ad and p.share_code
                    and ctx.source_rule.get("storage", "p115") == "p115")

    @classmethod
    def _wanted(cls, ctx: PipelineContext) -> bool:
        """还要列出分享读取季集：消息没写集数的剧集。"""
        p = ctx.parsed
        return cls._checkable(ctx) and not p.episodes and p.media_type in ("tv", "anime")

    def needs_refresh(self, ctx: PipelineContext) -> bool:
        """启动时补读：仍待审批、还没读过、上次出错、按旧规则读取或状态查询已过期的条目。"""
        share = ctx.meta.get("share", {})
        stale = (share.get("status") in (None, "error") or share.get("version") != PROBE_VERSION
                 or (share.get("status") != "unavailable" and time.time() - share.get("probed_at", 0) > CHECK_TTL))
        return (not self._dry_run and ctx.state == TaskState.PENDING_APPROVAL and self._checkable(ctx)
                and stale and get_store().has_cookie)

    async def run(self, ctx: PipelineContext) -> bool:
        if self._dry_run or not self._checkable(ctx) or not get_store().has_cookie:
            return True
        async with self._lock:  # 同一时间只读一个分享，并与上一次读取拉开间隔
            wait = self._last + PROBE_INTERVAL - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                result = await asyncio.to_thread(self._probe, ctx.parsed, self._wanted(ctx))
            except Exception as exc:  # noqa: BLE001 - 读取失败只影响展示
                log.warning("读取分享失败：%s", exc)
                result = {"status": "error", "note": type(exc).__name__}
            finally:
                self._last = time.monotonic()
        if result.get("size"):
            result["size_text"] = size_text(result["size"])
        result.update(probed_at=time.time(), version=PROBE_VERSION)
        ns = ctx.ns("share")
        ns.clear()
        ns.update(result)
        ctx.log(self._summary(result))
        return True

    @staticmethod
    def _probe(parsed, describe: bool) -> dict:
        from p115client import P115ShareFileSystem
        from ...providers.storage import describe_share, share_check
        client = get_store().cookie_client()
        problem, names = share_check(client, parsed.share_code, parsed.receive_code)
        if problem:
            return {"status": "unavailable", "note": f"分享无法访问：{problem}"}
        # 消息没写分辨率时，卡片按分享根目录里视频的文件名补（同一次请求取到，不多调 115）
        resolution = next(filter(None, (resolution_label(n) for n in names if is_video(n))), None)
        extra = {"resolution": resolution} if resolution else {}
        if not describe:
            return {"status": "available", **extra}
        return {**describe_share(P115ShareFileSystem(client, parsed.share_code, parsed.receive_code), parsed), **extra}

    @staticmethod
    def _summary(result: dict) -> str:
        if result["status"] == "ok":
            episodes = (pairs_label(result["pairs"]) if result["season"] is None
                        else f"S{result['season']:02d} {format_episodes(result['episodes'])}")
            return f"读取分享：{result['videos']} 个视频 {result.get('size_text', '')}，{episodes}"
        if result["status"] == "error":
            return f"读取分享失败（{result['note']}），稍后重试"
        if result["status"] == "available":
            return "分享有效"
        return f"读取分享：{result['note']}"
