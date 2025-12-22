import os
import oss2
import hashlib
from typing import Optional
from . import get_logger

logger = get_logger(__name__)

class OSSImageUploader:
    def __init__(self, access_key_id: str = None, access_key_secret: str = None, endpoint: str = None, bucket_name: str = None, oss_prefix: str = 'comic-gen-assets/'):
        """
        Initialize OSS Uploader.
        Defaults to environment variables if arguments are not provided.
        """
        self.access_key_id = access_key_id or os.getenv("ALIBABA_CLOUD_ACCESS_KEY_ID")
        self.access_key_secret = access_key_secret or os.getenv("ALIBABA_CLOUD_ACCESS_KEY_SECRET")
        self.endpoint = endpoint or os.getenv("OSS_ENDPOINT")
        self.bucket_name = bucket_name or os.getenv("OSS_BUCKET_NAME")
        self.oss_prefix = oss_prefix or os.getenv("OSS_PREFIX", 'comic-gen-assets/')
        
        if not all([self.access_key_id, self.access_key_secret, self.endpoint, self.bucket_name]):
            logger.warning("OSS credentials not fully configured. OSS upload will fail.")
            self.bucket = None
        else:
            try:
                self.auth = oss2.Auth(self.access_key_id, self.access_key_secret)
                self.bucket = oss2.Bucket(self.auth, self.endpoint, self.bucket_name)
            except Exception as e:
                logger.error(f"Failed to initialize OSS bucket: {e}")
                self.bucket = None

        self.uploaded_images = {}  # Cache uploaded images

    def get_file_md5(self, file_path: str) -> str:
        """Calculate file MD5."""
        md5_hash = hashlib.md5()
        with open(file_path, 'rb') as f:
            for chunk in iter(lambda: f.read(4096), b""):
                md5_hash.update(chunk)
        return md5_hash.hexdigest()

    def get_oss_url(self, object_name: str, use_public_url: bool = True) -> str:
        """
        Get OSS URL.
        
        Args:
            object_name: The object key in OSS
            use_public_url: If True, return public URL (for Dashscope API access).
                           If False, return signed URL (for private buckets).
        
        Note: For Dashscope API to access reference images, the URL must be publicly 
        accessible. Set use_public_url=True and ensure OSS bucket has public read access.
        """
        if not self.bucket:
             return ""
        
        if use_public_url:
            # Generate public URL (requires bucket to have public read access)
            # Format: https://{bucket}.{endpoint}/{object_name}
            # Remove 'https://' or 'http://' from endpoint if present
            endpoint_clean = self.endpoint.replace('https://', '').replace('http://', '')
            public_url = f"https://{self.bucket_name}.{endpoint_clean}/{object_name}"
            print(f"DEBUG: Generated PUBLIC OSS URL: {public_url}")
            return public_url
        else:
            # Generate signed URL for private buckets (valid for 1 hour)
            url = self.bucket.sign_url('GET', object_name, 3600)
            print(f"DEBUG: Generated SIGNED OSS URL: {url}")
            return url

    def upload_image(self, local_image_path: str) -> Optional[str]:
        """
        Upload a single image to OSS.
        Returns the OSS URL or None if failed.
        """
        if not self.bucket:
            logger.error("OSS bucket not initialized.")
            return None

        if not os.path.exists(local_image_path):
            logger.error(f"Image not found: {local_image_path}")
            return None

        # Return cached URL if available
        if local_image_path in self.uploaded_images:
            return self.uploaded_images[local_image_path]

        try:
            filename = os.path.basename(local_image_path)
            object_name = f"{self.oss_prefix}{filename}"

            # Check if file exists in OSS (optional optimization, skipping for speed unless needed)
            # For now, we overwrite or assume unique names based on UUIDs usually used in this project
            
            logger.info(f"Uploading to OSS: {local_image_path} -> {object_name}")
            
            # Use put_object with explicit file handling instead of put_object_from_file
            # to avoid potential file descriptor issues
            with open(local_image_path, 'rb') as fileobj:
                result = self.bucket.put_object(object_name, fileobj)

            if result.status == 200:
                oss_url = self.get_oss_url(object_name)
                self.uploaded_images[local_image_path] = oss_url
                logger.info(f"Upload success: {oss_url}")
                return oss_url
            else:
                logger.error(f"Upload failed with status: {result.status}")
                return None

        except Exception as e:
            logger.error(f"OSS upload error: {e}")
            return None

    def upload_video(self, local_video_path: str) -> Optional[str]:
        """
        Upload a video to OSS.
        Wrapper around upload_image since the logic is identical.
        """
        return self.upload_image(local_video_path)
