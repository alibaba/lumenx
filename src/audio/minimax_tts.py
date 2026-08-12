"""MiniMax text-to-audio HTTP client."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import requests

MINIMAX_TTS_ENDPOINTS = {
    "global": "https://api.minimax.io/v1/t2a_v2",
    "cn": "https://api.minimaxi.com/v1/t2a_v2",
}

MINIMAX_TTS_MODELS = (
    "speech-2.8-hd",
    "speech-2.8-turbo",
    "speech-2.6-hd",
    "speech-2.6-turbo",
    "speech-02-hd",
    "speech-02-turbo",
    "speech-01-hd",
    "speech-01-turbo",
)

MINIMAX_AUDIO_FORMATS = ("mp3", "wav", "flac", "pcm")
MINIMAX_OUTPUT_FORMATS = ("hex", "url")


class MiniMaxTTSClient:
    """Generate speech with the MiniMax non-streaming HTTP API."""

    def __init__(
        self,
        api_key: str | None = None,
        region: str = "global",
        timeout: float = 60.0,
    ) -> None:
        self.api_key = api_key or os.getenv("MINIMAX_API_KEY")
        if not self.api_key:
            raise ValueError("MINIMAX_API_KEY is required for speech synthesis")

        normalized_region = region.strip().lower()
        if normalized_region not in MINIMAX_TTS_ENDPOINTS:
            choices = ", ".join(MINIMAX_TTS_ENDPOINTS)
            raise ValueError(f"Unsupported MiniMax region '{region}'; choose {choices}")

        self.endpoint = MINIMAX_TTS_ENDPOINTS[normalized_region]
        self.timeout = timeout

    def synthesize(
        self,
        text: str,
        output_path: str,
        *,
        model: str = MINIMAX_TTS_MODELS[0],
        stream: bool = False,
        language_boost: str | None = None,
        output_format: str = "hex",
        voice_setting: Mapping[str, Any] | None = None,
        pronunciation_dict: Mapping[str, Any] | None = None,
        audio_setting: Mapping[str, Any] | None = None,
        voice_modify: Mapping[str, Any] | None = None,
        subtitle_enable: bool | None = None,
    ) -> tuple[str, float, str]:
        """Synthesize ``text`` and save the completed audio response."""
        if not text:
            raise ValueError("Text is required for MiniMax speech synthesis")
        if model not in MINIMAX_TTS_MODELS:
            raise ValueError(f"Unsupported MiniMax speech model '{model}'")
        if stream:
            raise ValueError("Streaming MiniMax speech responses cannot be saved directly")
        if output_format not in MINIMAX_OUTPUT_FORMATS:
            raise ValueError(f"Unsupported MiniMax output format '{output_format}'")

        normalized_audio_setting = dict(audio_setting or {})
        audio_format = normalized_audio_setting.get("format", "mp3")
        if audio_format not in MINIMAX_AUDIO_FORMATS:
            raise ValueError(f"Unsupported MiniMax audio format '{audio_format}'")
        normalized_audio_setting.setdefault("format", audio_format)

        payload: dict[str, Any] = {
            "model": model,
            "text": text,
            "stream": False,
            "output_format": output_format,
            "audio_setting": normalized_audio_setting,
        }
        optional_fields = {
            "language_boost": language_boost,
            "voice_setting": voice_setting,
            "pronunciation_dict": pronunciation_dict,
            "voice_modify": voice_modify,
            "subtitle_enable": subtitle_enable,
        }
        payload.update(
            {name: value for name, value in optional_fields.items() if value is not None}
        )

        response = requests.post(
            self.endpoint,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=self.timeout,
        )
        response.raise_for_status()
        result = response.json()

        base_response = result.get("base_resp") or {}
        if base_response.get("status_code") != 0:
            message = base_response.get("status_msg") or "unknown API error"
            raise RuntimeError(f"MiniMax speech synthesis failed: {message}")

        data = result.get("data") or {}
        if data.get("status") != 2:
            raise RuntimeError("MiniMax speech synthesis did not complete")
        audio_value = data.get("audio")
        if not audio_value:
            raise RuntimeError("MiniMax speech response did not include audio")

        if output_format == "hex":
            try:
                audio_bytes = bytes.fromhex(audio_value)
            except ValueError as exc:
                raise RuntimeError("MiniMax speech response contained invalid hex audio") from exc
        else:
            audio_response = requests.get(audio_value, timeout=self.timeout)
            audio_response.raise_for_status()
            audio_bytes = audio_response.content

        destination = Path(output_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(audio_bytes)
        return str(destination), 0.0, str(result.get("trace_id") or "")
