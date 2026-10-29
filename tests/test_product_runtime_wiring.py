"""Product-layer provider runtime wiring across Studio and Atelier."""

from __future__ import annotations

from unittest.mock import Mock

from fastapi.testclient import TestClient

from src.apps.comic_gen import api
from src.apps.comic_gen.models import Character, Scene, Script, StoryboardFrame
from src.models.provider_runtime import ProviderRecoveryRuntime, ProviderRuntimeBuildResult
from src.models.provider_tasks import ProviderScope
from tests.test_atelier_generation_app import seed as seed_atelier
from tests.test_atelier_generation_admission import request as atelier_request
from tests.test_atelier_generation_execution import FakeProvider as AtelierFakeProvider
from tests.test_studio_provider_recovery import PNG_BYTES, _admission_request


CLASSIC_SCOPE = ProviderScope(
    provider="dashscope",
    backend="dashscope",
    gateway="dashscope",
    base_url="https://dashscope.aliyuncs.com",
    region="cn-beijing",
)
WORKSPACE_SCOPE = ProviderScope(
    provider="dashscope",
    backend="dashscope",
    gateway="dashscope_workspace",
    base_url="https://dashscope.aliyuncs.com/api/v1/workspaces/ws-cn-beijing",
    region="cn-beijing",
    workspace_id="ws-cn-beijing",
)


def _workspace_env(monkeypatch) -> None:
    monkeypatch.setenv("DASHSCOPE_API_KEY", "workspace-secret")
    monkeypatch.setenv("DASHSCOPE_BASE_URL", WORKSPACE_SCOPE.base_url)
    monkeypatch.setenv("DASHSCOPE_REGION", "cn-beijing")
    monkeypatch.setenv("DASHSCOPE_WORKSPACE_ID", "ws-cn-beijing")
    monkeypatch.delenv("DASHSCOPE_WORKSPACE", raising=False)
    monkeypatch.delenv("DASHSCOPE_ACCOUNT_IDENTITY", raising=False)
    monkeypatch.delenv("DASHSCOPE_ACCOUNT_ID", raising=False)


def _runtime(scope: ProviderScope, *, provider=None, secret="workspace-secret") -> ProviderRecoveryRuntime:
    return ProviderRecoveryRuntime(
        scope=scope,
        secret_resolver=lambda: secret,
        provider=provider or object(),
        max_bytes=1024 * 1024,
    )


def _patch_selector_builders(monkeypatch, *, classic_provider=None, workspace_provider=None):
    calls: list[tuple[str, object]] = []
    classic_runtime = _runtime(CLASSIC_SCOPE, provider=classic_provider)
    workspace_runtime = _runtime(WORKSPACE_SCOPE, provider=workspace_provider)

    def admission(runtime_environment, *, output_root, provider_model_id, gateway):
        calls.append(("admission", provider_model_id, gateway, runtime_environment.snapshot().get("DASHSCOPE_WORKSPACE_ID")))
        if provider_model_id in {"wan2.6-i2v", "wan2.6-i2v-flash", "wan2.7-i2v"} and gateway == "dashscope":
            return ProviderRuntimeBuildResult(classic_runtime)
        if provider_model_id == "wan3.0-video" and gateway == "dashscope_workspace":
            return ProviderRuntimeBuildResult(workspace_runtime)
        return ProviderRuntimeBuildResult(None, "unsupported_provider_model")

    def scope(runtime_environment, *, output_root, scope):
        calls.append(("scope", scope.gateway, scope.base_url, runtime_environment.snapshot().get("DASHSCOPE_WORKSPACE_ID")))
        if scope == CLASSIC_SCOPE:
            return ProviderRuntimeBuildResult(classic_runtime)
        if scope == WORKSPACE_SCOPE:
            return ProviderRuntimeBuildResult(workspace_runtime)
        return ProviderRuntimeBuildResult(None, "unsupported_provider_scope")

    monkeypatch.setattr(api, "build_provider_runtime_for_admission", admission, raising=False)
    monkeypatch.setattr(api, "build_provider_runtime_for_scope", scope, raising=False)
    return calls, classic_runtime, workspace_runtime


