"""
Text-to-Speech (TTS) module with pluggable providers.

Supports:
- DashScope CosyVoice (legacy compatibility)
- OpenAI-compatible `/audio/speech` providers
"""
import logging
import os
import time
from abc import ABC, abstractmethod
from typing import Dict, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

TTS_PROVIDER_OPENAI = "openai"
TTS_PROVIDER_DASHSCOPE = "dashscope"

DEFAULT_DASHSCOPE_TTS_MODEL = "cosyvoice-v3-flash"
DEFAULT_DASHSCOPE_TTS_VOICE = "longanyang"

DEFAULT_OPENAI_TTS_MODEL = "qwen3-tts-flash"
DEFAULT_OPENAI_TTS_VOICE = "alloy"


# Voice registry: key -> {model_id, name, gender, model}
# model_id must match the model version (v2 voices for cosyvoice-v2, v3 for cosyvoice-v3-*)
# Reference: https://help.aliyun.com/zh/model-studio/cosyvoice-voice-list
DASHSCOPE_VOICES = {
    # === cosyvoice-v2 voices ===
    "longxiaochun": {"model_id": "longxiaochun_v2", "name": "龙小淳 (知性女)", "gender": "Female", "model": "cosyvoice-v2"},
    "longxiaoxia": {"model_id": "longxiaoxia_v2", "name": "龙小夏 (沉稳女)", "gender": "Female", "model": "cosyvoice-v2"},
    "longyue": {"model_id": "longyue_v2", "name": "龙悦 (温柔女)", "gender": "Female", "model": "cosyvoice-v2"},
    "longmiao": {"model_id": "longmiao_v2", "name": "龙淼 (有声书女)", "gender": "Female", "model": "cosyvoice-v2"},
    "longyuan": {"model_id": "longyuan_v2", "name": "龙媛 (治愈女)", "gender": "Female", "model": "cosyvoice-v2"},
    "longhua": {"model_id": "longhua_v2", "name": "龙华 (活力甜美女)", "gender": "Female", "model": "cosyvoice-v2"},
    "longwan": {"model_id": "longwan_v2", "name": "龙婉 (知性女)", "gender": "Female", "model": "cosyvoice-v2"},
    "longxing": {"model_id": "longxing_v2", "name": "龙星 (邻家女孩)", "gender": "Female", "model": "cosyvoice-v2"},
    "longfeifei": {"model_id": "longfeifei_v2", "name": "龙菲菲 (甜美女)", "gender": "Female", "model": "cosyvoice-v2"},
    "longyan": {"model_id": "longyan_v2", "name": "龙言 (温柔女)", "gender": "Female", "model": "cosyvoice-v2"},
    "longqiang": {"model_id": "longqiang_v2", "name": "龙蔷 (浪漫女)", "gender": "Female", "model": "cosyvoice-v2"},
    "longxiu": {"model_id": "longxiu_v2", "name": "龙修 (博学男)", "gender": "Male", "model": "cosyvoice-v2"},
    "longnan": {"model_id": "longnan_v2", "name": "龙楠 (睿智少年)", "gender": "Male", "model": "cosyvoice-v2"},
    "longcheng": {"model_id": "longcheng_v2", "name": "龙诚 (睿智青年)", "gender": "Male", "model": "cosyvoice-v2"},
    "longze": {"model_id": "longze_v2", "name": "龙泽 (阳光男)", "gender": "Male", "model": "cosyvoice-v2"},
    "longzhe": {"model_id": "longzhe_v2", "name": "龙哲 (暖心男)", "gender": "Male", "model": "cosyvoice-v2"},
    "longtian": {"model_id": "longtian_v2", "name": "龙天 (磁性男)", "gender": "Male", "model": "cosyvoice-v2"},
    "longhan": {"model_id": "longhan_v2", "name": "龙翰 (深情男)", "gender": "Male", "model": "cosyvoice-v2"},
    "longhao": {"model_id": "longhao_v2", "name": "龙浩 (忧郁男)", "gender": "Male", "model": "cosyvoice-v2"},
    "longshu": {"model_id": "longshu_v2", "name": "龙书 (播报男)", "gender": "Male", "model": "cosyvoice-v2"},
    "longshuo": {"model_id": "longshuo_v2", "name": "龙朔 (博学男)", "gender": "Male", "model": "cosyvoice-v2"},
    "longfei": {"model_id": "longfei_v2", "name": "龙飞 (磁性朗诵男)", "gender": "Male", "model": "cosyvoice-v2"},
    "longxiaocheng": {"model_id": "longxiaocheng_v2", "name": "龙小诚 (低音男)", "gender": "Male", "model": "cosyvoice-v2"},
    "longshao": {"model_id": "longshao_v2", "name": "龙少 (阳光男)", "gender": "Male", "model": "cosyvoice-v2"},
    "longjielidou": {"model_id": "longjielidou_v2", "name": "龙杰力豆 (童声男)", "gender": "Male", "model": "cosyvoice-v2"},
    "longhuhu": {"model_id": "longhuhu", "name": "龙虎虎 (童声女)", "gender": "Female", "model": "cosyvoice-v2"},
    "loongstella": {"model_id": "loongstella_v2", "name": "Stella (English Female)", "gender": "Female", "model": "cosyvoice-v2"},
    "loongbella": {"model_id": "loongbella_v2", "name": "Bella (English Female)", "gender": "Female", "model": "cosyvoice-v2"},
    # === cosyvoice-v3 voices (require cosyvoice-v3-flash or cosyvoice-v3-plus) ===
    "longanyang": {"model_id": "longanyang", "name": "龙安阳 (阳光少年)", "gender": "Male", "model": "cosyvoice-v3-flash"},
    "longanhuan": {"model_id": "longanhuan", "name": "龙安欢 (活力女)", "gender": "Female", "model": "cosyvoice-v3-flash"},
}

