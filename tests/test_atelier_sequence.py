from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from threading import RLock
from types import SimpleNamespace
import json
import os
import shutil
import subprocess

import pytest

from src.apps.atelier.models import AtelierNode, AtelierProject
from src.apps.atelier.sequence import (
    SequenceValidationError,
    _frame_rate_from_stream,
    preflight_sequence,
    resolve_sequence_snapshot,
    validate_sequence_snapshot,
)
from src.utils.json_storage import DirectoryWriterLock, StorageError
from src.utils.media_versions import MediaVersion, read_verified_media


@pytest.fixture
def owner(tmp_path):
    root = tmp_path / "runtime"
    root.mkdir()
    state = SimpleNamespace(
        root=root,
        lease=DirectoryWriterLock(root).acquire(),
        lock=RLock(),
        closed=False,
    )

    @contextmanager
    def guard():
        with state.lock:
            if state.closed:
                raise StorageError("Runtime is closed")
            state.lease.assert_owned()
            yield

    state.guard = guard
    state.scope = SimpleNamespace(data_dir=root, writer_lock=state.lease, storage_guard=guard)
    try:
        yield state
    finally:
        state.closed = True
        state.lease.close()


def _tool(name: str) -> str:
    env_name = "LUMENX_TEST_FFMPEG" if name == "ffmpeg" else "LUMENX_TEST_FFPROBE"
    path = os.environ.get(env_name) or shutil.which(name)
    if not path:
        pytest.skip(f"{name} not available")
    return path


def _make_clip(path: Path, *, color: str = "red", duration: float = 0.6) -> bytes:
    ffmpeg = _tool("ffmpeg")
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            (
                f"color=c={color}:s=64x48:d={duration}:r=25[out0];"
                f"sine=frequency=440:duration={duration}:sample_rate=44100[out1]"
            ),
            "-shortest",
            "-pix_fmt",
            "yuv420p",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            str(path),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    return path.read_bytes()



def _make_tone(path: Path, *, duration: float = 1.0) -> bytes:
    ffmpeg = _tool("ffmpeg")
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=660:duration={duration}:sample_rate=44100",
            "-c:a",
            "pcm_s16le",
            str(path),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    return path.read_bytes()


def _project_with_video_and_audio() -> AtelierProject:
    project = _project_with_video()
    project.nodes.append(
        AtelierNode(
            id="audio-1",
            project_id="atelier-1",
            type="audio",
            status="completed",
            data={
                "selected_candidate_id": "tone",
                "candidates": [
                    {
                        "id": "tone",
                        "status": "completed",
                        "media_kind": "audio",
                        "media_url": "uploads/tone.wav",
                    }
                ],
            },
        )
    )
    return project

def _project_with_video(ref: str = "uploads/clip.mp4") -> AtelierProject:
    return AtelierProject(
        id="atelier-1",
        title="Sequence",
        created_at=1,
        updated_at=1,
        nodes=[
            AtelierNode(
                id="video-1",
                project_id="atelier-1",
                type="video",
                status="completed",
                media_urls=[ref],
            ),
            AtelierNode(
                id="draft-1",
                project_id="atelier-1",
                type="draft",
                status="completed",
                data={
                    "candidates": [
                        {
                            "id": "take-1",
                            "status": "completed",
                            "media_kind": "video",
                            "media_url": ref,
                            "video_url": ref,
                        }
                    ]
                },
            ),
        ],
    )


def _codes(report):
    return [item["code"] for item in report["errors"]]



@pytest.mark.parametrize(
    ("avg", "real", "expected"),
    [
        ("24/1", "30/1", 24.0),
        ("0/0", "43/3", 43 / 3),
        ("0/1", "30/1", 30.0),
        ("-24/1", "25/1", 25.0),
        ("nan", "60/1", 60.0),
        (None, "15/1", 15.0),
    ],
)
def test_frame_rate_parser_prefers_valid_average_then_valid_real_rate(avg, real, expected):
    stream = {"r_frame_rate": real}
    if avg is not None:
        stream["avg_frame_rate"] = avg
    assert _frame_rate_from_stream(stream) == pytest.approx(expected)


def test_frame_rate_parser_returns_none_when_average_and_real_rates_are_invalid():
    assert _frame_rate_from_stream({"avg_frame_rate": "0/0", "r_frame_rate": "0/0"}) is None
    assert _frame_rate_from_stream({"avg_frame_rate": "unknown", "r_frame_rate": "-1/1"}) is None


