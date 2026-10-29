"""Vidu Q3 workspace provider payload and frozen-image contract."""

from __future__ import annotations

import base64
import binascii
from typing import Mapping

from .provider_image_policy import get_provider_image_policy
from .provider_tasks import PreparedMedia, ProviderBindingError, ProviderSubmission
from ..utils.generation_intent import GenerationIntent
from ..utils.media_probe import FrozenImageMetadata, MediaProbeError, probe_frozen_image


VIDU_PRO_I2V_PROVIDER_MODEL = "vidu/viduq3-pro_img2video"
VIDU_TURBO_I2V_PROVIDER_MODEL = "vidu/viduq3-turbo_img2video"
VIDU_MIX_R2V_PROVIDER_MODEL = "vidu/viduq3-mix_reference2video"
VIDU_TURBO_R2V_PROVIDER_MODEL = "vidu/viduq3-turbo_reference2video"
VIDU_PROVIDER_MODELS = frozenset({
    VIDU_PRO_I2V_PROVIDER_MODEL,
    VIDU_TURBO_I2V_PROVIDER_MODEL,
    VIDU_MIX_R2V_PROVIDER_MODEL,
    VIDU_TURBO_R2V_PROVIDER_MODEL,
})
VIDU_PROVIDER_CONTRACT = {
    VIDU_PRO_I2V_PROVIDER_MODEL: ("vidu/viduq3-pro-video#i2v", "i2v"),
    VIDU_TURBO_I2V_PROVIDER_MODEL: ("vidu/viduq3-turbo-video#i2v", "i2v"),
    VIDU_MIX_R2V_PROVIDER_MODEL: ("vidu/viduq3-pro-video#r2v", "r2v"),
    VIDU_TURBO_R2V_PROVIDER_MODEL: ("vidu/viduq3-turbo-video#r2v", "r2v"),
}

_VIDU_CANONICAL_MODES = {
    canonical: mode for canonical, mode in VIDU_PROVIDER_CONTRACT.values()
}
_VIDU_PROVIDER_BY_CANONICAL = {
    canonical: provider_model_id
    for provider_model_id, (canonical, _mode) in VIDU_PROVIDER_CONTRACT.items()
}
_VIDU_DATA_URI_BINDING = "data_uri_sha256_v1"
_VIDU_OSS_BINDING = "dashscope_oss_sha256_v1"
_VIDU_RESOLUTIONS = {"540P", "720P", "1080P"}
_VIDU_MIX_R2V_RESOLUTIONS = {"720P", "1080P"}
_VIDU_R2V_SIZES = {
    "540P": {
        "16:9": "960*528",
        "4:3": "1024*768",
        "1:1": "1024*1024",
        "3:4": "768*1024",
        "9:16": "576*1024",
    },
    "720P": {
        "16:9": "1280*720",
        "4:3": "1280*960",
        "1:1": "1280*1280",
        "3:4": "960*1280",
        "9:16": "720*1280",
    },
    "1080P": {
        "16:9": "1920*1080",
        "4:3": "1920*1440",
        "1:1": "1920*1920",
        "3:4": "1440*1920",
        "9:16": "1080*1920",
    },
}


def validate_vidu_intent(intent: GenerationIntent) -> dict:
    """Validate Vidu prompt and scalar params without media, credentials, or I/O."""
    if not isinstance(intent, GenerationIntent):
        raise ProviderBindingError("Unsupported or mismatched Vidu intent")
    expected_mode = _VIDU_CANONICAL_MODES.get(intent.model_id)
    if (
        expected_mode is None
        or intent.mode != expected_mode
        or intent.catalog_mode != expected_mode
    ):
        raise ProviderBindingError("Unsupported or mismatched Vidu intent")
    if not isinstance(intent.prompt, str) or not intent.prompt.strip() or len(intent.prompt) > 5000:
        raise ProviderBindingError("Invalid Vidu prompt")
    provider_model_id = _VIDU_PROVIDER_BY_CANONICAL.get(intent.model_id)
    return _vidu_parameters(intent.params, mode=expected_mode, provider_model_id=provider_model_id)


