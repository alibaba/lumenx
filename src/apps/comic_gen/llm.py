import json
import os
import time
import uuid
import logging
import traceback
import re
from difflib import SequenceMatcher
from typing import List, Dict, Any, Optional

from .models import Script, Character, Scene, Prop, StoryboardFrame, GenerationStatus


def _strip_markdown_json(content: str) -> str:
    """Strip markdown code fences from LLM JSON output."""
    if "```json" in content:
        content = content.split("```json")[1].split("```")[0]
    elif "```" in content:
        content = content.split("```")[1].split("```")[0]
    return content.strip()


class PolishError(Exception):
    """提示词润色失败的结构化异常。
    旧实现遇到任何问题都静默返回原文（fallback），导致前端无法判断
    "模型真润色完了" 还是 "出错了只是把原文吐回来"。这个异常把失败
    原因显式抛给上游，让 API 层翻译成 HTTP 502 + 结构化 JSON，
    前端按 reason 渲染对应错误/警告 UI。

    Reasons:
      - is_configured_false: LLM 未配置（缺 API key）
      - api_error: 上游 API 调用本身失败（网络/鉴权/限流/模型不可用）
      - json_parse_error: 模型返回内容不是合法 JSON
      - missing_keys: JSON 缺 prompt_cn 或 prompt_en
      - model_echo: 模型几乎原文返回（warning 级别，不是 hard error，
        前端展示为黄色警告 + 保留原文双语，让用户追加 feedback 重试）
    """

    def __init__(
        self,
        reason: str,
        message_zh: str,
        message_en: str,
        prompt_cn: str = "",
        prompt_en: str = "",
    ):
        self.reason = reason
        self.message_zh = message_zh
        self.message_en = message_en
        self.prompt_cn = prompt_cn
        self.prompt_en = prompt_en
        super().__init__(f"[{reason}] {message_en}")


def _is_echo(result_en: str, draft_en: str, threshold: float = 0.95) -> bool:
    """判断 LLM 输出是否与原文几乎相同（模型未做修改）。
    threshold 0.95 经验值：低于会误伤"做了轻微改动"的合理结果；
    高于则放过明显的"换几个标点就交差"。"""
    a = (result_en or "").strip().lower()
    b = (draft_en or "").strip().lower()
    if not a or not b:
        return False
    if a == b:
        return True
    return SequenceMatcher(None, a, b).ratio() >= threshold


def _resolve_image_for_vision(url: str) -> Optional[str]:
    """把任意形式的图像 URL 规整成 vision API 能直接消费的形式。
      - 已是 http(s):// 或 data:image/ → 原样返回（DashScope 能 fetch / 已内联）
      - 看起来是相对路径（output/* 或 /files/* 或裸文件名）→ 读本地文件做 base64 data URI
      - 找不到本地文件 → 返回 None，调用方应跳过这一张
    DashScope 无法访问 localhost 或私有 OSS 路径，所以本地路径必须 inline。"""
    import base64
    if not url or not isinstance(url, str):
        return None
    s = url.strip()
    if not s:
        return None
    if s.startswith("http://") or s.startswith("https://") or s.startswith("data:image/"):
        return s
    # 规整：'/files/foo/bar.png' → 'foo/bar.png'；'output/foo.png' 原样
    cleaned = s
    for prefix in ("/files/outputs/", "/files/output/", "/files/", "files/", "output/"):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix):]
            break
    # 只允许解析到 output/ 目录内的文件，防止路径穿越（../../etc/passwd）
    out_base = os.path.realpath("output")
    abs_path = None
    for candidate in (os.path.join(out_base, cleaned), cleaned):
        resolved = os.path.realpath(candidate)
        if not resolved.startswith(out_base + os.sep):
            continue
        if os.path.isfile(resolved):
            abs_path = resolved
            break
    if not abs_path:
        return None
    ext = os.path.splitext(abs_path)[1].lower()
    mime = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }.get(ext, "image/png")
    try:
        with open(abs_path, "rb") as fh:
            b64 = base64.b64encode(fh.read()).decode("ascii")
        return f"data:{mime};base64,{b64}"
    except OSError:
        return None


from ...utils import get_logger

logger = get_logger(__name__)

# ── Default system prompts for polish/refine stages ──────────────────────
# These are the built-in defaults. Users can override per-project via PromptConfig.
# Placeholders: {ASSETS} = asset context, {DRAFT} = draft prompt, {SLOTS} = R2V slot context

