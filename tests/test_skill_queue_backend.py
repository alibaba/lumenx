import pytest

from src.apps.platform.queue_backend import QueueBackendError, SkillQueueConfig, describe_queue_backend


def test_sqlite_is_safe_default_and_production_backends_are_explicit(monkeypatch):
    for key in ("LUMENX_SKILL_QUEUE_BACKEND", "LUMENX_SKILL_REDIS_URL", "LUMENX_SKILL_POSTGRES_URL"):
        monkeypatch.delenv(key, raising=False)
    config = SkillQueueConfig.from_env()
    assert describe_queue_backend(config)["local_fallback"] is True
    monkeypatch.setenv("LUMENX_SKILL_QUEUE_BACKEND", "redis")
    monkeypatch.setenv("LUMENX_SKILL_REDIS_URL", "redis://redis:6379/0")
    assert describe_queue_backend(SkillQueueConfig.from_env())["centralized"] is True


def test_queue_backend_rejects_missing_or_wrong_urls(monkeypatch):
    monkeypatch.setenv("LUMENX_SKILL_QUEUE_BACKEND", "redis")
    monkeypatch.delenv("LUMENX_SKILL_REDIS_URL", raising=False)
    with pytest.raises(QueueBackendError):
        SkillQueueConfig.from_env()
    monkeypatch.setenv("LUMENX_SKILL_BACKEND", "postgres")
    monkeypatch.setenv("LUMENX_SKILL_QUEUE_BACKEND", "postgres")
    monkeypatch.setenv("LUMENX_SKILL_POSTGRES_URL", "http://db")
    with pytest.raises(QueueBackendError):
        SkillQueueConfig.from_env()

