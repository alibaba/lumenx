"""Persisted edit revisions protect two clients sharing a Studio runtime."""

import json
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from threading import Event
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from src.apps.comic_gen import pipeline as module
from src.apps.comic_gen.api import create_app, get_pipeline
from src.apps.comic_gen.models import ArtDirection, Character, Script
from src.utils.json_storage import StorageConflictError, StorageError


@pytest.fixture
def make_pipeline(tmp_path, monkeypatch):
    for name in ("AssetGenerator", "StoryboardGenerator", "VideoGenerator", "AudioGenerator", "ExportManager"):
        monkeypatch.setattr(module, name, Mock())
    processor = Mock()
    processor.create_draft_script.side_effect = lambda title, text: Script(
        id=str(uuid.uuid4()), title=title, original_text=text, created_at=1, updated_at=1,
    )
    instances = []
    def make(root=None):
        item = module.ComicGenPipeline(data_dir=root or tmp_path / "data", script_processor=processor)
        instances.append(item)
        return item
    yield make
    for item in instances:
        item.close()


def _create(pipeline, kind):
    if kind == "project":
        return pipeline.create_project("project", "original", skip_analysis=True)
    return pipeline.create_series("series")


def _edit(pipeline, kind, item_id, text, expected):
    if kind == "project":
        return pipeline.update_script_text(item_id, text, expected_revision=expected)
    return pipeline.update_series(item_id, {"title": text}, expected_revision=expected)


def _get(pipeline, kind, item_id):
    return pipeline.get_script(item_id) if kind == "project" else pipeline.get_series(item_id)


def _value(kind, item):
    return item.original_text if kind == "project" else item.title


def _file(pipeline, kind):
    return pipeline.data_dir / ("projects.json" if kind == "project" else "series.json")


def _endpoint(kind, item_id):
    return f"/projects/{item_id}/text" if kind == "project" else f"/series/{item_id}"


@pytest.mark.parametrize("kind", ["project", "series"])
def test_stale_edit_is_a_conflict_without_poisoning_storage(make_pipeline, kind):
    pipeline = make_pipeline()
    item = _create(pipeline, kind)
    revision = item.revision
    updated = _edit(pipeline, kind, item.id, "first edit", revision)
    assert updated.revision == revision + 1
    before = _file(pipeline, kind).read_bytes()
    with pytest.raises(StorageConflictError):
        _edit(pipeline, kind, item.id, "stale edit", revision)
    assert _file(pipeline, kind).read_bytes() == before
    assert _value(kind, _get(pipeline, kind, item.id)) == "first edit"
    pipeline.assert_storage_available()
    assert _edit(pipeline, kind, item.id, "fresh edit", revision + 1).revision == revision + 2


@pytest.mark.parametrize("kind", ["project", "series"])
def test_http_requires_a_revision(make_pipeline, kind):
    pipeline = make_pipeline()
    item = _create(pipeline, kind)
    app = create_app(data_dir=pipeline.data_dir, load_config=False, pipeline_instance=pipeline)
    with TestClient(app) as client:
        response = client.put(_endpoint(kind, item.id), json={"text" if kind == "project" else "title": "edit"})
        assert response.status_code == 422


@pytest.mark.parametrize("kind", ["project", "series"])
@pytest.mark.parametrize("revision", [-1, True, "0", 0.0, None])
def test_http_revision_is_a_strict_nonnegative_integer(make_pipeline, kind, revision):
    pipeline = make_pipeline()
    item = _create(pipeline, kind)
    before = _file(pipeline, kind).read_bytes()
    app = create_app(data_dir=pipeline.data_dir, load_config=False, pipeline_instance=pipeline)
    with TestClient(app) as client:
        response = client.put(_endpoint(kind, item.id), json={
            "text" if kind == "project" else "title": "edit", "expected_revision": revision,
        })
        assert response.status_code == 422
    assert _file(pipeline, kind).read_bytes() == before


@pytest.mark.parametrize("kind", ["project", "series"])
def test_http_round_trip_returns_revision_and_rejects_second_tab(make_pipeline, kind):
    pipeline = make_pipeline()
    item = _create(pipeline, kind)
    revision = item.revision
    app = create_app(data_dir=pipeline.data_dir, load_config=False, pipeline_instance=pipeline)
    with TestClient(app) as client:
        payload = {"text" if kind == "project" else "title": "new", "expected_revision": revision}
        response = client.put(_endpoint(kind, item.id), json=payload)
        assert response.status_code == 200, response.text
        assert response.json()["revision"] == revision + 1
        before = _file(pipeline, kind).read_bytes()
        stale = client.put(_endpoint(kind, item.id), json=payload)
        assert stale.status_code == 409, stale.text
        assert stale.json()["code"] == "storage_conflict"
        assert _file(pipeline, kind).read_bytes() == before
    restored = make_pipeline()
    assert _get(restored, kind, item.id).revision == revision + 1
    assert _value(kind, _get(restored, kind, item.id)) == "new"


