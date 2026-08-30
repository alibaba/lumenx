import os
import time
import hashlib
from typing import Dict, Any, List, Optional
from .models import StoryboardFrame, Character, GenerationStatus
from ...utils import get_logger
from ...audio.tts import TTSProcessor

logger = get_logger(__name__)


def _compute_dialogue_hash(text: str, voice_id: Optional[str], instructions: Optional[str]) -> str:
    """PR-3j · Snapshot hash for stale detection. Frame is STALE when current
    (dialogue|voice_id|instructions) hash != stored snapshot."""
    payload = f"{text or ''}|{voice_id or ''}|{instructions or ''}"
    return hashlib.md5(payload.encode("utf-8")).hexdigest()


# PR-3k · BGM preset catalog. Each entry maps a stable id → human label,
# mood tag, and a relative path under output/presets/bgm/. v1 ships the
# catalog only; actual audio files are dropped in by the operator (or
# left empty, in which case merge_videos will skip the BGM track).
BGM_PRESETS: List[Dict[str, Any]] = [
    {"id": "calm_warm",      "label": "温暖治愈",   "mood": "warm",      "url": "presets/bgm/calm_warm.mp3"},
    {"id": "uplifting_pop",  "label": "明朗轻快",   "mood": "uplifting", "url": "presets/bgm/uplifting_pop.mp3"},
    {"id": "epic_cinematic", "label": "史诗电影感", "mood": "epic",      "url": "presets/bgm/epic_cinematic.mp3"},
    {"id": "mystery_ambient","label": "悬疑氛围",   "mood": "mystery",   "url": "presets/bgm/mystery_ambient.mp3"},
    {"id": "sad_piano",      "label": "忧伤钢琴",   "mood": "sad",       "url": "presets/bgm/sad_piano.mp3"},
    {"id": "tension_drama",  "label": "紧张戏剧",   "mood": "tense",     "url": "presets/bgm/tension_drama.mp3"},
    {"id": "lofi_chill",     "label": "Lo-Fi 慵懒", "mood": "chill",     "url": "presets/bgm/lofi_chill.mp3"},
    {"id": "fantasy_dreamy", "label": "奇幻梦境",   "mood": "dreamy",    "url": "presets/bgm/fantasy_dreamy.mp3"},
]


def get_bgm_presets() -> List[Dict[str, Any]]:
    """PR-3k · Return BGM preset list. UI displays these in the Mix phase
    picker; selected entry's url is stored on Script.bgm_url."""
    return list(BGM_PRESETS)


def _effective_dialogue_text(frame: StoryboardFrame) -> str:
    """Prefer dialogue_structured.line, fall back to legacy frame.dialogue."""
    if frame.dialogue_structured and frame.dialogue_structured.line:
        return frame.dialogue_structured.line
    return frame.dialogue or ""


def _effective_instructions(frame: StoryboardFrame) -> Optional[str]:
    """Prefer explicit dialogue_instructions; lazy-build from dialogue_structured if missing."""
    if frame.dialogue_instructions:
        return frame.dialogue_instructions
    if frame.dialogue_structured:
        parts = []
        if frame.dialogue_structured.emotion:
            parts.append(f"情绪：{frame.dialogue_structured.emotion}")
        if frame.dialogue_structured.delivery:
            parts.append(f"演绎：{frame.dialogue_structured.delivery}")
        if parts:
            return "；".join(parts)
    return None


def dialogue_audio_is_stale(frame: StoryboardFrame, character: Optional[Character]) -> bool:
    """True when frame.audio_url exists but its snapshot no longer matches
    the current (dialogue|voice|instructions) state."""
    if not frame.audio_url:
        return False
    if not frame.dialogue_text_hash:
        return True  # legacy frame without snapshot — treat as stale
    voice_id = character.voice_id if character else frame.dialogue_voice_id
    text = _effective_dialogue_text(frame)
    instructions = _effective_instructions(frame)
    current = _compute_dialogue_hash(text, voice_id, instructions)
    return current != frame.dialogue_text_hash

