"""ComfyUI video generation model.

Drop-in replacement for :class:`WanxModel` (same ``generate`` contract:
``(prompt, output_path, img_path=None, model_name=None, **kwargs) ->
(output_path, api_duration)``).  Supports i2v / r2v / first-last-frame video
generation through local ComfyUI workflows.
"""
from __future__ import annotations

import os
import tempfile
import time
from typing import Any, Dict, List, Optional

import requests

from ..utils import get_logger
from .comfyui_client import ComfyUIClient, load_workflow_mapping
from .comfyui_image import _normalize_model_name

logger = get_logger(__name__)


class ComfyUIVideoModel:
    """ComfyUI workflow-driven video generation (i2v / r2v / first-last frame)."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.comfyui_client = ComfyUIClient(
            base_url=self.config.get("base_url"),
            protocol=self.config.get("protocol"),
        )
        self.workflow_mapping = self.config.get("workflow_mapping") or load_workflow_mapping()
        self.output_dir = self.config.get("output_dir", "output/video")

    # ------------------------------------------------------------------
    # Primary interface (WanxModel-compatible)
    # ------------------------------------------------------------------

    def generate(
        self,
        prompt: str,
        output_path: str,
        img_path: Optional[str] = None,
        model_name: Optional[str] = None,
        **kwargs,
    ) -> tuple[str, float]:
        """Generate a video and save it to ``output_path``.

        Accepts the full kwarg surface the pipeline passes to WanxModel
        (duration, seed, resolution, ratio, negative_prompt, audio_url,
        ref_image_urls, ref_video_urls, generation_mode, watermark, ...).
        """
        start = time.time()
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

        mode = self._resolve_mode(model_name=model_name, **kwargs)
        base_id, _ = _normalize_model_name(model_name)
        workflow_id = self._resolve_workflow(model_name=model_name, mode=mode, **kwargs)
        if not workflow_id:
            raise RuntimeError(
                "No suitable ComfyUI workflow available for video generation "
                "(check COMFYUI_BASE_URL and config/workflow_mapping.json)"
            )
        logger.info("Using ComfyUI video workflow (%s): %s", mode, workflow_id)

        parameters: Dict[str, Any] = {
            "prompt": prompt or "",
            "duration": kwargs.get("duration", 5),
            "batch_size": kwargs.get("batch_size", 1),
        }
        if kwargs.get("seed") is not None:
            parameters["seed"] = kwargs["seed"]
        if kwargs.get("resolution"):
            parameters["resolution"] = kwargs["resolution"]
        if kwargs.get("ratio"):
            parameters["ratio"] = kwargs["ratio"]
        if kwargs.get("negative_prompt"):
            parameters["negative_prompt"] = kwargs["negative_prompt"]
        if kwargs.get("audio_url"):
            parameters["audio_url"] = kwargs["audio_url"]
        if mode == "r2v":
            parameters["reference_videos"] = self._resolve_media_list(
                kwargs.get("ref_video_urls") or []
            )
            parameters["reference_images"] = self._resolve_media_list(
                kwargs.get("ref_image_urls") or []
            )
        if mode == "first_last_frame":
            parameters["first_frame"] = self._resolve_image_input(
                kwargs.get("first_frame_path") or kwargs.get("image_url")
            )
            parameters["last_frame"] = self._resolve_image_input(
                kwargs.get("last_frame_path")
            )
        else:
            image_input = self._resolve_image_input(img_path or kwargs.get("img_url"))
            parameters["image"] = image_input
            if kwargs.get("image_url"):
                parameters["image_url"] = kwargs["image_url"]

        for key, value in kwargs.items():
            if key in {
                "output_path", "model", "model_name", "workflow_id", "generation_mode",
                "img_path", "img_url", "ref_image_urls", "ref_video_urls",
                "first_frame_path", "last_frame_path",
            }:
                continue
            if value is None or key in parameters:
                continue
            try:
                import json

                json.dumps(value)
                parameters[key] = value
            except (TypeError, ValueError):
                logger.debug("Skipping non-JSON ComfyUI video parameter %s", key)

        task_id = self.comfyui_client.submit_workflow_task(
            workflow_id=workflow_id,
            parameters=parameters,
        )
        if not task_id:
            raise RuntimeError("Failed to submit video generation task to ComfyUI")

        result = self.comfyui_client.wait_for_task_completion(
            task_id=task_id,
            timeout=int(self.config.get("timeout", 900)),
        )
        if not result or result.get("status") != "completed":
            raise RuntimeError(
                "ComfyUI video generation failed: %s"
                % (result.get("error") if result else "task failed or timed out")
            )

        video_path = self._download_first_video(result, output_path)
        if not video_path:
            raise RuntimeError("ComfyUI returned no generated videos")
        logger.info("ComfyUI video generated: %s", video_path)
        return video_path, time.time() - start

    # ------------------------------------------------------------------
    # Compatibility helpers (fork API)
    # ------------------------------------------------------------------

    def generate_video_i2v(
        self,
        image_path: str,
        prompt: str = "",
        output_path: Optional[str] = None,
        workflow_id: Optional[str] = None,
        duration: float = 5.0,
        seed: Optional[int] = None,
        batch_size: int = 1,
        **kwargs,
    ) -> List[str]:
        if not output_path:
            os.makedirs(self.output_dir, exist_ok=True)
            output_path = os.path.join(self.output_dir, f"comfyui_{int(time.time())}.mp4")
        path, _ = self.generate(
            prompt=prompt,
            output_path=output_path,
            img_path=image_path,
            workflow_id=workflow_id,
            duration=duration,
            seed=seed,
            batch_size=batch_size,
            **kwargs,
        )
        return [path]

    def generate_video_r2v(
        self,
        image_path: str,
        reference_video_path: str,
        output_path: Optional[str] = None,
        workflow_id: Optional[str] = None,
        **kwargs,
    ) -> Optional[str]:
        if not output_path:
            os.makedirs(self.output_dir, exist_ok=True)
            output_path = os.path.join(self.output_dir, f"comfyui_r2v_{int(time.time())}.mp4")
        try:
            path, _ = self.generate(
                prompt=kwargs.pop("prompt", ""),
                output_path=output_path,
                img_path=image_path,
                model_name=kwargs.pop("model_name", None),
                workflow_id=workflow_id,
                generation_mode="r2v",
                ref_video_urls=[reference_video_path],
                **kwargs,
            )
            return path
        except Exception as exc:  # noqa: BLE001
            logger.error("Error generating r2v video: %s", exc)
            return None

    def generate_video_first_last_frame(
        self,
        first_frame_path: str,
        last_frame_path: str,
        prompt: str = "",
        output_path: Optional[str] = None,
        workflow_id: Optional[str] = None,
        **kwargs,
    ) -> Optional[str]:
        if not output_path:
            os.makedirs(self.output_dir, exist_ok=True)
            output_path = os.path.join(self.output_dir, f"comfyui_flf_{int(time.time())}.mp4")
        try:
            path, _ = self.generate(
                prompt=prompt,
                output_path=output_path,
                workflow_id=workflow_id,
                generation_mode="first_last_frame",
                first_frame_path=first_frame_path,
                last_frame_path=last_frame_path,
                **kwargs,
            )
            return path
        except Exception as exc:  # noqa: BLE001
            logger.error("Error generating first-last frame video: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_mode(model_name: Optional[str], **kwargs) -> str:
        generation_mode = (kwargs.get("generation_mode") or "").lower()
        if "first" in generation_mode or "last" in generation_mode:
            return "first_last_frame"
        if "r2v" in generation_mode:
            return "r2v"
        if kwargs.get("ref_video_urls") or kwargs.get("ref_image_urls"):
            return "r2v"
        _, mode_from_id = _normalize_model_name(model_name)
        if "r2v" in mode_from_id:
            return "r2v"
        if "first" in mode_from_id:
            return "first_last_frame"
        return "i2v"

    def _resolve_workflow(
        self,
        model_name: Optional[str],
        mode: str,
        **kwargs,
    ) -> Optional[str]:
        explicit = kwargs.get("workflow_id")
        if explicit:
            return explicit

        base_id, _ = _normalize_model_name(model_name)
        overrides = self.workflow_mapping.get("model_overrides", {})
        for candidate in (
            model_name,
            base_id,
            f"comfyui-{base_id}" if base_id and not base_id.startswith("comfyui") else base_id,
        ):
            if not candidate:
                continue
            mapped = overrides.get(candidate) or overrides.get(str(candidate).lower())
            if mapped:
                logger.info("ComfyUI workflow from model override: %s -> %s", candidate, mapped)
                return mapped

        section = self.workflow_mapping.get("video_generation", {})
        mode_keys = {
            "i2v": ("i2v", "i2v_alternative"),
            "r2v": ("r2v", "r2v_v4"),
            "first_last_frame": ("i2v_first_last_frame",),
        }.get(mode, (mode,))
        for key in mode_keys:
            if section.get(key):
                return section[key]

        patterns = {
            "i2v": [
                "G03-图生视频-Wan2.2SmoothMix",
                "G10-图生视频-Wan2.2SmoothMixV2",
                "G01-图生视频-Wan2.2",
            ],
            "r2v": [
                "P07-动作迁移-Wan2.2AnimateV4",
                "P02-动作迁移-Wan2.2Animate角色迁移",
            ],
            "first_last_frame": ["G02-首尾帧-Wan2.2首尾帧视频"],
        }[mode]
        return self.comfyui_client.find_workflow_by_pattern(patterns)

    def _resolve_image_input(self, value: Optional[str]) -> Optional[str]:
        """Upload a local image; return URL as-is (or download+upload for standard)."""
        if not value:
            return None
        if os.path.isfile(value):
            uploaded = self.comfyui_client.upload_file(value, file_type="input")
            return uploaded.get("filename") if uploaded else None
        if str(value).startswith(("http://", "https://")):
            if self.comfyui_client.protocol == "standard":
                return self._download_and_upload(value)
            return value
        return value

    def _resolve_media_list(self, values: List[str]) -> List[Optional[str]]:
        resolved: List[Optional[str]] = []
        for value in values or []:
            resolved.append(self._resolve_image_input(value))
        return resolved

    def _download_and_upload(self, url: str) -> Optional[str]:
        try:
            response = requests.get(url, timeout=60)
            response.raise_for_status()
            suffix = os.path.splitext(url.split("?")[0])[1] or ".png"
            fd, temp_path = tempfile.mkstemp(suffix=suffix)
            try:
                with os.fdopen(fd, "wb") as fh:
                    fh.write(response.content)
                uploaded = self.comfyui_client.upload_file(temp_path, file_type="input")
                return uploaded.get("filename") if uploaded else None
            finally:
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to download remote media for ComfyUI upload: %s", exc)
            return None

    def _download_first_video(
        self, result: Dict[str, Any], output_path: str
    ) -> Optional[str]:
        results = result.get("results", result.get("files", []))
        for item in results:
            if not isinstance(item, dict):
                continue
            if item.get("type") not in (None, "video"):
                continue
            raw = item.get("raw") or {}
            filename = (
                raw.get("filename")
                or str(item.get("url", "")).split("/")[-1]
            )
            if not filename:
                continue
            if self.comfyui_client.download_file(
                filename,
                output_path,
                file_type=raw.get("type", "output"),
                subfolder=raw.get("subfolder", ""),
            ):
                return output_path
        return None
