"""Provider operation writeback survives ordinary runtime replacement."""

from concurrent.futures import ThreadPoolExecutor

from src.apps.comic_gen.provider_recovery import StudioProviderRecoveryRuntime
from tests.test_studio_provider_recovery import (
    FakeProvider,
    SECRET,
    SCOPE,
    _admission_request,
)
from tests.test_studio_provider_recovery_dispatcher import _make_runtime
from src.models.provider_tasks import ProviderTaskStatus


def _replacement_runtime(provider=None, secret="replacement-secret"):
    return StudioProviderRecoveryRuntime(
        scope=SCOPE,
        secret_resolver=lambda: secret,
        provider=provider or FakeProvider(),
        max_bytes=1024 * 1024,
    )


def test_inflight_submit_keeps_captured_runtime_after_config_replacement(tmp_path, monkeypatch):
    provider = FakeProvider()
    runtime = _make_runtime(tmp_path, monkeypatch, provider)
    try:
        _, task_id = runtime.admit_provider_video_task("p", _admission_request())
        provider.block_submit = True
        replacement_provider = FakeProvider()

        with ThreadPoolExecutor(max_workers=2) as pool:
            pending = pool.submit(runtime.submit_provider_video_task, "p", task_id)
            assert provider.network_entered.wait(2), "submit network did not start"
            runtime.provider_recovery_runtime = _replacement_runtime(replacement_provider)
            provider.network_release.set()
            script = pending.result(timeout=3)

        saved = next(task for task in script.video_tasks if task.id == task_id)
        assert saved.status == "processing"
        assert saved.provider_execution.phase == "submitted"
        assert saved.provider_execution.handle["task_id"] == "remote-task-1"
        assert len(provider.submits) == 1
        assert replacement_provider.submits == []
    finally:
        provider.network_release.set()
        runtime.close()


def test_inflight_query_keeps_captured_runtime_after_config_replacement(tmp_path, monkeypatch):
    provider = FakeProvider()
    runtime = _make_runtime(tmp_path, monkeypatch, provider)
    try:
        _, task_id = runtime.admit_provider_video_task("p", _admission_request())
        runtime.submit_provider_video_task("p", task_id)
        provider.query_status = ProviderTaskStatus.SUCCEEDED
        provider.query_raw_status = "SUCCEEDED"
        provider.query_video_url = "https://media.example.invalid/result.mp4"
        provider.block_query = True
        replacement_provider = FakeProvider()

        with ThreadPoolExecutor(max_workers=2) as pool:
            pending = pool.submit(runtime.query_provider_video_task, "p", task_id)
            assert provider.network_entered.wait(2), "query network did not start"
            runtime.provider_recovery_runtime = _replacement_runtime(replacement_provider)
            provider.network_release.set()
            script = pending.result(timeout=3)

        saved = next(task for task in script.video_tasks if task.id == task_id)
        assert saved.status == "processing"
        assert saved.provider_execution.phase == "observed"
        assert saved.provider_execution.observation["status"] == "succeeded"
        assert len(provider.queries) == 1
        assert replacement_provider.queries == []
    finally:
        provider.network_release.set()
        runtime.close()


def test_inflight_fetch_keeps_captured_runtime_after_config_replacement(tmp_path, monkeypatch):
    provider = FakeProvider()
    runtime = _make_runtime(tmp_path, monkeypatch, provider)
    try:
        _, task_id = runtime.admit_provider_video_task("p", _admission_request())
        runtime.submit_provider_video_task("p", task_id)
        provider.query_status = ProviderTaskStatus.SUCCEEDED
        provider.query_raw_status = "SUCCEEDED"
        provider.query_video_url = "https://media.example.invalid/result.mp4"
        runtime.query_provider_video_task("p", task_id)
        provider.block_fetch = True
        replacement_provider = FakeProvider()

        with ThreadPoolExecutor(max_workers=2) as pool:
            pending = pool.submit(runtime.fetch_provider_video_task, "p", task_id)
            assert provider.network_entered.wait(2), "fetch network did not start"
            runtime.provider_recovery_runtime = _replacement_runtime(replacement_provider)
            provider.network_release.set()
            script = pending.result(timeout=3)

        saved = next(task for task in script.video_tasks if task.id == task_id)
        assert saved.status == "completed"
        assert saved.provider_execution.phase == "completed"
        assert saved.video_url.startswith("video/provider-")
        assert (runtime.data_dir / saved.video_url).read_bytes() == provider.fetch_bytes
        assert len(provider.fetches) == 1
        assert replacement_provider.fetches == []
    finally:
        provider.network_release.set()
        runtime.close()


def test_future_query_after_runtime_replacement_uses_new_credentials_and_fails_closed(tmp_path, monkeypatch):
    provider = FakeProvider()
    runtime = _make_runtime(tmp_path, monkeypatch, provider)
    try:
        _, task_id = runtime.admit_provider_video_task("p", _admission_request())
        runtime.submit_provider_video_task("p", task_id)
        replacement_provider = FakeProvider()
        runtime.provider_recovery_runtime = _replacement_runtime(replacement_provider)

        script = runtime.query_provider_video_task("p", task_id)

        saved = next(task for task in script.video_tasks if task.id == task_id)
        assert saved.status == "needs_confirmation"
        assert saved.error == "provider_credentials_required"
        assert provider.queries == []
        assert replacement_provider.queries == []
    finally:
        runtime.close()


def test_future_query_uses_replacement_client_with_original_credential(tmp_path, monkeypatch):
    provider = FakeProvider()
    runtime = _make_runtime(tmp_path, monkeypatch, provider)
    try:
        _, task_id = runtime.admit_provider_video_task("p", _admission_request())
        runtime.submit_provider_video_task("p", task_id)
        replacement = FakeProvider()
        runtime.provider_recovery_runtime = _replacement_runtime(replacement, SECRET)

        runtime.query_provider_video_task("p", task_id)

        assert provider.queries == []
        assert len(replacement.queries) == 1
        assert replacement.queries[0][0].task_id == "remote-task-1"
        assert replacement.submits == []
    finally:
        runtime.close()
