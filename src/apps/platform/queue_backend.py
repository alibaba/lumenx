"""Central queue backend selection for Skill runs.

The SQLite ledger remains the local fallback. Redis/PostgreSQL are explicit
production modes; this module validates configuration without connecting at
import time.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Literal
from urllib.parse import urlparse


class QueueBackendError(ValueError):
    pass


@dataclass(frozen=True)
class SkillQueueConfig:
    backend: Literal["sqlite", "redis", "postgres"] = "sqlite"
    redis_url: str | None = None
    postgres_url: str | None = None
    queue_name: str = "lumenx-skill-runs"

    @classmethod
    def from_env(cls) -> "SkillQueueConfig":
        backend = os.getenv("LUMENX_SKILL_QUEUE_BACKEND", "sqlite").strip().lower()
        if backend not in {"sqlite", "redis", "postgres"}:
            raise QueueBackendError("LUMENX_SKILL_QUEUE_BACKEND must be sqlite, redis, or postgres")
        redis_url = os.getenv("LUMENX_SKILL_REDIS_URL") or None
        postgres_url = os.getenv("LUMENX_SKILL_POSTGRES_URL") or None
        queue_name = os.getenv("LUMENX_SKILL_QUEUE_NAME", "lumenx-skill-runs").strip()
        if not queue_name or len(queue_name) > 128:
            raise QueueBackendError("LUMENX_SKILL_QUEUE_NAME is invalid")
        if backend == "redis" and (not redis_url or urlparse(redis_url).scheme not in {"redis", "rediss"}):
            raise QueueBackendError("redis backend requires redis:// or rediss:// URL")
        if backend == "postgres" and (not postgres_url or urlparse(postgres_url).scheme not in {"postgres", "postgresql"}):
            raise QueueBackendError("postgres backend requires postgres:// or postgresql:// URL")
        return cls(backend=backend, redis_url=redis_url, postgres_url=postgres_url, queue_name=queue_name)


def describe_queue_backend(config: SkillQueueConfig) -> dict[str, str | bool]:
    return {
        "backend": config.backend,
        "queue_name": config.queue_name,
        "centralized": config.backend in {"redis", "postgres"},
        "local_fallback": config.backend == "sqlite",
        "worker_mode": "isolated-container" if config.backend in {"redis", "postgres"} else "single-process-or-subprocess",
    }

