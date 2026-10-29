from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from threading import RLock
from types import SimpleNamespace
import base64
import json
import os
import shutil

import pytest

from src.apps.atelier import media_upload
from src.utils.json_storage import DirectoryWriterLock, StorageError
from tests.test_merge_media_integration import _make_clip, _make_tone

PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)


@pytest.fixture
def owner(tmp_path):
    root = tmp_path / "runtime"
    root.mkdir()
    state = SimpleNamespace(root=root, lease=DirectoryWriterLock(root).acquire(), lock=RLock(), closed=False)

    @contextmanager
    def guard():
        with state.lock:
            if state.closed:
                raise StorageError("Runtime is closed")
            state.lease.assert_owned()
            yield

    state.guard = guard
    try:
        yield state
    finally:
        state.closed = True
        state.lease.close()


def _media_tool(name: str) -> str:
    env_name = "LUMENX_TEST_FFMPEG" if name == "ffmpeg" else "LUMENX_TEST_FFPROBE"
    path = os.environ.get(env_name) or shutil.which(name)
    if not path:
        pytest.skip(f"{name} not available")
    return path


def _path_chunks(path: Path):
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            yield chunk


def upload(owner, chunks, filename="pixel.png", content_type="image/png", **kwargs):
    return media_upload.save_managed_upload(
        chunks,
        filename=filename,
        content_type=content_type,
        data_dir=owner.root,
        writer_lock=owner.lease,
        storage_guard=owner.guard,
        upload_id_factory=kwargs.pop("upload_id_factory", lambda: "upload-id"),
        **kwargs,
    )


def test_image_upload_validates_before_private_managed_publish(owner):
    result = upload(owner, [PNG_1X1[:10], PNG_1X1[10:]])

    assert result.url == "uploads/upload-id.png"
    assert result.media_kind == "image"
    assert result.width == 1
    assert result.height == 1
    assert result.duration is None
    assert result.poster_url is None
    assert result.sha256
    assert result.size_bytes == len(PNG_1X1)
    assert (owner.root / result.url).read_bytes() == PNG_1X1
    assert not list(owner.root.glob(".atelier-upload-*"))


def test_image_upload_rejects_bad_mime_extension_empty_and_invalid_bytes(owner):
    with pytest.raises(media_upload.AtelierMediaUploadError, match="Unsupported media file type"):
        upload(owner, [PNG_1X1], filename="pixel.txt", content_type="image/png")
    with pytest.raises(media_upload.AtelierMediaUploadError, match="Unsupported media file type"):
        upload(owner, [PNG_1X1], filename="pixel.png", content_type="video/mp4")
    with pytest.raises(media_upload.AtelierMediaUploadError, match="Empty media upload"):
        upload(owner, [])
    with pytest.raises(media_upload.AtelierMediaUploadError, match="Invalid image upload"):
        upload(owner, [b"not an image"])
    assert not (owner.root / "uploads").exists()


def test_upload_size_limit_and_existing_destination_are_rejected(owner):
    with pytest.raises(media_upload.AtelierMediaUploadError, match="exceeds"):
        upload(owner, [b"1234"], max_bytes_by_kind={"image": 3, "audio": 10, "video": 10})
    with pytest.raises(media_upload.AtelierMediaUploadError, match="positive integer"):
        upload(owner, [PNG_1X1], max_bytes_by_kind={"image": True, "audio": 10, "video": 10})
    (owner.root / "uploads").mkdir()
    (owner.root / "uploads" / "upload-id.png").write_bytes(b"existing")
    with pytest.raises(media_upload.AtelierMediaUploadError, match="already exists"):
        upload(owner, [PNG_1X1])
    assert (owner.root / "uploads" / "upload-id.png").read_bytes() == b"existing"


