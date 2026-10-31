"""模拟模式：内置样例消息源与内存网盘，不访问网络、不需要任何账号。

``python -m reelay.main --dry-run`` 用它们走完「监听 → 识别 → 审批 → 转存 → 整理」的全过程：

- ``DryRunSource``（source/dryrun）：吐几条样例消息（剧集、广告、电影）后结束；
- ``DryRunStorage``（storage/dryrun）：分享内容按识别结果生成，转存与整理都在 ``MemoryDrive`` 里真实执行并读回
  核验，与 115 存储走同一套选集、核验与整理逻辑，只是网盘在内存里。

两者不自动注册（正式运行时不能选到，否则「转存成功」的资源只在内存里），由 ``install`` 加进模拟运行的注册目录。
"""
from __future__ import annotations

import asyncio
import re
import threading
from typing import Any, Awaitable, Callable

from ..core.contracts import PipelineContext, RawMessage, TransferUncertain
from ..core.media import resolution_name
from .storage import ShareStorage, _file_record, _landing_progress, _verified_files, describe_ed2k, describe_share

EmitFn = Callable[[RawMessage], Awaitable[None]]

SAMPLES = [
    # 一条正常剧集资源
    {
        "source": "@example_tv_bot",
        "text": (
            "📺 法医秦明之龙番往事 (2026) 第1季 更新至第8集 ✨4K WEB-DL\n"
            "🌍 地区： China\n💾 大小： 15.12 GB\n🔗 链接： https://115.com/s/swabc123?password=x1y2"
        ),
        "urls": ["https://115.com/s/swabc123?password=x1y2"],
    },
    # 一条广告（应被过滤）
    {
        "source": "@example_tv_bot",
        "text": "🔥 招商广告：更多资源关注本频道，点击进群 👉 t.me/xxx",
        "urls": ["https://t.me/xxx"],
    },
    # 一条电影资源
    {
        "source": "@example_movie_bot",
        "text": "🎬 流浪地球2 (2023) 4K HDR\n💾 62 GB\n🔗 https://115.com/s/mv2gala?password=abcd",
        "urls": ["https://115.com/s/mv2gala?password=abcd"],
    },
]


class DryRunSource:
    """内置模拟源：发完样例消息就结束（真实源是长驻的）。"""
    name = "dryrun"

    async def listen(self, emit: EmitFn) -> None:
        for i, s in enumerate(SAMPLES):
            await emit(RawMessage(source=s["source"], msg_id=f"dry-{i}", text=s["text"], urls=s["urls"]))
            await asyncio.sleep(0.05)


