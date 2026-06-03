"""LLM Adapter — multi-provider chat client surface.

Two layers in this module:

  ProviderClient (ABC)            ← v1.4 Batch 4
    Concrete subclasses dispatch to specific provider SDKs:
      - ChatCompletionsClient — OpenAI-compatible /chat/completions
        (DashScope's compatible-mode, OpenAI itself, DeepSeek, Ollama).
      - AnthropicMessagesClient — Anthropic /v1/messages SDK; lazy-imports
        the `anthropic` Python package and surfaces a clear error if not
        installed.
      - CodexResponsesClient — placeholder for v1.5 OpenAI Responses
        integration; raises NotImplementedError on chat()/stream_chat().

    All three return ChatResult / yield StreamChunk so the agent loop can
    extract usage records without a second round-trip.

  LLMAdapter                      ← legacy back-compat shim
    Pre-1.4 callers (storyboard.py, llm.py, audio.py, etc.) call
    `LLMAdapter().chat(...)` / `.stream_chat(...)` and expect the bare
    string response shape. The shim now delegates to a ChatCompletionsClient
    under the hood so its single-string interface continues to work without
    modification.

Configuration via environment variables:
  LLM_PROVIDER=dashscope|openai            # legacy LLMAdapter shim only
  ATELIER_AGENT_PROVIDER=dashscope|openai|anthropic   # v1.4 agent default
  DASHSCOPE_API_KEY=...
  ALIBABA_API_KEY=...                      # alias for DashScope
  OPENAI_API_KEY=...
  OPENAI_BASE_URL=https://api.openai.com/v1
  OPENAI_MODEL=gpt-4o
  ANTHROPIC_API_KEY=...
  DASHSCOPE_AGENT_MODEL=qwen-plus
"""
from __future__ import annotations

import logging
import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional

from ...utils.endpoints import get_provider_base_url

logger = logging.getLogger(__name__)


# ── v1.4 Batch 4 — adapter-layer types ────────────────────────────────────
# Distinct from atelier_agent's iter event union; ProviderClient is a
# generic chat surface that knows nothing about Atelier turns or planner
# packages. The agent loop translates ChatResult/StreamChunk into its own
# event shapes when it builds the iteration record.


@dataclass(frozen=True)
class UsageRecord:
    """Per-call token usage + latency snapshot from a ProviderClient.

    Best-effort: completion_tokens may be 0 if the provider didn't surface
    a usage block on stream completion. latency_ms is wall-clock from the
    HTTP request issue to the final chunk's arrival.
    """

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    model_id: str = ""
    latency_ms: float = 0.0


@dataclass
class ChatResult:
    """Non-streaming chat() return shape."""

    content: str
    usage: UsageRecord = field(default_factory=UsageRecord)


# StreamChunk is not a class — it's a dict union to keep monkeypatch seams
# trivial in tests:
#   {"type": "delta", "text": str}
#   {"type": "done",  "usage": UsageRecord}


class ProviderClient(ABC):
    """Abstract base for provider-specific chat clients.

    Subclasses must implement chat() and stream_chat(); both should return
    ChatResult / yield StreamChunk-shaped dicts. is_configured() checks the
    expected env key and is consulted by atelier_agent before dispatch so
    the user gets a clean "set <KEY> to enable" message rather than a 401.
    """

    provider: str = "unknown"
    model_id: str = ""
    key_env: str = ""

    @abstractmethod
    def chat(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        response_format: Optional[Dict[str, str]] = None,
        max_tokens: Optional[int] = None,
    ) -> ChatResult: ...

    @abstractmethod
    def stream_chat(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        response_format: Optional[Dict[str, str]] = None,
        max_tokens: Optional[int] = None,
    ) -> Iterator[Dict[str, Any]]: ...

    @abstractmethod
    def is_configured(self) -> bool: ...


# ── ChatCompletionsClient (OpenAI-compatible) ─────────────────────────────
# Covers DashScope (compatible-mode), OpenAI, DeepSeek, Ollama, etc. The
# provider-specific bits (base_url, key_env) are passed in by
# runtime_provider.client_for().

