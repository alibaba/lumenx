"""Atelier sequence rendering and Core-managed publication primitives.

This module consumes already-frozen ``atelier.timeline.v1`` snapshots. It does
not resolve mutable project media, create jobs, or mutate Atelier project JSON.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
import hashlib
import json
import math
from fractions import Fraction
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import uuid
import threading
from typing import Any, Callable, Iterator

from .sequence import (
    FPS,
    MAX_AUDIO_BYTES,
    MAX_VIDEO_BYTES,
    _ffmpeg_tools,
    _input_limits,
    validate_sequence_snapshot,
)
from ...utils.json_storage import StorageError
from ...utils.media_versions import MediaVersion, read_verified_media, store_media_stream

MAX_RENDER_BYTES = 1024 * 1024 * 1024
RENDER_TIMEOUT_SECONDS = 900
PROBE_TIMEOUT_SECONDS = 30


class SequenceRenderError(RuntimeError):
    """A sequence snapshot cannot be rendered or published."""


class SequenceRenderCancelled(SequenceRenderError):
    """The caller cancelled the owned render process."""


@dataclass(frozen=True)
class SequenceRenderResult:
    path: Path
    sha256: str
    size_bytes: int
    duration: float
    width: int
    height: int
    fps: int
    snapshot_sha256: str
    _context_token: str | None = field(default=None, repr=False, compare=False)
    _proof: "_RenderContextProof | None" = field(default=None, repr=False, compare=False)




@dataclass(frozen=True)
class _RenderContextProof:
    context_token: str
    path: Path
    scope_root: Path
    writer_lock: object
    sha256: str
    size_bytes: int
    duration: float
    width: int
    height: int
    fps: int
    snapshot_sha256: str


_ACTIVE_RENDER_CONTEXTS: set[str] = set()


@dataclass(frozen=True)
class SequencePublicationProof:
    schema_version: int
    relative_path: str
    filename: str
    sha256: str
    size_bytes: int
    duration: float
    width: int
    height: int
    fps: int
    snapshot_sha256: str


class SequenceRenderCancelToken:
    """Cancellation handle that can terminate only the process it starts."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._cancelled = False
        self._process: subprocess.Popen[bytes] | None = None

    @property
    def current_pid(self) -> int | None:
        with self._lock:
            return self._process.pid if self._process is not None else None

    def cancel(self) -> None:
        with self._lock:
            self._cancelled = True
            process = self._process
        if process is not None and process.poll() is None:
            _terminate_process(process)

    def assert_not_cancelled(self) -> None:
        with self._lock:
            if self._cancelled:
                raise SequenceRenderCancelled("Sequence render was cancelled")

    def run(self, cmd: list[str], *, timeout: float) -> subprocess.CompletedProcess[bytes]:
        self.assert_not_cancelled()
        try:
            process = subprocess.Popen(  # noqa: S603 - argv is constructed from trusted ffmpeg/tool paths and private files.
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=(os.name == "posix"),
            )
        except OSError as exc:
            raise SequenceRenderError(str(exc)) from exc
        with self._lock:
            self._process = process
            cancelled = self._cancelled
        if cancelled:
            _terminate_process(process)
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            _terminate_process(process)
            stdout, stderr = process.communicate()
            raise SequenceRenderError("FFmpeg timed out") from exc
        finally:
            with self._lock:
                if self._process is process:
                    self._process = None
        with self._lock:
            cancelled = self._cancelled
        if cancelled:
            raise SequenceRenderCancelled("Sequence render was cancelled")
        if process.returncode != 0:
            detail = stderr.decode(errors="replace")[:600] if stderr else "ffmpeg exited with an error"
            raise SequenceRenderError(detail)
        return subprocess.CompletedProcess(cmd, process.returncode, stdout, stderr)


def _terminate_process(process: subprocess.Popen[bytes]) -> None:
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
    except ProcessLookupError:
        return
    except OSError:
        try:
            process.terminate()
        except OSError:
            return
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        except OSError:
            pass


