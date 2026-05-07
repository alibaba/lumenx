import hashlib
import math
import os
import random
import shutil
import subprocess
import time
import wave
from pathlib import Path
from typing import Any, Dict, List, Optional

from .models import Character, GenerationStatus, StoryboardFrame
from ...audio.tts import TTSProcessor
from ...utils import get_logger
from ...utils.media_refs import output_media_ref, resolve_local_media_path
from ...utils.system_check import get_ffmpeg_path

logger = get_logger(__name__)

SAMPLE_RATE = 16000


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _audio_seed(*parts: Any) -> int:
    payload = "||".join(str(part or "") for part in parts)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return int(digest[:16], 16)


def _normalize_volume(volume: int) -> float:
    return _clamp(volume / 100.0, 0.0, 1.0)


def _samples_to_pcm16(samples: List[float]) -> bytes:
    frames = bytearray()
    for sample in samples:
        frames.extend(int(_clamp(sample, -1.0, 1.0) * 32767).to_bytes(2, "little", signed=True))
    return bytes(frames)


def _write_wav(path: str, samples: List[float], sample_rate: int = SAMPLE_RATE, channels: int = 1) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    pcm = _samples_to_pcm16(samples)
    with wave.open(path, "wb") as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        if channels == 1:
            wav_file.writeframes(pcm)
            return

        stereo = bytearray()
        for index in range(0, len(pcm), 2):
            frame = pcm[index : index + 2]
            stereo.extend(frame)
            stereo.extend(frame)
        wav_file.writeframes(bytes(stereo))


def _envelope(position: int, total: int, attack_ratio: float = 0.04, release_ratio: float = 0.12) -> float:
    if total <= 1:
        return 1.0
    attack = max(1, int(total * attack_ratio))
    release = max(1, int(total * release_ratio))
    if position < attack:
        return position / attack
    if position > total - release:
        return max(0.0, (total - position) / release)
    return 1.0


def _text_contains(text: str, keywords: tuple[str, ...]) -> bool:
    lowered = (text or "").lower()
    return any(keyword.lower() in lowered for keyword in keywords)


def _pick_action_profile(text: str) -> str:
    if _text_contains(text, ("爆炸", "爆裂", "crash", "slam", "bang", "撞", "摔", "砸", "collapse", "impact")):
        return "impact"
    if _text_contains(text, ("风", "whoosh", "奔跑", "跑", "飞", "chase", "移动", "转身", "swoosh", "rush")):
        return "whoosh"
    if _text_contains(text, ("门", "door", "敲", "knock", "click", "tap", "脚步", "step", "walk", "touch")):
        return "texture"
    if _text_contains(text, ("紧张", "恐怖", "horror", "tense", "悬疑", "暗", "night", "夜", "医院", "病房")):
        return "tense"
    return "ambient"


def _pick_bgm_mood(text: str) -> str:
    if _text_contains(text, ("温暖", "希望", "happy", "warm", "family", "sun", "light", "晨", "校园", "童年")):
        return "warm"
    if _text_contains(text, ("紧张", "悬疑", "horror", "tense", "暗", "医院", "追逐", "危机", "冷")):
        return "tense"
    if _text_contains(text, ("动作", "action", "奔跑", "追逐", "冲突", "决战", "鼓点", "accelerate")):
        return "driving"
    return "cinematic"


def _procedural_sfx_samples(profile: str, duration: float, seed: int) -> List[float]:
    rng = random.Random(seed)
    total_samples = max(1, int(SAMPLE_RATE * duration))
    samples: List[float] = []

    base_freq = rng.uniform(120.0, 420.0)
    sweep_freq = rng.uniform(700.0, 1800.0)
    for i in range(total_samples):
        t = i / SAMPLE_RATE
        env = _envelope(i, total_samples, attack_ratio=0.02, release_ratio=0.18)
        noise = (rng.random() * 2.0 - 1.0) * 0.15

        if profile == "impact":
            tone = math.sin(2 * math.pi * (base_freq * (1.0 - min(1.0, t / max(duration, 0.001)) * 0.5)) * t)
            burst = math.sin(2 * math.pi * sweep_freq * t) * math.exp(-t * 3.5)
            sample = (tone * 0.55 + burst * 0.4 + noise * 0.35) * env
        elif profile == "whoosh":
            freq = base_freq + (sweep_freq - base_freq) * (t / max(duration, 0.001))
            sample = (math.sin(2 * math.pi * freq * t) * 0.32 + noise * 0.65) * env
        elif profile == "texture":
            pulse = 1.0 if int(t * 18) % 2 == 0 else 0.0
            sample = (math.sin(2 * math.pi * (base_freq + 420.0 * pulse) * t) * 0.22 + noise * 0.28) * env
        elif profile == "tense":
            low = math.sin(2 * math.pi * 90.0 * t) * 0.25
            mid = math.sin(2 * math.pi * 138.0 * t + 0.4) * 0.12
            tremolo = 0.55 + 0.45 * math.sin(2 * math.pi * 2.5 * t)
            sample = (low + mid + noise * 0.08) * env * tremolo
        else:
            shimmer = math.sin(2 * math.pi * (220.0 + 80.0 * math.sin(2 * math.pi * 0.8 * t)) * t)
            sample = (shimmer * 0.18 + noise * 0.18) * env

        samples.append(_clamp(sample, -1.0, 1.0))

    return samples


