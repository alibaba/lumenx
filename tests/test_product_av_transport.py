"""Product execution uploads pending Wan3 AV only as transient submit transport."""

from __future__ import annotations

import hashlib
import io
import threading
import wave
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import pytest

from src.apps.atelier.agent_authorization import (
    TOOL_GENERATION_CREATE_VIDEO_CANDIDATES,
    build_agent_generation_approval_record,
    build_agent_generation_binding,
)
from src.apps.atelier.generation_execution import AtelierGenerationExecutionService
from src.apps.atelier.models import (
    AtelierAgentToolCall,
    AtelierAgentToolStatus,
    AtelierAgentTurn,
    AtelierGenerationAttempt,
    AtelierGenerationMediaSource,
    AtelierGenerationRequestReceipt,
    AtelierNode,
    AtelierProject,
)
from src.apps.atelier.storage import AtelierStorage
from src.apps.comic_gen import pipeline as studio_module
from src.apps.comic_gen import provider_recovery as studio_recovery
from src.apps.comic_gen.models import ProviderRequestReceipt, Script, StoryboardFrame, VideoTask
from src.models.provider_binding import ProviderCredentialStore
from src.models.provider_recovery import ProviderRecoveryError, build_accepted_provider_submission_and_record, record_with_changes
from src.models.provider_runtime import ProviderRecoveryRuntime
from src.models.provider_tasks import PreparedMedia, ProviderHandle, ProviderScope, QueryCapability
from src.models.provider_upload import ProviderUploadError, ProviderUploadResult
from src.utils.generation_intent import GenerationIntent
from src.utils.json_storage import StorageConflictError, serialize_json
from src.utils.media_versions import freeze_media, read_verified_media


WAN3_SCOPE = ProviderScope(
    provider="dashscope",
    backend="dashscope",
    gateway="dashscope_workspace",
    base_url="https://original-workspace.cn-beijing.maas.aliyuncs.com",
    region="cn-beijing",
    workspace_id="original-workspace",
)
SECRET = "wan3-secret"


def _wav_bytes(seconds: float = 1.0, sample_rate: int = 16_000) -> bytes:
    buffer = io.BytesIO()
    frames = int(seconds * sample_rate)
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(sample_rate)
        writer.writeframes(b"\0\0" * frames)
    return buffer.getvalue()


def _intent() -> GenerationIntent:
    return GenerationIntent(
        model_id="wan/wan3.0-video#video",
        catalog_mode="video",
        mode="r2v",
        prompt="Use the reference audio for rhythm",
        params={
            "duration": 5,
            "resolution": "1080P",
            "ratio": "adaptive",
            "prompt_extend": True,
            "watermark": False,
            "audio": False,
        },
        sources={},
        request_fields=(),
    )


def _freeze_audio(root, writer_lock, *, ref="uploads/ref.wav"):
    path = root / ref
    path.parent.mkdir(parents=True, exist_ok=True)
    data = _wav_bytes()
    path.write_bytes(data)
    version = freeze_media(
        ref,
        output_root=root,
        writer_lock=writer_lock,
        role="reference_audio",
        ordinal=0,
        max_bytes=15 * 1024 * 1024,
    )
    prepared = PreparedMedia.from_pending_av_bytes(
        version,
        read_verified_media(version, output_root=root, max_bytes=15 * 1024 * 1024),
    )
    return version, prepared, data


def _accepted_record(root, writer_lock, credential_ref: str, *, attempt_id="attempt-av"):
    version, prepared, data = _freeze_audio(root, writer_lock)
    submission, record = build_accepted_provider_submission_and_record(
        _intent(),
        provider_model_id="wan3.0-video",
        media=(prepared,),
        scope=WAN3_SCOPE,
        credential_ref=credential_ref,
        attempt_id=attempt_id,
    )
    return submission, record, version, data


class FakeProvider:
    def __init__(self):
        self.submits = []

    def submit(self, request, *, credential, on_submitted):
        self.submits.append((request, credential))
        handle = ProviderHandle(
            provider_model_id=request.provider_model_id,
            task_id="remote-task-1",
            scope=request.scope,
            credential_ref=request.credential_ref,
            query_capability=QueryCapability.BY_TASK_ID,
            submitted_at=123.0,
        )
        on_submitted(handle)
        return handle


