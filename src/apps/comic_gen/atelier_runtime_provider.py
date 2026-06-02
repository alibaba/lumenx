"""Atelier runtime provider — Hermes-style (provider, model_id) → ProviderConfig
resolution layer for the v1.4 Batch 4 multi-provider agent.

The agent loop calls `resolve(provider, model_id)` once per iteration to get
back a ProviderConfig describing the API mode (chat_completions /
anthropic_messages / codex_responses), base_url, key_env, and per-tier
fallback model. It then asks `client_for(cfg)` for the matching
ProviderClient subclass from llm_adapter and dispatches the LLM call.

Defaults: ATELIER_AGENT_PROVIDER (env, default 'dashscope') +
DASHSCOPE_AGENT_MODEL (env, default 'qwen-plus' if not set). Both are
overridable per-turn via the planner_package's `model_override` field, which
the agent loop injects into resolve() before building the iteration's
adapter.

Built-in registry covers DashScope (qwen-plus / qwen-max / qwen-turbo),
OpenAI (gpt-4o / gpt-4o-mini), Anthropic (claude-3-5-sonnet /
claude-3-5-haiku), and a Codex placeholder. New providers slot in by
appending to _BUILTIN.

Legacy ALIBABA_API_KEY is honored as an alias for DASHSCOPE_API_KEY inside
client_for() so users with the older env name continue to work.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Dict, List, Literal, Optional

logger = logging.getLogger(__name__)

if TYPE_CHECKING:  # pragma: no cover — type-only import to avoid cycle
    from .llm_adapter import ProviderClient


ApiMode = Literal["chat_completions", "anthropic_messages", "codex_responses"]


@dataclass(frozen=True)
class ProviderConfig:
    """Concrete runtime descriptor for one provider/model pair.

    Frozen so it's hashable and trivially comparable in tests. Mutable
    state (current ProviderClient instance, current api key, transient
    failure counters) lives in the caller, not on the config.
    """

    provider: str
    model_id: str
    api_mode: ApiMode
    base_url: Optional[str]
    key_env: str
    timeout_s: float = 60.0
    fallback_model: Optional[str] = None
    label: Optional[str] = None  # human-friendly label for the model picker

    def display_label(self) -> str:
        """User-facing string for the model picker pill."""
        return self.label or f"{self.provider} · {self.model_id}"


# DashScope OpenAI-compatible mode base URL — matches what llm_adapter.py
# currently builds via get_provider_base_url("DASHSCOPE") + /compatible-mode/v1.
_DASHSCOPE_BASE = "https://dashscope.aliyuncs.com/compatible-mode/v1"
_OPENAI_BASE = "https://api.openai.com/v1"


# Built-in registry. Order matters for `resolve(provider, None)` — first
# match by provider becomes the implicit default. Keep qwen-plus / gpt-4o /
# claude-3-5-sonnet at the head of their respective provider groups.
_BUILTIN: List[ProviderConfig] = [
    # DashScope (qwen family)
    ProviderConfig(
        provider="dashscope",
        model_id="qwen-plus",
        api_mode="chat_completions",
        base_url=_DASHSCOPE_BASE,
        key_env="DASHSCOPE_API_KEY",
        timeout_s=60.0,
        fallback_model="qwen-turbo",
        label="DashScope · Qwen Plus",
    ),
    ProviderConfig(
        provider="dashscope",
        model_id="qwen-max",
        api_mode="chat_completions",
        base_url=_DASHSCOPE_BASE,
        key_env="DASHSCOPE_API_KEY",
        timeout_s=90.0,
        fallback_model="qwen-plus",
        label="DashScope · Qwen Max",
    ),
    ProviderConfig(
        provider="dashscope",
        model_id="qwen-turbo",
        api_mode="chat_completions",
        base_url=_DASHSCOPE_BASE,
        key_env="DASHSCOPE_API_KEY",
        timeout_s=30.0,
        fallback_model=None,
        label="DashScope · Qwen Turbo",
    ),
    # OpenAI
    ProviderConfig(
        provider="openai",
        model_id="gpt-4o",
        api_mode="chat_completions",
        base_url=_OPENAI_BASE,
        key_env="OPENAI_API_KEY",
        timeout_s=60.0,
        fallback_model="gpt-4o-mini",
        label="OpenAI · GPT-4o",
    ),
    ProviderConfig(
        provider="openai",
        model_id="gpt-4o-mini",
        api_mode="chat_completions",
        base_url=_OPENAI_BASE,
        key_env="OPENAI_API_KEY",
        timeout_s=30.0,
        fallback_model=None,
        label="OpenAI · GPT-4o mini",
    ),
    # Anthropic — base_url=None means use the SDK default.
    ProviderConfig(
        provider="anthropic",
        model_id="claude-3-5-sonnet-20241022",
        api_mode="anthropic_messages",
        base_url=None,
        key_env="ANTHROPIC_API_KEY",
        timeout_s=90.0,
        fallback_model="claude-3-5-haiku-20241022",
        label="Anthropic · Claude 3.5 Sonnet",
    ),
    ProviderConfig(
        provider="anthropic",
        model_id="claude-3-5-haiku-20241022",
        api_mode="anthropic_messages",
        base_url=None,
        key_env="ANTHROPIC_API_KEY",
        timeout_s=30.0,
        fallback_model=None,
        label="Anthropic · Claude 3.5 Haiku",
    ),
    # Codex (v1.5 placeholder — client_for() returns CodexResponsesClient
    # which raises NotImplementedError on chat()/stream_chat()).
    ProviderConfig(
        provider="codex",
        model_id="codex-default",
        api_mode="codex_responses",
        base_url=None,
        key_env="OPENAI_API_KEY",
        timeout_s=60.0,
        fallback_model=None,
        label="Codex (v1.5 preview)",
    ),
]


_DEFAULT_PROVIDER_ENV = "ATELIER_AGENT_PROVIDER"
_DEFAULT_MODEL_ENV = "DASHSCOPE_AGENT_MODEL"


def _builtin_registry() -> List[ProviderConfig]:
    """Return a fresh copy of the built-in registry (defensive — callers
    must not mutate the module-level list)."""
    return list(_BUILTIN)


def list_all() -> List[ProviderConfig]:
    """Return every registered ProviderConfig regardless of key configuration."""
    return _builtin_registry()


def list_available() -> List[ProviderConfig]:
    """Return the registered configs whose required env key is set.

    The frontend model-pill renders disabled rows for entries whose
    `configured` flag is false; this helper is used by the GET
    /atelier/agent/models route to pre-compute that flag without leaking
    actual key values.
    """
    return [cfg for cfg in _builtin_registry() if _is_key_present(cfg)]


def _is_key_present(cfg: ProviderConfig) -> bool:
    """True if the env key for this config (or its DashScope alias) is set."""
    if os.getenv(cfg.key_env):
        return True
    if cfg.provider == "dashscope" and os.getenv("ALIBABA_API_KEY"):
        return True
    return False


def resolve(
    provider: Optional[str] = None,
    model_id: Optional[str] = None,
) -> ProviderConfig:
    """Pick a ProviderConfig from the registry.

    Resolution rules:
      - (None, None)        → resolve_default()
      - (provider, None)    → first registry entry with matching provider
      - (None, model_id)    → first registry entry with matching model_id
      - (provider, model_id) → exact match; fall back to provider-default
        with a logged warning if model_id is unknown for that provider
    """
    if not provider and not model_id:
        return resolve_default()
    registry = _builtin_registry()
    if provider and model_id:
        # Exact match first.
        for cfg in registry:
            if cfg.provider == provider and cfg.model_id == model_id:
                return cfg
        # Unknown model for this provider — log + fall back to provider default.
        logger.warning(
            "atelier_runtime_provider: unknown model_id=%s for provider=%s; "
            "falling back to provider default",
            model_id,
            provider,
        )
        for cfg in registry:
            if cfg.provider == provider:
                return cfg
        # Unknown provider entirely — log + fall back to global default.
        logger.warning(
            "atelier_runtime_provider: unknown provider=%s; falling back to default",
            provider,
        )
        return resolve_default()
    if provider:
        for cfg in registry:
            if cfg.provider == provider:
                return cfg
        logger.warning(
            "atelier_runtime_provider: unknown provider=%s; falling back to default",
            provider,
        )
        return resolve_default()
    # model_id only — search across all providers.
    for cfg in registry:
        if cfg.model_id == model_id:
            return cfg
    logger.warning(
        "atelier_runtime_provider: unknown model_id=%s (no provider); "
        "falling back to default",
        model_id,
    )
    return resolve_default()


def resolve_default() -> ProviderConfig:
    """Pick the implicit default config from env.

    ATELIER_AGENT_PROVIDER (default 'dashscope') picks the provider; within
    that provider, DASHSCOPE_AGENT_MODEL (legacy env, default 'qwen-plus')
    picks the model. Falls back to dashscope/qwen-plus if env values point
    at unknown entries.
    """
    provider_env = os.getenv(_DEFAULT_PROVIDER_ENV, "dashscope").lower().strip() or "dashscope"
    model_env = os.getenv(_DEFAULT_MODEL_ENV, "").strip() or None
    if model_env:
        for cfg in _builtin_registry():
            if cfg.provider == provider_env and cfg.model_id == model_env:
                return cfg
    # Provider-only fallback.
    for cfg in _builtin_registry():
        if cfg.provider == provider_env:
            return cfg
    # Hard fallback — first DashScope entry (qwen-plus).
    return _builtin_registry()[0]


def fallback_for(cfg: ProviderConfig) -> Optional[ProviderConfig]:
    """Return the ProviderConfig for cfg's fallback_model, or None.

    Used by the agent loop on rate-limit / timeout to retry with a cheaper
    or smaller model in the same provider family. Returns None if no
    fallback is registered for this entry.
    """
    if not cfg.fallback_model:
        return None
    for entry in _builtin_registry():
        if entry.provider == cfg.provider and entry.model_id == cfg.fallback_model:
            return entry
    return None


def resolve_api_key(cfg: ProviderConfig) -> Optional[str]:
    """Look up the API key for this config, honoring the ALIBABA_API_KEY alias.

    Returns None if no key is configured — caller decides whether to fail
    loud or surface a configuration message.
    """
    key = os.getenv(cfg.key_env)
    if not key and cfg.provider == "dashscope":
        key = os.getenv("ALIBABA_API_KEY")
    return key


def client_for(cfg: ProviderConfig) -> "ProviderClient":
    """Materialize the ProviderClient subclass that knows how to talk to cfg.

    Lazy-imports llm_adapter so this module stays importable from inside
    llm_adapter (no cycle). Each call returns a freshly constructed client
    so per-call state (timeout_s overrides, key rotation) doesn't leak
    between agent turns.
    """
    from .llm_adapter import (
        AnthropicMessagesClient,
        ChatCompletionsClient,
        CodexResponsesClient,
    )

    if cfg.api_mode == "chat_completions":
        return ChatCompletionsClient(
            provider=cfg.provider,
            model_id=cfg.model_id,
            base_url=cfg.base_url,
            key_env=cfg.key_env,
            timeout_s=cfg.timeout_s,
        )
    if cfg.api_mode == "anthropic_messages":
        return AnthropicMessagesClient(
            model_id=cfg.model_id,
            key_env=cfg.key_env,
            timeout_s=cfg.timeout_s,
        )
    if cfg.api_mode == "codex_responses":
        return CodexResponsesClient(
            model_id=cfg.model_id,
            key_env=cfg.key_env,
            timeout_s=cfg.timeout_s,
        )
    raise ValueError(f"Unknown api_mode for ProviderConfig: {cfg.api_mode!r}")


def to_wire_dict(cfg: ProviderConfig) -> Dict[str, object]:
    """Serialize a ProviderConfig for the GET /atelier/agent/models route.

    Drops base_url + timeout (server detail) and adds a `configured` boolean
    so the frontend can render disabled rows for unconfigured providers.
    """
    return {
        "provider": cfg.provider,
        "model_id": cfg.model_id,
        "label": cfg.display_label(),
        "key_env": cfg.key_env,
        "configured": _is_key_present(cfg),
        "fallback_model": cfg.fallback_model,
        "api_mode": cfg.api_mode,
    }
