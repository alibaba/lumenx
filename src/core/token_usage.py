"""Request-scoped token usage recording hook shared by Core and platform."""

from contextvars import ContextVar
from typing import Any, Callable

TokenUsageRecorder = Callable[[dict[str, Any]], None]

_recorder: ContextVar[TokenUsageRecorder | None] = ContextVar("lumenx_token_usage_recorder", default=None)


def bind_token_usage_recorder(recorder: TokenUsageRecorder | None):
    return _recorder.set(recorder)


def reset_token_usage_recorder(token) -> None:
    _recorder.reset(token)


def record_token_usage(**event: Any) -> None:
    recorder = _recorder.get()
    if recorder is not None:
        recorder(event)
