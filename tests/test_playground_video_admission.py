from __future__ import annotations

import base64
from datetime import datetime, timezone
from pathlib import Path
import uuid

import pytest

from src.apps.playground.models import GenerateRequest, PlaygroundAttempt, PlaygroundGeneration, PlaygroundMode
from src.apps.playground.service import PlaygroundService
from src.apps.playground.storage import PlaygroundStorage
from src.apps.playground.video_admission import PlaygroundVideoAdmission
from src.models.provider_binding import ProviderCredentialStore
from src.models.provider_runtime import ProviderRecoveryRuntime
from src.models.provider_tasks import ProviderScope
from src.utils.generation_intent import IntentValidationError
from src.utils.json_storage import DirectoryWriterLock
from tests.test_wan3_image_validation import JPEG_240_BASE64


SECRET_A = "sk-test-" + "a" * 44
SECRET_B = "sk-test-" + "b" * 44
SCOPE_A = ProviderScope("dashscope", "dashscope", "dashscope", "https://dashscope.aliyuncs.com", "cn-beijing")
SCOPE_B = ProviderScope("dashscope", "dashscope", "dashscope", "https://dashscope-intl.aliyuncs.com", "ap-southeast-1")


class RuntimeProvider:
    def __init__(self, runtime):
        self.runtime = runtime
        self.calls: list[tuple[str, str]] = []

    def __call__(self, *, provider_model_id: str, gateway: str):
        self.calls.append((provider_model_id, gateway))
        if self.runtime is None:
            raise RuntimeError("missing provider runtime")
        return self.runtime


def _runtime(tmp_path, *, scope=SCOPE_A, secret=SECRET_A):
    return ProviderRecoveryRuntime(scope, lambda: secret, provider=None)


def _admitter(tmp_path, runtime_provider):
    lease = DirectoryWriterLock(tmp_path)
    lease.acquire()
    storage = PlaygroundStorage(tmp_path, writer_lock=lease)
    credentials = ProviderCredentialStore(tmp_path, writer_lock=storage._writer_lock, runtime_lock=storage._lock)
    admission = PlaygroundVideoAdmission(
        output_root=tmp_path,
        writer_lock=storage._writer_lock,
        runtime_lock=storage._lock,
        runtime_provider=runtime_provider,
        credential_store=credentials,
    )
    return lease, storage, admission


def _generation(tmp_path, *, request_id="video-request", mode=PlaygroundMode.T2V, model_id="wan2.7-t2v", input_media=None, parameters=None, batch_size=2):
    request = GenerateRequest(
        client_request_id=request_id,
        mode=mode,
        model_id=model_id,
        prompt="A moonlit train crosses the sea",
        negative_prompt="",
        input_media=input_media,
        parameters=parameters if parameters is not None else {"duration": 2, "aspect_ratio": "3:4", "seed": 0, "prompt_extend": False, "watermark": False},
        batch_size=batch_size,
    )
    return PlaygroundGeneration(
        schema_version=1,
        id=str(uuid.uuid4()),
        client_request_id=request.client_request_id,
        request_contract_version="request-identity-v1",
        request_fingerprint="0" * 64,
        mode=request.mode,
        model_id=request.model_id,
        prompt=request.prompt,
        negative_prompt=request.negative_prompt,
        input_media=request.input_media or [],
        parameters=request.parameters or {},
        batch_size=request.batch_size or 1,
        outputs=[],
        attempts=[
            PlaygroundAttempt(
                attempt_id=f"{request.client_request_id}-{index}",
                ordinal=index,
                status="pending",
                error=None,
                provider_execution=None,
            )
            for index in range(request.batch_size or 1)
        ],
        status="pending",
        error=None,
        created_at=datetime.now(timezone.utc).isoformat(),
    )


def _write_frame(tmp_path, name="frame.jpg", data=None):
    path = tmp_path / "playground" / "uploads" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if data is not None else base64.b64decode(JPEG_240_BASE64))
    return "playground/uploads/" + name


def test_t2v_video_admission_maps_aspect_ratio_and_freezes_batch_scope_and_credential(tmp_path):
    provider = RuntimeProvider(_runtime(tmp_path, scope=SCOPE_A, secret=SECRET_A))
    lease, storage, admission = _admitter(tmp_path, provider)
    try:
        gen = _generation(tmp_path)
        admission.admit(gen)

        assert provider.calls == [("wan2.7-t2v", "dashscope")]
        records = [attempt.provider_execution for attempt in gen.attempts if attempt.provider_execution is not None]
        assert len(records) == 2
        assert records[0]["provider_model_id"] == "wan2.7-t2v"
        assert records[0]["intent"]["model_id"] == "wan/wan2.7-video#t2v"
        assert records[0]["intent"]["params"] == {
            "duration": 2,
            "negative_prompt": "",
            "prompt_extend": False,
            "ratio": "3:4",
            "resolution": "1080P",
            "seed": 0,
            "watermark": False,
        }
        assert records[0]["media"] == []
        assert records[1]["scope"] == records[0]["scope"] == SCOPE_A.to_dict()
        assert records[1]["credential_ref"] == records[0]["credential_ref"]
        assert records[1]["attempt_id"] != records[0]["attempt_id"]
    finally:
        storage.close()
        lease.close()


