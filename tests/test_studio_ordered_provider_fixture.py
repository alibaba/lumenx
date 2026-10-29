"""Real Studio ordered provider admission wire fixtures for frontend parser tests."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any
from fastapi.testclient import TestClient

from src.apps.comic_gen import api
from src.apps.comic_gen import pipeline as pipeline_module
from src.apps.comic_gen.models import Character, Scene, Script, StoryboardFrame
from src.models import provider_binding as provider_binding_module
from src.models.provider_tasks import ProviderScope
from src.utils.json_storage import serialize_json
from tests.test_provider_video_entry import URL
from tests.test_studio_ordered_provider_entry import _image_bytes
from tests.test_studio_provider_recovery import FakeProvider
from tests.test_studio_provider_recovery_dispatcher import _make_runtime

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "frontend" / "src" / "__tests__" / "fixtures"
FIXTURE_NAMES = {
    "t2v": "studio-ordered-provider-t2v.json",
    "i2v_batch": "studio-ordered-provider-i2v-batch.json",
    "r2v": "studio-ordered-provider-r2v.json",
}


def _canonical_bytes(value: Any) -> bytes:
    return (serialize_json(value).rstrip("\n") + "\n").encode("utf-8")


def _fixed_uuid4_factory():
    counter = 0

    def fixed_uuid4():
        nonlocal counter
        counter += 1
        return uuid.UUID(f"00000000-0000-4000-8000-{counter:012x}")

    return fixed_uuid4


def _install_determinism(monkeypatch) -> None:
    fixed_uuid4 = _fixed_uuid4_factory()
    monkeypatch.setattr(pipeline_module.uuid, "uuid4", fixed_uuid4)
    monkeypatch.setattr(provider_binding_module.uuid, "uuid4", fixed_uuid4)
    monkeypatch.setattr(pipeline_module.time, "time", lambda: 1791700000.0)


def _runtime(tmp_path, monkeypatch):
    _install_determinism(monkeypatch)
    runtime = _make_runtime(tmp_path, monkeypatch, FakeProvider())
    runtime.provider_recovery_runtime = runtime.provider_recovery_runtime.__class__(
        scope=ProviderScope(
            provider="dashscope",
            backend="dashscope",
            gateway="dashscope_workspace",
            base_url="https://workspace.cn-beijing.maas.aliyuncs.com",
            region="cn-beijing",
            workspace_id="workspace",
        ),
        secret_resolver=runtime.provider_recovery_runtime.secret_resolver,
        provider=runtime.provider_recovery_runtime.provider,
        max_bytes=runtime.provider_recovery_runtime.max_bytes,
    )
    runtime.scripts["p"] = Script(
        id="p",
        title="project",
        original_text="source",
        created_at=1,
        updated_at=1,
        scenes=[Scene(id="scene", name="Room", description="quiet", image_url="uploads/source.png")],
        characters=[Character(id="char", name="Hero", description="red coat", image_url="uploads/source.png")],
        frames=[StoryboardFrame(id="frame", scene_id="scene")],
        video_tasks=[],
    )
    uploads = runtime.data_dir / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    for name in ("source.png", "last.png", "ref.png"):
        (uploads / name).write_bytes(_image_bytes())
    # The AV validator accepts this simple PCM WAV as a 1s reference audio fixture.
    import io
    import wave

    audio = io.BytesIO()
    with wave.open(audio, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(16000)
        writer.writeframes(b"\0\0" * 16000)
    (uploads / "ref.wav").write_bytes(audio.getvalue())
    runtime._commit_storage("projects.json", "series.json", "library_assets.json")
    return runtime


def _payload(**changes):
    payload = {
        "client_request_id": "ordered-action",
        "contract_version": "ordered-media-v1",
        "model": "wan3.0-video",
        "generation_mode": "t2v",
        "frame_id": "frame",
        "prompt": "A quiet shot",
        "duration": -1,
        "resolution": "1080P",
        "ratio": "16:9",
        "seed": 0,
        "generate_audio": False,
        "prompt_extend": False,
        "watermark": False,
        "workbench_tab": "t2i_i2v",
        "batch_size": 1,
    }
    payload.update(changes)
    return payload


def _post_and_replay(client: TestClient, request: dict[str, Any]) -> dict[str, Any]:
    first = client.post(URL, json=request)
    assert first.status_code == 200, first.text
    replay = client.post(URL, json=request)
    assert replay.status_code == 200, replay.text
    first_body = first.json()
    replay_body = replay.json()
    assert first_body["replayed"] is False
    assert replay_body["replayed"] is True
    assert replay_body["task_ids"] == first_body["task_ids"]
    return {"request": request, "accepted": first_body, "replay": replay_body}


def generate_studio_ordered_provider_wire(tmp_path, monkeypatch) -> dict[str, dict[str, Any]]:
    runtime = _runtime(tmp_path, monkeypatch)
    monkeypatch.setattr(api.BackgroundTasks, "add_task", lambda self, func, *args, **kwargs: None)
    app = api.create_app(data_dir=runtime.data_dir, pipeline_instance=runtime, load_config=False, recover_orphan_tasks=False)
    with TestClient(app) as client:
        return {
            "t2v": _post_and_replay(client, _payload(client_request_id="ordered-t2v", prompt="A quiet T2V shot")),
            "i2v_batch": _post_and_replay(client, _payload(
                client_request_id="ordered-i2v-batch",
                model="wan3.0-video-prime",
                generation_mode="i2v",
                prompt="A first and last frame motion",
                image_url="uploads/source.png",
                last_frame_url="uploads/last.png",
                batch_size=2,
            )),
            "r2v": _post_and_replay(client, _payload(
                client_request_id="ordered-r2v",
                generation_mode="r2v",
                prompt="A reference image and audio driven shot",
                reference_image_urls=["uploads/ref.png"],
                reference_audio_urls=["uploads/ref.wav"],
                workbench_tab="direct_r2v",
            )),
        }


def test_checked_in_studio_ordered_provider_wire_matches_real_http(tmp_path, monkeypatch):
    generated = generate_studio_ordered_provider_wire(tmp_path, monkeypatch)
    generated_dir = tmp_path / "studio-ordered-provider-wire"
    generated_dir.mkdir()
    for key, value in generated.items():
        generated_path = generated_dir / FIXTURE_NAMES[key]
        generated_path.write_bytes(_canonical_bytes(value))
        print(f"Generated fixture: {generated_path}")
    for filename in FIXTURE_NAMES.values():
        assert (FIXTURE_DIR / filename).read_bytes() == (generated_dir / filename).read_bytes()