def test_real_wav_and_mp4_uploads_validate_and_publish_poster(owner, tmp_path):
    ffmpeg = _media_tool("ffmpeg")
    tone = tmp_path / "tone.wav"
    clip = tmp_path / "clip.mp4"
    _make_tone(ffmpeg, tone, freq=440, duration=0.25)
    _make_clip(ffmpeg, clip, color="red", freq=660, duration=0.3)

    audio = upload(
        owner,
        _path_chunks(tone),
        filename="tone.wav",
        content_type="audio/wav",
        upload_id_factory=lambda: "tone",
    )
    video = upload(
        owner,
        _path_chunks(clip),
        filename="clip.mp4",
        content_type="video/mp4",
        upload_id_factory=lambda: "clip",
    )

    assert audio.media_kind == "audio"
    assert audio.duration and audio.duration > 0
    assert audio.width is None and audio.height is None
    assert (owner.root / audio.url).read_bytes() == tone.read_bytes()
    assert video.media_kind == "video"
    assert video.duration and video.duration > 0
    assert video.width == 64
    assert video.height == 64
    assert (owner.root / video.url).read_bytes() == clip.read_bytes()
    assert video.poster_url == "uploads/clip_poster.jpg"
    assert (owner.root / video.poster_url).stat().st_size > 0



def test_audio_and_video_probe_metadata_and_video_poster_partial_failure(owner, monkeypatch):
    calls = []

    def fake_probe(path: Path, kind: str):
        calls.append(("probe", kind, path.read_bytes()))
        if kind == "audio":
            return media_upload.ProbedMedia(kind="audio", duration=1.25)
        return media_upload.ProbedMedia(kind="video", duration=2.5, width=640, height=360)

    def fake_poster(path: Path, destination: Path) -> bool:
        calls.append(("poster", path.name, destination.name))
        return False

    monkeypatch.setattr(media_upload, "_probe_media", fake_probe)
    monkeypatch.setattr(media_upload, "_extract_video_poster", fake_poster)

    audio = upload(owner, [b"audio bytes"], filename="sound.mp3", content_type="application/octet-stream", upload_id_factory=lambda: "audio-id")
    video = upload(owner, [b"video bytes"], filename="clip.mp4", content_type="video/mp4", upload_id_factory=lambda: "video-id")

    assert audio.url == "uploads/audio-id.mp3"
    assert audio.media_kind == "audio"
    assert audio.duration == 1.25
    assert video.url == "uploads/video-id.mp4"
    assert video.media_kind == "video"
    assert video.duration == 2.5
    assert video.width == 640
    assert video.height == 360
    assert video.poster_url is None
    assert (owner.root / video.url).read_bytes() == b"video bytes"
    assert calls[0][0] == "probe" and calls[-1][0] == "poster"


def test_probe_failure_does_not_publish(owner, monkeypatch):
    def fail_probe(_path: Path, _kind: str):
        raise media_upload.AtelierMediaUploadError("Uploaded content does not match its media type")

    monkeypatch.setattr(media_upload, "_probe_media", fail_probe)

    with pytest.raises(media_upload.AtelierMediaUploadError, match="does not match"):
        upload(owner, [b"video bytes"], filename="clip.mp4", content_type="video/mp4")

    assert not (owner.root / "uploads").exists()
    assert not list(owner.root.glob(".atelier-upload-*"))


def test_fake_png_header_is_rejected_by_real_decoder(owner):
    fake_png = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + (1).to_bytes(4, "big") + (1).to_bytes(4, "big") + b"not-real-pixels"

    with pytest.raises(media_upload.AtelierMediaUploadError, match="Invalid image upload"):
        upload(owner, [fake_png], filename="fake.png", content_type="image/png")

    assert not (owner.root / "uploads").exists()


def test_verified_upload_bytes_cannot_be_swapped_before_publish(owner, monkeypatch):
    original_chunks = media_upload._verified_file_chunks
    swapped = {"value": False}

    def swap_before_publish(path: Path, **kwargs):
        path.write_bytes(PNG_1X1 + b"tampered")
        swapped["value"] = True
        yield from original_chunks(path, **kwargs)

    monkeypatch.setattr(media_upload, "_verified_file_chunks", swap_before_publish)

    with pytest.raises(media_upload.AtelierMediaUploadError, match="changed after validation"):
        upload(owner, [PNG_1X1])

    assert swapped["value"] is True
    assert not (owner.root / "uploads" / "upload-id.png").exists()


