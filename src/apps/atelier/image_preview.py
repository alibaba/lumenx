"""Pure Atelier image generation preview normalization.

This module validates a future image generation request against the current
canvas graph and model catalog without creating receipts, attempts, frozen
media, credentials, provider submissions, or storage writes.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from ...utils.json_storage import serialize_json
from ...utils.media_refs import MEDIA_REF_LOCAL_PATH, classify_media_ref
from .generation_commands import (
    ATELIER_IMAGE_ENTRYPOINT,
    ImageCandidateCommand,
    normalize_image_candidate_request,
)
from .models import AtelierNode, AtelierProject
from .references import canonical_atelier_reference_data, has_pending_references, is_pending_reference_to, reference_media_urls

IMAGE_PREVIEW_CONTRACT_VERSION = "atelier-image-generation-preview-v1"
_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "webp", "bmp"}


@dataclass(frozen=True)
class AtelierImageGenerationPreview:
    """Secret-free pure image preview record."""

    preview_fingerprint: str
    public_record: dict[str, Any]
    intent: Any
    target_node: AtelierNode


def build_image_generation_preview(
    project: AtelierProject,
    node_id: str,
    command: ImageCandidateCommand,
) -> AtelierImageGenerationPreview:
    """Normalize an image generation request without filesystem/provider side effects."""

    intent = normalize_image_candidate_request(command)
    target = _require_image_target(project, node_id)
    media = _validate_image_sources(project, target, command)
    public_record = {
        "schema_version": 1,
        "preview_contract_version": IMAGE_PREVIEW_CONTRACT_VERSION,
        "entrypoint": ATELIER_IMAGE_ENTRYPOINT,
        "project_id": project.id,
        "node_id": target.id,
        "raw_request": command.model_dump(
            mode="json",
            exclude_unset=True,
            exclude={"expected_revision"},
        ),
        "intent": intent.to_dict(),
        "media": media,
    }
    return AtelierImageGenerationPreview(
        preview_fingerprint=_fingerprint(public_record),
        public_record=public_record,
        intent=intent,
        target_node=target,
    )


def _require_image_target(project: AtelierProject, node_id: str) -> AtelierNode:
    target = next((node for node in project.nodes if node.id == node_id), None)
    if target is None or target.type != "image":
        raise ValueError("Atelier image generation requires an image node")
    if target.project_id != project.id:
        raise ValueError("Atelier image target belongs to another project")
    return target


def _validate_image_sources(
    project: AtelierProject,
    target: AtelierNode,
    command: ImageCandidateCommand,
) -> list[dict[str, Any]]:
    if command.mode == "t2i":
        if command.media:
            raise ValueError("Atelier image T2I cannot include media")
        return []
    if not command.media:
        if has_pending_references(target.data):
            raise ValueError("atelier_reference_pending_media")
        raise ValueError("Atelier image I2I requires reference media")
    refs = canonical_atelier_reference_data(target, project.nodes)
    seen: set[tuple[str, int]] = set()
    result: list[dict[str, Any]] = []
    for item in command.media:
        key = (item.role, item.ordinal)
        if key in seen:
            raise ValueError("Atelier image media role ordinal is duplicated")
        seen.add(key)
        source = next((node for node in project.nodes if node.id == item.node_id), None)
        if source is None or source.type != "image" or source.project_id != project.id:
            raise ValueError("Atelier image source has the wrong media kind")
        if source.id not in refs["reference_node_ids"]:
            raise ValueError("Atelier image source must be attached to the target node")
        if item.media_url not in reference_media_urls(source):
            if is_pending_reference_to(target.data, source.id):
                raise ValueError("atelier_reference_pending_media")
            raise ValueError("Atelier image media does not belong to the reference node")
        if not _is_managed_image_ref(item.media_url):
            raise ValueError("Atelier image media must be a managed local image reference")
        result.append(item.model_dump(mode="json"))
    ordinals = [item["ordinal"] for item in result]
    if ordinals != list(range(len(ordinals))):
        raise ValueError("Atelier image media ordinals must be continuous within role")
    return result


def _is_managed_image_ref(value: str) -> bool:
    if classify_media_ref(value) != MEDIA_REF_LOCAL_PATH:
        return False
    suffix = value.split("?", 1)[0].rsplit(".", 1)[-1].lower() if "." in value else ""
    return suffix in _IMAGE_EXTENSIONS


def _fingerprint(public_record: dict[str, Any]) -> str:
    return hashlib.sha256(serialize_json(public_record).encode("utf-8")).hexdigest()
