"""A reparse confirmation must retain the revision its author actually read.

Real HTTP, command locks and temporary JSON storage; only novel extraction is
synthetic. Run through the approved isolated test wrapper.
"""

from copy import deepcopy
from threading import Thread
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from src.apps.comic_gen import api, pipeline as module
from src.apps.comic_gen.models import Character, Script, StoryboardFrame
from src.utils.json_storage import StorageError


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    for name in ("AssetGenerator", "StoryboardGenerator", "VideoGenerator", "AudioGenerator", "ExportManager"):
        monkeypatch.setattr(module, name, Mock())
    processor = Mock()
    processor.parse_novel.side_effect = lambda _title, text, *_args: Script(
        id="parsed", title="Parsed", original_text=text, created_at=2, updated_at=2,
        characters=[Character(id="replacement", name="Parsed character", description=text)],
    )
    item = module.ComicGenPipeline(data_dir=tmp_path / "data", script_processor=processor)
    item.scripts["p"] = Script(
        id="p", title="Project", original_text="Saved text", created_at=1, updated_at=1,
        characters=[Character(id="original", name="Original", description="Retain on conflict")],
        frames=[StoryboardFrame(id="frame", scene_id="", action_description="Retain on conflict")],
    )
    item._save_data()
    yield item
    item.close()


def client_for(runtime):
    return TestClient(api.create_app(data_dir=runtime.data_dir, load_config=False, pipeline_instance=runtime))


def disk(runtime):
    return (runtime.data_dir / "projects.json").read_bytes()


@pytest.mark.parametrize("cached", [False, True])
def test_stale_tab_reparse_rejects_before_llm_or_cache_consumption(runtime, cached):
    with client_for(runtime) as client:
        original = client.get("/projects/p").json()
        if cached:
            assert client.post("/projects/p/extract_preview", json={"text": "Old tab draft"}).status_code == 200
        saved = client.put("/projects/p/text", json={
            "text": "Other tab saved this", "expected_revision": original["revision"],
        })
        assert saved.status_code == 200
        before = runtime.read_committed_script("p").model_dump()
        before_disk = disk(runtime)
        before_cache = deepcopy(runtime._extraction_cache)
        parser_calls = runtime.script_processor.parse_novel.call_count

        response = client.put("/projects/p/reparse", json={
            "text": "Old tab draft", "expected_revision": original["revision"],
        })

        assert response.status_code == 409, response.text
        assert response.json()["code"] == "storage_conflict"
        assert response.json()["commit_uncertain"] is False
        assert runtime.script_processor.parse_novel.call_count == parser_calls
        assert runtime._extraction_cache == before_cache
        assert runtime.read_committed_script("p").model_dump() == before
        assert disk(runtime) == before_disk
        runtime.assert_storage_available()


@pytest.mark.parametrize("revision", ["missing", None, True, "0", 0.0, -1])
def test_reparse_requires_strict_nonnegative_revision(runtime, revision):
    payload = {"text": "Draft"}
    if revision != "missing":
        payload["expected_revision"] = revision
    before = disk(runtime)
    with client_for(runtime) as client:
        response = client.put("/projects/p/reparse", json=payload)
        assert response.status_code == 422, response.text
    runtime.script_processor.parse_novel.assert_not_called()
    assert not runtime._extraction_cache
    assert disk(runtime) == before


def test_current_reparse_uses_preview_once_and_stale_replay_does_not_parse(runtime):
    revision = runtime.read_committed_script("p").revision
    with client_for(runtime) as client:
        assert client.post("/projects/p/extract_preview", json={"text": "Confirmed draft"}).status_code == 200
        response = client.put("/projects/p/reparse", json={
            "text": "Confirmed draft", "expected_revision": revision,
        })
        assert response.status_code == 200, response.text
        assert response.json()["revision"] == revision + 1
        assert response.json()["id"] == "p"
        assert response.json()["original_text"] == "Confirmed draft"
        assert response.json()["characters"][0]["id"] == "replacement"
        assert response.json()["frames"] == []
        runtime.script_processor.parse_novel.assert_called_once()
        before = disk(runtime)
        replay = client.put("/projects/p/reparse", json={
            "text": "Confirmed draft", "expected_revision": revision,
        })
        assert replay.status_code == 409, replay.text
        runtime.script_processor.parse_novel.assert_called_once()
        assert disk(runtime) == before
    runtime.close()
    restored = module.ComicGenPipeline(data_dir=runtime.data_dir, script_processor=Mock())
    try:
        assert restored.read_committed_script("p").original_text == "Confirmed draft"
        assert restored.read_committed_script("p").revision == revision + 1
    finally:
        restored.close()


@pytest.mark.parametrize("change,expected_status", [("edit", 409), ("delete", 404), ("close", 503)])
def test_reparse_still_rechecks_after_unlocked_llm(runtime, change, expected_status):
    revision = runtime.read_committed_script("p").revision

    def parse(_title, text, *_args):
        errors = []

        def mutate():
            try:
                if change == "edit":
                    runtime.update_script_text("p", "Changed during parse", expected_revision=revision)
                elif change == "delete":
                    runtime.delete_project("p")
                else:
                    runtime.close()
            except BaseException as exc:
                errors.append(exc)

        thread = Thread(target=mutate, daemon=True)
        thread.start()
        thread.join(timeout=3)
        assert not thread.is_alive(), "The LLM must not hold the storage lock"
        assert not errors
        return Script(id="parsed", title="Parsed", original_text=text, created_at=2, updated_at=2)

    runtime.script_processor.parse_novel.side_effect = parse
    with client_for(runtime) as client:
        response = client.put("/projects/p/reparse", json={"text": "Draft", "expected_revision": revision})
        assert response.status_code == expected_status, response.text
        runtime.script_processor.parse_novel.assert_called_once()
        assert not runtime._extraction_cache
        if change == "edit":
            assert runtime.read_committed_script("p").original_text == "Changed during parse"


@pytest.mark.parametrize("failure,status", [("parse", 502), ("storage", 503)])
def test_reparse_keeps_explicit_failure_and_restores_saved_project(runtime, monkeypatch, failure, status):
    original = runtime.read_committed_script("p").model_dump()
    before = disk(runtime)
    if failure == "parse":
        runtime.script_processor.parse_novel.side_effect = RuntimeError("Synthetic parse failure")
    else:
        monkeypatch.setattr(runtime._storage, "commit", Mock(side_effect=StorageError("Synthetic disk full")))
    with client_for(runtime) as client:
        response = client.put("/projects/p/reparse", json={
            "text": "Draft", "expected_revision": original["revision"],
        })
        assert response.status_code == status, response.text
    assert disk(runtime) == before
    assert runtime.scripts["p"].model_dump() == original


def test_preview_remains_text_only_and_does_not_write(runtime):
    before = disk(runtime)
    with client_for(runtime) as client:
        response = client.post("/projects/p/extract_preview", json={"text": "Preview text"})
        assert response.status_code == 200, response.text
        assert set(response.json()) == {"characters", "scenes", "props"}
        assert disk(runtime) == before
        assert runtime.read_committed_script("p").original_text == "Saved text"
