import base64
import binascii
import mimetypes
import os
import time
import unicodedata
import requests
from http import HTTPStatus
from dashscope import VideoSynthesis
from .base import VideoGenModel
from .provider_result_fetch import _reject_deferred_callback, fetch_provider_result
from .provider_tasks import (
    ProviderArtifact, ProviderBindingError, ProviderHandle,
    ProviderObservation, ProviderQueryError,
    ProviderSubmission, ProviderSubmitError, ProviderTaskStatus, QueryCapability,
    ResolvedCredential, validate_fetch_binding, validate_query_binding, validate_result_url,
)
from .kling_core_contract import KLING_PROVIDER_MODELS, kling_submission_payload
from .vidu_core_contract import VIDU_PROVIDER_MODELS, vidu_submission_payload
from .pixverse_core_contract import PIXVERSE_PROVIDER_MODELS, pixverse_submission_payload
from ..utils import get_logger
from ..utils.endpoints import get_provider_base_url

from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, ContextManager, Dict, List, Mapping, Optional, Tuple

from ..utils.oss_utils import OSSImageUploader
from ..utils.provider_media import resolve_media_input, resolve_media_inputs
from ..utils.provider_registry import resolve_provider_backend
from ..utils.json_storage import DirectoryWriterLock, StorageError
from ..utils.generation_intent import GenerationIntent
from ..utils.media_probe import FrozenMediaMetadata, MediaProbeError, probe_frozen_image

logger = get_logger(__name__)

def _dashscope_scope_headers(scope) -> dict:
    """Validate an explicit official gateway and preserve its workspace scope."""
    import re

    if scope.provider != "dashscope" or scope.backend != "dashscope":
        raise ProviderBindingError("Receipt is not bound to DashScope")
    # Official host inventory, checked 2026-10-09:
    # https://help.aliyun.com/zh/model-studio/regions
    classic_hosts = {
        "dashscope.aliyuncs.com": "cn-beijing",
        "dashscope-intl.aliyuncs.com": "ap-southeast-1",
        "dashscope-us.aliyuncs.com": "us-east-1",
        "cn-hongkong.dashscope.aliyuncs.com": "cn-hongkong",
    }
    regions = {*classic_hosts.values(), "eu-central-1", "ap-northeast-1"}
    if scope.gateway == "dashscope":
        permitted_hosts = {
            host for host, region in classic_hosts.items() if region == scope.region
        }
    elif (
        scope.gateway == "dashscope_workspace" and scope.region in regions
        and scope.workspace_id != "trial"
        and re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", scope.workspace_id or "")
    ):
        permitted_hosts = {f"{scope.workspace_id}.{scope.region}.maas.aliyuncs.com"}
    else:
        raise ProviderBindingError("Unsupported or incomplete DashScope gateway scope")
    if scope.base_url not in {
        value for host in permitted_hosts for value in (f"https://{host}", f"https://{host}/")
    }:
        raise ProviderBindingError("Task operation requires an original official HTTPS base URL")
    headers = {}
    if scope.gateway == "dashscope" and scope.workspace_id is not None:
        # Match SDK workspace headers without deriving a workspace or gateway.
        if not re.fullmatch(r"[\x21-\x7e]+", scope.workspace_id):
            raise ProviderBindingError("Invalid workspace header")
        headers["X-DashScope-WorkSpace"] = scope.workspace_id
    return headers


def _dashscope_task_headers(scope, credential: ResolvedCredential) -> dict:
    return {**_dashscope_scope_headers(scope), "Authorization": f"Bearer {credential.secret}"}


def _wan_first_frame_submission_payload(request: ProviderSubmission) -> dict:
    """Narrow first-frame contract; never infer a model, defaults, or media roles.

    Official references checked 2026-10-11:
    https://help.aliyun.com/zh/model-studio/legacy-image-to-video-api-reference/
    https://help.aliyun.com/zh/model-studio/image-to-video-general-api-reference
    2.7 uses input.media and has no audio/shot controls. Audio control remains
    2.6-flash-only. No Wan3 rules or additional media roles are inferred here.
    """
    models = {
        "wan2.6-i2v": "wan/wan2.6-video#i2v",
        "wan2.6-i2v-flash": "wan/wan2.6-i2v-flash#i2v",
        "wan2.7-i2v": "wan/wan2.7-video#i2v",
    }
    intent = request.intent
    if (
        request.provider_model_id not in models
        or intent.model_id != models[request.provider_model_id]
        or intent.mode != "i2v" or intent.catalog_mode != "i2v"
    ):
        raise ProviderBindingError("Unsupported or mismatched submission model/mode")
    if len(request.media) != 1 or request.media[0].version.role != "first_frame":
        raise ProviderBindingError("Submission requires exactly one first frame")
    version = request.media[0].version
    if (
        version.declared_media_type not in {"image/png", "image/jpeg", "image/webp", "image/bmp"}
        or version.size_bytes > 20 * 1024 * 1024
    ):
        raise ProviderBindingError("Unsupported first-frame metadata")
    is_wan27 = request.provider_model_id == "wan2.7-i2v"
    if not isinstance(intent.prompt, str) or len(intent.prompt) > (5000 if is_wan27 else 1500):
        raise ProviderBindingError("Invalid submission prompt")
    params = dict(intent.params)
    allowed = {
        "duration", "resolution", "seed", "prompt_extend", "watermark", "negative_prompt",
    }
    if not is_wan27:
        allowed.add("shot_type")
    if request.provider_model_id == "wan2.6-i2v-flash":
        allowed.add("audio")
    if set(params) - allowed:
        raise ProviderBindingError("Unsupported submission parameter")
    if type(params.get("duration")) is not int or not 2 <= params["duration"] <= 15:
        raise ProviderBindingError("A supported frozen duration is required")
    if (
        not isinstance(params.get("resolution"), str)
        or params["resolution"].upper() not in {"720P", "1080P"}
    ):
        raise ProviderBindingError("A supported frozen resolution is required")
    params["resolution"] = params["resolution"].upper()
    if "seed" in params:
        if type(params["seed"]) is not int or not -1 <= params["seed"] <= 2147483647:
            raise ProviderBindingError("Invalid submission seed")
        if params["seed"] == -1:
            params.pop("seed")
    for name in ("prompt_extend", "watermark", "audio"):
        if name in params and type(params[name]) is not bool:
            raise ProviderBindingError("Invalid submission boolean")
    if "shot_type" in params and (
        params["shot_type"] not in {"single", "multi"} or params.get("prompt_extend") is False
    ):
        raise ProviderBindingError("Unsupported shot control without prompt extension")
    inputs: dict[str, object] = {"prompt": intent.prompt}
    if is_wan27:
        inputs["media"] = [{"type": "first_frame", "url": request.media[0].url}]
    else:
        inputs["img_url"] = request.media[0].url
    if "negative_prompt" in params:
        negative = params.pop("negative_prompt")
        if not isinstance(negative, str) or len(negative) > 500:
            raise ProviderBindingError("Invalid negative prompt")
        inputs["negative_prompt"] = negative
    return {"model": request.provider_model_id, "input": inputs, "parameters": params}


def _wan27_t2v_submission_payload(request: ProviderSubmission) -> dict:
    """Narrow Wan 2.7 text-to-video contract; no media or audio controls."""
    intent = request.intent
    if (
        request.provider_model_id != "wan2.7-t2v"
        or intent.model_id != "wan/wan2.7-video#t2v"
        or intent.mode != "t2v" or intent.catalog_mode != "t2v"
        or request.scope.gateway != "dashscope"
    ):
        raise ProviderBindingError("Unsupported or mismatched submission model/mode")
    if request.media:
        raise ProviderBindingError("Wan 2.7 text-to-video cannot include media")
    if not isinstance(intent.prompt, str) or not intent.prompt.strip() or len(intent.prompt) > 5000:
        raise ProviderBindingError("Invalid submission prompt")
    params = dict(intent.params)
    if set(params) - {
        "duration", "resolution", "ratio", "seed", "prompt_extend", "watermark", "negative_prompt",
    }:
        raise ProviderBindingError("Unsupported submission parameter")
    if type(params.get("duration")) is not int or not 2 <= params["duration"] <= 15:
        raise ProviderBindingError("A supported frozen duration is required")
    if (
        not isinstance(params.get("resolution"), str)
        or params["resolution"].upper() not in {"720P", "1080P"}
    ):
        raise ProviderBindingError("A supported frozen resolution is required")
    params["resolution"] = params["resolution"].upper()
    if not isinstance(params.get("ratio"), str) or params["ratio"] not in {"16:9", "9:16", "1:1", "4:3", "3:4"}:
        raise ProviderBindingError("A supported frozen ratio is required")
    if "seed" in params:
        if type(params["seed"]) is not int or not -1 <= params["seed"] <= 2147483647:
            raise ProviderBindingError("Invalid submission seed")
        if params["seed"] == -1:
            params.pop("seed")
    for name in ("prompt_extend", "watermark"):
        if name in params and type(params[name]) is not bool:
            raise ProviderBindingError("Invalid submission boolean")
    inputs: dict[str, object] = {"prompt": intent.prompt}
    if "negative_prompt" in params:
        negative = params.pop("negative_prompt")
        if not isinstance(negative, str) or len(negative) > 500:
            raise ProviderBindingError("Invalid negative prompt")
        inputs["negative_prompt"] = negative
    return {"model": request.provider_model_id, "input": inputs, "parameters": params}


