"""分享集数读取 —— after_parse Hook。

消息没写集数的剧集，识别后只读列出 115 分享，按文件名得到实际的季、集与总大小，供待审批展示；
分享失效、多季合集、文件名无法识别等转存时会转人工的情况提前标出。结果只记在 ``meta["share"]``，
不改识别结果：转存时仍以那时的分享清单为准（追更的分享会原地增加新集）。

为避免触发 115 风控：同一时间只读一个分享，两次读取至少间隔 ``PROBE_INTERVAL`` 秒，单个分享最多
列出 ``DESCRIBE_MAX_DIRS`` 个目录。读取出错不影响识别，启动时会为仍待审批的条目补读。
"""
from __future__ import annotations

import asyncio
import logging
import os
import time

from ..core.auth115 import get_store
from ..core.contracts import PipelineContext, TaskState
from ..core.registry import register
from .episode_tracker import format_episodes

log = logging.getLogger("reelay.plugin.share_probe")

PROBE_INTERVAL = 2.0


def size_text(size: int) -> str:
    gb = size / 1024 ** 3
    return f"{gb:.1f} GB" if gb >= 1 else f"{size / 1024 ** 2:.0f} MB"


@register(kind="hook", stage="after_parse", order=60)
class ShareProbe:
    name = "share_probe"
    ui_label = "分享集数读取"
    ui_description = "消息没写集数的剧集，识别后只读列出 115 分享，补出实际的季、集与大小，并提前标出失效链接与多季合集。"
    stage = "after_parse"
    order = 60

    def __init__(self) -> None:
        self._dry_run = os.getenv("REELAY_DRY_RUN", "").lower() in {"1", "true", "yes"}
        self._lock = asyncio.Lock()
        self._last = 0.0

    @staticmethod
    def _wanted(ctx: PipelineContext) -> bool:
        p = ctx.parsed
        return bool(p and p.is_media and not p.is_ad and p.share_code and not p.episodes
                    and p.media_type in ("tv", "anime")
                    and ctx.source_rule.get("storage", "p115") == "p115")

    def needs_refresh(self, ctx: PipelineContext) -> bool:
        """启动时补读：仍待审批、还没读过或上次读取出错的条目。"""
        return (not self._dry_run and ctx.state == TaskState.PENDING_APPROVAL and self._wanted(ctx)
                and ctx.meta.get("share", {}).get("status") in (None, "error") and get_store().has_cookie)

    async def run(self, ctx: PipelineContext) -> bool:
        if self._dry_run or not self._wanted(ctx) or not get_store().has_cookie:
            return True
        async with self._lock:  # 同一时间只读一个分享，并与上一次读取拉开间隔
            wait = self._last + PROBE_INTERVAL - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                result = await asyncio.to_thread(self._describe, ctx.parsed)
            except Exception as exc:  # noqa: BLE001 - 读取失败只影响展示
                log.warning("读取分享失败：%s", exc)
                result = {"status": "error", "note": type(exc).__name__}
            finally:
                self._last = time.monotonic()
        if result.get("size"):
            result["size_text"] = size_text(result["size"])
        result["probed_at"] = time.time()
        ns = ctx.ns("share")
        ns.clear()
        ns.update(result)
        ctx.log(self._summary(result))
        return True

    @staticmethod
    def _describe(parsed) -> dict:
        from p115client import P115ShareFileSystem
        from ..providers.storage import describe_share
        share_fs = P115ShareFileSystem(get_store().cookie_client(), parsed.share_code, parsed.receive_code)
        return describe_share(share_fs, parsed)

    @staticmethod
    def _summary(result: dict) -> str:
        if result["status"] == "ok":
            return (f"读取分享：{result['videos']} 个视频 {result.get('size_text', '')}，"
                    f"S{result['season']:02d} {format_episodes(result['episodes'])}")
        if result["status"] == "error":
            return f"读取分享失败（{result['note']}），稍后重试"
        return f"读取分享：{result['note']}"
