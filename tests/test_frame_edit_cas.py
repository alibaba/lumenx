"""A stale editor must not replace a newer committed frame patch."""

from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from src.apps.comic_gen.api import create_app
from tests import test_frame_visual_edits

runtime = test_frame_visual_edits.runtime


def app_for(runtime):
    return create_app(data_dir=runtime.data_dir, load_config=False, pipeline_instance=runtime)


@pytest.mark.parametrize("first,stale", [
    ({"visual_description": "newer visual"}, {"visual_description": "old-window visual"}),
    ({"dialogue": "newer words"}, {"dialogue": "old-window words"}),
    ({"duration": 8}, {"duration": 5}),
])
def test_old_revision_rejected_without_mutating_committed_frame(runtime, first, stale):
    with TestClient(app_for(runtime)) as client:
        base = client.get("/projects/p").json()["revision"]
        response = client.post("/projects/p/frames/update", json={"frame_id": "f", "expected_revision": base, **first})
        assert response.status_code == 200, response.text
        assert response.json()["revision"] == base + 1
        saved = runtime.read_committed_script("p").model_dump(mode="json")
        disk = (runtime.data_dir / "projects.json").read_bytes()
        conflict = client.post("/projects/p/frames/update", json={"frame_id": "f", "expected_revision": base, **stale})
        assert conflict.status_code == 409, conflict.text
        assert conflict.json()["code"] == "storage_conflict"
        assert runtime.read_committed_script("p").model_dump(mode="json") == saved
        assert (runtime.data_dir / "projects.json").read_bytes() == disk
        runtime.assert_storage_available()


@pytest.mark.parametrize("revision", [None, True, False, "0", 0.0, -1])
def test_invalid_revision_is_rejected_before_any_patch(runtime, revision):
    before = (runtime.data_dir / "projects.json").read_bytes()
    with TestClient(app_for(runtime)) as client:
        response = client.post("/projects/p/frames/update", json={
            "frame_id": "f", "expected_revision": revision, "visual_description": "not saved",
        })
        assert response.status_code == 422, response.text
        assert (runtime.data_dir / "projects.json").read_bytes() == before


def test_missing_revision_cannot_bypass_conflict_protection(runtime):
    before = (runtime.data_dir / "projects.json").read_bytes()
    with TestClient(app_for(runtime)) as client:
        response = client.post("/projects/p/frames/update", json={"frame_id": "f", "dialogue": "not saved"})
        assert response.status_code == 422, response.text
        assert (runtime.data_dir / "projects.json").read_bytes() == before


def test_same_revision_concurrent_writers_have_one_committed_winner(runtime):
    with TestClient(app_for(runtime)) as client:
        revision = client.get("/projects/p").json()["revision"]
        def save(text):
            return client.post("/projects/p/frames/update", json={
                "frame_id": "f", "expected_revision": revision, "visual_description": text,
            })
        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(save, ["writer A", "writer B"]))
        assert sorted(result.status_code for result in results) == [200, 409]
        winner = next(result for result in results if result.status_code == 200).json()
        assert runtime.read_committed_script("p").model_dump(mode="json") == winner
        assert winner["revision"] == revision + 1


def test_stale_noop_is_rejected_before_prompt_assembly(runtime, monkeypatch):
    with TestClient(app_for(runtime)) as client:
        base = runtime.read_committed_script("p").revision
        assert client.post("/projects/p/frames/update", json={
            "frame_id": "f", "expected_revision": base, "visual_description": "new visual",
        }).status_code == 200
        from src.apps.comic_gen import prompt_assembly
        assemble = Mock(side_effect=AssertionError("stale command reached assembly"))
        monkeypatch.setattr(prompt_assembly, "assemble_prompt", assemble)
        response = client.post("/projects/p/frames/update", json={
            "frame_id": "f", "expected_revision": base, "visual_description": "new visual",
        })
        assert response.status_code == 409, response.text
        assemble.assert_not_called()
