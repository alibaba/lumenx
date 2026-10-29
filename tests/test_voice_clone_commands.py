"""Voice clone command safety around provider I/O and series writes."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from src.apps.comic_gen import api, pipeline as module
from src.apps.comic_gen.models import CustomVoice, Series
from src.utils.json_storage import StorageConflictError, StorageError


class Response:
    status_code = 200
    text = "ok"

    def __init__(self, body):
        self._body = body

    def json(self):
        return self._body


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    for name in ("AssetGenerator", "StoryboardGenerator", "VideoGenerator", "AudioGenerator", "ExportManager"):
        monkeypatch.setattr(module, name, Mock())
    item = module.ComicGenPipeline(data_dir=tmp_path / "data", script_processor=Mock())
    item.series_store["series"] = Series(id="series", title="Series", created_at=1, updated_at=1)
    item.series_store["other"] = Series(id="other", title="Other", created_at=1, updated_at=1)
    item._commit_storage("projects.json", "series.json", "library_assets.json")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "offline-key")
    yield item
    item.close()


def install_post(monkeypatch, body=None, hook=None):
    calls = []

    def post(url, json, headers, timeout):
        calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        if hook:
            hook(calls[-1])
        response_body = body if body is not None else {"output": {"voice": f"voice-{len(calls)}"}}
        return Response(response_body)

    monkeypatch.setattr("requests.post", post)
    return calls


def clone(runtime, series_id="series", label="Voice", audio_url="uploads/ref.wav"):
    return runtime.create_voice_clone(series_id, audio_url, label)


def test_provider_call_does_not_hold_series_save_lock(runtime, monkeypatch):
    entered, release, update_done = Event(), Event(), Event()

    def hook(_call):
        entered.set()
        assert release.wait(3), "test did not release fake provider"

    install_post(monkeypatch, {"output": {"voice": "voice-from-provider"}}, hook)

    def update_series():
        runtime.update_series("series", {"title": "edited while provider pending"})
        update_done.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        cloning = pool.submit(clone, runtime)
        assert entered.wait(2), "voice clone did not reach provider"
        updater = pool.submit(update_series)
        try:
            assert update_done.wait(0.2), "voice clone held the save lock across provider I/O"
        finally:
            release.set()
        updater.result(timeout=2)
        result = cloning.result(timeout=2)
    assert result.id == "voice-from-provider"
    assert runtime.series_store["series"].title == "edited while provider pending"


@pytest.mark.parametrize("payload", [
    {"series_id": "series", "audio_url": "", "label": "Voice"},
    {"series_id": "series", "audio_url": "file:///etc/passwd", "label": "Voice"},
    {"series_id": "series", "audio_url": "data:audio/wav;base64,AAAA", "label": "Voice"},
    {"series_id": "series", "audio_url": "javascript:alert(1)", "label": "Voice"},
    {"series_id": "series", "audio_url": "http:///missing-host.wav", "label": "Voice"},
    {"series_id": "series", "audio_url": "https://:443/ref.wav", "label": "Voice"},
    {"series_id": "series", "audio_url": "https://[invalid/ref.wav", "label": "Voice"},
    {"series_id": "series", "audio_url": "uploads/ref.wav", "label": "   "},
    {"series_id": "series", "audio_url": "uploads/ref.wav", "label": "Voice", "target_model": ""},
])
def test_invalid_clone_request_is_422_before_provider(runtime, monkeypatch, payload):
    calls = install_post(monkeypatch)
    app = api.create_app(data_dir=runtime.data_dir, load_config=False, pipeline_instance=runtime)
    with TestClient(app) as client:
        response = client.post("/voice/clone", json=payload)
    assert response.status_code == 422, response.text
    assert calls == []
    assert runtime.series_store["series"].custom_voices == []


@pytest.mark.parametrize("kwargs", [
    {"audio_url": 123},
    {"label": "\ud800"},
])
def test_invalid_clone_command_rejects_non_text_or_non_utf8_before_provider(runtime, monkeypatch, kwargs):
    calls = install_post(monkeypatch)
    with pytest.raises(module.CommandValidationError):
        runtime.create_voice_clone(
            series_id="series",
            audio_url=kwargs.get("audio_url", "uploads/ref.wav"),
            label=kwargs.get("label", "Voice"),
            target_model="cosyvoice-v3.5-plus",
        )
    assert calls == []
    assert runtime.series_store["series"].custom_voices == []


@pytest.mark.parametrize("state", ["closed", "failed"])
def test_unavailable_runtime_rejects_before_provider(runtime, monkeypatch, state):
    calls = install_post(monkeypatch)
    if state == "closed":
        runtime.close()
    else:
        runtime._storage_failed = StorageError("previous save failed")
    with pytest.raises(StorageError):
        clone(runtime)
    assert calls == []
    assert runtime.series_store["series"].custom_voices == []


@pytest.mark.parametrize("change", ["delete", "replace", "close"])
def test_late_series_change_or_close_does_not_append_paid_voice(runtime, monkeypatch, change):
    def hook(_call):
        if change == "delete":
            del runtime.series_store["series"]
            runtime._save_series_data_unlocked()
        elif change == "replace":
            runtime.series_store["series"] = Series(id="series", title="Replacement", created_at=2, updated_at=2)
            runtime._save_series_data_unlocked()
        else:
            runtime.close()

    install_post(monkeypatch, {"output": {"voice": "paid-voice"}}, hook)
    expected = StorageError if change == "close" else StorageConflictError
    with pytest.raises(expected):
        clone(runtime)
    if change == "replace":
        assert runtime.series_store["series"].title == "Replacement"
        assert runtime.series_store["series"].custom_voices == []
    elif change == "delete":
        assert "series" not in runtime.series_store


def test_save_failure_preserves_existing_data_and_paid_response(runtime, monkeypatch):
    existing = CustomVoice(id="existing", label="Existing", origin="clone", target_model="cosyvoice-v3.5-plus", family="cosyvoice")
    runtime.series_store["series"].custom_voices.append(existing)
    runtime._save_series_data()
    calls = install_post(monkeypatch, {"output": {"voice": "paid-voice"}})
    failure = StorageError("disk full after provider")
    monkeypatch.setattr(runtime._storage, "commit", Mock(side_effect=failure))

    with pytest.raises(StorageError) as raised:
        clone(runtime)
    assert raised.value is failure
    assert len(calls) == 1
    assert calls[0]["json"]["input"]["url"] == "uploads/ref.wav"
    assert [voice.id for voice in runtime.series_store["series"].custom_voices] == ["existing"]


@pytest.mark.parametrize("body", [
    {},
    {"output": {"voice": ""}},
    {"output": {"voice": 123}},
    {"output": {"voice": {"id": "nested"}}},
    {"voice": ["list"]},
])
def test_malformed_provider_voice_id_does_not_append(runtime, monkeypatch, body):
    install_post(monkeypatch, body)
    with pytest.raises(RuntimeError):
        clone(runtime)
    assert runtime.series_store["series"].custom_voices == []


def test_duplicate_provider_voice_id_does_not_append_or_overwrite(runtime, monkeypatch):
    runtime.series_store["series"].custom_voices.append(
        CustomVoice(id="dup", label="Existing", origin="clone", target_model="cosyvoice-v3.5-plus", family="cosyvoice")
    )
    runtime._save_series_data()
    install_post(monkeypatch, {"output": {"voice": "dup"}})
    with pytest.raises(StorageConflictError):
        clone(runtime, label="New Voice")
    voices = runtime.series_store["series"].custom_voices
    assert [(voice.id, voice.label) for voice in voices] == [("dup", "Existing")]



def test_malformed_provider_body_is_safe_500_without_body_leak(runtime, monkeypatch, caplog):
    caplog.set_level("ERROR")
    install_post(monkeypatch, {"output": {"voice": {"id": "paid-voice-id"}}})
    app = api.create_app(data_dir=runtime.data_dir, load_config=False, pipeline_instance=runtime)
    with TestClient(app) as client:
        response = client.post("/voice/clone", json={"series_id": "series", "audio_url": "uploads/ref.wav", "label": "Voice"})
        assert response.status_code == 500, response.text
        assert "paid-voice-id" not in response.text
        assert "output" not in response.text
        assert "paid-voice-id" not in caplog.text
        assert "output" not in caplog.text
        assert runtime.series_store["series"].custom_voices == []
        runtime.assert_storage_available()


def test_provider_surrogate_voice_id_is_rejected_before_mutation_and_runtime_stays_healthy(runtime, monkeypatch):
    install_post(monkeypatch, {"output": {"voice": "bad\ud800voice"}})
    with pytest.raises(RuntimeError) as raised:
        clone(runtime)
    assert "bad" not in str(raised.value)
    assert runtime.series_store["series"].custom_voices == []
    runtime.assert_storage_available()


def test_duplicate_and_late_replacement_are_http_409_but_initial_missing_is_404(runtime, monkeypatch):
    runtime.series_store["series"].custom_voices.append(
        CustomVoice(id="dup", label="Existing", origin="clone", target_model="cosyvoice-v3.5-plus", family="cosyvoice")
    )
    runtime._save_series_data()
    install_post(monkeypatch, {"output": {"voice": "dup"}})
    app = api.create_app(data_dir=runtime.data_dir, load_config=False, pipeline_instance=runtime)
    with TestClient(app) as client:
        duplicate = client.post("/voice/clone", json={"series_id": "series", "audio_url": "uploads/ref.wav", "label": "Voice"})
        missing = client.post("/voice/clone", json={"series_id": "missing", "audio_url": "uploads/ref.wav", "label": "Voice"})

        def replace_before_response(_call):
            runtime.series_store["other"] = Series(id="other", title="Replacement", created_at=2, updated_at=2)
            runtime._save_series_data_unlocked()

        install_post(monkeypatch, {"output": {"voice": "paid-voice"}}, replace_before_response)
        replaced = client.post("/voice/clone", json={"series_id": "other", "audio_url": "uploads/ref.wav", "label": "Voice"})
    assert duplicate.status_code == 409, duplicate.text
    assert missing.status_code == 404, missing.text
    assert replaced.status_code == 409, replaced.text

def test_concurrent_independent_clones_append_safely(runtime, monkeypatch):
    install_post(monkeypatch)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(clone, runtime, "series", "First")
        second = pool.submit(clone, runtime, "other", "Second")
        assert first.result(timeout=2).id
        assert second.result(timeout=2).id
    assert [voice.label for voice in runtime.series_store["series"].custom_voices] == ["First"]
    assert [voice.label for voice in runtime.series_store["other"].custom_voices] == ["Second"]


def test_returned_clone_is_detached_from_live_series(runtime, monkeypatch):
    install_post(monkeypatch, {"output": {"voice": "detached"}})
    result = clone(runtime)
    result.label = "mutated response"
    assert runtime.series_store["series"].custom_voices[0].label == "Voice"
