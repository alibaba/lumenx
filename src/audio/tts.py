"""
Text-to-Speech (TTS) module using DashScope CosyVoice API.
Converts text to speech audio for use in video lip-sync.

Supports cosyvoice-v2 and cosyvoice-v3-flash/v3-plus models.
See: https://help.aliyun.com/zh/model-studio/cosyvoice-python-sdk
"""
import os
import logging
import math
from dataclasses import asdict, dataclass, field
from threading import RLock
from types import MappingProxyType
from typing import Mapping, Optional, Tuple

from src.utils.endpoints import get_provider_base_url

logger = logging.getLogger(__name__)

_DASHSCOPE_TTS_CONSTRUCTOR_LOCK = RLock()
_MISSING = object()
_DEFAULT_DASHSCOPE_WEBSOCKET_API_URL = 'wss://dashscope.aliyuncs.com/api-ws/v1/inference'


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
    'longanyang': {'model_id': 'longanyang', 'name': '龙安阳 (阳光少年)', 'gender': 'Male', 'model': 'cosyvoice-v3-flash'},
    'longanhuan': {'model_id': 'longanhuan', 'name': '龙安欢 (活力女)', 'gender': 'Female', 'model': 'cosyvoice-v3-flash'},
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


TTS_RULES_VERSION = 'tts-parameters-v1'
TTSScalar = str | int | float | bool | None


def _text_value(value, name, *, nonempty=False):
    if not isinstance(value, str) or (nonempty and not value.strip()):
        raise ValueError(f'{name} must be valid text')
    try:
        value.encode('utf-8')
    except UnicodeError as exc:
        raise ValueError(f'{name} must be valid UTF-8') from exc
    return value


def _finite_number(value, name):
    try:
        valid = type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError(f'{name} must be a finite number')
    return value


@dataclass(frozen=True)
class TTSParameterNotice:
    field: str
    reason: str
    requested: TTSScalar
    effective: TTSScalar = None

    def __post_init__(self):
        _text_value(self.field, 'field', nonempty=True)
        _text_value(self.reason, 'reason', nonempty=True)
        for value in (self.requested, self.effective):
            if isinstance(value, str):
                _text_value(value, 'parameter')
            elif value is not None and type(value) is not bool:
                _finite_number(value, 'parameter')


@dataclass(frozen=True)
class PreparedTTS:
    """Local SDK input, not proof of provider effects or a generation receipt.

    Only scalar tuples are retained. Output paths, credentials and response URLs
    belong to execution, never to this persistable value. Use prepare() to build.
    """
    family: str
    parameters: tuple[tuple[str, TTSScalar], ...]
    adjustments: tuple[TTSParameterNotice, ...] = ()
    unsupported: tuple[TTSParameterNotice, ...] = ()
    rules_version: str = field(default=TTS_RULES_VERSION, init=False)

    def __post_init__(self):
        if self.family not in ('cosyvoice', 'qwen3'):
            raise ValueError('Unsupported TTS family')
        parameters = tuple((key, value) for key, value in self.parameters)
        required = {'model', 'voice', 'text'}
        allowed = required | ({'speech_rate', 'pitch_rate', 'volume', 'instruction'}
                              if self.family == 'cosyvoice' else
                              {'language_type', 'stream', 'instructions', 'optimize_instructions'})
        keys = [key for key, _ in parameters]
        if len(set(keys)) != len(keys) or not required <= set(keys) or not set(keys) <= allowed:
            raise ValueError('Invalid prepared TTS parameter fields')
        for key, value in parameters:
            if isinstance(value, str):
                _text_value(value, key)
            elif type(value) is not bool:
                _finite_number(value, key)
        object.__setattr__(self, 'parameters', parameters)
        for name in ('adjustments', 'unsupported'):
            notices = tuple(getattr(self, name))
            if any(type(value) is not TTSParameterNotice for value in notices):
                raise ValueError('Invalid prepared TTS parameter notice')
            object.__setattr__(self, name, notices)

    @property
    def model(self) -> str:
        return dict(self.parameters)['model']

    @property
    def voice(self) -> str:
        return dict(self.parameters)['voice']

    @property
    def text(self) -> str:
        return dict(self.parameters)['text']

    def sdk_kwargs(self) -> dict:
        """Fresh copy; CosyVoice passes text to call, the rest to its constructor."""
        return dict(self.parameters)

    def to_snapshot(self) -> dict:
        return {
            'rules_version': self.rules_version, 'family': self.family,
            'parameters': self.sdk_kwargs(),
            'adjustments': [asdict(value) for value in self.adjustments],
            'unsupported': [asdict(value) for value in self.unsupported],
        }


