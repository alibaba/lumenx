"""Explicit user selection for completed Atelier generation candidates."""

from __future__ import annotations

import os
import stat
import time
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, Field

from ...core import project_package_media as package_media
from ...utils import media_versions
from .project_package import _MEDIA_POLICY, _STRIP_CANDIDATE_KEYS
from .generation_ledger import (
    GENERATION_AUDIO_ENTRYPOINT,
    GENERATION_CANDIDATE_CONTRACT,
    GENERATION_IMAGE_ENTRYPOINT,
    GENERATION_TERMINAL_STATUSES,
    MEDIA_GENERATION_CANDIDATE_CONTRACT,
)
from .models import (
    AtelierGenerationAttempt,
    AtelierGenerationRequestReceipt,
    AtelierMediaGenerationAttempt,
    AtelierNode,
    AtelierProject,
)
from .nodes import NodeCommandAck
from .storage import AtelierStorage


class SelectVideoCandidateCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    expected_revision: int = Field(..., ge=0)


class SetVideoCandidateVisibilityCommand(SelectVideoCandidateCommand):
    visible: bool


class GenerationCandidateNotFound(ValueError):
    """The requested generation candidate is not a live selectable candidate."""


class GenerationCandidateNotSelectable(ValueError):
    """The requested generation candidate exists but is not completed/selectable."""


class AtelierGenerationSelectionService:
    """Apply an explicit human choice of one completed generated take."""

    def __init__(self, storage: AtelierStorage, *, clock: Callable[[], float] = time.time):
        self.storage = storage
        self._clock = clock

    def select_video_candidate(
        self,
        project_id: str,
        node_id: str,
        candidate_id: str,
        command: SelectVideoCandidateCommand,
    ) -> NodeCommandAck:
        now = self._clock()

        def apply(project: AtelierProject) -> None:
            node = _find_video_node(project, node_id)
            candidate, requires_ledger = _find_live_selectable_candidate(self.storage, project, node, candidate_id)
            if requires_ledger:
                attempt = _matching_completed_attempt(project, node_id=node_id, candidate=candidate)
                _matching_receipt(project, attempt)
            video_url = candidate["video_url"]
            if node.data.get("selected_candidate_id") == candidate_id and node.media_urls == [video_url]:
                return
            data = dict(node.data)
            data["selected_candidate_id"] = candidate_id
            node.data = data
            node.media_urls = [video_url]
            node.updated_at = now
            project.updated_at = now

        project = self.storage.apply_project_command(
            project_id,
            expected_revision=command.expected_revision,
            command=apply,
        )
        return NodeCommandAck(project=project, node_ids=[node_id])

    def select_media_candidate(
        self,
        project_id: str,
        node_id: str,
        candidate_id: str,
        command: SelectVideoCandidateCommand,
    ) -> NodeCommandAck:
        now = self._clock()

        def apply(project: AtelierProject) -> None:
            node = _find_media_node(project, node_id)
            candidate = _find_live_selectable_media_candidate(self.storage, project, node, candidate_id)
            if candidate.get("authority") == "provider":
                attempt = _matching_media_attempt(project, node_id=node_id, candidate=candidate, require_completed=True)
                _matching_media_receipt(project, attempt)
                _verify_completed_media_artifact(self.storage, attempt, candidate)
            media_url = candidate["media_url"]
            if node.data.get("selected_candidate_id") == candidate_id and node.media_urls == [media_url]:
                return
            data = dict(node.data)
            data["selected_candidate_id"] = candidate_id
            node.data = data
            node.media_urls = [media_url]
            node.updated_at = now
            project.updated_at = now

        project = self.storage.apply_project_command(
            project_id,
            expected_revision=command.expected_revision,
            command=apply,
        )
        return NodeCommandAck(project=project, node_ids=[node_id])

    def set_media_candidate_visibility(
        self, project_id: str, node_id: str, candidate_id: str,
        command: SetVideoCandidateVisibilityCommand,
    ) -> NodeCommandAck:
        return self._set_candidate_visibility(project_id, node_id, candidate_id, command, media=True)

    def set_video_candidate_visibility(
        self, project_id: str, node_id: str, candidate_id: str,
        command: SetVideoCandidateVisibilityCommand,
    ) -> NodeCommandAck:
        return self._set_candidate_visibility(project_id, node_id, candidate_id, command, media=False)

    def _set_candidate_visibility(
        self, project_id: str, node_id: str, candidate_id: str,
        command: SetVideoCandidateVisibilityCommand, *, media: bool,
    ) -> NodeCommandAck:
        """Move a terminal take intact without changing execution or reselecting it."""
        now = self._clock()

        def apply(project: AtelierProject) -> None:
            node = _find_media_node(project, node_id) if media else _find_video_node(project, node_id)
            data = dict(node.data)
            collections = {key: data.get(key, []) for key in ("candidates", "deleted_candidates")}
            if any(not isinstance(items, list) or any(not isinstance(item, dict) for item in items)
                   for items in collections.values()):
                raise GenerationCandidateNotSelectable("Atelier candidate collections are invalid")
            matches = [(key, item) for key, items in collections.items() for item in items
                       if item.get("id") == candidate_id]
            if len(matches) != 1:
                raise GenerationCandidateNotFound("Atelier candidate identity is missing or ambiguous")
            source, candidate = matches[0]
            if media:
                if candidate.get("execution_contract") != MEDIA_GENERATION_CANDIDATE_CONTRACT:
                    raise GenerationCandidateNotFound("Atelier media generation candidate not found")
                if candidate.get("media_kind") != node.type or candidate.get("status") not in GENERATION_TERMINAL_STATUSES:
                    raise GenerationCandidateNotSelectable("Only terminal media candidates can change visibility")
                if candidate.get("authority") == "imported":
                    if not _is_imported_inert_media_candidate(self.storage, project, node, candidate):
                        raise GenerationCandidateNotFound("Atelier imported media candidate is not a safe copied artifact")
                elif candidate.get("authority") == "provider":
                    _matching_media_receipt(project, _matching_media_attempt(project, node_id=node_id, candidate=candidate, require_completed=False))
                else:
                    raise GenerationCandidateNotFound("Atelier media generation candidate authority is invalid")
            else:
                if candidate.get("media_kind", "video") != "video" or candidate.get("status") not in GENERATION_TERMINAL_STATUSES:
                    raise GenerationCandidateNotSelectable("Only terminal video candidates can change visibility")
                if candidate.get("execution_contract") == GENERATION_CANDIDATE_CONTRACT:
                    attempt = next((item for item in project.generation_attempts
                                    if item.attempt_id == candidate.get("attempt_id")), None)
                    if attempt is None or attempt.node_id != node_id or attempt.candidate_id != candidate_id:
                        raise GenerationCandidateNotFound("Atelier candidate does not match attempt")
                    if attempt.status not in GENERATION_TERMINAL_STATUSES:
                        raise GenerationCandidateNotSelectable("Active generation cannot change visibility")
                    _matching_receipt(project, attempt)
            target = "candidates" if command.visible else "deleted_candidates"
            if source == target:
                return
            data[source] = [item for item in collections[source] if item.get("id") != candidate_id]
            data[target] = [*collections[target], candidate]
            if not command.visible and data.get("selected_candidate_id") == candidate_id:
                data.pop("selected_candidate_id", None)
                node.media_urls = []
            node.data = data
            node.updated_at = now
            project.updated_at = now

        project = self.storage.apply_project_command(
            project_id, expected_revision=command.expected_revision, command=apply,
        )
        return NodeCommandAck(project=project, node_ids=[node_id])


