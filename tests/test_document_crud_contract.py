"""Document CRUD must use the current app storage owner and revision envelope."""

from __future__ import annotations

from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from src.apps.comic_gen import api, pipeline as module
from src.apps.comic_gen.models import Script
from src.utils.json_storage import StorageError


def _doc(label: str) -> dict:
    return {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": label}]}]}


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    for name in ("AssetGenerator", "StoryboardGenerator", "VideoGenerator", "AudioGenerator", "ExportManager"):
        monkeypatch.setattr(module, name, Mock())
    item = module.ComicGenPipeline(data_dir=tmp_path / "data", script_processor=Mock())
    item.scripts["p"] = Script(id="p", title="Project", original_text="stored", created_at=1, updated_at=1)
    item._commit_storage("projects.json")
    with TestClient(api.create_app(data_dir=item.data_dir, load_config=False, pipeline_instance=item)) as client:
        yield item, client
    item.close()


def test_document_get_default_and_save_envelope(runtime):
    item, client = runtime

    missing = client.get("/projects/p/document")
    assert missing.status_code == 200, missing.text
    assert missing.json() == {
        "project_id": "p",
        "revision": 0,
        "updated_at": None,
        "content": {"type": "doc", "content": []},
    }

    no_revision = client.post("/projects/p/document", json={"content": _doc("ignored")})
    assert no_revision.status_code == 422

    saved = client.post("/projects/p/document", json={"content": _doc("v1"), "expected_revision": 0})
    assert saved.status_code == 200, saved.text
    assert saved.json()["project_id"] == "p"
    assert saved.json()["revision"] == 1
    assert saved.json()["content"] == _doc("v1")
    assert (item.data_dir / "documents" / "p" / "document.json").is_file()

    loaded = client.get("/projects/p/document")
    assert loaded.json() == saved.json()


@pytest.mark.parametrize("bad_revision", [True, "0", -1])
def test_document_expected_revision_is_strict_non_negative_int(runtime, bad_revision):
    _, client = runtime
    response = client.post("/projects/p/document", json={"content": _doc("x"), "expected_revision": bad_revision})
    assert response.status_code == 422


def test_document_requires_existing_committed_project(runtime):
    _, client = runtime
    response = client.post("/projects/missing/document", json={"content": _doc("x"), "expected_revision": 0})
    assert response.status_code == 404


def test_document_rejects_stale_expected_revision(runtime):
    _, client = runtime
    assert client.post("/projects/p/document", json={"content": _doc("v1"), "expected_revision": 0}).status_code == 200
    stale = client.post("/projects/p/document", json={"content": _doc("v2"), "expected_revision": 0})
    assert stale.status_code == 409
    assert client.get("/projects/p/document").json()["content"] == _doc("v1")


def test_document_storage_is_isolated_across_app_data_dirs(tmp_path, monkeypatch):
    for name in ("AssetGenerator", "StoryboardGenerator", "VideoGenerator", "AudioGenerator", "ExportManager"):
        monkeypatch.setattr(module, name, Mock())
    a = module.ComicGenPipeline(data_dir=tmp_path / "a", script_processor=Mock())
    b = module.ComicGenPipeline(data_dir=tmp_path / "b", script_processor=Mock())
    for item in (a, b):
        item.scripts["p"] = Script(id="p", title="Project", original_text="stored", created_at=1, updated_at=1)
        item._commit_storage("projects.json")
    try:
        app_a = api.create_app(data_dir=a.data_dir, load_config=False, pipeline_instance=a)
        app_b = api.create_app(data_dir=b.data_dir, load_config=False, pipeline_instance=b)
        with TestClient(app_a) as client_a, TestClient(app_b) as client_b:
            assert client_a.post("/projects/p/document", json={"content": _doc("only-a"), "expected_revision": 0}).status_code == 200
            assert client_b.get("/projects/p/document").json()["revision"] == 0
            assert client_b.post("/projects/p/document", json={"content": _doc("only-b"), "expected_revision": 0}).json()["content"] == _doc("only-b")
            assert client_a.get("/projects/p/document").json()["content"] == _doc("only-a")
            assert client_b.get("/projects/p/document").json()["content"] == _doc("only-b")
    finally:
        a.close()
        b.close()


def test_document_snapshots_are_unique_and_restore_validates_before_replace(runtime, monkeypatch):
    item, client = runtime
    from src.apps.comic_gen import documents

    class FixedClock:
        calls = 0

        @classmethod
        def now(cls):
            cls.calls += 1
            return "2026-10-10T01:02:03.000000Z"

    monkeypatch.setattr(documents, "_utc_now_iso", FixedClock.now)
    assert client.post("/projects/p/document", json={"content": _doc("v1"), "expected_revision": 0}).status_code == 200
    first = client.post("/projects/p/document/snapshots").json()
    second = client.post("/projects/p/document/snapshots").json()
    assert first["snapshot_id"] != second["snapshot_id"]
    assert first["created_at"] == second["created_at"] == "2026-10-10T01:02:03.000000Z"
    assert ":" not in first["snapshot_id"]

    snapshot_path = item.data_dir / "documents" / "p" / "history" / f"{first['snapshot_id']}.json"
    snapshot_path.write_text("{", encoding="utf-8")
    response = client.post(f"/projects/p/document/snapshots/{first['snapshot_id']}/restore", json={"expected_revision": 1})
    assert response.status_code >= 500
    assert client.get("/projects/p/document").json()["content"] == _doc("v1")
    assert client.get("/projects/p/document").json()["revision"] == 1


def test_document_restore_requires_expected_revision(runtime):
    _, client = runtime
    assert client.post("/projects/p/document", json={"content": _doc("v1"), "expected_revision": 0}).status_code == 200
    snapshot = client.post("/projects/p/document/snapshots").json()
    assert client.post(f"/projects/p/document/snapshots/{snapshot['snapshot_id']}/restore", json={}).status_code == 422
    assert client.post(f"/projects/p/document/snapshots/{snapshot['snapshot_id']}/restore", json={"expected_revision": 0}).status_code == 409
    restored = client.post(f"/projects/p/document/snapshots/{snapshot['snapshot_id']}/restore", json={"expected_revision": 1})
    assert restored.status_code == 200, restored.text
    assert restored.json()["revision"] == 2


def test_document_rejects_corrupt_live_envelope_missing_updated_at(runtime):
    item, client = runtime
    path = item.data_dir / "documents" / "p" / "document.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        '{"schema_version":1,"project_id":"p","revision":1,"content":{"type":"doc","content":[]}}',
        encoding="utf-8",
    )

    response = client.get("/projects/p/document")

    assert response.status_code == 503
    assert response.json()["code"] == "storage_unavailable"