class ChatCompletionsClient(ProviderClient):
    """OpenAI Chat Completions API client (also speaks DashScope compat mode).

    Constructed with provider/model_id/base_url/key_env so a single
    implementation handles all OpenAI-compatible endpoints. is_configured()
    honors the DashScope ALIBABA_API_KEY alias.
    """

    def __init__(
        self,
        provider: str = "dashscope",
        model_id: str = "qwen-plus",
        base_url: Optional[str] = None,
        key_env: str = "DASHSCOPE_API_KEY",
        timeout_s: float = 60.0,
    ):
        self.provider = provider
        self.model_id = model_id
        self.base_url = base_url or self._default_base_url(provider)
        self.key_env = key_env
        self.timeout_s = timeout_s
        self._client = None

    @staticmethod
    def _default_base_url(provider: str) -> str:
        if provider == "openai":
            return os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
        return f"{get_provider_base_url('DASHSCOPE')}/compatible-mode/v1"

    def is_configured(self) -> bool:
        if os.getenv(self.key_env):
            return True
        if self.provider == "dashscope" and os.getenv("ALIBABA_API_KEY"):
            return True
        return False

    def _resolve_api_key(self) -> Optional[str]:
        return os.getenv(self.key_env) or (
            os.getenv("ALIBABA_API_KEY") if self.provider == "dashscope" else None
        )

    def _get_client(self):
        if self._client is None:
            try:
                from openai import OpenAI
            except ImportError as exc:
                raise RuntimeError(
                    "openai package not installed. Run: pip install openai>=1.0.0"
                ) from exc
            self._client = OpenAI(
                api_key=self._resolve_api_key(),
                base_url=self.base_url,
                timeout=self.timeout_s,
            )
        return self._client

    def chat(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        response_format: Optional[Dict[str, str]] = None,
        max_tokens: Optional[int] = None,
    ) -> ChatResult:
        client = self._get_client()
        model_name = model or self.model_id

        kwargs: Dict[str, Any] = {"model": model_name, "messages": messages}
        if response_format:
            kwargs["response_format"] = response_format
        if max_tokens is not None:
            kwargs["max_tokens"] = int(max_tokens)

        started = time.time()
        try:
            response = client.chat.completions.create(**kwargs)
        except Exception as exc:  # pragma: no cover — provider error surface
            label = "OpenAI" if self.provider == "openai" else "DashScope"
            raise RuntimeError(f"{label} API error: {exc}") from exc

        elapsed_ms = (time.time() - started) * 1000.0
        content = ""
        try:
            content = response.choices[0].message.content or ""
        except Exception:  # pragma: no cover — defensive
            content = ""
        usage = _coerce_usage(getattr(response, "usage", None), model_name, elapsed_ms)
        return ChatResult(content=content, usage=usage)

    def stream_chat(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        response_format: Optional[Dict[str, str]] = None,
        max_tokens: Optional[int] = None,
    ) -> Iterator[Dict[str, Any]]:
        client = self._get_client()
        model_name = model or self.model_id

        kwargs: Dict[str, Any] = {
            "model": model_name,
            "messages": messages,
            "stream": True,
            # OpenAI requires include_usage=true to surface usage on stream
            # final chunk. DashScope ignores unknown options so this is
            # safe for both. (We still get a usage block from DashScope's
            # native stream.)
            "stream_options": {"include_usage": True},
        }
        if response_format:
            kwargs["response_format"] = response_format
        if max_tokens is not None:
            kwargs["max_tokens"] = int(max_tokens)

        started = time.time()
        try:
            stream = client.chat.completions.create(**kwargs)
        except Exception as exc:
            label = "OpenAI" if self.provider == "openai" else "DashScope"
            raise RuntimeError(f"{label} streaming API error: {exc}") from exc

        last_usage_obj: Any = None
        try:
            for chunk in stream:
                # Capture usage block when the provider includes it (final chunk).
                chunk_usage = getattr(chunk, "usage", None)
                if chunk_usage is not None:
                    last_usage_obj = chunk_usage
                try:
                    choices = getattr(chunk, "choices", None) or []
                    if not choices:
                        continue
                    delta = getattr(choices[0], "delta", None)
                    if delta is None:
                        continue
                    content = getattr(delta, "content", None)
                    if content is None and isinstance(delta, dict):
                        content = delta.get("content")
                    if not content:
                        continue
                    yield {"type": "delta", "text": str(content)}
                except Exception:  # pragma: no cover — defensive per-chunk guard
                    logger.exception("ChatCompletionsClient: skipping malformed chunk")
                    continue
        finally:
            elapsed_ms = (time.time() - started) * 1000.0
            usage = _coerce_usage(last_usage_obj, model_name, elapsed_ms)
            yield {"type": "done", "usage": usage}


