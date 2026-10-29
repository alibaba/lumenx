from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from threading import RLock, Thread
from types import SimpleNamespace
import json
import os
import shutil
import subprocess
import time

import pytest

from src.apps.atelier.models import AtelierNode, AtelierProject
from src.apps.atelier.sequence import resolve_sequence_snapshot
from src.apps.atelier.sequence_render import (
    SequenceRenderCancelToken,
    SequenceRenderCancelled,
    publish_sequence_render,
    render_sequence_to_private_file,
)
from src.utils.json_storage import DirectoryWriterLock, StorageError


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


def _make_clip(path: Path, *, color: str = "red", duration: float = 0.9, frequency: int = 440) -> bytes:
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
            f"color=c={color}:s=64x48:d={duration}:r=25",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={frequency}:duration={duration}:sample_rate=44100",
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


def _make_tone(path: Path, *, duration: float = 0.35, frequency: int = 880) -> bytes:
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
            f"sine=frequency={frequency}:duration={duration}:sample_rate=44100",
            "-c:a",
            "pcm_s16le",
            str(path),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    return path.read_bytes()


def _probe(path: Path) -> dict:
    ffprobe = _tool("ffprobe")
    result = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return json.loads(result.stdout)


def _project() -> AtelierProject:
    return AtelierProject(
        id="atelier-1",
        title="Render",
        created_at=1,
        updated_at=1,
        nodes=[
            AtelierNode(id="clip-red", project_id="atelier-1", type="video", media_urls=["uploads/red.mp4"]),
            AtelierNode(id="clip-blue", project_id="atelier-1", type="video", media_urls=["uploads/blue.mp4"]),
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
            ),
        ],
    )


def _snapshot(owner):
    _make_clip(owner.root / "uploads" / "red.mp4", color="red", duration=0.9, frequency=440)
    _make_clip(owner.root / "uploads" / "blue.mp4", color="blue", duration=0.9, frequency=550)
    _make_tone(owner.root / "uploads" / "tone.wav", duration=0.25, frequency=990)
    return resolve_sequence_snapshot(
        _project(),
        [
            {"id": "red", "parentId": "clip-red", "trimStart": 0.0, "trimEnd": 0.6, "gain": 0.75},
            {
                "id": "blue",
                "parentId": "clip-blue",
                "trimStart": 0.0,
                "trimEnd": 0.6,
                "gain": 0.5,
                "transition": {"kind": "fade", "duration": 0.2},
            },
        ],
        audio_tracks=[
            {
                "id": "music",
                "node_id": "audio-1",
                "start": 0.15,
                "trim_start": 0.0,
                "trim_end": 0.2,
                "gain": 0.4,
                "loop": True,
            }
        ],
        media_scope=owner.scope,
    )


def test_render_uses_frozen_snapshot_bytes_and_cleans_private_file(owner):
    snapshot = _snapshot(owner)
    _make_clip(owner.root / "uploads" / "red.mp4", color="green", duration=0.9, frequency=330)

    with render_sequence_to_private_file(snapshot, media_scope=owner.scope) as result:
        assert result.path.exists()
        assert result.size_bytes == result.path.stat().st_size
        assert len(result.sha256) == 64
        assert len(result.snapshot_sha256) == 64
        info = _probe(result.path)
        streams = info["streams"]
        video = next(item for item in streams if item["codec_type"] == "video")
        audio = next(item for item in streams if item["codec_type"] == "audio")
        assert video["width"] == 64
        assert video["height"] == 48
        assert audio["codec_type"] == "audio"
        assert float(info["format"]["duration"]) == pytest.approx(snapshot["duration"], abs=0.25)
        rendered_path = result.path

    assert not rendered_path.exists()


def test_publication_uses_core_no_overwrite_and_prepared_proof(owner):
    snapshot = _snapshot(owner)
    prepared = []
    with render_sequence_to_private_file(snapshot, media_scope=owner.scope) as result:
        proof = publish_sequence_render(
            result,
            output_ref="video/atelier_seq_job1.mp4",
            media_scope=owner.scope,
            on_prepared=prepared.append,
        )

    assert proof.relative_path == "video/atelier_seq_job1.mp4"
    assert proof.filename == "atelier_seq_job1.mp4"
    assert proof == prepared[0]
    assert (owner.root / proof.relative_path).read_bytes()
    with pytest.raises(Exception, match="already exists"):
        with render_sequence_to_private_file(snapshot, media_scope=owner.scope) as second:
            publish_sequence_render(second, output_ref="video/atelier_seq_job1.mp4", media_scope=owner.scope)


def test_publish_prepared_failure_does_not_publish(owner):
    snapshot = _snapshot(owner)

    def fail_prepared(_proof):
        raise RuntimeError("cannot persist proof")

    with render_sequence_to_private_file(snapshot, media_scope=owner.scope) as result:
        with pytest.raises(RuntimeError, match="cannot persist proof"):
            publish_sequence_render(
                result,
                output_ref="video/atelier_seq_uncommitted.mp4",
                media_scope=owner.scope,
                on_prepared=fail_prepared,
            )

    assert not (owner.root / "video" / "atelier_seq_uncommitted.mp4").exists()


