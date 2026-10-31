"""Request-scoped provider credential binding.

The secret is intentionally held only in a ContextVar for the duration of a
request. It is never serialized into a project, receipt, or frontend payload.
"""

from contextvars import ContextVar
from typing import Any

_credential: ContextVar[dict[str, Any] | None] = ContextVar("lumenx_provider_credential", default=None)


def bind_provider_credential(credential: dict[str, Any] | None):
    return _credential.set(credential)


def reset_provider_credential(token) -> None:
    _credential.reset(token)


def current_provider_credential(provider: str) -> dict[str, Any] | None:
    value = _credential.get()
    return value if value and value.get("provider") == provider else None
