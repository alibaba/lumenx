"""Validated Studio import receipts, persisted by the existing Studio transaction."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .models import Series
from ...utils.json_storage import CorruptStorageError, read_json
from ...utils.request_identity import fingerprint_request, validate_client_request_id


MAX_IMPORT_TEXT_BYTES = 2 * 1024 * 1024
IMPORT_PREVIEW_TTL_SECONDS = 24 * 60 * 60
MAX_PENDING_IMPORTS = 100


class ImportPreviewUnavailable(ValueError):
    def __init__(self, message: str, status_code: int):
        super().__init__(message)
        self.status_code = status_code


def validate_episodes(episodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not 1 <= len(episodes) <= 50:
        raise ValueError("An import needs 1-50 episodes")
    numbers = set()
    for episode in episodes:
        number = episode.get("episode_number")
        if type(number) is not int or number < 1 or number in numbers:
            raise ValueError("Episode numbers must be unique positive integers")
        numbers.add(number)
        if not isinstance(episode.get("title"), str) or not episode["title"].strip():
            raise ValueError("Every episode needs a title")
        for field in ("start_marker", "end_marker", "summary", "estimated_duration"):
            if field in episode and not isinstance(episode[field], str):
                raise ValueError(f"Episode {field} must be text")
    return episodes


class ConfirmImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str
    description: str = ""
    import_id: str = Field(description="Preview the file first and provide its import_id; text-only confirmation is unsupported")
    episodes: list[dict[str, Any]]

    @field_validator("import_id")
    @classmethod
    def valid_import_id(cls, value):
        try:
            return validate_client_request_id(value)
        except ValueError as exc:
            raise ValueError("Preview the file first and provide a valid import_id") from exc

    @field_validator("title")
    @classmethod
    def valid_title(cls, value):
        if not value.strip():
            raise ValueError("Series title cannot be empty")
        return value.strip()

    @field_validator("episodes")
    @classmethod
    def valid_episodes(cls, value):
        return validate_episodes(value)


class ImportedEpisode(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: str = Field(min_length=1)
    title: str
    episode_number: int = Field(ge=1)
    text_length: int = Field(ge=0)


class ImportResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    series: Series
    episodes: list[ImportedEpisode] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def relationship_is_consistent(self):
        ids = [episode.id for episode in self.episodes]
        if len(set(ids)) != len(ids) or self.series.episode_ids != ids:
            raise ValueError("Import result episodes do not match the series")
        return self


class ImportRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    schema_version: Literal[1] = 1
    import_id: str = Field(min_length=1)
    created_at: float
    expires_at: float
    text: str | None
    status: Literal["preview", "completed"] = "preview"
    request_fingerprint: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")] | None = None
    result: ImportResult | None = None

    @model_validator(mode="after")
    def state_is_consistent(self):
        if self.expires_at <= self.created_at:
            raise ValueError("Invalid import expiration")
        if self.status == "preview":
            if not self.text or not self.text.strip() or self.result is not None or self.request_fingerprint is not None:
                raise ValueError("Invalid import preview")
            if len(self.text.encode("utf-8")) > MAX_IMPORT_TEXT_BYTES:
                raise ValueError("Import preview text is too large")
        elif self.text is not None or self.result is None or self.request_fingerprint is None:
            raise ValueError("Invalid completed import receipt")
        return self


def validate_records(value: Any) -> dict[str, ImportRecord]:
    if not isinstance(value, dict):
        raise ValueError("Expected an import receipt map")
    records = {key: ImportRecord.model_validate(item) for key, item in value.items()}
    if any(key != item.import_id for key, item in records.items()):
        raise ValueError("Import receipt key does not match its identity")
    return records


def load_import_records(path) -> dict[str, ImportRecord]:
    try:
        return validate_records(read_json(path, dict))
    except (ValueError, TypeError) as exc:
        raise CorruptStorageError("Invalid import_receipts.json schema; existing data preserved") from exc


def import_fingerprint(title: str, description: str, episodes: list[dict[str, Any]]) -> str:
    return fingerprint_request(
        {"title": title, "description": description, "episodes": episodes},
        entrypoint="studio.series.import.v1", contract_version="request-json-v1",
    )
