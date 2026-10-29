"""Video dispatcher/executor must not mutate Core image provider records."""

from __future__ import annotations

import pytest

from src.apps.playground.models import PlaygroundAttempt, PlaygroundGeneration, PlaygroundMode
from src.apps.playground.provider_video_execution import PlaygroundProviderVideoExecutionService
from src.apps.playground.recovery_dispatcher import PlaygroundVideoRecoveryDispatcher
from src.apps.playground.storage import PlaygroundStorage
from src.models.provider_binding import ProviderCredentialStore
from src.models.provider_execution import ProviderExecutionRecord
from src.models.provider_media_intents import build_accepted_sync_artifact_record
from src.models.provider_recovery import record_with_changes
from src.models.provider_runtime import ProviderRecoveryRuntime
from src.models.provider_tasks import ProviderScope
from src.utils.generation_intent import GenerationIntent


SECRET = "fake-secret"
SCOPE = ProviderScope("dashscope", "dashscope", "dashscope", "https://dashscope.aliyuncs.com", "cn-beijing")


class ServiceFake:
    def __init__(self):
        self.calls = []
        self.stop_count = 0

    def run_attempt(self, generation_id, attempt_id):
        self.calls.append((generation_id, attempt_id))
        raise AssertionError("video dispatcher must not run image attempts")

    def request_stop(self):
        self.stop_count += 1

    def join(self, timeout=None):
        return True


class ProviderFake:
    def __init__(self):
        self.submits = []
        self.queries = []
        self.fetches = []

    def submit(self, request, *, credential, on_submitted):
        self.submits.append(request)
        raise AssertionError("video executor must not submit image records")

    def query(self, handle, *, credential):
        self.queries.append(handle)
        raise AssertionError("video executor must not query image records")

    def fetch(self, *args, **kwargs):
        self.fetches.append((args, kwargs))
        raise AssertionError("video executor must not fetch image records")


def _intent() -> GenerationIntent:
    return GenerationIntent(
        model_id="wan/wan2.7-image#t2i",
        catalog_mode="image",
        mode="t2i",
        prompt="A calm ocean at sunrise",
        params={},
        sources={"model_id": "request", "mode": "request", "prompt": "request"},
        request_fields=("mode", "model_id", "prompt"),
    )


def _image_record(attempt_id: str, *, phase: str = "accepted") -> ProviderExecutionRecord:
    record = build_accepted_sync_artifact_record(
        intent=_intent(),
        provider_model_id="wan2.7-t2i",
        media=(),
        scope=SCOPE,
        credential_ref="credential-ref",
        attempt_id=attempt_id,
        output_ref=f"playground/images/{attempt_id}.png",
        media_kind="image",
    )
    if phase == "accepted":
        return record
    if phase == "submitting":
        return record_with_changes(record, phase="submitting")
    if phase == "completed":
        proof = {
            "schema_version": 2,
            "contract_version": "provider-sync-artifact-v1",
            "attempt_id": attempt_id,
            "media_kind": "image",
            "relative_path": f"playground/images/{attempt_id}.png",
            "output_ref": f"playground/images/{attempt_id}.png",
            "sha256": "a" * 64,
            "size_bytes": 12,
        }
        return record_with_changes(record, phase="completed", prepared_artifact=proof, artifact=proof)
    raise AssertionError(phase)


def _async_video_record(attempt_id: str) -> ProviderExecutionRecord:
    return ProviderExecutionRecord(
        schema_version=1,
        execution_kind=None,
        media_kind=None,
        output_ref=None,
        attempt_id=attempt_id,
        phase="accepted",
        provider_model_id="wan2.7-t2v",
        intent={},
        media=[],
        scope=SCOPE.to_dict(),
        credential_ref="credential-ref",
        fingerprint="a" * 64,
        handle=None,
        observation=None,
        prepared_artifact=None,
        artifact=None,
    )


def _attempt(attempt_id: str, *, status: str, provider_execution) -> PlaygroundAttempt:
    return PlaygroundAttempt(
        attempt_id=attempt_id,
        ordinal=int(attempt_id.rsplit("-", 1)[-1]) if attempt_id.rsplit("-", 1)[-1].isdigit() else 0,
        status=status,
        error=None,
        provider_execution=provider_execution,
    )


def _generation(
    *attempts: PlaygroundAttempt,
    status: str = "processing",
    mode: PlaygroundMode = PlaygroundMode.T2I,
    model_id: str = "wan/wan2.7-image#t2i",
    generation_id: str = "image-generation",
) -> PlaygroundGeneration:
    return PlaygroundGeneration(
        schema_version=1,
        id=generation_id,
        client_request_id=f"client-{generation_id}",
        request_contract_version=None,
        request_fingerprint=f"fingerprint-{generation_id}",
        mode=mode,
        model_id=model_id,
        prompt="A calm ocean at sunrise",
        negative_prompt=None,
        parameters={},
        batch_size=len(attempts),
        status=status,
        attempts=list(attempts),
        outputs=[],
        error=None,
        created_at="2026-10-13T00:00:00+00:00",
    )


