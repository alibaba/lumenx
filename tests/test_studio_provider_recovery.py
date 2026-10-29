"""Studio provider execution admission is durable and isolated from legacy generate()."""

import hashlib
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from src.apps.comic_gen import pipeline as module
from src.apps.comic_gen.models import Character, ProviderExecutionRecord, Scene, Script, StoryboardFrame
from src.apps.comic_gen.provider_recovery import StudioProviderRecoveryError, StudioProviderRecoveryRuntime
from src.models.wanx import WanxModel
from src.models.provider_tasks import (
    ProviderArtifact,
    ProviderBindingError,
    ProviderHandle,
    ProviderObservation,
    ProviderScope,
    ProviderTaskStatus,
    QueryCapability,
)
from src.utils.generation_intent import IntentValidationError
from src.utils.media_versions import MediaVersion, read_verified_media
from src.utils.json_storage import StorageConflictError, StorageError


PNG_BYTES = b"\x89PNG\r\n\x1a\nprovider-admission-fixture"
SECRET = "example-admission-key"
SCOPE = ProviderScope(
    provider="dashscope",
    backend="dashscope",
    gateway="dashscope",
    base_url="https://dashscope.aliyuncs.com",
    region="cn-beijing",
)


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    for name in ("AssetGenerator", "StoryboardGenerator", "AudioGenerator", "ExportManager"):
        monkeypatch.setattr(module, name, Mock())
    item = module.ComicGenPipeline(data_dir=tmp_path / "data", script_processor=Mock(), warmup_demucs=False)
    item.provider_recovery_runtime = StudioProviderRecoveryRuntime(
        scope=SCOPE,
        secret_resolver=lambda: SECRET,
        provider=object(),
        max_bytes=1024 * 1024,
    )
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
    yield item
    item.close()


def _admission_request(**overrides):
    request = {
        "image_url": "uploads/source.png",
        "prompt": "make a cinematic shot",
        "model": "wan2.6-i2v-flash",
        "frame_id": "frame",
        "resolution": "720p",
        "duration": 5,
        "generate_audio": False,
        "prompt_extend": False,
        "seed": 0,
    }
    request.update(overrides)
    return request


class FakeProvider:
    def __init__(self):
        self.submits = []
        self.queries = []
        self.submit_error = None
        self.query_error = None
        self.query_status = ProviderTaskStatus.RUNNING
        self.query_raw_status = "RUNNING"
        self.query_video_url = None
        self.query_error_code = None
        self.next_task_id = "remote-task-1"
        self.fetch_bytes = b"paid provider video bytes"
        self.fetches = []
        self.fetch_error = None
        self.skip_fetch_callback = False
        self.skip_fetch_write = False
        self.fetch_output_ref_override = None
        self.network_entered = Event()
        self.network_release = Event()
        self.block_submit = False
        self.block_query = False
        self.block_fetch = False
        self.before_submit_callback = None

    def submit(self, request, *, credential, on_submitted):
        self.submits.append((request, credential))
        if self.block_submit:
            self.network_entered.set()
            assert self.network_release.wait(3), "submit network was not released"
        if self.submit_error is not None:
            raise self.submit_error
        handle = ProviderHandle(
            provider_model_id=request.provider_model_id,
            task_id=self.next_task_id,
            scope=request.scope,
            credential_ref=request.credential_ref,
            request_id="submit-request-1",
            query_capability=QueryCapability.BY_TASK_ID,
            submitted_at=123.0,
        )
        if self.before_submit_callback is not None:
            self.before_submit_callback()
        on_submitted(handle)
        return handle

    def query(self, handle, *, credential):
        self.queries.append((handle, credential))
        if self.block_query:
            self.network_entered.set()
            assert self.network_release.wait(3), "query network was not released"
        if self.query_error is not None:
            raise self.query_error
        return ProviderObservation(
            task_id=handle.task_id,
            status=self.query_status,
            raw_status=self.query_raw_status,
            request_id="query-request-1",
            video_url=self.query_video_url,
            error_code=self.query_error_code,
            source_handle=handle,
        )

    def fetch(
        self, handle, observation, *, output_root, output_ref, writer_lock,
        storage_guard, max_bytes, on_prepared=None, publish_policy="replace",
    ):
        self.fetches.append((handle, observation, output_ref))
        if self.block_fetch:
            self.network_entered.set()
            assert self.network_release.wait(3), "fetch network was not released"
        if self.fetch_error is not None:
            raise self.fetch_error
        if max_bytes < len(self.fetch_bytes):
            raise AssertionError("fetch output budget is too small")
        target_ref = self.fetch_output_ref_override or output_ref
        digest = hashlib.sha256(self.fetch_bytes).hexdigest()
        artifact = ProviderArtifact(handle, target_ref, digest, len(self.fetch_bytes))
        if on_prepared is not None and not self.skip_fetch_callback:
            on_prepared(artifact)
        if not self.skip_fetch_write:
            target = output_root / target_ref
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(self.fetch_bytes)
        return artifact


def _install_provider(runtime, provider, secret=SECRET):
    runtime.provider_recovery_runtime = StudioProviderRecoveryRuntime(
        scope=SCOPE, secret_resolver=lambda: secret, provider=provider, max_bytes=1024 * 1024,
    )


def _admit_for_submit(runtime, provider=None):
    provider = provider or FakeProvider()
    _install_provider(runtime, provider)
    _, task_id = runtime.admit_provider_video_task("p", _admission_request())
    return task_id, provider


def _reload_runtime(runtime, monkeypatch, provider, secret=SECRET):
    data_dir = runtime.data_dir
    runtime.close()
    for name in ("AssetGenerator", "StoryboardGenerator", "AudioGenerator", "ExportManager"):
        monkeypatch.setattr(module, name, Mock())
    item = module.ComicGenPipeline(data_dir=data_dir, script_processor=Mock(), warmup_demucs=False)
    _install_provider(item, provider, secret=secret)
    return item