def _find_video_node(project: AtelierProject, node_id: str) -> AtelierNode:
    node = next((item for item in project.nodes if item.id == node_id), None)
    if node is None or node.type != "video":
        raise GenerationCandidateNotFound("Atelier generation candidate owner not found")
    return node


def _find_live_selectable_candidate(
    storage: AtelierStorage,
    project: AtelierProject,
    node: AtelierNode,
    candidate_id: str,
) -> tuple[dict[str, Any], bool]:
    values = node.data.get("candidates", []) if isinstance(node.data, dict) else []
    if not isinstance(values, list):
        raise GenerationCandidateNotFound("Atelier generation candidate not found")
    for item in values:
        if not isinstance(item, dict) or item.get("id") != candidate_id:
            continue
        candidate = dict(item)
        if candidate.get("execution_contract") == GENERATION_CANDIDATE_CONTRACT:
            _require_completed_video_candidate(candidate)
            return candidate, True
        _require_completed_video_candidate(candidate)
        if _is_imported_inert_candidate(storage, project, candidate):
            return candidate, False
        raise GenerationCandidateNotFound("Atelier generation candidate not found")
    raise GenerationCandidateNotFound("Atelier generation candidate not found")


def _find_media_node(project: AtelierProject, node_id: str) -> AtelierNode:
    node = next((item for item in project.nodes if item.id == node_id), None)
    if node is None or node.type not in {"image", "audio"}:
        raise GenerationCandidateNotFound("Atelier media generation candidate owner not found")
    return node