DEFAULT_STORYBOARD_POLISH_PROMPT = """
# 角色
你是一名资深分镜师与提示词工程师。你的任务是把一段草稿提示词改写成高质量的图像生成提示词，专用于多参考图（multi-reference）工作流。

# 背景
用户已选定若干参考图（素材）来构成一个画面。
在提示词中描述这些素材时，你必须用它们的 Image ID（如 "Image 1"、"Image 2"）来指代。

# 可用素材
{ASSETS}

# 规则
1.  **整合素材**：描述对应的角色、场景或道具时，显式写出 "Image X"。
2.  **自然连贯**：不要简单拼接。要写成连贯的句子或段落来描述视觉画面。
3.  **严格忠实**：不要臆造草稿中不存在的情绪、动作或剧情。若草稿写 "坐着"，不要擅自加 "悲伤地" 或 "开心地"，除非草稿明确写明。保持叙述中性、准确。
4.  **丰富细节**：可基于草稿补充视觉细节（光线、氛围、情绪），但要保持素材指代清晰。
5.  **不要解释**：只返回润色后的提示词文本。
6.  **双语输出**：
    - **Prompt CN**：流畅中文，严格遵循草稿内容。
    - **Prompt EN**：自然英文描述，优先体现视觉氛围。

# 输出格式
严格返回一个 JSON 对象：
{{
    "prompt_cn": "含 Image X 指代的中文描述……",
    "prompt_en": "English cinematic description with Image X references..."
}}

# 示例
**输入草稿**：男孩（Image 1）坐在病床（Image 2）上。
**输出**：
{{
    "prompt_cn": "图像1中的男孩坐在图像2的病床边缘。病房内光线柔和，自然光从侧面照射在男孩身上，勾勒出真实的轮廓。画面构图稳定，质感写实。",
    "prompt_en": "The boy from Image 1 is seated on the edge of the hospital bed in Image 2. Soft natural light illuminates the scene from the side, highlighting the fabric textures of the bedding and the realistic skin tone of the boy. Cinematic composition, high resolution, photorealistic."
}}

# 用户草稿提示词
{DRAFT}
""".strip()

DEFAULT_VIDEO_POLISH_PROMPT = """你是一名资深视频提示词工程师。你的任务是为「图生视频」（Image-to-Video）模型优化一段草稿提示词。

准则：
1.  **结构**：提示词 = 运动描述 + 镜头运动。
2.  **运动描述**：描述图像中各元素（角色、物体）的动态动作。用副词控制速度与强度（如 "缓慢地"、"快速地"、"轻微的"）。
3.  **镜头运动**：如有需要，显式说明镜头移动（如 "推近"、"向左平移"、"固定镜头"）。
4.  **清晰**：简洁但具体，聚焦视觉运动。

示例：

*   **拉远（Zoom Out）**："一个柔软圆润的动画角色带着好奇的表情醒来，发现自己的床是一颗巨大的金色玉米粒。镜头拉远，露出整个房间原来是一座巨大的玉米仓，回声四起，玉米粒堆得像墙一样高，一束温暖的阳光从高处的窗户洒入，投下长长的影子。"
*   **向左平移（Pan Left）**："镜头向左平移，缓缓扫过一扇奢华的橱窗，里面满是光鲜的模特与昂贵商品。镜头继续向左，离开橱窗，露出隔壁巷子角落里一个衣衫褴褛、瑟瑟发抖的流浪汉。"

任务：
按上述准则，把下面的草稿提示词改写成高质量的视频生成提示词。

输出格式：
严格返回一个 JSON 对象：
{{
    "prompt_cn": "润色后的中文视频提示词，关注运动和镜头",
    "prompt_en": "Polished English video prompt, focusing on motion and camera"
}}"""

