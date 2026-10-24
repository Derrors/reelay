"""媒体库整理：before_save 选存放目录 + after_save 整理入库。

两个 Hook 同名（organizer），共用一个开关与 plugins.organizer 设置：

- ``LibraryRouter``（转存前）：按目录方式（``layout``）决定存进哪个保存目录、其下哪个文件夹——
  二级分类（本目录的 ``category.yaml``，MoviePilot 风格，管理台只读）、按媒体类型（``type_dirs``）或不分类。分类结果的
  第一段与某个保存目录别名同名时转存根目录跟随它，所以必须在转存前决定（去重键含目标 CID）。
- ``Organizer``（转存完成后）：把落地的视频文件按媒体库规范改名、组织目录结构（全在网盘内部，
  不下载到本地），读回核验。网盘操作经存储 Provider 的 ``library()`` 接口（115 或模拟存储）。

执行顺序：before_save(LibraryRouter) → StorageProvider(转存) → after_save(Organizer) → 读回。

稳妥要点（整理有副作用）：
- 集数/季/标题来自 AI 解析，可能出错 → 关键字段不全时**不硬整理**，标 need_manual；
- 每一步先在 ctx 记「整理计划」，Pipeline 持久化后才执行；按文件 ID 读取实际位置续跑；
- 重名冲突策略（plugins.organizer.conflict）：
  - manual（默认）：保留两份文件并转人工，不标记完成；
  - skip：保留库中已有文件，删除本次新转存的文件（进 115 回收站）；
  - suffix：新文件改名为「名称 (2).ext」并存；
  - upgrade（洗版）：按 ``release`` 偏好比较（偏好的字幕 → 清晰度 → HDR → 音轨 → 片源 → 体积，
    与收件箱推荐版本同一套比较），新文件更好则删除旧文件（进回收站）后入库，否则同 skip。
    删除前先把决定记进任务（替换记录与扩展状态）并落库，中断后重放不会重复删除或误判。

命名模板取自 plugins.organizer.naming（遵循 Jellyfin/Emby 通用 SxxEyy 规范）。

本目录：``settings.py`` 默认设置与校验，``naming.py`` 受限 Jinja2 命名模板，
``category.py`` / ``category.yaml`` 目录方式与二级分类规则。
"""

from __future__ import annotations

import asyncio
import logging
import re

from ...core.contracts import ParsedResource, PipelineContext, StorageBusy
from ...core.io import run_blocking
from ...core.media import episode_of as _episode_of, is_video, resolution_rank, strip_ext
from ...core.registry import register
from ...core.validation import validate_resource
from ...core import release, runtime, tmdb
from .category import DEFAULT_TYPE_DIRS, categories, classify, describe, type_dir
from .naming import EMPTY_VARIABLES, file_info, render_path, sample_values, validate_template
from .settings import DEFAULT_NAMING

log = logging.getLogger("reelay.plugin.organizer")


def _safe(name: str) -> str:
    """清洗成合法目录/文件名片段。"""
    return re.sub(r'[\\/:*?"<>|]', " ", str(name or "")).strip()


def _ext_of(filename: str, default: str = "mp4") -> str:
    m = re.search(r"\.([A-Za-z0-9]{1,5})$", filename or "")
    return m.group(1) if m else default


def _layout() -> tuple[str, dict]:
    """当前作用域的（目录方式, 媒体类型文件夹）。"""
    organizer = runtime.get("plugins.organizer", {}) or {}
    return organizer.get("layout", "category"), organizer.get("type_dirs") or DEFAULT_TYPE_DIRS


