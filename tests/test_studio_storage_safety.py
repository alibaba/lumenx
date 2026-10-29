"""Studio saves must preserve the last committed project relationships."""

import uuid
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from src.apps.comic_gen import pipeline as module
from src.apps.comic_gen.models import Character, Script
from src.utils.json_storage import CorruptStorageError, StorageConflictError, StorageError


@pytest.fixture
def make_pipeline(tmp_path, monkeypatch):
    for name in ("AssetGenerator", "StoryboardGenerator", "VideoGenerator", "AudioGenerator", "ExportManager"):
        monkeypatch.setattr(module, name, Mock())
    processor = Mock()
    processor.create_draft_script.side_effect = lambda title, text: Script(
        id=str(uuid.uuid4()), title=title, original_text=text, created_at=1, updated_at=1,
    )
    processor.parse_novel.side_effect = processor.create_draft_script.side_effect
    pipelines = []

    def make(root=None, **kwargs):
        result = module.ComicGenPipeline(data_dir=root or tmp_path / "data", script_processor=processor, **kwargs)
        pipelines.append(result)
        return result

    yield make
    for item in pipelines:
        close = getattr(item, "close", None)
        if close:
            close()


@pytest.mark.parametrize("filename", ["projects.json", "series.json", "library_assets.json"])
@pytest.mark.parametrize("contents", ["{", "[]", '{"broken": {}}'])
def test_invalid_database_stops_startup_and_preserves_contents(make_pipeline, tmp_path, filename, contents):
    root = tmp_path / "broken"
    root.mkdir()
    path = root / filename
    path.write_text(contents)
    before = path.read_bytes()
    with pytest.raises(CorruptStorageError):
        make_pipeline(root)
    assert path.read_bytes() == before
    path.unlink()
    assert make_pipeline(root).scripts == {}


def test_second_pipeline_cannot_load_or_recover_owned_directory(make_pipeline, monkeypatch):
    first = make_pipeline()
    recovery = Mock()
    monkeypatch.setattr(module.ComicGenPipeline, "_recover_orphan_tasks", recovery)
    with pytest.raises(StorageConflictError):
        make_pipeline(recover_orphan_tasks=True)
    recovery.assert_not_called()
    first.close()
    make_pipeline(recover_orphan_tasks=True)
    recovery.assert_called_once()


def _fail_save(monkeypatch, pipeline, uncertain=False):
    def fail(payloads):
        raise StorageError("disk unavailable", commit_uncertain=uncertain)
    monkeypatch.setattr(pipeline._storage, "commit", fail)


@pytest.mark.parametrize("uncertain", [False, True])
def test_failed_save_restores_snapshot_and_blocks_further_provider_calls(make_pipeline, monkeypatch, uncertain):
    pipeline = make_pipeline()
    project = pipeline.create_project("saved", "text", skip_analysis=True)
    before = (pipeline.data_dir / "projects.json").read_bytes()
    _fail_save(monkeypatch, pipeline, uncertain)
    project.title = "unsaved"
    with pytest.raises(StorageError):
        pipeline._save_data()
    assert pipeline.scripts[project.id].title == "saved"
    assert (pipeline.data_dir / "projects.json").read_bytes() == before
    pipeline.script_processor.parse_novel.reset_mock()
    with pytest.raises(StorageError, match="restart"):
        pipeline.create_project("new", "text")
    pipeline.script_processor.parse_novel.assert_not_called()


@pytest.mark.parametrize("operation", ["attach", "detach", "delete_series", "delete_project"])
def test_relationship_failure_restores_both_aggregates(make_pipeline, monkeypatch, operation):
    pipeline = make_pipeline()
    series = pipeline.create_series("series")
    project = pipeline.create_project("episode", "text", skip_analysis=True,
                                      series_id=None if operation == "attach" else series.id)
    before = {name: (pipeline.data_dir / name).read_bytes() for name in ("projects.json", "series.json")}
    _fail_save(monkeypatch, pipeline)
    with pytest.raises(StorageError):
        if operation == "attach":
            pipeline.add_episode_to_series(series.id, project.id)
        elif operation == "detach":
            pipeline.remove_episode_from_series(series.id, project.id)
        elif operation == "delete_series":
            pipeline.delete_series(series.id)
        else:
            pipeline.delete_project(project.id)
    for name, contents in before.items():
        assert (pipeline.data_dir / name).read_bytes() == contents
    assert pipeline.scripts[project.id].series_id == (None if operation == "attach" else series.id)
    assert pipeline.series_store[series.id].episode_ids == ([] if operation == "attach" else [project.id])


def test_bound_creation_and_import_never_commit_half_a_relationship(make_pipeline, monkeypatch):
    pipeline = make_pipeline()
    series = pipeline.create_series("existing")
    commits = []
    commit = pipeline._storage.commit
    def record(payloads):
        commits.append(set(payloads))
        return commit(payloads)
    monkeypatch.setattr(pipeline._storage, "commit", record)
    pipeline.create_project("bound", "text", skip_analysis=True, series_id=series.id)
    assert commits == [{"projects.json", "series.json"}]
    commits.clear()
    pipeline.create_series_from_import("imported", "episode text", [{"title": "first"}])
    assert commits == [{"projects.json", "series.json"}]


def test_missing_series_is_rejected_before_project_analysis(make_pipeline):
    pipeline = make_pipeline()
    with pytest.raises(ValueError, match="Series not found"):
        pipeline.create_project("new", "text", series_id="missing")
    pipeline.script_processor.parse_novel.assert_not_called()
    assert pipeline.scripts == {}


