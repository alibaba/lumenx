"""Frozen product contract for the three existing standard Seedance HTTP modes."""

from __future__ import annotations

import base64
import binascii
from typing import Mapping

from .provider_binding import ProviderCredentialStore
from .provider_tasks import PreparedMedia, ProviderBindingError, ProviderScope, ProviderSubmission
from ..utils.generation_intent import GenerationIntent
from ..utils.media_probe import MediaProbeError, probe_frozen_image


SEEDANCE_PROVIDER_CONTRACT = {
    f"seedance-2.0-{mode}": (f"seedance/seedance-2.0-video#{mode}", mode)
    for mode in ("t2v", "i2v", "r2v")
}
SEEDANCE_PROVIDER_MODELS = frozenset(SEEDANCE_PROVIDER_CONTRACT)
SEEDANCE_HTTP_ENDPOINTS = {
    "seedance-2.0-t2v": "/vendors/bytedance/v1/seedance-2.0/text-to-video/generation",
    "seedance-2.0-i2v": "/vendors/bytedance/v1/seedance-2.0/image-to-video/generation",
    "seedance-2.0-r2v": "/vendors/bytedance/v1/seedance-2.0/reference-to-video/generation",
}
# Product input guardrails, not a claim about undocumented provider decode limits.
SEEDANCE_IMAGE_MAX_BYTES = 20 * 1024 * 1024
SEEDANCE_IMAGE_MIMES = frozenset({"image/png", "image/jpeg", "image/webp"})
_SEEDANCE_MODES = {canonical: mode for canonical, mode in SEEDANCE_PROVIDER_CONTRACT.values()}
_SEEDANCE_RATIOS = {"16:9", "9:16", "1:1", "4:3", "3:4"}


def validate_seedance_scope(scope: ProviderScope) -> None:
    """Use the credential store's exact HTTP authority without resolving secrets."""
    ProviderCredentialStore._validate_mulerouter_http_scope(scope)


def validate_seedance_intent(intent: GenerationIntent) -> dict:
    """Validate frozen product values without adding provider or current defaults."""
    if not isinstance(intent, GenerationIntent):
        raise ProviderBindingError("Unsupported Seedance intent")
    mode = _SEEDANCE_MODES.get(intent.model_id)
    if mode is None or intent.mode != mode or intent.catalog_mode != mode:
        raise ProviderBindingError("Unsupported or mismatched Seedance identity")
    if not isinstance(intent.prompt, str) or not intent.prompt.strip():
        raise ProviderBindingError("Invalid Seedance prompt")
    params = intent.params
    if not isinstance(params, Mapping) or any(type(key) is not str for key in params):
        raise ProviderBindingError("Invalid Seedance parameters")
    required = {"duration", "resolution", "aspect_ratio", "audio"}
    if mode == "t2v":
        required.add("watermark")
    if required - set(params) or set(params) - (required | {"seed"}):
        raise ProviderBindingError("Missing or unsupported Seedance parameter")
    if type(params["duration"]) is not int or not 4 <= params["duration"] <= 15:
        raise ProviderBindingError("Invalid Seedance duration")
    if not isinstance(params["resolution"], str) or params["resolution"] not in {"720p", "1080p"}:
        raise ProviderBindingError("Invalid Seedance resolution")
    if not isinstance(params["aspect_ratio"], str) or params["aspect_ratio"] not in _SEEDANCE_RATIOS:
        raise ProviderBindingError("Invalid Seedance aspect ratio")
    for name in ("audio", "watermark"):
        if name in params and type(params[name]) is not bool:
            raise ProviderBindingError("Invalid Seedance boolean")
    if "seed" in params and (type(params["seed"]) is not int or not -1 <= params["seed"] <= 4294967295):
        raise ProviderBindingError("Invalid Seedance seed")
    result = dict(params)
    result["generate_audio"] = result.pop("audio")
    return result


def seedance_submission_payload(request: ProviderSubmission) -> dict:
    """Build HTTP body only from the exact accepted identity and frozen bytes."""
    if not isinstance(request, ProviderSubmission):
        raise ProviderBindingError("Validation requires a frozen submission")
    validate_seedance_scope(request.scope)
    contract = SEEDANCE_PROVIDER_CONTRACT.get(request.provider_model_id)
    if contract is None or request.intent.model_id != contract[0]:
        raise ProviderBindingError("Unsupported or mismatched Seedance provider model")
    parameters = validate_seedance_intent(request.intent)
    mode = contract[1]
    media = tuple(request.media)
    if (
        mode == "t2v" and media
        or mode == "i2v" and len(media) != 1
        or mode == "r2v" and not 1 <= len(media) <= 9
    ):
        raise ProviderBindingError("Invalid Seedance media count")
    role = "first_frame" if mode == "i2v" else "reference_image"
    for ordinal, item in enumerate(media):
        _validate_image(item, role=role, ordinal=ordinal)
    result = {"prompt": request.intent.prompt, **parameters}
    if mode == "i2v":
        result["image"] = media[0].url
    elif mode == "r2v":
        result["images"] = [item.url for item in media]
    return result


def _validate_image(item: PreparedMedia, *, role: str, ordinal: int) -> None:
    if not isinstance(item, PreparedMedia):
        raise ProviderBindingError("Invalid Seedance frozen image")
    version = item.version
    if (
        version.role != role or version.ordinal != ordinal
        or version.declared_media_type not in SEEDANCE_IMAGE_MIMES
        or version.size_bytes > SEEDANCE_IMAGE_MAX_BYTES
        or item.transport_binding != "data_uri_sha256_v1"
        or not isinstance(item.url, str)
    ):
        raise ProviderBindingError("Unsupported Seedance frozen image")
    prefix = f"data:{version.declared_media_type};base64,"
    if not item.url.startswith(prefix):
        raise ProviderBindingError("Invalid Seedance image data URI")
    encoded = item.url[len(prefix):]
    if not encoded or len(encoded) > 4 * ((SEEDANCE_IMAGE_MAX_BYTES + 2) // 3):
        raise ProviderBindingError("Invalid Seedance image size")
    try:
        data = base64.b64decode(encoded.encode("ascii"), validate=True)
        # Recheck hash, size, MIME and actual decoding; never reread the source.
        probe_frozen_image(version, data, max_bytes=SEEDANCE_IMAGE_MAX_BYTES)
    except (binascii.Error, UnicodeEncodeError, ValueError, TypeError, MediaProbeError):
        raise ProviderBindingError("Invalid Seedance frozen image bytes") from None
