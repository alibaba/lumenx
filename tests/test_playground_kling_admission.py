"""Playground Kling durable provider admission."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast
import uuid

import pytest

from src.apps.playground.kling_video_admission import KlingVideoAdmission, admitted_kling_video_generation
from src.apps.playground.models import GenerateRequest, PlaygroundAttempt, PlaygroundGeneration, PlaygroundMode
from src.apps.playground.storage import PlaygroundStorage
from src.models.provider_binding import ProviderCredentialStore
from src.models.provider_runtime import ProviderRecoveryRuntime
from src.models.provider_tasks import ProviderScope
from src.utils.generation_intent import IntentValidationError
from src.utils.json_storage import DirectoryWriterLock
from tests.frozen_image_fixtures import rgb_png_bytes
from tests.test_wan3_image_validation import png_bytes


SECRET = "sk-test-" + "k" * 44
WORKSPACE_SCOPE = ProviderScope(
    "dashscope",
    "dashscope",
    "dashscope_workspace",
    "https://kling-space.cn-beijing.maas.aliyuncs.com",
    "cn-beijing",
    workspace_id="kling-space",
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


def _runtime(tmp_path: Path):
    return ProviderRecoveryRuntime(WORKSPACE_SCOPE, lambda: SECRET, provider=None)


def _admitter(tmp_path: Path, runtime_provider: RuntimeProvider):
    lease = DirectoryWriterLock(tmp_path)
    lease.acquire()
    storage = PlaygroundStorage(tmp_path, writer_lock=lease)
    credentials = ProviderCredentialStore(tmp_path, writer_lock=storage._writer_lock, runtime_lock=storage._lock)
    admission = KlingVideoAdmission(
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


def _write_image(
    tmp_path: Path,
    name: str,
    *,
    width: int = 512,
    height: int = 512,
    data: bytes | None = None,
) -> str:
    path = tmp_path / "playground" / "uploads" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if data is not None else rgb_png_bytes(name, width=width, height=height))
    return f"playground/uploads/{name}"


def _generation(
    tmp_path: Path,
    *,
    request_id: str = "kling-request",
    mode: PlaygroundMode = PlaygroundMode.I2V,
    model_id: str = "kling-v3-i2v",
    input_media: list[str] | None = None,
    parameters: dict[str, object] | None = None,
    negative_prompt: str | None = None,
    batch_size: int = 2,
) -> PlaygroundGeneration:
    request = GenerateRequest(
        client_request_id=request_id,
        mode=mode,
        model_id=model_id,
        prompt="A precise Kling Playground request.",
        negative_prompt=negative_prompt,
        input_media=input_media,
        parameters=parameters if parameters is not None else {"duration": 5, "mode": "pro", "audio": False, "watermark": False},
        batch_size=batch_size,
    )
    return PlaygroundGeneration(
        schema_version=1,
        id=str(uuid.uuid4()),
        client_request_id=request.client_request_id,
        request_contract_version="request-identity-v1",
        request_fingerprint="2" * 64,
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


def test_kling_predicate_routes_known_ids_even_when_mode_is_wrong() -> None:
    assert admitted_kling_video_generation(PlaygroundMode.I2V, "kling-v3-i2v")
    assert admitted_kling_video_generation(PlaygroundMode.R2V, "kling-v3-r2v")
    assert admitted_kling_video_generation(PlaygroundMode.I2V, "kling/kling-v3-video#i2v")
    assert admitted_kling_video_generation(PlaygroundMode.R2V, "kling/kling-v3-video#r2v")
    assert admitted_kling_video_generation(PlaygroundMode.T2V, "kling-v3-i2v")
    assert not admitted_kling_video_generation(PlaygroundMode.I2V, "viduq3-pro-i2v")


def test_kling_i2v_admission_freezes_one_first_frame_with_exact_api_model(tmp_path: Path) -> None:
    provider = RuntimeProvider(_runtime(tmp_path))
    frame = _write_image(tmp_path, "first.png", width=512, height=512)
    generation = _generation(tmp_path, input_media=[frame], negative_prompt="low detail")
    lease, storage, admission = _admitter(tmp_path, provider)
    try:
        admission.admit(generation)

        assert provider.calls == [("kling/kling-v3-video-generation", "dashscope_workspace")]
        records = [_record(attempt.provider_execution) for attempt in generation.attempts]
        assert records[0]["provider_model_id"] == "kling/kling-v3-video-generation"
        assert records[0]["scope"] == WORKSPACE_SCOPE.to_dict()
        assert records[0]["intent"]["model_id"] == "kling/kling-v3-video#i2v"
        assert records[0]["intent"]["mode"] == "i2v"
        assert records[0]["intent"]["params"] == {
            "audio": False,
            "duration": 5,
            "mode": "pro",
            "negative_prompt": "low detail",
            "watermark": False,
        }
        assert records[0]["media"][0]["role"] == "first_frame"
        assert records[0]["media"][0]["ordinal"] == 0
        assert records[1]["media"][0]["sha256"] == records[0]["media"][0]["sha256"]
    finally:
        storage.close()
        lease.close()


def test_kling_r2v_admission_preserves_reference_order_and_ratio(tmp_path: Path) -> None:
    provider = RuntimeProvider(_runtime(tmp_path))
    first = _write_image(tmp_path, "ref-0.png", data=rgb_png_bytes("ref-0", width=600, height=600))
    second = _write_image(tmp_path, "ref-1.png", data=rgb_png_bytes("ref-1", width=600, height=600))
    generation = _generation(
        tmp_path,
        mode=PlaygroundMode.R2V,
        model_id="kling/kling-v3-video#r2v",
        input_media=[first, second],
        parameters={"duration": 3, "mode": "4k", "audio": True, "watermark": False, "aspect_ratio": "9:16"},
        batch_size=1,
    )
    lease, storage, admission = _admitter(tmp_path, provider)
    try:
        admission.admit(generation)

        record = _record(generation.attempts[0].provider_execution)
        assert provider.calls == [("kling/kling-v3-omni-video-generation", "dashscope_workspace")]
        assert record["intent"]["model_id"] == "kling/kling-v3-video#r2v"
        assert record["intent"]["params"] == {
            "aspect_ratio": "9:16",
            "audio": True,
            "duration": 3,
            "mode": "4k",
            "watermark": False,
        }
        assert [(item["role"], item["ordinal"]) for item in record["media"]] == [
            ("reference_image", 0),
            ("reference_image", 1),
        ]
        assert record["media"][0]["sha256"] != record["media"][1]["sha256"]
    finally:
        storage.close()
        lease.close()


@pytest.mark.parametrize(
    "generation",
    [
        lambda tmp_path: _generation(tmp_path, mode=PlaygroundMode.T2V, model_id="kling-v3-i2v"),
        lambda tmp_path: _generation(tmp_path, input_media=[], parameters={"duration": 5, "mode": "pro"}),
        lambda tmp_path: _generation(tmp_path, input_media=["missing.png"], parameters={"duration": 5, "aspect_ratio": "16:9", "mode": "pro"}),
    ],
)
def test_kling_invalid_requests_fail_before_runtime(tmp_path: Path, generation) -> None:
    provider = RuntimeProvider(_runtime(tmp_path))
    lease, storage, admission = _admitter(tmp_path, provider)
    try:
        with pytest.raises(IntentValidationError):
            admission.admit(generation(tmp_path))
        assert provider.calls == []
    finally:
        storage.close()
        lease.close()


def test_kling_rejects_alpha_and_webp_inputs_before_runtime(tmp_path: Path) -> None:
    provider = RuntimeProvider(_runtime(tmp_path))
    alpha = _write_image(tmp_path, "alpha.png", data=png_bytes(512, 512, alpha=True))
    webp = _write_image(tmp_path, "bad.webp", data=rgb_png_bytes("bad-webp", width=512, height=512))
    lease, storage, admission = _admitter(tmp_path, provider)
    try:
        for ref in (alpha, webp):
            with pytest.raises(IntentValidationError):
                admission.admit(_generation(tmp_path, input_media=[ref]))
        assert provider.calls == []
    finally:
        storage.close()
        lease.close()
