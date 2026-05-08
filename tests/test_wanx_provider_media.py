import base64
from pathlib import Path

from src.models.wanx import WanxModel


PNG_1X1_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4//8/AwAI/AL+"
    "X2VINQAAAABJRU5ErkJggg=="
)


class _FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self):
        return self._payload


def _write_output_file(rel_path: str, raw_bytes: bytes) -> str:
    file_path = Path("output") / rel_path
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_bytes(raw_bytes)
    return str(file_path)


def _install_fake_requests(monkeypatch, captured):
    def fake_post(url, headers=None, json=None, timeout=None, data=None, files=None):
        if "video-synthesis" in url:
            captured["create_headers"] = dict(headers or {})
            captured["create_payload"] = json
            return _FakeResponse(
                200,
                {"output": {"task_id": "task-1", "task_status": "PENDING"}},
            )
        # Keep compatibility with potential temp-upload POSTs if any test forgets to patch helper.
        if data is not None and files is not None:
            return _FakeResponse(204, {})
        return _FakeResponse(404, {"message": "unexpected URL"})

    def fake_get(url, headers=None, timeout=None, params=None):
        if "/api/v1/tasks/" in url:
            return _FakeResponse(
                200,
                {"output": {"task_status": "SUCCEEDED", "video_url": "https://example.com/out.mp4"}},
            )
        if "/api/v1/uploads" in url:
            return _FakeResponse(
                200,
                {
                    "output": {
                        "upload_host": "https://upload.example",
                        "upload_dir": "dashscope-temp/session",
                        "policy": "policy",
                        "signature": "sig",
                        "oss_access_key_id": "ak",
                    }
                },
            )
        return _FakeResponse(404, {"message": "unexpected URL"})

    monkeypatch.setattr("src.models.wanx.requests.post", fake_post)
    monkeypatch.setattr("src.models.wanx.requests.get", fake_get)
    monkeypatch.setattr("src.models.wanx.time.sleep", lambda _: None)
    monkeypatch.setattr("src.models.wanx.WanxModel._download_video", lambda self, *_: None)


def _install_fake_uploader(monkeypatch, configured: bool):
    class _FakeUploader:
        def __init__(self):
            self.is_configured = configured

        def upload_file(self, local_path, sub_path="", custom_filename=None):
            if not self.is_configured:
                return None
            filename = custom_filename or Path(local_path).name
            return f"lumenx/{sub_path.strip('/')}/{filename}".replace("//", "/")

        def sign_url_for_api(self, object_key):
            return f"https://oss.example/{object_key}"

    monkeypatch.setattr("src.models.wanx.OSSImageUploader", _FakeUploader)


