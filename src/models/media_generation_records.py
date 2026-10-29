"""Dormant strict records for media generation candidates.

These models are intentionally not wired to Atelier routes yet. They provide a
neutral validation contract for future image/audio candidate projections without
changing legacy video candidate wire shapes.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


_TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
_NONTERMINAL_STATUSES = {"pending", "processing", "needs_confirmation"}
_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "webp", "bmp"}
_AUDIO_EXTENSIONS = {"mp3", "wav"}


def _valid_candidate_media_url(relative_path: str, media_kind: str, authority: str) -> bool:
    suffix = relative_path.rsplit(".", 1)[-1].lower() if "." in relative_path else ""
    if media_kind == "image":
        roots = ("assets/imported/",) if authority == "imported" else ("assets/atelier/",)
        extensions = _IMAGE_EXTENSIONS
    elif media_kind == "audio":
        roots = ("audio/imported/",) if authority == "imported" else ("audio/atelier/",)
        extensions = _AUDIO_EXTENSIONS
    else:
        return False
    return relative_path.startswith(roots) and suffix in extensions


class MediaGenerationCandidateRecord(BaseModel):
    """Strict v2 candidate projection for future image/audio generation."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[2]
    execution_contract: Literal["atelier-provider-media-v2"]
    id: str = Field(min_length=1)
    media_kind: Literal["image", "audio"]
    status: Literal["pending", "processing", "needs_confirmation", "failed", "cancelled", "completed"]
    media_url: str | None = Field(None, min_length=1)
    error_code: str | None = Field(None, min_length=1, max_length=128)
    authority: Literal["provider", "imported"]
    attempt_id: str | None = Field(None, min_length=1)
    execution_fingerprint: str | None = Field(None, pattern=r"^[0-9a-f]{64}$")

    @field_validator("schema_version", mode="before")
    @classmethod
    def _schema_version(cls, value: int) -> int:
        if type(value) is not int or value != 2:
            raise ValueError("Invalid media candidate schema")
        return value

    @field_validator("media_url")
    @classmethod
    def _media_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if value.strip() != value or value.startswith("/") or ".." in value.split("/"):
            raise ValueError("Invalid media_url")
        return value

    @model_validator(mode="after")
    def _validate_candidate_contract(self) -> "MediaGenerationCandidateRecord":
        if self.status == "completed":
            if self.media_url is None:
                raise ValueError("Completed media candidate requires media_url")
            if not _valid_candidate_media_url(self.media_url, self.media_kind, self.authority):
                raise ValueError("Invalid media_url")
        elif self.media_url is not None:
            raise ValueError("Nonterminal media candidate cannot expose media_url")
        if self.status not in {"needs_confirmation", "failed"} and self.error_code is not None:
            raise ValueError("Media candidate error_code is only allowed for failed or needs_confirmation")
        if self.authority == "provider":
            if self.attempt_id is None or self.execution_fingerprint is None:
                raise ValueError("Provider media candidate requires execution authority")
        else:
            if self.status != "completed":
                raise ValueError("Imported media candidate must be completed inert")
            if self.attempt_id is not None or self.execution_fingerprint is not None:
                raise ValueError("Imported media candidate cannot carry provider authority")
            if self.error_code is not None:
                raise ValueError("Imported media candidate cannot carry provider error state")
        return self
