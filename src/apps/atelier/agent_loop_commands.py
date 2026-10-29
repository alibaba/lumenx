"""Typed commands for durable local Atelier Agent loop execution."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ...utils.request_identity import fingerprint_request, validate_client_request_id
from .models import AtelierAgentLoopStartContext, AtelierAgentToolCall
from .product_skills import AtelierProductSkillSnapshot

_AGENT_LOOP_START_ENTRYPOINT = "atelier.agent.loop.start.v1"


class StartAgentLoopCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    expected_revision: int = Field(ge=0)
    client_turn_id: str
    user_message: str = ""
    selected_node_id: str | None = Field(default=None, min_length=1)
    planner: Literal["deterministic_core", "model_adapter"] = "deterministic_core"
    skill_name: str | None = Field(default=None, min_length=1, pattern=r"^[a-z][a-z0-9-]{2,63}$")
    max_iterations: int = Field(default=3, ge=1, le=8, strict=True)

    @field_validator("client_turn_id")
    @classmethod
    def _client_turn_id(cls, value: str) -> str:
        return validate_client_request_id(value)


def loop_start_context(
    command: StartAgentLoopCommand,
    *,
    resolved_skill_snapshot: AtelierProductSkillSnapshot | None = None,
) -> AtelierAgentLoopStartContext:
    if command.skill_name is None:
        if resolved_skill_snapshot is not None:
            raise ValueError("atelier_product_skill_unexpected_snapshot")
        return AtelierAgentLoopStartContext(
            schema_version=1,
            user_message=command.user_message,
            selected_node_id=command.selected_node_id,
            planner=command.planner,
            skill_name=None,
            max_iterations=command.max_iterations,
        )
    if resolved_skill_snapshot is None:
        raise ValueError("atelier_product_skill_snapshot_required")
    if resolved_skill_snapshot.name != command.skill_name:
        raise ValueError("atelier_product_skill_snapshot_mismatch")
    return AtelierAgentLoopStartContext(
        schema_version=2,
        user_message=command.user_message,
        selected_node_id=command.selected_node_id,
        planner=command.planner,
        skill_name=command.skill_name,
        max_iterations=command.max_iterations,
        skill_snapshot=resolved_skill_snapshot,
    )


def fingerprint_agent_loop_start(
    command: StartAgentLoopCommand,
    *,
    resolved_skill_snapshot: AtelierProductSkillSnapshot | None = None,
) -> str:
    return fingerprint_request(
        {"client_turn_id": command.client_turn_id, **loop_start_context(command, resolved_skill_snapshot=resolved_skill_snapshot).model_dump(mode="json")},
        entrypoint=_AGENT_LOOP_START_ENTRYPOINT,
    )


class SaveAgentLoopIterationCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    expected_revision: int = Field(ge=0)
    turn_id: str = Field(min_length=1)
    iteration_index: int = Field(ge=1, le=8, strict=True)
    planner: Literal["deterministic_core", "model_adapter"] = "deterministic_core"
    assistant_message: str = ""
    tool_calls: tuple[AtelierAgentToolCall, ...] = Field(default_factory=tuple)
    stop_reason: str | None = Field(default=None, min_length=1)


def iteration_fingerprint(command: SaveAgentLoopIterationCommand) -> dict:
    return {
        "iteration_index": command.iteration_index,
        "planner": command.planner,
        "assistant_message": command.assistant_message,
        "tool_calls": [call.model_dump(mode="json") for call in command.tool_calls],
        "stop_reason": command.stop_reason,
    }


class ResumeAgentLoopCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    turn_id: str

    @field_validator("turn_id")
    @classmethod
    def _turn_id(cls, value: str) -> str:
        return validate_client_request_id(value)