def _wan27_r2v_submission_payload(request: ProviderSubmission) -> dict:
    """Wan 2.7 reference-to-video with image references only for this slice.

    Official reference checked 2026-10-13:
    https://help.aliyun.com/zh/model-studio/wan-video-to-video-api-reference
    The complete provider contract also permits reference videos, first frame
    and voice references. Product code does not admit those roles in this Core
    slice, so this builder accepts only 1..5 ``reference_image`` items.
    """
    intent = request.intent
    if (
        request.provider_model_id not in {"wan2.7-r2v", "wan2.7-r2v-2026-06-12"}
        or intent.model_id != "wan/wan2.7-video#r2v"
        or intent.mode != "r2v" or intent.catalog_mode != "r2v"
        or request.scope.gateway != "dashscope"
    ):
        raise ProviderBindingError("Unsupported or mismatched submission model/mode")
    if not isinstance(intent.prompt, str) or not intent.prompt.strip() or len(intent.prompt) > 5000:
        raise ProviderBindingError("Invalid submission prompt")
    if not 1 <= len(request.media) <= 5:
        raise ProviderBindingError("Wan 2.7 R2V requires 1 to 5 reference images")
    media_payload = []
    for ordinal, item in enumerate(request.media):
        version = item.version
        if (
            version.role != "reference_image"
            or version.ordinal != ordinal
            or version.declared_media_type not in {"image/png", "image/jpeg", "image/webp", "image/bmp"}
            or version.size_bytes > 20 * 1024 * 1024
        ):
            raise ProviderBindingError("Unsupported Wan 2.7 R2V reference image")
        media_payload.append({"type": "reference_image", "url": item.url})
    params = dict(intent.params)
    if set(params) - {
        "duration", "resolution", "ratio", "seed", "prompt_extend", "watermark", "negative_prompt",
    }:
        raise ProviderBindingError("Unsupported submission parameter")
    if type(params.get("duration")) is not int or not 2 <= params["duration"] <= 15:
        raise ProviderBindingError("A supported frozen duration is required")
    if (
        not isinstance(params.get("resolution"), str)
        or params["resolution"].upper() not in {"720P", "1080P"}
    ):
        raise ProviderBindingError("A supported frozen resolution is required")
    params["resolution"] = params["resolution"].upper()
    if not isinstance(params.get("ratio"), str) or params["ratio"] not in {"16:9", "9:16", "1:1", "4:3", "3:4"}:
        raise ProviderBindingError("A supported frozen ratio is required")
    if "seed" in params:
        if type(params["seed"]) is not int or not -1 <= params["seed"] <= 2147483647:
            raise ProviderBindingError("Invalid submission seed")
        if params["seed"] == -1:
            params.pop("seed")
    for name in ("prompt_extend", "watermark"):
        if name in params and type(params[name]) is not bool:
            raise ProviderBindingError("Invalid submission boolean")
    inputs: dict[str, object] = {"prompt": intent.prompt, "media": media_payload}
    if "negative_prompt" in params:
        negative = params.pop("negative_prompt")
        if not isinstance(negative, str) or len(negative) > 500:
            raise ProviderBindingError("Invalid negative prompt")
        inputs["negative_prompt"] = negative
    return {"model": request.provider_model_id, "input": inputs, "parameters": params}


def _wan27_videoedit_submission_payload(
    request: ProviderSubmission,
    *,
    require_transport_ready: bool = False,
) -> dict:
    """Wan 2.7 instruction video editing with one video plus optional images.

    Official reference checked 2026-10-13:
    https://help.aliyun.com/en/model-studio/wan-video-editing-api-reference
    The video input must be a provider-resolved temporary URL before submit.
    Reference images remain frozen data URI media in this Core slice.
    """
    intent = request.intent
    if (
        request.provider_model_id != "wan2.7-videoedit"
        or intent.model_id != "wan/wan2.7-video#v2v"
        or intent.mode != "v2v" or intent.catalog_mode != "v2v"
        or request.scope.gateway != "dashscope"
    ):
        raise ProviderBindingError("Unsupported or mismatched submission model/mode")
    if not isinstance(intent.prompt, str) or len(intent.prompt) > 5000:
        raise ProviderBindingError("Invalid submission prompt")
    if not 1 <= len(request.media) <= 5:
        raise ProviderBindingError("Wan 2.7 VideoEdit requires one video and up to four reference images")
    video, *images = request.media
    media_payload = [_wan27_videoedit_video_payload(video, require_transport_ready=require_transport_ready)]
    for ordinal, item in enumerate(images):
        media_payload.append(_wan27_videoedit_image_payload(item, ordinal=ordinal))

    params = dict(intent.params)
    if set(params) - {
        "duration", "resolution", "ratio", "seed", "prompt_extend", "watermark",
        "negative_prompt", "audio_setting",
    }:
        raise ProviderBindingError("Unsupported submission parameter")
    if "duration" in params:
        if type(params["duration"]) is not int or not (params["duration"] == 0 or 2 <= params["duration"] <= 10):
            raise ProviderBindingError("A supported frozen duration is required")
    if "resolution" in params:
        if not isinstance(params["resolution"], str) or params["resolution"] not in {"720P", "1080P"}:
            raise ProviderBindingError("A supported frozen resolution is required")
    if "ratio" in params and params["ratio"] not in {"16:9", "9:16", "1:1", "4:3", "3:4"}:
        raise ProviderBindingError("Invalid submission ratio")
    if "seed" in params and (
        type(params["seed"]) is not int or not 0 <= params["seed"] <= 2147483647
    ):
        raise ProviderBindingError("Invalid submission seed")
    for name in ("prompt_extend", "watermark"):
        if name in params and type(params[name]) is not bool:
            raise ProviderBindingError("Invalid submission boolean")
    if "audio_setting" in params and params["audio_setting"] not in {"auto", "origin"}:
        raise ProviderBindingError("Invalid submission audio setting")
    inputs: dict[str, object] = {"prompt": intent.prompt, "media": media_payload}
    if "negative_prompt" in params:
        negative = params.pop("negative_prompt")
        if not isinstance(negative, str) or len(negative) > 500:
            raise ProviderBindingError("Invalid negative prompt")
        inputs["negative_prompt"] = negative
    return {"model": request.provider_model_id, "input": inputs, "parameters": params}


def _wan27_videoedit_video_payload(item, *, require_transport_ready: bool) -> dict:
    version = item.version
    if (
        version.role != "reference_video"
        or version.ordinal != 0
        or version.declared_media_type not in {"video/mp4", "video/quicktime"}
        or version.size_bytes > 100 * 1024 * 1024
    ):
        raise ProviderBindingError("Unsupported Wan 2.7 VideoEdit video media")
    _validate_wan3_av_transport(item, require_transport_ready=require_transport_ready)
    metadata = item.frozen_metadata
    if (
        metadata is None
        or metadata.kind != "video"
        or not 2 <= metadata.duration <= 10
        or metadata.width is None
        or metadata.height is None
        or not 240 <= metadata.width <= 4096
        or not 240 <= metadata.height <= 4096
        or max(metadata.width / metadata.height, metadata.height / metadata.width) > 8
    ):
        raise ProviderBindingError("Wan 2.7 VideoEdit video metadata is outside supported limits")
    return {"type": "video", "url": item.url}


def _wan27_videoedit_image_payload(item, *, ordinal: int) -> dict:
    version = item.version
    if (
        version.role != "reference_image"
        or version.ordinal != ordinal
        or version.declared_media_type not in _WAN3_IMAGE_MIMES
        or version.size_bytes > _WAN3_IMAGE_MAX_BYTES
        or item.transport_binding != _WAN3_IMAGE_BINDING
    ):
        raise ProviderBindingError("Unsupported Wan 2.7 VideoEdit reference image")
    metadata = _validate_wan3_image_bytes(item)
    if (
        not 240 <= metadata.width <= 8000
        or not 240 <= metadata.height <= 8000
        or max(metadata.width / metadata.height, metadata.height / metadata.width) > 8
        or metadata.has_alpha
    ):
        raise ProviderBindingError("Wan 2.7 VideoEdit image dimensions are outside supported limits")
    return {"type": "reference_image", "url": item.url}


_HAPPYHORSE_11_MODEL_MODES = {
    "happyhorse-1.1-t2v": ("happyhorse/happyhorse-1.1-t2v#t2v", "t2v"),
    "happyhorse-1.1-i2v": ("happyhorse/happyhorse-1.1-video#i2v", "i2v"),
    "happyhorse-1.1-r2v": ("happyhorse/happyhorse-1.1-video#r2v", "r2v"),
}
_HAPPYHORSE_IMAGE_MIMES = {"image/png", "image/jpeg", "image/webp"}
_HAPPYHORSE_IMAGE_BINDING = "data_uri_sha256_v1"
_HAPPYHORSE_IMAGE_MAX_BYTES = 20 * 1024 * 1024
_HAPPYHORSE_RESOLUTIONS = {"480P", "720P", "1080P"}
_HAPPYHORSE_RATIOS = {"16:9", "9:16", "1:1", "4:3", "3:4", "4:5", "5:4", "9:21", "21:9"}
_HAPPYHORSE_SEED_MAX = 2147483647


def _happyhorse_11_submission_payload(request: ProviderSubmission) -> dict:
    """HappyHorse 1.1 Core provider contract, workspace-only and media-frozen.

    Official references checked 2026-10-13:
    https://help.aliyun.com/en/model-studio/happyhorse-text-to-video-api-reference
    https://help.aliyun.com/en/model-studio/happyhorse-image-to-video-api-reference
    https://help.aliyun.com/en/model-studio/happyhorse-reference-to-video-api-reference
    """
    intent = request.intent
    contract = _HAPPYHORSE_11_MODEL_MODES.get(request.provider_model_id)
    if contract is None:
        raise ProviderBindingError("Unsupported HappyHorse provider model")
    canonical_model_id, mode = contract
    if (
        intent.model_id != canonical_model_id
        or intent.mode != mode
        or intent.catalog_mode != mode
        or request.scope.gateway != "dashscope_workspace"
    ):
        raise ProviderBindingError("Unsupported or mismatched HappyHorse model/mode/gateway")
    params = validate_happyhorse_11_intent(intent, mode=mode)
    inputs: dict[str, object] = {"prompt": intent.prompt}
    media_payload = _happyhorse_11_media_payload(tuple(request.media), mode=mode)
    if media_payload:
        inputs["media"] = media_payload
    return {"model": request.provider_model_id, "input": inputs, "parameters": params}


def validate_happyhorse_11_intent(intent: GenerationIntent, *, mode: str) -> dict:
    """Validate HappyHorse 1.1 user-controlled prompt and params without media, scope, or credentials."""
    if mode not in {"t2v", "i2v", "r2v"}:
        raise ProviderBindingError("Unsupported HappyHorse mode")
    if not _happyhorse_prompt_is_supported(intent.prompt):
        raise ProviderBindingError("Invalid HappyHorse prompt")
    if mode in {"t2v", "r2v"} and not intent.prompt.strip():
        raise ProviderBindingError("HappyHorse text/reference-to-video requires a prompt")
    return _happyhorse_11_parameters(intent.params, mode=mode)


def _happyhorse_11_parameters(params: Mapping[str, object], *, mode: str) -> dict:
    allowed = {"duration", "resolution", "seed", "watermark"}
    if mode in {"t2v", "r2v"}:
        allowed.add("ratio")
    params = dict(params)
    if set(params) - allowed:
        raise ProviderBindingError("Unsupported HappyHorse parameter")
    if "duration" in params and (
        type(params["duration"]) is not int or not 3 <= params["duration"] <= 15
    ):
        raise ProviderBindingError("Invalid HappyHorse duration")
    if "resolution" in params and (
        not isinstance(params["resolution"], str) or params["resolution"] not in _HAPPYHORSE_RESOLUTIONS
    ):
        raise ProviderBindingError("Invalid HappyHorse resolution")
    if "ratio" in params and (
        not isinstance(params["ratio"], str) or params["ratio"] not in _HAPPYHORSE_RATIOS
    ):
        raise ProviderBindingError("Invalid HappyHorse ratio")
    if "seed" in params and (
        type(params["seed"]) is not int or not 0 <= params["seed"] <= _HAPPYHORSE_SEED_MAX
    ):
        raise ProviderBindingError("Invalid HappyHorse seed")
    if "watermark" in params and type(params["watermark"]) is not bool:
        raise ProviderBindingError("Invalid HappyHorse watermark")
    return params


