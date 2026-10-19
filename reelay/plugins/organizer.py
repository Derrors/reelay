"""整理入库 Organizer —— after_save Hook（方案 Stage 3 核心）。

转存进 115 后，把落地的视频文件按媒体库规范改名、组织目录结构（全在 115 内部，
不下载到本地）。执行顺序在管线里是：before_save(DirRouter 选目录) →
StorageProvider(转存) → **after_save(本 Hook：建目录 + 改名 + 移动)** → 读回。

稳妥要点（方案「整理有副作用，务必稳妥」）：
- 集数/季/标题来自 AI 解析，可能出错 → 关键字段不全时**不硬整理**，标 need_manual；
- 每一步先在 ctx 记「整理计划」，Pipeline 持久化后才执行；
- 重名冲突策略（organize.conflict）：
  - manual（默认）：保留两份文件并转人工，不标记完成；
  - skip：保留库中已有文件，删除本次新转存的文件（进 115 回收站）；
  - suffix：新文件改名为「名称 (2).ext」并存；
  - upgrade（洗版）：按 ``release`` 偏好比较（偏好的字幕 → 清晰度 → HDR → 音轨 → 片源 → 体积，
    与收件箱推荐版本同一套比较），新文件更好则删除旧文件（进回收站）后入库，否则同 skip。
    删除前先记入扩展状态，中断后重放不会重复删除或误判。

命名模板取自 config.organize.naming（遵循 Jellyfin/Emby 通用 SxxEyy 规范）。
dry-run 只生成整理计划；真实模式缺客户端则失败。
"""

from __future__ import annotations

import logging
import os
import re

