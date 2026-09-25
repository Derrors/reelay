"""网盘执行器 Provider（StorageProvider）—— 确定性代码，负责真正转存 + 读回验证。

关键原则（评审）：AI 只负责理解判断，真正调 115、写目录、去重必须是确定性代码，
且转存后**读回验证**文件真的落地了，再置终态。

真实实现（装了 p115client 且配了 cookie）：

1. 用 ``P115ShareFileSystem(client, share_code, receive_code)`` 遍历分享内文件，
   只挑视频文件（过滤广告 txt / 图片等），拿到它们在分享里的 id。
2. ``share_fs.receive(ids, to_pid=目标cid)`` 把这些文件转存进我的网盘目录。
3. **读回验证**：转存前后各读一次目标目录，对比新增项，确认文件真的落地；
   把新落地的文件 (id, name) 写进 ``ctx.ns("storage")["landed"]``，供整理 Hook 使用。

未装依赖 / 未配 cookie：dry-run 占位，返回模拟成功回执。
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from ..core.contracts import PipelineContext
from ..core.registry import register


# 视频扩展名白名单（只转存这些，天然滤掉广告 txt / 图片 / nfo 等）
VIDEO_EXT = {
    ".mp4", ".mkv", ".ts", ".avi", ".mov", ".flv", ".wmv", ".webm",
    ".m2ts", ".mpg", ".mpeg", ".rmvb", ".iso",
}


def _is_video(name: str) -> bool:
    lower = (name or "").lower()
    return any(lower.endswith(ext) for ext in VIDEO_EXT)


@register(kind="provider", ptype="storage")
class P115Storage:
    name = "p115"

    def __init__(self) -> None:
        self._client = None
        cookies = os.getenv("REELAY_115_COOKIES")
        if cookies:
            try:
                from p115client import P115Client  # 延迟导入
                self._client = P115Client(cookies)
            except Exception:
                self._client = None

    # 是否已接入真实 115（供 pipeline/organizer 判断走真实还是占位）
    @property
    def live(self) -> bool:
        return self._client is not None

    async def save(self, ctx: PipelineContext) -> dict[str, Any]:
        p = ctx.parsed
        assert p is not None
        to_cid = int(ctx.save_dir_cid or 0)

        if self._client is None:
            ctx.log(f"[dry-run] 模拟转存 share={p.share_code} → cid={to_cid}")
            landed = [{"id": f"dry-{i}", "name": f"E{ep:02d}.mp4", "ep": ep}
                      for i, ep in enumerate(p.episodes or [1])]
            ctx.ns("storage")["landed"] = landed
            return {
                "dry_run": True,
                "share_code": p.share_code,
                "to_cid": to_cid,
                "saved_files": [x["name"] for x in landed],
                "verified": True,
            }

        # ---- 真实转存（同步 API 丢线程池，避免阻塞事件循环）----
        try:
            result = await asyncio.to_thread(self._save_sync, p, to_cid)
        except Exception as exc:  # 交给 pipeline 置 FAILED
            raise RuntimeError(f"115 转存失败：{exc}") from exc

        ctx.ns("storage")["landed"] = result["landed"]
        ctx.log(f"转存 {len(result['landed'])} 个文件 → cid={to_cid}，验证={result['verified']}")
        return result

    # ------------------------------------------------------------------ #
    # 同步实现（在 to_thread 中执行）
    # ------------------------------------------------------------------ #
    def _save_sync(self, parsed, to_cid: int) -> dict[str, Any]:
        from p115client import P115ShareFileSystem, P115FileSystem

        share_fs = P115ShareFileSystem(self._client, parsed.share_code, parsed.receive_code)
        my_fs = P115FileSystem(self._client)

        # 1) 列出分享内视频文件（递归一层：先根，遇目录再进）
        video_items = self._collect_share_videos(share_fs)
        if not video_items:
            raise RuntimeError("分享内未找到视频文件")
        file_ids = [it["id"] for it in video_items]

        # 2) 转存前快照（读回验证基线）
        before = {str(x["id"]) for x in my_fs.iterdir(to_cid)}

        # 3) 转存
        recv = share_fs.receive(file_ids, to_pid=to_cid)

        # 4) 转存后快照，对比新增项 = 真正落地的文件
        after = list(my_fs.iterdir(to_cid))
        landed = [
            {"id": x["id"], "name": x.get("name", ""), "is_dir": bool(x.get("is_directory") or x.get("is_dir"))}
            for x in after if str(x["id"]) not in before
        ]
        verified = len(landed) > 0

        return {
            "share_code": parsed.share_code,
            "to_cid": to_cid,
            "received": recv,
            "requested": len(file_ids),
            "landed": landed,
            "verified": verified,
        }

    @staticmethod
    def _collect_share_videos(share_fs, root: int = 0, max_depth: int = 3) -> list[dict]:
        """遍历分享，收集视频文件（含子目录，限制深度防止异常分享无限递归）。"""
        out: list[dict] = []

        def walk(cid: int, depth: int) -> None:
            if depth > max_depth:
                return
            for item in share_fs.iterdir(cid):
                is_dir = bool(item.get("is_directory") or item.get("is_dir"))
                name = item.get("name", "")
                if is_dir:
                    walk(item["id"], depth + 1)
                elif _is_video(name):
                    out.append({"id": item["id"], "name": name})

        walk(root, 0)
        return out