def test_resolve_freezes_real_ffprobe_clip_and_detaches_from_mutable_source(owner):
    original = _make_clip(owner.root / "uploads" / "clip.mp4", color="red")
    project = _project_with_video()

    snapshot = resolve_sequence_snapshot(
        project,
        [{"id": "clip-a", "parentId": "video-1", "trimStart": 0, "trimEnd": 0.4}],
        media_scope=owner.scope,
    )

    assert snapshot["schema_version"] == "atelier.timeline.v1"
    assert snapshot["fps"] == 30
    assert snapshot["width"] == 64
    assert snapshot["height"] == 48
    assert snapshot["duration"] == pytest.approx(0.4, abs=1 / 30)
    clip = snapshot["clips"][0]
    assert clip["clip_id"] == "clip-a"
    assert clip["source_url"] == "uploads/clip.mp4"
    frozen = MediaVersion(**clip["media_version"])
    assert frozen.role == "reference_video"
    assert frozen.ordinal == 0
    assert (
        read_verified_media(frozen, output_root=owner.root, max_bytes=10 * 1024 * 1024)
        == original
    )

    _make_clip(owner.root / "uploads" / "clip.mp4", color="blue")
    checked = validate_sequence_snapshot(snapshot, media_scope=owner.scope)
    checked_frozen = MediaVersion(**checked["clips"][0]["media_version"])
    assert (
        read_verified_media(checked_frozen, output_root=owner.root, max_bytes=10 * 1024 * 1024)
        == original
    )



def test_resolve_video_prefers_valid_average_frame_rate_but_falls_back_to_real_rate(owner, monkeypatch):
    _make_clip(owner.root / "uploads" / "clip.mp4", color="red")
    project = _project_with_video()

    from src.apps.atelier import sequence as sequence_module

    def fake_probe(cmd, *, check, capture_output, text, timeout):
        assert cmd and str(cmd[0]).endswith("ffprobe")
        return subprocess.CompletedProcess(
            cmd,
            0,
            json.dumps(
                {
                    "streams": [
                        {
                            "index": 0,
                            "codec_type": "video",
                            "width": 64,
                            "height": 48,
                            "duration": "0.6",
                            "avg_frame_rate": "0/0",
                            "r_frame_rate": "43/3",
                            "sample_aspect_ratio": "1:1",
                        },
                        {"index": 1, "codec_type": "audio", "duration": "0.6"},
                    ],
                    "format": {"duration": "0.6"},
                }
            ),
            "",
        )

    monkeypatch.setattr(sequence_module.subprocess, "run", fake_probe)

    snapshot = resolve_sequence_snapshot(
        project,
        [{"id": "clip-a", "parentId": "video-1", "trimStart": 0, "trimEnd": 0.4}],
        media_scope=owner.scope,
    )

    assert snapshot["clips"][0]["fps"] == pytest.approx(43 / 3)


def test_preflight_reports_all_invalid_entries_with_locatable_ids(owner):
    project = _project_with_video()
    report = preflight_sequence(
        project,
        [
            {"id": "missing-parent", "parentId": "missing"},
            {"id": "bad-trim", "parentId": "video-1", "trimStart": 1.0, "trimEnd": 0.5},
        ],
        media_scope=owner.scope,
    )

    assert _codes(report) == ["missing_node", "invalid_trim"]
    assert [item["clip_id"] for item in report["errors"]] == ["missing-parent", "bad-trim"]


def test_preflight_rejects_remote_symlink_playlist_and_type_coercion(owner):
    _make_clip(owner.root / "uploads" / "clip.mp4", color="red")
    (owner.root / "uploads" / "playlist.m3u8").write_text("#EXTM3U\n", encoding="utf-8")
    os.symlink("clip.mp4", owner.root / "uploads" / "link.mp4")
    project = AtelierProject(
        id="atelier-1",
        title="Sequence",
        created_at=1,
        updated_at=1,
        nodes=[
            AtelierNode(
                id="remote",
                project_id="atelier-1",
                type="video",
                media_urls=["https://example.invalid/clip.mp4"],
            ),
            AtelierNode(
                id="linked",
                project_id="atelier-1",
                type="video",
                media_urls=["uploads/link.mp4"],
            ),
            AtelierNode(
                id="playlist",
                project_id="atelier-1",
                type="video",
                media_urls=["uploads/playlist.m3u8"],
            ),
            AtelierNode(
                id="ok",
                project_id="atelier-1",
                type="video",
                media_urls=["uploads/clip.mp4"],
            ),
        ],
    )

    report = preflight_sequence(
        project,
        [
            {"id": "remote", "parentId": "remote"},
            {"id": "linked", "parentId": "linked"},
            {"id": "playlist", "parentId": "playlist"},
            {"id": "coerced", "parentId": 123},
            {"id": "bool-gain", "parentId": "ok", "gain": False},
        ],
        media_scope=owner.scope,
    )

    assert _codes(report) == [
        "remote_media",
        "unsafe_media",
        "unsafe_media",
        "invalid_entry",
        "invalid_entry",
    ]
    assert [item["clip_id"] for item in report["errors"]] == [
        "remote",
        "linked",
        "playlist",
        "coerced",
        "bool-gain",
    ]


