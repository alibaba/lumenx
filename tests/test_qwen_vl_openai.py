import base64
import sys
import types

from src.models.qwen_vl import QwenVLModel


PNG_1X1_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4//8/AwAI/AL+"
    "X2VINQAAAABJRU5ErkJggg=="
)


def test_qwen_vl_uses_openai_compatible_multimodal_client(monkeypatch, tmp_path):
    captured = {}

    class FakeCompletions:
        def create(self, **kwargs):
            captured["request"] = kwargs
            return types.SimpleNamespace(
                choices=[
                    types.SimpleNamespace(
                        message=types.SimpleNamespace(content="优化后的图生视频提示词")
                    )
                ]
            )

    class FakeOpenAI:
        def __init__(self, api_key=None, base_url=None, timeout=None):
            captured["init"] = {
                "api_key": api_key,
                "base_url": base_url,
                "timeout": timeout,
            }
            self.chat = types.SimpleNamespace(completions=FakeCompletions())

    fake_openai_module = types.ModuleType("openai")
    fake_openai_module.OpenAI = FakeOpenAI
    monkeypatch.setitem(sys.modules, "openai", fake_openai_module)

    monkeypatch.setenv("OPENAI_MULTIMODAL_API_KEY", "mm-key")
    monkeypatch.setenv("OPENAI_MULTIMODAL_BASE_URL", "https://mm.example.com/v1/chat/completions")
    monkeypatch.setenv("OPENAI_MULTIMODAL_MODEL", "qwen-vl-max")

    image_path = tmp_path / "frame.png"
    image_path.write_bytes(base64.b64decode(PNG_1X1_BASE64))

    model = QwenVLModel({"params": {}})
    optimized_prompt, duration = model.optimize_prompt(str(image_path), "保留人物构图，增加镜头运动")

    assert optimized_prompt == "优化后的图生视频提示词"
    assert duration >= 0
    assert captured["init"]["api_key"] == "mm-key"
    assert captured["init"]["base_url"] == "https://mm.example.com/v1"
    assert captured["request"]["model"] == "qwen-vl-max"

    content = captured["request"]["messages"][0]["content"]
    assert content[0]["type"] == "image_url"
    assert content[0]["image_url"]["url"].startswith("data:image/png;base64,")
    assert content[1]["type"] == "text"
    assert "保留人物构图，增加镜头运动" in content[1]["text"]


def test_qwen_vl_falls_back_to_dashscope_compatible_base_url(monkeypatch):
    monkeypatch.delenv("OPENAI_MULTIMODAL_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "dashscope-key")

    model = QwenVLModel({"params": {}})

    assert model.base_url.endswith("/compatible-mode/v1")


def test_qwen_vl_ignores_text_only_openai_model_when_multimodal_not_set(monkeypatch):
    monkeypatch.delenv("OPENAI_MULTIMODAL_MODEL", raising=False)
    monkeypatch.setenv("OPENAI_MODEL", "qwen3.6-plus")

    model = QwenVLModel({"params": {}})

    assert model.model_name == "qwen-vl-max"
