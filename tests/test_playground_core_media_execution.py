from __future__ import annotations

import base64
import hashlib
import json
import os
from dataclasses import replace
import threading
from contextlib import contextmanager
from pathlib import Path

import pytest

from src.apps.playground import service as service_module
from src.apps.playground import storage as storage_module

from src.apps.playground.models import GenerateRequest, PlaygroundAttempt, PlaygroundGeneration, PlaygroundMode
from src.apps.playground.service import PlaygroundService
from src.apps.playground.storage import PlaygroundStorage
from src.models.provider_execution import ProviderExecutionRecord
from src.models.provider_recovery import record_with_changes
from src.models.provider_tasks import ProviderScope, ResolvedCredential
from src.models.provider_media_runtime import MediaRuntimeAuthority
from src.models.provider_media_intents import build_accepted_sync_artifact_record, image_intent_from_sync_record
from src.utils.generation_intent import IntentValidationError
from src.utils.json_storage import StorageConflictError, StorageError
from src.utils.request_identity import REQUEST_IDENTITY_CONTRACT_VERSION, fingerprint_request
from tests.test_wan3_image_validation import JPEG_240_BASE64

DASHSCOPE_SECRET = "sk-test-" + "d" * 44


def _dashscope_authority(*, model: str = "wan2.7-image-pro") -> MediaRuntimeAuthority:
    scope = ProviderScope("dashscope", "dashscope", "dashscope", "https://dashscope.aliyuncs.com", "cn-beijing")
    return MediaRuntimeAuthority(
        media_kind="image",
        provider_model_id=model,
        scope=scope,
        credential_ref="cred-playground",
        source_key="DASHSCOPE_API_KEY",
        credential=ResolvedCredential(scope, "cred-playground", DASHSCOPE_SECRET),
    )


@contextmanager
def _authority_provider(intent):
    assert intent.catalog_mode == "image"
    assert intent.mode in {"t2i", "i2i"}
    yield _dashscope_authority(model=intent.model_id.split("/", 1)[1].split("#", 1)[0])


@contextmanager
def _authority_resolver(record: ProviderExecutionRecord):
    assert record.schema_version == 2
    assert record.execution_kind == "sync_artifact"
    assert record.media_kind == "image"
    yield _dashscope_authority(model=record.provider_model_id)


class FakeWanx:
    """Legacy adapter fixture; new Wan/Qwen T2I have separate real HTTP tests."""
    calls: list[dict] = []
    fail_on_call: int | None = None

    def __init__(self, config, *, env=None):
        self.config = config
        self.env = dict(env or {})

    def generate(self, prompt, output_path, **kwargs):
        call_number = len(self.calls) + 1
        ref_bytes = [Path(item).read_bytes() for item in kwargs.get("ref_image_paths", [])]
        self.calls.append({"prompt": prompt, "output_path": output_path, "kwargs": kwargs, "env": self.env, "ref_bytes": ref_bytes})
        if self.fail_on_call == call_number:
            raise RuntimeError("injected image provider ambiguity")
        Path(output_path).write_bytes(base64.b64decode(JPEG_240_BASE64))
        return output_path, 0.1


def _service(tmp_path, *, storage=None, model_factory=FakeWanx):
    storage = storage or PlaygroundStorage(tmp_path)
    return storage, PlaygroundService(
        storage,
        data_dir=tmp_path,
        media_authority_provider=_authority_provider,
        media_authority_resolver=_authority_resolver,
        image_model_factory=model_factory,
    )


def _request(request_id: str, *, batch_size=1, mode=PlaygroundMode.T2I, input_media=None, prompt="paint a glass moon"):
    return GenerateRequest(
        client_request_id=request_id,
        mode=mode,
        # Qwen T2I fixtures are admitted from the captured pre-migration wire below.
        # Wan2.7 I2I remains a current legacy admission.
        model_id="wan2.7-image-pro" if mode == PlaygroundMode.I2I else "qwen-image-2.0-pro",
        prompt=prompt,
        negative_prompt=None,
        input_media=input_media,
        parameters={"size": "1280*1280", "seed": 0, "prompt_extend": False, "watermark": False},
        batch_size=batch_size,
    )


def _admit_legacy_generation(service: PlaygroundService, request: GenerateRequest):
    """Re-key a frozen 5e4 intent for fault tests, never normalize a new Qwen request.

    The independent migration suite loads the complete original wire unchanged.
    This suite varies only local identities, batch members and synthetic credentials
    to keep its existing legacy adapter/publication failure scenarios independent.
    """
    if request.mode != PlaygroundMode.T2I:
        return service.admit_generation(request)
    frozen = json.loads((Path(__file__).parent / "fixtures" / "playground_qwen20_legacy_5e4e9918.json").read_text())
    source = PlaygroundGeneration.model_validate(frozen["snapshots"]["accepted"]["generation"])
    original = ProviderExecutionRecord.model_validate(source.attempts[0].provider_execution)
    assert "intent_contract_version" not in original.intent
    assert request.model_id == source.model_id
    assert request.prompt == source.prompt
    assert request.parameters == source.parameters
    assert request.negative_prompt is None and not request.input_media
    intent = image_intent_from_sync_record(original)
    authority = _dashscope_authority(model=original.provider_model_id)
    generation = source.model_copy(update={
        "id": "legacy-" + request.client_request_id,
        "client_request_id": request.client_request_id,
        "request_contract_version": REQUEST_IDENTITY_CONTRACT_VERSION,
        "request_fingerprint": fingerprint_request(request.identity_payload(), entrypoint="playground.generate"),
        "batch_size": request.batch_size or 1,
        "attempts": [],
    }, deep=True)
    for index in range(generation.batch_size):
        record = build_accepted_sync_artifact_record(
            intent=intent, provider_model_id=original.provider_model_id, media_kind="image", media=[],
            scope=authority.scope, credential_ref=authority.credential_ref,
            attempt_id=f"{request.client_request_id}-{index}",
            output_ref=f"playground/images/{generation.id}-{index}.png",
        )
        generation.attempts.append(PlaygroundAttempt(
            attempt_id=record.attempt_id, ordinal=index,
            provider_execution=record.model_dump(mode="json"),
        ))
    return service.storage.admit_generation(generation)