def test_admit_provider_video_task_persists_accepted_record_with_raw_provenance(runtime):
    script, task_id = runtime.admit_provider_video_task("p", _admission_request())

    task = next(item for item in script.video_tasks if item.id == task_id)
    assert task.status == "pending"
    assert task.provider_execution is not None
    assert task.provider_execution.phase == "accepted"
    assert task.provider_execution.handle is None
    assert task.provider_execution.scope == SCOPE.to_dict()
    assert task.provider_execution.credential_ref
    assert len(task.provider_execution.fingerprint) == 64
    assert (runtime.data_dir / "provider_credentials.json").exists()
    assert task.provider_execution.intent["request_fields"] == [
        "model_id",
        "params.audio",
        "params.duration",
        "params.prompt_extend",
        "params.resolution",
        "params.seed",
        "prompt",
    ]
    assert task.provider_execution.intent["sources"]["mode"] == "entry_default"
    assert task.provider_execution.intent["sources"]["params.prompt_extend"] == "request"
    assert "generation_mode" not in task.provider_execution.intent["admission_request_fields"]
    assert task.provider_execution.intent["params"]["prompt_extend"] is False
    assert task.provider_execution.intent["params"]["audio"] is False
    assert task.provider_execution.intent["params"]["seed"] == 0
    frozen = task.provider_execution.media[0]
    import hashlib

    assert frozen["role"] == "first_frame"
    assert frozen["sha256"] == hashlib.sha256(PNG_BYTES).hexdigest()
    assert frozen["size_bytes"] == len(PNG_BYTES)
    assert frozen["declared_media_type"] == "image/png"
    assert task.image_url == "uploads/source.png"

    data_dir = runtime.data_dir
    runtime.close()
    reloaded = module.ComicGenPipeline(data_dir=data_dir, script_processor=Mock(), warmup_demucs=False)
    saved = reloaded.read_committed_script("p").video_tasks[0]
    assert saved.provider_execution.attempt_id == task.provider_execution.attempt_id
    reloaded.close()


def test_provider_admission_rejects_unsupported_without_task_or_provider_call(runtime):
    before = runtime.read_committed_script("p").video_tasks

    with pytest.raises(ProviderBindingError):
        runtime.admit_provider_video_task("p", _admission_request(resolution="480p"))

    after = runtime.read_committed_script("p").video_tasks
    assert before == after == []


def test_provider_admission_accepts_model_id_alias_without_model(runtime):
    request = _admission_request()
    request["model_id"] = request.pop("model")

    script, task_id = runtime.admit_provider_video_task("p", request)

    task = next(item for item in script.video_tasks if item.id == task_id)
    assert task.model == "wan2.6-i2v-flash"
    assert task.provider_execution.provider_model_id == "wan2.6-i2v-flash"


def test_provider_admission_rejects_nulls_unknown_fields_and_model_alias_conflicts(runtime):
    cases = [
        (_admission_request(duration=None), IntentValidationError),
        (_admission_request(watermark=None), StudioProviderRecoveryError),
        (_admission_request(ratio="16:9"), StudioProviderRecoveryError),
        (_admission_request(model="wan2.6-i2v", model_id="wan2.6-i2v-flash"), StudioProviderRecoveryError),
        (_admission_request(model=""), StudioProviderRecoveryError),
    ]

    for request, error_type in cases:
        with pytest.raises(error_type):
            runtime.admit_provider_video_task("p", request)

    assert runtime.read_committed_script("p").video_tasks == []


def test_provider_admission_detaches_request_and_freezes_immutable_media(runtime, monkeypatch):
    request = _admission_request()
    original = module.provider_recovery.build_accepted_submission_and_record

    def mutate_caller(raw, *args, **kwargs):
        request["prompt"] = "mutated prompt"
        return original(raw, *args, **kwargs)

    monkeypatch.setattr(module.provider_recovery, "build_accepted_submission_and_record", mutate_caller)

    script, task_id = runtime.admit_provider_video_task("p", request)
    task = next(item for item in script.video_tasks if item.id == task_id)
    assert task.image_url == "uploads/source.png"
    (runtime.data_dir / task.image_url).write_bytes(b"changed-after-admission")

    saved = runtime.read_committed_script("p").video_tasks[0]
    media_version = MediaVersion(**saved.provider_execution.media[0])
    fixed_bytes = read_verified_media(media_version, output_root=runtime.data_dir, max_bytes=1024 * 1024)
    assert saved.prompt == "make a cinematic shot"
    assert saved.provider_execution.intent["prompt"] == "make a cinematic shot"
    assert saved.provider_execution.media[0]["sha256"] == hashlib.sha256(PNG_BYTES).hexdigest()
    assert fixed_bytes == PNG_BYTES


def test_provider_admission_credential_store_failure_closes_pipeline(runtime, monkeypatch):
    def fail_bind(*args, **kwargs):
        raise StorageError("credential store failed")

    monkeypatch.setattr(runtime._provider_credential_store, "bind", fail_bind)

    with pytest.raises(StorageError):
        runtime.admit_provider_video_task("p", _admission_request())
    with pytest.raises(StorageError):
        runtime.assert_storage_available()


def test_provider_admission_rejects_present_unsupported_fields_even_empty_or_null(runtime):
    for request in (
        _admission_request(asset_id=None),
        _admission_request(reference_video_urls=[]),
        _admission_request(reference_image_urls=[]),
    ):
        with pytest.raises(StudioProviderRecoveryError):
            runtime.admit_provider_video_task("p", request)

    assert runtime.read_committed_script("p").video_tasks == []