DEFAULT_R2V_POLISH_PROMPT = """# 角色
你是参考生视频（Reference-to-Video）模型的提示词工程师。

# 背景
R2V（Reference-to-Video）模型通过把参考角色视频与文本提示词结合来生成视频片段。
用户已上传以下参考视频：
{SLOTS}

用户输入的提示词中可能已包含写作 [characterN:name] 的参考标签（例如 [character1:小兔子]）。
这些标签是指代某个 slot 的规范写法——characterN 是模型需要的 slot 编号，:name 是便于你和
用户辨认每个 slot 对应哪个角色的可读标签。模型通过对标签内 "characterN" 的字面匹配来解析
slot，因此 :name 后缀不会干扰——它只是补充可见的上下文。

# 任务
严格遵循以下规则，把用户输入的提示词改写成结构化格式：

1. **原样保留 [characterN:name] 标签**。不要去掉方括号、slot 编号或 :name 后缀。
   只要指代 SLOTS 列表中存在的角色，就写出完整标签（例如 [character1:小兔子]）——
   绝不要只写裸的 "character1"，也绝不要只写不带标签的角色名 "小兔子"。如果输入中有
   未加括号、但能与某个 SLOTS 条目匹配的角色名，首次指代时把它转换为完整的
   [characterN:name] 形式；同一段提示词中后续指代可复用该完整标签。
   **同一角色的每次提及都复用同一个 slot 编号。** slot 编号由上面的 SLOTS 列表按角色固定——
   [character1:小兔子] 被引用三次，三次都保持 [character1:小兔子]。不要为已有 slot 的角色
   臆造新的 slot 编号（如 [character3:小兔子]）。每个 slot 与一张参考图 1:1 对应，新增 slot
   会破坏模型对参考图数量的预期。
2. **结构**：使用如下格式：
   - 场景设定（环境、光线、氛围）
   - 角色动作（[characterN:name] 在做什么、表情、动作）
   - 镜头运动（如适用）
3. **对白格式**：若提示词包含对白，按如下格式书写：
   '[character1:name] says: "对白内容"'
4. **保留意图**：保持原有意图与情绪基调。
5. **强化**：补充视觉细节以增强戏剧效果（光线、"缓慢地"/"快速地" 等速度副词）。

# 输出格式
严格返回一个 JSON 对象：
{{
    "prompt_cn": "润色后的中文提示词，保留 [characterN:name] 完整标签",
    "prompt_en": "Polished English prompt, preserving [characterN:name] tags verbatim"
}}

# 示例

输入：主角从门里跳出来说话
SLOTS：character1 = "White rabbit / 小兔子", character2 = "Robot dog / 机械狗"
输出：
{{
    "prompt_cn": "[character1:小兔子] 从门里猛然跳出，落地时耳朵竖起，充满活力。房间昏暗，温暖的光线从尘土飞扬的窗户中透入。[character1:小兔子] 兴奋地环顾四周说道：'我正好赶上了！' 镜头随着跳跃略微倾斜。",
    "prompt_en": "[character1:White rabbit] bursts through the door with an exaggerated jump, landing energetically with ears perked up. The room is dimly lit with warm ambient light streaming through dusty windows. [character1:White rabbit] looks around excitedly and says: 'I made it just in time!' Camera follows the jump with a slight tilt."
}}""".strip()


DEFAULT_ENTITY_EXTRACTION_PROMPT = """
你是一名专业的分镜师与编剧。
分析下面的小说文本，为漫画/视频制作提取结构化数据。

重要：
- 所有描述性内容（名称、描述）必须使用中文（简体中文）。
- 只提取角色（characters）、场景（scenes）和道具（props）。

严格按以下结构输出合法 JSON：
{
    "characters": [
        {
            "id": "char_001",
            "name": "角色名（如 '叶墨'、'叶墨 (古装)'）",
            "description": "外观描述（发型、眼睛、体型、显著特征）。不要包含具体的面部表情（如 悲伤、愤怒）或临时动作（如 奔跑、哭泣）。聚焦于长期固定的外形特征。",
            "age": "年龄估计（如 '25'）",
            "gender": "性别",
            "clothing": "默认服装描述。若某角色服装有显著变化（如 从便装换成婚纱），为每个服装变体单独创建一个角色条目，并取一个有区分度的名字（如 '名字 (服装)'）。",
            "visual_weight": 5  // 1-5 重要度
        }
    ],
    "scenes": [
        {
            "id": "scene_001",
            "name": "地点名（如 '咖啡店'、'古代遗迹'）",
            "description": "外观描述（光线、氛围、关键元素）",
            "visual_weight": 3
        }
    ],
    "props": [
        {
            "id": "prop_001",
            "name": "道具名",
            "description": "外观描述"
        }
    ]
}

文本：
{text}
"""


DEFAULT_STYLE_ANALYSIS_PROMPT = """你是一个专业的电影美术指导和视觉风格顾问。
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


DEFAULT_STORYBOARD_EXTRACTION_PROMPT = """# 角色
你是一名电影级的分镜师。你的任务是将剧本文本拆解为一系列连续的分镜帧。

# 核心规则
1. **视觉节拍拆解**: 一行包含多个动作时，拆为多帧。每帧仅含一个主要动作。
2. **角色可见性**: character_ref_names 只列画面中可见的角色。
3. **实体约束**: 场景名、角色名、道具名严格匹配已提取实体。
4. **语言**: 简体中文。
5. **景别枚举**: 必须从以下选项中选择: 大特写 | 特写 | 近景 | 中景 | 全景 | 远景 | 大远景
6. **角度枚举**: 必须从以下选项中选择: 平视 | 俯视 | 仰视 | 鸟瞰 | 蚁视 | 过肩 | 荷兰角 | 主观视角
7. **时长**: 基于动作复杂度估算整数秒（范围 3-10 秒）。简单静态 3-4s，标准动作 5-6s，复杂/情绪镜头 7-10s。
8. **对白**: 如果帧中有角色说话，dialogue 和 speaker 必须填写。一帧只能有一个说话人——多人对话必须拆为多帧。

# 剧本格式说明
- **场景标题行**: `1-1 地点名称 [时间] [内/外]`
- **人物行**: `人物：角色名1，角色名2`
- **动作描述**: 以 `△` 开头
- **对话**: `角色名（情绪）：对话内容`，或 `角色名 (V.O.)：` 表示画外音

# 已提取的实体
{entities_str}