def _successful_uploader(calls):
    def upload(*, scope, provider_model_id, credential, version, data):
        calls.append({
            "scope": scope,
            "provider_model_id": provider_model_id,
            "credential": credential,
            "version": version,
            "sha256": hashlib.sha256(data).hexdigest(),
        })
        return ProviderUploadResult(
            url=f"oss://dashscope-temp/{version.role}-{len(calls)}.wav",
            key=f"dashscope-temp/{version.role}-{len(calls)}.wav",
            provider_model_id=provider_model_id,
            version=version,
            scope=scope,
            credential_ref=credential.credential_ref,
        )
    return upload


def _install_upload(monkeypatch, uploader):
    monkeypatch.setattr("src.models.provider_upload.upload_dashscope_temporary_media", uploader)


def _hash64(value):
    return hashlib.sha256(serialize_json(value).encode("utf-8")).hexdigest()


def _make_atelier_attempt_agent_authorized(store, *, decision="approved", granted=True):
    project = store.get_project("p")
    attempt = project.generation_attempts[0]
    public_record = {
        "project_id": project.id,
        "server_storage_identity": "storage-a",
        "provider_model_id": attempt.provider_execution.provider_model_id,
        "intent": attempt.provider_execution.intent,
        "media": attempt.provider_execution.media,
        "scope": attempt.provider_execution.scope,
        "credential_ref": attempt.provider_execution.credential_ref,
        "raw_request": {"client_request_id": "agent-request-av"},
    }
    preview = SimpleNamespace(
        public_record=public_record,
        record=attempt.provider_execution,
        preview_fingerprint=_hash64(public_record),
    )
    binding = build_agent_generation_binding(
        preview,
        turn_id="turn-av",
        call_id="call-av",
        approval_id="approval-av",
        policy=project.agent_policy,
        clock=lambda: 30.0,
    )
    approval = build_agent_generation_approval_record(
        binding.agent_authorization_ref,
        decision=decision,
        clock=lambda: 31.0,
    )
    project.agent_turns = [AtelierAgentTurn(
        id="turn-av",
        project_id="p",
        status="waiting_approval",
        tool_calls=[AtelierAgentToolCall(
            call_id="call-av",
            tool_name=TOOL_GENERATION_CREATE_VIDEO_CANDIDATES,
            status=AtelierAgentToolStatus.APPROVAL_REQUIRED,
            preview_binding=binding,
            approval_record=approval,
            approval_granted=granted,
        )],
    )]
    attempt.schema_version = 2
    attempt.agent_authorization_ref = binding.agent_authorization_ref
    project.generation_attempts = [attempt]
    project.generation_request_receipts[0].client_request_id = "agent-request-av"
    project.generation_request_receipts[0].request_fingerprint = binding.agent_authorization_ref.request_fingerprint
    store._commit({"p": project})
    return binding


def _revoke_atelier_agent_approval(store):
    project = store.get_project("p")
    call = project.agent_turns[0].tool_calls[0]
    call.approval_granted = False
    call.approval_record.decision = "revoked"
    store._commit({"p": project})