def test_provider_admission_secret_resolver_runs_outside_storage_lock(runtime):
    entered = Event()
    release = Event()

    def resolver():
        entered.set()
        assert release.wait(3), "resolver was not released"
        return SECRET

    runtime.provider_recovery_runtime = StudioProviderRecoveryRuntime(
        scope=SCOPE, secret_resolver=resolver, provider=object(), max_bytes=1024 * 1024,
    )

    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(runtime.admit_provider_video_task, "p", _admission_request())
        assert entered.wait(2), "resolver did not start"
        # This read needs the same lifecycle lock; it must not be blocked by the resolver.
        assert runtime.read_committed_script("p").id == "p"
        release.set()
        pending.result(timeout=3)


def test_provider_admission_runtime_change_during_secret_resolution_rejects(runtime):
    entered = Event()
    release = Event()

    def resolver():
        entered.set()
        assert release.wait(3), "resolver was not released"
        return SECRET

    runtime.provider_recovery_runtime = StudioProviderRecoveryRuntime(
        scope=SCOPE, secret_resolver=resolver, provider=object(), max_bytes=1024 * 1024,
    )
    replacement = StudioProviderRecoveryRuntime(
        scope=SCOPE, secret_resolver=lambda: SECRET, provider=object(), max_bytes=1024 * 1024,
    )

    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(runtime.admit_provider_video_task, "p", _admission_request())
        assert entered.wait(2), "resolver did not start"
        runtime.provider_recovery_runtime = replacement
        release.set()
        with pytest.raises(StorageConflictError):
            pending.result(timeout=3)

    assert runtime.read_committed_script("p").video_tasks == []


def test_provider_admission_same_id_owner_replacement_during_secret_resolution_rejects(runtime):
    entered = Event()
    release = Event()

    def resolver():
        entered.set()
        assert release.wait(3), "resolver was not released"
        return SECRET

    runtime.provider_recovery_runtime = StudioProviderRecoveryRuntime(
        scope=SCOPE, secret_resolver=resolver, provider=object(), max_bytes=1024 * 1024,
    )
    replacement = Script(
        id="p",
        title="replacement",
        original_text="same ids",
        created_at=2,
        updated_at=2,
        scenes=[Scene(id="scene", name="Room", description="changed", image_url="uploads/source.png")],
        characters=[],
        frames=[StoryboardFrame(id="frame", scene_id="scene")],
        video_tasks=[],
    )

    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(runtime.admit_provider_video_task, "p", _admission_request())
        assert entered.wait(2), "resolver did not start"
        with runtime._save_lock:
            runtime.scripts["p"] = replacement
            runtime._commit_storage("projects.json")
        release.set()
        with pytest.raises(StorageConflictError):
            pending.result(timeout=3)

    saved = runtime.read_committed_script("p")
    assert saved.title == "replacement"
    assert saved.video_tasks == []


def test_provider_admission_freeze_holds_lifecycle_lock_until_media_read(runtime, monkeypatch):
    entered = Event()
    release = Event()
    original_read = module.provider_recovery.PreparedMedia.from_media_version

    def blocked_read(*args, **kwargs):
        entered.set()
        assert release.wait(3), "media read was not released"
        return original_read(*args, **kwargs)

    monkeypatch.setattr(module.provider_recovery.PreparedMedia, "from_media_version", blocked_read)
    close_entered = Event()
    original_close = runtime.close

    def observed_close():
        close_entered.set()
        return original_close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(runtime.admit_provider_video_task, "p", _admission_request())
        assert entered.wait(2), "media read did not start"
        close_future = pool.submit(observed_close)
        assert close_entered.wait(2), "close did not start"
        with pytest.raises(TimeoutError):
            close_future.result(timeout=0.05)
        release.set()
        pending.result(timeout=3)
        close_future.result(timeout=3)


def test_provider_admission_save_failure_does_not_return_task(runtime, monkeypatch):
    def fail_commit(payloads):
        raise StorageError("boom")

    monkeypatch.setattr(runtime._storage, "commit", fail_commit)

    with pytest.raises(StorageError):
        runtime.admit_provider_video_task("p", _admission_request())

    with pytest.raises(StorageError):
        runtime.assert_storage_available()


def test_provider_admission_owner_change_aborts_without_task(runtime, monkeypatch):
    original = module.provider_recovery.build_accepted_submission_and_record

    def mutate_owner(*args, **kwargs):
        result = original(*args, **kwargs)
        with runtime._save_lock:
            runtime.scripts["p"].frames = []
            runtime._commit_storage("projects.json")
        return result

    monkeypatch.setattr(module.provider_recovery, "build_accepted_submission_and_record", mutate_owner)

    with pytest.raises(StorageConflictError):
        runtime.admit_provider_video_task("p", _admission_request())

    assert runtime.read_committed_script("p").video_tasks == []


def test_process_existing_provider_execution_stops_without_legacy_generate(runtime):
    _, task_id = runtime.admit_provider_video_task("p", _admission_request())
    state = SimpleNamespace(legacy_calls=0)

    def legacy_generate(**kwargs):
        state.legacy_calls += 1
        with open(kwargs["output_path"], "wb") as handle:
            handle.write(b"legacy")

    runtime.video_generator.model = SimpleNamespace(generate=legacy_generate)
    runtime.process_video_task("p", task_id)

    saved = runtime.read_committed_script("p").video_tasks[0]
    assert state.legacy_calls == 0
    assert saved.status == "needs_confirmation"
    assert saved.error == "provider_execution_not_enabled"
    assert saved.provider_execution.phase == "accepted"