@contextmanager
def render_sequence_to_private_file(
    snapshot: dict[str, Any],
    *,
    media_scope: Any,
    cancel_token: SequenceRenderCancelToken | None = None,
) -> Iterator[SequenceRenderResult]:
    """Render a validated snapshot to a private temporary MP4.

    The returned path is valid only inside the context manager and is removed on
    exit. Callers that need persistence must publish it with
    ``publish_sequence_render`` before leaving the context.
    """

    token = cancel_token or SequenceRenderCancelToken()
    checked = validate_sequence_snapshot(snapshot, media_scope=media_scope, process_runner=token.run)
    token.assert_not_cancelled()
    work_dir = Path(tempfile.mkdtemp(prefix="lumenx-atelier-render-"))
    context_token: str | None = None
    primary: BaseException | None = None
    try:
        result = _render_checked_snapshot(checked, media_scope=media_scope, token=token, work_dir=work_dir)
        context_token = result._context_token
        if context_token is None:
            raise SequenceRenderError("Rendered sequence is missing active render context")
        _ACTIVE_RENDER_CONTEXTS.add(context_token)
        yield result
    except BaseException as exc:
        primary = exc
        raise
    finally:
        try:
            _cleanup_work_dir(work_dir)
        except SequenceRenderError as cleanup_error:
            if primary is not None:
                primary.add_note(str(cleanup_error))
            else:
                raise
        finally:
            if context_token is not None:
                _ACTIVE_RENDER_CONTEXTS.discard(context_token)


def publish_sequence_render(
    result: SequenceRenderResult,
    *,
    output_ref: str,
    media_scope: Any,
    on_prepared: Callable[[SequencePublicationProof], None] | None = None,
    max_bytes: int = MAX_RENDER_BYTES,
) -> SequencePublicationProof:
    _assert_publishable_result(result, media_scope=media_scope)
    if not isinstance(output_ref, str) or not output_ref.startswith("video/") or not output_ref.endswith(".mp4"):
        raise SequenceRenderError("Atelier sequence exports must publish to video/*.mp4")

    prepared: SequencePublicationProof | None = None

    def core_prepared(relative: str, digest: str, size: int) -> None:
        nonlocal prepared
        if digest != result.sha256 or size != result.size_bytes:
            raise SequenceRenderError("Prepared render identity differs from validated output")
        proof = SequencePublicationProof(
            schema_version=1,
            relative_path=relative,
            filename=Path(relative).name,
            sha256=digest,
            size_bytes=size,
            duration=result.duration,
            width=result.width,
            height=result.height,
            fps=result.fps,
            snapshot_sha256=result.snapshot_sha256,
        )
        if on_prepared is not None:
            on_prepared(proof)
        prepared = proof

    relative, digest, size = store_media_stream(
        _file_chunks(result.path),
        output_ref=output_ref,
        output_root=media_scope.data_dir,
        writer_lock=media_scope.writer_lock,
        storage_guard=media_scope.storage_guard,
        max_bytes=max_bytes,
        on_prepared=core_prepared,
        publish_policy="no_overwrite",
    )
    if prepared is None:
        raise SequenceRenderError("Prepared publication proof was not recorded")
    if relative != prepared.relative_path or digest != prepared.sha256 or size != prepared.size_bytes:
        raise StorageError("Published render identity differs from prepared proof", commit_uncertain=True)
    return prepared


def _render_checked_snapshot(
    snapshot: dict[str, Any], *, media_scope: Any, token: SequenceRenderCancelToken, work_dir: Path,
) -> SequenceRenderResult:
    if not snapshot.get("clips"):
        raise SequenceRenderError("Cannot render an empty sequence")
    ffmpeg, ffprobe = _ffmpeg_tools()
    inputs = _materialize_inputs(snapshot, media_scope=media_scope, work_dir=work_dir, token=token)
    segments = _render_segments(snapshot, inputs, ffmpeg=ffmpeg, token=token, work_dir=work_dir)
    joined = _join_segments(snapshot, segments, ffmpeg=ffmpeg, token=token, work_dir=work_dir)
    output = _mix_audio_tracks(snapshot, inputs, joined, ffmpeg=ffmpeg, token=token, work_dir=work_dir)
    info = _probe_rendered(output, ffprobe=ffprobe, token=token, expected=snapshot)
    duration = float(info["duration"])
    width = int(info["width"])
    height = int(info["height"])
    digest, size = _digest_file(output)
    snapshot_sha256 = _snapshot_digest(snapshot)
    context_token = uuid.uuid4().hex
    proof = _RenderContextProof(
        context_token=context_token,
        path=output,
        scope_root=Path(media_scope.data_dir).resolve(),
        writer_lock=media_scope.writer_lock,
        sha256=digest,
        size_bytes=size,
        duration=duration,
        width=width,
        height=height,
        fps=FPS,
        snapshot_sha256=snapshot_sha256,
    )
    return SequenceRenderResult(
        path=output,
        sha256=digest,
        size_bytes=size,
        duration=duration,
        width=width,
        height=height,
        fps=FPS,
        snapshot_sha256=snapshot_sha256,
        _context_token=context_token,
        _proof=proof,
    )


