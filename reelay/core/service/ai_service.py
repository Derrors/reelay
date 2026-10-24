"""AI 服务：调用记录、获取模型列表、测试连通性、检测推理强度，以及一句话偏好起草规则。"""
from __future__ import annotations

import time
import re
import asyncio
import logging
from typing import Any


from .. import ai, runtime, secret_store
from .base import ConflictError, ServiceBase

log = logging.getLogger("reelay.service")


class AIServiceMixin(ServiceBase):
    """AI 服务设置。"""

    def llm_usage(self, days: int = 30) -> dict[str, Any]:
        return self.store.llm_usage(time.time() - days * 86400)

    # ------------------------------------------------------------------ #
    # AI 服务：获取模型列表、测试连通性。用表单里的地址（可以还没保存），密钥用已保存的
    # ------------------------------------------------------------------ #
    @staticmethod
    def _llm_client_for(base_url: str | None, api_key: str | None = None) -> tuple[Any, str, str]:
        """（客户端, 接口地址, 用的密钥）。已保存的密钥只发给已保存的地址：试别的地址要带上那个地址的密钥。"""
        saved = str(runtime.get("llm.base_url") or "").strip().rstrip("/")
        url = (base_url if base_url is not None else saved).strip().rstrip("/")
        if url and not re.match(r"https?://[^/\s]+", url):
            raise ValueError("接口地址需以 http:// 或 https:// 开头，留空使用 OpenAI 官方地址")
        key = (api_key or "").strip()
        if not key and url != saved:
            raise ConflictError("接口地址和已保存的不同：请填写这个地址的密钥（已保存的密钥只会发给已保存的地址）")
        key = key or secret_store.get("llm_api_key") or ""
        client = ai.client_for(url, key) if key else None
        if client is None:
            raise ConflictError("请先填写 AI 接口密钥")
        return client, url, key

    @staticmethod
    def _llm_error(exc: Exception, key: str = "") -> str:
        """SDK 的错误说明里可能带地址，不会带密钥；仍按用到的密钥脱敏后再返回。"""
        status = getattr(exc, "status_code", None)
        body = getattr(exc, "body", None)
        detail = body.get("message") if isinstance(body, dict) and isinstance(body.get("message"), str) else None
        if detail is None and isinstance(body, dict) and isinstance(body.get("error"), dict):
            detail = body["error"].get("message")
        text = f"{type(exc).__name__}{f'（HTTP {status}）' if status else ''}：{detail or exc}"
        key = key or secret_store.get("llm_api_key")
        return (text.replace(key, "***") if key else text)[:300]

    async def llm_models(self, base_url: str | None = None, api_key: str | None = None) -> dict[str, Any]:
        """接口提供的模型列表（OpenAI 兼容的 GET /models）。"""
        client, _, key = self._llm_client_for(base_url, api_key)
        try:
            page = await asyncio.wait_for(client.models.list(), timeout=20)
        except Exception as exc:  # noqa: BLE001 - 原因交给用户判断（地址、密钥或网关不支持列模型）
            raise ConflictError(f"获取模型列表失败：{self._llm_error(exc, key)}") from None
        ids = sorted({str(getattr(m, "id", "")) for m in getattr(page, "data", None) or [] if getattr(m, "id", None)})
        return {"models": ids}

    async def llm_test(self, *, base_url: str | None = None, model: str | None = None,
                       reasoning_effort: str | None = None, api_key: str | None = None) -> dict[str, Any]:
        """用一次很短的对话测试地址、密钥、模型与推理强度能否使用；不写入调用记录。"""
        from ..config import REASONING_EFFORTS
        client, _, key = self._llm_client_for(base_url, api_key)
        model = (model or str(runtime.get("llm.model") or "")).strip()
        if not model:
            raise ValueError("请先填写模型")
        effort = (reasoning_effort if reasoning_effort is not None else str(runtime.get("llm.reasoning_effort") or ""))
        if effort not in REASONING_EFFORTS:
            raise ValueError("推理强度无效")
        return await self._llm_try(client, model, effort, key=key)

    async def _llm_try(self, client, model: str, effort: str, timeout: float = 60, key: str = "") -> dict[str, Any]:
        params: dict[str, Any] = {"model": model, "temperature": 0,
                                  "messages": [{"role": "user", "content": "只回复两个字：正常"}]}
        if effort:
            params["reasoning_effort"] = effort
        started = time.monotonic()
        try:
            response = await client.chat.completions.create(**params, timeout=timeout)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "model": model, "error": self._llm_error(exc, key),
                    "effort_rejected": ai.effort_rejected(exc), "seconds": round(time.monotonic() - started, 1)}
        usage = getattr(response, "usage", None)
        reply = ((response.choices[0].message.content or "") if response.choices else "").strip()
        return {"ok": True, "model": getattr(response, "model", None) or model, "reply": reply[:100],
                "seconds": round(time.monotonic() - started, 1),
                "prompt_tokens": getattr(usage, "prompt_tokens", None),
                "completion_tokens": getattr(usage, "completion_tokens", None)}

    def llm_efforts(self, base_url: str | None = None, model: str | None = None) -> dict[str, Any]:
        """上次检测到的这个地址与模型支持的推理强度；没检测过时 efforts 为 None。"""
        url = (base_url if base_url is not None else str(runtime.get("llm.base_url") or "")).strip().rstrip("/")
        model = (model or str(runtime.get("llm.model") or "")).strip()
        return {"model": model, **(self.store.state_get("llm_efforts", f"{url}|{model}") or {"efforts": None})}

    async def probe_llm_efforts(self, base_url: str | None = None, model: str | None = None,
                                api_key: str | None = None) -> dict[str, Any]:
        """逐个试推理强度（并发），记下这个模型支持哪些。只有接口明确因推理强度拒绝的才算不支持，
        超时、限流等其他失败记为「未知」，不让用户误以为不能选。"""
        from ..config import REASONING_EFFORTS
        client, url, key = self._llm_client_for(base_url, api_key)
        model = (model or str(runtime.get("llm.model") or "")).strip()
        if not model:
            raise ValueError("请先选择模型")
        efforts = [e for e in REASONING_EFFORTS if e]
        results = await asyncio.gather(*(self._llm_try(client, model, e, timeout=45, key=key) for e in efforts))
        out = {e: ("supported" if r["ok"] else "unsupported" if r.get("effort_rejected") else "unknown")
               for e, r in zip(efforts, results, strict=True)}
        unsupported = next((r["error"] for r in results if r.get("effort_rejected")), None)
        record = {"efforts": out, "note": unsupported, "checked_at": time.time()}
        self.store.state_set("llm_efforts", f"{url}|{model}", record)
        return {"model": model, **record}

    # ------------------------------------------------------------------ #
    # 一句话偏好 → 确定性规则（AI 起草，配置校验，用户确认后经配置接口写入）
    # ------------------------------------------------------------------ #
    async def compile_rules(self, text: str, scope: str = "defaults") -> dict[str, Any]:
        from ..compiler import compile_rules
        client, model = self._require_ai()
        cfg = self.get_config()
        if self.pipeline.config_error:
            raise ConflictError("配置文件当前无效，先修复原始文件")
        with self.ai_scope():
            try:
                return await compile_rules(client, model, cfg=cfg, scope=scope or "defaults", request=text,
                                           check=self.pipeline.reg.validate_config)
            except ai.AIError as exc:
                raise ConflictError(f"AI 起草规则失败（{exc.category}），请稍后重试") from None
