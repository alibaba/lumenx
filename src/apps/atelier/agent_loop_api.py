"""Thin HTTP routes for durable Atelier Agent loops."""

from __future__ import annotations

from collections.abc import Callable
from threading import Event

from fastapi import APIRouter, HTTPException, Request

from .agent_canvas_commands import RecordAgentCanvasApprovalCommand
from .agent_loop import AgentLoopAck, AgentLoopNotFound
from .agent_loop_commands import ResumeAgentLoopCommand, StartAgentLoopCommand
from .agent_stream_http import AgentEventStreamResponse
from .storage import AtelierProjectNotFound
from .product_skills import list_product_skill_catalog
from ...models.provider_recovery import ProviderRecoveryError
from ...utils.json_storage import StorageConflictError, StorageError

router = APIRouter(tags=["atelier"])


@router.get("/agent/product_skills")
def list_agent_product_skills():
    return list_product_skill_catalog()


@router.post("/projects/{project_id}/agent/loops", response_model=AgentLoopAck)
def start_agent_loop(project_id: str, payload: StartAgentLoopCommand, request: Request) -> AgentLoopAck:
    return _call_loop(lambda: _loop(request).start_loop(project_id, payload))


@router.post("/projects/{project_id}/agent/loops/stream")
def stream_agent_loop(project_id: str, payload: StartAgentLoopCommand, request: Request):
    service = _loop(request)
    cancel = Event()
    return AgentEventStreamResponse(
        service.iter_start_loop(project_id, payload, cancel_event=cancel), cancel,
        project_id=project_id, turn_id=payload.client_turn_id,
    )


@router.get("/projects/{project_id}/agent/loops/{turn_id}", response_model=AgentLoopAck)
def read_agent_loop(project_id: str, turn_id: str, request: Request) -> AgentLoopAck:
    return _call_loop(lambda: _loop(request).read_loop(project_id, turn_id))


@router.post("/projects/{project_id}/agent/loops/{turn_id}/resume", response_model=AgentLoopAck)
def resume_agent_loop(project_id: str, turn_id: str, payload: ResumeAgentLoopCommand, request: Request) -> AgentLoopAck:
    _require_turn_identity(turn_id, payload.turn_id)
    return _call_loop(lambda: _loop(request).resume_loop(project_id, payload.turn_id))


@router.post("/projects/{project_id}/agent/loops/{turn_id}/resume/stream")
def stream_resume_agent_loop(project_id: str, turn_id: str, payload: ResumeAgentLoopCommand, request: Request):
    _require_turn_identity(turn_id, payload.turn_id)
    service = _loop(request)
    cancel = Event()
    return AgentEventStreamResponse(
        service.iter_resume_loop(project_id, payload.turn_id, cancel_event=cancel), cancel,
        project_id=project_id, turn_id=turn_id,
    )


@router.post("/projects/{project_id}/agent/loops/{turn_id}/canvas_approval", response_model=AgentLoopAck)
def record_loop_canvas_approval(
    project_id: str, turn_id: str, payload: RecordAgentCanvasApprovalCommand, request: Request,
) -> AgentLoopAck:
    _require_turn_identity(turn_id, payload.turn_id)

    def call() -> AgentLoopAck:
        service = _loop(request)
        before = service.storage.get_project(project_id)
        existing = next((item for item in before.agent_turns if item.id == payload.turn_id), None)
        if existing is None or existing.loop_start_context is None:
            raise StorageConflictError("atelier_agent_loop_turn_missing")
        project = service.storage.record_agent_canvas_approval(project_id, payload, decided_at=service._clock())
        turn = next((item for item in project.agent_turns if item.id == payload.turn_id), None)
        if turn is None or turn.loop_start_context is None:
            raise StorageConflictError("atelier_agent_loop_turn_missing")
        return service.read_loop(project_id, payload.turn_id, replayed=False, events=[{
            "type": "canvas_approval_recorded", "turn_id": payload.turn_id,
            "call_id": payload.call_id, "approval_id": payload.approval_id, "decision": payload.decision,
        }])

    return _call_loop(call)


def _loop(request: Request):
    service = getattr(request.app.state, "atelier_agent_loop_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail={
            "code": "atelier_agent_loop_unavailable", "detail": "Atelier Agent loop service is unavailable",
        })
    return service


def _require_turn_identity(path_id: str, body_id: str) -> None:
    if path_id != body_id:
        raise HTTPException(status_code=422, detail={
            "code": "atelier_agent_identity_mismatch", "detail": "Agent turn identity does not match the route",
        })


def _call_loop(call: Callable[[], object]):
    try:
        return call()
    except (AtelierProjectNotFound, AgentLoopNotFound) as exc:
        raise HTTPException(status_code=404, detail={
            "code": "atelier_agent_not_found", "detail": "Atelier project or Agent loop turn not found",
        }) from exc
    except StorageConflictError as exc:
        raise HTTPException(status_code=409, detail={
            "code": "atelier_agent_loop_conflict", "detail": "Atelier Agent loop state changed; read the saved turn before retrying",
        }) from exc
    except StorageError:
        raise
    except ProviderRecoveryError as exc:
        unavailable = exc.reason in {"provider_recovery_runtime_missing", "provider_secret_unavailable"}
        raise HTTPException(status_code=503 if unavailable else 422, detail={
            "code": "atelier_agent_runtime_unavailable" if unavailable else "atelier_agent_request_invalid",
            "detail": "Agent generation runtime is unavailable" if unavailable else "Invalid Agent generation request",
        }) from exc
    except ValueError as exc:
        code = str(exc)
        typed = {
            "atelier_agent_loop_skill_requires_model_adapter",
            "atelier_product_skill_unknown",
            "atelier_product_skill_disabled",
            "atelier_product_skill_tools_unavailable",
        }
        raise HTTPException(status_code=422, detail={
            "code": code if code in typed else "atelier_agent_request_invalid",
            "detail": "Agent loop request could not be prepared",
        }) from exc