def test_closed_scope_fails_before_render_or_publish(owner):
    snapshot = _snapshot(owner)
    owner.closed = True
    with pytest.raises(Exception, match="Runtime is closed"):
        with render_sequence_to_private_file(snapshot, media_scope=owner.scope):
            pass


def test_cancel_token_terminates_owned_ffmpeg_process_and_cleans_temp(owner):
    snapshot = _snapshot(owner)
    token = SequenceRenderCancelToken()
    seen_path: list[Path] = []
    error: list[BaseException] = []

    def run_render():
        try:
            with render_sequence_to_private_file(snapshot, media_scope=owner.scope, cancel_token=token) as result:
                seen_path.append(result.path)
        except BaseException as exc:  # noqa: BLE001 - asserted below
            error.append(exc)

    thread = Thread(target=run_render)
    thread.start()
    deadline = time.time() + 5
    while time.time() < deadline and token.current_pid is None and not error:
        time.sleep(0.01)
    token.cancel()
    thread.join(timeout=10)

    assert not thread.is_alive()
    assert error and isinstance(error[0], SequenceRenderCancelled)
    for path in seen_path:
        assert not path.exists()



def test_probe_rendered_rejects_invalid_average_frame_rate_even_when_real_rate_matches(owner, tmp_path):
    from src.apps.atelier import sequence_render as render_module

    output = tmp_path / "rendered.mp4"
    output.write_bytes(b"not a real mp4 but probe is mocked")
    snapshot = _snapshot(owner)

    class FakeToken:
        def run(self, cmd, *, timeout):
            assert str(cmd[0]).endswith("ffprobe")
            return subprocess.CompletedProcess(
                cmd,
                0,
                json.dumps(
                    {
                        "streams": [
                            {
                                "codec_type": "video",
                                "codec_name": "h264",
                                "width": snapshot["width"],
                                "height": snapshot["height"],
                                "avg_frame_rate": "0/0",
                                "r_frame_rate": "30/1",
                            },
                            {"codec_type": "audio", "codec_name": "aac"},
                        ],
                        "format": {"duration": str(snapshot["duration"])},
                    }
                ).encode(),
                b"",
            )

    with pytest.raises(render_module.SequenceRenderError, match="frame rate"):
        render_module._probe_rendered(output, ffprobe=_tool("ffprobe"), token=FakeToken(), expected=snapshot)


@pytest.mark.parametrize("avg", [None, "0/0", "0/1", "-30/1"] )
def test_rendered_frame_rate_requires_valid_average(avg):
    from src.apps.atelier import sequence_render as render_module

    video = {"r_frame_rate": "30/1"}
    if avg is not None:
        video["avg_frame_rate"] = avg
    assert render_module._frame_rate(video) is None


def test_rendered_frame_rate_uses_valid_average_even_when_real_rate_differs():
    from src.apps.atelier import sequence_render as render_module

    assert render_module._frame_rate({"avg_frame_rate": "24/1", "r_frame_rate": "30/1"}) == pytest.approx(24)


def test_probe_rejects_rendered_output_with_wrong_timeline_metadata(owner, monkeypatch, tmp_path):
    from src.apps.atelier import sequence_render as render_module

    output = tmp_path / "bad.mp4"
    output.write_bytes(b"not a real mp4 but probe is mocked")
    snapshot = _snapshot(owner)
    calls = []

    class FakeToken:
        def run(self, cmd, *, timeout):
            calls.append(cmd)
            assert str(cmd[0]).endswith("ffprobe")
            return subprocess.CompletedProcess(
                cmd,
                0,
                json.dumps(
                    {
                        "streams": [
                            {
                                "codec_type": "video",
                                "codec_name": "h264",
                                "width": snapshot["width"] + 2,
                                "height": snapshot["height"],
                                "avg_frame_rate": "30/1",
                            },
                            {"codec_type": "audio", "codec_name": "aac"},
                        ],
                        "format": {"duration": str(snapshot["duration"])},
                    }
                ).encode(),
                b"",
            )

    monkeypatch.setattr(render_module.subprocess, "run", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("ffprobe must use token.run")))
    with pytest.raises(render_module.SequenceRenderError, match="dimensions"):
        render_module._probe_rendered(output, ffprobe=_tool("ffprobe"), token=FakeToken(), expected=snapshot)
    assert calls


def test_private_render_cleanup_failure_is_reported(owner, monkeypatch):
    from src.apps.atelier import sequence_render as render_module

    snapshot = _snapshot(owner)
    real_rmtree = render_module.shutil.rmtree
    failures = {"raised": False}

    def fail_once(path, *args, **kwargs):
        if not failures["raised"] and "lumenx-atelier-render-" in str(path):
            failures["raised"] = True
            raise OSError("cleanup denied")
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(render_module.shutil, "rmtree", fail_once)
    with pytest.raises(render_module.SequenceRenderError, match="cleanup denied"):
        with render_sequence_to_private_file(snapshot, media_scope=owner.scope):
            pass
    assert failures["raised"] is True


