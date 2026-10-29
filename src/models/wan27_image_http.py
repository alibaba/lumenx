"""Explicitly bound Wan2.7 T2I transport and opt-in durable task operations.

Official contract checked 2026-10-14:
https://help.aliyun.com/zh/model-studio/wan-image-generation-and-editing-api-reference
Only explicitly versioned Core image receipts opt into this transport; legacy generation is unchanged.
"""

from pathlib import Path
import re
import time
from urllib.parse import urlsplit


from .wan27_image_contract import WAN27_IMAGE_CONTRACT_VERSION, validate_wan27_image_scope, wan27_image_payload as _payload
from .bound_image_http import download_private as _download_private, request_json as _request_json
from .bound_image_http import (
    ImageReceiptWriter, request_json_envelope, validate_receipt_writer, write_submitted_handle,
)
from . import provider_tasks as _image_tasks
from .provider_tasks import (
    ProviderBindingError, ProviderQueryError,
    ProviderSubmitError, ResolvedCredential, validate_result_url,
)
from .wanx import _dashscope_scope_headers

# Local path-safety bound, not a claimed provider task-ID format.
_TASK_ID = re.compile(r"[A-Za-z0-9_-]{1,256}\Z")
_POLL_ATTEMPTS = 60
_POLL_INTERVAL_SECONDS = 10



def _single_image(output: dict) -> str:
    # The official async HTTP schema declares assistant messages and typed
    # text/image content items; scan every choice rather than taking the first.
    choices = output.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ProviderQueryError("protocol")
    images = []
    for choice in choices:
        message = choice.get("message") if isinstance(choice, dict) else None
        if not isinstance(message, dict) or message.get("role") != "assistant":
            raise ProviderQueryError("protocol")
        content = message.get("content")
        if not isinstance(content, list) or not content:
            raise ProviderQueryError("protocol")
        for item in content:
            if not isinstance(item, dict):
                raise ProviderQueryError("protocol")
            if item.get("type") == "text" and set(item) == {"type", "text"} and isinstance(item["text"], str):
                continue
            if item.get("type") != "image" or set(item) != {"type", "image"}:
                raise ProviderQueryError("protocol")
            url = item["image"]
            try:
                validate_result_url(url)
                if urlsplit(url).scheme != "https":
                    raise ProviderBindingError("HTTPS result required")
            except ProviderBindingError:
                raise ProviderQueryError("protocol") from None
            images.append(url)
    if len(images) != 1:
        raise ProviderQueryError("protocol")
    return images[0]


def generate_bound_wan27_image(
    prompt: str, output_path: str, credential: ResolvedCredential, *,
    model_name: str, size: str, max_bytes: int,
    seed: int | None = None, watermark: bool = False,
) -> tuple[str, float]:
    """One POST maximum, serial read-only polling, then a new private file.

    A failure after submission is uncertain and never authorizes resubmission.
    The caller owns image normalization, artifact proof and publication.
    """
    payload = _payload(prompt, model_name, size, seed, watermark)
    if not isinstance(credential, ResolvedCredential):
        raise ProviderBindingError("Explicit image credential required")
    validate_wan27_image_scope(credential.scope)
    scope = credential.scope
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
    headers = {**_dashscope_scope_headers(scope), "Authorization": f"Bearer {credential.secret}"}
    base = scope.base_url.rstrip("/")
    started = time.monotonic()
    submitted = _request_json("POST", base + "/api/v1/services/aigc/image-generation/generation", {
        **headers, "Content-Type": "application/json", "X-DashScope-Async": "enable",
    }, payload)
    task_id = submitted.get("task_id")
    if (
        not isinstance(task_id, str) or _TASK_ID.fullmatch(task_id) is None
        or not isinstance(submitted.get("task_status"), str)
        or submitted["task_status"] not in {"PENDING", "RUNNING", "SUCCEEDED"}
    ):
        raise ProviderSubmitError("protocol")
    for _ in range(_POLL_ATTEMPTS):
        time.sleep(_POLL_INTERVAL_SECONDS)
        observed = _request_json("GET", base + "/api/v1/tasks/" + task_id, headers)
        if observed.get("task_id") != task_id:
            raise ProviderQueryError("protocol")
        status = observed.get("task_status")
        if not isinstance(status, str):
            raise ProviderQueryError("protocol")
        if status in {"PENDING", "RUNNING"}:
            continue
        if status != "SUCCEEDED":
            raise ProviderQueryError("terminal" if status in {"FAILED", "CANCELED"} else "protocol")
        url = _single_image(observed)
        elapsed = time.monotonic() - started
        _download_private(url, output, max_bytes)
        return output_path, elapsed
    raise ProviderQueryError("timeout")


