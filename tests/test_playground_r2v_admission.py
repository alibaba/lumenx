from __future__ import annotations

import base64

import pytest

from src.apps.playground.models import GenerateRequest, PlaygroundMode
from src.apps.playground.service import PlaygroundService
from src.apps.playground.storage import PlaygroundStorage
from src.apps.playground.video_admission import admitted_wan27_video_generation
from src.utils.generation_intent import IntentValidationError
from src.utils.media_probe import MediaProbeError
from src.utils.media_versions import MediaVersionError
from tests.test_playground_video_admission import RuntimeProvider, SCOPE_A, _admitter, _generation, _runtime
from tests.test_wan3_image_validation import JPEG_240_BASE64


def _write_reference(tmp_path, name: str, data: bytes | None = None) -> str:
    path = tmp_path / "playground" / "uploads" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if data is not None else base64.b64decode(JPEG_240_BASE64))
    return "playground/uploads/" + name


def _r2v_generation(tmp_path, *, input_media: list[str] | None = None, parameters: dict | None = None, model_id="wan2.7-r2v"):
    return _generation(
        tmp_path,
        request_id="r2v-admission",
        mode=PlaygroundMode.R2V,
        model_id=model_id,
        input_media=input_media if input_media is not None else [_write_reference(tmp_path, "ref-0.jpg")],
        parameters=parameters if parameters is not None else {
            "duration": 2,
            "aspect_ratio": "3:4",
            "resolution": "720P",
            "seed": 0,
            "prompt_extend": False,
            "watermark": False,
        },
        batch_size=1,
    )


@pytest.mark.parametrize("model_id", ["wan2.7-r2v", "wan/wan2.7-video#r2v"])
def test_r2v_admission_entrypoint_routes_known_r2v_model_into_core_validator(model_id):
    assert admitted_wan27_video_generation(PlaygroundMode.R2V, model_id) is True
    assert admitted_wan27_video_generation(PlaygroundMode.I2V, model_id) is True
    assert admitted_wan27_video_generation(PlaygroundMode.T2V, model_id) is True
    assert admitted_wan27_video_generation(PlaygroundMode.R2V, "unknown-r2v") is False


@pytest.mark.parametrize("count", [1, 5])
def test_r2v_admission_freezes_ordered_reference_images_and_binds_runtime_once(tmp_path, count):
    refs = [_write_reference(tmp_path, f"ref-{index}.jpg") for index in range(count)]
    refs.append(refs[0])
    provider = RuntimeProvider(_runtime(tmp_path))
    lease, storage, admission = _admitter(tmp_path, provider)
    try:
        gen = _r2v_generation(tmp_path, input_media=refs[:count], parameters={
            "duration": 2,
            "aspect_ratio": "3:4",
            "resolution": "720P",
            "seed": 0,
            "prompt_extend": False,
            "watermark": False,
        })

        admission.admit(gen)

        record = gen.attempts[0].provider_execution
        assert provider.calls == [("wan2.7-r2v", "dashscope")]
        assert record is not None
        assert record["provider_model_id"] == "wan2.7-r2v"
        assert record["intent"]["model_id"] == "wan/wan2.7-video#r2v"
        assert record["intent"]["mode"] == "r2v"
        assert record["intent"]["params"] == {
            "duration": 2,
            "negative_prompt": "",
            "prompt_extend": False,
            "ratio": "3:4",
            "resolution": "720P",
            "seed": 0,
            "watermark": False,
        }
        assert [item["role"] for item in record["media"]] == ["reference_image"] * count
        assert [item["ordinal"] for item in record["media"]] == list(range(count))
        assert len({(item["sha256"], item["ordinal"]) for item in record["media"]}) == count
    finally:
        storage.close()
        lease.close()