def test_cancel_token_timeout_kills_owned_process(monkeypatch):
    from src.apps.atelier import sequence_render as render_module

    class SlowProcess:
        pid = 123456
        returncode = None
        killed = False

        def poll(self):
            return self.returncode

        def communicate(self, timeout=None):
            if timeout is not None:
                raise subprocess.TimeoutExpired(["fake"], timeout)
            self.returncode = -9
            return b"", b""

        def wait(self, timeout=None):
            self.returncode = -15
            return self.returncode

        def terminate(self):
            self.returncode = -15

        def kill(self):
            self.killed = True
            self.returncode = -9

    slow = SlowProcess()
    monkeypatch.setattr(render_module.subprocess, "Popen", lambda *_args, **_kwargs: slow)
    monkeypatch.setattr(render_module.os, "killpg", lambda *_args, **_kwargs: slow.kill())
    token = SequenceRenderCancelToken()
    with pytest.raises(Exception, match="timed out"):
        token.run(["ffmpeg", "-version"], timeout=0.05)
    assert token.current_pid is None
    assert slow.killed is True


def test_publication_commit_uncertain_propagates(owner, monkeypatch):
    from src.apps.atelier import sequence_render as render_module

    snapshot = _snapshot(owner)

    def uncertain_store(*_args, **_kwargs):
        raise StorageError("publish uncertain", commit_uncertain=True)

    monkeypatch.setattr(render_module, "store_media_stream", uncertain_store)
    with render_sequence_to_private_file(snapshot, media_scope=owner.scope) as result:
        with pytest.raises(StorageError) as exc:
            publish_sequence_render(result, output_ref="video/atelier_seq_uncertain.mp4", media_scope=owner.scope)
    assert exc.value.commit_uncertain is True


def test_closed_publication_fails_without_success(owner):
    snapshot = _snapshot(owner)
    with render_sequence_to_private_file(snapshot, media_scope=owner.scope) as result:
        owner.closed = True
        with pytest.raises(StorageError, match="Runtime is closed"):
            publish_sequence_render(result, output_ref="video/atelier_seq_closed.mp4", media_scope=owner.scope)
    assert not (owner.root / "video" / "atelier_seq_closed.mp4").exists()


def test_publish_rejects_forged_render_result_even_with_matching_hash_and_size(owner, tmp_path):
    from src.apps.atelier.sequence_render import SequenceRenderResult, SequenceRenderError

    payload = b"garbage with matching self hash"
    forged = tmp_path / "forged.bin"
    forged.write_bytes(payload)
    import hashlib

    result = SequenceRenderResult(
        path=forged,
        sha256=hashlib.sha256(payload).hexdigest(),
        size_bytes=len(payload),
        duration=1.0,
        width=64,
        height=48,
        fps=30,
        snapshot_sha256="a" * 64,
    )

    with pytest.raises(SequenceRenderError, match="active render context"):
        publish_sequence_render(result, output_ref="video/forged.mp4", media_scope=owner.scope)
    assert not (owner.root / "video" / "forged.mp4").exists()


def test_cleanup_failure_preserves_commit_uncertain_publication_error(owner, monkeypatch):
    from src.apps.atelier import sequence_render as render_module

    snapshot = _snapshot(owner)

    def uncertain_store(*_args, **_kwargs):
        raise StorageError("publish uncertain", commit_uncertain=True)

    real_rmtree = render_module.shutil.rmtree

    def fail_cleanup(path, *args, **kwargs):
        if "lumenx-atelier-render-" in str(path):
            raise OSError("cleanup denied after uncertain publish")
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(render_module, "store_media_stream", uncertain_store)
    monkeypatch.setattr(render_module.shutil, "rmtree", fail_cleanup)
    with pytest.raises(StorageError) as exc:
        with render_sequence_to_private_file(snapshot, media_scope=owner.scope) as result:
            publish_sequence_render(result, output_ref="video/atelier_seq_uncertain_cleanup.mp4", media_scope=owner.scope)
    assert exc.value.commit_uncertain is True
    assert any("cleanup denied after uncertain publish" in note for note in getattr(exc.value, "__notes__", []))


def test_validate_snapshot_probe_uses_owned_runner_and_can_cancel(owner):
    from src.apps.atelier.sequence import validate_sequence_snapshot

    snapshot = _snapshot(owner)

    def cancelled_runner(_cmd, *, timeout):
        raise SequenceRenderCancelled("cancelled validation probe")

    with pytest.raises(SequenceRenderCancelled):
        validate_sequence_snapshot(snapshot, media_scope=owner.scope, process_runner=cancelled_runner)


def test_runtime_closed_after_validation_cannot_materialize_success(owner, monkeypatch):
    from src.apps.atelier import sequence_render as render_module

    snapshot = _snapshot(owner)
    real_validate = render_module.validate_sequence_snapshot

    def close_after_validate(*args, **kwargs):
        checked = real_validate(*args, **kwargs)
        owner.closed = True
        return checked

    monkeypatch.setattr(render_module, "validate_sequence_snapshot", close_after_validate)
    with pytest.raises(Exception, match="Runtime is closed"):
        with render_sequence_to_private_file(snapshot, media_scope=owner.scope):
            pass