class TTSProcessor:
    """Text-to-Speech processor using CosyVoice"""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "cosyvoice-v3-flash",
        voice: str = "longanyang",
        *,
        env: Optional[Mapping[str, str]] = None,
    ):
        """
        Initialize TTS processor

        Args:
            api_key: DashScope API key. If None, reads from env when provided,
                otherwise keeps standalone process environment compatibility.
            model: TTS model name (default: cosyvoice-v2)
            voice: Default voice ID (default: longxiaochun_v2)
            env: Optional explicit runtime environment snapshot. env={} is an
                authoritative empty app config and never falls back to os.environ.
        """
        self._env = MappingProxyType(dict(env)) if env is not None else None
        self.api_key = api_key if api_key is not None else self._env_value('DASHSCOPE_API_KEY')
        self.model = model
        self.voice = voice

        logger.info(f"TTS Processor initialized with model={model}, voice={voice}")

    def _env_value(self, key: str, default: Optional[str] = None) -> Optional[str]:
        explicit_env = getattr(self, '_env', None)
        if explicit_env is not None:
            value = explicit_env.get(key)
            return value if value is not None else default
        return os.getenv(key, default)

    def _require_dashscope_api_key(self) -> str:
        api_key = getattr(self, 'api_key', None)
        if api_key:
            return api_key
        if getattr(self, '_env', None) is None:
            import dashscope

            ambient_key = getattr(dashscope, 'api_key', None) or os.getenv('DASHSCOPE_API_KEY')
            if ambient_key:
                return ambient_key
        raise RuntimeError('DASHSCOPE_API_KEY is not configured')

    def _dashscope_tts_websocket_url(self) -> Optional[str]:
        for key in ('DASHSCOPE_TTS_WEBSOCKET_URL', 'DASHSCOPE_WEBSOCKET_URL', 'DASHSCOPE_BASE_WEBSOCKET_API_URL'):
            value = self._env_value(key)
            if value:
                return value
        if getattr(self, '_env', None) is not None:
            return _DEFAULT_DASHSCOPE_WEBSOCKET_API_URL
        return None

    def _dashscope_http_base_address(self) -> Optional[str]:
        if getattr(self, '_env', None) is not None:
            return f"{get_provider_base_url('DASHSCOPE', env=self._env)}/api/v1"
        return None

    def _dashscope_workspace(self) -> Optional[str]:
        value = self._env_value('DASHSCOPE_WORKSPACE')
        return value or None

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
        """Prepare once, then execute; preserve the legacy result tuple.

        Inspect prepare().unsupported before execution when the caller requires
        an explicitly requested control to be supported. Legacy callers retain
        best-effort behavior for unsupported controls.
        """
        prepared = self.prepare(
            text, voice=voice, speech_rate=speech_rate, pitch_rate=pitch_rate,
            volume=volume, instructions=instructions, model_override=model_override,
            family_override=family_override,
        )
        return self.execute(prepared, output_path)

    def prepare(
        self, text: str, voice: Optional[str] = None,
        speech_rate: float = 1.0, pitch_rate: float = 1.0, volume: int = 50,
        instructions: Optional[str] = None, model_override: Optional[str] = None,
        family_override: Optional[str] = None,
    ) -> PreparedTTS:
        """Resolve the existing transports without credentials, I/O or mutation."""
        text = _text_value(text, 'text')
        voice = _text_value(self.voice if voice is None else voice, 'voice', nonempty=True)
        if instructions is not None:
            instructions = _text_value(instructions, 'instructions')
        numbers = {'speech_rate': speech_rate, 'pitch_rate': pitch_rate, 'volume': volume}
        for key, value in numbers.items():
            _finite_number(value, key)
        # Copy registry metadata now; execute must never resolve it again.
        meta = dict(self._voice_meta(voice))
        family = meta.get('family', 'cosyvoice') if family_override is None else family_override
        if family not in ('cosyvoice', 'qwen3'):
            raise ValueError('Unsupported TTS family')
        model = _text_value(
            meta.get('model', self.model) if model_override is None else model_override,
            'model', nonempty=True,
        )
        params = {'model': model, 'voice': voice, 'text': text}
        adjustments, unsupported = [], []
        if family == 'cosyvoice':
            for key, value in numbers.items():
                lower, upper = (0, 100) if key == 'volume' else (0.5, 2.0)
                effective = max(lower, min(upper, value))
                params[key] = effective
                if effective != value:
                    adjustments.append(TTSParameterNotice(key, 'clamped', value, effective))
            # Preserve the known legacy support range, but check the selected
            # model as well as the voice. v3.5 does not support system voices.
            model_supports = model in ('cosyvoice-v3-flash', 'cosyvoice-v3.5-flash', 'cosyvoice-v3.5-plus')
            voice_supports = not meta or (
                meta.get('family', 'cosyvoice') == 'cosyvoice'
                and not model.startswith('cosyvoice-v3.5-')
                and meta.get('supports_instruction', meta.get('model') == 'cosyvoice-v3-flash') is True
            )
            if instructions and model_supports and voice_supports:
                params['instruction'] = instructions
            elif instructions:
                unsupported.append(TTSParameterNotice('instructions', 'voice_or_model_not_supported', instructions))
        else:
            unsupported.extend(TTSParameterNotice(key, 'transport_not_supported', value)
                               for key, value in numbers.items())
            voice_supports = meta.get('family') == 'qwen3' and meta.get('supports_instruction') is True
            if instructions and voice_supports and model_override is None and model == 'qwen3-tts-flash':
                model = 'qwen3-tts-instruct-flash'
                adjustments.append(TTSParameterNotice('model', 'instruction_model', params['model'], model))
                params['model'] = model
            language_map = {
                'es': 'Spanish', 'ru': 'Russian', 'it': 'Italian', 'ko': 'Korean',
                'ja': 'Japanese', 'de': 'German', 'fr': 'French', 'pt': 'Portuguese',
            }
            params.update(language_type=language_map.get(meta.get('lang_primary'), 'Chinese'), stream=False)
            if instructions and voice_supports and model == 'qwen3-tts-instruct-flash':
                params.update(instructions=instructions, optimize_instructions=True)
            elif instructions:
                unsupported.append(TTSParameterNotice('instructions', 'voice_or_model_not_supported', instructions))
        return PreparedTTS(family, tuple(params.items()), tuple(adjustments), tuple(unsupported))

    def execute(self, prepared: PreparedTTS, output_path: str) -> Tuple[str, float, str]:
        """Execute exactly the prepared transport input; only credentials are live."""
        if not isinstance(prepared, PreparedTTS):
            raise ValueError('Expected a prepared TTS input')
        if prepared.family == 'qwen3':
            return self._execute_qwen3(prepared, output_path)
        return self._execute_cosyvoice(prepared, output_path)

    def _execute_cosyvoice(self, prepared: PreparedTTS, output_path: str) -> Tuple[str, float, str]:
        import time
        import dashscope
        from dashscope.audio.tts_v2 import SpeechSynthesizer

        start_time = time.time()
        api_key = self._require_dashscope_api_key()
        kwargs = prepared.sdk_kwargs()
        text = kwargs.pop('text')
        url = self._dashscope_tts_websocket_url()
        if url:
            kwargs['url'] = url
        workspace = self._dashscope_workspace()
        if workspace:
            kwargs['workspace'] = workspace
        logger.info('CosyVoice synth: model=%s, voice=%s', prepared.model, prepared.voice)
        with _DASHSCOPE_TTS_CONSTRUCTOR_LOCK:
            previous_key = getattr(dashscope, 'api_key', _MISSING)
            try:
                dashscope.api_key = api_key
                synthesizer = SpeechSynthesizer(**kwargs)
            finally:
                if previous_key is _MISSING:
                    try:
                        delattr(dashscope, 'api_key')
                    except AttributeError:
                        pass
                else:
                    dashscope.api_key = previous_key
        audio_data = synthesizer.call(text)
        request_id = synthesizer.get_last_request_id()
        first_package_delay = synthesizer.get_first_package_delay()
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, 'wb') as f:
            f.write(audio_data)
        logger.info('CosyVoice audio synthesized: request_id=%s, total=%.2fs -> %s',
                    request_id, time.time() - start_time, output_path)
        return output_path, first_package_delay, request_id

    def _execute_qwen3(self, prepared: PreparedTTS, output_path: str) -> Tuple[str, float, str]:
        import time
        import requests
        import dashscope

        start_time = time.time()
        api_key = self._require_dashscope_api_key()
        params = prepared.sdk_kwargs()
        params['api_key'] = api_key
        base_address = self._dashscope_http_base_address()
        if base_address:
            params['base_address'] = base_address
        workspace = self._dashscope_workspace()
        if workspace:
            params['workspace'] = workspace
        logger.info('Qwen3 synth: model=%s, voice=%s', prepared.model, prepared.voice)
        response = dashscope.MultiModalConversation.call(**params)
        if not response or not getattr(response, 'output', None):
            raise RuntimeError('Qwen3 TTS returned an empty response')
        audio = response.output.audio
        if audio is None or not getattr(audio, 'url', None):
            raise RuntimeError('Qwen3 TTS returned no audio URL')
        audio_resp = requests.get(audio.url, timeout=30)
        audio_resp.raise_for_status()
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, 'wb') as f:
            f.write(audio_resp.content)
        request_id = getattr(response, 'request_id', '') or ''
        logger.info('Qwen3 audio synthesized: request_id=%s, total=%.2fs -> %s',
                    request_id, time.time() - start_time, output_path)
        return output_path, 0.0, request_id

    def _voice_meta(self, voice_id: str) -> dict:
        """Return registry metadata for a voice_id (matched via model_id field)."""
        for meta in VOICES.values():
            if meta['model_id'] == voice_id:
                return meta
        return {}

    @staticmethod
    def list_voices():
        """List available voices with metadata"""
        return VOICES
