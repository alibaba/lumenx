"""Provider runtime registration at the app-owned boundary."""

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from src.apps.comic_gen import api
from src.apps.comic_gen.provider_recovery import StudioProviderRecoveryRuntime
from src.apps.comic_gen.provider_runtime import StudioProviderRuntimeBuildResult
from src.models.provider_tasks import ProviderScope
from src.utils.json_storage import StorageError
from tests.test_app_lifecycle import StubPipeline


SCOPE = ProviderScope(
    provider="dashscope",
    backend="dashscope",
    gateway="dashscope",
    base_url="https://dashscope.aliyuncs.com",
    region="cn-beijing",
)


def runtime(secret="secret", provider=None):
    return StudioProviderRecoveryRuntime(
        scope=SCOPE,
        secret_resolver=lambda: secret,
        provider=provider or object(),
    )


class DispatcherSpy:
    started = []
    stopped = []

    def __init__(self, pipeline):
        self.pipeline = pipeline
        self.is_running = False
        DispatcherSpy.started.append(self)

    def start(self):
        self.is_running = True

    def request_stop(self):
        self.is_running = False
        DispatcherSpy.stopped.append(self)

    def join(self, timeout=None):
        return None

    def wait_for_idle(self, timeout=None):
        return True


@pytest.fixture(autouse=True)
def reset_spy():
    DispatcherSpy.started = []
    DispatcherSpy.stopped = []


def patch_factory(monkeypatch, result):
    calls = []

    def fake_factory(runtime_environment, *, output_root):
        calls.append((runtime_environment.snapshot(), output_root))
        return result

    monkeypatch.setattr(api, "build_studio_provider_recovery_runtime", fake_factory, raising=False)
    return calls


def patch_dispatcher(monkeypatch):
    from src.apps.comic_gen import provider_recovery_dispatcher as dispatch

    monkeypatch.setattr(dispatch, "StudioProviderRecoveryDispatcher", DispatcherSpy)


def test_configure_runtime_registers_managed_provider_runtime_from_factory(monkeypatch, tmp_path):
    built = runtime("managed-secret")
    calls = patch_factory(monkeypatch, StudioProviderRuntimeBuildResult(built))
    app = api.create_app(data_dir=tmp_path / "media", load_config=False)

    with TestClient(app):
        assert calls and calls[0][1] == tmp_path / "media"
        assert app.state.pipeline.provider_recovery_runtime is built
        assert app.state.provider_recovery_runtime_owner == "managed"
        assert app.state.pipeline.provider_recovery_runtime.resolve_secret() == "managed-secret"


def test_explicit_pipeline_runtime_is_injected_and_not_replaced(monkeypatch, tmp_path):
    injected = runtime("injected-secret")
    pipeline = StubPipeline(data_dir=tmp_path / "media")
    pipeline.provider_recovery_runtime = injected
    calls = patch_factory(monkeypatch, StudioProviderRuntimeBuildResult(runtime("managed-secret")))
    app = api.create_app(data_dir=tmp_path / "media", load_config=False, pipeline_instance=pipeline)

    with TestClient(app):
        assert calls == []
        assert app.state.pipeline.provider_recovery_runtime is injected
        assert app.state.provider_recovery_runtime_owner == "injected"


def test_recover_false_does_not_start_dispatcher_even_with_managed_runtime(monkeypatch, tmp_path):
    patch_dispatcher(monkeypatch)
    patch_factory(monkeypatch, StudioProviderRuntimeBuildResult(runtime()))
    app = api.create_app(data_dir=tmp_path / "media", load_config=False, recover_orphan_tasks=False)

    with TestClient(app):
        assert app.state.pipeline.provider_recovery_runtime is not None
        assert not hasattr(app.state, "provider_recovery_dispatcher")
        assert DispatcherSpy.started == []


def test_recover_true_starts_dispatcher_when_selector_exists(monkeypatch, tmp_path):
    patch_dispatcher(monkeypatch)
    patch_factory(monkeypatch, StudioProviderRuntimeBuildResult(runtime()))
    app = api.create_app(data_dir=tmp_path / "valid", load_config=False, recover_orphan_tasks=True)
    with TestClient(app):
        assert hasattr(app.state, "provider_recovery_dispatcher")
        assert DispatcherSpy.started[-1].pipeline is app.state.pipeline

    DispatcherSpy.started = []
    patch_factory(monkeypatch, StudioProviderRuntimeBuildResult(None, "unsupported_dashscope_endpoint"))
    app = api.create_app(data_dir=tmp_path / "unsupported", load_config=False, recover_orphan_tasks=True)
    with TestClient(app):
        assert app.state.pipeline.provider_recovery_runtime is None
        assert hasattr(app.state, "provider_recovery_dispatcher")
        assert DispatcherSpy.started[-1].pipeline is app.state.pipeline


