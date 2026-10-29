"""Current visible first-frame model uses the durable request contract."""

from dataclasses import replace

import pytest

from src.models.provider_tasks import ProviderBindingError
from src.utils.generation_intent import normalize_generation_intent
from src.utils.model_catalog import get_catalog_accessor
from tests import test_provider_submit as submit_fixtures
from tests import test_provider_video_entry as entry_fixtures


contract = submit_fixtures.contract
credential = submit_fixtures.credential
model = submit_fixtures.model
scope = submit_fixtures.scope
submission = submit_fixtures.submission
transport = submit_fixtures.transport
entry = entry_fixtures.entry


def request(**overrides):
    return {
        "client_request_id": "wan27-action", "batch_size": 2,
        "image_url": "uploads/source.png", "frame_id": "frame",
        "model": "wan2.7-i2v", "prompt": "A slow sunrise", "generation_mode": "i2v",
        "workbench_tab": "t2i_i2v", **overrides,
    }


def intent(**params):
    return normalize_generation_intent(
        {"model_id": "wan2.7-i2v", "mode": "i2v", "prompt": "a" * 5000, "params": params},
        allowed_modes={"i2v"}, allowed_parameters={
            "duration", "resolution", "seed", "prompt_extend", "negative_prompt", "watermark",
        },
    )


def test_visible_catalog_does_not_offer_unsupported_ratio():
    catalog = get_catalog_accessor()
    entry = catalog.get_mode_entry(catalog.resolve_legacy_to_canonical("wan2.7-i2v"))
    assert "video_sidebar" in entry["ui"]["visible_in"]
    assert "ratio" not in entry["params"]


@pytest.mark.parametrize("watermark", [False, True])
def test_actual_submit_uses_media_protocol_and_preserves_explicit_values(
    submission, model, credential, transport, watermark,
):
    value = replace(submission, provider_model_id="wan2.7-i2v", intent=intent(
        duration=15, resolution="720p", seed=0, prompt_extend=False,
        negative_prompt="", watermark=watermark,
    ))
    saved = []
    handle = model.submit(value, credential=credential, on_submitted=lambda item: saved.append(item))
    assert saved == [handle] and handle.provider_model_id == "wan2.7-i2v"
    assert len(transport[1]) == 1
    payload = transport[1][0][1]["json"]
    assert payload == {
        "model": "wan2.7-i2v", "input": {
            "prompt": "a" * 5000, "negative_prompt": "",
            "media": [{"type": "first_frame", "url": value.media[0].url}],
        },
        "parameters": {"duration": 15, "resolution": "720P", "seed": 0,
                       "prompt_extend": False, "watermark": watermark},
    }


@pytest.mark.parametrize("params", [
    {"ratio": "16:9"}, {"audio": False}, {"shot_type": "single"},
    {"seed": 2147483648}, {"watermark": 0}, {"negative_prompt": "a" * 501},
])
def test_frozen_invalid_submission_fails_before_http(submission, model, credential, transport, params):
    original = intent()
    value = replace(submission, provider_model_id="wan2.7-i2v",
                    intent=replace(original, params={**original.params, **params}))
    with pytest.raises(ProviderBindingError):
        model.submit(value, credential=credential, on_submitted=lambda item: None)
    assert transport[1] == []


def test_product_admission_freezes_defaults_watermark_and_replays_without_dispatch(entry):
    runtime, client, scheduled = entry
    raw = request(watermark=False, prompt="a" * 5000)
    response = client.post("/projects/p/provider_video_tasks", json=raw)
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["tasks"]) == len(scheduled) == 2
    record = body["tasks"][0]["provider_execution"]
    assert record["provider_model_id"] == "wan2.7-i2v"
    assert record["intent"]["params"] == {"duration": 5, "resolution": "1080p", "watermark": False}
    assert record["intent"]["sources"]["params.duration"] == "catalog"
    assert record["intent"]["sources"]["params.watermark"] == "request"
    runtime.provider_recovery_runtime = None
    (runtime.data_dir / "uploads/source.png").unlink()
    scheduled.clear()
    replay = client.post("/projects/p/provider_video_tasks", json=raw)
    assert replay.status_code == 200 and replay.json()["replayed"]
    assert replay.json()["task_ids"] == body["task_ids"] and scheduled == []


def test_product_submission_uses_frozen_media_through_real_adapter(entry, transport, model, monkeypatch):
    runtime, client, scheduled = entry
    runtime.provider_recovery_runtime = replace(runtime.provider_recovery_runtime, provider=model)
    response = client.post("/projects/p/provider_video_tasks", json=request(batch_size=1, watermark=False))
    assert response.status_code == 200, response.text
    task_id = response.json()["task_ids"][0]
    (runtime.data_dir / "uploads/source.png").write_bytes(b"different content after admission")
    monkeypatch.setattr("src.utils.model_catalog.get_catalog_accessor", lambda: pytest.fail("catalog reread"))
    func, args, kwargs = scheduled.pop()
    func(*args, **kwargs)
    task = runtime.read_committed_script("p").video_tasks[0]
    assert task.id == task_id and task.provider_execution.handle["task_id"] == "task-1"
    assert len(transport[1]) == 1
    sent = transport[1][0][1]["json"]
    assert sent["input"]["media"][0]["type"] == "first_frame"
    assert sent["input"]["media"][0]["url"].startswith("data:image/png;base64,")
    assert sent["parameters"] == {"duration": 5, "resolution": "1080P", "watermark": False}
    replay = client.post("/projects/p/provider_video_tasks", json=request(batch_size=1, watermark=False))
    assert replay.status_code == 200 and replay.json()["replayed"]
    assert len(transport[1]) == 1 and scheduled == []


@pytest.mark.parametrize("overrides", [
    {"generate_audio": False}, {"shot_type": "single"}, {"ratio": "16:9"},
    {"audio_url": "uploads/audio.wav"}, {"reference_image_urls": []},
    {"watermark": None}, {"prompt": "a" * 5001}, {"seed": 2147483648},
])
def test_product_does_not_drop_unsupported_explicit_fields(entry, overrides):
    runtime, client, scheduled = entry
    response = client.post("/projects/p/provider_video_tasks", json=request(**overrides))
    assert response.status_code == 422, response.text
    assert runtime.read_committed_script("p").video_tasks == [] and scheduled == []