# 输出格式
返回 JSON 对象 {"frames": [...]}。不要包含 Markdown 标记。

每帧字段:
{
    "scene_ref_name": "场景名",
    "character_ref_names": ["角色名"],
    "prop_ref_names": ["道具名"],
    "action_summary": "一句话概括这帧发生什么（含角色动作 + 物理事件 + 神态表情）",
    "shot_size": "中景",
    "camera_angle": "平视",
    "camera_movement": "静止",
    "dialogue": "台词内容（无对白则为 null）",
    "speaker": "说话人（无对白则为 null）",
    "duration": 5
}

# 示例
{
    "frames": [
        {
            "scene_ref_name": "卧室",
            "character_ref_names": ["叶墨"],
            "prop_ref_names": ["手机"],
            "action_summary": "手机在床头柜上震动，叶墨烦躁翻身，眉头紧锁，被子滑落",
            "shot_size": "中景",
            "camera_angle": "俯视",
            "camera_movement": "静止",
            "dialogue": "妈，这才几点啊！",
            "speaker": "叶墨",
            "duration": 4
        },
        {
            "scene_ref_name": "卧室",
            "character_ref_names": ["叶墨"],
            "prop_ref_names": ["手机"],
            "action_summary": "叶墨看到来电显示，猛地坐起，表情惊恐",
            "shot_size": "特写",
            "camera_angle": "平视",
            "camera_movement": "快速推镜",
            "dialogue": "已经来了？",
            "speaker": "叶墨",
            "duration": 3
        }
    ]
}