def test_r2v_admission_preserves_duplicate_sources_as_separate_ordinals(tmp_path):
    ref = _write_reference(tmp_path, "same-ref.jpg")
    provider = RuntimeProvider(_runtime(tmp_path))
    lease, storage, admission = _admitter(tmp_path, provider)
    try:
        gen = _r2v_generation(tmp_path, input_media=[ref, ref])

        admission.admit(gen)

        record = gen.attempts[0].provider_execution
        assert record is not None
        media = record["media"]
        assert [item["ordinal"] for item in media] == [0, 1]
        assert media[0]["sha256"] == media[1]["sha256"]
    finally:
        storage.close()
        lease.close()


@pytest.mark.parametrize("count", [0, 6])
def test_r2v_admission_rejects_reference_image_count_before_runtime_binding(tmp_path, count):
    refs = [_write_reference(tmp_path, f"ref-{index}.jpg") for index in range(count)]
    provider = RuntimeProvider(_runtime(tmp_path))
    lease, storage, admission = _admitter(tmp_path, provider)
    try:
        gen = _r2v_generation(tmp_path, input_media=refs)

        with pytest.raises(IntentValidationError):
            admission.admit(gen)

        assert provider.calls == []
        assert gen.attempts[0].provider_execution is None
    finally:
        storage.close()
        lease.close()


def test_r2v_admission_rejects_bad_reference_image_bytes_before_runtime_binding(tmp_path):
    bad_ref = _write_reference(tmp_path, "bad.jpg", data=b"not an image")
    provider = RuntimeProvider(_runtime(tmp_path))
    lease, storage, admission = _admitter(tmp_path, provider)
    try:
        gen = _r2v_generation(tmp_path, input_media=[bad_ref])

        with pytest.raises(MediaProbeError):
            admission.admit(gen)

        assert provider.calls == []
        assert gen.attempts[0].provider_execution is None
    finally:
        storage.close()
        lease.close()


@pytest.mark.parametrize("mode,model_id", [
    (PlaygroundMode.I2V, "wan2.7-r2v"),
    (PlaygroundMode.R2V, "wan2.7-i2v"),
    (PlaygroundMode.V2V, "wan2.7-r2v"),
])
def test_r2v_admission_rejects_wrong_model_or_mode_before_runtime_binding(tmp_path, mode, model_id):
    ref = _write_reference(tmp_path, "ref.jpg")
    provider = RuntimeProvider(_runtime(tmp_path))
    lease, storage, admission = _admitter(tmp_path, provider)
    try:
        gen = _generation(
            tmp_path,
            request_id="bad-r2v-mode",
            mode=mode,
            model_id=model_id,
            input_media=[ref],
            parameters={"duration": 2, "ratio": "16:9"},
            batch_size=1,
        )

        with pytest.raises(IntentValidationError):
            admission.admit(gen)

        assert provider.calls == []
        assert gen.attempts[0].provider_execution is None
    finally:
        storage.close()
        lease.close()


def test_r2v_admission_rejects_ratio_alias_conflict_before_runtime_binding(tmp_path):
    provider = RuntimeProvider(_runtime(tmp_path))
    lease, storage, admission = _admitter(tmp_path, provider)
    try:
        gen = _r2v_generation(tmp_path, parameters={"ratio": "16:9", "aspect_ratio": "3:4"})

        with pytest.raises(IntentValidationError):
            admission.admit(gen)

        assert provider.calls == []
        assert gen.attempts[0].provider_execution is None
    finally:
        storage.close()
        lease.close()


def _service_request(tmp_path, *, request_id="service-r2v", input_media: list[str] | None = None, mode=PlaygroundMode.R2V, model_id="wan2.7-r2v", batch_size=1):
    return GenerateRequest(
        client_request_id=request_id,
        mode=mode,
        model_id=model_id,
        prompt="A moonlit train crosses the sea",
        negative_prompt="",
        input_media=input_media if input_media is not None else [_write_reference(tmp_path, "service-ref-0.jpg")],
        parameters={
            "duration": 2,
            "aspect_ratio": "16:9",
            "resolution": "720P",
            "seed": 0,
            "prompt_extend": False,
            "watermark": False,
        },
        batch_size=batch_size,
    )


