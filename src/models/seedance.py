"""Seedance video generation model adapter.

Official API: https://ark.cn-beijing.volces.com/api/v3
Auth: Bearer ARK_API_KEY
Model: doubao-seedance-2-0-260128
"""

import logging
import os
import time
from typing import Any, Dict, List, Optional, Tuple

import requests

from .base import VideoGenModel
from ..utils.endpoints import get_provider_base_url
from ..utils.oss_utils import OSSImageUploader
from ..utils.provider_media import resolve_media_input, resolve_media_inputs

logger = logging.getLogger(__name__)

DEFAULT_I2V_MODEL = "doubao-seedance-2-0-260128"
TASKS_PATH = "/contents/generations/tasks"


class SeedanceModel(VideoGenModel):
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.api_key = config.get("api_key") or os.getenv("ARK_API_KEY", "")
        self.model_name = config.get("params", {}).get("model_name", DEFAULT_I2V_MODEL)

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _resolve_vendor_image_input(
        self,
        *,
        img_url: Optional[str] = None,
        img_path: Optional[str] = None,
        model_name: Optional[str] = None,
    ) -> str:
        if isinstance(img_url, str) and img_url.startswith(("http://", "https://")):
            return img_url

        image_ref = img_path or img_url
        if not image_ref:
            raise ValueError("Seedance image input requires img_path or img_url")

        resolved = resolve_media_input(
            image_ref,
            model_name=model_name or self.model_name,
            modality="image",
            backend="vendor",
            uploader=OSSImageUploader(),
        )
        return resolved.value

    def _resolve_vendor_audio_input(
        self,
        *,
        audio_url: Optional[str] = None,
        model_name: Optional[str] = None,
    ) -> str:
        if isinstance(audio_url, str) and audio_url.startswith(("http://", "https://")):
            return audio_url
        if not audio_url:
            raise ValueError("Seedance audio reference requires audio_url")

        resolved = resolve_media_input(
            audio_url,
            model_name=model_name or self.model_name,
            modality="audio",
            backend="vendor",
            uploader=OSSImageUploader(),
        )
        return resolved.value

    def _resolve_vendor_video_inputs(
        self,
        *,
        reference_video_urls: Optional[List[str]] = None,
        model_name: Optional[str] = None,
    ) -> List[str]:
        if not reference_video_urls:
            return []

        resolved_items = resolve_media_inputs(
            reference_video_urls,
            model_name=model_name or self.model_name,
            modality="reference_video",
            backend="vendor",
            uploader=OSSImageUploader(),
        )
        return [item.value for item in resolved_items]

    @staticmethod
    def _request_error_message(resp: requests.Response, action: str) -> RuntimeError:
        try:
            payload = resp.json()
        except Exception:
            payload = resp.text
        return RuntimeError(f"Seedance {action} failed (HTTP {resp.status_code}): {payload}")

    @staticmethod
    def _extract_status(payload: Dict[str, Any]) -> str:
        status = (
            payload.get("status")
            or payload.get("data", {}).get("status")
            or ""
        )
        return str(status).strip().lower()

    @staticmethod
    def _extract_video_url(payload: Dict[str, Any]) -> Optional[str]:
        content = payload.get("content") or payload.get("data", {}).get("content") or {}
        if isinstance(content, dict):
            direct = content.get("video_url") or content.get("videoUrl")
            if isinstance(direct, str) and direct.strip():
                return direct.strip()

            video_urls = content.get("video_urls") or content.get("videoUrls")
            if isinstance(video_urls, list) and video_urls:
                first = video_urls[0]
                if isinstance(first, str) and first.strip():
                    return first.strip()
                if isinstance(first, dict):
                    candidate = first.get("url")
                    if isinstance(candidate, str) and candidate.strip():
                        return candidate.strip()

        for candidate in (
            payload.get("video_url"),
            payload.get("url"),
        ):
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
        return None

    @staticmethod
    def _extract_error(payload: Dict[str, Any]) -> str:
        for key in ("message", "error", "detail"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        if isinstance(payload.get("error"), dict):
            message = payload["error"].get("message")
            if isinstance(message, str) and message.strip():
                return message.strip()
        return str(payload)

    def _submit_task(
        self,
        *,
        prompt: str,
        model_name: str,
        img_url: Optional[str],
        img_path: Optional[str],
        duration: int,
        resolution: str,
        ratio: str,
        seed: Optional[int],
        generate_audio: bool,
        watermark: bool,
        camera_fixed: Optional[bool],
        reference_audio_url: Optional[str],
        reference_video_urls: Optional[List[str]],
        reference_mode: Optional[str],
        workflow: Optional[str],
        workflow_mode: Optional[str],
    ) -> str:
        content = [{"type": "text", "text": prompt or ""}]
        normalized_reference_mode = (reference_mode or "image").strip().lower()
        use_image_reference = normalized_reference_mode in {"image", "combo"}
        use_video_reference = normalized_reference_mode in {"video", "combo"}
        use_audio_reference = normalized_reference_mode == "combo"
        normalized_workflow = (workflow or "standard").strip().lower()

        if use_image_reference and (img_url or img_path):
            content.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": self._resolve_vendor_image_input(
                            img_url=img_url,
                            img_path=img_path,
                            model_name=model_name,
                        )
                    },
                }
            )

        if use_video_reference:
            for reference_video_url in self._resolve_vendor_video_inputs(
                reference_video_urls=reference_video_urls,
                model_name=model_name,
            ):
                content.append(
                    {
                        "type": "video_url",
                        "video_url": {
                            "url": reference_video_url,
                        },
                    }
                )

        if use_audio_reference and reference_audio_url:
            content.append(
                {
                    "type": "audio_url",
                    "audio_url": {
                        "url": self._resolve_vendor_audio_input(
                            audio_url=reference_audio_url,
                            model_name=model_name,
                        ),
                    },
                }
            )

        body: Dict[str, Any] = {
            "model": model_name,
            "content": content,
            "duration": int(duration),
            "generate_audio": bool(generate_audio),
            "watermark": bool(watermark),
            "ratio": ratio,
            "reference_mode": normalized_reference_mode,
            "workflow": normalized_workflow,
        }
        if resolution:
            body["resolution"] = str(resolution).lower()
        if seed is not None:
            body["seed"] = seed
        if camera_fixed is not None:
            body["camera_fixed"] = bool(camera_fixed)
        if workflow_mode and normalized_workflow != "standard":
            body["workflow_mode"] = workflow_mode

        submit_url = f"{get_provider_base_url('ARK')}{TASKS_PATH}"
        logger.info("[Seedance] Submitting task (model=%s, resolution=%s, duration=%ss)", model_name, resolution, duration)
        response = requests.post(submit_url, headers=self._headers(), json=body, timeout=30)
        if response.status_code not in (200, 201):
            raise self._request_error_message(response, "submission")

        data = response.json()
        task_id = data.get("id") or data.get("data", {}).get("id") or data.get("task_id")
        if not task_id:
            raise RuntimeError(f"Seedance submission missing task id: {data}")
        return str(task_id)

    def generate(
        self,
        prompt: str,
        output_path: str,
        img_url: str = None,
        img_path: str = None,
        **kwargs,
    ) -> Tuple[str, float]:
        """Generate video using Seedance / ARK task API."""
        if not self.api_key:
            raise ValueError("ARK_API_KEY is required for Seedance video generation")

        if kwargs.get("audio_url"):
            logger.warning("[Seedance] audio_url is not wired yet and will be ignored.")

        start_time = time.time()
        model_name = kwargs.get("model") or self.model_name
        duration = kwargs.get("duration", 5)
        resolution = kwargs.get("resolution") or "720p"
        ratio = kwargs.get("aspect_ratio") or ("adaptive" if (img_url or img_path) else "16:9")
        seed = kwargs.get("seed")
        generate_audio = bool(kwargs.get("generate_audio", kwargs.get("audio", False)))
        watermark = bool(kwargs.get("watermark", False))
        camera_fixed = kwargs.get("camera_fixed")
        reference_audio_url = kwargs.get("reference_audio_url")
        reference_video_urls = kwargs.get("reference_video_urls") or []
        reference_mode = kwargs.get("reference_mode")
        workflow = kwargs.get("workflow")
        workflow_mode = kwargs.get("workflow_mode")

        task_id = self._submit_task(
            prompt=prompt,
            model_name=model_name,
            img_url=img_url,
            img_path=img_path,
            duration=duration,
            resolution=resolution,
            ratio=ratio,
            seed=seed,
            generate_audio=generate_audio,
            watermark=watermark,
            camera_fixed=camera_fixed,
            reference_audio_url=reference_audio_url,
            reference_video_urls=reference_video_urls,
            reference_mode=reference_mode,
            workflow=workflow,
            workflow_mode=workflow_mode,
        )

        poll_url = f"{get_provider_base_url('ARK')}{TASKS_PATH}/{task_id}"
        max_wait = 900
        poll_interval = 10
        elapsed = 0

        while elapsed < max_wait:
            time.sleep(poll_interval)
            elapsed += poll_interval

            resp = requests.get(poll_url, headers=self._headers(), timeout=30)
            if resp.status_code != 200:
                raise self._request_error_message(resp, "polling")

            payload = resp.json()
            status = self._extract_status(payload)
            logger.info("[Seedance] Task status: %s (%ss)", status or "unknown", elapsed)

            if status == "succeeded":
                video_url = self._extract_video_url(payload)
                if not video_url:
                    raise RuntimeError(f"Seedance task succeeded but video_url is missing: {payload}")

                download_resp = requests.get(video_url, timeout=120)
                download_resp.raise_for_status()
                os.makedirs(os.path.dirname(output_path), exist_ok=True)
                with open(output_path, "wb") as f:
                    f.write(download_resp.content)

                generation_time = time.time() - start_time
                logger.info("[Seedance] Done in %.1fs -> %s", generation_time, output_path)
                return output_path, generation_time

            if status in {"failed", "canceled", "cancelled"}:
                raise RuntimeError(f"Seedance task failed: {self._extract_error(payload)}")

        raise RuntimeError(f"Seedance task timed out after {max_wait}s")
