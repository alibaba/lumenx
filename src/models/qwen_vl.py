import base64
import logging
import mimetypes
import os
import time
from typing import Any, Tuple

from ..utils.endpoints import get_provider_base_url

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


def _first_non_empty(*values: str) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _normalize_openai_base_url(base_url: str) -> str:
    normalized = (base_url or "").strip().rstrip("/")
    for suffix in ("/chat/completions", "/responses"):
        if normalized.endswith(suffix):
            normalized = normalized[: -len(suffix)]
            break
    return normalized.rstrip("/")


def _looks_like_multimodal_model(model_name: str) -> str:
    normalized = (model_name or "").strip()
    lowered = normalized.lower()
    if not normalized:
        return ""
    if any(token in lowered for token in ("vl", "vision", "qvq")):
        return normalized
    return ""


class QwenVLModel:
    def __init__(self, config: dict):
        self.params = config.get("params", {})
        configured_model = _first_non_empty(
            self.params.get("model_name"),
            os.getenv("OPENAI_MULTIMODAL_MODEL"),
        )
        fallback_model = _first_non_empty(
            _looks_like_multimodal_model(os.getenv("OPENAI_MODEL")),
            "qwen-vl-max",
        )
        self.model_name = configured_model or fallback_model
        self._client = None

        if not configured_model and _looks_like_multimodal_model(os.getenv("OPENAI_MODEL")):
            logger.info(
                "OPENAI_MULTIMODAL_MODEL not configured; falling back to OPENAI_MODEL=%s for multimodal prompt optimization.",
                os.getenv("OPENAI_MODEL"),
            )

    @property
    def api_key(self):
        api_key = _first_non_empty(
            self.params.get("api_key"),
            os.getenv("OPENAI_MULTIMODAL_API_KEY"),
            os.getenv("OPENAI_API_KEY"),
            os.getenv("DASHSCOPE_API_KEY"),
        )
        if not api_key:
            logger.warning("Multimodal API key not found in config or environment variables.")
        return api_key

    @property
    def base_url(self) -> str:
        configured_base_url = _first_non_empty(
            self.params.get("base_url"),
            os.getenv("OPENAI_MULTIMODAL_BASE_URL"),
            os.getenv("OPENAI_BASE_URL"),
        )
        if configured_base_url:
            return _normalize_openai_base_url(configured_base_url)

        if os.getenv("DASHSCOPE_API_KEY"):
            return f"{get_provider_base_url('DASHSCOPE')}/compatible-mode/v1"

        return "https://api.openai.com/v1"

    def _get_client(self):
        """Get or create the OpenAI-compatible client (lazy, cached)."""
        if self._client is None:
            try:
                from openai import OpenAI
            except ImportError as exc:
                raise RuntimeError(
                    "openai package not installed. Run: pip install openai>=1.0.0"
                ) from exc

            self._client = OpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=120.0,
            )
        return self._client

    def _encode_image_to_base64(self, image_path: str) -> str:
        """Convert local image to base64 string."""
        with open(image_path, "rb") as image_file:
            return base64.b64encode(image_file.read()).decode("utf-8")

    def _to_image_url(self, image_path: str) -> str:
        if image_path.startswith(("http://", "https://", "data:")):
            return image_path

        base64_image = self._encode_image_to_base64(image_path)
        mime_type, _ = mimetypes.guess_type(image_path)
        return f"data:{mime_type or 'image/png'};base64,{base64_image}"

    def _extract_text(self, content: Any) -> str:
        if isinstance(content, str):
            return content.strip()

        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    parts.append(item.get("text", ""))
                elif hasattr(item, "text"):
                    parts.append(getattr(item, "text", ""))
            return "\n".join(part for part in parts if part).strip()

        return str(content or "").strip()

    def optimize_prompt(self, image_path: str, original_prompt: str) -> Tuple[str, float]:
        """
        Optimize prompt using an OpenAI-compatible multimodal model.

        Args:
            image_path: Path or URL to the reference image
            original_prompt: Original user prompt

        Returns:
            Tuple[str, float]: (optimized_prompt, duration)
        """
        start_time = time.time()
        image_url = self._to_image_url(image_path)
        system_prompt = I2V_OPTIMIZATION_PROMPT.format(original_prompt=original_prompt)

        logger.info(
            "Calling multimodal prompt optimizer with model=%s, base_url=%s",
            self.model_name,
            self.base_url,
        )

        client = self._get_client()
        response = client.chat.completions.create(
            model=self.model_name,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": image_url}},
                        {"type": "text", "text": system_prompt},
                    ],
                }
            ],
        )

        optimized_prompt = self._extract_text(response.choices[0].message.content)
        duration = time.time() - start_time
        logger.info("Optimized prompt: %s...", optimized_prompt[:100])
        return optimized_prompt, duration