def _admit_and_process(service: PlaygroundService, request: GenerateRequest) -> PlaygroundGeneration:
    receipt = _admit_legacy_generation(service, request)
    assert receipt.generation is not None
    service.process_generation(receipt.generation.id)
    saved = service.storage.get_generation(receipt.generation.id)
    assert saved is not None
    return saved


def test_playground_image_generation_uses_core_execution_and_publishes_scoped_output(tmp_path):
    FakeWanx.calls.clear()
    FakeWanx.fail_on_call = None
    storage, service = _service(tmp_path)
    try:
        saved = _admit_and_process(service, _request("playground-core-image"))

        assert saved.status == "completed"
        assert len(saved.outputs) == 1
        output = saved.outputs[0]
        assert output.media_type == "image"
        assert output.media_path.startswith("playground/images/")
        assert output.media_path.endswith(".png")
        assert (tmp_path / output.media_path).is_file()
        provider_execution = saved.attempts[0].provider_execution
        assert provider_execution is not None
        assert output.media_path == provider_execution["artifact"]["relative_path"]
        assert output.media_path == provider_execution["artifact"]["output_ref"]
        assert output.attempt_id == saved.attempts[0].attempt_id
        assert saved.attempts[0].status == "completed"
        assert provider_execution["execution_kind"] == "sync_artifact"
        assert provider_execution["media_kind"] == "image"
        assert provider_execution["phase"] == "completed"
        assert provider_execution["provider_model_id"] == "qwen-image-2.0-pro"
        assert "intent_contract_version" not in provider_execution["intent"]
        assert FakeWanx.calls[0]["kwargs"]["seed"] == 0
        assert FakeWanx.calls[0]["kwargs"]["prompt_extend"] is False
        assert FakeWanx.calls[0]["kwargs"]["watermark"] is False
        assert FakeWanx.calls[0]["env"]["DASHSCOPE_API_KEY"] == DASHSCOPE_SECRET
    finally:
        storage.close()


def test_i2i_admission_without_input_media_is_intent_validation_error(tmp_path):
    storage, service = _service(tmp_path)
    try:
        request = _request(
            "playground-i2i-empty-media",
            mode=PlaygroundMode.I2I,
            input_media=[],
        )
        try:
            service.admit_generation(request)
        except IntentValidationError as exc:
            assert "requires input media" in str(exc)
        else:
            raise AssertionError("empty i2i input_media should be rejected as intent validation")
    finally:
        storage.close()


def test_image_admission_freezes_intent_media_and_authority_for_replay_and_execution(tmp_path):
    FakeWanx.calls.clear()
    input_ref = tmp_path / "playground" / "uploads" / "reference.jpg"
    input_ref.parent.mkdir(parents=True)
    input_ref.write_bytes(base64.b64decode(JPEG_240_BASE64))
    storage, service = _service(tmp_path)
    try:
        request = _request(
            "playground-core-i2i-freeze",
            mode=PlaygroundMode.I2I,
            input_media=["playground/uploads/reference.jpg"],
            prompt="first prompt",
        )
        first = service.admit_generation(request)
        assert first.generation is not None
        frozen_record = first.generation.attempts[0].provider_execution
        assert frozen_record is not None
        input_ref.write_bytes(b"mutated after admission")
        second = service.admit_generation(request)
        assert second.replayed is True
        assert second.generation is not None
        assert second.generation.id == first.generation.id
        assert second.generation.attempts[0].provider_execution == frozen_record

        service.process_generation(first.generation.id)
        saved = storage.get_generation(first.generation.id)
        assert saved is not None
        assert saved.status == "completed"
        call = FakeWanx.calls[-1]
        assert call["prompt"] == "first prompt"
        assert call["kwargs"]["ref_image_paths"]
        assert call["ref_bytes"][0] != b"mutated after admission"
    finally:
        storage.close()


