"""Atelier-owned TTS generation command normalization.

The command and preview path deliberately stop before admission. They validate a
future audio generation request and freeze the prepared TTS SDK identity without
creating receipts, attempts, storage writes, credentials, or provider I/O.
"""

from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ...audio.tts import PreparedTTS, TTSProcessor, VOICES
from ...utils.request_identity import fingerprint_request, validate_client_request_id

ATELIER_TTS_ENTRYPOINT = "atelier.provider_audio.v1"
TTS_PREVIEW_PARAMETERS = frozenset({"speech_rate", "pitch_rate", "volume", "instructions"})
_TTS_NUMERIC_RANGES = {
    "speech_rate": (0.5, 2.0),
    "pitch_rate": (0.5, 2.0),
    "volume": (0, 100),
}


class TTSCandidateCommand(BaseModel):
    """Pure TTS generation request shape; not an admission or execution right."""

    model_config = ConfigDict(extra="forbid", strict=True)

    expected_revision: int = Field(ge=0)
    client_request_id: str
    voice: str = Field(min_length=1)
    text: str = Field(min_length=1)
    params: dict[str, Any] = Field(default_factory=dict)

    @field_validator("client_request_id")
    @classmethod
    def _request_id(cls, value: str) -> str:
        return validate_client_request_id(value)

    @field_validator("voice")
    @classmethod
    def _voice(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Atelier TTS text and voice must be nonempty")
        if not _is_builtin_voice(value):
            raise ValueError("Atelier TTS requires a built-in voice")
        return value

    @field_validator("text")
    @classmethod
    def _text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Atelier TTS text and voice must be nonempty")
        return value

    @field_validator("params")
    @classmethod
    def _supported_params(cls, value: dict[str, Any]) -> dict[str, Any]:
        if set(value) - TTS_PREVIEW_PARAMETERS:
            raise ValueError("Atelier TTS request contains unsupported parameters")
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if key == "instructions":
                if not isinstance(item, str):
                    raise ValueError("Atelier TTS instructions must be text")
                normalized[key] = item
                continue
            if type(item) not in (int, float) or not math.isfinite(item):
                raise ValueError("Atelier TTS numeric parameters must be finite numbers")
            lower, upper = _TTS_NUMERIC_RANGES[key]
            if item < lower or item > upper:
                raise ValueError("Atelier TTS numeric parameters are out of range")
            normalized[key] = item
        return normalized


def normalize_tts_candidate_request(command: TTSCandidateCommand) -> PreparedTTS:
    """Resolve a built-in voice into a frozen prepared TTS value.

    The neutral TTS processor is used only for normalization. It has an explicit
    empty environment so preview construction never falls back to live process
    credentials or mutable app settings.
    """

    _require_builtin_voice(command.voice)
    explicit_params = set(command.params)
    processor = TTSProcessor(api_key="", env={})
    prepared = processor.prepare(
        command.text,
        voice=command.voice,
        speech_rate=command.params.get("speech_rate", 1.0),
        pitch_rate=command.params.get("pitch_rate", 1.0),
        volume=command.params.get("volume", 50),
        instructions=command.params.get("instructions"),
    )
    explicit_unsupported = [notice for notice in prepared.unsupported if notice.field in explicit_params]
    if explicit_unsupported:
        fields = ", ".join(sorted({notice.field for notice in explicit_unsupported}))
        raise ValueError(f"Atelier TTS request contains unsupported explicit parameters: {fields}")
    implicit_numeric_defaults = {"speech_rate", "pitch_rate", "volume"} - explicit_params
    unsupported = tuple(
        notice for notice in prepared.unsupported
        if notice.field not in implicit_numeric_defaults
    )
    return PreparedTTS(prepared.family, prepared.parameters, prepared.adjustments, unsupported)


def fingerprint_tts_candidate_request(command: TTSCandidateCommand, *, node_id: str) -> str:
    return fingerprint_request(
        {
            "node_id": node_id,
            **command.model_dump(
                mode="python", exclude_unset=True,
                exclude={"expected_revision", "client_request_id"},
            ),
        },
        entrypoint=ATELIER_TTS_ENTRYPOINT,
    )


def _require_builtin_voice(voice: str) -> None:
    if not _is_builtin_voice(voice):
        raise ValueError("Atelier TTS requires a built-in voice")


def _is_builtin_voice(voice: str) -> bool:
    return any(meta.get("model_id") == voice for meta in VOICES.values())


class BuiltinTTSVoice(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id: str
    name: str
    family: Literal["cosyvoice", "qwen3"]
    supported_parameters: list[Literal["instructions", "pitch_rate", "speech_rate", "volume"]]


def list_builtin_tts_voices() -> list[dict[str, Any]]:
    """Pure voice read model, sharing the exact Core preparation capability rules."""
    processor = TTSProcessor(api_key="", env={})
    result = []
    seen: set[str] = set()
    for metadata in VOICES.values():
        voice = metadata["model_id"]
        if voice in seen:
            continue
        seen.add(voice)
        prepared = processor.prepare("Preview", voice=voice, instructions="Calm narration")
        unsupported = {notice.field for notice in prepared.unsupported}
        result.append({"id": voice, "name": metadata["name"], "family": prepared.family,
            "supported_parameters": sorted(TTS_PREVIEW_PARAMETERS - unsupported)})
    return result
