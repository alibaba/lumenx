"""ComfyUI audio / TTS model.

Routes dialogue synthesis to a local ComfyUI audio workflow (e.g. the
``N2-单人声音克隆FishAudio S2pro`` voice-clone workflow from the
lumenx-comfyui fork).  Enabled with ``COMFYUI_TTS_ENABLED=1``; the existing
CosyVoice path remains the default so nothing breaks for cloud users.
"""
from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Optional

from ..utils import get_logger
from .comfyui_client import ComfyUIClient, load_workflow_mapping

logger = get_logger(__name__)


class ComfyUIAudioModel:
    """ComfyUI workflow-driven TTS with a ``synthesize`` interface compatible
    with :class:`TTSProcessor` for the dialogue generation flow."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.comfyui_client = ComfyUIClient(
            base_url=self.config.get("base_url"),
            protocol=self.config.get("protocol"),
        )
        self.workflow_mapping = self.config.get("workflow_mapping") or load_workflow_mapping()

    def synthesize(
        self,
        text: str,
        output_path: str,
        voice: Optional[str] = None,
        speech_rate: float = 1.0,
        pitch_rate: float = 1.0,
        volume: int = 50,
        instructions: Optional[str] = None,
        **kwargs,
    ) -> str:
        """Synthesize speech and return the output path."""
        start = time.time()
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

        workflow_id = self._resolve_workflow(**kwargs)
        if not workflow_id:
            raise RuntimeError(
                "No ComfyUI audio workflow configured "
                "(check config/workflow_mapping.json -> audio_generation)"
            )

        parameters: Dict[str, Any] = {
            "text": text,
            "voice": voice or "default",
            "speech_rate": speech_rate,
            "pitch_rate": pitch_rate,
            "volume": volume,
        }
        if instructions:
            parameters["instructions"] = instructions
        reference_voice = kwargs.get("reference_voice") or kwargs.get("voice_file")
        upload_files: Optional[Dict[str, str]] = None
        if reference_voice and os.path.isfile(str(reference_voice)):
            upload_files = {"reference_voice": str(reference_voice)}

        task_id = self.comfyui_client.submit_workflow_task(
            workflow_id=workflow_id,
            parameters=parameters,
            upload_files=upload_files,
        )
        if not task_id:
            raise RuntimeError("Failed to submit audio generation task to ComfyUI")

        result = self.comfyui_client.wait_for_task_completion(
            task_id=task_id,
            timeout=int(self.config.get("timeout", 600)),
        )
        if not result or result.get("status") != "completed":
            raise RuntimeError(
                "ComfyUI audio generation failed: %s"
                % (result.get("error") if result else "task failed or timed out")
            )

        if not self._download_first_audio(result, output_path):
            raise RuntimeError("ComfyUI returned no generated audio")
        logger.info("ComfyUI audio generated in %.1fs: %s", time.time() - start, output_path)
        return output_path

    @staticmethod
    def list_voices() -> Dict[str, Dict[str, Any]]:
        """Expose a single generic local voice so the voice picker still works."""
        return {
            "comfyui_local": {
                "model_id": "comfyui_local",
                "name": "ComfyUI 本地音色 (FishAudio 克隆)",
                "gender": "Unknown",
                "model": "comfyui-audio",
                "family": "comfyui",
                "supports_instruction": True,
            }
        }

    def _resolve_workflow(self, **kwargs) -> Optional[str]:
        explicit = kwargs.get("workflow_id")
        if explicit:
            return explicit
        section = self.workflow_mapping.get("audio_generation", {})
        for key in ("voice_clone", "tts", "default"):
            if section.get(key):
                return section[key]
        return self.comfyui_client.find_workflow_by_pattern(
            ["N2-单人声音克隆FishAudio", "FishAudio", "TTS", "voice_clone"]
        )

    def _download_first_audio(
        self, result: Dict[str, Any], output_path: str
    ) -> bool:
        results = result.get("results", result.get("files", []))
        for item in results:
            if not isinstance(item, dict):
                continue
            raw = item.get("raw") or {}
            filename = raw.get("filename") or str(item.get("url", "")).split("/")[-1]
            if not filename:
                continue
            if self.comfyui_client.download_file(
                filename,
                output_path,
                file_type=raw.get("type", "output"),
                subfolder=raw.get("subfolder", ""),
            ):
                return True
        return False