def test_prepared_save_failure_stops_before_publish(tmp_path, monkeypatch):
    FakeWanx.calls.clear()
    storage, service = _service(tmp_path)
    real_update = storage.update_claimed_generation

    def fail_prepared(gen):
        record = gen.attempts[0].provider_execution or {}
        if record.get("prepared_artifact") is not None:
            raise StorageError("injected prepared save failure")
        return real_update(gen)

    monkeypatch.setattr(storage, "update_claimed_generation", fail_prepared)
    receipt = _admit_legacy_generation(service, _request("playground-prepared-save-failure"))
    assert receipt.generation is not None
    try:
        service.process_generation(receipt.generation.id)
    except StorageError as exc:
        assert "prepared save failure" in str(exc)
    else:
        raise AssertionError("prepared proof save should fail")
    saved = storage.get_generation(receipt.generation.id)
    assert saved is not None
    assert saved.outputs == []
    record = saved.attempts[0].provider_execution
    assert record is not None
    assert record["phase"] == "submitting"
    assert record.get("prepared_artifact") is None
    assert not (tmp_path / record["output_ref"]).exists()
    assert len(FakeWanx.calls) == 1


def test_published_prepared_record_completes_after_restart_without_reexecution(tmp_path, monkeypatch):
    FakeWanx.calls.clear()
    storage, service = _service(tmp_path)
    real_update = storage.update_claimed_generation

    def lose_completion(gen):
        record = gen.attempts[0].provider_execution or {}
        if record.get("phase") == "completed":
            return False
        return real_update(gen)

    monkeypatch.setattr(storage, "update_claimed_generation", lose_completion)
    receipt = _admit_legacy_generation(service, _request("playground-restart-published"))
    assert receipt.generation is not None
    service.process_generation(receipt.generation.id)
    saved = storage.get_generation(receipt.generation.id)
    assert saved is not None
    assert saved.status == "processing"
    record = saved.attempts[0].provider_execution
    assert record is not None
    assert record["phase"] == "submitting"
    assert record["prepared_artifact"] is not None
    assert (tmp_path / record["output_ref"]).is_file()
    assert len(FakeWanx.calls) == 1

    monkeypatch.setattr(storage, "update_claimed_generation", real_update)
    service.process_generation(receipt.generation.id)
    recovered = storage.get_generation(receipt.generation.id)
    assert recovered is not None
    assert recovered.status == "completed"
    recovered_record = recovered.attempts[0].provider_execution
    assert recovered_record is not None
    assert recovered_record["phase"] == "completed"
    assert len(recovered.outputs) == 1
    assert len(FakeWanx.calls) == 1


def test_unknown_submitting_record_does_not_reexecute_provider(tmp_path):
    FakeWanx.calls.clear()
    storage, service = _service(tmp_path)
    receipt = _admit_legacy_generation(service, _request("playground-unknown-submitting"))
    assert receipt.generation is not None
    gen = storage.get_generation(receipt.generation.id)
    assert gen is not None
    record = ProviderExecutionRecord.model_validate(gen.attempts[0].provider_execution)
    gen.status = "processing"
    gen.attempts[0].status = "processing"
    gen.attempts[0].provider_execution = record_with_changes(record, phase="submitting").model_dump(mode="json")
    storage.update_generation(gen)

    service.process_generation(gen.id)
    saved = storage.get_generation(gen.id)
    assert saved is not None
    assert saved.status == "needs_confirmation"
    assert saved.attempts[0].status == "needs_confirmation"
    assert saved.outputs == []
    assert FakeWanx.calls == []


def test_old_processing_image_without_provider_record_does_not_pay(tmp_path):
    FakeWanx.calls.clear()
    storage, service = _service(tmp_path)
    legacy = PlaygroundGeneration(
        schema_version=1,
        id="legacy-image",
        client_request_id="legacy-image-request",
        request_contract_version="request-identity-v1",
        request_fingerprint="0" * 64,
        mode=PlaygroundMode.T2I,
        model_id="wan2.7-image-pro",
        prompt="legacy",
        negative_prompt=None,
        parameters={},
        batch_size=1,
        status="processing",
        error=None,
        created_at="2026-10-13T00:00:00Z",
    )
    storage.add_generation(legacy)

    service.process_generation("legacy-image")
    saved = storage.get_generation("legacy-image")
    assert saved is not None
    assert saved.status == "needs_confirmation"
    assert saved.attempts[0].status == "needs_confirmation"
    assert saved.outputs == []
    assert FakeWanx.calls == []


def test_partial_image_batch_preserves_completed_output_and_marks_uncertain_attempt(tmp_path):
    FakeWanx.calls.clear()
    FakeWanx.fail_on_call = 2
    storage, service = _service(tmp_path)
    try:
        saved = _admit_and_process(service, _request("playground-partial-batch", batch_size=2))
        assert saved.status == "needs_confirmation"
        assert len(saved.outputs) == 1
        assert saved.attempts[0].status == "completed"
        assert saved.attempts[1].status == "needs_confirmation"
        partial_record = saved.attempts[1].provider_execution
        assert partial_record is not None
        assert partial_record["phase"] == "submitting"
        assert len(FakeWanx.calls) == 2
    finally:
        FakeWanx.fail_on_call = None
        storage.close()