def test_video_admission_replay_record_is_not_rebound_after_runtime_changes(tmp_path):
    first_provider = RuntimeProvider(_runtime(tmp_path, scope=SCOPE_A, secret=SECRET_A))
    lease, storage, admission = _admitter(tmp_path, first_provider)
    try:
        gen = _generation(tmp_path, request_id="replay-video")
        storage.add_generation(gen)
        admission.admit(gen)
        storage.update_generation(gen)
        saved = storage.get_generation(gen.id)
        assert saved is not None
        frozen = saved.attempts[0].provider_execution
        assert frozen is not None

        replay = storage.read_request("replay-video")
        assert replay is not None and replay.generation is not None
        changed_provider = RuntimeProvider(_runtime(tmp_path, scope=SCOPE_B, secret=SECRET_B))
        # Replayed records are already accepted by product storage; the service
        # must return them without asking a helper to bind current runtime.
        assert replay.generation.attempts[0].provider_execution == frozen
        assert changed_provider.calls == []
    finally:
        storage.close()
        lease.close()


def test_i2v_video_admission_freezes_exact_first_frame_and_ignores_later_file_mutation(tmp_path):
    frame_ref = _write_frame(tmp_path)
    provider = RuntimeProvider(_runtime(tmp_path))
    lease, storage, admission = _admitter(tmp_path, provider)
    try:
        gen = _generation(
            tmp_path,
            request_id="i2v-freeze",
            mode=PlaygroundMode.I2V,
            model_id="wan/wan2.7-video#i2v",
            input_media=[frame_ref],
            parameters={"duration": 5, "resolution": "720p", "seed": 0, "prompt_extend": False, "watermark": False},
            batch_size=1,
        )
        admission.admit(gen)
        record = gen.attempts[0].provider_execution
        assert record is not None
        media = record["media"]
        assert len(media) == 1
        assert media[0]["role"] == "first_frame"
        original_hash = media[0]["sha256"]

        (tmp_path / frame_ref).write_bytes(b"not the admitted frame")
        assert record["media"][0]["sha256"] == original_hash
        assert record["provider_model_id"] == "wan2.7-i2v"
        assert record["intent"]["params"] == {
            "duration": 5,
            "negative_prompt": "",
            "prompt_extend": False,
            "resolution": "720p",
            "seed": 0,
            "watermark": False,
        }
    finally:
        storage.close()
        lease.close()


@pytest.mark.parametrize("parameters", [
    {"ratio": "16:9", "aspect_ratio": "3:4"},
    {"watermark": 0},
    {"seed": True},
])
def test_video_admission_rejects_bad_parameters_before_runtime_binding(tmp_path, parameters):
    provider = RuntimeProvider(_runtime(tmp_path))
    lease, storage, admission = _admitter(tmp_path, provider)
    try:
        gen = _generation(tmp_path, request_id="bad-params", parameters=parameters, batch_size=1)
        with pytest.raises(IntentValidationError):
            admission.admit(gen)
        assert provider.calls == []
        assert gen.attempts[0].provider_execution is None
    finally:
        storage.close()
        lease.close()


@pytest.mark.parametrize("mode,input_media,parameters", [
    (PlaygroundMode.T2V, ["playground/uploads/frame.png"], {"ratio": "16:9"}),
    (PlaygroundMode.I2V, [], {"duration": 5}),
    (PlaygroundMode.I2V, ["playground/uploads/a.png", "playground/uploads/b.png"], {"duration": 5}),
    (PlaygroundMode.I2V, ["playground/uploads/frame.png"], {"ratio": "16:9"}),
    (PlaygroundMode.I2V, ["playground/uploads/frame.png"], {"aspect_ratio": "16:9"}),
])
def test_video_admission_rejects_unsupported_media_and_i2v_ratio_before_provider_work(tmp_path, mode, input_media, parameters):
    for item in input_media:
        _write_frame(tmp_path, Path(item).name)
    provider = RuntimeProvider(_runtime(tmp_path))
    lease, storage, admission = _admitter(tmp_path, provider)
    try:
        gen = _generation(
            tmp_path,
            request_id="unsupported-video",
            mode=mode,
            model_id="wan2.7-t2v" if mode == PlaygroundMode.T2V else "wan2.7-i2v",
            input_media=input_media,
            parameters=parameters,
            batch_size=1,
        )
        with pytest.raises(IntentValidationError):
            admission.admit(gen)
        assert provider.calls == []
        assert gen.attempts[0].provider_execution is None
    finally:
        storage.close()
        lease.close()