def _seed_studio(app, root) -> None:
    pipeline = app.state.pipeline
    image = pipeline.data_dir / "uploads/source.png"
    image.parent.mkdir(parents=True, exist_ok=True)
    image.write_bytes(PNG_BYTES)
    pipeline.scripts["p"] = Script(
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
    pipeline._commit_storage("projects.json", "series.json", "library_assets.json")


def test_atelier_wan2_admission_uses_classic_runtime_from_workspace_snapshot(monkeypatch, tmp_path):
    _workspace_env(monkeypatch)
    calls, _, _ = _patch_selector_builders(monkeypatch)
    app = api.create_app(data_dir=tmp_path, load_config=False, recover_orphan_tasks=False)

    with TestClient(app) as client:
        seed_atelier(app, tmp_path)
        schedule = Mock(return_value=True)
        monkeypatch.setattr(app.state.atelier_generation_dispatcher, "schedule", schedule)
        response = client.post(
            "/atelier/projects/p/nodes/video/video_candidates",
            json=atelier_request().model_dump(exclude_unset=True),
        )

    assert response.status_code == 200, response.text
    ack = response.json()
    assert ack["attempt"]["provider_execution"]["scope"] == CLASSIC_SCOPE.to_dict()
    assert ("admission", "wan2.7-i2v", "dashscope", "ws-cn-beijing") in calls
    schedule.assert_called_once()


def test_atelier_execution_restores_original_classic_scope_from_workspace_snapshot(monkeypatch, tmp_path):
    _workspace_env(monkeypatch)
    provider = AtelierFakeProvider()
    calls, _, _ = _patch_selector_builders(monkeypatch, classic_provider=provider)
    app = api.create_app(data_dir=tmp_path, load_config=False, recover_orphan_tasks=False)

    with TestClient(app) as client:
        seed_atelier(app, tmp_path)
        monkeypatch.setattr(app.state.atelier_generation_dispatcher, "schedule", Mock(return_value=True))
        created = client.post(
            "/atelier/projects/p/nodes/video/video_candidates",
            json=atelier_request().model_dump(exclude_unset=True),
        )
        assert created.status_code == 200, created.text
        attempt = created.json()["attempt"]
        progressed = client.post(
            f"/atelier/projects/p/generation/attempts/{attempt['attempt_id']}/continue",
            json={
                "candidate_id": attempt["candidate_id"],
                "expected_attempt_revision": attempt["attempt_revision"],
                "execution_fingerprint": attempt["provider_execution"]["fingerprint"],
            },
        )

    assert progressed.status_code == 200, progressed.text
    assert provider.submits and provider.submits[0][0].scope == CLASSIC_SCOPE
    assert ("scope", "dashscope", CLASSIC_SCOPE.base_url, "ws-cn-beijing") in calls


def test_studio_wan2_admission_uses_classic_runtime_from_workspace_snapshot(monkeypatch, tmp_path):
    _workspace_env(monkeypatch)
    calls, _, _ = _patch_selector_builders(monkeypatch)
    app = api.create_app(data_dir=tmp_path, load_config=False, recover_orphan_tasks=False)

    with TestClient(app):
        _seed_studio(app, tmp_path)
        script, task_id = app.state.pipeline.admit_provider_video_task("p", _admission_request())

    task = next(item for item in script.video_tasks if item.id == task_id)
    assert task.provider_execution.scope == CLASSIC_SCOPE.to_dict()
    assert ("admission", "wan2.6-i2v-flash", "dashscope", "ws-cn-beijing") in calls


def test_startup_dispatchers_start_for_scope_recovery_without_current_classic_runtime(monkeypatch, tmp_path):
    _workspace_env(monkeypatch)
    _patch_selector_builders(monkeypatch)

    studio_started = []
    atelier_started = []

    class StudioDispatcherSpy:
        def __init__(self, pipeline):
            self.pipeline = pipeline
            self.is_running = False
        def start(self):
            self.is_running = True
            studio_started.append(self)
        def wake(self):
            pass
        def request_stop(self):
            self.is_running = False
        def join(self, timeout=None):
            pass

    def atelier_start(self):
        self._started_by_test = True
        atelier_started.append(self)

    from src.apps.comic_gen import provider_recovery_dispatcher as studio_dispatcher
    from src.apps.atelier import generation_dispatcher as atelier_dispatcher

    monkeypatch.setattr(studio_dispatcher, "StudioProviderRecoveryDispatcher", StudioDispatcherSpy)
    monkeypatch.setattr(atelier_dispatcher.AtelierGenerationDispatcher, "start", atelier_start)
    monkeypatch.setattr(atelier_dispatcher.AtelierGenerationDispatcher, "wake", lambda self: None)

    app = api.create_app(data_dir=tmp_path, load_config=False, recover_orphan_tasks=True)
    with TestClient(app):
        assert app.state.provider_recovery_runtime is None
        assert studio_started
        assert atelier_started


def test_managed_selector_returns_stable_identity_under_parallel_admission(monkeypatch, tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    import time

    _workspace_env(monkeypatch)
    calls = []

    def admission(runtime_environment, *, output_root, provider_model_id, gateway):
        calls.append((provider_model_id, gateway))
        time.sleep(0.01)
        return ProviderRuntimeBuildResult(_runtime(CLASSIC_SCOPE, provider=object()))

    monkeypatch.setattr(api, "build_provider_runtime_for_admission", admission, raising=False)
    app = api.create_app(data_dir=tmp_path, load_config=False, recover_orphan_tasks=False)

    with TestClient(app):
        selector = app.state.provider_runtime_selector
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(
                lambda _: selector.for_admission(provider_model_id="wan2.7-i2v", gateway="dashscope"),
                range(8),
            ))

    assert len(calls) == 1
    assert all(result.runtime is results[0].runtime for result in results)


def test_managed_selector_is_app_local_for_same_workspace_snapshot(monkeypatch, tmp_path):
    _workspace_env(monkeypatch)
    calls = []

    def admission(runtime_environment, *, output_root, provider_model_id, gateway):
        calls.append(output_root)
        return ProviderRuntimeBuildResult(_runtime(CLASSIC_SCOPE, provider={"root": str(output_root)}))

    monkeypatch.setattr(api, "build_provider_runtime_for_admission", admission, raising=False)
    app_a = api.create_app(data_dir=tmp_path / "a", load_config=False, recover_orphan_tasks=False)
    app_b = api.create_app(data_dir=tmp_path / "b", load_config=False, recover_orphan_tasks=False)

    with TestClient(app_a), TestClient(app_b):
        runtime_a = app_a.state.provider_runtime_selector.for_admission(provider_model_id="wan2.7-i2v", gateway="dashscope").runtime
        runtime_b = app_b.state.provider_runtime_selector.for_admission(provider_model_id="wan2.7-i2v", gateway="dashscope").runtime

    assert runtime_a is not runtime_b
    assert runtime_a.provider["root"].endswith("/a")
    assert runtime_b.provider["root"].endswith("/b")
    assert calls == [tmp_path / "a", tmp_path / "b"]


def test_injected_classic_runtime_rejects_unsupported_admission_without_fallback():
    selector = api._InjectedProviderRuntimeSelector(_runtime(CLASSIC_SCOPE))

    result = selector.for_admission(provider_model_id="wan3.0-video", gateway="dashscope")

    assert result.runtime is None
    assert result.reason == "unsupported_provider_model"


def test_recover_false_starts_no_dispatchers_for_workspace_scope(monkeypatch, tmp_path):
    _workspace_env(monkeypatch)
    _patch_selector_builders(monkeypatch)
    studio_started = []
    atelier_started = []

    class StudioDispatcherSpy:
        def __init__(self, pipeline):
            self.pipeline = pipeline
        def start(self):
            studio_started.append(self)
        def wake(self):
            pass
        def request_stop(self):
            pass
        def join(self, timeout=None):
            pass

    from src.apps.comic_gen import provider_recovery_dispatcher as studio_dispatcher
    from src.apps.atelier import generation_dispatcher as atelier_dispatcher

    monkeypatch.setattr(studio_dispatcher, "StudioProviderRecoveryDispatcher", StudioDispatcherSpy)
    monkeypatch.setattr(atelier_dispatcher.AtelierGenerationDispatcher, "start", lambda self: atelier_started.append(self))

    app = api.create_app(data_dir=tmp_path, load_config=False, recover_orphan_tasks=False)
    with TestClient(app):
        assert app.state.provider_recovery_runtime is None

    assert studio_started == []
    assert atelier_started == []
