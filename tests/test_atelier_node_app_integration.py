"""Exercise node commands through the production app's access and storage boundary."""

import json

from fastapi.testclient import TestClient

from src.apps.comic_gen.api import create_app


def _app(root, **kwargs):
    return create_app(
        data_dir=root, load_config=False, recover_orphan_tasks=False, **kwargs
    )


def test_node_commands_are_isolated_persisted_and_read_back_after_restart(tmp_path):
    root_a, root_b = tmp_path / "a", tmp_path / "b"
    with TestClient(_app(root_a)) as a, TestClient(_app(root_b)) as b:
        project = a.post("/atelier/projects", json={"title": "A"}).json()
        path = f"/atelier/projects/{project['id']}"
        result = a.post(
            path + "/nodes",
            json={"expected_revision": 0, "type": "idea", "prompt": "Opening"},
        )
        assert result.status_code == 200, result.text
        node_id = result.json()["node_ids"][0]
        result = a.put(
            path + f"/nodes/{node_id}/layout",
            json={"expected_revision": 1, "x": 17},
        )
        assert result.status_code == 200, result.text
        saved = result.json()["project"]
        assert saved["revision"] == 2
        assert saved["nodes"][0]["x"] == 17
        assert saved["nodes"][0]["data"] == {"body": "Opening"}
        assert a.get(path).json() == saved
        assert b.get(path).status_code == 404
        assert b.get("/atelier/projects").json() == []
        assert a.get("/projects/").json() == []
    with TestClient(_app(root_a)) as reopened:
        assert reopened.get(path).json() == saved


def test_node_commands_use_real_access_identity_and_nonfinite_validation(tmp_path):
    token = "synthetic-node-test-token-" * 3
    with TestClient(_app(tmp_path, access_mode="remote", access_token=token)) as client:
        auth = {"Authorization": "Bearer " + token}
        identity = client.get("/runtime/environment", headers=auth).json()["storage_id"]
        headers = {**auth, "X-LumenX-Environment": identity}
        project = client.post("/atelier/projects", json={"title": "A"}, headers=headers).json()
        path = f"/atelier/projects/{project['id']}/nodes"
        database = tmp_path / "atelier_projects.json"
        original = database.read_bytes()
        assert client.post(path, json={"expected_revision": 0}).status_code == 401
        wrong = {**auth, "X-LumenX-Environment": "11111111-1111-4111-8111-111111111111"}
        assert client.post(path, json={"expected_revision": 0}, headers=wrong).status_code == 409
        for numeric in ("NaN", "Infinity", "-Infinity", "1" + "0" * 400):
            response = client.post(
                path,
                content='{"expected_revision":0,"x":' + numeric + "}",
                headers={**headers, "Content-Type": "application/json"},
            )
            assert response.status_code == 422, response.text
            json.loads(response.content)
            assert "NaN" not in response.text
            assert "Infinity" not in response.text
        assert database.read_bytes() == original
        valid = client.post(path, json={"expected_revision": 0, "type": "video"}, headers=headers)
        assert valid.status_code == 200, valid.text
        assert valid.json()["project"]["revision"] == 1
