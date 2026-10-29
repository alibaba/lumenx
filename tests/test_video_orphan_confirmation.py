"""A restarted legacy task has an unknown provider outcome, not a known failure."""

from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from src.apps.comic_gen import api, pipeline as module
from src.apps.comic_gen.models import Character, Script, Series, VideoTask
from src.utils.json_storage import StorageError


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    providers = {}
    for name in ("AssetGenerator", "StoryboardGenerator", "VideoGenerator", "AudioGenerator", "ExportManager"):
        providers[name] = Mock()
        monkeypatch.setattr(module, name, providers[name])
    item = module.ComicGenPipeline(data_dir=tmp_path / "data", script_processor=Mock())
    item.scripts["p"] = Script(id="p", title="Project", original_text="source", created_at=1, updated_at=1)
    yield item, providers
    item.close()


def task(status="processing", **values):
    return VideoTask(id="t", project_id="p", image_url="uploads/source.png", prompt="shot", status=status, **values)


@pytest.mark.parametrize("status", ["pending", "processing"])
def test_real_restart_preserves_unknown_outcome_and_diagnostics(runtime, status):
    item, providers = runtime
    item.scripts["p"].video_tasks = [task(
        status, provider_name="dashscope", provider_task_id="paid-task", provider_request_id="request",
        error="previous transport error", video_url="video/paid.mp4", is_starred=True, label="keep",
    )]
    media = item.data_dir / "video/paid.mp4"
    media.parent.mkdir(parents=True)
    media.write_bytes(b"paid output")
    item._commit_storage("projects.json")
    before = item.read_committed_script("p").video_tasks[0].model_dump()
    item.close()

    restarted = module.ComicGenPipeline(data_dir=item.data_dir, script_processor=Mock(), recover_orphan_tasks=True)
    try:
        saved = restarted.read_committed_script("p").video_tasks[0]
        assert saved.status == "needs_confirmation"
        assert saved.model_dump(exclude={"status"}) == {k: v for k, v in before.items() if k != "status"}
        assert media.read_bytes() == b"paid output"
        assert not providers["VideoGenerator"].return_value.model.generate.called
        restarted.process_video_task("p", "t")
        assert not providers["VideoGenerator"].return_value.model.generate.called
        contents = (item.data_dir / "projects.json").read_bytes()
        restarted._recover_orphan_tasks()
        assert (item.data_dir / "projects.json").read_bytes() == contents
    finally:
        restarted.close()


@pytest.mark.parametrize("owner", ["series", "global"])
def test_recovery_persists_matching_shared_asset_mirror(runtime, owner):
    item, _ = runtime
    original = task(asset_id="c")
    item.scripts["p"].video_tasks = [original]
    character = Character(id="c", name="Hero", description="Coat", video_assets=[original.model_copy(deep=True)])
    if owner == "series":
        item.scripts["p"].series_id = "s"
        item.series_store["s"] = Series(id="s", title="Series", created_at=1, updated_at=1, characters=[character])
    else:
        item.library_store.characters = [character]
    item._commit_storage("projects.json", "series.json", "library_assets.json")

    item._recover_orphan_tasks()

    assert item.read_committed_script("p").video_tasks[0].status == "needs_confirmation"
    _, series, library = item.read_committed_project_view("p")
    shared = series.characters[0] if owner == "series" else library.characters[0]
    assert shared.video_assets[0].status == "needs_confirmation"


def test_different_shared_task_is_not_overwritten_by_legacy_recovery(runtime):
    item, _ = runtime
    item.scripts["p"].video_tasks = [task(asset_id="c")]
    newer = task("completed", asset_id="c", video_url="video/newer.mp4")
    item.library_store.characters = [Character(id="c", name="Hero", description="Coat", video_assets=[newer])]
    item._commit_storage("projects.json", "library_assets.json")

    item._recover_orphan_tasks()

    assert item.read_committed_script("p").video_tasks[0].status == "needs_confirmation"
    assert item.read_committed_project_view("p")[2].characters[0].video_assets[0] == newer


def test_recovery_save_failure_preserves_previous_committed_state(runtime, monkeypatch):
    item, _ = runtime
    item.scripts["p"].video_tasks = [task()]
    item._commit_storage("projects.json")
    path = item.data_dir / "projects.json"
    contents = path.read_bytes()
    monkeypatch.setattr(item._storage, "commit", Mock(side_effect=StorageError("injected write failure")))

    with pytest.raises(StorageError):
        item._recover_orphan_tasks()

    assert path.read_bytes() == contents
    assert item.scripts["p"].video_tasks[0].status == "processing"
    with pytest.raises(StorageError):
        item.assert_storage_available()


def test_late_failure_cannot_turn_confirmation_into_a_retryable_failure(runtime):
    item, _ = runtime
    item.scripts["p"].video_tasks = [task("needs_confirmation")]
    item._commit_storage("projects.json")
    assert item.mark_video_task_failed("p", "t", "late wrapper error") is False
    assert item.read_committed_script("p").video_tasks[0].status == "needs_confirmation"


def test_cancel_unknown_result_does_not_claim_remote_failure(runtime):
    item, _ = runtime
    item.scripts["p"].video_tasks = [task("needs_confirmation")]
    item._commit_storage("projects.json")
    app = api.create_app(data_dir=item.data_dir, user_data_dir=item.data_dir.parent / "settings", load_config=False, pipeline_instance=item)
    with TestClient(app) as client:
        response = client.post("/projects/p/video_tasks/t/cancel")
        assert response.status_code == 409
        assert item.read_committed_script("p").video_tasks[0].status == "needs_confirmation"
