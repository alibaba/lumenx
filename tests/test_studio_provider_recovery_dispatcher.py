"""Studio provider recovery dispatcher scans committed provider tasks only."""

import hashlib
import time
from unittest.mock import Mock

import pytest

from src.apps.comic_gen import pipeline as module
from src.apps.comic_gen import provider_recovery_dispatcher as dispatch_module
from src.apps.comic_gen.models import Character, Scene, Script, StoryboardFrame
from src.apps.comic_gen.provider_recovery_dispatcher import StudioProviderRecoveryDispatcher
from src.models.provider_tasks import ProviderTaskStatus
from src.utils.json_storage import StorageError

from tests.test_studio_provider_recovery import (
    PNG_BYTES,
    SECRET,
    FakeProvider,
    _admission_request,
    _install_provider,
)


def _make_runtime(tmp_path, monkeypatch, provider=None, secret=SECRET):
    for name in ("AssetGenerator", "StoryboardGenerator", "AudioGenerator", "ExportManager"):
        monkeypatch.setattr(module, name, Mock())
    item = module.ComicGenPipeline(data_dir=tmp_path / "data", script_processor=Mock(), warmup_demucs=False)
    image = item.data_dir / "uploads/source.png"
    image.parent.mkdir(parents=True, exist_ok=True)
    image.write_bytes(PNG_BYTES)
    item.scripts["p"] = Script(
        id="p",
        title="project",
        original_text="source",
        created_at=1,
        updated_at=1,
        scenes=[Scene(id="scene", name="Room", description="quiet", image_url="uploads/source.png")],
        characters=[Character(id="char", name="Hero", description="red coat", image_url="uploads/source.png")],
        frames=[StoryboardFrame(id="frame", scene_id="scene")],
        video_tasks=[],
    )
    item._commit_storage("projects.json", "series.json", "library_assets.json")
    _install_provider(item, provider or FakeProvider(), secret=secret)
    return item


def _admit(runtime):
    _, task_id = runtime.admit_provider_video_task("p", _admission_request())
    return task_id


def _submit(runtime, provider):
    task_id = _admit(runtime)
    runtime.submit_provider_video_task("p", task_id)
    return task_id


def _observe_success(runtime, provider):
    task_id = _submit(runtime, provider)
    provider.query_status = ProviderTaskStatus.SUCCEEDED
    provider.query_raw_status = "SUCCEEDED"
    provider.query_video_url = "https://media.example.invalid/result.mp4"
    runtime.query_provider_video_task("p", task_id)
    return task_id


def _dispatcher(runtime, **kwargs):
    options = {"interval_seconds": 0.01, "transient_backoff_seconds": 0.25}
    options.update(kwargs)
    return StudioProviderRecoveryDispatcher(runtime, **options)


def test_dispatcher_has_no_constructor_side_effects(tmp_path, monkeypatch):
    provider = FakeProvider()
    runtime = _make_runtime(tmp_path, monkeypatch, provider)
    _submit(runtime, provider)
    provider.queries.clear()

    dispatcher = _dispatcher(runtime)
    time.sleep(0.03)

    assert provider.queries == []
    assert not dispatcher.is_running
    runtime.close()


def test_scan_queries_submitted_task_without_posting(tmp_path, monkeypatch):
    provider = FakeProvider()
    runtime = _make_runtime(tmp_path, monkeypatch, provider)
    task_id = _submit(runtime, provider)
    provider.submits.clear()

    result = _dispatcher(runtime).run_once()

    saved = runtime.read_committed_script("p").video_tasks[0]
    assert result == ("queried", "p", task_id)
    assert provider.submits == []
    assert len(provider.queries) == 1
    assert saved.provider_execution.phase == "observed"
    assert saved.status == "processing"
    runtime.close()