def test_legacy_wan_task_without_provider_record_still_uses_generate(runtime):
    runtime.provider_recovery_runtime = None
    _, task_id = runtime.create_video_task(
        "p",
        "uploads/source.png",
        "legacy path",
        model="wan2.6-i2v-flash",
        frame_id="frame",
        resolution="720p",
        duration=5,
    )
    state = SimpleNamespace(legacy_calls=0)

    def legacy_generate(**kwargs):
        state.legacy_calls += 1
        with open(kwargs["output_path"], "wb") as handle:
            handle.write(b"legacy")

    runtime.video_generator.model = SimpleNamespace(generate=legacy_generate)
    runtime.process_video_task("p", task_id)

    saved = runtime.read_committed_script("p").video_tasks[0]
    assert state.legacy_calls == 1
    assert saved.status == "completed"
    assert saved.provider_execution is None


def test_copy_frame_clears_provider_execution_from_cloned_completed_task(runtime):
    script, task_id = runtime.admit_provider_video_task("p", _admission_request())
    task = script.video_tasks[0].model_copy(deep=True)
    task.status = "completed"
    task.video_url = "video/paid.mp4"
    runtime.scripts["p"].video_tasks = [task]
    runtime.scripts["p"].frames[0].selected_video_id = task_id
    runtime._commit_storage("projects.json")

    copied = runtime.copy_frame("p", "frame")

    cloned = [item for item in copied.video_tasks if item.id != task_id][0]
    assert cloned.video_url == "video/paid.mp4"
    assert cloned.provider_execution is None
    assert cloned.provider_task_id is None
    assert copied.frames[1].selected_video_id == cloned.id


def test_admission_requires_explicit_runtime(tmp_path, monkeypatch):
    for name in ("AssetGenerator", "StoryboardGenerator", "AudioGenerator", "ExportManager"):
        monkeypatch.setattr(module, name, Mock())
    item = module.ComicGenPipeline(data_dir=tmp_path / "data", script_processor=Mock(), warmup_demucs=False)
    image = item.data_dir / "uploads/source.png"
    image.parent.mkdir(parents=True, exist_ok=True)
    image.write_bytes(PNG_BYTES)
    item.scripts["p"] = Script(
        id="p", title="project", original_text="source", created_at=1, updated_at=1,
        scenes=[], characters=[], frames=[StoryboardFrame(id="frame", scene_id="scene")], video_tasks=[],
    )
    item._commit_storage("projects.json", "series.json", "library_assets.json")

    with pytest.raises(ValueError, match="provider_recovery_runtime_missing"):
        item.admit_provider_video_task("p", _admission_request())

    assert item.read_committed_script("p").video_tasks == []
    item.close()


def test_cancel_does_not_mark_provider_execution_as_remote_failed(runtime):
    _, task_id = runtime.admit_provider_video_task("p", _admission_request())

    changed = runtime.mark_video_task_failed("p", task_id, "Canceled by user")

    saved = runtime.read_committed_script("p").video_tasks[0]
    assert changed is False
    assert saved.status == "pending"
    assert saved.provider_execution.phase == "accepted"


def test_provider_record_schema_is_strict():
    valid = {
        "schema_version": 1,
        "attempt_id": "attempt",
        "phase": "accepted",
        "provider_model_id": "wan2.6-i2v-flash",
        "intent": {},
        "media": [],
        "scope": SCOPE.to_dict(),
        "credential_ref": "ref",
        "fingerprint": "0" * 64,
    }
    ProviderExecutionRecord(**valid)
    with pytest.raises(ValidationError):
        ProviderExecutionRecord(**{**valid, "schema_version": True})
    with pytest.raises(ValidationError):
        ProviderExecutionRecord(**{**valid, "unexpected": "field"})

def test_submit_save_failure_before_post_makes_zero_provider_calls(runtime, monkeypatch):
    task_id, provider = _admit_for_submit(runtime)

    def fail_commit(payloads):
        raise StorageError("cannot save submitting")

    monkeypatch.setattr(runtime._storage, "commit", fail_commit)

    with pytest.raises(StorageError):
        runtime.submit_provider_video_task("p", task_id)

    assert provider.submits == []
    with pytest.raises(StorageError):
        runtime.assert_storage_available()


def test_submit_handle_save_failure_posts_once_and_restart_never_posts_again(runtime, monkeypatch):
    task_id, provider = _admit_for_submit(runtime)
    real_commit = runtime._storage.commit
    commit_count = {"value": 0}

    def fail_second_commit(payloads):
        commit_count["value"] += 1
        if commit_count["value"] == 2:
            raise StorageError("cannot save handle")
        return real_commit(payloads)

    monkeypatch.setattr(runtime._storage, "commit", fail_second_commit)

    with pytest.raises(StorageError):
        runtime.submit_provider_video_task("p", task_id)

    assert len(provider.submits) == 1
    reloaded_provider = FakeProvider()
    reloaded = _reload_runtime(runtime, monkeypatch, reloaded_provider)
    try:
        script = reloaded.submit_provider_video_task("p", task_id)
        saved = next(item for item in script.video_tasks if item.id == task_id)
        assert saved.status == "needs_confirmation"
        assert saved.error == "provider_submit_receipt_missing"
        assert saved.provider_execution.phase == "submitting"
        assert saved.provider_execution.handle is None
        assert reloaded_provider.submits == []
    finally:
        reloaded.close()


