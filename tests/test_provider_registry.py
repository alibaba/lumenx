from src.apps.comic_gen.models import ProviderBackend, ProviderRoutingConfig
from src.utils.provider_registry import ProviderFamilyConfig, ProviderRegistry, get_default_provider_registry


class TestProviderRegistryRouting:
    def test_comfyui_models_route_to_comfyui(self):
        registry = get_default_provider_registry()

        assert registry.resolve_backend("comfyui-wan2.2-i2v") == "comfyui"
        assert registry.resolve_backend("comfyui-wan2.2-t2i") == "comfyui"
        assert registry.resolve_backend("comfyui/comfyui-wan2.2-video#i2v") == "comfyui"

    def test_wan26_models_route_to_dashscope(self):
        registry = get_default_provider_registry()

        assert registry.resolve_backend("wan2.6-t2i") == "dashscope"
        assert registry.resolve_backend("wan2.6-image") == "dashscope"
        assert registry.resolve_backend("wan2.6-i2v") == "dashscope"

    def test_kling_defaults_to_dashscope_when_mode_is_unset(self):
        registry = get_default_provider_registry()

        assert registry.resolve_backend("kling-v1") == "dashscope"
        assert registry.resolve_backend("kling-v1", env={"KLING_PROVIDER_MODE": ""}) == "dashscope"

    def test_vidu_defaults_to_dashscope_when_mode_is_unset(self):
        registry = get_default_provider_registry()

        assert registry.resolve_backend("vidu2.0") == "dashscope"
        assert registry.resolve_backend("vidu2.0", env={"VIDU_PROVIDER_MODE": ""}) == "dashscope"

    def test_pixverse_defaults_to_dashscope_when_mode_is_unset(self):
        registry = get_default_provider_registry()

        assert registry.resolve_backend("pixverse-v4-i2v") == "dashscope"
        assert (
            registry.resolve_backend(
                "pixverse-v4-i2v",
                env={"PIXVERSE_PROVIDER_MODE": ""},
            )
            == "dashscope"
        )

    def test_kling_vidu_and_pixverse_can_route_to_vendor(self):
        registry = get_default_provider_registry()
        env = {
            "KLING_PROVIDER_MODE": "vendor",
            "VIDU_PROVIDER_MODE": "vendor",
            "PIXVERSE_PROVIDER_MODE": "vendor",
        }

        assert registry.resolve_backend("kling-v1", env=env) == "vendor"
        assert registry.resolve_backend("vidu2.0", env=env) == "vendor"
        # Pixverse currently has no vendor backend (catalog defines
        # supported_backends: [dashscope] only and no backend_env_key).
        # Stays on dashscope regardless of the env override.
        assert registry.resolve_backend("pixverse-v4-i2v", env=env) == "dashscope"

    def test_invalid_provider_mode_falls_back_to_default_backend(self):
        registry = get_default_provider_registry()
        env = {"KLING_PROVIDER_MODE": "not-a-valid-backend"}

        assert registry.resolve_backend("kling-v1", env=env) == "dashscope"

    def test_future_pixverse_family_can_be_registered_without_resolver_changes(self):
        registry = ProviderRegistry()
        registry.register_family(
            ProviderFamilyConfig(
                model_family="pixverse-",
                backend_default="dashscope",
                backend_env_key="PIXVERSE_PROVIDER_MODE",
                credential_sources={
                    "dashscope": ("DASHSCOPE_API_KEY",),
                    "vendor": ("PIXVERSE_API_KEY",),
                },
                supported_modalities=("t2v", "i2v"),
                image_input_mode={
                    "dashscope": "dashscope_image_input",
                    "vendor": "pixverse_vendor_image_input",
                },
                audio_input_mode={
                    "dashscope": "dashscope_temp_file_url",
                    "vendor": "pixverse_vendor_audio_url",
                },
                reference_video_input_mode={
                    "dashscope": "dashscope_temp_file_url",
                    "vendor": "pixverse_vendor_reference_video_url",
                },
            )
        )

        assert registry.resolve_backend("pixverse-v4-i2v") == "dashscope"
        assert (
            registry.resolve_backend(
                "pixverse-v4-i2v",
                env={"PIXVERSE_PROVIDER_MODE": "vendor"},
            )
            == "vendor"
        )


class TestProviderRoutingConfig:
    def test_provider_modes_default_to_dashscope(self):
        config = ProviderRoutingConfig()

        assert config.KLING_PROVIDER_MODE == ProviderBackend.DASHSCOPE
        assert config.VIDU_PROVIDER_MODE == ProviderBackend.DASHSCOPE
        assert config.PIXVERSE_PROVIDER_MODE == ProviderBackend.DASHSCOPE

    def test_provider_modes_accept_vendor_override(self):
        config = ProviderRoutingConfig(
            KLING_PROVIDER_MODE="vendor",
            VIDU_PROVIDER_MODE="vendor",
            PIXVERSE_PROVIDER_MODE="vendor",
        )

        assert config.KLING_PROVIDER_MODE == ProviderBackend.VENDOR
        assert config.VIDU_PROVIDER_MODE == ProviderBackend.VENDOR
        assert config.PIXVERSE_PROVIDER_MODE == ProviderBackend.VENDOR
