"""Video task processing claims, validates targets, and commits results by short lock."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.apps.comic_gen import pipeline as module
from src.apps.comic_gen.models import Character, Scene, Script, Series, StoryboardFrame, VideoTask
from src.utils.json_storage import StorageConflictError, StorageError


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    for name in ("AssetGenerator", "StoryboardGenerator", "AudioGenerator", "ExportManager"):
        monkeypatch.setattr(module, name, Mock())
    item = module.ComicGenPipeline(data_dir=tmp_path / "data", script_processor=Mock(), warmup_demucs=False)
    image = item.data_dir / "uploads/source.png"
    image.parent.mkdir(parents=True, exist_ok=True)
    image.write_bytes(b"png")
    item.scripts["p"] = Script(
        id="p",
        title="project",
        original_text="source",
        created_at=1,
        updated_at=1,
        scenes=[Scene(id="scene", name="Room", description="quiet", image_url="uploads/source.png")],
        characters=[Character(id="char", name="Hero", description="red coat", image_url="uploads/source.png")],
        frames=[StoryboardFrame(id="frame", scene_id="scene")],
        video_tasks=[_task("task")],
    )
    item._commit_storage("projects.json", "series.json", "library_assets.json")
    state = SimpleNamespace(calls=[], hook=None, write_file=True)

    def generate(**kwargs):
        state.calls.append(dict(kwargs))
        if state.hook:
            state.hook()
        if state.write_file:
            with open(kwargs["output_path"], "wb") as handle:
                handle.write(b"mp4")
        return kwargs["output_path"], {"provider": "fake"}

    item.video_generator.model = SimpleNamespace(generate=generate)
    yield item, state
    item.close()


def _task(task_id="task", *, asset_id=None, frame_id="frame"):
    return VideoTask(
        id=task_id,
        project_id="p",
        frame_id=frame_id,
        asset_id=asset_id,
        image_url="uploads/source.png",
        prompt="make video",
        status="pending",
        model="wan2.7-i2v",
    )


def test_duplicate_process_does_not_pay_provider_twice(runtime):
    item, state = runtime

    item.process_video_task("p", "task")
    item.process_video_task("p", "task")

    assert len(state.calls) == 1, "completed/in-flight task must be claimed once per process"


def test_late_cancel_blocks_provider_result_commit(runtime):
    item, state = runtime

    def cancel_midflight():
        assert item.mark_video_task_failed("p", "task", "Canceled by user") is True

    state.hook = cancel_midflight
    item.process_video_task("p", "task")

    saved = item.read_committed_script("p").video_tasks[0]
    assert saved.status == "failed"
    assert saved.video_url is None
    assert saved.error == "Canceled by user"


def test_late_asset_recreate_does_not_attach_stale_result_to_new_owner(runtime):
    item, state = runtime
    item.scripts["p"].video_tasks = [_task("asset-task", asset_id="char", frame_id=None)]
    item.scripts["p"].characters[0].video_assets = [item.scripts["p"].video_tasks[0]]
    item._commit_storage("projects.json")

    def recreate_asset_midflight():
        item.scripts["p"].characters = [
            Character(id="char", name="Replacement", description="new", image_url="uploads/source.png")
        ]
        item._commit_storage("projects.json")

    state.hook = recreate_asset_midflight

    with pytest.raises(StorageConflictError):
        item.process_video_task("p", "asset-task")

    replacement = item.read_committed_script("p").characters[0]
    assert replacement.name == "Replacement"
    assert replacement.video_assets == []


def test_missing_result_file_cannot_finish_completed(runtime):
    item, state = runtime
    state.write_file = False

    item.process_video_task("p", "task")

    saved = item.read_committed_script("p").video_tasks[0]
    assert saved.status == "failed"
    assert saved.video_url is None


def test_series_asset_video_result_writes_shared_owner(runtime):
    item, state = runtime
    shared_scene = item.scripts["p"].scenes.pop()
    item.scripts["p"].series_id = "series"
    series_task = _task("series-task", asset_id="scene", frame_id=None)
    item.scripts["p"].video_tasks = [series_task]
    shared_scene.video_assets = [series_task]
    item.series_store["series"] = Series(
        id="series",
        title="series",
        created_at=1,
        updated_at=1,
        scenes=[shared_scene],
    )
    item._commit_storage("projects.json", "series.json")

    item.process_video_task("p", "series-task")

    _, series, _ = item.read_committed_project_view("p")
    assert series.scenes[0].video_assets[0].id == "series-task"
    assert series.scenes[0].video_assets[0].status == "completed"


def test_concurrent_process_claim_allows_one_provider_call(runtime):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    item, state = runtime
    entered, release = Event(), Event()

    def block_once():
        if len(state.calls) == 1:
            entered.set()
            assert release.wait(3), "test did not release provider"

    state.hook = block_once
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(item.process_video_task, "p", "task")
        assert entered.wait(2), "first provider call did not start"
        second = pool.submit(item.process_video_task, "p", "task")
        release.set()
        first.result(timeout=3)
        second.result(timeout=3)

    assert len(state.calls) == 1
    assert item.read_committed_script("p").video_tasks[0].status == "completed"


def test_provider_id_callback_after_cancel_does_not_resurrect_or_overwrite(runtime):
    item, state = runtime

    def cancel_and_report_ids():
        assert item.mark_video_task_failed("p", "task", "Canceled by user") is True
        callback = state.calls[-1]["on_provider_ids"]
        callback("dashscope", "provider-task", "provider-request")

    state.hook = cancel_and_report_ids
    item.process_video_task("p", "task")

    saved = item.read_committed_script("p").video_tasks[0]
    assert saved.status == "failed"
    assert saved.error == "Canceled by user"
    assert saved.provider_task_id is None
    assert saved.provider_request_id is None


def test_provider_failure_after_cancel_keeps_cancel_reason(runtime):
    item, state = runtime

    def cancel_then_raise(**kwargs):
        state.calls.append(dict(kwargs))
        assert item.mark_video_task_failed("p", "task", "Canceled by user") is True
        raise RuntimeError("provider failed later")

    item.video_generator.model.generate = cancel_then_raise
    item.process_video_task("p", "task")

    saved = item.read_committed_script("p").video_tasks[0]
    assert saved.status == "failed"
    assert saved.error == "Canceled by user"


def test_global_asset_video_result_writes_shared_owner(runtime):
    item, state = runtime
    shared_char = item.scripts["p"].characters.pop()
    global_task = _task("global-task", asset_id="char", frame_id=None)
    item.scripts["p"].video_tasks = [global_task]
    shared_char.video_assets = [global_task]
    item.library_store.characters = [shared_char]
    item._commit_storage("projects.json", "library_assets.json")

    item.process_video_task("p", "global-task")

    _, _, library = item.read_committed_project_view("p")
    assert library.characters[0].video_assets[0].id == "global-task"
    assert library.characters[0].video_assets[0].status == "completed"


def test_series_owner_processing_and_provider_ids_are_committed(runtime):
    item, state = runtime
    shared_scene = item.scripts["p"].scenes.pop()
    item.scripts["p"].series_id = "series"
    series_task = _task("series-provider-task", asset_id="scene", frame_id=None)
    item.scripts["p"].video_tasks = [series_task]
    shared_scene.video_assets = [series_task]
    item.series_store["series"] = Series(
        id="series",
        title="series",
        created_at=1,
        updated_at=1,
        scenes=[shared_scene],
    )
    item._commit_storage("projects.json", "series.json")

    def report_ids_then_stop():
        _, series, _ = item.read_committed_project_view("p")
        assert series.scenes[0].video_assets[0].status == "processing"
        callback = state.calls[-1]["on_provider_ids"]
        callback("dashscope", "provider-task", "provider-request")
        _, series, _ = item.read_committed_project_view("p")
        assert series.scenes[0].video_assets[0].provider_task_id == "provider-task"
        raise RuntimeError("hold after provider ids")

    state.hook = report_ids_then_stop
    item.process_video_task("p", "series-provider-task")

    _, series, _ = item.read_committed_project_view("p")
    saved = series.scenes[0].video_assets[0]
    assert saved.status == "failed"
    assert saved.provider_name == "dashscope"
    assert saved.provider_task_id == "provider-task"
    assert saved.provider_request_id == "provider-request"


def test_global_owner_cancel_commits_failed_state(runtime):
    item, state = runtime
    shared_char = item.scripts["p"].characters.pop()
    global_task = _task("global-cancel-task", asset_id="char", frame_id=None)
    item.scripts["p"].video_tasks = [global_task]
    shared_char.video_assets = [global_task]
    item.library_store.characters = [shared_char]
    item._commit_storage("projects.json", "library_assets.json")

    def cancel_midflight():
        assert item.mark_video_task_failed("p", "global-cancel-task", "Canceled by user") is True

    state.hook = cancel_midflight
    item.process_video_task("p", "global-cancel-task")

    _, _, library = item.read_committed_project_view("p")
    saved = library.characters[0].video_assets[0]
    assert saved.status == "failed"
    assert saved.error == "Canceled by user"
    assert saved.video_url is None


def test_claim_released_when_initial_processing_save_fails(runtime, monkeypatch):
    item, state = runtime
    real_commit = item._storage.commit
    failures = {"remaining": 1}

    def fail_once(payloads):
        if failures["remaining"]:
            failures["remaining"] -= 1
            raise RuntimeError("synthetic processing save failure")
        return real_commit(payloads)

    monkeypatch.setattr(item._storage, "commit", fail_once)

    with pytest.raises(Exception):
        item.process_video_task("p", "task")

    monkeypatch.setattr(item._storage, "commit", real_commit)
    assert item._video_task_runs == {}
    item.process_video_task("p", "task")
    assert len(state.calls) == 1
    assert item.read_committed_script("p").video_tasks[0].status == "completed"


def test_missing_frame_rejected_before_provider(runtime):
    item, state = runtime
    item.scripts["p"].frames = []
    item._commit_storage("projects.json")

    with pytest.raises(StorageConflictError):
        item.process_video_task("p", "task")

    assert state.calls == []
    assert item.read_committed_script("p").video_tasks[0].status == "pending"


def test_foreign_project_task_rejected_before_provider(runtime):
    item, state = runtime
    item.scripts["p"].video_tasks[0].project_id = "other-project"
    item._commit_storage("projects.json")

    with pytest.raises(StorageConflictError):
        item.process_video_task("p", "task")

    assert state.calls == []
    saved = item.read_committed_script("p").video_tasks[0]
    assert saved.project_id == "other-project"
    assert saved.status == "pending"


def test_task_project_id_mutation_during_paid_call_blocks_result(runtime):
    item, state = runtime

    def mutate_project_id_midflight():
        item.scripts["p"].video_tasks[0].project_id = "other-project"
        item._commit_storage("projects.json")

    state.hook = mutate_project_id_midflight

    with pytest.raises(StorageConflictError):
        item.process_video_task("p", "task")

    saved = item.read_committed_script("p").video_tasks[0]
    assert saved.project_id == "other-project"
    assert saved.status == "processing"
    assert saved.video_url is None


def test_non_dependency_frame_metadata_edit_during_paid_call_keeps_result(runtime):
    item, state = runtime
    item.scripts["p"].frames[0].action_description = "generation input"
    item._commit_storage("projects.json")

    def edit_non_provider_metadata_midflight():
        frame = item.scripts["p"].frames[0]
        frame.is_video_pinned = True
        frame.selected_video_id = "manual-existing-take"
        frame.final_take_id = "final-existing-take"
        frame.workbench_tab_mode = "direct_r2v"
        frame.workbench_generate_count = 3
        item._commit_storage("projects.json")

    state.hook = edit_non_provider_metadata_midflight
    item.process_video_task("p", "task")

    saved = item.read_committed_script("p")
    assert saved.frames[0].is_video_pinned is True
    assert saved.frames[0].selected_video_id == "manual-existing-take"
    assert saved.frames[0].workbench_tab_mode == "direct_r2v"
    assert saved.video_tasks[0].status == "completed"
    assert saved.video_tasks[0].video_url


def test_frame_dialogue_edit_during_paid_call_keeps_accepted_task_result(runtime):
    item, state = runtime
    item.scripts["p"].frames[0].dialogue = "original line"
    item._commit_storage("projects.json")

    def edit_dialogue_midflight():
        frame = item.scripts["p"].frames[0]
        frame.dialogue = "changed line"
        item._commit_storage("projects.json")

    state.hook = edit_dialogue_midflight

    item.process_video_task("p", "task")

    saved = item.read_committed_script("p")
    assert saved.frames[0].dialogue == "changed line"
    assert saved.video_tasks[0].status == "completed"
    assert saved.video_tasks[0].video_url


def test_late_frame_recreate_blocks_result_commit(runtime):
    item, state = runtime

    def recreate_frame_midflight():
        item.scripts["p"].frames = [StoryboardFrame(id="frame", scene_id="scene", action_description="replacement")]
        item._commit_storage("projects.json")

    state.hook = recreate_frame_midflight

    with pytest.raises(StorageConflictError):
        item.process_video_task("p", "task")

    saved = item.read_committed_script("p")
    assert saved.frames[0].action_description == "replacement"
    assert saved.video_tasks[0].status == "processing"
    assert saved.video_tasks[0].video_url is None


def test_task_provider_input_mutation_during_paid_call_blocks_result(runtime):
    item, state = runtime

    def mutate_task_input_midflight():
        item.scripts["p"].video_tasks[0].prompt = "mutated provider input"
        item._commit_storage("projects.json")

    state.hook = mutate_task_input_midflight

    with pytest.raises(StorageConflictError):
        item.process_video_task("p", "task")

    saved = item.read_committed_script("p").video_tasks[0]
    assert saved.prompt == "mutated provider input"
    assert saved.status == "processing"
    assert saved.video_url is None


def test_late_task_recreate_with_same_id_does_not_receive_provider_ids_or_result(runtime):
    item, state = runtime

    def recreate_task_midflight():
        replacement = _task("task")
        replacement.status = "processing"
        replacement.prompt = "replacement"
        item.scripts["p"].video_tasks = [replacement]
        item._commit_storage("projects.json")
        callback = state.calls[-1]["on_provider_ids"]
        callback("dashscope", "provider-task", "provider-request")

    state.hook = recreate_task_midflight

    with pytest.raises(StorageConflictError):
        item.process_video_task("p", "task")

    saved = item.read_committed_script("p").video_tasks[0]
    assert saved.prompt == "replacement"
    assert saved.video_url is None
    assert saved.provider_task_id is None


def test_asset_owner_source_move_blocks_stale_result(runtime):
    item, state = runtime
    asset_task = _task("asset-task", asset_id="char", frame_id=None)
    item.scripts["p"].video_tasks = [asset_task]
    item.scripts["p"].characters[0].video_assets = [asset_task]
    item._commit_storage("projects.json")

    def move_owner_midflight():
        moved = item.scripts["p"].characters.pop()
        item.library_store.characters = [moved]
        item._commit_storage("projects.json", "library_assets.json")

    state.hook = move_owner_midflight

    with pytest.raises(StorageConflictError):
        item.process_video_task("p", "asset-task")

    _, _, library = item.read_committed_project_view("p")
    assert library.characters[0].video_assets[0].status == "processing"
    assert library.characters[0].video_assets[0].video_url is None


def test_asset_owner_replacement_before_process_is_rejected_without_paid_call(runtime):
    item, state = runtime
    asset_task = _task("asset-replaced-task", asset_id="char", frame_id=None)
    item.scripts["p"].video_tasks = [asset_task]
    replacement = _task("asset-replaced-task", asset_id="char", frame_id=None)
    replacement.prompt = "replacement owner"
    item.scripts["p"].characters[0].video_assets = [replacement]
    item._commit_storage("projects.json")

    with pytest.raises(StorageConflictError):
        item.process_video_task("p", "asset-replaced-task")

    assert state.calls == []
    saved = item.read_committed_script("p")
    assert saved.video_tasks[0].status == "pending"
    assert saved.characters[0].video_assets[0].prompt == "replacement owner"


def test_cancel_does_not_overwrite_replaced_asset_owner_entry(runtime):
    item, _ = runtime
    asset_task = _task("asset-cancel-task", asset_id="char", frame_id=None)
    item.scripts["p"].video_tasks = [asset_task]
    replacement = _task("asset-cancel-task", asset_id="char", frame_id=None)
    replacement.prompt = "replacement owner"
    item.scripts["p"].characters[0].video_assets = [replacement]
    item._commit_storage("projects.json")

    assert item.mark_video_task_failed("p", "asset-cancel-task", "Canceled by user") is True

    saved = item.read_committed_script("p")
    assert saved.video_tasks[0].status == "failed"
    assert saved.characters[0].video_assets[0].prompt == "replacement owner"
    assert saved.characters[0].video_assets[0].status == "pending"


def test_preexisting_output_path_does_not_make_empty_provider_result_completed(runtime):
    item, state = runtime
    stale = item.data_dir / "video" / "video_task.mp4"
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_bytes(b"old")
    state.write_file = False

    item.process_video_task("p", "task")

    saved = item.read_committed_script("p").video_tasks[0]
    assert saved.status == "failed"
    assert saved.video_url is None
    assert state.calls[0]["output_path"] != str(stale)


def test_create_video_task_dialogue_edit_during_snapshot_is_rejected(runtime, monkeypatch):
    item, _ = runtime
    item.scripts["p"].frames[0].dialogue = "accepted line"
    item._commit_storage("projects.json")
    import shutil
    original_copy2 = shutil.copy2

    def edit_dialogue_before_copy(src, dst):
        item.scripts["p"].frames[0].dialogue = "changed before admission"
        item._commit_storage("projects.json")
        return original_copy2(src, dst)

    monkeypatch.setattr("shutil.copy2", edit_dialogue_before_copy)

    with pytest.raises(StorageConflictError):
        item.create_video_task("p", "uploads/source.png", "base prompt", frame_id="frame")

    saved = item.read_committed_script("p")
    assert saved.frames[0].dialogue == "changed before admission"
    assert [task.id for task in saved.video_tasks] == ["task"]


def test_create_video_task_returns_committed_detached_script(runtime):
    item, _ = runtime

    returned, task_id = item.create_video_task("p", "http://example.test/source.png", "new task")
    returned.video_tasks.clear()

    saved = item.read_committed_script("p")
    assert [task.id for task in saved.video_tasks] == ["task", task_id]
    assert returned is not item.scripts["p"]


def test_create_video_task_save_failure_leaves_no_visible_task(runtime, monkeypatch):
    item, _ = runtime
    before = item.read_committed_script("p").model_dump(mode="json")
    real_commit = item._storage.commit

    def fail(payloads):
        raise StorageError("synthetic create save failure")

    monkeypatch.setattr(item._storage, "commit", fail)

    with pytest.raises(StorageError):
        item.create_video_task("p", "uploads/source.png", "new task")

    monkeypatch.setattr(item._storage, "commit", real_commit)
    assert item.scripts["p"].model_dump(mode="json") == before
    assert [task.id for task in item.scripts["p"].video_tasks] == ["task"]


def test_create_video_task_deleted_project_during_snapshot_fails_without_task(runtime, monkeypatch):
    item, _ = runtime
    import shutil
    original_copy2 = shutil.copy2

    def delete_project_before_copy(src, dst):
        del item.scripts["p"]
        item._commit_storage("projects.json")
        return original_copy2(src, dst)

    monkeypatch.setattr("shutil.copy2", delete_project_before_copy)

    with pytest.raises(StorageConflictError):
        item.create_video_task("p", "uploads/source.png", "new task")

    assert "p" not in item.scripts


def test_create_video_task_concurrent_deletion_after_snapshot_fails_without_resurrecting(runtime, monkeypatch):
    item, _ = runtime
    original_uuid4 = module.uuid.uuid4

    class DeletingUUID:
        hex = "fixedtaskid0001"
        def __str__(self):
            del item.scripts["p"]
            item._commit_storage("projects.json")
            return "fixed-task-id"

    monkeypatch.setattr(module.uuid, "uuid4", lambda: DeletingUUID())

    with pytest.raises(StorageConflictError):
        item.create_video_task("p", "http://example.test/source.png", "new task")

    assert "p" not in item.scripts
    monkeypatch.setattr(module.uuid, "uuid4", original_uuid4)