@pytest.mark.parametrize("kind", ["project", "series"])
def test_revision_only_changes_for_modified_entity(make_pipeline, kind):
    pipeline = make_pipeline()
    first = _create(pipeline, kind)
    other = _create(pipeline, kind)
    first_revision, other_revision = first.revision, other.revision
    updated = _edit(pipeline, kind, first.id, "edited", first_revision)
    assert updated.revision == first_revision + 1
    assert _get(pipeline, kind, other.id).revision == other_revision
    _edit(pipeline, kind, first.id, "edited", updated.revision)
    assert updated.revision == first_revision + 1
    if kind == "project":
        pipeline._save_data()
    else:
        pipeline._save_series_data()
    assert updated.revision == first_revision + 1
    assert _get(pipeline, kind, other.id).revision == other_revision


@pytest.mark.parametrize("kind", ["project", "series"])
def test_existing_mutation_paths_also_advance_revisions(make_pipeline, kind):
    pipeline = make_pipeline()
    item = _create(pipeline, kind)
    revision = item.revision
    item = _get(pipeline, kind, item.id)
    # Existing commands still mutate the model before entering the save gate.
    # Versioning must detect their content changes even without a new timestamp.
    item.title = "changed by an existing command"
    if kind == "project":
        pipeline._save_data()
    else:
        pipeline._save_series_data()
    assert item.revision == revision + 1
    with pytest.raises(StorageConflictError):
        _edit(pipeline, kind, item.id, "old page", revision)
    pipeline.assert_storage_available()


@pytest.mark.parametrize("kind", ["project", "series"])
def test_revision_is_published_after_successful_commit(make_pipeline, monkeypatch, kind):
    pipeline = make_pipeline()
    item = _create(pipeline, kind)
    old = item.revision
    original = pipeline._storage.commit
    def observe(payloads):
        assert item.revision == old
        assert payloads[_file(pipeline, kind).name][item.id]["revision"] == old + 1
        original(payloads)
    monkeypatch.setattr(pipeline._storage, "commit", observe)
    assert _edit(pipeline, kind, item.id, "new", old).revision == old + 1


@pytest.mark.parametrize("kind", ["project", "series"])
def test_failed_save_preserves_old_revision_and_content(make_pipeline, monkeypatch, kind):
    pipeline = make_pipeline()
    item = _create(pipeline, kind)
    revision, value = item.revision, _value(kind, item)
    before = _file(pipeline, kind).read_bytes()
    monkeypatch.setattr(pipeline._storage, "commit", Mock(side_effect=StorageError("disk full")))
    with pytest.raises(StorageError):
        _edit(pipeline, kind, item.id, "unsaved", revision)
    records = pipeline.scripts if kind == "project" else pipeline.series_store
    assert records[item.id].revision == revision
    assert _value(kind, records[item.id]) == value
    assert _file(pipeline, kind).read_bytes() == before


@pytest.mark.parametrize("kind", ["project", "series"])
def test_legacy_schema_starts_at_zero_and_can_be_edited(make_pipeline, tmp_path, kind):
    root = tmp_path / "legacy"
    root.mkdir()
    data = {"id": "old", "title": "legacy", "created_at": 1, "updated_at": 1}
    if kind == "project":
        data["original_text"] = "original"
    name = "projects.json" if kind == "project" else "series.json"
    (root / name).write_text(json.dumps({"old": data}))
    pipeline = make_pipeline(root)
    assert _get(pipeline, kind, "old").revision == 0
    assert _edit(pipeline, kind, "old", "edited", 0).revision == 1
    assert json.loads((root / name).read_text())["old"]["revision"] == 1


@pytest.mark.parametrize("kind", ["project", "series"])
def test_two_concurrent_edits_of_same_revision_have_one_winner(make_pipeline, kind):
    pipeline = make_pipeline()
    item = _create(pipeline, kind)
    revision = item.revision
    def edit(value):
        try:
            _edit(pipeline, kind, item.id, value, revision)
            return "saved"
        except StorageConflictError:
            return "conflict"
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(edit, ["first", "second"]))
    assert sorted(outcomes) == ["conflict", "saved"]
    assert _get(pipeline, kind, item.id).revision == revision + 1
    pipeline.assert_storage_available()


