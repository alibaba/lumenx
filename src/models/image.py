import base64
from abc import ABC, abstractmethod
from http import HTTPStatus
from io import BytesIO
import mimetypes
import os
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import requests

try:
    import dashscope
    from dashscope import ImageSynthesis
except ImportError:  # pragma: no cover - optional in some dev setups
    dashscope = None
    ImageSynthesis = None

try:
    from PIL import Image, ImageOps, UnidentifiedImageError
except ImportError:  # pragma: no cover - optional in some dev setups
    Image = None
    ImageOps = None
    UnidentifiedImageError = OSError

from ..utils import get_logger
from ..utils.endpoints import get_provider_base_url
from ..utils.http_downloads import download_url_to_bytes, download_url_to_file
from ..utils.media_refs import MEDIA_REF_UNKNOWN, classify_media_ref
from ..utils.oss_utils import OSSImageUploader, is_object_key
from ..utils.provider_media import resolve_media_input
from ..utils.provider_registry import resolve_provider_backend

logger = get_logger(__name__)

IMAGE_PROVIDER_OPENAI = "openai"
IMAGE_PROVIDER_DASHSCOPE = "dashscope"
IMAGE2_OPENAI_HOST = "api.bltcy.ai"

OPENAI_T2I_MODEL_ALIAS = "openai-image"
OPENAI_I2I_MODEL_ALIAS = "openai-image-edit"
DEFAULT_OPENAI_IMAGE_BASE_URL = "https://api.bltcy.ai/v1"
DEFAULT_OPENAI_IMAGE_MODEL = "gpt-image2"
DEFAULT_OPENAI_IMAGE_EDIT_BASE_URL = "https://api.bltcy.ai/v1"
DEFAULT_OPENAI_IMAGE_EDIT_MODEL = "gpt-image2"
OPENAI_EDIT_REFERENCE_MAX_SIDE = 3840
# Match the CLI fallback single-image cap so both paths accept up to 50MB per reference image.
OPENAI_EDIT_REFERENCE_MAX_BYTES = 50 * 1024 * 1024
OPENAI_EDIT_REFERENCE_JPEG_QUALITY = 85
OPENAI_EDIT_REFERENCE_MIN_JPEG_QUALITY = 45
OPENAI_EDIT_REFERENCE_MIN_SIDE = 1024
OPENAI_EDIT_REFERENCE_SCALE_FACTOR = 0.85
OPENAI_GPT_IMAGE_EDIT_REFERENCE_LIMIT = 16
OPENAI_EDIT_REQUEST_MAX_BYTES_ENV = "OPENAI_EDIT_REQUEST_MAX_BYTES"
OPENAI_EDIT_REQUEST_MAX_BYTES = 8 * 1024 * 1024
OPENAI_EDIT_REQUEST_SAFETY_OVERHEAD_BYTES = 256 * 1024
OPENAI_EDIT_REQUEST_MIN_PER_REFERENCE_BYTES = 256 * 1024
OPENAI_EDIT_REQUEST_FIT_MAX_SIDES = (1920, 1600, 1360, 1156, 1024, 870, 768)
OPENAI_EDIT_REQUEST_FIT_MIN_SIDE = 768
OPENAI_EDIT_REFERENCE_SUPPORTED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp"}
OPENAI_IMAGE_DEFAULT_MAX_RETRY_ATTEMPTS = 5
OPENAI_IMAGE_RETRY_MAX_WAIT_SECONDS = 60

DASHSCOPE_WAN_T2I_DEFAULT = "wan2.6-t2i"
DASHSCOPE_WAN_I2I_DEFAULT = "wan2.6-image"


def _first_non_empty(*values: Optional[str]) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


class ImageGenModel(ABC):
    def __init__(self, config: Dict[str, Any]):
        self.config = config

    @abstractmethod
    def generate(self, prompt: str, output_path: str, **kwargs) -> Tuple[str, float]:
        raise NotImplementedError


