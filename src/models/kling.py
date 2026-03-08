"""Kling video generation model adapter.

API: https://api-beijing.klingai.com/v1
Auth: JWT (HS256) using KLING_ACCESS_KEY + KLING_SECRET_KEY
Models: kling-v2-6 (default), kling-v2-5-turbo
"""

import base64
import logging
import os
import re
import time
from typing import Dict, Any, Tuple

import jwt
import requests

from .base import VideoGenModel

logger = logging.getLogger(__name__)

BASE_URL = "https://api-beijing.klingai.com/v1"


class KlingModel(VideoGenModel):
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.access_key = config.get("access_key") or os.getenv("KLING_ACCESS_KEY", "")
        self.secret_key = config.get("secret_key") or os.getenv("KLING_SECRET_KEY", "")
        self.model_name = config.get("params", {}).get("model_name", "kling-v3")
        self._cached_token = None
        self._token_exp = 0

    def _get_token(self) -> str:
        """Generate a signed JWT token, cached until near expiry."""
        now = int(time.time())
        # Reuse cached token if still valid (with 60s buffer)
        if self._cached_token and now < self._token_exp - 60:
            return self._cached_token
        headers = {"alg": "HS256", "typ": "JWT"}
        exp = now + 1800
        payload = {
            "iss": self.access_key,
            "exp": exp,
            "nbf": now - 30,
        }
        self._cached_token = jwt.encode(payload, self.secret_key, algorithm="HS256", headers=headers)
        self._token_exp = exp
        return self._cached_token

    def _auth_headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self._get_token()}",
            "Content-Type": "application/json",
        }

    @staticmethod
    def _resolve_image(image_path: str) -> str:
        """Return a URL string or base64 data for a local file."""
        if image_path.startswith(("http://", "https://", "data:")):
            return image_path
        if not os.path.isfile(image_path):
            raise FileNotFoundError(f"Image file not found: {image_path}")
        with open(image_path, "rb") as f:
            data = base64.b64encode(f.read()).decode()
        ext = os.path.splitext(image_path)[1].lower().lstrip(".")
        mime_map = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "webp": "webp"}
        mime_sub = mime_map.get(ext, "png")
        return f"data:image/{mime_sub};base64,{data}"

    @staticmethod
    def _strip_data_prefix(value: str) -> str:
        """Strip 'data:xxx;base64,' prefix. Kling expects pure base64."""
        match = re.match(r"^data:[^;]+;base64,(.+)$", value, re.DOTALL)
        if match:
            return match.group(1)
        return value

    def generate(self, prompt: str, output_path: str, img_url: str = None,
                 img_path: str = None, **kwargs) -> Tuple[str, float]:
        """Generate video using Kling API (T2V or I2V)."""
        headers = self._auth_headers()
        model_name = kwargs.get("model") or self.model_name
        duration = kwargs.get("duration", 5)
        aspect_ratio = kwargs.get("aspect_ratio", "16:9")
        negative_prompt = kwargs.get("negative_prompt", "")
        mode = kwargs.get("mode", "pro")

        start_time = time.time()

        is_i2v = bool(img_url or img_path)

        if is_i2v:
            # Image-to-Video
            body: Dict[str, Any] = {
                "model_name": model_name,
                "prompt": prompt,
                "negative_prompt": negative_prompt,
                "mode": mode,
                "duration": duration,
                "aspect_ratio": aspect_ratio,
            }

            # Resolve image
            image_source = img_url or img_path
            image_value = self._resolve_image(image_source)
            body["image"] = self._strip_data_prefix(image_value)

            submit_url = f"{BASE_URL}/videos/image2video"
            poll_base = f"{BASE_URL}/videos/image2video"
        else:
            # Text-to-Video
            body = {
                "model_name": model_name,
                "prompt": prompt,
                "negative_prompt": negative_prompt,
                "mode": mode,
                "duration": str(duration),  # T2V expects string
                "aspect_ratio": aspect_ratio,
            }
            submit_url = f"{BASE_URL}/videos/text2video"
            poll_base = f"{BASE_URL}/videos/text2video"

        # Submit task
        logger.info(f"[Kling] Submitting {'i2v' if is_i2v else 't2v'} task (model={model_name})")
        response = requests.post(submit_url, headers=headers, json=body, timeout=30)
        response.raise_for_status()
        task_data = response.json()

        if task_data.get("code") != 0:
            raise RuntimeError(
                f"Kling API error (code {task_data.get('code')}): "
                f"{task_data.get('message', 'unknown error')}"
            )

        task_id = task_data["data"]["task_id"]
        logger.info(f"[Kling] Task submitted: {task_id}")

        # Poll for result
        poll_url = f"{poll_base}/{task_id}"
        max_wait = 600
        poll_interval = 10
        elapsed = 0

        while elapsed < max_wait:
            time.sleep(poll_interval)
            elapsed += poll_interval

            resp = requests.get(poll_url, headers=self._auth_headers(), timeout=30)
            resp.raise_for_status()
            result_data = resp.json()

            if result_data.get("code") != 0:
                raise RuntimeError(f"Kling poll error: {result_data.get('message')}")

            status = result_data["data"]["task_status"]
            logger.info(f"[Kling] Task status: {status} ({elapsed}s)")

            if status == "succeed":
                video_url = result_data["data"]["task_result"]["videos"][0]["url"]
                # Download video
                video_content = requests.get(video_url, timeout=120).content
                os.makedirs(os.path.dirname(output_path), exist_ok=True)
                with open(output_path, "wb") as f:
                    f.write(video_content)

                generation_time = time.time() - start_time
                logger.info(f"[Kling] Done in {generation_time:.1f}s -> {output_path}")
                return output_path, generation_time

            elif status == "failed":
                msg = result_data["data"].get("task_status_msg", "Unknown error")
                raise RuntimeError(f"Kling task failed: {msg}")

        raise RuntimeError(f"Kling task timed out after {max_wait}s")
