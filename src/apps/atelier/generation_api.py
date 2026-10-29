"""FastAPI routes for durable Atelier provider generation."""

from __future__ import annotations

from typing import Callable, TypeVar

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from .generation_admission import GenerationAdmissionAck, GenerationBatchAdmissionAck, GenerationRequestNotFound
from .candidate_branch import (
    BranchCandidateCommand,
    CandidateBranchNotFound,
    CandidateBranchNotSelectable,
)
from .candidate_judgment import CandidateJudgmentCommand, CandidateJudgmentNotFound, CandidateJudgmentNotReviewable
from .generation_commands import ManualImageCandidateBatchCommand, ManualOrderedVideoCandidateBatchCommand, ManualVideoCandidateBatchCommand
from .generation_draft import AudioGenerationDraftCommand, GenerationDraftCommand, ImageGenerationDraftCommand, OrderedGenerationDraftCommand
from .generation_execution import GenerationAttemptAck
from .media_generation_admission import MediaGenerationAdmissionAck, MediaGenerationRequestNotFound
from .media_generation_execution import MediaGenerationAttemptAck
from .generation_selection import GenerationCandidateNotFound, SelectVideoCandidateCommand, SetVideoCandidateVisibilityCommand
from .nodes import NodeCommandAck
from .tts_commands import BuiltinTTSVoice, TTSCandidateCommand, list_builtin_tts_voices
from .storage import AtelierProjectNotFound
from ...models.provider_media_runtime import MediaRuntimeAuthorityError
from ...models.provider_recovery import ProviderRecoveryError
from ...models.provider_tasks import ProviderBindingError
from ...utils.json_storage import StorageError


router = APIRouter(tags=["atelier"])

_ServiceResult = TypeVar("_ServiceResult")


_PROVIDER_UNAVAILABLE_REASONS = {"provider_recovery_runtime_missing", "provider_secret_unavailable"}
_PROVIDER_UNAVAILABLE_VALUE_ERRORS = {"atelier_provider_recovery_runtime_missing"}


def _generation_unavailable() -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={"code": "atelier_generation_unavailable", "detail": "Atelier generation runtime is unavailable"},
    )


class GenerationAttemptIdentityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    candidate_id: str = Field(min_length=1)
    expected_attempt_revision: int = Field(ge=0)
    execution_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


@router.get("/voices", response_model=list[BuiltinTTSVoice])
def get_builtin_voices():
    return list_builtin_tts_voices()


@router.put("/projects/{project_id}/nodes/{node_id}/generation_draft", response_model=NodeCommandAck)
def update_generation_draft(
    project_id: str,
    node_id: str,
    payload: GenerationDraftCommand | OrderedGenerationDraftCommand | ImageGenerationDraftCommand | AudioGenerationDraftCommand,
    request: Request,
) -> NodeCommandAck:
    return _call_service(lambda: _draft(request).update_generation_draft(project_id, node_id, payload))


@router.post("/projects/{project_id}/nodes/{node_id}/video_candidates", response_model=GenerationAdmissionAck | GenerationBatchAdmissionAck)
def create_video_candidate(
    project_id: str,
    node_id: str,
    payload: ManualVideoCandidateBatchCommand | ManualOrderedVideoCandidateBatchCommand,
    request: Request,
) -> GenerationAdmissionAck | GenerationBatchAdmissionAck:
    ack = _call_service(lambda: _admission(request).admit(project_id, node_id, payload))
    _schedule_all_or_503(request, project_id, ack)
    return ack


@router.post("/projects/{project_id}/nodes/{node_id}/image_candidates", response_model=MediaGenerationAdmissionAck)
def create_image_candidate(
    project_id: str,
    node_id: str,
    payload: ManualImageCandidateBatchCommand,
    request: Request,
) -> MediaGenerationAdmissionAck:
    ack = _call_service(lambda: _media_admission(request).admit_image(project_id, node_id, payload))
    _schedule_all_or_503(request, project_id, ack)
    return ack