def test_submit_persists_handle_and_restart_query_uses_same_original_task(runtime, monkeypatch):
    task_id, provider = _admit_for_submit(runtime)

    script = runtime.submit_provider_video_task("p", task_id)
    saved = next(item for item in script.video_tasks if item.id == task_id)
    assert len(provider.submits) == 1
    assert saved.status == "processing"
    assert saved.provider_execution.phase == "submitted"
    assert saved.provider_execution.handle["task_id"] == "remote-task-1"

    reloaded_provider = FakeProvider()
    reloaded = _reload_runtime(runtime, monkeypatch, reloaded_provider)
    try:
        script = reloaded.query_provider_video_task("p", task_id)
        queried = next(item for item in script.video_tasks if item.id == task_id)
        assert len(reloaded_provider.submits) == 0
        assert len(reloaded_provider.queries) == 1
        assert reloaded_provider.queries[0][0].task_id == "remote-task-1"
        assert queried.status == "processing"
        assert queried.provider_execution.phase == "observed"
        assert queried.provider_execution.observation["status"] == "running"
    finally:
        reloaded.close()


def test_query_historical_handle_without_intent_or_media_uses_receipt_only(runtime):
    task_id, provider = _admit_for_submit(runtime)
    runtime.submit_provider_video_task("p", task_id)
    with runtime._save_lock:
        task = runtime.scripts["p"].video_tasks[0]
        task.provider_execution.intent = {}
        task.provider_execution.media = []
        runtime._commit_storage("projects.json")

    script = runtime.query_provider_video_task("p", task_id)

    saved = next(item for item in script.video_tasks if item.id == task_id)
    assert len(provider.queries) == 1
    assert provider.queries[0][0].task_id == "remote-task-1"
    assert saved.provider_execution.observation["status"] == "running"


def test_query_changed_key_does_not_call_provider_and_needs_credentials(runtime):
    task_id, provider = _admit_for_submit(runtime)
    runtime.submit_provider_video_task("p", task_id)
    _install_provider(runtime, provider, secret="example-other-key")

    script = runtime.query_provider_video_task("p", task_id)

    saved = next(item for item in script.video_tasks if item.id == task_id)
    assert len(provider.queries) == 0
    assert saved.status == "needs_confirmation"
    assert saved.error == "provider_credentials_required"


def test_submit_network_wait_does_not_block_unrelated_storage_reads(runtime):
    task_id, provider = _admit_for_submit(runtime)
    provider.block_submit = True

    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(runtime.submit_provider_video_task, "p", task_id)
        assert provider.network_entered.wait(2), "submit network did not start"
        assert runtime.read_committed_script("p").id == "p"
        provider.network_release.set()
        pending.result(timeout=3)


def test_submit_replaced_owner_during_network_does_not_overwrite(runtime):
    task_id, provider = _admit_for_submit(runtime)

    def replace_owner():
        with runtime._save_lock:
            runtime.scripts["p"].video_tasks = []
            runtime._commit_storage("projects.json")

    provider.before_submit_callback = replace_owner

    with pytest.raises(StorageConflictError):
        runtime.submit_provider_video_task("p", task_id)

    assert len(provider.submits) == 1
    assert runtime.read_committed_script("p").video_tasks == []


def test_repeated_submit_after_submitting_or_submitted_never_posts_again(runtime):
    task_id, provider = _admit_for_submit(runtime)

    runtime.submit_provider_video_task("p", task_id)
    first_count = len(provider.submits)
    runtime.submit_provider_video_task("p", task_id)

    assert first_count == 1
    assert len(provider.submits) == 1


def test_query_unknown_and_success_do_not_complete_until_fetch(runtime):
    task_id, provider = _admit_for_submit(runtime)
    runtime.submit_provider_video_task("p", task_id)
    provider.query_status = ProviderTaskStatus.UNKNOWN
    provider.query_raw_status = "NEW_PROVIDER_STATE"

    script = runtime.query_provider_video_task("p", task_id)
    unknown = next(item for item in script.video_tasks if item.id == task_id)
    assert unknown.status == "needs_confirmation"
    assert unknown.error == "provider_query_unknown"

    unknown.status = "processing"
    unknown.error = None
    runtime._commit_storage("projects.json")
    provider.query_status = ProviderTaskStatus.SUCCEEDED
    provider.query_raw_status = "SUCCEEDED"
    provider.query_video_url = "https://media.example.invalid/result.mp4"

    script = runtime.query_provider_video_task("p", task_id)
    succeeded = next(item for item in script.video_tasks if item.id == task_id)
    assert succeeded.status == "processing"
    assert succeeded.video_url is None
    assert succeeded.provider_execution.phase == "observed"
    assert succeeded.provider_execution.observation["status"] == "succeeded"


def test_query_failed_and_canceled_are_trusted_terminal_failures(runtime):
    task_id, provider = _admit_for_submit(runtime)
    runtime.submit_provider_video_task("p", task_id)

    provider.query_status = ProviderTaskStatus.FAILED
    provider.query_raw_status = "FAILED"
    provider.query_error_code = "Provider.Error"
    script = runtime.query_provider_video_task("p", task_id)
    failed = next(item for item in script.video_tasks if item.id == task_id)
    assert failed.status == "failed"
    assert failed.error == "provider_remote_failed"


def test_provider_execution_persistence_does_not_store_secret(runtime):
    task_id, provider = _admit_for_submit(runtime)
    runtime.submit_provider_video_task("p", task_id)

    payload = (runtime.data_dir / "projects.json").read_text()
    assert SECRET not in payload
    assert provider.submits[0][1].secret == SECRET


def test_submit_rejects_provider_record_mutation_during_network(runtime):
    task_id, provider = _admit_for_submit(runtime)

    def mutate_record():
        with runtime._save_lock:
            task = runtime.scripts["p"].video_tasks[0]
            task.provider_execution.intent["prompt"] = "tampered prompt"
            runtime._commit_storage("projects.json")

    provider.before_submit_callback = mutate_record

    with pytest.raises(StorageConflictError):
        runtime.submit_provider_video_task("p", task_id)

    saved = runtime.read_committed_script("p").video_tasks[0]
    assert saved.provider_execution.handle is None
    assert saved.provider_execution.phase == "submitting"


