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
