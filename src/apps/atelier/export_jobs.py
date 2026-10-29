"""Pure Atelier sequence export job DTOs and request identity helpers."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .models import AtelierProject, AtelierSequenceExportJob, ExportRecord

REQUEST_CONTRACT_VERSION = "atelier-sequence-export-v1"
REDO_REQUEST_CONTRACT_VERSION = "atelier-sequence-export-redo-v1"
_CLIENT_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


class CreateSequenceExportJob(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(..., ge=0, strict=True)
    client_request_id: str = Field(..., min_length=1, max_length=128)
    entries: list[Any] | None = None
    audio_tracks: list[Any] | None = None

    @field_validator("client_request_id")
    @classmethod
    def _client_request_id(cls, value: str) -> str:
        if _CLIENT_REQUEST_ID_PATTERN.fullmatch(value) is None:
            raise ValueError("Invalid client_request_id")
        return value

    @model_validator(mode="after")
    def _reject_explicit_null_lists(self) -> "CreateSequenceExportJob":
        if "entries" in self.model_fields_set and self.entries is None:
            raise ValueError("entries must be omitted or a list")
        if "audio_tracks" in self.model_fields_set and self.audio_tracks is None:
            raise ValueError("audio_tracks must be omitted or a list")
        return self


class RedoSequenceExportJob(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(..., ge=0, strict=True)
    client_request_id: str = Field(..., min_length=1, max_length=128)

    @field_validator("client_request_id")
    @classmethod
    def _client_request_id(cls, value: str) -> str:
        if _CLIENT_REQUEST_ID_PATTERN.fullmatch(value) is None:
            raise ValueError("Invalid client_request_id")
        return value


class SequenceExportJobAck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project: AtelierProject
    job: AtelierSequenceExportJob
    export_record: ExportRecord | None = None
    replayed: bool = False
    new_job_ids: list[str] = Field(default_factory=list)


def fingerprint_sequence_export_request(command: CreateSequenceExportJob) -> str:
    fields: dict[str, Any] = {}
    for name in command.__class__.model_fields:
        if name in {"expected_revision", "client_request_id"}:
            continue
        value = getattr(command, name)
        fields[name] = {
            "present": name in command.model_fields_set,
            "value": _json_value(value),
        }
    payload = {
        "request_contract_version": REQUEST_CONTRACT_VERSION,
        "fields": fields,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def fingerprint_sequence_export_redo_request(command: RedoSequenceExportJob, *, source_export_id: str) -> str:
    payload = {
        "request_contract_version": REDO_REQUEST_CONTRACT_VERSION,
        "source_export_id": source_export_id,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _json_value(value: Any) -> Any:
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value