def test_video_admission_rejects_missing_runtime_authority_without_accepting_provider_record(tmp_path):
    provider = RuntimeProvider(None)
    lease, storage, admission = _admitter(tmp_path, provider)
    try:
        gen = _generation(tmp_path, request_id="missing-runtime", batch_size=1)
        with pytest.raises(RuntimeError, match="missing provider runtime"):
            admission.admit(gen)
        assert provider.calls == [("wan2.7-t2v", "dashscope")]
        assert gen.attempts[0].provider_execution is None
    finally:
        storage.close()
        lease.close()


def test_video_admission_records_remain_accepted_and_do_not_claim_legacy_video_execution(tmp_path):
    provider = RuntimeProvider(_runtime(tmp_path))
    lease, storage, admission = _admitter(tmp_path, provider)
    try:
        gen = _generation(tmp_path, request_id="accepted-only", batch_size=1)
        admission.admit(gen)
        record = gen.attempts[0].provider_execution
        assert record is not None
        assert record["phase"] == "accepted"
        assert "handle" not in record or record["handle"] is None
        assert gen.outputs == []
        assert gen.attempts[0].status == "pending"
    finally:
        storage.close()
        lease.close()



def _service_request(request_id="service-video", **changes):
    payload = {
        "client_request_id": request_id,
        "mode": PlaygroundMode.T2V,
        "model_id": "wan2.7-t2v",
        "prompt": "A moonlit train crosses the sea",
        "negative_prompt": "",
        "input_media": None,
        "parameters": {"duration": 2, "aspect_ratio": "3:4", "seed": 0, "prompt_extend": False, "watermark": False},
        "batch_size": 1,
    }
    payload.update(changes)
    return GenerateRequest(**payload)


def test_service_admits_wan27_video_record_and_replay_does_not_rebind_or_execute_legacy(tmp_path, monkeypatch):
    provider = RuntimeProvider(_runtime(tmp_path, scope=SCOPE_A, secret=SECRET_A))
    storage = PlaygroundStorage(tmp_path)
    service = PlaygroundService(storage, data_dir=tmp_path, video_runtime_provider=provider)
    try:
        request = _service_request()
        receipt = service.admit_generation(request)
        assert receipt.generation is not None
        record = receipt.generation.attempts[0].provider_execution
        assert record is not None
        assert record["phase"] == "accepted"
        assert record["provider_model_id"] == "wan2.7-t2v"
        assert provider.calls == [("wan2.7-t2v", "dashscope")]

        provider.runtime = _runtime(tmp_path, scope=SCOPE_B, secret=SECRET_B)
        replay = service.admit_generation(request)
        assert replay.replayed is True
        assert replay.generation is not None
        assert replay.generation.attempts[0].provider_execution == record
        assert provider.calls == [("wan2.7-t2v", "dashscope")]

        monkeypatch.setattr(service, "_generate_video_wanx", lambda *_args, **_kwargs: pytest.fail("Core-bound video must not use legacy generation"))
        execution_calls = []

        class FakeVideoExecution:
            def run_attempt(self, generation_id, attempt_id, **_kwargs):
                execution_calls.append((generation_id, attempt_id))
                from src.apps.playground.provider_video_execution import PlaygroundVideoAttemptAck
                current = storage.get_generation(generation_id)
                assert current is not None
                return PlaygroundVideoAttemptAck(
                    current,
                    next(attempt for attempt in current.attempts if attempt.attempt_id == attempt_id),
                )

        monkeypatch.setattr(service, "video_execution_service", lambda: FakeVideoExecution())
        service.process_generation(receipt.generation.id)
        saved = storage.get_generation(receipt.generation.id)
        assert saved is not None
        assert execution_calls == [(receipt.generation.id, receipt.generation.attempts[0].attempt_id)]
        assert saved.outputs == []
        assert saved.attempts[0].status == "pending"
        assert saved.attempts[0].error is None
        assert saved.attempts[0].provider_execution == record
    finally:
        storage.close()


def test_service_rejects_invalid_wan27_video_admission_without_receipt_or_runtime_binding(tmp_path):
    provider = RuntimeProvider(_runtime(tmp_path))
    storage = PlaygroundStorage(tmp_path)
    service = PlaygroundService(storage, data_dir=tmp_path, video_runtime_provider=provider)
    try:
        with pytest.raises(IntentValidationError):
            service.admit_generation(_service_request(parameters={"ratio": "16:9", "aspect_ratio": "3:4"}))
        assert provider.calls == []
        assert storage.list_history() == []
    finally:
        storage.close()