def _atelier_app(tmp_path):
    store = AtelierStorage(tmp_path)
    credentials = ProviderCredentialStore(tmp_path, writer_lock=store._writer_lock, runtime_lock=store._lock)
    credential = credentials.bind(WAN3_SCOPE, SECRET)
    _submission, record, version, data = _accepted_record(tmp_path, store._writer_lock, credential.credential_ref)
    attempt = AtelierGenerationAttempt(
        project_id="p",
        node_id="video",
        candidate_id="candidate-av",
        attempt_id=record.attempt_id,
        attempt_revision=0,
        status="pending",
        input_contract_version="ordered-media-v1",
        input_media=[AtelierGenerationMediaSource(role="reference_audio", ordinal=0, node_id="audio", media_url="uploads/ref.wav")],
        provider_execution=record,
        created_at=10.0,
        updated_at=10.0,
    )
    candidate = {
        "execution_contract": "atelier-provider-video-v1",
        "id": attempt.candidate_id,
        "attempt_id": attempt.attempt_id,
        "media_kind": "video",
        "status": "pending",
        "video_url": None,
        "error_code": None,
        "created_at": attempt.created_at,
        "updated_at": attempt.updated_at,
    }
    store.create_project(AtelierProject(
        id="p",
        title="Canvas",
        created_at=1.0,
        updated_at=1.0,
        nodes=[AtelierNode(id="video", project_id="p", type="video", data={})],
    ))
    project = store.get_project("p")
    project.revision = 1
    project.nodes[0].data = {"candidates": [candidate]}
    project.generation_attempts = [attempt]
    project.generation_request_receipts = [AtelierGenerationRequestReceipt(
        project_id="p",
        entrypoint="atelier.provider_video.v2",
        request_contract_version="request-json-v1",
        client_request_id="request-av",
        request_fingerprint="b" * 64,
        node_id="video",
        candidate_id=attempt.candidate_id,
        attempt_id=attempt.attempt_id,
        execution_fingerprint=record.fingerprint,
        base_revision=0,
        committed_revision=1,
        created_at=10.0,
    )]
    store._commit({"p": project})
    provider = FakeProvider()
    runtime = ProviderRecoveryRuntime(scope=WAN3_SCOPE, secret_resolver=lambda: SECRET, provider=provider, max_bytes=1024 * 1024)
    service = AtelierGenerationExecutionService(
        store,
        runtime_provider=lambda scope: runtime if scope == WAN3_SCOPE else None,
        credential_store=credentials,
        clock=lambda: 20.0,
        server_storage_identity="storage-a",
    )
    return service, store, provider, attempt, version, data


def _current_attempt(store):
    return store.get_project("p").generation_attempts[0]


def _continue_atelier(service, attempt):
    current = _current_attempt(service.storage)
    return service.continue_attempt(
        "p",
        attempt.attempt_id,
        candidate_id=attempt.candidate_id,
        expected_attempt_revision=current.attempt_revision,
        execution_fingerprint=attempt.provider_execution.fingerprint,
    )