def test_resolve_raises_report_and_empty_requires_explicit_allowance(owner):
    project = _project_with_video()

    with pytest.raises(SequenceValidationError) as exc:
        resolve_sequence_snapshot(project, [], media_scope=owner.scope)

    assert exc.value.report["errors"] == [
        {"code": "empty_sequence", "message": "Sequence is empty; add at least one clip"}
    ]
    assert (
        resolve_sequence_snapshot(project, [], media_scope=owner.scope, allow_empty=True)["clips"]
        == []
    )


def test_audio_track_freezes_selected_candidate_and_preserves_edit_fields(owner):
    _make_clip(owner.root / "uploads" / "clip.mp4", color="red", duration=1.0)
    original_audio = _make_tone(owner.root / "uploads" / "tone.wav", duration=1.0)
    project = _project_with_video_and_audio()

    snapshot = resolve_sequence_snapshot(
        project,
        [{"id": "clip", "parentId": "video-1", "trimEnd": 0.6}],
        audio_tracks=[
            {
                "id": "music",
                "node_id": "audio-1",
                "start": 0.25,
                "trim_start": 0.1,
                "trim_end": 0.6,
                "gain": 0.5,
                "loop": True,
            }
        ],
        media_scope=owner.scope,
    )

    track = snapshot["audio_tracks"][0]
    assert track["source_url"] == "uploads/tone.wav"
    assert track["start"] == 0.25
    assert track["trim_start"] == 0.1
    assert track["trim_end"] == pytest.approx(0.6, abs=0.02)
    assert track["gain"] == 0.5
    assert track["loop"] is True
    frozen = MediaVersion(**track["media_version"])
    assert frozen.role == "reference_audio"
    assert frozen.ordinal == 0
    assert (
        read_verified_media(frozen, output_root=owner.root, max_bytes=10 * 1024 * 1024)
        == original_audio
    )


def test_transition_rounding_duration_limits_and_candidate_selection(owner):
    _make_clip(owner.root / "uploads" / "clip.mp4", color="red", duration=1.0)
    project = _project_with_video()
    project.nodes[1].data["candidates"].append(
        {
            "id": "alt",
            "status": "completed",
            "media_kind": "video",
            "media_url": "uploads/clip.mp4",
        }
    )

    report = preflight_sequence(
        project,
        [
            {"id": "a", "parentId": "draft-1", "candidateId": "take-1", "trimEnd": 0.35},
            {
                "id": "b",
                "parentId": "draft-1",
                "candidateId": "alt",
                "trimEnd": 0.35,
                "transition": {"kind": "fade", "duration": 0.05},
            },
        ],
        media_scope=owner.scope,
    )

    assert report["errors"] == []
    assert report["duration"] == pytest.approx(0.6, abs=1 / 30)
    assert {warning["code"] for warning in report["warnings"]} >= {"frame_quantized"}

    invalid_transition = preflight_sequence(
        project,
        [{"id": "first", "parentId": "video-1", "transition": {"kind": "fade", "duration": 0.1}}],
        media_scope=owner.scope,
    )
    assert invalid_transition["errors"][0]["code"] == "invalid_transition"
    missing_candidate = preflight_sequence(
        project,
        [{"id": "missing", "parentId": "draft-1", "candidateId": "does-not-exist"}],
        media_scope=owner.scope,
    )
    assert missing_candidate["errors"][0]["code"] == "missing_candidate"
    assert missing_candidate["errors"][0]["clip_id"] == "missing"

    too_many = preflight_sequence(
        project,
        [{"parentId": "video-1"} for _ in range(65)],
        media_scope=owner.scope,
    )
    assert too_many["errors"] == [
        {"code": "timeline_limit", "message": "At most 64 clips and 16 audio tracks are supported"}
    ]