def _happyhorse_11_media_payload(media: tuple, *, mode: str) -> list[dict]:
    if mode == "t2v":
        if media:
            raise ProviderBindingError("HappyHorse text-to-video cannot include media")
        return []
    if mode == "i2v":
        if len(media) != 1:
            raise ProviderBindingError("HappyHorse image-to-video requires one first frame")
        return [_happyhorse_11_image_payload(media[0], role="first_frame", ordinal=0, mode=mode)]
    if not 1 <= len(media) <= 9:
        raise ProviderBindingError("HappyHorse reference-to-video requires 1 to 9 reference images")
    return [
        _happyhorse_11_image_payload(item, role="reference_image", ordinal=ordinal, mode=mode)
        for ordinal, item in enumerate(media)
    ]


def _happyhorse_11_image_payload(item, *, role: str, ordinal: int, mode: str) -> dict:
    version = item.version
    if (
        version.role != role
        or version.ordinal != ordinal
        or version.declared_media_type not in _HAPPYHORSE_IMAGE_MIMES
        or version.size_bytes > _HAPPYHORSE_IMAGE_MAX_BYTES
        or item.transport_binding != _HAPPYHORSE_IMAGE_BINDING
    ):
        raise ProviderBindingError("Unsupported HappyHorse image media")
    metadata = _validate_happyhorse_image_bytes(item)
    if mode == "i2v":
        if (
            metadata.width < 300
            or metadata.height < 300
            or max(metadata.width / metadata.height, metadata.height / metadata.width) > 2.5
        ):
            raise ProviderBindingError("HappyHorse first-frame dimensions are outside supported limits")
    else:
        if min(metadata.width, metadata.height) < 400:
            raise ProviderBindingError("HappyHorse reference image dimensions are outside supported limits")
    return {"type": role, "url": item.url}


