"""Ordered video intent uses the existing draft record without execution rights."""

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from src.apps.atelier.generation_draft import AtelierGenerationDraftService
from src.apps.atelier.storage import AtelierStorage
from tests.test_atelier_generation_draft import _store, _node


def ordered_draft(**changes):
    from src.apps.atelier.generation_draft import OrderedGenerationDraftCommand

    return OrderedGenerationDraftCommand(**{
        "contract_version": "ordered-media-v1", "expected_revision": 1,
        "prompt": "", "model_id": "wan/wan3.0-video#video", "mode": "i2v",
        "media": [], "params": {}, "batch_size": 2, **changes,
    })


def test_ordered_draft_allows_incomplete_input_and_preserves_explicit_values_on_restart(tmp_path, monkeypatch):
    store = _store(tmp_path)
    before = store.get_project("p1")
    monkeypatch.setattr("src.apps.atelier.generation_preview.freeze_media", lambda *_a, **_k: pytest.fail("draft froze media"))
    service = AtelierGenerationDraftService(store, clock=lambda: 90.0)
    command = ordered_draft(params={"audio": False, "seed": 0, "duration": -1})
    ack = service.update_generation_draft("p1", "node-1", command)
    expected = {
        "schema_version": 2, "input_contract_version": "ordered-media-v1",
        "model_id": "wan/wan3.0-video#video", "mode": "i2v", "media": [],
        "params": {"audio": False, "seed": 0, "duration": -1}, "batch_size": 2,
    }
    assert _node(ack.project).data["generation_draft"] == expected
    assert _node(ack.project).prompt == ""
    assert ack.project.generation_attempts == before.generation_attempts
    assert ack.project.generation_request_receipts == before.generation_request_receipts
    same = service.update_generation_draft("p1", "node-1", command.model_copy(update={"expected_revision": 2}))
    assert same.project == ack.project
    store.close()
    reopened = AtelierStorage(tmp_path)
    try:
        assert _node(reopened.get_project("p1")).data["generation_draft"] == expected
    finally:
        reopened.close()


@pytest.mark.parametrize("changes", [
    {"model_id": "wan/wan2.7-video#i2v"}, {"contract_version": "unknown"},
    {"params": {"negative_prompt": "unsupported"}}, {"params": {"seed": False}},
    {"params": {"audio": None}}, {"params": {"duration": 0}},
    {"batch_size": 7}, {"batch_size": True}, {"media": None},
])
def test_ordered_draft_rejects_unknown_or_invalid_fields(changes):
    with pytest.raises(ValidationError):
        ordered_draft(**changes)


def test_ordered_draft_keeps_source_identity_and_resets_omitted_overrides(tmp_path):
    store = _store(tmp_path)
    try:
        service = AtelierGenerationDraftService(store)
        media = [{"role": "last_frame", "ordinal": 0, "node_id": "last", "media_url": "uploads/last.png"}]
        saved = service.update_generation_draft("p1", "node-1", ordered_draft(media=media, params={"seed": -1}))
        assert _node(saved.project).data["generation_draft"]["media"] == media
        reset = service.update_generation_draft("p1", "node-1", ordered_draft(expected_revision=2))
        assert _node(reset.project).data["generation_draft"]["params"] == {}
        assert _node(reset.project).data["generation_draft"]["media"] == []
    finally:
        store.close()


def test_ordered_draft_http_uses_existing_endpoint_and_does_not_accept_stale_writes(tmp_path):
    from src.apps.atelier.models import AtelierNode, AtelierProject
    from src.apps.comic_gen.api import create_app

    app = create_app(data_dir=tmp_path, load_config=False, recover_orphan_tasks=False)
    with TestClient(app) as client:
        store = app.state.atelier_storage
        store.create_project(AtelierProject(
            id="p1", title="Canvas", created_at=1.0, updated_at=1.0,
            nodes=[AtelierNode(id="node-1", project_id="p1", type="video")],
        ))
        before = store.get_project("p1")
        payload = ordered_draft(expected_revision=0, mode="t2v", prompt="Still editing", params={"audio": False}).model_dump(mode="json")
        saved = client.put("/atelier/projects/p1/nodes/node-1/generation_draft", json=payload)
        assert saved.status_code == 200, saved.text
        wire = saved.json()
        assert wire["project"]["nodes"][0]["data"]["generation_draft"]["schema_version"] == 2
        assert wire["project"]["nodes"][0]["prompt"] == "Still editing"
        assert client.put("/atelier/projects/p1/nodes/node-1/generation_draft", json=payload).status_code == 409
        missing = {key: value for key, value in payload.items() if key != "contract_version"}
        assert client.put("/atelier/projects/p1/nodes/node-1/generation_draft", json=missing).status_code == 422
        after = store.get_project("p1")
        assert after.generation_attempts == before.generation_attempts
        assert after.generation_request_receipts == before.generation_request_receipts
