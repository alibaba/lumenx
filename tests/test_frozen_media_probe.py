"""Reusable AV probes derive metadata only from verified frozen bytes."""

import hashlib
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.utils import media_probe
from src.utils.media_versions import MediaVersion


def version(data, *, role="reference_video", mime="video/mp4"):
    return MediaVersion(hashlib.sha256(data).hexdigest(), len(data), role, 0, mime)


def probe_payload(**stream_updates):
    return {"format": {"duration": "2.5", "format_name": "mov,mp4,m4a,3gp,3g2,mj2"}, "streams": [{
        "codec_type": "video", "codec_name": "h264", "duration": "2.5", "width": 640,
        "height": 360, "avg_frame_rate": "24000/1001", **stream_updates,
    }]}


def test_probe_verifies_frozen_bytes_uses_private_file_and_removes_it(monkeypatch):
    data = b"fixed container bytes"
    paths = []

    def run(args, **kwargs):
        path = Path(args[-1])
        paths.append(path)
        assert path.read_bytes() == data
        assert path.parent.stat().st_mode & 0o077 == 0
        assert args[args.index("-protocol_whitelist") + 1] == "file"
        assert set(args[args.index("-format_whitelist") + 1].split(",")) <= {"mov", "mp4", "m4a", "3gp", "3g2", "mj2", "matroska", "webm"}
        assert kwargs["timeout"] == 20 and kwargs["stdin"] == subprocess.DEVNULL
        return SimpleNamespace(stdout=json.dumps(probe_payload()))

    monkeypatch.setattr(media_probe.subprocess, "run", run)
    result = media_probe.probe_frozen_media(version(data), data)
    assert result.kind == "video" and result.duration == 2.5
    assert (result.width, result.height) == (640, 360)
    assert result.fps == pytest.approx(24000 / 1001)
    assert all(not path.exists() and not path.parent.exists() for path in paths)


@pytest.mark.parametrize("mutation", ["bytes", "size", "role", "mime", "empty"])
def test_invalid_frozen_identity_is_rejected_before_probe(monkeypatch, mutation):
    data = b"fixed bytes"
    record = version(data)
    if mutation == "bytes":
        data = b"other bytes"
    elif mutation == "size":
        data += b"!"
    elif mutation == "role":
        record = version(data, role="reference_image")
    elif mutation == "mime":
        record = version(data, mime="video/webm")
    else:
        data = b""
    monkeypatch.setattr(media_probe.subprocess, "run", lambda *_a, **_k: pytest.fail("unexpected probe"))
    with pytest.raises(media_probe.MediaProbeError):
        media_probe.probe_frozen_media(record, data)


@pytest.mark.parametrize("payload", [
    [], {"streams": []}, {"streams": [None]}, probe_payload(width=0), probe_payload(height=True),
    {**probe_payload(), "format": {"duration": "nan"}},
    {**probe_payload(), "format": {"duration": "-1"}},
])
def test_malformed_metadata_fails_closed(monkeypatch, tmp_path, payload):
    monkeypatch.setattr(media_probe.subprocess, "run", lambda *_a, **_k: SimpleNamespace(stdout=json.dumps(payload)))
    with pytest.raises(media_probe.MediaProbeError):
        media_probe.probe_media_file(tmp_path / "input.mp4", "video")


def test_missing_fps_remains_unknown_for_legacy_uploads(monkeypatch, tmp_path):
    payload = probe_payload(avg_frame_rate="0/0")
    monkeypatch.setattr(media_probe.subprocess, "run", lambda *_a, **_k: SimpleNamespace(stdout=json.dumps(payload)))
    assert media_probe.probe_media_file(tmp_path / "input.mp4", "video").fps is None


def test_declared_audio_container_mismatch_is_rejected(monkeypatch):
    payload = {"format": {"duration": "2", "format_name": "ogg"}, "streams": [{"codec_type": "audio"}]}
    monkeypatch.setattr(media_probe.subprocess, "run", lambda *_a, **_k: SimpleNamespace(stdout=json.dumps(payload)))
    data = b"audio"
    with pytest.raises(media_probe.MediaProbeError):
        media_probe.probe_frozen_media(version(data, role="reference_audio", mime="audio/wav"), data)


def test_probe_failure_removes_private_file(monkeypatch):
    paths = []

    def fail(args, **kwargs):
        paths.append(Path(args[-1]))
        raise subprocess.TimeoutExpired(args, 20)

    monkeypatch.setattr(media_probe.subprocess, "run", fail)
    data = b"video"
    with pytest.raises(media_probe.MediaProbeError):
        media_probe.probe_frozen_media(version(data), data)
    assert paths and all(not p.parent.exists() for p in paths)


@pytest.mark.parametrize("kind,extension,mime,source", [
    ("video", "mp4", "video/mp4", "color=c=blue:s=320x240:r=24:d=1"),
    ("audio", "wav", "audio/wav", "sine=frequency=440:duration=1"),
])
def test_real_frozen_media_probe(tmp_path, kind, extension, mime, source):
    target = tmp_path / f"sample.{extension}"
    ffmpeg = media_probe.get_ffmpeg_path() or "ffmpeg"
    subprocess.run([ffmpeg, "-v", "error", "-nostdin", "-f", "lavfi", "-i", source, str(target)],
                   capture_output=True, check=True, timeout=20)
    data = target.read_bytes()
    target.unlink()
    result = media_probe.probe_frozen_media(version(data, role=f"reference_{kind}", mime=mime), data)
    assert result.kind == kind and result.duration == pytest.approx(1, abs=0.1)
    if kind == "video":
        assert (result.width, result.height, result.fps) == (320, 240, 24)
