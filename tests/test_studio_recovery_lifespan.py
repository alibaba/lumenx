"""Lifespan recovery reads persisted scoped handles and owns its worker."""

import time
from threading import Event
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from src.apps.comic_gen import api, pipeline as module
from src.apps.comic_gen import provider_recovery_dispatcher as dispatch
from src.apps.comic_gen.provider_recovery import record_with_changes
from src.models.provider_tasks import ProviderTaskStatus
from tests.test_studio_provider_recovery import FakeProvider, _admission_request, _install_provider
from tests.test_studio_provider_recovery_dispatcher import _make_runtime


def persisted_submission(tmp_path, monkeypatch):
    provider = FakeProvider()
    original = _make_runtime(tmp_path, monkeypatch, provider)
    _, task_id = original.admit_provider_video_task("p", _admission_request())
    original.submit_provider_video_task("p", task_id)
    data_dir = original.data_dir
    original.close()
    resumed = module.ComicGenPipeline(data_dir=data_dir, script_processor=Mock(), recover_orphan_tasks=True)
    return resumed, task_id


def test_legacy_orphan_pass_preserves_scoped_receipt_for_read_only_recovery(tmp_path, monkeypatch):
    resumed, task_id = persisted_submission(tmp_path, monkeypatch)
    try:
        task = resumed.read_committed_script("p").video_tasks[0]
        assert task.id == task_id
        assert task.status == "processing"
        assert task.provider_execution.phase == "submitted"
    finally:
        resumed.close()


@pytest.mark.parametrize("phase,has_handle,expected", [
    ("accepted", False, "needs_confirmation"),
    ("submitting", False, "needs_confirmation"),
    ("submitted", False, "needs_confirmation"),
    ("submitting", True, "processing"),
    ("submitted", True, "processing"),
    ("observed", True, "processing"),
])
def test_startup_preserves_only_recoverable_receipts(tmp_path, monkeypatch, phase, has_handle, expected):
    original = _make_runtime(tmp_path, monkeypatch, FakeProvider())
    _, task_id = original.admit_provider_video_task("p", _admission_request())
    original.submit_provider_video_task("p", task_id)
    task = original.scripts["p"].video_tasks[0]
    task.provider_execution = record_with_changes(
        task.provider_execution, phase=phase,
        handle=task.provider_execution.handle if has_handle else None,
    )
    original._commit_storage("projects.json")
    original.close()
    resumed = module.ComicGenPipeline(data_dir=original.data_dir, script_processor=Mock(), recover_orphan_tasks=True)
    try:
        saved = resumed.read_committed_script("p").video_tasks[0]
        assert saved.status == expected
        assert saved.provider_execution == task.provider_execution
    finally:
        resumed.close()


def test_lifespan_recovers_same_handle_then_fetches_and_stops_only_owned_worker(tmp_path, monkeypatch):
    resumed, task_id = persisted_submission(tmp_path, monkeypatch)
    provider = FakeProvider()
    provider.query_status = ProviderTaskStatus.SUCCEEDED
    provider.query_raw_status = "SUCCEEDED"
    provider.query_video_url = "https://media.example.invalid/recovered.mp4"
    _install_provider(resumed, provider)
    cls = dispatch.StudioProviderRecoveryDispatcher
    monkeypatch.setattr(dispatch, "StudioProviderRecoveryDispatcher", lambda p: cls(p, interval_seconds=0.01))
    app = api.create_app(data_dir=resumed.data_dir, load_config=False, pipeline_instance=resumed, recover_orphan_tasks=True)
    with TestClient(app):
        worker = app.state.provider_recovery_dispatcher
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if resumed.read_committed_script("p").video_tasks[0].status == "completed":
                break
            Event().wait(0.01)
        task = resumed.read_committed_script("p").video_tasks[0]
        assert task.id == task_id and task.status == "completed"
        assert len(provider.queries) == 1 and len(provider.fetches) == 1
        assert not provider.submits
        assert provider.queries[0][0].task_id == "remote-task-1"
    assert resumed._closed
    assert not worker.is_running
    assert not hasattr(app.state, "provider_recovery_dispatcher")


