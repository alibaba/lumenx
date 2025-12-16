import os
import time
import requests
from http import HTTPStatus
from dashscope import VideoSynthesis
import dashscope
from .base import VideoGenModel
from ..utils import get_logger

from typing import Tuple

from ..utils.oss_utils import OSSImageUploader

logger = get_logger(__name__)


class WanxModel(VideoGenModel):
    def __init__(self, config):
        super().__init__(config)
        self.api_key = config.get('api_key')
        if not self.api_key:
            # Try getting from env if not in config
            self.api_key = os.getenv("DASHSCOPE_API_KEY")

        if not self.api_key:
            logger.warning("Dashscope API Key not found in config or environment variables.")

        # Set dashscope API key
        dashscope.api_key = self.api_key

        self.params = config.get('params', {})

    def generate(self, prompt: str, output_path: str, img_path: str = None, **kwargs) -> Tuple[str, float]:
        # Determine model
        if img_path or kwargs.get('img_url'):
            model_name = self.params.get('i2v_model_name', 'wan2.5-i2v-preview')  # Default to I2V model
            logger.info(f"Using I2V model: {model_name}")
        else:
            model_name = self.params.get('model_name', 'wan2.5-t2v-preview')
            logger.info(f"Using T2V model: {model_name}")

        size = self.params.get('size', '1280*720')
        prompt_extend = self.params.get('prompt_extend', True)
        watermark = self.params.get('watermark', False)

        # New parameters - prioritize kwargs, fallback to params
        duration = kwargs.get('duration') or self.params.get('duration', 5)
        negative_prompt = kwargs.get('negative_prompt') or self.params.get('negative_prompt', '')
        audio_url = kwargs.get('audio_url') or self.params.get('audio_url', '')
        seed = kwargs.get('seed') or self.params.get('seed')

        # Resolution mapping
        resolution = kwargs.get('resolution') or self.params.get('resolution', '720p')
        if resolution == '1080p':
            size = "1920*1080"
        elif resolution == '480p':
            size = "832*480"
        else:
            size = "1280*720"

        # Motion params
        camera_motion = kwargs.get('camera_motion')
        subject_motion = kwargs.get('subject_motion')

        logger.info(f"Starting generation with model: {model_name}")
        logger.info(f"Prompt: {prompt}")

        try:
            api_start_time = time.time()

            # Prepare arguments
            call_args = {
                "api_key": self.api_key,
                "model": model_name,
                "prompt": prompt,
                "size": size,
                "prompt_extend": prompt_extend,
                "watermark": watermark,
            }

            # Add optional arguments if they exist
            if negative_prompt:
                call_args['negative_prompt'] = negative_prompt
            if duration:
                call_args['duration'] = duration
            if audio_url:
                call_args['audio_url'] = audio_url
            if seed:
                call_args['seed'] = seed
            if camera_motion:
                # Assuming API accepts camera_motion as is or we need to format it
                # For now passing as is, assuming the SDK handles it or it's a dict
                call_args['camera_motion'] = camera_motion
            if subject_motion:
                call_args[
                    'motion_scale'] = subject_motion  # Mapping subject_motion to motion_scale? Or just passing it.
                # Common param name is motion_scale or similar. Let's try motion_scale if subject_motion is a number.

            # Image to Video support
            img_url = kwargs.get('img_url')

            # If local path provided, upload to OSS
            if img_path:
                if not os.path.exists(img_path):
                    raise ValueError(f"Input image not found: {img_path}")

                logger.info(f"Uploading input image to OSS: {img_path}")
                uploaded_url = OSSImageUploader().upload_image(img_path)
                if not uploaded_url:
                    raise RuntimeError("Failed to upload input image to OSS")
                img_url = uploaded_url

            if img_url:
                call_args['img_url'] = img_url
                logger.info(f"Image to Video mode. Input Image URL: {img_url}")

            rsp = VideoSynthesis.async_call(**call_args)

            if rsp.status_code != HTTPStatus.OK:
                logger.error(f"Failed to submit task: {rsp.code}, {rsp.message}")
                raise RuntimeError(f"Task submission failed: {rsp.message}")

            task_id = rsp.output.task_id
            logger.info(f"Task submitted. Task ID: {task_id}")

            # Wait for completion
            rsp = VideoSynthesis.wait(rsp)
            api_end_time = time.time()
            api_duration = api_end_time - api_start_time

            logger.info(f"Final response: {rsp}")

            if rsp.status_code != HTTPStatus.OK:
                logger.error(
                    f"Task failed with status code: {rsp.status_code}, code: {rsp.code}, message: {rsp.message}")
                raise RuntimeError(f"Task failed: {rsp.message}")

            if rsp.output.task_status != 'SUCCEEDED':
                logger.error(
                    f"Task finished but status is {rsp.output.task_status}. Code: {rsp.output.code}, Message: {rsp.output.message}")
                raise RuntimeError(f"Task failed with status {rsp.output.task_status}: {rsp.output.message}")

            video_url = rsp.output.video_url
            if not video_url:
                logger.error("Video URL is empty despite SUCCEEDED status.")
                raise RuntimeError("Video URL is empty.")

            logger.info(f"Generation success. Video URL: {video_url}")

            # Download video
            self._download_video(video_url, output_path)
            return output_path, api_duration

        except Exception as e:
            logger.error(f"Error during generation: {e}")
            raise

    def _download_video(self, url: str, path: str):
        logger.info(f"Downloading video to {path}...")

        from requests.adapters import HTTPAdapter
        from requests.packages.urllib3.util.retry import Retry

        session = requests.Session()
        retry = Retry(connect=3, backoff_factor=0.5)
        adapter = HTTPAdapter(max_retries=retry)
        session.mount('http://', adapter)
        session.mount('https://', adapter)

        temp_path = path + ".tmp"
        try:
            response = session.get(url, stream=True, timeout=60)
            response.raise_for_status()

            with open(temp_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)

            # Atomic rename
            os.rename(temp_path, path)
            logger.info("Download complete.")

        except Exception as e:
            logger.error(f"Failed to download video: {e}")
            if os.path.exists(temp_path):
                os.remove(temp_path)
            raise