# Backwards-compatible export name.
VOICES = DASHSCOPE_VOICES

OPENAI_COMPATIBLE_VOICES = {
    "alloy": {"model_id": "alloy", "name": "Alloy", "gender": "Neutral", "model": DEFAULT_OPENAI_TTS_MODEL},
    "ash": {"model_id": "ash", "name": "Ash", "gender": "Male", "model": DEFAULT_OPENAI_TTS_MODEL},
    "ballad": {"model_id": "ballad", "name": "Ballad", "gender": "Neutral", "model": DEFAULT_OPENAI_TTS_MODEL},
    "coral": {"model_id": "coral", "name": "Coral", "gender": "Female", "model": DEFAULT_OPENAI_TTS_MODEL},
    "echo": {"model_id": "echo", "name": "Echo", "gender": "Male", "model": DEFAULT_OPENAI_TTS_MODEL},
    "fable": {"model_id": "fable", "name": "Fable", "gender": "Male", "model": DEFAULT_OPENAI_TTS_MODEL},
    "nova": {"model_id": "nova", "name": "Nova", "gender": "Female", "model": DEFAULT_OPENAI_TTS_MODEL},
    "onyx": {"model_id": "onyx", "name": "Onyx", "gender": "Male", "model": DEFAULT_OPENAI_TTS_MODEL},
    "sage": {"model_id": "sage", "name": "Sage", "gender": "Neutral", "model": DEFAULT_OPENAI_TTS_MODEL},
    "shimmer": {"model_id": "shimmer", "name": "Shimmer", "gender": "Female", "model": DEFAULT_OPENAI_TTS_MODEL},
}


def _first_non_empty(*values: Optional[str]) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _normalize_openai_base_url(base_url: str) -> str:
    normalized = (base_url or "").strip().rstrip("/")
    for suffix in ("/audio/speech", "/chat/completions", "/responses"):
        if normalized.endswith(suffix):
            normalized = normalized[: -len(suffix)]
            break
    return normalized.rstrip("/")


def _looks_like_audio_model(model_name: Optional[str]) -> str:
    normalized = (model_name or "").strip()
    lowered = normalized.lower()
    if not normalized:
        return ""
    if any(token in lowered for token in ("tts", "speech", "audio")):
        return normalized
    return ""


def _resolve_tts_provider(explicit_provider: Optional[str] = None) -> str:
    for candidate in (explicit_provider, os.getenv("TTS_PROVIDER")):
        normalized = (candidate or "").strip().lower()
        if normalized in {TTS_PROVIDER_OPENAI, TTS_PROVIDER_DASHSCOPE}:
            return normalized

    if any(os.getenv(key) for key in ("OPENAI_TTS_API_KEY", "OPENAI_TTS_BASE_URL", "OPENAI_TTS_MODEL")):
        return TTS_PROVIDER_OPENAI
    if os.getenv("OPENAI_API_KEY") and os.getenv("OPENAI_BASE_URL"):
        return TTS_PROVIDER_OPENAI
    if os.getenv("DASHSCOPE_API_KEY"):
        return TTS_PROVIDER_DASHSCOPE
    return TTS_PROVIDER_OPENAI


