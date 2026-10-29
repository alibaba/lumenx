"""Application-owned logging context helpers for LumenX."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import logging
from pathlib import Path
from typing import Iterator
from uuid import uuid4

_current_app_log_id: ContextVar[str | None] = ContextVar("lumenx_app_log_id", default=None)


def current_app_log_id() -> str | None:
    return _current_app_log_id.get()


@contextmanager
def app_log_context(log_id: str | None) -> Iterator[None]:
    token = _current_app_log_id.set(log_id)
    try:
        yield
    finally:
        _current_app_log_id.reset(token)


class AppLogContextFilter(logging.Filter):
    def __init__(self, log_id: str) -> None:
        super().__init__()
        self.log_id = log_id

    def filter(self, record: logging.LogRecord) -> bool:
        return _current_app_log_id.get() == self.log_id


def create_app_file_handler(log_file: str | Path) -> tuple[str, logging.FileHandler]:
    log_id = str(uuid4())
    handler = logging.FileHandler(str(log_file), mode="a", encoding="utf-8")
    handler.addFilter(AppLogContextFilter(log_id))
    handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
    return log_id, handler
