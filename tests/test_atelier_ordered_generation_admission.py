"""Wan3 modes retain ordered frozen inputs in the existing Atelier ledger."""

import io
import wave

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.apps.atelier.generation_admission import AtelierGenerationAdmissionService
from src.apps.atelier.generation_commands import ManualOrderedVideoCandidateBatchCommand
from src.apps.atelier.models import AtelierNode, AtelierProject
from src.apps.atelier.storage import AtelierStorage
from src.models.provider_tasks import ProviderScope, ResolvedCredential
from src.utils.json_storage import StorageConflictError
from tests.frozen_image_fixtures import rgb_png_bytes


PNG = rgb_png_bytes("ordered admission")
SCOPE = ProviderScope(provider="dashscope", backend="dashscope", gateway="dashscope_workspace",
                      base_url="https://workspace.cn-beijing.maas.aliyuncs.com", region="cn-beijing", workspace_id="workspace")


def command(**changes):
    return ManualOrderedVideoCandidateBatchCommand(**{
        "contract_version": "ordered-media-v1", "expected_revision": 0,
        "client_request_id": "ordered-request", "model_id": "wan/wan3.0-video#video",
        "mode": "t2v", "prompt": "Slow sunrise", "media": [], **changes,
    })


def image(role, node="image", ordinal=0):
    return {"role": role, "node_id": node, "ordinal": ordinal, "media_url": f"uploads/{node}.png"}


@pytest.fixture
def owner(tmp_path):
    store = AtelierStorage(tmp_path)
    store.create_project(AtelierProject(id="p", title="Canvas", created_at=1.0, updated_at=1.0, nodes=[
        AtelierNode(id=name, project_id="p", type="image", media_urls=[f"uploads/{name}.png"])
        for name in ("image", "last", "unattached")
    ] + [
        AtelierNode(id="audio", project_id="p", type="audio", media_urls=["uploads/ref.wav"]),
        AtelierNode(id="video", project_id="p", type="video", data={"reference_node_ids": ["image", "last", "audio"]}),
    ]))
    (tmp_path / "uploads").mkdir()
    for name in ("image", "last", "unattached"):
        (tmp_path / "uploads" / f"{name}.png").write_bytes(PNG)
    bindings = []

    def bind(**kwargs):
        bindings.append(kwargs)
        return ResolvedCredential(SCOPE, "wan3-key", "test-only-secret")

    service = AtelierGenerationAdmissionService(store, credential_provider=bind)
    yield service, store, bindings, tmp_path
    store.close()


@pytest.mark.parametrize("batch_size", [1, 3])
def test_ordered_text_only_admission_freezes_defaults_and_all_batch_members(owner, batch_size):
    service, store, bindings, _ = owner
    ack = service.admit("p", "video", command(batch_size=batch_size, params={"audio": False, "seed": 0, "duration": -1}))
    attempts = ack.project.generation_attempts
    assert len(attempts) == batch_size and len(ack.project.generation_request_receipts) == 1
    assert ack.receipt.entrypoint == "atelier.provider_video.v2"
    assert ack.receipt.request_contract_version == "request-json-v1"
    assert len(ack.receipt.receipt_members) == batch_size
    for attempt in attempts:
        assert attempt.input_contract_version == "ordered-media-v1" and attempt.input_media == []
        assert attempt.first_frame_source is None
        record = attempt.provider_execution
        assert record.phase == "accepted" and record.media == []
        assert record.intent["params"]["audio"] is False and record.intent["params"]["seed"] == 0
        assert record.intent["params"]["duration"] == -1 and record.intent["params"]["resolution"] == "1080P"
    assert bindings == [{"provider_model_id": "wan3.0-video", "gateway": "dashscope_workspace"}]
    assert "test-only-secret" not in store.path.read_text()
    assert store.get_project("p") == ack.project


@pytest.mark.parametrize("mode,media", [
    ("i2v", [image("first_frame"), image("last_frame", "last")]),
    ("r2v", [image("reference_image"), image("reference_image", "last", 1)]),
])
def test_ordered_images_persist_source_order_and_replay_without_sources(owner, monkeypatch, mode, media):
    service, store, bindings, root = owner
    request = command(mode=mode, media=media, batch_size=2)
    ack = service.admit("p", "video", request)
    for attempt in ack.project.generation_attempts:
        assert [s.model_dump() for s in attempt.input_media] == media
        assert [(m["role"], m["ordinal"]) for m in attempt.provider_execution.media] == [(m["role"], m["ordinal"]) for m in media]
    for path in (root / "uploads").iterdir():
        path.unlink()
    monkeypatch.setattr("src.apps.atelier.generation_preview.freeze_media", lambda *_a, **_k: pytest.fail("replay froze media"))
    replay = service.admit("p", "video", request.model_copy(update={"expected_revision": 999}))
    assert replay.replayed and replay.new_candidate_ids == []
    assert replay.receipt == ack.receipt and replay.project == store.get_project("p")
    assert len(bindings) == 1


