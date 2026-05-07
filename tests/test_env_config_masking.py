import asyncio
import os
import sys
import types

dashscope_module = types.ModuleType("dashscope")
dashscope_module.api_key = ""
dashscope_module.VideoSynthesis = object()
dashscope_module.ImageSynthesis = object()

dashscope_audio_module = types.ModuleType("dashscope.audio")
dashscope_tts_module = types.ModuleType("dashscope.audio.tts_v2")
dashscope_tts_module.SpeechSynthesizer = object

sys.modules.setdefault("dashscope", dashscope_module)
sys.modules.setdefault("dashscope.audio", dashscope_audio_module)
sys.modules.setdefault("dashscope.audio.tts_v2", dashscope_tts_module)

from src.apps.comic_gen import api


def test_get_env_config_masks_sensitive_values(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "secret-text-key")
    monkeypatch.setenv("OPENAI_IMAGE_API_KEY", "secret-image-key")
    monkeypatch.setenv("OPENAI_IMAGE_EDIT_API_KEY", "secret-image-edit-key")
    monkeypatch.setenv("OPENAI_TTS_API_KEY", "secret-tts-key")
    monkeypatch.setenv("OPENAI_MULTIMODAL_API_KEY", "secret-mm-key")
    monkeypatch.setenv("ARK_API_KEY", "secret-ark-key")
    monkeypatch.setenv("TOS_SECRET_ACCESS_KEY", "secret-tos-key")

    result = asyncio.run(api.get_env_config())

    assert result["OPENAI_API_KEY"] == api.MASKED_SECRET_PLACEHOLDER
    assert result["OPENAI_IMAGE_API_KEY"] == api.MASKED_SECRET_PLACEHOLDER
    assert result["OPENAI_IMAGE_EDIT_API_KEY"] == api.MASKED_SECRET_PLACEHOLDER
    assert result["OPENAI_TTS_API_KEY"] == api.MASKED_SECRET_PLACEHOLDER
    assert result["OPENAI_MULTIMODAL_API_KEY"] == api.MASKED_SECRET_PLACEHOLDER
    assert result["ARK_API_KEY"] == api.MASKED_SECRET_PLACEHOLDER
    assert result["TOS_SECRET_ACCESS_KEY"] == api.MASKED_SECRET_PLACEHOLDER


def test_update_env_config_skips_masked_secret_values(monkeypatch):
    captured = {}

    monkeypatch.setattr(api, "save_user_config", lambda config_dict: captured.update(config_dict))
    monkeypatch.setattr(api, "remove_user_config_keys", lambda keys: None)
    monkeypatch.setattr(api.OSSImageUploader, "reset_instance", lambda *args, **kwargs: None)

    result = asyncio.run(
        api.update_env_config(
            api.EnvConfig(
                IMAGE_PROVIDER="openai",
                IMAGE_EDIT_PROVIDER="openai",
                TTS_PROVIDER="openai",
                OPENAI_API_KEY=api.MASKED_SECRET_PLACEHOLDER,
                OPENAI_IMAGE_API_KEY=api.MASKED_SECRET_PLACEHOLDER,
                OPENAI_IMAGE_EDIT_API_KEY=api.MASKED_SECRET_PLACEHOLDER,
                OPENAI_TTS_API_KEY=api.MASKED_SECRET_PLACEHOLDER,
                OPENAI_MULTIMODAL_API_KEY=api.MASKED_SECRET_PLACEHOLDER,
                OPENAI_IMAGE_MODEL="qwen-image",
                OPENAI_TTS_MODEL="gpt-4o-mini-tts",
                OPENAI_MULTIMODAL_MODEL="qwen-vl-max",
                OPENAI_IMAGE_EDIT_MODEL="",
            )
        )
    )

    assert result["status"] == "success"
    assert captured["IMAGE_PROVIDER"] == "openai"
    assert captured["IMAGE_EDIT_PROVIDER"] == "openai"
    assert captured["TTS_PROVIDER"] == "openai"
    assert captured["OPENAI_IMAGE_MODEL"] == "qwen-image"
    assert captured["OPENAI_TTS_MODEL"] == "gpt-4o-mini-tts"
    assert captured["OPENAI_MULTIMODAL_MODEL"] == "qwen-vl-max"
    assert "OPENAI_API_KEY" not in captured
    assert "OPENAI_IMAGE_API_KEY" not in captured
    assert "OPENAI_IMAGE_EDIT_API_KEY" not in captured
    assert "OPENAI_TTS_API_KEY" not in captured
    assert "OPENAI_MULTIMODAL_API_KEY" not in captured


def test_get_env_config_returns_recommended_tts_and_multimodal_defaults(monkeypatch):
    monkeypatch.delenv("OPENAI_IMAGE_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_IMAGE_EDIT_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_IMAGE_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_IMAGE_EDIT_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_TTS_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_MULTIMODAL_MODEL", raising=False)
    monkeypatch.setenv("OPENAI_MODEL", "qwen3.6-plus")

    result = asyncio.run(api.get_env_config())

    assert result["IMAGE_EDIT_PROVIDER"] == "openai"
    assert result["OPENAI_IMAGE_BASE_URL"] == api.DEFAULT_OPENAI_IMAGE_BASE_URL
    assert result["OPENAI_IMAGE_EDIT_BASE_URL"] == api.DEFAULT_OPENAI_IMAGE_EDIT_BASE_URL
    assert result["OPENAI_IMAGE_MODEL"] == api.DEFAULT_OPENAI_IMAGE_MODEL
    assert result["OPENAI_IMAGE_EDIT_MODEL"] == api.DEFAULT_OPENAI_IMAGE_EDIT_MODEL
    assert result["OPENAI_TTS_MODEL"] == api.DEFAULT_OPENAI_TTS_MODEL
    assert result["OPENAI_MULTIMODAL_MODEL"] == api.DEFAULT_OPENAI_MULTIMODAL_MODEL


def test_get_env_config_does_not_mask_generic_key_as_dedicated_image_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "generic-key")
    monkeypatch.delenv("OPENAI_IMAGE_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_IMAGE_EDIT_API_KEY", raising=False)

    result = asyncio.run(api.get_env_config())

    assert result["OPENAI_API_KEY"] == api.MASKED_SECRET_PLACEHOLDER
    assert result["OPENAI_IMAGE_API_KEY"] == ""
    assert result["OPENAI_IMAGE_EDIT_API_KEY"] == ""


def test_get_user_config_path_prefers_custom_dev_config_path(monkeypatch):
    monkeypatch.setenv(api.DEV_CONFIG_PATH_ENV_VAR, "output/config/runtime.env")
    monkeypatch.setattr(api.sys, "frozen", False, raising=False)
    monkeypatch.setenv("LUMEN_X_PACKAGED", "false")

    config_path = api.get_user_config_path()

    assert config_path.endswith(os.path.join("output", "config", "runtime.env"))
    assert os.path.isabs(config_path)


def test_save_user_config_creates_parent_dir_for_custom_dev_path(monkeypatch, tmp_path):
    custom_path = tmp_path / "nested" / "config" / "runtime.env"
    monkeypatch.setenv(api.DEV_CONFIG_PATH_ENV_VAR, str(custom_path))
    monkeypatch.setattr(api.sys, "frozen", False, raising=False)
    monkeypatch.setenv("LUMEN_X_PACKAGED", "false")

    api.save_user_config({"OPENAI_IMAGE_MODEL": "nano-banana"})

    assert custom_path.exists()
    assert "OPENAI_IMAGE_MODEL='nano-banana'" in custom_path.read_text(encoding="utf-8")