@router.post("/projects/{project_id}/nodes/{node_id}/audio_candidates", response_model=MediaGenerationAdmissionAck)
def create_audio_candidate(
    project_id: str,
    node_id: str,
    payload: TTSCandidateCommand,
    request: Request,
) -> MediaGenerationAdmissionAck:
    ack = _call_service(lambda: _media_admission(request).admit_tts(project_id, node_id, payload))
    _schedule_all_or_503(request, project_id, ack)
    return ack


@router.get("/projects/{project_id}/generation/requests/{client_request_id}", response_model=GenerationAdmissionAck | GenerationBatchAdmissionAck | MediaGenerationAdmissionAck)
def get_generation_request(project_id: str, client_request_id: str, request: Request) -> GenerationAdmissionAck | GenerationBatchAdmissionAck | MediaGenerationAdmissionAck:
    def read():
        storage = _admission(request).storage
        project = storage.get_project(project_id)
        receipt = next((item for item in project.generation_request_receipts if item.client_request_id == client_request_id), None)
        if receipt is None:
            raise GenerationRequestNotFound("Atelier generation request not found")
        if receipt.entrypoint in {"atelier.provider_image.v1", "atelier.provider_audio.v1"}:
            return _media_admission(request).read_request(project_id, client_request_id)
        return _admission(request).read_request(project_id, client_request_id)
    return _call_service(read)


@router.get("/projects/{project_id}/generation/attempts/{attempt_id}", response_model=GenerationAttemptAck | MediaGenerationAttemptAck)
def get_generation_attempt(project_id: str, attempt_id: str, request: Request) -> GenerationAttemptAck | MediaGenerationAttemptAck:
    return _call_service(lambda: _execution(request).read_attempt(project_id, attempt_id))


@router.post("/projects/{project_id}/generation/attempts/{attempt_id}/continue", response_model=GenerationAttemptAck | MediaGenerationAttemptAck)
def continue_generation_attempt(
    project_id: str,
    attempt_id: str,
    payload: GenerationAttemptIdentityRequest,
    request: Request,
) -> GenerationAttemptAck | MediaGenerationAttemptAck:
    return _call_service(
        lambda: _execution(request).continue_attempt(
            project_id,
            attempt_id,
            candidate_id=payload.candidate_id,
            expected_attempt_revision=payload.expected_attempt_revision,
            execution_fingerprint=payload.execution_fingerprint,
        )
    )


@router.post("/projects/{project_id}/generation/attempts/{attempt_id}/cancel", response_model=GenerationAttemptAck | MediaGenerationAttemptAck)
def cancel_generation_attempt(
    project_id: str,
    attempt_id: str,
    payload: GenerationAttemptIdentityRequest,
    request: Request,
) -> GenerationAttemptAck | MediaGenerationAttemptAck:
    return _call_service(
        lambda: _execution(request).cancel_attempt(
            project_id,
            attempt_id,
            candidate_id=payload.candidate_id,
            expected_attempt_revision=payload.expected_attempt_revision,
            execution_fingerprint=payload.execution_fingerprint,
        )
    )


@router.post("/projects/{project_id}/nodes/{node_id}/video_candidates/{candidate_id}/select", response_model=NodeCommandAck)
def select_video_candidate(
    project_id: str,
    node_id: str,
    candidate_id: str,
    payload: SelectVideoCandidateCommand,
    request: Request,
) -> NodeCommandAck:
    return _call_service(lambda: _selection(request).select_video_candidate(project_id, node_id, candidate_id, payload))


@router.post("/projects/{project_id}/nodes/{node_id}/video_candidates/{candidate_id}/visibility", response_model=NodeCommandAck)
def set_video_candidate_visibility(
    project_id: str,
    node_id: str,
    candidate_id: str,
    payload: SetVideoCandidateVisibilityCommand,
    request: Request,
) -> NodeCommandAck:
    return _call_service(
        lambda: _selection(request).set_video_candidate_visibility(project_id, node_id, candidate_id, payload)
    )