def test_scan_fetches_succeeded_observation_after_restart(tmp_path, monkeypatch):
    provider = FakeProvider()
    runtime = _make_runtime(tmp_path, monkeypatch, provider)
    task_id = _observe_success(runtime, provider)
    data_dir = runtime.data_dir
    runtime.close()

    restart_provider = FakeProvider()
    runtime = module.ComicGenPipeline(data_dir=data_dir, script_processor=Mock(), warmup_demucs=False)
    _install_provider(runtime, restart_provider)

    result = _dispatcher(runtime).run_once()

    saved = runtime.read_committed_script("p").video_tasks[0]
    assert result == ("fetched", "p", task_id)
    assert saved.status == "completed"
    assert saved.video_url.startswith("video/provider-")
    assert (runtime.data_dir / saved.video_url).read_bytes() == restart_provider.fetch_bytes
    assert restart_provider.submits == []
    assert restart_provider.queries == []
    assert len(restart_provider.fetches) == 1
    runtime.close()


def test_scan_repairs_prepared_artifact_before_querying_other_tasks(tmp_path, monkeypatch):
    provider = FakeProvider()
    runtime = _make_runtime(tmp_path, monkeypatch, provider)
    repair_id = _observe_success(runtime, provider)
    runtime.fetch_provider_video_task("p", repair_id)
    with runtime._save_lock:
        repair_task = runtime.scripts["p"].video_tasks[0]
        repair_task.status = "processing"
        repair_task.video_url = None
        repair_task.provider_execution = module.provider_recovery.record_with_changes(
            repair_task.provider_execution,
            phase="observed",
            artifact=None,
        )
        query_id = _admit(runtime)
        # _admit committed while lock is reentrant; submit makes query candidate.
    runtime.submit_provider_video_task("p", query_id)
    provider.fetches.clear()
    provider.queries.clear()

    result = _dispatcher(runtime).run_once()

    tasks = {task.id: task for task in runtime.read_committed_script("p").video_tasks}
    assert result == ("repaired", "p", repair_id)
    assert tasks[repair_id].status == "completed"
    assert provider.fetches == []
    assert provider.queries == []
    runtime.close()


def test_scan_skips_accepted_missing_handle_legacy_and_terminal_states(tmp_path, monkeypatch):
    provider = FakeProvider()
    runtime = _make_runtime(tmp_path, monkeypatch, provider)
    accepted_id = _admit(runtime)
    legacy_script, legacy_id = runtime.create_video_task(
        "p", "uploads/source.png", "legacy", model="wan2.6-i2v-flash", frame_id="frame", resolution="720p", duration=5,
    )
    submitted_id = _submit(runtime, provider)
    with runtime._save_lock:
        tasks = {task.id: task for task in runtime.scripts["p"].video_tasks}
        tasks[accepted_id].status = "needs_confirmation"
        tasks[legacy_id].status = "processing"
        tasks[submitted_id].status = "completed"
        tasks[submitted_id].provider_execution = module.provider_recovery.record_with_changes(
            tasks[submitted_id].provider_execution,
            phase="completed",
            artifact={
                "schema_version": 1,
                "attempt_id": tasks[submitted_id].provider_execution.attempt_id,
                "handle": tasks[submitted_id].provider_execution.handle,
                "relative_path": "video/existing.mp4",
                "sha256": hashlib.sha256(b"existing").hexdigest(),
                "size_bytes": len(b"existing"),
            },
        )
        runtime._commit_storage("projects.json")
    provider.submits.clear()
    provider.queries.clear()

    result = _dispatcher(runtime).run_once()

    assert result is None
    assert provider.submits == []
    assert provider.queries == []
    runtime.close()


def test_worker_duplicate_wakes_process_one_task_at_a_time(tmp_path, monkeypatch):
    provider = FakeProvider()
    runtime = _make_runtime(tmp_path, monkeypatch, provider)
    first = _submit(runtime, provider)
    provider.next_task_id = "remote-task-2"
    second = _submit(runtime, provider)
    provider.queries.clear()
    provider.query_status = ProviderTaskStatus.RUNNING
    dispatcher = _dispatcher(runtime, interval_seconds=60)

    dispatcher.start()
    dispatcher.wake()
    dispatcher.wake()
    assert dispatcher.wait_for_idle(timeout=2)
    dispatcher.request_stop()
    dispatcher.join(timeout=2)

    assert len(provider.queries) == 2
    statuses = {task.id: task.provider_execution.phase for task in runtime.read_committed_script("p").video_tasks if task.id in {first, second}}
    assert list(statuses.values()).count("observed") == 2
    runtime.close()