# ── AnthropicMessagesClient ───────────────────────────────────────────────
# Lazy-imports anthropic so installs without the SDK still load the rest of
# atelier. chat()/stream_chat() raise a clear ImportError-derived
# RuntimeError on first use.

class AnthropicMessagesClient(ProviderClient):
    """Anthropic Messages API client (claude-3-x family).

    Translates the OpenAI message shape to Anthropic's format on the way in
    (system messages collapse into a top-level `system` parameter; rest
    pass through untouched). Stream output returns text deltas; final
    chunk carries usage from message_delta + message_stop events.
    """

    def __init__(
        self,
        model_id: str = "claude-3-5-sonnet-20241022",
        key_env: str = "ANTHROPIC_API_KEY",
        timeout_s: float = 60.0,
    ):
        self.provider = "anthropic"
        self.model_id = model_id
        self.key_env = key_env
        self.timeout_s = timeout_s
        self._client = None

    def is_configured(self) -> bool:
        return bool(os.getenv(self.key_env))

    def _get_client(self):
        if self._client is None:
            try:
                import anthropic  # type: ignore[import-not-found]
            except ImportError as exc:  # pragma: no cover — optional dep
                raise RuntimeError(
                    "anthropic package not installed. "
                    "Run: pip install anthropic>=0.40.0 to enable Claude models."
                ) from exc
            self._client = anthropic.Anthropic(
                api_key=os.getenv(self.key_env),
                timeout=self.timeout_s,
            )
        return self._client

    @staticmethod
    def _split_system(messages: List[Dict[str, str]]):
        system_parts: List[str] = []
        chat_msgs: List[Dict[str, str]] = []
        for msg in messages:
            role = msg.get("role")
            content = msg.get("content") or ""
            if role == "system":
                if content:
                    system_parts.append(content)
            else:
                # Anthropic only accepts user/assistant; tool messages would
                # need to be encoded as user-content. The agent loop uses
                # tool messages for OpenAI-style — for now we stringify them
                # into a user message so the conversation thread stays
                # coherent. (Pure-vanity case until we wire tool_use blocks.)
                if role == "tool":
                    role = "user"
                chat_msgs.append({"role": role, "content": content})
        return "\n\n".join(system_parts), chat_msgs

    def chat(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        response_format: Optional[Dict[str, str]] = None,
        max_tokens: Optional[int] = None,
    ) -> ChatResult:
        client = self._get_client()
        model_name = model or self.model_id
        system_str, chat_msgs = self._split_system(messages)
        kwargs: Dict[str, Any] = {
            "model": model_name,
            "messages": chat_msgs or [{"role": "user", "content": ""}],
            "max_tokens": int(max_tokens) if max_tokens is not None else 4096,
        }
        if system_str:
            kwargs["system"] = system_str

        started = time.time()
        try:
            response = client.messages.create(**kwargs)
        except Exception as exc:  # pragma: no cover — provider surface
            raise RuntimeError(f"Anthropic API error: {exc}") from exc
        elapsed_ms = (time.time() - started) * 1000.0

        content = ""
        try:
            blocks = getattr(response, "content", None) or []
            text_blocks = []
            for block in blocks:
                btext = getattr(block, "text", None)
                if btext is None and isinstance(block, dict):
                    btext = block.get("text")
                if btext:
                    text_blocks.append(str(btext))
            content = "".join(text_blocks)
        except Exception:  # pragma: no cover — defensive
            content = ""

        usage = _coerce_anthropic_usage(getattr(response, "usage", None), model_name, elapsed_ms)
        return ChatResult(content=content, usage=usage)

    def stream_chat(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        response_format: Optional[Dict[str, str]] = None,
        max_tokens: Optional[int] = None,
    ) -> Iterator[Dict[str, Any]]:
        client = self._get_client()
        model_name = model or self.model_id
        system_str, chat_msgs = self._split_system(messages)
        kwargs: Dict[str, Any] = {
            "model": model_name,
            "messages": chat_msgs or [{"role": "user", "content": ""}],
            "max_tokens": int(max_tokens) if max_tokens is not None else 4096,
        }
        if system_str:
            kwargs["system"] = system_str

        started = time.time()
        prompt_tokens = 0
        completion_tokens = 0
        try:
            with client.messages.stream(**kwargs) as stream:
                for text in stream.text_stream:
                    if not text:
                        continue
                    yield {"type": "delta", "text": str(text)}
                final = stream.get_final_message()
                final_usage = getattr(final, "usage", None)
                if final_usage is not None:
                    prompt_tokens = int(getattr(final_usage, "input_tokens", 0) or 0)
                    completion_tokens = int(getattr(final_usage, "output_tokens", 0) or 0)
        except Exception as exc:  # pragma: no cover — provider surface
            elapsed_ms = (time.time() - started) * 1000.0
            yield {
                "type": "done",
                "usage": UsageRecord(model_id=model_name, latency_ms=elapsed_ms),
            }
            raise RuntimeError(f"Anthropic streaming error: {exc}") from exc

        elapsed_ms = (time.time() - started) * 1000.0
        yield {
            "type": "done",
            "usage": UsageRecord(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens,
                model_id=model_name,
                latency_ms=elapsed_ms,
            ),
        }