def _find_live_selectable_media_candidate(
    storage: AtelierStorage,
    project: AtelierProject,
    node: AtelierNode,
    candidate_id: str,
) -> dict[str, Any]:
    values = node.data.get("candidates", []) if isinstance(node.data, dict) else []
    if not isinstance(values, list):
        raise GenerationCandidateNotFound("Atelier media generation candidate not found")
    for item in values:
        if not isinstance(item, dict) or item.get("id") != candidate_id:
            continue
        candidate = dict(item)
        if candidate.get("execution_contract") != MEDIA_GENERATION_CANDIDATE_CONTRACT:
            raise GenerationCandidateNotFound("Atelier media generation candidate not found")
        if candidate.get("media_kind") != node.type:
            raise GenerationCandidateNotSelectable("Atelier media generation candidate kind does not match owner")
        if candidate.get("status") != "completed":
            raise GenerationCandidateNotSelectable("Atelier media generation candidate is not completed")
        media_url = candidate.get("media_url")
        if not isinstance(media_url, str) or not media_url:
            raise GenerationCandidateNotSelectable("Atelier media generation candidate has no completed media")
        if candidate.get("authority") == "imported":
            if _is_imported_inert_media_candidate(storage, project, node, candidate):
                return candidate
            raise GenerationCandidateNotFound("Atelier imported media candidate is not a safe copied artifact")
        if candidate.get("authority") != "provider":
            raise GenerationCandidateNotFound("Atelier media generation candidate authority is invalid")
        return candidate
    raise GenerationCandidateNotFound("Atelier media generation candidate not found")


def _matching_media_attempt(
    project: AtelierProject,
    *,
    node_id: str,
    candidate: dict[str, Any],
    require_completed: bool,
) -> AtelierMediaGenerationAttempt:
    if candidate.get("authority") == "imported":
        raise GenerationCandidateNotFound("Atelier imported media candidate has no provider attempt")
    attempt_id = candidate.get("attempt_id")
    attempt = next((item for item in project.generation_attempts if item.attempt_id == attempt_id), None)
    if not isinstance(attempt, AtelierMediaGenerationAttempt):
        raise GenerationCandidateNotFound("Atelier media generation attempt not found")
    if (
        attempt.project_id != project.id
        or attempt.node_id != node_id
        or attempt.candidate_id != candidate.get("id")
        or attempt.media_kind != candidate.get("media_kind")
        or attempt.provider_execution.fingerprint != candidate.get("execution_fingerprint")
    ):
        raise GenerationCandidateNotFound("Atelier media generation candidate does not match attempt")
    if require_completed:
        if attempt.status != "completed" or attempt.output_ref != candidate.get("media_url"):
            raise GenerationCandidateNotSelectable("Atelier media generation candidate is not completed")
    else:
        if attempt.status != candidate.get("status"):
            raise GenerationCandidateNotSelectable("Atelier media generation candidate status does not match attempt")
        if attempt.status == "completed" and attempt.output_ref != candidate.get("media_url"):
            raise GenerationCandidateNotSelectable("Atelier media generation candidate output does not match attempt")
    return attempt


def _matching_media_receipt(project: AtelierProject, attempt: AtelierMediaGenerationAttempt) -> AtelierGenerationRequestReceipt:
    expected_entrypoint = GENERATION_IMAGE_ENTRYPOINT if attempt.media_kind == "image" else GENERATION_AUDIO_ENTRYPOINT
    receipt = next((item for item in project.generation_request_receipts if any(member.attempt_id == attempt.attempt_id for member in item.receipt_members)), None)
    if receipt is None:
        raise GenerationCandidateNotFound("Atelier media generation receipt not found")
    member = next((item for item in receipt.receipt_members if item.attempt_id == attempt.attempt_id), None)
    if (
        member is None
        or receipt.entrypoint != expected_entrypoint
        or receipt.project_id != project.id
        or receipt.node_id != attempt.node_id
        or member.candidate_id != attempt.candidate_id
        or member.execution_fingerprint != attempt.provider_execution.fingerprint
    ):
        raise GenerationCandidateNotFound("Atelier media generation receipt does not match attempt")
    return receipt


def _is_imported_inert_media_candidate(storage: AtelierStorage, project: AtelierProject, node: AtelierNode, candidate: dict[str, Any]) -> bool:
    if project.import_provenance is None:
        return False
    if candidate.get("authority") != "imported" or candidate.get("attempt_id") is not None or candidate.get("execution_fingerprint") is not None:
        return False
    if candidate.get("media_kind") != node.type or candidate.get("status") != "completed" or candidate.get("error_code") is not None:
        return False
    media_url = candidate.get("media_url")
    if not isinstance(media_url, str) or not _is_safe_imported_media_ref(media_url, project_id=project.id, media_kind=node.type):
        return False
    scope = storage._media_write_scope()
    try:
        with package_media.media_parent(
            scope.data_dir,
            media_url,
            scope.writer_lock,
            policy=_MEDIA_POLICY,
            error=GenerationCandidateNotFound,
        ) as (parent_fd, name, check):
            linked = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            if not stat.S_ISREG(linked.st_mode):
                return False
            check()
            return True
    except (GenerationCandidateNotFound, OSError):
        return False