def _validate_happyhorse_image_bytes(item):
    version = item.version
    if not isinstance(item.url, str):
        raise ProviderBindingError("HappyHorse image media bytes are invalid")
    prefix = f"data:{version.declared_media_type};base64,"
    if not item.url.startswith(prefix):
        raise ProviderBindingError("HappyHorse image media bytes are invalid")
    encoded = item.url[len(prefix):]
    if not encoded or len(encoded) > ((_HAPPYHORSE_IMAGE_MAX_BYTES + 2) // 3) * 4 + 4:
        raise ProviderBindingError("HappyHorse image media bytes are invalid")
    try:
        data = base64.b64decode(encoded.encode("ascii"), validate=True)
    except (binascii.Error, UnicodeEncodeError, ValueError):
        raise ProviderBindingError("HappyHorse image media bytes are invalid") from None
    try:
        return probe_frozen_image(version, data)
    except MediaProbeError:
        raise ProviderBindingError("HappyHorse image media bytes are invalid") from None


def _happyhorse_prompt_is_supported(prompt: object) -> bool:
    if not isinstance(prompt, str):
        return False
    total = 0
    for char in prompt:
        total += 2 if _is_han_ideograph(char) else 1
        if total > 5000:
            return False
    return True


def _is_han_ideograph(char: str) -> bool:
    name = unicodedata.name(char, "")
    return name.startswith(("CJK UNIFIED IDEOGRAPH", "CJK COMPATIBILITY IDEOGRAPH"))


def _happyhorse_videoedit_submission_payload(
    request: ProviderSubmission,
    *,
    require_transport_ready: bool = False,
) -> dict:
    """HappyHorse 1.0 VideoEdit with one provider-uploaded video plus optional images.

    Official reference checked 2026-10-13:
    https://help.aliyun.com/en/model-studio/happyhorse-video-edit-api-reference
    """
    intent = request.intent
    if request.provider_model_id != "happyhorse-1.0-video-edit" or request.scope.gateway != "dashscope_workspace":
        raise ProviderBindingError("Unsupported or mismatched HappyHorse VideoEdit model/mode/gateway")
    validate_happyhorse_videoedit_intent(intent)
    if not 1 <= len(request.media) <= 6:
        raise ProviderBindingError("HappyHorse VideoEdit requires one video and up to five reference images")
    video, *images = request.media
    media_payload = [
        _happyhorse_videoedit_video_payload(video, require_transport_ready=require_transport_ready)
    ]
    for ordinal, item in enumerate(images):
        media_payload.append(_happyhorse_videoedit_image_payload(item, ordinal=ordinal))

    params = dict(intent.params)
    return {
        "model": request.provider_model_id,
        "input": {"prompt": intent.prompt, "media": media_payload},
        "parameters": params,
    }


def validate_happyhorse_videoedit_intent(intent: GenerationIntent) -> None:
    """Pure HappyHorse VideoEdit intent admission check; performs no I/O."""
    if (
        not isinstance(intent, GenerationIntent)
        or intent.model_id != "happyhorse/happyhorse-1.0-video-edit#v2v"
        or intent.mode != "v2v"
        or intent.catalog_mode != "v2v"
    ):
        raise ProviderBindingError("Unsupported or mismatched HappyHorse VideoEdit intent")
    if not _happyhorse_prompt_is_supported(intent.prompt) or not intent.prompt.strip():
        raise ProviderBindingError("Invalid HappyHorse VideoEdit prompt")
    params = dict(intent.params)
    if set(params) - {"resolution", "seed", "watermark", "audio_setting"}:
        raise ProviderBindingError("Unsupported HappyHorse VideoEdit parameter")
    if "resolution" in params and (
        not isinstance(params["resolution"], str) or params["resolution"] not in {"720P", "1080P"}
    ):
        raise ProviderBindingError("Invalid HappyHorse VideoEdit resolution")
    if "seed" in params and (
        type(params["seed"]) is not int or not 0 <= params["seed"] <= _HAPPYHORSE_SEED_MAX
    ):
        raise ProviderBindingError("Invalid HappyHorse VideoEdit seed")
    if "watermark" in params and type(params["watermark"]) is not bool:
        raise ProviderBindingError("Invalid HappyHorse VideoEdit watermark")
    if "audio_setting" in params and (
        not isinstance(params["audio_setting"], str)
        or params["audio_setting"] not in {"auto", "origin"}
    ):
        raise ProviderBindingError("Invalid HappyHorse VideoEdit audio setting")


def validate_happyhorse_videoedit_video_metadata(metadata: FrozenMediaMetadata) -> None:
    """Pure reported video metadata preflight; does not inspect files or frames."""
    if (
        not isinstance(metadata, FrozenMediaMetadata)
        or metadata.kind != "video"
        or not 3 <= metadata.duration <= 60
        or metadata.width is None
        or metadata.height is None
        or min(metadata.width, metadata.height) < 360
        or max(metadata.width, metadata.height) > 4096
        or max(metadata.width / metadata.height, metadata.height / metadata.width) > 2.5
        or metadata.fps is None
        or not 8 <= metadata.fps <= 60
    ):
        raise ProviderBindingError("HappyHorse VideoEdit video metadata is outside supported limits")


def _happyhorse_videoedit_video_payload(item, *, require_transport_ready: bool) -> dict:
    version = item.version
    if (
        version.role != "reference_video"
        or version.ordinal != 0
        or version.declared_media_type not in {"video/mp4", "video/quicktime"}
        or version.size_bytes > 100 * 1024 * 1024
    ):
        raise ProviderBindingError("Unsupported HappyHorse VideoEdit video media")
    _validate_wan3_av_transport(item, require_transport_ready=require_transport_ready)
    validate_happyhorse_videoedit_video_metadata(item.frozen_metadata)
    return {"type": "video", "url": item.url}


def _happyhorse_videoedit_image_payload(item, *, ordinal: int) -> dict:
    version = item.version
    if (
        version.role != "reference_image"
        or version.ordinal != ordinal
        or version.declared_media_type not in _HAPPYHORSE_IMAGE_MIMES
        or version.size_bytes > _HAPPYHORSE_IMAGE_MAX_BYTES
        or item.transport_binding != _HAPPYHORSE_IMAGE_BINDING
    ):
        raise ProviderBindingError("Unsupported HappyHorse VideoEdit reference image")
    metadata = _validate_happyhorse_image_bytes(item)
    if (
        metadata.width < 300
        or metadata.height < 300
        or max(metadata.width / metadata.height, metadata.height / metadata.width) > 2.5
    ):
        raise ProviderBindingError("HappyHorse VideoEdit image dimensions are outside supported limits")
    return {"type": "reference_image", "url": item.url}


_WAN3_IMAGE_MIMES = {"image/png", "image/jpeg", "image/webp", "image/bmp"}
_WAN3_RATIOS = {"adaptive", "21:9", "16:9", "4:3", "1:1", "3:4", "9:16"}
_WAN3_IMAGE_ROLES = {"first_frame", "last_frame", "reference_image"}
_WAN3_VIDEO_ROLES = {"reference_video"}
_WAN3_AUDIO_ROLES = {"reference_audio"}
_WAN3_ROLE_ORDER = {
    "first_frame": 0,
    "last_frame": 1,
    "reference_image": 2,
    "reference_video": 3,
    "reference_audio": 4,
}
_WAN3_PENDING_AV_BINDING = "frozen_av_sha256_v1"
_WAN3_OSS_AV_BINDING = "dashscope_oss_sha256_v1"
_WAN3_IMAGE_BINDING = "data_uri_sha256_v1"
_WAN3_IMAGE_MAX_BYTES = 20 * 1024 * 1024


def _validate_wan3_media_items(
    request: ProviderSubmission,
    *,
    params: Mapping[str, object],
    require_transport_ready: bool,
) -> list[dict]:
    roles = [item.version.role for item in request.media]
    if len(request.media) > 20:
        raise ProviderBindingError("Wan3 media count exceeds the supported total")
    last_order = -1
    counts = {"image": 0, "video": 0, "audio": 0}
    durations = {"video": 0.0, "audio": 0.0}
    for item in request.media:
        version = item.version
        role = version.role
        if role not in _WAN3_ROLE_ORDER:
            raise ProviderBindingError("Unsupported Wan3 media role")
        order = _WAN3_ROLE_ORDER[role]
        if order < last_order:
            raise ProviderBindingError("Wan3 media roles must be ordered")
        last_order = order
        if role in _WAN3_IMAGE_ROLES:
            if (
                version.declared_media_type not in _WAN3_IMAGE_MIMES
                or version.size_bytes > _WAN3_IMAGE_MAX_BYTES
                or item.transport_binding != _WAN3_IMAGE_BINDING
            ):
                raise ProviderBindingError("Unsupported Wan3 image media metadata")
            metadata = _validate_wan3_image_bytes(item)
            if (
                not 240 <= metadata.width <= 8000
                or not 240 <= metadata.height <= 8000
                or max(metadata.width / metadata.height, metadata.height / metadata.width) > 8
            ):
                raise ProviderBindingError("Wan3 image dimensions are outside supported limits")
            if version.declared_media_type == "image/png" and metadata.has_alpha:
                raise ProviderBindingError("Wan3 PNG transparency is unsupported")
            counts["image"] += 1
        elif role in _WAN3_VIDEO_ROLES:
            if (
                version.declared_media_type not in {"video/mp4", "video/quicktime"}
                or version.size_bytes > 100 * 1024 * 1024
            ):
                raise ProviderBindingError("Unsupported Wan3 video media metadata")
            _validate_wan3_av_transport(item, require_transport_ready=require_transport_ready)
            metadata = item.frozen_metadata
            if metadata is None or metadata.kind != "video":
                raise ProviderBindingError("Wan3 video metadata is required")
            if (
                not 1 <= metadata.duration <= 15
                or metadata.fps is None or metadata.fps < 16
                or metadata.width is None or metadata.height is None
                or not 240 <= metadata.width <= 4096
                or not 240 <= metadata.height <= 4096
                or max(metadata.width / metadata.height, metadata.height / metadata.width) > 8
            ):
                raise ProviderBindingError("Wan3 video metadata is outside supported limits")
            counts["video"] += 1
            durations["video"] += metadata.duration
        else:
            if (
                version.declared_media_type not in {"audio/wav", "audio/mpeg"}
                or version.size_bytes > 15 * 1024 * 1024
            ):
                raise ProviderBindingError("Unsupported Wan3 audio media metadata")
            _validate_wan3_av_transport(item, require_transport_ready=require_transport_ready)
            metadata = item.frozen_metadata
            if metadata is None or metadata.kind != "audio" or not 1 <= metadata.duration <= 15:
                raise ProviderBindingError("Wan3 audio metadata is outside supported limits")
            counts["audio"] += 1
            durations["audio"] += metadata.duration
    if counts["image"] > 10 or counts["video"] > 5 or counts["audio"] > 5:
        raise ProviderBindingError("Wan3 media count exceeds supported role limits")
    if durations["video"] > 15 or durations["audio"] > 15:
        raise ProviderBindingError("Wan3 AV duration exceeds supported limits")
    output_duration = params.get("duration")
    if type(output_duration) is int and output_duration > 0 and durations["video"] + output_duration > 30:
        raise ProviderBindingError("Wan3 input and output video duration exceeds supported limits")
    if request.intent.mode == "t2v":
        if request.media:
            raise ProviderBindingError("Wan3 text-to-video cannot include media")
        return []
    if request.intent.mode == "i2v":
        if tuple(roles) not in {("first_frame",), ("first_frame", "last_frame")}:
            raise ProviderBindingError("Wan3 image-to-video requires first frame or first and last frames")
    elif request.intent.mode == "r2v":
        if not 1 <= len(request.media) <= 20 or any(role not in {"reference_image", "reference_video", "reference_audio"} for role in roles):
            raise ProviderBindingError("Wan3 reference-to-video requires reference media")
    else:
        raise ProviderBindingError("Unsupported or mismatched Wan3 model/mode/gateway")
    ordinals_by_role: dict[str, list[int]] = {}
    for item in request.media:
        ordinals_by_role.setdefault(item.version.role, []).append(item.version.ordinal)
    for role, ordinals in ordinals_by_role.items():
        if ordinals != list(range(len(ordinals))):
            raise ProviderBindingError("Wan3 media ordinals must be continuous within each role")
        if role in {"first_frame", "last_frame"} and ordinals != [0]:
            raise ProviderBindingError("Wan3 frame roles require ordinal zero")
    return [{"type": item.version.role, "url": item.url} for item in request.media]


def _validate_wan3_image_bytes(item):
    version = item.version
    if not isinstance(item.url, str):
        raise ProviderBindingError("Wan3 image media bytes are invalid")
    prefix = f"data:{version.declared_media_type};base64,"
    if not item.url.startswith(prefix):
        raise ProviderBindingError("Wan3 image media bytes are invalid")
    encoded = item.url[len(prefix):]
    if not encoded or len(encoded) > ((_WAN3_IMAGE_MAX_BYTES + 2) // 3) * 4 + 4:
        raise ProviderBindingError("Wan3 image media bytes are invalid")
    try:
        data = base64.b64decode(encoded.encode("ascii"), validate=True)
    except (binascii.Error, UnicodeEncodeError, ValueError):
        raise ProviderBindingError("Wan3 image media bytes are invalid") from None
    try:
        return probe_frozen_image(version, data)
    except MediaProbeError:
        raise ProviderBindingError("Wan3 image media bytes are invalid") from None


def _validate_wan3_av_transport(item, *, require_transport_ready: bool) -> None:
    if require_transport_ready:
        if item.transport_binding != _WAN3_OSS_AV_BINDING or not item.url.startswith("oss://"):
            raise ProviderBindingError("Wan3 AV media must be uploaded before submit")
    elif item.transport_binding not in {_WAN3_PENDING_AV_BINDING, _WAN3_OSS_AV_BINDING}:
        raise ProviderBindingError("Wan3 AV media requires verified frozen bytes or provider upload")


def _wan3_submission_payload(request: ProviderSubmission, *, require_transport_ready: bool = False) -> dict:
    """Validate frozen Wan3 inputs without catalog reads or default inference.

    Official contract checked 2026-10-12:
    https://help.aliyun.com/zh/model-studio/wan3-video-generation-api-reference
    This durable path accepts explicit T2V (no media), I2V first/last frames,
    and R2V reference image/video/audio only after product code has frozen bytes.
    """
    intent = request.intent
    if (
        request.provider_model_id not in {"wan3.0-video", "wan3.0-video-prime"}
        or intent.model_id != f"wan/{request.provider_model_id}#video"
        or intent.catalog_mode != "video"
        or request.scope.gateway != "dashscope_workspace"
    ):
        raise ProviderBindingError("Unsupported or mismatched Wan3 model/mode/gateway")
    if not isinstance(intent.prompt, str) or len(intent.prompt) > 20000:
        raise ProviderBindingError("Invalid submission prompt")
    if intent.mode == "t2v" and not intent.prompt.strip():
        raise ProviderBindingError("Wan3 text-to-video requires a prompt")
    params = dict(intent.params)
    if set(params) - {"duration", "resolution", "ratio", "seed", "prompt_extend", "watermark", "audio"}:
        raise ProviderBindingError("Unsupported submission parameter")
    if type(params.get("duration")) is not int or not (
        params["duration"] == -1 or 2 <= params["duration"] <= 30
    ):
        raise ProviderBindingError("A supported frozen duration is required")
    if (
        not isinstance(params.get("resolution"), str)
        or params["resolution"] not in {"480P", "720P", "1080P"}
    ):
        raise ProviderBindingError("A supported frozen resolution is required")
    if "ratio" in params and params["ratio"] not in _WAN3_RATIOS:
        raise ProviderBindingError("Invalid submission ratio")
    if "seed" in params and (
        type(params["seed"]) is not int or not -1 <= params["seed"] <= 2147483647
    ):
        raise ProviderBindingError("Invalid submission seed")
    for name in ("prompt_extend", "watermark", "audio"):
        if name in params and type(params[name]) is not bool:
            raise ProviderBindingError("Invalid submission boolean")
    media_payload = _validate_wan3_media_items(
        request, params=params, require_transport_ready=require_transport_ready,
    )
    inputs: dict[str, object] = {"prompt": intent.prompt}
    if media_payload:
        inputs["media"] = media_payload
    return {"model": request.provider_model_id, "input": inputs, "parameters": params}


def _wan_submission_payload(request: ProviderSubmission, *, require_transport_ready: bool = False) -> dict:
    if request.provider_model_id in PIXVERSE_PROVIDER_MODELS:
        return pixverse_submission_payload(request, require_transport_ready=require_transport_ready)
    if request.provider_model_id in VIDU_PROVIDER_MODELS:
        return vidu_submission_payload(request, require_transport_ready=require_transport_ready)
    if request.provider_model_id in KLING_PROVIDER_MODELS:
        return kling_submission_payload(
            request,
            require_transport_ready=require_transport_ready,
        )
    if request.provider_model_id == "happyhorse-1.0-video-edit":
        return _happyhorse_videoedit_submission_payload(
            request,
            require_transport_ready=require_transport_ready,
        )
    if request.provider_model_id in _HAPPYHORSE_11_MODEL_MODES:
        return _happyhorse_11_submission_payload(request)
    if request.provider_model_id == "wan2.7-t2v":
        return _wan27_t2v_submission_payload(request)
    if request.provider_model_id in {"wan2.7-r2v", "wan2.7-r2v-2026-06-12"}:
        return _wan27_r2v_submission_payload(request)
    if request.provider_model_id == "wan2.7-videoedit":
        return _wan27_videoedit_submission_payload(request, require_transport_ready=require_transport_ready)
    if request.provider_model_id in {"wan3.0-video", "wan3.0-video-prime"}:
        return _wan3_submission_payload(request, require_transport_ready=require_transport_ready)
    return _wan_first_frame_submission_payload(request)


class WanxModel(VideoGenModel):
    def __init__(self, config, *, env: Mapping[str, str] | None = None):
        super().__init__(config)
        self._explicit_env = env is not None
        self._env = os.environ if env is None else MappingProxyType(dict(env))

        self.params = self.config.get('params', {})

    @staticmethod
    def validate_submission(request: ProviderSubmission) -> None:
        """Check the submit contract before product admission, without I/O.

        Uses the same gateway and payload rules as submit. This does not prove
        credential ownership, media-byte binding, or permission to spend.
        """
        if not isinstance(request, ProviderSubmission):
            raise ProviderBindingError("Validation requires a frozen submission")
        _dashscope_scope_headers(request.scope)
        _wan_submission_payload(request, require_transport_ready=False)

    def submit(
        self, request: ProviderSubmission, *, credential: ResolvedCredential,
        on_submitted: Callable[[ProviderHandle], None],
    ) -> ProviderHandle:
        """One opt-in HTTP POST, then mandatory receipt persistence; no poll/fetch.

        Product admission must prove content binding before calling this method.
        Never use current config to fill this frozen request.
        """
        import inspect
        import re

        if not callable(on_submitted) or any(
            check(candidate)
            for candidate in (on_submitted, getattr(on_submitted, "__call__", None))
            for check in (
                inspect.iscoroutinefunction, inspect.isgeneratorfunction, inspect.isasyncgenfunction,
            )
        ):
            raise ProviderBindingError("Submission requires a synchronous receipt writer")
        if (
            not isinstance(request, ProviderSubmission)
            or not isinstance(credential, ResolvedCredential)
        ):
            raise ProviderBindingError("Submission requires an explicit credential binding")
        if credential.scope != request.scope or credential.credential_ref != request.credential_ref:
            raise ProviderBindingError("Credential binding does not match the submission")
        headers = _dashscope_task_headers(request.scope, credential)
        payload = _wan_submission_payload(request, require_transport_ready=True)
        headers.update({"Content-Type": "application/json", "X-DashScope-Async": "enable"})
        if any(item.url.startswith("oss://") for item in request.media):
            headers["X-DashScope-OssResourceResolve"] = "enable"
        submitted_at = time.time()
        try:
            response = requests.post(
                request.scope.base_url.rstrip('/')
                + "/api/v1/services/aigc/video-generation/video-synthesis",
                headers=headers, json=payload, auth=lambda prepared: prepared,
                timeout=120, allow_redirects=False,
            )
        except requests.RequestException:
            raise ProviderSubmitError("transport") from None
        try:
            result = response.json()
        except ValueError:
            raise ProviderSubmitError("protocol", status_code=response.status_code) from None
        output = result.get("output") if isinstance(result, dict) else None
        task_id = output.get("task_id") if isinstance(output, dict) else None
        if not isinstance(task_id, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_-]{0,255}", task_id,
        ):
            # Even a non-200 response can carry an accepted task. Without a
            # usable ID we cannot prove rejection, and must never retry here.
            raise ProviderSubmitError("missing_receipt", status_code=response.status_code) from None
        request_id = result.get("request_id")
        if not isinstance(request_id, str) or not re.fullmatch(
            r"[A-Za-z0-9_.:-]{1,256}", request_id,
        ):
            request_id = None  # Bad optional diagnostics must not erase a usable receipt.
        handle = ProviderHandle(
            provider_model_id=request.provider_model_id, task_id=task_id,
            scope=request.scope, credential_ref=request.credential_ref, request_id=request_id,
            query_capability=QueryCapability.BY_TASK_ID, submitted_at=submitted_at,
        )
        # Every writer exception, including StorageError, propagates unchanged.
        acknowledgement = on_submitted(handle)
        if acknowledgement is not None:
            # A synchronous wrapper can still return a coroutine/generator.
            # Never acknowledge persistence before that deferred body runs.
            if inspect.iscoroutine(acknowledgement) or inspect.isgenerator(acknowledgement):
                acknowledgement.close()
            raise TypeError("Receipt writer must return None synchronously")
        return handle

    def query(
        self, handle: ProviderHandle, *, credential: ResolvedCredential,
    ) -> ProviderObservation:
        """GET one original DashScope task; never resolve current routes or submit.

        Only explicitly bound receipts can use this opt-in interface. The old
        SDK/HTTP generate paths do not acquire query capability by model name.
        """
        import re

        scope = validate_query_binding(handle, credential)
        headers = _dashscope_task_headers(scope, credential)
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,255}", handle.task_id):
            raise ProviderBindingError("Invalid provider task ID")

        try:
            response = requests.get(
                f"{scope.base_url.rstrip('/')}/api/v1/tasks/{handle.task_id}",
                headers=headers,
                # An explicit auth handler prevents ambient .netrc credentials
                # from overwriting the already bound Authorization header.
                auth=lambda request: request,
                timeout=30, allow_redirects=False,
            )
        except requests.RequestException:
            # Transport exceptions may include URLs, headers or response bodies.
            raise ProviderQueryError("transport") from None
        if response.status_code != 200:
            raise ProviderQueryError("http", status_code=response.status_code)
        try:
            payload = response.json()
        except ValueError:
            raise ProviderQueryError("protocol") from None
        if not isinstance(payload, dict) or not isinstance(payload.get("output"), dict):
            raise ProviderQueryError("protocol")
        output = payload["output"]
        raw_status = output.get("task_status")
        if not isinstance(raw_status, str) or not re.fullmatch(
            r"[A-Za-z][A-Za-z0-9_-]{0,63}", raw_status,
        ):
            raise ProviderQueryError("protocol")
        if output.get("task_id") is not None and output["task_id"] != handle.task_id:
            raise ProviderQueryError("protocol")
        request_id = payload.get("request_id")
        error_code = output.get("code")
        for value in (request_id, error_code):
            if value is not None and (
                not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,256}", value)
            ):
                raise ProviderQueryError("protocol")
        status = {item.name: item for item in ProviderTaskStatus}.get(
            raw_status, ProviderTaskStatus.UNKNOWN,
        )
        video_url = None
        if status == ProviderTaskStatus.SUCCEEDED:
            video_url = output.get("video_url")
            try:
                validate_result_url(video_url)
            except ProviderBindingError:
                raise ProviderQueryError("protocol") from None
        return ProviderObservation(
            task_id=handle.task_id, status=status, raw_status=raw_status,
            request_id=request_id, video_url=video_url, error_code=error_code,
            source_handle=handle,
        )

    def fetch(
        self, handle: ProviderHandle, observation: ProviderObservation, *,
        output_root: Path, output_ref: str, writer_lock: DirectoryWriterLock,
        storage_guard: Callable[[], ContextManager[None]], max_bytes: int,
        on_prepared: Callable[[ProviderArtifact], None] | None = None,
        publish_policy: str = "replace",
    ) -> ProviderArtifact:
        """Download once, anonymously, into the caller's explicitly owned root.

        No current credentials, generation, query, retries, or product mutation.
        Callers may explicitly fetch this same receipt again after a failure.
        """
        _reject_deferred_callback(on_prepared)
        validate_fetch_binding(handle, observation)
        _dashscope_scope_headers(handle.scope)  # Validate provenance, never send its headers.

        return fetch_provider_result(
            handle, observation, output_root=output_root, output_ref=output_ref,
            writer_lock=writer_lock, storage_guard=storage_guard, max_bytes=max_bytes,
            on_prepared=on_prepared, publish_policy=publish_policy,
        )

    @property
    def api_key(self):
        api_key = self._env.get("DASHSCOPE_API_KEY")
        if self._explicit_env and not api_key:
            raise RuntimeError("DashScope API key is not configured for this environment")
        if not api_key:
            logger.warning("Dashscope API Key not found in config or environment variables.")
        return api_key

    def _resolve_provider_backend_for_model(self, model_name: str) -> str:
        try:
            return resolve_provider_backend(model_name, env=self._env)
        except (KeyError, ValueError):
            logger.debug(
                "Provider backend not registered for model %s, defaulting to dashscope.",
                model_name,
            )
            return "dashscope"
        except Exception as e:
            logger.warning(
                "Unexpected error resolving provider backend for model %s: %s. "
                "Falling back to dashscope.",
                model_name,
                e,
            )
            return "dashscope"

    def _resolver_model_for_media(self, model_name: str) -> str:
        # `wan2.5-i2v` follows the same DashScope media transport profile as `wan2.6-i2v`.
        if (model_name or "").strip().lower() == "wan2.5-i2v":
            return "wan2.6-i2v"
        # Wan2.7 I2V follows the same media transport profile as Wan2.6 I2V.
        if (model_name or "").strip().lower() == "wan2.7-i2v":
            return "wan2.6-i2v"
        # Wan2.7 R2V follows the same media transport profile as Wan2.6 R2V.
        if (model_name or "").strip().lower() == "wan2.7-r2v":
            return "wan2.6-r2v"
        return model_name

    def _build_dashscope_temp_url_resolver(self, model_name: str) -> Callable[[str], str]:
        return lambda local_path: self._create_dashscope_temp_url(local_path, model_name)

    @staticmethod
    def _merge_media_headers(target: Dict[str, str], source: Optional[Mapping[str, str]]) -> None:
        if not source:
            return
        for key, value in source.items():
            if value:
                target[key] = value

    def _encode_local_image_as_data_uri(self, local_path: str) -> str:
        mime_type, _ = mimetypes.guess_type(local_path)
        if not mime_type:
            mime_type = "image/png"
        with open(local_path, "rb") as f:
            encoded = base64.b64encode(f.read()).decode("ascii")
        return f"data:{mime_type};base64,{encoded}"

    def _resolve_sdk_image_input(
        self,
        *,
        model_name: str,
        img_path: Optional[str],
        img_url: Optional[str],
        uploader,
    ) -> Optional[str]:
        """
        Resolve image input for SDK-based I2V calls.

        This keeps DashScope provider-mode routing consistent for non-Wan model
        names (e.g. Kling/Vidu via DashScope) and prevents raw local filesystem
        paths from leaking into SDK payloads.
        """
        image_ref = img_path or img_url
        if not image_ref:
            return img_url

        resolver_model = self._resolver_model_for_media(model_name)
        backend = self._resolve_provider_backend_for_model(resolver_model)
        temp_url_resolver = self._build_dashscope_temp_url_resolver(resolver_model)

        try:
            resolved_image = resolve_media_input(
                image_ref,
                model_name=resolver_model,
                modality="image",
                backend=backend,
                uploader=uploader,
                output_root=self.output_root,
                dashscope_temp_url_resolver=temp_url_resolver,
            )
            if resolved_image.headers:
                logger.warning(
                    "SDK path for model %s received additional media headers %s; "
                    "continuing with resolved image value only.",
                    model_name,
                    list(resolved_image.headers.keys()),
                )
            return resolved_image.value
        except Exception as e:
            logger.warning(
                "Failed to resolve SDK image input via provider-media for model %s: %s. "
                "Falling back to raw image reference handling.",
                model_name,
                e,
            )

        if self.output_root is None:
            local_candidate = None
            if img_path and os.path.exists(img_path):
                local_candidate = img_path
            elif isinstance(img_url, str) and os.path.exists(img_url):
                local_candidate = img_url

            if local_candidate:
                return self._encode_local_image_as_data_uri(local_candidate)
        return img_url

    def _create_dashscope_temp_url(self, local_path: str, model_name: str) -> str:
        """
        Upload a local file to DashScope temporary storage and return an `oss://` URL.
        """
        if not os.path.exists(local_path):
            raise FileNotFoundError(f"Local media file not found: {local_path}")

        base = get_provider_base_url("DASHSCOPE", env=self._env)
        policy_url = f"{base}/api/v1/uploads"
        headers = {"Authorization": f"Bearer {self.api_key}"}

        policy_resp = requests.get(
            policy_url,
            params={"action": "getPolicy", "model": model_name},
            headers=headers,
            timeout=30,
        )
        if policy_resp.status_code != 200:
            raise RuntimeError(
                f"Failed to get DashScope upload policy (HTTP {policy_resp.status_code}): "
                f"{policy_resp.text}"
            )

        policy_body = policy_resp.json()
        policy_data = policy_body.get("output") or policy_body.get("data") or policy_body

        upload_host = policy_data.get("upload_host") or policy_data.get("host")
        if not upload_host:
            raise RuntimeError(f"DashScope upload policy missing upload_host: {policy_body}")

        upload_dir = policy_data.get("upload_dir") or policy_data.get("dir") or ""
        object_key = (
            policy_data.get("upload_file_path")
            or policy_data.get("object_key")
            or policy_data.get("key")
            or policy_data.get("file_path")
        )
        if not object_key:
            filename = os.path.basename(local_path)
            object_key = f"{upload_dir.rstrip('/')}/{filename}" if upload_dir else filename

        form_data: Dict[str, str] = {"key": object_key}
        field_map = {
            "policy": "policy",
            "signature": "signature",
            "oss_access_key_id": "OSSAccessKeyId",
            "x_oss_security_token": "x-oss-security-token",
            "x_oss_signature_version": "x-oss-signature-version",
            "x_oss_credential": "x-oss-credential",
            "x_oss_date": "x-oss-date",
            "x_oss_signature": "x-oss-signature",
            "x_oss_object_acl": "x-oss-object-acl",
            "x_oss_forbid_overwrite": "x-oss-forbid-overwrite",
            "success_action_status": "success_action_status",
            "callback": "callback",
        }
        for source_key, target_key in field_map.items():
            value = policy_data.get(source_key)
            if value:
                form_data[target_key] = str(value)

        # DashScope OSS policy requires x-oss-object-acl=private and
        # x-oss-forbid-overwrite=true; ensure they are always present
        # even if the policy response omits them (Bucket Policy enforcement)
        if "x-oss-object-acl" not in form_data:
            form_data["x-oss-object-acl"] = "private"
        if "x-oss-forbid-overwrite" not in form_data:
            form_data["x-oss-forbid-overwrite"] = "true"

        for key, value in policy_data.items():
            if key.startswith("x-oss-") and value and key not in form_data:
                form_data[key] = str(value)

        with open(local_path, "rb") as file_handle:
            files = {"file": (os.path.basename(local_path), file_handle)}
            upload_resp = requests.post(upload_host, data=form_data, files=files, timeout=120)

        if upload_resp.status_code not in (200, 201, 204):
            raise RuntimeError(
                f"Failed to upload temp media to DashScope (HTTP {upload_resp.status_code}): "
                f"{upload_resp.text}"
            )

        return f"oss://{object_key}"

    def generate(self, prompt: str, output_path: str, img_path: str = None, model_name: str = None, **kwargs) ->Tuple[str, float]:
        # Determine model - allow explicit override via model_name param or 'model' kwarg
        # Fix: pipeline.py passes 'model=task.model', we need to accept both
        if model_name:
            final_model_name = model_name
        elif kwargs.get('model'):
            final_model_name = kwargs.get('model')
            logger.info(f"Using model from kwargs: {final_model_name}")
        elif img_path or kwargs.get('img_url'):
            final_model_name = self.params.get('i2v_model_name', 'wan2.7-i2v')  # Default to I2V model
            logger.info(f"Using I2V model: {final_model_name}")
        else:
            final_model_name = self.params.get('model_name', 'wan2.7-i2v')
            logger.info(f"Using T2V model: {final_model_name}")

        size = self.params.get('size', '1280*720')
        prompt_extend = kwargs.get('prompt_extend', self.params.get('prompt_extend', True))
        watermark = kwargs.get('watermark', self.params.get('watermark', False))

        # New parameters - prioritize kwargs, fallback to params
        duration = kwargs.get('duration') or self.params.get('duration', 5)
        negative_prompt = kwargs.get('negative_prompt') or self.params.get('negative_prompt', '')
        audio_url = kwargs.get('audio_url') or self.params.get('audio_url', '')
        seed = kwargs.get('seed') or self.params.get('seed')
        if final_model_name == 'wan2.7-i2v' and kwargs.get('seed') is not None:
            seed = kwargs['seed']

        # Resolution mapping - normalize to uppercase for API
        resolution = kwargs.get('resolution') or self.params.get('resolution', '720P')
        resolution = resolution.upper()  # API requires uppercase (720P, 1080P)
        if resolution == '1080P':
            size = "1920*1080"
        elif resolution == '480P':
            size = "832*480"
        else:
            size = "1280*720"

        # Motion params
        camera_motion = kwargs.get('camera_motion')
        subject_motion = kwargs.get('subject_motion')

        logger.info(f"Starting generation with model: {final_model_name}")
        logger.info(f"Prompt: {prompt}")

        try:
            api_start_time = time.time()

            img_url = kwargs.get('img_url')
            uploader = (OSSImageUploader(env=self._env, output_root=self.output_root)
                        if self._explicit_env else OSSImageUploader())
            extra_media_headers: Dict[str, str] = {}

            # Use HTTP API for wan2.7-i2v, wan2.6-i2v, wan2.5-i2v, or wan2.6-r2v
            if final_model_name in ['wan2.7-i2v', 'wan2.6-i2v', 'wan2.6-i2v-flash', 'wan2.5-i2v']:
                resolver_model = self._resolver_model_for_media(final_model_name)
                backend = self._resolve_provider_backend_for_model(resolver_model)
                temp_url_resolver = self._build_dashscope_temp_url_resolver(resolver_model)

                image_ref = img_path or img_url
                if image_ref:
                    resolved_image = resolve_media_input(
                        image_ref,
                        model_name=resolver_model,
                        modality="reference_video",
                        backend=backend,
                        uploader=uploader,
                        output_root=self.output_root,
                        dashscope_temp_url_resolver=temp_url_resolver,
                    )

                    img_url = resolved_image.value
                    self._merge_media_headers(extra_media_headers, resolved_image.headers)

                if audio_url:
                    resolved_audio = resolve_media_input(
                        audio_url,
                        model_name=resolver_model,
                        modality="audio",
                        backend=backend,
                        uploader=uploader,
                        output_root=self.output_root,
                        dashscope_temp_url_resolver=temp_url_resolver,
                    )
                    audio_url = resolved_audio.value
                    self._merge_media_headers(extra_media_headers, resolved_audio.headers)

                is_wan27_i2v = final_model_name == 'wan2.7-i2v'
                if is_wan27_i2v and any(kwargs.get(name) is not None for name in ('ratio', 'shot_type')):
                    raise ValueError("wan2.7-i2v does not support explicit ratio or shot_type")
                shot_type = kwargs.get('shot_type', 'single')
                video_url = self._generate_wan_i2v_http(
                    prompt=prompt,
                    img_url=img_url,
                    model_name=final_model_name,
                    resolution=resolution,
                    duration=duration,
                    prompt_extend=prompt_extend,
                    negative_prompt=negative_prompt,
                    audio_url=audio_url,
                    watermark=watermark,
                    seed=seed,
                    shot_type=shot_type,
                    extra_headers=extra_media_headers,
                )
            elif final_model_name in ('wan2.6-r2v', 'wan2.7-r2v'):
                # R2V generation
                is_wan27_r2v = final_model_name == 'wan2.7-r2v'
                ref_key = 'ref_image_urls' if is_wan27_r2v else 'ref_video_urls'
                ref_urls = kwargs.get(ref_key, [])
                if not ref_urls:
                    raise ValueError(f"{ref_key} is required for {final_model_name}")

                resolver_model = self._resolver_model_for_media(final_model_name)
                backend = self._resolve_provider_backend_for_model(resolver_model)
                temp_url_resolver = self._build_dashscope_temp_url_resolver(resolver_model)

                modality = "image" if is_wan27_r2v else "reference_video"
                resolved_ref_urls = resolve_media_inputs(
                    ref_urls,
                    model_name=resolver_model,
                    modality=modality,
                    backend=backend,
                    uploader=uploader,
                    output_root=self.output_root,
                    dashscope_temp_url_resolver=temp_url_resolver,
                )
                ref_urls_resolved = [item.value for item in resolved_ref_urls]
                for resolved_item in resolved_ref_urls:
                    self._merge_media_headers(extra_media_headers, resolved_item.headers)

                shot_type = kwargs.get('shot_type', 'multi') # Default to multi for R2V as per PRD
                ratio = kwargs.get('ratio')

                video_url = self._generate_wan_r2v_http(
                    prompt=prompt,
                    ref_video_urls=ref_urls_resolved,
                    model_name=final_model_name,
                    size=size if not is_wan27_r2v else None,
                    ratio=ratio if is_wan27_r2v else None,
                    duration=duration,
                    audio=kwargs.get('audio', True), # Default to True for R2V
                    shot_type=shot_type,
                    seed=seed,
                    extra_headers=extra_media_headers,
                )
            elif final_model_name in ('wan2.7-t2v', 'wan2.7-videoedit'):
                # Wan2.7 T2V and VideoEdit via HTTP API
                ratio = kwargs.get('ratio')
                media = None
                resolver_model = self._resolver_model_for_media(final_model_name)
                backend = self._resolve_provider_backend_for_model(resolver_model)
                temp_url_resolver = self._build_dashscope_temp_url_resolver(resolver_model)

                if final_model_name == 'wan2.7-videoedit':
                    # VideoEdit: video + optional reference images
                    video_input = kwargs.get('video_url')
                    if video_input:
                        resolved_video = resolve_media_input(
                            video_input,
                            model_name=resolver_model,
                            modality="reference_video",
                            backend=backend,
                            uploader=uploader,
                            output_root=self.output_root,
                            dashscope_temp_url_resolver=temp_url_resolver,
                        )
                        media = [{"type": "video", "url": resolved_video.value}]
                        self._merge_media_headers(extra_media_headers, resolved_video.headers)
                    ref_image_urls = kwargs.get('ref_image_urls', [])
                    if ref_image_urls:
                        resolved_imgs = resolve_media_inputs(
                            ref_image_urls,
                            model_name=resolver_model,
                            modality="image",
                            backend=backend,
                            uploader=uploader,
                            output_root=self.output_root,
                            dashscope_temp_url_resolver=temp_url_resolver,
                        )
                        for r in resolved_imgs:
                            media.append({"type": "reference_image", "url": r.value})
                            self._merge_media_headers(extra_media_headers, r.headers)

                video_url = self._generate_hh_http(
                    prompt=prompt,
                    model_name=final_model_name,
                    media=media,
                    duration=duration,
                    ratio=ratio,
                    seed=seed,
                    watermark=watermark,
                    extra_headers=extra_media_headers,
                )
            elif final_model_name.startswith('happyhorse-1.0-'):
                # HappyHorse model family (I2V, R2V, T2V, V2V)
                resolver_model = final_model_name
                backend = self._resolve_provider_backend_for_model(resolver_model)
                temp_url_resolver = self._build_dashscope_temp_url_resolver(resolver_model)

                # Build media array based on model type
                media = None
                if final_model_name == 'happyhorse-1.0-i2v':
                    # I2V: first_frame image
                    image_ref = img_path or img_url
                    if image_ref:
                        resolved = resolve_media_input(
                            image_ref,
                            model_name=resolver_model,
                            modality="image",
                            backend=backend,
                            uploader=uploader,
                            output_root=self.output_root,
                            dashscope_temp_url_resolver=temp_url_resolver,
                        )
                        media = [{"type": "first_frame", "url": resolved.value}]
                        self._merge_media_headers(extra_media_headers, resolved.headers)

                elif final_model_name == 'happyhorse-1.0-r2v':
                    # R2V: reference images (1-9)
                    ref_image_urls = kwargs.get('ref_image_urls', [])
                    if not ref_image_urls:
                        raise ValueError("ref_image_urls is required for happyhorse-1.0-r2v")
                    resolved_refs = resolve_media_inputs(
                        ref_image_urls,
                        model_name=resolver_model,
                        modality="image",
                        backend=backend,
                        uploader=uploader,
                        output_root=self.output_root,
                        dashscope_temp_url_resolver=temp_url_resolver,
                    )
                    media = [{"type": "reference_image", "url": r.value} for r in resolved_refs]
                    for r in resolved_refs:
                        self._merge_media_headers(extra_media_headers, r.headers)

                elif final_model_name == 'happyhorse-1.0-video-edit':
                    # V2V: video + optional reference images (reserved for future)
                    ref_image_urls = kwargs.get('ref_image_urls', [])
                    video_input = kwargs.get('video_url')
                    if video_input:
                        resolved_video = resolve_media_input(
                            video_input,
                            model_name=resolver_model,
                            modality="reference_video",
                            backend=backend,
                            uploader=uploader,
                            output_root=self.output_root,
                            dashscope_temp_url_resolver=temp_url_resolver,
                        )
                        media = [{"type": "video", "url": resolved_video.value}]
                        self._merge_media_headers(extra_media_headers, resolved_video.headers)
                        # Add reference images if provided
                        if ref_image_urls:
                            resolved_imgs = resolve_media_inputs(
                                ref_image_urls,
                                model_name=resolver_model,
                                modality="image",
                                backend=backend,
                                uploader=uploader,
                                output_root=self.output_root,
                                dashscope_temp_url_resolver=temp_url_resolver,
                            )
                            for r in resolved_imgs:
                                media.append({"type": "reference_image", "url": r.value})
                                self._merge_media_headers(extra_media_headers, r.headers)
                # T2V: no media needed

                video_url = self._generate_hh_http(
                    prompt=prompt,
                    model_name=final_model_name,
                    media=media,
                    resolution=resolution,
                    duration=duration,
                    ratio=kwargs.get('ratio'),
                    seed=seed,
                    watermark=watermark,
                    audio_setting=kwargs.get('audio_setting'),
                    extra_headers=extra_media_headers,
                    on_provider_ids=kwargs.get('on_provider_ids'),
                )
            else:
                # Use SDK for other models
                if img_path or img_url:
                    img_url = self._resolve_sdk_image_input(
                        model_name=final_model_name,
                        img_path=img_path,
                        img_url=img_url,
                        uploader=uploader,
                    )
                video_url = self._generate_sdk(
                    prompt=prompt,
                    model_name=final_model_name,
                    img_url=img_url,
                    size=size,
                    duration=duration,
                    prompt_extend=prompt_extend,
                    negative_prompt=negative_prompt,
                    audio_url=audio_url,
                    watermark=watermark,
                    seed=seed,
                    camera_motion=camera_motion,
                    subject_motion=subject_motion
                )

            api_end_time = time.time()
            api_duration = api_end_time - api_start_time

            logger.info(f"Generation success. Video URL: {video_url}")
            logger.info(f"API duration: {api_duration:.2f}s")

            # Download video
            self._download_video(video_url, output_path)
            return output_path, api_duration

        except Exception as e:
            logger.error(f"Error during generation: {e}")
            raise

    def _generate_wan_i2v_http(self, prompt: str, img_url: str, model_name: str = "wan2.6-i2v",
                                  resolution: str = "720P", ratio: Optional[str] = None,
                                  duration: int = 5, prompt_extend: bool = True,
                                  negative_prompt: str = None, audio_url: str = None,
                                  watermark: bool = False, seed: int = None,
                                  shot_type: str = "single",
                                  extra_headers: Optional[Mapping[str, str]] = None) -> str:
        """Generate video using Wan I2V (2.5/2.6/2.7) via HTTP API (asynchronous with polling)."""
        base = get_provider_base_url("DASHSCOPE", env=self._env)
        create_url = f"{base}/api/v1/services/aigc/video-generation/video-synthesis"
        
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
            "X-DashScope-Async": "enable"  # Required for async mode
        }
        if extra_headers:
            headers.update(dict(extra_headers))
        
        payload: Dict[str, Any] = {
            "model": model_name,
            "input": {"prompt": prompt},
            "parameters": {
                "duration": duration,
                "prompt_extend": prompt_extend,
                "watermark": watermark,
            },
        }
        if model_name == "wan2.7-i2v":
            # Wan 2.7 represents both first frame and driving audio as media.
            media = [{"type": "first_frame", "url": img_url}]
            if audio_url:
                media.append({"type": "driving_audio", "url": audio_url})
            payload["input"]["media"] = media
            payload["parameters"]["resolution"] = resolution
        else:
            payload["input"]["img_url"] = img_url
            payload["parameters"].update({"audio": True, "shot_type": shot_type})
            if ratio:
                payload["parameters"]["ratio"] = ratio
            elif resolution:
                payload["parameters"]["resolution"] = resolution
            if audio_url:
                payload["input"]["audio_url"] = audio_url
                del payload["parameters"]["audio"]  # audio_url takes precedence

        if negative_prompt:
            payload["input"]["negative_prompt"] = negative_prompt
        if model_name == "wan2.7-i2v":
            if seed is not None:
                payload["parameters"]["seed"] = seed
        elif seed:
            payload["parameters"]["seed"] = seed
        
        logger.info(f"Calling {model_name} HTTP API (async)...")
        logger.info(f"Payload: {payload}")
        
        # Step 1: Create task
        response = requests.post(create_url, headers=headers, json=payload, timeout=120)  # 2 minutes for task creation
        
        logger.info(f"Create task response status: {response.status_code}")
        logger.info(f"Create task response body: {response.text[:500] if response.text else 'empty'}")
        
        if response.status_code != 200:
            error_data = response.json() if response.text else {}
            error_msg = error_data.get('message', response.text)
            raise RuntimeError(f"{model_name} task creation failed: {error_msg}")
        
        result = response.json()
        task_id = result.get('output', {}).get('task_id')
        if not task_id:
            raise RuntimeError(f"No task_id in response: {result}")
        
        logger.info(f"Task created: {task_id}")
        
        # Step 2: Poll for task completion
        poll_url = f"{base}/api/v1/tasks/{task_id}"
        poll_headers = {
            "Authorization": f"Bearer {self.api_key}"
        }
        
        max_wait_time = 900  # 15 minutes max wait (video generation takes longer)
        poll_interval = 15   # Poll every 15 seconds
        elapsed = 0
        
        while elapsed < max_wait_time:
            time.sleep(poll_interval)
            elapsed += poll_interval
            
            poll_response = requests.get(poll_url, headers=poll_headers, timeout=30)
            
            if poll_response.status_code != 200:
                logger.warning(f"Poll request failed: {poll_response.status_code}")
                continue
            
            poll_result = poll_response.json()
            task_status = poll_result.get('output', {}).get('task_status')
            
            logger.info(f"Task {task_id} status: {task_status} (elapsed: {elapsed}s)")
            
            if task_status == 'SUCCEEDED':
                video_url = poll_result.get('output', {}).get('video_url')
                if not video_url:
                    raise RuntimeError(f"No video_url in completed task: {poll_result}")
                
                logger.info(f"Task completed. Video URL: {video_url}")
                return video_url
            
            elif task_status == 'FAILED':
                error_msg = poll_result.get('output', {}).get('message', 'Unknown error')
                code = poll_result.get('output', {}).get('code', '')
                raise RuntimeError(f"{model_name} task failed: {code} - {error_msg}")
            
            elif task_status in ['CANCELED', 'UNKNOWN']:
                raise RuntimeError(f"{model_name} task {task_status}: {poll_result}")
            
            # PENDING or RUNNING - continue polling
        
        raise RuntimeError(f"{model_name} task timed out after {max_wait_time}s")

    def _generate_wan_r2v_http(self, prompt: str, ref_video_urls: list, model_name: str = "wan2.6-r2v",
                                  size: Optional[str] = "1280*720", ratio: Optional[str] = None,
                                  duration: int = 5, audio: bool = True,
                                  shot_type: str = "multi", seed: int = None,
                                  extra_headers: Optional[Mapping[str, str]] = None) -> str:
        """Generate video using Wan R2V (2.6/2.7) via HTTP API (asynchronous with polling)."""
        base = get_provider_base_url("DASHSCOPE", env=self._env)
        create_url = f"{base}/api/v1/services/aigc/video-generation/video-synthesis"

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
            "X-DashScope-Async": "enable"
        }
        if extra_headers:
            headers.update(dict(extra_headers))

        input_key = "reference_image_urls" if model_name.startswith("wan2.7-") else "reference_video_urls"
        payload = {
            "model": model_name,
            "input": {
                "prompt": prompt,
                input_key: ref_video_urls
            },
            "parameters": {
                "duration": duration,
                "audio": audio,
                "shot_type": shot_type
            }
        }

        # Wan2.7 uses ratio; older models use size
        if ratio:
            payload["parameters"]["ratio"] = ratio
        elif size:
            payload["parameters"]["size"] = size
        
        if seed:
            payload["parameters"]["seed"] = seed
        
        logger.info(f"Calling {model_name} HTTP API (async)...")
        logger.info(f"Payload: {payload}")
        
        # Step 1: Create task
        response = requests.post(create_url, headers=headers, json=payload, timeout=120)
        
        logger.info(f"Create task response status: {response.status_code}")
        logger.info(f"Create task response body: {response.text[:500] if response.text else 'empty'}")
        
        if response.status_code != 200:
            error_data = response.json() if response.text else {}
            error_msg = error_data.get('message', response.text)
            raise RuntimeError(f"{model_name} task creation failed: {error_msg}")
        
        result = response.json()
        task_id = result.get('output', {}).get('task_id')
        if not task_id:
            raise RuntimeError(f"No task_id in response: {result}")
        
        logger.info(f"Task created: {task_id}")
        
        # Step 2: Poll for task completion
        poll_url = f"{base}/api/v1/tasks/{task_id}"
        poll_headers = {
            "Authorization": f"Bearer {self.api_key}"
        }
        
        max_wait_time = 900  # 15 minutes max wait
        poll_interval = 15   # Poll every 15 seconds
        elapsed = 0
        
        while elapsed < max_wait_time:
            time.sleep(poll_interval)
            elapsed += poll_interval
            
            poll_response = requests.get(poll_url, headers=poll_headers, timeout=30)
            
            if poll_response.status_code != 200:
                logger.warning(f"Poll request failed: {poll_response.status_code}")
                continue
            
            poll_result = poll_response.json()
            task_status = poll_result.get('output', {}).get('task_status')
            
            logger.info(f"Task {task_id} status: {task_status} (elapsed: {elapsed}s)")
            
            if task_status == 'SUCCEEDED':
                video_url = poll_result.get('output', {}).get('video_url')
                if not video_url:
                    raise RuntimeError(f"No video_url in completed task: {poll_result}")
                
                logger.info(f"Task completed. Video URL: {video_url}")
                return video_url
            
            elif task_status == 'FAILED':
                error_msg = poll_result.get('output', {}).get('message', 'Unknown error')
                code = poll_result.get('output', {}).get('code', '')
                raise RuntimeError(f"{model_name} task failed: {code} - {error_msg}")
            
            elif task_status in ['CANCELED', 'UNKNOWN']:
                raise RuntimeError(f"{model_name} task {task_status}: {poll_result}")
            
        raise RuntimeError(f"{model_name} task timed out after {max_wait_time}s")

    def _generate_hh_http(self, *, prompt: str, model_name: str,
                           media: Optional[List[Dict[str, str]]] = None,
                           resolution: str = "1080P", duration: int = 5,
                           ratio: Optional[str] = None, seed: Optional[int] = None,
                           watermark: bool = False, audio_setting: Optional[str] = None,
                           extra_headers: Optional[Mapping[str, str]] = None,
                           on_provider_ids: Optional[Callable[[str, Optional[str], Optional[str]], None]] = None) -> str:
        """Generate video using HappyHorse models via HTTP API (asynchronous with polling).

        on_provider_ids: optional callback fired AS SOON AS task creation succeeds,
        BEFORE the long polling loop begins. Signature is
        `(provider_name, provider_task_id, provider_request_id)`. Used by the
        pipeline to persist Bailian / DashScope IDs onto our VideoTask so the
        user can paste them into the 百炼 console for diagnosis even while the
        task is still running (Issue 17). Storage failures must reach the caller
        before polling; other diagnostic callback failures remain warnings.
        """
        base = get_provider_base_url("DASHSCOPE", env=self._env)
        create_url = f"{base}/api/v1/services/aigc/video-generation/video-synthesis"

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
            "X-DashScope-Async": "enable"
        }
        if extra_headers:
            headers.update(dict(extra_headers))

        payload = {
            "model": model_name,
            "input": {
                "prompt": prompt,
            },
            "parameters": {
                "resolution": resolution,
                "duration": duration,
                "watermark": watermark,
            }
        }

        # Add media array if provided (I2V/R2V/V2V)
        if media:
            payload["input"]["media"] = media

        # Model-specific parameters
        if ratio and model_name != "happyhorse-1.0-i2v":
            # I2V doesn't support ratio parameter
            payload["parameters"]["ratio"] = ratio
        if seed:
            payload["parameters"]["seed"] = seed
        if audio_setting and model_name == "happyhorse-1.0-video-edit":
            payload["parameters"]["audio_setting"] = audio_setting

        logger.info(f"Calling HappyHorse {model_name} HTTP API (async)...")
        logger.info(f"Payload: {payload}")

        # Step 1: Create task
        response = requests.post(create_url, headers=headers, json=payload, timeout=120)

        logger.info(f"Create task response status: {response.status_code}")
        logger.info(f"Create task response body: {response.text[:500] if response.text else 'empty'}")

        if response.status_code != 200:
            error_data = response.json() if response.text else {}
            error_msg = error_data.get('message', response.text)
            raise RuntimeError(f"{model_name} task creation failed: {error_msg}")

        result = response.json()
        task_id = result.get('output', {}).get('task_id')
        request_id = result.get('request_id')
        if not task_id:
            raise RuntimeError(f"No task_id in response: {result}")

        logger.info(f"HappyHorse task created: {task_id} (request_id={request_id})")

        # Fire provider-id callback ASAP — pipeline persists these onto our
        # VideoTask so the UI / user can copy them while the task is still
        # running. A failed durable save must stop this execution path;
        # unrelated diagnostic callback failures retain their warning behavior.
        if on_provider_ids is not None:
            try:
                on_provider_ids("dashscope", task_id, request_id)
            except StorageError:
                raise
            except Exception as cb_err:
                logger.warning(f"on_provider_ids callback failed: {cb_err}")

        # Step 2: Poll for task completion
        poll_url = f"{base}/api/v1/tasks/{task_id}"
        poll_headers = {
            "Authorization": f"Bearer {self.api_key}"
        }

        max_wait_time = 900  # 15 minutes max wait
        poll_interval = 15   # Poll every 15 seconds
        elapsed = 0

        while elapsed < max_wait_time:
            time.sleep(poll_interval)
            elapsed += poll_interval

            poll_response = requests.get(poll_url, headers=poll_headers, timeout=30)

            if poll_response.status_code != 200:
                logger.warning(f"Poll request failed: {poll_response.status_code}")
                continue

            poll_result = poll_response.json()
            task_status = poll_result.get('output', {}).get('task_status')

            logger.info(f"Task {task_id} status: {task_status} (elapsed: {elapsed}s)")

            if task_status == 'SUCCEEDED':
                video_url = poll_result.get('output', {}).get('video_url')
                if not video_url:
                    raise RuntimeError(f"No video_url in completed task: {poll_result}")

                logger.info(f"HappyHorse task completed. Video URL: {video_url}")
                return video_url

            elif task_status == 'FAILED':
                error_msg = poll_result.get('output', {}).get('message', 'Unknown error')
                code = poll_result.get('output', {}).get('code', '')
                raise RuntimeError(f"{model_name} task failed: {code} - {error_msg}")

            elif task_status in ['CANCELED', 'UNKNOWN']:
                raise RuntimeError(f"{model_name} task {task_status}: {poll_result}")

            # PENDING or RUNNING - continue polling

        raise RuntimeError(f"{model_name} task timed out after {max_wait_time}s")

    def _generate_sdk(self, prompt: str, model_name: str, img_url: str = None, size: str = "1280*720",
                      duration: int = 5, prompt_extend: bool = True, negative_prompt: str = None,
                      audio_url: str = None, watermark: bool = False, seed: int = None,
                      camera_motion: str = None, subject_motion: str = None) -> str:
        """Generate video using Dashscope SDK (for older models)."""
        # Prepare arguments
        call_args = {
            "api_key": self.api_key,
            "model": model_name,
            "prompt": prompt,
            "size": size,
            "prompt_extend": prompt_extend,
            "watermark": watermark,
        }
        
        # Add optional arguments if they exist
        if negative_prompt:
            call_args['negative_prompt'] = negative_prompt
        if duration:
            call_args['duration'] = duration
        if audio_url:
            call_args['audio_url'] = audio_url
        if seed:
            call_args['seed'] = seed
        if camera_motion:
            call_args['camera_motion'] = camera_motion
        if subject_motion:
            call_args['motion_scale'] = subject_motion
        
        if img_url:
            call_args['img_url'] = img_url
            logger.info(f"Image to Video mode. Input Image URL: {img_url}")

        if self._explicit_env:
            call_args["base_address"] = f"{get_provider_base_url('DASHSCOPE', env=self._env)}/api/v1"
        rsp = VideoSynthesis.async_call(**call_args)
        
        if rsp.status_code != HTTPStatus.OK:
            logger.error(f"Failed to submit task: {rsp.code}, {rsp.message}")
            raise RuntimeError(f"Task submission failed: {rsp.message}")
        
        task_id = rsp.output.task_id
        logger.info(f"Task submitted. Task ID: {task_id}")
        
        # Wait for completion
        wait_kwargs = {"api_key": call_args["api_key"]}
        if self._explicit_env:
            wait_kwargs["base_address"] = call_args["base_address"]
        rsp = VideoSynthesis.wait(rsp, **wait_kwargs)
        
        logger.info(f"SDK response: {rsp}")

        if rsp.status_code != HTTPStatus.OK:
            logger.error(f"Task failed with status code: {rsp.status_code}, code: {rsp.code}, message: {rsp.message}")
            raise RuntimeError(f"Task failed: {rsp.message}")
        
        if rsp.output.task_status != 'SUCCEEDED':
             logger.error(f"Task finished but status is {rsp.output.task_status}. Code: {rsp.output.code}, Message: {rsp.output.message}")
             raise RuntimeError(f"Task failed with status {rsp.output.task_status}: {rsp.output.message}")

        video_url = rsp.output.video_url
        if not video_url:
             logger.error("Video URL is empty despite SUCCEEDED status.")
             raise RuntimeError("Video URL is empty.")
        
        return video_url

    def _download_video(self, url: str, path: str):
        logger.info(f"Downloading video to {path}...")

        from requests.adapters import HTTPAdapter
        from requests.packages.urllib3.util.retry import Retry

        session = requests.Session()
        retry = Retry(connect=3, backoff_factor=0.5)
        adapter = HTTPAdapter(max_retries=retry)
        session.mount('http://', adapter)
        session.mount('https://', adapter)

        temp_path = path + ".tmp"
        try:
            response = session.get(url, stream=True, timeout=120)  # 2 minutes for large video files
            response.raise_for_status()

            with open(temp_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)

            # Atomic rename
            os.rename(temp_path, path)
            logger.info("Download complete.")

        except Exception as e:
            logger.error(f"Failed to download video: {e}")
            if os.path.exists(temp_path):
                os.remove(temp_path)
            raise