def test_transient_query_unavailable_gets_bounded_memory_backoff(tmp_path, monkeypatch):
    from src.models.provider_tasks import ProviderQueryError

    provider = FakeProvider()
    runtime = _make_runtime(tmp_path, monkeypatch, provider)
    task_id = _submit(runtime, provider)
    provider.submits.clear()
    provider.query_error = ProviderQueryError("temporary")

    dispatcher = _dispatcher(runtime, transient_backoff_seconds=60)
    first = dispatcher.run_once()
    second = dispatcher.run_once()

    saved = runtime.read_committed_script("p").video_tasks[0]
    assert first == ("transient", "p", task_id)
    assert second is None
    assert len(provider.queries) == 1
    assert saved.status == "needs_confirmation"
    assert saved.error == "provider_query_unavailable"
    assert provider.submits == []
    runtime.close()


def test_stale_owner_change_during_dispatch_does_not_write_late_result(tmp_path, monkeypatch):
    provider = FakeProvider()
    runtime = _make_runtime(tmp_path, monkeypatch, provider)
    task_id = _submit(runtime, provider)
    provider.submits.clear()

    def replace_owner(handle, *, credential):
        provider.queries.append((handle, credential))
        with runtime._save_lock:
            runtime.scripts["p"].video_tasks = []
            runtime._commit_storage("projects.json")
        return module.provider_recovery.ProviderObservation(
            task_id=handle.task_id,
            status=ProviderTaskStatus.SUCCEEDED,
            raw_status="SUCCEEDED",
            source_handle=handle,
        )

    provider.query = replace_owner

    result = _dispatcher(runtime).run_once()

    assert result == ("stale", "p", task_id)
    assert runtime.read_committed_script("p").video_tasks == []
    assert provider.submits == []
    runtime.close()


def test_request_stop_before_close_prevents_late_write_after_pipeline_close(tmp_path, monkeypatch):
    provider = FakeProvider()
    provider.block_query = True
    runtime = _make_runtime(tmp_path, monkeypatch, provider)
    _submit(runtime, provider)
    dispatcher = _dispatcher(runtime, interval_seconds=60)

    dispatcher.start()
    dispatcher.wake()
    assert provider.network_entered.wait(2), "query did not start"
    dispatcher.request_stop()
    runtime.close()
    provider.network_release.set()
    dispatcher.join(timeout=2)

    assert not dispatcher.is_running
    with pytest.raises(StorageError):
        runtime.assert_storage_available()

class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def test_start_processes_immediately_without_manual_wake(tmp_path, monkeypatch):
    provider = FakeProvider()
    runtime = _make_runtime(tmp_path, monkeypatch, provider)
    _submit(runtime, provider)
    provider.queries.clear()
    dispatcher = StudioProviderRecoveryDispatcher(runtime, interval_seconds=60, transient_backoff_seconds=1)

    dispatcher.start()
    assert dispatcher.wait_for_idle(timeout=2)
    dispatcher.request_stop()
    dispatcher.join(timeout=2)

    assert len(provider.queries) == 1
    runtime.close()


def test_running_query_gets_bounded_backoff_so_later_task_can_advance(tmp_path, monkeypatch):
    clock = FakeClock()
    provider = FakeProvider()
    runtime = _make_runtime(tmp_path, monkeypatch, provider)
    first = _submit(runtime, provider)
    second = _submit(runtime, provider)
    provider.queries.clear()
    dispatcher = _dispatcher(runtime, transient_backoff_seconds=10, time_fn=clock)

    assert dispatcher.run_once() == ("queried", "p", first)
    assert dispatcher.run_once() == ("queried", "p", second)

    queried = [call[0].task_id for call in provider.queries]
    tasks = {task.id: task for task in runtime.read_committed_script("p").video_tasks}
    assert len(queried) == 2
    assert tasks[first].provider_execution.phase == "observed"
    assert tasks[second].provider_execution.phase == "observed"
    runtime.close()