def route(p, source_rule: dict, *, current: str | None = None, pinned: str | None = None,
          preferred: str | None = None) -> dict:
    """按当前作用域的配置算出目标目录，不修改任务。

    返回 alias（None 表示沿用任务/来源目录）、category_path / relative_category（保存目录下的子目录）
    与写入轨迹的 note。指定的目录与分类结果不一致时直接存进该目录、不套用分类子目录：
    两者不一致通常说明分类本身就不对。人工指定（pinned）优先于 AI 按偏好选出的目录（preferred）。
    """
    chosen = pinned or preferred
    if chosen:
        auto = route(p, source_rule)
        if chosen == (auto["alias"] or source_rule.get("save_to") or "默认"):
            return auto
        who = "手动指定目录" if pinned else "AI 按偏好选择目录"
        return {"alias": chosen, "category_path": None, "relative_category": "",
                "note": f"{who}「{chosen}」，不套用分类子目录"}
    result = {"alias": None, "category_path": None, "relative_category": "", "note": None}
    layout, type_dirs = _layout()
    if layout == "none":
        return result
    if layout == "category":
        selected = classify(categories(), p.media_type, p.genre_ids, p.origin_country, p.year) if p else None
        note = f"二级分类命中「{selected}」"
    else:
        selected = type_dir(type_dirs, p.media_type, p.genre_ids) if p else None
        note = f"按媒体类型归入「{selected}」"
    if selected:
        root, _, rest = selected.partition("/")
        # 第一段是某个保存目录的别名时，转存根目录跟随它；否则保留来源指定目录，
        # 整理时在其下创建完整路径。
        if root in (runtime.get("save_dirs", {}) or {}):
            return {**result, "alias": root, "category_path": selected, "relative_category": rest, "note": note}
        return {**result, "alias": current, "category_path": selected, "relative_category": selected, "note": note}
    alias = current or source_rule.get("save_to") or "默认"
    missing = "二级分类元数据" if layout == "category" else "可识别的媒体类型"
    return {**result, "alias": alias, "note": f"无{missing}，用根目录「{alias}」"}


def build_plan(p, landed: list[dict], category_path: str = "",
               mapping: dict[str, tuple] | None = None) -> list[dict]:
    """按命名模板生成整理计划：每个落地视频文件的目标目录与文件名（相对保存目录）。"""
    naming = runtime.get("plugins.organizer.naming", {}) or {}
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
            season, template = None, naming.get("movie", DEFAULT_NAMING["movie"])
        rel = render_path(template, _naming_values(p, f.get("name", ""), ext, season, episode),
                          label=f"{'剧集' if is_tv else '电影'}命名模板")
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


def _naming_values(p, name: str, ext: str, season: int | None, episode: int | None) -> dict:
    """命名模板的变量，名称与含义同 MoviePilot；电影的季集为空字符串。"""
    stem = strip_ext(name)
    en_name = _title_before_markers(re.sub(r"^\s*[\[【][^\]】]+[\]】]", "", stem))
    tv = episode is not None
    return {
        **{key: "" for key in EMPTY_VARIABLES},
        # 清晰度、编码、片源等以每个文件自己的文件名为准；文件名里没有清晰度时用识别结果
        **{key: _safe(value) for key, value in file_info(name, p.resolution).items()},
        "title": _safe(p.title), "original_title": _safe(p.raw_fields.get("original_title") or p.title),
        "name": _safe(_guess_title(stem) or p.title), "en_name": "" if _CJK.search(en_name) else _safe(en_name),
        "year": str(p.year or ""), "tmdbid": str(p.raw_fields.get("tmdbid") or ""),
        "season": season if tv else "", "episode": episode if tv else "",
        "season_episode": f"S{season:02d}E{episode:02d}" if tv else "",
        "fileExt": f".{ext}", "original_name": name,
    }


_YEAR = re.compile(r"(?<![0-9])(19[0-9]{2}|20[0-9]{2})(?![0-9])")
_CJK = re.compile(r"[\u4e00-\u9fff]")


def _guess_title(stem: str) -> str:
    bracket = re.match(r"^\s*[\[【]([^\]】]+)[\]】]", stem)
    rest = _title_before_markers(stem[bracket.end():] if bracket else stem)
    # 开头方括号：后面还有中文片名时它是字幕组标签，否则（后面是英文名）它就是中文片名
    if bracket and _CJK.search(bracket.group(1)) and not _CJK.search(rest):
        return bracket.group(1).strip()
    return rest


def _title_before_markers(stem: str) -> str:
    cut = re.search(r"(?i)(?<![A-Za-z0-9])(?:S\d{1,2}[ ._-]*E\d{1,4}|S\d{1,2}|E\d{1,4}|19\d{2}|20\d{2}|"
                    r"2160p|1080p|720p|4k)(?![A-Za-z0-9])|第\s*\d+\s*[集话話季]", stem)
    title = stem[:cut.start()] if cut else stem
    return re.sub(r"[._]+", " ", title).strip(" -[]【】")