def test_series_patch_cannot_set_its_revision(make_pipeline):
    pipeline = make_pipeline()
    item = pipeline.create_series("series")
    revision = item.revision
    result = pipeline.update_series(item.id, {"revision": 999}, expected_revision=revision)
    assert result.revision == revision
    assert pipeline.update_series(item.id, {"description": "internal caller"}).revision == revision + 1


@pytest.mark.parametrize("field", ["title", "description", "workflow_mode", "content_mode", "default_generation_mode"])
def test_http_series_rejects_null_for_required_fields(make_pipeline, field):
    pipeline = make_pipeline()
    series = pipeline.create_series("original")
    before = _file(pipeline, "series").read_bytes()
    app = create_app(data_dir=pipeline.data_dir, load_config=False, pipeline_instance=pipeline)
    with TestClient(app) as client:
        response = client.put(f"/series/{series.id}", json={
            field: None, "expected_revision": series.revision,
        })
        assert response.status_code == 422, response.text
        assert _file(pipeline, "series").read_bytes() == before
        pipeline.assert_storage_available()


def test_http_series_can_clear_art_direction_with_null(make_pipeline):
    pipeline = make_pipeline()
    series = pipeline.create_series("original")
    series = pipeline.update_series(series.id, {"art_direction": ArtDirection(selected_style_id="test", style_config={})})
    revision = series.revision
    app = create_app(data_dir=pipeline.data_dir, load_config=False, pipeline_instance=pipeline)
    with TestClient(app) as client:
        response = client.put(f"/series/{series.id}", json={
            "art_direction": None, "expected_revision": revision,
        })
        assert response.status_code == 200, response.text
        assert response.json()["art_direction"] is None
        assert response.json()["revision"] == revision + 1
    reopened = make_pipeline()
    assert reopened.get_series(series.id).art_direction is None


@pytest.mark.parametrize("kind", ["project", "series", "library"])
def test_invalid_internal_mutation_never_reaches_disk(make_pipeline, monkeypatch, kind):
    pipeline = make_pipeline()
    if kind == "library":
        item = Character(id="char", name="original", description="description")
        pipeline.library_store.characters.append(item)
        name = "library_assets.json"
    else:
        item = _create(pipeline, kind)
        item = _get(pipeline, kind, item.id)
        name = _file(pipeline, kind).name
    pipeline._commit_storage(name)
    before = (pipeline.data_dir / name).read_bytes()
    snapshot = pipeline._committed[name]
    setattr(item, "name" if kind == "library" else "title", None)
    commit = Mock(wraps=pipeline._storage.commit)
    monkeypatch.setattr(pipeline._storage, "commit", commit)
    with pytest.raises(StorageError):
        pipeline._commit_storage(name)
    commit.assert_not_called()
    assert (pipeline.data_dir / name).read_bytes() == before
    assert pipeline._storage_payloads((name,))[name] == snapshot
    with pytest.raises(StorageError, match="restart"):
        pipeline.assert_storage_available()
    pipeline.close()
    reopened = make_pipeline()
    assert reopened._storage_payloads((name,))[name] == snapshot


@pytest.mark.parametrize("kind", ["project", "series"])
def test_invalid_internal_identity_never_reaches_disk(make_pipeline, monkeypatch, kind):
    pipeline = make_pipeline()
    item = _create(pipeline, kind)
    path = _file(pipeline, kind)
    before = path.read_bytes()
    old_id = item.id
    item = _get(pipeline, kind, item.id)
    item.id = "wrong-map-key"
    commit = Mock(wraps=pipeline._storage.commit)
    monkeypatch.setattr(pipeline._storage, "commit", commit)
    with pytest.raises(StorageError):
        pipeline._commit_storage(path.name)
    commit.assert_not_called()
    assert path.read_bytes() == before
    records = pipeline.scripts if kind == "project" else pipeline.series_store
    assert records[old_id].id == old_id


@pytest.mark.parametrize("field,value", [
    ("prompt_config", {"storyboard_polish": "custom prompt"}),
    ("model_settings", {"t2i_model": "custom-model"}),
])
def test_valid_nested_dict_updates_remain_reloadable(make_pipeline, field, value):
    pipeline = make_pipeline()
    series = pipeline.create_series("series")
    revision = series.revision
    pipeline.update_series(series.id, {field: value}, expected_revision=revision)
    pipeline.close()
    restored = make_pipeline().get_series(series.id)
    assert restored.revision == revision + 1
    for key, expected in value.items():
        assert getattr(getattr(restored, field), key) == expected