def test_unknown_fields_and_bad_snapshot_scope_fail_closed(owner):
    _make_clip(owner.root / "uploads" / "clip.mp4", color="red")
    project = _project_with_video()
    report = preflight_sequence(
        project,
        [{"id": "unknown", "parentId": "video-1", "mystery": "silently drop me"}],
        media_scope=owner.scope,
    )
    assert report["errors"][0]["code"] == "invalid_entry"
    assert report["errors"][0]["clip_id"] == "unknown"

    snapshot = resolve_sequence_snapshot(
        project,
        [{"id": "clip", "parentId": "video-1"}],
        media_scope=owner.scope,
    )
    tampered = {**snapshot, "fps": 24}
    with pytest.raises(SequenceValidationError) as invalid:
        validate_sequence_snapshot(tampered, media_scope=owner.scope)
    assert invalid.value.report["errors"][0]["code"] == "invalid_snapshot"
    bad_version = {
        **snapshot,
        "clips": [
            {
                **snapshot["clips"][0],
                "media_version": {
                    **snapshot["clips"][0]["media_version"],
                    "sha256": "0" * 64,
                },
            }
        ],
    }
    with pytest.raises(SequenceValidationError) as bad_media:
        validate_sequence_snapshot(bad_version, media_scope=owner.scope)
    assert bad_media.value.report["errors"][0]["code"] == "invalid_snapshot"

    other_root = owner.root.parent / "other-runtime"
    other_root.mkdir()
    other_lock = DirectoryWriterLock(other_root).acquire()

    @contextmanager
    def other_guard():
        other_lock.assert_owned()
        yield

    other = SimpleNamespace(
        data_dir=other_root,
        writer_lock=other_lock,
        storage_guard=other_guard,
    )
    try:
        with pytest.raises(SequenceValidationError) as wrong_scope:
            validate_sequence_snapshot(snapshot, media_scope=other)
        assert wrong_scope.value.report["errors"][0]["code"] == "invalid_snapshot"
    finally:
        other.writer_lock.close()

    owner.closed = True
    with pytest.raises(SequenceValidationError) as closed:
        validate_sequence_snapshot(snapshot, media_scope=owner.scope)
    assert closed.value.report["errors"][0]["code"] == "storage_scope"


def test_repeated_source_is_frozen_once_and_storage_failures_are_not_entry_errors(owner, monkeypatch):
    _make_clip(owner.root / "uploads" / "clip.mp4", color="red")
    project = _project_with_video()
    from src.apps.atelier import sequence as sequence_module

    real_freeze = sequence_module.freeze_media
    calls = []

    def counted_freeze(*args, **kwargs):
        calls.append((args, kwargs))
        return real_freeze(*args, **kwargs)

    monkeypatch.setattr(sequence_module, "freeze_media", counted_freeze)
    resolve_sequence_snapshot(
        project,
        [
            {"id": "a", "parentId": "video-1", "trimEnd": 0.2},
            {"id": "b", "parentId": "video-1", "trimEnd": 0.2},
        ],
        media_scope=owner.scope,
    )
    assert len(calls) == 1

    def storage_fails(*_args, **_kwargs):
        raise StorageError("runtime closed")

    monkeypatch.setattr(sequence_module, "freeze_media", storage_fails)
    with pytest.raises(SequenceValidationError) as exc:
        resolve_sequence_snapshot(
            project,
            [{"id": "stored", "parentId": "video-1"}],
            media_scope=owner.scope,
        )
    assert exc.value.report["errors"][0]["code"] == "storage_scope"
    assert exc.value.report["errors"][0]["clip_id"] == "stored"


def test_freeze_and_probe_run_under_storage_guard(owner, monkeypatch):
    _make_clip(owner.root / "uploads" / "clip.mp4", color="red")
    project = _project_with_video()
    from src.apps.atelier import sequence as sequence_module

    owner.guard_depth = 0
    real_guard = owner.guard

    @contextmanager
    def tracked_guard():
        with real_guard():
            owner.guard_depth += 1
            try:
                yield
            finally:
                owner.guard_depth -= 1

    owner.scope.storage_guard = tracked_guard
    real_freeze = sequence_module.freeze_media
    real_run = sequence_module.subprocess.run
    observed = {"freeze": False, "probe": False}

    def guarded_freeze(*args, **kwargs):
        assert owner.guard_depth > 0, "freeze_media must run under the media scope guard"
        observed["freeze"] = True
        return real_freeze(*args, **kwargs)

    def guarded_run(args, **kwargs):
        if args and str(args[0]).endswith("ffprobe"):
            assert owner.guard_depth > 0, "ffprobe must run under the media scope guard"
            observed["probe"] = True
        return real_run(args, **kwargs)

    monkeypatch.setattr(sequence_module, "freeze_media", guarded_freeze)
    monkeypatch.setattr(sequence_module.subprocess, "run", guarded_run)

    resolve_sequence_snapshot(
        project,
        [{"id": "clip", "parentId": "video-1"}],
        media_scope=owner.scope,
    )
    assert observed == {"freeze": True, "probe": True}