@pytest.mark.parametrize("mode,model_id", [
    (PlaygroundMode.I2V, "wan2.7-r2v"),
    (PlaygroundMode.T2V, "wan2.7-r2v"),
    (PlaygroundMode.V2V, "wan2.7-r2v"),
    (PlaygroundMode.R2V, "wan2.7-i2v"),
])
def test_service_wrong_mode_for_known_wan27_r2v_model_reaches_core_validator_not_legacy(tmp_path, mode, model_id):
    provider = RuntimeProvider(_runtime(tmp_path))
    storage = PlaygroundStorage(tmp_path)
    service = PlaygroundService(storage, data_dir=tmp_path, video_runtime_provider=provider)
    try:
        with pytest.raises(IntentValidationError):
            service.admit_generation(_service_request(tmp_path, mode=mode, model_id=model_id))

        assert provider.calls == []
        assert storage.list_history() == []
    finally:
        storage.close()


def test_service_r2v_batch_four_binds_runtime_once_for_all_attempts(tmp_path):
    refs = [_write_reference(tmp_path, f"batch-ref-{index}.jpg") for index in range(2)]
    provider = RuntimeProvider(_runtime(tmp_path, scope=SCOPE_A))
    storage = PlaygroundStorage(tmp_path)
    service = PlaygroundService(storage, data_dir=tmp_path, video_runtime_provider=provider)
    try:
        receipt = service.admit_generation(_service_request(tmp_path, input_media=refs, batch_size=4))

        assert receipt.generation is not None
        assert provider.calls == [("wan2.7-r2v", "dashscope")]
        records = [attempt.provider_execution for attempt in receipt.generation.attempts]
        assert len(records) == 4
        assert all(record is not None for record in records)
        accepted_records = [record for record in records if record is not None]
        assert {record["credential_ref"] for record in accepted_records} == {accepted_records[0]["credential_ref"]}
        assert {tuple((item["role"], item["ordinal"]) for item in record["media"]) for record in accepted_records} == {
            (("reference_image", 0), ("reference_image", 1)),
        }
        assert {record["scope"]["base_url"] for record in accepted_records} == {SCOPE_A.base_url}
    finally:
        storage.close()


def test_service_r2v_replay_does_not_refreeze_or_rebind(tmp_path):
    ref = _write_reference(tmp_path, "replay-ref.jpg")
    provider = RuntimeProvider(_runtime(tmp_path))
    storage = PlaygroundStorage(tmp_path)
    service = PlaygroundService(storage, data_dir=tmp_path, video_runtime_provider=provider)
    try:
        request = _service_request(tmp_path, request_id="replay-r2v", input_media=[ref])
        first = service.admit_generation(request)
        assert first.generation is not None
        record = first.generation.attempts[0].provider_execution
        assert record is not None
        frozen_hash = record["media"][0]["sha256"]

        (tmp_path / ref).write_bytes(b"changed bytes after admission")
        replay = service.admit_generation(request)

        assert replay.replayed is True
        assert replay.generation is not None
        assert replay.generation.attempts[0].provider_execution == record
        replay_record = replay.generation.attempts[0].provider_execution
        assert replay_record is not None
        assert replay_record["media"][0]["sha256"] == frozen_hash
        assert provider.calls == [("wan2.7-r2v", "dashscope")]
    finally:
        storage.close()


def test_service_r2v_rejects_path_escape_before_runtime_binding(tmp_path):
    provider = RuntimeProvider(_runtime(tmp_path))
    storage = PlaygroundStorage(tmp_path)
    service = PlaygroundService(storage, data_dir=tmp_path, video_runtime_provider=provider)
    try:
        with pytest.raises(MediaVersionError):
            service.admit_generation(_service_request(tmp_path, input_media=["../outside.jpg"]))

        assert provider.calls == []
        assert storage.list_history() == []
    finally:
        storage.close()