def _materialize_inputs(
    snapshot: dict[str, Any], *, media_scope: Any, work_dir: Path, token: SequenceRenderCancelToken,
) -> dict[tuple[str, int], Path]:
    result: dict[tuple[str, int], Path] = {}
    for kind, items, max_bytes in (
        ("video", snapshot["clips"], MAX_VIDEO_BYTES),
        ("audio", snapshot.get("audio_tracks", []), MAX_AUDIO_BYTES),
    ):
        for index, item in enumerate(items):
            token.assert_not_cancelled()
            version = MediaVersion(**item["media_version"])
            suffix = Path(item["source_url"]).suffix.lower()
            with media_scope.storage_guard():
                media_scope.writer_lock.assert_owned()
                data = read_verified_media(version, output_root=media_scope.data_dir, max_bytes=max_bytes)
                media_scope.writer_lock.assert_owned()
            token.assert_not_cancelled()
            path = work_dir / f"{kind}-{index}{suffix}"
            path.write_bytes(data)
            with media_scope.storage_guard():
                media_scope.writer_lock.assert_owned()
            token.assert_not_cancelled()
            result[(kind, index)] = path
    return result


def _render_segments(
    snapshot: dict[str, Any], inputs: dict[tuple[str, int], Path], *, ffmpeg: str,
    token: SequenceRenderCancelToken, work_dir: Path,
) -> list[Path]:
    outputs: list[Path] = []
    width = int(snapshot["width"])
    height = int(snapshot["height"])
    for index, clip in enumerate(snapshot["clips"]):
        source = inputs[("video", index)]
        output = work_dir / f"segment-{index}.mkv"
        duration = float(clip["duration"])
        start = float(clip["trimStart"])
        end = float(clip["trimEnd"])
        vf = (
            f"[0:{int(clip.get('stream_index', 0))}]"
            f"trim=start={start:.6f}:end={end:.6f},setpts=PTS-STARTPTS,"
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,"
            f"fps={FPS},format=yuv420p[v]"
        )
        if clip.get("audio_index") is None:
            filter_complex = (
                vf
                + f";anullsrc=channel_layout=stereo:sample_rate=48000,atrim=duration={duration:.6f}[a]"
            )
        else:
            audio_index = int(clip["audio_index"])
            gain = float(clip["gain"])
            filter_complex = (
                vf
                + ";"
                + f"[0:{audio_index}]atrim=start={start:.6f}:end={end:.6f},"
                + f"asetpts=PTS-STARTPTS,volume={gain:.6f},aresample=48000,"
                + "aformat=channel_layouts=stereo[a]"
            )
        cmd = [
            ffmpeg,
            "-nostdin",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            *_input_limits(str(source), "video"),
            "-i",
            str(source),
            "-filter_complex",
            filter_complex,
            "-map",
            "[v]",
            "-map",
            "[a]",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            "-c:a",
            "pcm_s16le",
            "-t",
            f"{duration:.6f}",
            str(output),
        ]
        token.run(cmd, timeout=RENDER_TIMEOUT_SECONDS)
        outputs.append(output)
    return outputs


