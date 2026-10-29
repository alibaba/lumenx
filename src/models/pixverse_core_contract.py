"""PixVerse workspace payloads and frozen images for the four existing modes."""

from __future__ import annotations

import base64
import binascii
from typing import Mapping

from .provider_image_policy import get_provider_image_policy
from .provider_tasks import PreparedMedia, ProviderBindingError, ProviderSubmission
from ..utils.endpoints import dashscope_workspace_scope_matches
from ..utils.generation_intent import GenerationIntent
from ..utils.media_probe import FrozenImageMetadata, MediaProbeError, probe_frozen_image


PIXVERSE_V6_I2V_PROVIDER_MODEL = "pixverse/pixverse-v6-it2v"
PIXVERSE_C1_I2V_PROVIDER_MODEL = "pixverse/pixverse-c1-it2v"
PIXVERSE_C1_R2V_PROVIDER_MODEL = "pixverse/pixverse-c1-r2v"
PIXVERSE_V56_R2V_PROVIDER_MODEL = "pixverse/pixverse-v5.6-r2v"
PIXVERSE_PROVIDER_CONTRACT = {
    # This repeated prefix is the existing canonical identity, including receipts.
    PIXVERSE_V6_I2V_PROVIDER_MODEL: ("pixverse/pixverse/pixverse-v6-video#i2v", "i2v"),
    PIXVERSE_C1_I2V_PROVIDER_MODEL: ("pixverse/pixverse-c1-video#i2v", "i2v"),
    PIXVERSE_C1_R2V_PROVIDER_MODEL: ("pixverse/pixverse-c1-video#r2v", "r2v"),
    PIXVERSE_V56_R2V_PROVIDER_MODEL: ("pixverse/pixverse-v5.6-r2v#r2v", "r2v"),
}
PIXVERSE_PROVIDER_MODELS = frozenset(PIXVERSE_PROVIDER_CONTRACT)
_PIXVERSE_PROVIDER_BY_CANONICAL = {
    canonical: provider_model_id
    for provider_model_id, (canonical, _mode) in PIXVERSE_PROVIDER_CONTRACT.items()
}
_PIXVERSE_I2V_RESOLUTIONS = {"360P", "540P", "720P", "1080P"}
_PIXVERSE_DATA_URI_BINDING = "data_uri_sha256_v1"
_PIXVERSE_OSS_BINDING = "dashscope_oss_sha256_v1"
_PIXVERSE_R2V_SIZES = {
    PIXVERSE_C1_R2V_PROVIDER_MODEL: {
        "720P": {"16:9": "1280*720", "4:3": "1108*832", "1:1": "960*960", "3:4": "832*1108", "9:16": "720*1280"},
        "1080P": {"16:9": "1920*1080", "4:3": "1664*1248", "1:1": "1440*1440", "3:4": "1248*1664", "9:16": "1080*1920"},
    },
    PIXVERSE_V56_R2V_PROVIDER_MODEL: {
        "720P": {"16:9": "1280*720", "4:3": "1108*830", "1:1": "960*960", "3:4": "830*1108", "9:16": "720*1280"},
        "1080P": {"16:9": "1920*1080", "4:3": "1662*1246", "1:1": "1440*1440", "3:4": "1246*1662", "9:16": "1080*1920"},
    },
}


def validate_pixverse_intent(intent: GenerationIntent) -> dict:
    """Validate frozen scalar intent without credentials, media, or I/O."""
    if not isinstance(intent, GenerationIntent):
        raise ProviderBindingError("Unsupported or mismatched PixVerse intent")
    provider_model_id = _PIXVERSE_PROVIDER_BY_CANONICAL.get(intent.model_id)
    if provider_model_id is None:
        raise ProviderBindingError("Unsupported or mismatched PixVerse intent")
    contract = PIXVERSE_PROVIDER_CONTRACT.get(provider_model_id)
    if contract is None or intent.mode != contract[1] or intent.catalog_mode != contract[1]:
        raise ProviderBindingError("Unsupported or mismatched PixVerse intent")
    prompt_limit = 2048 if provider_model_id == PIXVERSE_V56_R2V_PROVIDER_MODEL else 5000
    if not isinstance(intent.prompt, str) or not intent.prompt.strip() or len(intent.prompt) > prompt_limit:
        raise ProviderBindingError("Invalid PixVerse prompt")
    return _pixverse_parameters(intent.params, mode=contract[1], provider_model_id=provider_model_id)