def test_query_wrong_task_failed_observation_is_needs_confirmation_not_failure(runtime):
    task_id, provider = _admit_for_submit(runtime)
    runtime.submit_provider_video_task("p", task_id)

    def wrong_failed(handle, *, credential):
        provider.queries.append((handle, credential))
        return ProviderObservation(
            task_id="wrong-task",
            status=ProviderTaskStatus.FAILED,
            raw_status="FAILED",
            source_handle=handle,
        )

    provider.query = wrong_failed

    script = runtime.query_provider_video_task("p", task_id)

    saved = next(item for item in script.video_tasks if item.id == task_id)
    assert saved.status == "needs_confirmation"
    assert saved.error == "provider_query_invalid"
    assert len(provider.queries) == 1


def test_query_completed_provider_execution_is_noop_and_preserves_video_url(runtime):
    task_id, provider = _admit_for_submit(runtime)
    runtime.submit_provider_video_task("p", task_id)
    with runtime._save_lock:
        task = runtime.scripts["p"].video_tasks[0]
        task.status = "completed"
        task.video_url = "video/already-fetched.mp4"
        artifact = module.provider_recovery.artifact_proof_from_record(
            task.provider_execution,
            ProviderArtifact(
                ProviderHandle.from_dict(task.provider_execution.handle),
                "video/already-fetched.mp4",
                hashlib.sha256(b"already fetched").hexdigest(),
                len(b"already fetched"),
            ),
        )
        task.provider_execution = module.provider_recovery.record_with_changes(
            task.provider_execution,
            phase="completed",
            artifact=artifact,
        )
        runtime._commit_storage("projects.json")

    script = runtime.query_provider_video_task("p", task_id)

    saved = next(item for item in script.video_tasks if item.id == task_id)
    assert saved.status == "completed"
    assert saved.video_url == "video/already-fetched.mp4"
    assert provider.queries == []


def test_submit_missing_secret_becomes_needs_confirmation_without_post(runtime):
    task_id, provider = _admit_for_submit(runtime)
    _install_provider(runtime, provider, secret="")

    script = runtime.submit_provider_video_task("p", task_id)

    saved = next(item for item in script.video_tasks if item.id == task_id)
    assert saved.status == "needs_confirmation"
    assert saved.error == "provider_credentials_required"
    assert provider.submits == []


def test_submit_binding_storage_error_closes_runtime_before_network(runtime):
    task_id, provider = _admit_for_submit(runtime)
    (runtime.data_dir / "provider_credentials.json").write_text("null\n", encoding="utf-8")

    with pytest.raises(StorageError):
        runtime.submit_provider_video_task("p", task_id)

    assert provider.submits == []
    with pytest.raises(StorageError):
        runtime.assert_storage_available()


def test_submit_requires_callable_provider_before_persisting_submitting(runtime):
    _, task_id = runtime.admit_provider_video_task("p", _admission_request())

    with pytest.raises(StorageConflictError):
        runtime.submit_provider_video_task("p", task_id)

    saved = runtime.read_committed_script("p").video_tasks[0]
    assert saved.provider_execution.phase == "accepted"
    assert saved.status == "pending"


def test_query_receipt_without_query_capability_is_typed_needs_confirmation(runtime):
    task_id, provider = _admit_for_submit(runtime)
    runtime.submit_provider_video_task("p", task_id)
    with runtime._save_lock:
        task = runtime.scripts["p"].video_tasks[0]
        handle = dict(task.provider_execution.handle)
        handle["query_capability"] = "unknown"
        task.provider_execution = module.provider_recovery.record_with_changes(
            task.provider_execution,
            handle=handle,
        )
        runtime._commit_storage("projects.json")

    script = runtime.query_provider_video_task("p", task_id)

    saved = next(item for item in script.video_tasks if item.id == task_id)
    assert saved.status == "needs_confirmation"
    assert saved.error == "provider_receipt_invalid"
    assert provider.queries == []


def test_query_missing_secret_becomes_needs_confirmation_without_query(runtime):
    task_id, provider = _admit_for_submit(runtime)
    runtime.submit_provider_video_task("p", task_id)
    _install_provider(runtime, provider, secret="")

    script = runtime.query_provider_video_task("p", task_id)

    saved = next(item for item in script.video_tasks if item.id == task_id)
    assert saved.status == "needs_confirmation"
    assert saved.error == "provider_credentials_required"
    assert provider.queries == []


def test_query_network_wait_does_not_block_unrelated_storage_reads(runtime):
    task_id, provider = _admit_for_submit(runtime)
    runtime.submit_provider_video_task("p", task_id)
    provider.block_query = True

    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(runtime.query_provider_video_task, "p", task_id)
        assert provider.network_entered.wait(2), "query network did not start"
        assert runtime.read_committed_script("p").id == "p"
        provider.network_release.set()
        pending.result(timeout=3)


def test_query_real_adapter_invalid_historical_task_id_is_typed_receipt_invalid(runtime):
    task_id, provider = _admit_for_submit(runtime)
    runtime.submit_provider_video_task("p", task_id)
    with runtime._save_lock:
        task = runtime.scripts["p"].video_tasks[0]
        handle = dict(task.provider_execution.handle)
        handle["task_id"] = "bad/task"
        task.provider_execution = module.provider_recovery.record_with_changes(
            task.provider_execution,
            handle=handle,
        )
        runtime._commit_storage("projects.json")
    _install_provider(runtime, WanxModel({"params": {}}))

    script = runtime.query_provider_video_task("p", task_id)

    saved = next(item for item in script.video_tasks if item.id == task_id)
    assert saved.status == "needs_confirmation"
    assert saved.error == "provider_receipt_invalid"

