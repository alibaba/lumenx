import os
from typing import Dict, Any
from .models import StoryboardFrame, GenerationStatus
from ...models.wanx import WanxModel
from ...utils import get_logger

logger = get_logger(__name__)

class VideoGenerator:
    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {}
        self.model = WanxModel(self.config.get('model', {}))
        self._comfyui_video_model = None
        self.output_dir = self.config.get('output_dir', 'output/video')

    def get_model(self, model_name: str = None):
        """Route to the ComfyUI video adapter for local models, else default Wanx."""
        if model_name and (model_name.startswith("comfyui/") or model_name.startswith("comfyui-")):
            if self._comfyui_video_model is None:
                from ...models.comfyui_video import ComfyUIVideoModel
                self._comfyui_video_model = ComfyUIVideoModel(self.config.get('model') or {})
            return self._comfyui_video_model
        return self.model

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
        import uuid
        
        logger.info(f"Generating I2V motion reference: prompt={prompt[:50]}..., duration={duration}")
        
        # Handle local file paths
        img_path = None
        if image_url and not image_url.startswith("http"):
            potential_path = os.path.join("output", image_url)
            if os.path.exists(potential_path):
                img_path = os.path.abspath(potential_path)
            elif os.path.exists(image_url):
                img_path = image_url
        
        try:
            output_filename = f"motion_ref_{uuid.uuid4().hex[:8]}.mp4"
            output_path = os.path.join(self.output_dir, output_filename)
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            
            video_path, _ = self.get_model().generate(
                prompt=prompt,
                output_path=output_path,
                img_path=img_path,
                img_url=image_url if not img_path else None
            )
            
            # Upload to OSS if configured
            video_url = os.path.relpath(output_path, "output")
            try:
                from ...utils.oss_utils import OSSImageUploader
                uploader = OSSImageUploader()
                if uploader.is_configured:
                    object_key = uploader.upload_file(output_path, sub_path="motion_ref")
                    if object_key:
                        logger.info(f"Uploaded motion ref video to OSS: {object_key}")
                        video_url = object_key
            except Exception as e:
                logger.error(f"Failed to upload motion ref to OSS: {e}")
            
            return {"video_url": video_url}
            
        except Exception as e:
            logger.error(f"Failed to generate I2V motion reference: {e}")
            raise

    def generate_clip(self, frame: StoryboardFrame) -> StoryboardFrame:
        """Generates a video clip from a storyboard frame."""
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
             # assets.py stores "characters/xxx.png" (relative to output dir usually, but let's check)
             # Wait, assets.py stores `rel_sheet_path = os.path.relpath(sheet_path, "output")`
             # So it is "characters/xxx.png".
             # We need to prepend the output directory.
             
             # Assuming we are running from project root
             potential_path = os.path.join("output", img_url)
             if os.path.exists(potential_path):
                 img_path = os.path.abspath(potential_path)
             else:
                 # Try absolute if it was stored absolute
                 if os.path.exists(img_url):
                     img_path = img_url
        
        try:
            output_path = os.path.join(self.output_dir, f"{frame.id}.mp4")
            
            video_path, _ = self.get_model().generate(
                prompt=prompt,
                output_path=output_path,
                img_path=img_path, # Pass local path, model will upload
                img_url=img_url if not img_path else None # Pass URL if it's already remote
            )
            
            # Store relative path for frontend serving
            rel_path = os.path.relpath(output_path, "output")
            frame.video_url = rel_path
            frame.status = GenerationStatus.COMPLETED
            
            # Try uploading to OSS if configured - store Object Key (not full URL)
            try:
                from ...utils.oss_utils import OSSImageUploader
                uploader = OSSImageUploader()
                if uploader.is_configured:
                    object_key = uploader.upload_file(output_path, sub_path="video")
                    if object_key:
                        logger.info(f"Uploaded video for frame {frame.id} to OSS: {object_key}")
                        # Store Object Key (will be converted to signed URL on API response)
                        frame.video_url = object_key
            except Exception as e:
                logger.error(f"Failed to upload video for frame {frame.id} to OSS: {e}")
                # Continue even if OSS upload fails
        except Exception as e:
            logger.error(f"Failed to generate video for frame {frame.id}: {e}")
            frame.status = GenerationStatus.FAILED
            
        return frame