def validate_pixverse_image_metadata(metadata: object) -> None:
    """Enforce documented edge limits without inventing alpha or ratio rules."""
    if not isinstance(metadata, FrozenImageMetadata) or metadata.kind != "image":
        raise ProviderBindingError("PixVerse image metadata is outside supported limits")
    if any(type(edge) is not int or not 1 <= edge <= 10000 for edge in (metadata.width, metadata.height)):
        raise ProviderBindingError("PixVerse image metadata is outside supported limits")


def pixverse_submission_payload(
    request: ProviderSubmission,
    *,
    require_transport_ready: bool = False,
) -> dict:
    """Build the exact workspace payload from an already accepted Core intent."""
    if not isinstance(request, ProviderSubmission):
        raise ProviderBindingError("Validation requires a frozen submission")
    contract = PIXVERSE_PROVIDER_CONTRACT.get(request.provider_model_id)
    if contract is None:
        raise ProviderBindingError("Unsupported PixVerse provider model")
    canonical, mode = contract
    scope = request.scope
    if (
        request.intent.model_id != canonical
        or scope.provider != "dashscope"
        or scope.backend != "dashscope"
        or scope.gateway != "dashscope_workspace"
        or scope.region != "cn-beijing"
        or not isinstance(scope.workspace_id, str)
        or not dashscope_workspace_scope_matches(scope.base_url, scope.workspace_id, scope.region)
    ):
        raise ProviderBindingError("Unsupported or mismatched PixVerse model/mode/gateway")
    params = validate_pixverse_intent(request.intent)
    media = _pixverse_media_payload(
        tuple(request.media), mode=mode, provider_model_id=request.provider_model_id,
        require_transport_ready=require_transport_ready,
    )
    return {"model": request.provider_model_id,
            "input": {"prompt": request.intent.prompt, "media": media},
            "parameters": params}


def _pixverse_parameters(
    params: Mapping[str, object],
    *,
    mode: str,
    provider_model_id: str,
) -> dict:
    if not isinstance(params, Mapping) or any(type(key) is not str for key in params):
        raise ProviderBindingError("Invalid PixVerse parameters")
    allowed = {"duration", "resolution", "audio", "watermark", "seed"}
    if mode == "r2v":
        allowed.add("aspect_ratio")
    if provider_model_id == PIXVERSE_V6_I2V_PROVIDER_MODEL:
        allowed.add("shot_type")
    if set(params) - allowed:
        raise ProviderBindingError("Unsupported PixVerse parameter")
    # Admission freezes both required values. The provider never recreates defaults.
    duration = params.get("duration")
    resolution = params.get("resolution")
    if type(duration) is not int or not 1 <= duration <= 15:
        raise ProviderBindingError("Invalid PixVerse duration")
    resolutions = _PIXVERSE_I2V_RESOLUTIONS if mode == "i2v" else _PIXVERSE_R2V_SIZES[provider_model_id]
    if not isinstance(resolution, str) or resolution not in resolutions:
        raise ProviderBindingError("Invalid PixVerse resolution")
    if provider_model_id == PIXVERSE_V56_R2V_PROVIDER_MODEL:
        durations = (5, 8) if resolution == "1080P" else (5, 8, 10)
        if duration not in durations:
            raise ProviderBindingError("Invalid PixVerse duration for resolution")
    result = dict(params)
    for name in ("audio", "watermark"):
        if name in params and type(params[name]) is not bool:
            raise ProviderBindingError("Invalid PixVerse boolean")
    if "seed" in params and (type(params["seed"]) is not int or not 0 <= params["seed"] <= 2147483647):
        raise ProviderBindingError("Invalid PixVerse seed")
    if "shot_type" in params and (
        not isinstance(params["shot_type"], str) or params["shot_type"] not in {"single", "multi"}
    ):
        raise ProviderBindingError("Invalid PixVerse shot type")
    if mode == "r2v":
        sizes = _PIXVERSE_R2V_SIZES[provider_model_id][resolution]
        ratio = params.get("aspect_ratio")
        if not isinstance(ratio, str) or ratio not in sizes:
            raise ProviderBindingError("PixVerse reference-to-video requires a supported aspect ratio")
        result.pop("resolution")
        result.pop("aspect_ratio")
        result["size"] = sizes[ratio]
    return result


