"""
Text-to-Speech (TTS) module using DashScope CosyVoice API.
Converts text to speech audio for use in video lip-sync.
"""
import os
import logging
from typing import Optional, Tuple
from dashscope.audio.tts_v2 import SpeechSynthesizer

logger = logging.getLogger(__name__)


class TTSProcessor:
    """Text-to-Speech processor using CosyVoice"""
    
    # Available voices
    VOICES = {
        'longxiaochun': {'model_id': 'longxiaochun_v2', 'name': '龙小淳', 'gender': 'Female'},
        'longyueyue': {'model_id': 'longyueyue_v2', 'name': '龙悦悦', 'gender': 'Female'},
        'longxiaobai': {'model_id': 'longxiaobai_v2', 'name': '龙小白', 'gender': 'Male'},
        'longfeiyan': {'model_id': 'longfeiyan_v2', 'name': '龙飞燕', 'gender': 'Female'},
        'longxiaoxin': {'model_id': 'longxiaoxin_v2', 'name': '龙小新', 'gender': 'Male'},
        'longshu': {'model_id': 'longshu_v2', 'name': '龙书 (旁白)', 'gender': 'Male'},
        'longshuo': {'model_id': 'longshuo_v2', 'name': '龙朔 (少年)', 'gender': 'Male'},
        'longjielidou': {'model_id': 'longjielidou_v2', 'name': '龙杰力豆 (童声)', 'gender': 'Male'},
        'longxiaofei': {'model_id': 'longxiaofei_v2', 'name': '龙小飞 (活力女)', 'gender': 'Female'},
        'longyue': {'model_id': 'longyue_v2', 'name': '龙悦 (温柔女)', 'gender': 'Female'},
        'loongstella': {'model_id': 'loongstella_v2', 'name': 'Stella (English Female)', 'gender': 'Female'},
        'loongbella': {'model_id': 'loongbella_v2', 'name': 'Bella (English Female)', 'gender': 'Female'},
    }
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "cosyvoice-v2",
        voice: str = "longxiaochun_v2"
    ):
        """
        Initialize TTS processor
        
        Args:
            api_key: DashScope API key. If None, will read from DASHSCOPE_API_KEY env var
            model: TTS model name (default: cosyvoice-v2)
            voice: Voice name (default: longxiaochun_v2)
        """
        import dashscope
        
        self.api_key = api_key or os.getenv('DASHSCOPE_API_KEY')
        if self.api_key:
            dashscope.api_key = self.api_key
        
        self.model = model
        self.voice = voice
        
        logger.info(f"TTS Processor initialized with model={model}, voice={voice}")
    
    def synthesize(
        self,
        text: str,
        output_path: str,
        voice: Optional[str] = None,
        speech_rate: float = 1.0,
        pitch_rate: float = 1.0
    ) -> Tuple[str, float, str]:
        """
        Synthesize speech from text

        Args:
            text: Text to synthesize
            output_path: Path to save audio file (should end with .mp3 or .wav)
            voice: Optional voice override
            speech_rate: Speech speed multiplier (0.5-2.0, default 1.0)
            pitch_rate: Pitch multiplier (0.5-2.0, default 1.0)

        Returns:
            Tuple[str, float, str]: (output_path, first_package_delay_ms, request_id)
        """
        import time

        start_time = time.time()

        voice = voice or self.voice

        logger.info(f"Synthesizing text with voice '{voice}' (rate={speech_rate}, pitch={pitch_rate})...")
        logger.info(f"Text: {text[:100]}{'...' if len(text) > 100 else ''}")

        # Create synthesizer with speed/pitch parameters
        synth_kwargs = dict(model=self.model, voice=voice)
        if speech_rate != 1.0:
            synth_kwargs['speech_rate'] = speech_rate
        if pitch_rate != 1.0:
            synth_kwargs['pitch_rate'] = pitch_rate
        synthesizer = SpeechSynthesizer(**synth_kwargs)
        
        # Synthesize audio
        audio_data = synthesizer.call(text)
        
        # Get metrics
        request_id = synthesizer.get_last_request_id()
        first_package_delay = synthesizer.get_first_package_delay()
        
        # Save audio
        with open(output_path, 'wb') as f:
            f.write(audio_data)
        
        duration = time.time() - start_time
        
        logger.info(f"Audio synthesized successfully!")
        logger.info(f"Request ID: {request_id}")
        logger.info(f"First package delay: {first_package_delay}ms")
        logger.info(f"Total duration: {duration:.2f}s")
        logger.info(f"Output: {output_path}")
        
        return output_path, first_package_delay, request_id
    
    @staticmethod
    def list_voices():
        """List available voices with metadata"""
        return TTSProcessor.VOICES
