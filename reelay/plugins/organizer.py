"""整理入库 Organizer —— after_save Hook（方案 Stage 3 核心）。

转存进 115 后，把落地的视频文件按媒体库规范改名、组织目录结构（全在 115 内部，
不下载到本地）。执行顺序在管线里是：before_save(DirRouter 选目录) →
StorageProvider(转存) → **after_save(本 Hook：建目录 + 改名 + 移动)** → 读回。

稳妥要点（方案「整理有副作用，务必稳妥」）：
- 集数/季/标题来自 AI 解析，可能出错 → 关键字段不全时**不硬整理**，标 need_manual；
- 每一步先在 ctx 记「整理计划」，失败可追溯；
- 重名冲突策略：跳过（不覆盖，交人工）。

命名模板取自 config.organize.naming（遵循 Jellyfin/Emby 通用 SxxEyy 规范）。
无 115 client（dry-run）时只生成整理计划、不实际操作。
"""

from __future__ import annotations

import asyncio
import logging
import os
import re

from ..core.contracts import PipelineContext, TaskState
from ..core.registry import register
from ..core import runtime

log = logging.getLogger("reelay.plugin.organizer")


def _safe(name: str) -> str:
    """清洗成合法目录/文件名片段。"""
    return re.sub(r'[\\/:*?"<>|]', " ", str(name or "")).strip()


def _ext_of(filename: str, default: str = "mp4") -> str:
    m = re.search(r"\.([A-Za-z0-9]{1,5})$", filename or "")
    return m.group(1) if m else default


@register(kind="hook", stage="after_save", order=20)
class Organizer:
    stage = "after_save"
    order = 20

    def __init__(self) -> None:
        self._client = None
        cookies = os.getenv("REELAY_115_COOKIES")
        if cookies:
            try:
                from p115client import P115Client
                self._client = P115Client(cookies)
            except Exception:
                self._client = None

    async def run(self, ctx: PipelineContext) -> bool:
        p = ctx.parsed
        landed = ctx.ns("storage").get("landed") or []
        if not p or not landed:
            return True  # 没东西可整理，放行

        # --- 字段校验：不全则不硬整理，标 need_manual ---
        problem = self._validate(p)
        if problem:
            ctx.ns("organize")["skipped"] = problem
            ctx.log(f"整理跳过（{problem}），标记 need_manual 待人工")
            if ctx.result is not None:
                ctx.result["verified"] = False   # 促使 pipeline 置 need_manual
            return True

        # --- 生成整理计划 ---
        plan = self._build_plan(p, landed)
        ctx.ns("organize")["plan"] = plan
        ctx.log(f"整理计划：{len(plan)} 个文件 → 「{plan[0]['target_dir']}」" if plan else "整理计划为空")

        # --- 执行 ---
        if self._client is None:
            ctx.ns("organize")["applied"] = False
            ctx.log("[dry-run] 未接 115，仅生成整理计划")
            return True

        try:
            await asyncio.to_thread(self._apply_sync, ctx, p, plan)
            ctx.ns("organize")["applied"] = True
            ctx.log("整理入库完成")
        except Exception as exc:  # 整理失败不回滚「已转存」既成事实，转人工
            log.warning("整理失败：%s", exc)
            ctx.ns("organize")["error"] = str(exc)
            ctx.log(f"整理失败（{exc}），文件已转存，标记 need_manual")
            if ctx.result is not None:
                ctx.result["verified"] = False
        return True

    # ------------------------------------------------------------------ #
    def _validate(self, p) -> str | None:
        if not p.title:
            return "缺标题"
        if p.media_type == "tv":
            if not p.episodes:
                return "剧集缺集数"
            # 季允许缺省为 1
        return None

    def _build_plan(self, p, landed: list[dict]) -> list[dict]:
        naming = runtime.get("organize.naming", {}) or {}
        year = p.year or ""
        title = _safe(p.title)
        is_tv = p.media_type == "tv" or bool(p.episodes)

        plan = []
        # 只整理视频文件；按落地顺序对应集数（简化：landed[i] ↔ episodes[i]）
        videos = [x for x in landed if not x.get("is_dir")]
        eps = p.episodes or []
        for i, f in enumerate(videos):
            ext = _ext_of(f.get("name", ""))
            if is_tv:
                season = p.season or 1
                ep = eps[i] if i < len(eps) else (i + 1)
                tpl = naming.get("tv", "{title} ({year})/Season {season:02d}/{title} - S{season:02d}E{ep:02d}.{ext}")
                rel = tpl.format(title=title, year=year, season=season, ep=ep, resolution=_safe(p.resolution), ext=ext)
            else:
                tpl = naming.get("movie", "{title} ({year})/{title} ({year}) - {resolution}.{ext}")
                rel = tpl.format(title=title, year=year, resolution=_safe(p.resolution) or "", ext=ext)
            parts = [seg for seg in rel.split("/") if seg]
            plan.append({
                "src_id": f["id"],
                "src_name": f.get("name", ""),
                "target_dir": "/".join(parts[:-1]),
                "target_name": parts[-1],
            })
        return plan

    def _apply_sync(self, ctx: PipelineContext, p, plan: list[dict]) -> None:
        from p115client import P115FileSystem

        fs = P115FileSystem(self._client)
        base_cid = int(ctx.save_dir_cid or 0)
        dir_cache: dict[str, int] = {}

        def ensure_dir(rel_dir: str) -> int:
            """按相对路径逐级建目录（幂等：已存在则复用），返回末级目录 cid。"""
            if rel_dir in dir_cache:
                return dir_cache[rel_dir]
            pid = base_cid
            acc = []
            for seg in rel_dir.split("/"):
                if not seg:
                    continue
                acc.append(seg)
                key = "/".join(acc)
                if key in dir_cache:
                    pid = dir_cache[key]
                    continue
                # 找同名子目录，没有则建
                found = None
                for item in fs.iterdir(pid):
                    if (item.get("is_directory") or item.get("is_dir")) and item.get("name") == seg:
                        found = item["id"]
                        break
                if found is None:
                    made = fs.mkdir(name=seg, pid=pid)
                    found = made["id"] if isinstance(made, dict) else made
                dir_cache[key] = found
                pid = found
            dir_cache[rel_dir] = pid
            return pid

        for item in plan:
            target_cid = ensure_dir(item["target_dir"])
            # 重名冲突：目标目录已有同名文件则跳过（不覆盖）
            exists = any(x.get("name") == item["target_name"] for x in fs.iterdir(target_cid))
            if exists:
                ctx.log(f"整理跳过重名：{item['target_name']}")
                continue
            # 先改名，再移动到目标目录
            if item["src_name"] != item["target_name"]:
                fs.rename(item["src_id"], name=item["target_name"])
            fs.move(item["src_id"], to_dir=target_cid)
