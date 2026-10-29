"""Atelier-owned request boundary for the first durable video admission path.

Request identity keeps the original supplied fields. Core resolves execution
defaults separately; neither a valid command nor its fingerprint grants a
provider submission right.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ...models.provider_tasks import ProviderBindingError
from ...models.qwen20_image_contract import QWEN20_IMAGE_CONTRACT_VERSION, QWEN20_IMAGE_PROVIDER_MODELS, qwen20_image_payload
from ...models.wan27_image_contract import WAN27_IMAGE_CONTRACT_VERSION, WAN27_IMAGE_PROVIDER_MODELS, wan27_image_payload
from ...utils.generation_intent import GenerationIntent, IntentValidationError, normalize_generation_intent
from ...utils.model_catalog import get_catalog_accessor
from .models import ordered_generation_media_is_canonical
from ...utils.request_identity import fingerprint_request, validate_client_request_id


ATELIER_VIDEO_ENTRYPOINT = "atelier.provider_video.v1"
ATELIER_ORDERED_VIDEO_ENTRYPOINT = "atelier.provider_video.v2"
ATELIER_IMAGE_ENTRYPOINT = "atelier.provider_image.v1"
ORDERED_MEDIA_CONTRACT_VERSION = "ordered-media-v1"
ATELIER_VIDEO_MODEL = "wan/wan2.7-video#i2v"
ORDERED_VIDEO_MODELS = frozenset({"wan/wan3.0-video#video", "wan/wan3.0-video-prime#video"})
VIDEO_PARAMETERS = frozenset({
    "duration", "resolution", "seed", "negative_prompt", "prompt_extend", "watermark",
})
ORDERED_VIDEO_PARAMETERS = frozenset({
    "duration", "resolution", "ratio", "seed", "prompt_extend", "watermark", "audio",
})
IMAGE_PARAMETERS = frozenset({"size", "seed", "negative_prompt", "prompt_extend", "watermark"})
_STRICT_WAN27_T2I_IDS = {alias: model for model in WAN27_IMAGE_PROVIDER_MODELS
                       for alias in (model, f"wan/{model}#image")}
_STRICT_QWEN20_T2I_IDS = {alias: model for model in QWEN20_IMAGE_PROVIDER_MODELS
                        for alias in (model, f"qwen/{model}#image")}
ORDERED_MEDIA_ROLES = frozenset({
    "first_frame", "last_frame", "reference_image", "reference_video", "reference_audio",
})
REFERENCE_MEDIA_ROLES = frozenset({"reference_image", "reference_video", "reference_audio"})


class FirstFrameSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    node_id: str = Field(min_length=1)
    media_url: str = Field(min_length=1)

    @field_validator("node_id", "media_url")
    @classmethod
    def _nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("First frame identity must be nonempty")
        return value


class CreateVideoCandidateCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    expected_revision: int = Field(ge=0)
    client_request_id: str
    model_id: str = Field(min_length=1)
    mode: Literal["i2v"]
    prompt: str
    first_frame: FirstFrameSelection
    params: dict[str, Any] = Field(default_factory=dict)

    @field_validator("client_request_id")
    @classmethod
    def _request_id(cls, value: str) -> str:
        return validate_client_request_id(value)

    @field_validator("params")
    @classmethod
    def _supported_params(cls, value: dict[str, Any]) -> dict[str, Any]:
        if set(value) - VIDEO_PARAMETERS:
            raise ValueError("Atelier video request contains unsupported parameters")
        return value


class ManualVideoCandidateBatchCommand(CreateVideoCandidateCommand):
    """Manual HTTP admission command; batch_size is product admission metadata."""

    batch_size: int = Field(1, ge=1, le=6, strict=True)


class OrderedMediaSelection(BaseModel):
    """Product source identity for one ordered provider input."""

    model_config = ConfigDict(extra="forbid", strict=True)

    role: Literal["first_frame", "last_frame", "reference_image", "reference_video", "reference_audio"]
    ordinal: int = Field(ge=0, strict=True)
    node_id: str = Field(min_length=1)
    media_url: str = Field(min_length=1)

    @field_validator("node_id", "media_url")
    @classmethod
    def _nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Ordered media identity must be nonempty")
        return value

    @model_validator(mode="after")
    def _frame_ordinals_are_singleton(self) -> "OrderedMediaSelection":
        if self.role in {"first_frame", "last_frame"} and self.ordinal != 0:
            raise ValueError("Frame media roles require ordinal zero")
        return self


class OrderedVideoCandidateCommand(BaseModel):
    """Explicit Wan3 ordered-media request for durable admission."""

    model_config = ConfigDict(extra="forbid", strict=True)

    expected_revision: int = Field(ge=0)
    client_request_id: str
    contract_version: Literal["ordered-media-v1"]
    model_id: Literal["wan/wan3.0-video#video", "wan/wan3.0-video-prime#video"]
    mode: Literal["t2v", "i2v", "r2v"]
    prompt: str
    media: tuple[OrderedMediaSelection, ...]
    params: dict[str, Any] = Field(default_factory=dict)

    @field_validator("client_request_id")
    @classmethod
    def _request_id(cls, value: str) -> str:
        return validate_client_request_id(value)

    @field_validator("media", mode="before")
    @classmethod
    def _media_tuple(cls, value):
        if isinstance(value, list):
            return tuple(value)
        return value

    @field_validator("params")
    @classmethod
    def _supported_params(cls, value: dict[str, Any]) -> dict[str, Any]:
        if set(value) - ORDERED_VIDEO_PARAMETERS:
            raise ValueError("Atelier ordered video request contains unsupported parameters")
        if any(type(item) not in (str, int, float, bool, type(None)) for item in value.values()):
            raise ValueError("Atelier ordered video parameters must be scalar")
        return value

    @model_validator(mode="after")
    def _validate_ordered_media_matrix(self) -> "OrderedVideoCandidateCommand":
        roles = [item.role for item in self.media]
        image_count = sum(role in {"first_frame", "last_frame", "reference_image"} for role in roles)
        video_count = roles.count("reference_video")
        audio_count = roles.count("reference_audio")
        if image_count > 10 or video_count > 5 or audio_count > 5 or len(roles) > 20:
            raise ValueError("Atelier ordered media count exceeds Wan3 limits")
        if self.mode == "t2v":
            if self.media:
                raise ValueError("Atelier ordered T2V cannot include media")
            if not self.prompt.strip():
                raise ValueError("Atelier ordered T2V requires a prompt")
            return self
        if self.mode == "i2v":
            if tuple(roles) not in {("first_frame",), ("first_frame", "last_frame")}:
                raise ValueError("Atelier ordered I2V requires first frame or first and last frames")
        elif self.mode == "r2v":
            if not self.media or any(role not in REFERENCE_MEDIA_ROLES for role in roles):
                raise ValueError("Atelier ordered R2V requires reference media")
        if not ordered_generation_media_is_canonical(self.media):
            raise ValueError("Atelier ordered media roles must be canonical")
        seen: set[tuple[str, int]] = set()
        ordinals_by_role: dict[str, list[int]] = {}
        for item in self.media:
            key = (item.role, item.ordinal)
            if key in seen:
                raise ValueError("Atelier ordered media role ordinal is duplicated")
            seen.add(key)
            ordinals_by_role.setdefault(item.role, []).append(item.ordinal)
        for role, ordinals in ordinals_by_role.items():
            if ordinals != list(range(len(ordinals))):
                raise ValueError("Atelier ordered media ordinals must be continuous within each role")
            if role in {"first_frame", "last_frame"} and ordinals != [0]:
                raise ValueError("Atelier ordered frame roles require ordinal zero")
        return self


class ManualOrderedVideoCandidateBatchCommand(OrderedVideoCandidateCommand):
    """Manual ordered-media batch command sharing the existing admission ledger."""

    batch_size: int = Field(1, ge=1, le=6, strict=True)


class ImageReferenceSelection(BaseModel):
    """Product source identity for a future image-to-image input."""

    model_config = ConfigDict(extra="forbid", strict=True)

    role: Literal["reference_image"]
    ordinal: int = Field(ge=0, strict=True)
    node_id: str = Field(min_length=1)
    media_url: str = Field(min_length=1)

    @field_validator("node_id", "media_url")
    @classmethod
    def _nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Image generation source identity must be nonempty")
        return value


class ImageCandidateCommand(BaseModel):
    """Pure image generation request shape; not an admission or execution right."""

    model_config = ConfigDict(extra="forbid", strict=True)

    expected_revision: int = Field(ge=0)
    client_request_id: str
    model_id: str = Field(min_length=1)
    mode: Literal["t2i", "i2i"]
    prompt: str
    media: tuple[ImageReferenceSelection, ...] = Field(default_factory=tuple)
    params: dict[str, Any] = Field(default_factory=dict)

    @field_validator("client_request_id")
    @classmethod
    def _request_id(cls, value: str) -> str:
        return validate_client_request_id(value)

    @field_validator("media", mode="before")
    @classmethod
    def _media_tuple(cls, value):
        if isinstance(value, list):
            return tuple(value)
        return value

    @field_validator("params")
    @classmethod
    def _supported_params(cls, value: dict[str, Any]) -> dict[str, Any]:
        if set(value) - IMAGE_PARAMETERS:
            raise ValueError("Atelier image request contains unsupported parameters")
        if any(type(item) not in (str, int, bool) for item in value.values()):
            raise ValueError("Atelier image parameters must be scalar")
        return value

    @model_validator(mode="after")
    def _validate_image_media_matrix(self) -> "ImageCandidateCommand":
        if self.mode == "t2i" and self.media:
            raise ValueError("Atelier image T2I cannot include media")
        if self.mode == "i2i":
            if not self.media:
                raise ValueError("Atelier image I2I requires reference media")
            ordinals = [item.ordinal for item in self.media]
            if ordinals != list(range(len(ordinals))):
                raise ValueError("Atelier image media ordinals must be continuous within role")
        return self


class ManualImageCandidateBatchCommand(ImageCandidateCommand):
    """Manual image preview command; batch_size is identity metadata only."""

    batch_size: int = Field(1, ge=1, le=6, strict=True)


def fingerprint_video_candidate_request(command: CreateVideoCandidateCommand, *, node_id: str) -> str:
    return fingerprint_request(
        {
            "node_id": node_id,
            **command.model_dump(
                mode="python", exclude_unset=True,
                exclude={"expected_revision", "client_request_id"},
            ),
        },
        entrypoint=ATELIER_VIDEO_ENTRYPOINT,
    )


def fingerprint_ordered_video_candidate_request(command: OrderedVideoCandidateCommand, *, node_id: str) -> str:
    return fingerprint_request(
        {
            "node_id": node_id,
            **command.model_dump(
                mode="python", exclude_unset=True,
                exclude={"expected_revision", "client_request_id"},
            ),
        },
        entrypoint=ATELIER_ORDERED_VIDEO_ENTRYPOINT,
    )


def fingerprint_image_candidate_request(command: ImageCandidateCommand, *, node_id: str) -> str:
    return fingerprint_request(
        {
            "node_id": node_id,
            **command.model_dump(
                mode="python", exclude_unset=True,
                exclude={"expected_revision", "client_request_id"},
            ),
        },
        entrypoint=ATELIER_IMAGE_ENTRYPOINT,
    )


def generation_entrypoint_for_command(
    command: CreateVideoCandidateCommand | OrderedVideoCandidateCommand,
) -> Literal["atelier.provider_video.v1", "atelier.provider_video.v2"]:
    if isinstance(command, OrderedVideoCandidateCommand):
        return ATELIER_ORDERED_VIDEO_ENTRYPOINT
    if isinstance(command, CreateVideoCandidateCommand):
        return ATELIER_VIDEO_ENTRYPOINT
    raise TypeError("Unsupported Atelier generation command")


def normalize_video_candidate_request(command: CreateVideoCandidateCommand) -> GenerationIntent:
    intent = normalize_generation_intent(
        {
            "model_id": command.model_id,
            "mode": command.mode,
            "prompt": command.prompt,
            "params": command.params,
        },
        allowed_modes={"i2v"},
        allowed_parameters=VIDEO_PARAMETERS,
    )
    if intent.model_id != ATELIER_VIDEO_MODEL:
        raise ValueError("Atelier durable video admission currently supports Wan 2.7 I2V")
    return intent


def normalize_ordered_video_candidate_request(command: OrderedVideoCandidateCommand) -> GenerationIntent:
    intent = normalize_generation_intent(
        {
            "model_id": command.model_id,
            "mode": command.mode,
            "prompt": command.prompt,
            "params": command.params,
        },
        allowed_modes={"t2v", "i2v", "r2v"},
        allowed_parameters=ORDERED_VIDEO_PARAMETERS,
    )
    if intent.model_id not in ORDERED_VIDEO_MODELS:
        raise ValueError("Atelier ordered video admission requires a Wan 3.0 video model")
    return intent


def strict_wan27_image_provider_model(model_id: str, mode: str | None) -> str | None:
    """Select only this entrance's exact new T2I scope, never historical records."""
    return _STRICT_WAN27_T2I_IDS.get(model_id) if mode == "t2i" else None


