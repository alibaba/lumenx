"""Playground HappyHorse 1.1 durable provider admission."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast
import uuid

import pytest

from src.apps.playground.happyhorse_video_admission import HappyHorseVideoAdmission, admitted_happyhorse_11_video_generation
from src.apps.playground.models import GenerateRequest, PlaygroundAttempt, PlaygroundGeneration, PlaygroundMode
from src.apps.comic_gen.api import create_app
from src.apps.playground.service import PlaygroundService
from src.apps.playground.storage import PlaygroundStorage
from src.models.provider_binding import ProviderCredentialStore
from src.models.provider_runtime import ProviderRecoveryRuntime
from src.models.provider_tasks import ProviderScope
from src.utils.generation_intent import IntentValidationError
from src.utils.json_storage import DirectoryWriterLock
from tests.frozen_image_fixtures import rgb_png_bytes
from tests.test_wan3_image_validation import png_bytes


SECRET = "sk-test-" + "h" * 44
WORKSPACE_SCOPE = ProviderScope(
    "dashscope",
    "dashscope",
    "dashscope_workspace",
    "https://happyhorse.cn-beijing.maas.aliyuncs.com",
    "cn-beijing",
    workspace_id="happyhorse",
)


class RuntimeProvider:
    def __init__(self, runtime):
        self.runtime = runtime
        self.calls: list[tuple[str, str]] = []

    def __call__(self, *, provider_model_id: str, gateway: str):
        self.calls.append((provider_model_id, gateway))
        if self.runtime is None:
            raise RuntimeError("missing provider runtime")
        return self.runtime


def _runtime(tmp_path: Path, *, scope: ProviderScope = WORKSPACE_SCOPE, secret: str = SECRET):
    return ProviderRecoveryRuntime(scope, lambda: secret, provider=None)


def _admitter(tmp_path: Path, runtime_provider: RuntimeProvider):
    lease = DirectoryWriterLock(tmp_path)
    lease.acquire()
    storage = PlaygroundStorage(tmp_path, writer_lock=lease)
    credentials = ProviderCredentialStore(tmp_path, writer_lock=storage._writer_lock, runtime_lock=storage._lock)
    admission = HappyHorseVideoAdmission(
        output_root=tmp_path,
        writer_lock=storage._writer_lock,
        runtime_lock=storage._lock,
        runtime_provider=runtime_provider,
        credential_store=credentials,
    )
    return lease, storage, admission


def _record(value: dict[str, Any] | None) -> dict[str, Any]:
    assert value is not None
    return cast(dict[str, Any], value)


def _write_image(tmp_path: Path, name: str, *, width: int = 400, height: int = 400, data: bytes | None = None) -> str:
    path = tmp_path / "playground" / "uploads" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    if data is None:
        data = rgb_png_bytes(name, width=width, height=height)
    path.write_bytes(data)
    return f"playground/uploads/{name}"


def _generation(
    tmp_path: Path,
    *,
    request_id: str = "happyhorse-request",
    mode: PlaygroundMode = PlaygroundMode.T2V,
    model_id: str = "happyhorse-1.1-t2v",
    input_media: list[str] | None = None,
    parameters: dict[str, object] | None = None,
    negative_prompt: str | None = None,
    batch_size: int = 2,
) -> PlaygroundGeneration:
    request = GenerateRequest(
        client_request_id=request_id,
        mode=mode,
        model_id=model_id,
        prompt="A precise HappyHorse Playground request.",
        negative_prompt=negative_prompt,
        input_media=input_media,
        parameters=parameters if parameters is not None else {"duration": 5, "resolution": "720P", "aspect_ratio": "16:9", "seed": 0, "watermark": True},
        batch_size=batch_size,
    )
    return PlaygroundGeneration(
        schema_version=1,
        id=str(uuid.uuid4()),
        client_request_id=request.client_request_id,
        request_contract_version="request-identity-v1",
        request_fingerprint="1" * 64,
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


def test_happyhorse_predicate_routes_only_known_explicit_pairs() -> None:
    assert admitted_happyhorse_11_video_generation(PlaygroundMode.T2V, "happyhorse-1.1-t2v")
    assert admitted_happyhorse_11_video_generation(PlaygroundMode.T2V, "happyhorse/happyhorse-1.1-t2v#t2v")
    assert admitted_happyhorse_11_video_generation(PlaygroundMode.T2V, "happyhorse/happyhorse-1.1-t2v#i2v")
    assert admitted_happyhorse_11_video_generation(PlaygroundMode.I2V, "happyhorse-1.1-i2v")
    assert admitted_happyhorse_11_video_generation(PlaygroundMode.R2V, "happyhorse-1.1-r2v")

    assert admitted_happyhorse_11_video_generation(PlaygroundMode.R2V, "happyhorse-1.1-i2v")
    assert admitted_happyhorse_11_video_generation(PlaygroundMode.I2V, "happyhorse-1.1-r2v")
    assert admitted_happyhorse_11_video_generation(PlaygroundMode.V2V, "happyhorse-1.1-t2v")
    assert not admitted_happyhorse_11_video_generation(PlaygroundMode.V2V, "happyhorse-1.0-video-edit")


def test_happyhorse_t2v_admission_remaps_old_t2v_canonical_without_mutating_generation_identity(tmp_path: Path) -> None:
    provider = RuntimeProvider(_runtime(tmp_path))
    generation = _generation(tmp_path, model_id="happyhorse/happyhorse-1.1-t2v#i2v")
    original_model_id = generation.model_id
    lease, storage, admission = _admitter(tmp_path, provider)
    try:
        admission.admit(generation)

        assert generation.model_id == original_model_id
        assert provider.calls == [("happyhorse-1.1-t2v", "dashscope_workspace")]
        records = [_record(attempt.provider_execution) for attempt in generation.attempts]
        assert len(records) == 2
        first = records[0]
        assert first["provider_model_id"] == "happyhorse-1.1-t2v"
        assert first["scope"] == WORKSPACE_SCOPE.to_dict()
        assert first["intent"]["model_id"] == "happyhorse/happyhorse-1.1-t2v#t2v"
        assert first["intent"]["mode"] == "t2v"
        assert first["intent"]["params"] == {"duration": 5, "ratio": "16:9", "resolution": "720P", "seed": 0, "watermark": True}
        assert first["media"] == []
        assert records[1]["credential_ref"] == first["credential_ref"]
    finally:
        storage.close()
        lease.close()


def test_happyhorse_i2v_freezes_one_first_frame_and_reuses_batch_binding(tmp_path: Path) -> None:
    provider = RuntimeProvider(_runtime(tmp_path))
    frame = _write_image(tmp_path, "first.png", width=300, height=750)
    generation = _generation(
        tmp_path,
        mode=PlaygroundMode.I2V,
        model_id="happyhorse-1.1-i2v",
        input_media=[frame],
        parameters={"duration": 15, "resolution": "1080P", "seed": 0, "watermark": False},
    )
    lease, storage, admission = _admitter(tmp_path, provider)
    try:
        admission.admit(generation)

        assert provider.calls == [("happyhorse-1.1-i2v", "dashscope_workspace")]
        records = [_record(attempt.provider_execution) for attempt in generation.attempts]
        assert records[0]["intent"]["model_id"] == "happyhorse/happyhorse-1.1-video#i2v"
        assert records[0]["intent"]["params"] == {"duration": 15, "resolution": "1080P", "seed": 0, "watermark": False}
        assert [record["media"][0]["role"] for record in records] == ["first_frame", "first_frame"]
        assert [record["media"][0]["sha256"] for record in records] == [records[0]["media"][0]["sha256"]] * 2
        (tmp_path / frame).write_bytes(b"mutated after admission")
        assert records[0]["media"][0]["sha256"] == _record(generation.attempts[0].provider_execution)["media"][0]["sha256"]
    finally:
        storage.close()
        lease.close()


@pytest.mark.parametrize("ratio", ["16:9", "9:16", "1:1", "4:3", "3:4", "9:21", "21:9", "4:5", "5:4"])
def test_happyhorse_t2v_accepts_all_supported_ratios_without_provider_io(tmp_path: Path, ratio: str) -> None:
    provider = RuntimeProvider(_runtime(tmp_path))
    generation = _generation(tmp_path, parameters={"duration": 5, "resolution": "1080P", "aspect_ratio": ratio, "seed": 0, "watermark": False}, batch_size=1)
    lease, storage, admission = _admitter(tmp_path, provider)
    try:
        admission.admit(generation)

        record = _record(generation.attempts[0].provider_execution)
        assert record["intent"]["params"]["ratio"] == ratio
        assert record["media"] == []
        assert provider.calls == [("happyhorse-1.1-t2v", "dashscope_workspace")]
    finally:
        storage.close()
        lease.close()


@pytest.mark.parametrize("ratio", ["16:9", "9:16", "1:1", "4:3", "3:4", "9:21", "21:9", "4:5", "5:4"])
def test_happyhorse_r2v_accepts_all_supported_ratios_without_provider_io(tmp_path: Path, ratio: str) -> None:
    provider = RuntimeProvider(_runtime(tmp_path))
    generation = _generation(
        tmp_path,
        mode=PlaygroundMode.R2V,
        model_id="happyhorse-1.1-r2v",
        input_media=[_write_image(tmp_path, "ratio-ref.png", width=400, height=400)],
        parameters={"duration": 5, "resolution": "1080P", "aspect_ratio": ratio, "seed": 0, "watermark": False},
        batch_size=1,
    )
    lease, storage, admission = _admitter(tmp_path, provider)
    try:
        admission.admit(generation)

        record = _record(generation.attempts[0].provider_execution)
        assert record["intent"]["params"]["ratio"] == ratio
        assert [item["role"] for item in record["media"]] == ["reference_image"]
        assert provider.calls == [("happyhorse-1.1-r2v", "dashscope_workspace")]
    finally:
        storage.close()
        lease.close()


@pytest.mark.parametrize("ratio", ["3:2", "2:3"])
def test_happyhorse_rejects_removed_ratios_before_runtime(tmp_path: Path, ratio: str) -> None:
    provider = RuntimeProvider(_runtime(tmp_path))
    generation = _generation(tmp_path, parameters={"duration": 5, "resolution": "1080P", "aspect_ratio": ratio})
    lease, storage, admission = _admitter(tmp_path, provider)
    try:
        with pytest.raises(IntentValidationError):
            admission.admit(generation)
        assert provider.calls == []
    finally:
        storage.close()
        lease.close()


def test_happyhorse_r2v_freezes_ordered_reference_images_and_preserves_alpha(tmp_path: Path) -> None:
    provider = RuntimeProvider(_runtime(tmp_path))
    media = [
        _write_image(tmp_path, "ref0.png", width=400, height=401),
        _write_image(tmp_path, "ref1.png", data=png_bytes(400, 401, alpha=True)),
    ]
    generation = _generation(
        tmp_path,
        mode=PlaygroundMode.R2V,
        model_id="happyhorse-1.1-r2v",
        input_media=media,
        parameters={"duration": 3, "resolution": "480P", "aspect_ratio": "4:5", "seed": 2147483647, "watermark": True},
        batch_size=1,
    )
    lease, storage, admission = _admitter(tmp_path, provider)
    try:
        admission.admit(generation)

        record = _record(generation.attempts[0].provider_execution)
        assert provider.calls == [("happyhorse-1.1-r2v", "dashscope_workspace")]
        assert record["provider_model_id"] == "happyhorse-1.1-r2v"
        assert record["intent"]["model_id"] == "happyhorse/happyhorse-1.1-video#r2v"
        assert record["intent"]["params"]["ratio"] == "4:5"
        assert [item["role"] for item in record["media"]] == ["reference_image", "reference_image"]
        assert [item["ordinal"] for item in record["media"]] == [0, 1]
    finally:
        storage.close()
        lease.close()


@pytest.mark.parametrize(
    ("mode", "model_id"),
    [
        (PlaygroundMode.R2V, "happyhorse-1.1-i2v"),
        (PlaygroundMode.I2V, "happyhorse-1.1-r2v"),
        (PlaygroundMode.T2V, "happyhorse-1.1-i2v"),
        (PlaygroundMode.I2V, "happyhorse-1.1-t2v"),
    ],
)
def test_happyhorse_known_wrong_pairs_reject_before_runtime(tmp_path: Path, mode: PlaygroundMode, model_id: str) -> None:
    provider = RuntimeProvider(_runtime(tmp_path))
    generation = _generation(tmp_path, mode=mode, model_id=model_id, input_media=[] if mode == PlaygroundMode.T2V else [_write_image(tmp_path, "x.png")])
    lease, storage, admission = _admitter(tmp_path, provider)
    try:
        with pytest.raises(ValueError, match="does not support"):
            admission.admit(generation)
        assert provider.calls == []
    finally:
        storage.close()
        lease.close()


@pytest.mark.parametrize(
    "parameters",
    [
        {"duration": 2, "resolution": "720P"},
        {"duration": 16, "resolution": "720P"},
        {"duration": 5, "resolution": "720p"},
        {"duration": 5, "resolution": "720P", "seed": -1},
        {"duration": 5, "resolution": "720P", "negative_prompt": "bad"},
        {"duration": 5, "resolution": "720P", "prompt_extend": False},
        {"duration": 5, "resolution": "720P", "audio": False},
    ],
)
def test_happyhorse_invalid_parameters_reject_before_runtime(tmp_path: Path, parameters: dict[str, object]) -> None:
    provider = RuntimeProvider(_runtime(tmp_path))
    generation = _generation(tmp_path, parameters=parameters)
    lease, storage, admission = _admitter(tmp_path, provider)
    try:
        with pytest.raises(ValueError):
            admission.admit(generation)
        assert provider.calls == []
    finally:
        storage.close()
        lease.close()



def _service_request(
    tmp_path: Path,
    *,
    request_id: str = "service-happyhorse",
    mode: PlaygroundMode = PlaygroundMode.T2V,
    model_id: str = "happyhorse-1.1-t2v",
    input_media: list[str] | None = None,
    parameters: dict[str, object] | None = None,
    batch_size: int = 1,
) -> GenerateRequest:
    return GenerateRequest(
        client_request_id=request_id,
        mode=mode,
        model_id=model_id,
        prompt="A service-level HappyHorse admission request.",
        negative_prompt=None,
        input_media=input_media,
        parameters=parameters if parameters is not None else {"duration": 5, "resolution": "720P", "aspect_ratio": "16:9", "seed": 0, "watermark": True},
        batch_size=batch_size,
    )


def test_service_happyhorse_t2v_old_canonical_preserves_generation_identity_and_remaps_record(tmp_path: Path) -> None:
    provider = RuntimeProvider(_runtime(tmp_path))
    storage = PlaygroundStorage(tmp_path)
    service = PlaygroundService(storage, data_dir=tmp_path, video_runtime_provider=provider)
    try:
        request = _service_request(tmp_path, model_id="happyhorse/happyhorse-1.1-t2v#i2v")
        receipt = service.admit_generation(request)

        assert receipt.generation is not None
        assert receipt.generation.model_id == "happyhorse/happyhorse-1.1-t2v#i2v"
        record = _record(receipt.generation.attempts[0].provider_execution)
        assert record["provider_model_id"] == "happyhorse-1.1-t2v"
        assert record["intent"]["model_id"] == "happyhorse/happyhorse-1.1-t2v#t2v"
        assert provider.calls == [("happyhorse-1.1-t2v", "dashscope_workspace")]
    finally:
        storage.close()


def test_service_happyhorse_i2v_replay_does_not_refreeze_or_rebind(tmp_path: Path) -> None:
    frame = _write_image(tmp_path, "service-first.png", width=320, height=320)
    provider = RuntimeProvider(_runtime(tmp_path))
    storage = PlaygroundStorage(tmp_path)
    service = PlaygroundService(storage, data_dir=tmp_path, video_runtime_provider=provider)
    try:
        request = _service_request(
            tmp_path,
            request_id="service-happyhorse-replay",
            mode=PlaygroundMode.I2V,
            model_id="happyhorse-1.1-i2v",
            input_media=[frame],
            parameters={"duration": 5, "resolution": "720P", "seed": 0, "watermark": True},
        )
        first = service.admit_generation(request)
        assert first.generation is not None
        record = _record(first.generation.attempts[0].provider_execution)
        frozen_sha = record["media"][0]["sha256"]

        (tmp_path / frame).write_bytes(b"changed after accepted replay")
        replay = service.admit_generation(request)

        assert replay.replayed is True
        assert replay.generation is not None
        assert replay.generation.attempts[0].provider_execution == record
        assert _record(replay.generation.attempts[0].provider_execution)["media"][0]["sha256"] == frozen_sha
        assert provider.calls == [("happyhorse-1.1-i2v", "dashscope_workspace")]
    finally:
        storage.close()


def test_service_happyhorse_invalid_parameters_reject_before_credential_or_history(tmp_path: Path) -> None:
    provider = RuntimeProvider(_runtime(tmp_path))
    storage = PlaygroundStorage(tmp_path)
    service = PlaygroundService(storage, data_dir=tmp_path, video_runtime_provider=provider)
    try:
        with pytest.raises(IntentValidationError):
            service.admit_generation(
                _service_request(tmp_path, parameters={"duration": 5, "resolution": "720P", "negative_prompt": "bad"})
            )
        assert provider.calls == []
        assert storage.list_history() == []
    finally:
        storage.close()


def test_service_happyhorse_11_v2v_rejects_in_core_validator_without_runtime_or_history(tmp_path: Path) -> None:
    provider = RuntimeProvider(_runtime(tmp_path))
    storage = PlaygroundStorage(tmp_path)
    service = PlaygroundService(storage, data_dir=tmp_path, video_runtime_provider=provider)
    try:
        with pytest.raises(IntentValidationError):
            service.admit_generation(
                _service_request(
                    tmp_path,
                    request_id="service-happyhorse-v2v-reject",
                    mode=PlaygroundMode.V2V,
                    model_id="happyhorse-1.1-t2v",
                    parameters={"duration": 5, "resolution": "1080P"},
                )
            )
        assert provider.calls == []
        assert storage.list_history() == []
    finally:
        storage.close()



def test_api_happyhorse_rejects_provider_intent_errors_as_422_before_runtime_or_credentials(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    provider = RuntimeProvider(_runtime(tmp_path))
    app = create_app(data_dir=tmp_path, load_config=False)
    with TestClient(app) as client:
        app.state.playground_service._video_runtime_provider = provider
        ref = _write_image(tmp_path, "api-ref.png", width=420, height=420)
        cases = [
            {
                "client_request_id": "hh-api-weighted-prompt-too-long",
                "mode": "t2v",
                "model_id": "happyhorse-1.1-t2v",
                "prompt": "字" * 2501,
                "parameters": {"duration": 5, "resolution": "1080P", "aspect_ratio": "16:9"},
            },
            {
                "client_request_id": "hh-api-blank-t2v-prompt",
                "mode": "t2v",
                "model_id": "happyhorse-1.1-t2v",
                "prompt": "   ",
                "parameters": {"duration": 5, "resolution": "1080P", "aspect_ratio": "16:9"},
            },
            {
                "client_request_id": "hh-api-blank-r2v-prompt",
                "mode": "r2v",
                "model_id": "happyhorse-1.1-r2v",
                "prompt": "\t\n",
                "input_media": [ref],
                "parameters": {"duration": 5, "resolution": "1080P", "aspect_ratio": "4:5"},
            },
            {
                "client_request_id": "hh-api-seed-too-large",
                "mode": "t2v",
                "model_id": "happyhorse-1.1-t2v",
                "prompt": "A prompt with an invalid seed.",
                "parameters": {"duration": 5, "resolution": "1080P", "aspect_ratio": "16:9", "seed": 2147483648},
            },
        ]
        for payload in cases:
            response = client.post("/playground/generate", json=payload)
            assert response.status_code == 422, response.text
            assert response.json()["detail"]["code"] == "invalid_playground_generation_request"

    assert provider.calls == []
