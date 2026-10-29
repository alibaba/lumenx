"""Versioned strict Wan2.7 image rules, with no configuration or provider I/O."""

from collections.abc import Mapping, Sequence
import re

from .provider_tasks import ProviderBindingError, ProviderScope
from ..utils.generation_intent import GenerationIntent
from ..utils.media_probe import FrozenImageMetadata
from .dashscope_image_scope import (
    validate_classic_image_scope as validate_wan27_image_scope,
    classic_image_scope_from_snapshot,
)

WAN27_IMAGE_CONTRACT_VERSION = "provider-wan27-t2i-bound-http-v1"
WAN27_IMAGE_EDIT_CONTRACT_VERSION = "provider-wan27-i2i-bound-http-v1"
WAN27_IMAGE_CONTRACT_VERSIONS = (WAN27_IMAGE_CONTRACT_VERSION, WAN27_IMAGE_EDIT_CONTRACT_VERSION)
WAN27_IMAGE_REFERENCE_MAX_BYTES = 20 * 1024 * 1024
WAN27_IMAGE_PROVIDER_MODELS = frozenset({"wan2.7-image", "wan2.7-image-pro"})
_SIZE = re.compile(r"([1-9][0-9]{0,4})\*([1-9][0-9]{0,4})\Z")


def wan27_image_scope_from_snapshot(snapshot: Mapping[str, str]) -> ProviderScope:
    return classic_image_scope_from_snapshot(snapshot)


def _wan27_image_parameters(
    prompt: str, model_name: str, size: str, seed: int | None, watermark: bool,
    contract_version: object,
) -> dict:
    if contract_version not in WAN27_IMAGE_CONTRACT_VERSIONS:
        raise ProviderBindingError("Unsupported bound image contract")
    if not isinstance(model_name, str) or model_name not in WAN27_IMAGE_PROVIDER_MODELS:
        raise ProviderBindingError("Unsupported bound image model")
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 5000:
        raise ProviderBindingError("Invalid bound image prompt")
    try:
        prompt.encode("utf-8")
    except UnicodeEncodeError:
        raise ProviderBindingError("Invalid bound image prompt") from None
    dimensions = _SIZE.fullmatch(size) if isinstance(size, str) else None
    if dimensions is None:
        raise ProviderBindingError("Invalid bound image size")
    width, height = map(int, dimensions.groups())
    edge = 4096 if model_name == "wan2.7-image-pro" and contract_version == WAN27_IMAGE_CONTRACT_VERSION else 2048
    if (
        not 768**2 <= width * height <= edge**2
        or width > 8 * height
        or height > 8 * width
    ):
        raise ProviderBindingError("Unsupported bound image size")
    if seed is not None and (type(seed) is not int or not 0 <= seed <= 2147483647):
        raise ProviderBindingError("Invalid bound image seed")
    if type(watermark) is not bool:
        raise ProviderBindingError("Invalid bound image watermark")
    parameters = {"size": size, "n": 1, "watermark": watermark}
    if seed is not None:
        parameters["seed"] = seed
    return parameters


def wan27_image_payload(
    prompt: str, model_name: str, size: str, seed: int | None, watermark: bool,
    *, contract_version: object = WAN27_IMAGE_CONTRACT_VERSION,
    images: Sequence[str] | None = None,
) -> dict:
    parameters = _wan27_image_parameters(prompt, model_name, size, seed, watermark, contract_version)
    content = []
    if contract_version == WAN27_IMAGE_EDIT_CONTRACT_VERSION:
        if (not isinstance(images, (tuple, list)) or not 1 <= len(images) <= 9
                or any(not isinstance(image, str) or not image.startswith("data:image/") for image in images)):
            raise ProviderBindingError("Bound Wan Edit requires frozen image data URIs")
        content.extend({"image": image} for image in images)
    elif images:
        raise ProviderBindingError("Bound Wan T2I cannot include reference images")
    content.append({"text": prompt})
    return {
        "model": model_name,
        "input": {"messages": [{"role": "user", "content": content}]},
        "parameters": parameters,
    }


def validate_wan27_image_reference(metadata: FrozenImageMetadata, size_bytes: int) -> None:
    """Check the provider limits against the already decoded frozen bytes."""
    if not 0 < size_bytes <= WAN27_IMAGE_REFERENCE_MAX_BYTES:
        raise ProviderBindingError("Unsupported Wan Edit reference size")
    if (not 240 <= metadata.width <= 8000 or not 240 <= metadata.height <= 8000
            or metadata.width > 8 * metadata.height or metadata.height > 8 * metadata.width):
        raise ProviderBindingError("Unsupported Wan Edit reference dimensions")
    if metadata.codec_name == "png" and metadata.has_alpha is not False:
        raise ProviderBindingError("Wan Edit PNG reference cannot contain transparency")


def validate_wan27_image_intent(
    intent: GenerationIntent, provider_model_id: str, scope: ProviderScope,
    *, contract_version: object = WAN27_IMAGE_CONTRACT_VERSION,
) -> None:
    """Validate already-frozen image values; never fill defaults or read a catalog."""
    if (
        not isinstance(intent, GenerationIntent)
        or not isinstance(provider_model_id, str)
        or provider_model_id not in WAN27_IMAGE_PROVIDER_MODELS
        or intent.model_id != f"wan/{provider_model_id}#image"
        or intent.catalog_mode != "image"
        or contract_version not in WAN27_IMAGE_CONTRACT_VERSIONS
        or intent.mode != ("i2i" if contract_version == WAN27_IMAGE_EDIT_CONTRACT_VERSION else "t2i")
    ):
        raise ProviderBindingError("Unsupported bound image intent")
    params = intent.params
    if (
        not isinstance(params, Mapping)
        or set(params) - {"size", "watermark", "seed"}
        or not {"size", "watermark"}.issubset(params)
        or "seed" in params
        and params["seed"] is None
    ):
        raise ProviderBindingError("Invalid bound image parameters")
    _wan27_image_parameters(
        intent.prompt,
        provider_model_id,
        params["size"],
        params.get("seed"),
        params["watermark"],
        contract_version,
    )
    validate_wan27_image_scope(scope)
