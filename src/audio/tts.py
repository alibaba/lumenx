"""
Text-to-Speech (TTS) module using DashScope CosyVoice API.
Converts text to speech audio for use in video lip-sync.

Supports cosyvoice-v2 and cosyvoice-v3-flash/v3-plus models.
See: https://help.aliyun.com/zh/model-studio/cosyvoice-python-sdk
"""
import os
import logging
import re
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


# Voice registry: key -> {model_id, name, gender, model}
# model_id must match the model version (v2 voices for cosyvoice-v2, v3 for cosyvoice-v3-*)
# Reference: https://help.aliyun.com/zh/model-studio/cosyvoice-voice-list
VOICES = {
    # === cosyvoice-v2 voices ===
    'longxiaochun': {'model_id': 'longxiaochun_v2', 'name': '龙小淳 (知性女)', 'gender': 'Female', 'model': 'cosyvoice-v2'},
    'longxiaoxia': {'model_id': 'longxiaoxia_v2', 'name': '龙小夏 (沉稳女)', 'gender': 'Female', 'model': 'cosyvoice-v2'},
    'longyue': {'model_id': 'longyue_v2', 'name': '龙悦 (温柔女)', 'gender': 'Female', 'model': 'cosyvoice-v2'},
    'longmiao': {'model_id': 'longmiao_v2', 'name': '龙淼 (有声书女)', 'gender': 'Female', 'model': 'cosyvoice-v2'},
    'longyuan': {'model_id': 'longyuan_v2', 'name': '龙媛 (治愈女)', 'gender': 'Female', 'model': 'cosyvoice-v2'},
    'longhua': {'model_id': 'longhua_v2', 'name': '龙华 (活力甜美女)', 'gender': 'Female', 'model': 'cosyvoice-v2'},
    'longwan': {'model_id': 'longwan_v2', 'name': '龙婉 (知性女)', 'gender': 'Female', 'model': 'cosyvoice-v2'},
    'longxing': {'model_id': 'longxing_v2', 'name': '龙星 (邻家女孩)', 'gender': 'Female', 'model': 'cosyvoice-v2'},
    'longfeifei': {'model_id': 'longfeifei_v2', 'name': '龙菲菲 (甜美女)', 'gender': 'Female', 'model': 'cosyvoice-v2'},
    'longyan': {'model_id': 'longyan_v2', 'name': '龙言 (温柔女)', 'gender': 'Female', 'model': 'cosyvoice-v2'},
    'longqiang': {'model_id': 'longqiang_v2', 'name': '龙蔷 (浪漫女)', 'gender': 'Female', 'model': 'cosyvoice-v2'},
    'longxiu': {'model_id': 'longxiu_v2', 'name': '龙修 (博学男)', 'gender': 'Male', 'model': 'cosyvoice-v2'},
    'longnan': {'model_id': 'longnan_v2', 'name': '龙楠 (睿智少年)', 'gender': 'Male', 'model': 'cosyvoice-v2'},
    'longcheng': {'model_id': 'longcheng_v2', 'name': '龙诚 (睿智青年)', 'gender': 'Male', 'model': 'cosyvoice-v2'},
    'longze': {'model_id': 'longze_v2', 'name': '龙泽 (阳光男)', 'gender': 'Male', 'model': 'cosyvoice-v2'},
    'longzhe': {'model_id': 'longzhe_v2', 'name': '龙哲 (暖心男)', 'gender': 'Male', 'model': 'cosyvoice-v2'},
    'longtian': {'model_id': 'longtian_v2', 'name': '龙天 (磁性男)', 'gender': 'Male', 'model': 'cosyvoice-v2'},
    'longhan': {'model_id': 'longhan_v2', 'name': '龙翰 (深情男)', 'gender': 'Male', 'model': 'cosyvoice-v2'},
    'longhao': {'model_id': 'longhao_v2', 'name': '龙浩 (忧郁男)', 'gender': 'Male', 'model': 'cosyvoice-v2'},
    'longshu': {'model_id': 'longshu_v2', 'name': '龙书 (播报男)', 'gender': 'Male', 'model': 'cosyvoice-v2'},
    'longshuo': {'model_id': 'longshuo_v2', 'name': '龙朔 (博学男)', 'gender': 'Male', 'model': 'cosyvoice-v2'},
    'longfei': {'model_id': 'longfei_v2', 'name': '龙飞 (磁性朗诵男)', 'gender': 'Male', 'model': 'cosyvoice-v2'},
    'longxiaocheng': {'model_id': 'longxiaocheng_v2', 'name': '龙小诚 (低音男)', 'gender': 'Male', 'model': 'cosyvoice-v2'},
    'longshao': {'model_id': 'longshao_v2', 'name': '龙少 (阳光男)', 'gender': 'Male', 'model': 'cosyvoice-v2'},
    'longjielidou': {'model_id': 'longjielidou_v2', 'name': '龙杰力豆 (童声男)', 'gender': 'Male', 'model': 'cosyvoice-v2'},
    'longhuhu': {'model_id': 'longhuhu', 'name': '龙虎虎 (童声女)', 'gender': 'Female', 'model': 'cosyvoice-v2'},
    'loongstella': {'model_id': 'loongstella_v2', 'name': 'Stella (English Female)', 'gender': 'Female', 'model': 'cosyvoice-v2'},
    'loongbella': {'model_id': 'loongbella_v2', 'name': 'Bella (English Female)', 'gender': 'Female', 'model': 'cosyvoice-v2'},
    # === cosyvoice-v3 voices (require cosyvoice-v3-flash or cosyvoice-v3-plus) ===
    'longanyang': {
        'model_id': 'longanyang', 'name': '龙安阳 (阳光少年)',
        'gender': 'Male', 'model': 'cosyvoice-v3-flash',
        # This benchmark voice rejects free-form instructions. DashScope
        # requires exactly: 你说话的情感是<supported emotion>。
        'supports_instruction': True, 'instruction_mode': 'strict_emotion',
    },
    'longanhuan': {
        'model_id': 'longanhuan', 'name': '龙安欢 (活力女)',
        'gender': 'Female', 'model': 'cosyvoice-v3-flash',
        'supports_instruction': True, 'instruction_mode': 'strict_emotion',
    },
    # === Qwen3-TTS voices (PR-3g Stage A — added 2026-05-25 from official doc)
    # Use qwen3-tts-flash for standard / qwen3-tts-instruct-flash for instructions
    # control. Voice IDs are case-sensitive (Cherry not cherry). Supports 10 langs:
    # zh / en / fr / de / ru / it / es / pt / ja / ko.
    # `family: 'qwen3'` is the new optional metadata - existing CosyVoice entries
    # implicitly family='cosyvoice'. Will be migrated to explicit field in next commit.
    # === Standard Mandarin voices ===
    'Cherry': {'model_id': 'Cherry', 'name': 'Cherry · 芊悦 (阳光积极)', 'gender': 'Female', 'model': 'qwen3-tts-flash', 'family': 'qwen3', 'supports_instruction': True},
    'Serena': {'model_id': 'Serena', 'name': 'Serena · 苏瑶 (温柔小姐姐)', 'gender': 'Female', 'model': 'qwen3-tts-flash', 'family': 'qwen3', 'supports_instruction': True},
    'Ethan': {'model_id': 'Ethan', 'name': 'Ethan · 晨煦 (阳光温暖男)', 'gender': 'Male', 'model': 'qwen3-tts-flash', 'family': 'qwen3', 'supports_instruction': True},
    'Chelsie': {'model_id': 'Chelsie', 'name': 'Chelsie · 千雪 (二次元虚拟女友)', 'gender': 'Female', 'model': 'qwen3-tts-flash', 'family': 'qwen3', 'supports_instruction': True},
    'Momo': {'model_id': 'Momo', 'name': 'Momo · 茉兔 (撒娇搞怪)', 'gender': 'Female', 'model': 'qwen3-tts-flash', 'family': 'qwen3', 'supports_instruction': True},
    'Vivian': {'model_id': 'Vivian', 'name': 'Vivian · 十三 (拽拽小暴躁)', 'gender': 'Female', 'model': 'qwen3-tts-flash', 'family': 'qwen3', 'supports_instruction': True},
    'Moon': {'model_id': 'Moon', 'name': 'Moon · 月白 (率性帅气男)', 'gender': 'Male', 'model': 'qwen3-tts-flash', 'family': 'qwen3', 'supports_instruction': True},
    'Maia': {'model_id': 'Maia', 'name': 'Maia · 四月 (知性温柔)', 'gender': 'Female', 'model': 'qwen3-tts-flash', 'family': 'qwen3', 'supports_instruction': True},
    'Kai': {'model_id': 'Kai', 'name': 'Kai · 凯 (耳朵 SPA 男)', 'gender': 'Male', 'model': 'qwen3-tts-flash', 'family': 'qwen3', 'supports_instruction': True},
    'Nofish': {'model_id': 'Nofish', 'name': 'Nofish · 不吃鱼 (设计师不卷舌)', 'gender': 'Male', 'model': 'qwen3-tts-flash', 'family': 'qwen3', 'supports_instruction': True},
    'Bella': {'model_id': 'Bella', 'name': 'Bella · 萌宝 (小萝莉)', 'gender': 'Female', 'model': 'qwen3-tts-flash', 'family': 'qwen3', 'supports_instruction': True},
    'Eldric Sage': {'model_id': 'Eldric Sage', 'name': 'Eldric Sage · 沧明子 (沉稳睿智老者)', 'gender': 'Male', 'model': 'qwen3-tts-flash', 'family': 'qwen3', 'supports_instruction': True},
    'Mia': {'model_id': 'Mia', 'name': 'Mia · 乖小妹 (温顺乖巧)', 'gender': 'Female', 'model': 'qwen3-tts-flash', 'family': 'qwen3', 'supports_instruction': True},
    'Mochi': {'model_id': 'Mochi', 'name': 'Mochi · 沙小弥 (聪明伶俐童声男)', 'gender': 'Male', 'model': 'qwen3-tts-flash', 'family': 'qwen3', 'supports_instruction': True},
    'Bellona': {'model_id': 'Bellona', 'name': 'Bellona · 燕铮莺 (声音洪亮人物鲜活)', 'gender': 'Female', 'model': 'qwen3-tts-flash', 'family': 'qwen3', 'supports_instruction': True},
    'Vincent': {'model_id': 'Vincent', 'name': 'Vincent · 田叔 (沙哑烟嗓江湖)', 'gender': 'Male', 'model': 'qwen3-tts-flash', 'family': 'qwen3', 'supports_instruction': True},
    'Bunny': {'model_id': 'Bunny', 'name': 'Bunny · 萌小姬 (萌属性小萝莉)', 'gender': 'Female', 'model': 'qwen3-tts-flash', 'family': 'qwen3', 'supports_instruction': True},
    'Neil': {'model_id': 'Neil', 'name': 'Neil · 阿闻 (新闻主持人)', 'gender': 'Male', 'model': 'qwen3-tts-flash', 'family': 'qwen3', 'supports_instruction': True},
    'Elias': {'model_id': 'Elias', 'name': 'Elias · 墨讲师 (严谨知识讲解女)', 'gender': 'Female', 'model': 'qwen3-tts-flash', 'family': 'qwen3', 'supports_instruction': True},
    'Arthur': {'model_id': 'Arthur', 'name': 'Arthur · 徐大爷 (旱烟嗓村奇闻)', 'gender': 'Male', 'model': 'qwen3-tts-flash', 'family': 'qwen3', 'supports_instruction': True},
    'Nini': {'model_id': 'Nini', 'name': 'Nini · 邻家妹妹 (糯米糍甜腻)', 'gender': 'Female', 'model': 'qwen3-tts-flash', 'family': 'qwen3', 'supports_instruction': True},
    'Seren': {'model_id': 'Seren', 'name': 'Seren · 小婉 (助眠温和)', 'gender': 'Female', 'model': 'qwen3-tts-flash', 'family': 'qwen3', 'supports_instruction': True},
    'Pip': {'model_id': 'Pip', 'name': 'Pip · 顽屁小孩 (调皮捣蛋男童)', 'gender': 'Male', 'model': 'qwen3-tts-flash', 'family': 'qwen3', 'supports_instruction': True},
    'Stella': {'model_id': 'Stella', 'name': 'Stella · 少女阿月 (迷糊少女→正义)', 'gender': 'Female', 'model': 'qwen3-tts-flash', 'family': 'qwen3', 'supports_instruction': True},
    'Jennifer': {'model_id': 'Jennifer', 'name': 'Jennifer · 詹妮弗 (电影质感美语女)', 'gender': 'Female', 'model': 'qwen3-tts-flash', 'family': 'qwen3', 'supports_instruction': False},
    'Ryan': {'model_id': 'Ryan', 'name': 'Ryan · 甜茶 (戏感炸裂男)', 'gender': 'Male', 'model': 'qwen3-tts-flash', 'family': 'qwen3', 'supports_instruction': False},
    'Katerina': {'model_id': 'Katerina', 'name': 'Katerina · 卡捷琳娜 (御姐韵律)', 'gender': 'Female', 'model': 'qwen3-tts-flash', 'family': 'qwen3', 'supports_instruction': False},
    'Aiden': {'model_id': 'Aiden', 'name': 'Aiden · 艾登 (美语大男孩)', 'gender': 'Male', 'model': 'qwen3-tts-flash', 'family': 'qwen3', 'supports_instruction': False},
    # === Qwen3 dialect voices (extremely valuable for Chinese drama creation) ===
    'Jada': {'model_id': 'Jada', 'name': 'Jada · 上海-阿珍 (沪上阿姐)', 'gender': 'Female', 'model': 'qwen3-tts-flash', 'family': 'qwen3', 'dialect': 'shanghai', 'supports_instruction': False},
    'Dylan': {'model_id': 'Dylan', 'name': 'Dylan · 北京-晓东 (胡同少年)', 'gender': 'Male', 'model': 'qwen3-tts-flash', 'family': 'qwen3', 'dialect': 'beijing', 'supports_instruction': False},
    'Li': {'model_id': 'Li', 'name': 'Li · 南京-老李 (耐心瑜伽老师)', 'gender': 'Male', 'model': 'qwen3-tts-flash', 'family': 'qwen3', 'dialect': 'nanjing', 'supports_instruction': False},
    'Marcus': {'model_id': 'Marcus', 'name': 'Marcus · 陕西-秦川 (老陕实在)', 'gender': 'Male', 'model': 'qwen3-tts-flash', 'family': 'qwen3', 'dialect': 'shaanxi', 'supports_instruction': False},
    'Roy': {'model_id': 'Roy', 'name': 'Roy · 闽南-阿杰 (台湾哥仔诙谐)', 'gender': 'Male', 'model': 'qwen3-tts-flash', 'family': 'qwen3', 'dialect': 'minnan', 'supports_instruction': False},
    'Peter': {'model_id': 'Peter', 'name': 'Peter · 天津-李彼得 (相声捧哏)', 'gender': 'Male', 'model': 'qwen3-tts-flash', 'family': 'qwen3', 'dialect': 'tianjin', 'supports_instruction': False},
    'Sunny': {'model_id': 'Sunny', 'name': 'Sunny · 四川-晴儿 (川妹子甜)', 'gender': 'Female', 'model': 'qwen3-tts-flash', 'family': 'qwen3', 'dialect': 'sichuan', 'supports_instruction': False},
    'Eric': {'model_id': 'Eric', 'name': 'Eric · 四川-程川 (跳脱成都男)', 'gender': 'Male', 'model': 'qwen3-tts-flash', 'family': 'qwen3', 'dialect': 'sichuan', 'supports_instruction': False},
    'Rocky': {'model_id': 'Rocky', 'name': 'Rocky · 粤语-阿强 (幽默风趣)', 'gender': 'Male', 'model': 'qwen3-tts-flash', 'family': 'qwen3', 'dialect': 'cantonese', 'supports_instruction': False},
    'Kiki': {'model_id': 'Kiki', 'name': 'Kiki · 粤语-阿清 (港妹闺蜜)', 'gender': 'Female', 'model': 'qwen3-tts-flash', 'family': 'qwen3', 'dialect': 'cantonese', 'supports_instruction': False},
    # === Qwen3 international voices (multilingual) ===
    'Bodega': {'model_id': 'Bodega', 'name': 'Bodega · 博德加 (西班牙大叔)', 'gender': 'Male', 'model': 'qwen3-tts-flash', 'family': 'qwen3', 'lang_primary': 'es', 'supports_instruction': False},
    'Sonrisa': {'model_id': 'Sonrisa', 'name': 'Sonrisa · 索尼莎 (拉美开朗大姐)', 'gender': 'Female', 'model': 'qwen3-tts-flash', 'family': 'qwen3', 'lang_primary': 'es', 'supports_instruction': False},
    'Alek': {'model_id': 'Alek', 'name': 'Alek · 阿列克 (战斗民族冷暖)', 'gender': 'Male', 'model': 'qwen3-tts-flash', 'family': 'qwen3', 'lang_primary': 'ru', 'supports_instruction': False},
    'Dolce': {'model_id': 'Dolce', 'name': 'Dolce · 多尔切 (慵懒意大利)', 'gender': 'Male', 'model': 'qwen3-tts-flash', 'family': 'qwen3', 'lang_primary': 'it', 'supports_instruction': False},
    'Sohee': {'model_id': 'Sohee', 'name': 'Sohee · 素熙 (韩国温柔欧尼)', 'gender': 'Female', 'model': 'qwen3-tts-flash', 'family': 'qwen3', 'lang_primary': 'ko', 'supports_instruction': False},
    'Ono Anna': {'model_id': 'Ono Anna', 'name': 'Ono Anna · 小野杏 (鬼灵精怪青梅)', 'gender': 'Female', 'model': 'qwen3-tts-flash', 'family': 'qwen3', 'lang_primary': 'ja', 'supports_instruction': False},
    'Lenn': {'model_id': 'Lenn', 'name': 'Lenn · 莱恩 (理性德国青年)', 'gender': 'Male', 'model': 'qwen3-tts-flash', 'family': 'qwen3', 'lang_primary': 'de', 'supports_instruction': False},
    'Emilien': {'model_id': 'Emilien', 'name': 'Emilien · 埃米尔安 (浪漫法国大哥)', 'gender': 'Male', 'model': 'qwen3-tts-flash', 'family': 'qwen3', 'lang_primary': 'fr', 'supports_instruction': False},
    'Andre': {'model_id': 'Andre', 'name': 'Andre · 安德雷 (磁性沉稳男)', 'gender': 'Male', 'model': 'qwen3-tts-flash', 'family': 'qwen3', 'supports_instruction': False},
    'Radio Gol': {'model_id': 'Radio Gol', 'name': 'Radio Gol · 拉迪奥·戈尔 (足球诗人解说男)', 'gender': 'Male', 'model': 'qwen3-tts-flash', 'family': 'qwen3', 'supports_instruction': False},
}


