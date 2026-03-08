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
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)

DASHSCOPE_OPENAI_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"


class LLMAdapter:
    """Unified LLM call interface supporting DashScope and OpenAI-compatible APIs."""

    def __init__(self):
        self.provider = os.getenv("LLM_PROVIDER", "dashscope").lower()
        self._client = None
        logger.info(f"LLM Adapter initialized with provider: {self.provider}")

    @property
    def is_configured(self) -> bool:
        if self.provider == "openai":
            return bool(os.getenv("OPENAI_API_KEY"))
        return bool(os.getenv("DASHSCOPE_API_KEY"))

    def _get_client(self):
        """Get or create the OpenAI-compatible client (lazy, cached)."""
        if self._client is None:
            try:
                from openai import OpenAI
            except ImportError:
                raise RuntimeError(
                    "openai package not installed. Run: pip install openai>=1.0.0"
                )

            if self.provider == "openai":
                self._client = OpenAI(
                    api_key=os.getenv("OPENAI_API_KEY"),
                    base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
                )
            else:
                # DashScope uses OpenAI-compatible endpoint
                self._client = OpenAI(
                    api_key=os.getenv("DASHSCOPE_API_KEY"),
                    base_url=DASHSCOPE_OPENAI_BASE_URL,
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
    ) -> str:
        """
        Send a chat completion request and return the response content.

        Args:
            messages: List of {"role": ..., "content": ...} dicts
            model: Model name override (uses provider default if None)
            response_format: Optional {"type": "json_object"} constraint

        Returns:
            The assistant's response content as a string.

        Raises:
            RuntimeError: If the API call fails.
        """
        client = self._get_client()
        model = model or self._get_default_model()

        kwargs: Dict[str, Any] = {
            "model": model,
            "messages": messages,
        }
        if response_format:
            kwargs["response_format"] = response_format

        try:
            response = client.chat.completions.create(**kwargs)
            return response.choices[0].message.content
        except Exception as e:
            provider_label = "DashScope" if self.provider != "openai" else "OpenAI"
            raise RuntimeError(f"{provider_label} API error: {e}") from e
