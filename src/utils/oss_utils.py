import mimetypes
import os
import time
from urllib.parse import unquote, urlparse
from typing import Optional

try:
    import oss2
except ImportError:  # pragma: no cover - optional dependency in local dev
    oss2 = None

import requests

from . import get_logger
from .media_refs import MEDIA_REF_LOCAL_PATH, MEDIA_REF_OBJECT_KEY, classify_media_ref

try:
    import tos
    from tos import HttpMethodType
    from tos.exceptions import TosClientError, TosServerError
except ImportError:  # pragma: no cover - optional dependency in local dev
    tos = None
    HttpMethodType = None
    TosClientError = Exception
    TosServerError = Exception


logger = get_logger(__name__)

# Default configuration
DEFAULT_OSS_BASE_PATH = "lumenx"
SIGN_URL_EXPIRES_DISPLAY = 7200  # 2 hours for frontend display
SIGN_URL_EXPIRES_API = 1800  # 30 minutes for AI API calls
TOS_FALLBACK_PUT_EXPIRES = 600
TOS_FALLBACK_MAX_FILE_SIZE = 20 * 1024 * 1024

STORAGE_PROVIDER_NONE = ""
STORAGE_PROVIDER_OSS = "oss"
STORAGE_PROVIDER_TOS = "tos"


def _first_non_empty_env(*keys: str) -> str:
    for key in keys:
        value = os.getenv(key)
        if value and value.strip():
            return value.strip()
    return ""


def _normalize_provider(provider: Optional[str]) -> str:
    value = (provider or "").strip().lower()
    if value in {STORAGE_PROVIDER_OSS, STORAGE_PROVIDER_TOS}:
        return value
    if value in {"local", "none", "disabled", "off"}:
        return STORAGE_PROVIDER_NONE
    return STORAGE_PROVIDER_NONE


def _normalize_endpoint(provider: str, endpoint: str) -> str:
    value = (endpoint or "").strip().rstrip("/")
    if not value:
        return ""

    if provider == STORAGE_PROVIDER_TOS and not value.startswith(("http://", "https://")):
        return f"https://{value}"

    if provider == STORAGE_PROVIDER_OSS and value.startswith(("http://", "https://")):
        return value.split("://", 1)[1]

    return value


def get_object_storage_provider() -> str:
    raw_provider = os.getenv("OBJECT_STORAGE_PROVIDER")
    if raw_provider is not None:
        explicit = _normalize_provider(raw_provider)
        if explicit or not raw_provider.strip() or raw_provider.strip().lower() in {"local", "none", "disabled", "off"}:
            return explicit

    has_tos = all(
        [
            _first_non_empty_env("TOS_ACCESS_KEY_ID", "VOLCENGINE_ACCESS_KEY_ID"),
            _first_non_empty_env("TOS_SECRET_ACCESS_KEY", "VOLCENGINE_ACCESS_KEY_SECRET"),
            get_object_storage_bucket_name(STORAGE_PROVIDER_TOS),
            get_object_storage_endpoint(STORAGE_PROVIDER_TOS),
            get_object_storage_region(STORAGE_PROVIDER_TOS),
        ]
    )
    if has_tos:
        return STORAGE_PROVIDER_TOS

    has_oss = all(
        [
            _first_non_empty_env("ALIBABA_CLOUD_ACCESS_KEY_ID"),
            _first_non_empty_env("ALIBABA_CLOUD_ACCESS_KEY_SECRET"),
            get_object_storage_bucket_name(STORAGE_PROVIDER_OSS),
            get_object_storage_endpoint(STORAGE_PROVIDER_OSS),
        ]
    )
    if has_oss:
        return STORAGE_PROVIDER_OSS

    return STORAGE_PROVIDER_NONE


