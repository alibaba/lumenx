import os
import logging
import base64
from typing import Tuple

import dashscope

logger = logging.getLogger(__name__)

# System prompt template for I2V prompt optimization
I2V_OPTIMIZATION_PROMPT = """你是一个AI视频提示词专家，我需要你帮我根据参考图和已上传的现有提示词，去优化和补充现有的让图生视频的提示词，要求：

提示词=主体(主体描述)+场景(场景描述)+运动(运动描述)+镜头语言+氛围词+风格化

主体描述:主体描述是对主体外观特征细节的描述，可通过形容词或短句列举，例如"一位身着少数民族服饰的黑发苗族少女"、"一位来自异世界的飞天仙子，身着破旧却华丽的服饰，背后展开一对由废墟碎片构成的奇异翅膀"。

场景描述:场景描述是对主体所处环境特征细节的描述，可通过形容词或短句列举

运动描述:运动描述是对运动特征细节的描述，包含运动的幅度、速率和运动作用的效果，例如"猛烈地摇摆"、"缓慢地移动"、"打碎了玻璃"。

镜头语言:镜头语言包含景别、视角、镜头、运镜等，常见镜头语言包含：特写、。

氛围词:氛围词是对预期画面氛围的描述，例如"梦幻"、"孤独"、"宏伟"，常见氛围词包含：。

风格化:风格化是对画面风格语言的描述，例如"赛博朋克"、"勾线插画"、"废土风格"，常见风格化描述包含：。

参考案例如下：
```
一位身穿轻盈白色连衣裙的长发美女，肤色白皙，眼神温柔，微笑着，神情宁静。在金色的沙滩上，阳光明媚，海浪轻拍岸边，远处是碧蓝的大海与无边的天空交接，海风轻拂。她轻轻地在沙滩上步行，步伐优雅而缓慢，时而低头踩踏着海水，留下清晰的脚印，时而抬起头看向远方，微风吹动她的长发。镜头采用中景，稍微偏低的视角，以侧面跟随镜头运作，画面随她的步伐缓缓推进。镜头会偶尔拉近，捕捉她面部的柔和表情和细微的动作变化。宁静、柔和、浪漫、梦幻。清新自然的摄影风格，带有暖色调，画面略带柔焦效果，给人一种温暖的海边度假感。
```

请你仅输出优化的提示词，不要带有任何解释或说明。

现在，请根据参考图和用户提供的现有提示词，优化和补充该提示词，使其更加详细和完整。只需要输出优化后的完整提示词，不需要其他说明。

用户现有提示词：{original_prompt}"""


class QwenVLModel:
    def __init__(self, config: dict):
        self.model_name = config.get('params', {}).get('model_name', 'qwen-vl-plus')

    @property
    def api_key(self):
        api_key = os.getenv("DASHSCOPE_API_KEY")
        if not api_key:
            logger.warning("Dashscope API Key not found in config or environment variables.")
        return api_key

    def _encode_image_to_base64(self, image_path: str) -> str:
        """Convert local image to base64 string"""
        with open(image_path, "rb") as image_file:
            return base64.b64encode(image_file.read()).decode('utf-8')

    def optimize_prompt(self, image_path: str, original_prompt: str) -> Tuple[str, float]:
        """
        Optimize prompt using Qwen-VL model
        
        Args:
            image_path: Path to the reference image
            original_prompt: Original user prompt
            
        Returns:
            Tuple[str, float]: (optimized_prompt, duration)
        """
        dashscope.api_key = self.api_key

        try:
            from dashscope import MultiModalConversation
        except ImportError:
            logger.error("dashscope package not found. Please install it.")
            raise

        import time
        start_time = time.time()

        # Prepare image URL
        if image_path.startswith('http'):
            image_url = image_path
        else:
            # Convert to base64 data URL
            base64_image = self._encode_image_to_base64(image_path)
            ext = os.path.splitext(image_path)[1].lower()
            mime_type = "image/png" if ext == ".png" else "image/jpeg"
            image_url = f"data:{mime_type};base64,{base64_image}"

        # Construct system prompt
        system_prompt = I2V_OPTIMIZATION_PROMPT.format(original_prompt=original_prompt)

        # Call Qwen-VL API
        messages = [
            {
                'role': 'user',
                'content': [
                    {'image': image_url},
                    {'text': system_prompt}
                ]
            }
        ]

        logger.info(f"Calling Qwen-VL {self.model_name} for prompt optimization...")
        response = MultiModalConversation.call(
            model=self.model_name,
            messages=messages
        )

        if response.status_code == 200:
            optimized_prompt = response.output.choices[0].message.content[0]['text']
            logger.info(f"Optimized prompt: {optimized_prompt[:100]}...")
            duration = time.time() - start_time
            return optimized_prompt, duration
        else:
            logger.error(f"Qwen-VL API error: {response}")
            raise RuntimeError(f"Qwen-VL optimization failed: {response.message}")
