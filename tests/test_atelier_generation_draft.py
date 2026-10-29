"""Strict Atelier generation draft updates stay separate from execution authority."""

from __future__ import annotations

from copy import deepcopy
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from src.apps.atelier.generation_draft import AtelierGenerationDraftService, GenerationDraftCommand
from src.apps.atelier.models import AtelierNode, AtelierProject
from src.apps.atelier.storage import AtelierStorage
from src.apps.comic_gen.api import create_app
from src.utils.json_storage import StorageConflictError, StorageError
from tests.test_atelier_generation_ledger_storage import _project as generated_project
from tests.test_atelier_generation_ledger_storage import _store_with_project

MODEL = "wan/wan2.7-video#i2v"


def _store(tmp_path, project: AtelierProject | None = None) -> AtelierStorage:
    return _store_with_project(tmp_path, project or generated_project(status="completed"))


def _draft(**changes) -> GenerationDraftCommand:
    data = {
        "expected_revision": 1,
        "prompt": "Draft prompt",
        "model_id": MODEL,
        "mode": "i2v",
        "params": {
            "duration": 5,
            "resolution": "1080p",
            "seed": 0,
            "negative_prompt": "",
            "prompt_extend": False,
            "watermark": False,
        },
    }
    data.update(changes)
    return GenerationDraftCommand(**data)


def _node(project: AtelierProject) -> AtelierNode:
    return project.nodes[0]


def test_generation_draft_saves_canonical_prompt_and_explicit_params_without_ledger_authority(tmp_path):
    store = _store(tmp_path)
    service = AtelierGenerationDraftService(store, clock=lambda: 90.0)
    before = store.get_project("p1")
    before_candidates = deepcopy(_node(before).data["candidates"])
    before_attempts = deepcopy(before.generation_attempts)
    before_receipts = deepcopy(before.generation_request_receipts)

    ack = service.update_generation_draft("p1", "node-1", _draft())

    node = _node(ack.project)
    assert ack.node_ids == ["node-1"]
    assert ack.project.revision == 2
    assert ack.project.updated_at == 90.0
    assert node.updated_at == 90.0
    assert node.prompt == "Draft prompt"
    assert node.data["generation_draft"] == {
        "schema_version": 1,
        "model_id": MODEL,
        "mode": "i2v",
        "params": {
            "duration": 5,
            "negative_prompt": "",
            "prompt_extend": False,
            "resolution": "1080p",
            "seed": 0,
            "watermark": False,
        },
    }
    assert node.data["candidates"] == before_candidates
    assert ack.project.generation_attempts == before_attempts
    assert ack.project.generation_request_receipts == before_receipts
    assert "fingerprint" not in node.data["generation_draft"]
    assert "sources" not in node.data["generation_draft"]


def test_generation_draft_replaces_params_and_omitted_params_reset_overrides(tmp_path):
    store = _store(tmp_path)
    service = AtelierGenerationDraftService(store)
    first = service.update_generation_draft("p1", "node-1", _draft(params={"seed": -1}, prompt="First"))

    second = service.update_generation_draft(
        "p1",
        "node-1",
        GenerationDraftCommand(expected_revision=first.project.revision, prompt="", model_id=MODEL, mode="i2v", params={}),
    )

    node = _node(second.project)
    assert node.prompt == ""
    assert node.data["generation_draft"] == {
        "schema_version": 1,
        "model_id": MODEL,
        "mode": "i2v",
        "params": {},
    }


def test_generation_draft_noop_and_restart_roundtrip(tmp_path):
    store = _store(tmp_path)
    service = AtelierGenerationDraftService(store)
    saved = service.update_generation_draft("p1", "node-1", _draft(params={"seed": -1}, prompt="Persist me"))
    same = service.update_generation_draft("p1", "node-1", _draft(expected_revision=saved.project.revision, params={"seed": -1}, prompt="Persist me"))
    assert same.project == saved.project
    store.close()

    reopened = AtelierStorage(tmp_path)
    try:
        project = reopened.get_project("p1")
        node = _node(project)
        assert node.prompt == "Persist me"
        assert node.data["generation_draft"]["params"] == {"seed": -1}
        assert project.revision == saved.project.revision
    finally:
        reopened.close()