def get_object_storage_bucket_name(provider: Optional[str] = None) -> str:
    normalized = _normalize_provider(provider) or get_object_storage_provider()
    if normalized == STORAGE_PROVIDER_TOS:
        return _first_non_empty_env("OBJECT_STORAGE_BUCKET_NAME", "TOS_BUCKET_NAME", "OSS_BUCKET_NAME")
    return _first_non_empty_env("OBJECT_STORAGE_BUCKET_NAME", "OSS_BUCKET_NAME", "TOS_BUCKET_NAME")


def get_object_storage_endpoint(provider: Optional[str] = None) -> str:
    normalized = _normalize_provider(provider) or get_object_storage_provider()
    if normalized == STORAGE_PROVIDER_TOS:
        raw = _first_non_empty_env("OBJECT_STORAGE_ENDPOINT", "TOS_ENDPOINT", "OSS_ENDPOINT")
    else:
        raw = _first_non_empty_env("OBJECT_STORAGE_ENDPOINT", "OSS_ENDPOINT", "TOS_ENDPOINT")
    return _normalize_endpoint(normalized, raw)


def get_object_storage_region(provider: Optional[str] = None) -> str:
    normalized = _normalize_provider(provider) or get_object_storage_provider()
    if normalized != STORAGE_PROVIDER_TOS:
        return ""
    return _first_non_empty_env("OBJECT_STORAGE_REGION", "TOS_REGION")


def is_oss_configured() -> bool:
    """Backward-compatible alias for object storage readiness check."""
    provider = get_object_storage_provider()
    if provider == STORAGE_PROVIDER_TOS:
        required = [
            _first_non_empty_env("TOS_ACCESS_KEY_ID", "VOLCENGINE_ACCESS_KEY_ID"),
            _first_non_empty_env("TOS_SECRET_ACCESS_KEY", "VOLCENGINE_ACCESS_KEY_SECRET"),
            get_object_storage_endpoint(provider),
            get_object_storage_bucket_name(provider),
            get_object_storage_region(provider),
        ]
        return all(required)

    if provider == STORAGE_PROVIDER_OSS:
        required = [
            _first_non_empty_env("ALIBABA_CLOUD_ACCESS_KEY_ID"),
            _first_non_empty_env("ALIBABA_CLOUD_ACCESS_KEY_SECRET"),
            get_object_storage_endpoint(provider),
            get_object_storage_bucket_name(provider),
        ]
        return all(required)

    return False


def get_oss_base_path() -> str:
    """Backward-compatible alias returning the active object storage base path."""
    return _first_non_empty_env("OSS_BASE_PATH", "TOS_BASE_PATH", "OBJECT_STORAGE_BASE_PATH") or DEFAULT_OSS_BASE_PATH


def is_object_key(value: str) -> bool:
    """
    Check if a string value is an object key (not a full URL or local path).
    """
    return classify_media_ref(value, oss_base_path=get_oss_base_path()) == MEDIA_REF_OBJECT_KEY


def is_local_path(value: str) -> bool:
    """Check if a string is a local file path (relative or absolute)."""
    return classify_media_ref(value, oss_base_path=get_oss_base_path()) == MEDIA_REF_LOCAL_PATH


def extract_object_key_from_url(value: str) -> str:
    """
    Try to recover an object key from an object-storage URL.

    If the value is already an object key, return it unchanged.
    If it cannot be safely normalized, return the original string.
    """
    if not isinstance(value, str):
        return value

    raw = value.strip()
    if not raw:
        return raw

    if is_object_key(raw):
        return raw

    if not raw.startswith(("http://", "https://")):
        return raw

    try:
        parsed = urlparse(raw)
        path = unquote(parsed.path or "").lstrip("/")
        if not path:
            return raw

        bucket_name = get_object_storage_bucket_name()
        if bucket_name and path.startswith(f"{bucket_name}/"):
            path = path[len(bucket_name) + 1:]

        base_path = get_oss_base_path().strip("/ ")
        if base_path and path.startswith(f"{base_path}/"):
            return path
    except Exception:
        return raw

    return raw


