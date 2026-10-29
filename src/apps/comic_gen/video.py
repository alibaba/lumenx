import os
from types import MappingProxyType
from typing import Callable, Dict, Any, Optional
from .models import StoryboardFrame, GenerationStatus
from ...models.wanx import WanxModel
from ...utils import get_logger
from ...utils.json_storage import StorageError

logger = get_logger(__name__)


def _default_output_root(output_dir: str, leaf: str) -> str:
    normalized = os.path.normpath(os.fspath(output_dir))
    if os.path.basename(normalized) == leaf:
        parent = os.path.dirname(normalized)
        if parent:
            return parent
    return "output"


def _resolve_output_ref(output_root: str, value: str) -> Optional[str]:
    raw = (value or "").strip()
    if not raw:
        return None
    for prefix in ("/files/outputs/", "/files/output/", "/files/", "files/", "output/", "outputs/"):
        if raw.startswith(prefix):
            raw = raw[len(prefix):]
            break
    root = os.path.realpath(output_root)
    if os.path.isabs(raw):
        candidate = os.path.realpath(raw)
    else:
        candidate = os.path.realpath(os.path.join(root, raw.lstrip("/")))
    if candidate == root or candidate.startswith(root + os.sep):
        return candidate
    return None

class VideoGenerator:
    def __init__(
        self, config: Dict[str, Any] = None,
        *, before_generation: Optional[Callable[[], None]] = None, env=None,
    ):
        self.config = config or {}
        self._env = None if env is None else MappingProxyType(dict(env))
        self._before_generation = before_generation
        self.output_dir = self.config.get('output_dir', 'output/video')
        self.output_root = self.config.get('output_root') or _default_output_root(self.output_dir, "video")
        model_config = dict(self.config.get('model') or {})
        model_config.setdefault('output_root', self.output_root)
        self.model = WanxModel(model_config, **({"env": self._env} if self._env is not None else {}))

    def _check_storage(self) -> None:
        if self._before_generation is not None:
            self._before_generation()

    def _rel_output_path(self, path: str) -> str:
        return os.path.relpath(path, self.output_root)

    def _resolve_output_ref(self, value: str) -> Optional[str]:
        return _resolve_output_ref(self.output_root, value)

    def generate_i2v(self, image_url: str, prompt: str, duration: int = 5, audio_url: str = None) -> Dict[str, Any]:
        """
        Generate Image-to-Video for motion reference.
        
        Args:
            image_url: Source image URL (can be local path or remote URL)
            prompt: Motion description prompt
            duration: Video duration in seconds (default 5)
            audio_url: Optional audio URL to drive lip-sync
            
        Returns:
            Dict with video_url key containing the generated video URL
        """
        self._check_storage()
        if (
            self.model.params.get("i2v_model_name") == "happyhorse-1.0-i2v"
            and (audio_url or self.model.params.get("audio_url"))
        ):
            raise ValueError("Motion reference driving audio is not supported for happyhorse-1.0-i2v")
        import uuid
        
        logger.info(f"Generating I2V motion reference: prompt={prompt[:50]}..., duration={duration}")
        
        # Handle local file paths
        img_path = None
        if image_url and not image_url.startswith("http"):
            potential_path = self._resolve_output_ref(image_url)
            if potential_path and os.path.exists(potential_path):
                img_path = os.path.abspath(potential_path)
        
        try:
            output_filename = f"motion_ref_{uuid.uuid4().hex[:8]}.mp4"
            output_path = os.path.join(self.output_dir, output_filename)
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            
            self._check_storage()
            video_path, _ = self.model.generate(
                prompt=prompt,
                output_path=output_path,
                img_path=img_path,
                img_url=image_url if not img_path else None,
                duration=duration,
                audio_url=audio_url,
            )
            self._check_storage()
            
            # Upload to OSS if configured
            video_url = self._rel_output_path(output_path)
            try:
                from ...utils.oss_utils import OSSImageUploader
                uploader = OSSImageUploader(**({"env": self._env, "output_root": self.output_root} if self._env is not None else {}))
                if uploader.is_configured:
                    self._check_storage()
                    object_key = uploader.upload_file(output_path, sub_path="motion_ref")
                    self._check_storage()
                    if object_key:
                        logger.info(f"Uploaded motion ref video to OSS: {object_key}")
                        video_url = object_key
            except StorageError:
                raise
            except Exception as e:
                logger.error(f"Failed to upload motion ref to OSS: {e}")
            
            return {"video_url": video_url}
            
        except Exception as e:
            logger.error(f"Failed to generate I2V motion reference: {e}")
            raise

    def generate_clip(self, frame: StoryboardFrame) -> StoryboardFrame:
        """Generates a video clip from a storyboard frame."""
        self._check_storage()
        if not frame.image_url:
            logger.error(f"Frame {frame.id} has no image URL. Cannot generate video.")
            frame.status = GenerationStatus.FAILED
            return frame
            
        frame.status = GenerationStatus.PROCESSING
        
        # Use the optimized video prompt if available, otherwise fallback to image prompt or description
        prompt = frame.video_prompt or frame.image_prompt or frame.action_description
        
        # Convert file:// URL to local path if necessary, or ensure the model can handle it.
        # Wanx API needs a public URL or OSS URL. 
        # For this local demo, we might need to assume the user has a way to serve files or upload them.
        # OR we mock the upload.
        # For now, let's assume the image_url is accessible to the API (e.g. if we used an OSS URL earlier).
        # If it's a local file, we can't really call the API unless we upload it.
        
        # TODO: Implement file upload to OSS/S3 here if needed.
        # For the purpose of this demo code, we'll assume the image_url is valid for the API.
        # If it starts with file://, we strip it, but the API won't be able to read local files.
        # We will log a warning.
        
        img_url = frame.image_url
        img_path = None
        
        # Handle local file paths
        if img_url and not img_url.startswith("http"):
             # Assuming img_url is a relative path from project root or output dir
             # We need to resolve it to an absolute path
             # In this project, image_url is usually relative to 'output' or project root?
             # assets.py stores stable URLs relative to the instance output root.
             
             # Assuming we are running from project root
             potential_path = self._resolve_output_ref(img_url)
             if potential_path and os.path.exists(potential_path):
                 img_path = os.path.abspath(potential_path)
        
        try:
            output_path = os.path.join(self.output_dir, f"{frame.id}.mp4")
            
            self._check_storage()
            video_path, _ = self.model.generate(
                prompt=prompt,
                output_path=output_path,
                img_path=img_path, # Pass local path, model will upload
                img_url=img_url if not img_path else None # Pass URL if it's already remote
            )
            self._check_storage()
            
            # Store relative path for frontend serving
            rel_path = self._rel_output_path(output_path)
            frame.video_url = rel_path
            frame.status = GenerationStatus.COMPLETED
            
            # Try uploading to OSS if configured - store Object Key (not full URL)
            try:
                from ...utils.oss_utils import OSSImageUploader
                uploader = OSSImageUploader(**({"env": self._env, "output_root": self.output_root} if self._env is not None else {}))
                if uploader.is_configured:
                    self._check_storage()
                    object_key = uploader.upload_file(output_path, sub_path="video")
                    self._check_storage()
                    if object_key:
                        logger.info(f"Uploaded video for frame {frame.id} to OSS: {object_key}")
                        # Store Object Key (will be converted to signed URL on API response)
                        frame.video_url = object_key
            except StorageError:
                raise
            except Exception as e:
                logger.error(f"Failed to upload video for frame {frame.id} to OSS: {e}")
                # Continue even if OSS upload fails
        except StorageError:
            raise
        except Exception as e:
            logger.error(f"Failed to generate video for frame {frame.id}: {e}")
            frame.status = GenerationStatus.FAILED
            
        return frame
