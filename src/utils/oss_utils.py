import os
import oss2
import hashlib
import time
from typing import Optional, Tuple
from . import get_logger

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


def get_oss_base_path() -> str:
    """Get OSS base path from environment or use default."""
    return os.getenv("OSS_BASE_PATH", DEFAULT_OSS_BASE_PATH).rstrip("/")


def is_object_key(value: str) -> bool:
    """
    Check if a string value is an OSS Object Key (not a full URL or local path).
    """
    if not value or not isinstance(value, str):
        return False
    # Skip full URLs
    if value.startswith(("http://", "https://", "blob:", "data:")):
        return False
    # Skip empty or whitespace-only
    if not value.strip():
        return False
    
    # Skip local paths (these start with known local directories)
    # Be very inclusive here to avoid signing local files
    local_prefixes = (
        "assets/", "storyboard/", "video/", "audio/", "export/", "uploads/", "output/", "outputs/",
        "/assets/", "/storyboard/", "/video/", "/audio/", "/export/", "/uploads/", "/output/", "/outputs/"
    )
    if value.startswith(local_prefixes):
        return False
    
    # Get the current OSS base path and ensure it ends with a single slash
    # Strip quotes and slashes to be robust
    base_path = get_oss_base_path().strip("'\"/")
    
    # Object keys MUST start with the OSS base path (e.g., 'lumenx/')
    # This is the ONLY valid check. Do NOT add a fallback that might match local paths.
    return value.startswith(f"{base_path}/")









def is_local_path(value: str) -> bool:
    """Check if a string is a local file path (relative or absolute)."""
    if not value or not isinstance(value, str):
        return False
    if value.startswith("http://") or value.startswith("https://"):
        return False
    # Check if it's a relative path starting with known directories
    return value.startswith(("assets/", "storyboard/", "video/", "audio/", "export/", "uploads/", "output/"))


class OSSImageUploader:
    """
    OSS Uploader supporting Private OSS + Dynamic Signing strategy.
    
    Key principles:
    - Upload files and return Object Keys (not full URLs)
    - Generate signed URLs on-demand with configurable expiry
    - Support both private bucket access and AI API access
    """
    
    _instance = None
    
    def __new__(cls):
        """Singleton pattern to reuse OSS connection."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
            cls._instance._url_cache = {}  # (object_key, expires) -> (signed_url, timestamp)
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
            
        self.access_key_id = os.getenv("ALIBABA_CLOUD_ACCESS_KEY_ID")
        self.access_key_secret = os.getenv("ALIBABA_CLOUD_ACCESS_KEY_SECRET")
        self.endpoint = os.getenv("OSS_ENDPOINT")
        self.bucket_name = os.getenv("OSS_BUCKET_NAME")
        self.base_path = get_oss_base_path()
        
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
        if not self.bucket:
            logger.warning("OSS not configured, cannot upload file.")
            return None
        
        if not os.path.exists(local_path):
            logger.error(f"File not found: {local_path}")
            return None
        
        try:
            filename = custom_filename or os.path.basename(local_path)
            object_key = self._build_object_key(sub_path, filename)
            
            logger.info(f"Uploading to OSS: {local_path} -> {object_key}")
            
            with open(local_path, 'rb') as f:
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

            url = self.bucket.sign_url('GET', object_key, expires)
            
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
            if is_object_key(value):
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