def test_config_save_success_refreshes_managed_runtime_from_new_snapshot(monkeypatch, tmp_path):
    first = runtime("first")
    second = runtime("second")
    results = [StudioProviderRuntimeBuildResult(first), StudioProviderRuntimeBuildResult(second)]
    calls = []

    def fake_factory(runtime_environment, *, output_root):
        calls.append(runtime_environment.snapshot())
        return results.pop(0)

    monkeypatch.setattr(api, "build_studio_provider_recovery_runtime", fake_factory, raising=False)
    app = api.create_app(data_dir=tmp_path / "media", user_data_dir=tmp_path / "user", load_config=False)

    with TestClient(app) as client:
        assert app.state.pipeline.provider_recovery_runtime is first
        response = client.post("/config/env", json={"DASHSCOPE_API_KEY": "new-secret"})
        assert response.status_code == 200, response.text
        assert app.state.pipeline.provider_recovery_runtime is second
        assert calls[-1]["DASHSCOPE_API_KEY"] == "new-secret"


def test_config_save_failure_keeps_old_environment_and_managed_runtime(monkeypatch, tmp_path):
    first = runtime("first")
    calls = patch_factory(monkeypatch, StudioProviderRuntimeBuildResult(first))

    def fail_save(*args, **kwargs):
        raise StorageError("cannot save")

    monkeypatch.setattr(api, "save_user_config", fail_save)
    app = api.create_app(data_dir=tmp_path / "media", user_data_dir=tmp_path / "user", load_config=False)

    with TestClient(app) as client:
        before_env = app.state.runtime_environment.snapshot()
        response = client.post("/config/env", json={"DASHSCOPE_API_KEY": "new-secret"})
        assert response.status_code == 503
        assert app.state.pipeline.provider_recovery_runtime is first
        assert app.state.runtime_environment.snapshot() == before_env
        assert len(calls) == 1


def test_endpoint_transition_validity_controls_managed_runtime_and_dispatcher(monkeypatch, tmp_path):
    patch_dispatcher(monkeypatch)
    valid = runtime("valid")
    refreshed = runtime("refreshed")
    results = [
        StudioProviderRuntimeBuildResult(None, "unsupported_dashscope_endpoint"),
        StudioProviderRuntimeBuildResult(valid),
        StudioProviderRuntimeBuildResult(None, "unsupported_dashscope_endpoint"),
        StudioProviderRuntimeBuildResult(refreshed),
    ]

    def fake_factory(runtime_environment, *, output_root):
        return results.pop(0)

    monkeypatch.setattr(api, "build_studio_provider_recovery_runtime", fake_factory, raising=False)
    app = api.create_app(data_dir=tmp_path / "media", user_data_dir=tmp_path / "user", load_config=False, recover_orphan_tasks=True)

    with TestClient(app) as client:
        assert app.state.pipeline.provider_recovery_runtime is None
        assert hasattr(app.state, "provider_recovery_dispatcher")
        dispatcher = app.state.provider_recovery_dispatcher
        ok = client.post("/config/env", json={"endpoint_overrides": {"DASHSCOPE_BASE_URL": "https://dashscope.aliyuncs.com"}})
        assert ok.status_code == 200, ok.text
        assert app.state.pipeline.provider_recovery_runtime is valid
        assert app.state.provider_recovery_dispatcher is dispatcher

        unsupported = client.post("/config/env", json={"endpoint_overrides": {"DASHSCOPE_BASE_URL": "https://example.invalid"}})
        assert unsupported.status_code == 200, unsupported.text
        assert app.state.pipeline.provider_recovery_runtime is None
        assert app.state.provider_recovery_dispatcher is dispatcher
        assert dispatcher not in DispatcherSpy.stopped

        restored = client.post("/config/env", json={"endpoint_overrides": {"DASHSCOPE_BASE_URL": "https://dashscope.aliyuncs.com"}})
        assert restored.status_code == 200, restored.text
        assert app.state.pipeline.provider_recovery_runtime is refreshed
        assert app.state.provider_recovery_dispatcher is dispatcher


