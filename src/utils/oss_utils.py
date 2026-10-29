import os
from io import BytesIO
import oss2
import tempfile
import time
from copy import deepcopy
from types import MappingProxyType
from typing import Mapping, Optional
from . import get_logger
from .json_storage import StorageError
from .media_refs import classify_media_ref, MEDIA_REF_LOCAL_PATH, MEDIA_REF_OBJECT_KEY

logger = get_logger(__name__)

# Default configuration
DEFAULT_OSS_BASE_PATH = "lumenx"
SIGN_URL_EXPIRES_DISPLAY = 7200  # 2 hours for frontend display
SIGN_URL_EXPIRES_API = 1800      # 30 minutes for AI API calls


def is_oss_configured() -> bool:
    """Check if OSS is properly configured."""
    required = [
        os.getenv("ALIBABA_CLOUD_ACCESS_KEY_ID"),
        os.getenv("ALIBABA_CLOUD_ACCESS_KEY_SECRET"),
        os.getenv("OSS_ENDPOINT"),
        os.getenv("OSS_BUCKET_NAME")
    ]
    return all(required)


def is_oss_enabled(*, env: Mapping[str, str] | None = None) -> bool:
    """Check whether OSS upload is enabled.

    Controlled by the ``OSS_ENABLE`` flag (env / saved config). Defaults to True
    when unset so existing installs keep uploading. When disabled, new uploads
    are skipped and callers fall back to the local ``output/`` path
    (local-first). This only gates uploads — signing/display of objects that
    were already uploaded is unaffected.
    """
    value = (os.environ if env is None else env).get("OSS_ENABLE")
    if value is None:
        return True
    return value.strip().lower() not in ("false", "0", "no", "off")


def get_oss_base_path(*, env: Mapping[str, str] | None = None) -> str:
    """Get OSS base path from environment or use default."""
    return (os.environ if env is None else env).get("OSS_BASE_PATH", DEFAULT_OSS_BASE_PATH).rstrip("/")


def is_object_key(value: str, *, env: Mapping[str, str] | None = None) -> bool:
    """
    Check if a string value is an OSS Object Key (not a full URL or local path).
    """
    return (
        classify_media_ref(value, oss_base_path=get_oss_base_path(env=env))
        == MEDIA_REF_OBJECT_KEY
    )

def is_local_path(value: str) -> bool:
    """Check if a string is a local file path (relative or absolute)."""
    return (
        classify_media_ref(value, oss_base_path=get_oss_base_path())
        == MEDIA_REF_LOCAL_PATH
    )