def test_http_save_failure_is_explicit_and_next_request_is_unavailable(make_pipeline, monkeypatch):
    from src.apps.comic_gen.api import create_app
    pipeline = make_pipeline()
    project = pipeline.create_project("saved", "text", skip_analysis=True)
    app = create_app(data_dir=pipeline.data_dir, load_config=False, pipeline_instance=pipeline)
    with TestClient(app) as client:
        _fail_save(monkeypatch, pipeline)
        response = client.delete(f"/projects/{project.id}")
        assert response.status_code == 503, response.text
        assert response.json()["code"] == "storage_unavailable"
        assert client.get("/projects/").status_code == 503
    assert project.id in pipeline.scripts


def test_http_storage_conflict_is_explicit(make_pipeline):
    from src.apps.comic_gen.api import create_app, get_pipeline
    pipeline = make_pipeline()
    app = create_app(data_dir=pipeline.data_dir, load_config=False, pipeline_instance=pipeline)
    def conflict():
        raise StorageConflictError("stale revision")
    app.dependency_overrides[get_pipeline] = conflict
    with TestClient(app) as client:
        response = client.get("/projects/")
        assert response.status_code == 409
        assert response.json()["code"] == "storage_conflict"


def test_library_source_key_is_idempotent_across_types_and_restart(make_pipeline):
    pipeline = make_pipeline()
    first = pipeline.create_library_asset("character", {"name": "saved", "description": "first"}, source_key="playground:g:o")
    again = pipeline.create_library_asset("scene", {"name": "retry", "description": "changed"}, source_key="playground:g:o")
    assert again.id == first.id
    assert again.name == "saved"
    other = pipeline.create_library_asset("character", {"name": "other", "description": "other"}, source_key="playground:g:o2")
    assert other.id != first.id
    pipeline.close()
    restored = make_pipeline()
    assert restored.create_library_asset("prop", {"name": "retry"}, source_key="playground:g:o").id == first.id
    assert len(restored.library_store.characters) == 2
    assert restored.library_store.scenes == []
    assert restored.library_store.props == []


def test_failed_library_creation_does_not_leave_uncommitted_asset(make_pipeline, monkeypatch):
    pipeline = make_pipeline()
    _fail_save(monkeypatch, pipeline)
    with pytest.raises(StorageError):
        pipeline.create_library_asset("character", {"name": "unsaved", "description": "test"}, source_key="playground:g:o")
    assert pipeline.library_store.characters == []


def test_reconcile_failure_restores_local_and_series_assets(make_pipeline, monkeypatch):
    from src.apps.comic_gen.api import create_app
    pipeline = make_pipeline()
    series = pipeline.create_series("series")
    project = pipeline.create_project("episode", "text", skip_analysis=True, series_id=series.id)
    project.characters.append(Character(id="local", name="hero", description="local"))
    pipeline._save_data()
    app = create_app(data_dir=pipeline.data_dir, load_config=False, pipeline_instance=pipeline)
    with TestClient(app) as client:
        _fail_save(monkeypatch, pipeline)
        response = client.post(f"/projects/{project.id}/reconcile/apply", json={
            "characters": [{"local_id": "local", "action": "create_new_in_series"}],
        })
        assert response.status_code == 503
    assert [item.id for item in pipeline.scripts[project.id].characters] == ["local"]
    assert pipeline.series_store[series.id].characters == []


def test_real_second_file_failure_reopens_consistent_relationships(make_pipeline, monkeypatch):
    from src.utils import json_transactions
    pipeline = make_pipeline()
    series = pipeline.create_series("series")
    project = pipeline.create_project("episode", "text", skip_analysis=True)
    original = json_transactions.atomic_write_json
    attempted = []
    def fail_series(path, value):
        attempted.append(path.name)
        if path.name == "series.json":
            raise StorageError("second file write failed")
        return original(path, value)
    with monkeypatch.context() as patch:
        patch.setattr(json_transactions, "atomic_write_json", fail_series)
        with pytest.raises(StorageError):
            pipeline.add_episode_to_series(series.id, project.id)
    assert attempted.index("projects.json") < attempted.index("series.json")
    pipeline.close()
    restored = make_pipeline()
    assert restored.scripts[project.id].series_id is None
    assert restored.series_store[series.id].episode_ids == []


def test_failed_pending_status_save_does_not_call_video_provider(make_pipeline, monkeypatch):
    pipeline = make_pipeline()
    project = pipeline.create_project("episode", "text", skip_analysis=True)
    _, task_id = pipeline.create_video_task(project.id, "", "motion")
    provider = Mock()
    pipeline.video_generator.model.generate = provider
    _fail_save(monkeypatch, pipeline)
    with pytest.raises(StorageError):
        pipeline.process_video_task(project.id, task_id)
    provider.assert_not_called()


def test_component_construction_failure_releases_directory_ownership(make_pipeline, monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(module, "AssetGenerator", Mock(side_effect=RuntimeError("component failed")))
        with pytest.raises(RuntimeError, match="component failed"):
            make_pipeline()
    assert make_pipeline().scripts == {}


def test_voice_accept_preserves_storage_status_before_runtime_error_handler(make_pipeline, monkeypatch):
    from src.apps.comic_gen.api import create_app
    pipeline = make_pipeline()
    series = pipeline.create_series("series")
    app = create_app(data_dir=pipeline.data_dir, load_config=False, pipeline_instance=pipeline)
    with TestClient(app) as client:
        _fail_save(monkeypatch, pipeline)
        response = client.post("/voice/design/accept", json={
            "series_id": series.id, "voice_id": "voice-existing", "voice_prompt": "warm", "label": "voice",
        })
        assert response.status_code == 503, response.text
    assert pipeline.series_store[series.id].custom_voices == []