class BaseTTSProvider(ABC):
    provider_name = ""
    provider_label = ""
    voices: Dict[str, Dict[str, str]] = {}

    def __init__(self, model: str, voice: str):
        self.model = model
        self.voice = voice

    @abstractmethod
    def synthesize(
        self,
        text: str,
        output_path: str,
        voice: Optional[str] = None,
        speech_rate: float = 1.0,
        pitch_rate: float = 1.0,
        volume: int = 50,
    ) -> Tuple[str, float, str]:
        raise NotImplementedError

    def list_voices(self) -> Dict[str, Dict[str, str]]:
        return self.voices


class DashScopeTTSProvider(BaseTTSProvider):
    provider_name = TTS_PROVIDER_DASHSCOPE
    provider_label = "DashScope"
    voices = DASHSCOPE_VOICES

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = DEFAULT_DASHSCOPE_TTS_MODEL,
        voice: str = DEFAULT_DASHSCOPE_TTS_VOICE,
    ):
        try:
            import dashscope
        except ImportError as exc:  # pragma: no cover - depends on optional local environment
            raise RuntimeError(
                "dashscope package not installed. DashScope TTS provider is unavailable."
            ) from exc

        self._dashscope = dashscope
        self.api_key = _first_non_empty(api_key, os.getenv("DASHSCOPE_API_KEY"))
        if self.api_key:
            self._dashscope.api_key = self.api_key
        else:
            logger.warning("DashScope TTS provider enabled, but DASHSCOPE_API_KEY is missing.")

        super().__init__(model=model or DEFAULT_DASHSCOPE_TTS_MODEL, voice=voice or DEFAULT_DASHSCOPE_TTS_VOICE)

    def synthesize(
        self,
        text: str,
        output_path: str,
        voice: Optional[str] = None,
        speech_rate: float = 1.0,
        pitch_rate: float = 1.0,
        volume: int = 50,
    ) -> Tuple[str, float, str]:
        if not self.api_key:
            raise ValueError("DashScope TTS provider requires DASHSCOPE_API_KEY.")

        from dashscope.audio.tts_v2 import SpeechSynthesizer

        start_time = time.time()
        voice = voice or self.voice
        model = self._resolve_model_for_voice(voice)

        logger.info(
            "Synthesizing speech via DashScope with model=%s, voice=%s (rate=%s, pitch=%s, volume=%s)...",
            model,
            voice,
            speech_rate,
            pitch_rate,
            volume,
        )

        speech_rate = max(0.5, min(2.0, speech_rate))
        pitch_rate = max(0.5, min(2.0, pitch_rate))
        volume = max(0, min(100, volume))

        synthesizer = SpeechSynthesizer(
            model=model,
            voice=voice,
            speech_rate=speech_rate,
            pitch_rate=pitch_rate,
            volume=volume,
        )
        audio_data = synthesizer.call(text)

        request_id = synthesizer.get_last_request_id()
        first_package_delay = synthesizer.get_first_package_delay()

        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "wb") as output_file:
            output_file.write(audio_data)

        duration = time.time() - start_time
        logger.info(
            "DashScope TTS synthesized: request_id=%s, delay=%sms, total=%.2fs -> %s",
            request_id,
            first_package_delay,
            duration,
            output_path,
        )
        return output_path, first_package_delay, request_id

    def _resolve_model_for_voice(self, voice_id: str) -> str:
        for meta in self.voices.values():
            if meta["model_id"] == voice_id:
                return meta.get("model", self.model)
        return self.model


