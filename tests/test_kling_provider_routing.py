import base64
from pathlib import Path
from types import SimpleNamespace

from src.apps.comic_gen.models import VideoTask
from src.apps.comic_gen.pipeline import ComicGenPipeline
from src.models.kling import KlingModel


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

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}: {self.text}")

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
    pipeline._download_temp_image = lambda _: "/tmp/downloaded-kling.png"
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


def test_pipeline_routes_kling_vendor_mode_to_vendor_adapter(monkeypatch):
    monkeypatch.setenv("KLING_PROVIDER_MODE", "vendor")

    task = VideoTask(
        id="task-kling-vendor",
        project_id="script-1",
        image_url="https://example.com/ref.png",
        prompt="demo",
        model="kling-v1",
    )

    calls = {}

    class FakeKlingModel:
        def __init__(self, config):
            calls["init_config"] = config

        def generate(self, **kwargs):
            calls["vendor_kwargs"] = kwargs
            return kwargs["output_path"], 0.0

    class FakeWanxModel:
        def generate(self, **kwargs):
            calls["wanx_kwargs"] = kwargs
            raise AssertionError("DashScope path should not be used in vendor mode")

    monkeypatch.setattr("src.models.kling.KlingModel", FakeKlingModel)

    pipeline = _build_pipeline(task, FakeWanxModel())
    pipeline.process_video_task("script-1", "task-kling-vendor")

    assert "vendor_kwargs" in calls
    assert "wanx_kwargs" not in calls
    assert calls["vendor_kwargs"]["model"] == "kling-v1"
    assert calls["vendor_kwargs"]["img_path"] == "/tmp/downloaded-kling.png"
    assert task.status == "completed"


def test_pipeline_routes_kling_dashscope_mode_to_wanx_without_vendor_credentials(monkeypatch):
    monkeypatch.setenv("KLING_PROVIDER_MODE", "dashscope")
    monkeypatch.delenv("KLING_ACCESS_KEY", raising=False)
    monkeypatch.delenv("KLING_SECRET_KEY", raising=False)

    task = VideoTask(
        id="task-kling-dashscope",
        project_id="script-1",
        image_url="https://example.com/ref.png",
        prompt="demo",
        model="kling-v1",
    )

    calls = {}

    class FakeKlingModel:
        def __init__(self, config):
            calls["vendor_init"] = config

        def generate(self, **kwargs):
            calls["vendor_kwargs"] = kwargs
            raise AssertionError("Vendor adapter should not be used in dashscope mode")

    class FakeWanxModel:
        def generate(self, **kwargs):
            calls["wanx_kwargs"] = kwargs
            return kwargs["output_path"], 0.0

    monkeypatch.setattr("src.models.kling.KlingModel", FakeKlingModel)

    pipeline = _build_pipeline(task, FakeWanxModel())
    pipeline.process_video_task("script-1", "task-kling-dashscope")

    assert "wanx_kwargs" in calls
    assert "vendor_kwargs" not in calls
    assert calls["wanx_kwargs"]["model"] == "kling-v1"
    assert calls["wanx_kwargs"]["img_path"] == "/tmp/downloaded-kling.png"
    assert task.status == "completed"


def test_vendor_kling_local_image_uses_base64_payload(monkeypatch, tmp_path):
    captured = {}
    local_path = _write_output_png("uploads/test_kling_vendor_ref.png")

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["submit_url"] = url
        captured["headers"] = dict(headers or {})
        captured["body"] = json or {}
        return _FakeResponse(200, {"code": 0, "data": {"task_id": "kling-task-1"}})

    def fake_get(url, headers=None, timeout=None):
        if "image2video" in url or "text2video" in url:
            return _FakeResponse(
                200,
                {
                    "code": 0,
                    "data": {
                        "task_status": "succeed",
                        "task_result": {"videos": [{"url": "https://example.com/out.mp4"}]},
                    },
                },
            )
        return _FakeResponse(200, content=b"video")

    def fake_download(url, output_path, timeout=None):
        captured["download_url"] = url
        Path(output_path).write_bytes(b"video")

    monkeypatch.setattr("src.models.kling.requests.post", fake_post)
    monkeypatch.setattr("src.models.kling.requests.get", fake_get)
    monkeypatch.setattr("src.models.kling.download_url_to_file", fake_download)
    monkeypatch.setattr("src.models.kling.time.sleep", lambda _: None)

    model = KlingModel({"access_key": "test-ak", "secret_key": "test-sk"})
    out_path = str(tmp_path / "out.mp4")
    model.generate(
        prompt="demo",
        output_path=out_path,
        img_path=local_path,
        model="kling-v1",
    )

    assert captured["submit_url"].endswith("/videos/image2video")
    assert captured["headers"]["Authorization"].startswith("Bearer ")
    assert captured["body"]["image"] == PNG_1X1_BASE64
    assert captured["download_url"] == "https://example.com/out.mp4"
