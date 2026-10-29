"""Durable planner invocation claim and settlement primitives."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ...utils.request_identity import fingerprint_request, validate_client_request_id
from .models import (
    AtelierAgentPlannerConfigIdentity,
    AtelierAgentPlannerInvocation,
    AtelierAgentPlannerInvocationUsage,
    AtelierAgentPlannerMessage,
)

_CLAIM_ENTRYPOINT = "atelier.agent.planner.invocation.claim.v1"
_SETTLE_ENTRYPOINT = "atelier.agent.planner.invocation.settle.v1"


class ClaimAgentPlannerInvocationCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    expected_revision: int = Field(ge=0, strict=True)
    turn_id: str = Field(min_length=1)
    iteration_index: int = Field(ge=1, le=8, strict=True)
    claim_request_id: str
    planner: Literal["model_adapter"] = "model_adapter"
    provider_id: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    config_identity: AtelierAgentPlannerConfigIdentity
    input_messages: tuple[AtelierAgentPlannerMessage, ...] = Field(min_length=1)
    max_completion_tokens: int = Field(ge=1, le=16384, strict=True)
    timeout_seconds: float = Field(gt=0, allow_inf_nan=False)

    @field_validator("claim_request_id")
    @classmethod
    def _claim_request_id(cls, value: str) -> str:
        return validate_client_request_id(value)


class SettleAgentPlannerInvocationCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    invocation_id: str = Field(min_length=1)
    outcome: Literal["success", "failed", "timeout_unknown"]
    result_content: str | None = None
    provider_model: str | None = Field(default=None, min_length=1)
    usage: AtelierAgentPlannerInvocationUsage | None = None
    error_code: str | None = Field(default=None, min_length=1, max_length=128)
    error_message: str | None = Field(default=None, min_length=1, max_length=500)

    @model_validator(mode="after")
    def _validate_outcome_fields(self) -> "SettleAgentPlannerInvocationCommand":
        if self.outcome == "success":
            if not isinstance(self.result_content, str) or not self.result_content.strip():
                raise ValueError("successful planner settlement requires non-empty content")
            if self.error_code is not None or self.error_message is not None:
                raise ValueError("successful planner settlement cannot carry error fields")
        elif self.result_content is not None:
            raise ValueError("failed planner settlement cannot carry success content")
        return self


@dataclass(frozen=True)
class PlannerInvocationClaimAck:
    invocation: AtelierAgentPlannerInvocation
    claimed: bool


@dataclass(frozen=True)
class PlannerInvocationSettleAck:
    invocation: AtelierAgentPlannerInvocation
    settled: bool


def claim_input_fingerprint(command: ClaimAgentPlannerInvocationCommand) -> str:
    return fingerprint_request(
        {
            "messages": [message.model_dump(mode="json") for message in command.input_messages],
        },
        entrypoint=f"{_CLAIM_ENTRYPOINT}.input",
    )


def claim_config_fingerprint(command: ClaimAgentPlannerInvocationCommand) -> str:
    return fingerprint_request(
        {
            "provider_id": command.provider_id,
            "model_id": command.model_id,
            "config_identity": command.config_identity.model_dump(mode="json"),
            "max_completion_tokens": command.max_completion_tokens,
            "timeout_seconds": command.timeout_seconds,
        },
        entrypoint=f"{_CLAIM_ENTRYPOINT}.config",
    )


def claim_fingerprint(command: ClaimAgentPlannerInvocationCommand) -> str:
    return fingerprint_request(
        {
            "turn_id": command.turn_id,
            "iteration_index": command.iteration_index,
            "claim_request_id": command.claim_request_id,
            "planner": command.planner,
            "provider_id": command.provider_id,
            "model_id": command.model_id,
            "config_identity": command.config_identity.model_dump(mode="json"),
            "input_messages": [message.model_dump(mode="json") for message in command.input_messages],
            "max_completion_tokens": command.max_completion_tokens,
            "timeout_seconds": command.timeout_seconds,
        },
        entrypoint=_CLAIM_ENTRYPOINT,
    )


def settlement_fingerprint(command: SettleAgentPlannerInvocationCommand) -> str:
    return fingerprint_request(
        {
            "invocation_id": command.invocation_id,
            "outcome": command.outcome,
            "result_content": command.result_content,
            "provider_model": command.provider_model,
            "usage": command.usage.model_dump(mode="json") if command.usage is not None else None,
            "error_code": command.error_code,
            "error_message": command.error_message,
        },
        entrypoint=_SETTLE_ENTRYPOINT,
    )