@router.post("/projects/{project_id}/nodes/{node_id}/media_candidates/{candidate_id}/select", response_model=NodeCommandAck)
def select_media_candidate(
    project_id: str,
    node_id: str,
    candidate_id: str,
    payload: SelectVideoCandidateCommand,
    request: Request,
) -> NodeCommandAck:
    return _call_service(lambda: _selection(request).select_media_candidate(project_id, node_id, candidate_id, payload))


@router.post("/projects/{project_id}/nodes/{node_id}/media_candidates/{candidate_id}/visibility", response_model=NodeCommandAck)
def set_media_candidate_visibility(
    project_id: str,
    node_id: str,
    candidate_id: str,
    payload: SetVideoCandidateVisibilityCommand,
    request: Request,
) -> NodeCommandAck:
    return _call_service(lambda: _selection(request).set_media_candidate_visibility(project_id, node_id, candidate_id, payload))


@router.put("/projects/{project_id}/nodes/{node_id}/candidates/{candidate_id}/judgment", response_model=NodeCommandAck)
def set_candidate_judgment(
    project_id: str,
    node_id: str,
    candidate_id: str,
    payload: CandidateJudgmentCommand,
    request: Request,
) -> NodeCommandAck:
    return _call_service(lambda: _judgment(request).set_candidate_judgment(project_id, node_id, candidate_id, payload))


@router.post("/projects/{project_id}/nodes/{node_id}/candidates/{candidate_id}/branch", response_model=NodeCommandAck)
def branch_candidate(
    project_id: str,
    node_id: str,
    candidate_id: str,
    payload: BranchCandidateCommand,
    request: Request,
) -> NodeCommandAck:
    return _call_service(lambda: _branch(request).branch_from_candidate(project_id, node_id, candidate_id, payload))


def _new_attempt_ids(ack: GenerationAdmissionAck | GenerationBatchAdmissionAck | MediaGenerationAdmissionAck) -> list[str]:
    members = getattr(ack, "members", None)
    if members is not None:
        new_ids = set(ack.new_candidate_ids)
        return [member.attempt.attempt_id for member in members if member.attempt.candidate_id in new_ids]
    attempts_by_candidate = {attempt.candidate_id: attempt.attempt_id for attempt in ack.project.generation_attempts}
    result: list[str] = []
    for candidate_id in ack.new_candidate_ids:
        attempt_id = attempts_by_candidate.get(candidate_id)
        if attempt_id is not None:
            result.append(attempt_id)
    return result


def _schedule_all_or_503(request: Request, project_id: str, ack: GenerationAdmissionAck | GenerationBatchAdmissionAck | MediaGenerationAdmissionAck) -> None:
    failures: list[dict[str, str]] = []
    scheduled: list[str] = []
    for attempt_id in _new_attempt_ids(ack):
        try:
            _dispatcher(request).schedule(project_id, attempt_id)
            scheduled.append(attempt_id)
        except Exception:
            failures.append({"attempt_id": attempt_id, "error_code": "atelier_generation_dispatch_unavailable"})
    if failures:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "atelier_generation_dispatch_unavailable",
                "detail": "Generation request was saved but one or more attempts could not be scheduled; read it back by request id or continue later",
                "project_id": project_id,
                "attempt_id": failures[0]["attempt_id"],
                "client_request_id": ack.receipt.client_request_id,
                "replayed": ack.replayed,
                "new_candidate_ids": ack.new_candidate_ids,
                "members": [
                    {"candidate_id": member.candidate_id, "attempt_id": member.attempt_id}
                    for member in ack.receipt.receipt_members
                ],
                "scheduled_attempt_ids": scheduled,
                "failed_attempts": failures,
            },
        )


def _admission(request: Request):
    service = getattr(request.app.state, "atelier_generation_admission", None)
    if service is None:
        raise HTTPException(
            status_code=503,
            detail={"code": "atelier_generation_unavailable", "detail": "Atelier generation admission service is unavailable"},
        )
    return service