def _submit_and_observe_success(runtime, provider):
    task_id, _ = _admit_for_submit(runtime, provider)
    runtime.submit_provider_video_task("p", task_id)
    provider.query_status = ProviderTaskStatus.SUCCEEDED
    provider.query_raw_status = "SUCCEEDED"
    provider.query_video_url = "https://media.example.invalid/result.mp4"
    runtime.query_provider_video_task("p", task_id)
    return task_id


def test_fetch_success_attaches_prepared_and_artifact_without_provider_url(runtime):
    provider = FakeProvider()
    task_id = _submit_and_observe_success(runtime, provider)

    script = runtime.fetch_provider_video_task("p", task_id)

    saved = next(item for item in script.video_tasks if item.id == task_id)
    record = saved.provider_execution
    assert saved.status == "completed"
    assert saved.video_url == record.artifact["relative_path"]
    assert record.phase == "completed"
    assert record.prepared_artifact == record.artifact
    assert record.artifact["attempt_id"] == record.attempt_id
    assert record.artifact["handle"] == record.handle
    assert record.artifact["relative_path"].startswith("video/provider-")
    assert saved.video_url.startswith("video/provider-")
    assert "media.example.invalid" not in repr(record.prepared_artifact)
    assert "media.example.invalid" not in repr(record.artifact)
    assert (runtime.data_dir / saved.video_url).read_bytes() == provider.fetch_bytes
    assert len(provider.fetches) == 1


def test_fetch_existing_destination_without_matching_proof_fails_closed(runtime):
    provider = FakeProvider()
    task_id = _submit_and_observe_success(runtime, provider)
    task = runtime.read_committed_script("p").video_tasks[0]
    destination = runtime.data_dir / f"video/provider-{task.id}-{task.provider_execution.attempt_id}.mp4"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(b"belongs to a prior operation")

    script = runtime.fetch_provider_video_task("p", task_id)

    saved = next(item for item in script.video_tasks if item.id == task_id)
    assert saved.status == "needs_confirmation"
    assert saved.error == "provider_fetch_artifact_unrecorded"
    assert destination.read_bytes() == b"belongs to a prior operation"
    assert provider.fetches == []


def test_fetch_restart_repairs_final_bytes_after_attach_save_failure_without_http(
    runtime, monkeypatch,
):
    provider = FakeProvider()
    task_id = _submit_and_observe_success(runtime, provider)
    real_commit = runtime._storage.commit
    fetch_commits = {"count": 0}

    def fail_final_attach(payloads):
        fetch_commits["count"] += 1
        if fetch_commits["count"] == 2:
            raise StorageError("cannot save final attachment")
        return real_commit(payloads)

    monkeypatch.setattr(runtime._storage, "commit", fail_final_attach)

    with pytest.raises(StorageError):
        runtime.fetch_provider_video_task("p", task_id)

    assert len(provider.fetches) == 1
    data_dir = runtime.data_dir
    runtime.close()
    repair_provider = FakeProvider()
    repair_provider.fetch_error = AssertionError("repair must not perform HTTP fetch")
    for name in ("AssetGenerator", "StoryboardGenerator", "AudioGenerator", "ExportManager"):
        monkeypatch.setattr(module, name, Mock())
    reloaded = module.ComicGenPipeline(data_dir=data_dir, script_processor=Mock(), warmup_demucs=False)
    _install_provider(reloaded, repair_provider)
    try:
        script = reloaded.fetch_provider_video_task("p", task_id)
        saved = next(item for item in script.video_tasks if item.id == task_id)
        assert saved.status == "completed"
        assert saved.provider_execution.artifact == saved.provider_execution.prepared_artifact
        assert (reloaded.data_dir / saved.video_url).read_bytes() == provider.fetch_bytes
        assert repair_provider.fetches == []
    finally:
        reloaded.close()

def test_fetch_prepared_missing_final_rejects_changed_refetch_bytes(runtime):
    provider = FakeProvider()
    task_id = _submit_and_observe_success(runtime, provider)
    with runtime._save_lock:
        task = runtime.scripts["p"].video_tasks[0]
        record = task.provider_execution
        output_ref = f"video/provider-{task.id}-{record.attempt_id}.mp4"
        expected = b"original paid provider video"
        proof = module.provider_recovery.artifact_proof_from_record(
            record,
            ProviderArtifact(
                ProviderHandle.from_dict(record.handle),
                output_ref,
                hashlib.sha256(expected).hexdigest(),
                len(expected),
            ),
        )
        task.provider_execution = module.provider_recovery.record_with_changes(
            record,
            prepared_artifact=proof,
        )
        runtime._commit_storage("projects.json")
    provider.fetch_bytes = b"changed paid provider video"

    script = runtime.fetch_provider_video_task("p", task_id)

    saved = next(item for item in script.video_tasks if item.id == task_id)
    assert saved.status == "needs_confirmation"
    assert saved.error == "provider_fetch_artifact_invalid"
    assert saved.provider_execution.prepared_artifact == proof
    assert saved.provider_execution.artifact is None
    assert not (runtime.data_dir / output_ref).exists()


def test_fetch_network_wait_does_not_block_unrelated_storage_reads(runtime):
    provider = FakeProvider()
    task_id = _submit_and_observe_success(runtime, provider)
    provider.block_fetch = True

    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(runtime.fetch_provider_video_task, "p", task_id)
        assert provider.network_entered.wait(2), "fetch network did not start"
        assert runtime.read_committed_script("p").id == "p"
        provider.network_release.set()
        pending.result(timeout=3)

