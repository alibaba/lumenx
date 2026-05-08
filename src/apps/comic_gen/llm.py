import json
import os
import time
import uuid
import logging
import traceback
import re
from collections import Counter
from typing import List, Dict, Any, Optional, Tuple, Set, Sequence

from .models import (
    Script,
    Character,
    Scene,
    Prop,
    StoryboardFrame,
    GenerationStatus,
    StoryAnalysis,
    StoryBeat,
    CharacterPresenceEntry,
    CharacterRelationshipEdge,
)
from .prompt_recipes import (
    R2V_POLISH_RECIPE,
    STORYBOARD_POLISH_RECIPE,
    VIDEO_POLISH_RECIPE,
)


def _strip_markdown_json(content: str) -> str:
    """Strip markdown code fences from LLM JSON output."""
    if "```json" in content:
        content = content.split("```json")[1].split("```")[0]
    elif "```" in content:
        content = content.split("```")[1].split("```")[0]
    return content.strip()

from ...utils import get_logger

logger = get_logger(__name__)

ENTITY_CATEGORY_KEYS = ("characters", "scenes", "props")

COMMON_CHINESE_SURNAMES = (
    "赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜"
    "戚谢邹喻柏水窦章云苏潘葛奚范彭郎鲁韦昌马苗凤花方俞任袁柳鲍史唐"
    "费廉岑薛雷贺倪汤滕殷罗毕郝安常乐于时傅皮卞齐康伍余元顾孟平黄和"
    "穆萧尹姚邵湛汪祁毛禹狄米贝明臧计伏成戴谈宋茅庞熊纪舒屈项祝董梁"
    "杜阮蓝闵席季麻强贾路娄危江童颜郭梅盛林刁钟徐邱骆高夏蔡田樊胡凌"
    "霍虞万支柯管卢莫经房裘缪干解应宗丁宣贲邓郁单杭洪包诸左石崔吉钮"
    "龚程嵇邢裴陆荣翁荀羊於惠甄曲家封芮羿储靳汲邴糜松井段富巫乌焦巴"
    "弓牧隗山谷车侯宓蓬全郗班仰秋仲伊宫宁仇栾暴甘厉戎祖武符刘景詹束"
    "龙叶幸司韶郜黎蓟薄印宿白怀蒲台从鄂索咸籍赖卓蔺屠蒙池乔阴郁胥能"
    "苍双闻莘党翟谭贡劳逄姬申扶堵冉宰郦雍却璩桑桂濮牛寿通边扈燕冀浦"
    "尚农温别庄晏柴瞿阎充慕连茹习宦艾鱼容向古易慎戈廖庾终暨居衡步都"
    "耿满弘匡国文寇广禄阙东欧殳沃利蔚越夔隆师巩厍聂晁勾敖融冷訾辛阚"
    "那简饶空曾毋沙乜养鞠须丰巢关蒯相查后荆红游竺权逯盖益桓公"
)

CHARACTER_ROLE_WORDS = (
    "守卫", "保安", "司机", "老板", "老板娘", "医生", "护士", "警察", "老师",
    "学生", "同学", "店员", "服务员", "记者", "助理", "管家", "队长", "队员",
    "母亲", "父亲", "妈妈", "爸爸", "奶奶", "爷爷", "阿姨", "叔叔", "哥哥",
    "姐姐", "弟弟", "妹妹", "女人", "男人", "老人", "少年", "少女", "男孩",
    "女孩", "小孩", "孩子", "婴儿", "孕妇", "路人", "室友", "同事", "秘书",
    "经理", "前台", "门卫", "佣人", "师傅", "掌柜", "捕快", "将军", "王爷",
    "皇后", "公主", "皇子", "侍卫", "道士", "和尚"
)

CHARACTER_STOP_PREFIXES = (
    "他", "她", "它", "我", "你", "您", "我们", "你们", "他们", "她们", "它们",
    "自己", "有人", "没人", "众人", "两人", "大家", "对方", "仿佛", "然后", "随后",
    "于是", "不过", "因为", "如果", "虽然", "只是", "还是", "已经", "正在", "继续",
    "立刻", "赶紧", "忽然", "突然",
)

CHARACTER_ACTION_HINTS = (
    "说道", "说", "问", "看", "站", "坐", "走", "跑", "冲", "回", "转", "推", "拿",
    "盯", "笑", "哭", "喊", "叫", "抬", "低", "伸", "摸", "按", "抱", "拉", "握",
    "躺", "靠", "扑", "撞", "醒", "闭", "睁", "尝试", "继续", "诚恳", "窒息", "歪着",
    "歪头", "弯起", "含含糊糊", "心一横", "当场", "仿佛", "打断", "怒道", "喃喃", "回应",
    "轻声", "低声", "端着", "冒头", "示意", "扣回", "看着", "坐下", "穿着", "惊醒", "勾起", "摔倒",
)

CHARACTER_NAME_CUE_WORDS = (
    "微微", "轻轻", "缓缓", "静静", "慢慢", "忽然", "突然", "依旧", "立刻", "转身",
    "抬起", "站起", "走到", "蹲下", "开口", "说道", "问道", "看着", "盯着", "笑着",
    "歪头", "弯起", "伸手", "俯身", "眨", "点头", "沉默", "脸红", "嘴角", "眸子",
    "推开", "拿起", "低头", "抬头", "后退", "深吸", "回应", "喃喃",
)

SCENE_KEYWORDS = (
    "卧室", "房间", "客厅", "厨房", "餐厅", "浴室", "卫生间", "阳台", "天台", "屋顶",
    "楼道", "走廊", "楼梯间", "地下室", "仓库", "车库", "办公室", "会议室", "工位", "实验室",
    "病房", "医院", "诊室", "学校", "教室", "操场", "图书馆", "宿舍", "礼堂", "街道",
    "巷子", "胡同", "十字路口", "桥上", "桥下", "码头", "海边", "沙滩", "河边", "湖边",
    "山林", "森林", "山洞", "洞穴", "寺庙", "祠堂", "宫殿", "庭院", "院子", "村口",
    "田野", "广场", "商场", "便利店", "超市", "酒吧", "咖啡馆", "餐馆", "酒店", "旅馆",
    "地铁站", "站台", "车站", "车厢", "出租车", "公交车", "审讯室", "审查室", "监控室", "警局", "派出所",
    "监狱", "牢房", "门口", "大厅", "荒野", "墓地", "灵堂", "舞台", "后台", "片场"
)

PROP_KEYWORDS = (
    "手机", "纸鹤", "钥匙", "钥匙扣", "箱子", "行李箱", "信封", "纸条", "照片", "相册",
    "相机", "手电", "手枪", "匕首", "短刀", "长刀", "剑", "项链", "吊坠", "戒指",
    "耳环", "手镯", "玉佩", "文件", "档案", "笔记本", "日记本", "地图", "车票", "门卡",
    "证件", "护照", "徽章", "录音笔", "怀表", "手表", "U盘", "硬盘", "电脑", "平板",
    "耳机", "麦克风", "拐杖", "轮椅", "玩偶", "布偶", "伞", "花束", "杯子", "酒杯",
    "茶杯", "药瓶", "试管", "面具", "背包", "书包", "包裹", "快递", "绳子", "锁链",
    "火把", "灯笼", "纸箱", "木箱", "木盒", "首饰盒", "遥控器", "镜子", "手套", "围巾",
    "玉镯", "发卡", "发簪", "琴盒", "吉他", "录音机", "摄像机", "证物袋", "电击器", "蛋糕"
)

TIME_OF_DAY_HINTS = ("凌晨", "清晨", "早晨", "上午", "中午", "午后", "傍晚", "黄昏", "夜晚", "深夜", "雨夜")
LIGHTING_HINTS = ("昏暗", "明亮", "阴冷", "冷光", "暖光", "月光", "灯光", "霓虹", "夕阳", "阳光", "烛光", "逆光")
CLOTHING_HINTS = (
    "外套", "风衣", "雨衣", "夹克", "大衣", "斗篷", "披风", "西装", "制服", "校服",
    "衬衫", "T恤", "毛衣", "卫衣", "裙", "长裙", "短裙", "旗袍", "盔甲", "牛仔裤",
    "长裤", "短裤", "皮靴", "球鞋", "高跟鞋", "围巾", "手套", "帽子"
)
APPEARANCE_HINTS = (
    "短发", "长发", "卷发", "黑发", "白发", "银发", "马尾", "寸头", "清瘦", "瘦削",
    "高挑", "魁梧", "苍白", "疲惫", "警觉", "凌乱", "利落", "稚气", "胡茬", "眼镜",
    "伤疤", "雀斑", "纹身"
)
CHARACTER_ALIAS_ROLE_WORDS = (
    "少女", "少年", "男孩", "女孩", "女人", "男人", "老人", "大叔",
    "阿姨", "叔叔", "哥哥", "姐姐", "弟弟", "妹妹", "室友", "同事",
)
CHARACTER_DESCRIPTOR_PREFIXES = (
    "银发", "白发", "黑发", "长发", "短发", "卷发", "马尾", "寸头",
    "高挑", "清瘦", "瘦削", "苍白", "机械", "金发", "红发",
)
CHARACTER_SUFFIX_STOPWORDS = (
    "赶紧", "差点", "连忙", "立刻", "马上", "微微", "轻轻", "缓缓", "悄悄",
)
CHARACTER_STATE_STOPWORDS = {
    "直勾勾", "平静", "安静", "歪了", "呆呆", "愣愣", "怔怔", "默默", "静静", "空洞", "白皙",
    "轻轻", "缓缓", "慢慢", "冷冷", "死死", "狠狠", "悄悄", "乖乖",
}
CHARACTER_NARRATIVE_STOPWORDS = {
    "那么", "这么", "怎么", "像是", "直接", "轻盈", "温柔", "温顺", "永远", "那我", "机械",
    "但这", "暂时", "可以", "拔腿", "拍手", "红光", "就这么", "下意识", "原来", "仿佛", "只是", "还是", "然后", "终于", "甚至",
    "什么", "哪里", "为何", "忽然", "突然", "时间", "但现", "此时", "一声", "游戏", "干嘛", "安抚",
    "茶香", "琥珀色", "那样", "这绝", "明显是", "何时", "幸福", "方式", "周末", "红瞳", "这么僵", "么多", "明显",
}
CHARACTER_BODY_PART_WORDS = (
    "眼睛", "双目", "双腿", "双手", "左眼", "右眼", "眼神", "腿", "手", "手指", "瞳孔", "嘴角",
    "唇角", "鼻尖", "发丝", "指尖", "五指", "脸颊", "腰肢", "凤眼", "纤手",
)
CHARACTER_PRIMARY_SUFFIX_FRAGMENTS = ("现", "惨", "笑", "哭", "道", "问", "说", "喊", "叫")
SCENE_DESCRIPTOR_PREFIXES = (
    "昏暗", "明亮", "冰冷", "温暖", "安静", "空荡", "狭小", "狭窄", "纯白", "科幻", "极简",
)
SCENE_PARTICLE_PREFIXES = ("着", "朝", "往", "向", "从", "到", "进", "出", "回", "去", "来")
PROP_ACTION_PREFIXES = (
    "端起", "接过", "递来", "递上", "递给", "递出", "拿起", "拿着", "拿出", "端着", "举着",
    "捧着", "抱着", "握着", "抓着", "提着", "拎着", "放下", "放在", "扣回", "送来", "接住",
)
STORY_SHIFT_MARKERS = (
    "次日", "第二天", "翌日", "当天晚上", "当晚", "夜里", "深夜", "凌晨",
    "与此同时", "另一边", "同一时间", "片刻后", "不久后", "几分钟后", "半小时后",
    "回到", "来到", "赶到", "转场", "画面切到",
)
ENTITY_STOPWORDS = {
    "自己", "他们", "她们", "我们", "你们", "有人", "那人", "这人", "其中", "这里", "那里",
    "时候", "地方", "东西", "声音", "目光", "电话", "系统", "画外音", "旁白", "镜头",
    "场景", "人物", "角色", "道具", "如果", "但是", "于是", "然后", "终于", "突然", "作者", "主人", "没有",
}

DETERMINER_PREFIX_RE = re.compile(r"^(?:一[位名个只把张条间座家栋封串辆件口把]|这[位个只把张条间座家栋封串辆件]|那[位个只把张条间座家栋封串辆件])")
CHAPTER_HEADING_RE = re.compile(
    r"^(?:#+\s*)?第[一二三四五六七八九十百零\d]+(?:章|卷|集|回|节|幕|篇)(?:[:：\s-]*.+)?$"
)
SCENE_HEADING_RE = re.compile(
    r"^(?:\d+(?:-\d+)?\s+.+(?:\[[^\]]+\]\s*)*|第[一二三四五六七八九十百零\d]+场[:：]?\s*.+|(?:场景|地点)[:：].+)$"
)
DIALOGUE_LINE_RE = re.compile(
    r"^([A-Za-z\u4e00-\u9fff]{1,12})(?:（[^）]{0,12}）|\([^)]{0,12}\))?[:：]\s*(.+)$"
)
INLINE_QUOTE_RE = re.compile(r"[“\"]([^”\"]{2,40})[”\"]")

# ── Default system prompts for polish/refine stages ──────────────────────
# These are the built-in defaults. Users can override per-project via PromptConfig.
# Placeholders: {ASSETS} = asset context, {DRAFT} = draft prompt, {SLOTS} = R2V slot context

DEFAULT_STORYBOARD_POLISH_PROMPT = f"""
# ROLE
You are a senior storyboard artist and image prompt engineer.
Your task is to rewrite a draft into a production-ready multi-reference image prompt.

# CONTEXT
The user has selected reference assets that must be cited by Image ID.
You must explicitly mention Image X when grounding characters, scenes, or props.

# AVAILABLE ASSETS
{{ASSETS}}

# RECIPE
{STORYBOARD_POLISH_RECIPE}

# RULES
1. Keep the original narrative intent exact. Do not invent new plot beats, emotions, or props unless they are already implied by the draft.
2. Explicitly bind every reused asset to its Image ID, but write the final prompt as natural visual prose instead of a bullet list.
3. Favor concrete visual facts: pose, blocking, wardrobe, light direction, atmosphere, lens feeling, material detail, and spatial relationships.
4. Preserve cross-image consistency. If a character or prop already exists in the assets, keep the same appearance, costume, silhouette, and design language.
5. Avoid keyword spam, vague praise, or contradictory instructions.
6. Return only the JSON object.

# OUTPUT FORMAT
Return STRICTLY a JSON object:
{{
    "prompt_cn": "中文图像提示词，包含 Image X 引用，适合直接生图",
    "prompt_en": "English image-generation prompt with Image X references, concise and cinematic"
}}

# USER DRAFT PROMPT
{{DRAFT}}
""".strip()

