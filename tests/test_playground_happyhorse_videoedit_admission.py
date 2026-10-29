"""Playground HappyHorse 1.0 VideoEdit durable provider admission."""

from __future__ import annotations

import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast
import uuid

import pytest

from src.apps.playground.happyhorse_video_admission import (
    admitted_happyhorse_video_generation,
    admitted_happyhorse_videoedit_generation,
)
from src.apps.playground.models import GenerateRequest, PlaygroundAttempt, PlaygroundGeneration, PlaygroundMode
from src.apps.playground.service import PlaygroundService
from src.apps.playground.storage import PlaygroundStorage
from src.utils.generation_intent import IntentValidationError

from .test_playground_happyhorse_admission import RuntimeProvider, WORKSPACE_SCOPE, _admitter, _runtime


CANONICAL = "happyhorse/happyhorse-1.0-video-edit#v2v"
OLD_CANONICAL = "happyhorse/happyhorse-1.0-video-edit#i2v"
LEGACY = "happyhorse-1.0-video-edit"


def _ffmpeg_or_skip() -> str:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.skip("ffmpeg is required for immutable HappyHorse VideoEdit fixtures")
    return ffmpeg


def _write_video(
    tmp_path: Path,
    name: str = "happyhorse-videoedit-source.mp4",
    *,
    seconds: float = 3.0,
    size: str = "640x360",
    fps: int = 24,
) -> str:
    upload_root = tmp_path / "playground" / "uploads"
    upload_root.mkdir(parents=True, exist_ok=True)
    target = upload_root / name
    subprocess.run(
        [
            _ffmpeg_or_skip(),
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"color=c=black:s={size}:r={fps}:d={seconds}",
            "-pix_fmt",
            "yuv420p",
            str(target),
        ],
        check=True,
    )
    return f"playground/uploads/{name}"


