"""Platform-only configuration with safe defaults."""

from dataclasses import dataclass
import os
from pathlib import Path


def _bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class PlatformConfig:
    enabled: bool = False
    database_url: str = "sqlite:///output/platform.db"
    session_ttl_seconds: int = 86_400
    password_min_length: int = 12
    credentials_key: str | None = None
    skill_queue_backend: str = "sqlite"
    skill_redis_url: str | None = None
    skill_postgres_url: str | None = None

    @classmethod
    def from_env(cls) -> "PlatformConfig":
        ttl = int(os.getenv("LUMENX_PLATFORM_SESSION_TTL_SECONDS", "86400"))
        password_min_length = int(os.getenv("LUMENX_PLATFORM_PASSWORD_MIN_LENGTH", "12"))
        if not 900 <= ttl <= 2_592_000:
            raise ValueError("LUMENX_PLATFORM_SESSION_TTL_SECONDS must be between 900 and 2592000")
        if password_min_length < 12:
            raise ValueError("LUMENX_PLATFORM_PASSWORD_MIN_LENGTH must be at least 12")
        return cls(
            enabled=_bool_env("LUMENX_PLATFORM_ENABLED"),
            database_url=os.getenv("LUMENX_PLATFORM_DB_URL", cls.database_url),
            session_ttl_seconds=ttl,
            password_min_length=password_min_length,
            credentials_key=os.getenv("LUMENX_PLATFORM_CREDENTIALS_KEY"),
            skill_queue_backend=os.getenv("LUMENX_SKILL_QUEUE_BACKEND", "sqlite"),
            skill_redis_url=os.getenv("LUMENX_SKILL_REDIS_URL") or None,
            skill_postgres_url=os.getenv("LUMENX_SKILL_POSTGRES_URL") or None,
        )

    def sqlite_path(self) -> Path:
        if not self.database_url.startswith("sqlite:///"):
            raise ValueError("the first platform implementation only accepts sqlite:/// URLs")
        return Path(self.database_url.removeprefix("sqlite:///"))