def test_cleanup_stops_only_owned_dispatcher(monkeypatch, tmp_path):
    patch_dispatcher(monkeypatch)
    patch_factory(monkeypatch, StudioProviderRuntimeBuildResult(runtime()))
    app = api.create_app(data_dir=tmp_path / "media", load_config=False, recover_orphan_tasks=True)

    with TestClient(app):
        dispatcher = app.state.provider_recovery_dispatcher
        assert dispatcher.is_running

    assert dispatcher in DispatcherSpy.stopped


def test_concurrent_config_updates_refresh_runtime_in_saved_order(monkeypatch, tmp_path):
    from threading import Event, Thread

    patch_dispatcher(monkeypatch)
    first = runtime("first")
    slow = runtime("slow")
    fast = runtime("fast")
    entered_slow = Event()
    release_slow = Event()
    calls = []

    def fake_factory(runtime_environment, *, output_root):
        snapshot = runtime_environment.snapshot()
        calls.append(snapshot.get("DASHSCOPE_API_KEY"))
        if snapshot.get("DASHSCOPE_API_KEY") == "slow-secret":
            entered_slow.set()
            assert release_slow.wait(3), "test did not release slow factory"
            return StudioProviderRuntimeBuildResult(slow)
        if snapshot.get("DASHSCOPE_API_KEY") == "fast-secret":
            return StudioProviderRuntimeBuildResult(fast)
        return StudioProviderRuntimeBuildResult(first)

    monkeypatch.setattr(api, "build_studio_provider_recovery_runtime", fake_factory, raising=False)
    app = api.create_app(data_dir=tmp_path / "media", user_data_dir=tmp_path / "user", load_config=False)

    with TestClient(app) as client:
        responses = []

        def post_secret(secret):
            responses.append(client.post("/config/env", json={"DASHSCOPE_API_KEY": secret}))

        slow_thread = Thread(target=post_secret, args=("slow-secret",))
        fast_thread = Thread(target=post_secret, args=("fast-secret",))
        slow_thread.start()
        assert entered_slow.wait(3), "slow factory was not reached"
        fast_thread.start()
        release_slow.set()
        slow_thread.join(3)
        fast_thread.join(3)
        assert not slow_thread.is_alive()
        assert not fast_thread.is_alive()

        assert len(responses) == 2
        assert all(response.status_code == 200 for response in responses)
        assert app.state.runtime_environment.snapshot()["DASHSCOPE_API_KEY"] == "fast-secret"
        assert app.state.pipeline.provider_recovery_runtime is fast
        assert calls == [None, "slow-secret", "fast-secret"]


def test_real_factory_builds_separate_app_owned_runtimes_without_network(monkeypatch, tmp_path):
    for key in (
        "DASHSCOPE_WORKSPACE",
        "DASHSCOPE_WORKSPACE_ID",
        "DASHSCOPE_ACCOUNT_IDENTITY",
        "DASHSCOPE_ACCOUNT_ID",
        "DASHSCOPE_BASE_URL",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "")

    app_a = api.create_app(data_dir=tmp_path / "a", load_config=False, recover_orphan_tasks=False)
    app_b = api.create_app(data_dir=tmp_path / "b", load_config=False, recover_orphan_tasks=False)

    with TestClient(app_a), TestClient(app_b):
        runtime_a = app_a.state.pipeline.provider_recovery_runtime
        runtime_b = app_b.state.pipeline.provider_recovery_runtime
        assert runtime_a is not None and runtime_b is not None
        assert runtime_a is not runtime_b
        assert runtime_a.provider.output_root == str((tmp_path / "a").resolve())
        assert runtime_b.provider.output_root == str((tmp_path / "b").resolve())
        assert app_a.state.provider_recovery_runtime_owner == "managed"
        assert app_b.state.provider_recovery_runtime_owner == "managed"