def test_document_rejects_bool_schema_version(runtime):
    item, client = runtime
    path = item.data_dir / "documents" / "p" / "document.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        '{"schema_version":true,"project_id":"p","revision":1,"updated_at":"2026-10-10T00:00:00Z","content":{"type":"doc","content":[]}}',
        encoding="utf-8",
    )

    response = client.get("/projects/p/document")

    assert response.status_code == 503
    assert response.json()["code"] == "storage_unavailable"


def test_document_rejects_snapshot_id_mismatch_on_list_and_restore(runtime):
    item, client = runtime
    assert client.post("/projects/p/document", json={"content": _doc("v1"), "expected_revision": 0}).status_code == 200
    snapshot = client.post("/projects/p/document/snapshots").json()
    snapshot_path = item.data_dir / "documents" / "p" / "history" / f"{snapshot['snapshot_id']}.json"
    text = snapshot_path.read_text(encoding="utf-8")
    snapshot_path.write_text(text.replace(snapshot["snapshot_id"], "wrong-snapshot-id", 1), encoding="utf-8")

    listed = client.get("/projects/p/document/snapshots")
    restored = client.post(f"/projects/p/document/snapshots/{snapshot['snapshot_id']}/restore", json={"expected_revision": 1})

    assert listed.status_code == 503
    assert restored.status_code == 503
    assert client.get("/projects/p/document").json()["revision"] == 1


