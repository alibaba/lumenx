"""Thin read-only planning and durable Agent turn routes."""

from __future__ import annotations

from typing import Any, Callable
from threading import Event

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from .models import AtelierAgentPlan, AtelierAgentPlannerPackage
from .agent_commands import RecordAgentApprovalCommand, AdmitApprovedAgentGenerationCommand
from .agent_turn_commands import StartAgentTurnCommand, CancelAgentTurnCommand
from .agent_executor import AgentStartRejected, AgentTurnAck, AgentTurnNotFound
from .agent_stream_http import AgentEventStreamResponse
from ...models.provider_recovery import ProviderRecoveryError
from .storage import AtelierProjectNotFound
from ...utils.json_storage import StorageError


router = APIRouter(tags=["atelier"])


class BuildAtelierAgentPlannerPackageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    user_message: str = ""
    selected_node_id: str | None = Field(default=None, min_length=1)
    skill_name: str | None = Field(default=None, min_length=1)


class PlanAtelierAgentTurnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    user_message: str = ""
    selected_node_id: str | None = Field(default=None, min_length=1)
    skill_name: str | None = Field(default=None, min_length=1)
    planner: str | None = Field(default=None, min_length=1)
    planner_input: dict[str, Any] = Field(default_factory=dict)


@router.get("/projects/{project_id}/agent/tools")
def list_agent_tools(project_id: str, request: Request) -> list[dict[str, Any]]:
    return _call_service(lambda: _agent(request).list_tools(project_id))


@router.post("/projects/{project_id}/agent/planner_package", response_model=AtelierAgentPlannerPackage)
def build_agent_planner_package(
    project_id: str,
    payload: BuildAtelierAgentPlannerPackageRequest,
    request: Request,
) -> AtelierAgentPlannerPackage:
    return _call_service(
        lambda: _agent(request).build_planner_package(
            project_id,
            user_message=payload.user_message,
            selected_node_id=payload.selected_node_id,
            skill_name=payload.skill_name,
        )
    )


@router.post("/projects/{project_id}/agent/plan", response_model=AtelierAgentPlan)
def plan_agent_turn(
    project_id: str,
    payload: PlanAtelierAgentTurnRequest,
    request: Request,
) -> AtelierAgentPlan:
    return _call_service(
        lambda: _agent(request).plan(
            project_id,
            user_message=payload.user_message,
            selected_node_id=payload.selected_node_id,
            skill_name=payload.skill_name,
            planner=payload.planner,
            planner_input=payload.planner_input,
        )
    )


def _agent(request: Request):
    service = getattr(request.app.state, "atelier_agent_service", None)
    if service is None:
        raise HTTPException(
            status_code=503,
            detail={"code": "atelier_agent_unavailable", "detail": "Atelier Agent planner service is unavailable"},
        )
    return service


def _call_service(call: Callable[[], object]):
    try:
        return call()
    except AtelierProjectNotFound as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "atelier_project_not_found", "detail": "Atelier project not found"},
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except StorageError:
        raise


@router.post("/projects/{project_id}/agent/turns", response_model=AgentTurnAck)
def start_agent_turn(project_id: str, payload: StartAgentTurnCommand, request: Request) -> AgentTurnAck:
    return _call_executor(lambda: _executor(request).start_turn(project_id, payload))


@router.post("/projects/{project_id}/agent/turns/stream")
def stream_agent_turn(project_id: str, payload: StartAgentTurnCommand, request: Request):
    executor = _executor(request)
    cancel = Event()
    return AgentEventStreamResponse(
        executor.iter_start_turn(project_id, payload, cancel_event=cancel), cancel,
        project_id=project_id, turn_id=payload.client_turn_id,
    )


@router.post("/projects/{project_id}/agent/turns/{turn_id}/continue/stream")
def stream_continue_agent_call(
    project_id: str, turn_id: str, payload: AdmitApprovedAgentGenerationCommand, request: Request,
):
    _require_turn_identity(turn_id, payload.turn_id)
    executor = _executor(request)
    cancel = Event()
    return AgentEventStreamResponse(
        executor.iter_continue_call(project_id, payload, cancel_event=cancel), cancel,
        project_id=project_id, turn_id=turn_id,
    )


@router.get("/projects/{project_id}/agent/turns/{turn_id}", response_model=AgentTurnAck)
def read_agent_turn(project_id: str, turn_id: str, request: Request) -> AgentTurnAck:
    return _call_executor(lambda: _executor(request).read_turn(project_id, turn_id))


@router.post("/projects/{project_id}/agent/turns/{turn_id}/approval", response_model=AgentTurnAck)
def approve_agent_call(
    project_id: str, turn_id: str, payload: RecordAgentApprovalCommand, request: Request,
) -> AgentTurnAck:
    _require_turn_identity(turn_id, payload.turn_id)
    return _call_executor(lambda: _executor(request).approve_call(project_id, payload))


@router.post("/projects/{project_id}/agent/turns/{turn_id}/continue", response_model=AgentTurnAck)
def continue_agent_call(
    project_id: str, turn_id: str, payload: AdmitApprovedAgentGenerationCommand, request: Request,
) -> AgentTurnAck:
    _require_turn_identity(turn_id, payload.turn_id)
    return _call_executor(lambda: _executor(request).continue_call(project_id, payload))


@router.post("/projects/{project_id}/agent/turns/{turn_id}/cancel", response_model=AgentTurnAck)
def cancel_agent_turn(
    project_id: str, turn_id: str, payload: CancelAgentTurnCommand, request: Request,
) -> AgentTurnAck:
    _require_turn_identity(turn_id, payload.turn_id)
    return _call_executor(lambda: _executor(request).cancel_turn(project_id, payload))


def _require_turn_identity(path_id: str, body_id: str) -> None:
    if path_id != body_id:
        raise HTTPException(status_code=422, detail={
            "code": "atelier_agent_identity_mismatch", "detail": "Agent turn identity does not match the route",
        })


def _executor(request: Request):
    executor = getattr(request.app.state, "atelier_agent_executor", None)
    if executor is None:
        raise HTTPException(status_code=503, detail={
            "code": "atelier_agent_executor_unavailable", "detail": "Atelier Agent execution service is unavailable",
        })
    return executor


def _call_executor(call: Callable[[], object]):
    try:
        return call()
    except (AtelierProjectNotFound, AgentTurnNotFound) as exc:
        raise HTTPException(status_code=404, detail={
            "code": "atelier_agent_not_found", "detail": "Atelier project or Agent turn not found",
        }) from exc
    except AgentStartRejected as exc:
        raise HTTPException(status_code=422, detail={
            "code": "atelier_agent_start_rejected", "detail": "Agent start request could not be prepared",
        }) from exc
    except ProviderRecoveryError as exc:
        unavailable = exc.reason in {"provider_recovery_runtime_missing", "provider_secret_unavailable"}
        raise HTTPException(status_code=503 if unavailable else 422, detail={
            "code": "atelier_agent_runtime_unavailable" if unavailable else "atelier_agent_request_invalid",
            "detail": "Agent generation runtime is unavailable" if unavailable else "Invalid Agent generation request",
        }) from exc
    except ValueError as exc:
        # Do not expose credential/provider exception strings to the browser.
        raise HTTPException(status_code=422, detail={
            "code": "atelier_agent_request_invalid", "detail": "Agent request could not be prepared",
        }) from exc