def _verify_completed_media_artifact(
    storage: AtelierStorage,
    attempt: AtelierMediaGenerationAttempt,
    candidate: dict[str, Any],
) -> None:
    record = attempt.provider_execution
    proof = record.artifact
    media_url = candidate.get("media_url")
    if (
        attempt.status != "completed"
        or record.phase != "completed"
        or proof is None
        or record.prepared_artifact != proof
        or proof.get("relative_path") != media_url
        or proof.get("media_kind") != attempt.media_kind
    ):
        raise GenerationCandidateNotSelectable("Atelier media generation completed proof is invalid")
    scope = storage._media_write_scope()
    try:
        media_versions.verify_managed_media_file(
            media_url,
            output_root=scope.data_dir,
            writer_lock=scope.writer_lock,
            storage_guard=scope.storage_guard,
            expected_sha256=proof["sha256"],
            expected_size=proof["size_bytes"],
            max_bytes=100 * 1024 * 1024,
            media_kind=attempt.media_kind,
        )
    except (KeyError, TypeError, media_versions.MediaVersionError) as exc:
        raise GenerationCandidateNotSelectable("Atelier media generation completed bytes are unavailable") from exc


def _is_imported_inert_candidate(storage: AtelierStorage, project: AtelierProject, candidate: dict[str, Any]) -> bool:
    if project.import_provenance is None:
        return False
    if any(key in candidate for key in _STRIP_CANDIDATE_KEYS):
        return False
    video_url = candidate.get("video_url")
    if not isinstance(video_url, str) or not _is_safe_imported_media_ref(video_url, project_id=project.id):
        return False
    scope = storage._media_write_scope()
    try:
        with package_media.media_parent(
            scope.data_dir,
            video_url,
            scope.writer_lock,
            policy=_MEDIA_POLICY,
            error=GenerationCandidateNotFound,
        ) as (parent_fd, name, check):
            linked = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            if not stat.S_ISREG(linked.st_mode):
                return False
            check()
            return True
    except (GenerationCandidateNotFound, OSError):
        return False


def _is_safe_imported_media_ref(value: str, *, project_id: str, media_kind: str = "video") -> bool:
    try:
        ref = package_media.normalize_media_ref(value, policy=_MEDIA_POLICY, error=GenerationCandidateNotFound)
    except GenerationCandidateNotFound:
        return False
    parts = ref.split("/")
    root = {"video": "video", "image": "assets", "audio": "audio"}.get(media_kind)
    return root is not None and len(parts) > 3 and parts[:3] == [root, "imported", project_id]


def _require_completed_video_candidate(candidate: dict[str, Any]) -> None:
    if candidate.get("media_kind") != "video":
        raise GenerationCandidateNotSelectable("Atelier generation candidate is not a video")
    if candidate.get("status") != "completed":
        raise GenerationCandidateNotSelectable("Atelier generation candidate is not completed")
    video_url = candidate.get("video_url")
    if not isinstance(video_url, str) or not video_url:
        raise GenerationCandidateNotSelectable("Atelier generation candidate has no completed media")


def _matching_completed_attempt(
    project: AtelierProject,
    *,
    node_id: str,
    candidate: dict[str, Any],
) -> AtelierGenerationAttempt:
    attempt_id = candidate.get("attempt_id")
    attempt = next((item for item in project.generation_attempts if item.attempt_id == attempt_id), None)
    if attempt is None:
        raise GenerationCandidateNotFound("Atelier generation attempt not found")
    if attempt.project_id != project.id or attempt.node_id != node_id or attempt.candidate_id != candidate.get("id"):
        raise GenerationCandidateNotFound("Atelier generation candidate does not match attempt")
    if attempt.status != "completed" or attempt.output_ref != candidate.get("video_url"):
        raise GenerationCandidateNotSelectable("Atelier generation candidate is not completed")
    return attempt


def _matching_receipt(project: AtelierProject, attempt: AtelierGenerationAttempt) -> AtelierGenerationRequestReceipt:
    receipt = next((item for item in project.generation_request_receipts if any(member.attempt_id == attempt.attempt_id for member in item.receipt_members)), None)
    if receipt is None:
        raise GenerationCandidateNotFound("Atelier generation receipt not found")
    member = next((item for item in receipt.receipt_members if item.attempt_id == attempt.attempt_id), None)
    if (
        member is None
        or receipt.project_id != project.id
        or receipt.node_id != attempt.node_id
        or member.candidate_id != attempt.candidate_id
        or member.execution_fingerprint != attempt.provider_execution.fingerprint
    ):
        raise GenerationCandidateNotFound("Atelier generation receipt does not match attempt")
    return receipt