class TestWanxProviderMediaIntegration:
    def test_i2v_local_image_without_oss_uses_temp_url_and_header(self, monkeypatch):
        monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
        _install_fake_uploader(monkeypatch, configured=False)

        captured = {}
        _install_fake_requests(monkeypatch, captured)
        monkeypatch.setattr(
            "src.models.wanx.WanxModel._create_dashscope_temp_url",
            lambda self, local_path, model_name: "oss://dashscope-temp/image-001",
        )

        img_path = _write_output_file("uploads/wanx_i2v_local.png", base64.b64decode(PNG_1X1_BASE64))

        model = WanxModel({"params": {}})
        model.generate(
            prompt="demo",
            output_path="output/video/wanx_i2v_local.mp4",
            img_path=img_path,
            model_name="wan2.6-i2v",
        )

        assert captured["create_payload"]["input"]["img_url"] == "oss://dashscope-temp/image-001"
        assert captured["create_headers"]["X-DashScope-OssResourceResolve"] == "enable"

    def test_i2v_local_audio_without_oss_uses_temp_url_and_header(self, monkeypatch):
        monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
        _install_fake_uploader(monkeypatch, configured=False)

        captured = {}
        _install_fake_requests(monkeypatch, captured)
        monkeypatch.setattr(
            "src.models.wanx.WanxModel._create_dashscope_temp_url",
            lambda self, local_path, model_name: "oss://dashscope-temp/audio-001",
        )

        img_path = _write_output_file("uploads/wanx_i2v_audio_img.png", base64.b64decode(PNG_1X1_BASE64))
        audio_path = _write_output_file("audio/wanx_i2v_audio.wav", b"fake-audio")

        model = WanxModel({"params": {}})
        model.generate(
            prompt="demo",
            output_path="output/video/wanx_i2v_audio.mp4",
            img_path=img_path,
            model_name="wan2.6-i2v",
            audio_url=audio_path,
        )

        assert captured["create_payload"]["input"]["audio_url"] == "oss://dashscope-temp/audio-001"
        assert captured["create_headers"]["X-DashScope-OssResourceResolve"] == "enable"

    def test_r2v_local_reference_video_without_oss_uses_temp_url_and_header(self, monkeypatch):
        monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
        _install_fake_uploader(monkeypatch, configured=False)

        captured = {}
        _install_fake_requests(monkeypatch, captured)
        monkeypatch.setattr(
            "src.models.wanx.WanxModel._create_dashscope_temp_url",
            lambda self, local_path, model_name: "oss://dashscope-temp/ref-video-001",
        )

        ref_video_path = _write_output_file("uploads/wanx_r2v_ref_local.bin", b"fake-video")

        model = WanxModel({"params": {}})
        model.generate(
            prompt="demo",
            output_path="output/video/wanx_r2v.mp4",
            model_name="wan2.6-r2v",
            ref_video_urls=[ref_video_path],
        )

        assert captured["create_payload"]["input"]["reference_video_urls"] == [
            "oss://dashscope-temp/ref-video-001"
        ]
        assert captured["create_headers"]["X-DashScope-OssResourceResolve"] == "enable"

    def test_i2v_object_key_with_oss_configured_uses_signed_url(self, monkeypatch):
        monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
        _install_fake_uploader(monkeypatch, configured=True)

        captured = {}
        _install_fake_requests(monkeypatch, captured)

        model = WanxModel({"params": {}})
        model.generate(
            prompt="demo",
            output_path="output/video/wanx_i2v_object_key.mp4",
            img_path="lumenx/temp/i2v_input/ref.png",
            model_name="wan2.6-i2v",
        )

        assert (
            captured["create_payload"]["input"]["img_url"]
            == "https://oss.example/lumenx/temp/i2v_input/ref.png"
        )

    def test_create_dashscope_temp_url_calls_policy_and_multipart_upload(self, monkeypatch):
        monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
        local_path = _write_output_file("uploads/wanx_temp_upload_source.png", b"img-bytes")

        captured = {}

        def fake_get(url, params=None, headers=None, timeout=None):
            captured["policy_url"] = url
            captured["policy_params"] = dict(params or {})
            captured["policy_headers"] = dict(headers or {})
            return _FakeResponse(
                200,
                {
                    "output": {
                        "upload_host": "https://upload.example",
                        "upload_dir": "dashscope-temp/dir",
                        "policy": "policy-xyz",
                        "signature": "sig-xyz",
                        "oss_access_key_id": "ak-xyz",
                    }
                },
            )

        def fake_post(url, data=None, files=None, timeout=None, headers=None, json=None):
            captured["upload_url"] = url
            captured["upload_data"] = dict(data or {})
            captured["upload_file_name"] = files["file"][0] if files else None
            captured["upload_file_content"] = files["file"][1].read() if files else None
            return _FakeResponse(204, {})

        monkeypatch.setattr("src.models.wanx.requests.get", fake_get)
        monkeypatch.setattr("src.models.wanx.requests.post", fake_post)

        model = WanxModel({"params": {}})
        resolved = model._create_dashscope_temp_url(local_path, "wan2.6-i2v")

        assert resolved == "oss://dashscope-temp/dir/wanx_temp_upload_source.png"
        assert captured["policy_url"].endswith("/api/v1/uploads")
        assert captured["policy_params"] == {"action": "getPolicy", "model": "wan2.6-i2v"}
        assert captured["policy_headers"]["Authorization"] == "Bearer test-key"
        assert captured["upload_url"] == "https://upload.example"
        assert captured["upload_data"]["key"] == "dashscope-temp/dir/wanx_temp_upload_source.png"
        assert captured["upload_data"]["policy"] == "policy-xyz"
        assert captured["upload_data"]["signature"] == "sig-xyz"
        assert captured["upload_data"]["OSSAccessKeyId"] == "ak-xyz"
        assert captured["upload_file_name"] == "wanx_temp_upload_source.png"
        assert captured["upload_file_content"] == b"img-bytes"

    def test_sdk_dashscope_proxy_model_local_image_uses_resolved_image_value(self, monkeypatch):
        monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
        _install_fake_uploader(monkeypatch, configured=False)

        captured = {}

        def fake_generate_sdk(
            self,
            prompt,
            model_name,
            img_url=None,
            size="1280*720",
            duration=5,
            prompt_extend=True,
            negative_prompt=None,
            audio_url=None,
            watermark=False,
            seed=None,
            camera_motion=None,
            subject_motion=None,
        ):
            captured["model_name"] = model_name
            captured["img_url"] = img_url
            return "https://example.com/out.mp4"

        monkeypatch.setattr("src.models.wanx.WanxModel._generate_sdk", fake_generate_sdk)
        monkeypatch.setattr("src.models.wanx.WanxModel._download_video", lambda self, *_: None)

        img_path = _write_output_file("uploads/wanx_sdk_kling_local.png", base64.b64decode(PNG_1X1_BASE64))

        model = WanxModel({"params": {}})
        model.generate(
            prompt="demo",
            output_path="output/video/wanx_sdk_kling_local.mp4",
            img_path=img_path,
            model_name="kling-v1",
        )

        assert captured["model_name"] == "kling-v1"
        assert captured["img_url"].startswith("data:image/")
        assert ";base64," in captured["img_url"]
