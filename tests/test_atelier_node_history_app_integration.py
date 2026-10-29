"""Delete receipts cross the production access, lifecycle and storage boundary."""

from fastapi.testclient import TestClient

from src.apps.atelier import storage as atelier_storage
from src.apps.comic_gen.api import create_app
from src.utils.json_storage import StorageError


def _app(root, **kwargs):
    return create_app(data_dir=root, load_config=False, recover_orphan_tasks=False, **kwargs)


def _seed(client, headers=None):
    project = client.post("/atelier/projects", json={"title": "Undo"}, headers=headers).json()
    path = f"/atelier/projects/{project['id']}"
    result = client.post(path + "/nodes", json={"expected_revision": 0, "type": "idea", "body": "Keep this"}, headers=headers)
    assert result.status_code == 200, result.text
    return path, result.json()["node_ids"][0], result.json()["project"]


def test_delete_restore_auth_identity_restart_and_no_media_cleanup(tmp_path):
    token = "synthetic-node-history-test-token-" * 3
    auth = {"Authorization": "Bearer " + token}
    root_a, root_b = tmp_path / "a", tmp_path / "b"
    with TestClient(_app(root_a, access_mode="remote", access_token=token)) as a, TestClient(_app(root_b)) as b:
        identity = a.get("/runtime/environment", headers=auth).json()["storage_id"]
        headers = {**auth, "X-LumenX-Environment": identity}
        path, node_id, original = _seed(a, headers)
        media = root_a / "uploads" / "paid-existing.bin"
        media.parent.mkdir(parents=True, exist_ok=True)
        media.write_bytes(b"existing-media-must-remain")
        database = root_a / "atelier_projects.json"
        before = database.read_bytes()
        payload = {"expected_revision": 1, "node_ids": [node_id]}
        assert a.post(path + "/nodes/delete", json=payload).status_code == 401
        wrong = {**auth, "X-LumenX-Environment": "11111111-1111-4111-8111-111111111111"}
        assert a.post(path + "/nodes/delete", json=payload, headers=wrong).status_code == 409
        assert database.read_bytes() == before
        result = a.post(path + "/nodes/delete", json=payload, headers=headers)
        assert result.status_code == 200, result.text
        receipt_id = result.json()["receipt_id"]
        deleted = result.json()["project"]
        assert deleted["revision"] == 2
        assert deleted["nodes"] == []
        assert a.get(path, headers=headers).json() == deleted
        assert b.get("/atelier/projects").json() == []
        assert a.get("/projects/", headers=headers).json() == []
        assert media.read_bytes() == b"existing-media-must-remain"
    with TestClient(_app(root_a, access_mode="remote", access_token=token)) as reopened:
        assert reopened.get(path, headers=headers).json() == deleted
        body = {"expected_revision": 2, "receipt_id": receipt_id}
        assert reopened.post(path + "/nodes/restore", json=body).status_code == 401
        restored = reopened.post(path + "/nodes/restore", json=body, headers=headers)
        assert restored.status_code == 200, restored.text
        saved = restored.json()["project"]
        assert saved["nodes"] == original["nodes"]
        assert saved["revision"] == 3
        assert reopened.get(path, headers=headers).json() == saved
        assert media.exists()


def test_definite_receipt_write_failure_publishes_no_delete(tmp_path, monkeypatch):
    with TestClient(_app(tmp_path)) as client:
        path, node_id, original = _seed(client)
        before = (tmp_path / "atelier_projects.json").read_bytes()

        def fail(*_args, **_kwargs):
            raise StorageError("injected definite write failure")

        with monkeypatch.context() as patch:
            patch.setattr(atelier_storage, "atomic_write_json", fail)
            result = client.post(path + "/nodes/delete", json={"expected_revision": 1, "node_ids": [node_id]})
        assert result.status_code == 503, result.text
        assert (tmp_path / "atelier_projects.json").read_bytes() == before
        assert client.get(path).status_code == 503
    # The production app latches any storage fault, including a definite failure.
    # Reopen to verify that the previously committed project remains authoritative.
    with TestClient(_app(tmp_path)) as reopened:
        assert reopened.get(path).json() == original
        assert original["node_delete_receipts"] == []


def test_uncertain_delete_receipt_can_be_read_and_restored_after_restart(tmp_path, monkeypatch):
    write = atelier_storage.atomic_write_json
    with TestClient(_app(tmp_path)) as client:
        path, node_id, original = _seed(client)

        def write_then_lose_ack(*args, **kwargs):
            write(*args, **kwargs)
            raise StorageError("injected uncertain write", commit_uncertain=True)

        with monkeypatch.context() as patch:
            patch.setattr(atelier_storage, "atomic_write_json", write_then_lose_ack)
            result = client.post(path + "/nodes/delete", json={"expected_revision": 1, "node_ids": [node_id]})
        assert result.status_code == 503, result.text
        assert client.get(path).status_code == 503
        assert client.post(path + "/nodes/delete", json={"expected_revision": 1, "node_ids": [node_id]}).status_code == 503
    with TestClient(_app(tmp_path)) as reopened:
        saved = reopened.get(path).json()
        assert saved["revision"] == 2 and saved["nodes"] == []
        assert len(saved["node_delete_receipts"]) == 1
        receipt = saved["node_delete_receipts"][0]
        restored = reopened.post(path + "/nodes/restore", json={"expected_revision": 2, "receipt_id": receipt["receipt_id"]})
        assert restored.status_code == 200, restored.text
        assert restored.json()["project"]["nodes"] == original["nodes"]
