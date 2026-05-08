"""Kling video generation model adapter.

API: https://api-beijing.klingai.com/v1
Auth: JWT (HS256) using KLING_ACCESS_KEY + KLING_SECRET_KEY
Models: kling-v2-6 (default), kling-v2-5-turbo
"""

import logging
import os
import time
from typing import Dict, Any, Tuple

import jwt
import requests

from .base import VideoGenModel
from ..utils.endpoints import get_provider_base_url
from ..utils.http_downloads import download_url_to_file
from ..utils.oss_utils import OSSImageUploader
from ..utils.provider_media import resolve_media_input

logger = logging.getLogger(__name__)


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

    def _resolve_vendor_image_input(
        self,
        *,
        img_url: str = None,
        img_path: str = None,
        model_name: str = None,
    ) -> str:
        """
        Resolve Kling vendor image input via the shared provider-media layer.

        Prefer the local file when available so pipeline-downloaded or output-relative
        refs become valid base64 payloads. If only a remote URL is available, keep the
        legacy pass-through behavior for direct adapter calls.
        """
        image_ref = img_path or img_url
        if not image_ref:
            raise ValueError("Kling image input requires img_path or img_url")

        if not img_path and isinstance(img_url, str) and img_url.startswith(("http://", "https://")):
            return img_url

        resolved = resolve_media_input(
            image_ref,
            model_name=model_name or self.model_name,
            modality="image",
            backend="vendor",
            uploader=OSSImageUploader(),
        )
        return resolved.value

    def generate(self, prompt: str, output_path: str, img_url: str = None,
                 img_path: str = None, **kwargs) -> Tuple[str, float]:
        """Generate video using Kling API (T2V or I2V)."""
        headers = self._auth_headers()
        model_name = kwargs.get("model") or self.model_name
        duration = kwargs.get("duration", 5)
        aspect_ratio = kwargs.get("aspect_ratio", "16:9")
        negative_prompt = kwargs.get("negative_prompt", "")
        mode = kwargs.get("mode", "pro")
        sound = kwargs.get("sound")  # "on" or "off"
        cfg_scale = kwargs.get("cfg_scale")  # 0-1

        start_time = time.time()

        is_i2v = bool(img_url or img_path)
        base_url = get_provider_base_url("KLING")

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
            body["image"] = self._resolve_vendor_image_input(
                img_url=img_url,
                img_path=img_path,
                model_name=model_name,
            )

            submit_url = f"{base_url}/videos/image2video"
            poll_base = f"{base_url}/videos/image2video"
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
            submit_url = f"{base_url}/videos/text2video"
            poll_base = f"{base_url}/videos/text2video"

        # Optional params
        if sound is not None:
            body["sound"] = sound
        if cfg_scale is not None:
            body["cfg_scale"] = cfg_scale

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
                download_url_to_file(video_url, output_path, timeout=(30, 120))

                generation_time = time.time() - start_time
                logger.info(f"[Kling] Done in {generation_time:.1f}s -> {output_path}")
                return output_path, generation_time

            elif status == "failed":
                msg = result_data["data"].get("task_status_msg", "Unknown error")
                raise RuntimeError(f"Kling task failed: {msg}")

        raise RuntimeError(f"Kling task timed out after {max_wait}s")
