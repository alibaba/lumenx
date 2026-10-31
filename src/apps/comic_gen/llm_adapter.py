"""
LLM Adapter - Unified interface for DashScope and OpenAI-compatible APIs.

Supports two providers:
  - dashscope (default): Alibaba Cloud DashScope via OpenAI-compatible endpoint
  - openai: Any OpenAI-compatible API (OpenAI, DeepSeek, Ollama, etc.)

Configuration via environment variables:
  LLM_PROVIDER=dashscope|openai
  DASHSCOPE_API_KEY=...
  OPENAI_API_KEY=...
  OPENAI_BASE_URL=https://api.openai.com/v1
  OPENAI_MODEL=gpt-4o
"""
import os
import logging
import math
from dataclasses import dataclass
from types import MappingProxyType
from typing import Dict, List, Optional, Any, Mapping

from ...utils.endpoints import get_provider_base_url
from ...core.token_usage import record_token_usage

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LLMChatResult:
    content: str
    requested_model: str
    provider_model: Optional[str]
    prompt_tokens: Optional[int]
    completion_tokens: Optional[int]
    total_tokens: Optional[int]


class LLMAdapter:
    """Unified LLM call interface supporting DashScope and OpenAI-compatible APIs."""

    def __init__(self, *, env: Mapping[str, str] | None = None):
        # Explicit app configuration is captured before lazy client creation.
        # Only standalone callers retain process-environment compatibility.
        self._env = os.environ if env is None else MappingProxyType(dict(env))
        self.provider = self._env.get("LLM_PROVIDER", "dashscope").lower()
        self._client = None
        logger.info(f"LLM Adapter initialized with provider: {self.provider}")

    @property
    def is_configured(self) -> bool:
        if self.provider == "openai":
            return bool(self._env.get("OPENAI_API_KEY"))
        return bool(self._env.get("DASHSCOPE_API_KEY"))

    def _get_client(self):
        """Get or create the OpenAI-compatible client (lazy, cached)."""
        if self._client is None:
            if not self.is_configured:
                # OpenAI(api_key=None) can read OPENAI_API_KEY implicitly.
                raise RuntimeError("LLM provider credentials are not configured")
            try:
                from openai import OpenAI
            except ImportError:
                raise RuntimeError(
                    "openai package not installed. Run: pip install openai>=1.0.0"
                )

            if self.provider == "openai":
                self._client = OpenAI(
                    api_key=self._env.get("OPENAI_API_KEY"),
                    base_url=self._env.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
                )
            else:
                # DashScope uses OpenAI-compatible endpoint
                self._client = OpenAI(
                    api_key=self._env.get("DASHSCOPE_API_KEY"),
                    base_url=f"{get_provider_base_url('DASHSCOPE', env=self._env)}/compatible-mode/v1",
                )
        return self._client

    # DashScope qwen 系列：首选 qwen3.7-plus（最新），不可用时回退到 qwen3.6-plus，
    # 最终回退到 qwen-plus alias（始终指向最新稳定通用版）。
    # 维护 fallback chain 而不是硬写一个名字，避免新版本上下线时整条 LLM 链断掉。
    _DASHSCOPE_MODEL_FALLBACK_CHAIN = ["qwen3.7-plus", "qwen3.6-plus", "qwen-plus"]

    def _get_default_model(self) -> str:
        if self.provider == "openai":
            return self._env.get("OPENAI_MODEL", "gpt-4o")
        return self._DASHSCOPE_MODEL_FALLBACK_CHAIN[0]

    def chat(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        response_format: Optional[Dict[str, str]] = None,
        *,
        timeout: Optional[float] = None,
        max_retries: Optional[int] = None,
    ) -> str:
        """
        Send a chat completion request and return the response content.

        Args:
            messages: List of {"role": ..., "content": ...} dicts
            model: Model name override (uses provider default if None)
            response_format: Optional {"type": "json_object"} constraint
            timeout: Optional provider request timeout. Existing callers keep SDK defaults.
            max_retries: Optional provider retry count. Existing callers keep SDK defaults.

        Returns:
            The assistant's response content as a string.

        Raises:
            RuntimeError: If the API call fails.
        """
        client = self._get_client()

        # 显式 model override 路径：单次尝试，失败就抛。
        if model:
            return self._chat_once(client, model, messages, response_format, timeout=timeout, max_retries=max_retries)

        # Provider 默认路径：DashScope 走 fallback chain，OpenAI 单次尝试。
        if self.provider == "openai":
            return self._chat_once(
                client,
                self._get_default_model(),
                messages,
                response_format,
                timeout=timeout,
                max_retries=max_retries,
            )

        last_err: Optional[Exception] = None
        for idx, candidate in enumerate(self._DASHSCOPE_MODEL_FALLBACK_CHAIN):
            try:
                return self._chat_once(
                    client,
                    candidate,
                    messages,
                    response_format,
                    timeout=timeout,
                    max_retries=max_retries,
                )
            except RuntimeError as e:
                # 仅在 "模型不存在 / 不可用" 类错误时回退；其他错误（鉴权、限流、网络）
                # 直接抛，不浪费第二次重试。判定关键字宽松匹配 DashScope 文案。
                msg = str(e).lower()
                is_model_unavailable = any(k in msg for k in (
                    "model not found", "invalidmodel", "model_not_found",
                    "no such model", "not supported", "modelnotfound", "404",
                ))
                last_err = e
                if is_model_unavailable and idx < len(self._DASHSCOPE_MODEL_FALLBACK_CHAIN) - 1:
                    next_candidate = self._DASHSCOPE_MODEL_FALLBACK_CHAIN[idx + 1]
                    logger.warning(
                        "DashScope model %s unavailable (%s); falling back to %s",
                        candidate, e, next_candidate,
                    )
                    continue
                raise
        # 理论上不可达（最后一次失败已 raise），保留兜底
        raise last_err if last_err else RuntimeError("DashScope: no models available")

    def chat_result(
        self,
        messages: List[Dict[str, str]],
        *,
        model: str,
        max_completion_tokens: int,
        timeout: float,
        response_format: Optional[Dict[str, str]] = None,
    ) -> LLMChatResult:
        """
        Send one bounded chat request and return typed content plus provider metadata.

        This opt-in path is intentionally stricter than legacy chat():
        callers must provide the model, output cap, and timeout; SDK retries are
        disabled; provider fallback is not attempted.
        """
        self._validate_chat_result_args(
            messages=messages,
            model=model,
            max_completion_tokens=max_completion_tokens,
            timeout=timeout,
            response_format=response_format,
        )
        try:
            client = self._get_client()
            response = self._chat_result_once(
                client,
                model,
                messages,
                max_completion_tokens=max_completion_tokens,
                timeout=timeout,
                response_format=response_format,
            )
        except Exception as e:
            provider_label = "DashScope" if self.provider != "openai" else "OpenAI"
            raise RuntimeError(f"{provider_label} bounded chat failed") from e

        try:
            content = self._extract_result_content(response)
            usage = self._extract_result_usage(response)
        except RuntimeError:
            raise
        provider_model = self._get_response_value(response, "model")
        if not isinstance(provider_model, str) or not provider_model.strip():
            provider_model = None
        record_token_usage(
            operation="llm.chat_result",
            provider=self.provider,
            requested_model=model,
            provider_model=provider_model,
            prompt_tokens=usage["prompt_tokens"],
            completion_tokens=usage["completion_tokens"],
            total_tokens=usage["total_tokens"],
        )
        return LLMChatResult(
            content=content,
            requested_model=model,
            provider_model=provider_model,
            prompt_tokens=usage["prompt_tokens"],
            completion_tokens=usage["completion_tokens"],
            total_tokens=usage["total_tokens"],
        )

    def _chat_result_once(
        self,
        client,
        model: str,
        messages: List[Dict[str, str]],
        *,
        max_completion_tokens: int,
        timeout: float,
        response_format: Optional[Dict[str, str]],
    ):
        kwargs: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_completion_tokens": max_completion_tokens,
        }
        if response_format is not None:
            kwargs["response_format"] = response_format
        request_client = client.with_options(timeout=timeout, max_retries=0)
        return request_client.chat.completions.create(**kwargs)

    def _chat_once(
        self,
        client,
        model: str,
        messages: List[Dict[str, str]],
        response_format: Optional[Dict[str, str]],
        *,
        timeout: Optional[float] = None,
        max_retries: Optional[int] = None,
    ) -> str:
        kwargs: Dict[str, Any] = {
            "model": model,
            "messages": messages,
        }
        if response_format:
            kwargs["response_format"] = response_format

        request_client = client
        if timeout is not None or max_retries is not None:
            options: Dict[str, Any] = {}
            if timeout is not None:
                options["timeout"] = timeout
            if max_retries is not None:
                options["max_retries"] = max_retries
            request_client = client.with_options(**options)

        try:
            response = request_client.chat.completions.create(**kwargs)
            self._record_usage(response, model)
            return response.choices[0].message.content
        except Exception as e:
            provider_label = "DashScope" if self.provider != "openai" else "OpenAI"
            raise RuntimeError(f"{provider_label} API error: {e}") from e

    def _record_usage(self, response, requested_model: str) -> None:
        usage = self._extract_result_usage(response)
        record_token_usage(
            operation="llm.chat",
            provider=self.provider,
            requested_model=requested_model,
            provider_model=self._get_response_value(response, "model"),
            prompt_tokens=usage["prompt_tokens"],
            completion_tokens=usage["completion_tokens"],
            total_tokens=usage["total_tokens"],
        )

    def _validate_chat_result_args(
        self,
        *,
        messages: List[Dict[str, str]],
        model: str,
        max_completion_tokens: int,
        timeout: float,
        response_format: Optional[Dict[str, str]],
    ) -> None:
        if not isinstance(messages, list) or not messages:
            raise ValueError("messages must be a non-empty list")
        allowed_roles = {"system", "user", "assistant"}
        for message in messages:
            if not isinstance(message, dict):
                raise ValueError("messages must contain role/content records")
            if set(message.keys()) != {"role", "content"}:
                raise ValueError("messages must contain only role and content")
            role = message["role"]
            content = message["content"]
            if role not in allowed_roles:
                raise ValueError("message role is not supported")
            if not isinstance(content, str):
                raise ValueError("message content must be a string")
        if not isinstance(model, str) or not model.strip():
            raise ValueError("model must be a non-empty string")
        if not isinstance(max_completion_tokens, int) or isinstance(max_completion_tokens, bool) or max_completion_tokens <= 0:
            raise ValueError("max_completion_tokens must be a positive integer")
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be a positive finite number")
        if response_format is None:
            return
        if not isinstance(response_format, dict):
            raise ValueError("response_format must be a mapping")
        if set(response_format.keys()) != {"type"} or response_format["type"] not in {"json_object", "text"}:
            raise ValueError("response_format must be {'type': 'json_object'} or {'type': 'text'}")

    def _extract_result_content(self, response) -> str:
        try:
            choices = self._get_response_value(response, "choices")
            first = choices[0]
            message = self._get_response_value(first, "message")
            content = self._get_response_value(message, "content")
        except Exception as e:
            raise RuntimeError("LLM bounded chat returned malformed response") from e
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("LLM bounded chat returned malformed response")
        return content

    def _extract_result_usage(self, response) -> Dict[str, Optional[int]]:
        usage = self._get_response_value(response, "usage")
        result: Dict[str, Optional[int]] = {}
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            value = self._get_response_value(usage, key) if usage is not None else None
            if value is None:
                result[key] = None
                continue
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise RuntimeError("LLM bounded chat returned malformed usage")
            result[key] = value
        return result

    def _get_response_value(self, item, key: str):
        if isinstance(item, dict):
            return item.get(key)
        return getattr(item, key, None)