def test_startup_without_current_classic_runtime_preserves_selector_recovery_state(tmp_path, monkeypatch):
    monkeypatch.setenv("DASHSCOPE_WORKSPACE", "unsupported-workspace")

    started = []

    class DispatcherSpy:
        def __init__(self, pipeline):
            self.pipeline = pipeline
            self.is_running = False

        def start(self):
            self.is_running = True
            started.append(self)

        def wake(self):
            pass

        def request_stop(self):
            self.is_running = False

        def join(self, timeout=None):
            pass

    monkeypatch.setattr(dispatch, "StudioProviderRecoveryDispatcher", DispatcherSpy)
    app = api.create_app(data_dir=tmp_path / "output", load_config=False, recover_orphan_tasks=True)
    with TestClient(app):
        assert app.state.pipeline.provider_recovery_runtime is None
        assert app.state.provider_recovery_runtime_reason == "unsupported_dashscope_workspace"
        assert app.state.pipeline.provider_runtime_selector is app.state.provider_runtime_selector
        assert started == [app.state.provider_recovery_dispatcher]


def test_startup_failure_before_runtime_ready_does_not_start_worker(tmp_path, monkeypatch):
    resumed, _ = persisted_submission(tmp_path, monkeypatch)
    _install_provider(resumed, FakeProvider())
    calls = []
    monkeypatch.setattr(dispatch, "StudioProviderRecoveryDispatcher", lambda *args: calls.append(args))
    def fail(*args):
        raise RuntimeError("mount failed")
    monkeypatch.setattr(api, "_mount_output_routes", fail)
    app = api.create_app(data_dir=resumed.data_dir, load_config=False, pipeline_instance=resumed, recover_orphan_tasks=True)
    with pytest.raises(RuntimeError, match="mount failed"):
        with TestClient(app):
            pass
    assert calls == []
    assert resumed._closed


def test_shutdown_closes_storage_before_bounded_join_and_rejects_late_query(tmp_path, monkeypatch):
    resumed, _ = persisted_submission(tmp_path, monkeypatch)
    provider = FakeProvider()
    entered, release = Event(), Event()
    original_query = provider.query
    def blocked_query(*args, **kwargs):
        entered.set()
        assert release.wait(3), "test did not release provider"
        return original_query(*args, **kwargs)
    provider.query = blocked_query
    _install_provider(resumed, provider)
    joins = []
    cls = dispatch.StudioProviderRecoveryDispatcher
    original_join = cls.join
    def bounded_join(worker, timeout=None):
        joins.append((timeout, resumed._closed))
        return original_join(worker, timeout=0.01)
    monkeypatch.setattr(cls, "join", bounded_join)
    app = api.create_app(data_dir=resumed.data_dir, load_config=False, pipeline_instance=resumed, recover_orphan_tasks=True)
    other_app = api.create_app(data_dir=tmp_path / "other", load_config=False)
    try:
        with TestClient(other_app) as other:
            with TestClient(app):
                worker = app.state.provider_recovery_dispatcher
                assert entered.wait(1)
                before = (resumed.data_dir / "projects.json").read_bytes()
            assert joins == [(5.0, True)]
            assert not other_app.state.pipeline._closed
            assert other.get("/health").status_code == 200
            release.set()
            original_join(worker, timeout=1)
            assert not worker.is_running
            assert (resumed.data_dir / "projects.json").read_bytes() == before
            assert not provider.submits and not provider.fetches
    finally:
        release.set()


def test_worker_start_failure_releases_pipeline_and_runtime_state(tmp_path, monkeypatch):
    resumed, _ = persisted_submission(tmp_path, monkeypatch)
    _install_provider(resumed, FakeProvider())
    events = []
    class FailedWorker:
        def __init__(self, pipeline):
            pass
        def start(self):
            raise RuntimeError("thread start failed")
        def request_stop(self):
            events.append("stop")
        def join(self, timeout):
            events.append(("join", timeout, resumed._closed))
    monkeypatch.setattr(dispatch, "StudioProviderRecoveryDispatcher", FailedWorker)
    app = api.create_app(data_dir=resumed.data_dir, load_config=False, pipeline_instance=resumed, recover_orphan_tasks=True)
    with pytest.raises(RuntimeError, match="thread start failed"):
        with TestClient(app):
            pass
    assert events == ["stop", ("join", 5.0, True)]
    assert resumed._closed
    assert not hasattr(app.state, "provider_recovery_dispatcher")