class OpenAICompatibleTTSProvider(BaseTTSProvider):
    provider_name = TTS_PROVIDER_OPENAI
    provider_label = "OpenAI-compatible"
    voices = OPENAI_COMPATIBLE_VOICES

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        voice: str = DEFAULT_OPENAI_TTS_VOICE,
    ):
        resolved_model = _first_non_empty(
            model,
            os.getenv("OPENAI_TTS_MODEL"),
            _looks_like_audio_model(os.getenv("OPENAI_MODEL")),
            DEFAULT_OPENAI_TTS_MODEL,
        )
        resolved_voice = voice or DEFAULT_OPENAI_TTS_VOICE
        super().__init__(model=resolved_model, voice=resolved_voice)

        self.api_key = _first_non_empty(
            api_key,
            os.getenv("OPENAI_TTS_API_KEY"),
            os.getenv("OPENAI_API_KEY"),
        )
        self.base_url = _normalize_openai_base_url(
            _first_non_empty(
                base_url,
                os.getenv("OPENAI_TTS_BASE_URL"),
                os.getenv("OPENAI_BASE_URL"),
                "https://api.openai.com/v1",
            )
        )

        if not self.api_key:
            logger.warning(
                "OpenAI-compatible TTS provider enabled, but OPENAI_TTS_API_KEY / OPENAI_API_KEY is missing."
            )

    def synthesize(
        self,
        text: str,
        output_path: str,
        voice: Optional[str] = None,
        speech_rate: float = 1.0,
        pitch_rate: float = 1.0,
        volume: int = 50,
    ) -> Tuple[str, float, str]:
        if not self.api_key:
            raise ValueError(
                "OpenAI-compatible TTS provider requires OPENAI_TTS_API_KEY or OPENAI_API_KEY."
            )

        start_time = time.time()
        resolved_voice = voice or self.voice or DEFAULT_OPENAI_TTS_VOICE
        speed = max(0.25, min(4.0, float(speech_rate or 1.0)))
        response_format = self._resolve_response_format(output_path)

        payload = {
            "model": self.model,
            "voice": resolved_voice,
            "input": text,
            "response_format": response_format,
            "speed": speed,
        }

        if pitch_rate != 1.0 or volume != 50:
            logger.info(
                "OpenAI-compatible TTS does not expose standard pitch/volume controls; ignoring pitch_rate=%s, volume=%s",
                pitch_rate,
                volume,
            )

        request_url = f"{self.base_url}/audio/speech"
        logger.info(
            "Synthesizing speech via OpenAI-compatible TTS with model=%s, voice=%s, format=%s...",
            self.model,
            resolved_voice,
            response_format,
        )

        response = requests.post(
            request_url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=300,
        )

        if response.status_code != 200:
            try:
                error_data = response.json()
            except Exception:
                error_data = response.text
            raise RuntimeError(f"OpenAI-compatible TTS failed: {error_data}")

        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "wb") as output_file:
            output_file.write(response.content)

        duration_ms = (time.time() - start_time) * 1000
        request_id = self._extract_request_id(response.headers)
        logger.info(
            "OpenAI-compatible TTS synthesized: request_id=%s, duration=%.2fms -> %s",
            request_id,
            duration_ms,
            output_path,
        )
        return output_path, duration_ms, request_id

    def _resolve_response_format(self, output_path: str) -> str:
        extension = os.path.splitext(output_path)[1].lower().lstrip(".")
        if extension in {"mp3", "wav", "aac", "flac", "opus", "pcm"}:
            return extension
        return "mp3"

    def _extract_request_id(self, headers: Dict[str, str]) -> str:
        for header_key in ("x-request-id", "request-id", "x-openai-request-id"):
            if header_key in headers:
                return headers[header_key]
        return ""


class TTSProcessor:
    """Text-to-Speech processor facade with pluggable providers."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        voice: Optional[str] = None,
        provider: Optional[str] = None,
        base_url: Optional[str] = None,
    ):
        self.provider_name = _resolve_tts_provider(provider)
        if self.provider_name == TTS_PROVIDER_DASHSCOPE:
            self._provider = DashScopeTTSProvider(
                api_key=api_key,
                model=model or DEFAULT_DASHSCOPE_TTS_MODEL,
                voice=voice or DEFAULT_DASHSCOPE_TTS_VOICE,
            )
        else:
            self._provider = OpenAICompatibleTTSProvider(
                api_key=api_key,
                base_url=base_url,
                model=model,
                voice=voice or DEFAULT_OPENAI_TTS_VOICE,
            )

        self.provider_label = self._provider.provider_label
        self.model = self._provider.model
        self.voice = self._provider.voice

        logger.info(
            "TTS Processor initialized with provider=%s, model=%s, voice=%s",
            self.provider_name,
            self.model,
            self.voice,
        )

    def synthesize(
        self,
        text: str,
        output_path: str,
        voice: Optional[str] = None,
        speech_rate: float = 1.0,
        pitch_rate: float = 1.0,
        volume: int = 50,
    ) -> Tuple[str, float, str]:
        return self._provider.synthesize(
            text=text,
            output_path=output_path,
            voice=voice,
            speech_rate=speech_rate,
            pitch_rate=pitch_rate,
            volume=volume,
        )

    def list_voices(self) -> Dict[str, Dict[str, str]]:
        return self._provider.list_voices()
