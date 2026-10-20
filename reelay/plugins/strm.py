"""STRM 生成 —— after_save Hook（排在 Organizer 之后）。

整理完成后，在本地 ``plugins.strm.output_dir`` 下按「目录别名/整理后的相对路径」生成同名 .strm，
内容是 Reelay 的签名播放地址（``plugins.strm.base_url`` 必须是媒体服务器能访问到的 Reelay 地址）。
媒体服务器播放时 Reelay 用 cookie 换取 115 直链并 302 跳转，视频不经过本机。

默认关闭；生成失败只记录在 ``ctx.meta["strm"]``，不影响已完成的转存与整理。
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path

from ..core import runtime
from ..core.auth115 import get_store
from ..core.contracts import PipelineContext
from ..core.io import run_blocking
from ..core.registry import register
from ..core.strm import play_url

log = logging.getLogger("reelay.plugin.strm")


def _segment(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|\x00-\x1f]', " ", str(name or "")).strip(" .") or "_"


@register(kind="hook", stage="after_save", order=30)
class StrmGenerator:
    name = "strm"
    default_enabled = False
    storage_provider = "p115"
    stage = "after_save"
    order = 30
    ui_label = "STRM 生成"
    ui_description = "整理后在本地目录生成 .strm，媒体服务器经 Reelay 签名地址 302 跳转到 115 直链播放。"
    ui_fields = [
        {'path': 'output_dir', 'label': 'STRM 输出目录（本机路径）', 'type': 'string'},
        {'path': 'base_url', 'label': 'Reelay 访问地址（媒体服务器可达，如 http://192.168.1.10:28080）',
         'type': 'string'},
    ]

    def __init__(self) -> None:
        self._dry_run = os.getenv("REELAY_DRY_RUN", "").lower() in {"1", "true", "yes"}
        self._auth = get_store()

    def validate_config(self, cfg: dict) -> None:
        section = runtime.plugin_config(self.name, cfg)
        if not str(section.get("output_dir", "")).strip():
            raise ValueError("启用 STRM 生成时必须配置输出目录（plugins.strm.output_dir）")
        if not re.match(r"https?://[^/\s]+", str(section.get("base_url", ""))):
            raise ValueError("plugins.strm.base_url 必须是 http(s):// 开头的 Reelay 访问地址")

    async def run(self, ctx: PipelineContext) -> bool:
        organize = ctx.ns("organize")
        if self._dry_run or not organize.get("applied"):
            return True
        try:
            section = runtime.plugin_config(self.name)
            written = await run_blocking(self._write, ctx, organize.get("plan") or [], section)
            ctx.ns("strm").update(files=written, error=None)
            ctx.log(f"已生成 {len(written)} 个 STRM 文件")
        except Exception as exc:  # noqa: BLE001 - STRM 是附加产物，失败不改变任务结果
            log.warning("STRM 生成失败：%s", exc)
            ctx.ns("strm")["error"] = str(exc)
        return True

    def _write(self, ctx: PipelineContext, plan: list[dict], section: dict) -> list[str]:
        from p115client import P115FileSystem
        fs = P115FileSystem(self._auth.cookie_client())
        root = Path(section["output_dir"]).expanduser().resolve()
        base = root / _segment(ctx.save_dir_alias or "默认")
        written = []
        for item in plan:
            try:
                attr = fs.get_attr(int(item["src_id"]), refresh=True)
            except Exception:  # noqa: BLE001 - 冲突时被删除的新文件等
                continue
            pickcode = attr.get("pickcode") or attr.get("pick_code") or attr.get("pc")
            if not pickcode:
                continue
            stem = Path(attr.get("name") or item["target_name"]).stem
            folder = base.joinpath(*[_segment(part) for part in item["target_dir"].split("/") if part])
            target = (folder / f"{_segment(stem)}.strm").resolve()
            if not target.is_relative_to(root):
                raise ValueError("STRM 路径越界")
            folder.mkdir(parents=True, exist_ok=True)
            temporary = target.with_suffix(".strm.tmp")
            temporary.write_text(play_url(section["base_url"], str(pickcode)) + "\n", encoding="utf-8")
            temporary.replace(target)
            written.append(str(target.relative_to(root)))
        return written
