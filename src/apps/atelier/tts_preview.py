"""Pure Atelier TTS generation preview normalization.

This module validates a future audio generation request against the current
canvas graph and built-in TTS voice catalog without creating receipts, attempts,
credentials, provider submissions, output files, or storage writes.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from ...audio.tts import PreparedTTS
from ...utils.json_storage import serialize_json
from .models import AtelierNode, AtelierProject
from .tts_commands import ATELIER_TTS_ENTRYPOINT, TTSCandidateCommand, normalize_tts_candidate_request

TTS_PREVIEW_CONTRACT_VERSION = "atelier-tts-generation-preview-v1"


@dataclass(frozen=True)
class AtelierTTSGenerationPreview:
    """Secret-free pure TTS preview record."""

    preview_fingerprint: str
    public_record: dict[str, Any]
    prepared: PreparedTTS
    target_node: AtelierNode


def build_tts_generation_preview(
    project: AtelierProject,
    node_id: str,
    command: TTSCandidateCommand,
) -> AtelierTTSGenerationPreview:
    """Normalize a TTS generation request without filesystem/provider side effects."""

    target = _require_audio_target(project, node_id)
    prepared = normalize_tts_candidate_request(command)
    public_record = {
        "schema_version": 1,
        "preview_contract_version": TTS_PREVIEW_CONTRACT_VERSION,
        "entrypoint": ATELIER_TTS_ENTRYPOINT,
        "project_id": project.id,
        "node_id": target.id,
        "raw_request": command.model_dump(
            mode="json",
            exclude_unset=True,
            exclude={"expected_revision"},
        ),
        "prepared_tts": prepared.to_snapshot(),
    }
    return AtelierTTSGenerationPreview(
        preview_fingerprint=_fingerprint(public_record),
        public_record=public_record,
        prepared=prepared,
        target_node=target,
    )


def _require_audio_target(project: AtelierProject, node_id: str) -> AtelierNode:
    target = next((node for node in project.nodes if node.id == node_id), None)
    if target is None or target.type != "audio":
        raise ValueError("Atelier TTS generation requires an audio node")
    if target.project_id != project.id:
        raise ValueError("Atelier TTS target node must belong to the current project")
    return target


def _fingerprint(public_record: dict[str, Any]) -> str:
    return hashlib.sha256(serialize_json(public_record).encode("utf-8")).hexdigest()