class OSSImageUploader:
    """
    OSS Uploader supporting Private OSS + Dynamic Signing strategy.
    
    Key principles:
    - Upload files and return Object Keys (not full URLs)
    - Generate signed URLs on-demand with configurable expiry
    - Support both private bucket access and AI API access
    """
    
    _instance = None
    
    def __new__(cls, *, env: Mapping[str, str] | None = None, output_root=None):
        """Keep the standalone singleton; explicit runtimes own their client."""
        if env is not None or output_root is not None:
            instance = super().__new__(cls)
            instance._initialized = False
            instance._url_cache = {}
            return instance
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
            cls._instance._url_cache = {}  # (object_key, expires) -> (signed_url, timestamp)
        return cls._instance
    
    def __init__(self, *, env: Mapping[str, str] | None = None, output_root=None):
        if self._initialized:
            return
            
        self._env = os.environ if env is None else MappingProxyType(dict(env))
        self.output_root = os.path.realpath(output_root or "output")
        self.access_key_id = self._env.get("ALIBABA_CLOUD_ACCESS_KEY_ID")
        self.access_key_secret = self._env.get("ALIBABA_CLOUD_ACCESS_KEY_SECRET")
        self.endpoint = self._env.get("OSS_ENDPOINT")
        self.bucket_name = self._env.get("OSS_BUCKET_NAME")
        self.base_path = self._env.get("OSS_BASE_PATH", DEFAULT_OSS_BASE_PATH).rstrip("/")
        
        # Debug prints for terminal
        print(f"DEBUG: OSS init - ID={'***' if self.access_key_id else 'None'}, Secret={'***' if self.access_key_secret else 'None'}, Endpoint={self.endpoint}, Bucket={self.bucket_name}, Base={self.base_path}")
        
        if not all([self.access_key_id, self.access_key_secret, self.endpoint, self.bucket_name]):
            logger.warning("OSS credentials not fully configured. OSS upload will be disabled.")
            print("DEBUG: OSS init - FAILED: missing credentials")
            self.bucket = None
        else:
            try:
                self.auth = oss2.Auth(self.access_key_id, self.access_key_secret)
                # Set connection timeout to prevent long blocking on network issues
                self.bucket = oss2.Bucket(
                    self.auth, 
                    self.endpoint, 
                    self.bucket_name,
                    connect_timeout=5  # 5 seconds connection timeout
                )
                logger.info(f"OSS initialized: bucket={self.bucket_name}, base_path={self.base_path}")
                print(f"DEBUG: OSS init - SUCCESS: bucket={self.bucket_name}")
            except Exception as e:
                logger.error(f"Failed to initialize OSS bucket: {e}")
                print(f"DEBUG: OSS init - ERROR: {e}")
                self.bucket = None
        
        self._initialized = True

    
    @classmethod
    def reset_instance(cls):
        """Reset singleton instance (useful when credentials change)."""
        cls._instance = None
    
    @property
    def is_configured(self) -> bool:
        """Check if OSS is properly configured and ready."""
        return self.bucket is not None
    
    def _build_object_key(self, sub_path: str, filename: str) -> str:
        """
        Build full Object Key from base path, sub path, and filename.
        
        Example: lumenx/proj_123/assets/characters/char_001.png
        """
        parts = [self.base_path]
        if sub_path:
            parts.append(sub_path.strip("/"))
        parts.append(filename)
        return "/".join(parts)
    
    def upload_file(self, local_path: str, sub_path: str = "", custom_filename: str = None) -> Optional[str]:
        """
        Upload a file to OSS and return the Object Key.
        
        Args:
            local_path: Local file path to upload
            sub_path: Sub-directory path (e.g., "proj_123/assets/characters")
            custom_filename: Optional custom filename, defaults to original filename
        
        Returns:
            Object Key (e.g., "lumenx/proj_123/assets/characters/file.png") or None if failed
        """
        if not is_oss_enabled(env=self._env):
            logger.info("OSS upload disabled (OSS_ENABLE=false); skipping, using local path.")
            return None

        if not self.bucket:
            logger.warning("OSS not configured, cannot upload file.")
            return None

        # Only allow uploads from the managed output/ tree or the system temp
        # directory; anything else (e.g. path traversal via a crafted media
        # ref) is rejected and callers fall back gracefully (data URI / local).
        resolved = os.path.realpath(local_path)
        out_base = self.output_root
        tmp_base = os.path.realpath(tempfile.gettempdir())
        if not resolved.startswith(out_base + os.sep):
            if not resolved.startswith(tmp_base + os.sep):
                logger.error(f"Refusing OSS upload outside allowed directories: {local_path}")
                return None

        if not os.path.exists(resolved):
            logger.error(f"File not found: {local_path}")
            return None
        
        try:
            filename = custom_filename or os.path.basename(resolved)
            object_key = self._build_object_key(sub_path, filename)
            
            logger.info(f"Uploading to OSS: {resolved} -> {object_key}")
            
            with open(resolved, 'rb') as f:
                result = self.bucket.put_object(object_key, f)
            
            if result.status == 200:
                logger.info(f"Upload success: {object_key}")
                return object_key
            else:
                logger.error(f"Upload failed with status: {result.status}")
                return None
                
        except Exception as e:
            logger.error(f"OSS upload error: {e}")
            return None
    
    def upload_bytes(self, payload: bytes, *, custom_filename: str, sub_path: str = "", max_bytes: int = 100 * 1024 * 1024) -> Optional[str]:
        """Mirror already-verified immutable bytes without reopening a mutable path."""
        if type(payload) is not bytes or type(max_bytes) is not int or not 0 < len(payload) <= max_bytes:
            raise ValueError("OSS byte upload requires nonempty bytes within the size limit")
        if not isinstance(custom_filename, str) or not custom_filename or not isinstance(sub_path, str):
            raise ValueError("OSS byte upload requires a filename and text sub-path")
        if not is_oss_enabled(env=self._env) or self.bucket is None:
            return None
        object_key = self._build_object_key(sub_path, custom_filename)
        try:
            with BytesIO(payload) as stream:
                result = self.bucket.put_object(object_key, stream)
            if result.status == 200:
                return object_key
            logger.warning("OSS byte mirror did not complete")
        except StorageError:
            raise
        except Exception:
            logger.warning("OSS byte mirror failed")
        return None

    def generate_signed_url(self, object_key: str, expires: int = SIGN_URL_EXPIRES_DISPLAY) -> str:
        """
        Generate a signed URL for accessing a private OSS object.
        
        Args:
            object_key: The Object Key in OSS
            expires: URL validity in seconds (default 2 hours)
        
        Returns:
            Signed URL string
        """
        if not self.bucket:
            logger.warning("OSS not configured, cannot generate signed URL.")
            return ""
        
        try:
            # Cache check: reuse signed URL if it's still valid (with 10 min buffer)
            cache_key = (object_key, expires)
            now = time.time()
            if cache_key in self._url_cache:
                cached_url, timestamp = self._url_cache[cache_key]
                # If the URL was generated recently enough (at least 10 mins left before expiry)
                if now - timestamp < (expires - 600):
                    return cached_url

            url = self.bucket.sign_url('GET', object_key, expires, slash_safe=True)

            # Ensure HTTPS - some AI APIs (e.g. DashScope wan2.6-i2v) require HTTPS
            if url.startswith("http://"):
                url = "https://" + url[7:]

            # Update cache
            self._url_cache[cache_key] = (url, now)
            return url
        except Exception as e:
            logger.error(f"Failed to generate signed URL for {object_key}: {e}")
            return ""
    
    def sign_url_for_display(self, object_key: str) -> str:
        """Generate signed URL for frontend display (2 hours validity)."""
        signed_url = self.generate_signed_url(object_key, SIGN_URL_EXPIRES_DISPLAY)
        # print(f"DEBUG: sign_url_for_display('{object_key}') -> '{signed_url}'")
        return signed_url


    
    def sign_url_for_api(self, object_key: str) -> str:
        """Generate signed URL for AI API calls (30 minutes validity)."""
        return self.generate_signed_url(object_key, SIGN_URL_EXPIRES_API)
    
    def object_exists(self, object_key: str) -> bool:
        """Check if an object exists in OSS."""
        if not self.bucket:
            return False
        try:
            return self.bucket.object_exists(object_key)
        except:
            return False
    
    # Legacy methods for backward compatibility
    def upload_image(self, local_image_path: str, sub_path: str = "assets") -> Optional[str]:
        """Legacy method: Upload image and return Object Key."""
        return self.upload_file(local_image_path, sub_path)
    
    def upload_video(self, local_video_path: str, sub_path: str = "video") -> Optional[str]:
        """Legacy method: Upload video and return Object Key."""
        return self.upload_file(local_video_path, sub_path)
    
    def get_oss_url(self, object_key: str, use_public_url: bool = False) -> str:
        """
        Legacy method: Get OSS URL.
        
        Note: For Private OSS strategy, always use signed URLs.
        The use_public_url parameter is deprecated.
        """
        if use_public_url:
            logger.warning("Public URLs are deprecated. Using signed URL instead for security.")
        return self.sign_url_for_display(object_key)


