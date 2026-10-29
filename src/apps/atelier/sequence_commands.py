"""Storage-backed commands for Atelier sequence edits and preflight."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from .models import AtelierAudioTrack, AtelierProject, AtelierSequenceEntry
from .sequence import normalize_sequence_inputs, preflight_sequence, resolve_sequence_snapshot


@dataclass(frozen=True)
class SequenceAudioTracksOmitted:
    """Sentinel for preserving the project's current audio tracks."""


AUDIO_TRACKS_OMITTED = SequenceAudioTracksOmitted()


@dataclass(frozen=True)
class ReplaceSequenceCommand:
    expected_revision: int
    entries: list[dict[str, Any] | AtelierSequenceEntry]
    audio_tracks: (
        list[dict[str, Any] | AtelierAudioTrack] | SequenceAudioTracksOmitted
    ) = AUDIO_TRACKS_OMITTED


@dataclass(frozen=True)
class SequencePreflightReport:
    project_id: str
    revision: int
    report: dict[str, Any]


def replace_sequence(
    project: AtelierProject,
    command: ReplaceSequenceCommand,
    *,
    media_scope: Any,
    now: float,
) -> None:
    """Validate and atomically replace the saved sequence on a project copy."""
    audio_tracks = (
        None
        if isinstance(command.audio_tracks, SequenceAudioTracksOmitted)
        else command.audio_tracks
    )
    resolve_sequence_snapshot(
        project,
        command.entries,
        media_scope=media_scope,
        audio_tracks=audio_tracks,
        allow_empty=True,
    )
    normalized_entries, normalized_audio = normalize_sequence_inputs(
        command.entries,
        audio_tracks=audio_tracks,
    )
    changed = project.sequence != normalized_entries
    if normalized_audio is not None:
        changed = changed or project.audio_tracks != normalized_audio
    project.sequence = normalized_entries
    if normalized_audio is not None:
        project.audio_tracks = normalized_audio
    if changed:
        project.updated_at = now


def preflight_saved_sequence(
    project: AtelierProject,
    entries: Iterable[dict[str, Any] | AtelierSequenceEntry],
    *,
    media_scope: Any,
    audio_tracks: Iterable[dict[str, Any] | AtelierAudioTrack] | None = None,
) -> SequencePreflightReport:
    """Run authoritative timeline preflight against a captured committed project."""
    return SequencePreflightReport(
        project_id=project.id,
        revision=project.revision,
        report=preflight_sequence(
            project,
            entries,
            media_scope=media_scope,
            audio_tracks=audio_tracks,
        ),
    )