def test_query_unavailable_retries_after_backoff_without_post(tmp_path, monkeypatch):
    from src.models.provider_tasks import ProviderQueryError

    clock = FakeClock()
    provider = FakeProvider()
    runtime = _make_runtime(tmp_path, monkeypatch, provider)
    task_id = _submit(runtime, provider)
    provider.submits.clear()
    provider.query_error = ProviderQueryError("temporary")
    dispatcher = _dispatcher(runtime, transient_backoff_seconds=10, time_fn=clock)

    assert dispatcher.run_once() == ("transient", "p", task_id)
    assert dispatcher.run_once() is None
    provider.query_error = None
    clock.advance(10.1)
    assert dispatcher.run_once() == ("queried", "p", task_id)

    assert provider.submits == []
    assert len(provider.queries) == 2
    assert runtime.read_committed_script("p").video_tasks[0].status == "processing"
    runtime.close()


def test_prepared_proof_repairs_even_when_credentials_required(tmp_path, monkeypatch):
    provider = FakeProvider()
    runtime = _make_runtime(tmp_path, monkeypatch, provider)
    task_id = _observe_success(runtime, provider)
    runtime.fetch_provider_video_task("p", task_id)
    with runtime._save_lock:
        task = runtime.scripts["p"].video_tasks[0]
        task.status = "needs_confirmation"
        task.error = "provider_credentials_required"
        task.video_url = None
        task.provider_execution = module.provider_recovery.record_with_changes(
            task.provider_execution,
            phase="observed",
            artifact=None,
        )
        runtime._commit_storage("projects.json")
    provider.fetches.clear()
    provider.queries.clear()

    assert _dispatcher(runtime).run_once() == ("repaired", "p", task_id)

    saved = runtime.read_committed_script("p").video_tasks[0]
    assert saved.status == "completed"
    assert provider.fetches == []
    assert provider.queries == []
    runtime.close()


def test_unexpected_provider_error_is_logged_backed_off_and_worker_continues_other_jobs(tmp_path, monkeypatch, caplog):
    clock = FakeClock()
    provider = FakeProvider()
    runtime = _make_runtime(tmp_path, monkeypatch, provider)
    first = _submit(runtime, provider)
    provider.next_task_id = "remote-task-2"
    second = _submit(runtime, provider)
    provider.queries.clear()
    failing_task_id = runtime.read_committed_script("p").video_tasks[0].provider_execution.handle["task_id"]

    def sometimes_fails(handle, *, credential):
        if handle.task_id == failing_task_id:
            provider.queries.append((handle, credential))
            raise RuntimeError("boom")
        return FakeProvider.query(provider, handle, credential=credential)

    provider.query = sometimes_fails
    dispatcher = _dispatcher(runtime, transient_backoff_seconds=10, time_fn=clock)

    with caplog.at_level("ERROR"):
        assert dispatcher.run_once() == ("error", "p", first)
        assert dispatcher.run_once() == ("queried", "p", second)

    assert "Provider recovery dispatcher failed" in caplog.text
    assert dispatcher.is_running is False
    assert len(provider.queries) == 2
    runtime.close()


def test_round_robin_query_fairness_reaches_third_running_job_without_starvation(tmp_path, monkeypatch):
    clock = FakeClock()
    provider = FakeProvider()
    runtime = _make_runtime(tmp_path, monkeypatch, provider)
    first = _submit(runtime, provider)
    provider.next_task_id = "remote-task-2"
    second = _submit(runtime, provider)
    provider.next_task_id = "remote-task-3"
    third = _submit(runtime, provider)
    provider.queries.clear()
    dispatcher = StudioProviderRecoveryDispatcher(
        runtime,
        interval_seconds=30,
        transient_backoff_seconds=60,
        time_fn=clock,
    )

    assert dispatcher.run_once() == ("queried", "p", first)
    clock.advance(30)
    assert dispatcher.run_once() == ("queried", "p", second)
    clock.advance(30)
    assert dispatcher.run_once() == ("queried", "p", third)

    queried = [call[0].task_id for call in provider.queries]
    assert queried == ["remote-task-1", "remote-task-2", "remote-task-3"]
    runtime.close()


def test_succeeded_query_can_fetch_on_next_scan_without_waiting_for_query_backoff(tmp_path, monkeypatch):
    clock = FakeClock()
    provider = FakeProvider()
    runtime = _make_runtime(tmp_path, monkeypatch, provider)
    task_id = _submit(runtime, provider)
    provider.query_status = ProviderTaskStatus.SUCCEEDED
    provider.query_raw_status = "SUCCEEDED"
    provider.query_video_url = "https://media.example.invalid/result.mp4"
    dispatcher = _dispatcher(runtime, transient_backoff_seconds=60, time_fn=clock)

    assert dispatcher.run_once() == ("queried", "p", task_id)
    assert dispatcher.run_once() == ("fetched", "p", task_id)

    saved = runtime.read_committed_script("p").video_tasks[0]
    assert saved.status == "completed"
    assert len(provider.queries) == 1
    assert len(provider.fetches) == 1
    runtime.close()


