from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.models import wanx


def test_wanx_keeps_immutable_separate_configurations(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "ambient-example")
    settings = {"DASHSCOPE_API_KEY": "first-example", "KLING_PROVIDER_MODE": "vendor"}
    first = wanx.WanxModel({}, env=settings)
    second = wanx.WanxModel({}, env={"DASHSCOPE_API_KEY": "second-example"})
    settings["DASHSCOPE_API_KEY"] = "changed-example"
    assert first.api_key == "first-example"
    assert second.api_key == "second-example"
    assert first._resolve_provider_backend_for_model("kling-v3") == "vendor"
    assert second._resolve_provider_backend_for_model("kling-v3") == "dashscope"


@pytest.mark.parametrize("settings", [{}, {"DASHSCOPE_API_KEY": ""}])
def test_empty_explicit_key_never_reaches_sdk_or_http(monkeypatch, settings):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "ambient-example")
    sdk = Mock()
    http = Mock()
    monkeypatch.setattr(wanx.VideoSynthesis, "async_call", sdk)
    monkeypatch.setattr(wanx.requests, "post", http)
    model = wanx.WanxModel({}, env=settings)
    with pytest.raises(RuntimeError, match="configured"):
        model._generate_sdk("test", "wanx2.1-t2v-turbo")
    with pytest.raises(RuntimeError, match="configured"):
        model._generate_wan_i2v_http("test", "https://media.invalid/image.png")
    sdk.assert_not_called()
    http.assert_not_called()


def test_legacy_http_submit_and_poll_share_captured_key_and_endpoint(monkeypatch):
    settings = {"DASHSCOPE_API_KEY": "first-example", "DASHSCOPE_BASE_URL": "https://first.invalid"}
    model = wanx.WanxModel({}, env=settings)
    calls = []

    def post(url, **kwargs):
        calls.append((url, kwargs["headers"]["Authorization"]))
        settings.update(DASHSCOPE_API_KEY="second-example", DASHSCOPE_BASE_URL="https://second.invalid")
        return SimpleNamespace(status_code=200, text="ok", json=lambda: {"output": {"task_id": "task-1"}})

    def get(url, **kwargs):
        calls.append((url, kwargs["headers"]["Authorization"]))
        return SimpleNamespace(status_code=200, json=lambda: {"output": {"task_status": "SUCCEEDED", "video_url": "https://media.invalid/video.mp4"}})

    monkeypatch.setattr(wanx.requests, "post", post)
    monkeypatch.setattr(wanx.requests, "get", get)
    monkeypatch.setattr(wanx.time, "sleep", lambda _: None)
    assert model._generate_wan_i2v_http("test", "https://media.invalid/image.png") == "https://media.invalid/video.mp4"
    assert calls == [("https://first.invalid/api/v1/services/aigc/video-generation/video-synthesis", "Bearer first-example"), ("https://first.invalid/api/v1/tasks/task-1", "Bearer first-example")]


def test_sdk_submit_and_wait_receive_same_explicit_scope(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_BASE_URL", "https://ambient.invalid")
    response = SimpleNamespace(status_code=200, output=SimpleNamespace(task_id="t", task_status="SUCCEEDED", video_url="https://media.invalid/video.mp4"))
    submit, wait = Mock(return_value=response), Mock(return_value=response)
    monkeypatch.setattr(wanx.VideoSynthesis, "async_call", submit)
    monkeypatch.setattr(wanx.VideoSynthesis, "wait", wait)
    model = wanx.WanxModel({}, env={"DASHSCOPE_API_KEY": "first-example", "DASHSCOPE_BASE_URL": "https://first.invalid/"})
    assert model._generate_sdk("test", "wanx2.1-t2v-turbo") == "https://media.invalid/video.mp4"
    for call in (submit.call_args, wait.call_args):
        assert call.kwargs["api_key"] == "first-example"
        assert call.kwargs["base_address"] == "https://first.invalid/api/v1"


def test_managed_generation_constructs_its_own_uploader(monkeypatch, tmp_path):
    captured = []
    monkeypatch.setattr(wanx, "OSSImageUploader", lambda **kwargs: captured.append(kwargs))
    monkeypatch.setattr(wanx.WanxModel, "_generate_sdk", lambda *args, **kwargs: "https://media.invalid/video.mp4")
    monkeypatch.setattr(wanx.WanxModel, "_download_video", lambda *args: None)
    settings = {"DASHSCOPE_API_KEY": "first-example", "OSS_BASE_PATH": "first"}
    model = wanx.WanxModel({"output_root": str(tmp_path)}, env=settings)
    model.generate("test", str(tmp_path / "video.mp4"), model_name="wanx2.1-t2v-turbo")
    assert dict(captured[0]["env"]) == settings
    assert captured[0]["output_root"] == str(tmp_path)


def test_standalone_wanx_key_compatibility(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "standalone-example")
    assert wanx.WanxModel({}).api_key == "standalone-example"
