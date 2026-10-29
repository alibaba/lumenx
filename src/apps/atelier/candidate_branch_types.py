"""Strict reusable candidate-branch source provenance types."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


BranchRecipeStatus = Literal[
    "available",
    "unavailable_imported",
    "unavailable_legacy",
    "unavailable_inputs",
    "unsupported",
]

_UPLOAD_PATH_RE = re.compile(r"^uploads/(?:imported/[A-Za-z0-9][A-Za-z0-9._-]*/)*[A-Za-z0-9_-][A-Za-z0-9._-]*$")
_BRANCH_SOURCE_KEYS = {
    "schema_version",
    "origin_project_id",
    "origin_node_id",
    "origin_candidate_id",
    "media_kind",
    "media_url",
    "media_sha256",
    "media_size_bytes",
    "recipe_status",
}


class BranchSource(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal[1]
    origin_project_id: str = Field(..., min_length=1)
    origin_node_id: str = Field(..., min_length=1)
    origin_candidate_id: str = Field(..., min_length=1)
    media_kind: Literal["image", "video", "audio"]
    media_url: str = Field(..., min_length=1)
    media_sha256: str = Field(..., pattern=r"^[0-9a-f]{64}$")
    media_size_bytes: int = Field(..., gt=0, strict=True)
    recipe_status: BranchRecipeStatus

    @field_validator("schema_version", mode="before")
    @classmethod
    def _schema_version(cls, value: int) -> int:
        if type(value) is not int or value != 1:
            raise ValueError("Branch source schema_version must be exact integer 1")
        return value

    @field_validator("origin_project_id", "origin_node_id", "origin_candidate_id", "media_url")
    @classmethod
    def _safe_text(cls, value: str) -> str:
        if value.strip() != value or _contains_surrogate(value):
            raise ValueError("Branch source text fields must be canonical non-surrogate strings")
        return value

    @field_validator("media_url")
    @classmethod
    def _media_url(cls, value: str) -> str:
        if _UPLOAD_PATH_RE.fullmatch(value) is None or "/../" in f"/{value}/" or value.startswith("/"):
            raise ValueError("Branch source media_url must be a managed uploads path")
        return value


def validate_branch_source(value: Any) -> BranchSource:
    if not isinstance(value, dict) or set(value) != _BRANCH_SOURCE_KEYS:
        raise ValueError("Branch source fields are invalid")
    return BranchSource.model_validate(value)


def _contains_surrogate(value: str) -> bool:
    return any(0xD800 <= ord(ch) <= 0xDFFF for ch in value)