def test_fetch_unavailable_retries_after_backoff_without_false_success(tmp_path, monkeypatch):
    from src.models.provider_tasks import ProviderFetchError

    clock = FakeClock()
    provider = FakeProvider()
    runtime = _make_runtime(tmp_path, monkeypatch, provider)
    task_id = _observe_success(runtime, provider)
    provider.fetch_error = ProviderFetchError("temporary fetch")
    dispatcher = _dispatcher(runtime, transient_backoff_seconds=10, time_fn=clock)

    assert dispatcher.run_once() == ("transient", "p", task_id)
    assert dispatcher.run_once() is None
    provider.fetch_error = None
    clock.advance(10.1)
    assert dispatcher.run_once() == ("fetched", "p", task_id)

    saved = runtime.read_committed_script("p").video_tasks[0]
    assert saved.status == "completed"
    assert len(provider.fetches) == 2
    runtime.close()


def test_start_rolls_back_unstarted_thread_when_thread_start_fails(tmp_path, monkeypatch):
    provider = FakeProvider()
    runtime = _make_runtime(tmp_path, monkeypatch, provider)
    dispatcher = _dispatcher(runtime)

    class FailingThread:
        def __init__(self, *args, **kwargs):
            self.started = False

        def start(self):
            raise RuntimeError("thread start failed")

        def is_alive(self):
            return self.started

        def join(self, timeout=None):
            raise AssertionError("unstarted thread must not be joined")

    monkeypatch.setattr(dispatch_module.threading, "Thread", FailingThread)

    with pytest.raises(RuntimeError, match="thread start failed"):
        dispatcher.start()

    dispatcher.close()
    assert not dispatcher.is_running
    runtime.close()


def test_prepared_artifact_missing_is_confirmed_once_then_other_jobs_advance(tmp_path, monkeypatch):
    provider = FakeProvider()
    runtime = _make_runtime(tmp_path, monkeypatch, provider)
    repair_id = _observe_success(runtime, provider)
    runtime.fetch_provider_video_task("p", repair_id)
    with runtime._save_lock:
        repair_task = runtime.scripts["p"].video_tasks[0]
        proof = repair_task.provider_execution.prepared_artifact
        (runtime.data_dir / proof["relative_path"]).unlink()
        repair_task.status = "processing"
        repair_task.video_url = None
        repair_task.provider_execution = module.provider_recovery.record_with_changes(
            repair_task.provider_execution,
            phase="observed",
            artifact=None,
        )
        runtime._commit_storage("projects.json")
    provider.next_task_id = "remote-task-2"
    query_id = _submit(runtime, provider)
    provider.fetches.clear()
    provider.queries.clear()
    provider.skip_fetch_write = True

    dispatcher = _dispatcher(runtime)
    assert dispatcher.run_once() == ("needs_confirmation", "p", repair_id)
    assert dispatcher.run_once() == ("queried", "p", query_id)

    tasks = {task.id: task for task in runtime.read_committed_script("p").video_tasks}
    assert tasks[repair_id].status == "needs_confirmation"
    assert tasks[repair_id].error == "provider_fetch_artifact_missing"
    assert len(provider.fetches) == 1
    assert len(provider.queries) == 1
    runtime.close()