from ..core.auth115 import get_store
from ..core.config import DEFAULT_NAMING
from ..core.contracts import PipelineContext
from ..core.io import run_blocking
from ..core.media import episode_of as _episode_of, resolution_rank
from ..core.registry import register
from ..core import release, runtime

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
        # 读（列目录/读属性）走凭证中心的 cookie 客户端；写（建目录/改名/移动/删除）
        # 走混合鉴权（OAuth 优先、cookie 回退）。测试替换 ``_auth`` 为伪凭证中心。
        self._dry_run = os.getenv("REELAY_DRY_RUN", "").lower() in {"1", "true", "yes"}
        self._auth = get_store()

    def _read_client(self):
        return None if self._dry_run else self._auth.cookie_client()

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
        # 转存清单里每个文件的季集已经过校验（含转人工前 AI 补出、经规则校验的季集），整理沿用它。
        manifest = ctx.ns("storage").get("manifest") or {}
        mapping = {f["name"]: (f.get("season"), f["episode"]) for f in manifest.get("files", [])
                   if f.get("name") and type(f.get("episode")) is int}
        ns["plan"] = self._build_plan(ctx.parsed, landed, relative_category, mapping)
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
            self._auth.delete([stage])
            ctx.log("已清理空暂存目录")
        except Exception as exc:  # noqa: BLE001
            # 清理失败不影响已完成的整理结果
            log.warning("清理暂存目录失败（已忽略）：%s", exc)

    def _resolve_conflict(self, strategy, fs, target_cid, item, source_id, existing, candidate, state):
        """按策略处理同名不同 ID 的文件，返回 (动作, 本次文件的最终名称)。

        candidate 是本次文件的 ``{"rank", "release", "size"}``；库中文件优先用入库时记下的信息，
        没有记录时从文件名识别。
        """
        name = item["target_name"]
        if strategy == "suffix":
            stem, dot, ext = name.rpartition(".")
            stem, ext = (stem, f".{ext}") if dot else (name, "")
            taken = {x.get("name"): int(x["id"]) for x in fs.iterdir(target_cid)}
            for index in range(2, 100):
                candidate = f"{stem} ({index}){ext}"
                if taken.get(candidate) in (None, source_id):
                    return "suffix", candidate
            raise FileExistsError(f"同名文件过多，保留源文件：{name}")
        if strategy in ("skip", "upgrade"):
            existing_id = int(existing["id"])
            recorded = state.get(f"file:{existing_id}") or {}
            current = {"rank": recorded.get("rank") or resolution_rank(existing.get("name")),
                       "release": recorded.get("release") or release.from_text(existing.get("name")),
                       "size": existing.get("size") or recorded.get("size") or 0}
            if strategy == "upgrade" and release.compare(candidate, current, runtime.get("release"), strict=True) > 0:
                state.set(f"replaced:{existing_id}", {"by": source_id, "name": name})
                self._auth.delete([existing_id])
                return "upgrade", name
            state.set(f"skipped:{source_id}", {"kept": existing_id, "name": name})
            self._auth.delete([source_id])
            return "skip", name
        raise FileExistsError(f"目标已有其他同名文件，保留源文件：{name}")

    # ------------------------------------------------------------------ #
    def _validate(self, p) -> str | None:
        if not p.title:
            return "缺标题"
        if p.media_type == "tv":
            if not p.episodes:
                return "剧集缺集数"
            # 季允许缺省为 1
        return None

    def _build_plan(self, p, landed: list[dict], category_path: str = "",
                    mapping: dict[str, tuple] | None = None) -> list[dict]:
        from ..core.naming_template import render_path
        naming = runtime.get("organize.naming", {}) or {}
        title = _safe(p.title)
        is_tv = p.media_type == "tv" or bool(p.episodes)
        plan = []
        videos = [x for x in landed if not x.get("is_dir")]
        allowed_episodes = set(p.episodes or [])
        mapping = mapping or {}
        for f in videos:
            ext = _ext_of(f.get("name", ""))
            detected_season, episode = ((mapping.get(f.get("name", "")) or _episode_of(f.get("name", "")))
                                        if is_tv else (None, None))
            if is_tv:
                if episode is None:
                    raise ValueError(f"无法从文件名识别集号：{f.get('name', '')}")
                if allowed_episodes and episode not in allowed_episodes:
                    raise ValueError(f"文件集号 E{episode:02d} 不在已识别集数中：{f.get('name', '')}")
                season = detected_season if detected_season is not None else (p.season if p.season is not None else 1)
                if p.season is not None and season != p.season:
                    raise ValueError(f"文件季号与识别结果不一致：{f.get('name', '')}")
                template = naming.get("tv", DEFAULT_NAMING["tv"])
            else:
                season, episode = p.season or 1, None
                template = naming.get("movie", DEFAULT_NAMING["movie"])
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

        fs = P115FileSystem(self._read_client())
        auth = self._auth

        def do_mkdir(parent: int, name: str) -> int:
            return int(auth.mkdir(int(parent), name)["id"])

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

        strategy = runtime.get("organize.conflict", "manual")
        state = runtime.services().state(self.name)
        conflicts = ctx.ns("organize").setdefault("conflicts", [])
        new_rank = resolution_rank(p.resolution)

        for item in plan:
            source_id = int(item["src_id"])
            if state.get(f"skipped:{source_id}"):
                continue  # 冲突时已按策略删除了本次的新文件
            target_cid = ensure_dir(item["target_dir"])
            current = fs.get_attr(source_id, refresh=True)
            if item.get("size") is not None and current.get("size") != item["size"]:
                raise ValueError("落地文件大小已变化，停止整理")
            if item.get("sha1") and (current.get("sha1") or '').upper() != item["sha1"].upper():
                raise ValueError("落地文件内容已变化，停止整理")
            target_name = item["target_name"]
            others = [x for x in fs.iterdir(target_cid)
                      if x.get("name") == target_name and int(x["id"]) != source_id]
            candidate = {"rank": resolution_rank(item.get("src_name")) or new_rank,
                         "release": release.fill(p.release, release.from_text(item.get("src_name"))),
                         "size": current.get("size") or 0}
            if others:
                action, target_name = self._resolve_conflict(
                    strategy, fs, target_cid, item, source_id, others[0], candidate, state)
                conflicts.append({"target": f"{item['target_dir']}/{item['target_name']}",
                                  "action": action, "existing_id": str(others[0]["id"])})
                if action == "skip":
                    continue
            # 根据真实文件 ID 的当前位置续跑；已改名或已移动的步骤无需重做。
            if current["name"] != target_name:
                auth.rename(source_id, target_name)
            if int(current["parent_id"]) != target_cid:
                auth.move(source_id, target_cid)
            state.set(f"file:{source_id}", {"rank": candidate["rank"], "release": candidate["release"],
                                            "size": current.get("size"), "name": item.get("src_name")})
            final = fs.get_attr(source_id, refresh=True)
            if int(final["parent_id"]) != target_cid or final["name"] != target_name:
                raise RuntimeError("整理读回核验不通过，保留计划供后续重试")