def _pixverse_media_payload(
    media: tuple[PreparedMedia, ...],
    *,
    mode: str,
    provider_model_id: str,
    require_transport_ready: bool,
) -> list[dict]:
    if mode == "i2v" and len(media) != 1:
        raise ProviderBindingError("PixVerse image-to-video requires one first frame")
    if mode == "r2v" and not 1 <= len(media) <= 7:
        raise ProviderBindingError("PixVerse reference-to-video requires 1 to 7 reference images")
    policy = get_provider_image_policy(provider_model_id)
    if policy is None:
        raise ProviderBindingError("Unsupported PixVerse image media")
    result = []
    for ordinal, item in enumerate(media):
        if not isinstance(item, PreparedMedia):
            raise ProviderBindingError("Unsupported PixVerse image media")
        version = item.version
        if (
            version.role != policy.role or version.ordinal != ordinal
            or version.declared_media_type not in policy.mime_suffixes
            or version.size_bytes > policy.max_bytes
        ):
            raise ProviderBindingError("Unsupported PixVerse image media")
        _validate_pixverse_image_transport(item, max_bytes=policy.max_bytes,
                                          require_transport_ready=require_transport_ready)
        result.append({"type": "image_url", "url": item.url})
    return result


def _validate_pixverse_image_transport(
    item: PreparedMedia,
    *,
    max_bytes: int,
    require_transport_ready: bool,
) -> None:
    if require_transport_ready:
        if item.transport_binding != _PIXVERSE_OSS_BINDING or not item.url.startswith("oss://"):
            raise ProviderBindingError("PixVerse image media must be uploaded before submit")
        validate_pixverse_image_metadata(getattr(item, "frozen_image_metadata", None))
        return
    if item.transport_binding == _PIXVERSE_DATA_URI_BINDING:
        validate_pixverse_image_metadata(_pixverse_image_metadata_from_data_uri(item, max_bytes=max_bytes))
        return
    if item.transport_binding == _PIXVERSE_OSS_BINDING and item.url.startswith("oss://"):
        validate_pixverse_image_metadata(getattr(item, "frozen_image_metadata", None))
        return
    raise ProviderBindingError("PixVerse image media requires verified bytes or provider upload")


def _pixverse_image_metadata_from_data_uri(item: PreparedMedia, *, max_bytes: int) -> FrozenImageMetadata:
    version = item.version
    if not isinstance(item.url, str):
        raise ProviderBindingError("PixVerse image media bytes are invalid")
    prefix = f"data:{version.declared_media_type};base64,"
    if not item.url.startswith(prefix):
        raise ProviderBindingError("PixVerse image media bytes are invalid")
    encoded = item.url[len(prefix):]
    if not encoded or len(encoded) > ((max_bytes + 2) // 3) * 4 + 4:
        raise ProviderBindingError("PixVerse image media bytes are invalid")
    try:
        data = base64.b64decode(encoded.encode("ascii"), validate=True)
    except (binascii.Error, UnicodeEncodeError, ValueError):
        raise ProviderBindingError("PixVerse image media bytes are invalid") from None
    try:
        return probe_frozen_image(version, data, max_bytes=max_bytes)
    except (MediaProbeError, TypeError):
        raise ProviderBindingError("PixVerse image media bytes are invalid") from None
