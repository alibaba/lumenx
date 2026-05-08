import os
import time
import logging
import base64
from typing import Tuple, Optional
from .base import VideoGenModel
from ..utils.http_downloads import download_url_to_file

# Try to import Ark, handle if not installed (though user said they installed it)
try:
    from volcenginesdkarkruntime import Ark
except ImportError:
    Ark = None

logger = logging.getLogger(__name__)

class DoubaoModel(VideoGenModel):
    def __init__(self, config: dict):
        super().__init__(config)
        self.api_key = os.getenv("ARK_API_KEY")
        self.model_name = config.get('params', {}).get('model_name', 'doubao-seedance-1-0-pro-fast-251015')
        
        if not self.api_key:
            logger.warning("ARK_API_KEY not found in environment variables.")
            
        if Ark:
            self.client = Ark(
                base_url="https://ark.cn-beijing.volces.com/api/v3",
                api_key=self.api_key
            )
        else:
            self.client = None
            logger.error("volcenginesdkarkruntime not installed.")

    def _encode_image_to_base64(self, image_path: str) -> str:
        with open(image_path, "rb") as image_file:
            return base64.b64encode(image_file.read()).decode('utf-8')

    def generate(self, prompt: str, output_path: str, **kwargs) -> Tuple[str, float]:
        """
        Generate video using Doubao SeeDance-Pro-Fast model via Ark SDK.
        """
        if not self.client:
            raise RuntimeError("Ark client not initialized. Please install volcenginesdkarkruntime.")

        img_url = kwargs.get('img_url')
        if not img_url:
            raise ValueError("Doubao SeeDance model requires an input image (img_url).")

        # Handle file:// prefix
        if img_url.startswith("file://"):
            local_path = img_url[7:]
            # Convert local file to base64 data URL
            base64_image = self._encode_image_to_base64(local_path)
            # Guess mime type based on extension
            ext = os.path.splitext(local_path)[1].lower()
            mime_type = "image/png" if ext == ".png" else "image/jpeg"
            final_image_url = f"data:{mime_type};base64,{base64_image}"
        else:
            final_image_url = img_url

        logger.info(f"Calling Doubao {self.model_name} with prompt: {prompt}")
        start_time = time.time()

        try:
            # Create task
            create_result = self.client.content_generation.tasks.create(
                model=self.model_name,
                content=[
                    {
                        "type": "text",
                        "text": f"{prompt} --resolution 720p --duration 5 --camerafixed false --watermark false"
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": final_image_url
                        }
                    }
                ]
            )
            
            task_id = create_result.id
            logger.info(f"Doubao Task ID: {task_id}")

            # Poll for result
            while True:
                get_result = self.client.content_generation.tasks.get(task_id=task_id)
                status = get_result.status
                
                if status == "succeeded":
                    logger.info("Doubao task succeeded.")
                    # Get video URL from response
                    # Response structure: get_result.content.video_url
                    video_url = None
                    if hasattr(get_result, 'content') and get_result.content:
                        if hasattr(get_result.content, 'video_url'):
                            video_url = get_result.content.video_url
                    
                    if not video_url:
                        logger.warning(f"Could not parse video URL from result: {get_result}")
                        raise ValueError("No video URL found in response")

                    # Download video
                    self._download_video(video_url, output_path)
                    break
                    
                elif status == "failed":
                    logger.error(f"Doubao task failed: {get_result.error}")
                    raise RuntimeError(f"Doubao generation failed: {get_result.error}")
                else:
                    time.sleep(2)
                    
        except Exception as e:
            logger.error(f"Error calling Doubao API: {e}")
            raise

        api_duration = time.time() - start_time
        return output_path, api_duration

    def _download_video(self, url: str, output_path: str):
        logger.info(f"Downloading video from {url} to {output_path}...")
        download_url_to_file(url, output_path, timeout=(30, 120))
        logger.info("Download complete.")
