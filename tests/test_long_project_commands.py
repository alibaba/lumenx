"""Project extraction/context commands keep LLM work outside committed writes.

Synthetic data and LLM substitutes only; run through run_tests_isolated.py.
"""

from threading import Thread
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from src.apps.comic_gen import api, pipeline as module
from src.apps.comic_gen.models import Character, Script, Series, StoryboardFrame, VideoTask
from src.utils.json_storage import StorageConflictError, StorageError


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    for name in ("AssetGenerator", "StoryboardGenerator", "VideoGenerator", "AudioGenerator", "ExportManager"):
        monkeypatch.setattr(module, name, Mock())
    item = module.ComicGenPipeline(data_dir=tmp_path / "data", script_processor=Mock())
    for key in ("prev", "p"):
        item.scripts[key] = Script(
            id=key, title=key, original_text=f"{key} original", created_at=1, updated_at=1,
            series_id="s", characters=[Character(id=key + "-c", name="saved", description="saved")],
        )
    item.series_store["s"] = Series(id="s", title="series", created_at=1, updated_at=1, episode_ids=["prev", "p"])
    item._commit_storage("projects.json", "series.json")
    yield item
    item.close()


def parsed(text):
    return Script(id="parsed", title="parsed", original_text=text, created_at=2, updated_at=2,
                  characters=[Character(id="fresh", name=text, description=text)])


@pytest.mark.parametrize("change", ["edit", "delete"])
def test_reparse_rejects_a_changed_or_deleted_target(runtime, change):
    def parse(*args):
        if change == "delete":
            runtime.delete_project("p")
        else:
            runtime.update_script_text("p", "newer user edit", expected_revision=0)
        return parsed("old request")
    runtime.script_processor.parse_novel.side_effect = parse
    with pytest.raises((StorageConflictError, ValueError)):
        runtime.reparse_project("p", "old request", expected_revision=0)
    if change == "delete":
        assert "p" not in runtime.scripts
    else:
        assert runtime.scripts["p"].original_text == "newer user edit"
    runtime.assert_storage_available()


@pytest.mark.parametrize("change", ["different_text", "revision", "returned_preview"])
def test_extraction_cache_is_bound_to_input_and_not_shared_with_preview(runtime, change):
    runtime.script_processor.parse_novel.side_effect = lambda title, text, *args: parsed(text)
    preview = runtime.extract_preview("p", "preview text")
    text = "preview text"
    if change == "different_text":
        text = "apply text"
    elif change == "revision":
        runtime.update_script_text("p", "newer user edit", expected_revision=0)
    else:
        preview.characters[0].name = "response-only mutation"
    if change == "revision":
        with pytest.raises(StorageConflictError):
            runtime.reparse_project("p", text, expected_revision=0)
        assert runtime.read_committed_script("p").original_text == "newer user edit"
        runtime.script_processor.parse_novel.assert_called_once()
        # A new, explicitly read confirmation may use the changed project.
        revision = runtime.read_committed_script("p").revision
    else:
        revision = 0
    result = runtime.reparse_project("p", text, expected_revision=revision)
    assert result.original_text == text
    assert result.characters[0].name == text
    assert runtime.script_processor.parse_novel.call_count == (1 if change == "returned_preview" else 2)


@pytest.mark.parametrize("endpoint,change", [(endpoint,change)
    for endpoint in ("next_hook", "previous_episode/summary")
    for change in ("target_edit", "target_delete", "source_edit", "series_order")
    if endpoint != "next_hook" or change in ("target_edit", "target_delete")])
def test_generated_context_does_not_apply_across_changed_inputs(runtime, monkeypatch, endpoint, change):
    from src.apps.comic_gen import llm_adapter

    def chat(**kwargs):
        if change == "target_delete":
            runtime.delete_project("p")
        elif change == "series_order":
            runtime.series_store["s"].episode_ids.reverse()
            runtime._save_series_data()
        else:
            key = "prev" if change == "source_edit" else "p"
            runtime.update_script_text(key, "newer user text", expected_revision=0)
        return "generated from old inputs"

    def adapter_factory(*args, **kwargs):
        assert not args
        assert kwargs.get("env") is not None
        return Mock(chat=chat)

    monkeypatch.setattr(llm_adapter, "LLMAdapter", adapter_factory)
    app = api.create_app(data_dir=runtime.data_dir, load_config=False, pipeline_instance=runtime)
    with TestClient(app) as client:
        response = client.post(f"/projects/p/{endpoint}")
        assert response.status_code == (404 if change == "target_delete" else 409), response.text
        if change == "target_delete":
            assert "p" not in runtime.scripts
        else:
            project = runtime.scripts["p"]
            assert project.next_hook_cache is None and project.last_episode_summary_cache is None
        runtime.assert_storage_available()