def test_prepared_artifact_invalid_is_confirmed_once_then_other_jobs_advance(tmp_path, monkeypatch):
    provider = FakeProvider()
    runtime = _make_runtime(tmp_path, monkeypatch, provider)
    repair_id = _observe_success(runtime, provider)
    runtime.fetch_provider_video_task("p", repair_id)
    with runtime._save_lock:
        repair_task = runtime.scripts["p"].video_tasks[0]
        proof = dict(repair_task.provider_execution.prepared_artifact)
        (runtime.data_dir / proof["relative_path"]).write_bytes(b"tampered final bytes")
        repair_task.status = "processing"
        repair_task.video_url = None
        repair_task.provider_execution = module.provider_recovery.record_with_changes(
            repair_task.provider_execution,
            phase="observed",
            artifact=None,
        )
        runtime._commit_storage("projects.json")
    provider.next_task_id = "remote-task-2"
    query_id = _submit(runtime, provider)
    provider.fetches.clear()
    provider.queries.clear()

    dispatcher = _dispatcher(runtime)
    assert dispatcher.run_once() == ("needs_confirmation", "p", repair_id)
    assert dispatcher.run_once() == ("queried", "p", query_id)

    tasks = {task.id: task for task in runtime.read_committed_script("p").video_tasks}
    assert tasks[repair_id].status == "needs_confirmation"
    assert tasks[repair_id].error == "provider_fetch_artifact_invalid"
    assert provider.fetches == []
    assert len(provider.queries) == 1
    runtime.close()


def test_prepared_artifact_target_invalid_is_confirmed_once_then_other_jobs_advance(tmp_path, monkeypatch):
    provider = FakeProvider()
    runtime = _make_runtime(tmp_path, monkeypatch, provider)
    repair_id = _observe_success(runtime, provider)
    runtime.fetch_provider_video_task("p", repair_id)
    changed_id = f"changed-{repair_id}"
    with runtime._save_lock:
        repair_task = runtime.scripts["p"].video_tasks[0]
        repair_task.id = changed_id
        repair_task.status = "processing"
        repair_task.video_url = None
        repair_task.provider_execution = module.provider_recovery.record_with_changes(
            repair_task.provider_execution,
            phase="observed",
            artifact=None,
        )
        runtime._commit_storage("projects.json")
    provider.next_task_id = "remote-task-2"
    query_id = _submit(runtime, provider)
    provider.fetches.clear()
    provider.queries.clear()

    dispatcher = _dispatcher(runtime)
    assert dispatcher.run_once() == ("needs_confirmation", "p", changed_id)
    assert dispatcher.run_once() == ("queried", "p", query_id)

    tasks = {task.id: task for task in runtime.read_committed_script("p").video_tasks}
    assert tasks[changed_id].status == "needs_confirmation"
    assert tasks[changed_id].error == "provider_fetch_artifact_target_invalid"
    assert provider.fetches == []
    assert len(provider.queries) == 1
    runtime.close()


def test_prepared_artifact_with_credentials_error_still_repairs_without_http(tmp_path, monkeypatch):
    provider = FakeProvider()
    runtime = _make_runtime(tmp_path, monkeypatch, provider)
    task_id = _observe_success(runtime, provider)
    runtime.fetch_provider_video_task("p", task_id)
    with runtime._save_lock:
        task = runtime.scripts["p"].video_tasks[0]
        task.status = "needs_confirmation"
        task.error = "provider_credentials_required"
        task.video_url = None
        task.provider_execution = module.provider_recovery.record_with_changes(
            task.provider_execution,
            phase="observed",
            artifact=None,
        )
        runtime._commit_storage("projects.json")
    _install_provider(runtime, object(), secret="")

    assert _dispatcher(runtime).run_once() == ("repaired", "p", task_id)

    saved = runtime.read_committed_script("p").video_tasks[0]
    assert saved.status == "completed"
    runtime.close()


def test_worker_logs_unexpected_scan_exception_and_continues_next_interval(tmp_path, monkeypatch, caplog):
    provider = FakeProvider()
    runtime = _make_runtime(tmp_path, monkeypatch, provider)
    _submit(runtime, provider)
    provider.queries.clear()
    clock = FakeClock()
    dispatcher = _dispatcher(runtime, interval_seconds=0.01, transient_backoff_seconds=1, time_fn=clock)
    calls = {"count": 0}
    real_read = runtime.read_committed_projects

    def flaky_read():
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("scan exploded")
        return real_read()

    monkeypatch.setattr(runtime, "read_committed_projects", flaky_read)

    with caplog.at_level("ERROR"):
        dispatcher.start()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and not provider.queries:
            time.sleep(0.01)
        dispatcher.request_stop()
        dispatcher.join(timeout=2)

    assert "Provider recovery dispatcher scan failed" in caplog.text
    assert len(provider.queries) == 1
    runtime.close()
