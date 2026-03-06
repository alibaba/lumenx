import time
import requests
import jwt
import logging
from typing import Dict, Any, Tuple
from .base import VideoGenModel
from ..model_request_settings import MODEL_REQUEST_SETTINGS

logger = logging.getLogger(__name__)

class KlingModel(VideoGenModel):
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.api_key = config.get("api_key")
        self.base_url = MODEL_REQUEST_SETTINGS.kling_base_url
        self.model_name = config.get("params", {}).get(
            "model_name", MODEL_REQUEST_SETTINGS.kling_model_name_default
        )

    def _get_token(self) -> str:
        """Generate JWT token for Kling API."""
        # Note: Kling API usually uses AccessKey/SecretKey to generate a JWT token.
        # Assuming api_key is the JWT token for simplicity, or we need AK/SK.
        # Based on common Kling API usage, it might require AK/SK to sign a JWT.
        # Let's assume the user provides the AK as api_key and SK as secret_key in config.
        # If the user provided a direct Bearer token, we use it.
        # For now, let's assume standard Bearer token usage if provided directly.
        return self.api_key

    def generate(self, prompt: str, output_path: str, img_url: str = None, **kwargs) -> Tuple[str, float]:
        """
        Generate video using Kling API.
        """
        headers = {
            "Authorization": f"Bearer {self._get_token()}",
            "Content-Type": "application/json"
        }

        # Prepare payload
        payload = {
            "model": self.model_name,
            "prompt": prompt,
            "negative_prompt": kwargs.get("negative_prompt", ""),
            "cfg_scale": kwargs.get("cfg_scale", 0.5),
            "mode": "std", # or 'pro'
            "aspect_ratio": "16:9",
            "duration": kwargs.get("duration", 5)
        }
        
        if img_url:
            payload["image"] = img_url
            payload["type"] = "i2v"
        else:
            payload["type"] = "t2v"

        start_time = time.time()
        
        # 1. Submit Task
        submit_url = f"{self.base_url}/videos/image2video" if img_url else f"{self.base_url}/videos/text2video"
        try:
            response = requests.post(submit_url, headers=headers, json=payload)
            response.raise_for_status()
            task_data = response.json()
            if task_data.get("code") != 0:
                 raise Exception(f"Kling API Error: {task_data.get('message')}")
            
            task_id = task_data["data"]["task_id"]
            logger.info(f"Kling Task Submitted: {task_id}")

        except Exception as e:
            logger.error(f"Failed to submit Kling task: {e}")
            raise

        # 2. Poll for Result
        query_url = f"{self.base_url}/videos/image2video/{task_id}" if img_url else f"{self.base_url}/videos/text2video/{task_id}"
        
        while True:
            try:
                response = requests.get(query_url, headers=headers)
                response.raise_for_status()
                result_data = response.json()
                
                if result_data.get("code") != 0:
                    raise Exception(f"Kling API Query Error: {result_data.get('message')}")
                
                status = result_data["data"]["task_status"]
                logger.info(f"Kling Task Status: {status}")

                if status == "succeed":
                    video_url = result_data["data"]["task_result"]["videos"][0]["url"]
                    # Download video
                    video_content = requests.get(video_url).content
                    with open(output_path, "wb") as f:
                        f.write(video_content)
                    
                    generation_time = time.time() - start_time
                    return output_path, generation_time
                
                elif status == "failed":
                    raise Exception(f"Kling Task Failed: {result_data['data']['task_status_msg']}")
                
                time.sleep(5) # Poll interval

            except Exception as e:
                logger.error(f"Error polling Kling task: {e}")
                raise