DEFAULT_VIDEO_POLISH_PROMPT = f"""
You are an expert video prompt engineer specializing in Seedance-class image-to-video models.

RECIPE:
{VIDEO_POLISH_RECIPE}

RULES:
1. Rewrite the draft into a short, high-control video prompt that is easy for a modern I2V model to follow.
2. Keep the prompt visually grounded in the source frame. Do not introduce a new location, new character, or impossible action unless the draft asks for it.
3. When the draft is sparse, strengthen motion, camera, lighting, and texture detail, but keep a single clear dramatic focus.
4. Prefer camera language that is explicit and filmable: static, slow push-in, dolly, pan, tilt, handheld drift, arc, whip pan, overhead, close-up, medium shot, wide shot.
5. Prefer action language with tempo and physical feedback: glance, exhale, turn, step forward, fabric sways, dust lifts, reflections ripple, sparks scatter.
6. Avoid stacking too many styles, conflicting camera moves, or more than one dominant action line.
7. Return only the JSON object.

OUTPUT FORMAT:
Return STRICTLY a JSON object:
{{
    "prompt_cn": "润色后的中文视频提示词，强调动作时间线、镜头、质感与约束",
    "prompt_en": "Polished English video prompt optimized for Seedance-style I2V control"
}}
""".strip()

DEFAULT_R2V_POLISH_PROMPT = f"""
# Role
You are a prompt engineer for reference-to-video generation.

# Context
The model must respect uploaded reference videos and keep character continuity.
Available reference slots:
{{SLOTS}}

# Recipe
{R2V_POLISH_RECIPE}

# Rules
1. Replace character names with character1 / character2 / character3 only. Do not invent character4 or unnamed extras.
2. Keep the original emotional tone, scene goal, and dialogue meaning.
3. Make actions stageable and ordered. The prompt should read like an actionable shot direction rather than a vague story summary.
4. If the draft includes dialogue, format it as: characterN says: "dialogue content"
5. Respect reference continuity: same identity, costume logic, screen direction, and interaction relationships.
6. Return only the JSON object.

# Output Format
Return STRICTLY a JSON object:
{{
    "prompt_cn": "润色后的中文提示词，使用 character1/character2/character3 并强调动作顺序与镜头",
    "prompt_en": "Polished English R2V prompt using character1/character2/character3 with clear blocking and camera direction"
}}
""".strip()