def test_real_factory_unsupported_snapshot_keeps_dispatcher_without_network(monkeypatch, tmp_path):
    patch_dispatcher(monkeypatch)
    monkeypatch.setenv("DASHSCOPE_WORKSPACE", "unsupported-workspace")
    app = api.create_app(data_dir=tmp_path / "media", load_config=False, recover_orphan_tasks=True)

    with TestClient(app):
        assert app.state.pipeline.provider_recovery_runtime is None
        assert app.state.provider_recovery_runtime_owner == "none"
        assert app.state.provider_recovery_runtime_reason == "unsupported_dashscope_workspace"
        assert hasattr(app.state, "provider_recovery_dispatcher")
        assert DispatcherSpy.started[-1].pipeline is app.state.pipeline


def test_dispatcher_skips_video_without_managed_runtime_but_scans_images():
    from src.apps.comic_gen.provider_recovery_dispatcher import StudioProviderRecoveryDispatcher

    class ImageService:
        scans = 0

        def read_committed_requests(self):
            self.scans += 1
            return []

    class Pipeline:
        provider_recovery_runtime = None
        asset_image_service = ImageService()

        def read_committed_projects(self):
            raise AssertionError("dispatcher should pause before reading projects without runtime")

    pipeline = Pipeline()
    dispatcher = StudioProviderRecoveryDispatcher(pipeline, interval_seconds=0.01)

    assert dispatcher.run_once() is None
    assert pipeline.asset_image_service.scans == 1


def test_config_transition_retains_blocked_worker_and_its_original_receipt(monkeypatch, tmp_path):
    import time
    from threading import Event

    from src.apps.comic_gen import provider_recovery_dispatcher as dispatch
    from src.models.provider_tasks import ProviderTaskStatus
    from tests.test_studio_provider_recovery import FakeProvider, _admission_request
    from tests.test_studio_provider_recovery_dispatcher import _make_runtime

    old_provider = FakeProvider()
    pipeline = _make_runtime(tmp_path, monkeypatch, old_provider)
    _, task_id = pipeline.admit_provider_video_task("p", _admission_request())
    pipeline.submit_provider_video_task("p", task_id)
    captured = pipeline.provider_recovery_runtime
    pipeline.provider_recovery_runtime = None
    old_provider.block_query = True
    old_provider.query_status = ProviderTaskStatus.SUCCEEDED
    old_provider.query_video_url = "https://example.invalid/video.mp4"
    next_provider = FakeProvider()
    replacement = replace(captured, provider=next_provider)
    results = iter([
        StudioProviderRuntimeBuildResult(captured),
        StudioProviderRuntimeBuildResult(None, "unsupported_dashscope_endpoint"),
        StudioProviderRuntimeBuildResult(replacement),
    ])
    monkeypatch.setattr(api, "build_studio_provider_recovery_runtime", lambda *args, **kwargs: next(results))
    cls = dispatch.StudioProviderRecoveryDispatcher
    monkeypatch.setattr(dispatch, "StudioProviderRecoveryDispatcher", lambda p: cls(p, interval_seconds=0.01))
    app = api.create_app(data_dir=pipeline.data_dir, user_data_dir=tmp_path / "user", load_config=False, pipeline_instance=pipeline, recover_orphan_tasks=True)
    try:
        with TestClient(app) as client:
            worker = app.state.provider_recovery_dispatcher
            assert old_provider.network_entered.wait(2)
            response = client.post("/config/env", json={"endpoint_overrides": {"DASHSCOPE_BASE_URL": "https://example.invalid"}})
            assert response.status_code == 200
            assert pipeline.provider_recovery_runtime is None
            assert app.state.provider_recovery_dispatcher is worker and worker.is_running
            assert pipeline.read_committed_script("p").video_tasks[0].provider_execution.handle["task_id"] == "remote-task-1"
            response = client.post("/config/env", json={"endpoint_overrides": {"DASHSCOPE_BASE_URL": "https://dashscope.aliyuncs.com"}})
            assert response.status_code == 200
            assert app.state.provider_recovery_dispatcher is worker
            old_provider.network_release.set()
            deadline = time.monotonic() + 3
            while pipeline.read_committed_script("p").video_tasks[0].status != "completed" and time.monotonic() < deadline:
                Event().wait(0.01)
            task = pipeline.read_committed_script("p").video_tasks[0]
            assert task.id == task_id and task.status == "completed"
            assert task.provider_execution.handle["task_id"] == "remote-task-1"
            assert len(old_provider.submits) == len(old_provider.queries) == len(next_provider.fetches) == 1
            assert next_provider.submits == next_provider.queries == []
        assert not worker.is_running
    finally:
        old_provider.network_release.set()
        pipeline.close()
