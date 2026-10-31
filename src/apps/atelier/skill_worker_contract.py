"""Isolated Skill worker job contract.

This slice defines the message boundary only. A worker implementation must
consume this contract in a separate process/container and may not inherit the
API process environment or user data root.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .product_skills import AtelierProductSkillSnapshot
from .skill_runtime import SkillExecutionBinding, bind_skill_to_video_request


class SkillWorkerJob(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(1, strict=True)
    job_id: str = Field(min_length=1, max_length=128)
    user_id: str = Field(min_length=1, max_length=128)
    project_id: str = Field(min_length=1, max_length=128)
    skill_binding: SkillExecutionBinding
    request_payload: dict[str, Any]
    allowed_tools: tuple[str, ...] = ()
    network_access: Literal["disabled"] = "disabled"
    credential_ref: None = None
    status: Literal["queued", "running", "succeeded", "failed", "cancelled"] = "queued"

    @model_validator(mode="after")
    def _safe_boundary(self) -> "SkillWorkerJob":
        forbidden = {"script", "command", "path", "secret", "credential_ref", "provider_handle"}
        if forbidden.intersection(self.request_payload):
            raise ValueError("skill_worker_request_contains_forbidden_fields")
        return self


def build_skill_worker_job(
    *,
    job_id: str,
    user_id: str,
    project_id: str,
    snapshot: AtelierProductSkillSnapshot,
    request_payload: dict[str, Any],
) -> SkillWorkerJob:
    binding = bind_skill_to_video_request(snapshot, request_payload)
    return SkillWorkerJob(
        job_id=job_id,
        user_id=user_id,
        project_id=project_id,
        skill_binding=binding,
        request_payload=request_payload,
        allowed_tools=tuple(snapshot.expected_tools),
    )