def test_image_probe_uses_explicit_file_only_protocol_and_demuxer_whitelists(owner, monkeypatch):
    calls = []

    def fake_run(args, **_kwargs):
        calls.append(list(args))
        if "-show_streams" in args:
            return SimpleNamespace(stdout=json.dumps({"streams": [{"codec_type": "video", "codec_name": "png", "width": 1, "height": 1}]}))
        return SimpleNamespace(stdout="")

    monkeypatch.setattr(media_upload.subprocess, "run", fake_run)

    result = upload(owner, [PNG_1X1])

    assert result.url == "uploads/upload-id.png"
    assert len(calls) == 2
    for args in calls:
        assert "-protocol_whitelist" in args
        protocol_index = args.index("-protocol_whitelist")
        assert args[protocol_index + 1] == "file"
        assert "-format_whitelist" in args
        formats = set(args[args.index("-format_whitelist") + 1].split(","))
        assert formats <= {"image2", "png_pipe", "jpeg_pipe", "webp_pipe", "gif"}
        assert "concat" not in formats
        assert "hls" not in formats
        assert "dash" not in formats


def test_cleanup_failure_after_primary_publish_is_commit_uncertain(owner, monkeypatch, tmp_path):
    stage_root = tmp_path / "private-stage"

    class FailingTemporaryDirectory:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            stage_root.mkdir()
            return str(stage_root)

        def __exit__(self, _exc_type, _exc, _tb):
            raise OSError("cleanup failed")

    monkeypatch.setattr(media_upload.tempfile, "TemporaryDirectory", FailingTemporaryDirectory)

    with pytest.raises(StorageError) as error:
        upload(owner, [PNG_1X1])

    assert error.value.commit_uncertain is True
    assert (owner.root / "uploads" / "upload-id.png").read_bytes() == PNG_1X1



def test_upload_id_rejects_path_and_special_characters(owner):
    with pytest.raises(media_upload.AtelierMediaUploadError, match="Invalid upload id"):
        upload(owner, [PNG_1X1], upload_id_factory=lambda: "bad.id")
    with pytest.raises(media_upload.AtelierMediaUploadError, match="Invalid upload id"):
        upload(owner, [PNG_1X1], upload_id_factory=lambda: "bad$id")
    assert not (owner.root / "uploads").exists()


def test_runtime_closed_during_image_probe_prevents_publish(owner, monkeypatch):
    def close_during_probe(_path: Path):
        owner.closed = True
        return media_upload.ProbedMedia(kind="image", width=1, height=1)

    monkeypatch.setattr(media_upload, "_probe_image", close_during_probe)

    with pytest.raises(StorageError):
        upload(owner, [PNG_1X1])

    assert not (owner.root / "uploads").exists()


def test_storage_error_after_primary_publish_is_commit_uncertain(owner, monkeypatch):
    original_poster = media_upload._publish_video_poster

    def fake_probe(_path: Path, kind: str):
        return media_upload.ProbedMedia(kind=kind, duration=1.0, width=320, height=180)

    def fail_after_primary(*args, **kwargs):
        raise StorageError("poster definite failure", commit_uncertain=False)

    monkeypatch.setattr(media_upload, "_probe_media", fake_probe)
    monkeypatch.setattr(media_upload, "_publish_video_poster", fail_after_primary)

    with pytest.raises(StorageError) as error:
        upload(owner, [b"video bytes"], filename="clip.mp4", content_type="video/mp4")

    assert error.value.commit_uncertain is True
    assert (owner.root / "uploads" / "upload-id.mp4").read_bytes() == b"video bytes"
    monkeypatch.setattr(media_upload, "_publish_video_poster", original_poster)



