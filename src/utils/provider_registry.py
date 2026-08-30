import os
from dataclasses import dataclass, field, replace
from typing import Dict, Mapping, Optional, Sequence, Tuple

from .model_catalog import build_provider_family_configs, load_generated_model_catalog

SUPPORTED_PROVIDER_BACKENDS = ("dashscope", "vendor", "mulerouter", "comfyui")


@dataclass
class ProviderFamilyConfig:
    model_family: str
    backend_default: str = "dashscope"
    backend_env_key: Optional[str] = None
    credential_sources: Dict[str, Tuple[str, ...]] = field(default_factory=dict)
    supported_modalities: Tuple[str, ...] = field(default_factory=tuple)
    image_input_mode: Dict[str, str] = field(default_factory=dict)
    audio_input_mode: Dict[str, str] = field(default_factory=dict)
    reference_video_input_mode: Dict[str, str] = field(default_factory=dict)


class ProviderRegistry:
    """Data-driven provider routing registry keyed by model family prefix."""

    def __init__(self, families: Optional[Sequence[ProviderFamilyConfig]] = None):
        self._families: Dict[str, ProviderFamilyConfig] = {}
        for family in families or ():
            self.register_family(family)

    def register_family(self, config: ProviderFamilyConfig) -> None:
        family = (config.model_family or "").strip().lower()
        if not family:
            raise ValueError("model_family cannot be empty")
        backend_default = (config.backend_default or "").strip().lower()
        if backend_default not in SUPPORTED_PROVIDER_BACKENDS:
            raise ValueError(f"Unsupported backend_default: {config.backend_default}")
        self._families[family] = replace(
            config,
            model_family=family,
            backend_default=backend_default,
        )

    def get_family_config(self, model_name: str) -> ProviderFamilyConfig:
        normalized = (model_name or "").strip().lower()
        if not normalized:
            raise ValueError("model_name cannot be empty")

        for family in sorted(self._families.keys(), key=len, reverse=True):
            if normalized.startswith(family):
                return self._families[family]
        raise KeyError(f"No provider family registered for model '{model_name}'")

    def resolve_backend(self, model_name: str, env: Optional[Mapping[str, str]] = None) -> str:
        family = self.get_family_config(model_name)
        mode = ""
        if family.backend_env_key:
            env_mapping = env if env is not None else os.environ
            mode = (env_mapping.get(family.backend_env_key) or "").strip().lower()

        if mode in SUPPORTED_PROVIDER_BACKENDS:
            return mode
        return family.backend_default