def run_other_thread(command):
    result = []
    def run():
        try:
            result.append(command())
        except BaseException as exc:
            result.append(exc)
    thread = Thread(target=run, daemon=True)
    thread.start()
    thread.join(timeout=3)
    assert not thread.is_alive(), "The LLM call must not hold the storage command lock"
    if isinstance(result[0], BaseException):
        raise result[0]
    return result[0]


@pytest.mark.parametrize("method", ["extract_preview", "reparse_project"])
@pytest.mark.parametrize("change", ["edit", "delete", "close"])
def test_extraction_rechecks_after_an_unlocked_llm_call(runtime, method, change):
    before = runtime.read_committed_script("p").model_dump()
    def parse(*args):
        def mutate():
            if change == "close":
                runtime.close()
            elif change == "delete":
                runtime.delete_project("p")
            else:
                runtime.update_script_text("p", "concurrent text", expected_revision=0)
        run_other_thread(mutate)
        return parsed("generated")
    runtime.script_processor.parse_novel.side_effect = parse
    expected = StorageError if change == "close" else ValueError if change == "delete" else StorageConflictError
    with pytest.raises(expected):
        getattr(runtime, method)("p", "requested", **({"expected_revision": before["revision"]} if method == "reparse_project" else {}))
    assert not runtime._extraction_cache
    assert runtime.script_processor.parse_novel.call_count == 1
    if change == "close":
        assert runtime.scripts["p"].model_dump() == before
    elif change == "delete":
        assert "p" not in runtime.scripts
    else:
        assert runtime.read_committed_script("p").original_text == "concurrent text"
        runtime.assert_storage_available()


@pytest.mark.parametrize("change", ["expiry", "prompt"])
def test_preview_cache_expires_with_time_or_extraction_prompt(runtime, monkeypatch, change):
    clock = [1000.0]
    monkeypatch.setattr(module.time, "time", lambda: clock[0])
    runtime.script_processor.parse_novel.side_effect = lambda title, text, *args: parsed(text)
    revision = runtime.read_committed_script("p").revision
    runtime.extract_preview("p", "same text")
    if change == "expiry":
        clock[0] += 301
    else:
        runtime.scripts["p"].prompt_config.entity_extraction = "custom extraction policy"
        runtime._save_data()
        with pytest.raises(StorageConflictError):
            runtime.reparse_project("p", "same text", expected_revision=revision)
        runtime.script_processor.parse_novel.assert_called_once()
        revision = runtime.read_committed_script("p").revision
    runtime.reparse_project("p", "same text", expected_revision=revision)
    assert runtime.script_processor.parse_novel.call_count == 2
    if change == "prompt":
        assert runtime.script_processor.parse_novel.call_args.args[2] == "custom extraction policy"


def test_reparse_keeps_the_existing_project_field_policy_and_returns_a_detached_snapshot(runtime):
    script = runtime.scripts["p"]
    script.episode_number = 2
    script.workflow_mode, script.default_generation_mode = "r2v", "i2v"
    script.style_preset, script.style_prompt = "custom", "preserved style"
    script.prompt_config.entity_extraction = "preserved extraction"
    script.bgm_url, script.merged_video_url = "audio/retained.mp3", "video/retained.mp4"
    script.mix_settings = {"bgm": 42}
    script.frames = [StoryboardFrame(id="old-frame", scene_id="", action_description="old frame")]
    script.video_tasks = [VideoTask(id="old-task", project_id="p", image_url="assets/old.png", prompt="old")]
    script.starred, script.next_hook_cache, script.last_episode_summary_cache = True, "old hook", "old summary"
    runtime._save_data()
    retained_fields = (
        "id", "created_at", "art_direction", "model_settings", "style_preset", "style_prompt",
        "merged_video_url", "workflow_mode", "series_id", "episode_number", "prompt_config",
        "default_generation_mode", "bgm_url", "mix_settings",
    )
    before = script.model_dump()
    fresh = parsed("new text")
    runtime.script_processor.parse_novel.return_value = fresh
    result = runtime.reparse_project("p", "new text", expected_revision=before["revision"])
    saved = runtime.read_committed_script("p")
    assert {field: saved.model_dump()[field] for field in retained_fields} == {field: before[field] for field in retained_fields}
    assert saved.revision == before["revision"] + 1
    assert saved.characters[0].id == "fresh" and saved.original_text == "new text"
    assert saved.frames == [] and saved.video_tasks == []
    assert not saved.starred and saved.next_hook_cache is None and saved.last_episode_summary_cache is None
    assert runtime.series_store["s"].episode_ids == ["prev", "p"]
    result.characters[0].name = "response changed"
    fresh.characters[0].description = "provider object changed"
    assert runtime.scripts["p"].model_dump() == saved.model_dump()