class AudioGenerator:
    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {}
        self.output_dir = self.config.get('output_dir', 'output/audio')
        
        # COMFYUI_TTS_ENABLED=1 → route dialogue synthesis to a local
        # ComfyUI audio workflow (FishAudio 等), otherwise keep CosyVoice.
        comfyui_tts_enabled = os.getenv("COMFYUI_TTS_ENABLED", "").strip().lower() in (
            "1", "true", "yes", "on"
        )
        if comfyui_tts_enabled:
            try:
                from ...models.comfyui_audio import ComfyUIAudioModel
                self.tts = ComfyUIAudioModel()
                logger.info("TTS initialized: ComfyUI audio model (local)")
            except Exception as e:
                logger.warning(f"Failed to initialize ComfyUI TTS: {e}. Using mock mode.")
                self.tts = None
        else:
            # Initialize TTS Processor
            try:
                self.tts = TTSProcessor()
                logger.info("TTS Processor initialized successfully")
            except Exception as e:
                logger.warning(f"Failed to initialize TTS Processor: {e}. Using mock mode.")
                self.tts = None

    def get_available_voices(self) -> List[Dict[str, Any]]:
        """Returns a list of available voices with full registry metadata.

        Shape expanded in PR-3g #3 — frontend voice picker (Q15.5 B) needs
        family/dialect/lang_primary/supports_instruction to render the
        3 tabs (系统音色 / 我的复刻 / 我的设计) with dialect/international
        sub-groupings inside 系统音色.
        """
        if self.tts:
            voices_dict = self.tts.list_voices()
            return [
                {
                    # Use actual model_id (server sends "Cherry" not "qwen3_cherry")
                    # so frontend can pass it back unchanged for synthesis.
                    "id": meta['model_id'],
                    "name": meta['name'],
                    "gender": meta.get('gender', 'Unknown'),
                    "model": meta.get('model', 'cosyvoice-v2'),
                    "family": meta.get('family', 'cosyvoice'),
                    "supports_instruction": meta.get('supports_instruction', False),
                    "dialect": meta.get('dialect'),
                    "lang_primary": meta.get('lang_primary'),
                    "origin": "system",  # custom voices (clone/design) come from a separate endpoint
                }
                for meta in voices_dict.values()
            ]
        else:
            return [
                {"id": "longxiaochun_v2", "name": "龙小淳 (知性女) - CosyVoice", "gender": "Female", "family": "cosyvoice", "origin": "system"},
                {"id": "longyue_v2", "name": "龙悦 (温柔女) - CosyVoice", "gender": "Female", "family": "cosyvoice", "origin": "system"},
                {"id": "longcheng_v2", "name": "龙诚 (睿智青年) - CosyVoice", "gender": "Male", "family": "cosyvoice", "origin": "system"},
                {"id": "longshu_v2", "name": "龙书 (播报男) - CosyVoice", "gender": "Male", "family": "cosyvoice", "origin": "system"},
            ]

    def generate_dialogue(
        self,
        frame: StoryboardFrame,
        character: Character,
        speed: float = 1.0,
        pitch: float = 1.0,
        volume: int = 50,
        instructions: Optional[str] = None,
        model_override: Optional[str] = None,
        family_override: Optional[str] = None,
    ) -> StoryboardFrame:
        """Generates TTS audio for the dialogue."""
        text = _effective_dialogue_text(frame)
        if not text:
            return frame

        frame.status = GenerationStatus.PROCESSING

        if instructions is None:
            instructions = _effective_instructions(frame)

        logger.info(f"Generating dialogue for {character.name}: {text} (Speed: {speed}, Pitch: {pitch}, Volume: {volume}, instr: {instructions or '-'})")

        if not self.tts:
            frame.status = GenerationStatus.FAILED
            frame.audio_error = "TTS service not available. Check DASHSCOPE_API_KEY configuration."
            logger.warning(f"TTS not initialized, cannot generate audio for frame {frame.id}")
            return frame

        if not character.voice_id:
            frame.status = GenerationStatus.FAILED
            frame.audio_error = f"No voice assigned to character '{character.name}'. Please assign a voice first."
            logger.warning(f"No voice_id for character {character.name}, cannot generate audio")
            return frame

        return self._real_generate_dialogue(
            frame, character, text, speed, pitch, volume,
            instructions=instructions,
            model_override=model_override,
            family_override=family_override,
        )

    def _real_generate_dialogue(
        self,
        frame: StoryboardFrame,
        character: Character,
        text: str,
        speed: float,
        pitch: float,
        volume: int,
        instructions: Optional[str] = None,
        model_override: Optional[str] = None,
        family_override: Optional[str] = None,
    ) -> StoryboardFrame:
        """Generate dialogue using real TTS."""
        try:
            output_path = os.path.join(self.output_dir, 'dialogue', f"{frame.id}.mp3")
            os.makedirs(os.path.dirname(output_path), exist_ok=True)

            voice = character.voice_id

            self.tts.synthesize(
                text, output_path, voice=voice,
                speech_rate=speed, pitch_rate=pitch, volume=volume,
                instructions=instructions,
                model_override=model_override,
                family_override=family_override,
            )

            rel_path = os.path.relpath(output_path, "output")
            frame.audio_url = rel_path
            frame.audio_error = None
            frame.status = GenerationStatus.COMPLETED
            # PR-3j · snapshot for stale detection
            frame.dialogue_voice_id = voice
            frame.dialogue_instructions = instructions
            frame.dialogue_text_hash = _compute_dialogue_hash(text, voice, instructions)

        except Exception as e:
            logger.error(f"TTS generation failed for frame {frame.id}: {e}")
            frame.status = GenerationStatus.FAILED
            frame.audio_error = f"TTS generation failed: {str(e)}"

        return frame

    def _mock_generate_dialogue(self, frame: StoryboardFrame, character: Character, text: str, speed: float, pitch: float, volume: int) -> StoryboardFrame:
        """Mock fallback — marks frame as FAILED instead of writing dummy bytes."""
        frame.status = GenerationStatus.FAILED
        frame.audio_error = "TTS service unavailable (mock mode)"
        logger.warning(f"Mock generate_dialogue called for frame {frame.id} — marking as FAILED")
        return frame

    def generate_sfx(self, frame: StoryboardFrame) -> StoryboardFrame:
        """Generates sound effects for the frame."""
        frame.status = GenerationStatus.PROCESSING
        
        try:
            # TODO: Implement actual SFX call (e.g., MMAudio)
            # For now, we mock it.
            logger.info(f"Generating SFX for: {frame.action_description}")
            
            output_path = os.path.join(self.output_dir, 'sfx', f"{frame.id}.mp3")
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            
            # Create a dummy file
            with open(output_path, 'wb') as f:
                f.write(b'dummy sfx content')
                
            # Store relative path for frontend serving
            rel_path = os.path.relpath(output_path, "output")
            frame.sfx_url = rel_path
            frame.status = GenerationStatus.COMPLETED
            
        except Exception as e:
            logger.error(f"Failed to generate SFX for frame {frame.id}: {e}")
            frame.status = GenerationStatus.FAILED
            
        return frame

    def generate_sfx_from_video(self, frame: StoryboardFrame) -> StoryboardFrame:
        """Generates SFX based on video content (Video-to-Audio)."""
        if not frame.video_url:
            return frame
            
        logger.info(f"Generating SFX from video for frame {frame.id}")
        # Mock V2A Logic
        time.sleep(1)
        
        output_path = os.path.join(self.output_dir, 'sfx', f"{frame.id}_v2a.mp3")
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        
        with open(output_path, 'wb') as f:
            f.write(b'dummy v2a sfx content')
            
        frame.sfx_url = os.path.relpath(output_path, "output")
        return frame

    def generate_bgm(self, frame: StoryboardFrame) -> StoryboardFrame:
        """Generates BGM based on frame context."""
        logger.info(f"Generating BGM for frame {frame.id}")
        # Mock MusicGen Logic
        time.sleep(1)
        
        output_path = os.path.join(self.output_dir, 'bgm', f"{frame.id}.mp3")
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        
        with open(output_path, 'wb') as f:
            f.write(b'dummy bgm content')
            
        frame.bgm_url = os.path.relpath(output_path, "output")
        return frame
