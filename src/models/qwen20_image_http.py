"""Opt-in Qwen 2.0 synchronous images, with one POST and no retry authority.

Official contract captured 2026-10-20:
https://help.aliyun.com/zh/model-studio/qwen-image-api
"""

from pathlib import Path
import time
from urllib.parse import urlsplit

from .bound_image_http import download_private, request_json
from .dashscope_image_scope import validate_classic_image_scope
from .provider_tasks import (
    ProviderBindingError,
    ProviderSubmitError,
    ResolvedCredential,
    validate_result_url,
)
from .qwen20_image_contract import QWEN20_IMAGE_CONTRACT_VERSION, qwen20_image_payload
from .wanx import _dashscope_scope_headers


def _single_image(output: dict) -> str:
    choices = output.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ProviderSubmitError("protocol")
    images = []
    for choice in choices:
        if (
            not isinstance(choice, dict)
            or choice.get("finish_reason", "stop") != "stop"
        ):
            raise ProviderSubmitError("protocol")
        message = choice.get("message")
        if not isinstance(message, dict) or message.get("role") != "assistant":
            raise ProviderSubmitError("protocol")
        content = message.get("content")
        if not isinstance(content, list) or not content:
            raise ProviderSubmitError("protocol")
        for item in content:
            if (
                isinstance(item, dict)
                and set(item) == {"text"}
                and isinstance(item["text"], str)
            ):
                continue
            if not isinstance(item, dict) or set(item) != {"image"}:
                raise ProviderSubmitError("protocol")
            url = item["image"]
            try:
                validate_result_url(url)
                if urlsplit(url).scheme != "https":
                    raise ProviderBindingError("HTTPS image required")
            except ProviderBindingError:
                raise ProviderSubmitError("protocol") from None
            images.append(url)
    if len(images) != 1:
        raise ProviderSubmitError("protocol")
    return images[0]


def generate_bound_qwen20_image(
    prompt: str,
    output_path: str,
    credential: ResolvedCredential,
    *,
    model_name: str,
    size: str,
    max_bytes: int,
    prompt_extend: bool,
    seed: int | None = None,
    watermark: bool = False,
    negative_prompt: str | None = None,
    image_contract_version: object = QWEN20_IMAGE_CONTRACT_VERSION,
    reference_image: str | None = None,
) -> tuple[str, float]:
    payload = qwen20_image_payload(
        prompt, model_name, size, seed, watermark, prompt_extend, negative_prompt,
        contract_version=image_contract_version, reference_image=reference_image,
    )
    if not isinstance(credential, ResolvedCredential):
        raise ProviderBindingError("Explicit image credential required")
    validate_classic_image_scope(credential.scope)
    if type(max_bytes) is not int or max_bytes <= 0:
        raise ProviderBindingError("Invalid image download bound")
    try:
        if not isinstance(output_path, str) or not output_path or "\x00" in output_path:
            raise ValueError
        output = Path(output_path)
        if not output.parent.is_dir() or output.exists() or output.is_symlink():
            raise ValueError
    except (OSError, ValueError):
        raise ProviderBindingError("A new private image path is required") from None
    headers = {
        **_dashscope_scope_headers(credential.scope),
        "Authorization": f"Bearer {credential.secret}",
        "Content-Type": "application/json",
    }
    started = time.monotonic()
    result = request_json(
        "POST",
        credential.scope.base_url.rstrip("/")
        + "/api/v1/services/aigc/multimodal-generation/generation",
        headers,
        payload,
    )
    url = _single_image(result)
    elapsed = time.monotonic() - started
    download_private(url, output, max_bytes)
    return output_path, elapsed