@pytest.mark.parametrize("method", ["extract_preview", "reparse_project"])
def test_extraction_rejects_a_closed_runtime_without_calling_llm(runtime, method):
    runtime.close()
    with pytest.raises(StorageError):
        getattr(runtime, method)("p", "new text", **({"expected_revision": 0} if method == "reparse_project" else {}))
    runtime.script_processor.parse_novel.assert_not_called()


def test_reparse_storage_failure_restores_memory_and_disk(runtime, monkeypatch):
    before = runtime.read_committed_script("p").model_dump()
    disk = runtime.data_dir / "projects.json"
    disk_before = disk.read_bytes()
    runtime.script_processor.parse_novel.return_value = parsed("new text")
    monkeypatch.setattr(runtime._storage, "commit", Mock(side_effect=StorageError("disk full")))
    with pytest.raises(StorageError):
        runtime.reparse_project("p", "new text", expected_revision=before["revision"])
    assert disk.read_bytes() == disk_before
    assert runtime.scripts["p"].model_dump() == before
    with pytest.raises(StorageError):
        runtime.assert_storage_available()


@pytest.mark.parametrize("endpoint", ["extract_preview", "reparse"])
def test_extraction_provider_failure_does_not_mutate_and_is_explicit_502(runtime, endpoint):
    before = runtime.read_committed_script("p").model_dump()
    runtime.script_processor.parse_novel.side_effect = RuntimeError("synthetic LLM failure")
    app = api.create_app(data_dir=runtime.data_dir, load_config=False, pipeline_instance=runtime)
    with TestClient(app) as client:
        response = getattr(client, "put" if endpoint == "reparse" else "post")(
            f"/projects/p/{endpoint}", json={"text": "new text", **({"expected_revision": before["revision"]} if endpoint == "reparse" else {})},
        )
        assert response.status_code == 502, response.text
        assert runtime.read_committed_script("p").model_dump() == before
        assert not runtime._extraction_cache
        runtime.assert_storage_available()


def llm_chat(monkeypatch, chat):
    from src.apps.comic_gen import llm_adapter
    adapter = Mock()
    adapter.chat.side_effect = chat
    adapter.init_kwargs = []

    def adapter_factory(*args, **kwargs):
        assert not args
        assert kwargs.get("env") is not None
        adapter.init_kwargs.append(kwargs)
        return adapter

    monkeypatch.setattr(llm_adapter, "LLMAdapter", adapter_factory)
    return adapter


@pytest.mark.parametrize("endpoint", ["next_hook", "previous_episode/summary"])
def test_context_generation_saves_hash_from_its_input_and_preserves_other_fields(runtime, monkeypatch, endpoint):
    adapter = llm_chat(monkeypatch, lambda **kwargs: "  generated context  ")
    before = runtime.read_committed_script("p").model_dump()
    app = api.create_app(data_dir=runtime.data_dir, load_config=False, pipeline_instance=runtime)
    with TestClient(app) as client:
        response = client.post(f"/projects/p/{endpoint}")
        assert response.status_code == 200, response.text
        current = runtime.read_committed_script("p")
        prefix = "next_hook" if endpoint == "next_hook" else "last_episode_summary"
        text = before["original_text"] if endpoint == "next_hook" else "prev original"
        assert getattr(current, f"{prefix}_cache") == "generated context"
        assert getattr(current, f"{prefix}_revision") == api._prev_text_revision(text)
        assert current.revision == before["revision"] + 1
        for field, value in before.items():
            if field not in {"revision", "updated_at", f"{prefix}_cache", f"{prefix}_revision"}:
                assert current.model_dump()[field] == value
        assert text in adapter.chat.call_args.kwargs["messages"][0]["content"]
        assert adapter.chat.call_count == 1
    restored = module.ComicGenPipeline(data_dir=runtime.data_dir, script_processor=Mock())
    try:
        assert restored.read_committed_script("p").model_dump() == current.model_dump()
    finally:
        restored.close()