def _join_segments(snapshot: dict[str, Any], segments: list[Path], *, ffmpeg: str, token: SequenceRenderCancelToken, work_dir: Path) -> Path:
    if len(segments) == 1:
        return segments[0]
    output = work_dir / "joined.mkv"
    cmd = [ffmpeg, "-nostdin", "-y", "-hide_banner", "-loglevel", "error"]
    for segment in segments:
        cmd += [*_input_limits(str(segment), "video"), "-i", str(segment)]
    filters: list[str] = []
    current_v = "0:v"
    current_a = "0:a"
    accumulated = float(snapshot["clips"][0]["duration"])
    for index in range(1, len(segments)):
        transition = snapshot["clips"][index]["transition"]
        fade = float(transition["duration"]) if transition["kind"] == "fade" else 0.0
        out_v = f"v{index}"
        out_a = f"a{index}"
        if fade > 0:
            offset = max(0.0, accumulated - fade)
            filters.append(
                f"[{current_v}][{index}:v]xfade=transition=fade:duration={fade:.6f}:offset={offset:.6f}[{out_v}]"
            )
            filters.append(f"[{current_a}][{index}:a]acrossfade=d={fade:.6f}[{out_a}]")
            accumulated += float(snapshot["clips"][index]["duration"]) - fade
        else:
            filters.append(f"[{current_v}][{current_a}][{index}:v][{index}:a]concat=n=2:v=1:a=1[{out_v}][{out_a}]")
            accumulated += float(snapshot["clips"][index]["duration"])
        current_v = out_v
        current_a = out_a
    cmd += [
        "-filter_complex",
        ";".join(filters),
        "-map",
        f"[{current_v}]",
        "-map",
        f"[{current_a}]",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-c:a",
        "pcm_s16le",
        str(output),
    ]
    token.run(cmd, timeout=RENDER_TIMEOUT_SECONDS)
    return output


def _mix_audio_tracks(
    snapshot: dict[str, Any], inputs: dict[tuple[str, int], Path], joined: Path, *, ffmpeg: str,
    token: SequenceRenderCancelToken, work_dir: Path,
) -> Path:
    output = work_dir / "rendered.mp4"
    tracks = snapshot.get("audio_tracks", [])
    if not tracks:
        cmd = [
            ffmpeg,
            "-nostdin",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            *_input_limits(str(joined), "video"),
            "-i",
            str(joined),
            "-map",
            "0:v",
            "-map",
            "0:a?",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            str(output),
        ]
        token.run(cmd, timeout=RENDER_TIMEOUT_SECONDS)
        return output

    cmd = [ffmpeg, "-nostdin", "-y", "-hide_banner", "-loglevel", "error", *_input_limits(str(joined), "video"), "-i", str(joined)]
    for index, track in enumerate(tracks):
        source = inputs[("audio", index)]
        cmd += [*_input_limits(str(source), "audio"), "-i", str(source)]
    filters = ["[0:a]aresample=48000,aformat=channel_layouts=stereo[basea]"]
    mix_inputs = ["[basea]"]
    timeline_duration = float(snapshot["duration"])
    for index, track in enumerate(tracks, start=1):
        label = f"exta{index}"
        trim_start = float(track["trim_start"])
        trim_end = float(track["trim_end"])
        start_ms = int(round(float(track["start"]) * 1000))
        gain = float(track["gain"])
        chain = (
            f"[{index}:{int(track.get('stream_index', 0))}]"
            f"atrim=start={trim_start:.6f}:end={trim_end:.6f},asetpts=PTS-STARTPTS"
        )
        if track["loop"]:
            chain += ",aloop=loop=-1:size=2147483647"
        remaining = max(0.001, timeline_duration - float(track["start"]))
        chain += (
            f",atrim=duration={remaining:.6f},volume={gain:.6f},"
            f"aresample=48000,aformat=channel_layouts=stereo,adelay={start_ms}|{start_ms}[{label}]"
        )
        filters.append(chain)
        mix_inputs.append(f"[{label}]")
    filters.append("".join(mix_inputs) + f"amix=inputs={len(mix_inputs)}:duration=first:dropout_transition=0[aout]")
    cmd += [
        "-filter_complex",
        ";".join(filters),
        "-map",
        "0:v",
        "-map",
        "[aout]",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-shortest",
        "-movflags",
        "+faststart",
        str(output),
    ]
    token.run(cmd, timeout=RENDER_TIMEOUT_SECONDS)
    return output


