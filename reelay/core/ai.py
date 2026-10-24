"""AI 调用基础设施：OpenAI 兼容客户端、结构化输出与调用记录。

所有 AI 能力（解析、偏好决策、转人工前的方案兜底、规则起草、建议措辞……）都经过这里：

- ``make_client()``：按系统配置 ``llm``（地址、模型）与密钥创建客户端；未配 key 或未装 SDK 时
  返回 None，调用方自行降级；使用方继承 ``UsesAI``，每次用到时取，管理台改了立即生效；
- ``complete_json()``：一次结构化调用 —— JSON schema 约束输出，调用方的 ``validate`` 做本地严格校验；
  瞬时错误退避重试，非法输出重试一次；
- 每次请求（含失败）记入 ``llm_calls``：用途、模型、耗时、token 数，以及输出是否通过校验。

AI 只给提议：调用失败统一抛 ``AIError``，由调用方按确定性规则降级或转人工。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any, Callable

from . import runtime, secret_store
from .config import DEFAULT_LLM_MODEL as DEFAULT_MODEL

# 瞬时错误（超时、限流、5xx）的退避重试次数与首次间隔（秒）
RETRIES = 2
RETRY_DELAY = 2.0

_TRANSIENT_ERRORS = {"APITimeoutError", "APIConnectionError", "RateLimitError",
                     "InternalServerError", "TimeoutError", "ConnectionError"}


class AIError(RuntimeError):
    """AI 调用没有得到可用结果；category 是失败类别（异常类型名、refused、invalid、unavailable）。"""

    def __init__(self, category: str, detail: str = "") -> None:
        super().__init__(detail or category)
        self.category = category


log = logging.getLogger("reelay.ai")
_CLIENTS: dict[tuple[str, str], Any] = {}


def client_for(base_url: str, key: str | None = None) -> Any | None:
    """用给定地址与密钥（省略时用已保存的）创建客户端；没有密钥或没装 SDK 时为 None。"""
    key = key or secret_store.get("llm_api_key")
    if not key:
        return None
    try:
        from openai import AsyncOpenAI  # 延迟导入
        # 关掉 SDK 自带的重试：重试只由 request() 负责，否则两层叠加，超时时一次调用最多要发 9 次请求，
        # 调用记录也对不上实际请求。
        return AsyncOpenAI(api_key=key, base_url=base_url or None, max_retries=0)
    except Exception:  # noqa: BLE001 - 未安装 SDK 时按未配置处理
        return None


def make_client() -> tuple[Any | None, str]:
    """返回 (客户端或 None, 模型名)；同一组 key 与地址复用同一个客户端。"""
    model = str(runtime.get("llm.model") or DEFAULT_MODEL)
    key = secret_store.get("llm_api_key")
    if not key:
        return None, model
    base_url = str(runtime.get("llm.base_url") or "")
    client = _CLIENTS.get((key, base_url))
    if client is None:
        client = client_for(base_url)
        if client is None:
            return None, model
        _CLIENTS.clear()  # 换了 key 或地址，旧客户端不再使用
        _CLIENTS[(key, base_url)] = client
    return client, model


class UsesAI:
    """使用 LLM 的扩展继承它：``_client`` / ``_model`` 在用到时按当前配置与密钥取得；
    测试或调用方直接赋值即可覆盖（赋 None 表示不用 AI）。"""

    @property
    def _client(self) -> Any | None:
        if "_client_override" in self.__dict__:
            return self.__dict__["_client_override"]
        return make_client()[0]

    @_client.setter
    def _client(self, value: Any | None) -> None:
        self.__dict__["_client_override"] = value

    @property
    def _model(self) -> str:
        if "_model_override" in self.__dict__:
            return self.__dict__["_model_override"]
        return make_client()[1]

    @_model.setter
    def _model(self, value: str) -> None:
        self.__dict__["_model_override"] = value


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


# 模型拒绝过的推理强度：(模型, 强度)。之后不再对这个模型发送它，免得每次请求都先失败一次
_REJECTED_EFFORTS: set[tuple[str, str]] = set()


def effort_rejected(exc: Exception) -> bool:
    """接口因 reasoning_effort 不被支持而拒绝了请求（HTTP 400 且错误说明提到它）。"""
    return getattr(exc, "status_code", None) == 400 and "reasoning" in str(exc).lower()


async def request(client, *, purpose: str = "other", task_id: str | None = None,
                  **params: Any) -> tuple[Any, float]:
    """发送一次请求，只对瞬时错误退避重试；参数错误、拒绝等立即抛出交给调用方降级。

    系统配置的推理强度（llm.reasoning_effort）被模型拒绝时，去掉它立即重发：一项偏好设置不能让所有
    AI 调用都失败。返回 (响应, 开始时间)：失败的请求在这里记录，成功的由调用方校验输出后再记录结果。
    """
    retries, delay = RETRIES, RETRY_DELAY
    model = str(params.get("model", ""))
    effort = str(runtime.get("llm.reasoning_effort") or "")
    configured = bool(effort) and "reasoning_effort" not in params and (model, effort) not in _REJECTED_EFFORTS
    if configured:
        params["reasoning_effort"] = effort
    attempt = 0
    while True:
        started = time.monotonic()
        try:
            return await client.chat.completions.create(**params), started
        except Exception as exc:
            record(purpose, model, started, outcome="error", error=type(exc).__name__, task_id=task_id)
            if configured and effort_rejected(exc):
                configured = False
                params.pop("reasoning_effort", None)
                _REJECTED_EFFORTS.add((model, effort))
                log.warning("模型 %s 不支持推理强度 %s，已改为不传；请在「设置 → AI 与 TMDB」重新选择", model, effort)
                continue
            if attempt >= retries or not is_transient(exc):
                raise
            await asyncio.sleep(delay * 2 ** attempt)
            attempt += 1


def response_format(schema: dict, name: str, rule: dict | None) -> dict:
    # 显式兼容 JSON mode 时仍执行完整本地校验，不在 API 报错后静默降级格式。
    if (rule or {}).get("llm_response_format") == "json_object":
        return {"type": "json_object"}
    return {"type": "json_schema", "json_schema": {"name": name, "strict": True, "schema": schema}}


async def complete_json(client, *, model: str, purpose: str, messages: list[dict], schema: dict, name: str,
                        validate: Callable[[Any], Any], rule: dict | None = None, task_id: str | None = None,
                        timeout: float = 60, invalid_retries: int = 1) -> tuple[Any, int]:
    """结构化调用；返回 (validate 的返回值, 请求次数)。失败抛 AIError。

    默认超时按识别一条消息定：带推理的模型输出 500–1000 token，实测单条 11–28 秒，并发时更慢。
    """
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