def test_active_processing_accepted_record_does_not_reenter_or_mark_confirmation(tmp_path):
    FakeWanx.calls.clear()
    storage, service = _service(tmp_path)
    try:
        receipt = _admit_legacy_generation(service, _request("playground-active-accepted"))
        assert receipt.generation is not None
        claimed = storage.claim_generation_for_processing(receipt.generation.id)
        assert claimed is not None

        with service._active_image_processing(receipt.generation.id):
            shared_storage = PlaygroundStorage(tmp_path, writer_lock=storage._writer_lock)
            try:
                _, shared_service = _service(tmp_path, storage=shared_storage)
                shared_service.process_generation(receipt.generation.id)
            finally:
                shared_storage.close()
            active_saved = storage.get_generation(receipt.generation.id)
            assert active_saved is not None
            assert active_saved.status == "processing"
            assert active_saved.attempts[0].status == "processing"
            active_record = active_saved.attempts[0].provider_execution
            assert active_record is not None
            assert active_record["phase"] == "accepted"
            assert FakeWanx.calls == []

            service._process_image_generation(claimed, allow_accepted_execution=True)

        claimed.status = "completed"
        assert storage.update_claimed_generation(claimed)
        saved = storage.get_generation(receipt.generation.id)
        assert saved is not None
        assert saved.status == "completed"
        saved_record = saved.attempts[0].provider_execution
        assert saved_record is not None
        assert saved_record["phase"] == "completed"
        assert len(saved.outputs) == 1
        assert len(FakeWanx.calls) == 1
    finally:
        storage.close()


def test_restart_processing_accepted_record_needs_confirmation_without_reexecution(tmp_path):
    FakeWanx.calls.clear()
    storage, service = _service(tmp_path)
    try:
        receipt = _admit_legacy_generation(service, _request("playground-restart-accepted"))
        assert receipt.generation is not None
        claimed = storage.claim_generation_for_processing(receipt.generation.id)
        assert claimed is not None

        service.process_generation(receipt.generation.id)

        saved = storage.get_generation(receipt.generation.id)
        assert saved is not None
        assert saved.status == "needs_confirmation"
        assert saved.attempts[0].status == "needs_confirmation"
        restart_record = saved.attempts[0].provider_execution
        assert restart_record is not None
        assert restart_record["phase"] == "accepted"
        assert saved.outputs == []
        assert FakeWanx.calls == []
    finally:
        storage.close()


def test_claim_reentry_returns_without_second_provider_call(tmp_path, monkeypatch):
    FakeWanx.calls.clear()
    storage, service = _service(tmp_path)
    real_claim = storage.claim_generation_for_processing
    reentered = False
    try:
        receipt = _admit_legacy_generation(service, _request("playground-claim-reentry"))
        assert receipt.generation is not None

        def claim_with_reentry(generation_id: str):
            nonlocal reentered
            if not reentered:
                reentered = True
                service.process_generation(generation_id)
            return real_claim(generation_id)

        monkeypatch.setattr(storage, "claim_generation_for_processing", claim_with_reentry)
        service.process_generation(receipt.generation.id)

        saved = storage.get_generation(receipt.generation.id)
        assert saved is not None
        assert saved.status == "completed"
        assert len(saved.outputs) == 1
        assert len(FakeWanx.calls) == 1
    finally:
        storage.close()


def test_submitting_reentry_same_thread_does_not_mark_confirmation_or_drop_output(tmp_path):
    class ReenteringWanx(FakeWanx):
        calls: list[dict] = []
        service: PlaygroundService | None = None
        generation_id: str | None = None
        reentered = False

        def generate(self, prompt, output_path, **kwargs):
            if not type(self).reentered:
                type(self).reentered = True
                service = type(self).service
                generation_id = type(self).generation_id
                assert service is not None
                assert generation_id is not None
                service.process_generation(generation_id)
            return super().generate(prompt, output_path, **kwargs)

    storage, service = _service(tmp_path, model_factory=ReenteringWanx)
    ReenteringWanx.calls.clear()
    ReenteringWanx.reentered = False
    try:
        receipt = _admit_legacy_generation(service, _request("playground-submitting-reentry"))
        assert receipt.generation is not None
        ReenteringWanx.service = service
        ReenteringWanx.generation_id = receipt.generation.id

        service.process_generation(receipt.generation.id)

        saved = storage.get_generation(receipt.generation.id)
        assert saved is not None
        assert saved.status == "completed"
        assert saved.attempts[0].status == "completed"
        assert len(saved.outputs) == 1
        assert len(ReenteringWanx.calls) == 1
    finally:
        ReenteringWanx.service = None
        ReenteringWanx.generation_id = None
        storage.close()


def test_parallel_same_generation_callback_returns_while_different_generation_can_run(tmp_path):
    class ParallelReenteringWanx(FakeWanx):
        calls: list[dict] = []
        service: PlaygroundService | None = None
        active_generation_id: str | None = None
        other_generation_id: str | None = None
        callback_started = False

        def generate(self, prompt, output_path, **kwargs):
            if not type(self).callback_started:
                type(self).callback_started = True
                service = type(self).service
                active_generation_id = type(self).active_generation_id
                other_generation_id = type(self).other_generation_id
                assert service is not None
                assert active_generation_id is not None
                assert other_generation_id is not None
                same_thread = threading.Thread(
                    target=service.process_generation,
                    args=(active_generation_id,),
                )
                other_thread = threading.Thread(
                    target=service.process_generation,
                    args=(other_generation_id,),
                )
                same_thread.start()
                other_thread.start()
                same_thread.join(timeout=5)
                other_thread.join(timeout=5)
                assert not same_thread.is_alive()
                assert not other_thread.is_alive()
            return super().generate(prompt, output_path, **kwargs)

    storage, service = _service(tmp_path, model_factory=ParallelReenteringWanx)
    ParallelReenteringWanx.calls.clear()
    ParallelReenteringWanx.callback_started = False
    try:
        first = _admit_legacy_generation(service, _request("playground-parallel-same"))
        other = _admit_legacy_generation(service, _request("playground-parallel-other"))
        assert first.generation is not None
        assert other.generation is not None
        ParallelReenteringWanx.service = service
        ParallelReenteringWanx.active_generation_id = first.generation.id
        ParallelReenteringWanx.other_generation_id = other.generation.id

        service.process_generation(first.generation.id)

        first_saved = storage.get_generation(first.generation.id)
        other_saved = storage.get_generation(other.generation.id)
        assert first_saved is not None
        assert other_saved is not None
        assert first_saved.status == "completed"
        assert other_saved.status == "completed"
        assert len(first_saved.outputs) == 1
        assert len(other_saved.outputs) == 1
        assert len(ParallelReenteringWanx.calls) == 2
    finally:
        ParallelReenteringWanx.service = None
        ParallelReenteringWanx.active_generation_id = None
        ParallelReenteringWanx.other_generation_id = None
        storage.close()