def guess_from_names(names: list[str]) -> dict:
    """从文件名猜片名、年份、季与集：开头方括号里的中文名优先，否则取年份 / 季集之前的部分。"""
    if not names:
        return {"title": "", "year": None, "media_type": None, "season": None, "episodes": []}
    stem = strip_ext(names[0])
    year = _YEAR.search(re.sub(r"(?i)\d{3,4}[pi]", "", stem))
    pairs = [_episode_of(name) for name in names]
    episodes = sorted({ep for _, ep in pairs if ep is not None})
    seasons = {season for season, ep in pairs if ep is not None and season is not None}
    return {"title": _guess_title(stem), "year": int(year.group(1)) if year else None,
            "media_type": "tv" if episodes else "movie",
            "season": seasons.pop() if len(seasons) == 1 else (1 if episodes and not seasons else None),
            "episodes": episodes}


def _same_slot(existing: dict, target_name: str, title: str, is_tv: bool) -> bool:
    """库里的这个文件是不是同一部作品的同一个位置（同一集 / 同一部电影的另一个版本）。

    文件名带清晰度、编码等信息后，同一集的两个版本名字不同，不能只看是否同名；也要认出以前按别的模板
    命名的文件。同名一定算；否则须是以片名开头的视频，剧集还要季集相同。
    """
    name = existing.get("name") or ""
    if name == target_name:
        return True
    if existing.get("is_dir") or not is_video(name) \
            or not name.casefold().startswith(title.casefold()):
        return False
    if not is_tv:
        return True
    episode = _episode_of(name)
    return episode[1] is not None and episode == _episode_of(target_name)


class _MediaLibrary:
    """两个挂载点共用名称：一个开关、一份 plugins.organizer 设置，管理台里是一张卡片。"""
    name = "organizer"
    ui_label = "媒体库整理"
    ui_description = ("转存前按目录方式（二级分类 / 按媒体类型 / 不分类）选定存放位置，"
                      "转存后按命名模板建目录、改名、移动，并读回核验。")
    ui_config_type = "media_library"
    # 管理台设置编辑器调用的动作（POST /api/extensions/organizer/actions/<名>）
    ui_actions = ("preview_naming", "category_rules", "preview_plan", "parse_names")
    # 整理要用存储 Provider 的网盘接口（见 contracts.StorageProvider）；没有它的存储不能开启整理
    requires_storage = ("library",)

    def preview_naming(self, payload: dict) -> dict:
        """用示例影片渲染命名模板，返回整理后的相对路径；模板不安全或无效时抛 ValueError。"""
        kind = payload.get("kind", "movie")
        if kind not in ("movie", "tv"):
            raise ValueError("kind 必须为 movie 或 tv")
        label = f"{kind}命名模板"
        validate_template(payload.get("template"), label)
        return {"path": render_path(payload["template"], sample_values(kind), label=label)}

    def category_rules(self, payload: dict) -> dict:
        """二级分类规则（category.yaml）的只读展示：按文件顺序，条件值附中文名。"""
        return {"media": describe(categories())}

    def preview_plan(self, payload: dict) -> dict:
        """整理效果预览：算出一组文件名整理后的位置（不碰网盘）。

        naming 是设置页里还没保存的命名模板（{movie, tv}），省略时用已保存的。
        """
        landed = payload.get("landed", [])
        if not isinstance(landed, list) or any(not isinstance(f, dict) for f in landed):
            raise ValueError("landed 必须是文件对象数组")
        resource = ParsedResource(**validate_resource(payload.get("parsed")))
        naming = payload.get("naming")
        if naming is None:
            return {"items": build_plan(resource, landed)}
        if not isinstance(naming, dict) or set(naming) - {"movie", "tv"}:
            raise ValueError("naming 只能包含 movie 与 tv 模板")
        for kind, template in naming.items():
            validate_template(template, f"{kind}命名模板")
        cfg = runtime.get_config()
        draft = {**cfg, "plugins": {**cfg.get("plugins", {}), "organizer": {
            **(cfg.get("plugins", {}).get("organizer") or {}),
            "naming": {**(runtime.get("plugins.organizer.naming", {}) or {}), **naming}}}}
        with runtime.scope(draft):
            return {"items": build_plan(resource, landed)}

    async def parse_names(self, payload: dict) -> dict:
        """「预览整理效果」：从贴进来的文件名识别片名、年份、季与集，用来预填表单。

        和真实整理一样以 TMDB 为准：配了 TMDB key 时用文件名里的片名（中文名、英文名依次）去搜，
        可信匹配时换成规范片名、年份与 tmdbid；tmdb 字段说明结果来源。
        """
        names = payload.get("names", [])
        if not isinstance(names, list) or any(type(n) is not str for n in names):
            raise ValueError("names 必须是文件名数组")
        names = [n.strip() for n in names if n.strip()]
        guess = {**guess_from_names(names), "tmdbid": None, "tmdb": "unconfigured"}
        if not names or not guess["title"] or not tmdb.api_key():
            return guess
        stem = strip_ext(names[0])
        en_name = _title_before_markers(re.sub(r"^\s*[\[【][^\]】]+[\]】]", "", stem))
        queries = list(dict.fromkeys(q for q in (guess["title"], en_name) if q))
        kinds = ["tv"] if guess["episodes"] else ["movie", "tv"]
        language, timeout = runtime.get("tmdb.language", "zh-CN"), runtime.get("tmdb.timeout", 10)

        def lookup(kind: str, query: str) -> dict | None:
            hit, _ = tmdb.best_match(kind, query, guess["year"], language=language, timeout=timeout)
            if hit or _CJK.search(query) or language.lower().startswith("en"):
                return hit
            # 英文文件名：按所设语言搜到的是译名，比不上英文名；用英文结果确定条目，再取所设语言的片名
            hit, _ = tmdb.best_match(kind, query, guess["year"], language="en-US", timeout=timeout)
            if not hit:
                return None
            local = [c for c in tmdb.search(kind, query, guess["year"], language=language, timeout=timeout)
                     if c.get("id") == hit.get("id")]
            return {**hit, **(local[0] if local else {})}

        try:
            for query in queries:
                for kind in kinds:
                    hit = await asyncio.to_thread(lookup, kind, query)
                    if hit:
                        return {**guess, "title": hit.get("title") or hit.get("name") or guess["title"],
                                "year": tmdb.candidate_year(hit) or guess["year"], "media_type": kind,
                                "tmdbid": hit.get("id"), "tmdb": "matched"}
        except Exception as exc:
            log.warning("命名预览查询 TMDB 失败：%s", exc)
            return {**guess, "tmdb": "failed"}
        return {**guess, "tmdb": "unmatched"}