class TTSProcessor:
    """Text-to-Speech processor using CosyVoice"""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "cosyvoice-v3-flash",
        voice: str = "longanyang"
    ):
        """
        Initialize TTS processor

        Args:
            api_key: DashScope API key. If None, will read from DASHSCOPE_API_KEY env var
            model: TTS model name (default: cosyvoice-v2)
            voice: Default voice ID (default: longxiaochun_v2)
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
        pitch_rate: float = 1.0,
        volume: int = 50,
        instructions: Optional[str] = None,
        model_override: Optional[str] = None,
        family_override: Optional[str] = None,
    ) -> Tuple[str, float, str]:
        """
        Synthesize speech from text. Dispatches to CosyVoice or Qwen3-TTS
        based on the voice's family (registry metadata).

        Args:
            text: Text to synthesize (max 20,000 characters)
            output_path: Path to save audio file
            voice: Voice ID override (must match model version)
            speech_rate: Speech speed multiplier (0.5-2.0, default 1.0)
            pitch_rate: Pitch multiplier (0.5-2.0, default 1.0)
            volume: Volume level (0-100, default 50)
            instructions: Natural-language instruction for voice control
                (CosyVoice ≤100 chars / Qwen3 ≤1600 tokens). Only honored by
                models with `supports_instruction=True`; ignored silently
                otherwise. PR-3g #2.
            model_override: Force a specific TTS model (e.g. cosyvoice-v3.5-plus
                for custom cloned voices not in TTS_VOICE_REGISTRY). PR-3h #2.
            family_override: Force dispatch family ('cosyvoice' | 'qwen3') for
                voices not in the registry. PR-3h #2.

        Returns:
            Tuple[str, float, str]: (output_path, first_package_delay_ms, request_id)
        """
        voice = voice or self.voice
        family = family_override or self._resolve_family_for_voice(voice)

        if family == 'qwen3':
            return self._synthesize_qwen3(
                text, output_path, voice,
                speech_rate=speech_rate, instructions=instructions,
                model_override=model_override,
            )
        # CosyVoice (default for legacy entries without family metadata)
        return self._synthesize_cosyvoice(
            text, output_path, voice,
            speech_rate=speech_rate, pitch_rate=pitch_rate, volume=volume,
            instructions=instructions, model_override=model_override,
        )

    def _synthesize_cosyvoice(
        self, text: str, output_path: str, voice: str,
        speech_rate: float = 1.0, pitch_rate: float = 1.0, volume: int = 50,
        instructions: Optional[str] = None,
        model_override: Optional[str] = None,
    ) -> Tuple[str, float, str]:
        """Synthesize using CosyVoice SpeechSynthesizer (dashscope.audio.tts_v2)."""
        import time
        from dashscope.audio.tts_v2 import SpeechSynthesizer

        start_time = time.time()
        # PR-3h #2: model_override lets caller (e.g. /voice/preview for a
        # custom cloned voice) bypass registry lookup which would fall back
        # to self.model and produce wrong output. Cloned voices need their
        # target_model (typically cosyvoice-v3.5-plus).
        model = model_override or self._resolve_model_for_voice(voice)

        # Clamp parameters
        speech_rate = max(0.5, min(2.0, speech_rate))
        pitch_rate = max(0.5, min(2.0, pitch_rate))
        volume = max(0, min(100, volume))

        synth_kwargs = {
            'model': model,
            'voice': voice,
            'speech_rate': speech_rate,
            'pitch_rate': pitch_rate,
            'volume': volume,
        }
        normalized_instruction = self._normalize_cosyvoice_instruction(
            voice, instructions
        )

        # DashScope's CosyVoice SDK names this constructor argument
        # ``instruction`` (singular).  Qwen3's separate API uses
        # ``instructions`` (plural), so keep the provider-specific spelling
        # here instead of forwarding our public parameter name verbatim.
        if normalized_instruction:
            synth_kwargs['instruction'] = normalized_instruction

        logger.info(
            f"CosyVoice synth: model={model}, voice='{voice}' "
            f"(rate={speech_rate}, pitch={pitch_rate}, vol={volume}, "
            f"instr={'yes' if normalized_instruction else 'no'})"
        )
        logger.info(f"Text: {text[:100]}{'...' if len(text) > 100 else ''}")

        synthesizer = SpeechSynthesizer(**synth_kwargs)
        audio_data = synthesizer.call(text)

        request_id = synthesizer.get_last_request_id()
        first_package_delay = synthesizer.get_first_package_delay()

        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, 'wb') as f:
            f.write(audio_data)

        duration = time.time() - start_time
        logger.info(
            f"CosyVoice audio synthesized: request_id={request_id}, "
            f"delay={first_package_delay}ms, total={duration:.2f}s -> {output_path}"
        )
        return output_path, first_package_delay, request_id

    def _normalize_cosyvoice_instruction(
        self, voice: str, instructions: Optional[str]
    ) -> Optional[str]:
        """Convert generic UI directions to the format required by a voice.

        Most instruction-capable CosyVoice models accept natural language.
        A small set of v3 benchmark voices (including ``longanyang`` and
        ``longanhuan``) only accepts one of seven emotion values in a fixed
        Chinese sentence. Passing LumenX's generic ``情绪：…；演绎：…`` string
        makes DashScope fail with engine code 428, so reduce it to the closest
        supported emotion. Unknown directions are omitted rather than making
        the whole dialogue synthesis fail.
        """
        if not instructions or not self._voice_supports_instruction(voice):
            return None

        meta = self._voice_meta(voice)
        if meta.get('instruction_mode') != 'strict_emotion':
            return instructions

        value = instructions.strip().lower()
        canonical = re.fullmatch(
            r"你说话的情感是(neutral|fearful|angry|sad|surprised|happy|disgusted)。",
            value,
        )
        if canonical:
            return f"你说话的情感是{canonical.group(1)}。"

        # Storyboard extraction emits "情绪：…；演绎：…". Prefer the explicit
        # emotion field over delivery keywords when they conflict.
        explicit_emotion = re.search(
            r"(?:情绪|emotion)\s*[:：]\s*([^;；,，\n]+)",
            value,
        )
        emotion_source = explicit_emotion.group(1) if explicit_emotion else value
        emotion_keywords = (
            ('fearful', ('fearful', '恐惧', '害怕', '惊恐', '恐慌', '焦急', '紧张', '不安')),
            ('angry', ('angry', '愤怒', '生气', '恼怒', '气愤')),
            ('sad', ('sad', '悲伤', '难过', '伤心', '哀伤', '沮丧')),
            ('surprised', ('surprised', '惊讶', '吃惊', '震惊', '意外')),
            ('happy', ('happy', '开心', '高兴', '喜悦', '兴奋', '欢快')),
            ('disgusted', ('disgusted', '厌恶', '反感', '恶心', '嫌弃')),
            ('neutral', ('neutral', '平静', '中性', '冷静', '淡定', '平稳')),
        )
        for emotion, keywords in emotion_keywords:
            if any(keyword in emotion_source for keyword in keywords):
                return f"你说话的情感是{emotion}。"

        logger.warning(
            "Ignoring unsupported free-form instruction for strict CosyVoice "
            "voice '%s': %s",
            voice,
            instructions[:100],
        )
        return None

    def _synthesize_qwen3(
        self, text: str, output_path: str, voice: str,
        speech_rate: float = 1.0, instructions: Optional[str] = None,
        model_override: Optional[str] = None,
    ) -> Tuple[str, float, str]:
        """Synthesize using Qwen3-TTS via dashscope.MultiModalConversation.

        Qwen3 has a different API surface than CosyVoice:
          - Endpoint: /services/aigc/multimodal-generation/generation
          - SDK call: MultiModalConversation.call(model=..., text=..., voice=...,
                       language_type=..., instructions=..., stream=False)
          - Returns audio URL (not bytes); we download to output_path.
        """
        import time
        import requests
        import dashscope

        start_time = time.time()
        # Qwen3 doesn't expose per-call rate; speech control happens via
        # `instructions` string (e.g. "语速偏快"). Param kept for signature
        # parity with CosyVoice path.
        _ = speech_rate

        # PR-3h #2: explicit override wins (for custom voices not in registry).
        # Otherwise resolve from registry, then switch to instruct variant
        # when instructions supplied (per doc).
        model = model_override or self._resolve_model_for_voice(voice)
        if instructions and self._voice_supports_instruction(voice):
            if 'instruct' not in model:
                model = 'qwen3-tts-instruct-flash'

        # Detect target language hint from voice metadata if present
        lang_primary = self._voice_meta(voice).get('lang_primary') or ''
        LANG_MAP: dict = {
            'es': 'Spanish', 'ru': 'Russian', 'it': 'Italian',
            'ko': 'Korean', 'ja': 'Japanese', 'de': 'German', 'fr': 'French',
            'pt': 'Portuguese',
        }
        language_type = LANG_MAP.get(lang_primary, 'Chinese')

        params = {
            'api_key': self.api_key,
            'model': model,
            'text': text,
            'voice': voice,
            'language_type': language_type,
            'stream': False,
        }
        if instructions and 'instruct' in model:
            params['instructions'] = instructions
            params['optimize_instructions'] = True

        logger.info(
            f"Qwen3 synth: model={model}, voice='{voice}', "
            f"lang={language_type}, instr={'yes' if instructions else 'no'}"
        )
        logger.info(f"Text: {text[:100]}{'...' if len(text) > 100 else ''}")

        response = dashscope.MultiModalConversation.call(**params)
        if not response or not getattr(response, 'output', None):
            raise RuntimeError(f"Qwen3 TTS empty response: {response}")
        audio = response.output.audio
        if audio is None or not getattr(audio, 'url', None):
            raise RuntimeError(f"Qwen3 TTS no audio URL: {response}")

        # Download the audio file (URL valid 24h)
        audio_resp = requests.get(audio.url, timeout=30)
        audio_resp.raise_for_status()

        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, 'wb') as f:
            f.write(audio_resp.content)

        duration = time.time() - start_time
        request_id = getattr(response, 'request_id', '') or ''
        logger.info(
            f"Qwen3 audio synthesized: request_id={request_id}, "
            f"total={duration:.2f}s -> {output_path}"
        )
        # Qwen3 non-streaming has no first_package_delay; report 0.
        return output_path, 0.0, request_id

    def _voice_meta(self, voice_id: str) -> dict:
        """Return registry metadata for a voice_id (matched via model_id field)."""
        for meta in VOICES.values():
            if meta['model_id'] == voice_id:
                return meta
        return {}

    def _resolve_model_for_voice(self, voice_id: str) -> str:
        """Resolve the correct model for a given voice ID.

        Falls back to self.model if voice is not in the registry (e.g.
        cloned/designed voices, which have voice_id but no static entry).
        """
        meta = self._voice_meta(voice_id)
        return meta.get('model', self.model) if meta else self.model

    def _resolve_family_for_voice(self, voice_id: str) -> str:
        """Resolve voice family: 'cosyvoice' (default) or 'qwen3'.

        Registry metadata `family` field is the source of truth. CosyVoice
        entries pre-PR-3g don't have the field and implicitly belong to
        family='cosyvoice'.
        """
        meta = self._voice_meta(voice_id)
        return meta.get('family', 'cosyvoice')

    def _voice_supports_instruction(self, voice_id: str) -> bool:
        """Whether this voice's model accepts the `instructions` parameter."""
        meta = self._voice_meta(voice_id)
        if 'supports_instruction' in meta:
            return bool(meta['supports_instruction'])
        # Heuristic for legacy CosyVoice entries: v3-flash / v3.5-* support it.
        model = meta.get('model', self.model)
        return any(tag in model for tag in ('v3.5-', 'v3-flash'))

    @staticmethod
    def list_voices():
        """List available voices with metadata"""
        return VOICES