def _procedural_bgm_samples(profile: str, duration: float, seed: int) -> List[float]:
    rng = random.Random(seed)
    total_samples = max(1, int(SAMPLE_RATE * duration))

    if profile == "warm":
        freqs = [220.0, 277.18, 329.63]
        volume = 0.11
        pulse_rate = 0.55
    elif profile == "tense":
        freqs = [110.0, 146.83, 220.0]
        volume = 0.10
        pulse_rate = 0.85
    elif profile == "driving":
        freqs = [196.0, 246.94, 293.66]
        volume = 0.12
        pulse_rate = 1.15
    else:
        freqs = [146.83, 196.0, 246.94]
        volume = 0.11
        pulse_rate = 0.75

    phases = [rng.random() * math.tau for _ in freqs]
    samples: List[float] = []
    for i in range(total_samples):
        t = i / SAMPLE_RATE
        env = _envelope(i, total_samples, attack_ratio=0.08, release_ratio=0.18)
        pulse = 0.62 + 0.38 * math.sin(2 * math.pi * pulse_rate * t)
        shimmer = math.sin(2 * math.pi * (0.25 + 0.05 * math.sin(2 * math.pi * 0.1 * t)) * t)
        base = 0.0
        for freq, phase in zip(freqs, phases):
            base += math.sin(2 * math.pi * freq * t + phase)
        base /= max(len(freqs), 1)
        noise = (rng.random() * 2.0 - 1.0) * 0.012
        sample = ((base * 0.75 + shimmer * 0.18 + noise) * volume * pulse) * env
        samples.append(_clamp(sample, -1.0, 1.0))

    return samples


def _append_audio_error(frame: StoryboardFrame, message: str) -> None:
    if frame.audio_error:
        if message not in frame.audio_error:
            frame.audio_error = f"{frame.audio_error}; {message}"
    else:
        frame.audio_error = message


def _get_ffprobe_path() -> Optional[str]:
    ffmpeg_path = get_ffmpeg_path()
    if not ffmpeg_path:
        return shutil.which("ffprobe")

    candidate = Path(ffmpeg_path).with_name("ffprobe.exe" if os.name == "nt" else "ffprobe")
    if candidate.exists():
        return str(candidate)
    return shutil.which("ffprobe")


