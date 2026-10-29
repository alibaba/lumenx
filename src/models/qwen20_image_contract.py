"""Versioned Qwen Image 2.0 values; no live catalog, settings or provider I/O."""

from collections.abc import Mapping
import re

from .dashscope_image_scope import validate_classic_image_scope
from .provider_tasks import ProviderBindingError, ProviderScope
from ..utils.generation_intent import GenerationIntent, RULES_VERSION

QWEN20_IMAGE_CONTRACT_VERSION = "provider-qwen20-t2i-bound-http-v1"
QWEN20_IMAGE_EDIT_CONTRACT_VERSION = "provider-qwen20-i2i-bound-http-v1"
QWEN20_IMAGE_CONTRACT_VERSIONS = (QWEN20_IMAGE_CONTRACT_VERSION, QWEN20_IMAGE_EDIT_CONTRACT_VERSION)
QWEN20_IMAGE_REFERENCE_MAX_BYTES = 10 * 1024 * 1024
QWEN20_IMAGE_PROVIDER_MODELS = frozenset({"qwen-image-2.0", "qwen-image-2.0-pro"})
_SIZE = re.compile(r"([1-9][0-9]{0,6})\*([1-9][0-9]{0,6})\Z")
# Local input-memory ceiling, not a claim to count the provider's prompt tokens.
_MAX_PROMPT_BYTES = 16384


def _qwen20_image_parameters(
    prompt: str,
    model_name: str,
    size: str,
    seed: int | None,
    watermark: bool,
    prompt_extend: bool,
    negative_prompt: str | None,
) -> dict:
    if (
        not isinstance(model_name, str)
        or model_name not in QWEN20_IMAGE_PROVIDER_MODELS
    ):
        raise ProviderBindingError("Unsupported bound Qwen image model")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ProviderBindingError("Invalid bound Qwen image prompt")
    try:
        if len(prompt.encode("utf-8")) > _MAX_PROMPT_BYTES:
            raise ProviderBindingError("Bound Qwen prompt exceeds local input limit")
        if negative_prompt is not None:
            if not isinstance(negative_prompt, str) or len(negative_prompt) > 500:
                raise ProviderBindingError("Invalid bound Qwen negative prompt")
            negative_prompt.encode("utf-8")
    except UnicodeEncodeError:
        raise ProviderBindingError("Invalid bound Qwen prompt encoding") from None
    dimensions = _SIZE.fullmatch(size) if isinstance(size, str) else None
    if dimensions is None:
        raise ProviderBindingError("Invalid bound Qwen image size")
    width, height = map(int, dimensions.groups())
    if not 512**2 <= width * height <= 2048**2:
        raise ProviderBindingError("Unsupported bound Qwen image size")
    if seed is not None and (type(seed) is not int or not 0 <= seed <= 2147483647):
        raise ProviderBindingError("Invalid bound Qwen image seed")
    if type(watermark) is not bool or type(prompt_extend) is not bool:
        raise ProviderBindingError("Invalid bound Qwen image flag")
    parameters = {
        "size": size,
        "n": 1,
        "watermark": watermark,
        "prompt_extend": prompt_extend,
    }
    if seed is not None:
        parameters["seed"] = seed
    if negative_prompt is not None:
        parameters["negative_prompt"] = negative_prompt
    return parameters


def qwen20_image_payload(
    prompt: str, model_name: str, size: str, seed: int | None, watermark: bool,
    prompt_extend: bool, negative_prompt: str | None,
    *, contract_version: object = QWEN20_IMAGE_CONTRACT_VERSION,
    reference_image: str | None = None,
) -> dict:
    parameters = _qwen20_image_parameters(
        prompt, model_name, size, seed, watermark, prompt_extend, negative_prompt,
    )
    content = []
    if contract_version == QWEN20_IMAGE_EDIT_CONTRACT_VERSION:
        if not isinstance(reference_image, str) or not reference_image.startswith("data:image/"):
            raise ProviderBindingError("Bound Qwen Edit requires one frozen image data URI")
        content.append({"image": reference_image})
    elif contract_version != QWEN20_IMAGE_CONTRACT_VERSION or reference_image is not None:
        raise ProviderBindingError("Unsupported bound Qwen image contract or reference")
    content.append({"text": prompt})
    return {
        "model": model_name,
        "input": {"messages": [{"role": "user", "content": content}]},
        "parameters": parameters,
    }


def validate_qwen20_image_intent(
    intent: GenerationIntent, provider_model_id: str, scope: ProviderScope,
    *, contract_version: object = QWEN20_IMAGE_CONTRACT_VERSION,
) -> None:
    """Validate already-frozen fields without filling any provider default."""
    if (
        not isinstance(intent, GenerationIntent)
        or not isinstance(provider_model_id, str)
        or provider_model_id not in QWEN20_IMAGE_PROVIDER_MODELS
        or intent.model_id != f"qwen/{provider_model_id}#image"
        or intent.catalog_mode != "image"
        or contract_version not in QWEN20_IMAGE_CONTRACT_VERSIONS
        or intent.mode != ("i2i" if contract_version == QWEN20_IMAGE_EDIT_CONTRACT_VERSION else "t2i")
        or intent.rules_version != RULES_VERSION
    ):
        raise ProviderBindingError("Unsupported bound Qwen image intent")
    params = intent.params
    if (
        not isinstance(params, Mapping)
        or set(params)
        - {"size", "watermark", "prompt_extend", "seed", "negative_prompt"}
        or not {"size", "watermark", "prompt_extend"}.issubset(params)
        or any(
            name in params and params[name] is None
            for name in ("seed", "negative_prompt")
        )
    ):
        raise ProviderBindingError("Invalid bound Qwen image parameters")
    _qwen20_image_parameters(
        intent.prompt,
        provider_model_id,
        params["size"],
        params.get("seed"),
        params["watermark"],
        params["prompt_extend"],
        params.get("negative_prompt"),
    )
    fields = {"mode", "prompt", "model_id", *(f"params.{name}" for name in params)}
    if not isinstance(intent.sources, Mapping) or any(
        type(key) is not str
        or key not in fields
        or type(value) is not str
        or value not in {"request", "preset", "catalog"}
        for key, value in intent.sources.items()
    ):
        raise ProviderBindingError("Invalid bound Qwen parameter provenance")
    if (
        not isinstance(intent.request_fields, (tuple, list))
        or any(
            type(field) is not str or field not in fields
            for field in intent.request_fields
        )
        or len(intent.request_fields) != len(set(intent.request_fields))
    ):
        raise ProviderBindingError("Invalid bound Qwen request fields")
    for value in (intent.preset_id, intent.preset_version):
        if value is not None:
            if (
                type(value) is not str
                or not value
                or value.strip() != value
                or any(ord(char) < 32 or ord(char) == 127 for char in value)
            ):
                raise ProviderBindingError("Invalid bound Qwen preset identity")
            try:
                value.encode("utf-8")
            except UnicodeEncodeError:
                raise ProviderBindingError(
                    "Invalid bound Qwen preset identity"
                ) from None
    validate_classic_image_scope(scope)