def _history_bytes(storage: PlaygroundStorage) -> bytes:
    return storage.history_path.read_bytes()


def _executor(storage: PlaygroundStorage, tmp_path) -> tuple[PlaygroundProviderVideoExecutionService, ProviderFake]:
    credentials = ProviderCredentialStore(tmp_path, writer_lock=storage._writer_lock, runtime_lock=storage._lock)
    provider = ProviderFake()
    runtime = ProviderRecoveryRuntime(scope=SCOPE, secret_resolver=lambda: SECRET, provider=provider, max_bytes=1024 * 1024)
    return PlaygroundProviderVideoExecutionService(storage, runtime_provider=lambda _scope: runtime, credential_store=credentials), provider


@pytest.mark.parametrize("attempt_status", ["pending", "processing"])
@pytest.mark.parametrize("record_kind", ["image_schema2", "missing", "malformed", "legacy_schema1"])
def test_video_dispatcher_skips_image_generations_before_parsing_or_claiming(tmp_path, attempt_status, record_kind):
    storage = PlaygroundStorage(tmp_path)
    if record_kind == "image_schema2":
        provider_execution = _image_record("attempt-1").model_dump(mode="json")
    elif record_kind == "missing":
        provider_execution = None
    elif record_kind == "malformed":
        provider_execution = {"schema_version": 1, "phase": "accepted"}
    else:
        provider_execution = _async_video_record("attempt-1").model_dump(mode="json")
    original = _generation(_attempt("attempt-1", status=attempt_status, provider_execution=provider_execution), status=attempt_status)
    storage.add_generation(original)
    before = _history_bytes(storage)
    service = ServiceFake()
    dispatcher = PlaygroundVideoRecoveryDispatcher(storage, service)

    assert dispatcher.run_once() is None

    assert _history_bytes(storage) == before
    assert storage.get_generation("image-generation") == original
    assert service.calls == []
    storage.close()


@pytest.mark.parametrize("attempt_status", ["pending", "processing"])
@pytest.mark.parametrize("record_kind", ["image_schema2", "missing", "malformed", "legacy_schema1"])
def test_video_executor_noops_image_generation_before_claim_or_provider_calls(tmp_path, attempt_status, record_kind):
    storage = PlaygroundStorage(tmp_path)
    if record_kind == "image_schema2":
        provider_execution = _image_record("attempt-1").model_dump(mode="json")
    elif record_kind == "missing":
        provider_execution = None
    elif record_kind == "malformed":
        provider_execution = {"schema_version": 1, "phase": "accepted"}
    else:
        provider_execution = _async_video_record("attempt-1").model_dump(mode="json")
    generation = _generation(_attempt("attempt-1", status=attempt_status, provider_execution=provider_execution), status=attempt_status)
    storage.add_generation(generation)
    before = _history_bytes(storage)
    executor, provider = _executor(storage, tmp_path)

    ack = executor.run_attempt("image-generation", "attempt-1")

    assert ack.generation == generation
    assert _history_bytes(storage) == before
    assert storage.get_generation("image-generation") == generation
    assert provider.submits == []
    assert provider.queries == []
    assert provider.fetches == []
    storage.close()


def test_video_dispatcher_and_executor_skip_mislabeled_video_generation_with_image_record(tmp_path):
    storage = PlaygroundStorage(tmp_path)
    image_record = _image_record("attempt-1")
    generation = _generation(
        _attempt("attempt-1", status="processing", provider_execution=image_record.model_dump(mode="json")),
        status="processing",
        mode=PlaygroundMode.T2V,
        model_id="wan/wan2.7-video#t2v",
        generation_id="mislabeled-video-generation",
    )
    storage.add_generation(generation)
    before = _history_bytes(storage)
    executor, provider = _executor(storage, tmp_path)
    dispatcher = PlaygroundVideoRecoveryDispatcher(storage, executor)

    assert dispatcher.run_once() is None
    ack = executor.run_attempt("mislabeled-video-generation", "attempt-1")

    assert ack.generation == generation
    assert _history_bytes(storage) == before
    assert provider.submits == []
    assert provider.queries == []
    assert provider.fetches == []
    storage.close()


def test_known_video_malformed_record_still_needs_confirmation(tmp_path):
    storage = PlaygroundStorage(tmp_path)
    generation = _generation(
        _attempt("attempt-1", status="processing", provider_execution={"schema_version": 1, "phase": "accepted"}),
        status="processing",
        mode=PlaygroundMode.T2V,
        model_id="wan/wan2.7-video#t2v",
        generation_id="video-generation",
    )
    storage.add_generation(generation)
    executor, provider = _executor(storage, tmp_path)
    dispatcher = PlaygroundVideoRecoveryDispatcher(storage, executor)

    assert dispatcher.run_once() == ("confirm", "video-generation", "attempt-1")

    saved = storage.get_generation("video-generation")
    assert saved is not None
    assert saved.status == "needs_confirmation"
    assert saved.attempts[0].status == "needs_confirmation"
    assert saved.attempts[0].error == "provider_receipt_invalid"
    assert provider.submits == []
    assert provider.queries == []
    assert provider.fetches == []
    storage.close()
