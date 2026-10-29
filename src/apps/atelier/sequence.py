"""Atelier local sequence preflight and immutable timeline snapshots.

This module intentionally stops before rendering, jobs, API, or storage writes.
Callers pass an explicit Atelier media scope so this code never falls back to a
repository ``output`` directory. Resolved snapshots bind every source to Core's
content-addressed ``MediaVersion`` records; redo/render consumers must use those
immutable blobs instead of reopening mutable source URLs.
"""

from __future__ import annotations

from copy import deepcopy
from fractions import Fraction
import json
import math
from pathlib import Path
import os
import subprocess
import tempfile
from typing import Any, Callable, Iterable, Literal

from pydantic import ValidationError

from .models import AtelierAudioTrack, AtelierProject, AtelierSequenceEntry
from ...utils.json_storage import DirectoryWriterLock, StorageError
from ...utils.media_versions import (
    MediaVersion,
    MediaVersionError,
    freeze_media,
    read_verified_media,
)
from ...utils.system_check import get_ffmpeg_path

FPS = 30
SNAPSHOT_SCHEMA_VERSION = "atelier.timeline.v1"
PROBE_TIMEOUT_SECONDS = 20
MAX_CLIPS = 64
MAX_AUDIO_TRACKS = 16
MAX_DURATION_SECONDS = 3600.0
MAX_VIDEO_BYTES = 250 * 1024 * 1024
MAX_AUDIO_BYTES = 100 * 1024 * 1024

_VIDEO_DEMUXERS = {
    ".mp4": "mov",
    ".mov": "mov",
    ".webm": "matroska",
    ".mkv": "matroska",
}
_AUDIO_DEMUXERS = {
    ".mp3": "mp3",
    ".wav": "wav",
    ".m4a": "mov",
    ".ogg": "ogg",
    ".flac": "flac",
    ".aac": "aac",
}
_VIDEO_ENTRY_FIELDS = {
    "parentId",
    "candidateId",
    "id",
    "trimStart",
    "trimEnd",
    "gain",
    "transition",
}
_AUDIO_TRACK_FIELDS = {
    "id",
    "node_id",
    "start",
    "trim_start",
    "trim_end",
    "gain",
    "loop",
}
_SNAPSHOT_FIELDS = {
    "schema_version",
    "fps",
    "width",
    "height",
    "duration",
    "clips",
    "audio_tracks",
}
_SNAPSHOT_CLIP_FIELDS = {
    "clip_id",
    "parentId",
    "candidateId",
    "source_url",
    "trimStart",
    "trimEnd",
    "duration",
    "gain",
    "transition",
    "source_duration",
    "stream_index",
    "media_version",
    "width",
    "height",
    "fps",
    "sar",
    "audio_index",
}
_SNAPSHOT_AUDIO_FIELDS = {
    "id",
    "node_id",
    "source_url",
    "start",
    "trim_start",
    "trim_end",
    "gain",
    "loop",
    "source_duration",
    "stream_index",
    "media_version",
}


