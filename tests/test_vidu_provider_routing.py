import base64
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.apps.comic_gen.models import VideoTask
from src.apps.comic_gen.pipeline import ComicGenPipeline
from src.models.vidu import ViduModel


PNG_1X1_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4//8/AwAI/AL+"
    "X2VINQAAAABJRU5ErkJggg=="
)


class _FakeResponse:
    def __init__(self, status_code, payload=None, content=b""):
        self.status_code = status_code
        self._payload = payload or {}
        self.content = content
        self.text = str(self._payload)

    def json(self):
        return self._payload


def _build_pipeline(task: VideoTask, wanx_model) -> ComicGenPipeline:
    pipeline = ComicGenPipeline.__new__(ComicGenPipeline)
    script = SimpleNamespace(
        id=task.project_id,
        video_tasks=[task],
        characters=[],
        scenes=[],
        props=[],
        updated_at=0,
    )
    pipeline.scripts = {task.project_id: script}
    pipeline._save_data = lambda: None
    pipeline._download_temp_image = lambda _: "/tmp/downloaded-vidu.png"
    pipeline._kling_model = None
    pipeline._vidu_model = None
    pipeline.video_generator = SimpleNamespace(model=wanx_model)
    pipeline.get_script = lambda script_id: pipeline.scripts.get(script_id)
    return pipeline


def _write_output_png(rel_path: str) -> str:
    file_path = Path("output") / rel_path
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_bytes(base64.b64decode(PNG_1X1_BASE64))
    return str(file_path.resolve())


def test_pipeline_routes_vidu_vendor_mode_to_vendor_adapter(monkeypatch):
    monkeypatch.setenv("VIDU_PROVIDER_MODE", "vendor")

    task = VideoTask(
        id="task-vidu-vendor",
        project_id="script-1",
        image_url="https://example.com/ref.png",
        prompt="demo",
        model="viduq3-pro",
    )

    calls = {}

    class FakeViduModel:
        def __init__(self, config):
            calls["init_config"] = config

        def generate(self, **kwargs):
            calls["vendor_kwargs"] = kwargs
            return kwargs["output_path"], 0.0

    class FakeWanxModel:
        def generate(self, **kwargs):
            calls["wanx_kwargs"] = kwargs
            raise AssertionError("DashScope path should not be used in vendor mode")

    monkeypatch.setattr("src.models.vidu.ViduModel", FakeViduModel)

    pipeline = _build_pipeline(task, FakeWanxModel())
    pipeline.process_video_task("script-1", "task-vidu-vendor")

    assert "vendor_kwargs" in calls
    assert "wanx_kwargs" not in calls
    assert calls["vendor_kwargs"]["model"] == "viduq3-pro"
    assert calls["vendor_kwargs"]["img_path"] == "/tmp/downloaded-vidu.png"
    assert task.status == "completed"


def test_pipeline_routes_vidu_dashscope_mode_to_wanx_without_vendor_credentials(monkeypatch):
    monkeypatch.setenv("VIDU_PROVIDER_MODE", "dashscope")
    monkeypatch.delenv("VIDU_API_KEY", raising=False)

    task = VideoTask(
        id="task-vidu-dashscope",
        project_id="script-1",
        image_url="https://example.com/ref.png",
        prompt="demo",
        model="viduq3-pro",
    )

    calls = {}

    class FakeViduModel:
        def __init__(self, config):
            calls["vendor_init"] = config

        def generate(self, **kwargs):
            calls["vendor_kwargs"] = kwargs
            raise AssertionError("Vendor adapter should not be used in dashscope mode")

    class FakeWanxModel:
        def generate(self, **kwargs):
            calls["wanx_kwargs"] = kwargs
            return kwargs["output_path"], 0.0

    monkeypatch.setattr("src.models.vidu.ViduModel", FakeViduModel)

    pipeline = _build_pipeline(task, FakeWanxModel())
    pipeline.process_video_task("script-1", "task-vidu-dashscope")

    assert "wanx_kwargs" in calls
    assert "vendor_kwargs" not in calls
    assert calls["wanx_kwargs"]["model"] == "viduq3-pro"
    assert calls["wanx_kwargs"]["img_path"] == "/tmp/downloaded-vidu.png"
    assert task.status == "completed"


def test_vendor_vidu_local_image_without_oss_fails_clearly(monkeypatch, tmp_path):
    local_path = _write_output_png("uploads/test_vidu_vendor_ref_no_oss.png")

    class FakeUploader:
        def __init__(self):
            self.is_configured = False

    monkeypatch.setattr("src.models.vidu.OSSImageUploader", FakeUploader)

    model = ViduModel({"api_key": "test-key"})

    with pytest.raises(ValueError, match="requires a URL-compatible media source"):
        model.generate(
            prompt="demo",
            output_path=str(tmp_path / "out.mp4"),
            img_path=local_path,
            model="viduq3-pro",
        )


def test_vendor_vidu_local_image_with_oss_uses_signed_url(monkeypatch, tmp_path):
    captured = {}
    local_path = _write_output_png("uploads/test_vidu_vendor_ref_with_oss.png")

    class FakeUploader:
        def __init__(self):
            self.is_configured = True

        def upload_file(self, local_path, sub_path="", custom_filename=None):
            filename = custom_filename or Path(local_path).name
            return f"lumenx/{sub_path.strip('/')}/{filename}".replace("//", "/")

        def sign_url_for_api(self, object_key):
            return f"https://oss.example/{object_key}"

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["submit_url"] = url
        captured["headers"] = dict(headers or {})
        captured["body"] = json or {}
        return _FakeResponse(200, {"task_id": "vidu-task-1"})

    def fake_get(url, headers=None, timeout=None):
        if "tasks" in url:
            return _FakeResponse(
                200,
                {"state": "success", "creations": [{"url": "https://example.com/out.mp4"}]},
            )
        return _FakeResponse(200, content=b"video")

    def fake_download(url, output_path, timeout=None, **kwargs):
        captured["download_url"] = url
        Path(output_path).write_bytes(b"video")

    monkeypatch.setattr("src.models.vidu.OSSImageUploader", FakeUploader)
    monkeypatch.setattr("src.models.vidu.requests.post", fake_post)
    monkeypatch.setattr("src.models.vidu.requests.get", fake_get)
    monkeypatch.setattr("src.models.vidu.download_url_to_file", fake_download)
    monkeypatch.setattr("src.models.vidu.time.sleep", lambda _: None)

    model = ViduModel({"api_key": "test-key"})
    model.generate(
        prompt="demo",
        output_path=str(tmp_path / "out.mp4"),
        img_path=local_path,
        model="viduq3-pro",
    )

    assert captured["submit_url"].endswith("/img2video")
    assert captured["headers"]["Authorization"] == "Token test-key"
    assert captured["body"]["images"][0].startswith("https://oss.example/lumenx/")
    assert captured["download_url"] == "https://example.com/out.mp4"