# 剧本内容
{text}
"""


class ScriptProcessor:
    def __init__(self, api_key: str = None):
        self._api_key = api_key
        from .llm_adapter import LLMAdapter
        self.llm = LLMAdapter()

    @property
    def is_configured(self):
        return self.llm.is_configured

    def parse_novel(self, title: str, text: str, custom_extraction_prompt: str = "") -> Script:
        """
        Parses the raw novel text into a structured Script object using an LLM.

        custom_extraction_prompt: optional per-project override for the entity
        extraction system prompt (PromptConfig.entity_extraction). Empty =
        use the built-in _construct_prompt template.
        """
        logger.info(f"Parsing novel: {title}...")
        
        if not self.is_configured:
             logger.error("LLM API key not configured.")
             raise ValueError("LLM API Key 未配置。请在 API 配置中设置对应的 API Key 后重试。")

        prompt = self._construct_prompt(text, custom_extraction_prompt)

        try:
            content = self.llm.chat(
                messages=[{"role": "user", "content": prompt}],
            )
            logger.debug(f"LLM Response Content:\n{content}")

            content = _strip_markdown_json(content)
            data = json.loads(content)
            return self._create_script_from_data(title, text, data)
                
        except json.JSONDecodeError as e:
            error_msg = f"LLM 返回的数据格式错误，无法解析 JSON: {e}"
            logger.error(error_msg, exc_info=True)
            raise RuntimeError(error_msg)
        except ValueError:
            # Re-raise ValueError (e.g., API key not set)
            raise
        except Exception as e:
            error_msg = f"剧本解析失败: {str(e)}"
            logger.error(error_msg, exc_info=True)
            raise RuntimeError(error_msg)

    def _create_script_from_data(self, title: str, original_text: str, data: Dict[str, Any]) -> Script:
        script_id = str(uuid.uuid4())
        
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
                status=GenerationStatus.PENDING
            ))
            
        return Script(
            id=script_id,
            title=title,
            original_text=original_text,
            characters=characters,
            scenes=scenes,
            props=props,
            frames=frames,
            created_at=time.time(),
            updated_at=time.time()
        )

    def create_draft_script(self, title: str, text: str) -> Script:
        """
        Creates a draft script object without LLM analysis.
        """
        return Script(
            id=str(uuid.uuid4()),
            title=title,
            original_text=text,
            characters=[],
            scenes=[],
            props=[],
            frames=[],
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
            created_at=time.time(),
            updated_at=time.time()
        )
        
        return script

    def _construct_prompt(self, text: str, custom_prompt: str = "") -> str:
        """
        Prompt A: Entity Extractor
        Constructs the system prompt for extracting characters, scenes, and props ONLY.
        Frames are generated separately via analyze_to_storyboard (Prompt B).

        custom_prompt: optional override (PromptConfig.entity_extraction). When
        provided, '{text}' is replaced by the novel content (via str.replace so
        JSON braces in the template are not parsed); if no placeholder exists the
        novel content is appended. Empty = use the built-in template below.
        """
        if custom_prompt and custom_prompt.strip():
            if "{text}" in custom_prompt:
                return custom_prompt.replace("{text}", text)
            return f"{custom_prompt}\n\nText:\n{text}"
        return DEFAULT_ENTITY_EXTRACTION_PROMPT.replace("{text}", text)

    def analyze_script_for_styles(self, script_text: str, custom_style_prompt: str = "") -> List[Dict[str, Any]]:
        """使用 LLM 分析剧本并推荐视觉风格

        custom_style_prompt: optional per-project override
        (PromptConfig.style_analysis). Empty = use the built-in system prompt.
        """
        
        logger.info("Analyzing script for visual style recommendations...")
        
        if not self.is_configured:
            logger.warning("DASHSCOPE_API_KEY not set. Returning default recommendations.")
            return self._mock_style_recommendations()
        
        if custom_style_prompt and custom_style_prompt.strip():
            system_prompt = custom_style_prompt
        else:
            system_prompt = DEFAULT_STYLE_ANALYSIS_PROMPT

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
                "is_custom": False
            },
            {
                "id": f"mock-anime-{str(uuid.uuid4())[:8]}",
                "name": "Anime Style",
                "description": "日式动漫风格，明快色彩",
                "reason": "适合充满情感表现的故事",
                "positive_prompt": "anime style, cel shading, vibrant colors, expressive, detailed character design",
                "negative_prompt": "photorealistic, 3d, blurry, washed out",
                "is_custom": False
            },
            {
                "id": f"mock-noir-{str(uuid.uuid4())[:8]}",
                "name": "Film Noir",
                "description": "黑色电影风格，高对比度",
                "reason": "适合悬疑、神秘题材的叙事",
                "positive_prompt": "black and white, film noir, high contrast, dramatic shadows, moody lighting",
                "negative_prompt": "colorful, bright, happy, modern",
                "is_custom": False
            }
        ]
    
    def analyze_to_storyboard(self, text: str, entities_json: Dict[str, Any], custom_extraction_prompt: str = "") -> List[Dict[str, Any]]:
        """
        Analyzes script text and generates storyboard frames using Prompt B (Storyboard Director).
        Returns a list of frame dictionaries with visual atoms.

        custom_extraction_prompt: optional override (PromptConfig.storyboard_extraction).
        Empty = use the built-in DEFAULT_STORYBOARD_EXTRACTION_PROMPT. The template may
        contain {entities_str} and {text} placeholders, substituted before the call.
        """
        logger.info(f"Analyzing text to storyboard: {text[:100]}...")
        
        if not self.is_configured:
            logger.warning("DASHSCOPE_API_KEY not set. Returning mock frames.")
            return self._mock_storyboard_frames(text)
        
        # Build entities context
        characters_list = entities_json.get("characters", [])
        scenes_list = entities_json.get("scenes", [])
        props_list = entities_json.get("props", [])

        entities_str = json.dumps({
            "characters": characters_list,
            "scenes": scenes_list,
            "props": props_list,
        }, ensure_ascii=False, indent=2)

        template = (
            custom_extraction_prompt
            if custom_extraction_prompt and custom_extraction_prompt.strip()
            else DEFAULT_STORYBOARD_EXTRACTION_PROMPT
        )
        system_prompt = template.replace("{entities_str}", entities_str).replace("{text}", text)

        try:
            content = self.llm.chat(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": "请开始生成分镜帧列表，确保覆盖剧本中的所有内容。"}
                ],
            ).strip()
            logger.debug(f"Storyboard Analysis Raw Response: {content[:500]}...")

            frames = self._parse_storyboard_json(content)
            if frames is not None:
                return frames

            # First parse failed — retry once with response_format constraint
            logger.warning("Storyboard JSON parse failed, retrying with response_format=json_object...")
            retry_content = self.llm.chat(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": "请开始生成分镜帧列表，确保覆盖剧本中的所有内容。请务必输出合法的JSON格式。"}
                ],
                response_format={'type': 'json_object'},
            ).strip()
            logger.debug(f"Storyboard Analysis Retry Response: {retry_content[:500]}...")
            frames = self._parse_storyboard_json(retry_content)
            if frames is not None:
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

    def _mock_storyboard_frames(self, text: str) -> List[Dict[str, Any]]:
        """Returns mock storyboard frames for testing when API is unavailable."""
        return [
            {
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
                "speaker": None
            }
        ]

    def refine_frame_to_rich(
        self,
        coarse_frame: Dict[str, Any],
        character_assets: List[Dict[str, Any]],
        scene_assets: List[Dict[str, Any]],
        prev_frame_context: Optional[str] = None,
        next_frame_context: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Phase 2: Refine a coarse frame into a rich frame with full structured fields."""
        if not self.is_configured:
            logger.warning("LLM not configured, cannot refine frame")
            return None

        system_prompt = f"""# Role
You are a film storyboard refinement specialist. Enrich one coarse frame into full structured data + visual description.

# Input Context
- Coarse frame info (scene, characters, action, shot_size, angle, duration)
- Character assets (appearance, clothing)
- Scene assets (environment, atmosphere)
- Adjacent frames for continuity

# Output
Return a JSON object with ALL fields below. null is acceptable for optional fields.

{{
    "visual_description": "Complete visual description (100-200 chars). Describe environment, character acting, physical action, lighting.",
    "shot_size": "One of: 大特写|特写|近景|中景|全景|远景|大远景",
    "camera_angle": "One of: 平视|俯视|仰视|鸟瞰|蚁视|过肩|荷兰角|主观视角",
    "camera_movement": {{
        "primary": "static|push_in|pull_out|pan_left|pan_right|tilt_up|tilt_down|orbit|follow|crane_up|crane_down|handheld|zoom_in|zoom_out",
        "secondary": null,
        "speed": "slow|normal|fast",
        "description": "Natural language description of camera motion"
    }},
    "blocking": {{
        "description": "Spatial layout description: left->right: CharA(depth) | CharB(depth)",
        "stage": [
            {{
                "ref": "character/prop name",
                "zone": "left|center|right (combinable: left-top, center-bottom)",
                "depth": "fore|mid|back",
                "height": "ground|low|surface|eye|high|overhead",
                "facing": "toward-camera|away|left|right|profile-left|profile-right",
                "posture": "standing|sitting|lying|crouching|dynamic"
            }}
        ],
        "camera_relation": "eye-level|elevated|low-angle|behind-subject|over-shoulder"
    }},
    "dialogue_structured": {{
        "speaker": "speaker name",
        "line": "dialogue text",
        "emotion": "emotion tag",
        "delivery": "delivery style (volume, speed, tone)"
    }},
    "audio_note": {{
        "sfx": "sound effect description",
        "ambience": "ambient sound",
        "bgm_note": "BGM change note or null"
    }},
    "lighting": {{
        "direction": "light source direction (left-above/right/top/back/front)",
        "quality": "soft|hard",
        "color_temp": "warm|neutral|cool",
        "description": "Natural language lighting description"
    }},
    "transition_hint": "Cut type to next frame (硬切|叠化|黑场|匹配剪辑) or null",
    "duration": 5
}}

# Rules
1. visual_description must be fluent Chinese, covering environment + performance + action + lighting feel.
2. dialogue_structured is null if this frame has no dialogue.
3. audio_note can be null.
4. blocking.stage should cover all visible characters and key props.
5. Maintain continuity with adjacent frames.
6. camera_movement has at most primary + secondary.

# Coarse Frame
{json.dumps(coarse_frame, ensure_ascii=False, indent=2)}

# Character Assets
{json.dumps(character_assets, ensure_ascii=False, indent=2)}

# Scene Assets
{json.dumps(scene_assets, ensure_ascii=False, indent=2)}

# Previous Frame Context
{prev_frame_context or "None (this is the first frame)"}

# Next Frame Context
{next_frame_context or "None (this is the last frame)"}
"""
        try:
            content = self.llm.chat(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": "Please refine this frame. Output valid JSON only, no markdown fences."}
                ],
                response_format={'type': 'json_object'},
            ).strip()
            content = _strip_markdown_json(content)
            result = json.loads(content)
            return result
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse frame refine JSON: {e}")
            return None
        except Exception as e:
            logger.error(f"Frame refine LLM call failed: {e}")
            return None

    def polish_storyboard_prompt(self, draft_prompt: str, assets: List[Dict[str, Any]], feedback: str = "", custom_system_prompt: str = "") -> Dict[str, str]:
        """
        Polishes the storyboard prompt using Qwen-Plus, incorporating asset references.
        Returns a dict with 'prompt_cn' and 'prompt_en'.
        """
        logger.debug(f"Polishing prompt: {draft_prompt}")

        fallback_result = {"prompt_cn": draft_prompt, "prompt_en": draft_prompt}

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
                    return result
                else:
                    logger.warning("LLM response missing prompt_cn or prompt_en")
                    return fallback_result
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse polish response JSON: {e}")
                return fallback_result
                
        except Exception as e:
            logger.error(f"Error polishing prompt: {e}", exc_info=True)
            return fallback_result
    def polish_video_prompt(
        self,
        draft_prompt: str,
        feedback: str = "",
        custom_system_prompt: str = "",
        prev_cn: str = "",
        image_urls: Optional[List[str]] = None,
        polish_model: str = "",
    ) -> Dict[str, str]:
        """
        Polishes a video generation prompt using Qwen.
        Returns bilingual prompts {prompt_cn, prompt_en}.

        迭代时（feedback 非空）支持传入 prev_cn 实现双语锚点：
          - 首次 polish: draft_prompt = 用户原文，prev_cn 留空
          - 迭代 polish: draft_prompt = 上次的 EN，prev_cn = 上次的 CN，
            feedback = 用户中文反馈。模型同时看到双语版，用 CN 锚点
            定位反馈意图，再同步修改双语，降低 drift。

        image_urls: I2V 模式下传入 active first frame URL（让 vision-capable
        模型真正"看见"图像，比纯文本润色质量大幅提升）。空列表/None = 走
        纯文本路径（兼容老的 t2i polish 或无 frame 的 shot）。

        polish_model: 显式覆盖 LLMAdapter 默认模型；空 = 用 system default。

        Raises:
            PolishError: 4 种失败原因，由 API 层翻译成 HTTP 502。
        """
        if not self.is_configured:
            raise PolishError(
                reason="is_configured_false",
                message_zh="LLM 未配置（缺少 DASHSCOPE_API_KEY），请到设置中检查。",
                message_en="LLM not configured (missing DASHSCOPE_API_KEY). Please check settings.",
            )

        has_images = bool(image_urls)
        system_prompt = (
            custom_system_prompt.strip()
            if custom_system_prompt and custom_system_prompt.strip()
            else DEFAULT_VIDEO_POLISH_PROMPT
        )
        # 让模型知道有图可看（仅在多模态分支才追加，避免无图时误导）。
        if has_images:
            system_prompt = (
                system_prompt
                + "\n\nIMPORTANT: The user has attached the first frame image(s) of the clip. "
                "Look at the image(s) to ground your polish — describe what is actually visible "
                "(subjects, composition, lighting, color palette) and use that to enrich the "
                "motion/camera description. Do NOT invent elements absent from the image."
            )

        # Build user TEXT message — 首次 vs 迭代不同形态
        if feedback and feedback.strip():
            if prev_cn and prev_cn.strip():
                user_text = f"""[当前提示词-CN]
{prev_cn.strip()}

[当前提示词-EN]
{draft_prompt}

[用户反馈]
{feedback.strip()}

请根据用户反馈同步修改双语版本，只修改用户指出的问题，保持其他部分不变。"""
            else:
                # 向后兼容：旧调用方未带 prev_cn 时仍可工作
                user_text = f"""[当前提示词]
{draft_prompt}

[用户反馈]
{feedback.strip()}

请根据用户反馈修改提示词，只修改用户指出的问题，保持其他部分不变。"""
        else:
            user_text = draft_prompt

        user_content: Any = user_text
        if has_images:
            # Multimodal user message — Qwen-VL & Kimi-K2 vision API
            # follow OpenAI's chat-completions multimodal schema. We
            # always put images FIRST so the model attends to them
            # before reading the text-side instructions.
            parts: List[Dict[str, Any]] = []
            for url in image_urls:
                resolved = _resolve_image_for_vision(url)
                if resolved:
                    parts.append({"type": "image_url", "image_url": {"url": resolved}})
            if not parts:
                # All image URLs failed to resolve → fall back to text-only
                # rather than crashing. log so users can diagnose later.
                logger.warning("polish: image_urls provided but none resolved; falling back to text-only")
                has_images = False
            else:
                parts.append({"type": "text", "text": user_text})
                user_content = parts

        try:
            content = self.llm.chat(
                messages=[
                    {'role': 'system', 'content': system_prompt},
                    {'role': 'user', 'content': user_content},
                ],
                model=polish_model or None,
                response_format={'type': 'json_object'},
            ).strip()
        except Exception as e:
            logger.exception("Video polish: LLM API error")
            raise PolishError(
                reason="api_error",
                message_zh=f"模型调用失败：{e}",
                message_en=f"Model call failed: {e}",
            ) from e
        logger.debug(f"Video Prompt Polish Raw: {content[:200]}...")

        content = _strip_markdown_json(content)
        try:
            result = json.loads(content.strip())
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse video polish JSON: {e}")
            raise PolishError(
                reason="json_parse_error",
                message_zh="模型返回了无效响应，建议重试或简化提示词。",
                message_en="Model returned invalid response. Try again or simplify the prompt.",
            ) from e

        if "prompt_cn" not in result or "prompt_en" not in result:
            logger.warning("Video polish missing bilingual keys")
            raise PolishError(
                reason="missing_keys",
                message_zh="模型返回了不完整的双语结果，建议重试。",
                message_en="Model returned incomplete bilingual result. Please retry.",
            )

        # Echo 检测：模型几乎原文返回。本质是 warning（带原文给前端），
        # 不是 hard error；前端按黄色警告渲染，让用户补 feedback。
        if _is_echo(result["prompt_en"], draft_prompt):
            raise PolishError(
                reason="model_echo",
                message_zh="模型未做明显修改。建议在下方反馈框补充具体要求（如运镜、光影、情绪）后重试。",
                message_en="Model made no notable changes. Add more specific feedback (camera, lighting, mood) and retry.",
                prompt_cn=result["prompt_cn"],
                prompt_en=result["prompt_en"],
            )

        return result

    def polish_r2v_prompt(
        self,
        draft_prompt: str,
        slots: List[Dict[str, str]],
        feedback: str = "",
        custom_system_prompt: str = "",
        prev_cn: str = "",
        image_urls: Optional[List[str]] = None,
        polish_model: str = "",
    ) -> Dict[str, str]:
        """
        Polishes a R2V (Reference-to-Video) prompt using Qwen.
        R2V requires explicit character references using character1, character2, character3 tags.
        Returns bilingual prompts {prompt_cn, prompt_en}.

        prev_cn: 双语锚点迭代用。详见 polish_video_prompt 文档。
        image_urls: R2V 模式下传入用户挂载的 reference image URLs，让 vision
        模型看见 character1/2/3 实际长什么样，再写出符合形象的运镜描述。
        polish_model: 显式覆盖 LLMAdapter 默认模型；空 = 用 system default。

        Raises:
            PolishError: 4 种失败原因，由 API 层翻译成 HTTP 502。
        """
        if not self.is_configured:
            raise PolishError(
                reason="is_configured_false",
                message_zh="LLM 未配置（缺少 DASHSCOPE_API_KEY），请到设置中检查。",
                message_en="LLM not configured (missing DASHSCOPE_API_KEY). Please check settings.",
            )

        has_images = bool(image_urls)

        # Build slot context - using character1/2/3 format
        slot_context = []
        for i, slot in enumerate(slots):
            char_id = f"character{i + 1}"
            slot_context.append(f"- {char_id}: {slot['description']}")
        slot_context_str = "\n".join(slot_context) if slot_context else "No reference videos provided."

        # Use custom prompt or default, substituting {SLOTS} placeholder
        template = (
            custom_system_prompt.strip()
            if custom_system_prompt and custom_system_prompt.strip()
            else DEFAULT_R2V_POLISH_PROMPT
        )
        system_prompt = template.replace("{SLOTS}", slot_context_str)
        if has_images:
            system_prompt = (
                system_prompt
                + "\n\nIMPORTANT: The user has attached the reference image(s) for character1/2/3 above. "
                "Look at the images to understand each character's actual appearance, costume, and pose. "
                "Use that grounding when writing the action / camera description so the polished prompt "
                "is faithful to what the references show. Do NOT contradict visible details."
            )

        if feedback and feedback.strip():
            if prev_cn and prev_cn.strip():
                user_text = f"""[当前提示词-CN]
{prev_cn.strip()}

[当前提示词-EN]
{draft_prompt}

[用户反馈]
{feedback.strip()}

请根据用户反馈同步修改双语版本，只修改用户指出的问题，保持其他部分不变。"""
            else:
                user_text = f"""[当前提示词]
{draft_prompt}

[用户反馈]
{feedback.strip()}

请根据用户反馈修改提示词，只修改用户指出的问题，保持其他部分不变。"""
        else:
            user_text = draft_prompt

        user_content: Any = user_text
        if has_images:
            parts: List[Dict[str, Any]] = []
            for url in image_urls:
                resolved = _resolve_image_for_vision(url)
                if resolved:
                    parts.append({"type": "image_url", "image_url": {"url": resolved}})
            if not parts:
                # All image URLs failed to resolve → fall back to text-only
                # rather than crashing. log so users can diagnose later.
                logger.warning("polish: image_urls provided but none resolved; falling back to text-only")
                has_images = False
            else:
                parts.append({"type": "text", "text": user_text})
                user_content = parts

        try:
            content = self.llm.chat(
                messages=[
                    {'role': 'system', 'content': system_prompt},
                    {'role': 'user', 'content': user_content},
                ],
                model=polish_model or None,
                response_format={'type': 'json_object'},
            ).strip()
        except Exception as e:
            logger.exception("R2V polish: LLM API error")
            raise PolishError(
                reason="api_error",
                message_zh=f"模型调用失败：{e}",
                message_en=f"Model call failed: {e}",
            ) from e
        logger.debug(f"R2V Polished Raw: {content[:200]}...")

        content = _strip_markdown_json(content)
        try:
            result = json.loads(content.strip())
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse R2V polish JSON: {e}")
            raise PolishError(
                reason="json_parse_error",
                message_zh="模型返回了无效响应，建议重试或简化提示词。",
                message_en="Model returned invalid response. Try again or simplify the prompt.",
            ) from e

        if "prompt_cn" not in result or "prompt_en" not in result:
            logger.warning("R2V polish missing bilingual keys")
            raise PolishError(
                reason="missing_keys",
                message_zh="模型返回了不完整的双语结果，建议重试。",
                message_en="Model returned incomplete bilingual result. Please retry.",
            )

        if _is_echo(result["prompt_en"], draft_prompt):
            raise PolishError(
                reason="model_echo",
                message_zh="模型未做明显修改。建议在下方反馈框补充具体要求（如运镜、光影、情绪）后重试。",
                message_en="Model made no notable changes. Add more specific feedback (camera, lighting, mood) and retry.",
                prompt_cn=result["prompt_cn"],
                prompt_en=result["prompt_en"],
            )

        return result