def submit_bound_wan27_image(
    prompt: str, credential: ResolvedCredential, *, model_name: str, size: str,
    on_submitted: ImageReceiptWriter, seed: int | None = None, watermark: bool = False,
    image_contract_version: object = WAN27_IMAGE_CONTRACT_VERSION,
    reference_images: list[str] | None = None,
) -> _image_tasks.ProviderHandle:
    """Submit once and synchronously save its handle; never poll or download.

    The caller must own the original paid worker claim and persist the handle.
    No return value is available if that persistence fails.
    """
    validate_receipt_writer(on_submitted)
    payload = _payload(prompt, model_name, size, seed, watermark,
                       contract_version=image_contract_version, images=reference_images)
    if not isinstance(credential, ResolvedCredential):
        raise ProviderBindingError("Explicit image credential required")
    contract = _image_tasks.ProviderImageQueryContract.WAN27_IMAGE
    _image_tasks.validate_image_query_contract(contract, model_name, credential.scope)
    scope = credential.scope
    submitted_at = time.time()
    status_code, result = request_json_envelope(
        "POST", scope.base_url.rstrip("/") + "/api/v1/services/aigc/image-generation/generation",
        {**_dashscope_scope_headers(scope), "Authorization": f"Bearer {credential.secret}",
         "Content-Type": "application/json", "X-DashScope-Async": "enable"}, payload,
    )
    output = result.get("output")
    task_id = output.get("task_id") if isinstance(output, dict) else None
    if not isinstance(task_id, str):
        raise ProviderSubmitError("missing_receipt", status_code=status_code)
    request_id = result.get("request_id")
    if not isinstance(request_id, str) or re.fullmatch(r"[A-Za-z0-9_.:-]{1,256}", request_id) is None:
        request_id = None
    try:
        handle = _image_tasks.ProviderHandle(
            schema_version=2, provider_model_id=model_name, task_id=task_id,
            scope=scope, credential_ref=credential.credential_ref, request_id=request_id,
            query_capability=_image_tasks.QueryCapability.BY_TASK_ID,
            query_contract=contract, submitted_at=submitted_at,
        )
    except ProviderBindingError:
        raise ProviderSubmitError("missing_receipt", status_code=status_code) from None
    # Retain a usable ID even on an unusual status; that is defensive behavior,
    # not a claim that the provider guarantees task IDs on error responses.
    write_submitted_handle(handle, on_submitted)
    return handle


def query_bound_wan27_image(
    handle: _image_tasks.ProviderHandle, *, credential: ResolvedCredential,
) -> _image_tasks.ProviderObservation:
    """Observe one original image task without input reconstruction or resubmission."""
    if not isinstance(handle, _image_tasks.ProviderHandle):
        raise ProviderBindingError("Explicit image handle required")
    handle = _image_tasks.ProviderHandle.from_dict(handle.to_dict())
    scope = _image_tasks.validate_query_binding(handle, credential, media_kind="image")
    if handle.query_contract is not _image_tasks.ProviderImageQueryContract.WAN27_IMAGE:
        raise ProviderBindingError("Wan image query requires its original contract")
    status_code, result = request_json_envelope(
        "GET", scope.base_url.rstrip("/") + _image_tasks.image_query_path(handle),
        {**_dashscope_scope_headers(scope), "Authorization": f"Bearer {credential.secret}"},
    )
    if status_code != 200:
        raise ProviderQueryError("http", status_code=status_code)
    output = result.get("output")
    if not isinstance(output, dict) or result.get("code") or output.get("task_id") != handle.task_id:
        raise ProviderQueryError("protocol")
    raw_status = output.get("task_status")
    if not isinstance(raw_status, str):
        raise ProviderQueryError("protocol")
    try:
        status = _image_tasks.image_status_from_raw(handle, raw_status)
        request_id, error_code = result.get("request_id"), output.get("code")
        for value in (request_id, error_code):
            if value is not None and (
                not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9_.:-]{1,256}", value) is None
            ):
                raise ProviderBindingError("Invalid image query diagnostic")
        image_url = _single_image(output) if status is _image_tasks.ProviderTaskStatus.SUCCEEDED else None
        return _image_tasks.ProviderObservation(
            task_id=output["task_id"], status=status, raw_status=raw_status,
            source_handle=handle, request_id=request_id, error_code=error_code, image_url=image_url,
        )
    except ProviderBindingError:
        raise ProviderQueryError("protocol") from None