def _media_admission(request: Request):
    service = getattr(request.app.state, "atelier_media_generation_admission", None)
    if service is None:
        raise HTTPException(
            status_code=503,
            detail={"code": "atelier_generation_unavailable", "detail": "Atelier media generation admission service is unavailable"},
        )
    return service


def _execution(request: Request):
    service = getattr(request.app.state, "atelier_generation_execution", None)
    if service is None:
        raise HTTPException(
            status_code=503,
            detail={"code": "atelier_generation_unavailable", "detail": "Atelier generation execution service is unavailable"},
        )
    return service


def _draft(request: Request):
    service = getattr(request.app.state, "atelier_generation_draft", None)
    if service is None:
        raise HTTPException(
            status_code=503,
            detail={"code": "atelier_generation_unavailable", "detail": "Atelier generation draft service is unavailable"},
        )
    return service


def _selection(request: Request):
    service = getattr(request.app.state, "atelier_generation_selection", None)
    if service is None:
        raise HTTPException(
            status_code=503,
            detail={"code": "atelier_generation_unavailable", "detail": "Atelier generation selection service is unavailable"},
        )
    return service


def _judgment(request: Request):
    service = getattr(request.app.state, "atelier_candidate_judgment", None)
    if service is None:
        raise HTTPException(
            status_code=503,
            detail={"code": "atelier_generation_unavailable", "detail": "Atelier candidate judgment service is unavailable"},
        )
    return service


def _branch(request: Request):
    service = getattr(request.app.state, "atelier_candidate_branch", None)
    if service is None:
        raise HTTPException(
            status_code=503,
            detail={"code": "atelier_generation_unavailable", "detail": "Atelier candidate branch service is unavailable"},
        )
    return service


def _dispatcher(request: Request):
    dispatcher = getattr(request.app.state, "atelier_generation_dispatcher", None)
    if dispatcher is None:
        raise HTTPException(
            status_code=503,
            detail={"code": "atelier_generation_unavailable", "detail": "Atelier generation dispatcher is unavailable"},
        )
    return dispatcher


def _call_service(call: Callable[[], _ServiceResult]) -> _ServiceResult:
    try:
        return call()
    except (GenerationRequestNotFound, MediaGenerationRequestNotFound) as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "atelier_generation_request_not_found", "detail": "Atelier generation request not found"},
        ) from exc
    except AtelierProjectNotFound as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "atelier_project_not_found", "detail": "Atelier project not found"},
        ) from exc
    except (GenerationCandidateNotFound, CandidateBranchNotFound) as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "atelier_generation_candidate_not_found", "detail": "Atelier generation candidate not found"},
        ) from exc
    except CandidateBranchNotSelectable as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "atelier_generation_candidate_not_selectable", "detail": "Atelier generation candidate cannot be branched"},
        ) from exc
    except CandidateJudgmentNotFound as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "atelier_candidate_judgment_not_found", "detail": "Atelier candidate judgment target not found"},
        ) from exc
    except CandidateJudgmentNotReviewable as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "atelier_candidate_judgment_not_reviewable", "detail": "Atelier candidate judgment target is not reviewable"},
        ) from exc
    except ProviderRecoveryError as exc:
        if exc.reason in _PROVIDER_UNAVAILABLE_REASONS:
            raise _generation_unavailable() from exc
        raise HTTPException(status_code=422, detail="Invalid Atelier generation request") from exc
    except (MediaRuntimeAuthorityError, ProviderBindingError) as exc:
        raise _generation_unavailable() from exc
    except ValueError as exc:
        reason = str(exc)
        if reason == "atelier_generation_attempt_not_found":
            raise HTTPException(
                status_code=404,
                detail={"code": "atelier_generation_attempt_not_found", "detail": "Atelier generation attempt not found"},
            ) from exc
        if reason in _PROVIDER_UNAVAILABLE_VALUE_ERRORS:
            raise _generation_unavailable() from exc
        raise HTTPException(status_code=422, detail="Invalid Atelier generation request") from exc
    except StorageError:
        raise
