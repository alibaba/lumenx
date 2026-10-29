"""Strict local draft settings, separate from generation execution authority."""

from __future__ import annotations

import time
from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ...utils.generation_intent import IntentValidationError, normalize_generation_intent
from .generation_commands import (
    IMAGE_PARAMETERS, ORDERED_VIDEO_PARAMETERS, VIDEO_PARAMETERS, ImageReferenceSelection,
    OrderedMediaSelection, _require_routable_image_model,
)
from .tts_commands import TTSCandidateCommand, normalize_tts_candidate_request
from .models import AtelierNode, AtelierProject, ordered_generation_media_is_canonical
from .nodes import NodeCommandAck
from .storage import AtelierStorage

GENERATION_DRAFT_SCHEMA_VERSION = 1
GENERATION_DRAFT_MODE = "i2v"
GENERATION_DRAFT_PARAMETERS = VIDEO_PARAMETERS


class GenerationDraftCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    expected_revision: int = Field(..., ge=0, strict=True)
    prompt: str
    model_id: Literal["wan/wan2.7-video#i2v"]
    mode: Literal["i2v"]
    params: dict[str, Any]

    @field_validator("params", mode="before")
    @classmethod
    def _params(cls, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise ValueError("params must be an object")
        if any(not isinstance(key, str) or key not in GENERATION_DRAFT_PARAMETERS for key in value):
            raise ValueError("params contains unsupported generation draft field")
        return dict(value)

    @model_validator(mode="after")
    def _validate_with_core_intent_rules(self) -> "GenerationDraftCommand":
        try:
            normalize_generation_intent(
                {
                    "model_id": self.model_id,
                    "mode": self.mode,
                    "prompt": self.prompt,
                    "params": self.params,
                },
                allowed_modes={GENERATION_DRAFT_MODE},
                allowed_parameters=GENERATION_DRAFT_PARAMETERS,
            )
        except IntentValidationError as exc:
            raise ValueError("Invalid Atelier generation draft") from exc
        return self

    def draft_payload(self) -> dict[str, Any]:
        return {
            "schema_version": GENERATION_DRAFT_SCHEMA_VERSION,
            "model_id": self.model_id,
            "mode": self.mode,
            "params": dict(self.params),
        }


class OrderedGenerationDraftCommand(BaseModel):
    """Incomplete user intent; admission separately validates executable inputs."""

    model_config = ConfigDict(extra="forbid", strict=True)

    expected_revision: int = Field(..., ge=0, strict=True)
    contract_version: Literal["ordered-media-v1"]
    prompt: str
    model_id: Literal["wan/wan3.0-video#video", "wan/wan3.0-video-prime#video"]
    mode: Literal["t2v", "i2v", "r2v"]
    params: dict[str, Any]
    media: list[OrderedMediaSelection]
    batch_size: int = Field(1, ge=1, le=6, strict=True)

    @model_validator(mode="after")
    def _validate_draft_inputs(self) -> "OrderedGenerationDraftCommand":
        normalize_generation_intent(
            {"model_id": self.model_id, "mode": self.mode, "prompt": self.prompt, "params": self.params},
            allowed_modes={"t2v", "i2v", "r2v"},
            allowed_parameters=ORDERED_VIDEO_PARAMETERS,
        )
        if not ordered_generation_media_is_canonical(self.media):
            raise ValueError("Ordered draft media must have canonical role order")
        counts: dict[str, int] = {}
        for item in self.media:
            ordinal = counts.get(item.role, 0)
            if item.ordinal != ordinal:
                raise ValueError("Ordered draft media ordinals must be continuous")
            counts[item.role] = ordinal + 1
        if (
            len(self.media) > 20
            or sum(counts.get(role, 0) for role in ("first_frame", "last_frame", "reference_image")) > 10
            or counts.get("reference_video", 0) > 5
            or counts.get("reference_audio", 0) > 5
        ):
            raise ValueError("Ordered draft media exceeds supported limits")
        return self

    def draft_payload(self) -> dict[str, Any]:
        return {
            "schema_version": 2,
            "input_contract_version": self.contract_version,
            "model_id": self.model_id,
            "mode": self.mode,
            "params": dict(self.params),
            "media": [item.model_dump(mode="json") for item in self.media],
            "batch_size": self.batch_size,
        }


class ImageGenerationDraftCommand(BaseModel):
    """Editable image intent; incomplete references cannot be admitted yet."""

    model_config = ConfigDict(extra="forbid", strict=True)

    expected_revision: int = Field(ge=0)
    media_kind: Literal["image"]
    prompt: str
    model_id: str = Field(min_length=1)
    mode: Literal["t2i", "i2i"]
    params: dict[str, Any] = Field(default_factory=dict)
    media: list[ImageReferenceSelection] = Field(default_factory=list, max_length=10)
    batch_size: int = Field(1, ge=1, le=6)

    @model_validator(mode="after")
    def _validate_draft(self) -> "ImageGenerationDraftCommand":
        # Empty text is valid editing state. The sentinel never reaches storage
        # or execution; only the catalog's parameter/model rules are reused.
        normalize_generation_intent(
            {"model_id": self.model_id, "mode": self.mode,
             "prompt": self.prompt if self.prompt.strip() else "Draft",
             "params": self.params},
            allowed_modes={"t2i", "i2i"}, allowed_parameters=IMAGE_PARAMETERS,
        )
        _require_routable_image_model(self.model_id)
        if self.mode == "t2i" and self.media:
            raise ValueError("Image T2I draft cannot include references")
        if [item.ordinal for item in self.media] != list(range(len(self.media))):
            raise ValueError("Image draft reference ordinals must be continuous")
        return self

    def draft_payload(self) -> dict[str, Any]:
        return {"schema_version": 3, **self.model_dump(mode="json", exclude={"expected_revision", "prompt"})}


class AudioGenerationDraftCommand(BaseModel):
    """Built-in speech intent, with voice-specific controls validated by Core."""

    model_config = ConfigDict(extra="forbid", strict=True)

    expected_revision: int = Field(ge=0)
    media_kind: Literal["audio"]
    prompt: str
    voice: str
    params: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_draft(self) -> "AudioGenerationDraftCommand":
        normalize_tts_candidate_request(TTSCandidateCommand(
            expected_revision=self.expected_revision, client_request_id="draft-validation",
            voice=self.voice, text=self.prompt if self.prompt.strip() else "Draft", params=self.params,
        ))
        return self

    def draft_payload(self) -> dict[str, Any]:
        return {"schema_version": 3, **self.model_dump(mode="json", exclude={"expected_revision", "prompt"})}


class AtelierGenerationDraftService:
    """Persist generation draft intent without granting execution authority."""

    def __init__(self, storage: AtelierStorage, *, clock: Callable[[], float] = time.time):
        self.storage = storage
        self._clock = clock

    def update_generation_draft(
        self,
        project_id: str,
        node_id: str,
        command: GenerationDraftCommand | OrderedGenerationDraftCommand | ImageGenerationDraftCommand | AudioGenerationDraftCommand,
    ) -> NodeCommandAck:
        now = self._clock()
        draft = command.draft_payload()

        def apply(project: AtelierProject) -> None:
            node = _find_generation_node(project, node_id, kind=getattr(command, "media_kind", "video"))
            data = dict(node.data)
            if node.prompt == command.prompt and data.get("generation_draft") == draft:
                return
            node.prompt = command.prompt
            data["generation_draft"] = draft
            node.data = data
            node.updated_at = now
            project.updated_at = now

        project = self.storage.apply_project_command(
            project_id,
            expected_revision=command.expected_revision,
            command=apply,
        )
        return NodeCommandAck(project=project, node_ids=[node_id])


def _find_generation_node(project: AtelierProject, node_id: str, *, kind: str) -> AtelierNode:
    node = next((item for item in project.nodes if item.id == node_id), None)
    if node is None:
        raise ValueError("Atelier generation draft node not found")
    if node.type != kind:
        raise ValueError(f"Atelier generation draft requires a {kind} node")
    return node
