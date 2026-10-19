"""AI 调用基础设施：OpenAI 兼容客户端、结构化输出与调用记录。

所有 AI 能力（解析、偏好决策、转人工前的方案兜底、规则起草、建议措辞……）都经过这里：

- ``make_client()``：按环境变量创建客户端；未配 key、未装 SDK 或模拟模式返回 None，调用方自行降级；
- ``complete_json()``：一次结构化调用 —— JSON schema 约束输出，调用方的 ``validate`` 做本地严格校验；
  瞬时错误退避重试，非法输出重试一次；
- 每次请求（含失败）记入 ``llm_calls``：用途、模型、耗时、token 数，以及输出是否通过校验。

AI 只给提议：调用失败统一抛 ``AIError``，由调用方按确定性规则降级或转人工。
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from typing import Any, Callable

from . import runtime

DEFAULT_MODEL = "gpt-4o-mini"

_TRANSIENT_ERRORS = {"APITimeoutError", "APIConnectionError", "RateLimitError",
                     "InternalServerError", "TimeoutError", "ConnectionError"}


class AIError(RuntimeError):
    """AI 调用没有得到可用结果；category 是失败类别（异常类型名、refused、invalid、unavailable）。"""

    def __init__(self, category: str, detail: str = "") -> None:
        super().__init__(detail or category)
        self.category = category


def dry_run() -> bool:
    return os.getenv("REELAY_DRY_RUN", "").lower() in {"1", "true", "yes"}


def make_client() -> tuple[Any | None, str]:
    """返回 (客户端或 None, 模型名)。每个使用方各持一份，测试可直接替换 ``_client``。"""
    model = os.getenv("REELAY_LLM_MODEL", DEFAULT_MODEL)
    if dry_run():
        return None, model
    key = os.getenv("REELAY_LLM_API_KEY")
    if not key:
        return None, model
    try:
        from openai import AsyncOpenAI  # 延迟导入
        return AsyncOpenAI(api_key=key, base_url=os.getenv("REELAY_LLM_BASE_URL") or None), model
    except Exception:  # noqa: BLE001 - 未安装 SDK 时按未配置处理
        return None, model


def is_transient(exc: Exception) -> bool:
    status = getattr(exc, "status_code", None)
    return (type(exc).__name__ in _TRANSIENT_ERRORS or isinstance(exc, (asyncio.TimeoutError, ConnectionError))
            or (type(status) is int and (status == 429 or status >= 500)))


def record(purpose: str, model: str, started: float, *, outcome: str, error: str = "",
           usage: Any = None, task_id: str | None = None) -> None:
    """记一次请求；不在管线或服务作用域内（如单元测试直接调用）时忽略。"""
    try:
        services = runtime.services()
    except RuntimeError:
        return
    services.store.record_llm_call(
        purpose=purpose, model=model, task_id=task_id, outcome=outcome, error=error[:200] or None,
        duration_ms=int((time.monotonic() - started) * 1000),
        prompt_tokens=_tokens(usage, "prompt_tokens"), completion_tokens=_tokens(usage, "completion_tokens"))


def _tokens(usage: Any, name: str) -> int | None:
    value = getattr(usage, name, None) if usage is not None else None
    return value if type(value) is int else None


async def request(client, *, purpose: str = "other", task_id: str | None = None,
                  **params: Any) -> tuple[Any, float]:
    """发送一次请求，只对瞬时错误退避重试；参数错误、拒绝等立即抛出交给调用方降级。

    返回 (响应, 开始时间)：失败的请求在这里记录，成功的由调用方校验输出后再记录结果。
    """
    retries = max(0, int(os.getenv("REELAY_LLM_RETRIES", "2")))
    delay = max(0.0, float(os.getenv("REELAY_LLM_RETRY_DELAY", "2")))
    model = str(params.get("model", ""))
    for attempt in range(retries + 1):
        started = time.monotonic()
        try:
            return await client.chat.completions.create(**params), started
        except Exception as exc:
            record(purpose, model, started, outcome="error", error=type(exc).__name__, task_id=task_id)
            if attempt >= retries or not is_transient(exc):
                raise
            await asyncio.sleep(delay * 2 ** attempt)
    raise AssertionError("unreachable")


def response_format(schema: dict, name: str, rule: dict | None) -> dict:
    # 显式兼容 JSON mode 时仍执行完整本地校验，不在 API 报错后静默降级格式。
    if (rule or {}).get("llm_response_format") == "json_object":
        return {"type": "json_object"}
    return {"type": "json_schema", "json_schema": {"name": name, "strict": True, "schema": schema}}


async def complete_json(client, *, model: str, purpose: str, messages: list[dict], schema: dict, name: str,
                        validate: Callable[[Any], Any], rule: dict | None = None, task_id: str | None = None,
                        timeout: float = 30, invalid_retries: int = 1) -> tuple[Any, int]:
    """结构化调用；返回 (validate 的返回值, 请求次数)。失败抛 AIError。"""
    if client is None:
        raise AIError("unavailable")
    attempts = 0
    while True:
        attempts += 1
        try:
            response, started = await request(
                client, purpose=purpose, task_id=task_id, model=model, messages=messages,
                response_format=response_format(schema, name, rule), temperature=0, timeout=timeout)
        except Exception as exc:
            # 不保留 SDK 异常正文（可能带网关凭证或请求头），只留类别。
            raise AIError(type(exc).__name__) from None
        usage = getattr(response, "usage", None)
        message = response.choices[0].message
        if getattr(message, "refusal", None):
            record(purpose, model, started, outcome="refused", usage=usage, task_id=task_id)
            raise AIError("refused")
        try:
            result = validate(json.loads(message.content or "{}"))
        except (ValueError, TypeError, KeyError) as exc:
            record(purpose, model, started, outcome="invalid", error=str(exc), usage=usage, task_id=task_id)
            if attempts > invalid_retries:
                raise AIError("invalid", str(exc)) from None
            continue
        record(purpose, model, started, outcome="ok", usage=usage, task_id=task_id)
        return result, attempts


# --------------------------------------------------------------------------- #
# 交给 AI 与写入样本前的脱敏
# --------------------------------------------------------------------------- #
_URL_RE = re.compile(r"https?://\S+|115(?:cdn)?\.(?:com|cn)/s/\S+")
_CODE_RE = re.compile(r"((?:访问码|password|pwd|提取码)[:：]?\s*)[0-9a-zA-Z]{4}", re.I)


def scrub(text: str | None) -> str:
    """去掉链接与访问码：样本与提示词只需要正文语义，不需要分享凭证。"""
    return _CODE_RE.sub(r"\1****", _URL_RE.sub("<链接>", text or "")).strip()


def text_field(value: Any, *, limit: int = 2000, label: str = "文本") -> str:
    if type(value) is not str or len(value) > limit:
        raise ValueError(f"{label}必须是不超过 {limit} 字的字符串")
    return value.strip()


def string_list(value: Any, *, limit: int = 50, item_limit: int = 100, label: str = "列表") -> list[str]:
    if type(value) is not list or len(value) > limit or any(
            type(item) is not str or not item.strip() or len(item) > item_limit for item in value):
        raise ValueError(f"{label}必须是非空字符串数组")
    seen, out = set(), []
    for item in value:
        item = item.strip()
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out
