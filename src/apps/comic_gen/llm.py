import json
import os
import time
import uuid
import logging
import traceback
import re
from typing import List, Dict, Any

from .models import Script, Character, Scene, Prop, StoryboardFrame, GenerationStatus
from ...utils import get_logger
from ...model_request_settings import MODEL_REQUEST_SETTINGS

logger = get_logger(__name__)

class ScriptProcessor:
    def __init__(self, api_key: str = None):
        self._api_key = api_key
        self.parse_novel_model_name = MODEL_REQUEST_SETTINGS.llm_parse_novel_model_name
        self.storyboard_analysis_model_name = MODEL_REQUEST_SETTINGS.llm_storyboard_analysis_model_name
        self.style_recommend_model_name = MODEL_REQUEST_SETTINGS.llm_style_recommend_model_name
        self.storyboard_polish_model_name = MODEL_REQUEST_SETTINGS.llm_storyboard_polish_model_name
        self.video_polish_model_name = MODEL_REQUEST_SETTINGS.llm_video_polish_model_name
        self.r2v_polish_model_name = MODEL_REQUEST_SETTINGS.llm_r2v_polish_model_name

    @property
    def api_key(self):
        api_key = os.getenv("DASHSCOPE_API_KEY")
        if not api_key:
            logger.warning("Warning: DASHSCOPE_API_KEY not set.")
        return api_key

    def parse_novel(self, title: str, text: str) -> Script:
        """
        Parses the raw novel text into a structured Script object using an LLM.
        """
        logger.info(f"Parsing novel: {title}...")
        
        if not self.api_key:
             logger.error("DASHSCOPE_API_KEY not set.")
             raise ValueError("DASHSCOPE_API_KEY 未配置。请在 API 配置中设置 DASHSCOPE_API_KEY 后重试。")

        prompt = self._construct_prompt(text)
        
        try:
            import dashscope
            dashscope.api_key = self.api_key
            
            response = dashscope.Generation.call(
                model=self.parse_novel_model_name,
                prompt=prompt,
                result_format='message',
            )
            
            if response.status_code == 200:
                content = response.output.choices[0].message.content
                logger.debug(f"LLM Response Content:\n{content}")
                
                # Clean up markdown code blocks if present
                if "```json" in content:
                    content = content.split("```json")[1].split("```")[0]
                elif "```" in content:
                    content = content.split("```")[1].split("```")[0]
                    
                data = json.loads(content.strip())
                return self._create_script_from_data(title, text, data)
            else:
                error_msg = f"LLM 调用失败: {response.code} - {response.message}"
                logger.error(error_msg)
                raise RuntimeError(error_msg)
                
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

    def _construct_prompt(self, text: str) -> str:
        """
        Prompt A: Entity Extractor
        Constructs the system prompt for extracting characters, scenes, and props ONLY.
        Frames are generated separately via analyze_to_storyboard (Prompt B).
        """
        return f"""
        You are a professional storyboard artist and scriptwriter.
        Analyze the following novel text and extract structured data for a comic/video production.
        
        IMPORTANT: 
        - All descriptive content (names, descriptions) MUST be in CHINESE (Simplified Chinese).
        - Extract ONLY characters, scenes, and props.
        
        Output strictly in valid JSON format with the following structure:
        {{
            "characters": [
                {{
                    "id": "char_001",
                    "name": "Character Name (e.g. '叶墨', '叶墨 (古装)')",
                    "description": "Visual description (hair, eyes, build, distinct features). DO NOT include specific facial expressions (e.g. sad, angry) or temporary actions (e.g. running, crying). Focus on permanent physical traits.",
                    "age": "Age estimate (e.g. '25')",
                    "gender": "Gender",
                    "clothing": "Default outfit description. If a character changes outfits significantly (e.g. from casual to wedding dress), create a separate character entry for each outfit variant with a distinct name (e.g. 'Name (Outfit)').",
                    "visual_weight": 5  // 1-5 importance
                }}
            ],
            "scenes": [
                {{
                    "id": "scene_001",
                    "name": "Location Name (e.g. '咖啡店', '古代遗迹')",
                    "description": "Visual description (lighting, mood, key elements)",
                    "visual_weight": 3
                }}
            ],
            "props": [
                {{
                    "id": "prop_001",
                    "name": "Prop Name",
                    "description": "Visual description"
                }}
            ]
        }}

        Text:
        {text}
        """

    def analyze_script_for_styles(self, script_text: str) -> List[Dict[str, Any]]:
        """使用 LLM 分析剧本并推荐视觉风格"""
        
        logger.info("Analyzing script for visual style recommendations...")
        
        if not self.api_key:
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
            import dashscope
            dashscope.api_key = self.api_key
            
            response = dashscope.Generation.call(
                model=self.style_recommend_model_name,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                result_format='message',
                # Enable JSON mode to ensure valid JSON output
                response_format={'type': 'json_object'}
            )
            
            if response.status_code == 200:
                content = response.output.choices[0].message.content
                logger.debug(f"Style Analysis Response:\n{content}")
                
                # Clean up markdown code blocks if present
                if "```json" in content:
                    content = content.split("```json")[1].split("```")[0]
                elif "```" in content:
                    content = content.split("```")[1].split("```")[0]
                
                # Additional cleanup: remove any leading/trailing whitespace and newlines
                content = content.strip()
                
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
            else:
                logger.error(f"LLM Call Failed: {response.code} - {response.message}")
                return self._mock_style_recommendations()
                
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
    
    def analyze_to_storyboard(self, text: str, entities_json: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Analyzes script text and generates storyboard frames using Prompt B (Storyboard Director).
        Returns a list of frame dictionaries with visual atoms.
        """
        logger.info(f"Analyzing text to storyboard: {text[:100]}...")
        
        if not self.api_key:
            logger.warning("DASHSCOPE_API_KEY not set. Returning mock frames.")
            return self._mock_storyboard_frames(text)
        
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
        
        system_prompt = f"""
# 角色
你是一名电影级的分镜师（Storyboard Artist）和导演。你的任务是将剧本文本拆解为可供 AI 视频模型生成的一系列精细分镜帧。

# 任务目标
不仅仅是提取文本，而是要进行**视觉化拆解**。你需要将剧本中的文字转化为一系列连续的、单一动作的视觉画面。

# 剧本格式说明
剧本遵循以下格式：
- **场景标题行**: `1-1 地点名称 [时间] [内/外]` 
- **人物行**: `人物： 角色名1，角色名2`
- **动作描述**: 以 `△` 开头，描述画面中发生的动作
- **对话**: `角色名（情绪）： 对话内容`，或 `角色名 (V.O.)：` 表示画外音

# 已提取的实体上下文
{entities_str}

# 核心规则 (CRITICAL)
1. **视觉节拍拆解 (VISUAL ATOMIZATION)**:
   - 如果一行动作描述包含多个连续动作，**必须**将其拆分为多个分镜帧。
   - 每个分镜只应包含一个清晰的主要动作，时长控制在 3-5 秒。
2. **合并动作描述 (MERGE ACTION)**:
   - **`action_description` 字段必须包含画面中发生的所有动态要素**。
   - 包括：人物的神态/微表情 + 肢体动作 + 道具的物理运动（如手机震动、烟雾缭绕）。
   - 不要遗漏非人物主体的动作（如“车门打开”、“杯子摔碎”）。

3. **角色可见性**:
   - `character_ref_names` 只列出**当前分镜画面中可见**的角色。

4. **实体约束**: 
   - 场景名、角色名、道具名必须严格匹配"已提取的实体"。

5. **语言**: 所有输出必须使用简体中文。

# 输出格式
返回一个包含 `frames` 数组的 JSON 对象。不要包含 Markdown 格式标记（如 ```json）。

{{
    "frames": [
        {{
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
{text}
"""

        try:
            import dashscope
            dashscope.api_key = self.api_key
            
            response = dashscope.Generation.call(
                model=self.storyboard_analysis_model_name,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": "请开始生成分镜帧列表，确保覆盖剧本中的所有内容。"}
                ],
                result_format='message',
                # response_format={'type': 'json_object'} # Removed to allow freer generation
            )
            
            if response.status_code == 200:
                content = response.output.choices[0].message.content.strip()
                logger.debug(f"Storyboard Analysis Raw Response: {content[:500]}...")
                
                # Parse JSON
                if "```json" in content:
                    content = content.split("```json")[1].split("```")[0]
                elif "```" in content:
                    content = content.split("```")[1].split("```")[0]
                
                try:
                    result = json.loads(content.strip())
                    frames = result.get("frames", [])
                    logger.info(f"Storyboard Analysis generated {len(frames)} frames")
                    return frames
                except json.JSONDecodeError as e:
                    logger.error(f"Failed to parse storyboard analysis JSON: {e}")
                    return self._mock_storyboard_frames(text)
            else:
                logger.error(f"LLM Call Failed: {response.code} - {response.message}")
                return self._mock_storyboard_frames(text)
                
        except Exception as e:
            logger.error(f"Error in storyboard analysis: {e}", exc_info=True)
            return self._mock_storyboard_frames(text)
    
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

    def polish_storyboard_prompt(self, draft_prompt: str, assets: List[Dict[str, Any]]) -> Dict[str, str]:
        """
        Polishes the storyboard prompt using Qwen-Plus, incorporating asset references.
        Returns a dict with 'prompt_cn' and 'prompt_en'.
        """
        logger.debug(f"Polishing prompt: {draft_prompt}")
        
        fallback_result = {"prompt_cn": draft_prompt, "prompt_en": draft_prompt}
        
        if not self.api_key:
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
        
        system_prompt = f"""
# ROLE
You are an expert storyboard artist and prompt engineer. Your task is to rewrite a draft prompt into a high-quality image generation prompt, specifically for a multi-reference image workflow.

# CONTEXT:
The user has selected specific reference images (assets) to compose a scene.
You must refer to these assets by their Image ID (e.g., "Image 1", "Image 2") when describing them in the prompt.

# AVAILABLE ASSETS:
{context_str}

# RULES:
1.  **Integrate Assets**: Explicitly mention "Image X" when describing the corresponding character, scene, or prop.
2.  **Natural Flow**: Do not just concatenate. Write a coherent sentence or paragraph describing the visual scene.
3.  **Strict Adherence**: DO NOT hallucinate emotions, actions, or plot details not present in the draft. If the draft says "sitting", do NOT add "sadly" or "happily" unless specified. Keep the narrative neutral and accurate.
4.  **Enhance Detail**: Add visual details (lighting, atmosphere, emotion) based on the draft prompt, but keep the asset references clear.
5.  **No Explanations**: Return ONLY the polished prompt text.
6.  **Bilingual Output**: 
    - **Prompt CN**: Fluent Chinese, strictly following the content of the draft.
    - **Prompt EN**: Natural English description, prioritizing visual atmosphere.

# OUTPUT FORMAT
Return STRICTLY a JSON object:
{{
    "prompt_cn": "Chinese description with Image X references...",
    "prompt_en": "English cinematic description with Image X references..."
}}

# EXAMPLES
**Input Draft**: Boy (Image 1) sitting on hospital bed (Image 2).
**Output**:
{{
    "prompt_cn": "图像1中的男孩坐在图像2的病床边缘。病房内光线柔和，自然光从侧面照射在男孩身上，勾勒出真实的轮廓。画面构图稳定，质感写实。",
    "prompt_en": "The boy from Image 1 is seated on the edge of the hospital bed in Image 2. Soft natural light illuminates the scene from the side, highlighting the fabric textures of the bedding and the realistic skin tone of the boy. Cinematic composition, high resolution, photorealistic."
}}

# USER DRAFT PROMPT
{draft_prompt}
"""

        try:
            import dashscope
            dashscope.api_key = self.api_key
            
            response = dashscope.Generation.call(
                model=self.storyboard_polish_model_name,
                prompt=system_prompt,
                result_format='message',
                response_format={'type': 'json_object'}
            )
            
            if response.status_code == 200:
                content = response.output.choices[0].message.content.strip()
                logger.debug(f"Polished Prompt Raw: {content}")
                
                # Parse JSON response
                if "```json" in content:
                    content = content.split("```json")[1].split("```")[0]
                elif "```" in content:
                    content = content.split("```")[1].split("```")[0]
                
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
            else:
                logger.error(f"LLM Call Failed: {response.code} - {response.message}")
                return fallback_result
                
        except Exception as e:
            logger.error(f"Error polishing prompt: {e}", exc_info=True)
            return fallback_result
    def polish_video_prompt(self, draft_prompt: str) -> Dict[str, str]:
        """
        Polishes a video generation prompt using Qwen-Plus.
        Returns bilingual prompts {prompt_cn, prompt_en}.
        """
        fallback = {"prompt_cn": draft_prompt, "prompt_en": draft_prompt}
        
        if not self.api_key:
            return fallback

        system_prompt = """You are an expert video prompt engineer. Your task is to optimize a draft prompt for an Image-to-Video generation model.

GUIDELINES:
1.  **Structure**: Prompt = Motion Description + Camera Movement.
2.  **Motion Description**: Describe the dynamic action of elements (characters, objects) in the image. Use adjectives to control speed and intensity (e.g., "slowly", "rapidly", "subtle").
3.  **Camera Movement**: Explicitly state camera moves if needed (e.g., "Zoom in", "Pan left", "Static camera").
4.  **Clarity**: Be concise but descriptive. Focus on visual movement.

EXAMPLES:

*   **Zoom Out**: "A soft, round animated character with a curious expression wakes up to find their bed is a giant golden corn kernel. Camera zooms out to reveal the room is a massive corn silo, with echoes reverberating, corn kernels piled high like walls, and a beam of warm sunlight streaming from a high window, casting long shadows."
*   **Pan Left**: "Camera pans left, slowly sweeping across a luxury store window filled with glamorous models and expensive goods. The camera continues panning left, leaving the window to reveal a ragged homeless man shivering in the corner of the adjacent alley."

TASK:
Rewrite the following draft prompt into a high-quality video generation prompt following the guidelines above.

OUTPUT FORMAT:
Return STRICTLY a JSON object:
{
    "prompt_cn": "润色后的中文视频提示词，关注运动和镜头",
    "prompt_en": "Polished English video prompt, focusing on motion and camera"
}
"""

        try:
            import dashscope
            dashscope.api_key = self.api_key

            response = dashscope.Generation.call(
                model=self.video_polish_model_name,
                messages=[
                    {'role': 'system', 'content': system_prompt},
                    {'role': 'user', 'content': draft_prompt}
                ],
                result_format='message',
                response_format={'type': 'json_object'}
            )

            if response.status_code == 200:
                content = response.output.choices[0].message.content.strip()
                logger.debug(f"Video Prompt Polish Raw: {content[:200]}...")
                
                # Parse JSON
                if "```json" in content:
                    content = content.split("```json")[1].split("```")[0]
                elif "```" in content:
                    content = content.split("```")[1].split("```")[0]
                
                try:
                    result = json.loads(content.strip())
                    if "prompt_cn" in result and "prompt_en" in result:
                        return result
                    else:
                        logger.warning("Video polish missing bilingual keys")
                        return fallback
                except json.JSONDecodeError as e:
                    logger.error(f"Failed to parse video polish JSON: {e}")
                    return fallback
            else:
                logger.error(f"DashScope API Error: {response.code} - {response.message}")
                return fallback

        except Exception:
            logger.exception("Failed to polish video prompt")
            return fallback

    def polish_r2v_prompt(self, draft_prompt: str, slots: List[Dict[str, str]]) -> Dict[str, str]:
        """
        Polishes a R2V (Reference-to-Video) prompt using Qwen-Plus.
        R2V requires explicit character references using character1, character2, character3 tags.
        Returns bilingual prompts {prompt_cn, prompt_en}.
        """
        fallback = {"prompt_cn": draft_prompt, "prompt_en": draft_prompt}
        
        if not self.api_key:
            return fallback

        # Build slot context - using character1/2/3 format
        slot_context = []
        for i, slot in enumerate(slots):
            char_id = f"character{i + 1}"
            slot_context.append(f"- {char_id}: {slot['description']}")
        slot_context_str = "\n".join(slot_context) if slot_context else "No reference videos provided."

        system_prompt = f"""# Role
You are a prompt engineer for the Wan 2.6 Reference-to-Video model.

# Context
The R2V (Reference-to-Video) model generates video clips by combining reference character videos with a text prompt.
The user has uploaded the following reference videos:
{slot_context_str}

# Task
Rewrite the user's input prompt into a structured format strictly following these rules:

1. **REPLACE character names with their ID**: Use "character1" for the first character, "character2" for the second, "character3" for the third.
2. **STRUCTURE**: Use this format:
   - Scene setup (environment, lighting, mood)
   - Character action (what character1/character2/character3 are doing, their expressions, movements)
   - Camera movement (if applicable)
3. **DIALOGUE FORMAT**: If the prompt includes dialogue, format it as: 'character1 says: "dialogue content"'
4. **PRESERVE**: Keep the original intent and emotional tone.
5. **ENHANCE**: Add visual details for dramatic effect (lighting, speed descriptors like "slowly", "rapidly").

# Output Format
Return STRICTLY a JSON object:
{{
    "prompt_cn": "润色后的中文提示词，使用 character1/character2/character3 格式",
    "prompt_en": "Polished English prompt using character1/character2/character3 format"
}}

# Examples

INPUT: 主角从门里跳出来说话
SLOTS: character1 = "White rabbit", character2 = "Robot dog"
OUTPUT:
{{
    "prompt_cn": "character1 从门里猛然跳出，落地时耳朵竖起，充满活力。房间昏暗，温暖的光线从尘土飞扬的窗户中透入。character1 兴奋地环顾四周说道：'我正好赶上了！' 镜头随着跳跃略微倾斜。",
    "prompt_en": "character1 bursts through the door with an exaggerated jump, landing energetically with ears perked up. The room is dimly lit with warm ambient light streaming through dusty windows. character1 looks around excitedly and says: 'I made it just in time!' Camera follows the jump with a slight tilt."
}}
"""

        try:
            import dashscope
            dashscope.api_key = self.api_key

            response = dashscope.Generation.call(
                model=self.r2v_polish_model_name,
                messages=[
                    {'role': 'system', 'content': system_prompt},
                    {'role': 'user', 'content': draft_prompt}
                ],
                result_format='message',
                response_format={'type': 'json_object'}
            )

            if response.status_code == 200:
                content = response.output.choices[0].message.content.strip()
                logger.debug(f"R2V Polished Raw: {content[:200]}...")
                
                # Parse JSON
                if "```json" in content:
                    content = content.split("```json")[1].split("```")[0]
                elif "```" in content:
                    content = content.split("```")[1].split("```")[0]
                
                try:
                    result = json.loads(content.strip())
                    if "prompt_cn" in result and "prompt_en" in result:
                        return result
                    else:
                        logger.warning("R2V polish missing bilingual keys")
                        return fallback
                except json.JSONDecodeError as e:
                    logger.error(f"Failed to parse R2V polish JSON: {e}")
                    return fallback
            else:
                logger.error(f"DashScope API Error: {response.code} - {response.message}")
                return fallback

        except Exception:
            logger.exception("Failed to polish R2V prompt")
            return fallback