def test_storage_error_releases_active_guard_for_retry_repair(tmp_path, monkeypatch):
    FakeWanx.calls.clear()
    storage, service = _service(tmp_path)
    real_update = storage.update_claimed_generation
    fail_once = True
    try:
        receipt = _admit_legacy_generation(service, _request("playground-final-update-failure"))
        assert receipt.generation is not None

        def fail_final_update(gen):
            nonlocal fail_once
            if fail_once and gen.status == "completed":
                fail_once = False
                raise StorageError("injected final update failure")
            return real_update(gen)

        monkeypatch.setattr(storage, "update_claimed_generation", fail_final_update)
        try:
            service.process_generation(receipt.generation.id)
        except StorageError as exc:
            assert "final update failure" in str(exc)
        else:
            raise AssertionError("final update should fail once")

        monkeypatch.setattr(storage, "update_claimed_generation", real_update)
        service.process_generation(receipt.generation.id)

        saved = storage.get_generation(receipt.generation.id)
        assert saved is not None
        assert saved.status == "completed"
        assert len(saved.outputs) == 1
        assert len(FakeWanx.calls) == 1
    finally:
        storage.close()


def test_playground_core_image_execution_uses_real_dashscope_adapter_with_fake_http_transport(tmp_path, monkeypatch):
    import src.models.image as image_module

    FakeWanx.calls.clear()
    storage = PlaygroundStorage(tmp_path)
    posts: list[dict] = []
    gets: list[str] = []

    class JsonResponse:
        def __init__(self, payload, status_code=200):
            self._payload = payload
            self.status_code = status_code
            self.text = str(payload)

        def json(self):
            return self._payload

    class DownloadResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size=8192):
            data = base64.b64decode(JPEG_240_BASE64)
            yield data[: len(data) // 2]
            yield data[len(data) // 2 :]

    class FakeSession:
        def mount(self, *args, **kwargs):
            return None

        def get(self, url, **kwargs):
            gets.append(url)
            return DownloadResponse()

    def fake_post(url, *, headers, json, timeout):
        posts.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        return JsonResponse({"output": {"task_id": "task-playground-wire"}})

    def fake_get(url, *, headers, timeout):
        gets.append(url)
        return JsonResponse({
            "output": {
                "task_status": "SUCCEEDED",
                "choices": [{"message": {"content": [{"image": "https://example.invalid/generated.jpg"}]}}],
            }
        })

    monkeypatch.setattr(image_module.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(image_module.requests, "post", fake_post)
    monkeypatch.setattr(image_module.requests, "get", fake_get)
    monkeypatch.setattr(image_module.requests, "Session", FakeSession)

    try:
        service = PlaygroundService(
            storage,
            data_dir=tmp_path,
            media_authority_provider=_authority_provider,
            media_authority_resolver=_authority_resolver,
        )
        saved = _admit_and_process(service, _request("playground-real-adapter-wire"))

        assert saved.status == "completed"
        assert len(saved.outputs) == 1
        assert posts
        payload = posts[0]["json"]
        assert posts[0]["headers"]["Authorization"] == f"Bearer {DASHSCOPE_SECRET}"
        assert payload["model"] == "qwen-image-2.0-pro"
        assert payload["input"]["messages"][0]["content"][-1] == {"text": "paint a glass moon"}
        assert payload["parameters"]["seed"] == 0
        assert payload["parameters"]["prompt_extend"] is False
        assert payload["parameters"]["watermark"] is False
        assert any(url.endswith("/api/v1/tasks/task-playground-wire") for url in gets)
        assert "https://example.invalid/generated.jpg" in gets
        assert (tmp_path / saved.outputs[0].media_path).read_bytes().startswith(b"\x89PNG")
    finally:
        storage.close()


@pytest.fixture
def private_image_execution(tmp_path, monkeypatch):
    """Observe actual Core normalization; put every temporary file under the test root."""
    FakeWanx.calls.clear()
    FakeWanx.fail_on_call = None
    storage, service = _service(tmp_path / "managed")
    real_execute = service._execute_core_media
    results = []
    scratches = []

    def observe(*args, **kwargs):
        scratches.append(Path(kwargs["scratch_dir"]))
        result = real_execute(*args, **kwargs)
        results.append(result)
        return result

    monkeypatch.setattr(service, "_execute_core_media", observe)
    try:
        yield storage, service, results, scratches
    finally:
        storage.close()


@pytest.mark.parametrize("commit_uncertain", [False, True])
def test_verified_private_png_survives_prepared_write_failure(private_image_execution, monkeypatch, commit_uncertain):
    storage, service, results, scratches = private_image_execution
    real_write = storage_module.atomic_write_json
    stages = []

    def fail_prepared(path, payload, **kwargs):
        if isinstance(payload, list):
            for generation in payload:
                for attempt in generation.get("attempts", []):
                    record = attempt.get("provider_execution") or {}
                    if record.get("prepared_artifact") is not None:
                        stages.extend(service.image_output_dir.glob(".fetch-*.tmp"))
                        assert any(hashlib.sha256(stage.read_bytes()).hexdigest() == results[0].sha256 for stage in stages)
                        raise StorageError("injected prepared write failure", commit_uncertain=commit_uncertain)
        return real_write(path, payload, **kwargs)

    receipt = _admit_legacy_generation(service, _request(f"retain-prepared-{commit_uncertain}"))
    with monkeypatch.context() as patch:
        patch.setattr(storage_module, "atomic_write_json", fail_prepared)
        with pytest.raises(StorageError, match="prepared write failure"):
            service.process_generation(receipt.generation.id)
    saved = storage.get_generation(receipt.generation.id)
    record = saved.attempts[0].provider_execution
    assert saved.outputs == [] and record["phase"] == "submitting"
    assert record["prepared_artifact"] is None
    assert len(FakeWanx.calls) == 1
    assert stages and all(not stage.exists() for stage in stages)
    assert not (service.data_dir / record["output_ref"]).exists()
    storage.close()
    restarted_storage, restarted = _service(service.data_dir)
    try:
        restarted.process_generation(receipt.generation.id)
        after = restarted_storage.get_generation(receipt.generation.id)
        assert after is not None
        assert after.status == "needs_confirmation" and after.outputs == []
        assert len(FakeWanx.calls) == 1
    finally:
        restarted_storage.close()
    assert results[0].path.is_file(), "verified PNG was deleted along with unpublished stage"
    assert hashlib.sha256(results[0].path.read_bytes()).hexdigest() == results[0].sha256
    assert list(scratches[0].iterdir()) == [results[0].path]
    assert scratches[0].stat().st_mode & 0o777 == 0o700


@pytest.mark.parametrize("failure", ["owner_deleted", "claim_lost", "publish"])
def test_verified_private_png_survives_owner_claim_or_publication_loss(private_image_execution, monkeypatch, failure):
    storage, service, results, scratches = private_image_execution
    observe = service._execute_core_media
    receipt = _admit_legacy_generation(service, _request("retain-" + failure, batch_size=2))

    def lose_after_result(*args, **kwargs):
        result = observe(*args, **kwargs)
        (result.path.parent / "raw-provider.part").write_bytes(b"unverified")
        nested = result.path.parent / "raw-inputs"
        nested.mkdir()
        (nested / "reference.part").write_bytes(b"unverified reference")
        if failure == "owner_deleted":
            assert storage.delete_generation(receipt.generation.id)
        elif failure == "claim_lost":
            changed = storage.get_generation(receipt.generation.id)
            changed.status = "needs_confirmation"
            storage.update_generation(changed)
        return result

    monkeypatch.setattr(service, "_execute_core_media", lose_after_result)
    link = os.link

    def refuse_publish(source, destination, **kwargs):
        if isinstance(source, str) and source.startswith(".fetch-"):
            raise OSError("injected publication failure")
        return link(source, destination, **kwargs)

    if failure == "publish":
        monkeypatch.setattr(os, "link", refuse_publish)
    with pytest.raises(StorageError):
        service.process_generation(receipt.generation.id)
    assert len(FakeWanx.calls) == 1
    assert results[0].path.is_file()
    assert hashlib.sha256(results[0].path.read_bytes()).hexdigest() == results[0].sha256
    assert list(scratches[0].iterdir()) == [results[0].path]
    assert scratches[0].stat().st_mode & 0o777 == 0o700
    saved = storage.get_generation(receipt.generation.id)
    if failure == "owner_deleted":
        assert saved is None
    else:
        assert saved.outputs == []
        assert not (service.data_dir / saved.attempts[0].provider_execution["output_ref"]).exists()
        assert (saved.attempts[0].provider_execution["prepared_artifact"] is not None) == (failure == "publish")
    storage.close()
    restarted_storage, restarted = _service(service.data_dir)
    try:
        restarted.process_generation(receipt.generation.id)
        if failure == "publish":
            recovered = restarted_storage.get_generation(receipt.generation.id)
            assert recovered.status == "needs_confirmation" and recovered.outputs == []
            assert recovered.attempts[0].status == "needs_confirmation"
            assert recovered.attempts[0].provider_execution == saved.attempts[0].provider_execution
            assert recovered.attempts[1] == saved.attempts[1]
        assert len(FakeWanx.calls) == 1
        assert results[0].path.is_file()
        assert hashlib.sha256(results[0].path.read_bytes()).hexdigest() == results[0].sha256
    finally:
        restarted_storage.close()


@pytest.mark.parametrize("alias", [False, True])
def test_durable_png_cleans_canonical_private_scratch(private_image_execution, monkeypatch, tmp_path, alias):
    storage, service, results, scratches = private_image_execution
    actual = tmp_path / "real-temp"
    actual.mkdir()
    temporary_root = tmp_path / "temp-alias" if alias else actual
    if alias:
        temporary_root.symlink_to(actual, target_is_directory=True)
    mkdtemp = service_module.tempfile.mkdtemp
    def in_temporary_root(suffix=None, prefix=None, dir=None):
        return mkdtemp(suffix=suffix, prefix=prefix, dir=temporary_root)

    monkeypatch.setattr(service_module.tempfile, "mkdtemp", in_temporary_root)
    saved = _admit_and_process(service, _request("retain-success"))
    assert saved.status == "completed" and len(saved.outputs) == 1
    assert scratches[0] == scratches[0].resolve()
    assert results[0].path.parent.parent == actual
    assert not scratches[0].exists() and not results[0].path.exists()
    assert (service.data_dir / saved.outputs[0].media_path).is_file()
    assert len(FakeWanx.calls) == 1


@pytest.mark.parametrize("damage", ["kind", "encoding", "hash", "size", "oversize", "symlink", "outside", "partial", "jpeg", "truncated_png"])
def test_invalid_private_image_is_never_retained_or_published(private_image_execution, monkeypatch, tmp_path, damage):
    storage, service, results, scratches = private_image_execution
    observe = service._execute_core_media
    outside = tmp_path / "external.png"

    def damaged(*args, **kwargs):
        result = observe(*args, **kwargs)
        if damage == "kind":
            return replace(result, media_kind="video")
        if damage == "encoding":
            return replace(result, encoding="image/jpeg")
        if damage == "hash":
            return replace(result, sha256="0" * 64)
        if damage == "size":
            return replace(result, size_bytes=result.size_bytes + 1)
        if damage == "oversize":
            return replace(result, size_bytes=service._max_media_bytes + 1)
        if damage in {"outside", "symlink"}:
            outside.write_bytes(result.path.read_bytes())
            if damage == "outside":
                return replace(result, path=outside)
            result.path.unlink()
            result.path.symlink_to(outside)
        if damage == "partial":
            raise RuntimeError("incomplete executor result")
        if damage in {"jpeg", "truncated_png"}:
            payload = base64.b64decode(JPEG_240_BASE64) if damage == "jpeg" else b"\x89PNG\r\n\x1a\ntruncated"
            result.path.write_bytes(payload)
            return replace(result, sha256=hashlib.sha256(payload).hexdigest(), size_bytes=len(payload))
        return result

    monkeypatch.setattr(service, "_execute_core_media", damaged)
    receipt = _admit_legacy_generation(service, _request("retain-invalid-" + damage))
    try:
        service.process_generation(receipt.generation.id)
    except StorageError:
        pass
    saved = storage.get_generation(receipt.generation.id)
    assert saved.outputs == []
    record = saved.attempts[0].provider_execution
    assert record["phase"] == "submitting" and record["prepared_artifact"] is None
    assert not (service.data_dir / record["output_ref"]).exists()
    assert not scratches[0].exists()
    if damage in {"outside", "symlink"}:
        assert outside.is_file()
    assert len(FakeWanx.calls) == 1


def test_private_png_mutated_during_publication_is_not_retained(private_image_execution, monkeypatch):
    storage, service, results, scratches = private_image_execution
    real_store = service_module.store_media_stream

    def change_before_stream(chunks, **kwargs):
        changed = b"changed after initial verification"
        results[0].path.write_bytes(changed)
        return real_store((changed for _chunk in chunks), **kwargs)

    monkeypatch.setattr(service_module, "store_media_stream", change_before_stream)
    receipt = _admit_legacy_generation(service, _request("retain-mutated"))
    with pytest.raises(StorageError):
        service.process_generation(receipt.generation.id)
    saved = storage.get_generation(receipt.generation.id)
    assert saved.outputs == [] and saved.attempts[0].provider_execution["prepared_artifact"] is None
    assert not scratches[0].exists()
    assert len(FakeWanx.calls) == 1


def test_durable_prepared_png_cleans_private_copy_before_local_completion_repair(private_image_execution, monkeypatch):
    storage, service, results, scratches = private_image_execution
    update = storage.update_claimed_generation

    def fail_completion(gen):
        if gen.attempts[0].provider_execution["phase"] == "completed":
            raise StorageError("injected completion failure")
        return update(gen)

    receipt = _admit_legacy_generation(service, _request("retain-durable-repair"))
    with monkeypatch.context() as patch:
        patch.setattr(storage, "update_claimed_generation", fail_completion)
        with pytest.raises(StorageError, match="completion failure"):
            service.process_generation(receipt.generation.id)
    record = storage.get_generation(receipt.generation.id).attempts[0].provider_execution
    assert record["phase"] == "submitting" and record["prepared_artifact"] is not None
    assert (service.data_dir / record["output_ref"]).is_file()
    assert not scratches[0].exists() and not results[0].path.exists()
    storage.close()
    restarted_storage, restarted = _service(service.data_dir)
    try:
        restarted.process_generation(receipt.generation.id)
        recovered = restarted_storage.get_generation(receipt.generation.id)
        assert recovered is not None
        assert recovered.status == "completed" and len(recovered.outputs) == 1
        assert len(FakeWanx.calls) == 1
    finally:
        restarted_storage.close()


@pytest.mark.parametrize("replacement", ["symlink", "directory"])
def test_replaced_private_root_never_deletes_foreign_files(private_image_execution, monkeypatch, tmp_path, replacement):
    storage, service, results, scratches = private_image_execution
    real_write = storage_module.atomic_write_json
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    sentinel = foreign / "user-file.txt"
    sentinel.write_text("external user file must survive")
    renamed = []

    def replace_root_and_fail(path, payload, **kwargs):
        if isinstance(payload, list) and any(
            (attempt.get("provider_execution") or {}).get("prepared_artifact") is not None
            for gen in payload for attempt in gen.get("attempts", [])
        ):
            result = results[0]
            scratch = scratches[0]
            original = scratch.with_name(scratch.name + "-original")
            (foreign / result.path.name).write_bytes(result.path.read_bytes())
            scratch.rename(original)
            renamed.append(original)
            if replacement == "symlink":
                scratch.symlink_to(foreign, target_is_directory=True)
            else:
                foreign.rename(scratch)
            raise StorageError("prepared save failed after private root replacement")
        return real_write(path, payload, **kwargs)

    receipt = _admit_legacy_generation(service, _request("retain-replaced-" + replacement))
    with monkeypatch.context() as patch:
        patch.setattr(storage_module, "atomic_write_json", replace_root_and_fail)
        with pytest.raises(StorageError, match="private root replacement"):
            service.process_generation(receipt.generation.id)
    actual_sentinel = sentinel if replacement == "symlink" else scratches[0] / sentinel.name
    assert actual_sentinel.read_text() == "external user file must survive"
    assert hashlib.sha256((renamed[0] / results[0].path.name).read_bytes()).hexdigest() == results[0].sha256
    saved = storage.get_generation(receipt.generation.id)
    assert saved.outputs == [] and saved.attempts[0].provider_execution["prepared_artifact"] is None
    assert len(FakeWanx.calls) == 1


@pytest.mark.parametrize("loss", ["proof", "owner", "claim", "file"])
def test_published_path_alone_cannot_discard_verified_private_png(private_image_execution, monkeypatch, loss):
    storage, service, results, scratches = private_image_execution
    publish = service._publish_core_image_result

    def lose_durability(gen, idx, record, result, **kwargs):
        proof = publish(gen, idx, record, result, **kwargs)
        saved = storage.get_generation(gen.id)
        if loss == "proof":
            prepared = ProviderExecutionRecord.model_validate(saved.attempts[idx].provider_execution)
            saved.attempts[idx].provider_execution = record_with_changes(prepared, prepared_artifact=None).model_dump(mode="json")
            storage.update_generation(saved)
        elif loss == "owner":
            storage.delete_generation(gen.id)
        elif loss == "claim":
            saved.status = "needs_confirmation"
            storage.update_generation(saved)
        else:
            (service.data_dir / proof["relative_path"]).write_bytes(b"damaged managed artifact")
        return proof

    monkeypatch.setattr(service, "_publish_core_image_result", lose_durability)
    receipt = _admit_legacy_generation(service, _request("retain-undurable-" + loss))
    with pytest.raises(StorageConflictError, match="not durably recorded"):
        service.process_generation(receipt.generation.id)
    assert hashlib.sha256(results[0].path.read_bytes()).hexdigest() == results[0].sha256
    assert list(scratches[0].iterdir()) == [results[0].path]
    saved = storage.get_generation(receipt.generation.id)
    assert saved is None if loss == "owner" else saved.outputs == []
    storage.close()
    restarted_storage, restarted = _service(service.data_dir)
    try:
        restarted.process_generation(receipt.generation.id)
        assert len(FakeWanx.calls) == 1
        assert results[0].path.is_file()
    finally:
        restarted_storage.close()


def test_private_cleanup_failure_does_not_replace_original_storage_error(private_image_execution, monkeypatch, caplog):
    storage, service, results, _scratches = private_image_execution
    observe = service._execute_core_media
    update = storage.update_claimed_generation
    unlink = os.unlink

    def with_unverified_extra(*args, **kwargs):
        result = observe(*args, **kwargs)
        (result.path.parent / "raw-provider.part").write_bytes(b"raw")
        return result

    def failed_prepared(gen):
        if gen.attempts[0].provider_execution["prepared_artifact"] is not None:
            raise StorageError("original prepared save failed")
        return update(gen)

    def failed_cleanup(path, **kwargs):
        if path == "raw-provider.part":
            raise OSError("private cleanup failure")
        return unlink(path, **kwargs)

    monkeypatch.setattr(service, "_execute_core_media", with_unverified_extra)
    receipt = _admit_legacy_generation(service, _request("retain-cleanup-error"))
    with monkeypatch.context() as patch:
        patch.setattr(storage, "update_claimed_generation", failed_prepared)
        patch.setattr(os, "unlink", failed_cleanup)
        with pytest.raises(StorageError, match="original prepared save failed"):
            service.process_generation(receipt.generation.id)
    assert hashlib.sha256(results[0].path.read_bytes()).hexdigest() == results[0].sha256
    assert "Could not safely clean private media directory" in caplog.text
    assert len(FakeWanx.calls) == 1
