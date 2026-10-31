from src.apps.platform.skill_worker_main import _dry_run
from src.apps.platform.central_queue import QueueJob


def test_worker_dry_run_handler_accepts_safe_job():
    _dry_run(QueueJob("job-1", {"skill_name": "story-scout"}))


def test_worker_dry_run_handler_rejects_forbidden_job():
    try:
        _dry_run(QueueJob("job-1", {"script": "unsafe"}))
    except ValueError as exc:
        assert "forbidden" in str(exc)
    else:
        raise AssertionError("forbidden worker payload was accepted")


def test_provider_job_requires_credential_proxy():
    try:
        _dry_run(QueueJob("job-1", {"provider": "dashscope"}))
    except ValueError as exc:
        assert "credential proxy" in str(exc)
    else:
        raise AssertionError("provider job bypassed credential proxy")


def test_provider_job_receives_opaque_credential_ref():
    seen = {}

    class Proxy:
        def issue(self, **kwargs):
            seen.update(kwargs)
            return "broker/ref/1"

    _dry_run(
        QueueJob("job-1", {"provider": "dashscope", "credential_scope": "tenant-1"}),
        worker_id="w1",
        credential_proxy=Proxy(),
    )
    assert seen == {"worker_id": "w1", "job_id": "job-1", "provider": "dashscope", "scope": "tenant-1"}
