from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from src.apps.playground.models import PlaygroundMode
from src.apps.playground.video_admission import admitted_wan27_video_generation

from .test_playground_video_admission import RuntimeProvider, _admitter, _generation, _runtime


def _ffmpeg_or_skip() -> str:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.skip("ffmpeg is required for immutable video admission fixtures")
    return ffmpeg


def _write_video(tmp_path: Path, name: str = "source.mp4", *, seconds: int = 2) -> str:
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
            f"color=c=black:s=320x240:r=24:d={seconds}",
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
    model_id: str = "wan2.7-videoedit",
    mode: PlaygroundMode = PlaygroundMode.V2V,
    params: dict[str, object] | None = None,
    input_media: list[str] | None = None,
    n: int = 1,
):
    return _generation(
        tmp_path,
        mode=mode,
        model_id=model_id,
        input_media=input_media if input_media is not None else [_write_video(tmp_path)],
        batch_size=n,
        parameters={
            "duration": 0,
            "resolution": "720P",
            "aspect_ratio": "3:4",
            "audio_setting": "origin",
            "seed": 0,
            "prompt_extend": False,
            "watermark": False,
            "negative_prompt": "",
            **(params or {}),
        },
    )


def test_wan27_videoedit_predicate_catches_supported_and_wrong_pairs() -> None:
    assert admitted_wan27_video_generation(PlaygroundMode.V2V, "wan2.7-videoedit")
    assert admitted_wan27_video_generation(PlaygroundMode.V2V, "wan/wan2.7-video#v2v")
    assert admitted_wan27_video_generation(PlaygroundMode.V2V, "wan/wan2.7-video#videoedit")

    assert admitted_wan27_video_generation(PlaygroundMode.R2V, "wan2.7-videoedit")
    assert admitted_wan27_video_generation(PlaygroundMode.R2V, "wan/wan2.7-video#videoedit")
    assert admitted_wan27_video_generation(PlaygroundMode.V2V, "wan2.7-r2v")

    assert not admitted_wan27_video_generation(PlaygroundMode.V2V, "wanx-2.1-v2v")



def test_videoedit_old_canonical_alias_freezes_video_as_v2v_without_mutating_request_id(tmp_path: Path) -> None:
    provider = RuntimeProvider(_runtime(tmp_path))
    generation = _videoedit_generation(tmp_path, model_id="wan/wan2.7-video#videoedit")
    original_model_id = generation.model_id
    lease, storage, admission = _admitter(tmp_path, provider)
    try:
        admission.admit(generation)

        assert generation.model_id == original_model_id
        assert provider.calls == [("wan2.7-videoedit", "dashscope")]
        record = generation.attempts[0].provider_execution
        assert record is not None
        assert record["provider_model_id"] == "wan2.7-videoedit"
        assert record["intent"]["model_id"] == "wan/wan2.7-video#v2v"
        assert record["intent"]["mode"] == "v2v"
        assert record["media"][0]["role"] == "reference_video"
    finally:
        storage.close()
        lease.close()

def test_videoedit_admission_freezes_single_source_video_and_binds_runtime(tmp_path: Path) -> None:
    provider = RuntimeProvider(_runtime(tmp_path))
    generation = _videoedit_generation(tmp_path)
    lease, storage, admission = _admitter(tmp_path, provider)
    try:
        admission.admit(generation)

        assert provider.calls == [("wan2.7-videoedit", "dashscope")]
        record = generation.attempts[0].provider_execution
        assert record is not None
        assert record["provider_model_id"] == "wan2.7-videoedit"
        assert record["scope"]["provider"] == "dashscope"
        assert record["intent"]["model_id"] == "wan/wan2.7-video#v2v"
        assert record["intent"]["mode"] == "v2v"
        assert record["intent"]["params"]["duration"] == 0
        assert record["intent"]["params"]["resolution"] == "720P"
        assert record["intent"]["params"]["ratio"] == "3:4"
        assert record["intent"]["params"]["audio_setting"] == "origin"
        assert record["intent"]["params"]["seed"] == 0
        assert record["intent"]["params"]["prompt_extend"] is False
        assert record["intent"]["params"]["watermark"] is False

        assert len(record["media"]) == 1
        media = record["media"][0]
        assert media["role"] == "reference_video"
        assert media["ordinal"] == 0
        assert media["declared_media_type"] == "video/mp4"
        original_sha = media["sha256"]

        source = tmp_path / generation.input_media[0]
        source.write_bytes(b"mutated source")
        assert media["sha256"] == original_sha
    finally:
        storage.close()
        lease.close()