def _videoedit_generation(
    tmp_path: Path,
    *,
    request_id: str = "happyhorse-videoedit-request",
    model_id: str = LEGACY,
    mode: PlaygroundMode = PlaygroundMode.V2V,
    prompt: str = "A careful HappyHorse VideoEdit request.",
    parameters: dict[str, object] | None = None,
    input_media: list[str] | None = None,
    negative_prompt: str | None = None,
    batch_size: int = 2,
) -> PlaygroundGeneration:
    request = GenerateRequest(
        client_request_id=request_id,
        mode=mode,
        model_id=model_id,
        prompt=prompt,
        negative_prompt=negative_prompt,
        input_media=input_media if input_media is not None else [_write_video(tmp_path)],
        parameters=parameters if parameters is not None else {
            "resolution": "1080P",
            "audio_setting": "origin",
            "seed": 0,
            "watermark": False,
        },
        batch_size=batch_size,
    )
    return PlaygroundGeneration(
        schema_version=1,
        id=str(uuid.uuid4()),
        client_request_id=request.client_request_id,
        request_contract_version="request-identity-v1",
        request_fingerprint="v" * 64,
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


def _service_request(
    tmp_path: Path,
    *,
    request_id: str = "service-happyhorse-videoedit",
    model_id: str = LEGACY,
    input_media: list[str] | None = None,
    parameters: dict[str, object] | None = None,
) -> GenerateRequest:
    return GenerateRequest(
        client_request_id=request_id,
        mode=PlaygroundMode.V2V,
        model_id=model_id,
        prompt="A HappyHorse VideoEdit service request.",
        negative_prompt=None,
        input_media=input_media if input_media is not None else [_write_video(tmp_path)],
        parameters=parameters if parameters is not None else {
            "resolution": "1080P",
            "audio_setting": "origin",
            "seed": 0,
            "watermark": False,
        },
        batch_size=1,
    )


def _record(value: dict[str, Any] | None) -> dict[str, Any]:
    assert value is not None
    return cast(dict[str, Any], value)


def test_happyhorse_videoedit_predicates_route_known_pairs_without_legacy_fallthrough() -> None:
    assert admitted_happyhorse_videoedit_generation(PlaygroundMode.V2V, LEGACY)
    assert admitted_happyhorse_videoedit_generation(PlaygroundMode.V2V, CANONICAL)
    assert admitted_happyhorse_videoedit_generation(PlaygroundMode.V2V, OLD_CANONICAL)

    assert admitted_happyhorse_videoedit_generation(PlaygroundMode.I2V, LEGACY)
    assert admitted_happyhorse_videoedit_generation(PlaygroundMode.R2V, CANONICAL)
    assert admitted_happyhorse_videoedit_generation(PlaygroundMode.T2V, OLD_CANONICAL)
    assert admitted_happyhorse_video_generation(PlaygroundMode.V2V, LEGACY)


def test_happyhorse_videoedit_alias_remaps_to_v2v_without_mutating_generation_identity(tmp_path: Path) -> None:
    provider = RuntimeProvider(_runtime(tmp_path))
    generation = _videoedit_generation(tmp_path, model_id=OLD_CANONICAL)
    original_model_id = generation.model_id
    lease, storage, admission = _admitter(tmp_path, provider)
    try:
        admission.admit(generation)

        assert generation.model_id == original_model_id
        assert provider.calls == [(LEGACY, "dashscope_workspace")]
        records = [_record(attempt.provider_execution) for attempt in generation.attempts]
        first = records[0]
        assert first["provider_model_id"] == LEGACY
        assert first["scope"] == WORKSPACE_SCOPE.to_dict()
        assert first["intent"]["model_id"] == CANONICAL
        assert first["intent"]["mode"] == "v2v"
        assert first["intent"]["params"] == {"audio_setting": "origin", "resolution": "1080P", "seed": 0, "watermark": False}
        assert [record["media"][0]["role"] for record in records] == ["reference_video", "reference_video"]
        assert [record["media"][0]["sha256"] for record in records] == [first["media"][0]["sha256"]] * 2
    finally:
        storage.close()
        lease.close()


def test_happyhorse_videoedit_freezes_single_managed_video_and_binds_workspace_runtime(tmp_path: Path) -> None:
    provider = RuntimeProvider(_runtime(tmp_path))
    generation = _videoedit_generation(tmp_path, model_id=CANONICAL, batch_size=1)
    lease, storage, admission = _admitter(tmp_path, provider)
    try:
        admission.admit(generation)

        assert provider.calls == [(LEGACY, "dashscope_workspace")]
        record = _record(generation.attempts[0].provider_execution)
        assert record["provider_model_id"] == LEGACY
        assert record["intent"]["model_id"] == CANONICAL
        assert record["media"][0]["role"] == "reference_video"
        assert record["media"][0]["ordinal"] == 0
        assert record["media"][0]["declared_media_type"] == "video/mp4"
        original_sha = record["media"][0]["sha256"]

        (tmp_path / generation.input_media[0]).write_bytes(b"changed after admission")
        assert _record(generation.attempts[0].provider_execution)["media"][0]["sha256"] == original_sha
    finally:
        storage.close()
        lease.close()


def test_service_happyhorse_videoedit_replay_does_not_refreeze_or_rebind(tmp_path: Path) -> None:
    source = _write_video(tmp_path, "service-videoedit-source.mp4")
    provider = RuntimeProvider(_runtime(tmp_path))
    storage = PlaygroundStorage(tmp_path)
    service = PlaygroundService(storage, data_dir=tmp_path, video_runtime_provider=provider)
    try:
        request = _service_request(tmp_path, input_media=[source])
        first = service.admit_generation(request)
        assert first.generation is not None
        record = _record(first.generation.attempts[0].provider_execution)
        frozen_sha = record["media"][0]["sha256"]

        (tmp_path / source).write_bytes(b"corrupted after accepted replay")
        replay = service.admit_generation(request)

        assert replay.replayed is True
        assert replay.generation is not None
        assert replay.generation.model_id == LEGACY
        assert replay.generation.attempts[0].provider_execution == record
        assert _record(replay.generation.attempts[0].provider_execution)["media"][0]["sha256"] == frozen_sha
        assert provider.calls == [(LEGACY, "dashscope_workspace")]
    finally:
        storage.close()


@pytest.mark.parametrize(
    "parameters",
    [
        {"duration": 5},
        {"aspect_ratio": "16:9"},
        {"ratio": "16:9"},
        {"resolution": "720p"},
        {"resolution": "480P"},
        {"audio_setting": "silent"},
        {"seed": -1},
        {"seed": 2147483648},
        {"watermark": "false"},
        {"prompt_extend": False},
        {"negative_prompt": "avoid blur"},
    ],
)
def test_happyhorse_videoedit_rejects_invalid_parameters_before_runtime(
    tmp_path: Path, parameters: dict[str, object]
) -> None:
    provider = RuntimeProvider(_runtime(tmp_path))
    generation = _videoedit_generation(tmp_path, parameters=parameters)
    lease, storage, admission = _admitter(tmp_path, provider)
    try:
        with pytest.raises(IntentValidationError):
            admission.admit(generation)
        assert provider.calls == []
    finally:
        storage.close()
        lease.close()


@pytest.mark.parametrize("prompt", ["", "   ", "字" * 2501])
def test_happyhorse_videoedit_rejects_invalid_prompt_before_runtime(tmp_path: Path, prompt: str) -> None:
    provider = RuntimeProvider(_runtime(tmp_path))
    generation = _videoedit_generation(tmp_path, prompt=prompt)
    lease, storage, admission = _admitter(tmp_path, provider)
    try:
        with pytest.raises(IntentValidationError):
            admission.admit(generation)
        assert provider.calls == []
    finally:
        storage.close()
        lease.close()


@pytest.mark.parametrize(
    ("input_media", "video_kwargs"),
    [
        ([], None),
        (["one.mp4", "two.mp4"], None),
        (None, {"seconds": 2.0}),
        (None, {"size": "320x240"}),
        (None, {"size": "1000x360"}),
        (None, {"fps": 5}),
    ],
)
def test_happyhorse_videoedit_rejects_invalid_media_before_runtime(
    tmp_path: Path, input_media: list[str] | None, video_kwargs: dict[str, object] | None
) -> None:
    provider = RuntimeProvider(_runtime(tmp_path))
    if video_kwargs is not None:
        input_media = [_write_video(tmp_path, "invalid-videoedit.mp4", **cast(Any, video_kwargs))]
    generation = _videoedit_generation(tmp_path, input_media=input_media)
    lease, storage, admission = _admitter(tmp_path, provider)
    try:
        with pytest.raises(IntentValidationError):
            admission.admit(generation)
        assert provider.calls == []
    finally:
        storage.close()
        lease.close()


@pytest.mark.parametrize(
    ("mode", "model_id"),
    [
        (PlaygroundMode.I2V, LEGACY),
        (PlaygroundMode.R2V, CANONICAL),
        (PlaygroundMode.T2V, OLD_CANONICAL),
        (PlaygroundMode.V2V, "happyhorse-1.1-i2v"),
    ],
)
def test_known_wrong_happyhorse_videoedit_pairs_reject_before_runtime(
    tmp_path: Path, mode: PlaygroundMode, model_id: str
) -> None:
    provider = RuntimeProvider(_runtime(tmp_path))
    generation = _videoedit_generation(tmp_path, mode=mode, model_id=model_id)
    lease, storage, admission = _admitter(tmp_path, provider)
    try:
        with pytest.raises(IntentValidationError):
            admission.admit(generation)
        assert provider.calls == []
    finally:
        storage.close()
        lease.close()