def test_runtime_closed_during_poster_publish_is_commit_uncertain(owner, monkeypatch):
    def fake_probe(_path: Path, kind: str):
        return media_upload.ProbedMedia(kind=kind, duration=1.0, width=320, height=180)

    def close_after_poster(_path: Path, destination: Path) -> bool:
        destination.write_bytes(PNG_1X1)
        owner.closed = True
        return True

    monkeypatch.setattr(media_upload, "_probe_media", fake_probe)
    monkeypatch.setattr(media_upload, "_extract_video_poster", close_after_poster)

    with pytest.raises(StorageError) as error:
        upload(owner, [b"video bytes"], filename="clip.mp4", content_type="video/mp4")

    assert error.value.commit_uncertain is True
    assert (owner.root / "uploads" / "upload-id.mp4").read_bytes() == b"video bytes"


def test_runtime_closed_during_missing_poster_is_commit_uncertain(owner, monkeypatch):
    def fake_probe(_path: Path, kind: str):
        return media_upload.ProbedMedia(kind=kind, duration=1.0, width=320, height=180)

    def close_and_no_poster(_path: Path, _destination: Path) -> bool:
        owner.closed = True
        return False

    monkeypatch.setattr(media_upload, "_probe_media", fake_probe)
    monkeypatch.setattr(media_upload, "_extract_video_poster", close_and_no_poster)

    with pytest.raises(StorageError) as error:
        upload(owner, [b"video bytes"], filename="clip.mp4", content_type="video/mp4")

    assert error.value.commit_uncertain is True
    assert (owner.root / "uploads" / "upload-id.mp4").read_bytes() == b"video bytes"



def test_video_poster_commit_uncertain_is_not_downgraded_to_missing_poster(owner, monkeypatch):
    original_store = media_upload.store_upload_stream

    def fake_probe(_path: Path, kind: str):
        return media_upload.ProbedMedia(kind=kind, duration=1.0, width=320, height=180)

    def fake_poster(_path: Path, destination: Path) -> bool:
        destination.write_bytes(PNG_1X1)
        return True

    def fail_uncertain_poster(chunks, **kwargs):
        if kwargs["output_ref"].endswith("_poster.jpg"):
            raise StorageError("poster visible uncertain", commit_uncertain=True)
        return original_store(chunks, **kwargs)

    monkeypatch.setattr(media_upload, "_probe_media", fake_probe)
    monkeypatch.setattr(media_upload, "_extract_video_poster", fake_poster)
    monkeypatch.setattr(media_upload, "store_upload_stream", fail_uncertain_poster)

    with pytest.raises(StorageError) as error:
        upload(owner, [b"video bytes"], filename="clip.mp4", content_type="video/mp4")

    assert error.value.commit_uncertain is True


def test_discard_managed_upload_poster_failure_is_commit_uncertain(owner):
    uploads = owner.root / "uploads"
    uploads.mkdir()
    main = uploads / "clip.mp4"
    poster = uploads / "clip_poster.jpg"
    main.write_bytes(b"video bytes")
    poster.write_bytes(b"wrong poster")
    managed = media_upload.ManagedUpload(
        url="uploads/clip.mp4",
        media_kind="video",
        size_bytes=len(b"video bytes"),
        sha256="8cc3cb4fbfce91660b8a52e80e5798cbf0200e9cd86cefd95c125f44f71ab1d3",
        poster_url="uploads/clip_poster.jpg",
        poster_size_bytes=len(PNG_1X1),
        poster_sha256="0" * 64,
    )

    with pytest.raises(StorageError) as error:
        media_upload.discard_managed_upload(
            managed,
            data_dir=owner.root,
            writer_lock=owner.lease,
            storage_guard=owner.guard,
        )

    assert error.value.commit_uncertain is True


def test_closed_or_wrong_writer_lock_rejected(owner, tmp_path):
    other = DirectoryWriterLock(tmp_path / "other").acquire()
    try:
        with pytest.raises(media_upload.AtelierMediaUploadError, match="Writer lease"):
            media_upload.save_managed_upload(
                [PNG_1X1],
                filename="pixel.png",
                content_type="image/png",
                data_dir=owner.root,
                writer_lock=other,
                storage_guard=owner.guard,
            )
    finally:
        other.close()
    owner.closed = True
    with pytest.raises(StorageError):
        upload(owner, [PNG_1X1], upload_id_factory=lambda: "closed")