def _probe_rendered(
    path: Path, *, ffprobe: str, token: SequenceRenderCancelToken, expected: dict[str, Any],
) -> dict[str, float | int]:
    try:
        result = token.run(
            [
                ffprobe,
                "-v",
                "error",
                *_input_limits(str(path), "video"),
                "-i",
                str(path),
                "-show_streams",
                "-show_format",
                "-of",
                "json",
            ],
            timeout=PROBE_TIMEOUT_SECONDS,
        )
        info = json.loads(result.stdout.decode("utf-8"))
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise SequenceRenderError("Cannot probe rendered sequence") from exc
    streams = info.get("streams", [])
    video = next((item for item in streams if item.get("codec_type") == "video"), None)
    audio = next((item for item in streams if item.get("codec_type") == "audio"), None)
    duration = _positive_duration((info.get("format") or {}).get("duration"))
    if video is None or audio is None or duration is None:
        raise SequenceRenderError("Rendered sequence is missing video/audio or duration")
    if video.get("codec_name") != "h264":
        raise SequenceRenderError("Rendered sequence video codec is invalid")
    if audio.get("codec_name") != "aac":
        raise SequenceRenderError("Rendered sequence audio codec is invalid")
    width = video.get("width")
    height = video.get("height")
    if type(width) is not int or type(height) is not int or width <= 0 or height <= 0:
        raise SequenceRenderError("Rendered sequence dimensions are invalid")
    if width != expected.get("width") or height != expected.get("height"):
        raise SequenceRenderError("Rendered sequence dimensions do not match snapshot")
    fps = _frame_rate(video)
    if fps is None or abs(fps - FPS) > 0.01:
        raise SequenceRenderError("Rendered sequence frame rate does not match snapshot")
    expected_duration = float(expected.get("duration", 0.0))
    if abs(duration - expected_duration) > max(0.12, 2 / FPS):
        raise SequenceRenderError("Rendered sequence duration does not match snapshot")
    return {"duration": duration, "width": width, "height": height}



def _assert_publishable_result(result: SequenceRenderResult, *, media_scope: Any) -> None:
    proof = result._proof
    if proof is None or result._context_token is None or result._context_token not in _ACTIVE_RENDER_CONTEXTS:
        raise SequenceRenderError("Rendered sequence is not from an active render context")
    if proof.context_token != result._context_token:
        raise SequenceRenderError("Rendered sequence context proof is invalid")
    if proof.path != result.path or proof.scope_root != Path(media_scope.data_dir).resolve() or proof.writer_lock is not media_scope.writer_lock:
        raise SequenceRenderError("Rendered sequence belongs to another media scope")
    digest, size = _digest_file(result.path)
    if (
        digest != proof.sha256
        or size != proof.size_bytes
        or result.sha256 != proof.sha256
        or result.size_bytes != proof.size_bytes
        or result.duration != proof.duration
        or result.width != proof.width
        or result.height != proof.height
        or result.fps != proof.fps
        or result.snapshot_sha256 != proof.snapshot_sha256
    ):
        raise SequenceRenderError("Rendered sequence proof does not match current file")


def _frame_rate(video: dict[str, Any]) -> float | None:
    raw = video.get("avg_frame_rate")
    try:
        fps = float(Fraction(str(raw)))
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    return fps if math.isfinite(fps) and fps > 0 else None


def _cleanup_work_dir(work_dir: Path) -> None:
    if not work_dir.exists():
        return
    try:
        shutil.rmtree(work_dir)
    except OSError as exc:
        raise SequenceRenderError(f"Private render cleanup failed: {exc}") from exc


def _positive_duration(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) and parsed > 0 else None


def _digest_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while data := handle.read(1024 * 1024):
            size += len(data)
            digest.update(data)
    if size <= 0:
        raise SequenceRenderError("Rendered sequence is empty")
    return digest.hexdigest(), size


def _file_chunks(path: Path):
    with path.open("rb") as source:
        while data := source.read(1024 * 1024):
            yield data


def _snapshot_digest(snapshot: dict[str, Any]) -> str:
    payload = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