# ── CodexResponsesClient (v1.5 placeholder) ───────────────────────────────

class CodexResponsesClient(ProviderClient):
    """Placeholder for the OpenAI Responses API client.

    Constructor succeeds (so runtime_provider.client_for() can hand it back
    in dry-runs), but chat() and stream_chat() raise NotImplementedError.
    """

    def __init__(
        self,
        model_id: str = "codex-default",
        key_env: str = "OPENAI_API_KEY",
        timeout_s: float = 60.0,
    ):
        self.provider = "codex"
        self.model_id = model_id
        self.key_env = key_env
        self.timeout_s = timeout_s

    def is_configured(self) -> bool:
        return bool(os.getenv(self.key_env))

    def chat(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        response_format: Optional[Dict[str, str]] = None,
        max_tokens: Optional[int] = None,
    ) -> ChatResult:
        raise NotImplementedError(
            "CodexResponsesClient is a v1.5 placeholder; route via dashscope/openai for now"
        )

    def stream_chat(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        response_format: Optional[Dict[str, str]] = None,
        max_tokens: Optional[int] = None,
    ) -> Iterator[Dict[str, Any]]:
        raise NotImplementedError(
            "CodexResponsesClient is a v1.5 placeholder; route via dashscope/openai for now"
        )


# ── Helpers ───────────────────────────────────────────────────────────────


def _coerce_usage(raw: Any, model_id: str, latency_ms: float) -> UsageRecord:
    """Normalize an OpenAI-shaped usage object/dict into UsageRecord."""
    if raw is None:
        return UsageRecord(model_id=model_id, latency_ms=latency_ms)
    if isinstance(raw, dict):
        prompt = int(raw.get("prompt_tokens") or 0)
        completion = int(raw.get("completion_tokens") or 0)
        total = int(raw.get("total_tokens") or (prompt + completion))
    else:
        prompt = int(getattr(raw, "prompt_tokens", 0) or 0)
        completion = int(getattr(raw, "completion_tokens", 0) or 0)
        total = int(getattr(raw, "total_tokens", 0) or (prompt + completion))
    return UsageRecord(
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=total,
        model_id=model_id,
        latency_ms=latency_ms,
    )


