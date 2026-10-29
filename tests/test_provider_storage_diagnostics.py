"""Provider callbacks must expose failed handle persistence before polling."""

import json

import pytest

from src.models.wanx import WanxModel
from src.utils.json_storage import StorageError


class FakeResponse:
    status_code = 200

    def __init__(self, payload):
        self.payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self.payload

    def raise_for_status(self):
        pass

    def iter_content(self, chunk_size):
        yield b"synthetic completed video"


@pytest.fixture
def fake_transport(monkeypatch):
    """Replace only HTTP/time boundaries; use the real adapter and downloader."""
    events = []

    def submit(url, **kwargs):
        assert url.endswith("/api/v1/services/aigc/video-generation/video-synthesis")
        assert kwargs["json"]["model"] == "happyhorse-1.0-t2v"
        events.append("submit")
        return FakeResponse({"request_id": "request-1", "output": {"task_id": "task-1"}})

    def poll(url, **kwargs):
        assert url.endswith("/api/v1/tasks/task-1")
        events.append("poll")
        return FakeResponse({"output": {
            "task_status": "SUCCEEDED", "video_url": "https://media.example.invalid/result.mp4",
        }})

    def download(session, url, **kwargs):
        assert url == "https://media.example.invalid/result.mp4"
        events.append("download")
        return FakeResponse({})

    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-only-placeholder")
    monkeypatch.setattr("src.models.wanx.OSSImageUploader._instance", None)
    monkeypatch.setattr("src.models.wanx.requests.post", submit)
    monkeypatch.setattr("src.models.wanx.requests.get", poll)
    monkeypatch.setattr("src.models.wanx.requests.Session.get", download)
    monkeypatch.setattr("src.models.wanx.time.sleep", lambda _: events.append("sleep"))
    return events


@pytest.mark.parametrize("entry", ["http_helper", "public_generate"])
@pytest.mark.parametrize("uncertain", [False, True])
def test_handle_save_failure_stops_real_adapter_before_polling(
    tmp_path, fake_transport, entry, uncertain,
):
    model = WanxModel({"output_root": str(tmp_path)})
    failure = StorageError("handle persistence failed", commit_uncertain=uncertain)
    output_path = tmp_path / "result.mp4"

    def save_handle(provider, task_id, request_id):
        assert (provider, task_id, request_id) == ("dashscope", "task-1", "request-1")
        fake_transport.append("callback")
        raise failure

    with pytest.raises(StorageError) as raised:
        if entry == "http_helper":
            model._generate_hh_http(
                prompt="test", model_name="happyhorse-1.0-t2v", on_provider_ids=save_handle,
            )
        else:
            model.generate(
                prompt="test", model_name="happyhorse-1.0-t2v",
                output_path=str(output_path), on_provider_ids=save_handle,
            )

    assert raised.value is failure
    assert raised.value.commit_uncertain is uncertain
    assert fake_transport == ["submit", "callback"]
    assert not output_path.exists()


def test_non_storage_callback_error_preserves_existing_warning_behavior(
    tmp_path, fake_transport, caplog,
):
    model = WanxModel({"output_root": str(tmp_path)})

    def broken_diagnostic(*args):
        fake_transport.append("callback")
        raise ValueError("diagnostic display failed")

    url = model._generate_hh_http(
        prompt="test", model_name="happyhorse-1.0-t2v", on_provider_ids=broken_diagnostic,
    )
    assert url == "https://media.example.invalid/result.mp4"
    assert fake_transport == ["submit", "callback", "sleep", "poll"]
    assert "on_provider_ids callback failed: diagnostic display failed" in caplog.text


def test_successful_callback_precedes_poll_and_public_download(tmp_path, fake_transport):
    model = WanxModel({"output_root": str(tmp_path)})
    output_path = tmp_path / "result.mp4"

    def save_handle(provider, task_id, request_id):
        assert (provider, task_id, request_id) == ("dashscope", "task-1", "request-1")
        fake_transport.append("callback")

    result, _ = model.generate(
        prompt="test", model_name="happyhorse-1.0-t2v",
        output_path=str(output_path), on_provider_ids=save_handle,
    )
    assert result == str(output_path)
    assert output_path.read_bytes() == b"synthetic completed video"
    assert fake_transport == ["submit", "callback", "sleep", "poll", "download"]