class OSSImageUploader:
    """
    Backward-compatible object storage uploader.

    The class name is preserved so existing callers do not need to change, while
    the implementation now supports both Alibaba OSS and ByteDance TOS.
    """

    _instance = None

    def __new__(cls):
        """Singleton pattern to reuse storage connection."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
            cls._instance._url_cache = {}  # (object_key, expires) -> (signed_url, timestamp)
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self.provider = get_object_storage_provider()
        self.base_path = get_oss_base_path().rstrip("/")
        self.bucket_name = get_object_storage_bucket_name(self.provider)
        self.endpoint = get_object_storage_endpoint(self.provider)
        self.region = get_object_storage_region(self.provider)
        self.access_key_id = ""
        self.access_key_secret = ""
        self.auth = None
        self.bucket = None
        self.client = None

        if self.provider == STORAGE_PROVIDER_OSS:
            self.access_key_id = _first_non_empty_env("ALIBABA_CLOUD_ACCESS_KEY_ID")
            self.access_key_secret = _first_non_empty_env("ALIBABA_CLOUD_ACCESS_KEY_SECRET")
            self._init_oss_client()
        elif self.provider == STORAGE_PROVIDER_TOS:
            self.access_key_id = _first_non_empty_env("TOS_ACCESS_KEY_ID", "VOLCENGINE_ACCESS_KEY_ID")
            self.access_key_secret = _first_non_empty_env(
                "TOS_SECRET_ACCESS_KEY",
                "VOLCENGINE_ACCESS_KEY_SECRET",
            )
            self._init_tos_client()
        else:
            logger.info("Object storage is not configured. Upload mirror stays disabled.")

        self._initialized = True

    def _init_oss_client(self) -> None:
        if not all([self.access_key_id, self.access_key_secret, self.endpoint, self.bucket_name]):
            logger.warning("Alibaba OSS credentials are incomplete. OSS upload will be disabled.")
            return

        if oss2 is None:
            logger.warning("Python package 'oss2' is not installed. OSS upload will be disabled.")
            return

        try:
            self.auth = oss2.Auth(self.access_key_id, self.access_key_secret)
            self.bucket = oss2.Bucket(
                self.auth,
                self.endpoint,
                self.bucket_name,
                connect_timeout=5,
            )
            self.client = self.bucket
            logger.info(
                "Object storage initialized with Alibaba OSS: bucket=%s, base_path=%s",
                self.bucket_name,
                self.base_path,
            )
        except Exception as exc:
            logger.error("Failed to initialize Alibaba OSS bucket: %s", exc)
            self.bucket = None
            self.client = None

    def _init_tos_client(self) -> None:
        if tos is None:
            logger.warning("Python package 'tos' is not installed. TOS upload will be disabled.")
            return

        if not all(
            [
                self.access_key_id,
                self.access_key_secret,
                self.endpoint,
                self.bucket_name,
                self.region,
            ]
        ):
            logger.warning("ByteDance TOS credentials are incomplete. TOS upload will be disabled.")
            return

        try:
            self.client = tos.TosClientV2(
                self.access_key_id,
                self.access_key_secret,
                self.endpoint,
                self.region,
                connection_time=5,
                socket_timeout=30,
            )
            self.bucket = self.client
            logger.info(
                "Object storage initialized with ByteDance TOS: bucket=%s, region=%s, base_path=%s",
                self.bucket_name,
                self.region,
                self.base_path,
            )
        except Exception as exc:
            logger.error("Failed to initialize ByteDance TOS client: %s", exc)
            self.bucket = None
            self.client = None

    @classmethod
    def reset_instance(cls):
        """Reset singleton instance (useful when credentials change)."""
        cls._instance = None

    @property
    def is_configured(self) -> bool:
        """Check if object storage is properly configured and ready."""
        return self.client is not None

    def _build_object_key(self, sub_path: str, filename: str) -> str:
        """
        Build full object key from base path, sub path, and filename.

        Example: seedance-inputs/proj_123/assets/characters/char_001.png
        """
        parts = [self.base_path]
        if sub_path:
            parts.append(sub_path.strip("/"))
        parts.append(filename)
        return "/".join(part for part in parts if part)

    @staticmethod
    def _guess_content_type(local_path: str) -> str:
        content_type, _ = mimetypes.guess_type(local_path)
        return content_type or "application/octet-stream"

    def _upload_tos_via_presigned_put(self, local_path: str, object_key: str) -> bool:
        file_size = os.path.getsize(local_path)
        if file_size > TOS_FALLBACK_MAX_FILE_SIZE:
            logger.warning(
                "TOS fallback upload skipped for %s because file size %s exceeds %s bytes.",
                object_key,
                file_size,
                TOS_FALLBACK_MAX_FILE_SIZE,
            )
            return False

        try:
            signed = self.client.pre_signed_url(
                HttpMethodType.Http_Method_Put,
                self.bucket_name,
                key=object_key,
                expires=TOS_FALLBACK_PUT_EXPIRES,
            )
            signed_url = getattr(signed, "signed_url", "")
            if not signed_url:
                logger.warning("TOS fallback upload could not generate a signed PUT URL for %s.", object_key)
                return False

            headers = {"Content-Type": self._guess_content_type(local_path)}
            with open(local_path, "rb") as file_obj:
                payload = file_obj.read()

            for attempt in range(1, 4):
                try:
                    response = requests.put(signed_url, data=payload, headers=headers, timeout=30)
                    if response.status_code in (200, 201):
                        logger.info(
                            "TOS fallback presigned PUT succeeded for %s on attempt %s.",
                            object_key,
                            attempt,
                        )
                        return True
                    logger.warning(
                        "TOS fallback presigned PUT failed for %s on attempt %s with HTTP %s.",
                        object_key,
                        attempt,
                        response.status_code,
                    )
                except Exception as exc:
                    logger.warning(
                        "TOS fallback presigned PUT error for %s on attempt %s: %s",
                        object_key,
                        attempt,
                        exc,
                    )
                time.sleep(min(attempt, 2))
        except Exception as exc:
            logger.warning("TOS fallback upload setup failed for %s: %s", object_key, exc)
        return False

    def upload_file(self, local_path: str, sub_path: str = "", custom_filename: str = None) -> Optional[str]:
        """
        Upload a file to object storage and return the object key.

        Args:
            local_path: Local file path to upload
            sub_path: Sub-directory path (e.g., "proj_123/assets/characters")
            custom_filename: Optional custom filename, defaults to original filename

        Returns:
            Object key or None if failed
        """
        if not self.client:
            logger.warning("Object storage is not configured, cannot upload file.")
            return None

        if not os.path.exists(local_path):
            logger.error("File not found: %s", local_path)
            return None

        try:
            filename = custom_filename or os.path.basename(local_path)
            object_key = self._build_object_key(sub_path, filename)
            logger.info("Uploading to %s: %s -> %s", self.provider or "local", local_path, object_key)

            if self.provider == STORAGE_PROVIDER_OSS:
                with open(local_path, "rb") as file_obj:
                    result = self.bucket.put_object(object_key, file_obj)
                if result.status == 200:
                    return object_key
            elif self.provider == STORAGE_PROVIDER_TOS:
                try:
                    result = self.client.put_object_from_file(self.bucket_name, object_key, local_path)
                    if result.status_code == 200:
                        return object_key
                except Exception as exc:
                    logger.warning("TOS SDK direct upload failed for %s: %s", object_key, exc)

                if self._upload_tos_via_presigned_put(local_path, object_key):
                    return object_key

            logger.error("Upload failed for object key: %s", object_key)
            return None
        except Exception as exc:
            logger.error("Object storage upload error: %s", exc)
            return None

    def generate_signed_url(self, object_key: str, expires: int = SIGN_URL_EXPIRES_DISPLAY) -> str:
        """
        Generate a signed URL for accessing a private object.

        Args:
            object_key: The object key in storage
            expires: URL validity in seconds

        Returns:
            Signed URL string
        """
        if not self.client:
            logger.warning("Object storage is not configured, cannot generate signed URL.")
            return ""

        try:
            cache_key = (object_key, expires)
            now = time.time()
            if cache_key in self._url_cache:
                cached_url, timestamp = self._url_cache[cache_key]
                if now - timestamp < max(expires - 600, 0):
                    return cached_url

            if self.provider == STORAGE_PROVIDER_OSS:
                url = self.bucket.sign_url("GET", object_key, expires, slash_safe=True)
            elif self.provider == STORAGE_PROVIDER_TOS:
                signed = self.client.pre_signed_url(
                    HttpMethodType.Http_Method_Get,
                    self.bucket_name,
                    key=object_key,
                    expires=expires,
                )
                url = getattr(signed, "signed_url", "")
            else:
                return ""

            if url.startswith("http://"):
                url = "https://" + url[7:]

            self._url_cache[cache_key] = (url, now)
            return url
        except Exception as exc:
            logger.error("Failed to generate signed URL for %s: %s", object_key, exc)
            return ""

    def sign_url_for_display(self, object_key: str) -> str:
        """Generate signed URL for frontend display (2 hours validity)."""
        return self.generate_signed_url(object_key, SIGN_URL_EXPIRES_DISPLAY)

    def sign_url_for_api(self, object_key: str) -> str:
        """Generate signed URL for AI API calls (30 minutes validity)."""
        return self.generate_signed_url(object_key, SIGN_URL_EXPIRES_API)

    def object_exists(self, object_key: str) -> bool:
        """Check if an object exists in the configured storage provider."""
        if not self.client:
            return False

        try:
            if self.provider == STORAGE_PROVIDER_OSS:
                return self.bucket.object_exists(object_key)
            if self.provider == STORAGE_PROVIDER_TOS:
                self.client.head_object(self.bucket_name, object_key)
                return True
            return False
        except (TosClientError, TosServerError, Exception):
            return False

    # Legacy methods for backward compatibility
    def upload_image(self, local_image_path: str, sub_path: str = "assets") -> Optional[str]:
        """Legacy method: Upload image and return object key."""
        return self.upload_file(local_image_path, sub_path)

    def upload_video(self, local_video_path: str, sub_path: str = "video") -> Optional[str]:
        """Legacy method: Upload video and return object key."""
        return self.upload_file(local_video_path, sub_path)

    def get_oss_url(self, object_key: str, use_public_url: bool = False) -> str:
        """
        Legacy method: Get signed object URL.

        Note: For private object storage strategy, always use signed URLs.
        The use_public_url parameter is deprecated.
        """
        if use_public_url:
            logger.warning("Public URLs are deprecated. Using signed URL instead for security.")
        return self.sign_url_for_display(object_key)


def sign_oss_urls_in_data(data, uploader: OSSImageUploader = None):
    """
    Recursively traverse data structure and convert object keys to signed URLs.

    This is the core function for the "dynamic signing" strategy.
    Called before returning API responses to frontend.
    """
    if uploader is None:
        uploader = OSSImageUploader()

    if not uploader.is_configured:
        return data

    def process_value(value):
        if isinstance(value, str):
            if is_object_key(value):
                signed_url = uploader.sign_url_for_display(value)
                return signed_url if signed_url else value
            return value
        if isinstance(value, dict):
            return {k: process_value(v) for k, v in value.items()}
        if isinstance(value, list):
            return [process_value(item) for item in value]
        return value

    return process_value(data)


def convert_local_path_to_object_key(local_path: str, project_id: str = None) -> str:
    """
    Convert a local relative path to an object key format.

    Example:
        "assets/characters/char_001.png" -> "seedance-inputs/proj_123/assets/characters/char_001.png"
    """
    base_path = get_oss_base_path()

    local_path = str(local_path).strip().replace("\\", "/")

    if local_path.startswith("output/"):
        local_path = local_path[7:]

    if project_id:
        return f"{base_path}/{project_id}/{local_path}"
    return f"{base_path}/{local_path}"
