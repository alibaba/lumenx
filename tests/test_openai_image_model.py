import base64
from io import BytesIO
import os

import pytest
import requests
from PIL import Image

from src.models.image import (
    DEFAULT_OPENAI_IMAGE_BASE_URL,
    DEFAULT_OPENAI_IMAGE_EDIT_BASE_URL,
    DEFAULT_OPENAI_IMAGE_EDIT_MODEL,
    DEFAULT_OPENAI_IMAGE_MODEL,
    OPENAI_EDIT_REQUEST_MAX_BYTES_ENV,
    OPENAI_EDIT_REFERENCE_MAX_BYTES,
    OPENAI_EDIT_REFERENCE_MAX_SIDE,
    OPENAI_EDIT_REFERENCE_MIN_SIDE,
    OPENAI_GPT_IMAGE_EDIT_REFERENCE_LIMIT,
    OPENAI_I2I_MODEL_ALIAS,
    OPENAI_T2I_MODEL_ALIAS,
    WanxImageModel,
)

PNG_1X1_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4//8/AwAI/AL+"
    "X2VINQAAAABJRU5ErkJggg=="
)

# Legacy compatibility coverage only.
LEGACY_OPENAI_IMAGE_MODEL_ALIAS = "gpt-image-2"


class FakeResponse:
    def __init__(self, payload, status_code=200, headers=None):
        self._payload = payload
        self.status_code = status_code
        self.text = str(payload)
        self.headers = headers or {}
        self.content = b""

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeDownloadResponse:
    def __init__(self, content: bytes, status_code: int = 200):
        self.content = content
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size=8192):
        for index in range(0, len(self.content), chunk_size):
            yield self.content[index : index + chunk_size]


def test_openai_t2i_alias_uses_openai_compatible_generation_endpoint(monkeypatch, tmp_path):
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        captured["timeout"] = timeout
        return FakeResponse({"data": [{"b64_json": PNG_1X1_BASE64}]})

    monkeypatch.setenv("IMAGE_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_IMAGE_API_KEY", "img-key")
    monkeypatch.setenv("OPENAI_IMAGE_BASE_URL", "https://image.example.com/v1")
    monkeypatch.setenv("OPENAI_IMAGE_MODEL", "qwen-image")
    monkeypatch.setattr("src.models.image.requests.post", fake_post)

    output_path = tmp_path / "generated.png"
    model = WanxImageModel({"params": {"model_name": OPENAI_T2I_MODEL_ALIAS}})

    generated_path, duration = model.generate("a silver android in snowfall", str(output_path))

    assert generated_path == str(output_path)
    assert duration >= 0
    assert output_path.exists()
    assert captured["url"] == "https://image.example.com/v1/images/generations"
    assert captured["json"]["model"] == "qwen-image"
    assert captured["json"]["size"] == "1280x1280"


def test_openai_t2i_alias_uses_gpt_image2_when_configured(monkeypatch, tmp_path):
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        captured["timeout"] = timeout
        return FakeResponse({"data": [{"b64_json": PNG_1X1_BASE64}]})

    monkeypatch.setenv("IMAGE_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_IMAGE_API_KEY", "img-key")
    monkeypatch.setenv("OPENAI_IMAGE_BASE_URL", "https://image.example.com/v1")
    monkeypatch.setenv("OPENAI_IMAGE_MODEL", "gpt-image2")
    monkeypatch.setattr("src.models.image.requests.post", fake_post)

    output_path = tmp_path / "gpt-image2.png"
    model = WanxImageModel({"params": {"model_name": OPENAI_T2I_MODEL_ALIAS}})

    generated_path, duration = model.generate("a silver android in snowfall", str(output_path))

    assert generated_path == str(output_path)
    assert duration >= 0
    assert output_path.exists()
    assert captured["url"] == "https://image.example.com/v1/images/generations"
    assert captured["json"]["model"] == "gpt-image2"


@pytest.mark.legacy_compat
def test_openai_t2i_alias_legacy_gpt_image_2_uses_edit_key_as_backup(monkeypatch, tmp_path):
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        captured["timeout"] = timeout
        return FakeResponse({"data": [{"b64_json": PNG_1X1_BASE64}]})

    monkeypatch.setenv("IMAGE_PROVIDER", "openai")
    monkeypatch.delenv("OPENAI_IMAGE_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_IMAGE_EDIT_API_KEY", "edit-backup-key")
    monkeypatch.setenv("OPENAI_API_KEY", "general-openai-key")
    monkeypatch.setenv("OPENAI_IMAGE_BASE_URL", "https://image.example.com/v1")
    monkeypatch.setenv("OPENAI_IMAGE_MODEL", LEGACY_OPENAI_IMAGE_MODEL_ALIAS)
    monkeypatch.setattr("src.models.image.requests.post", fake_post)

    output_path = tmp_path / "generated-with-backup-key.png"
    model = WanxImageModel({"params": {"model_name": OPENAI_T2I_MODEL_ALIAS}})

    generated_path, duration = model.generate("a quiet classroom after rain", str(output_path))

    assert generated_path == str(output_path)
    assert duration >= 0
    assert output_path.exists()
    assert captured["url"] == "https://image.example.com/v1/images/generations"
    assert captured["headers"]["Authorization"] == "Bearer edit-backup-key"
    assert captured["json"]["model"] == LEGACY_OPENAI_IMAGE_MODEL_ALIAS