def test_validate_snapshot_rejects_metadata_pollution_matrix(owner):
    _make_clip(owner.root / "uploads" / "clip.mp4", color="red", duration=1.0)
    _make_tone(owner.root / "uploads" / "tone.wav", duration=1.0)
    project = _project_with_video_and_audio()
    snapshot = resolve_sequence_snapshot(
        project,
        [{"id": "clip", "parentId": "video-1", "trimStart": 0.1, "trimEnd": 0.7}],
        audio_tracks=[
            {
                "id": "music",
                "node_id": "audio-1",
                "start": 0.1,
                "trim_start": 0.1,
                "trim_end": 0.5,
                "gain": 0.5,
                "loop": True,
            }
        ],
        media_scope=owner.scope,
    )
    assert validate_sequence_snapshot(snapshot, media_scope=owner.scope) == snapshot

    def corrupted(path, value):
        import copy

        clone = copy.deepcopy(snapshot)
        cursor = clone
        for key in path[:-1]:
            cursor = cursor[key]
        cursor[path[-1]] = value
        return clone

    cases = [
        (("width",), 999),
        (("height",), 999),
        (("duration",), snapshot["duration"] + 0.2),
        (("unexpected",), True),
        (("clips", 0, "trimStart"), 0.2),
        (("clips", 0, "trimEnd"), 0.9),
        (("clips", 0, "duration"), snapshot["clips"][0]["duration"] + 0.1),
        (("clips", 0, "gain"), 3),
        (("clips", 0, "transition"), {"kind": "cut", "duration": 0.1}),
        (("clips", 0, "width"), 999),
        (("clips", 0, "height"), 999),
        (("clips", 0, "sar"), 2),
        (("clips", 0, "fps"), 1),
        (("clips", 0, "audio_index"), "0"),
        (("clips", 0, "source_duration"), 999),
        (("clips", 0, "extra"), "bad"),
        (("audio_tracks", 0, "start"), -1),
        (("audio_tracks", 0, "trim_start"), 0.8),
        (("audio_tracks", 0, "trim_end"), 2.0),
        (("audio_tracks", 0, "gain"), -0.1),
        (("audio_tracks", 0, "loop"), "yes"),
        (("audio_tracks", 0, "source_duration"), 999),
        (("audio_tracks", 0, "extra"), "bad"),
    ]
    for path, value in cases:
        with pytest.raises(SequenceValidationError):
            validate_sequence_snapshot(corrupted(path, value), media_scope=owner.scope)

    empty = resolve_sequence_snapshot(project, [], media_scope=owner.scope, allow_empty=True)
    assert validate_sequence_snapshot(empty, media_scope=owner.scope) == empty
    with pytest.raises(SequenceValidationError):
        validate_sequence_snapshot({**empty, "width": 1}, media_scope=owner.scope)


@pytest.mark.parametrize("kind", ["clip", "audio"])
def test_frozen_snapshot_rejects_duplicate_instance_ids(owner, kind):
    _make_clip(owner.root / "uploads" / "clip.mp4", duration=1.0)
    _make_tone(owner.root / "uploads" / "tone.wav", duration=1.0)
    snapshot = resolve_sequence_snapshot(
        _project_with_video_and_audio(),
        [{"id": "first", "parentId": "video-1"}, {"id": "second", "parentId": "video-1"}],
        audio_tracks=[{"id": "first", "node_id": "audio-1"}, {"id": "second", "node_id": "audio-1"}],
        media_scope=owner.scope,
    )
    # Equal ids across clip/audio namespaces are valid; duplicates within either
    # namespace are not, including in a historical snapshot imported without jobs.
    assert validate_sequence_snapshot(snapshot, media_scope=owner.scope) == snapshot
    items, key = (snapshot["clips"], "clip_id") if kind == "clip" else (snapshot["audio_tracks"], "id")
    items[1][key] = items[0][key]
    with pytest.raises(SequenceValidationError) as invalid:
        validate_sequence_snapshot(snapshot, media_scope=owner.scope)
    assert any(error["code"] == "duplicate_id" for error in invalid.value.report["errors"])


def test_generated_clip_id_cannot_collide_with_explicit_id(owner):
    _make_clip(owner.root / "uploads" / "clip.mp4", duration=1.0)
    with pytest.raises(SequenceValidationError) as invalid:
        resolve_sequence_snapshot(
            _project_with_video_and_audio(),
            [{"id": "clip-2", "parentId": "video-1"}, {"parentId": "video-1"}],
            media_scope=owner.scope,
        )
    assert any(error["code"] == "duplicate_id" for error in invalid.value.report["errors"])
