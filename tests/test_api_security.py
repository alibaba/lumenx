import sys
import types

from fastapi.testclient import TestClient

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


def _make_client() -> TestClient:
    return TestClient(api.app)


class DummyUploader:
    is_configured = False
    provider = None

    def upload_image(self, *args, **kwargs):
        return None

    def upload_video(self, *args, **kwargs):
        return None

    def upload_file(self, *args, **kwargs):
        return None


def test_cors_allows_loopback_origin(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "output").mkdir()

    client = _make_client()
    response = client.get("/projects/", headers={"Origin": "http://localhost:3000"})

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"


def test_public_files_do_not_expose_json_artifacts(monkeypatch, tmp_path):
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    (output_dir / "projects.json").write_text("{}", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    client = _make_client()
    response = client.get("/files/projects.json")

    assert response.status_code == 404


def test_upload_rejects_unsupported_extension(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "output").mkdir()

    client = _make_client()
    response = client.post(
        "/upload",
        files={"file": ("notes.txt", b"hello", "text/plain")},
    )

    assert response.status_code == 415


def test_upload_accepts_image_files(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "output").mkdir()
    monkeypatch.setattr(api, "OSSImageUploader", DummyUploader)

    client = _make_client()
    response = client.post(
        "/upload",
        files={"file": ("cover.png", b"fake-png-data", "image/png")},
    )

    assert response.status_code == 200
    assert response.json()["url"].startswith("uploads/")
