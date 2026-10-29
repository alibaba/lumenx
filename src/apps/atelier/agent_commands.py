"""Strict identities for persistent Agent approval commands."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ...utils.request_identity import validate_client_request_id


class AgentCallIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    expected_revision: int = Field(ge=0)
    turn_id: str = Field(min_length=1)
    call_id: str = Field(min_length=1)
    approval_id: str = Field(min_length=1)
    authorization_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class AdmitApprovedAgentGenerationCommand(AgentCallIdentity):
    client_request_id: str

    @field_validator("client_request_id")
    @classmethod
    def _request_id(cls, value: str) -> str:
        return validate_client_request_id(value)


class RecordAgentApprovalCommand(AgentCallIdentity):
    decision: Literal["approved", "denied", "revoked"]
