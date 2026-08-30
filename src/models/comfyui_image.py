"""ComfyUI image generation model.

Drop-in replacement for :class:`WanxImageModel` (same ``generate`` contract:
``(prompt, output_path, ref_image_path=None, ref_image_paths=None,
model_name=None, **kwargs) -> (output_path, api_duration)``).  All image
generation is routed to a local ComfyUI / ZEALMAN panel workflow.
"""
from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Optional

from ..utils import get_logger
from .comfyui_client import ComfyUIClient, load_workflow_mapping

logger = get_logger(__name__)


def _normalize_model_name(model_name: Optional[str]) -> tuple[str, str]:
    """Return ``(base_id, mode)`` for canonical or legacy ComfyUI model ids.

    ``comfyui/comfyui-wan2.2-video#i2v`` -> ``("wan2.2-video", "i2v")``
    ``comfyui-wan2.2-t2i``              -> ``("wan2.2-t2i", "")``
    """
    if not model_name:
        return "", ""
    name = str(model_name).strip()
    mode = ""
    if "#" in name:
        name, _, mode = name.partition("#")
    for prefix in ("comfyui/", "comfyui-"):
        if name.startswith(prefix):
            name = name[len(prefix):]
            break
    return name, mode.lower()


class ComfyUIImageModel:
    """ComfyUI workflow-driven image generation (T2I / I2I / enhance)."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.comfyui_client = ComfyUIClient(
            base_url=self.config.get("base_url"),
            protocol=self.config.get("protocol"),
        )
        self.workflow_mapping = self.config.get("workflow_mapping") or load_workflow_mapping()
        self.output_dir = self.config.get("output_dir", "output/assets")

    # ------------------------------------------------------------------
    # Primary interface (WanxImageModel-compatible)
    # ------------------------------------------------------------------

    def generate(
        self,
        prompt: str,
        output_path: str,
        ref_image_path: Optional[str] = None,
        ref_image_paths: Optional[List[str]] = None,
        model_name: Optional[str] = None,
        **kwargs,
    ) -> tuple[str, float]:
        """Generate an image and save it to ``output_path``.

        Returns:
            ``(output_path, api_duration_seconds)``
        """
        start = time.time()
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

        all_ref_paths: List[str] = []
        if ref_image_path:
            all_ref_paths.append(ref_image_path)
        if ref_image_paths:
            all_ref_paths.extend(ref_image_paths)
        all_ref_paths = list(dict.fromkeys(all_ref_paths))

        has_reference = bool(all_ref_paths)
        asset_type = kwargs.get("asset_type") or self._guess_asset_type(model_name)
        workflow_id = self._resolve_workflow(
            model_name=model_name,
            mode="i2i" if has_reference else "t2i",
            asset_type=asset_type,
            **kwargs,
        )
        if not workflow_id:
            raise RuntimeError(
                "No suitable ComfyUI workflow available for image generation "
                "(check COMFYUI_BASE_URL and config/workflow_mapping.json)"
            )
        logger.info("Using ComfyUI image workflow: %s", workflow_id)

        handled_kwargs = {
            key: value
            for key, value in kwargs.items()
            if key not in ("negative_prompt", "size", "seed", "n", "batch_size", "ref_strength")
        }
        parameters = self._build_parameters(
            prompt=prompt,
            negative_prompt=kwargs.get("negative_prompt", ""),
            size=kwargs.get("size"),
            seed=kwargs.get("seed"),
            batch_size=kwargs.get("n", kwargs.get("batch_size", 1)),
            ref_strength=kwargs.get("ref_strength", 0.5),
            **handled_kwargs,
        )

        upload_files: Dict[str, str] = {}
        if has_reference:
            upload_files = {
                f"reference_image_{idx}": path
                for idx, path in enumerate(all_ref_paths)
                if os.path.isfile(path)
            }

        task_id = self.comfyui_client.submit_workflow_task(
            workflow_id=workflow_id,
            parameters=parameters,
            upload_files=upload_files or None,
        )
        if not task_id:
            raise RuntimeError("Failed to submit image generation task to ComfyUI")

        result = self.comfyui_client.wait_for_task_completion(
            task_id=task_id,
            timeout=int(self.config.get("timeout", 600)),
        )
        if not result or result.get("status") != "completed":
            raise RuntimeError(
                "ComfyUI image generation failed: %s"
                % (result.get("error") if result else "task failed or timed out")
            )

        image_path = self._download_first_image(result, output_path)
        if not image_path:
            raise RuntimeError("ComfyUI returned no generated images")
        logger.info("ComfyUI image generated: %s", image_path)
        return image_path, time.time() - start

    # ------------------------------------------------------------------
    # Convenience helpers (kept for API compatibility with the fork)
    # ------------------------------------------------------------------

    def generate_image(
        self,
        prompt: str,
        negative_prompt: str = "",
        output_path: Optional[str] = None,
        workflow_id: Optional[str] = None,
        seed: Optional[int] = None,
        batch_size: int = 1,
        **kwargs,
    ) -> List[str]:
        """Generate one or more images and return their file paths."""
        if not output_path:
            os.makedirs(self.output_dir, exist_ok=True)
            output_path = os.path.join(self.output_dir, f"comfyui_{int(time.time())}.png")
        image_path, _ = self.generate(
            prompt=prompt,
            output_path=output_path,
            negative_prompt=negative_prompt,
            seed=seed,
            batch_size=batch_size,
            workflow_id=workflow_id,
            **kwargs,
        )
        return [image_path]

    def generate_character_asset(
        self,
        character_prompt: str,
        reference_image: Optional[str] = None,
        output_dir: Optional[str] = None,
        **kwargs,
    ) -> Dict[str, str]:
        assets: Dict[str, str] = {}
        out_dir = output_dir or os.path.join(self.output_dir, "characters")
        fullbody_path = self.generate_image(
            prompt=f"{character_prompt}, full body shot, no background, character design",
            output_path=os.path.join(out_dir, "character_fullbody.png"),
            reference_image=reference_image,
            **kwargs,
        )
        if fullbody_path:
            assets["fullbody"] = fullbody_path[0]
        return assets

    def generate_scene_asset(
        self,
        scene_prompt: str,
        output_dir: Optional[str] = None,
        **kwargs,
    ) -> Dict[str, str]:
        out_dir = output_dir or os.path.join(self.output_dir, "scenes")
        paths = self.generate_image(
            prompt=scene_prompt,
            output_path=os.path.join(out_dir, "scene.png"),
            **kwargs,
        )
        return {"scene": paths[0]} if paths else {}

    def enhance_image(
        self,
        input_image: str,
        output_path: Optional[str] = None,
        **kwargs,
    ) -> Optional[str]:
        """Upscale / enhance an existing image via the enhance workflow."""
        workflow_id = self._resolve_workflow(
            model_name=kwargs.get("model_name"),
            mode="enhance",
            asset_type="enhance",
            **kwargs,
        )
        if not workflow_id:
            logger.error("No enhance workflow configured")
            return None
        uploaded = self.comfyui_client.upload_file(input_image)
        if not uploaded:
            return None
        parameters = {"input_image": uploaded.get("filename"), **kwargs}
        task_id = self.comfyui_client.submit_workflow_task(
            workflow_id=workflow_id, parameters=parameters
        )
        if not task_id:
            return None
        result = self.comfyui_client.wait_for_task_completion(task_id)
        if not result or result.get("status") != "completed":
            return None
        return self._download_first_image(result, output_path or os.path.join(self.output_dir, "enhanced.png"))

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _resolve_workflow(
        self,
        model_name: Optional[str],
        mode: str,
        asset_type: str,
        **kwargs,
    ) -> Optional[str]:
        explicit = kwargs.get("workflow_id")
        if explicit:
            return explicit

        base_id, mode_from_id = _normalize_model_name(model_name)
        mode = mode_from_id or mode

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

        section = self.workflow_mapping.get("asset_generation", {})
        if asset_type in section and section[asset_type]:
            return section[asset_type]
        storyboard_section = self.workflow_mapping.get("storyboard", {})
        story_key = "fast" if kwargs.get("fast") else "default"
        if asset_type == "storyboard" and story_key in story_section and story_section[story_key]:
            return story_section[story_key]
        if mode == "enhance" and self.workflow_mapping.get("asset_generation", {}).get("enhance"):
            return self.workflow_mapping["asset_generation"]["enhance"]

        patterns = self._default_patterns(mode, asset_type)
        return self.comfyui_client.find_workflow_by_pattern(patterns)

    def _build_parameters(
        self,
        prompt: str,
        negative_prompt: str = "",
        size: Optional[str] = None,
        seed: Optional[int] = None,
        batch_size: int = 1,
        ref_strength: float = 0.5,
        **kwargs,
    ) -> Dict[str, Any]:
        node_map = self.workflow_mapping.get("node_mapping", {})
        positive_key = node_map.get("positive_prompt", "49:text")
        negative_key = node_map.get("negative_prompt", "16:text")
        parameters: Dict[str, Any] = {
            positive_key: prompt,
            negative_key: negative_prompt or "",
            "batch_size": batch_size,
        }
        effective_size = size or kwargs.get("size") or "1280*1280"
        parameters["size"] = effective_size
        if seed is not None:
            parameters["seed"] = seed
        if ref_strength is not None:
            parameters["reference_strength"] = ref_strength
        for key, value in kwargs.items():
            if key in {
                "output_path", "model_name", "workflow_id", "asset_type",
                "fast", "ref_image_path", "ref_image_paths", "reference_image",
            }:
                continue
            if value is None:
                continue
            try:
                json.dumps(value)
                parameters[key] = value
            except (TypeError, ValueError):
                logger.debug("Skipping non-JSON ComfyUI parameter %s", key)
        return parameters

    def _download_first_image(
        self, result: Dict[str, Any], output_path: str
    ) -> Optional[str]:
        results = result.get("results", result.get("files", []))
        for item in results:
            if not isinstance(item, dict):
                continue
            if item.get("type") not in (None, "image"):
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

    @staticmethod
    def _default_patterns(mode: str, asset_type: str) -> List[str]:
        if mode == "enhance" or asset_type == "enhance":
            return ["D20-RAW画质重建", "A01-文生图-Qwen2512"]
        if asset_type in ("character", "character_multiangle"):
            return ["B13-千问角色一键多角度", "C16-短剧文生图专用", "C07-文生图"]
        if mode == "i2i":
            return ["B13-千问角色一键多角度", "D20-RAW画质重建", "C16-短剧文生图专用"]
        return ["C16-短剧文生图专用", "C07-文生图", "A01-文生图"]

    @staticmethod
    def _guess_asset_type(model_name: Optional[str]) -> str:
        base_id, _ = _normalize_model_name(model_name)
        if "character" in base_id or "角色" in base_id:
            return "character"
        if "scene" in base_id or "场景" in base_id:
            return "scene"
        if "prop" in base_id or "道具" in base_id:
            return "prop"
        return "image"
