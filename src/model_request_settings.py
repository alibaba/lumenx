import os
from pathlib import Path
from typing import List

from dotenv import load_dotenv


_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_ENV_PATH = _PROJECT_ROOT / ".env"
if _ENV_PATH.exists():
    load_dotenv(_ENV_PATH, override=False)


def _split_csv(value: str, default: List[str]) -> List[str]:
    if not value:
        return default
    items = [item.strip() for item in value.split(",") if item.strip()]
    return items or default


class ModelRequestSettings:
    # DashScope request URLs
    dashscope_video_create_url: str = os.getenv(
        "DASHSCOPE_VIDEO_CREATE_URL",
        "https://dashscope.aliyuncs.com/api/v1/services/aigc/video-generation/video-synthesis",
    )
    dashscope_image_t2i_url: str = os.getenv(
        "DASHSCOPE_IMAGE_T2I_URL",
        "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation",
    )
    dashscope_image_i2i_url: str = os.getenv(
        "DASHSCOPE_IMAGE_I2I_URL",
        "https://dashscope.aliyuncs.com/api/v1/services/aigc/image-generation/generation",
    )
    dashscope_task_query_url_template: str = os.getenv(
        "DASHSCOPE_TASK_QUERY_URL_TEMPLATE",
        "https://dashscope.aliyuncs.com/api/v1/tasks/{task_id}",
    )

    # Wanx model names
    wanx_t2v_model_name_default: str = os.getenv(
        "WANX_T2V_MODEL_NAME_DEFAULT",
        "wan2.5-t2v-preview",
    )
    wanx_i2v_model_name_default: str = os.getenv(
        "WANX_I2V_MODEL_NAME_DEFAULT",
        "wan2.6-i2v",
    )
    wanx_r2v_model_name_default: str = os.getenv(
        "WANX_R2V_MODEL_NAME_DEFAULT",
        "wan2.6-r2v",
    )
    wanx_http_i2v_model_names: List[str] = _split_csv(
        os.getenv("WANX_HTTP_I2V_MODEL_NAMES", ""),
        ["wan2.6-i2v", "wan2.5-i2v"],
    )
    wanx_http_r2v_model_names: List[str] = _split_csv(
        os.getenv("WANX_HTTP_R2V_MODEL_NAMES", ""),
        ["wan2.6-r2v"],
    )

    # Wanx image model names
    wanx_image_t2i_model_name_default: str = os.getenv(
        "WANX_IMAGE_T2I_MODEL_NAME_DEFAULT",
        "wan2.6-t2i",
    )
    wanx_image_i2i_model_name_default: str = os.getenv(
        "WANX_IMAGE_I2I_MODEL_NAME_DEFAULT",
        "wan2.6-image",
    )
    wanx_image_four_ref_models: List[str] = _split_csv(
        os.getenv("WANX_IMAGE_FOUR_REF_MODELS", ""),
        ["wan2.6-image"],
    )
    wanx_image_http_t2i_model_names: List[str] = _split_csv(
        os.getenv("WANX_IMAGE_HTTP_T2I_MODEL_NAMES", ""),
        ["wan2.6-t2i"],
    )
    wanx_image_http_i2i_model_names: List[str] = _split_csv(
        os.getenv("WANX_IMAGE_HTTP_I2I_MODEL_NAMES", ""),
        ["wan2.6-image"],
    )

    # LLM model names
    llm_parse_novel_model_name: str = os.getenv("LLM_PARSE_NOVEL_MODEL_NAME", "qwen-max")
    llm_storyboard_analysis_model_name: str = os.getenv("LLM_STORYBOARD_ANALYSIS_MODEL_NAME", "qwen-max")
    llm_style_recommend_model_name: str = os.getenv("LLM_STYLE_RECOMMEND_MODEL_NAME", "qwen-plus")
    llm_storyboard_polish_model_name: str = os.getenv("LLM_STORYBOARD_POLISH_MODEL_NAME", "qwen-plus")
    llm_video_polish_model_name: str = os.getenv("LLM_VIDEO_POLISH_MODEL_NAME", "qwen-plus")
    llm_r2v_polish_model_name: str = os.getenv("LLM_R2V_POLISH_MODEL_NAME", "qwen-plus")

    # Other model providers
    qwen_vl_model_name_default: str = os.getenv("QWEN_VL_MODEL_NAME_DEFAULT", "qwen-vl-plus")
    doubao_base_url: str = os.getenv("DOUBAO_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3")
    doubao_model_name_default: str = os.getenv(
        "DOUBAO_MODEL_NAME_DEFAULT",
        "doubao-seedance-1-0-pro-fast-251015",
    )
    kling_base_url: str = os.getenv("KLING_BASE_URL", "https://api.klingai.com/v1")
    kling_model_name_default: str = os.getenv("KLING_MODEL_NAME_DEFAULT", "kling-v2-5-turbo")


MODEL_REQUEST_SETTINGS = ModelRequestSettings()
