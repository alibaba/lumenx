from __future__ import annotations

from contextvars import ContextVar
from threading import Event

import pytest

from src.apps.atelier.sequence_export_dispatcher import SequenceExportDispatcher
from src.utils.json_storage import StorageConflictError


def test_dispatcher_is_lazy_serial_and_deduplicates_active_and_queued_jobs():
    started, release, second = Event(), Event(), Event()
    calls = []

    class Service:
        def run_sequence_export_job(self, project_id, job_id):
            calls.append((project_id, job_id))
            if job_id == "one":
                started.set()
                assert release.wait(3)
            else:
                second.set()

        def request_stop(self):
            release.set()

    dispatcher = SequenceExportDispatcher(Service())
    assert not dispatcher.is_running
    try:
        assert dispatcher.schedule("p", "one")
        assert started.wait(3)
        assert not dispatcher.schedule("p", "one")
        assert dispatcher.schedule("p", "two")
        assert not dispatcher.schedule("p", "two")
        assert calls == [("p", "one")]
        release.set()
        assert second.wait(3)
        assert dispatcher.wait_for_idle(3)
        assert calls == [("p", "one"), ("p", "two")]
    finally:
        dispatcher.request_stop()
        dispatcher.join(3)
    assert not dispatcher.is_running


def test_stop_discards_only_memory_queue_and_prevents_worker_resurrection():
    started, release = Event(), Event()
    calls, stops = [], []

    class Service:
        def run_sequence_export_job(self, project_id, job_id):
            calls.append(job_id)
            started.set()
            assert release.wait(3)

        def request_stop(self):
            stops.append(True)
            release.set()

    dispatcher = SequenceExportDispatcher(Service(), max_pending=1)
    assert dispatcher.schedule("p", "one")
    assert started.wait(3)
    assert dispatcher.schedule("p", "two")
    with pytest.raises(StorageConflictError, match="queue"):
        dispatcher.schedule("p", "three")
    dispatcher.request_stop()
    dispatcher.request_stop()
    dispatcher.join(3)
    assert calls == ["one"]
    assert stops == [True]
    with pytest.raises(StorageConflictError, match="stopped"):
        dispatcher.schedule("p", "new")
    assert not dispatcher.is_running


def test_worker_inherits_context_and_an_error_does_not_replay_or_block_next_job():
    context = ContextVar("test_export_context", default="none")
    done = Event()
    calls = []

    class Service:
        def run_sequence_export_job(self, project_id, job_id):
            calls.append((job_id, context.get()))
            if job_id == "bad":
                raise RuntimeError("private diagnostic")
            done.set()

        def request_stop(self):
            pass

    dispatcher = SequenceExportDispatcher(Service())
    token = context.set("app-A")
    try:
        dispatcher.schedule("p", "bad")
        context.set("app-B")
        dispatcher.schedule("p", "good")
        assert done.wait(3)
        assert dispatcher.wait_for_idle(3)
        assert calls == [("bad", "app-A"), ("good", "app-A")]
    finally:
        context.reset(token)
        dispatcher.request_stop()
        dispatcher.join(3)


def test_thread_start_failure_does_not_keep_a_phantom_dispatch(monkeypatch):
    import threading

    class Service:
        def run_sequence_export_job(self, *_args):
            pass

        def request_stop(self):
            pass

    dispatcher = SequenceExportDispatcher(Service())
    real_start = threading.Thread.start
    monkeypatch.setattr(threading.Thread, "start", lambda self: (_ for _ in ()).throw(RuntimeError("no threads")))
    with pytest.raises(RuntimeError, match="no threads"):
        dispatcher.schedule("p", "job")
    assert not dispatcher.is_running
    monkeypatch.setattr(threading.Thread, "start", real_start)
    try:
        assert dispatcher.schedule("p", "job")
        assert dispatcher.wait_for_idle(3)
    finally:
        dispatcher.request_stop()
        dispatcher.join(3)
