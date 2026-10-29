import os
import hashlib
import uuid
from types import MappingProxyType
from typing import Callable, Dict, Any, List, Optional
from .models import StoryboardFrame, Character, GenerationStatus, DialogueAudioOptions
from ...utils import get_logger
from ...audio.tts import TTSProcessor, PreparedTTS
from ...utils.json_storage import StorageError

logger = get_logger(__name__)


def _default_output_root(output_dir: str, leaf: str) -> str:
    normalized = os.path.normpath(os.fspath(output_dir))
    if os.path.basename(normalized) == leaf:
        parent = os.path.dirname(normalized)
        if parent:
            return parent
    return "output"


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
    """Derive current desired instructions; historical output is never an override."""
    if frame.dialogue_structured:
        parts = []
        if frame.dialogue_structured.emotion:
            parts.append(f"情绪：{frame.dialogue_structured.emotion}")
        if frame.dialogue_structured.delivery:
            parts.append(f"演绎：{frame.dialogue_structured.delivery}")
        if parts:
            return "；".join(parts)
    return None


class UnsupportedAudioParameters(ValueError):
    def __init__(self, fields: List[str], prepared: PreparedTTS):
        super().__init__("The selected voice/model cannot apply the requested audio overrides")
        self.detail = {"code": "unsupported_audio_parameters", "fields": sorted(fields),
                       "family": prepared.family, "model": prepared.model}


def dialogue_prepared_projection(value: Dict[str, Any]) -> Dict[str, Any]:
    """Only submitted parameters affect equivalence, not ignored input or provenance."""
    return {key: value.get(key) for key in ("rules_version", "family", "parameters")}


def prepared_dialogue_is_stale(frame: StoryboardFrame, prepared: Optional[PreparedTTS]) -> bool:
    snapshot = frame.dialogue_audio_snapshot
    return (prepared is None or snapshot is None or snapshot.schema_version != 1
            or snapshot.audio_url != frame.audio_url
            or dialogue_prepared_projection(snapshot.prepared) != dialogue_prepared_projection(prepared.to_snapshot()))


class AudioGenerator:
    def __init__(
        self, config: Dict[str, Any] = None,
        *, before_generation: Optional[Callable[[], None]] = None, env=None,
    ):
        self.config = config or {}
        self._env = None if env is None else MappingProxyType(dict(env))
        self._before_generation = before_generation
        self.output_dir = self.config.get('output_dir', 'output/audio')
        self.output_root = self.config.get('output_root') or _default_output_root(self.output_dir, "audio")
        
        # Initialize TTS Processor
        try:
            self.tts = TTSProcessor(**({"env": self._env} if self._env is not None else {}))
            logger.info("TTS Processor initialized successfully")
        except Exception as e:
            logger.warning(f"Failed to initialize TTS Processor: {e}. Using mock mode.")
            self.tts = None

    def _check_storage(self) -> None:
        if self._before_generation is not None:
            self._before_generation()

    def _rel_output_path(self, path: str) -> str:
        return os.path.relpath(path, self.output_root)

    def prepare_dialogue(self, frame: StoryboardFrame, character: Character,
                         options: DialogueAudioOptions, *, model_override=None, family_override=None):
        """Resolve desired values once, then let TTS preparation define actual SDK input."""
        self._check_storage()
        resolved = {}
        sources = {}
        for name in ("speed", "pitch", "volume"):
            override = getattr(options, name)
            resolved[name] = getattr(character, f"voice_{name}") if override is None else override
            sources[name] = "character_binding" if override is None else "override"
        resolved["instructions"] = (_effective_instructions(frame) if options.instructions is None
                                    else options.instructions)
        sources["instructions"] = "structured_dialogue" if options.instructions is None else "override"
        if not self.tts or not character.voice_id:
            return None, resolved, sources
        prepared = self.tts.prepare(
            _effective_dialogue_text(frame), voice=character.voice_id,
            speech_rate=resolved["speed"], pitch_rate=resolved["pitch"], volume=resolved["volume"],
            instructions=resolved["instructions"], model_override=model_override, family_override=family_override,
        )
        names = {"speech_rate": "speed", "pitch_rate": "pitch"}
        unsupported = {names.get(item.field, item.field) for item in prepared.unsupported}
        explicit = [name for name in unsupported if getattr(options, name) is not None
                    and not (name == "instructions" and options.instructions == "")]
        if explicit:
            raise UnsupportedAudioParameters(explicit, prepared)
        return prepared, resolved, sources

    def generate_prepared_dialogue(self, frame: StoryboardFrame, character: Character,
                                  prepared: Optional[PreparedTTS]) -> StoryboardFrame:
        self._check_storage()
        if prepared is None:
            frame.status = GenerationStatus.FAILED
            frame.audio_error = ("No voice assigned to the dialogue character" if not character.voice_id
                                 else "TTS service not available. Check DASHSCOPE_API_KEY configuration.")
            return frame
        return self._real_generate_dialogue(
            frame, character, prepared.text, 1.0, 1.0, 50, prepared=prepared,
        )

    def get_available_voices(self) -> List[Dict[str, Any]]:
        """Returns a list of available voices with full registry metadata.

        Shape expanded in PR-3g #3 — frontend voice picker (Q15.5 B) needs
        family/dialect/lang_primary/supports_instruction to render the
        3 tabs (系统音色 / 我的复刻 / 我的设计) with dialect/international
        sub-groupings inside 系统音色.
        """
        if self.tts:
            voices_dict = TTSProcessor.list_voices()
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
        self._check_storage()
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
        *, prepared: Optional[PreparedTTS] = None,
    ) -> StoryboardFrame:
        """Generate dialogue using real TTS."""
        try:
            output_path = os.path.join(self.output_dir, 'dialogue', f"{uuid.uuid4().hex}.mp3")
            os.makedirs(os.path.dirname(output_path), exist_ok=True)

            voice = character.voice_id

            self._check_storage()
            if prepared is None:
                self.tts.synthesize(
                    text, output_path, voice=voice,
                    speech_rate=speed, pitch_rate=pitch, volume=volume,
                    instructions=instructions,
                    model_override=model_override,
                    family_override=family_override,
                )
            else:
                self.tts.execute(prepared, output_path)
                voice = prepared.voice
                actual = prepared.sdk_kwargs()
                instructions = actual.get("instruction", actual.get("instructions"))
            self._check_storage()

            rel_path = self._rel_output_path(output_path)
            frame.audio_url = rel_path
            frame.audio_error = None
            frame.status = GenerationStatus.COMPLETED
            # PR-3j · snapshot for stale detection
            frame.dialogue_voice_id = voice
            if prepared is None:
                frame.dialogue_instructions = instructions
            frame.dialogue_text_hash = _compute_dialogue_hash(text, voice, instructions)

        except StorageError:
            raise
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
        """SFX generation is intentionally unsupported until a real provider exists."""
        self._check_storage()
        raise NotImplementedError("SFX generation is not supported; no SFX provider/task boundary is wired")

    def generate_sfx_from_video(self, frame: StoryboardFrame) -> StoryboardFrame:
        """Video-to-audio SFX generation is intentionally unsupported."""
        self._check_storage()
        raise NotImplementedError("Video-to-audio SFX generation is not supported; no provider/task boundary is wired")

    def generate_bgm(self, frame: StoryboardFrame) -> StoryboardFrame:
        """Generative BGM is intentionally unsupported; use script-level BGM presets/uploads."""
        self._check_storage()
        raise NotImplementedError("Generative BGM is not supported; use BGM presets or upload audio via audio_mix")