def test_videoedit_batch_reuses_one_frozen_video_binding(tmp_path: Path) -> None:
    provider = RuntimeProvider(_runtime(tmp_path))
    generation = _videoedit_generation(tmp_path, n=3)
    lease, storage, admission = _admitter(tmp_path, provider)
    try:
        admission.admit(generation)

        assert provider.calls == [("wan2.7-videoedit", "dashscope")]
        records = [attempt.provider_execution for attempt in generation.attempts]
        assert all(record is not None for record in records)
        media_hashes = [record["media"][0]["sha256"] for record in records if record is not None]
        assert media_hashes == [media_hashes[0], media_hashes[0], media_hashes[0]]
        assert [record["attempt_id"] for record in records if record is not None] == [
            "video-request-0",
            "video-request-1",
            "video-request-2",
        ]
    finally:
        storage.close()
        lease.close()


@pytest.mark.parametrize(
    ("params", "message"),
    [
        ({"duration": 1}, "duration"),
        ({"duration": 11}, "duration"),
        ({"duration": True}, "duration"),
        ({"resolution": "480P"}, "resolution"),
        ({"audio_setting": "silent"}, "audio_setting"),
        ({"seed": -1}, "seed"),
        ({"seed": True}, "seed"),
    ],
)
def test_videoedit_rejects_invalid_parameters_before_runtime(
    tmp_path: Path, params: dict[str, object], message: str
) -> None:
    provider = RuntimeProvider(_runtime(tmp_path))
    generation = _videoedit_generation(tmp_path, params=params)
    lease, storage, admission = _admitter(tmp_path, provider)
    try:
        with pytest.raises(ValueError, match=message):
            admission.admit(generation)

        assert provider.calls == []
    finally:
        storage.close()
        lease.close()


@pytest.mark.parametrize(
    ("input_media", "message"),
    [
        ([], "exactly one video"),
        (["one.mp4", "two.mp4"], "exactly one video"),
    ],
)
def test_videoedit_rejects_wrong_media_count_before_runtime(
    tmp_path: Path, input_media: list[str], message: str
) -> None:
    provider = RuntimeProvider(_runtime(tmp_path))
    generation = _videoedit_generation(tmp_path, input_media=input_media)
    lease, storage, admission = _admitter(tmp_path, provider)
    try:
        with pytest.raises(ValueError, match=message):
            admission.admit(generation)

        assert provider.calls == []
    finally:
        storage.close()
        lease.close()


def test_videoedit_rejects_unverified_video_before_runtime(tmp_path: Path) -> None:
    provider = RuntimeProvider(_runtime(tmp_path))
    bad_path = tmp_path / "playground" / "uploads" / "bad.mp4"
    bad_path.parent.mkdir(parents=True, exist_ok=True)
    bad_path.write_bytes(b"not a real video")
    generation = _videoedit_generation(tmp_path, input_media=["playground/uploads/bad.mp4"])

    lease, storage, admission = _admitter(tmp_path, provider)
    try:
        with pytest.raises(ValueError, match="video"):
            admission.admit(generation)

        assert provider.calls == []
    finally:
        storage.close()
        lease.close()


@pytest.mark.parametrize(
    ("mode", "model_id"),
    [
        (PlaygroundMode.R2V, "wan2.7-videoedit"),
        (PlaygroundMode.R2V, "wan/wan2.7-video#videoedit"),
        (PlaygroundMode.V2V, "wan2.7-r2v"),
        (PlaygroundMode.T2V, "wan2.7-videoedit"),
    ],
)
def test_known_wrong_wan27_pairs_reject_before_runtime(
    tmp_path: Path, mode: PlaygroundMode, model_id: str
) -> None:
    provider = RuntimeProvider(_runtime(tmp_path))
    generation = _videoedit_generation(tmp_path, mode=mode, model_id=model_id)
    lease, storage, admission = _admitter(tmp_path, provider)
    try:
        with pytest.raises(ValueError, match="does not support"):
            admission.admit(generation)

        assert provider.calls == []
    finally:
        storage.close()
        lease.close()


def test_videoedit_rejects_wrong_runtime_binding(tmp_path: Path) -> None:
    provider = RuntimeProvider(None)
    generation = _videoedit_generation(tmp_path)
    lease, storage, admission = _admitter(tmp_path, provider)
    try:
        with pytest.raises(RuntimeError, match="missing provider runtime"):
            admission.admit(generation)
        assert provider.calls == [("wan2.7-videoedit", "dashscope")]
        assert generation.attempts[0].provider_execution is None
    finally:
        storage.close()
        lease.close()