def validate_vidu_image_metadata(metadata: object) -> None:
    """Validate reported frozen image metadata for Vidu without inspecting bytes."""
    if not isinstance(metadata, FrozenImageMetadata) or metadata.kind != "image":
        raise ProviderBindingError("Vidu image metadata is outside supported limits")
    if (
        type(metadata.width) is not int
        or type(metadata.height) is not int
        or metadata.width <= 0
        or metadata.height <= 0
        or max(metadata.width / metadata.height, metadata.height / metadata.width) > 4
    ):
        raise ProviderBindingError("Vidu image metadata is outside supported limits")


def vidu_submission_payload(
    request: ProviderSubmission,
    *,
    require_transport_ready: bool = False,
) -> dict:
    """Build the exact DashScope Vidu workspace payload for accepted Core requests."""
    if not isinstance(request, ProviderSubmission):
        raise ProviderBindingError("Validation requires a frozen submission")
    contract = VIDU_PROVIDER_CONTRACT.get(request.provider_model_id)
    if contract is None:
        raise ProviderBindingError("Unsupported Vidu provider model")
    canonical_model_id, mode = contract
    intent = request.intent
    if (
        intent.model_id != canonical_model_id
        or intent.mode != mode
        or intent.catalog_mode != mode
        or request.scope.gateway != "dashscope_workspace"
        or request.scope.region != "cn-beijing"
    ):
        raise ProviderBindingError("Unsupported or mismatched Vidu model/mode/gateway")
    if not isinstance(intent.prompt, str) or not intent.prompt.strip() or len(intent.prompt) > 5000:
        raise ProviderBindingError("Invalid Vidu prompt")
    params = _vidu_parameters(
        intent.params,
        mode=mode,
        provider_model_id=request.provider_model_id,
    )
    media_payload = _vidu_media_payload(
        tuple(request.media),
        mode=mode,
        provider_model_id=request.provider_model_id,
        require_transport_ready=require_transport_ready,
    )
    inputs: dict[str, object] = {"prompt": intent.prompt, "media": media_payload}
    return {"model": request.provider_model_id, "input": inputs, "parameters": params}


def _vidu_parameters(
    params: Mapping[str, object],
    *,
    mode: str,
    provider_model_id: str | None,
) -> dict:
    if not isinstance(params, Mapping) or any(type(key) is not str for key in params):
        raise ProviderBindingError("Invalid Vidu parameters")
    params = dict(params)
    allowed = {"duration", "resolution", "audio", "watermark", "seed"}
    if mode == "r2v":
        allowed.add("aspect_ratio")
    if set(params) - allowed:
        raise ProviderBindingError("Unsupported Vidu parameter")

    result: dict[str, object] = {}

    if "duration" in params:
        duration = params.pop("duration")
        if type(duration) is not int or not 1 <= duration <= 16:
            raise ProviderBindingError("Invalid Vidu duration")
        result["duration"] = duration

    resolution = None
    if "resolution" in params:
        resolution = params.pop("resolution")
        if not isinstance(resolution, str) or resolution not in _VIDU_RESOLUTIONS:
            raise ProviderBindingError("Invalid Vidu resolution")
        if provider_model_id == VIDU_MIX_R2V_PROVIDER_MODEL and resolution not in _VIDU_MIX_R2V_RESOLUTIONS:
            raise ProviderBindingError("Invalid Vidu resolution")
        result["resolution"] = resolution

    for name in ("audio", "watermark"):
        if name in params:
            value = params.pop(name)
            if type(value) is not bool:
                raise ProviderBindingError("Invalid Vidu boolean")
            result[name] = value

    if "seed" in params:
        seed = params.pop("seed")
        if type(seed) is not int or not 0 <= seed <= 2147483647:
            raise ProviderBindingError("Invalid Vidu seed")
        result["seed"] = seed

    if mode == "r2v":
        aspect_ratio = params.pop("aspect_ratio", None)
        if not isinstance(resolution, str):
            raise ProviderBindingError("Vidu reference-to-video requires an explicit resolution")
        sizes = _VIDU_R2V_SIZES.get(resolution, {})
        if not isinstance(aspect_ratio, str) or aspect_ratio not in sizes:
            raise ProviderBindingError("Vidu reference-to-video requires a supported aspect ratio")
        result["size"] = sizes[aspect_ratio]

    if params:
        raise ProviderBindingError("Unsupported Vidu parameter")
    return result