class ScriptProcessor:
    def __init__(self, api_key: str = None):
        self._api_key = api_key
        from .llm_adapter import LLMAdapter
        self.llm = LLMAdapter()

    @property
    def is_configured(self):
        return self.llm.is_configured

    def _build_generation_meta(
        self,
        source: str,
        *,
        degraded: bool = False,
        reason: str = "",
        details: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        meta: Dict[str, Any] = {
            "source": source,
            "degraded": degraded,
        }
        if reason:
            meta["reason"] = reason
        if details:
            meta.update(details)
        return meta

    def _build_prompt_result(
        self,
        prompt_cn: str,
        prompt_en: str,
        *,
        source: str,
        degraded: bool,
        reason: str = "",
    ) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "prompt_cn": prompt_cn,
            "prompt_en": prompt_en,
            "generation_source": source,
            "generation_degraded": degraded,
        }
        if reason:
            result["generation_reason"] = reason
        return result

    def parse_novel(self, title: str, text: str) -> Script:
        """
        Parses the raw novel text into a structured Script object using an LLM.
        """
        logger.info(f"Parsing novel: {title}...")
        
        if not self.is_configured:
             logger.error("LLM API key not configured.")
             raise ValueError("LLM API Key 未配置。请在 API 配置中设置对应的 API Key 后重试。")

        prompt = self._construct_prompt(text)

        try:
            content = self.llm.chat(
                messages=[
                    {
                        "role": "system",
                        "content": "你是一名影视开发实体提取器。你只能返回严格的 JSON 对象，不要返回解释、markdown 或额外文字。",
                    },
                    {"role": "user", "content": prompt},
                ],
                response_format={"type": "json_object"},
            )
            logger.debug(f"LLM Response Content:\n{content}")

            content = _strip_markdown_json(content)
            data = self._normalize_entity_payload(json.loads(content))
            data, filled_categories = self._fill_missing_entities_with_fallback(text, data)
            if filled_categories:
                logger.warning(
                    "LLM entity extraction returned incomplete result. Heuristic fallback filled: %s",
                    ", ".join(filled_categories),
                )
            return self._create_script_from_data(
                title,
                text,
                data,
                generation_metadata={
                    "novel_parse": self._build_generation_meta(
                        "llm",
                        degraded=bool(filled_categories),
                        reason="Heuristic fallback filled missing categories" if filled_categories else "",
                        details={"filled_categories": filled_categories},
                    )
                },
            )
                
        except json.JSONDecodeError as e:
            logger.warning("LLM entity extraction returned invalid JSON, switching to heuristic fallback: %s", e)
            fallback_script = self._build_fallback_script(title, text, f"JSON 解析失败: {e}")
            if fallback_script is not None:
                return fallback_script
            error_msg = f"LLM 返回的数据格式错误，无法解析 JSON: {e}"
            logger.error(error_msg, exc_info=True)
            raise RuntimeError(error_msg)
        except ValueError:
            # Re-raise ValueError (e.g., API key not set)
            raise
        except Exception as e:
            logger.warning("LLM entity extraction failed, switching to heuristic fallback: %s", e)
            fallback_script = self._build_fallback_script(title, text, str(e))
            if fallback_script is not None:
                return fallback_script
            error_msg = f"剧本解析失败: {str(e)}"
            logger.error(error_msg, exc_info=True)
            raise RuntimeError(error_msg)

    def _build_fallback_script(self, title: str, text: str, reason: str) -> Optional[Script]:
        data = self._fallback_extract_entities(text)
        if self._has_meaningful_entity_payload(data):
            logger.warning("Using heuristic entity fallback for %s because %s", title, reason)
            return self._create_script_from_data(
                title,
                text,
                data,
                generation_metadata={
                    "novel_parse": self._build_generation_meta(
                        "heuristic_fallback",
                        degraded=True,
                        reason=reason,
                    )
                },
            )
        return None

    def _normalize_entity_payload(self, data: Any) -> Dict[str, Any]:
        normalized: Dict[str, Any] = {"frames": []}
        source = data if isinstance(data, dict) else {}

        for key in ENTITY_CATEGORY_KEYS:
            value = source.get(key)
            normalized[key] = value if isinstance(value, list) else []

        if isinstance(source.get("frames"), list):
            normalized["frames"] = source["frames"]
        return normalized

    def _entity_items_have_names(self, items: Any) -> bool:
        if not isinstance(items, list):
            return False
        for item in items:
            if isinstance(item, dict) and str(item.get("name", "")).strip():
                return True
        return False

    def _has_meaningful_entity_payload(self, data: Dict[str, Any]) -> bool:
        return any(self._entity_items_have_names(data.get(key, [])) for key in ENTITY_CATEGORY_KEYS)

    def _fill_missing_entities_with_fallback(self, text: str, data: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
        normalized = self._normalize_entity_payload(data)
        fallback = self._fallback_extract_entities(text)
        filled_categories: List[str] = []

        for key in ENTITY_CATEGORY_KEYS:
            if self._entity_items_have_names(normalized.get(key, [])):
                continue
            fallback_items = fallback.get(key, [])
            if self._entity_items_have_names(fallback_items):
                normalized[key] = fallback_items
                filled_categories.append(f"{key}={len(fallback_items)}")

        return normalized, filled_categories

    def _fallback_extract_entities(self, text: str) -> Dict[str, Any]:
        normalized_text = str(text or "").strip()
        segments = self._split_text_segments(normalized_text)
        characters = self._extract_fallback_characters(normalized_text, segments)
        known_character_names = [str(item.get("name", "")).strip() for item in characters if str(item.get("name", "")).strip()]
        scenes = self._extract_fallback_scenes(normalized_text, segments, known_character_names)
        props = self._extract_fallback_props(normalized_text, segments, known_character_names)

        return {
            "characters": characters,
            "scenes": scenes,
            "props": props,
            "frames": [],
        }

    def _split_text_segments(self, text: str) -> List[str]:
        if not text:
            return []
        raw_segments = re.split(r"(?<=[。！？!?；;])|\n+", text)
        return [segment.strip() for segment in raw_segments if segment and segment.strip()]

    def _clean_entity_name(self, name: str, *, is_scene: bool = False) -> str:
        cleaned = str(name or "").strip()
        cleaned = cleaned.strip("“”\"'`·()[]（）<>《》【】,，。！？!?:：；;、 ")
        cleaned = DETERMINER_PREFIX_RE.sub("", cleaned)
        cleaned = re.sub(r"^(?:在|从|向|回到|来到|走进|进入|站在|躲进|躺在|坐在|冲到)", "", cleaned)
        cleaned = re.sub(r"(?:里|中|上|下|前|后|旁|内|外)$", "", cleaned) if is_scene else cleaned
        return cleaned.strip("，。！？!?:：；;、 ")

    def _dedupe_entities_by_name(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        deduped: List[Dict[str, Any]] = []
        seen: Set[str] = set()
        for item in items:
            name = self._clean_entity_name(item.get("name", ""), is_scene=item.get("_scene", False))
            if not name or name in seen:
                continue
            seen.add(name)
            normalized_item = dict(item)
            normalized_item["name"] = name
            normalized_item.pop("_scene", None)
            deduped.append(normalized_item)
        return deduped

    def _collect_context(self, segments: List[str], token: str, limit: int = 2) -> str:
        matches: List[str] = []
        for segment in segments:
            if token and token in segment:
                matches.append(segment)
            if len(matches) >= limit:
                break
        return " ".join(matches).strip()

    def _collect_candidate_windows(self, text: str, token: str, window: int = 6, limit: int = 3) -> List[str]:
        snippets: List[str] = []
        if not text or not token:
            return snippets

        start = 0
        while len(snippets) < limit:
            index = text.find(token, start)
            if index < 0:
                break
            left = max(0, index - window)
            right = min(len(text), index + len(token) + window)
            snippet = text[left:right].strip()
            if snippet:
                snippets.append(snippet)
            start = index + len(token)
        return snippets

    def _truncate_text(self, text: str, max_length: int = 40) -> str:
        cleaned = re.sub(r"\s+", " ", str(text or "").strip())
        if len(cleaned) <= max_length:
            return cleaned
        return cleaned[: max_length - 1].rstrip("，、 ") + "…"

    def _extract_script_line_candidates(self, text: str) -> List[str]:
        candidates: List[str] = []
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue

            people_match = re.match(r"^(?:人物|角色)[:：]\s*(.+)$", line)
            if people_match:
                for token in re.split(r"[，、,/／\s]+", people_match.group(1).strip()):
                    cleaned = self._normalize_character_candidate(token)
                    if cleaned:
                        candidates.append(cleaned)

            dialogue_match = re.match(r"^(.+?)(?:（[^）]*）|\([^)]*\))?[:：]", line)
            if dialogue_match:
                prefix = dialogue_match.group(1).strip()
                leading_name = re.match(r"^([A-Za-z\u4e00-\u9fff]{2,6}?)(?=(?:在|向|朝|对|看|问|说|道|喊|叫|冒头|开口|回应|低声|轻声|喃喃|怒道))", prefix)
                cleaned = self._normalize_character_candidate(leading_name.group(1) if leading_name else prefix)
                if cleaned:
                    candidates.append(cleaned)
        return candidates

    def _normalize_character_candidate(self, name: str) -> str:
        cleaned = self._clean_entity_name(name)
        if not cleaned:
            return ""

        removable_suffixes = tuple(
            dict.fromkeys(sorted(CHARACTER_ACTION_HINTS + CHARACTER_NAME_CUE_WORDS + CHARACTER_SUFFIX_STOPWORDS + tuple(CHARACTER_STATE_STOPWORDS), key=len, reverse=True))
        )
        removable_prefixes = tuple(
            dict.fromkeys(sorted(CHARACTER_STOP_PREFIXES + CHARACTER_ACTION_HINTS + CHARACTER_NAME_CUE_WORDS + CHARACTER_SUFFIX_STOPWORDS + tuple(CHARACTER_STATE_STOPWORDS), key=len, reverse=True))
        )
        cleaned = re.sub(r"^(?:第[一二三四五六七八九十百零\d]+[章节卷集回幕篇节])", "", cleaned)
        cleaned = re.sub(r"^(?:而|但|可|却|并|又|还|便|再|刚刚|刚|正|就|将|把|向|朝|对|与|跟|那|这)", "", cleaned)

        changed = True
        while cleaned and changed:
            changed = False
            for prefix in removable_prefixes:
                if cleaned.startswith(prefix) and len(cleaned) > len(prefix):
                    cleaned = cleaned[len(prefix) :]
                    changed = True
                    break
            for suffix in removable_suffixes:
                if cleaned.endswith(suffix) and len(cleaned) > len(suffix):
                    cleaned = cleaned[: -len(suffix)]
                    changed = True
                    break
            stripped = re.sub(r"^(?:着|被|在|于|因)", "", cleaned)
            if stripped != cleaned:
                cleaned = stripped
                changed = True
            stripped = re.sub(r"(?:刚刚|刚|正|又|还|便|就|将|把|向|朝|对|与|跟|地|在|被|着|了|吗|嘛|呢|啊)$", "", cleaned)
            if stripped != cleaned:
                cleaned = stripped
                changed = True

        cleaned = re.sub(
            rf"(?:{'|'.join(sorted((re.escape(item) for item in CHARACTER_STATE_STOPWORDS), key=len, reverse=True))})$",
            "",
            cleaned,
        )
        if cleaned.endswith("猛") and len(cleaned) > 2:
            cleaned = cleaned[:-1]

        return self._clean_entity_name(cleaned)

    def _is_valid_character_name(self, name: str) -> bool:
        cleaned = self._normalize_character_candidate(name)
        if not cleaned or len(cleaned) < 2 or len(cleaned) > 6:
            return False
        if cleaned in ENTITY_STOPWORDS:
            return False
        if cleaned in CHARACTER_NARRATIVE_STOPWORDS or any(token in cleaned for token in CHARACTER_NARRATIVE_STOPWORDS):
            return False
        if cleaned in CHARACTER_STATE_STOPWORDS or any(token in cleaned for token in CHARACTER_STATE_STOPWORDS):
            return False
        if cleaned in CHARACTER_STOP_PREFIXES:
            return False
        if any(cleaned.startswith(prefix) for prefix in CHARACTER_STOP_PREFIXES):
            return False
        if cleaned.startswith(("这么", "那么", "这样", "那样")):
            return False
        if any(keyword in cleaned for keyword in SCENE_KEYWORDS):
            return False
        if any(keyword in cleaned for keyword in PROP_KEYWORDS):
            return False
        if any(keyword in cleaned for keyword in CLOTHING_HINTS):
            return False
        if any(keyword in cleaned for keyword in CHARACTER_BODY_PART_WORDS):
            return False
        if any(action in cleaned for action in CHARACTER_ACTION_HINTS):
            return False
        if any(cue in cleaned for cue in CHARACTER_NAME_CUE_WORDS):
            return False
        if any(cleaned == f"{fragment}{role}" for fragment in ("发", "色", "头", "着") for role in CHARACTER_ALIAS_ROLE_WORDS):
            return False
        if any(pronoun in cleaned for pronoun in ("她", "他")):
            return False
        if any(char.isdigit() for char in cleaned):
            return False
        if cleaned.startswith(("着", "被")):
            return False
        if len(cleaned) == 2 and cleaned[0] == cleaned[1]:
            return False
        if "的" in cleaned or cleaned.endswith(("时候", "地方", "东西", "画面", "声音", "一下", "起来", "下去", "过去", "过来")):
            return False
        if cleaned.endswith(("前", "后", "里", "外", "边", "上", "下", "在", "被", "着", "吗", "嘛", "呢", "啊")):
            return False
        if any(token in cleaned for token in ("没有", "最终", "最后", "继续")):
            return False
        if any(token in cleaned for token in ("正在", "继续", "立刻", "忽然", "突然", "仿佛", "然后", "已经", "还是")):
            return False
        if len(cleaned) > 4 and cleaned not in CHARACTER_ROLE_WORDS and not any(cleaned.endswith(role) for role in CHARACTER_ROLE_WORDS):
            return False
        return bool(re.fullmatch(r"[A-Za-z\u4e00-\u9fff]+", cleaned))

    def _looks_like_character_alias_candidate(self, name: str) -> bool:
        cleaned = self._normalize_character_candidate(name)
        if not cleaned:
            return False
        if cleaned in CHARACTER_ALIAS_ROLE_WORDS:
            return True
        return any(
            cleaned.endswith(role) and len(cleaned) > len(role)
            for role in CHARACTER_ALIAS_ROLE_WORDS
        )

    def _is_probable_named_character(self, profile: Dict[str, Any]) -> bool:
        name = str(profile.get("name", "") or "").strip()
        sources = set(profile.get("sources", set()) or set())

        if not name:
            return False
        if sources & {"script", "named", "surname"}:
            if name.endswith(CHARACTER_PRIMARY_SUFFIX_FRAGMENTS + ("时", "末", "式")):
                return False
            return True
        if "leading" in sources and len(name) in (2, 3) and not name.endswith(("是", "现", "惨")):
            return True
        if name.startswith(("小", "阿", "老")):
            return True
        if name.endswith(("先生", "小姐", "女士", "老师", "医生")):
            return True
        if len(name) in (2, 3) and name[0] in COMMON_CHINESE_SURNAMES:
            if name.endswith(CHARACTER_PRIMARY_SUFFIX_FRAGMENTS + ("时", "末", "式")):
                return False
            return True
        return False

    def _extract_character_descriptor_tokens(self, text: str) -> Set[str]:
        source = str(text or "")
        tokens = {token for token in APPEARANCE_HINTS if token in source}
        tokens.update(token for token in CHARACTER_DESCRIPTOR_PREFIXES if token in source)
        tokens.update(token for token in CHARACTER_ALIAS_ROLE_WORDS if token in source)
        return tokens

    def _infer_alias_gender(self, name: str) -> str:
        cleaned = self._normalize_character_candidate(name)
        if not cleaned:
            return ""
        female_tokens = ("少女", "女孩", "女人", "姐姐", "妹妹", "阿姨", "妈妈", "公主", "皇后")
        male_tokens = ("少年", "男孩", "男人", "大叔", "叔叔", "哥哥", "弟弟", "爸爸", "爷爷", "皇子")
        if any(token in cleaned for token in female_tokens):
            return "女"
        if any(token in cleaned for token in male_tokens):
            return "男"
        return ""

    def _score_character_alias_target(
        self,
        alias_profile: Dict[str, Any],
        target_profile: Dict[str, Any],
    ) -> int:
        if alias_profile["name"] == target_profile["name"]:
            return 0

        score = 0
        distance = abs(alias_profile["first_position"] - target_profile["first_position"])
        if distance <= 120:
            score += 4
        elif distance <= 360:
            score += 3
        elif distance <= 800:
            score += 2
        elif distance <= 1600:
            score += 1

        shared_descriptors = alias_profile["descriptor_tokens"] & target_profile["descriptor_tokens"]
        if shared_descriptors:
            score += min(6, len(shared_descriptors) * 2)

        for segment in alias_profile["contexts"]:
            if target_profile["name"] in segment:
                score += 1
                if re.search(
                    rf"{re.escape(alias_profile['name'])}.*(?:看向|望向|对着|朝着|盯着|看着|问|喊|叫).*{re.escape(target_profile['name'])}",
                    segment,
                ):
                    score -= 9
                between_match = re.search(
                    rf"{re.escape(target_profile['name'])}([^\n。！？]{{0,16}}){re.escape(alias_profile['name'])}",
                    segment,
                )
                if between_match and not re.search(r"(?:看|盯|望|瞥|瞅|瞄|朝|向|对)", between_match.group(1)):
                    score += 10
                continue
            if target_profile["descriptor_tokens"] and target_profile["descriptor_tokens"] & self._extract_character_descriptor_tokens(segment):
                score += 2

        alias_gender = alias_profile["gender"] or self._infer_alias_gender(alias_profile["name"])
        target_gender = target_profile["gender"] or self._infer_alias_gender(target_profile["name"])
        if alias_gender and target_gender and alias_gender == target_gender:
            score += 1

        return score

    def _looks_like_story_shift_paragraph(self, paragraph: str) -> bool:
        cleaned = str(paragraph or "").strip().strip("[]【】()（）")
        if not cleaned or len(cleaned) > 36:
            return False
        if CHAPTER_HEADING_RE.match(cleaned) or SCENE_HEADING_RE.match(cleaned):
            return True
        if DIALOGUE_LINE_RE.match(cleaned):
            return False
        if any(cleaned.startswith(marker) for marker in STORY_SHIFT_MARKERS):
            return True
        if any(time_hint in cleaned for time_hint in TIME_OF_DAY_HINTS) and any(scene_hint in cleaned for scene_hint in SCENE_KEYWORDS):
            return True
        return False

    def _register_ranked_candidate(
        self,
        scores: Counter,
        first_positions: Dict[str, int],
        text: str,
        candidate: str,
        score: int,
    ) -> None:
        normalized = str(candidate or "").strip()
        if not normalized:
            return

        scores[normalized] += score
        position = text.find(normalized)
        if position < 0:
            position = len(text) + len(first_positions)
        previous_position = first_positions.get(normalized)
        if previous_position is None or position < previous_position:
            first_positions[normalized] = position

    def _rank_candidates(
        self,
        scores: Counter,
        first_positions: Dict[str, int],
        text: str,
        *,
        min_score: int = 1,
        max_items: int = 8,
    ) -> List[str]:
        ranked: List[Tuple[str, int, int, int]] = []
        for name, score in scores.items():
            if score < min_score:
                continue
            mention_count = text.count(name)
            ranked.append((name, score, mention_count, first_positions.get(name, len(text))))

        ranked.sort(key=lambda item: (-item[1], -item[2], item[3], len(item[0])))
        return [name for name, _, _, _ in ranked[:max_items]]

    def _extract_fallback_characters(self, text: str, segments: List[str]) -> List[Dict[str, Any]]:
        if not text:
            return []

        ordered_names: Counter = Counter()
        first_positions: Dict[str, int] = {}
        candidate_sources: Dict[str, Set[str]] = {}

        def add_character(raw_name: str, score: int, source: str) -> None:
            normalized = self._normalize_character_candidate(raw_name)
            if not self._is_valid_character_name(normalized):
                return
            self._register_ranked_candidate(ordered_names, first_positions, text, normalized, score)
            candidate_sources.setdefault(normalized, set()).add(source)

        for candidate in self._extract_script_line_candidates(text):
            add_character(candidate, 8, "script")

        action_pattern = "|".join(sorted((re.escape(item) for item in CHARACTER_ACTION_HINTS), key=len, reverse=True))
        cue_pattern = "|".join(sorted((re.escape(item) for item in CHARACTER_NAME_CUE_WORDS), key=len, reverse=True))
        state_pattern = "|".join(sorted((re.escape(item) for item in CHARACTER_STATE_STOPWORDS), key=len, reverse=True))
        action_with_state_pattern = rf"(?:(?:{state_pattern})地?)?(?:{action_pattern})" if state_pattern else action_pattern

        for segment in segments:
            leading_match = re.match(
                rf"^([\u4e00-\u9fff]{{2,4}}?)(?=(?:在|正|刚|把|将|向|朝|对|从|跟|和|{action_with_state_pattern}|{cue_pattern}))",
                segment,
            )
            if leading_match:
                add_character(leading_match.group(1), 4, "leading")

        for match in re.finditer(rf"([\u4e00-\u9fff]{{2,4}}?)(?=(?:{action_with_state_pattern}))", text):
            add_character(match.group(1), 3, "action")

        for match in re.finditer(rf"([\u4e00-\u9fff]{{2,4}}?)(?=(?:{cue_pattern}))", text):
            add_character(match.group(1), 2, "cue")

        for match in re.finditer(r"([\u4e00-\u9fff]{2,4}?)(?=(?:把|将))", text):
            add_character(match.group(1), 2, "object")

        for match in re.finditer(
            rf"([{COMMON_CHINESE_SURNAMES}][\u4e00-\u9fff]{{1,2}})(?=(?:{action_pattern}|{cue_pattern}|[，。！？；、“”\"'：:）])|$)",
            text,
        ):
            score = 2 if text.count(match.group(1)) > 1 else 1
            add_character(match.group(1), score, "surname")

        for match in re.finditer(r"(?:叫|名叫|名为|名字是)([\u4e00-\u9fff]{2,4})", text):
            add_character(match.group(1), 5, "named")

        for role in CHARACTER_ROLE_WORDS:
            pattern = rf"((?:老|小|阿)?{re.escape(role)})"
            for match in re.finditer(pattern, text):
                add_character(match.group(1), 1, "role")

        sorted_profiles: List[Dict[str, Any]] = []
        for name, score in ordered_names.items():
            mention_count = text.count(name)
            contexts = [segment for segment in segments if name in segment][:3]
            context_seed = " ".join(contexts).strip() or text[:120]
            local_windows = self._collect_candidate_windows(text, name)
            local_context = " ".join(local_windows).strip() or context_seed
            role_like = name in CHARACTER_ROLE_WORDS or any(name.endswith(role) for role in CHARACTER_ROLE_WORDS)
            alias_like = self._looks_like_character_alias_candidate(name)
            descriptor_tokens = self._extract_character_descriptor_tokens(f"{name} {local_context}")
            if score < 2 and mention_count < 2 and not (alias_like and descriptor_tokens):
                continue

            sorted_profiles.append(
                {
                    "name": name,
                    "score": score,
                    "mention_count": mention_count,
                    "first_position": first_positions.get(name, len(text)),
                    "sources": candidate_sources.get(name, set()),
                    "contexts": contexts,
                    "local_context": local_context,
                    "role_like": role_like,
                    "alias_like": alias_like,
                    "clothing": self._extract_clothing_hint(local_context),
                    "age": self._infer_age_hint(local_context),
                    "gender": self._infer_gender_hint(name, local_context),
                    "descriptor_tokens": descriptor_tokens,
                    "aliases": [],
                }
            )

        sorted_profiles.sort(
            key=lambda item: (-item["score"], -item["mention_count"], item["first_position"], len(item["name"]))
        )

        primary_profiles = [
            profile for profile in sorted_profiles
            if not profile["role_like"] and not profile["alias_like"] and self._is_probable_named_character(profile)
        ]
        if not primary_profiles and sorted_profiles:
            primary_profiles = sorted_profiles[:1]

        primary_lookup = {profile["name"]: profile for profile in primary_profiles}
        kept_profiles: List[Dict[str, Any]] = []

        for profile in sorted_profiles:
            if profile["name"] in primary_lookup:
                continue
            if any(
                profile["name"].startswith(primary_name)
                and len(profile["name"]) > len(primary_name)
                and profile["name"][len(primary_name) :] in CHARACTER_PRIMARY_SUFFIX_FRAGMENTS
                for primary_name in primary_lookup
            ):
                continue

            if profile["alias_like"]:
                role_descriptors = {
                    role for role in CHARACTER_ALIAS_ROLE_WORDS if role in profile["name"]
                }
                if (
                    role_descriptors
                    and "script" not in profile["sources"]
                    and profile["descriptor_tokens"].issubset(role_descriptors)
                ):
                    continue

                ranked_targets = sorted(
                    (
                        (self._score_character_alias_target(profile, target), target)
                        for target in primary_profiles
                    ),
                    key=lambda item: (-item[0], item[1]["first_position"]),
                )
                if ranked_targets:
                    best_score, best_target = ranked_targets[0]
                    second_score = ranked_targets[1][0] if len(ranked_targets) > 1 else None
                    has_positive_anchor = any(
                        re.search(
                            rf"{re.escape(best_target['name'])}([^\n。！？]{{0,16}}){re.escape(profile['name'])}",
                            segment,
                        )
                        and not re.search(
                            r"(?:看|盯|望|瞥|瞅|瞄|朝|向|对)",
                            re.search(
                                rf"{re.escape(best_target['name'])}([^\n。！？]{{0,16}}){re.escape(profile['name'])}",
                                segment,
                            ).group(1),
                        )
                        for segment in profile["contexts"]
                    )
                    shared_descriptors = bool(profile["descriptor_tokens"] & best_target["descriptor_tokens"])
                    if (
                        best_score >= 6
                        and (second_score is None or best_score - second_score >= 2)
                        and (has_positive_anchor or shared_descriptors)
                    ):
                        best_target["aliases"] = list(dict.fromkeys(best_target["aliases"] + [profile["name"]]))
                        best_target["contexts"] = list(dict.fromkeys(best_target["contexts"] + profile["contexts"]))
                        best_target["descriptor_tokens"].update(profile["descriptor_tokens"])
                        continue

                if "script" not in profile["sources"]:
                    continue

            if profile["role_like"] and profile["score"] < 5 and profile["mention_count"] < 3 and "script" not in profile["sources"]:
                continue

            if not profile["alias_like"] and not profile["role_like"] and not self._is_probable_named_character(profile):
                continue

            kept_profiles.append(profile)

        final_profiles = (primary_profiles + kept_profiles)[:10]

        entities: List[Dict[str, Any]] = []
        for index, profile in enumerate(final_profiles, start=1):
            if not self._is_valid_character_name(profile["name"]):
                continue
            if profile["name"] in CHARACTER_NARRATIVE_STOPWORDS or any(
                token in profile["name"] for token in CHARACTER_NARRATIVE_STOPWORDS
            ):
                continue
            local_context = profile.get("local_context", "") or " ".join(profile["contexts"]).strip()
            context = " ".join(
                dict.fromkeys([profile["name"], local_context, *profile["aliases"], *profile["contexts"][:1]])
            ).strip() or text[:80]
            clothing = profile["clothing"] or self._extract_clothing_hint(local_context)
            age = profile["age"] or self._infer_age_hint(local_context)
            gender = profile["gender"] or self._infer_gender_hint(profile["name"], local_context)
            appearance = self._extract_keyword_hint(context, APPEARANCE_HINTS)
            action_hint = self._extract_action_hint(context, profile["name"])

            description_parts = [
                part for part in [appearance, clothing, action_hint] if part and self._is_useful_character_hint(part)
            ]
            description = "，".join(dict.fromkeys(description_parts))
            if not description:
                description = "文本中的关键人物，建议补充外形与穿着细节"

            entities.append(
                {
                    "id": f"fallback_char_{index:03d}",
                    "name": profile["name"],
                    "aliases": profile["aliases"],
                    "description": self._truncate_text(description, max_length=36),
                    "age": age,
                    "gender": gender,
                    "clothing": clothing,
                    "visual_weight": max(5 - index + 1, 2),
                }
            )

        return self._dedupe_entities_by_name(entities)

    def _strip_known_character_prefix(self, value: str, known_character_names: Sequence[str]) -> str:
        cleaned = str(value or "").strip()
        for name in sorted((str(item).strip() for item in known_character_names if str(item).strip()), key=len, reverse=True):
            if cleaned.startswith(name) and len(cleaned) > len(name):
                return cleaned[len(name) :].strip()
        return cleaned

    def _normalize_scene_candidate(self, candidate: str, known_character_names: Sequence[str]) -> str:
        cleaned = self._clean_entity_name(candidate, is_scene=True)
        if not cleaned:
            return ""

        cleaned = self._strip_known_character_prefix(cleaned, known_character_names)
        cleaned = re.sub(
            r"^(?:他|她|它|这里|那里|里面|外面|熟悉的|陌生的|空荡的|狭小的|狭窄的|昏暗的|明亮的|冰冷的|温暖的|安静的|拥挤的|破旧的|昏暗|明亮|冰冷|温暖|安静|空荡|狭小|狭窄|纯白|科幻|极简|在|从|向|往|朝|朝着|向着|回到|来到|走进|进入|站在|躲进|躺在|坐在|冲进|冲到|走出|推开|路过|经过|离开|看向|望向|是|又是|还是|正是|着)+",
            "",
            cleaned,
        )

        for keyword in sorted(SCENE_KEYWORDS, key=len, reverse=True):
            if not cleaned.endswith(keyword):
                continue

            prefix = cleaned[: -len(keyword)]
            prefix = self._strip_known_character_prefix(prefix, known_character_names)
            prefix = self._clean_entity_name(prefix)

            if (
                len(prefix) > 3
                or "的" in prefix
                or any(action in prefix for action in CHARACTER_ACTION_HINTS)
                or any(cue in prefix for cue in CHARACTER_NAME_CUE_WORDS)
                or prefix in SCENE_PARTICLE_PREFIXES
                or prefix in SCENE_DESCRIPTOR_PREFIXES
                or any(prefix.endswith(item) for item in SCENE_PARTICLE_PREFIXES)
            ):
                prefix = ""

            normalized = f"{prefix}{keyword}" if prefix else keyword
            return self._clean_entity_name(normalized, is_scene=True)

        return ""

    def _extract_fallback_scenes(self, text: str, segments: List[str], known_character_names: Sequence[str]) -> List[Dict[str, Any]]:
        if not text:
            return []

        candidate_scores: Counter = Counter()
        first_positions: Dict[str, int] = {}

        def add_scene(raw_name: str, score: int) -> None:
            normalized = self._normalize_scene_candidate(raw_name, known_character_names)
            if not normalized:
                return
            self._register_ranked_candidate(candidate_scores, first_positions, text, normalized, score)

        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue

            heading_match = re.match(r"^\d+(?:-\d+)?\s+(.+?)(?:\s*\[[^\]]+\]){0,3}$", line)
            if heading_match:
                add_scene(heading_match.group(1), 6)

            scene_match = re.match(r"^(?:场景|地点)[:：]\s*(.+)$", line)
            if scene_match:
                add_scene(scene_match.group(1), 8)

        for keyword in SCENE_KEYWORDS:
            pattern = rf"([\u4e00-\u9fff]{{0,4}}?{re.escape(keyword)})"
            for match in re.finditer(pattern, text):
                add_scene(match.group(1), 1 if match.group(1) == keyword else 2)

        unique_names = self._rank_candidates(candidate_scores, first_positions, text, min_score=1, max_items=8)

        entities: List[Dict[str, Any]] = []
        for index, name in enumerate(unique_names, start=1):
            context = self._collect_context(segments, name, limit=2) or text[:80]
            time_of_day = self._extract_keyword_hint(context, TIME_OF_DAY_HINTS)
            lighting = self._extract_keyword_hint(context, LIGHTING_HINTS)
            description = self._extract_scene_description(context, name, time_of_day, lighting)

            entities.append(
                {
                    "id": f"fallback_scene_{index:03d}",
                    "name": name,
                    "description": self._truncate_text(description, max_length=42),
                    "time_of_day": time_of_day or None,
                    "lighting_mood": lighting or None,
                    "visual_weight": max(5 - index + 1, 2),
                    "_scene": True,
                }
            )

        return self._dedupe_entities_by_name(entities)

    def _normalize_prop_candidate(self, candidate: str, known_character_names: Sequence[str]) -> str:
        cleaned = self._clean_entity_name(candidate)
        if not cleaned:
            return ""

        cleaned = self._strip_known_character_prefix(cleaned, known_character_names)
        cleaned = re.sub(
            r"^(?:他|她|它|我的|你的|他的|她的|它的|您的|手里的|怀里的|兜里的|桌上的|地上的|身上的|眼前的|面前的|居然是|正在拿着|拿着|握着|举着|抱着|推着|看着|盯着|放着|摆着|掉在|落在|用来|可以用|正在监控|把|将|端着|端起|接过|拿起|拿出|放下|放在|扣回|递出|递给|递来|递上|送来)+",
            "",
            cleaned,
        )

        for keyword in sorted(PROP_KEYWORDS, key=len, reverse=True):
            if not cleaned.endswith(keyword):
                continue

            prefix = cleaned[: -len(keyword)]
            prefix = self._strip_known_character_prefix(prefix, known_character_names)
            prefix = self._clean_entity_name(prefix)

            if (
                len(prefix) > 3
                or "的" in prefix
                or any(action in prefix for action in CHARACTER_ACTION_HINTS)
                or any(cue in prefix for cue in CHARACTER_NAME_CUE_WORDS)
                or prefix in PROP_ACTION_PREFIXES
                or any(prefix.endswith(item) for item in PROP_ACTION_PREFIXES)
            ):
                prefix = ""

            normalized = f"{prefix}{keyword}" if prefix else keyword
            return self._clean_entity_name(normalized)

        return ""

    def _extract_fallback_props(self, text: str, segments: List[str], known_character_names: Sequence[str]) -> List[Dict[str, Any]]:
        if not text:
            return []

        candidate_scores: Counter = Counter()
        first_positions: Dict[str, int] = {}

        def add_prop(raw_name: str, score: int) -> None:
            normalized = self._normalize_prop_candidate(raw_name, known_character_names)
            if not normalized:
                return
            self._register_ranked_candidate(candidate_scores, first_positions, text, normalized, score)

        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            prop_match = re.match(r"^(?:道具|物件|物品)[:：]\s*(.+)$", line)
            if prop_match:
                for token in re.split(r"[，、,/／\s]+", prop_match.group(1).strip()):
                    add_prop(token, 8)

        for keyword in PROP_KEYWORDS:
            pattern = rf"([\u4e00-\u9fff]{{0,4}}?{re.escape(keyword)})"
            for match in re.finditer(pattern, text):
                add_prop(match.group(1), 1 if match.group(1) == keyword else 2)

        unique_names = self._rank_candidates(candidate_scores, first_positions, text, min_score=1, max_items=10)

        entities: List[Dict[str, Any]] = []
        for index, name in enumerate(unique_names, start=1):
            context = self._collect_context(segments, name, limit=2) or text[:80]
            description = self._extract_prop_description(context, name)
            entities.append(
                {
                    "id": f"fallback_prop_{index:03d}",
                    "name": name,
                    "description": self._truncate_text(description, max_length=36),
                }
            )

        return self._dedupe_entities_by_name(entities)

    def _extract_keyword_hint(self, context: str, keywords: Sequence[str]) -> str:
        for keyword in keywords:
            if keyword in context:
                return keyword
        return ""

    def _extract_clothing_hint(self, context: str) -> str:
        for keyword in CLOTHING_HINTS:
            match = re.search(rf"([\u4e00-\u9fff]{{0,4}}{keyword})", context)
            if match:
                return self._truncate_text(match.group(1), max_length=16)
        return ""

    def _infer_age_hint(self, context: str) -> str:
        age_map = {
            "小孩": "儿童",
            "孩子": "儿童",
            "男孩": "少年",
            "女孩": "少女",
            "少年": "少年",
            "少女": "少女",
            "青年": "青年",
            "中年": "中年",
            "老人": "老年",
            "老太太": "老年",
        }
        for key, value in age_map.items():
            if key in context:
                return value
        return ""

    def _infer_gender_hint(self, name: str, context: str) -> str:
        female_hints = ("她", "女孩", "少女", "女人", "妈妈", "母亲", "姐姐", "妹妹", "阿姨", "奶奶")
        male_hints = ("他", "男孩", "少年", "男人", "爸爸", "父亲", "哥哥", "弟弟", "叔叔", "爷爷")
        local_windows = re.findall(rf".{{0,8}}{re.escape(name)}.{{0,8}}", context) if name else []
        gender_context = " ".join(local_windows) or context
        if any(hint in gender_context or hint in name for hint in female_hints):
            return "女"
        if any(hint in gender_context or hint in name for hint in male_hints):
            return "男"
        return ""

    def _extract_action_hint(self, context: str, token: str) -> str:
        if not context:
            return ""
        sentence = context.replace(token, "", 1)
        sentence = re.sub(r"[“”\"'`]", "", sentence)
        sentence = re.sub(r"\s+", "", sentence)
        sentence = sentence.strip("，。！？；:：")
        if not sentence:
            return ""
        for breaker in ("。", "！", "？", "；", "\n"):
            if breaker in sentence:
                sentence = sentence.split(breaker)[0]
        return self._truncate_text(sentence, max_length=20)

    def _is_useful_character_hint(self, value: str) -> bool:
        cleaned = self._truncate_text(self._clean_entity_name(value), max_length=24)
        if not cleaned or len(cleaned) < 2:
            return False
        if any(marker in value for marker in ("【", "】")):
            return False
        if any(token in cleaned for token in CHARACTER_NARRATIVE_STOPWORDS):
            return False
        if any(token in cleaned for token in ("AI", "伴侣", "主人", "世界", "结局")):
            return False
        return True

    def _extract_scene_description(self, context: str, name: str, time_of_day: str, lighting: str) -> str:
        parts: List[str] = []
        if time_of_day:
            parts.append(time_of_day)
        if lighting and lighting not in parts:
            parts.append(lighting)

        cleaned = context.replace(name, "", 1)
        cleaned = re.sub(r"[“”\"'`]", "", cleaned)
        cleaned = re.sub(r"\s+", "", cleaned).strip("，。！？；:：")
        if cleaned:
            parts.append(cleaned)
        if not parts:
            parts.append(f"文本中出现的{name}")
        return "，".join(dict.fromkeys(part for part in parts if part))

    def _extract_prop_description(self, context: str, name: str) -> str:
        if context:
            surrounding = re.search(rf"([^\n。！？]{{0,12}}{re.escape(name)}[^\n。！？]{{0,12}})", context)
            if surrounding:
                snippet = surrounding.group(1).strip("，。！？；:：")
                if snippet:
                    return snippet
        return f"文本中的关键道具{name}"

    def build_story_analysis(
        self,
        text: str,
        characters: List[Character],
        scenes: List[Scene],
        props: List[Prop],
    ) -> StoryAnalysis:
        normalized_text = str(text or "").strip()
        if not normalized_text:
            return StoryAnalysis()

        raw_blocks = self._split_story_blocks(normalized_text)
        if not raw_blocks:
            return StoryAnalysis()

        story_beats = self._build_story_beats(raw_blocks, characters, scenes, props)
        plot_points = [beat.summary for beat in story_beats[:5] if beat.summary]

        if not plot_points and story_beats:
            plot_points = [beat.title for beat in story_beats[:5] if beat.title]

        summary = self._build_story_summary(story_beats, plot_points)
        character_presence = self._build_character_presence(story_beats, characters, normalized_text)
        character_relationships = self._build_character_relationships(story_beats)

        return StoryAnalysis(
            summary=summary,
            plot_points=plot_points,
            scene_beats=story_beats,
            character_presence=character_presence,
            character_relationships=character_relationships,
        )

    def _looks_like_inline_section_title(self, paragraph: str) -> bool:
        cleaned = str(paragraph or "").strip().strip("[]【】()（）")
        if not cleaned or len(cleaned) > 24:
            return False
        if CHAPTER_HEADING_RE.match(cleaned) or SCENE_HEADING_RE.match(cleaned):
            return True
        if DIALOGUE_LINE_RE.match(cleaned):
            return False
        if re.search(r"[。！？!?；;:：,，]", cleaned):
            return False
        return bool(re.fullmatch(r"[A-Za-z0-9\u4e00-\u9fff·《》【】（）()\-\s]+", cleaned))

    def _append_story_block(
        self,
        blocks: List[Dict[str, Any]],
        *,
        heading: str,
        content: str,
        chapter_title: str = "",
        chapter_order: Optional[int] = None,
    ) -> None:
        normalized_heading = str(heading or "").strip()
        normalized_content = str(content or "").strip()
        if not normalized_heading and not normalized_content:
            return

        blocks.append(
            {
                "heading": normalized_heading,
                "content": normalized_content,
                "chapter_title": str(chapter_title or "").strip(),
                "chapter_order": chapter_order,
            }
        )

    def _chunk_paragraphs_into_story_blocks(
        self,
        paragraphs: List[str],
        *,
        chapter_title: str = "",
        chapter_order: Optional[int] = None,
        detect_inline_titles: bool = True,
        soft_limit: int = 280,
        hard_limit: int = 420,
        max_parts: int = 3,
        split_on_shift_markers: bool = False,
    ) -> List[Dict[str, Any]]:
        blocks: List[Dict[str, Any]] = []
        pending_heading = ""
        current_paragraphs: List[str] = []
        current_length = 0

        def flush() -> None:
            nonlocal pending_heading, current_paragraphs, current_length
            self._append_story_block(
                blocks,
                heading=pending_heading,
                content="\n\n".join(current_paragraphs),
                chapter_title=chapter_title,
                chapter_order=chapter_order,
            )
            pending_heading = ""
            current_paragraphs = []
            current_length = 0

        for paragraph in paragraphs:
            normalized = str(paragraph or "").strip()
            if not normalized:
                continue

            if detect_inline_titles and self._looks_like_inline_section_title(normalized):
                if current_paragraphs:
                    flush()
                elif pending_heading:
                    self._append_story_block(
                        blocks,
                        heading=pending_heading,
                        content="",
                        chapter_title=chapter_title,
                        chapter_order=chapter_order,
                    )
                pending_heading = normalized
                continue

            paragraph_length = len(re.sub(r"\s+", "", normalized))
            if split_on_shift_markers and current_paragraphs and self._looks_like_story_shift_paragraph(normalized):
                flush()
            if current_paragraphs and current_length >= soft_limit and (paragraph_length >= 120 or len(current_paragraphs) >= 2):
                flush()

            current_paragraphs.append(normalized)
            current_length += paragraph_length

            if current_length >= hard_limit or len(current_paragraphs) >= max_parts:
                flush()

        if current_paragraphs or pending_heading:
            flush()

        return blocks

    def _split_story_blocks(self, text: str) -> List[Dict[str, Any]]:
        stripped = str(text or "").strip()
        if not stripped:
            return []

        lines = [line.rstrip() for line in stripped.splitlines()]
        chapter_starts = [
            (index, line.strip())
            for index, line in enumerate(lines)
            if line.strip() and CHAPTER_HEADING_RE.match(line.strip())
        ]

        def split_within_chapter(
            chapter_text: str,
            *,
            chapter_title: str = "",
            chapter_order: Optional[int] = None,
        ) -> List[Dict[str, Any]]:
            local_lines = [line.rstrip() for line in chapter_text.splitlines()]
            heading_indices = [
                index
                for index, line in enumerate(local_lines)
                if line.strip() and SCENE_HEADING_RE.match(line.strip())
            ]

            local_blocks: List[Dict[str, Any]] = []
            if heading_indices:
                heading_starts = heading_indices + [len(local_lines)]
                for idx, start in enumerate(heading_indices):
                    end = heading_starts[idx + 1]
                    heading = local_lines[start].strip()
                    content_lines = [line.strip() for line in local_lines[start + 1 : end] if line.strip()]
                    self._append_story_block(
                        local_blocks,
                        heading=heading,
                        content="\n".join(content_lines).strip(),
                        chapter_title=chapter_title,
                        chapter_order=chapter_order,
                    )
                return local_blocks

            paragraphs = [paragraph.strip() for paragraph in re.split(r"\n\s*\n+", chapter_text) if paragraph.strip()]
            if len(paragraphs) > 1:
                return self._chunk_paragraphs_into_story_blocks(
                    paragraphs,
                    chapter_title=chapter_title,
                    chapter_order=chapter_order,
                    detect_inline_titles=True,
                )

            line_paragraphs = [line.strip() for line in local_lines if line.strip()]
            if len(line_paragraphs) > 1:
                return self._chunk_paragraphs_into_story_blocks(
                    line_paragraphs,
                    chapter_title=chapter_title,
                    chapter_order=chapter_order,
                    detect_inline_titles=False,
                    soft_limit=1800,
                    hard_limit=2600,
                    max_parts=80,
                    split_on_shift_markers=True,
                )

            sentences = [
                segment.strip()
                for segment in re.split(r"(?<=[。！？!?；;])", chapter_text)
                if re.sub(r"[“”\"'`\s]+", "", segment).strip()
            ]
            if not sentences:
                return [
                    {
                        "heading": "",
                        "content": chapter_text,
                        "chapter_title": chapter_title,
                        "chapter_order": chapter_order,
                    }
                ]

            current_sentences: List[str] = []
            current_length = 0
            for sentence in sentences:
                current_sentences.append(sentence)
                current_length += len(sentence)
                if current_length >= 360 or len(current_sentences) >= 5:
                    self._append_story_block(
                        local_blocks,
                        heading="",
                        content="".join(current_sentences).strip(),
                        chapter_title=chapter_title,
                        chapter_order=chapter_order,
                    )
                    current_sentences = []
                    current_length = 0

            if current_sentences:
                self._append_story_block(
                    local_blocks,
                    heading="",
                    content="".join(current_sentences).strip(),
                    chapter_title=chapter_title,
                    chapter_order=chapter_order,
                )

            return local_blocks

        if chapter_starts:
            ranges = chapter_starts + [(len(lines), "")]
            explicit_blocks: List[Dict[str, Any]] = []
            for chapter_index, (start, heading) in enumerate(chapter_starts, start=1):
                end = ranges[chapter_index][0]
                chapter_lines = [line.rstrip() for line in lines[start + 1 : end]]
                chapter_text = "\n".join(chapter_lines).strip()
                chapter_blocks = split_within_chapter(
                    chapter_text,
                    chapter_title=heading,
                    chapter_order=chapter_index,
                )
                if chapter_blocks:
                    explicit_blocks.extend(chapter_blocks)
                else:
                    self._append_story_block(
                        explicit_blocks,
                        heading=heading,
                        content="",
                        chapter_title=heading,
                        chapter_order=chapter_index,
                    )
            return explicit_blocks

        return split_within_chapter(stripped)

    def _build_story_beats(
        self,
        raw_blocks: List[Dict[str, Any]],
        characters: List[Character],
        scenes: List[Scene],
        props: List[Prop],
    ) -> List[StoryBeat]:
        beats: List[StoryBeat] = []
        active_scene: Optional[Scene] = None
        active_chapter_order: Optional[int] = None
        active_chapter_title: str = ""
        for order, block in enumerate(raw_blocks, start=1):
            block_chapter_order = block.get("chapter_order")
            block_chapter_title = str(block.get("chapter_title", "") or "").strip()
            if block_chapter_order != active_chapter_order or block_chapter_title != active_chapter_title:
                active_chapter_order = block_chapter_order
                active_chapter_title = block_chapter_title
                active_scene = None

            heading = str(block.get("heading", "") or "").strip()
            content = str(block.get("content", "") or "").strip()
            combined_text = "\n".join(part for part in [heading, content] if part).strip()
            if not combined_text:
                continue

            matched_characters = self._match_assets_in_text(combined_text, characters)
            matched_props = self._match_assets_in_text(combined_text, props)
            primary_scene = self._resolve_primary_scene(heading, content, scenes)
            if primary_scene:
                active_scene = primary_scene
            elif active_scene is not None:
                primary_scene = active_scene

            location_hint = primary_scene.name if primary_scene else self._infer_location_hint(combined_text)
            time_hint = (
                primary_scene.time_of_day
                if primary_scene and primary_scene.time_of_day
                else self._extract_keyword_hint(combined_text, TIME_OF_DAY_HINTS)
            )

            summary = self._summarize_story_block(heading, content)
            action_summary = self._build_action_summary(content, summary)
            dialogue_excerpt = self._extract_dialogue_excerpt(content)
            title = self._build_story_beat_title(order, heading, location_hint, summary)
            storyboard_focus = self._build_storyboard_focus(summary, matched_characters, matched_props, location_hint)
            normalized_content = re.sub(r"\s+", "", content)
            sentence_count = len([segment for segment in re.split(r"(?<=[。！？!?；;])", content) if segment.strip()])
            quality_flags: List[str] = []
            if heading and not normalized_content:
                quality_flags.append("title_only")
            if not matched_characters:
                quality_flags.append("no_characters")
            if not location_hint:
                quality_flags.append("no_scene")
            if (heading and not normalized_content) or (normalized_content and len(normalized_content) < 80 and sentence_count <= 1):
                quality_flags.append("over_segmented")
            storyboard_goal = self._build_storyboard_goal(
                location_hint=location_hint,
                action_summary=action_summary,
                dialogue_excerpt=dialogue_excerpt,
                characters=matched_characters,
                storyboard_focus=storyboard_focus,
            )

            beats.append(
                StoryBeat(
                    id=f"story_beat_{order:03d}",
                    order=order,
                    title=title,
                    chapter_order=block_chapter_order,
                    chapter_title=block_chapter_title or None,
                    summary=summary,
                    action_summary=action_summary,
                    dialogue_excerpt=dialogue_excerpt,
                    storyboard_goal=storyboard_goal,
                    scene_id=primary_scene.id if primary_scene and primary_scene.id else None,
                    scene_name=primary_scene.name if primary_scene else location_hint or None,
                    location_hint=location_hint or None,
                    time_hint=time_hint or None,
                    character_ids=[character.id for character in matched_characters],
                    character_names=[character.name for character in matched_characters],
                    prop_ids=[prop.id for prop in matched_props],
                    prop_names=[prop.name for prop in matched_props],
                    source_excerpt=self._truncate_text(combined_text, max_length=88),
                    storyboard_focus=storyboard_focus,
                    quality_flags=quality_flags,
                )
            )

        return beats

    def _match_assets_in_text(self, text: str, assets: List[Any]) -> List[Any]:
        matched: List[Any] = []
        seen_ids: Set[str] = set()
        sorted_assets = sorted(
            assets,
            key=lambda asset: len(str(getattr(asset, "name", "") or "").strip()),
            reverse=True,
        )
        for asset in sorted_assets:
            name = str(getattr(asset, "name", "") or "").strip()
            if not name:
                continue
            aliases = [name]
            raw_aliases = getattr(asset, "aliases", None)
            if isinstance(raw_aliases, list):
                aliases.extend(str(alias).strip() for alias in raw_aliases if str(alias).strip())
            if "(" in name:
                aliases.append(name.split("(")[0].strip())
            if "（" in name:
                aliases.append(name.split("（")[0].strip())

            if any(alias and alias in text for alias in aliases):
                asset_id = str(getattr(asset, "id", "") or "")
                if asset_id and asset_id not in seen_ids:
                    seen_ids.add(asset_id)
                    matched.append(asset)
        return matched

    def _resolve_primary_scene(self, heading: str, content: str, scenes: List[Scene]) -> Optional[Scene]:
        search_text = "\n".join(part for part in [heading, content] if part)
        matched = self._match_assets_in_text(search_text, scenes)
        if matched:
            return matched[0]

        inferred_name = self._infer_location_hint(search_text)
        if inferred_name:
            return Scene(id="", name=inferred_name, description="")
        return None

    def _infer_location_hint(self, text: str) -> str:
        for keyword in SCENE_KEYWORDS:
            pattern = rf"([\u4e00-\u9fff]{{0,6}}{keyword})"
            match = re.search(pattern, text)
            if match:
                candidate = self._clean_entity_name(match.group(1), is_scene=True)
                if candidate:
                    return candidate
        return ""

    def _summarize_story_block(self, heading: str, content: str) -> str:
        body = str(content or "").strip()
        if not body:
            body = str(heading or "").strip()
        if not body:
            return ""

        sentences = [segment.strip() for segment in re.split(r"(?<=[。！？!?；;])", body) if segment.strip()]
        summary = "".join(sentences[:2]) if sentences else body
        return self._truncate_text(summary, max_length=90)

    def _build_story_beat_title(self, order: int, heading: str, location_hint: str, summary: str) -> str:
        if heading:
            title = self._clean_entity_name(heading, is_scene=True) or heading
            return self._truncate_text(title, max_length=24)

        if location_hint:
            return f"第{order}场 · {self._truncate_text(location_hint, max_length=14)}"

        summary_head = summary.split("，")[0].split("。")[0].strip()
        if summary_head:
            return f"第{order}场 · {self._truncate_text(summary_head, max_length=14)}"

        return f"第{order}场"

    def _build_storyboard_focus(
        self,
        summary: str,
        characters: List[Character],
        props: List[Prop],
        location_hint: str,
    ) -> str:
        focus_parts: List[str] = []
        if location_hint:
            focus_parts.append(location_hint)
        if characters:
            focus_parts.append("角色：" + "、".join(character.name for character in characters[:3]))
        if props:
            focus_parts.append("道具：" + "、".join(prop.name for prop in props[:3]))
        if summary:
            focus_parts.append(summary)
        return self._truncate_text("；".join(focus_parts), max_length=100)

    def _build_action_summary(self, content: str, summary: str) -> str:
        action_lines: List[str] = []
        for raw_line in str(content or "").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if re.match(r"^(?:人物|角色)[:：]", line):
                continue
            if DIALOGUE_LINE_RE.match(line):
                continue
            cleaned = re.sub(r"^[△▲◆●•\-\*]+\s*", "", line).strip()
            if cleaned:
                action_lines.append(cleaned)

        action_text = " ".join(action_lines[:2]).strip() or summary
        return self._truncate_text(action_text, max_length=80)

    def _extract_dialogue_excerpt(self, content: str) -> str:
        excerpts: List[str] = []
        for raw_line in str(content or "").splitlines():
            match = DIALOGUE_LINE_RE.match(raw_line.strip())
            if not match:
                continue

            speaker = self._clean_entity_name(match.group(1))
            dialogue = self._truncate_text(match.group(2).strip(), max_length=26)
            if speaker and dialogue:
                excerpts.append(f"{speaker}：{dialogue}")
            if len(excerpts) >= 2:
                break

        if excerpts:
            return " / ".join(excerpts[:2])

        for match in INLINE_QUOTE_RE.finditer(str(content or "")):
            quote = self._truncate_text(match.group(1).strip(), max_length=24)
            if quote:
                excerpts.append(f"“{quote}”")
            if len(excerpts) >= 2:
                break

        return " / ".join(excerpts[:2])

    def _build_storyboard_goal(
        self,
        *,
        location_hint: str,
        action_summary: str,
        dialogue_excerpt: str,
        characters: List[Character],
        storyboard_focus: str,
    ) -> str:
        goal_parts: List[str] = []
        if location_hint:
            goal_parts.append(f"先交代{location_hint}的空间关系")
        if characters:
            goal_parts.append("突出" + "、".join(character.name for character in characters[:3]) + "的在场状态")
        if action_summary:
            goal_parts.append(f"锁定核心动作：{action_summary}")
        if dialogue_excerpt:
            goal_parts.append("保留对白触发的情绪张力")
        elif storyboard_focus:
            goal_parts.append(storyboard_focus)
        return self._truncate_text("；".join(goal_parts), max_length=110)

    def _build_story_summary(self, beats: List[StoryBeat], plot_points: List[str]) -> str:
        if plot_points:
            if len(plot_points) == 1:
                return plot_points[0]
            summary_parts = plot_points[:3]
            if len(plot_points) > 3:
                summary_parts.append(plot_points[-1])
            return self._truncate_text(" ".join(summary_parts), max_length=160)

        beat_titles = [beat.title for beat in beats[:4] if beat.title]
        return self._truncate_text(" -> ".join(beat_titles), max_length=120)

    def _build_character_presence(
        self,
        beats: List[StoryBeat],
        characters: List[Character],
        original_text: str,
    ) -> List[CharacterPresenceEntry]:
        entries: List[CharacterPresenceEntry] = []
        for character in characters:
            related_beats = [beat for beat in beats if character.id in beat.character_ids]
            if not related_beats:
                continue

            aliases = [str(alias).strip() for alias in getattr(character, "aliases", []) if str(alias).strip()]
            mention_targets = list(dict.fromkeys([character.name, *aliases]))
            mention_count = max(sum(original_text.count(target) for target in mention_targets), len(related_beats))
            highlights = [beat.summary for beat in related_beats[:3] if beat.summary]
            entries.append(
                CharacterPresenceEntry(
                    character_id=character.id,
                    character_name=character.name,
                    scene_beat_ids=[beat.id for beat in related_beats],
                    scene_titles=[beat.title for beat in related_beats],
                    mention_count=mention_count,
                    highlights=highlights,
                )
            )

        return entries

    def _build_character_relationships(self, beats: List[StoryBeat]) -> List[CharacterRelationshipEdge]:
        stats: Dict[Tuple[str, str], Dict[str, Any]] = {}

        for beat in beats:
            participants: List[Tuple[str, str]] = []
            for index, name in enumerate(beat.character_names):
                char_id = beat.character_ids[index] if index < len(beat.character_ids) else f"name::{name}"
                cleaned_name = str(name or "").strip()
                if cleaned_name:
                    participants.append((char_id, cleaned_name))

            deduped_participants = list(dict.fromkeys(participants))
            if len(deduped_participants) < 2:
                continue

            for left_index in range(len(deduped_participants) - 1):
                left = deduped_participants[left_index]
                for right_index in range(left_index + 1, len(deduped_participants)):
                    right = deduped_participants[right_index]
                    pair = sorted([left, right], key=lambda item: (item[1], item[0]))
                    pair_key = (pair[0][0], pair[1][0])
                    current = stats.setdefault(
                        pair_key,
                        {
                            "source_character_id": pair[0][0],
                            "source_character_name": pair[0][1],
                            "target_character_id": pair[1][0],
                            "target_character_name": pair[1][1],
                            "shared_scene_beat_ids": [],
                            "shared_scene_titles": [],
                        },
                    )
                    if beat.id not in current["shared_scene_beat_ids"]:
                        current["shared_scene_beat_ids"].append(beat.id)
                    if beat.title not in current["shared_scene_titles"]:
                        current["shared_scene_titles"].append(beat.title)

        relationships: List[CharacterRelationshipEdge] = []
        for pair_key, data in stats.items():
            shared_titles = data["shared_scene_titles"]
            co_scene_count = len(data["shared_scene_beat_ids"])
            relationship_hint = self._truncate_text(
                f"{data['source_character_name']} 与 {data['target_character_name']} 在 "
                f"{'、'.join(shared_titles[:2]) or '多个场次'} 等 {co_scene_count} 场同场出现",
                max_length=100,
            )
            relationships.append(
                CharacterRelationshipEdge(
                    pair_id=f"{pair_key[0]}::{pair_key[1]}",
                    source_character_id=data["source_character_id"],
                    source_character_name=data["source_character_name"],
                    target_character_id=data["target_character_id"],
                    target_character_name=data["target_character_name"],
                    co_scene_count=co_scene_count,
                    shared_scene_beat_ids=data["shared_scene_beat_ids"],
                    shared_scene_titles=shared_titles,
                    relationship_hint=relationship_hint,
                )
            )

        relationships.sort(key=lambda item: (-item.co_scene_count, item.source_character_name, item.target_character_name))
        return relationships

    def _create_script_from_data(
        self,
        title: str,
        original_text: str,
        data: Dict[str, Any],
        generation_metadata: Optional[Dict[str, Any]] = None,
    ) -> Script:
        script_id = str(uuid.uuid4())
        generation_metadata = dict(generation_metadata or {})
        novel_meta = generation_metadata.get("novel_parse", {}) if isinstance(generation_metadata, dict) else {}
        frame_generation_source = str(novel_meta.get("source") or "llm")
        frame_generation_degraded = bool(novel_meta.get("degraded", False))
        frame_generation_reason = str(novel_meta.get("reason", "") or "").strip() or None
        
        characters = []
        name_to_char = {} # For variant linking
        llm_id_to_uuid = {} # For ID resolution

        # Pass 1: Create all characters
        for char_data in data.get("characters", []):
            char_uuid = str(uuid.uuid4())
            llm_id = char_data.get("id")
            if llm_id:
                llm_id_to_uuid[llm_id] = char_uuid
            
            char = Character(
                id=char_uuid,
                name=char_data.get("name", "Unknown"),
                aliases=[str(alias).strip() for alias in char_data.get("aliases", []) if str(alias).strip()],
                description=char_data.get("description", ""),
                age=char_data.get("age"),
                gender=char_data.get("gender"),
                clothing=char_data.get("clothing"), # Might be merged into description in new prompt, but keeping for compatibility
                visual_weight=char_data.get("visual_weight", 3),
                status=GenerationStatus.PENDING
            )
            characters.append(char)
            name_to_char[char.name] = char
            
        # Pass 2: Link variants to base characters (Logic remains valid even with new prompt if naming convention holds)
        for char in characters:
            if "(" in char.name and ")" in char.name:
                base_name = char.name.split("(")[0].strip()
                if base_name in name_to_char and name_to_char[base_name].id != char.id:
                    char.base_character_id = name_to_char[base_name].id
            
        scenes = []
        for scene_data in data.get("scenes", []):
            scene_uuid = str(uuid.uuid4())
            llm_id = scene_data.get("id")
            if llm_id:
                llm_id_to_uuid[llm_id] = scene_uuid

            scenes.append(Scene(
                id=scene_uuid,
                name=scene_data.get("name", "Unknown"),
                description=scene_data.get("description", ""),
                time_of_day=scene_data.get("time_of_day"),
                lighting_mood=scene_data.get("lighting_mood"),
                visual_weight=scene_data.get("visual_weight", 3),
                status=GenerationStatus.PENDING
            ))
            
        props = []
        for prop_data in data.get("props", []):
            prop_uuid = str(uuid.uuid4())
            llm_id = prop_data.get("id")
            if llm_id:
                llm_id_to_uuid[llm_id] = prop_uuid

            props.append(Prop(
                id=prop_uuid,
                name=prop_data.get("name", "Unknown"),
                description=prop_data.get("description", ""),
                status=GenerationStatus.PENDING
            ))
            
        frames = []
        for frame_data in data.get("frames", []):
            # Resolve Character IDs
            char_ids = []
            for cid in frame_data.get("character_ids", []):
                if cid in llm_id_to_uuid:
                    char_ids.append(llm_id_to_uuid[cid])
            
            # Resolve Prop IDs
            prop_ids = []
            for pid in frame_data.get("prop_ids", []):
                if pid in llm_id_to_uuid:
                    prop_ids.append(llm_id_to_uuid[pid])

            # Resolve Scene ID
            scene_llm_id = frame_data.get("scene_id")
            scene_id = llm_id_to_uuid.get(scene_llm_id)
            if not scene_id and scenes:
                scene_id = scenes[0].id # Fallback
            elif not scene_id:
                scene_id = str(uuid.uuid4()) # Fallback if no scenes

            # Handle Dialogue
            dialogue_data = frame_data.get("dialogue")
            dialogue_text = None
            speaker_name = None
            if isinstance(dialogue_data, dict):
                dialogue_text = dialogue_data.get("text")
                speaker_name = dialogue_data.get("speaker")
            elif isinstance(dialogue_data, str):
                dialogue_text = dialogue_data # Fallback for old format

            frames.append(StoryboardFrame(
                id=str(uuid.uuid4()),
                scene_id=scene_id,
                character_ids=char_ids,
                prop_ids=prop_ids,
                action_description=frame_data.get("action_description", ""),
                facial_expression=frame_data.get("facial_expression"),
                dialogue=dialogue_text,
                speaker=speaker_name,
                camera_angle=frame_data.get("camera_angle", "Medium Shot"),
                camera_movement=frame_data.get("camera_movement"),
                composition=frame_data.get("composition"),
                atmosphere=frame_data.get("atmosphere"),
                image_prompt=f"{frame_data.get('action_description')} {frame_data.get('facial_expression', '')} {frame_data.get('camera_angle')} {frame_data.get('lighting_mood', '')} {frame_data.get('atmosphere', '')}", 
                generation_source=frame_generation_source,
                generation_degraded=frame_generation_degraded,
                generation_reason=frame_generation_reason,
                status=GenerationStatus.PENDING
            ))
            
        story_analysis = self.build_story_analysis(original_text, characters, scenes, props)

        return Script(
            id=script_id,
            title=title,
            original_text=original_text,
            characters=characters,
            scenes=scenes,
            props=props,
            frames=frames,
            story_analysis=story_analysis,
            generation_metadata=generation_metadata,
            created_at=time.time(),
            updated_at=time.time()
        )

    def create_draft_script(self, title: str, text: str) -> Script:
        """
        Creates a draft script object without LLM analysis.
        """
        fallback_data = self._fallback_extract_entities(text)
        if self._has_meaningful_entity_payload(fallback_data):
            return self._create_script_from_data(
                title,
                text,
                fallback_data,
                generation_metadata={
                    "novel_parse": self._build_generation_meta(
                        "heuristic_draft",
                        degraded=True,
                        reason="Draft script created without LLM analysis",
                    )
                },
            )

        return Script(
            id=str(uuid.uuid4()),
            title=title,
            original_text=text,
            characters=[],
            scenes=[],
            props=[],
            frames=[],
            story_analysis=self.build_story_analysis(text, [], [], []),
            generation_metadata={
                "novel_parse": self._build_generation_meta(
                    "draft",
                    degraded=True,
                    reason="Draft script created without LLM analysis",
                )
            },
            created_at=time.time(),
            updated_at=time.time()
        )

    def split_into_episodes(self, text: str, suggested_episodes: int = 3) -> List[Dict[str, Any]]:
        """
        Uses LLM to split a long text into episodes by narrative rhythm.
        Returns a list of episode dicts with title, summary, start/end markers, etc.
        """
        if not self.is_configured:
            raise ValueError("LLM API Key 未配置。请在 API 配置中设置对应的 API Key 后重试。")

        MAX_TEXT_LENGTH = 80000
        if len(text) > MAX_TEXT_LENGTH:
            text = text[:MAX_TEXT_LENGTH] + "\n\n[文本已截断，请基于已有内容进行划分]"

        prompt = f"""你是一名专业的剧本编剧和分集策划师。

请将以下小说/剧本文本按叙事节奏划分为约 {suggested_episodes} 集。

划分原则：
1. 每集应有完整的叙事弧（开端/发展/高潮或悬念）
2. 在自然的情节转折点或场景切换处分集
3. 各集内容量大致均衡，但优先保证叙事完整性
4. 实际集数可以在建议集数 ±2 范围内浮动

输出纯 JSON（不要 markdown 代码块）:
{{
  "episodes": [
    {{
      "episode_number": 1,
      "title": "集标题",
      "summary": "50字以内的内容摘要",
      "start_marker": "该集起始的原文前20字",
      "end_marker": "该集结束的原文后20字",
      "estimated_duration": "预估时长（分钟）"
    }}
  ]
}}

原文如下：

{text}"""

        try:
            content = self.llm.chat(
                messages=[{"role": "user", "content": prompt}],
            )
            content = _strip_markdown_json(content)
            data = json.loads(content)
            episodes = data.get("episodes", [])
            if not episodes:
                raise RuntimeError("LLM 未返回任何分集数据")
            return episodes
        except json.JSONDecodeError as e:
            raise RuntimeError(f"LLM 返回的分集数据格式错误: {e}")
        except ValueError:
            raise
        except Exception as e:
            raise RuntimeError(f"分集划分失败: {str(e)}")

    def _mock_parse(self, title: str, text: str) -> Script:
        # ... (Existing mock logic moved here) ...
        script_id = str(uuid.uuid4())
        
        # Mock Characters
        char1 = Character(
            id=str(uuid.uuid4()),
            name="Alex",
            description="A young adventurer with messy brown hair and a determined look.",
            age="20",
            gender="Male",
            clothing="Leather jacket, jeans",
            visual_weight=5,
            status=GenerationStatus.PENDING
        )
        char2 = Character(
            id=str(uuid.uuid4()),
            name="Luna",
            description="A mysterious mage with silver hair and glowing blue eyes.",
            age="Unknown",
            gender="Female",
            clothing="Dark robe with silver embroidery",
            visual_weight=4,
            status=GenerationStatus.PENDING
        )
        
        # Mock Scene
        scene1 = Scene(
            id=str(uuid.uuid4()),
            name="Ancient Ruins",
            description="Crumbling stone walls covered in moss, illuminated by shafts of sunlight breaking through the canopy.",
            visual_weight=3,
            status=GenerationStatus.PENDING
        )
        
        # Mock Props
        prop1 = Prop(
            id=str(uuid.uuid4()),
            name="Glowing Crystal",
            description="A jagged crystal pulsing with a faint purple light.",
            status=GenerationStatus.PENDING
        )
        
        # Mock Frames
        frames = []
        
        # Frame 1
        frames.append(StoryboardFrame(
            id=str(uuid.uuid4()),
            scene_id=scene1.id,
            character_ids=[char1.id],
            action_description="Alex steps cautiously into the ruins, looking around.",
            camera_angle="Wide Shot",
            camera_movement="Pan Left",
            image_prompt="Wide shot of Alex stepping into ancient ruins, mossy stone walls, sunlight beams, cinematic lighting, pan left.",
            status=GenerationStatus.PENDING
        ))
        
        # Frame 2
        frames.append(StoryboardFrame(
            id=str(uuid.uuid4()),
            scene_id=scene1.id,
            character_ids=[char1.id, char2.id],
            action_description="Luna appears from the shadows, surprising Alex.",
            dialogue="Luna: You shouldn't be here.",
            camera_angle="Medium Shot",
            camera_movement="Static",
            image_prompt="Medium shot of Luna emerging from shadows behind Alex, mysterious atmosphere, static camera.",
            status=GenerationStatus.PENDING
        ))
        
        # Frame 3
        frames.append(StoryboardFrame(
            id=str(uuid.uuid4()),
            scene_id=scene1.id,
            character_ids=[char2.id],
            prop_ids=[prop1.id],
            action_description="Luna holds up the glowing crystal.",
            camera_angle="Close Up",
            camera_movement="Zoom In",
            image_prompt="Close up of Luna holding a glowing purple crystal, magical effects, zoom in.",
            status=GenerationStatus.PENDING
        ))
        
        script = Script(
            id=script_id,
            title=title,
            original_text=text,
            characters=[char1, char2],
            scenes=[scene1],
            props=[prop1],
            frames=frames,
            story_analysis=self.build_story_analysis(text, [char1, char2], [scene1], [prop1]),
            created_at=time.time(),
            updated_at=time.time()
        )
        
        return script

    def _construct_prompt(self, text: str) -> str:
        """
        Prompt A: Entity Extractor
        Constructs the system prompt for extracting characters, scenes, and props ONLY.
        Frames are generated separately via analyze_to_storyboard (Prompt B).
        """
        return f"""
你是一名影视项目的前期开发策划，请从小说文本中提取“角色 / 场景 / 道具”三类实体，供后续生图、分镜和视频使用。

硬性要求：
1. 只能输出 JSON，不要输出任何解释、标题、markdown 代码块。
2. 所有 name / description / clothing / scene description 等描述性内容都必须使用简体中文。
3. 必须“尽力提取”，不要因为信息不完整就返回空数组。
4. 只要文本里出现了明确的人物、地点、空间、物件、线索物，就应提取出来；必要时可以做合理概括。
5. 如果文本中出现了具名人物，`characters` 不能为空。
6. 如果文本中出现了明确空间、地点、房间、街道、建筑、自然环境，`scenes` 不能为空。
7. 如果文本中出现了有视觉辨识度或剧情作用的物件，`props` 不能为空。
8. 不要生成 `frames` 字段；分镜会在后续单独生成。

提取规则：
- 角色：优先提取具名人物，其次提取有明显身份的未具名人物（如“门口的守卫”）。
- 场景：按“可被画出来的空间”提取，而不是抽象章节名。场景名尽量短，描述里补充时间、光线、氛围、关键陈设。
- 道具：优先提取具有视觉锚点或剧情作用的物件，例如武器、纸条、箱子、手机、钥匙、纸鹤。
- 服装：写默认或当前阶段最具代表性的穿着；若同一角色有显著换装，可拆成变体角色。
- 视觉权重：1-5，主角/主场景/核心道具给更高值。
- 描述应聚焦“可视化信息”，不要写剧情分析，不要写情绪评价，不要写镜头语言。

输出 JSON 结构：
{{
  "characters": [
    {{
      "id": "char_001",
      "name": "角色名",
      "description": "外形、发型、体态、年龄感、辨识特征",
      "age": "年龄或年龄感（可留空字符串）",
      "gender": "性别或性别气质（可留空字符串）",
      "clothing": "默认或当前阶段最具代表性的穿着",
      "visual_weight": 5
    }}
  ],
  "scenes": [
    {{
      "id": "scene_001",
      "name": "场景名",
      "description": "空间结构、时间、光线、氛围、关键陈设",
      "visual_weight": 4
    }}
  ],
  "props": [
    {{
      "id": "prop_001",
      "name": "道具名",
      "description": "外观、材质、颜色、关键细节"
    }}
  ]
}}

参考示例：
示例文本：
林夏推开仓库铁门，看到地上有一只被雨水打湿的红色纸鹤。

示例输出：
{{
  "characters": [
    {{
      "id": "char_001",
      "name": "林夏",
      "description": "短发，身形清瘦，行动谨慎",
      "age": "",
      "gender": "女",
      "clothing": "便于行动的深色外套",
      "visual_weight": 5
    }}
  ],
  "scenes": [
    {{
      "id": "scene_001",
      "name": "仓库入口",
      "description": "老旧仓库入口，铁门生锈，空气潮湿昏暗",
      "visual_weight": 4
    }}
  ],
  "props": [
    {{
      "id": "prop_001",
      "name": "红色纸鹤",
      "description": "被雨水打湿的红色纸鹤"
    }}
  ]
}}

待分析文本：
{text}
""".strip()

    def analyze_script_for_styles(self, script_text: str) -> List[Dict[str, Any]]:
        """使用 LLM 分析剧本并推荐视觉风格"""
        
        logger.info("Analyzing script for visual style recommendations...")
        
        if not self.is_configured:
            logger.warning("DASHSCOPE_API_KEY not set. Returning default recommendations.")
            return self._mock_style_recommendations()
        
        system_prompt = """你是一个专业的电影美术指导和视觉风格顾问。
请根据提供的剧本内容，分析其题材、情绪和氛围，推荐3种截然不同但都适合的视觉风格。

对于每种风格，请提供：
1. 风格名称（简洁、专业，使用英文）
2. 风格描述（1-2句话，用中文）
3. 推荐理由（为什么这个风格适合这个剧本，用中文，50字以内）
4. Stable Diffusion 正向提示词（详细的风格关键词，英文，逗号分隔，不超过50个词）
5. Stable Diffusion 负向提示词（避免的视觉元素，英文，逗号分隔，不超过30个词）

IMPORTANT: 
- 你的回复必须是严格的JSON格式。
- 不要包含任何解释性文字。
- 所有文本中的引号必须使用转义符号 (例如 \")。
- 确保JSON完整，不要被截断。
- 保持内容精炼，避免过长的描述。
- 严禁重复生成相同的内容，不要陷入循环。
- 只返回3个推荐风格，不要多也不要少。

CRITICAL STYLE GUIDELINES:
- 正向提示词必须只描述：光影、色调、材质、艺术媒介、氛围、镜头语言 (e.g., "cinematic lighting, film grain, watercolor texture, dark atmosphere").
- 严禁描述具体实体：不要包含人物、服装、具体物品、环境细节 (e.g., 禁止 "cracked helmet", "blood stains", "monster", "forest", "sword").
- 风格必须是通用的，能套用到任何角色或场景上，而不会改变其原本的物理结构。

返回格式：
{
  "recommendations": [
    {
      "name": "风格名称",
      "description": "风格描述",
      "reason": "推荐理由",
      "positive_prompt": "正向提示词",
      "negative_prompt": "负向提示词"
    }
  ]
}"""

        user_prompt = f"剧本内容：\n\n{script_text[:2000]}"  # 限制长度避免 token 限制
        
        try:
            content = self.llm.chat(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                response_format={'type': 'json_object'},
            )
            logger.debug(f"Style Analysis Response:\n{content}")

            # Clean up markdown code blocks if present
            content = _strip_markdown_json(content)

            # Safety check: if content is suspiciously long, truncate it
            # This prevents issues where the model gets stuck in a loop
            if len(content) > 5000:
                logger.warning(f"Response too long ({len(content)} chars), truncating...")
                content = content[:5000]
                # Find the last closing brace of a recommendation object to make truncation cleaner
                last_brace = content.rfind("}")
                if last_brace != -1:
                    content = content[:last_brace+1]

            def repair_json(json_str):
                """Attempt to repair truncated or malformed JSON."""
                json_str = json_str.strip()

                # If truncated, try to close it
                if not json_str.endswith("}"):
                    # Count open braces/brackets
                    open_braces = json_str.count("{") - json_str.count("}")
                    open_brackets = json_str.count("[") - json_str.count("]")
                    open_quotes = json_str.count('"') % 2

                    if open_quotes:
                        json_str += '"'

                    json_str += "]" * open_brackets
                    json_str += "}" * open_braces

                # Ensure the root object is closed
                if json_str.count("{") > json_str.count("}"):
                     json_str += "}" * (json_str.count("{") - json_str.count("}"))

                return json_str

            try:
                data = json.loads(content)
            except json.JSONDecodeError as e:
                logger.error(f"JSON parsing error: {e}")
                logger.error(f"Raw content length: {len(content)}")

                # Try to fix common JSON issues
                try:
                    # 1. Attempt to extract JSON object from text using regex
                    import re
                    # Look for the outermost JSON object
                    json_match = re.search(r'\{[\s\S]*\}', content)
                    if json_match:
                        content = json_match.group(0)

                    # 2. Try to repair if it looks truncated
                    content = repair_json(content)

                    data = json.loads(content)
                except Exception as inner_e:
                    logger.error(f"Failed to recover JSON: {inner_e}")
                    # Last resort: try to parse partially using regex for fields
                    try:
                        logger.debug("Attempting regex extraction of fields...")
                        recommendations = []
                        # Regex to find style objects - improved to be non-greedy and handle newlines
                        style_matches = re.finditer(r'\{\s*"name":\s*"(.*?)",\s*"description":\s*"(.*?)".*?\}', content, re.DOTALL)

                        # If that fails, try a simpler regex that just looks for the array items
                        if not list(style_matches):
                            # Fallback manual parsing
                            pass

                        if not recommendations:
                            # Construct a basic valid JSON if we have at least some content
                            if "recommendations" in content:
                                # Try to close it forcefully
                                fixed_content = content + "}]}"
                                try:
                                    data = json.loads(fixed_content)
                                    recommendations = data.get("recommendations", [])
                                except:
                                    pass

                        if not recommendations:
                            raise ValueError("Regex extraction failed")
                    except:
                        return self._mock_style_recommendations()

            recommendations = data.get("recommendations", [])

            # Add unique IDs
            for i, rec in enumerate(recommendations):
                rec["id"] = f"ai-rec-{i+1}-{str(uuid.uuid4())[:8]}"
                rec["is_custom"] = False
                rec["generation_source"] = "llm"
                rec["generation_degraded"] = False

            return recommendations

        except Exception as e:
            logger.error(f"Error analyzing script for styles: {e}", exc_info=True)
            return self._mock_style_recommendations()
    
    def _mock_style_recommendations(self) -> List[Dict[str, Any]]:
        """返回默认的风格推荐"""
        return [
            {
                "id": f"mock-cinematic-{str(uuid.uuid4())[:8]}",
                "name": "Cinematic Realism",
                "description": "电影级写实风格，专业打光",
                "reason": "适合大多数叙事性内容，提供专业的视觉质感",
                "positive_prompt": "cinematic, photorealistic, 8k, volumetric lighting, film grain, dramatic lighting",
                "negative_prompt": "cartoon, anime, low quality, blurry",
                "is_custom": False,
                "generation_source": "mock",
                "generation_degraded": True,
                "generation_reason": "DASHSCOPE_API_KEY 未配置",
            },
            {
                "id": f"mock-anime-{str(uuid.uuid4())[:8]}",
                "name": "Anime Style",
                "description": "日式动漫风格，明快色彩",
                "reason": "适合充满情感表现的故事",
                "positive_prompt": "anime style, cel shading, vibrant colors, expressive, detailed character design",
                "negative_prompt": "photorealistic, 3d, blurry, washed out",
                "is_custom": False,
                "generation_source": "mock",
                "generation_degraded": True,
                "generation_reason": "DASHSCOPE_API_KEY 未配置",
            },
            {
                "id": f"mock-noir-{str(uuid.uuid4())[:8]}",
                "name": "Film Noir",
                "description": "黑色电影风格，高对比度",
                "reason": "适合悬疑、神秘题材的叙事",
                "positive_prompt": "black and white, film noir, high contrast, dramatic shadows, moody lighting",
                "negative_prompt": "colorful, bright, happy, modern",
                "is_custom": False,
                "generation_source": "mock",
                "generation_degraded": True,
                "generation_reason": "DASHSCOPE_API_KEY 未配置",
            }
        ]
    
    def _format_story_analysis_for_storyboard(self, story_analysis: Any) -> str:
        if not story_analysis:
            return "未提供结构化场次列表。"

        if hasattr(story_analysis, "model_dump"):
            data = story_analysis.model_dump()
        elif isinstance(story_analysis, dict):
            data = story_analysis
        else:
            return "未提供结构化场次列表。"

        summary = str(data.get("summary", "") or "").strip()
        plot_points = data.get("plot_points", []) or []
        scene_beats = data.get("scene_beats", []) or []
        character_presence = data.get("character_presence", []) or []
        character_relationships = data.get("character_relationships", []) or []

        lines: List[str] = []
        if summary:
            lines.append(f"总体剧情摘要：{summary}")

        if plot_points:
            lines.append("剧情要点：")
            for index, point in enumerate(plot_points[:6], start=1):
                lines.append(f"{index}. {point}")

        if scene_beats:
            lines.append("结构化场次列表：")
            for beat in scene_beats[:12]:
                beat_id = beat.get("id", "")
                title = beat.get("title", "未命名场次")
                chapter_order = beat.get("chapter_order")
                chapter_title = beat.get("chapter_title") or "未标注章节"
                scene_name = beat.get("scene_name") or beat.get("location_hint") or "未标注场景"
                time_hint = beat.get("time_hint") or "未标注时间"
                character_names = "、".join(beat.get("character_names", [])[:4]) or "无"
                prop_names = "、".join(beat.get("prop_names", [])[:4]) or "无"
                summary_text = beat.get("summary", "")
                action_summary = beat.get("action_summary", "")
                dialogue_excerpt = beat.get("dialogue_excerpt", "")
                storyboard_goal = beat.get("storyboard_goal", "")
                focus = beat.get("storyboard_focus", "")
                lines.append(
                    f"- {beat_id} | 章节序号:{chapter_order if chapter_order is not None else '无'} | 章节标题:{chapter_title} | {title} | 场景:{scene_name} | 时间:{time_hint} | 角色:{character_names} | 道具:{prop_names} | 摘要:{summary_text} | 动作摘要:{action_summary} | 对白摘录:{dialogue_excerpt or '无'} | 分镜目标:{storyboard_goal} | 系统重点:{focus}"
                )

        if character_presence:
            lines.append("角色出场表：")
            for entry in character_presence[:10]:
                lines.append(
                    f"- {entry.get('character_name', '未知角色')} -> {'、'.join(entry.get('scene_titles', [])[:6]) or '无场次'}"
                )

        if character_relationships:
            lines.append("角色关系/共场统计：")
            for edge in character_relationships[:10]:
                lines.append(
                    f"- {edge.get('source_character_name', '未知')} ↔ {edge.get('target_character_name', '未知')} | 共场:{edge.get('co_scene_count', 0)} | 场次:{'、'.join(edge.get('shared_scene_titles', [])[:4]) or '无'} | 提示:{edge.get('relationship_hint', '')}"
                )

        return "\n".join(lines).strip() or "未提供结构化场次列表。"

    def _format_single_story_beat_for_storyboard(self, story_analysis: Any, beat_id: str) -> str:
        if not story_analysis:
            return "未找到目标场次。"

        if hasattr(story_analysis, "model_dump"):
            data = story_analysis.model_dump()
        elif isinstance(story_analysis, dict):
            data = story_analysis
        else:
            return "未找到目标场次。"

        for beat in data.get("scene_beats", []) or []:
            if beat.get("id") == beat_id:
                chapter_order = beat.get("chapter_order")
                chapter_title = beat.get("chapter_title") or "未标注章节"
                return "\n".join(
                    [
                        f"目标场次 ID：{beat.get('id', '')}",
                        f"章节序号：{chapter_order if chapter_order is not None else '无'}",
                        f"章节标题：{chapter_title}",
                        f"标题：{beat.get('title', '未命名场次')}",
                        f"摘要：{beat.get('summary', '')}",
                        f"动作摘要：{beat.get('action_summary', '')}",
                        f"对白摘录：{beat.get('dialogue_excerpt', '') or '无'}",
                        f"分镜目标：{beat.get('storyboard_goal', '')}",
                        f"系统重点：{beat.get('storyboard_focus', '')}",
                        f"场景：{beat.get('scene_name') or beat.get('location_hint') or '未标注'}",
                        f"时间：{beat.get('time_hint') or '未标注'}",
                        f"角色：{'、'.join(beat.get('character_names', [])[:6]) or '无'}",
                        f"道具：{'、'.join(beat.get('prop_names', [])[:6]) or '无'}",
                        f"原文片段：{beat.get('source_excerpt', '')}",
                    ]
                )
        return "未找到目标场次。"

    def analyze_to_storyboard(
        self,
        text: str,
        entities_json: Dict[str, Any],
        story_analysis: Optional[Any] = None,
        target_beat_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Analyzes script text and generates storyboard frames using Prompt B (Storyboard Director).
        Returns a list of frame dictionaries with visual atoms.
        """
        logger.info(f"Analyzing text to storyboard: {text[:100]}...")

        target_beat_context = (
            self._format_single_story_beat_for_storyboard(story_analysis, target_beat_id)
            if target_beat_id
            else ""
        )
        if not self.is_configured:
            logger.warning("DASHSCOPE_API_KEY not set. Returning mock frames.")
            return self._mock_storyboard_frames(text, target_beat_id=target_beat_id, story_analysis=story_analysis)

        # Build entities context
        characters_list = entities_json.get("characters", [])
        scenes_list = entities_json.get("scenes", [])
        props_list = entities_json.get("props", [])

        entities_str = f"""
Characters:
{json.dumps(characters_list, ensure_ascii=False, indent=2)}

Scenes:
{json.dumps(scenes_list, ensure_ascii=False, indent=2)}

Props:
{json.dumps(props_list, ensure_ascii=False, indent=2)}
"""
        structured_story_str = self._format_story_analysis_for_storyboard(story_analysis)

        task_scope = (
            f"""
# 当前仅处理这个目标场次
{target_beat_context}
"""
            if target_beat_id
            else ""
        )
        scope_rule = (
            f"8. **目标场次锁定**:\n"
            f"   - 当前只允许为 `{target_beat_id}` 这一场生成分镜。\n"
            f"   - 每一帧都必须填写 `story_beat_id`=`{target_beat_id}`，并填写对应的 `story_beat_title`。\n"
            f"   - 不要越界生成其他场次的镜头。\n"
            if target_beat_id
            else
            "8. **场次归属标注**:\n"
            "   - 每一帧都必须填写 `story_beat_id` 和 `story_beat_title`，且必须对应上方结构化场次列表中的真实场次。\n"
            "   - 如果一个场次需要多个镜头，可以重复使用同一个 `story_beat_id`。\n"
        )
        task_text = target_beat_context if target_beat_id else text
        user_instruction = (
            f"请仅基于目标场次 {target_beat_id} 和实体上下文生成该场的分镜帧列表，不要覆盖其他场次。"
            if target_beat_id
            else "请基于结构化场次列表和实体上下文开始生成分镜帧列表，确保覆盖剧本中的所有内容。"
        )

        system_prompt = f"""
# 角色
你是一名电影级的分镜师（Storyboard Artist）和导演。你的任务是将剧本文本拆解为可供 AI 视频模型生成的一系列精细分镜帧。

# 任务目标
不仅仅是提取文本，而是要进行**视觉化拆解**。你需要将剧本中的文字转化为一系列连续的、单一动作的视觉画面。

# 结构化场次拆解（优先参考）
{structured_story_str}
{task_scope}

# 剧本格式说明
剧本遵循以下格式：
- **场景标题行**: `1-1 地点名称 [时间] [内/外]` 
- **人物行**: `人物： 角色名1，角色名2`
- **动作描述**: 以 `△` 开头，描述画面中发生的动作
- **对话**: `角色名（情绪）： 对话内容`，或 `角色名 (V.O.)：` 表示画外音

# 已提取的实体上下文
{entities_str}

# 核心规则 (CRITICAL)
1. **先按场次，再拆镜头**:
   - 必须优先按照“结构化场次列表”的顺序理解剧情，再在每个场次内部拆分镜头。
   - 不要跳过场次，也不要把不同场次的动作混成同一帧。
2. **视觉节拍拆解 (VISUAL ATOMIZATION)**:
   - 如果一行动作描述包含多个连续动作，**必须**将其拆分为多个分镜帧。
   - 每个分镜只应包含一个清晰的主要动作，时长控制在 3-5 秒。
3. **合并动作描述 (MERGE ACTION)**:
   - **`action_description` 字段必须包含画面中发生的所有动态要素**。
   - 包括：人物的神态/微表情 + 肢体动作 + 道具的物理运动（如手机震动、烟雾缭绕）。
   - 不要遗漏非人物主体的动作（如“车门打开”、“杯子摔碎”）。

4. **角色可见性**:
   - `character_ref_names` 只列出**当前分镜画面中可见**的角色。

5. **实体约束**:
   - 场景名、角色名、道具名必须严格匹配"已提取的实体"。

6. **结构一致性**:
   - 同一场次内的分镜应维持同一空间、同一人物阵容与同一剧情目标的连续性。
   - 如果结构化场次里已经标明了角色或道具，分镜时优先围绕这些元素展开，不要随意引入新主体。

7. **语言**: 所有输出必须使用简体中文。
{scope_rule}

# 输出格式
返回一个包含 `frames` 数组的 JSON 对象。不要包含 Markdown 格式标记（如 ```json）。

{{
    "frames": [
        {{
            "story_beat_id": "story_beat_001",
            "story_beat_title": "第1场 · 卧室",
            "scene_ref_name": "卧室",
            "character_ref_names": ["叶墨"],
            "prop_ref_names": ["手机"],
            "visual_atmosphere": "昏暗的卧室，窗外透进冷色调月光",
            "action_description": "手机在床头柜上疯狂震动。叶墨眉头紧锁，烦躁地翻身，肩膀挤压枕头产生形变",
            "shot_size": "中景",
            "camera_angle": "俯视",
            "camera_movement": "静止",
            "dialogue": "妈，这才几点啊！",
            "speaker": "叶墨"
        }},
        {{
            "story_beat_id": "story_beat_001",
            "story_beat_title": "第1场 · 卧室",
            "scene_ref_name": "卧室",
            "character_ref_names": ["叶墨"],
            "prop_ref_names": [],
            "visual_atmosphere": "昏暗的卧室",
            "action_description": "被子滑落，叶墨猛地坐起，一脸惊恐",
            "shot_size": "特写",
            "camera_angle": "平视",
            "camera_movement": "快速推镜头",
            "dialogue": "已经来了？",
            "speaker": "叶墨"
        }}
    ]
}}

# 剧本内容
{task_text}
"""

        try:
            content = self.llm.chat(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_instruction}
                ],
            ).strip()
            logger.debug(f"Storyboard Analysis Raw Response: {content[:500]}...")

            frames = self._parse_storyboard_json(content)
            if frames is not None:
                for frame in frames:
                    if isinstance(frame, dict):
                        frame.setdefault("generation_source", "llm")
                        frame.setdefault("generation_degraded", False)
                return frames

            # First parse failed — retry once with response_format constraint
            logger.warning("Storyboard JSON parse failed, retrying with response_format=json_object...")
            retry_content = self.llm.chat(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"{user_instruction} 请务必输出合法的JSON格式。"}
                ],
                response_format={'type': 'json_object'},
            ).strip()
            logger.debug(f"Storyboard Analysis Retry Response: {retry_content[:500]}...")
            frames = self._parse_storyboard_json(retry_content)
            if frames is not None:
                for frame in frames:
                    if isinstance(frame, dict):
                        frame.setdefault("generation_source", "llm")
                        frame.setdefault("generation_degraded", False)
                return frames

            raise RuntimeError(
                "AI 模型输出的 JSON 格式不合规，自动重试后仍然失败。请重新点击生成按钮再试一次。"
            )

        except RuntimeError:
            raise  # Re-raise our own descriptive errors
        except Exception as e:
            logger.error(f"Error in storyboard analysis: {e}", exc_info=True)
            raise RuntimeError(f"分镜分析过程出错: {str(e)}")
    
    def _parse_storyboard_json(self, content: str):
        """Try to parse storyboard JSON from LLM output. Returns frames list or None on failure."""
        content = _strip_markdown_json(content)

        try:
            result = json.loads(content.strip())
            frames = result.get("frames", [])
            if not frames:
                logger.warning("Parsed JSON successfully but 'frames' array is empty")
                return None
            logger.info(f"Storyboard Analysis generated {len(frames)} frames")
            return frames
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse storyboard analysis JSON: {e}")
            return None

    def _mock_storyboard_frames(
        self,
        text: str,
        target_beat_id: Optional[str] = None,
        story_analysis: Optional[Any] = None,
    ) -> List[Dict[str, Any]]:
        """Returns mock storyboard frames for testing when API is unavailable."""
        target_beat_title = None
        if target_beat_id and story_analysis:
            if hasattr(story_analysis, "scene_beats"):
                for beat in story_analysis.scene_beats:
                    if beat.id == target_beat_id:
                        target_beat_title = beat.title
                        break
            elif isinstance(story_analysis, dict):
                for beat in story_analysis.get("scene_beats", []) or []:
                    if beat.get("id") == target_beat_id:
                        target_beat_title = beat.get("title")
                        break

        return [
            {
                "story_beat_id": target_beat_id,
                "story_beat_title": target_beat_title,
                "scene_ref_name": "卧室",
                "character_ref_names": ["叶墨"],
                "prop_ref_names": ["手机"],
                "visual_atmosphere": "昏暗的卧室，窗外透进冷色调月光",
                "character_acting": "叶墨眉头紧锁，眼神迷离",
                "key_action_physics": "手机在柜上剧烈震动",
                "shot_size": "中景",
                "camera_angle": "平视",
                "camera_movement": "Static",
                "dialogue": None,
                "speaker": None,
                "generation_source": "mock",
                "generation_degraded": True,
                "generation_reason": "DASHSCOPE_API_KEY 未配置",
            }
        ]

    def polish_storyboard_prompt(self, draft_prompt: str, assets: List[Dict[str, Any]], feedback: str = "", custom_system_prompt: str = "") -> Dict[str, str]:
        """
        Polishes the storyboard prompt using Qwen-Plus, incorporating asset references.
        Returns a dict with 'prompt_cn' and 'prompt_en'.
        """
        logger.debug(f"Polishing prompt: {draft_prompt}")

        fallback_result = self._build_prompt_result(
            draft_prompt,
            draft_prompt,
            source="fallback",
            degraded=True,
            reason="LLM 未配置",
        )

        if not self.is_configured:
             return fallback_result

        # Construct context about assets
        asset_context = []
        for i, asset in enumerate(assets):
            asset_type = asset.get('type', 'Unknown')
            name = asset.get('name', 'Unknown')
            desc = asset.get('description', '')
            # Map index to "Image X"
            asset_context.append(f"Image {i+1}: {asset_type} - {name} ({desc})")

        context_str = "\n".join(asset_context)

        # Use custom prompt or default, substituting placeholders
        template = custom_system_prompt.strip() if custom_system_prompt and custom_system_prompt.strip() else DEFAULT_STORYBOARD_POLISH_PROMPT
        system_prompt = template.replace("{ASSETS}", context_str).replace("{DRAFT}", draft_prompt)

        # Build user message with optional feedback (injected in user content, not system prompt)
        user_content = system_prompt
        if feedback and feedback.strip():
            user_content += f"""
[用户反馈]
{feedback.strip()}

请根据用户反馈修改提示词，只修改用户指出的问题，保持其他部分不变。
"""

        try:
            content = self.llm.chat(
                messages=[{"role": "user", "content": user_content}],
                response_format={'type': 'json_object'},
            ).strip()
            logger.debug(f"Polished Prompt Raw: {content}")

            # Parse JSON response
            content = _strip_markdown_json(content)

            try:
                result = json.loads(content.strip())
                if "prompt_cn" in result and "prompt_en" in result:
                    logger.debug(f"Polished Prompt CN: {result['prompt_cn'][:100]}...")
                    logger.debug(f"Polished Prompt EN: {result['prompt_en'][:100]}...")
                    return self._build_prompt_result(
                        result["prompt_cn"],
                        result["prompt_en"],
                        source="llm",
                        degraded=False,
                    )
                else:
                    logger.warning("LLM response missing prompt_cn or prompt_en")
                    return fallback_result
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse polish response JSON: {e}")
                return self._build_prompt_result(
                    draft_prompt,
                    draft_prompt,
                    source="fallback",
                    degraded=True,
                    reason=f"LLM 输出不可解析: {e}",
                )
                
        except Exception as e:
            logger.error(f"Error polishing prompt: {e}", exc_info=True)
            return self._build_prompt_result(
                draft_prompt,
                draft_prompt,
                source="fallback",
                degraded=True,
                reason=str(e),
            )
    def polish_video_prompt(self, draft_prompt: str, feedback: str = "", custom_system_prompt: str = "") -> Dict[str, str]:
        """
        Polishes a video generation prompt using Qwen-Plus.
        Returns bilingual prompts {prompt_cn, prompt_en}.
        """
        fallback = self._build_prompt_result(
            draft_prompt,
            draft_prompt,
            source="fallback",
            degraded=True,
            reason="LLM 未配置",
        )

        if not self.is_configured:
            return fallback

        system_prompt = custom_system_prompt.strip() if custom_system_prompt and custom_system_prompt.strip() else DEFAULT_VIDEO_POLISH_PROMPT

        try:
            # Build user message with optional feedback
            user_message = draft_prompt
            if feedback and feedback.strip():
                user_message = f"""[当前提示词]
{draft_prompt}

[用户反馈]
{feedback.strip()}

请根据用户反馈修改提示词，只修改用户指出的问题，保持其他部分不变。"""

            content = self.llm.chat(
                messages=[
                    {'role': 'system', 'content': system_prompt},
                    {'role': 'user', 'content': user_message}
                ],
                response_format={'type': 'json_object'},
            ).strip()
            logger.debug(f"Video Prompt Polish Raw: {content[:200]}...")

            # Parse JSON
            content = _strip_markdown_json(content)

            try:
                result = json.loads(content.strip())
                if "prompt_cn" in result and "prompt_en" in result:
                    return self._build_prompt_result(
                        result["prompt_cn"],
                        result["prompt_en"],
                        source="llm",
                        degraded=False,
                    )
                else:
                    logger.warning("Video polish missing bilingual keys")
                    return fallback
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse video polish JSON: {e}")
                return self._build_prompt_result(
                    draft_prompt,
                    draft_prompt,
                    source="fallback",
                    degraded=True,
                    reason=f"LLM 输出不可解析: {e}",
                )

        except Exception:
            logger.exception("Failed to polish video prompt")
            return self._build_prompt_result(
                draft_prompt,
                draft_prompt,
                source="fallback",
                degraded=True,
                reason="视频提示词润色失败",
            )

    def polish_r2v_prompt(self, draft_prompt: str, slots: List[Dict[str, str]], feedback: str = "", custom_system_prompt: str = "") -> Dict[str, str]:
        """
        Polishes a R2V (Reference-to-Video) prompt using Qwen-Plus.
        R2V requires explicit character references using character1, character2, character3 tags.
        Returns bilingual prompts {prompt_cn, prompt_en}.
        """
        fallback = self._build_prompt_result(
            draft_prompt,
            draft_prompt,
            source="fallback",
            degraded=True,
            reason="LLM 未配置",
        )

        if not self.is_configured:
            return fallback

        # Build slot context - using character1/2/3 format
        slot_context = []
        for i, slot in enumerate(slots):
            char_id = f"character{i + 1}"
            slot_context.append(f"- {char_id}: {slot['description']}")
        slot_context_str = "\n".join(slot_context) if slot_context else "No reference videos provided."

        # Use custom prompt or default, substituting {SLOTS} placeholder
        template = custom_system_prompt.strip() if custom_system_prompt and custom_system_prompt.strip() else DEFAULT_R2V_POLISH_PROMPT
        system_prompt = template.replace("{SLOTS}", slot_context_str)

        try:
            # Build user message with optional feedback
            user_message = draft_prompt
            if feedback and feedback.strip():
                user_message = f"""[当前提示词]
{draft_prompt}

[用户反馈]
{feedback.strip()}

请根据用户反馈修改提示词，只修改用户指出的问题，保持其他部分不变。"""

            content = self.llm.chat(
                messages=[
                    {'role': 'system', 'content': system_prompt},
                    {'role': 'user', 'content': user_message}
                ],
                response_format={'type': 'json_object'},
            ).strip()
            logger.debug(f"R2V Polished Raw: {content[:200]}...")

            # Parse JSON
            content = _strip_markdown_json(content)

            try:
                result = json.loads(content.strip())
                if "prompt_cn" in result and "prompt_en" in result:
                    return self._build_prompt_result(
                        result["prompt_cn"],
                        result["prompt_en"],
                        source="llm",
                        degraded=False,
                    )
                else:
                    logger.warning("R2V polish missing bilingual keys")
                    return fallback
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse R2V polish JSON: {e}")
                return self._build_prompt_result(
                    draft_prompt,
                    draft_prompt,
                    source="fallback",
                    degraded=True,
                    reason=f"LLM 输出不可解析: {e}",
                )

        except Exception:
            logger.exception("Failed to polish R2V prompt")
            return self._build_prompt_result(
                draft_prompt,
                draft_prompt,
                source="fallback",
                degraded=True,
                reason="R2V 提示词润色失败",
            )