def test_document_save_failure_preserves_old_file_and_closes_runtime(runtime, monkeypatch):
    item, client = runtime
    from src.apps.comic_gen import documents

    assert client.post("/projects/p/document", json={"content": _doc("v1"), "expected_revision": 0}).status_code == 200
    before = (item.data_dir / "documents" / "p" / "document.json").read_text(encoding="utf-8")

    def fail(*args, **kwargs):
        raise StorageError("synthetic save failure")

    monkeypatch.setattr(documents, "atomic_write_json", fail)
    failed = client.post("/projects/p/document", json={"content": _doc("v2"), "expected_revision": 1})
    assert failed.status_code == 503, failed.text
    assert (item.data_dir / "documents" / "p" / "document.json").read_text(encoding="utf-8") == before
    retry = client.post("/projects/p/document", json={"content": _doc("v3"), "expected_revision": 1})
    assert retry.status_code == 503


def test_document_commit_uncertain_is_reported_and_closes_runtime(runtime, monkeypatch):
    _, client = runtime
    from src.apps.comic_gen import documents

    def fail(*args, **kwargs):
        raise StorageError("directory fsync failed", commit_uncertain=True)

    monkeypatch.setattr(documents, "atomic_write_json", fail)
    failed = client.post("/projects/p/document", json={"content": _doc("v1"), "expected_revision": 0})
    assert failed.status_code == 503, failed.text
    assert failed.json()["commit_uncertain"] is True
    assert client.get("/projects/p/document").status_code == 503


def test_document_rejects_non_finite_client_content_without_snapshot_or_runtime_poison(runtime):
    item, client = runtime
    assert client.post("/projects/p/document", json={"content": _doc("v1"), "expected_revision": 0}).status_code == 200
    snapshot_before = client.post("/projects/p/document/snapshots").json()

    body = '{"content":{"type":"doc","bad":NaN},"expected_revision":1,"create_snapshot":true}'
    response = client.post("/projects/p/document", content=body, headers={"content-type": "application/json"})

    assert response.status_code == 422
    assert client.get("/projects/p/document").json()["content"] == _doc("v1")
    assert [snap["snapshot_id"] for snap in client.get("/projects/p/document/snapshots").json()] == [
        snapshot_before["snapshot_id"]
    ]
    assert client.post("/projects/p/document", json={"content": _doc("v2"), "expected_revision": 1}).status_code == 200
    assert (item.data_dir / "documents" / "p" / "document.json").exists()


@pytest.mark.parametrize("project_id", ["..", "../escape", "p%2Fescape", "bad:name"])
def test_document_rejects_unsafe_project_ids(tmp_path, project_id):
    from src.apps.comic_gen.documents import DocumentStore

    store = DocumentStore(data_dir=tmp_path, writer_lock=None, lock=None, runtime_check=lambda: None, project_exists=lambda _id: True)
    with pytest.raises(ValueError):
        store.load(project_id)


def test_document_rejects_symlink_storage(runtime):
    item, client = runtime
    docs = item.data_dir / "documents"
    docs.mkdir()
    (docs / "p").symlink_to(item.data_dir)
    response = client.get("/projects/p/document")
    assert response.status_code >= 500


def test_document_rejects_documents_root_symlink_escape(runtime, tmp_path):
    item, client = runtime
    outside = tmp_path / "outside-documents"
    outside.mkdir()
    (item.data_dir / "documents").symlink_to(outside)

    response = client.post("/projects/p/document", json={"content": _doc("escape"), "expected_revision": 0})

    assert response.status_code >= 500
    assert not (outside / "p" / "document.json").exists()
    assert client.get("/projects/p/document").status_code >= 500


def test_document_write_rejects_closed_runtime(runtime):
    item, client = runtime
    item.close()
    response = client.post("/projects/p/document", json={"content": _doc("v1"), "expected_revision": 0})
    assert response.status_code == 503
