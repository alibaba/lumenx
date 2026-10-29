"""Kling workspace provider payload and frozen-media contract."""

from __future__ import annotations

import base64
import binascii
from typing import Mapping

from .provider_tasks import PreparedMedia, ProviderBindingError, ProviderSubmission
from ..utils.generation_intent import GenerationIntent
from ..utils.media_probe import FrozenImageMetadata, MediaProbeError, probe_frozen_image


KLING_I2V_PROVIDER_MODEL = "kling/kling-v3-video-generation"
KLING_R2V_PROVIDER_MODEL = "kling/kling-v3-omni-video-generation"
KLING_PROVIDER_MODELS = frozenset({KLING_I2V_PROVIDER_MODEL, KLING_R2V_PROVIDER_MODEL})

_KLING_CANONICAL_MODES = {
    "kling/kling-v3-video#i2v": "i2v",
    "kling/kling-v3-video#r2v": "r2v",
}
_KLING_PROVIDER_CONTRACT = {
    KLING_I2V_PROVIDER_MODEL: ("kling/kling-v3-video#i2v", "i2v"),
    KLING_R2V_PROVIDER_MODEL: ("kling/kling-v3-video#r2v", "r2v"),
}
_KLING_IMAGE_MIMES = {"image/png", "image/jpeg"}
_KLING_IMAGE_MAX_BYTES = 10 * 1024 * 1024
_KLING_DATA_URI_BINDING = "data_uri_sha256_v1"
_KLING_OSS_BINDING = "dashscope_oss_sha256_v1"
_KLING_MODES = {"std", "pro", "4k"}
_KLING_ASPECT_RATIOS = {"16:9", "9:16", "1:1"}


def validate_kling_intent(intent: GenerationIntent) -> dict:
    """Validate Kling prompt and scalar params without media, credentials, or I/O."""
    if not isinstance(intent, GenerationIntent):
        raise ProviderBindingError("Unsupported or mismatched Kling intent")
    expected_mode = _KLING_CANONICAL_MODES.get(intent.model_id)
    if (
        expected_mode is None
        or intent.mode != expected_mode
        or intent.catalog_mode != expected_mode
    ):
        raise ProviderBindingError("Unsupported or mismatched Kling intent")
    if not isinstance(intent.prompt, str) or not intent.prompt.strip() or len(intent.prompt) > 2500:
        raise ProviderBindingError("Invalid Kling prompt")
    return _kling_parameters(intent.params, mode=expected_mode)


def validate_kling_image_metadata(metadata: object) -> None:
    """Validate reported frozen image metadata for Kling without inspecting bytes."""
    if (
        not isinstance(metadata, FrozenImageMetadata)
        or metadata.kind != "image"
        or not 300 <= metadata.width <= 8000
        or not 300 <= metadata.height <= 8000
        or max(metadata.width / metadata.height, metadata.height / metadata.width) > 2.5
        or metadata.has_alpha
    ):
        raise ProviderBindingError("Kling image metadata is outside supported limits")


def kling_submission_payload(
    request: ProviderSubmission,
    *,
    require_transport_ready: bool = False,
) -> dict:
    """Build the exact DashScope Kling workspace payload for accepted Core requests."""
    if not isinstance(request, ProviderSubmission):
        raise ProviderBindingError("Validation requires a frozen submission")
    contract = _KLING_PROVIDER_CONTRACT.get(request.provider_model_id)
    if contract is None:
        raise ProviderBindingError("Unsupported Kling provider model")
    canonical_model_id, mode = contract
    intent = request.intent
    if (
        intent.model_id != canonical_model_id
        or intent.mode != mode
        or intent.catalog_mode != mode
        or request.scope.gateway != "dashscope_workspace"
        or request.scope.region != "cn-beijing"
    ):
        raise ProviderBindingError("Unsupported or mismatched Kling model/mode/gateway")
    params = validate_kling_intent(intent)
    media_payload = _kling_media_payload(
        tuple(request.media),
        mode=mode,
        require_transport_ready=require_transport_ready,
    )
    inputs: dict[str, object] = {"prompt": intent.prompt, "media": media_payload}
    if "negative_prompt" in params:
        inputs["negative_prompt"] = params.pop("negative_prompt")
    return {"model": request.provider_model_id, "input": inputs, "parameters": params}