class AudioGenerator:
    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {}
        self.project_root = Path(self.config.get("project_root") or Path(__file__).resolve().parents[3]).resolve()
        self.output_dir = self.config.get("output_dir", "output/audio")
        os.makedirs(self.output_dir, exist_ok=True)

        try:
            self.tts = TTSProcessor()
            logger.info("TTS Processor initialized successfully")
        except Exception as exc:
            logger.warning("Failed to initialize TTS Processor: %s. Using mock mode.", exc)
            self.tts = None

    def _output_media_ref(self, path: str) -> str:
        try:
            return output_media_ref(path, project_root=str(self.project_root))
        except Exception:
            return str(Path(path).resolve())

    def get_available_voices(self) -> List[Dict[str, str]]:
        """Returns a list of available voices."""
        if self.tts:
            voices_dict = self.tts.list_voices()
            provider_label = getattr(self.tts, "provider_label", "TTS")
            return [
                {
                    "id": key,
                    "name": f"{meta['name']} - {provider_label}",
                    "gender": meta.get("gender", "Unknown"),
                    "model": meta.get("model", getattr(self.tts, "model", "")),
                }
                for key, meta in voices_dict.items()
            ]
        return [
            {"id": "alloy", "name": "Alloy - OpenAI-compatible", "gender": "Neutral", "model": "gpt-4o-mini-tts"},
            {"id": "nova", "name": "Nova - OpenAI-compatible", "gender": "Female", "model": "gpt-4o-mini-tts"},
            {"id": "longanyang", "name": "龙安阳 - DashScope", "gender": "Male", "model": "cosyvoice-v3-flash"},
            {"id": "longanhuan", "name": "龙安欢 - DashScope", "gender": "Female", "model": "cosyvoice-v3-flash"},
        ]

    def _set_success_status(self, frame: StoryboardFrame, previous_status: GenerationStatus) -> None:
        if not frame.audio_error:
            frame.status = GenerationStatus.COMPLETED

    def generate_dialogue(
        self,
        frame: StoryboardFrame,
        character: Character,
        speed: float = 1.0,
        pitch: float = 1.0,
        volume: int = 50,
    ) -> StoryboardFrame:
        """Generates TTS audio for the dialogue."""
        if not frame.dialogue:
            return frame

        previous_status = frame.status
        frame.status = GenerationStatus.PROCESSING

        text = frame.dialogue
        logger.info(
            "Generating dialogue for %s: %s (Speed: %s, Pitch: %s, Volume: %s)",
            character.name,
            text,
            speed,
            pitch,
            volume,
        )

        if not self.tts:
            frame.status = GenerationStatus.FAILED
            _append_audio_error(frame, "TTS service not available. Please check TTS_PROVIDER and related API configuration.")
            logger.warning("TTS not initialized, cannot generate audio for frame %s", frame.id)
            return frame

        if not character.voice_id:
            frame.status = GenerationStatus.FAILED
            _append_audio_error(frame, f"No voice assigned to character '{character.name}'. Please assign a voice first.")
            logger.warning("No voice_id for character %s, cannot generate audio", character.name)
            return frame

        return self._real_generate_dialogue(frame, character, text, speed, pitch, volume, previous_status=previous_status)

    def _real_generate_dialogue(
        self,
        frame: StoryboardFrame,
        character: Character,
        text: str,
        speed: float,
        pitch: float,
        volume: int,
        previous_status: GenerationStatus,
    ) -> StoryboardFrame:
        """Generate dialogue using real TTS."""
        try:
            output_path = os.path.join(self.output_dir, "dialogue", f"{frame.id}.mp3")
            os.makedirs(os.path.dirname(output_path), exist_ok=True)

            voice = character.voice_id
            self.tts.synthesize(text, output_path, voice=voice, speech_rate=speed, pitch_rate=pitch, volume=volume)

            frame.audio_url = self._output_media_ref(output_path)
            self._set_success_status(frame, previous_status)
        except Exception as exc:
            logger.error("TTS generation failed for frame %s: %s", frame.id, exc)
            frame.status = GenerationStatus.FAILED
            _append_audio_error(frame, f"TTS generation failed: {exc}")

        return frame

    def _write_procedural_audio(
        self,
        output_path: str,
        samples: List[float],
        sample_rate: int = SAMPLE_RATE,
    ) -> None:
        _write_wav(output_path, samples, sample_rate=sample_rate, channels=1)

    def _generate_sfx_wave(self, frame: StoryboardFrame, duration: float, profile_seed: int, profile_text: str) -> List[float]:
        profile = _pick_action_profile(profile_text)
        adjusted_duration = _clamp(duration, 0.4, 4.0)
        if profile == "impact":
            adjusted_duration = min(adjusted_duration, 2.2)
        elif profile == "whoosh":
            adjusted_duration = max(1.0, adjusted_duration)
        elif profile == "texture":
            adjusted_duration = max(0.45, min(adjusted_duration, 1.8))

        return _procedural_sfx_samples(profile, adjusted_duration, profile_seed)

    def generate_sfx(self, frame: StoryboardFrame, duration: Optional[float] = None) -> StoryboardFrame:
        """Generates a real procedural sound effect for the frame."""
        previous_status = frame.status
        frame.status = GenerationStatus.PROCESSING

        try:
            profile_text = " ".join(
                value for value in [frame.action_description or "", frame.character_acting or "", frame.key_action_physics or ""] if value
            )
            sfx_duration = duration if duration is not None else max(1.0, min(3.0, 0.75 + len(profile_text) / 28.0))
            seed = _audio_seed(frame.id, profile_text, "sfx")
            samples = self._generate_sfx_wave(frame, sfx_duration, seed, profile_text)

            output_path = os.path.join(self.output_dir, "sfx", f"{frame.id}.wav")
            self._write_procedural_audio(output_path, samples)

            frame.sfx_url = self._output_media_ref(output_path)
            self._set_success_status(frame, previous_status)
            logger.info("Generated procedural SFX for frame %s -> %s", frame.id, output_path)
        except Exception as exc:
            logger.error("Failed to generate SFX for frame %s: %s", frame.id, exc)
            frame.status = GenerationStatus.FAILED
            _append_audio_error(frame, f"SFX generation failed: {exc}")

        return frame

    def _extract_video_audio(self, video_path: str, output_path: str) -> bool:
        ffmpeg_path = get_ffmpeg_path()
        if not ffmpeg_path:
            return False

        ffprobe_path = _get_ffprobe_path()
        has_audio = True
        if ffprobe_path:
            try:
                probe = subprocess.run(
                    [
                        ffprobe_path,
                        "-v",
                        "error",
                        "-select_streams",
                        "a:0",
                        "-show_entries",
                        "stream=index",
                        "-of",
                        "csv=p=0",
                        video_path,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=20,
                    cwd=os.path.dirname(video_path) or None,
                )
                has_audio = bool(probe.stdout.strip())
            except Exception as exc:
                logger.warning("ffprobe audio detection failed for %s: %s", video_path, exc)
                has_audio = True

        if not has_audio:
            return False

        try:
            subprocess.run(
                [
                    ffmpeg_path,
                    "-y",
                    "-i",
                    video_path,
                    "-vn",
                    "-ac",
                    "1",
                    "-ar",
                    str(SAMPLE_RATE),
                    output_path,
                ],
                capture_output=True,
                text=True,
                timeout=120,
            )
            return os.path.exists(output_path) and os.path.getsize(output_path) > 0
        except Exception as exc:
            logger.warning("Failed to extract audio from video %s: %s", video_path, exc)
            return False

    def generate_sfx_from_video(self, frame: StoryboardFrame) -> StoryboardFrame:
        """Generates SFX based on video content (Video-to-Audio)."""
        if not frame.video_url:
            return frame

        previous_status = frame.status
        frame.status = GenerationStatus.PROCESSING
        logger.info("Generating SFX from video for frame %s", frame.id)

        local_video_path = resolve_local_media_path(frame.video_url, project_root=str(self.project_root))
        if not local_video_path:
            candidate = Path(str(frame.video_url))
            if candidate.exists():
                local_video_path = str(candidate.resolve())
        output_path = os.path.join(self.output_dir, "sfx", f"{frame.id}_v2a.wav")
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        try:
            if local_video_path and self._extract_video_audio(local_video_path, output_path):
                frame.sfx_url = self._output_media_ref(output_path)
                self._set_success_status(frame, previous_status)
                return frame

            fallback_text = frame.action_description or frame.dialogue or frame.speaker or frame.id
            seed = _audio_seed(frame.id, fallback_text, "video_sfx")
            samples = self._generate_sfx_wave(frame, 1.6, seed, fallback_text)
            self._write_procedural_audio(output_path, samples)
            frame.sfx_url = self._output_media_ref(output_path)
            self._set_success_status(frame, previous_status)
            return frame
        except Exception as exc:
            logger.error("Failed to generate SFX from video for frame %s: %s", frame.id, exc)
            frame.status = GenerationStatus.FAILED
            _append_audio_error(frame, f"Video-to-audio SFX generation failed: {exc}")

        return frame

    def generate_bgm(
        self,
        frame: StoryboardFrame,
        duration: Optional[float] = None,
        context: Optional[str] = None,
    ) -> StoryboardFrame:
        """Generates BGM based on frame context."""
        previous_status = frame.status
        frame.status = GenerationStatus.PROCESSING

        try:
            context_text = context or " ".join(
                value
                for value in [frame.action_description or "", frame.dialogue or "", frame.visual_atmosphere or ""]
                if value
            )
            bgm_duration = duration if duration is not None else max(5.0, min(12.0, 3.5 + len(context_text) / 24.0))
            mood = _pick_bgm_mood(context_text)
            seed = _audio_seed(frame.id, context_text, "bgm")
            samples = _procedural_bgm_samples(mood, bgm_duration, seed)

            output_path = os.path.join(self.output_dir, "bgm", f"{frame.id}.wav")
            self._write_procedural_audio(output_path, samples)

            frame.bgm_url = self._output_media_ref(output_path)
            self._set_success_status(frame, previous_status)
            logger.info("Generated procedural BGM for frame %s -> %s", frame.id, output_path)
        except Exception as exc:
            logger.error("Failed to generate BGM for frame %s: %s", frame.id, exc)
            frame.status = GenerationStatus.FAILED
            _append_audio_error(frame, f"BGM generation failed: {exc}")

        return frame
