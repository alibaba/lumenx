"""Typed commands for Atelier Agent turn entrypoints."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ...utils.request_identity import validate_client_request_id


class StartAgentTurnCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    expected_revision: int = Field(ge=0)
    client_turn_id: str
    user_message: str = ""
    selected_node_id: str | None = Field(default=None, min_length=1)

    @field_validator("client_turn_id")
    @classmethod
    def _client_turn_id(cls, value: str) -> str:
        return validate_client_request_id(value)


class CancelAgentTurnCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    turn_id: str = Field(min_length=1, max_length=128)
