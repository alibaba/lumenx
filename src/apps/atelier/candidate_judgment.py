"""Append-only human judgments for Atelier generation candidates."""

from __future__ import annotations

import time
import uuid
from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field

from .generation_ledger import GENERATION_CANDIDATE_CONTRACT, MEDIA_GENERATION_CANDIDATE_CONTRACT
from .generation_selection import (
    _is_imported_inert_candidate,
    _is_imported_inert_media_candidate,
    _matching_completed_attempt,
    _matching_media_attempt,
    _matching_media_receipt,
    _matching_receipt,
)
from .models import AtelierCandidateJudgment, AtelierNode, AtelierProject
from .nodes import NodeCommandAck
from .storage import AtelierStorage


class CandidateJudgmentCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    expected_revision: int = Field(..., ge=0, strict=True)
    verdict: Literal["unreviewed", "keep", "reject"]
    reason: str = Field("", max_length=2000, strict=True)


class CandidateJudgmentNotFound(ValueError):
    """The requested candidate does not exist on a live node."""


class CandidateJudgmentNotReviewable(ValueError):
    """The requested candidate exists but cannot receive a new judgment."""


class AtelierCandidateJudgmentService:
    """Record independent user judgment without selecting or mutating a take."""

    def __init__(
        self,
        storage: AtelierStorage,
        *,
        clock: Callable[[], float] = time.time,
        id_factory: Callable[[], str] | None = None,
    ):
        self.storage = storage
        self._clock = clock
        self._id_factory = id_factory or (lambda: str(uuid.uuid4()))

    def set_candidate_judgment(
        self,
        project_id: str,
        node_id: str,
        candidate_id: str,
        command: CandidateJudgmentCommand,
    ) -> NodeCommandAck:
        now = self._clock()

        def apply(project: AtelierProject) -> None:
            before = _project_without_judgment_fields(project)
            node, candidate = _find_live_reviewable_candidate(self.storage, project, node_id, candidate_id)
            media_kind = _candidate_media_kind(node, candidate)
            previous = _latest_judgment(project, node_id=node_id, candidate_id=candidate_id)
            if previous is None and command.verdict == "unreviewed" and command.reason == "":
                return
            if previous is not None and previous.verdict == command.verdict and previous.reason == command.reason:
                return
            judgment = AtelierCandidateJudgment(
                schema_version=1,
                id=self._id_factory(),
                project_id=project.id,
                node_id=node_id,
                candidate_id=candidate_id,
                media_kind=media_kind,
                verdict=command.verdict,
                reason=command.reason,
                created_at=now,
                supersedes_judgment_id=previous.id if previous is not None else None,
            )
            project.candidate_judgments.append(judgment)
            project.updated_at = now
            _assert_only_judgment_changed(before, project)

        project = self.storage.apply_project_command(
            project_id,
            expected_revision=command.expected_revision,
            command=apply,
        )
        return NodeCommandAck(project=project, node_ids=[node_id])


def _find_live_reviewable_candidate(
    storage: AtelierStorage,
    project: AtelierProject,
    node_id: str,
    candidate_id: str,
) -> tuple[AtelierNode, dict[str, Any]]:
    node = next((item for item in project.nodes if item.id == node_id), None)
    if node is None or node.type not in {"image", "video", "audio"}:
        raise CandidateJudgmentNotFound("Atelier candidate judgment owner not found")
    data = node.data if isinstance(node.data, dict) else {}
    hidden = data.get("deleted_candidates", [])
    if isinstance(hidden, list) and any(isinstance(item, dict) and item.get("id") == candidate_id for item in hidden):
        raise CandidateJudgmentNotReviewable("Hidden Atelier candidate judgment is readonly")
    candidates = data.get("candidates", [])
    if not isinstance(candidates, list):
        raise CandidateJudgmentNotFound("Atelier candidate judgment candidate not found")
    matches = [item for item in candidates if isinstance(item, dict) and item.get("id") == candidate_id]
    if len(matches) != 1:
        raise CandidateJudgmentNotFound("Atelier candidate judgment candidate not found")
    candidate = dict(matches[0])
    _assert_reviewable_candidate(storage, project, node, candidate)
    return node, candidate


def _assert_reviewable_candidate(
    storage: AtelierStorage,
    project: AtelierProject,
    node: AtelierNode,
    candidate: dict[str, Any],
) -> None:
    if node.type == "video":
        if candidate.get("media_kind", "video") != "video":
            raise CandidateJudgmentNotReviewable("Atelier candidate judgment media kind mismatch")
        if candidate.get("status") != "completed" or not isinstance(candidate.get("video_url"), str) or not candidate.get("video_url"):
            raise CandidateJudgmentNotReviewable("Atelier candidate judgment requires completed media")
        if candidate.get("execution_contract") == GENERATION_CANDIDATE_CONTRACT:
            attempt = _matching_completed_attempt(project, node_id=node.id, candidate=candidate)
            _matching_receipt(project, attempt)
            return
        if _is_imported_inert_candidate(storage, project, candidate):
            return
        raise CandidateJudgmentNotFound("Atelier candidate judgment candidate not found")
    if candidate.get("execution_contract") != MEDIA_GENERATION_CANDIDATE_CONTRACT:
        raise CandidateJudgmentNotFound("Atelier candidate judgment media candidate not found")
    if candidate.get("media_kind") != node.type:
        raise CandidateJudgmentNotReviewable("Atelier candidate judgment media kind mismatch")
    if candidate.get("status") != "completed" or not isinstance(candidate.get("media_url"), str) or not candidate.get("media_url"):
        raise CandidateJudgmentNotReviewable("Atelier candidate judgment requires completed media")
    if candidate.get("authority") == "imported":
        if _is_imported_inert_media_candidate(storage, project, node, candidate):
            return
        raise CandidateJudgmentNotFound("Atelier imported media candidate is not a safe copied artifact")
    if candidate.get("authority") != "provider":
        raise CandidateJudgmentNotFound("Atelier candidate judgment media candidate authority is invalid")
    attempt = _matching_media_attempt(project, node_id=node.id, candidate=candidate, require_completed=True)
    _matching_media_receipt(project, attempt)


def _candidate_media_kind(node: AtelierNode, candidate: dict[str, Any]) -> Literal["image", "video", "audio"]:
    if node.type == "image":
        return "image"
    if node.type == "audio":
        return "audio"
    media_kind = candidate.get("media_kind", "video")
    if media_kind != "video":
        raise CandidateJudgmentNotReviewable("Atelier candidate judgment media kind mismatch")
    return "video"


def _latest_judgment(
    project: AtelierProject,
    *,
    node_id: str,
    candidate_id: str,
) -> AtelierCandidateJudgment | None:
    result: AtelierCandidateJudgment | None = None
    for judgment in project.candidate_judgments:
        if judgment.node_id == node_id and judgment.candidate_id == candidate_id:
            result = judgment
    return result


def _project_without_judgment_fields(project: AtelierProject) -> dict[str, Any]:
    payload = project.model_dump(mode="json")
    payload.pop("candidate_judgments", None)
    payload.pop("updated_at", None)
    return payload


def _assert_only_judgment_changed(before: dict[str, Any], project: AtelierProject) -> None:
    after = _project_without_judgment_fields(project)
    if before != after:
        raise CandidateJudgmentNotReviewable("Atelier candidate judgment cannot change other project state")
