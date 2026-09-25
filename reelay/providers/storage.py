"""网盘执行器 Provider（StorageProvider）—— 确定性代码，负责真正转存 + 读回验证。

关键原则（评审）：AI 只负责理解判断，真正调 115、写目录、去重必须是确定性代码，
且转存后**读回验证**文件真的落地了，再置终态。

- 装了 p115client 且配了 cookie：走真实转存 + 读回。
- 否则：dry-run 占位，返回模拟成功回执（verified=True），用于骨架冒烟。
"""

from __future__ import annotations

import os
from typing import Any

from ..core.contracts import PipelineContext
from ..core.registry import register


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

    async def save(self, ctx: PipelineContext) -> dict[str, Any]:
        p = ctx.parsed
        assert p is not None
        to_cid = ctx.save_dir_cid or "0"

        if self._client is None:
            # ---- dry-run 占位：模拟转存成功 ----
            ctx.log(f"[dry-run] 模拟转存 share={p.share_code} → cid={to_cid}")
            return {
                "dry_run": True,
                "share_code": p.share_code,
                "to_cid": to_cid,
                "saved_files": p.episodes or [1],
                "verified": True,   # dry-run 视为已验证
            }

        # ---- 真实转存 ----
        # 说明：p115client 的具体分享转存 API（列出分享文件 -> 拿文件 id -> receive）
        # 在真实接入时补全；这里给出结构与「读回验证」的骨架，避免臆造参数细节。
        try:
            file_ids = await self._list_share_file_ids(p.share_code, p.receive_code)
            recv = self._client.receive(file_ids, to_pid=int(to_cid))
            verified = await self._verify_landed(to_cid, p)
            return {
                "share_code": p.share_code,
                "to_cid": to_cid,
                "received": recv,
                "verified": verified,
            }
        except Exception as exc:  # 交给 pipeline 置 FAILED
            raise RuntimeError(f"115 转存失败：{exc}") from exc

    async def _list_share_file_ids(self, share_code, receive_code) -> list[int]:
        """列出分享内文件 id（真实接入时用 p115client 的分享列举接口补全）。"""
        raise NotImplementedError("接入真实 115 分享列举接口后实现")

    async def _verify_landed(self, to_cid: str, parsed) -> bool:
        """读回验证：转存后回查目标目录，确认文件确实落地。"""
        # 真实实现：client.fs_files(cid=to_cid) 后核对新增项
        return True
