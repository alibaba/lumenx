"""Request-scoped compute billing callbacks used by provider adapters."""

from contextvars import ContextVar
from typing import Any, Callable

ComputeBillingContext = dict[str, Any]
_context: ContextVar[ComputeBillingContext | None] = ContextVar("lumenx_compute_billing", default=None)


def bind_compute_billing(context: ComputeBillingContext | None):
    return _context.set(context)


def reset_compute_billing(token) -> None:
    _context.reset(token)


def current_compute_billing() -> ComputeBillingContext | None:
    return _context.get()