class MemoryDrive:
    """内存里的网盘：目录与文件都有 int ID，接口与整理用的 ``P115Drive`` 相同。没见过的目录 ID 当作空目录。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._items: dict[int, dict[str, Any]] = {0: self._entry(0, 0, "", is_dir=True)}
        self._next = 1000

    @staticmethod
    def _entry(fid: int, parent: int, name: str, *, is_dir: bool, size: int | None = None,
               sha1: str | None = None) -> dict[str, Any]:
        return {"id": fid, "name": name, "is_dir": is_dir, "size": 0 if is_dir else size, "sha1": sha1,
                "parent_id": parent}

    def _dir(self, cid: int) -> int:
        cid = int(cid)
        with self._lock:
            if cid not in self._items:
                self._items[cid] = self._entry(cid, 0, str(cid), is_dir=True)
            return cid

    def add(self, parent: int, name: str, *, size: int | None = None, sha1: str | None = None) -> int:
        with self._lock:
            self._next += 1
            self._items[self._next] = self._entry(self._next, self._dir(parent), name, is_dir=False, size=size,
                                                  sha1=sha1)
            return self._next

    def iterdir(self, cid: int) -> list[dict]:
        cid = self._dir(cid)
        with self._lock:
            return [dict(x) for x in self._items.values() if x["parent_id"] == cid and x["id"] != cid]

    def get_attr(self, fid: int) -> dict:
        with self._lock:
            if int(fid) not in self._items:
                raise FileNotFoundError(f"文件不存在：{fid}")
            return dict(self._items[int(fid)])

    def mkdir(self, parent: int, name: str) -> int:
        with self._lock:
            self._next += 1
            self._items[self._next] = self._entry(self._next, self._dir(parent), name, is_dir=True)
            return self._next

    def rename(self, fid: int, name: str) -> None:
        with self._lock:
            self._items[int(fid)]["name"] = name

    def move(self, fid: int, cid: int) -> None:
        with self._lock:
            self._items[int(fid)]["parent_id"] = self._dir(cid)

    def delete(self, ids: list[int]) -> None:
        with self._lock:
            pending = [int(x) for x in ids]
            while pending:
                fid = pending.pop()
                self._items.pop(fid, None)
                pending += [x["id"] for x in self._items.values() if x["parent_id"] == fid and x["id"] != fid]

    def path(self, cid: int) -> str:
        parts, cid = [], int(cid)
        with self._lock:
            while cid and cid in self._items:
                parts.append(self._items[cid]["name"])
                cid = self._items[cid]["parent_id"]
        return "/" + "/".join(reversed(parts))

    def tree(self, cid: int = 0, indent: str = "") -> list[str]:
        """目录树（模拟运行结束时打印）；以「.」开头的暂存目录不列出。"""
        lines = []
        for item in sorted(self.iterdir(cid), key=lambda x: (not x["is_dir"], x["name"])):
            if item["name"].startswith("."):
                continue
            lines.append(f"{indent}{item['name']}{'/' if item['is_dir'] else ''}")
            if item["is_dir"]:
                lines += self.tree(item["id"], indent + "    ")
        return lines


class _SampleShare:
    """按识别结果生成的分享内容，接口同 P115ShareFileSystem 的 iterdir。"""

    def __init__(self, parsed) -> None:
        title = re.sub(r'[\\/:*?"<>|]', " ", parsed.title or "影片").strip()
        resolution = resolution_name(parsed.resolution) or "1080p"
        if parsed.media_type in ("tv", "anime") or parsed.episodes:
            season = parsed.season if parsed.season is not None else 1
            names = [f"{title}.S{season:02d}E{ep:02d}.{resolution}.mkv" for ep in parsed.episodes or [1]]
        else:
            names = [f"{title}.{parsed.year or ''}.{resolution}.mkv".replace("..", ".")]
        self.files = [{"id": f"share-{i}", "name": name, "size": (i + 1) << 30, "sha1": None, "is_dir": False}
                      for i, name in enumerate(names)]

    def iterdir(self, cid: int = 0) -> list[dict]:
        return [dict(x) for x in self.files] if not cid else []


class DryRunStorage(ShareStorage):
    """模拟存储：转存与整理都在内存网盘里执行并读回核验，不访问网盘。"""
    name = "dryrun"
    ui_label = "模拟存储（不访问网盘）"

    def __init__(self) -> None:
        self.drive = MemoryDrive()

    def ready(self) -> bool:
        return True

    def dir_path(self, cid: str) -> str:
        return self.drive.path(int(cid))

    def library(self) -> MemoryDrive:
        return self.drive

    def inspect_share(self, parsed, *, describe: bool) -> dict[str, Any]:
        if not parsed.share_code and parsed.ed2k:
            return describe_ed2k(parsed)
        if not describe:
            return {"status": "available"}
        return describe_share(_SampleShare(parsed), parsed)

    def browse(self, cid: str, offset: int = 0, limit: int = 100) -> dict[str, Any]:
        dirs = sorted((x for x in self.drive.iterdir(int(cid)) if x["is_dir"]), key=lambda x: x["name"])
        page = dirs[offset:offset + limit]
        return {"items": [{"id": str(x["id"]), "name": x["name"], "pid": str(cid)} for x in page], "cid": cid,
                "path": [{"id": "0", "name": "根目录"}], "total_entries": len(dirs), "via": "dryrun",
                "next_offset": offset + len(page) if offset + len(page) < len(dirs) else None}

    async def prepare(self, ctx: PipelineContext) -> dict[str, Any]:
        selection = await self.select(ctx, _SampleShare(ctx.parsed).iterdir())
        parent, name = int(ctx.save_dir_cid), f".reelay-{ctx.task_id}"
        stage = next((x["id"] for x in self.drive.iterdir(parent) if x["is_dir"] and x["name"] == name), None)
        return {"to_cid": parent, "staging_cid": stage or self.drive.mkdir(parent, name), **selection}

    def _landed(self, manifest: dict) -> list[dict]:
        return [_file_record(x) for x in self.drive.iterdir(manifest["staging_cid"])]

    async def save(self, ctx: PipelineContext) -> dict[str, Any]:
        manifest = ctx.ns("storage")["manifest"]
        if not self._landed(manifest):  # 暂存目录非空时只核验，绝不追加一份
            for item in manifest["files"]:
                self.drive.add(manifest["staging_cid"], item["name"], size=item.get("size"), sha1=item.get("sha1"))
        return await self.reconcile(ctx)

    async def reconcile(self, ctx: PipelineContext) -> dict[str, Any]:
        manifest = ctx.ns("storage")["manifest"]
        return self._receipt(manifest, _verified_files(manifest["files"], self._landed(manifest)))

    async def reconcile_partial(self, ctx: PipelineContext) -> dict[str, Any]:
        manifest = ctx.ns("storage")["manifest"]
        progress = _landing_progress(manifest["files"], self._landed(manifest))
        if not progress:
            raise TransferUncertain("暂存目录里的文件与转存清单对不上，不能按已到的文件继续")
        return {**self._receipt(manifest, progress["landed"]), "missing": progress["missing"]}

    async def abandon(self, ctx: PipelineContext) -> None:
        self.drive.delete([int(ctx.ns("storage")["manifest"]["staging_cid"])])

    async def discard(self, ctx: PipelineContext) -> None:
        stage = (ctx.ns("storage").get("manifest") or {}).get("staging_cid")
        if isinstance(stage, int) and not self.drive.iterdir(stage):
            self.drive.delete([stage])


def install(registry) -> DryRunStorage:
    """把模拟源与模拟存储加进注册目录（模拟运行与测试用），返回模拟存储（它的 drive 是内存网盘）。"""
    storage = DryRunStorage()
    registry.add_provider("source", DryRunSource())
    registry.add_provider("storage", storage)
    return storage