@pytest.mark.parametrize("kind", ["project", "series"])
def test_edit_result_is_a_snapshot_even_if_another_edit_finishes_before_serialization(make_pipeline, kind):
    pipeline = make_pipeline()
    item = _create(pipeline, kind)
    revision = item.revision
    captured, serialize = Event(), Event()

    def first_client():
        result = _edit(pipeline, kind, item.id, "first edit", revision)
        captured.set()
        assert serialize.wait(5)
        return _value(kind, result), result.revision

    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(first_client)
        try:
            assert captured.wait(5)
            _edit(pipeline, kind, item.id, "second edit", revision + 1)
        finally:
            serialize.set()
        assert first.result(timeout=5) == ("first edit", revision + 1)


@pytest.mark.parametrize("kind", ["project", "series"])
@pytest.mark.parametrize("listing", [False, True])
def test_public_reads_do_not_publish_content_from_a_pending_commit(make_pipeline, monkeypatch, kind, listing):
    pipeline = make_pipeline()
    item = _create(pipeline, kind)
    revision, original_value = item.revision, _value(kind, item)
    entered, release, reading = Event(), Event(), Event()
    original_commit = pipeline._storage.commit

    def pause_commit(payloads):
        entered.set()
        assert release.wait(5)
        original_commit(payloads)

    monkeypatch.setattr(pipeline._storage, "commit", pause_commit)
    app = create_app(data_dir=pipeline.data_dir, load_config=False, pipeline_instance=pipeline)
    def observe_read():
        reading.set()
        return pipeline
    app.dependency_overrides[get_pipeline] = observe_read
    endpoint = ("/projects/" if kind == "project" else "/series") if listing else (
        f"/projects/{item.id}" if kind == "project" else f"/series/{item.id}"
    )
    with TestClient(app) as client, ThreadPoolExecutor(max_workers=2) as pool:
        editor = pool.submit(_edit, pipeline, kind, item.id, "pending edit", revision)
        try:
            assert entered.wait(5)
            reader = pool.submit(client.get, endpoint)
            assert reading.wait(5)
            try:
                reader.result(timeout=0.1)
            except TimeoutError:
                pass  # A read may wait for the commit's decision.
        finally:
            release.set()
        editor.result(timeout=5)
        response = reader.result(timeout=5)
        assert response.status_code == 200
        value = response.json()[0] if listing else response.json()
        pair = (value["original_text" if kind == "project" else "title"], value["revision"])
        assert pair in [(original_value, revision), ("pending edit", revision + 1)]


def test_public_views_use_committed_project_series_and_library_snapshots(make_pipeline):
    pipeline = make_pipeline()
    series = pipeline.create_series("series")
    project = pipeline.create_project("episode", "saved text", skip_analysis=True)
    pipeline.add_episode_to_series(series.id, project.id, episode_number=1)
    series = pipeline.series_store[series.id]
    project = pipeline.scripts[project.id]
    series.characters.append(Character(id="shared", name="saved shared", description=""))
    pipeline.library_store.characters.append(Character(id="global", name="saved global", description=""))
    pipeline._commit_storage("series.json", "library_assets.json")
    # Inject unsaved changes into the actual runtime aggregates. Detached
    # command responses must not make this committed-view test vacuous.
    project.title = "unsaved episode"
    project.original_text = "unsaved text"
    series.title = "unsaved series"
    series.characters[0].name = "unsaved shared"
    pipeline.library_store.characters[0].name = "unsaved global"
    app = create_app(data_dir=pipeline.data_dir, load_config=False, pipeline_instance=pipeline)
    with TestClient(app) as client:
        response = client.get(f"/projects/{project.id}").json()
        assert response["title"] == "episode"
        assert response["original_text"] == "saved text"
        assert [(asset["name"], asset["source"]) for asset in response["characters"]] == [
            ("saved shared", "series"), ("saved global", "global"),
        ]
        assert client.get("/projects/").json()[0]["title"] == "episode"
        assert client.get("/series").json()[0]["title"] == "series"
        series_response = client.get(f"/series/{series.id}").json()
        assert series_response["title"] == "series"
        assert series_response["episodes"][0]["title"] == "episode"
        assert client.get(f"/series/{series.id}/episodes").json()[0]["title"] == "episode"