class WanxImageModel(ImageGenModel):
    def __init__(self, config):
        super().__init__(config)
        self.params = config.get("params", {})

    @property
    def dashscope_api_key(self) -> str:
        api_key = os.getenv("DASHSCOPE_API_KEY")
        if not api_key:
            logger.warning("DashScope API Key not found in config or environment variables.")
        return api_key or ""

    @property
    def openai_image_api_key(self) -> str:
        api_key = _first_non_empty(
            self.config.get("api_key"),
            os.getenv("OPENAI_IMAGE_API_KEY"),
            os.getenv("OPENAI_IMAGE_EDIT_API_KEY"),
            os.getenv("OPENAI_API_KEY"),
        )
        if not api_key:
            logger.warning("OpenAI-compatible image API key not found.")
        return api_key

    @property
    def openai_image_edit_api_key(self) -> str:
        explicit_edit_key = _first_non_empty(
            self.config.get("edit_api_key"),
            os.getenv("OPENAI_IMAGE_EDIT_API_KEY"),
        )
        if explicit_edit_key:
            return explicit_edit_key

        api_key = _first_non_empty(
            os.getenv("OPENAI_IMAGE_API_KEY"),
            os.getenv("OPENAI_API_KEY"),
        )

        if not api_key:
            logger.warning("OpenAI-compatible image edit API key not found.")
        return api_key

    @property
    def openai_image_base_url(self) -> str:
        raw = _first_non_empty(
            self.config.get("base_url"),
            os.getenv("OPENAI_IMAGE_BASE_URL"),
            DEFAULT_OPENAI_IMAGE_BASE_URL,
        )
        return self._normalize_openai_base_url(raw)

    @property
    def openai_image_edit_base_url(self) -> str:
        raw = _first_non_empty(
            self.config.get("edit_base_url"),
            os.getenv("OPENAI_IMAGE_EDIT_BASE_URL"),
            DEFAULT_OPENAI_IMAGE_EDIT_BASE_URL,
        )
        return self._normalize_openai_base_url(raw)

    def generate(
        self,
        prompt: str,
        output_path: str,
        ref_image_path: str = None,
        ref_image_paths: list = None,
        model_name: str = None,
        **kwargs,
    ) -> Tuple[str, float]:
        all_ref_paths: List[str] = []
        if ref_image_path:
            all_ref_paths.append(ref_image_path)
        if ref_image_paths:
            all_ref_paths.extend(ref_image_paths)

        all_ref_paths = list(dict.fromkeys(all_ref_paths))
        has_reference = bool(all_ref_paths)
        requested_model_name = self._resolve_target_model_name(model_name, all_ref_paths)
        provider = self._resolve_image_provider(requested_model_name, has_reference=has_reference)
        final_model_name = self._resolve_runtime_model_name(
            requested_model_name,
            provider,
            has_reference=has_reference,
        )

        size = kwargs.pop("size", self.params.get("size", "1280*1280"))
        n = kwargs.pop("n", self.params.get("n", 1))
        negative_prompt = kwargs.pop("negative_prompt", None)
        kwargs.pop("model_name", None)

        ref_limit = self._reference_limit(final_model_name, provider)
        if len(all_ref_paths) > ref_limit:
            logger.warning(
                "Limiting reference images from %s to %s for model %s",
                len(all_ref_paths),
                ref_limit,
                final_model_name,
            )
            all_ref_paths = all_ref_paths[:ref_limit]

        logger.info("Starting image generation...")
        logger.info("Prompt: %s", prompt)
        logger.info(
            "Requested Model: %s, Runtime Model: %s, Provider: %s, Size: %s, N: %s",
            requested_model_name,
            final_model_name,
            provider,
            size,
            n,
        )

        if provider == IMAGE_PROVIDER_OPENAI:
            api_duration = self._generate_openai_image(
                prompt=prompt,
                output_path=output_path,
                model_name=final_model_name,
                size=size,
                n=n,
                negative_prompt=negative_prompt,
                ref_image_paths=all_ref_paths,
            )
            return output_path, api_duration

        if dashscope is not None:
            dashscope.api_key = self.dashscope_api_key

        api_start_time = time.time()
        if final_model_name == DASHSCOPE_WAN_T2I_DEFAULT:
            image_url = self._generate_wan26_http(prompt, size, n, negative_prompt)
        elif final_model_name == DASHSCOPE_WAN_I2I_DEFAULT:
            image_url = self._generate_wan26_image_http(
                prompt, size, n, negative_prompt, all_ref_paths
            )
        else:
            image_url = self._generate_sdk(
                prompt,
                final_model_name,
                size,
                n,
                negative_prompt,
                all_ref_paths,
                kwargs,
            )

        api_duration = time.time() - api_start_time
        logger.info("Generation success. Image URL: %s", image_url)
        logger.info("API duration: %.2fs", api_duration)
        self._download_image(image_url, output_path)
        return output_path, api_duration

    def _resolve_target_model_name(
        self, explicit_model_name: Optional[str], all_ref_paths: List[str]
    ) -> str:
        if explicit_model_name:
            return explicit_model_name
        if all_ref_paths:
            return self.params.get("i2i_model_name", self._default_model_name(for_i2i=True))
        return self.params.get("model_name", self._default_model_name(for_i2i=False))

    def _default_model_name(self, for_i2i: bool) -> str:
        provider = (
            self._resolve_default_edit_provider() if for_i2i else self._resolve_default_provider()
        )
        if provider == IMAGE_PROVIDER_OPENAI:
            return OPENAI_I2I_MODEL_ALIAS if for_i2i else OPENAI_T2I_MODEL_ALIAS
        return DASHSCOPE_WAN_I2I_DEFAULT if for_i2i else DASHSCOPE_WAN_T2I_DEFAULT

    def _resolve_default_provider(self) -> str:
        configured = (
            (self.params.get("provider") or os.getenv("IMAGE_PROVIDER") or "").strip().lower()
        )
        if configured in {IMAGE_PROVIDER_OPENAI, IMAGE_PROVIDER_DASHSCOPE}:
            return configured
        if any(
            [
                os.getenv("OPENAI_IMAGE_MODEL"),
                os.getenv("OPENAI_IMAGE_EDIT_MODEL"),
                os.getenv("OPENAI_IMAGE_BASE_URL"),
                os.getenv("OPENAI_IMAGE_EDIT_BASE_URL"),
                os.getenv("OPENAI_IMAGE_API_KEY"),
            ]
        ):
            return IMAGE_PROVIDER_OPENAI
        if os.getenv("OPENAI_API_KEY") and os.getenv("OPENAI_BASE_URL"):
            return IMAGE_PROVIDER_OPENAI
        return IMAGE_PROVIDER_DASHSCOPE

    def _resolve_default_edit_provider(self) -> str:
        configured = (
            (self.params.get("edit_provider") or os.getenv("IMAGE_EDIT_PROVIDER") or "")
            .strip()
            .lower()
        )
        if configured in {IMAGE_PROVIDER_OPENAI, IMAGE_PROVIDER_DASHSCOPE}:
            return configured
        if any(
            [
                self.config.get("edit_api_key"),
                self.config.get("edit_base_url"),
                os.getenv("OPENAI_IMAGE_EDIT_MODEL"),
                os.getenv("OPENAI_IMAGE_EDIT_BASE_URL"),
                os.getenv("OPENAI_IMAGE_EDIT_API_KEY"),
            ]
        ):
            return IMAGE_PROVIDER_OPENAI
        return self._resolve_default_provider()

    def _resolve_image_provider(self, model_name: str, has_reference: bool = False) -> str:
        normalized = (model_name or "").strip().lower()
        if normalized == OPENAI_T2I_MODEL_ALIAS:
            return self._resolve_default_provider()
        if normalized == OPENAI_I2I_MODEL_ALIAS:
            return self._resolve_default_edit_provider()
        if normalized.startswith("wan"):
            return IMAGE_PROVIDER_DASHSCOPE
        return (
            self._resolve_default_edit_provider()
            if has_reference
            else self._resolve_default_provider()
        )

    def _resolve_runtime_model_name(
        self, model_name: str, provider: str, has_reference: bool
    ) -> str:
        normalized = (model_name or "").strip().lower()
        if provider == IMAGE_PROVIDER_DASHSCOPE and normalized in {
            OPENAI_T2I_MODEL_ALIAS,
            OPENAI_I2I_MODEL_ALIAS,
        }:
            return DASHSCOPE_WAN_I2I_DEFAULT if has_reference else DASHSCOPE_WAN_T2I_DEFAULT
        return model_name

    def _reference_limit(self, model_name: str, provider: str) -> int:
        if provider == IMAGE_PROVIDER_OPENAI:
            if self._openai_model_supports_multi_reference_edit(model_name):
                return OPENAI_GPT_IMAGE_EDIT_REFERENCE_LIMIT
            return 1
        return 4 if model_name == DASHSCOPE_WAN_I2I_DEFAULT else 3

    def _openai_model_supports_multi_reference_edit(self, model_name: str) -> bool:
        resolved_model = self._resolve_openai_model_name(model_name, has_reference=True)
        normalized = (resolved_model or "").strip().lower().replace("_", "-")
        return normalized.startswith("gpt-image")

    def _normalize_openai_base_url(self, base_url: str) -> str:
        normalized = (base_url or "").strip().rstrip("/")
        for suffix in ("/chat/completions", "/images/generations", "/images/edits"):
            if normalized.endswith(suffix):
                normalized = normalized[: -len(suffix)]
                break
        return normalized.rstrip("/")

    def _normalize_openai_size(self, size: str) -> str:
        return (size or "1024*1024").strip().lower().replace("*", "x")

    def _is_image2_base_url(self, base_url: str) -> bool:
        return IMAGE2_OPENAI_HOST in (base_url or "").lower()

    def _is_using_fallback_openai_image_api_key(self) -> bool:
        return not _first_non_empty(os.getenv("OPENAI_IMAGE_API_KEY")) and bool(
            _first_non_empty(
                os.getenv("OPENAI_IMAGE_EDIT_API_KEY"),
                os.getenv("OPENAI_API_KEY"),
            )
        )

    def _is_using_fallback_openai_image_edit_api_key(self) -> bool:
        return not _first_non_empty(os.getenv("OPENAI_IMAGE_EDIT_API_KEY")) and bool(
            _first_non_empty(
                os.getenv("OPENAI_IMAGE_API_KEY"),
                os.getenv("OPENAI_API_KEY"),
            )
        )

    def _parse_openai_error_payload(self, response: requests.Response) -> Any:
        try:
            return response.json()
        except Exception:
            return response.text

    def _is_distributor_unavailable_error(self, error_payload: Any) -> bool:
        if isinstance(error_payload, dict):
            message = (
                error_payload.get("error", {}).get("message") or error_payload.get("message") or ""
            )
        else:
            message = str(error_payload or "")
        normalized = message.lower()
        return ("无可用渠道" in message) or ("no available distributor" in normalized)

    def _is_invalid_token_error(self, error_payload: Any) -> bool:
        if isinstance(error_payload, dict):
            message = (
                error_payload.get("error", {}).get("message") or error_payload.get("message") or ""
            )
            code = error_payload.get("error", {}).get("code") or error_payload.get("code") or ""
        else:
            message = str(error_payload or "")
            code = ""
        normalized = message.lower()
        return (
            ("无效的令牌" in message)
            or ("invalid token" in normalized)
            or (str(code).lower() == "invalid_request")
        )

    def _is_moderation_blocked_error(self, error_payload: Any) -> bool:
        if isinstance(error_payload, dict):
            error = (
                error_payload.get("error", {})
                if isinstance(error_payload.get("error"), dict)
                else {}
            )
            message = error.get("message") or error_payload.get("message") or ""
            code = error.get("code") or error_payload.get("code") or ""
        else:
            message = str(error_payload or "")
            code = ""
        normalized = message.lower()
        normalized_code = str(code or "").lower()
        return (
            "moderation_blocked" in normalized_code
            or "safety system" in normalized
            or "rejected by the safety" in normalized
        )

    def _should_retry_openai_response(self, response: requests.Response) -> bool:
        error_payload = self._parse_openai_error_payload(response)
        if self._is_distributor_unavailable_error(error_payload):
            return False
        if self._is_moderation_blocked_error(error_payload):
            return False

        if isinstance(error_payload, dict):
            message = (
                error_payload.get("error", {}).get("message") or error_payload.get("message") or ""
            )
        else:
            message = str(error_payload or "")
        normalized = message.lower()

        if response.status_code == 429:
            return True

        transient_markers = (
            "请稍后再试",
            "上游负载已饱和",
            "temporarily unavailable",
            "try again later",
            "rate limit",
            "overloaded",
            "timeout",
            "timed out",
            "upstream",
        )
        return response.status_code >= 500 or any(
            marker in normalized or marker in message for marker in transient_markers
        )

    def _openai_retry_after_seconds(self, response: requests.Response) -> Optional[float]:
        raw_retry_after = ""
        try:
            raw_retry_after = response.headers.get("Retry-After", "")
        except Exception:
            raw_retry_after = ""

        if not raw_retry_after:
            return None

        try:
            retry_after = float(raw_retry_after)
        except (TypeError, ValueError):
            return None

        if retry_after < 0:
            return None
        return min(retry_after, OPENAI_IMAGE_RETRY_MAX_WAIT_SECONDS)

    def _openai_retry_wait_seconds(
        self,
        *,
        attempt: int,
        response: Optional[requests.Response] = None,
    ) -> float:
        if response is not None:
            header_wait = self._openai_retry_after_seconds(response)
            if header_wait is not None:
                return header_wait
            if response.status_code == 429:
                return min(15 * attempt, OPENAI_IMAGE_RETRY_MAX_WAIT_SECONDS)

        return min(2 ** (attempt - 1), 8)

    def _format_openai_error(self, action: str, error_payload: Any, request_url: str) -> str:
        if (
            action == "generation"
            and self._is_image2_base_url(request_url)
            and self._is_invalid_token_error(error_payload)
            and self._is_using_fallback_openai_image_api_key()
        ):
            return (
                "Image2 生图当前未命中主用图像 API Key，已回退到备用图编 Key / 通用 OPENAI_API_KEY，"
                "但这把 key 对 api.bltcy.ai 无效。请在设置页同时保留“图像 API Key”和“图像编辑 API Key”，"
                "优先图像 Key，备用图编 Key。"
            )
        if (
            action == "edit"
            and self._is_image2_base_url(request_url)
            and self._is_invalid_token_error(error_payload)
            and self._is_using_fallback_openai_image_edit_api_key()
        ):
            return (
                "Image2 图编当前未命中主用图像编辑 API Key，已回退到备用图像 Key / 通用 OPENAI_API_KEY，"
                "但这把 key 对 api.bltcy.ai 无效。请在设置页同时保留“图像 API Key”和“图像编辑 API Key”，"
                "优先图编 Key，备用图像 Key。"
            )
        if self._is_moderation_blocked_error(error_payload):
            return f"OpenAI-compatible image {action} blocked by safety moderation: {error_payload}"
        return f"OpenAI-compatible image {action} failed: {error_payload}"

    def _resolve_openai_model_name(self, requested_model: str, has_reference: bool) -> str:
        normalized = (requested_model or "").strip()
        if normalized == OPENAI_T2I_MODEL_ALIAS:
            return _first_non_empty(os.getenv("OPENAI_IMAGE_MODEL"), DEFAULT_OPENAI_IMAGE_MODEL)
        if normalized == OPENAI_I2I_MODEL_ALIAS:
            return _first_non_empty(
                os.getenv("OPENAI_IMAGE_EDIT_MODEL"),
                DEFAULT_OPENAI_IMAGE_EDIT_MODEL,
                os.getenv("OPENAI_IMAGE_MODEL"),
                DEFAULT_OPENAI_IMAGE_MODEL,
            )
        if not normalized:
            if has_reference:
                return _first_non_empty(
                    os.getenv("OPENAI_IMAGE_EDIT_MODEL"),
                    DEFAULT_OPENAI_IMAGE_EDIT_MODEL,
                    os.getenv("OPENAI_IMAGE_MODEL"),
                    DEFAULT_OPENAI_IMAGE_MODEL,
                )
            return _first_non_empty(os.getenv("OPENAI_IMAGE_MODEL"), DEFAULT_OPENAI_IMAGE_MODEL)
        return normalized

    def _compose_openai_prompt(self, prompt: str, negative_prompt: Optional[str]) -> str:
        if not negative_prompt:
            return prompt
        return f"{prompt}\n\nAvoid: {negative_prompt}"

    def _openai_headers(self, api_key: Optional[str] = None) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {api_key or self.openai_image_api_key}",
        }

    def _post_openai_request_with_retries(
        self,
        *,
        request_url: str,
        action: str,
        timeout: int,
        **request_kwargs,
    ) -> requests.Response:
        retryable_errors = (
            requests.exceptions.ChunkedEncodingError,
            requests.exceptions.ConnectionError,
            requests.exceptions.SSLError,
            requests.exceptions.Timeout,
        )
        retryable_status_codes = {408, 409, 425, 429, 500, 502, 503, 504}
        max_attempts = OPENAI_IMAGE_DEFAULT_MAX_RETRY_ATTEMPTS
        last_error: Optional[Exception] = None
        last_response: Optional[requests.Response] = None

        for attempt in range(1, max_attempts + 1):
            try:
                response = requests.post(request_url, timeout=timeout, **request_kwargs)
            except retryable_errors as exc:
                last_error = exc
                if attempt >= max_attempts:
                    break
                wait_seconds = self._openai_retry_wait_seconds(attempt=attempt)
                logger.warning(
                    "OpenAI-compatible image %s request failed on attempt %s/%s: %s. Retrying in %ss.",
                    action,
                    attempt,
                    max_attempts,
                    exc,
                    wait_seconds,
                )
                time.sleep(wait_seconds)
                continue

            last_response = response
            if (
                response.status_code not in retryable_status_codes
                or not self._should_retry_openai_response(response)
                or attempt >= max_attempts
            ):
                return response

            wait_seconds = self._openai_retry_wait_seconds(
                attempt=attempt,
                response=response,
            )
            logger.warning(
                "OpenAI-compatible image %s request returned retryable status %s on attempt %s/%s. Retrying in %ss.",
                action,
                response.status_code,
                attempt,
                max_attempts,
                wait_seconds,
            )
            time.sleep(wait_seconds)

        if last_response is not None:
            return last_response
        raise RuntimeError(
            f"OpenAI-compatible image {action} request failed after {max_attempts} attempts: {last_error}"
        ) from last_error

    def _generate_openai_image(
        self,
        *,
        prompt: str,
        output_path: str,
        model_name: str,
        size: str,
        n: int,
        negative_prompt: Optional[str],
        ref_image_paths: List[str],
    ) -> float:
        required_api_key = (
            self.openai_image_edit_api_key if ref_image_paths else self.openai_image_api_key
        )
        if not required_api_key:
            raise ValueError(
                "OpenAI-compatible image provider requires a usable API key. "
                "T2I uses OPENAI_IMAGE_API_KEY / OPENAI_API_KEY; "
                "I2I uses OPENAI_IMAGE_EDIT_API_KEY / OPENAI_IMAGE_API_KEY / OPENAI_API_KEY."
            )

        api_start_time = time.time()
        if ref_image_paths:
            artifact = self._generate_openai_image_edit_http(
                prompt=prompt,
                model_name=model_name,
                size=size,
                n=n,
                negative_prompt=negative_prompt,
                ref_image_paths=ref_image_paths,
            )
        else:
            artifact = self._generate_openai_image_http(
                prompt=prompt,
                model_name=model_name,
                size=size,
                n=n,
                negative_prompt=negative_prompt,
            )

        api_duration = time.time() - api_start_time
        self._persist_openai_artifact(artifact, output_path)
        return api_duration

    def _generate_openai_image_http(
        self,
        *,
        prompt: str,
        model_name: str,
        size: str,
        n: int,
        negative_prompt: Optional[str],
    ) -> Dict[str, str]:
        request_url = f"{self.openai_image_base_url}/images/generations"
        payload = {
            "model": self._resolve_openai_model_name(model_name, has_reference=False),
            "prompt": self._compose_openai_prompt(prompt, negative_prompt),
            "n": int(n),
            "size": self._normalize_openai_size(size),
        }

        logger.info("Calling OpenAI-compatible image generation endpoint...")
        logger.info("Payload: %s", payload)

        response = self._post_openai_request_with_retries(
            request_url=request_url,
            action="generation",
            headers={
                **self._openai_headers(self.openai_image_api_key),
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=300,
        )
        if (
            response.status_code != 200
            and payload["model"] != DEFAULT_OPENAI_IMAGE_MODEL
            and self._is_image2_base_url(self.openai_image_base_url)
        ):
            error_payload = self._parse_openai_error_payload(response)
            if self._is_distributor_unavailable_error(error_payload):
                fallback_payload = {**payload, "model": DEFAULT_OPENAI_IMAGE_MODEL}
                logger.warning(
                    "Model %s is unavailable on Image2 provider. Retrying with fallback model %s.",
                    payload["model"],
                    DEFAULT_OPENAI_IMAGE_MODEL,
                )
                response = self._post_openai_request_with_retries(
                    request_url=request_url,
                    action="generation",
                    headers={
                        **self._openai_headers(self.openai_image_api_key),
                        "Content-Type": "application/json",
                    },
                    json=fallback_payload,
                    timeout=300,
                )
        return self._extract_openai_artifact(response, action="generation", request_url=request_url)

    def _generate_openai_image_edit_http(
        self,
        *,
        prompt: str,
        model_name: str,
        size: str,
        n: int,
        negative_prompt: Optional[str],
        ref_image_paths: List[str],
    ) -> Dict[str, str]:
        if not ref_image_paths:
            raise RuntimeError(
                "OpenAI-compatible image edit requires at least one reference image."
            )
        loaded_references = self._load_openai_reference_images_for_request(ref_image_paths)
        request_url = f"{self.openai_image_edit_base_url}/images/edits"
        data = {
            "model": self._resolve_openai_model_name(model_name, has_reference=True),
            "prompt": self._compose_openai_prompt(prompt, negative_prompt),
            "n": str(int(n)),
            "size": self._normalize_openai_size(size),
        }
        if len(loaded_references) == 1:
            filename, content, content_type = loaded_references[0]
            files: Any = {
                "image": (filename, content, content_type),
            }
        else:
            files = [
                ("image[]", (filename, content, content_type))
                for filename, content, content_type in loaded_references
            ]

        logger.info("Calling OpenAI-compatible image edit endpoint...")
        logger.info("Payload(meta): %s", data)

        response = self._post_openai_request_with_retries(
            request_url=request_url,
            action="edit",
            headers=self._openai_headers(self.openai_image_edit_api_key),
            data=data,
            files=files,
            timeout=300,
        )
        return self._extract_openai_artifact(response, action="edit", request_url=request_url)

    def _extract_openai_artifact(
        self,
        response: requests.Response,
        action: str,
        request_url: str = "",
    ) -> Dict[str, str]:
        logger.info("OpenAI-compatible image %s status: %s", action, response.status_code)
        logger.info("OpenAI-compatible image %s body: %s...", action, response.text[:500])

        if response.status_code != 200:
            error_data = self._parse_openai_error_payload(response)
            raise RuntimeError(self._format_openai_error(action, error_data, request_url))

        result = response.json()
        data = result.get("data") or []
        if not data:
            raise RuntimeError(f"OpenAI-compatible image {action} returned no data: {result}")

        first = data[0]
        image_url = first.get("url")
        if image_url:
            return {"kind": "url", "value": image_url}

        image_b64 = first.get("b64_json") or first.get("b64")
        if image_b64:
            return {"kind": "b64_json", "value": image_b64}

        raise RuntimeError(
            f"OpenAI-compatible image {action} returned no usable artifact: {result}"
        )

    def _persist_openai_artifact(self, artifact: Dict[str, str], output_path: str) -> None:
        if artifact["kind"] == "url":
            self._download_image(artifact["value"], output_path)
            return
        if artifact["kind"] == "b64_json":
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            with open(output_path, "wb") as output_file:
                output_file.write(base64.b64decode(artifact["value"]))
            return
        raise RuntimeError(f"Unsupported OpenAI-compatible image artifact kind: {artifact['kind']}")

    def _openai_edit_request_max_bytes(self) -> int:
        raw_value = (os.getenv(OPENAI_EDIT_REQUEST_MAX_BYTES_ENV) or "").strip()
        if not raw_value:
            return OPENAI_EDIT_REQUEST_MAX_BYTES
        try:
            parsed = int(raw_value)
        except ValueError:
            logger.warning(
                "Invalid %s=%r; using default %s bytes.",
                OPENAI_EDIT_REQUEST_MAX_BYTES_ENV,
                raw_value,
                OPENAI_EDIT_REQUEST_MAX_BYTES,
            )
            return OPENAI_EDIT_REQUEST_MAX_BYTES
        if parsed <= 0:
            logger.warning(
                "Invalid %s=%r; using default %s bytes.",
                OPENAI_EDIT_REQUEST_MAX_BYTES_ENV,
                raw_value,
                OPENAI_EDIT_REQUEST_MAX_BYTES,
            )
            return OPENAI_EDIT_REQUEST_MAX_BYTES
        return parsed

    def _load_openai_reference_images_for_request(
        self,
        ref_image_paths: List[str],
    ) -> List[Tuple[str, bytes, str]]:
        loaded_references = [
            self._load_openai_reference_image(ref_image_path) for ref_image_path in ref_image_paths
        ]
        request_budget = self._openai_edit_request_max_bytes()
        total_bytes = sum(len(content) for _, content, _ in loaded_references)
        if total_bytes <= request_budget:
            logger.info(
                "OpenAI edit reference aggregate preflight: refs=%s, bytes=%s, budget=%s.",
                len(loaded_references),
                total_bytes,
                request_budget,
            )
            return loaded_references

        logger.warning(
            "OpenAI edit reference aggregate bytes %s exceed request budget %s; "
            "compressing references before upload.",
            total_bytes,
            request_budget,
        )

        available_budget = max(
            OPENAI_EDIT_REQUEST_MIN_PER_REFERENCE_BYTES,
            request_budget - OPENAI_EDIT_REQUEST_SAFETY_OVERHEAD_BYTES,
        )
        per_reference_budget = max(
            OPENAI_EDIT_REQUEST_MIN_PER_REFERENCE_BYTES,
            available_budget // max(1, len(ref_image_paths)),
        )
        best_references: Optional[List[Tuple[str, bytes, str]]] = None
        best_total: Optional[int] = None

        for max_side in OPENAI_EDIT_REQUEST_FIT_MAX_SIDES:
            candidate_references = [
                self._load_openai_reference_image(
                    ref_image_path,
                    max_bytes=per_reference_budget,
                    force_transcode=True,
                    max_side=max_side,
                    min_side=OPENAI_EDIT_REQUEST_FIT_MIN_SIDE,
                )
                for ref_image_path in ref_image_paths
            ]
            candidate_total = sum(len(content) for _, content, _ in candidate_references)
            if best_total is None or candidate_total < best_total:
                best_total = candidate_total
                best_references = candidate_references
            if candidate_total <= request_budget:
                logger.info(
                    "OpenAI edit references fit aggregate budget after compression: "
                    "refs=%s, bytes=%s, budget=%s, max_side=%s, per_ref_budget=%s.",
                    len(candidate_references),
                    candidate_total,
                    request_budget,
                    max_side,
                    per_reference_budget,
                )
                return candidate_references

        if best_references is None or best_total is None:
            return loaded_references

        raise RuntimeError(
            "OpenAI-compatible image edit request would exceed the configured aggregate "
            f"payload budget after compression: {best_total} bytes > {request_budget} bytes. "
            f"Reduce reference count or lower {OPENAI_EDIT_REQUEST_MAX_BYTES_ENV} only if the "
            "upstream gateway is known to allow larger request bodies."
        )

    def _load_openai_reference_image(
        self,
        ref: str,
        *,
        max_bytes: Optional[int] = None,
        force_transcode: bool = False,
        max_side: Optional[int] = None,
        min_side: Optional[int] = None,
    ) -> Tuple[str, bytes, str]:
        resolved_ref = ref
        uploader = OSSImageUploader()

        if is_object_key(ref):
            if not uploader.is_configured:
                raise RuntimeError(
                    "Object storage is required to resolve object-key references for image editing."
                )
            resolved_ref = uploader.sign_url_for_api(ref)
            if not resolved_ref:
                raise RuntimeError(f"Failed to sign object-key reference: {ref}")

        if isinstance(resolved_ref, str) and resolved_ref.startswith(("http://", "https://")):
            content, headers = download_url_to_bytes(
                resolved_ref,
                timeout=(30, 60),
                max_bytes=OPENAI_EDIT_REFERENCE_MAX_BYTES * 4,
            )
            filename = os.path.basename(urlparse(resolved_ref).path) or "reference.png"
            content_type = headers.get("Content-Type") or self._guess_content_type(filename)
            return self._prepare_openai_edit_reference_image(
                filename,
                content,
                content_type,
                max_bytes=max_bytes,
                force_transcode=force_transcode,
                max_side=max_side,
                min_side=min_side,
            )

        candidate_paths = [resolved_ref]
        if isinstance(resolved_ref, str) and not os.path.isabs(resolved_ref):
            candidate_paths.append(os.path.join("output", resolved_ref))

        for candidate in candidate_paths:
            if isinstance(candidate, str) and os.path.exists(candidate):
                with open(candidate, "rb") as image_file:
                    return self._prepare_openai_edit_reference_image(
                        os.path.basename(candidate),
                        image_file.read(),
                        self._guess_content_type(candidate),
                        max_bytes=max_bytes,
                        force_transcode=force_transcode,
                        max_side=max_side,
                        min_side=min_side,
                    )

        raise RuntimeError(
            f"Reference image not found or unsupported for OpenAI-compatible edit: {ref}"
        )

    def _prepare_openai_edit_reference_image(
        self,
        filename: str,
        content: bytes,
        content_type: str,
        *,
        max_bytes: Optional[int] = None,
        force_transcode: bool = False,
        max_side: Optional[int] = None,
        min_side: Optional[int] = None,
    ) -> Tuple[str, bytes, str]:
        max_bytes = max_bytes if max_bytes is not None else OPENAI_EDIT_REFERENCE_MAX_BYTES
        max_side = max_side if max_side is not None else OPENAI_EDIT_REFERENCE_MAX_SIDE
        min_side = min_side if min_side is not None else OPENAI_EDIT_REFERENCE_MIN_SIDE
        normalized_content_type = (
            (content_type or self._guess_content_type(filename)).split(";")[0].strip().lower()
        )
        if not content or Image is None:
            return filename, content, normalized_content_type or self._guess_content_type(filename)

        try:
            with Image.open(BytesIO(content)) as source_image:
                image = (
                    ImageOps.exif_transpose(source_image)
                    if ImageOps is not None
                    else source_image.copy()
                )
                image.load()
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            logger.warning("Failed to preprocess OpenAI edit reference image %s: %s", filename, exc)
            return filename, content, normalized_content_type or self._guess_content_type(filename)

        original_width, original_height = image.size
        has_alpha = self._image_has_transparency(image)
        should_resize = max(image.size) > max_side
        supported_content_type = (
            normalized_content_type in OPENAI_EDIT_REFERENCE_SUPPORTED_CONTENT_TYPES
        )
        target_content_type = "image/png" if has_alpha else "image/jpeg"
        target_extension = ".png" if has_alpha else ".jpg"
        should_attempt_transcode = (
            force_transcode
            or should_resize
            or len(content) > max_bytes
            or not supported_content_type
        )

        logger.info(
            (
                "OpenAI edit reference preflight: file=%s, content_type=%s, original_size=%sx%s, "
                "bytes=%s, has_alpha=%s, max_side_cap=%s, byte_cap=%s, will_resize=%s, "
                "will_transcode=%s"
            ),
            filename,
            normalized_content_type,
            original_width,
            original_height,
            len(content),
            has_alpha,
            max_side,
            max_bytes,
            should_resize,
            should_attempt_transcode,
        )

        if not should_attempt_transcode:
            logger.info(
                "OpenAI edit reference kept original: file=%s, content_type=%s, size=%sx%s, bytes=%s",
                filename,
                normalized_content_type,
                original_width,
                original_height,
                len(content),
            )
            return filename, content, normalized_content_type or self._guess_content_type(filename)

        working = image
        if should_resize:
            scale = max_side / float(max(image.size))
            resized_width = max(1, int(round(image.size[0] * scale)))
            resized_height = max(1, int(round(image.size[1] * scale)))
            resampling = getattr(Image, "Resampling", Image).LANCZOS
            working = image.resize((resized_width, resized_height), resampling)
        else:
            resampling = getattr(Image, "Resampling", Image).LANCZOS

        if has_alpha:
            if working.mode not in {"RGBA", "LA"}:
                working = working.convert("RGBA")
        elif working.mode != "RGB":
            working = working.convert("RGB")

        processed_filename = self._replace_extension(filename, target_extension)
        working_base = working
        best_candidate: Optional[Tuple[bytes, Tuple[int, int], Optional[int]]] = None
        current_size = working_base.size

        while True:
            candidate_image = working_base
            if candidate_image.size != current_size:
                candidate_image = working_base.resize(current_size, resampling)

            if has_alpha:
                quality_candidates = [None]
            else:
                quality_candidates = [
                    OPENAI_EDIT_REFERENCE_JPEG_QUALITY,
                    75,
                    65,
                    55,
                    OPENAI_EDIT_REFERENCE_MIN_JPEG_QUALITY,
                ]

            for jpeg_quality in quality_candidates:
                processed_content = self._encode_openai_edit_reference_candidate(
                    candidate_image,
                    has_alpha=has_alpha,
                    jpeg_quality=jpeg_quality,
                )
                candidate_meta = (processed_content, candidate_image.size, jpeg_quality)
                if best_candidate is None or len(processed_content) < len(best_candidate[0]):
                    best_candidate = candidate_meta
                if len(processed_content) <= max_bytes:
                    best_candidate = candidate_meta
                    break

            if best_candidate is not None and len(best_candidate[0]) <= max_bytes:
                break

            max_side = max(current_size)
            if max_side <= min_side:
                break

            next_max_side = max(
                min_side,
                int(round(max_side * OPENAI_EDIT_REFERENCE_SCALE_FACTOR)),
            )
            if next_max_side >= max_side:
                break

            scale = next_max_side / float(max_side)
            current_size = (
                max(1, int(round(current_size[0] * scale))),
                max(1, int(round(current_size[1] * scale))),
            )

        if best_candidate is None:
            return filename, content, normalized_content_type or self._guess_content_type(filename)

        processed_content, processed_size, used_quality = best_candidate
        force_use_processed = force_transcode and (
            len(processed_content) < len(content)
            or len(content) > max_bytes
            or not supported_content_type
            or should_resize
        )
        should_use_processed = (
            should_resize
            or len(content) > max_bytes
            or not supported_content_type
            or force_use_processed
            or len(processed_content) + 1024 < len(content)
        )
        if not should_use_processed:
            logger.info(
                "OpenAI edit reference left unchanged after preprocessing check: file=%s, content_type=%s, original_size=%sx%s, bytes=%s",
                filename,
                normalized_content_type,
                original_width,
                original_height,
                len(content),
            )
            return filename, content, normalized_content_type or self._guess_content_type(filename)

        if len(processed_content) > max_bytes:
            logger.warning(
                "OpenAI edit reference image %s is still %s bytes after compression target %s bytes; using the smallest candidate %sx%s.",
                filename,
                len(processed_content),
                max_bytes,
                processed_size[0],
                processed_size[1],
            )

        logger.info(
            "Compressed OpenAI edit reference image %s from %sx%s/%s bytes to %sx%s/%s bytes (%s, quality=%s).",
            filename,
            original_width,
            original_height,
            len(content),
            processed_size[0],
            processed_size[1],
            len(processed_content),
            target_content_type,
            used_quality if used_quality is not None else "png-opt",
        )
        return processed_filename, processed_content, target_content_type

    def _image_has_transparency(self, image: "Image.Image") -> bool:
        if image.mode in {"RGBA", "LA"}:
            alpha = image.getchannel("A")
            return alpha.getextrema()[0] < 255
        if image.mode == "P":
            transparency = image.info.get("transparency")
            return transparency is not None
        return False

    def _replace_extension(self, filename: str, extension: str) -> str:
        stem, _ = os.path.splitext(filename or "reference")
        return f"{stem or 'reference'}{extension}"

    def _encode_openai_edit_reference_candidate(
        self,
        image: "Image.Image",
        *,
        has_alpha: bool,
        jpeg_quality: Optional[int],
    ) -> bytes:
        buffer = BytesIO()
        try:
            if has_alpha:
                image.save(buffer, format="PNG", optimize=True)
            else:
                image.save(
                    buffer,
                    format="JPEG",
                    quality=jpeg_quality or OPENAI_EDIT_REFERENCE_JPEG_QUALITY,
                    optimize=True,
                    progressive=True,
                )
        except OSError:
            buffer = BytesIO()
            if has_alpha:
                image.save(buffer, format="PNG")
            else:
                image.save(
                    buffer,
                    format="JPEG",
                    quality=jpeg_quality or OPENAI_EDIT_REFERENCE_JPEG_QUALITY,
                )
        return buffer.getvalue()

    def _generate_wan26_http(
        self, prompt: str, size: str, n: int, negative_prompt: str = None
    ) -> str:
        base = get_provider_base_url("DASHSCOPE")
        url = f"{base}/api/v1/services/aigc/multimodal-generation/generation"

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.dashscope_api_key}",
        }
        payload = {
            "model": DASHSCOPE_WAN_T2I_DEFAULT,
            "input": {
                "messages": [
                    {
                        "role": "user",
                        "content": [{"text": prompt}],
                    }
                ]
            },
            "parameters": {
                "prompt_extend": False,
                "watermark": False,
                "n": n,
                "size": size,
            },
        }
        if negative_prompt:
            payload["parameters"]["negative_prompt"] = negative_prompt

        logger.info("Calling Wan 2.6 T2I HTTP API...")
        logger.info("Payload: %s", payload)

        response = requests.post(url, headers=headers, json=payload, timeout=300)
        logger.info("Response status: %s", response.status_code)
        logger.info("Response body: %s...", response.text[:500])

        if response.status_code != 200:
            error_data = response.json() if response.text else {}
            error_msg = error_data.get("message", response.text)
            raise RuntimeError(f"Wan 2.6 API failed: {error_msg}")

        result = response.json()
        choices = result.get("output", {}).get("choices", [])
        if not choices:
            raise RuntimeError(f"No choices in response: {result}")

        first_choice = choices[0]
        content = first_choice.get("message", {}).get("content", [])
        if not content:
            raise RuntimeError(f"No content in choice: {first_choice}")

        image_url = content[0].get("image")
        if not image_url:
            raise RuntimeError(f"No image URL in content: {content}")
        return image_url

    def _generate_wan26_image_http(
        self,
        prompt: str,
        size: str,
        n: int,
        negative_prompt: str = None,
        ref_image_paths: list = None,
    ) -> str:
        base = get_provider_base_url("DASHSCOPE")
        create_url = f"{base}/api/v1/services/aigc/image-generation/generation"

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.dashscope_api_key}",
            "X-DashScope-Async": "enable",
        }

        content = []
        if ref_image_paths:
            for path in ref_image_paths[:4]:
                image_input = self._resolve_wan26_reference_image(path)
                if image_input:
                    content.append({"image": image_input})

        if ref_image_paths and not content:
            raise RuntimeError(
                "Wan 2.6 Image requires at least one usable reference image. "
                "Please provide a valid local image, public URL, or configure OSS."
            )

        content.append({"text": prompt})
        payload = {
            "model": DASHSCOPE_WAN_I2I_DEFAULT,
            "input": {
                "messages": [
                    {
                        "role": "user",
                        "content": content,
                    }
                ]
            },
            "parameters": {
                "prompt_extend": False,
                "watermark": False,
                "n": n,
                "size": size,
                "enable_interleave": False,
            },
        }
        if negative_prompt:
            payload["parameters"]["negative_prompt"] = negative_prompt

        logger.info("Calling Wan 2.6 Image HTTP API (async)...")
        logger.info("Payload: %s", payload)

        response = requests.post(create_url, headers=headers, json=payload, timeout=120)
        logger.info("Create task response status: %s", response.status_code)
        logger.info("Create task response body: %s", response.text[:500])

        if response.status_code != 200:
            error_data = response.json() if response.text else {}
            error_msg = error_data.get("message", response.text)
            raise RuntimeError(f"Wan 2.6 Image task creation failed: {error_msg}")

        result = response.json()
        task_id = result.get("output", {}).get("task_id")
        if not task_id:
            raise RuntimeError(f"No task_id in response: {result}")

        poll_url = f"{base}/api/v1/tasks/{task_id}"
        poll_headers = {
            "Authorization": f"Bearer {self.dashscope_api_key}",
        }

        max_wait_time = 600
        poll_interval = 10
        elapsed = 0
        while elapsed < max_wait_time:
            time.sleep(poll_interval)
            elapsed += poll_interval
            poll_response = requests.get(poll_url, headers=poll_headers, timeout=30)
            if poll_response.status_code != 200:
                logger.warning("Poll request failed: %s", poll_response.status_code)
                continue

            poll_result = poll_response.json()
            task_status = poll_result.get("output", {}).get("task_status")
            logger.info("Task %s status: %s (elapsed: %ss)", task_id, task_status, elapsed)

            if task_status == "SUCCEEDED":
                choices = poll_result.get("output", {}).get("choices", [])
                if not choices:
                    raise RuntimeError(f"No choices in completed task: {poll_result}")
                first_choice = choices[0]
                content = first_choice.get("message", {}).get("content", [])
                if not content:
                    raise RuntimeError(f"No content in choice: {first_choice}")
                image_url = content[0].get("image")
                if not image_url:
                    raise RuntimeError(f"No image URL in content: {content}")
                return image_url

            if task_status == "FAILED":
                logger.error("Task %s failed. Full response: %s", task_id, poll_result)
                error_msg = (
                    poll_result.get("output", {}).get("message", "")
                    or poll_result.get("output", {}).get("code", "")
                    or poll_result.get("message", "")
                    or poll_result.get("code", "")
                    or "Unknown error - check logs for full response"
                )
                raise RuntimeError(f"Wan 2.6 Image task failed: {error_msg}")

            if task_status in ["CANCELED", "UNKNOWN"]:
                raise RuntimeError(f"Wan 2.6 Image task {task_status}: {poll_result}")

        raise RuntimeError(f"Wan 2.6 Image task timed out after {max_wait_time}s")

    def _resolve_wan26_reference_image(
        self, path: str, model_name: str = DASHSCOPE_WAN_I2I_DEFAULT
    ) -> Optional[str]:
        uploader = OSSImageUploader()
        backend = self._resolve_provider_backend_for_model(model_name)

        try:
            resolved = resolve_media_input(
                path,
                model_name=model_name,
                modality="image",
                backend=backend,
                uploader=uploader,
            )
            return resolved.value
        except ValueError as e:
            if (
                uploader.is_configured
                and isinstance(path, str)
                and not path.startswith(("http://", "https://", "data:"))
                and not os.path.exists(path)
            ):
                signed_url = uploader.sign_url_for_api(path)
                if signed_url:
                    return signed_url

            ref_type = classify_media_ref(path)
            if ref_type == MEDIA_REF_UNKNOWN and os.path.isabs(path) and os.path.exists(path):
                if uploader.is_configured:
                    object_key = uploader.upload_file(path, sub_path="temp/ref_images")
                    if object_key:
                        signed_url = uploader.sign_url_for_api(object_key)
                        if signed_url:
                            return signed_url
                return self._encode_local_image_as_data_uri(path)

            logger.warning("Reference image could not be resolved: %s, reason: %s", path, e)
            return None

    def _resolve_provider_backend_for_model(self, model_name: str) -> str:
        try:
            return resolve_provider_backend(model_name)
        except (KeyError, ValueError):
            return IMAGE_PROVIDER_DASHSCOPE
        except Exception as e:
            logger.warning(
                "Unexpected error resolving provider backend for model %s: %s. Falling back to dashscope.",
                model_name,
                e,
            )
            return IMAGE_PROVIDER_DASHSCOPE

    def _encode_local_image_as_data_uri(self, path: str) -> str:
        mime_type, _ = mimetypes.guess_type(path)
        if not mime_type:
            mime_type = "image/png"
        with open(path, "rb") as image_file:
            encoded = base64.b64encode(image_file.read()).decode("ascii")
        return f"data:{mime_type};base64,{encoded}"

    def _generate_sdk(
        self,
        prompt: str,
        model_name: str,
        size: str,
        n: int,
        negative_prompt: Optional[str],
        all_ref_paths: list,
        kwargs: dict,
    ) -> str:
        if ImageSynthesis is None:
            raise RuntimeError(
                "dashscope package not installed. DashScope legacy image models are unavailable."
            )

        call_args = {
            "model": model_name,
            "prompt": prompt,
            "n": n,
            "size": size,
        }
        if negative_prompt:
            call_args["negative_prompt"] = negative_prompt
        call_args.update(kwargs)

        logger.info("SDK call_args: %s", {k: v for k, v in call_args.items() if k != "images"})

        if all_ref_paths:
            ref_image_urls = []
            uploader = OSSImageUploader()
            for path in all_ref_paths:
                if os.path.exists(path):
                    if uploader.is_configured:
                        object_key = uploader.upload_file(path, sub_path="temp/ref_images")
                        if object_key:
                            signed_url = uploader.sign_url_for_api(object_key)
                            ref_image_urls.append(signed_url)
                        else:
                            raise RuntimeError(f"Failed to upload reference image to OSS: {path}")
                    else:
                        logger.warning(
                            "OSS not configured, cannot upload reference image: %s", path
                        )
                elif path.startswith("http"):
                    ref_image_urls.append(path)
                else:
                    if is_object_key(path):
                        if uploader.is_configured:
                            signed_url = uploader.sign_url_for_api(path)
                            ref_image_urls.append(signed_url)
                        else:
                            raise ValueError(f"OSS not configured but Object Key provided: {path}")
                    else:
                        raise ValueError(f"Reference image not found: {path}")

            ref_limit = 4 if model_name == DASHSCOPE_WAN_I2I_DEFAULT else 3
            if len(ref_image_urls) > ref_limit:
                logger.warning(
                    "Limiting reference images from %s to %s", len(ref_image_urls), ref_limit
                )
                ref_image_urls = ref_image_urls[:ref_limit]
            call_args["images"] = ref_image_urls

        rsp = ImageSynthesis.call(**call_args)
        logger.info("SDK response: %s", rsp)

        if rsp.status_code != HTTPStatus.OK:
            logger.error(
                "Task failed with status code: %s, code: %s, message: %s",
                rsp.status_code,
                rsp.code,
                rsp.message,
            )
            raise RuntimeError(f"Task failed: {rsp.message}")

        if not hasattr(rsp, "output"):
            logger.error("Response has no output. Response: %s", rsp)
            raise RuntimeError("Response has no output.")

        results = rsp.output.get("results")
        url = rsp.output.get("url")
        if results and len(results) > 0:
            first_result = results[0]
            if isinstance(first_result, dict):
                image_url = first_result.get("url")
            else:
                image_url = getattr(first_result, "url", None)
        elif url:
            image_url = url
        else:
            logger.error("Unexpected response structure. Output: %s", rsp.output)
            raise RuntimeError("Could not find image URL in response.")

        return image_url

    def _guess_content_type(self, path_or_name: str) -> str:
        content_type, _ = mimetypes.guess_type(path_or_name)
        return content_type or "image/png"

    def _download_image(self, url: str, output_path: str):
        logger.info("Downloading image to %s...", output_path)
        download_url_to_file(url, output_path, timeout=(30, 180), max_attempts=3)
        logger.info("Download complete.")