def test_openai_i2i_alias_uses_edit_endpoint_and_first_reference_only(monkeypatch, tmp_path):
    captured = {}
    ref1 = tmp_path / "reference-1.png"
    ref2 = tmp_path / "reference-2.png"
    ref1.write_bytes(base64.b64decode(PNG_1X1_BASE64))
    ref2.write_bytes(base64.b64decode(PNG_1X1_BASE64))

    def fake_post(url, headers=None, data=None, files=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["data"] = data
        captured["files"] = files
        captured["timeout"] = timeout
        return FakeResponse({"data": [{"b64_json": PNG_1X1_BASE64}]})

    monkeypatch.setenv("IMAGE_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_IMAGE_API_KEY", "img-key")
    monkeypatch.setenv("OPENAI_IMAGE_BASE_URL", "https://image.example.com/v1")
    monkeypatch.setenv("OPENAI_IMAGE_EDIT_BASE_URL", "https://image.example.com/v1")
    monkeypatch.setenv("OPENAI_IMAGE_MODEL", "qwen-image")
    monkeypatch.setenv("OPENAI_IMAGE_EDIT_MODEL", "qwen-image-edit")
    monkeypatch.setattr("src.models.image.requests.post", fake_post)

    output_path = tmp_path / "edited.png"
    model = WanxImageModel({"params": {"i2i_model_name": OPENAI_I2I_MODEL_ALIAS}})

    generated_path, duration = model.generate(
        "keep the same heroine outfit",
        str(output_path),
        ref_image_paths=[str(ref1), str(ref2)],
        model_name=OPENAI_I2I_MODEL_ALIAS,
    )

    assert generated_path == str(output_path)
    assert duration >= 0
    assert output_path.exists()
    assert captured["url"] == "https://image.example.com/v1/images/edits"
    assert captured["data"]["model"] == "qwen-image-edit"
    assert captured["files"]["image"][0] == os.path.basename(ref1)


def test_openai_i2i_alias_uses_gpt_image2_when_configured(monkeypatch, tmp_path):
    captured = {}
    ref = tmp_path / "reference.png"
    ref.write_bytes(base64.b64decode(PNG_1X1_BASE64))

    def fake_post(url, headers=None, data=None, files=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["data"] = data
        captured["files"] = files
        return FakeResponse({"data": [{"b64_json": PNG_1X1_BASE64}]})

    monkeypatch.setenv("IMAGE_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_IMAGE_API_KEY", "img-key")
    monkeypatch.setenv("OPENAI_IMAGE_BASE_URL", "https://image.example.com/v1")
    monkeypatch.setenv("OPENAI_IMAGE_EDIT_BASE_URL", "https://image.example.com/v1")
    monkeypatch.setenv("OPENAI_IMAGE_EDIT_MODEL", "gpt-image2")
    monkeypatch.setattr("src.models.image.requests.post", fake_post)

    output_path = tmp_path / "gpt-image2-edit.png"
    model = WanxImageModel({"params": {"i2i_model_name": OPENAI_I2I_MODEL_ALIAS}})

    generated_path, duration = model.generate(
        "keep the same heroine outfit",
        str(output_path),
        ref_image_path=str(ref),
        model_name=OPENAI_I2I_MODEL_ALIAS,
    )

    assert generated_path == str(output_path)
    assert duration >= 0
    assert output_path.exists()
    assert captured["url"] == "https://image.example.com/v1/images/edits"
    assert captured["data"]["model"] == "gpt-image2"


def test_openai_i2i_alias_sends_multiple_references_for_gpt_image_models(monkeypatch, tmp_path):
    captured = {}
    ref1 = tmp_path / "reference-1.png"
    ref2 = tmp_path / "reference-2.png"
    ref1.write_bytes(base64.b64decode(PNG_1X1_BASE64))
    ref2.write_bytes(base64.b64decode(PNG_1X1_BASE64))

    def fake_post(url, headers=None, data=None, files=None, timeout=None):
        captured["url"] = url
        captured["data"] = data
        captured["files"] = files
        return FakeResponse({"data": [{"b64_json": PNG_1X1_BASE64}]})

    monkeypatch.setenv("IMAGE_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_IMAGE_API_KEY", "img-key")
    monkeypatch.setenv("OPENAI_IMAGE_BASE_URL", "https://image.example.com/v1")
    monkeypatch.setenv("OPENAI_IMAGE_EDIT_BASE_URL", "https://image.example.com/v1")
    monkeypatch.setenv("OPENAI_IMAGE_EDIT_MODEL", "gpt-image2")
    monkeypatch.setattr("src.models.image.requests.post", fake_post)

    output_path = tmp_path / "gpt-image2-multi-edit.png"
    model = WanxImageModel({"params": {"i2i_model_name": OPENAI_I2I_MODEL_ALIAS}})

    generated_path, duration = model.generate(
        "use all references to compose one coherent frame",
        str(output_path),
        ref_image_paths=[str(ref1), str(ref2)],
        model_name=OPENAI_I2I_MODEL_ALIAS,
    )

    assert generated_path == str(output_path)
    assert duration >= 0
    assert output_path.exists()
    assert captured["url"] == "https://image.example.com/v1/images/edits"
    assert captured["data"]["model"] == "gpt-image2"
    assert isinstance(captured["files"], list)
    assert [field for field, _ in captured["files"]] == ["image[]", "image[]"]
    assert captured["files"][0][1][0] == os.path.basename(ref1)
    assert captured["files"][1][1][0] == os.path.basename(ref2)


def test_openai_i2i_alias_limits_gpt_image_references_to_16(monkeypatch, tmp_path):
    assert OPENAI_GPT_IMAGE_EDIT_REFERENCE_LIMIT == 16

    captured = {}
    refs = []
    for index in range(17):
        ref = tmp_path / f"reference-{index}.png"
        ref.write_bytes(base64.b64decode(PNG_1X1_BASE64))
        refs.append(ref)

    def fake_post(url, headers=None, data=None, files=None, timeout=None):
        captured["files"] = files
        return FakeResponse({"data": [{"b64_json": PNG_1X1_BASE64}]})

    monkeypatch.setenv("IMAGE_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_IMAGE_API_KEY", "img-key")
    monkeypatch.setenv("OPENAI_IMAGE_BASE_URL", "https://image.example.com/v1")
    monkeypatch.setenv("OPENAI_IMAGE_EDIT_BASE_URL", "https://image.example.com/v1")
    monkeypatch.setenv("OPENAI_IMAGE_EDIT_MODEL", "gpt-image2")
    monkeypatch.setattr("src.models.image.requests.post", fake_post)

    output_path = tmp_path / "gpt-image2-limited-edit.png"
    model = WanxImageModel({"params": {"i2i_model_name": OPENAI_I2I_MODEL_ALIAS}})

    generated_path, duration = model.generate(
        "use the allowed references",
        str(output_path),
        ref_image_paths=[str(ref) for ref in refs],
        model_name=OPENAI_I2I_MODEL_ALIAS,
    )

    assert generated_path == str(output_path)
    assert duration >= 0
    assert output_path.exists()
    assert len(captured["files"]) == OPENAI_GPT_IMAGE_EDIT_REFERENCE_LIMIT
    assert captured["files"][-1][1][0] == os.path.basename(refs[15])


def test_openai_i2i_alias_fits_multi_reference_request_to_aggregate_budget(
    monkeypatch,
    tmp_path,
):
    captured = {}
    refs = []
    for index in range(3):
        ref = tmp_path / f"large-reference-{index}.png"
        Image.effect_noise((1400, 1000), 96).convert("RGB").save(ref, format="PNG")
        refs.append(ref)

    assert sum(ref.stat().st_size for ref in refs) > 1_200_000

    def fake_post(url, headers=None, data=None, files=None, timeout=None):
        captured["files"] = files
        return FakeResponse({"data": [{"b64_json": PNG_1X1_BASE64}]})

    monkeypatch.setenv("IMAGE_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_IMAGE_API_KEY", "img-key")
    monkeypatch.setenv("OPENAI_IMAGE_BASE_URL", "https://image.example.com/v1")
    monkeypatch.setenv("OPENAI_IMAGE_EDIT_BASE_URL", "https://image.example.com/v1")
    monkeypatch.setenv("OPENAI_IMAGE_EDIT_MODEL", "gpt-image2")
    monkeypatch.setenv(OPENAI_EDIT_REQUEST_MAX_BYTES_ENV, "1200000")
    monkeypatch.setattr("src.models.image.requests.post", fake_post)

    output_path = tmp_path / "gpt-image2-budgeted-edit.png"
    model = WanxImageModel({"params": {"i2i_model_name": OPENAI_I2I_MODEL_ALIAS}})

    model.generate(
        "use the references without exceeding the gateway body limit",
        str(output_path),
        ref_image_paths=[str(ref) for ref in refs],
        model_name=OPENAI_I2I_MODEL_ALIAS,
    )

    total_uploaded_bytes = sum(len(file_info[1][1]) for file_info in captured["files"])
    assert total_uploaded_bytes <= 1_200_000
    assert all(file_info[1][0].endswith(".jpg") for file_info in captured["files"])
    assert all(file_info[1][2] == "image/jpeg" for file_info in captured["files"])


def test_openai_t2i_alias_uses_recommended_gpt_image2_defaults_when_not_overridden(
    monkeypatch, tmp_path
):
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        return FakeResponse({"data": [{"b64_json": PNG_1X1_BASE64}]})

    monkeypatch.setenv("IMAGE_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_IMAGE_API_KEY", "img-key")
    monkeypatch.delenv("OPENAI_IMAGE_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_IMAGE_MODEL", raising=False)
    monkeypatch.setattr("src.models.image.requests.post", fake_post)

    output_path = tmp_path / "gpt-image2-default.png"
    model = WanxImageModel({"params": {"model_name": OPENAI_T2I_MODEL_ALIAS}})

    model.generate("a neon train station in the rain", str(output_path))

    assert captured["url"] == f"{DEFAULT_OPENAI_IMAGE_BASE_URL}/images/generations"
    assert captured["json"]["model"] == DEFAULT_OPENAI_IMAGE_MODEL


def test_openai_i2i_alias_uses_dedicated_edit_defaults(monkeypatch, tmp_path):
    captured = {}
    ref = tmp_path / "reference.png"
    ref.write_bytes(base64.b64decode(PNG_1X1_BASE64))

    def fake_post(url, headers=None, data=None, files=None, timeout=None):
        captured["url"] = url
        captured["data"] = data
        return FakeResponse({"data": [{"b64_json": PNG_1X1_BASE64}]})

    monkeypatch.setenv("IMAGE_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_IMAGE_API_KEY", "img-key")
    monkeypatch.setenv("OPENAI_IMAGE_BASE_URL", "https://api.bltcy.ai/v1")
    monkeypatch.delenv("OPENAI_IMAGE_EDIT_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_IMAGE_EDIT_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.setattr("src.models.image.requests.post", fake_post)

    output_path = tmp_path / "edit-default.png"
    model = WanxImageModel({"params": {"i2i_model_name": OPENAI_I2I_MODEL_ALIAS}})

    model.generate(
        "keep the same subject but change the background",
        str(output_path),
        ref_image_path=str(ref),
        model_name=OPENAI_I2I_MODEL_ALIAS,
    )

    assert captured["url"] == f"{DEFAULT_OPENAI_IMAGE_EDIT_BASE_URL}/images/edits"
    assert captured["data"]["model"] == DEFAULT_OPENAI_IMAGE_EDIT_MODEL


def test_openai_i2i_alias_can_use_independent_edit_provider(monkeypatch, tmp_path):
    captured = {}
    ref = tmp_path / "reference.png"
    ref.write_bytes(base64.b64decode(PNG_1X1_BASE64))

    def fake_post(url, headers=None, data=None, files=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["data"] = data
        captured["files"] = files
        return FakeResponse({"data": [{"b64_json": PNG_1X1_BASE64}]})

    monkeypatch.setenv("IMAGE_PROVIDER", "dashscope")
    monkeypatch.setenv("IMAGE_EDIT_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "text-key")
    monkeypatch.setenv("OPENAI_IMAGE_EDIT_BASE_URL", "https://edit.example.com/v1")
    monkeypatch.setenv("OPENAI_IMAGE_EDIT_MODEL", "gpt-image-1")
    monkeypatch.setattr("src.models.image.requests.post", fake_post)

    output_path = tmp_path / "edit-provider-switch.png"
    model = WanxImageModel({"params": {"i2i_model_name": OPENAI_I2I_MODEL_ALIAS}})

    generated_path, duration = model.generate(
        "replace the costume but keep the same face",
        str(output_path),
        ref_image_path=str(ref),
        model_name=OPENAI_I2I_MODEL_ALIAS,
    )

    assert generated_path == str(output_path)
    assert duration >= 0
    assert output_path.exists()
    assert captured["url"] == "https://edit.example.com/v1/images/edits"
    assert captured["data"]["model"] == "gpt-image-1"


def test_openai_edit_reference_image_over_soft_max_side_is_resized_before_upload(tmp_path):
    ref = tmp_path / "reference-large.png"
    image = Image.new("RGB", (4200, 2500), (104, 92, 86))
    image.save(ref, format="PNG")
    original_size = ref.stat().st_size

    model = WanxImageModel({"params": {"i2i_model_name": OPENAI_I2I_MODEL_ALIAS}})

    filename, content, content_type = model._load_openai_reference_image(str(ref))

    prepared = Image.open(BytesIO(content))
    assert filename.endswith(".jpg")
    assert content_type == "image/jpeg"
    assert max(prepared.size) <= OPENAI_EDIT_REFERENCE_MAX_SIDE
    assert len(content) <= OPENAI_EDIT_REFERENCE_MAX_BYTES


def test_openai_edit_reference_image_under_50mb_keeps_original_upload(tmp_path):
    assert OPENAI_EDIT_REFERENCE_MAX_BYTES == 50 * 1024 * 1024
    assert OPENAI_EDIT_REFERENCE_MAX_SIDE == 3840

    ref = tmp_path / "reference-under-50mb.png"
    noisy = Image.effect_noise((2600, 1800), 96).convert("RGB")
    noisy.save(ref, format="PNG")
    original_size = ref.stat().st_size

    # Regression guard for the old 1.5MB / 1536px caps: this image should now upload unchanged.
    assert original_size > 1_500_000
    assert max(noisy.size) > 1536
    assert max(noisy.size) <= OPENAI_EDIT_REFERENCE_MAX_SIDE
    assert original_size < OPENAI_EDIT_REFERENCE_MAX_BYTES

    model = WanxImageModel({"params": {"i2i_model_name": OPENAI_I2I_MODEL_ALIAS}})

    filename, content, content_type = model._load_openai_reference_image(str(ref))

    assert filename.endswith(".png")
    assert content_type == "image/png"
    assert len(content) == original_size


def test_openai_edit_reference_image_keeps_shrinking_until_under_size_cap(monkeypatch, tmp_path):
    assert OPENAI_EDIT_REFERENCE_MIN_SIDE == 1024

    ref = tmp_path / "reference-large.png"
    noisy = Image.effect_noise((2800, 2200), 128).convert("RGB")
    noisy.save(ref, format="PNG")

    monkeypatch.setattr("src.models.image.OPENAI_EDIT_REFERENCE_MAX_BYTES", 320_000)

    model = WanxImageModel({"params": {"i2i_model_name": OPENAI_I2I_MODEL_ALIAS}})

    filename, content, content_type = model._load_openai_reference_image(str(ref))

    prepared = Image.open(BytesIO(content))
    assert filename.endswith(".jpg")
    assert content_type == "image/jpeg"
    assert len(content) <= 320_000
    assert max(prepared.size) >= OPENAI_EDIT_REFERENCE_MIN_SIDE
    assert max(prepared.size) <= OPENAI_EDIT_REFERENCE_MAX_SIDE


def test_openai_generation_retries_with_default_model_when_distributor_unavailable(
    monkeypatch, tmp_path
):
    calls = []
    unavailable_body = {
        "error": {
            "message": "当前分组 [default] 下对于模型 [gemini-3.1-flash-image-preview-2k] 无可用渠道，请联系管理员"
        }
    }

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append({"url": url, "json": json})
        if len(calls) == 1:
            return FakeResponse(unavailable_body, status_code=503)
        return FakeResponse({"data": [{"b64_json": PNG_1X1_BASE64}]})

    monkeypatch.setenv("IMAGE_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_IMAGE_API_KEY", "img-key")
    monkeypatch.setenv("OPENAI_IMAGE_BASE_URL", DEFAULT_OPENAI_IMAGE_BASE_URL)
    monkeypatch.setenv("OPENAI_IMAGE_MODEL", "gemini-3.1-flash-image-preview-2k")
    monkeypatch.setattr("src.models.image.requests.post", fake_post)

    output_path = tmp_path / "image-fallback.png"
    model = WanxImageModel({"params": {"model_name": OPENAI_T2I_MODEL_ALIAS}})

    generated_path, duration = model.generate(
        "a studio portrait of a ceramic mug", str(output_path)
    )

    assert generated_path == str(output_path)
    assert duration >= 0
    assert output_path.exists()
    assert len(calls) == 2
    assert calls[0]["json"]["model"] == "gemini-3.1-flash-image-preview-2k"
    assert calls[1]["json"]["model"] == DEFAULT_OPENAI_IMAGE_MODEL


def test_openai_generation_reports_missing_dedicated_image_key_clearly(monkeypatch, tmp_path):
    def fake_post(url, headers=None, json=None, timeout=None):
        return FakeResponse(
            {
                "error": {
                    "code": "invalid_request",
                    "message": "无效的令牌 [sk-test]",
                    "type": "new_api_error",
                }
            },
            status_code=401,
        )

    monkeypatch.setenv("IMAGE_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "generic-text-key")
    monkeypatch.delenv("OPENAI_IMAGE_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_IMAGE_BASE_URL", DEFAULT_OPENAI_IMAGE_BASE_URL)
    monkeypatch.setenv("OPENAI_IMAGE_MODEL", DEFAULT_OPENAI_IMAGE_MODEL)
    monkeypatch.setattr("src.models.image.requests.post", fake_post)

    output_path = tmp_path / "image-invalid-token.png"
    model = WanxImageModel({"params": {"model_name": OPENAI_T2I_MODEL_ALIAS}})

    try:
        model.generate("a ceramic mug on white background", str(output_path))
    except RuntimeError as exc:
        assert "主用图像 API Key" in str(exc)
        assert "备用图编 Key" in str(exc)
    else:
        raise AssertionError("Expected RuntimeError for missing dedicated image key")


def test_openai_edit_reports_missing_dedicated_edit_key_clearly(monkeypatch, tmp_path):
    ref = tmp_path / "reference.png"
    ref.write_bytes(base64.b64decode(PNG_1X1_BASE64))

    def fake_post(url, headers=None, data=None, files=None, timeout=None):
        return FakeResponse(
            {
                "error": {
                    "code": "invalid_request",
                    "message": "无效的令牌 [sk-test]",
                    "type": "new_api_error",
                }
            },
            status_code=401,
        )

    monkeypatch.setenv("IMAGE_PROVIDER", "openai")
    monkeypatch.delenv("OPENAI_IMAGE_EDIT_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_IMAGE_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "generic-text-key")
    monkeypatch.setenv("OPENAI_IMAGE_EDIT_BASE_URL", DEFAULT_OPENAI_IMAGE_EDIT_BASE_URL)
    monkeypatch.setenv("OPENAI_IMAGE_EDIT_MODEL", DEFAULT_OPENAI_IMAGE_EDIT_MODEL)
    monkeypatch.setattr("src.models.image.requests.post", fake_post)

    output_path = tmp_path / "image-invalid-edit-token.png"
    model = WanxImageModel({"params": {"i2i_model_name": OPENAI_I2I_MODEL_ALIAS}})

    try:
        model.generate(
            "keep the same subject and adjust the framing",
            str(output_path),
            ref_image_path=str(ref),
            model_name=OPENAI_I2I_MODEL_ALIAS,
        )
    except RuntimeError as exc:
        assert "图像编辑 API Key" in str(exc)
    else:
        raise AssertionError("Expected RuntimeError for missing dedicated edit key")


def test_openai_edit_uses_dedicated_image_key_as_backup_before_general_key(monkeypatch, tmp_path):
    captured = {}
    ref = tmp_path / "reference.png"
    ref.write_bytes(base64.b64decode(PNG_1X1_BASE64))

    def fake_post(url, headers=None, data=None, files=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["data"] = data
        return FakeResponse({"data": [{"b64_json": PNG_1X1_BASE64}]})

    monkeypatch.setenv("IMAGE_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_IMAGE_API_KEY", "dedicated-image-key")
    monkeypatch.delenv("OPENAI_IMAGE_EDIT_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "general-openai-key")
    monkeypatch.setenv("OPENAI_IMAGE_BASE_URL", "https://api.bltcy.ai/v1")
    monkeypatch.setenv("OPENAI_IMAGE_EDIT_BASE_URL", "https://yunwu.ai/v1")
    monkeypatch.setenv("OPENAI_IMAGE_EDIT_MODEL", DEFAULT_OPENAI_IMAGE_EDIT_MODEL)
    monkeypatch.setattr("src.models.image.requests.post", fake_post)

    output_path = tmp_path / "edit-prefers-general-key.png"
    model = WanxImageModel({"params": {"i2i_model_name": OPENAI_I2I_MODEL_ALIAS}})

    generated_path, duration = model.generate(
        "keep the same subject and output three views",
        str(output_path),
        ref_image_path=str(ref),
        model_name=OPENAI_I2I_MODEL_ALIAS,
    )

    assert generated_path == str(output_path)
    assert duration >= 0
    assert output_path.exists()
    assert captured["url"] == "https://yunwu.ai/v1/images/edits"
    assert captured["headers"]["Authorization"] == "Bearer dedicated-image-key"


def test_openai_edit_retries_on_transient_chunked_response(monkeypatch, tmp_path):
    calls = {"count": 0}
    ref = tmp_path / "reference.png"
    ref.write_bytes(base64.b64decode(PNG_1X1_BASE64))

    def fake_post(url, headers=None, data=None, files=None, timeout=None):
        calls["count"] += 1
        if calls["count"] < 3:
            raise requests.exceptions.ChunkedEncodingError("Response ended prematurely")
        return FakeResponse({"data": [{"b64_json": PNG_1X1_BASE64}]})

    monkeypatch.setenv("IMAGE_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_IMAGE_API_KEY", "dedicated-image-key")
    monkeypatch.setenv("OPENAI_API_KEY", "general-openai-key")
    monkeypatch.setenv("OPENAI_IMAGE_BASE_URL", "https://api.bltcy.ai/v1")
    monkeypatch.setenv("OPENAI_IMAGE_EDIT_BASE_URL", "https://yunwu.ai/v1")
    monkeypatch.setenv("OPENAI_IMAGE_EDIT_MODEL", DEFAULT_OPENAI_IMAGE_EDIT_MODEL)
    monkeypatch.setattr("src.models.image.requests.post", fake_post)
    monkeypatch.setattr("src.models.image.time.sleep", lambda _: None)

    output_path = tmp_path / "edit-retry.png"
    model = WanxImageModel({"params": {"i2i_model_name": OPENAI_I2I_MODEL_ALIAS}})

    generated_path, duration = model.generate(
        "keep the same subject and change the background",
        str(output_path),
        ref_image_path=str(ref),
        model_name=OPENAI_I2I_MODEL_ALIAS,
    )

    assert generated_path == str(output_path)
    assert duration >= 0
    assert output_path.exists()
    assert calls["count"] == 3


def test_openai_edit_retries_on_transient_ssl_eof(monkeypatch, tmp_path):
    calls = {"count": 0}
    ref = tmp_path / "reference.png"
    ref.write_bytes(base64.b64decode(PNG_1X1_BASE64))

    def fake_post(url, headers=None, data=None, files=None, timeout=None):
        calls["count"] += 1
        if calls["count"] < 3:
            raise requests.exceptions.SSLError("EOF occurred in violation of protocol")
        return FakeResponse({"data": [{"b64_json": PNG_1X1_BASE64}]})

    monkeypatch.setenv("IMAGE_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_IMAGE_API_KEY", "dedicated-image-key")
    monkeypatch.setenv("OPENAI_API_KEY", "general-openai-key")
    monkeypatch.setenv("OPENAI_IMAGE_BASE_URL", "https://api.bltcy.ai/v1")
    monkeypatch.setenv("OPENAI_IMAGE_EDIT_BASE_URL", "https://api.bltcy.ai/v1")
    monkeypatch.setenv("OPENAI_IMAGE_EDIT_MODEL", DEFAULT_OPENAI_IMAGE_EDIT_MODEL)
    monkeypatch.setattr("src.models.image.requests.post", fake_post)
    monkeypatch.setattr("src.models.image.time.sleep", lambda _: None)

    output_path = tmp_path / "edit-retry-ssl.png"
    model = WanxImageModel({"params": {"i2i_model_name": OPENAI_I2I_MODEL_ALIAS}})

    generated_path, duration = model.generate(
        "keep the same subject and refine the first frame",
        str(output_path),
        ref_image_path=str(ref),
        model_name=OPENAI_I2I_MODEL_ALIAS,
    )

    assert generated_path == str(output_path)
    assert duration >= 0
    assert output_path.exists()
    assert calls["count"] == 3


def test_openai_edit_retries_on_transient_429_response(monkeypatch, tmp_path):
    calls = {"count": 0}
    ref = tmp_path / "reference.png"
    ref.write_bytes(base64.b64decode(PNG_1X1_BASE64))
    sleeps = []

    def fake_post(url, headers=None, data=None, files=None, timeout=None):
        calls["count"] += 1
        if calls["count"] < 3:
            return FakeResponse(
                {
                    "error": {
                        "message": "当前分组上游负载已饱和，请稍后再试",
                        "code": "429",
                    }
                },
                status_code=429,
            )
        return FakeResponse({"data": [{"b64_json": PNG_1X1_BASE64}]})

    monkeypatch.setenv("IMAGE_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_IMAGE_API_KEY", "dedicated-image-key")
    monkeypatch.setenv("OPENAI_IMAGE_EDIT_BASE_URL", "https://api.bltcy.ai/v1")
    monkeypatch.setenv("OPENAI_IMAGE_EDIT_MODEL", DEFAULT_OPENAI_IMAGE_EDIT_MODEL)
    monkeypatch.setattr("src.models.image.requests.post", fake_post)
    monkeypatch.setattr("src.models.image.time.sleep", lambda seconds: sleeps.append(seconds))

    output_path = tmp_path / "edit-retry-429.png"
    model = WanxImageModel({"params": {"i2i_model_name": OPENAI_I2I_MODEL_ALIAS}})

    generated_path, duration = model.generate(
        "keep the same composition but improve the lighting",
        str(output_path),
        ref_image_path=str(ref),
        model_name=OPENAI_I2I_MODEL_ALIAS,
    )

    assert generated_path == str(output_path)
    assert duration >= 0
    assert output_path.exists()
    assert calls["count"] == 3
    assert sleeps[0] >= 15


def test_openai_edit_respects_retry_after_header_for_429(monkeypatch, tmp_path):
    calls = {"count": 0}
    ref = tmp_path / "reference.png"
    ref.write_bytes(base64.b64decode(PNG_1X1_BASE64))
    sleeps = []

    def fake_post(url, headers=None, data=None, files=None, timeout=None):
        calls["count"] += 1
        if calls["count"] == 1:
            return FakeResponse(
                {
                    "error": {
                        "message": "请稍后再试",
                        "code": "429",
                    }
                },
                status_code=429,
                headers={"Retry-After": "9"},
            )
        return FakeResponse({"data": [{"b64_json": PNG_1X1_BASE64}]})

    monkeypatch.setenv("IMAGE_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_IMAGE_API_KEY", "dedicated-image-key")
    monkeypatch.setenv("OPENAI_IMAGE_EDIT_BASE_URL", "https://api.bltcy.ai/v1")
    monkeypatch.setenv("OPENAI_IMAGE_EDIT_MODEL", DEFAULT_OPENAI_IMAGE_EDIT_MODEL)
    monkeypatch.setattr("src.models.image.requests.post", fake_post)
    monkeypatch.setattr("src.models.image.time.sleep", lambda seconds: sleeps.append(seconds))

    output_path = tmp_path / "edit-retry-after.png"
    model = WanxImageModel({"params": {"i2i_model_name": OPENAI_I2I_MODEL_ALIAS}})

    generated_path, duration = model.generate(
        "keep the same composition but improve the lighting",
        str(output_path),
        ref_image_path=str(ref),
        model_name=OPENAI_I2I_MODEL_ALIAS,
    )

    assert generated_path == str(output_path)
    assert duration >= 0
    assert output_path.exists()
    assert calls["count"] == 2
    assert sleeps[0] == 9


@pytest.mark.legacy_compat
def test_openai_generation_legacy_gpt_image_2_does_not_retry_safety_moderation_blocks(
    monkeypatch, tmp_path
):
    calls = {"count": 0}

    def fake_post(url, headers=None, json=None, timeout=None):
        calls["count"] += 1
        return FakeResponse(
            {
                "error": {
                    "message": "Your request was rejected by the safety system.",
                    "type": "image_generation_user_error",
                    "code": "moderation_blocked",
                }
            },
            status_code=429,
        )

    monkeypatch.setenv("IMAGE_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_IMAGE_API_KEY", "img-key")
    monkeypatch.setenv("OPENAI_IMAGE_BASE_URL", "https://image.example.com/v1")
    monkeypatch.setenv("OPENAI_IMAGE_MODEL", LEGACY_OPENAI_IMAGE_MODEL_ALIAS)
    monkeypatch.setattr("src.models.image.requests.post", fake_post)
    monkeypatch.setattr("src.models.image.time.sleep", lambda _: None)

    output_path = tmp_path / "blocked.png"
    model = WanxImageModel({"params": {"model_name": OPENAI_T2I_MODEL_ALIAS}})

    try:
        model.generate("a blocked image request", str(output_path))
    except RuntimeError as exc:
        assert "blocked by safety moderation" in str(exc)
    else:
        raise AssertionError("Expected RuntimeError for moderation block")

    assert calls["count"] == 1


def test_download_image_retries_on_transient_timeout(monkeypatch, tmp_path):
    calls = {"count": 0, "verify": [], "stream": []}

    class FakeSession:
        def mount(self, *_args, **_kwargs):
            return None

        def get(self, url, stream=None, timeout=None, verify=None):
            calls["count"] += 1
            calls["verify"].append(verify)
            calls["stream"].append(stream)
            if calls["count"] < 3:
                raise requests.exceptions.Timeout("download timed out")
            return FakeDownloadResponse(base64.b64decode(PNG_1X1_BASE64))

    monkeypatch.setattr("src.utils.http_downloads.requests.Session", lambda: FakeSession())
    monkeypatch.setattr("src.utils.http_downloads.time.sleep", lambda _: None)

    output_path = tmp_path / "download-retry.png"
    model = WanxImageModel({"params": {"model_name": OPENAI_T2I_MODEL_ALIAS}})

    model._download_image("https://cdn.example.com/generated.png", str(output_path))

    assert output_path.exists()
    assert output_path.read_bytes() == base64.b64decode(PNG_1X1_BASE64)
    assert calls["count"] == 3
    assert calls["verify"] == [True, True, True]
    assert calls["stream"] == [True, True, True]