def _coerce_anthropic_usage(raw: Any, model_id: str, latency_ms: float) -> UsageRecord:
    """Normalize an Anthropic Messages-shaped usage block into UsageRecord."""
    if raw is None:
        return UsageRecord(model_id=model_id, latency_ms=latency_ms)
    if isinstance(raw, dict):
        prompt = int(raw.get("input_tokens") or 0)
        completion = int(raw.get("output_tokens") or 0)
    else:
        prompt = int(getattr(raw, "input_tokens", 0) or 0)
        completion = int(getattr(raw, "output_tokens", 0) or 0)
    return UsageRecord(
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=prompt + completion,
        model_id=model_id,
        latency_ms=latency_ms,
    )


# ── Legacy LLMAdapter shim ────────────────────────────────────────────────
# Kept as a thin wrapper so pre-1.4 callers (storyboard.py, llm.py, audio.py,
# everything outside atelier_agent.py) continue to work unchanged. The shim
# delegates to a ChatCompletionsClient under the hood and unwraps
# ChatResult.content / StreamChunk["text"] to preserve the legacy
# str / Iterator[str] surface.

class LLMAdapter:
    """Legacy single-provider chat surface — back-compat shim over ChatCompletionsClient.

    Pre-v1.4 callers expect:
      - `provider` attribute (str)
      - `is_configured` *property* (bool)
      - `chat(messages, model?, response_format?) -> str`
      - `stream_chat(messages, model?, response_format?) -> Iterator[str]`

    All four are preserved verbatim; the underlying ProviderClient is
    constructed lazily on first call.
    """

    def __init__(self):
        self.provider = os.getenv("LLM_PROVIDER", "dashscope").lower()
        self._client: Optional[ChatCompletionsClient] = None
        logger.info(f"LLM Adapter initialized with provider: {self.provider}")

    @property
    def is_configured(self) -> bool:
        if self.provider == "openai":
            return bool(os.getenv("OPENAI_API_KEY"))
        return bool(os.getenv("DASHSCOPE_API_KEY") or os.getenv("ALIBABA_API_KEY"))

    def _get_inner(self) -> ChatCompletionsClient:
        if self._client is None:
            if self.provider == "openai":
                self._client = ChatCompletionsClient(
                    provider="openai",
                    model_id=os.getenv("OPENAI_MODEL", "gpt-4o"),
                    base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
                    key_env="OPENAI_API_KEY",
                )
            else:
                self._client = ChatCompletionsClient(
                    provider="dashscope",
                    model_id="qwen3.5-plus",
                    base_url=f"{get_provider_base_url('DASHSCOPE')}/compatible-mode/v1",
                    key_env="DASHSCOPE_API_KEY",
                )
        return self._client

    def _get_default_model(self) -> str:
        if self.provider == "openai":
            return os.getenv("OPENAI_MODEL", "gpt-4o")
        return "qwen3.5-plus"

    def chat(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        response_format: Optional[Dict[str, str]] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        result = self._get_inner().chat(
            messages=messages,
            model=model or self._get_default_model(),
            response_format=response_format,
            max_tokens=max_tokens,
        )
        return result.content or ""

    def stream_chat(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        response_format: Optional[Dict[str, str]] = None,
        max_tokens: Optional[int] = None,
    ) -> Iterator[str]:
        for chunk in self._get_inner().stream_chat(
            messages=messages,
            model=model or self._get_default_model(),
            response_format=response_format,
            max_tokens=max_tokens,
        ):
            if chunk.get("type") == "delta":
                text = chunk.get("text") or ""
                if text:
                    yield text
            # `done` chunks carry usage and are silently dropped here so
            # the legacy `Iterator[str]` shape stays clean.
