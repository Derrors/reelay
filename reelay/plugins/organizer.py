"""整理入库 Organizer —— after_save Hook（方案 Stage 3 核心）。

转存进 115 后，把落地的视频文件按媒体库规范改名、组织目录结构（全在 115 内部，
不下载到本地）。执行顺序在管线里是：before_save(DirRouter 选目录) →
StorageProvider(转存) → **after_save(本 Hook：建目录 + 改名 + 移动)** → 读回。

稳妥要点（方案「整理有副作用，务必稳妥」）：
- 集数/季/标题来自 AI 解析，可能出错 → 关键字段不全时**不硬整理**，标 need_manual；
- 每一步先在 ctx 记「整理计划」，Pipeline 持久化后才执行；
- 重名冲突策略：保留两份文件并转人工，不标记完成。

命名模板取自 config.organize.naming（遵循 Jellyfin/Emby 通用 SxxEyy 规范）。
dry-run 只生成整理计划；真实模式缺客户端则失败。
"""

from __future__ import annotations

import logging
import os
import re

from ..core.contracts import PipelineContext
from ..core.io import run_blocking
from ..core.media import episode_of as _episode_of
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
    name = "organizer"
    storage_provider = "p115"
    stage = "after_save"
    order = 20

    def __init__(self) -> None:
        self._client = None
        self._dry_run = os.getenv("REELAY_DRY_RUN", "").lower() in {"1", "true", "yes"}
        cookies = os.getenv("REELAY_115_COOKIES")
        if cookies and not self._dry_run:
            try:
                from p115client import P115Client
                self._client = P115Client(cookies)
            except Exception:
                self._client = None

    async def prepare(self, ctx: PipelineContext) -> None:
        """Pipeline 在执行前持久化此计划；恢复沿同一组文件 ID 续跑。"""
        ns = ctx.ns("organize")
        if ns.get("plan") and ns.get("base_cid") == ctx.save_dir_cid:
            return
        problem = self._validate(ctx.parsed) if ctx.parsed else "缺识别结果"
        if problem:
            raise ValueError(problem)
        landed = ctx.ns("storage").get("landed") or []
        if not landed:
            raise ValueError("缺少已核验的落地文件")
        ns["plan"] = self._build_plan(ctx.parsed, landed)
        ns["base_cid"] = ctx.save_dir_cid
        ns.pop("error", None)
        if not ns["plan"]:
            raise ValueError("整理计划没有视频文件")

    async def run(self, ctx: PipelineContext) -> bool:
        try:
            await self.prepare(ctx)
            plan = ctx.ns("organize")["plan"]
            if self._dry_run:
                ctx.ns("organize")["applied"] = False
                ctx.log("[dry-run] 整理计划已生成，不执行网盘操作")
                return True
            if self._client is None:
                raise RuntimeError("整理客户端不可用，请检查依赖与凭证")
            await run_blocking(self._apply_sync, ctx, ctx.parsed, plan)
            ctx.ns("organize")["applied"] = True
            ctx.ns("organize").pop("error", None)
            ctx.log("整理文件 ID、目录和名称全部读回核验通过")
        except Exception as exc:
            ctx.ns("organize")["error"] = str(exc)
            ctx.ns("organize")["applied"] = False
            ctx.log(f"整理未完成：{exc}")
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
        videos = [x for x in landed if not x.get("is_dir")]
        allowed_episodes = set(p.episodes or [])
        for f in videos:
            ext = _ext_of(f.get("name", ""))
            if is_tv:
                detected_season, ep = _episode_of(f.get("name", ""))
                if ep is None:
                    raise ValueError(f"无法从文件名识别集号：{f.get('name', '')}")
                if allowed_episodes and ep not in allowed_episodes:
                    raise ValueError(f"文件集号 E{ep:02d} 不在已识别集数中：{f.get('name', '')}")
                season = detected_season if detected_season is not None else (p.season if p.season is not None else 1)
                if p.season is not None and season != p.season:
                    raise ValueError(f"文件季号与识别结果不一致：{f.get('name', '')}")
                tpl = naming.get("tv", "{title} ({year})/Season {season:02d}/{title} - S{season:02d}E{ep:02d}.{ext}")
                rel = tpl.format(title=title, year=year, season=season, ep=ep, resolution=_safe(p.resolution), ext=ext)
            else:
                tpl = naming.get("movie", "{title} ({year})/{title} ({year}) - {resolution}.{ext}")
                rel = tpl.format(title=title, year=year, resolution=_safe(p.resolution) or "", ext=ext)
            parts = [seg for seg in rel.split("/") if seg]
            if not parts or any(seg in (".", "..") for seg in parts):
                raise ValueError("命名模板包含无效路径")
            plan.append({
                "src_id": f["id"],
                "src_name": f.get("name", ""),
                "size": f.get("size"),
                "sha1": f.get("sha1"),
                "target_dir": "/".join(parts[:-1]),
                "target_name": parts[-1],
            })
        if len({(x["target_dir"], x["target_name"]) for x in plan}) != len(plan):
            raise ValueError("整理计划中的多个文件具有相同目标名称")
        return sorted(plan, key=lambda item: item["target_name"])

    def _apply_sync(self, ctx: PipelineContext, p, plan: list[dict]) -> None:
        from p115client import P115FileSystem

        client = self._client
        fs = P115FileSystem(client)

        def _mkdir_web(parent: int, name: str) -> int:
            """webapi 建目录（普通 cookie 可用；app 接口会 errno 99）。

            无该接口（如测试桩 fake client）时回退 fs.mkdir。
            """
            if hasattr(client, "fs_mkdir"):
                resp = client.fs_mkdir({"cname": name, "pid": int(parent)})
                if isinstance(resp, dict):
                    fid = resp.get("file_id") or (resp.get("data") or {}).get("category_id")
                    if fid:
                        return int(fid)
            made = fs.mkdir(parent, name=name)  # 回退
            return int(made["id"] if isinstance(made, dict) else made)
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
                        found = int(item["id"])
                        break
                if found is None:
                    found = _mkdir_web(pid, seg)
                dir_cache[key] = found
                pid = found
            dir_cache[rel_dir] = pid
            return pid

        for item in plan:
            source_id = int(item["src_id"])
            target_cid = ensure_dir(item["target_dir"])
            existing = [x for x in fs.iterdir(target_cid) if x.get("name") == item["target_name"]]
            if any(int(x["id"]) != source_id for x in existing):
                raise FileExistsError(f"目标已有其他同名文件，保留源文件：{item['target_name']}")
            current = fs.get_attr(source_id, refresh=True)
            if item.get("size") is not None and current.get("size") != item["size"]:
                raise ValueError("落地文件大小已变化，停止整理")
            if item.get("sha1") and (current.get("sha1") or '').upper() != item["sha1"].upper():
                raise ValueError("落地文件内容已变化，停止整理")
            # 根据真实文件 ID 的当前位置续跑；已改名或已移动的步骤无需重做。
            if current["name"] != item["target_name"]:
                if hasattr(client, "fs_rename"):
                    client.fs_rename((source_id, item["target_name"]))
                else:
                    fs.rename(source_id, name=item["target_name"])
            if int(current["parent_id"]) != target_cid:
                if hasattr(client, "fs_move"):
                    client.fs_move(source_id, pid=target_cid)
                else:
                    fs.move(source_id, to_dir=target_cid)
            final = fs.get_attr(source_id, refresh=True)
            if int(final["parent_id"]) != target_cid or final["name"] != item["target_name"]:
                raise RuntimeError("整理读回核验不通过，保留计划供后续重试")