@pytest.mark.parametrize("endpoint", ["next_hook", "previous_episode/summary"])
@pytest.mark.parametrize("outcome", ["error", "empty", "closed", "failed_storage", "concurrent_edit"])
def test_context_failure_and_unlocked_concurrent_commands_do_not_publish_stale_fields(runtime, monkeypatch, endpoint, outcome):
    before = runtime.read_committed_script("p").model_dump()
    def chat(**kwargs):
        if outcome == "error":
            raise RuntimeError("synthetic LLM error")
        if outcome == "empty":
            return "   "
        if outcome == "closed":
            run_other_thread(runtime.close)
        if outcome == "concurrent_edit":
            run_other_thread(lambda: runtime.update_script_text("p", "new text", expected_revision=0))
        return "generated context"
    adapter = llm_chat(monkeypatch, chat)
    if outcome == "failed_storage":
        monkeypatch.setattr(runtime._storage, "commit", Mock(side_effect=StorageError("disk full")))
    app = api.create_app(data_dir=runtime.data_dir, load_config=False, pipeline_instance=runtime)
    with TestClient(app) as client:
        response = client.post(f"/projects/p/{endpoint}")
        expected = 409 if outcome == "concurrent_edit" else 503 if outcome in {"closed", "failed_storage"} else 502
        assert response.status_code == expected, response.text
        current = runtime.scripts["p"]
        assert current.next_hook_cache is None and current.last_episode_summary_cache is None
        if outcome != "concurrent_edit":
            assert current.model_dump() == before
        assert adapter.chat.call_count == 1


@pytest.mark.parametrize("endpoint", ["next_hook", "previous_episode/summary"])
def test_unrelated_project_edit_can_finish_during_context_generation(runtime, monkeypatch, endpoint):
    runtime.scripts["unrelated"] = parsed("unrelated")
    runtime.scripts["unrelated"].id = "unrelated"
    runtime._save_data()
    def chat(**kwargs):
        run_other_thread(lambda: runtime.update_script_text("unrelated", "changed independently", expected_revision=0))
        return "generated context"
    llm_chat(monkeypatch, chat)
    app = api.create_app(data_dir=runtime.data_dir, load_config=False, pipeline_instance=runtime)
    with TestClient(app) as client:
        response = client.post(f"/projects/p/{endpoint}")
        assert response.status_code == 200, response.text
        assert runtime.read_committed_script("unrelated").original_text == "changed independently"


def test_preview_reads_committed_settings_and_does_not_save(runtime):
    before = (runtime.data_dir / "projects.json").read_bytes()
    runtime.scripts["p"].title = "uncommitted title from another operation"
    runtime.scripts["p"].prompt_config.entity_extraction = "uncommitted prompt"
    runtime.script_processor.parse_novel.return_value = parsed("preview text")
    runtime.extract_preview("p", "preview text")
    runtime.script_processor.parse_novel.assert_called_once_with("p", "preview text", "")
    assert (runtime.data_dir / "projects.json").read_bytes() == before


@pytest.mark.parametrize("endpoint,setup,status", [
    ("next_hook", "missing", 404),
    ("next_hook", "empty", 400),
    ("previous_episode/summary", "missing", 404),
    ("previous_episode/summary", "standalone", 400),
    ("previous_episode/summary", "empty_previous", 400),
    ("previous_episode/summary", "missing_series", 404),
])
def test_context_preconditions_do_not_call_llm(runtime, monkeypatch, endpoint, setup, status):
    if setup == "missing":
        runtime.delete_project("p")
    elif setup == "empty":
        runtime.update_script_text("p", "  ", expected_revision=0)
    elif setup == "empty_previous":
        runtime.update_script_text("prev", "  ", expected_revision=0)
    elif setup == "standalone":
        runtime.remove_episode_from_series("s", "p")
    else:
        del runtime.series_store["s"]
        runtime._save_series_data()
    adapter = llm_chat(monkeypatch, lambda **kwargs: "must not call")
    app = api.create_app(data_dir=runtime.data_dir, load_config=False, pipeline_instance=runtime)
    with TestClient(app) as client:
        response = client.post(f"/projects/p/{endpoint}")
        assert response.status_code == status, response.text
        adapter.chat.assert_not_called()


@pytest.mark.parametrize("endpoint,request_field,cache_prefix", [
    ("next_hook", "hook", "next_hook"),
    ("last_episode_summary", "ai_summary", "last_episode_summary"),
])
def test_manual_context_edit_and_clear_preserve_http_shape(runtime, monkeypatch, endpoint, request_field, cache_prefix):
    adapter = llm_chat(monkeypatch, lambda **kwargs: "must not call")
    app = api.create_app(data_dir=runtime.data_dir, load_config=False, pipeline_instance=runtime)
    with TestClient(app) as client:
        for text in ("  user edit  ", None):
            response = client.put(f"/projects/p/{endpoint}", json={request_field: text})
            assert response.status_code == 200, response.text
            current = runtime.read_committed_script("p")
            assert getattr(current, f"{cache_prefix}_cache") == ("user edit" if text else None)
            source_text = "p original" if endpoint == "next_hook" else "prev original"
            assert getattr(current, f"{cache_prefix}_revision") == (api._prev_text_revision(source_text) if text else None)
            if endpoint == "next_hook":
                assert response.json() == {"hook": "user edit" if text else None, "stale": False}
            else:
                assert response.json()["id"] == "p"
        adapter.chat.assert_not_called()
