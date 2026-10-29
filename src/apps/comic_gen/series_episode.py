"""Small, series-owned creation receipts; project data remains in projects.json."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ...utils.request_identity import fingerprint_request, validate_client_request_id


class CreateSeriesEpisodeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    client_request_id: str
    title: str
    expected_revision: int = Field(ge=0)

    @field_validator("client_request_id")
    @classmethod
    def valid_identity(cls, value):
        return validate_client_request_id(value)

    @field_validator("title")
    @classmethod
    def valid_title(cls, value):
        if not value.strip():
            raise ValueError("Episode title cannot be empty")
        return value.strip()


class SeriesEpisodeCreationReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, allow_inf_nan=False)

    schema_version: Literal[1] = 1
    series_id: str = Field(min_length=1)
    client_request_id: str
    request_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    episode_id: str = Field(min_length=1)
    episode_number: int = Field(ge=1)
    title: str = Field(min_length=1)
    created_at: float = Field(ge=0)

    @field_validator("client_request_id")
    @classmethod
    def valid_identity(cls, value):
        return validate_client_request_id(value)


def episode_creation_fingerprint(series_id: str, request: CreateSeriesEpisodeRequest) -> str:
    return fingerprint_request(
        {"series_id": series_id, **request.model_dump()},
        entrypoint="studio.series.create_episode.v1",
    )