_RESPONSE_MEDIA_FIELDS = frozenset({
    "audio_url",
    "avatar_url",
    "bg_audio_url",
    "bgm_url",
    "dubbed_video_url",
    "full_body_image_url",
    "headshot_image_url",
    "image_url",
    "merged_video_url",
    "preview_video_url",
    "rendered_image_url",
    "sfx_url",
    "source_audio_url",
    "three_view_image_url",
    "thumbnail_url",
    "video_url",
})
_RESPONSE_MEDIA_LIST_FIELDS = frozenset({
    "image_urls",
    "reference_image_urls",
    "reference_video_urls",
    "t2i_image_urls",
})
_RESPONSE_RAW_SNAPSHOT_FIELDS = frozenset({
    "dialogue_audio_snapshot",
    "dub_applied_source",
    "dub_preview_source",
})
_RESPONSE_RAW_METADATA_FIELDS = frozenset({
    "options",
    "prepared",
    "resolved_request",
    "settings",
    "sources",
})
_RESPONSE_MEDIA_REFS_FIELD = "media_refs"


def _is_uploader_object_key(value: str, uploader: OSSImageUploader) -> bool:
    # Older injected display signers only implement sign_url_for_display.
    base_path = getattr(uploader, "base_path", None)
    if not isinstance(base_path, str):
        return is_object_key(value)
    return classify_media_ref(value, oss_base_path=base_path) == MEDIA_REF_OBJECT_KEY


def _sign_response_media_value(value, uploader: OSSImageUploader, signing_enabled: bool):
    if not signing_enabled or not isinstance(value, str) or not _is_uploader_object_key(value, uploader):
        return value, None
    signed_url = uploader.sign_url_for_display(value)
    return signed_url if signed_url else value, value