@register(kind="hook", stage="before_save", order=10)
class LibraryRouter(_MediaLibrary):
    stage = "before_save"
    order = 10

    async def run(self, ctx: PipelineContext) -> bool:
        ns = ctx.ns("library")
        target = route(ctx.parsed, ctx.source_rule, current=ctx.save_dir_alias, pinned=ns.get("pinned"),
                       preferred=ns.get("preferred"))
        if target["alias"]:
            ctx.save_dir_alias = target["alias"]
        ns.pop("category_path", None)
        ns.pop("relative_category", None)
        if target["category_path"]:
            ns["category_path"] = target["category_path"]
            ns["relative_category"] = target["relative_category"]
        # 等待同一集的其他任务时每轮都会重新路由，结果不变就不再写轨迹
        if target["note"] and target["note"] != ns.get("note"):
            ctx.log(target["note"])
        ns["note"] = target["note"]
        return True

    def preview(self, ctx: PipelineContext) -> str:
        """待审批任务不改目录时将存入的位置，如「剧集/国产剧集」；含 AI 按偏好选出的目录。"""
        target = route(ctx.parsed, ctx.source_rule, preferred=ctx.ns("library").get("preferred"))
        alias = target["alias"] or ctx.source_rule.get("save_to") or "默认"
        return f"{alias}/{target['relative_category']}" if target["relative_category"] else alias