class SequenceError(ValueError):
    """A single timeline entry cannot be resolved."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class SequenceValidationError(ValueError):
    """The complete timeline report contains blocking errors."""

    def __init__(self, report: dict[str, Any]):
        self.report = report
        messages = "; ".join(error["message"] for error in report.get("errors", []))
        super().__init__(messages or "Invalid sequence timeline")


def _issue(
    code: str,
    message: str,
    *,
    clip_id: str | None = None,
    track_id: str | None = None,
) -> dict[str, str]:
    result = {"code": code, "message": message}
    if clip_id is not None:
        result["clip_id"] = clip_id
    if track_id is not None:
        result["track_id"] = track_id
    return result


def _empty_report() -> dict[str, Any]:
    return {"errors": [], "warnings": [], "duration": 0.0, "width": 0, "height": 0, "fps": FPS}


def _empty_snapshot() -> dict[str, Any]:
    return {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "fps": FPS,
        "width": 0,
        "height": 0,
        "duration": 0.0,
        "clips": [],
        "audio_tracks": [],
    }


def _scope_root(media_scope: Any) -> Path:
    root = Path(media_scope.data_dir).resolve()
    writer_lock = getattr(media_scope, "writer_lock", None)
    if not isinstance(writer_lock, DirectoryWriterLock) or writer_lock.data_dir != root:
        raise SequenceError("storage_scope", "Sequence media scope belongs to another output root")
    if not callable(getattr(media_scope, "storage_guard", None)):
        raise SequenceError("storage_scope", "Sequence media scope is missing a storage guard")
    try:
        with media_scope.storage_guard():
            writer_lock.assert_owned()
    except StorageError as exc:
        raise SequenceError("storage_scope", str(exc)) from exc
    return root


def _is_number(value: Any) -> bool:
    return type(value) in {int, float} and math.isfinite(float(value))


def _strict_nonempty_str(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise SequenceError("invalid_entry", f"{label} must be a nonempty string")
    return value


def _strict_optional_str(value: Any, label: str) -> str | None:
    if value is None:
        return None
    return _strict_nonempty_str(value, label)


def _strict_optional_number(value: Any, label: str, *, positive: bool = False) -> float | None:
    if value is None:
        return None
    if not _is_number(value):
        raise SequenceError("invalid_entry", f"{label} must be an explicit finite number")
    parsed = float(value)
    if positive and parsed <= 0:
        raise SequenceError("invalid_entry", f"{label} must be positive")
    if not positive and parsed < 0:
        raise SequenceError("invalid_entry", f"{label} must be nonnegative")
    return parsed


def _strict_gain(value: Any) -> float:
    if value is None:
        return 1.0
    if not _is_number(value):
        raise SequenceError("invalid_entry", "gain must be an explicit finite number")
    parsed = float(value)
    if not 0 <= parsed <= 2:
        raise SequenceError("invalid_entry", "gain must be between 0 and 2")
    return parsed


def _raw_id(raw: Any, *, prefix: str, index: int) -> str:
    if isinstance(raw, dict) and isinstance(raw.get("id"), str) and raw["id"]:
        return raw["id"]
    return f"{prefix}-{index + 1}"


def _parse_video_entry(raw: Any) -> AtelierSequenceEntry:
    if not isinstance(raw, dict):
        raise SequenceError("invalid_entry", "Sequence entry must be an object")
    _reject_unknown_fields(raw, _VIDEO_ENTRY_FIELDS, "sequence entry")
    transition = raw.get("transition", {})
    if transition is None:
        transition = {}
    if not isinstance(transition, dict):
        raise SequenceError("invalid_entry", "transition must be an object")
    kind = transition.get("kind", "cut")
    if not isinstance(kind, str) or kind not in {"cut", "fade"}:
        raise SequenceError("invalid_entry", "transition.kind must be cut or fade")
    duration = transition.get("duration", 0.0)
    if not _is_number(duration) or float(duration) < 0:
        raise SequenceError(
            "invalid_entry",
            "transition.duration must be a nonnegative finite number",
        )
    payload = {
        "parentId": _strict_nonempty_str(raw.get("parentId"), "parentId"),
        "candidateId": _strict_optional_str(raw.get("candidateId"), "candidateId"),
        "id": _strict_optional_str(raw.get("id"), "id"),
        "trimStart": _strict_optional_number(raw.get("trimStart"), "trimStart"),
        "trimEnd": _strict_optional_number(raw.get("trimEnd"), "trimEnd", positive=True),
        "gain": _strict_gain(raw.get("gain", 1.0)),
        "transition": {"kind": kind, "duration": float(duration)},
    }
    return AtelierSequenceEntry.model_validate(payload)


def _parse_audio_track(raw: Any) -> AtelierAudioTrack:
    if not isinstance(raw, dict):
        raise SequenceError("invalid_entry", "Audio track must be an object")
    _reject_unknown_fields(raw, _AUDIO_TRACK_FIELDS, "audio track")
    payload = {
        "id": _strict_nonempty_str(raw.get("id"), "id"),
        "node_id": _strict_nonempty_str(raw.get("node_id"), "node_id"),
        "start": _strict_optional_number(raw.get("start", 0.0), "start") or 0.0,
        "trim_start": _strict_optional_number(raw.get("trim_start"), "trim_start"),
        "trim_end": _strict_optional_number(raw.get("trim_end"), "trim_end", positive=True),
        "gain": _strict_gain(raw.get("gain", 1.0)),
        "loop": raw.get("loop", False),
    }
    if type(payload["loop"]) is not bool:
        raise SequenceError("invalid_entry", "loop must be a boolean")
    return AtelierAudioTrack.model_validate(payload)


def _reject_unknown_fields(raw: dict[str, Any], allowed: set[str], label: str) -> None:
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise SequenceError("invalid_entry", f"Unsupported {label} field: {unknown[0]}")


def _entry_dump(value: AtelierSequenceEntry | AtelierAudioTrack | dict[str, Any]) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")  # type: ignore[no-any-return]
    if isinstance(value, dict):
        return dict(value)
    raise SequenceError("invalid_entry", "Sequence entry must be an object")


def _ffmpeg_tools() -> tuple[str, str]:
    ffmpeg = get_ffmpeg_path()
    if not ffmpeg:
        raise SequenceError("ffmpeg_missing", "FFmpeg is required for local sequence preflight")
    return ffmpeg, str(Path(ffmpeg).with_name("ffprobe"))


def _demuxer_for(ref: str, kind: Literal["video", "audio"]) -> str:
    suffix = Path(ref).suffix.lower()
    table = _VIDEO_DEMUXERS if kind == "video" else _AUDIO_DEMUXERS
    try:
        return table[suffix]
    except KeyError as exc:
        raise SequenceError(
            "unsafe_media",
            "Unsupported media container; playlists are not allowed",
        ) from exc


def _input_limits(ref: str, kind: Literal["video", "audio"]) -> list[str]:
    demuxer = _demuxer_for(ref, kind)
    args = ["-f", demuxer]
    if demuxer == "mov":
        args += ["-enable_drefs", "0", "-use_absolute_path", "0"]
    args += ["-protocol_whitelist", "file", "-format_whitelist", demuxer]
    return args


def _temporary_probe_path(
    ref: str,
    version: MediaVersion,
    *,
    kind: Literal["video", "audio"],
    media_scope: Any,
) -> Path:
    root = _scope_root(media_scope)
    max_bytes = MAX_VIDEO_BYTES if kind == "video" else MAX_AUDIO_BYTES
    payload = read_verified_media(version, output_root=root, max_bytes=max_bytes)
    suffix = Path(ref).suffix.lower()
    probe_dir = root / ".atelier-sequence-probe"
    with media_scope.storage_guard():
        media_scope.writer_lock.assert_owned()
        probe_dir.mkdir(mode=0o700, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=f"{version.sha256[:12]}-", suffix=suffix, dir=probe_dir)
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    return Path(name)


def _freeze_source(
    ref: str,
    *,
    kind: Literal["video", "audio"],
    ordinal: int,
    media_scope: Any,
) -> MediaVersion:
    root = _scope_root(media_scope)
    role = "reference_video" if kind == "video" else "reference_audio"
    max_bytes = MAX_VIDEO_BYTES if kind == "video" else MAX_AUDIO_BYTES
    try:
        with media_scope.storage_guard():
            media_scope.writer_lock.assert_owned()
            return freeze_media(
                ref,
                output_root=root,
                writer_lock=media_scope.writer_lock,
                role=role,
                ordinal=ordinal,
                max_bytes=max_bytes,
            )
    except MediaVersionError as exc:
        message = str(exc)
        if ref.startswith(("http://", "https://", "//", "blob:", "data:")):
            raise SequenceError(
                "remote_media",
                "Remote media is not supported; upload it before sequencing",
            ) from exc
        raise SequenceError("unsafe_media", message) from exc
    except StorageError as exc:
        raise SequenceError("storage_scope", str(exc)) from exc


def _retag_version(version: MediaVersion, *, role: str, ordinal: int) -> MediaVersion:
    if version.role == role and version.ordinal == ordinal:
        return version
    return MediaVersion(
        sha256=version.sha256,
        size_bytes=version.size_bytes,
        role=role,
        ordinal=ordinal,
        declared_media_type=version.declared_media_type,
    )


def _resolve_media(
    ref: str,
    *,
    kind: Literal["video", "audio"],
    ordinal: int,
    media_scope: Any,
    cache: dict[tuple[str, str], tuple[MediaVersion, dict[str, Any]]],
) -> dict[str, Any]:
    key = (kind, ref)
    role = "reference_video" if kind == "video" else "reference_audio"
    if key not in cache:
        base_version = _freeze_source(ref, kind=kind, ordinal=ordinal, media_scope=media_scope)
        base_media = _probe_frozen(ref, base_version, kind=kind, media_scope=media_scope)
        cache[key] = (base_version, base_media)
        return deepcopy(base_media)
    base_version, base_media = cache[key]
    media = deepcopy(base_media)
    media["media_version"] = _retag_version(base_version, role=role, ordinal=ordinal).__dict__
    return media


def _probe_frozen(
    ref: str,
    version: MediaVersion,
    *,
    kind: Literal["video", "audio"],
    media_scope: Any,
    process_runner: Callable[[list[str]], Any] | None = None,
) -> dict[str, Any]:
    root = _scope_root(media_scope)
    max_bytes = MAX_VIDEO_BYTES if kind == "video" else MAX_AUDIO_BYTES
    _, ffprobe = _ffmpeg_tools()
    with media_scope.storage_guard():
        media_scope.writer_lock.assert_owned()
        read_verified_media(version, output_root=root, max_bytes=max_bytes)
        path = _temporary_probe_path(ref, version, kind=kind, media_scope=media_scope)
        try:
            command = [
                ffprobe,
                "-v",
                "error",
                *_input_limits(ref, kind),
                "-i",
                str(path),
                "-show_streams",
                "-show_format",
                "-of",
                "json",
            ]
            if process_runner is None:
                result = subprocess.run(
                    command,
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=PROBE_TIMEOUT_SECONDS,
                )
                stdout = result.stdout
            else:
                result = process_runner(command, timeout=PROBE_TIMEOUT_SECONDS)
                stdout = result.stdout.decode("utf-8") if isinstance(result.stdout, bytes) else result.stdout
            info = json.loads(stdout)
        except (OSError, subprocess.SubprocessError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise SequenceError("invalid_media", "Cannot probe local managed media") from exc
        finally:
            try:
                path.unlink()
            except OSError:
                pass
    streams = info.get("streams", [])
    stream = next(
        (
            item
            for item in streams
            if item.get("codec_type") == kind
            and not item.get("disposition", {}).get("attached_pic")
        ),
        None,
    )
    if stream is None:
        raise SequenceError("invalid_media", f"Source has no real {kind} stream")
    duration = _positive_duration(stream.get("duration")) or _positive_duration(
        (info.get("format") or {}).get("duration")
    )
    if duration is None or duration > MAX_DURATION_SECONDS:
        raise SequenceError("invalid_media", "Source duration must be finite and at most one hour")
    media: dict[str, Any] = {
        "source_duration": duration,
        "stream_index": stream.get("index", 0),
        "media_version": version.__dict__,
    }
    if kind == "video":
        width = stream.get("width")
        height = stream.get("height")
        if (
            type(width) is not int
            or type(height) is not int
            or not 0 < width <= 8192
            or not 0 < height <= 8192
        ):
            raise SequenceError("invalid_media", "Invalid video dimensions")
        fps = _frame_rate_from_stream(stream)
        try:
            sar = float(
                Fraction(str(stream.get("sample_aspect_ratio", "1:1")).replace(":", "/"))
            )
        except (ValueError, ZeroDivisionError) as exc:
            raise SequenceError(
                "invalid_media",
                "Video frame rate / pixel aspect ratio is unavailable",
            ) from exc
        if fps is None or not math.isfinite(sar) or sar <= 0:
            raise SequenceError(
                "invalid_media",
                "Video frame rate / pixel aspect ratio is unavailable",
            )
        audio = next((item for item in streams if item.get("codec_type") == "audio"), None)
        media.update(
            width=width,
            height=height,
            fps=fps,
            sar=sar,
            audio_index=audio.get("index") if audio else None,
        )
    return media


def _frame_rate_from_stream(stream: dict[str, Any]) -> float | None:
    for key in ("avg_frame_rate", "r_frame_rate"):
        raw = stream.get(key)
        if raw is None:
            continue
        try:
            fps = float(Fraction(str(raw)))
        except (TypeError, ValueError, ZeroDivisionError):
            continue
        if math.isfinite(fps) and fps > 0:
            return fps
    return None


def _positive_duration(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) and parsed > 0 else None


def _node_map(project: AtelierProject) -> dict[str, Any]:
    return {node.id: node for node in project.nodes}


def _candidate_url(node: Any, candidate_id: str, kind: Literal["video", "audio"]) -> str:
    candidates = node.data.get("candidates", []) if isinstance(node.data, dict) else []
    if not isinstance(candidates, list):
        raise SequenceError("missing_candidate", "Sequence candidate not found")
    candidate = next(
        (item for item in candidates if isinstance(item, dict) and item.get("id") == candidate_id),
        None,
    )
    if candidate is None:
        raise SequenceError("missing_candidate", "Sequence candidate not found")
    if candidate.get("status") != "completed" or candidate.get("media_kind", kind) != kind:
        raise SequenceError("candidate_not_ready", f"Sequence candidate must be a completed {kind}")
    url = candidate.get("video_url") if kind == "video" else None
    url = url or candidate.get("audio_url") or candidate.get("media_url")
    if not isinstance(url, str) or not url:
        raise SequenceError("missing_media", f"Node/candidate has no rendered {kind} media")
    return url


def _source_url(
    nodes: dict[str, Any],
    node_id: str,
    candidate_id: str | None,
    kind: Literal["video", "audio"],
) -> str:
    node = nodes.get(node_id)
    if node is None:
        raise SequenceError("missing_node", "Sequence parent node not found")
    allowed = {"video", "draft"} if kind == "video" else {"audio"}
    if node.type not in allowed:
        raise SequenceError("wrong_media_kind", f"Sequence requires a {kind} node")
    if candidate_id is not None:
        return _candidate_url(node, candidate_id, kind)
    if kind == "audio" and isinstance(node.data, dict):
        selected = node.data.get("selected_candidate_id")
        if isinstance(selected, str) and selected:
            return _candidate_url(node, selected, kind)
    if kind == "video" and node.type != "video":
        raise SequenceError("wrong_media_kind", "Uploaded clip requires a video node")
    url = node.media_urls[0] if node.media_urls else None
    if not isinstance(url, str) or not url:
        raise SequenceError("missing_media", f"Node/candidate has no rendered {kind} media")
    return url


def _trim(start: float | None, end: float | None, duration: float) -> tuple[float, float]:
    start_value = 0.0 if start is None else float(start)
    end_value = duration if end is None else float(end)
    if not 0 <= start_value < end_value <= duration:
        raise SequenceError("invalid_trim", "Trim requires 0 <= start < end <= sourceDuration")
    return start_value, end_value


def _frame_duration(start: float, end: float) -> float:
    frames = round((end - start) * FPS)
    if frames <= 0:
        raise SequenceError("invalid_trim", "Clip must be at least one output frame")
    return frames / FPS


def _compile_sequence(
    project: AtelierProject,
    entries: Iterable[dict[str, Any] | AtelierSequenceEntry],
    *,
    media_scope: Any,
    audio_tracks: Iterable[dict[str, Any] | AtelierAudioTrack] | None = None,
    allow_empty: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    report = _empty_report()
    snapshot = _empty_snapshot()
    raw_entries = list(entries)
    raw_audio = list(project.audio_tracks if audio_tracks is None else audio_tracks)
    if not raw_entries and not allow_empty:
        report["errors"].append(
            _issue("empty_sequence", "Sequence is empty; add at least one clip")
        )
    if len(raw_entries) > MAX_CLIPS or len(raw_audio) > MAX_AUDIO_TRACKS:
        report["errors"].append(
            _issue("timeline_limit", "At most 64 clips and 16 audio tracks are supported")
        )
        return report, snapshot
    nodes = _node_map(project)
    seen: set[tuple[str, str]] = set()
    media_cache: dict[tuple[str, str], tuple[MediaVersion, dict[str, Any]]] = {}

    for index, raw in enumerate(raw_entries):
        identifier = _raw_id(raw, prefix="clip", index=index)
        try:
            dumped = _entry_dump(raw)
            entry = _parse_video_entry(dumped)
            key = ("clip", entry.id or identifier)
            if key in seen:
                raise SequenceError("duplicate_id", "Clip/track instance ids must be unique")
            seen.add(key)
            if (entry.transition.kind == "cut" and entry.transition.duration != 0) or (
                entry.transition.kind == "fade" and (index == 0 or entry.transition.duration <= 0)
            ):
                raise SequenceError(
                    "invalid_transition",
                    "First clip must cut; cut duration is 0 and fade duration is positive",
                )
            if (
                entry.trimStart is not None
                and entry.trimEnd is not None
                and not entry.trimStart < entry.trimEnd
            ):
                raise SequenceError(
                    "invalid_trim",
                    "Trim requires 0 <= start < end <= sourceDuration",
                )
            source_url = _source_url(nodes, entry.parentId, entry.candidateId, "video")
            _demuxer_for(source_url, "video")
            media = _resolve_media(
                source_url,
                kind="video",
                ordinal=index,
                media_scope=media_scope,
                cache=media_cache,
            )
            start, end = _trim(entry.trimStart, entry.trimEnd, media["source_duration"])
            duration = _frame_duration(start, end)
            fade = round(entry.transition.duration * FPS) / FPS
            if entry.transition.kind == "fade":
                previous = snapshot["clips"][-1] if snapshot["clips"] else None
                if previous is None or fade <= 0 or fade > min(previous["duration"], duration) / 2:
                    raise SequenceError(
                        "invalid_transition",
                        "Fade overlap must not exceed half either adjacent clip",
                    )
            if not snapshot["clips"]:
                snapshot["width"] = max(2, round(media["width"] * media["sar"] / 2) * 2)
                snapshot["height"] = max(2, round(media["height"] / 2) * 2)
            resolved = {
                "clip_id": entry.id or identifier,
                "parentId": entry.parentId,
                "candidateId": entry.candidateId,
                "source_url": source_url,
                "trimStart": start,
                "trimEnd": end,
                "duration": duration,
                "gain": entry.gain,
                "transition": {"kind": entry.transition.kind, "duration": fade},
                **media,
            }
            if (
                abs(duration - (end - start)) > 1e-7
                or abs(fade - entry.transition.duration) > 1e-7
            ):
                report["warnings"].append(
                    _issue(
                        "frame_quantized",
                        "Video duration/transition rounded to nearest 30 fps frame",
                        clip_id=identifier,
                    )
                )
            if media["audio_index"] is None:
                report["warnings"].append(
                    _issue(
                        "silence_added",
                        "Silent clip receives timeline silence",
                        clip_id=identifier,
                    )
                )
            snapshot["clips"].append(resolved)
            snapshot["duration"] += duration - fade
        except ValidationError as exc:
            message = "; ".join(
                ".".join(map(str, item["loc"])) + ": " + item["msg"]
                for item in exc.errors()
            )
            report["errors"].append(_issue("invalid_entry", message, clip_id=identifier))
        except SequenceError as exc:
            report["errors"].append(_issue(exc.code, str(exc), clip_id=identifier))

    for index, raw in enumerate(raw_audio):
        identifier = _raw_id(raw, prefix="audio", index=index)
        try:
            dumped = _entry_dump(raw)
            track = _parse_audio_track(dumped)
            key = ("audio", track.id)
            if key in seen:
                raise SequenceError("duplicate_id", "Clip/track instance ids must be unique")
            seen.add(key)
            source_url = _source_url(nodes, track.node_id, None, "audio")
            _demuxer_for(source_url, "audio")
            media = _resolve_media(
                source_url,
                kind="audio",
                ordinal=index,
                media_scope=media_scope,
                cache=media_cache,
            )
            start, end = _trim(track.trim_start, track.trim_end, media["source_duration"])
            resolved = {
                "id": track.id,
                "node_id": track.node_id,
                "source_url": source_url,
                "start": track.start,
                "trim_start": start,
                "trim_end": end,
                "gain": track.gain,
                "loop": track.loop,
                **media,
            }
            if track.start >= snapshot["duration"] and snapshot["duration"] > 0:
                report["warnings"].append(
                    _issue(
                        "audio_outside_timeline",
                        "Audio starts after the video and will be inaudible",
                        track_id=identifier,
                    )
                )
            snapshot["audio_tracks"].append(resolved)
        except ValidationError as exc:
            message = "; ".join(
                ".".join(map(str, item["loc"])) + ": " + item["msg"]
                for item in exc.errors()
            )
            report["errors"].append(_issue("invalid_entry", message, track_id=identifier))
        except SequenceError as exc:
            report["errors"].append(_issue(exc.code, str(exc), track_id=identifier))

    snapshot["duration"] = round(snapshot["duration"] * FPS) / FPS
    if snapshot["duration"] > MAX_DURATION_SECONDS:
        report["errors"].append(_issue("timeline_limit", "Timeline must not exceed one hour"))
    report.update({key: snapshot[key] for key in ("duration", "width", "height", "fps")})
    return report, snapshot


def normalize_sequence_inputs(
    entries: Iterable[dict[str, Any] | AtelierSequenceEntry],
    *,
    audio_tracks: Iterable[dict[str, Any] | AtelierAudioTrack] | None = None,
) -> tuple[list[AtelierSequenceEntry], list[AtelierAudioTrack] | None]:
    """Return canonical timeline models using the same parser as preflight/render.

    This is intentionally pure: it validates entry shape/defaults only and does
    not resolve nodes, probe media, freeze blobs, or touch storage. Callers that
    persist the result must still run ``resolve_sequence_snapshot`` first.
    """
    normalized_entries = [_parse_video_entry(_entry_dump(entry)) for entry in entries]
    normalized_audio = None
    if audio_tracks is not None:
        normalized_audio = [_parse_audio_track(_entry_dump(track)) for track in audio_tracks]
    return normalized_entries, normalized_audio


def preflight_sequence(
    project: AtelierProject,
    entries: Iterable[dict[str, Any] | AtelierSequenceEntry],
    *,
    media_scope: Any,
    audio_tracks: Iterable[dict[str, Any] | AtelierAudioTrack] | None = None,
    allow_empty: bool = False,
) -> dict[str, Any]:
    """Return locatable timeline errors/warnings without hiding invalid entries."""
    return _compile_sequence(
        project,
        entries,
        media_scope=media_scope,
        audio_tracks=audio_tracks,
        allow_empty=allow_empty,
    )[0]


def resolve_sequence_snapshot(
    project: AtelierProject,
    entries: Iterable[dict[str, Any] | AtelierSequenceEntry],
    *,
    media_scope: Any,
    audio_tracks: Iterable[dict[str, Any] | AtelierAudioTrack] | None = None,
    allow_empty: bool = False,
) -> dict[str, Any]:
    """Resolve a project sequence into an immutable, renderer-ready snapshot."""
    report, snapshot = _compile_sequence(
        project,
        entries,
        media_scope=media_scope,
        audio_tracks=audio_tracks,
        allow_empty=allow_empty,
    )
    if report["errors"]:
        raise SequenceValidationError(report)
    return snapshot


def _validate_media_version(
    value: Any,
    *,
    expected_role: str,
    ordinal: int,
    media_scope: Any,
) -> MediaVersion:
    try:
        version = MediaVersion(**value)
    except (TypeError, ValueError) as exc:
        raise SequenceError("invalid_snapshot", "Snapshot media_version is invalid") from exc
    if version.role != expected_role or version.ordinal != ordinal:
        raise SequenceError(
            "invalid_snapshot",
            "Snapshot media_version does not match timeline position",
        )
    max_bytes = MAX_VIDEO_BYTES if expected_role == "reference_video" else MAX_AUDIO_BYTES
    try:
        read_verified_media(version, output_root=_scope_root(media_scope), max_bytes=max_bytes)
    except MediaVersionError as exc:
        raise SequenceError("invalid_snapshot", "Snapshot media_version cannot be verified") from exc
    except StorageError as exc:
        raise SequenceError("storage_scope", str(exc)) from exc
    return version


def _strict_snapshot_fields(raw: dict[str, Any], allowed: set[str], label: str) -> None:
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise SequenceError("invalid_snapshot", f"Unsupported snapshot {label} field: {unknown[0]}")


def _same_number(left: Any, right: float, *, tolerance: float = 1e-6) -> bool:
    return _is_number(left) and abs(float(left) - float(right)) <= tolerance


def _snapshot_str(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise SequenceError("invalid_snapshot", f"Snapshot {label} must be a nonempty string")
    return value


def _snapshot_optional_str(value: Any, label: str) -> str | None:
    if value is None:
        return None
    return _snapshot_str(value, label)


def _snapshot_gain(value: Any) -> float:
    if not _is_number(value) or not 0 <= float(value) <= 2:
        raise SequenceError("invalid_snapshot", "Snapshot gain is invalid")
    return float(value)


def _snapshot_transition(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SequenceError("invalid_snapshot", "Snapshot transition must be an object")
    _strict_snapshot_fields(value, {"kind", "duration"}, "transition")
    kind = value.get("kind")
    duration = value.get("duration")
    if kind not in {"cut", "fade"} or not _is_number(duration) or float(duration) < 0:
        raise SequenceError("invalid_snapshot", "Snapshot transition is invalid")
    return {"kind": kind, "duration": float(duration)}


def _validate_snapshot_clip(
    clip: Any,
    *,
    index: int,
    previous: dict[str, Any] | None,
    media_scope: Any,
    process_runner: Callable[[list[str]], Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(clip, dict):
        raise SequenceError("invalid_snapshot", "Snapshot clip must be an object")
    _strict_snapshot_fields(clip, _SNAPSHOT_CLIP_FIELDS, "clip")
    source_url = _snapshot_str(clip.get("source_url"), "source_url")
    version = _validate_media_version(
        clip.get("media_version"),
        expected_role="reference_video",
        ordinal=index,
        media_scope=media_scope,
    )
    actual = _probe_frozen(source_url, version, kind="video", media_scope=media_scope, process_runner=process_runner)
    clip_id = _snapshot_str(clip.get("clip_id"), "clip_id")
    parent_id = _snapshot_str(clip.get("parentId"), "parentId")
    candidate_id = _snapshot_optional_str(clip.get("candidateId"), "candidateId")
    gain = _snapshot_gain(clip.get("gain"))
    transition = _snapshot_transition(clip.get("transition"))
    start, end = _trim(
        _strict_optional_number(clip.get("trimStart"), "trimStart"),
        _strict_optional_number(clip.get("trimEnd"), "trimEnd", positive=True),
        actual["source_duration"],
    )
    duration = _frame_duration(start, end)
    fade = round(transition["duration"] * FPS) / FPS
    if (transition["kind"] == "cut" and fade != 0) or (
        transition["kind"] == "fade" and (index == 0 or fade <= 0)
    ):
        raise SequenceError("invalid_snapshot", "Snapshot transition is invalid")
    if transition["kind"] == "fade" and (
        previous is None or fade > min(previous["duration"], duration) / 2
    ):
        raise SequenceError("invalid_snapshot", "Snapshot fade exceeds adjacent clip duration")
    expected_pairs = {
        "trimStart": start,
        "trimEnd": end,
        "duration": duration,
        "source_duration": actual["source_duration"],
        "width": actual["width"],
        "height": actual["height"],
        "fps": actual["fps"],
        "sar": actual["sar"],
    }
    for key, expected in expected_pairs.items():
        if not _same_number(clip.get(key), expected, tolerance=1e-5):
            raise SequenceError("invalid_snapshot", f"Snapshot clip {key} does not match source")
    audio_index = clip.get("audio_index")
    if audio_index != actual.get("audio_index"):
        raise SequenceError("invalid_snapshot", "Snapshot clip audio_index does not match source")
    if transition != {"kind": transition["kind"], "duration": fade}:
        raise SequenceError("invalid_snapshot", "Snapshot transition is not frame-normalized")
    return {
        "clip_id": clip_id,
        "parentId": parent_id,
        "candidateId": candidate_id,
        "source_url": source_url,
        "trimStart": start,
        "trimEnd": end,
        "duration": duration,
        "gain": gain,
        "transition": transition,
        **actual,
    }


def _validate_snapshot_audio(
    track: Any, *, index: int, media_scope: Any,
    process_runner: Callable[[list[str]], Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(track, dict):
        raise SequenceError("invalid_snapshot", "Snapshot audio track must be an object")
    _strict_snapshot_fields(track, _SNAPSHOT_AUDIO_FIELDS, "audio track")
    source_url = _snapshot_str(track.get("source_url"), "source_url")
    version = _validate_media_version(
        track.get("media_version"),
        expected_role="reference_audio",
        ordinal=index,
        media_scope=media_scope,
    )
    actual = _probe_frozen(source_url, version, kind="audio", media_scope=media_scope, process_runner=process_runner)
    track_id = _snapshot_str(track.get("id"), "id")
    node_id = _snapshot_str(track.get("node_id"), "node_id")
    if not _is_number(track.get("start")) or float(track["start"]) < 0:
        raise SequenceError("invalid_snapshot", "Snapshot audio start is invalid")
    gain = _snapshot_gain(track.get("gain"))
    if type(track.get("loop")) is not bool:
        raise SequenceError("invalid_snapshot", "Snapshot audio loop is invalid")
    start, end = _trim(
        _strict_optional_number(track.get("trim_start"), "trim_start"),
        _strict_optional_number(track.get("trim_end"), "trim_end", positive=True),
        actual["source_duration"],
    )
    for key, expected in {
        "trim_start": start,
        "trim_end": end,
        "source_duration": actual["source_duration"],
    }.items():
        if not _same_number(track.get(key), expected, tolerance=1e-5):
            raise SequenceError("invalid_snapshot", f"Snapshot audio {key} does not match source")
    return {
        "id": track_id,
        "node_id": node_id,
        "source_url": source_url,
        "start": float(track["start"]),
        "trim_start": start,
        "trim_end": end,
        "gain": gain,
        "loop": track["loop"],
        **actual,
    }


def validate_sequence_snapshot(
    snapshot: dict[str, Any], *, media_scope: Any,
    process_runner: Callable[[list[str]], Any] | None = None,
) -> dict[str, Any]:
    """Validate immutable snapshot shape and fixed media blobs.

    The mutable ``source_url`` is retained for diagnostics only. Validation reads
    the content-addressed ``MediaVersion`` records, so a same-path source change
    cannot alter redo/render input.
    """
    if not isinstance(snapshot, dict) or snapshot.get("schema_version") != SNAPSHOT_SCHEMA_VERSION:
        raise SequenceValidationError(
            {
                "errors": [_issue("invalid_snapshot", "Unsupported Atelier timeline snapshot")],
                "warnings": [],
            }
        )
    report = _empty_report()
    checked = deepcopy(snapshot)
    resolved_clips: list[dict[str, Any]] = []
    resolved_audio: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    try:
        _strict_snapshot_fields(checked, _SNAPSHOT_FIELDS, "timeline")
        if (
            checked.get("fps") != FPS
            or type(checked.get("clips")) is not list
            or type(checked.get("audio_tracks")) is not list
        ):
            raise SequenceError("invalid_snapshot", "Snapshot timeline shape is invalid")
        for index, clip in enumerate(checked["clips"]):
            clip_id = (
                clip.get("clip_id")
                if isinstance(clip, dict) and isinstance(clip.get("clip_id"), str)
                else f"clip-{index + 1}"
            )
            try:
                key = ("clip", clip_id)
                if key in seen:
                    raise SequenceError("duplicate_id", "Clip/track instance ids must be unique")
                seen.add(key)
                resolved_clips.append(
                    _validate_snapshot_clip(
                        clip,
                        index=index,
                        previous=resolved_clips[-1] if resolved_clips else None,
                        media_scope=media_scope,
                        process_runner=process_runner,
                    )
                )
            except SequenceError as exc:
                report["errors"].append(_issue(exc.code, str(exc), clip_id=clip_id))
        for index, track in enumerate(checked["audio_tracks"]):
            track_id = (
                track.get("id")
                if isinstance(track, dict) and isinstance(track.get("id"), str)
                else f"audio-{index + 1}"
            )
            try:
                key = ("audio", track_id)
                if key in seen:
                    raise SequenceError("duplicate_id", "Clip/track instance ids must be unique")
                seen.add(key)
                resolved_audio.append(
                    _validate_snapshot_audio(track, index=index, media_scope=media_scope, process_runner=process_runner)
                )
            except SequenceError as exc:
                report["errors"].append(_issue(exc.code, str(exc), track_id=track_id))
        duration = round(sum(clip["duration"] - clip["transition"]["duration"] for clip in resolved_clips) * FPS) / FPS
        if resolved_clips:
            width = max(2, round(resolved_clips[0]["width"] * resolved_clips[0]["sar"] / 2) * 2)
            height = max(2, round(resolved_clips[0]["height"] / 2) * 2)
        else:
            width = height = 0
        for key, expected in {"duration": duration, "width": width, "height": height}.items():
            if not _same_number(checked.get(key), expected, tolerance=1e-5):
                report["errors"].append(
                    _issue(
                        "invalid_snapshot",
                        f"Snapshot timeline {key} does not match resolved clips",
                    )
                )
    except SequenceError as exc:
        report["errors"].append(_issue(exc.code, str(exc)))
    if report["errors"]:
        raise SequenceValidationError(report)
    return checked