@pytest.mark.parametrize("media", [
    [image("first_frame", "unattached")],
    [{**image("first_frame"), "media_url": "uploads/last.png"}],
    [{**image("first_frame"), "node_id": "foreign-image"}],
    [{**image("first_frame"), "node_id": "video"}],
])
def test_unowned_ordered_source_is_rejected_before_binding_or_transaction(owner, media):
    service, store, bindings, _ = owner
    before = store.path.read_bytes()
    with pytest.raises(ValueError):
        service.admit("p", "video", command(mode="i2v", media=media))
    assert bindings == [] and store.path.read_bytes() == before


def test_original_ordered_request_id_cannot_be_reused_with_other_mode(owner):
    service, store, bindings, _ = owner
    service.admit("p", "video", command())
    before = store.path.read_bytes()
    with pytest.raises(StorageConflictError):
        service.admit("p", "video", command(mode="i2v", media=[image("first_frame")], expected_revision=1))
    assert store.path.read_bytes() == before and len(bindings) == 1


def test_ordered_audio_admission_probes_fixed_bytes_without_upload(owner, monkeypatch):
    from src.models.provider_recovery import reconstruct_submission_from_record

    service, store, _, root = owner
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(b"\0\0" * 16000)
    data = buffer.getvalue()
    source = root / "uploads/ref.wav"
    source.write_bytes(data)
    monkeypatch.setattr("requests.get", lambda *_a, **_k: pytest.fail("admission requested upload policy"))
    monkeypatch.setattr("requests.post", lambda *_a, **_k: pytest.fail("admission performed network work"))
    ack = service.admit("p", "video", command(mode="r2v", media=[{
        "role": "reference_audio", "ordinal": 0, "node_id": "audio", "media_url": "uploads/ref.wav",
    }], params={"audio": False}))
    source.unlink()
    restored = reconstruct_submission_from_record(ack.attempt.provider_execution, output_root=root)
    assert restored.media[0].frozen_bytes == data
    assert restored.media[0].transport_binding == "frozen_av_sha256_v1"
    assert restored.intent.params["audio"] is False
    assert "oss://" not in store.path.read_text() and "frozen-av://" not in store.path.read_text()


@pytest.mark.parametrize("batch_size", [1, 2])
def test_ordered_http_ack_schedules_each_original_attempt_once(owner, batch_size):
    from src.apps.atelier.generation_api import router

    service, store, _, _ = owner
    scheduled = []

    class Dispatcher:
        def schedule(self, project_id, attempt_id):
            scheduled.append((project_id, attempt_id))

    app = FastAPI()
    app.state.atelier_generation_admission = service
    app.state.atelier_generation_dispatcher = Dispatcher()
    app.include_router(router, prefix="/atelier")
    with TestClient(app) as client:
        request = command(batch_size=batch_size).model_dump(mode="json", exclude_unset=True)
        response = client.post("/atelier/projects/p/nodes/video/video_candidates", json=request)
        assert response.status_code == 200, response.text
        wire = response.json()
        assert wire["receipt"]["entrypoint"] == "atelier.provider_video.v2"
        attempts = [wire["attempt"]] if batch_size == 1 else [member["attempt"] for member in wire["members"]]
        assert scheduled == [("p", attempt["attempt_id"]) for attempt in attempts]
        for attempt in attempts:
            assert "first_frame_source" not in attempt and attempt["input_media"] == []
        replay = client.post("/atelier/projects/p/nodes/video/video_candidates", json=request)
        assert replay.status_code == 200 and replay.json()["replayed"] is True
        assert len(scheduled) == batch_size
        saved = client.get("/atelier/projects/p/generation/requests/ordered-request")
        assert saved.status_code == 200 and saved.json()["receipt"] == wire["receipt"]
    assert len(store.get_project("p").generation_attempts) == batch_size


def test_ordered_http_rejects_missing_discriminator_or_media_before_admission(owner):
    from src.apps.atelier.generation_api import router

    service, store, bindings, _ = owner
    app = FastAPI()
    app.state.atelier_generation_admission = service
    app.include_router(router, prefix="/atelier")
    before = store.path.read_bytes()
    with TestClient(app) as client:
        for key in ("contract_version", "media"):
            request = command().model_dump(mode="json", exclude_unset=True)
            del request[key]
            assert client.post("/atelier/projects/p/nodes/video/video_candidates", json=request).status_code == 422
    assert store.path.read_bytes() == before and bindings == []