DEFAULT_PROVIDER_FAMILIES: Tuple[ProviderFamilyConfig, ...] = (
    ProviderFamilyConfig(
        model_family="comfyui",
        backend_default="comfyui",
        backend_env_key="COMFYUI_PROVIDER_MODE",
        credential_sources={
            "comfyui": ("COMFYUI_BASE_URL",),
        },
        supported_modalities=("t2i", "i2i", "image", "i2v", "r2v", "t2v"),
        image_input_mode={
            "comfyui": "comfyui_local_upload",
        },
        audio_input_mode={
            "comfyui": "comfyui_local_upload",
        },
        reference_video_input_mode={
            "comfyui": "comfyui_local_upload",
        },
    ),
    ProviderFamilyConfig(
        model_family="wan2.7-",
        backend_default="dashscope",
        credential_sources={
            "dashscope": ("DASHSCOPE_API_KEY",),
        },
        supported_modalities=("t2i", "i2i", "image", "i2v", "r2v"),
        image_input_mode={
            "dashscope": "dashscope_multimodal_message",
        },
        audio_input_mode={
            "dashscope": "dashscope_temp_file_url",
        },
        reference_video_input_mode={
            "dashscope": "dashscope_temp_file_url",
        },
    ),
    ProviderFamilyConfig(
        model_family="wan2.6-",
        backend_default="dashscope",
        credential_sources={
            "dashscope": ("DASHSCOPE_API_KEY",),
        },
        supported_modalities=("t2i", "i2i", "i2v", "r2v"),
        image_input_mode={
            "dashscope": "dashscope_multimodal_message",
        },
        audio_input_mode={
            "dashscope": "dashscope_temp_file_url",
        },
        reference_video_input_mode={
            "dashscope": "dashscope_temp_file_url",
        },
    ),
    ProviderFamilyConfig(
        model_family="qwen-image-",
        backend_default="dashscope",
        credential_sources={
            "dashscope": ("DASHSCOPE_API_KEY",),
        },
        supported_modalities=("t2i", "i2i", "image"),
        image_input_mode={
            "dashscope": "dashscope_multimodal_message",
        },
        audio_input_mode={
            "dashscope": "dashscope_temp_file_url",
        },
        reference_video_input_mode={
            "dashscope": "dashscope_temp_file_url",
        },
    ),
    ProviderFamilyConfig(
        model_family="kling/kling-",
        backend_default="dashscope",
        backend_env_key="KLING_PROVIDER_MODE",
        credential_sources={
            "dashscope": ("DASHSCOPE_API_KEY",),
            "vendor": ("KLING_ACCESS_KEY", "KLING_SECRET_KEY"),
        },
        supported_modalities=("t2v", "i2v", "r2v"),
        image_input_mode={
            "dashscope": "dashscope_image_to_video",
            "vendor": "kling_vendor_base64_image",
        },
        audio_input_mode={
            "dashscope": "dashscope_temp_file_url",
            "vendor": "kling_vendor_audio_url",
        },
        reference_video_input_mode={
            "dashscope": "dashscope_temp_file_url",
            "vendor": "kling_vendor_video_url",
        },
    ),
    ProviderFamilyConfig(
        model_family="kling-",
        backend_default="dashscope",
        backend_env_key="KLING_PROVIDER_MODE",
        credential_sources={
            "dashscope": ("DASHSCOPE_API_KEY",),
            "vendor": ("KLING_ACCESS_KEY", "KLING_SECRET_KEY"),
        },
        supported_modalities=("t2v", "i2v", "r2v"),
        image_input_mode={
            "dashscope": "dashscope_image_to_video",
            "vendor": "kling_vendor_base64_image",
        },
        audio_input_mode={
            "dashscope": "dashscope_temp_file_url",
            "vendor": "kling_vendor_audio_url",
        },
        reference_video_input_mode={
            "dashscope": "dashscope_temp_file_url",
            "vendor": "kling_vendor_video_url",
        },
    ),
    ProviderFamilyConfig(
        model_family="vidu/vidu",
        backend_default="dashscope",
        backend_env_key="VIDU_PROVIDER_MODE",
        credential_sources={
            "dashscope": ("DASHSCOPE_API_KEY",),
            "vendor": ("VIDU_API_KEY",),
        },
        supported_modalities=("t2v", "i2v", "r2v"),
        image_input_mode={
            "dashscope": "dashscope_image_to_video",
            "vendor": "vidu_vendor_image_url",
        },
        audio_input_mode={
            "dashscope": "dashscope_temp_file_url",
            "vendor": "vidu_vendor_audio_url",
        },
        reference_video_input_mode={
            "dashscope": "dashscope_temp_file_url",
            "vendor": "vidu_vendor_video_url",
        },
    ),
    ProviderFamilyConfig(
        model_family="vidu",
        backend_default="dashscope",
        backend_env_key="VIDU_PROVIDER_MODE",
        credential_sources={
            "dashscope": ("DASHSCOPE_API_KEY",),
            "vendor": ("VIDU_API_KEY",),
        },
        supported_modalities=("t2v", "i2v", "r2v"),
        image_input_mode={
            "dashscope": "dashscope_image_to_video",
            "vendor": "vidu_vendor_image_url",
        },
        audio_input_mode={
            "dashscope": "dashscope_temp_file_url",
            "vendor": "vidu_vendor_audio_url",
        },
        reference_video_input_mode={
            "dashscope": "dashscope_temp_file_url",
            "vendor": "vidu_vendor_video_url",
        },
    ),
    ProviderFamilyConfig(
        model_family="pixverse/pixverse-",
        backend_default="dashscope",
        credential_sources={
            "dashscope": ("DASHSCOPE_API_KEY",),
        },
        supported_modalities=("t2v", "i2v", "r2v"),
        image_input_mode={
            "dashscope": "dashscope_image_to_video",
        },
        audio_input_mode={
            "dashscope": "dashscope_temp_file_url",
        },
        reference_video_input_mode={
            "dashscope": "dashscope_temp_file_url",
        },
    ),
    ProviderFamilyConfig(
        model_family="pixverse-",
        backend_default="dashscope",
        credential_sources={
            "dashscope": ("DASHSCOPE_API_KEY",),
        },
        supported_modalities=("t2v", "i2v", "r2v"),
        image_input_mode={
            "dashscope": "dashscope_image_to_video",
        },
        audio_input_mode={
            "dashscope": "dashscope_temp_file_url",
        },
        reference_video_input_mode={
            "dashscope": "dashscope_temp_file_url",
        },
    ),
)


def get_default_provider_registry() -> ProviderRegistry:
    try:
        catalog = load_generated_model_catalog()
        return ProviderRegistry(build_provider_family_configs(catalog))
    except Exception:
        return ProviderRegistry(DEFAULT_PROVIDER_FAMILIES)


def resolve_provider_backend(model_name: str, env: Optional[Mapping[str, str]] = None) -> str:
    return get_default_provider_registry().resolve_backend(model_name=model_name, env=env)


# ---------------------------------------------------------------------------
# Phase 2: Gateway metadata inspection (read-only, no routing changes)
# ---------------------------------------------------------------------------

def get_gateway_for_model(
    model_id: str,
    backend: Optional[str] = None,
) -> Optional[str]:
    """Inspect the gateway metadata for a model (flat or canonical ID).

    This is a read-only diagnostic helper.  It does NOT change routing
    behavior — current routing remains family-prefix based.
    """
    from .model_catalog import get_catalog_accessor

    accessor = get_catalog_accessor()
    canonical_id = accessor.resolve_legacy_to_canonical(model_id)
    if canonical_id is None:
        canonical_id = model_id  # already canonical or unknown

    resolved_backend = backend or "dashscope"
    return accessor.get_gateway(canonical_id, resolved_backend)
