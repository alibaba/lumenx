import json
import os
from typing import List, Dict, Any
import time
import uuid

# Placeholder for actual LLM client (e.g., dashscope or openai)
# from dashscope import Generation

from .models import Script, Character, Scene, Prop, StoryboardFrame, GenerationStatus

class ScriptProcessor:
    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.getenv("DASHSCOPE_API_KEY")
        # self.model = "qwen-plus"

    def parse_novel(self, title: str, text: str) -> Script:
        """
        Parses the raw novel text into a structured Script object using an LLM.
        """
import logging
import traceback

logger = logging.getLogger(__name__)

# ... (imports)

class ScriptProcessor:
    def __init__(self, api_key: str = None):
        pass
        # self.model = "qwen-plus"

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
             logger.warning("Warning: DASHSCOPE_API_KEY not set. Using mock data.")
             return self._mock_parse(title, text)

        prompt = self._construct_prompt(text)
        
        try:
            import dashscope
            dashscope.api_key = self.api_key
            
            response = dashscope.Generation.call(
                # model='deepseek-v3.2',
                model='qwen-max',
                prompt=prompt,
                result_format='message',
            )
            
            if response.status_code == 200:
                content = response.output.choices[0].message.content
                logger.info(f"LLM Response Content:\n{content}")
                
                # Clean up markdown code blocks if present
                if "```json" in content:
                    content = content.split("```json")[1].split("```")[0]
                elif "```" in content:
                    content = content.split("```")[1].split("```")[0]
                    
                data = json.loads(content.strip())
                return self._create_script_from_data(title, text, data)
            else:
                logger.error(f"LLM Call Failed: {response.code} - {response.message}")
                return self._mock_parse(title, text)
                
        except Exception as e:
            logger.error(f"Error parsing novel with LLM: {e}", exc_info=True)
            return self._mock_parse(title, text)

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
        Constructs the system prompt for the LLM.
        """
        return f"""
        You are a professional storyboard artist and scriptwriter.
        Analyze the following novel text and extract structured data for a comic/video production.
        
        IMPORTANT: All descriptive content (names, descriptions, actions, dialogue) MUST be in CHINESE (Simplified Chinese).
        
        Output strictly in valid JSON format with the following structure:
        {{
            "characters": [
                {{
                    "name": "Character Name (e.g. 'Su Yueyao', 'Su Yueyao (Wedding)')",
                    "description": "Visual description (hair, eyes, build, distinct features). DO NOT include specific facial expressions (e.g. sad, angry) or temporary actions (e.g. running, crying). Focus on permanent physical traits.",
                    "age": "Age estimate (e.g. '25')",
                    "gender": "Gender",
                    "clothing": "Default outfit description. If a character changes outfits significantly (e.g. from casual to wedding dress), create a separate character entry for each outfit variant with a distinct name (e.g. 'Name (Outfit)').",
                    "visual_weight": 5  // 1-5 importance
                }}
            ],
            "scenes": [
                {{
                    "name": "Location Name (e.g. 'Coffee Shop', 'Ancient Ruins')",
                    "description": "Visual description (lighting, mood, key elements)",
                    "visual_weight": 3
                }}
            ],
            "props": [
                {{
                    "name": "Prop Name",
                    "description": "Visual description"
                }}
            ],
            "frames": [
                {{
                    "action_description": "What happens in this shot",
                    "dialogue": "Speaker: Content (or null if none)",
                    "camera_angle": "Shot type (e.g. 'Wide Shot', 'Close Up', 'Low Angle')",
                    "camera_movement": "Camera movement (e.g. 'Pan Left', 'Zoom In', 'Static')",
                    "characters": ["Name 1", "Name 2"], // Names must match character list
                    "props": ["Prop Name"], // Names must match prop list
                    "scene": "Location Name" // Must match a scene name
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
                model='qwen-plus',
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
                print(f"DEBUG: Full LLM Response ({len(content)} chars):\n{content}\n-------------------")
                logger.info(f"Style Analysis Response:\n{content}")
                
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
                            logger.info("Attempting regex extraction of fields...")
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
    def polish_storyboard_prompt(self, draft_prompt: str, assets: List[Dict[str, Any]]) -> str:
        """
        Polishes the storyboard prompt using Qwen-Plus, incorporating asset references.
        """
        logger.info(f"Polishing prompt: {draft_prompt}")
        
        if not self.api_key:
             return draft_prompt

        # Construct context about assets
        asset_context = []
        for i, asset in enumerate(assets):
            asset_type = asset.get('type', 'Unknown')
            name = asset.get('name', 'Unknown')
            desc = asset.get('description', '')
            # Map index to "Image X"
            asset_context.append(f"Image {i+1}: {asset_type} - {name} ({desc})")
            
        context_str = "\n".join(asset_context)
        
        system_prompt = f"""You are an expert storyboard artist and prompt engineer. Your task is to rewrite a draft prompt into a high-quality image generation prompt, specifically for a multi-reference image workflow.

CONTEXT:
The user has selected specific reference images (assets) to compose a scene.
You must refer to these assets by their Image ID (e.g., "Image 1", "Image 2") when describing them in the prompt.

AVAILABLE ASSETS:
{context_str}

RULES:
1.  **Integrate Assets**: Explicitly mention "Image X" when describing the corresponding character, scene, or prop.
2.  **Natural Flow**: Do not just concatenate. Write a coherent sentence or paragraph describing the visual scene.
3.  **Enhance Detail**: Add visual details (lighting, atmosphere, emotion) based on the draft prompt, but keep the asset references clear.
4.  **No Explanations**: Return ONLY the polished prompt text.

EXAMPLES:

Input: Old man (Image 2) casting spell in front of barrier (Image 1).
Output: The old man in Image 2 stands in front of the barrier in Image 1, eyes closed tight, brows furrowed, hands clasped at his chest, fully focused on casting a spell. His whole body is surrounded by yellow sword energy.

Input: Boy (Image 1) sitting on hospital bed (Image 2).
Output: The boy from Image 1 sits on the edge of the hospital bed in Image 2, hands resting at his sides, looking down at the floor, appearing lost and overwhelmed.

Input: Street scene (Image 1) with robot (Image 2) walking.
Output: The scene in Image 1 is filled with smoke. In the distance of the street, the silhouette of the robot from Image 2 appears, walking towards the camera.

DRAFT PROMPT:
{draft_prompt}
"""

        try:
            import dashscope
            dashscope.api_key = self.api_key
            
            response = dashscope.Generation.call(
                model='qwen-plus',
                prompt=system_prompt,
                result_format='message',
            )
            
            if response.status_code == 200:
                content = response.output.choices[0].message.content.strip()
                logger.info(f"Polished Prompt: {content}")
                return content
            else:
                logger.error(f"LLM Call Failed: {response.code} - {response.message}")
                return draft_prompt
                
        except Exception as e:
            logger.error(f"Error polishing prompt: {e}", exc_info=True)
            return draft_prompt
    def polish_video_prompt(self, draft_prompt: str) -> str:
        """
        Polishes a video generation prompt using Qwen-Plus.
        """
        if not self.api_key:
            return f"Polished: {draft_prompt} (Mock)"

        system_prompt = """You are an expert video prompt engineer. Your task is to optimize a draft prompt for an Image-to-Video generation model.

GUIDELINES:
1.  **Structure**: Prompt = Motion Description + Camera Movement.
2.  **Motion Description**: Describe the dynamic action of elements (characters, objects) in the image. Use adjectives to control speed and intensity (e.g., "slowly", "rapidly", "subtle").
3.  **Camera Movement**: Explicitly state camera moves if needed (e.g., "Zoom in", "Pan left", "Static camera").
4.  **Clarity**: Be concise but descriptive. Focus on visual movement.

EXAMPLES:

*   **Zoom Out**: "A soft, round animated character with a curious expression wakes up to find their bed is a giant golden corn kernel. Camera zooms out to reveal the room is a massive corn silo, with echoes reverberating, corn kernels piled high like walls, and a beam of warm sunlight streaming from a high window, casting long shadows."
*   **Pan Left**: "Camera pans left, slowly sweeping across a luxury store window filled with glamorous models and expensive goods. The camera continues panning left, leaving the window to reveal a ragged homeless man shivering in the corner of the adjacent alley."
*   **Orbit**: "Backlit, medium shot, sunset, soft light, silhouette, center composition. Orbit camera movement. The camera follows the character from back to front, revealing a rugged cowboy clutching a holster, eyes scanning a desolate western ghost town. He wears worn brown leather, a bullet belt, and a low hat brim, his silhouette softened by the sunset. Behind him are dilapidated wooden buildings with broken windows and scattered glass, dust swirling in the wind. The camera slowly orbits from his back to his front, with light spilling from behind, creating strong dramatic contrast and a warm, desolate atmosphere."

TASK:
Rewrite the following draft prompt into a high-quality video generation prompt following the guidelines above.
Return ONLY the polished prompt text.
"""

        try:
            import dashscope
            dashscope.api_key = self.api_key

            response = dashscope.Generation.call(
                model='qwen-plus',
                messages=[
                    {'role': 'system', 'content': system_prompt},
                    {'role': 'user', 'content': draft_prompt}
                ],
                result_format='message'
            )

            if response.status_code == 200:
                return response.output.choices[0].message.content.strip()
            else:
                logger.error(f"DashScope API Error: {response.code} - {response.message}")
                raise Exception(f"DashScope API Error: {response.message}")

        except Exception as e:
            logger.error(f"Failed to polish video prompt: {e}")
            traceback.print_exc()
            raise e