def strict_qwen20_image_provider_model(model_id: str, mode: str | None) -> str | None:
    """Opt in only new Qwen 2.0 T2I intents; historical records keep their version."""
    return _STRICT_QWEN20_T2I_IDS.get(model_id) if mode == "t2i" else None


def image_contract_options(intent: GenerationIntent) -> dict[str, str]:
    """Explicit opt-in arguments for a newly normalized image preview."""
    if strict_wan27_image_provider_model(intent.model_id, intent.mode):
        return {"image_contract_version": WAN27_IMAGE_CONTRACT_VERSION}
    if strict_qwen20_image_provider_model(intent.model_id, intent.mode):
        return {"image_contract_version": QWEN20_IMAGE_CONTRACT_VERSION}
    return {}


def normalize_image_candidate_request(command: ImageCandidateCommand) -> GenerationIntent:
    strict_model = strict_wan27_image_provider_model(command.model_id, command.mode)
    qwen_model = strict_qwen20_image_provider_model(command.model_id, command.mode)
    preset = None
    if strict_model:
        preset = {"id": "atelier.wan27.t2i", "version": "1", "mode": "t2i", "params": {"watermark": False}}
    elif qwen_model:
        preset = {"id": "atelier.qwen20.t2i", "version": "1", "mode": "t2i", "params": {"watermark": False, "prompt_extend": True}}
    intent = normalize_generation_intent(
        {
            "model_id": command.model_id,
            "mode": command.mode,
            "prompt": command.prompt,
            "params": command.params,
        },
        allowed_modes={"t2i", "i2i"},
        allowed_parameters={"size", "seed", "watermark"} if strict_model else IMAGE_PARAMETERS,
        preset=preset,
    )
    _require_routable_image_model(intent.model_id)
    if strict_model:
        if not {"size", "watermark"}.issubset(intent.params):
            raise IntentValidationError("Wan2.7 T2I requires resolved size and watermark")
        try:
            wan27_image_payload(intent.prompt, strict_model, intent.params["size"], intent.params.get("seed"), intent.params["watermark"])
        except ProviderBindingError as exc:
            raise IntentValidationError(str(exc)) from exc
    elif qwen_model:
        if not {"size", "watermark", "prompt_extend"}.issubset(intent.params):
            raise IntentValidationError("Qwen2.0 T2I requires resolved size, watermark and prompt_extend")
        try:
            qwen20_image_payload(
                intent.prompt, qwen_model, intent.params["size"], intent.params.get("seed"),
                intent.params["watermark"], intent.params["prompt_extend"], intent.params.get("negative_prompt"),
            )
        except ProviderBindingError as exc:
            raise IntentValidationError(str(exc)) from exc
    return intent


def _require_routable_image_model(model_id: str) -> None:
    entry = get_catalog_accessor().get_mode_entry(model_id)
    runtime = entry.get("runtime") if isinstance(entry, dict) else None
    provider = entry.get("provider") if isinstance(entry, dict) else None
    legacy_model_id = entry.get("legacy_model_id") if isinstance(entry, dict) else None
    if not isinstance(entry, dict) or entry.get("mode") != "image":
        raise ValueError("Atelier image preview requires a canonical image model")
    if entry.get("status") != "active":
        raise ValueError("Atelier image preview requires an active image model")
    if not isinstance(provider, str) or not provider or not isinstance(legacy_model_id, str) or not legacy_model_id:
        raise ValueError("Atelier image preview requires provider route metadata")
    if not isinstance(runtime, dict) or not runtime or not any(
        isinstance(config, dict) and isinstance(config.get("gateway"), str) and config["gateway"]
        for config in runtime.values()
    ):
        raise ValueError("Atelier image preview requires runtime route metadata")