@register(kind="hook", stage="after_save", order=20)
class Organizer(_MediaLibrary):
    stage = "after_save"
    order = 20

    async def prepare(self, ctx: PipelineContext) -> None:
        """Pipeline 在执行前持久化此计划；恢复沿同一组文件 ID 续跑。重新整理时内核会先清掉旧计划。"""
        ns = ctx.ns("organize")
        relative_category = ctx.ns("library").get("relative_category", "")
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
        ns["plan"] = build_plan(ctx.parsed, landed, relative_category, mapping)
        ns["base_cid"] = ctx.save_dir_cid
        ns["relative_category"] = relative_category
        ns.pop("error", None)
        if not ns["plan"]:
            raise ValueError("整理计划没有视频文件")

    async def run(self, ctx: PipelineContext) -> bool:
        try:
            await self.prepare(ctx)
            plan = ctx.ns("organize")["plan"]
            drive = runtime.services().storage(ctx.ns("storage").get("provider", "p115")).library()
            await run_blocking(self._apply_sync, ctx, ctx.parsed, plan, drive)
            self._cleanup(ctx, drive)
            ctx.ns("organize")["applied"] = True
            for key in ("error", "pending_conflict", "resolve"):
                ctx.ns("organize").pop(key, None)
            ctx.log("整理文件 ID、目录和名称全部读回核验通过")
        except StorageBusy:
            raise  # 网盘暂时拒绝一切请求（如 115 风控冷却）：交给内核到点再整理，不算失败
        except Exception as exc:
            ctx.ns("organize")["error"] = str(exc)
            ctx.ns("organize")["applied"] = False
            ctx.log(f"整理未完成：{exc}")
            if ctx.result is not None:
                ctx.result["verified"] = False
        return True

    def _cleanup(self, ctx: PipelineContext, drive) -> None:
        """整理成功后：删除空的 .reelay-<task> 暂存目录（非空则保留，绝不误删），清掉只在重放时用的删除记录。"""
        state = runtime.services().state(self.name)
        for item in ctx.ns("organize").get("plan") or []:
            state.delete(f"skipped:{int(item['src_id'])}")
        manifest = ctx.ns("storage").get("manifest") or {}
        stage = manifest.get("staging_cid")
        if not isinstance(stage, int):
            return
        try:
            if drive.iterdir(stage):
                log.info("暂存目录非空，保留：%s", stage)
                return
            drive.delete([stage])
            ctx.log("已清理空暂存目录")
        except Exception as exc:  # noqa: BLE001
            # 清理失败不影响已完成的整理结果
            log.warning("清理暂存目录失败（已忽略）：%s", exc)

    @staticmethod
    def _remove(drive, file_id: int, parent: int, what: str) -> None:
        """删除（进回收站）后读回目录，确认文件已不在原处。删除可重复执行，结果不明时重来即可。"""
        drive.delete([file_id])
        if any(x["id"] == file_id for x in drive.iterdir(int(parent))):
            raise RuntimeError(f"删除{what}后读回仍在原目录，停止整理")

    def _resolve_conflict(self, ctx, strategy, drive, target_cid, item, source_id, source_parent, existing, candidate,
                          state):
        """按策略处理库里同一位置已有的文件，返回 (动作, 本次文件的最终名称)。

        candidate 是本次文件的 ``{"rank", "release", "size"}``；库中文件优先用入库时记下的信息，
        没有记录时从文件名识别。replace 是人工选的「覆盖保存」：不比较版本，直接换掉库里的文件。
        删除前先在扩展状态里记下决定，恢复时据此续跑。
        """
        name = item["target_name"]
        if strategy == "suffix":
            stem, dot, ext = name.rpartition(".")
            stem, ext = (stem, f".{ext}") if dot else (name, "")
            taken = {x["name"]: x["id"] for x in drive.iterdir(target_cid)}
            if taken.get(name) in (None, source_id):
                return "suffix", name  # 文件名本来就不同（如清晰度不同），两份各用自己的名字
            for index in range(2, 100):
                candidate = f"{stem} ({index}){ext}"
                if taken.get(candidate) in (None, source_id):
                    return "suffix", candidate
            raise FileExistsError(f"同名文件过多，保留源文件：{name}")
        if strategy in ("skip", "upgrade", "replace"):
            existing_id = int(existing["id"])
            recorded = state.get(f"file:{existing_id}") or {}
            current = {"rank": recorded.get("rank") or resolution_rank(existing.get("name")),
                       "release": recorded.get("release") or release.from_text(existing.get("name")),
                       "size": existing.get("size") or recorded.get("size") or 0}
            if strategy == "replace" or strategy == "upgrade" \
                    and release.compare(candidate, current, runtime.get("release"), strict=True) > 0:
                # 先把替换记进任务并落库（媒体库据此不再计入旧文件），再删除；中断后重放时旧文件已不在
                self._record_conflict(ctx, item, strategy, existing_id)
                self._remove(drive, existing_id, target_cid, "库里的旧文件")
                return strategy, name
            state.set(f"skipped:{source_id}", {"kept": existing_id, "name": name, "parent": int(source_parent)})
            self._remove(drive, source_id, source_parent, "本次转存的文件")
            return "skip", name
        raise FileExistsError(f"库里已有「{existing.get('name')}」，请选择删除转存文件或覆盖保存")

    # ------------------------------------------------------------------ #
    def _validate(self, p) -> str | None:
        if not p.title:
            return "缺标题"
        if p.media_type == "tv":
            if not p.episodes:
                return "剧集缺集数"
            # 季允许缺省为 1
        return None

    @staticmethod
    def _record_conflict(ctx: PipelineContext, item: dict, action: str, existing_id: int) -> None:
        """记下这次冲突怎么处理的（同一个目标只记一次）；替换类的决定在删除前就落库。"""
        conflicts = ctx.ns("organize").setdefault("conflicts", [])
        target = f"{item['target_dir']}/{item['target_name']}"
        if not any(c["target"] == target and c["existing_id"] == str(existing_id) for c in conflicts):
            conflicts.append({"target": target, "action": action, "existing_id": str(existing_id)})
        if action in ("replace", "upgrade"):
            runtime.services().store.save_task(ctx)

    def _apply_sync(self, ctx: PipelineContext, p, plan: list[dict], drive) -> None:
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
                found = next((item["id"] for item in drive.iterdir(pid) if item["is_dir"] and item["name"] == seg),
                             None)
                if found is None:
                    found = drive.mkdir(pid, seg)
                dir_cache[key] = found
                pid = found
            dir_cache[rel_dir] = pid
            return pid

        organize = ctx.ns("organize")
        organize.setdefault("conflicts", [])  # 每处冲突怎么处理的（见 _record_conflict），媒体库据此排除被替换的文件
        # 人工在冲突时选的处理（删除转存文件 / 覆盖保存）优先于配置的策略，整理成功后清除
        strategy = organize.get("resolve") or runtime.get("plugins.organizer.conflict", "manual")
        state = runtime.services().state(self.name)
        new_rank = resolution_rank(p.resolution)
        title, is_tv = _safe(p.title), p.media_type == "tv" or bool(p.episodes)
        ours = {int(item["src_id"]) for item in plan}  # 同一批的其他文件（如分段电影）不算冲突

        for item in plan:
            source_id = int(item["src_id"])
            skipped = state.get(f"skipped:{source_id}")
            if skipped:
                # 冲突时已决定删除本次的新文件：上次删除结果不明就再删一次并读回
                parent = skipped.get("parent")
                if parent is not None and any(x["id"] == source_id for x in drive.iterdir(int(parent))):
                    self._remove(drive, source_id, parent, "本次转存的文件")
                continue
            target_cid = ensure_dir(item["target_dir"])
            current = drive.get_attr(source_id)
            if item.get("size") is not None and current.get("size") != item["size"]:
                raise ValueError("落地文件大小已变化，停止整理")
            if item.get("sha1") and (current.get("sha1") or '').upper() != item["sha1"].upper():
                raise ValueError("落地文件内容已变化，停止整理")
            target_name = item["target_name"]
            others = [x for x in drive.iterdir(target_cid)
                      if x["name"] == target_name and x["id"] != source_id
                      or x["id"] not in ours and _same_slot(x, target_name, title, is_tv)]
            candidate = {"rank": resolution_rank(item.get("src_name")) or new_rank,
                         "release": release.fill(p.release, release.from_text(item.get("src_name"))),
                         "size": current.get("size") or 0}
            if others:
                others.sort(key=lambda x: x.get("name") != target_name)  # 同名的先处理：覆盖后目标名要空出来
                existing = others[0]
                if strategy == "manual":
                    # 留给管理台展示两份文件，供人选择删除转存文件或覆盖保存
                    organize["pending_conflict"] = {
                        "target_dir": item["target_dir"], "target_name": item["target_name"],
                        "existing": {"id": str(existing["id"]), "name": existing.get("name"),
                                     "size": existing.get("size")},
                        "incoming": {"id": str(source_id), "name": item.get("src_name"), "size": current.get("size")},
                    }
                action, target_name = self._resolve_conflict(
                    ctx, strategy, drive, target_cid, item, source_id, current["parent_id"], existing, candidate, state)
                self._record_conflict(ctx, item, action, int(existing["id"]))
                if action == "skip":
                    ctx.log(f"删除了本次转存的「{item.get('src_name')}」，保留库里的「{existing.get('name')}」")
                    continue
                if action == "replace":
                    ctx.log(f"覆盖保存：库里的「{existing.get('name')}」已删除，换成本次转存的文件")
            # 根据真实文件 ID 的当前位置续跑；已改名或已移动的步骤无需重做。
            if current["name"] != target_name:
                drive.rename(source_id, target_name)
            if current["parent_id"] != target_cid:
                drive.move(source_id, target_cid)
            state.set(f"file:{source_id}", {"rank": candidate["rank"], "release": candidate["release"],
                                            "size": current.get("size"), "name": item.get("src_name")})
            final = drive.get_attr(source_id)
            if final["parent_id"] != target_cid or final["name"] != target_name:
                raise RuntimeError("整理读回核验不通过，保留计划供后续重试")