def test_provider_artifact_proof_schema_is_strict(runtime):
    provider = FakeProvider()
    task_id = _submit_and_observe_success(runtime, provider)
    record = runtime.read_committed_script("p").video_tasks[0].provider_execution
    proof = module.provider_recovery.artifact_proof_from_record(
        record,
        ProviderArtifact(
            ProviderHandle.from_dict(record.handle),
            f"video/provider-{task_id}-{record.attempt_id}.mp4",
            hashlib.sha256(b"strict proof").hexdigest(),
            len(b"strict proof"),
        ),
    )

    cases = []
    value = dict(proof)
    value["provider_url"] = "https://media.example.invalid/result.mp4"
    cases.append(value)
    value = dict(proof)
    value["attempt_id"] = "other-attempt"
    cases.append(value)
    value = dict(proof)
    value["sha256"] = value["sha256"].upper()
    cases.append(value)
    value = dict(proof)
    value["size_bytes"] = 0
    cases.append(value)

    for value in cases:
        with pytest.raises(ValidationError):
            module.provider_recovery.record_with_changes(record, prepared_artifact=value)

def test_fetch_missing_final_bytes_after_provider_return_is_typed_needs_confirmation(runtime):
    provider = FakeProvider()
    task_id = _submit_and_observe_success(runtime, provider)
    provider.skip_fetch_write = True

    script = runtime.fetch_provider_video_task("p", task_id)

    saved = next(item for item in script.video_tasks if item.id == task_id)
    assert saved.status == "needs_confirmation"
    assert saved.error == "provider_fetch_artifact_missing"
    assert saved.provider_execution.artifact is None


def test_fetch_provider_must_call_prepared_callback_before_return(runtime):
    provider = FakeProvider()
    task_id = _submit_and_observe_success(runtime, provider)
    provider.skip_fetch_callback = True

    script = runtime.fetch_provider_video_task("p", task_id)

    saved = next(item for item in script.video_tasks if item.id == task_id)
    assert saved.status == "needs_confirmation"
    assert saved.error == "provider_fetch_prepare_missing"
    assert saved.provider_execution.prepared_artifact is None
    assert saved.provider_execution.artifact is None


def test_fetch_prepared_and_returned_artifact_must_match_exact_target(runtime):
    provider = FakeProvider()
    task_id = _submit_and_observe_success(runtime, provider)
    provider.fetch_output_ref_override = "video/wrong-target.mp4"

    script = runtime.fetch_provider_video_task("p", task_id)

    saved = next(item for item in script.video_tasks if item.id == task_id)
    assert saved.status == "needs_confirmation"
    assert saved.error == "provider_fetch_artifact_target_invalid"
    assert saved.provider_execution.artifact is None


def test_fetch_local_prepared_final_repair_does_not_require_secret_or_provider(runtime):
    provider = FakeProvider()
    task_id = _submit_and_observe_success(runtime, provider)
    runtime.fetch_provider_video_task("p", task_id)
    with runtime._save_lock:
        task = runtime.scripts["p"].video_tasks[0]
        task.status = "processing"
        task.video_url = None
        task.provider_execution = module.provider_recovery.record_with_changes(
            task.provider_execution,
            phase="observed",
            artifact=None,
        )
        proof = task.provider_execution.prepared_artifact
        runtime._commit_storage("projects.json")
    _install_provider(runtime, object(), secret="")

    script = runtime.fetch_provider_video_task("p", task_id)

    saved = next(item for item in script.video_tasks if item.id == task_id)
    assert saved.status == "completed"
    assert saved.video_url == proof["relative_path"]
    assert saved.provider_execution.artifact == proof


def test_fetch_uses_output_budget_separate_from_input_media_budget(runtime):
    provider = FakeProvider()
    task_id = _submit_and_observe_success(runtime, provider)
    provider.fetch_bytes = b"x" * (len(PNG_BYTES) + 64)
    runtime.provider_recovery_runtime = StudioProviderRecoveryRuntime(
        scope=SCOPE,
        secret_resolver=lambda: SECRET,
        provider=provider,
        max_bytes=len(PNG_BYTES),
        output_max_bytes=len(provider.fetch_bytes) + 64,
    )

    script = runtime.fetch_provider_video_task("p", task_id)

    saved = next(item for item in script.video_tasks if item.id == task_id)
    assert saved.status == "completed"
    assert saved.provider_execution.artifact["size_bytes"] == len(provider.fetch_bytes)


def test_fetch_invalid_observation_becomes_typed_needs_confirmation(runtime):
    provider = FakeProvider()
    task_id = _submit_and_observe_success(runtime, provider)
    with runtime._save_lock:
        task = runtime.scripts["p"].video_tasks[0]
        observation = dict(task.provider_execution.observation)
        observation["source_handle"] = None
        task.provider_execution = module.provider_recovery.record_with_changes(
            task.provider_execution,
            observation=observation,
        )
        runtime._commit_storage("projects.json")

    script = runtime.fetch_provider_video_task("p", task_id)

    saved = next(item for item in script.video_tasks if item.id == task_id)
    assert saved.status == "needs_confirmation"
    assert saved.error == "provider_fetch_receipt_invalid"
    assert provider.fetches == []

def test_fetch_completed_artifact_missing_final_becomes_typed_needs_confirmation(runtime):
    provider = FakeProvider()
    task_id = _submit_and_observe_success(runtime, provider)
    script = runtime.fetch_provider_video_task("p", task_id)
    saved = next(item for item in script.video_tasks if item.id == task_id)
    (runtime.data_dir / saved.video_url).unlink()

    script = runtime.fetch_provider_video_task("p", task_id)

    saved = next(item for item in script.video_tasks if item.id == task_id)
    assert saved.status == "needs_confirmation"
    assert saved.error == "provider_fetch_artifact_missing"