def _is_generic_variant_media_url(container) -> bool:
    if not isinstance(container, dict):
        return False
    variant_markers = ("created_at", "prompt_used", "is_favorited", "is_uploaded_source", "cached")
    return any(key in container for key in variant_markers)


def sign_media_refs_in_response(data, uploader: OSSImageUploader = None, *, media_fields=None):
    """Sign declared response media fields and attach raw display refs.

    This is intentionally narrower than ``sign_oss_urls_in_data``. It signs only
    fields that are response display media and keeps provenance/snapshot objects
    raw so clients can compare source identity without stripping signed URL
    query parameters. The ``media_refs`` sidecar is response-only: incoming
    sidecars on response media objects are ignored and rebuilt from raw media
    fields when possible. Opaque source snapshots and parameter metadata are
    preserved literally; their keys are not interpreted as response fields.
    """
    if uploader is None:
        uploader = OSSImageUploader()

    signing_enabled = bool(uploader.is_configured)
    root_media_fields = frozenset(media_fields or ())

    def process(value, depth=0):
        if isinstance(value, dict):
            processed = {}
            media_refs = {}
            for child_key, child_value in value.items():
                if child_key == _RESPONSE_MEDIA_REFS_FIELD:
                    continue
                if child_key in _RESPONSE_RAW_SNAPSHOT_FIELDS or child_key in _RESPONSE_RAW_METADATA_FIELDS:
                    processed[child_key] = deepcopy(child_value)
                    continue
                if (
                    child_key in _RESPONSE_MEDIA_FIELDS
                    or (depth == 0 and child_key in root_media_fields)
                    or (child_key == "url" and _is_generic_variant_media_url(value))
                ):
                    signed_value, raw_ref = _sign_response_media_value(child_value, uploader, signing_enabled)
                    processed[child_key] = signed_value
                    if raw_ref is not None:
                        media_refs[child_key] = raw_ref
                    continue
                if child_key in _RESPONSE_MEDIA_LIST_FIELDS and isinstance(child_value, list):
                    signed_items = []
                    raw_items = []
                    has_ref = False
                    for item in child_value:
                        signed_item, raw_ref = _sign_response_media_value(item, uploader, signing_enabled)
                        signed_items.append(signed_item)
                        if raw_ref is not None:
                            has_ref = True
                        raw_items.append(raw_ref)
                    processed[child_key] = signed_items
                    if has_ref:
                        media_refs[child_key] = raw_items
                    continue
                processed[child_key] = process(child_value, depth + 1)
            if media_refs:
                processed[_RESPONSE_MEDIA_REFS_FIELD] = media_refs
            return processed
        if isinstance(value, list):
            return [process(item, depth + 1) for item in value]
        return value

    return process(data)

def sign_oss_urls_in_data(data, uploader: OSSImageUploader = None):
    """
    Recursively traverse data structure and convert Object Keys to signed URLs.
    
    This is the core function for the "Dynamic Signing" strategy.
    Called before returning API responses to frontend.
    
    Args:
        data: Dict, list, or primitive value to process
        uploader: OSSImageUploader instance (created if not provided)
    
    Returns:
        Processed data with Object Keys converted to signed URLs
    """
    if uploader is None:
        uploader = OSSImageUploader()
    
    if not uploader.is_configured:
        # OSS not configured, return data as-is (local mode)
        return data
    
    def process_value(value):
        if isinstance(value, str):
            if _is_uploader_object_key(value, uploader):
                signed_url = uploader.sign_url_for_display(value)
                return signed_url if signed_url else value
            # print(f"DEBUG: sign_oss_urls_in_data - skipping string '{value[:50]}...'")
            return value
        elif isinstance(value, dict):
            return {k: process_value(v) for k, v in value.items()}
        elif isinstance(value, list):
            return [process_value(item) for item in value]
        else:
            return value
    
    return process_value(data)


def convert_local_path_to_object_key(local_path: str, project_id: str = None) -> str:
    """
    Convert a local relative path to an OSS Object Key format.
    
    Example: 
        "assets/characters/char_001.png" -> "lumenx/proj_123/assets/characters/char_001.png"
    """
    base_path = get_oss_base_path()
    
    # Remove "output/" prefix if present
    if local_path.startswith("output/"):
        local_path = local_path[7:]
    
    # Build Object Key
    if project_id:
        return f"{base_path}/{project_id}/{local_path}"
    else:
        return f"{base_path}/{local_path}"
