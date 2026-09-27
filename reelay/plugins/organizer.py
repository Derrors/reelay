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

from ..core.auth115 import get_store
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
    ui_label = "媒体库整理"
    ui_description = "转存后按已持久化计划建目录、改名、移动，并读回核验。"
    ui_section = "organize"
    ui_config_type = "naming_templates"
    storage_provider = "p115"
    stage = "after_save"
    order = 20

    def __init__(self) -> None:
        # 测试可注入伪 client（self._client = fake）；生产不再自建 cookie
        # 客户端——读操作走凭证中心 cookie 客户端，写操作（建目录/改名/
        # 移动/删除）走混合鉴权（OAuth 优先、cookie 回退）。
        self._client = None
        self._dry_run = os.getenv("REELAY_DRY_RUN", "").lower() in {"1", "true", "yes"}
        self._auth = get_store()

    def _read_client(self):
        """列目录/读属性使用的客户端：注入伪 client 优先，否则用 cookie。"""
        if self._client is not None:
            return self._client
        if self._dry_run:
            return None
        return self._auth.cookie_client()

    async def prepare(self, ctx: PipelineContext) -> None:
        """Pipeline 在执行前持久化此计划；恢复沿同一组文件 ID 续跑。"""
        ns = ctx.ns("organize")
        relative_category = ctx.ns("dir_router").get("relative_category", "")
        if (ns.get("plan") and ns.get("base_cid") == ctx.save_dir_cid
                and ns.get("relative_category", "") == relative_category):
            return
        problem = self._validate(ctx.parsed) if ctx.parsed else "缺识别结果"
        if problem:
            raise ValueError(problem)
        landed = ctx.ns("storage").get("landed") or []
        if not landed:
            raise ValueError("缺少已核验的落地文件")
        ns["plan"] = self._build_plan(ctx.parsed, landed, relative_category)
        ns["base_cid"] = ctx.save_dir_cid
        ns["relative_category"] = relative_category
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
            if self._read_client() is None:
                raise RuntimeError("整理客户端不可用：读核验需要 cookie，写操作可走 OAuth")
            await run_blocking(self._apply_sync, ctx, ctx.parsed, plan)
            self._cleanup_staging(ctx)
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

    def _cleanup_staging(self, ctx: PipelineContext) -> None:
        """整理成功后删除空的 .reelay-<task> 暂存目录。

        仅在该暂存目录确实为空时删除；非空（有文件没被移走）则保留，绝不误删。
        dry-run 不清理。
        """
        if self._dry_run:
            return
        read_client = self._read_client()
        if read_client is None:
            return
        from p115client import P115FileSystem
        manifest = ctx.ns("storage").get("manifest") or {}
        stage = manifest.get("staging_cid")
        if not isinstance(stage, int):
            return
        try:
            fs = P115FileSystem(read_client)
            if list(fs.iterdir(stage)):
                log.info("暂存目录非空，保留：%s", stage)
                return
            if self._client is not None:
                # 测试注入的伪 client
                if hasattr(self._client, "fs_delete"):
                    self._client.fs_delete([stage])
                else:
                    return
            else:
                self._auth.delete([stage])
            ctx.log("已清理空暂存目录")
        except Exception as exc:  # noqa: BLE001
            # 清理失败不影响已完成的整理结果
            log.warning("清理暂存目录失败（已忽略）：%s", exc)

    # ------------------------------------------------------------------ #
    def _validate(self, p) -> str | None:
        if not p.title:
            return "缺标题"
        if p.media_type == "tv":
            if not p.episodes:
                return "剧集缺集数"
            # 季允许缺省为 1
        return None

    def _build_plan(self, p, landed: list[dict], category_path: str = "") -> list[dict]:
        from ..core.naming_template import render_path
        naming = runtime.get("organize.naming", {}) or {}
        title = _safe(p.title)
        is_tv = p.media_type == "tv" or bool(p.episodes)
        plan = []
        videos = [x for x in landed if not x.get("is_dir")]
        allowed_episodes = set(p.episodes or [])
        for f in videos:
            ext = _ext_of(f.get("name", ""))
            detected_season, episode = _episode_of(f.get("name", "")) if is_tv else (None, None)
            if is_tv:
                if episode is None:
                    raise ValueError(f"无法从文件名识别集号：{f.get('name', '')}")
                if allowed_episodes and episode not in allowed_episodes:
                    raise ValueError(f"文件集号 E{episode:02d} 不在已识别集数中：{f.get('name', '')}")
                season = detected_season if detected_season is not None else (p.season if p.season is not None else 1)
                if p.season is not None and season != p.season:
                    raise ValueError(f"文件季号与识别结果不一致：{f.get('name', '')}")
                template = naming.get("tv", "{{ title }} ({{ year|default('', true) }})/Season {{ season|pad2 }}/{{ title }} - S{{ season|pad2 }}E{{ episode|pad2 }}.{{ ext }}")
            else:
                season, episode = p.season or 1, None
                template = naming.get("movie", "{{ title }} ({{ year|default('', true) }})/{{ title }} ({{ year|default('', true) }}){% if resolution %} - {{ resolution }}{% endif %}.{{ ext }}")
            rel = render_path(template, {
                "title": title, "year": p.year, "season": season, "episode": episode,
                "ep": episode, "resolution": _safe(p.resolution) if p.resolution else "",
                "ext": ext, "media_type": p.media_type, "genre_ids": p.genre_ids,
                "origin_country": p.origin_country, "tmdb_id": p.raw_fields.get("tmdbid"),
                "original_name": f.get("name", ""),
            }, label=f"{'剧集' if is_tv else '电影'}命名模板")
            if category_path:
                rel = category_path.strip("/") + "/" + rel
            parts = rel.split("/")
            plan.append({
                "src_id": f["id"], "src_name": f.get("name", ""),
                "size": f.get("size"), "sha1": f.get("sha1"),
                "target_dir": "/".join(parts[:-1]), "target_name": parts[-1],
            })
        if len({(x["target_dir"], x["target_name"]) for x in plan}) != len(plan):
            raise ValueError("整理计划中的多个文件具有相同目标名称")
        return sorted(plan, key=lambda item: item["target_name"])

    def _apply_sync(self, ctx: PipelineContext, p, plan: list[dict]) -> None:
        from p115client import P115FileSystem

        # 读（列目录/读属性）走 cookie 客户端；测试注入伪 client 时全部走伪 client。
        read_client = self._read_client()
        fs = P115FileSystem(read_client)
        fake_mode = self._client is not None

        def do_mkdir(parent: int, name: str) -> int:
            if fake_mode:
                # 测试桩：有 webapi 风格接口就用，否则回退 fs.mkdir
                if hasattr(read_client, "fs_mkdir"):
                    resp = read_client.fs_mkdir({"cname": name, "pid": int(parent)})
                    if isinstance(resp, dict):
                        fid = resp.get("file_id") or (resp.get("data") or {}).get("category_id")
                        if fid:
                            return int(fid)
                made = fs.mkdir(parent, name=name)
                return int(made["id"] if isinstance(made, dict) else made)
            return int(self._auth.mkdir(int(parent), name)["id"])

        def do_rename(fid: int, name: str) -> None:
            if fake_mode:
                if hasattr(read_client, "fs_rename"):
                    read_client.fs_rename((fid, name))
                else:
                    fs.rename(fid, name=name)
            else:
                self._auth.rename(int(fid), name)

        def do_move(fid: int, pid: int) -> None:
            if fake_mode:
                if hasattr(read_client, "fs_move"):
                    read_client.fs_move(fid, pid=pid)
                else:
                    fs.move(fid, to_dir=pid)
            else:
                self._auth.move(int(fid), int(pid))

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
                    found = do_mkdir(pid, seg)
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
                do_rename(source_id, item["target_name"])
            if int(current["parent_id"]) != target_cid:
                do_move(source_id, target_cid)
            final = fs.get_attr(source_id, refresh=True)
            if int(final["parent_id"]) != target_cid or final["name"] != item["target_name"]:
                raise RuntimeError("整理读回核验不通过，保留计划供后续重试")