@pytest.mark.parametrize(
    "payload",
    [
        {"expected_revision": 1},
        {"expected_revision": 1, "model_id": MODEL, "mode": "i2v", "params": {}},
        {"expected_revision": 1, "prompt": "", "mode": "i2v", "params": {}},
        {"expected_revision": 1, "prompt": "", "model_id": MODEL, "params": {}},
        {"expected_revision": 1, "prompt": "", "model_id": MODEL, "mode": "i2v"},
        {"expected_revision": 1, "prompt": None, "model_id": MODEL, "mode": "i2v", "params": {}},
        {"expected_revision": 1, "model_id": None},
        {"expected_revision": 1, "mode": None},
        {"expected_revision": 1, "params": None},
        {"expected_revision": 1, "model_id": "other"},
        {"expected_revision": 1, "mode": "t2v"},
        {"expected_revision": 1, "params": {"first_frame": "uploads/a.png"}},
        {"expected_revision": 1, "params": {"duration": "5"}},
        {"expected_revision": 1, "params": {"duration": 5.0}},
        {"expected_revision": 1, "params": {"duration": True}},
        {"expected_revision": 1, "params": {"seed": False}},
        {"expected_revision": 1, "params": {"seed": -2}},
        {"expected_revision": 1, "params": {"resolution": 1080}},
        {"expected_revision": 1, "params": {"negative_prompt": None}},
        {"expected_revision": 1, "params": {"prompt_extend": 0}},
        {"expected_revision": 1, "params": {"watermark": "false"}},
    ],
)
def test_generation_draft_rejects_null_unknown_or_wrong_scalar(payload):
    with pytest.raises(ValidationError):
        GenerationDraftCommand.model_validate(payload)


def test_generation_draft_rejects_stale_revision_and_non_video_node(tmp_path):
    project = AtelierProject(
        id="p1",
        title="Canvas",
        created_at=1.0,
        updated_at=1.0,
        revision=0,
        nodes=[AtelierNode(id="idea", project_id="p1", type="idea", created_at=1.0, updated_at=1.0)],
    )
    store = _store(tmp_path, project)
    service = AtelierGenerationDraftService(store)
    with pytest.raises(StorageConflictError):
        service.update_generation_draft("p1", "idea", GenerationDraftCommand(expected_revision=1, prompt="", model_id=MODEL, mode="i2v", params={}))
    with pytest.raises(ValueError, match="video"):
        service.update_generation_draft("p1", "idea", GenerationDraftCommand(expected_revision=0, prompt="", model_id=MODEL, mode="i2v", params={}))


def test_generation_draft_save_failure_rolls_back(tmp_path, monkeypatch):
    store = _store(tmp_path)
    service = AtelierGenerationDraftService(store)
    before_bytes = store.path.read_bytes()
    monkeypatch.setattr("src.apps.atelier.storage.atomic_write_json", Mock(side_effect=StorageError("disk full")))

    with pytest.raises(StorageError):
        service.update_generation_draft("p1", "node-1", _draft())

    assert store.path.read_bytes() == before_bytes
    assert "generation_draft" not in _node(store.get_project("p1")).data


def test_generation_draft_http_put_uses_real_app_and_preserves_ledgers(tmp_path):
    app = create_app(data_dir=tmp_path, load_config=False, recover_orphan_tasks=False)
    with TestClient(app) as http:
        app.state.atelier_storage.create_project(AtelierProject(
            id="p1",
            title="Canvas",
            created_at=1.0,
            updated_at=1.0,
            nodes=[AtelierNode(id="node-1", project_id="p1", type="video", created_at=1.0, updated_at=1.0)],
        ))
        before = app.state.atelier_storage.get_project("p1")
        payload = {
            "expected_revision": 0,
            "prompt": "HTTP draft",
            "model_id": MODEL,
            "mode": "i2v",
            "params": {"seed": 0, "prompt_extend": False, "watermark": False},
        }

        response = http.put("/atelier/projects/p1/nodes/node-1/generation_draft", json=payload)

        assert response.status_code == 200, response.text
        body = response.json()
        node = body["project"]["nodes"][0]
        assert body["node_ids"] == ["node-1"]
        assert body["project"]["revision"] == 1
        assert node["prompt"] == "HTTP draft"
        assert node["data"]["generation_draft"] == {
            "schema_version": 1,
            "model_id": MODEL,
            "mode": "i2v",
            "params": {"prompt_extend": False, "seed": 0, "watermark": False},
        }
        assert body["project"]["generation_attempts"] == [item.model_dump(mode="json") for item in before.generation_attempts]
        assert body["project"]["generation_request_receipts"] == [item.model_dump(mode="json") for item in before.generation_request_receipts]


def test_generation_draft_http_rejects_invalid_payload(tmp_path):
    app = create_app(data_dir=tmp_path, load_config=False, recover_orphan_tasks=False)
    with TestClient(app) as http:
        app.state.atelier_storage.create_project(AtelierProject(
            id="p1",
            title="Canvas",
            created_at=1.0,
            updated_at=1.0,
            nodes=[AtelierNode(id="node-1", project_id="p1", type="video", created_at=1.0, updated_at=1.0)],
        ))
        response = http.put(
            "/atelier/projects/p1/nodes/node-1/generation_draft",
            json={"expected_revision": 0, "prompt": "", "model_id": MODEL, "mode": "i2v", "params": {"duration": "5"}},
        )
        assert response.status_code == 422