def test_atelier_upload_failure_keeps_accepted_and_continue_retries_original_bytes(tmp_path, monkeypatch):
    service, store, provider, attempt, version, data = _atelier_app(tmp_path)
    calls = []

    def fail_once_then_succeed(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise ProviderUploadError("policy_transport")
        return _successful_uploader([])(**kwargs)

    _install_upload(monkeypatch, fail_once_then_succeed)
    ack = _continue_atelier(service, attempt)
    assert ack.attempt.status == "needs_confirmation"
    assert ack.attempt.error_code == "provider_media_upload_failed"
    assert ack.attempt.provider_execution.phase == "accepted"
    assert provider.submits == []
    assert "oss://" not in store.path.read_text()

    retry = _continue_atelier(service, attempt)
    assert retry.attempt.provider_execution.phase == "submitted"
    assert len(provider.submits) == 1
    assert len(calls) == 2
    assert calls[1]["scope"] == WAN3_SCOPE
    assert calls[1]["provider_model_id"] == "wan3.0-video"
    assert calls[1]["version"] == version
    assert hashlib.sha256(calls[1]["data"]).digest() == hashlib.sha256(data).digest()
    assert provider.submits[0][0].media[0].url.startswith("oss://dashscope-temp/")
    assert "oss://" not in store.path.read_text()


def test_atelier_upload_race_prevents_generation_post(tmp_path, monkeypatch):
    service, store, provider, attempt, _version, _data = _atelier_app(tmp_path)
    entered = threading.Event()
    release = threading.Event()

    def blocking_upload(**kwargs):
        entered.set()
        assert release.wait(3), "upload was not released"
        return _successful_uploader([])(**kwargs)

    _install_upload(monkeypatch, blocking_upload)
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(_continue_atelier, service, attempt)
        assert entered.wait(2), "upload did not start"
        current = _current_attempt(store)
        store.update_generation_attempt(
            "p",
            attempt_id=current.attempt_id,
            expected_attempt_revision=current.attempt_revision,
            execution_fingerprint=current.provider_execution.fingerprint,
            command=lambda item: (
                setattr(item, "status", "cancelled"),
                setattr(item, "provider_execution", record_with_changes(item.provider_execution, phase="cancelled")),
            ),
        )
        release.set()
        with pytest.raises(StorageConflictError):
            pending.result(timeout=3)
    assert provider.submits == []
    assert _current_attempt(store).provider_execution.phase == "cancelled"


def _studio_runtime(tmp_path, monkeypatch):
    for name in ("AssetGenerator", "StoryboardGenerator", "AudioGenerator", "ExportManager"):
        monkeypatch.setattr(studio_module, name, Mock())
    runtime = studio_module.ComicGenPipeline(data_dir=tmp_path / "studio", script_processor=Mock(), warmup_demucs=False)
    runtime.scripts["p"] = Script(
        id="p",
        title="project",
        original_text="source",
        created_at=1,
        updated_at=1,
        frames=[StoryboardFrame(id="frame", scene_id="scene")],
        video_tasks=[],
    )
    credential = runtime._provider_credential_store.bind(WAN3_SCOPE, SECRET)
    _submission, record, version, data = _accepted_record(runtime.data_dir, runtime._writer_lock, credential.credential_ref, attempt_id="studio-attempt-av")
    task = VideoTask(
        id="task-av",
        project_id="p",
        frame_id="frame",
        image_url="",
        prompt="Use the reference audio for rhythm",
        model="wan3.0-video",
        generation_mode="r2v",
        status="pending",
        provider_execution=record,
    )
    runtime.scripts["p"].video_tasks.append(task)
    runtime.scripts["p"].provider_request_receipts.append(ProviderRequestReceipt(
        request_contract_version="request-json-v1",
        entrypoint=studio_recovery.STUDIO_ORDERED_VIDEO_ENTRYPOINT,
        client_request_id="studio-request-av",
        frame_id="frame",
        request_fingerprint="c" * 64,
        created_at=10.0,
        task_id=task.id,
        attempt_id=record.attempt_id,
        execution_fingerprint=record.fingerprint,
    ))
    provider = FakeProvider()
    runtime.provider_recovery_runtime = studio_recovery.StudioProviderRecoveryRuntime(
        scope=WAN3_SCOPE,
        secret_resolver=lambda: SECRET,
        provider=provider,
        max_bytes=1024 * 1024,
    )
    runtime._commit_storage("projects.json")
    return runtime, provider, task, version, data


def _studio_task(runtime):
    return runtime.read_committed_script("p").video_tasks[0]


def test_studio_upload_failure_keeps_accepted_and_continue_retries_original_bytes(tmp_path, monkeypatch):
    runtime, provider, task, version, data = _studio_runtime(tmp_path, monkeypatch)
    calls = []

    def fail_once_then_succeed(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise ProviderUploadError("policy_transport")
        return _successful_uploader([])(**kwargs)

    _install_upload(monkeypatch, fail_once_then_succeed)
    try:
        script = runtime.submit_provider_video_task("p", task.id)
        saved = next(item for item in script.video_tasks if item.id == task.id)
        assert saved.status == "needs_confirmation"
        assert saved.error == "provider_media_upload_failed"
        assert saved.provider_execution.phase == "accepted"
        assert provider.submits == []
        assert "oss://" not in (runtime.data_dir / "projects.json").read_text()

        retry = runtime.continue_accepted_provider_video_task(
            "p",
            task.id,
            expected_attempt_id=task.provider_execution.attempt_id,
            expected_fingerprint=task.provider_execution.fingerprint,
        )
        assert retry.provider_execution.phase == "submitted"
        assert len(provider.submits) == 1
        assert len(calls) == 2
        assert calls[1]["scope"] == WAN3_SCOPE
        assert calls[1]["version"] == version
        assert hashlib.sha256(calls[1]["data"]).digest() == hashlib.sha256(data).digest()
        assert provider.submits[0][0].media[0].url.startswith("oss://dashscope-temp/")
        assert "oss://" not in (runtime.data_dir / "projects.json").read_text()
    finally:
        runtime.close()


def test_studio_ordered_continue_rejects_legacy_receipt_before_upload(tmp_path, monkeypatch):
    runtime, provider, task, _version, _data = _studio_runtime(tmp_path, monkeypatch)
    uploads = []
    _install_upload(monkeypatch, _successful_uploader(uploads))
    try:
        runtime.scripts["p"].provider_request_receipts[0].entrypoint = studio_recovery.STUDIO_VIDEO_ENTRYPOINT
        runtime._commit_storage("projects.json")
        before = (runtime.data_dir / "projects.json").read_bytes()
        with pytest.raises(StorageConflictError, match="provider_continue_not_allowed"):
            runtime.continue_accepted_provider_video_task(
                "p", task.id, expected_attempt_id=task.provider_execution.attempt_id,
                expected_fingerprint=task.provider_execution.fingerprint,
            )
        assert uploads == [] and provider.submits == []
        assert (runtime.data_dir / "projects.json").read_bytes() == before
    finally:
        runtime.close()


def test_studio_upload_race_prevents_generation_post(tmp_path, monkeypatch):
    runtime, provider, task, _version, _data = _studio_runtime(tmp_path, monkeypatch)
    entered = threading.Event()
    release = threading.Event()

    def blocking_upload(**kwargs):
        entered.set()
        assert release.wait(3), "upload was not released"
        return _successful_uploader([])(**kwargs)

    _install_upload(monkeypatch, blocking_upload)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            pending = pool.submit(runtime.submit_provider_video_task, "p", task.id)
            assert entered.wait(2), "upload did not start"
            current = _studio_task(runtime)
            with runtime._save_lock:
                current_mutable = runtime.scripts["p"].video_tasks[0]
                current_mutable.provider_execution = record_with_changes(current.provider_execution, phase="cancelled")
                current_mutable.status = "failed"
                current_mutable.error = "provider_cancelled_before_submit"
                runtime._commit_storage("projects.json")
            release.set()
            with pytest.raises(StorageConflictError):
                pending.result(timeout=3)
        assert provider.submits == []
        assert _studio_task(runtime).provider_execution.phase == "cancelled"
    finally:
        release.set()
        runtime.close()


def test_atelier_revoked_agent_approval_before_upload_blocks_upload(tmp_path, monkeypatch):
    service, store, provider, attempt, _version, _data = _atelier_app(tmp_path)
    _make_atelier_attempt_agent_authorized(store, decision="revoked", granted=False)
    calls = []
    _install_upload(monkeypatch, _successful_uploader(calls))

    with pytest.raises(StorageConflictError):
        _continue_atelier(service, attempt)

    assert calls == []
    assert provider.submits == []
    assert _current_attempt(store).provider_execution.phase == "accepted"


def test_atelier_revoked_agent_approval_during_upload_blocks_post(tmp_path, monkeypatch):
    service, store, provider, attempt, _version, _data = _atelier_app(tmp_path)
    _make_atelier_attempt_agent_authorized(store)
    entered = threading.Event()
    release = threading.Event()

    def blocking_upload(**kwargs):
        entered.set()
        assert release.wait(3), "upload was not released"
        return _successful_uploader([])(**kwargs)

    _install_upload(monkeypatch, blocking_upload)
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(_continue_atelier, service, attempt)
        assert entered.wait(2), "upload did not start"
        _revoke_atelier_agent_approval(store)
        release.set()
        with pytest.raises(StorageConflictError):
            pending.result(timeout=3)
    assert provider.submits == []
    assert _current_attempt(store).provider_execution.phase == "accepted"


def test_atelier_stop_during_upload_blocks_post(tmp_path, monkeypatch):
    service, _store, provider, attempt, _version, _data = _atelier_app(tmp_path)
    entered = threading.Event()
    release = threading.Event()

    def blocking_upload(**kwargs):
        entered.set()
        assert release.wait(3), "upload was not released"
        return _successful_uploader([])(**kwargs)

    _install_upload(monkeypatch, blocking_upload)
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(_continue_atelier, service, attempt)
        assert entered.wait(2), "upload did not start"
        service.request_stop()
        release.set()
        with pytest.raises(StorageConflictError):
            pending.result(timeout=3)
    assert provider.submits == []


def test_atelier_runtime_replacement_during_upload_blocks_post(tmp_path, monkeypatch):
    service, _store, provider, attempt, _version, _data = _atelier_app(tmp_path)
    replacement = FakeProvider()
    entered = threading.Event()
    release = threading.Event()

    def blocking_upload(**kwargs):
        entered.set()
        assert release.wait(3), "upload was not released"
        return _successful_uploader([])(**kwargs)

    _install_upload(monkeypatch, blocking_upload)
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(_continue_atelier, service, attempt)
        assert entered.wait(2), "upload did not start"
        new_runtime = ProviderRecoveryRuntime(scope=WAN3_SCOPE, secret_resolver=lambda: SECRET, provider=replacement, max_bytes=1024 * 1024)
        service._runtime_provider = lambda scope: new_runtime if scope == WAN3_SCOPE else None
        release.set()
        with pytest.raises(StorageConflictError):
            pending.result(timeout=3)
    assert provider.submits == []
    assert replacement.submits == []


def test_atelier_nonupload_transport_error_is_reported_truthfully(tmp_path, monkeypatch):
    service, store, provider, attempt, _version, _data = _atelier_app(tmp_path)

    def invalid_submission(*_args, **_kwargs):
        raise ProviderRecoveryError("provider_submission_invalid")

    monkeypatch.setattr(
        "src.apps.atelier.generation_execution.provider_recovery.prepare_submission_transport",
        invalid_submission,
    )
    ack = _continue_atelier(service, attempt)
    assert ack.attempt.status == "needs_confirmation"
    assert ack.attempt.error_code == "provider_submission_invalid"
    assert ack.attempt.provider_execution.phase == "accepted"
    assert provider.submits == []
    assert "oss://" not in store.path.read_text()


def test_studio_runtime_replacement_during_upload_blocks_post(tmp_path, monkeypatch):
    runtime, provider, task, _version, _data = _studio_runtime(tmp_path, monkeypatch)
    replacement = FakeProvider()
    entered = threading.Event()
    release = threading.Event()

    def blocking_upload(**kwargs):
        entered.set()
        assert release.wait(3), "upload was not released"
        return _successful_uploader([])(**kwargs)

    _install_upload(monkeypatch, blocking_upload)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            pending = pool.submit(runtime.submit_provider_video_task, "p", task.id)
            assert entered.wait(2), "upload did not start"
            runtime.provider_recovery_runtime = studio_recovery.StudioProviderRecoveryRuntime(
                scope=WAN3_SCOPE,
                secret_resolver=lambda: SECRET,
                provider=replacement,
                max_bytes=1024 * 1024,
            )
            release.set()
            with pytest.raises(StorageConflictError):
                pending.result(timeout=3)
        assert provider.submits == []
        assert replacement.submits == []
    finally:
        release.set()
        runtime.close()


def test_studio_nonupload_transport_error_is_reported_truthfully(tmp_path, monkeypatch):
    runtime, provider, task, _version, _data = _studio_runtime(tmp_path, monkeypatch)

    def invalid_submission(*_args, **_kwargs):
        raise ProviderRecoveryError("provider_submission_invalid")

    monkeypatch.setattr(studio_module, "_prepare_submission_transport", invalid_submission)
    try:
        script = runtime.submit_provider_video_task("p", task.id)
        saved = next(item for item in script.video_tasks if item.id == task.id)
        assert saved.status == "needs_confirmation"
        assert saved.error == "provider_submission_invalid"
        assert saved.provider_execution.phase == "accepted"
        assert provider.submits == []
        assert "oss://" not in (runtime.data_dir / "projects.json").read_text()
    finally:
        runtime.close()