def _kling_parameters(params: Mapping[str, object], *, mode: str) -> dict:
    if not isinstance(params, Mapping) or any(type(key) is not str for key in params):
        raise ProviderBindingError("Invalid Kling parameters")
    params = dict(params)
    allowed = {"mode", "duration", "audio", "watermark", "negative_prompt"}
    if mode == "r2v":
        allowed.add("aspect_ratio")
    if set(params) - allowed:
        raise ProviderBindingError("Unsupported Kling parameter")
    if "mode" in params and (
        not isinstance(params["mode"], str) or params["mode"] not in _KLING_MODES
    ):
        raise ProviderBindingError("Invalid Kling mode")
    if "duration" in params and (
        type(params["duration"]) is not int or not 3 <= params["duration"] <= 15
    ):
        raise ProviderBindingError("Invalid Kling duration")
    for name in ("audio", "watermark"):
        if name in params and type(params[name]) is not bool:
            raise ProviderBindingError("Invalid Kling boolean")
    if "negative_prompt" in params and (
        not isinstance(params["negative_prompt"], str) or len(params["negative_prompt"]) > 2500
    ):
        raise ProviderBindingError("Invalid Kling negative prompt")
    if mode == "r2v":
        if not isinstance(params.get("aspect_ratio"), str) or params["aspect_ratio"] not in _KLING_ASPECT_RATIOS:
            raise ProviderBindingError("Kling reference-to-video requires a supported aspect ratio")
    return params


def _kling_media_payload(
    media: tuple[PreparedMedia, ...],
    *,
    mode: str,
    require_transport_ready: bool,
) -> list[dict]:
    if mode == "i2v":
        if len(media) != 1:
            raise ProviderBindingError("Kling image-to-video requires one first frame")
        return [
            _kling_image_payload(
                media[0],
                role="first_frame",
                ordinal=0,
                output_type="first_frame",
                require_transport_ready=require_transport_ready,
            )
        ]
    if not 1 <= len(media) <= 7:
        raise ProviderBindingError("Kling reference-to-video requires 1 to 7 reference images")
    return [
        _kling_image_payload(
            item,
            role="reference_image",
            ordinal=ordinal,
            output_type="refer",
            require_transport_ready=require_transport_ready,
        )
        for ordinal, item in enumerate(media)
    ]


def _kling_image_payload(
    item: PreparedMedia,
    *,
    role: str,
    ordinal: int,
    output_type: str,
    require_transport_ready: bool,
) -> dict:
    if not isinstance(item, PreparedMedia):
        raise ProviderBindingError("Unsupported Kling image media")
    version = item.version
    if (
        version.role != role
        or version.ordinal != ordinal
        or version.declared_media_type not in _KLING_IMAGE_MIMES
        or version.size_bytes > _KLING_IMAGE_MAX_BYTES
    ):
        raise ProviderBindingError("Unsupported Kling image media")
    _validate_kling_image_transport(item, require_transport_ready=require_transport_ready)
    return {"type": output_type, "url": item.url}


def _validate_kling_image_transport(item: PreparedMedia, *, require_transport_ready: bool) -> None:
    if require_transport_ready:
        if item.transport_binding != _KLING_OSS_BINDING or not item.url.startswith("oss://"):
            raise ProviderBindingError("Kling image media must be uploaded before submit")
        validate_kling_image_metadata(getattr(item, "frozen_image_metadata", None))
        return
    if item.transport_binding == _KLING_DATA_URI_BINDING:
        validate_kling_image_metadata(_kling_image_metadata_from_data_uri(item))
        return
    if item.transport_binding == _KLING_OSS_BINDING:
        validate_kling_image_metadata(getattr(item, "frozen_image_metadata", None))
        return
    raise ProviderBindingError("Kling image media requires verified bytes or provider upload")


def _kling_image_metadata_from_data_uri(item: PreparedMedia) -> FrozenImageMetadata:
    version = item.version
    if not isinstance(item.url, str):
        raise ProviderBindingError("Kling image media bytes are invalid")
    prefix = f"data:{version.declared_media_type};base64,"
    if not item.url.startswith(prefix):
        raise ProviderBindingError("Kling image media bytes are invalid")
    encoded = item.url[len(prefix):]
    if not encoded or len(encoded) > ((_KLING_IMAGE_MAX_BYTES + 2) // 3) * 4 + 4:
        raise ProviderBindingError("Kling image media bytes are invalid")
    try:
        data = base64.b64decode(encoded.encode("ascii"), validate=True)
    except (binascii.Error, UnicodeEncodeError, ValueError):
        raise ProviderBindingError("Kling image media bytes are invalid") from None
    try:
        return probe_frozen_image(version, data)
    except MediaProbeError:
        raise ProviderBindingError("Kling image media bytes are invalid") from None