def _vidu_media_payload(
    media: tuple[PreparedMedia, ...],
    *,
    mode: str,
    provider_model_id: str,
    require_transport_ready: bool,
) -> list[dict]:
    if mode == "i2v":
        if len(media) != 1:
            raise ProviderBindingError("Vidu image-to-video requires one first frame")
        return [
            _vidu_image_payload(
                media[0],
                role="first_frame",
                ordinal=0,
                provider_model_id=provider_model_id,
                require_transport_ready=require_transport_ready,
            )
        ]
    if not 1 <= len(media) <= 7:
        raise ProviderBindingError("Vidu reference-to-video requires 1 to 7 reference images")
    return [
        _vidu_image_payload(
            item,
            role="reference_image",
            ordinal=ordinal,
            provider_model_id=provider_model_id,
            require_transport_ready=require_transport_ready,
        )
        for ordinal, item in enumerate(media)
    ]


def _vidu_image_payload(
    item: PreparedMedia,
    *,
    role: str,
    ordinal: int,
    provider_model_id: str,
    require_transport_ready: bool,
) -> dict:
    if not isinstance(item, PreparedMedia):
        raise ProviderBindingError("Unsupported Vidu image media")
    policy = get_provider_image_policy(provider_model_id)
    if policy is None or policy.role != role:
        raise ProviderBindingError("Unsupported Vidu image media")
    version = item.version
    if (
        version.role != role
        or version.ordinal != ordinal
        or version.declared_media_type not in policy.mime_suffixes
        or version.size_bytes > policy.max_bytes
    ):
        raise ProviderBindingError("Unsupported Vidu image media")
    _validate_vidu_image_transport(
        item,
        max_bytes=policy.max_bytes,
        require_transport_ready=require_transport_ready,
    )
    return {"type": "image", "url": item.url}


def _validate_vidu_image_transport(
    item: PreparedMedia,
    *,
    max_bytes: int,
    require_transport_ready: bool,
) -> None:
    if require_transport_ready:
        if item.transport_binding != _VIDU_OSS_BINDING or not item.url.startswith("oss://"):
            raise ProviderBindingError("Vidu image media must be uploaded before submit")
        validate_vidu_image_metadata(getattr(item, "frozen_image_metadata", None))
        return
    if item.transport_binding == _VIDU_DATA_URI_BINDING:
        validate_vidu_image_metadata(_vidu_image_metadata_from_data_uri(item, max_bytes=max_bytes))
        return
    if item.transport_binding == _VIDU_OSS_BINDING and item.url.startswith("oss://"):
        validate_vidu_image_metadata(getattr(item, "frozen_image_metadata", None))
        return
    raise ProviderBindingError("Vidu image media requires verified bytes or provider upload")


def _vidu_image_metadata_from_data_uri(item: PreparedMedia, *, max_bytes: int) -> FrozenImageMetadata:
    version = item.version
    if not isinstance(item.url, str):
        raise ProviderBindingError("Vidu image media bytes are invalid")
    prefix = f"data:{version.declared_media_type};base64,"
    if not item.url.startswith(prefix):
        raise ProviderBindingError("Vidu image media bytes are invalid")
    encoded = item.url[len(prefix):]
    if not encoded or len(encoded) > ((max_bytes + 2) // 3) * 4 + 4:
        raise ProviderBindingError("Vidu image media bytes are invalid")
    try:
        data = base64.b64decode(encoded.encode("ascii"), validate=True)
    except (binascii.Error, UnicodeEncodeError, ValueError):
        raise ProviderBindingError("Vidu image media bytes are invalid") from None
    try:
        return probe_frozen_image(version, data, max_bytes=max_bytes)
    except (MediaProbeError, TypeError):
        raise ProviderBindingError("Vidu image media bytes are invalid") from None
