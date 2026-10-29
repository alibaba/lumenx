"""Frame refinement applies validated results only to unchanged saved inputs."""
import json
from threading import Thread
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from src.apps.comic_gen import api, pipeline as module
from src.apps.comic_gen.models import Character, Script, Series, StoryboardFrame
from src.utils.json_storage import StorageError


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    for name in ("AssetGenerator", "StoryboardGenerator", "VideoGenerator", "AudioGenerator", "ExportManager"):
        monkeypatch.setattr(module, name, Mock())
    item = module.ComicGenPipeline(data_dir=tmp_path / "data", script_processor=Mock())
    item.scripts["p"] = Script(
        id="p", title="project", original_text="original", created_at=1, updated_at=1,
        series_id="s", frames=[StoryboardFrame(id="f", scene_id="", action_description="coarse",
                                              visual_description="saved visual", character_ids=["c"])],
    )
    item.series_store["s"] = Series(id="s", title="s", created_at=1, updated_at=1, episode_ids=["p"],
        characters=[Character(id="c", name="shared", description="saved character")])
    item._commit_storage("projects.json", "series.json", "library_assets.json")
    yield item
    item.close()


def call(client, kind):
    if kind == "analyze":
        return client.post("/projects/p/storyboard/analyze", json={"text": "original"})
    if kind == "rich":
        return client.post("/projects/p/frames/f/refine")
    return client.post("/projects/p/storyboard/refine_prompt", json={"frame_id": "f", "raw_prompt": "draft"})


def result(kind):
    if kind == "analyze":
        return [{"action_summary": "old generated frame", "duration": 5}]
    if kind == "rich":
        return {"visual_description": "old generated visual"}
    return {"prompt_cn": "old generated CN", "prompt_en": "old generated EN"}


def setup_llm(runtime, kind, callback):
    name = {"analyze": "analyze_to_storyboard", "rich": "refine_frame_to_rich", "prompt": "polish_storyboard_prompt"}[kind]
    getattr(runtime.script_processor, name).side_effect = callback


@pytest.mark.parametrize("kind", ["analyze", "rich", "prompt"])
@pytest.mark.parametrize("change", ["edit", "delete_project", "delete_frame", "shared_asset"])
def test_refine_rejects_changed_inputs_before_committing(runtime, kind, change):
    def llm(*args, **kwargs):
        if change == "delete_project":
            runtime.delete_project("p")
        elif change == "delete_frame":
            runtime.delete_frame("p", "f")
        elif change == "shared_asset":
            runtime.update_asset_attributes("p", "c", "character", {"description": "new shared description"})
        else:
            runtime.update_frame("p", "f", expected_revision=runtime.read_committed_script("p").revision, visual_description="new user visual", image_prompt="new user prompt")
        return result(kind)
    setup_llm(runtime, kind, llm)
    app = api.create_app(data_dir=runtime.data_dir, load_config=False, pipeline_instance=runtime)
    with TestClient(app) as client:
        response = call(client, kind)
        # Prompt polishing consumes caller-provided assets; unlike rich/analyze,
        # a shared store change does not invalidate those explicit inputs.
        if kind == "prompt" and change == "shared_asset":
            assert response.status_code == 200
            return
        assert response.status_code in ({404} if change == "delete_project" else {404, 409}), response.text
        if change == "edit":
            frame = runtime.read_committed_script("p").frames[0]
            assert frame.visual_description == "new user visual" and frame.image_prompt == "new user prompt"
        if change == "delete_frame":
            assert runtime.read_committed_script("p").frames == []
        if change == "delete_project":
            assert "p" not in runtime.scripts


def test_invalid_rich_result_does_not_leak_partial_mutation(runtime):
    before = runtime.read_committed_script("p").model_dump(mode="json")
    runtime.script_processor.refine_frame_to_rich.return_value = {
        "visual_description": "partial bad result", "camera_movement": {"primary": []},
    }
    app = api.create_app(data_dir=runtime.data_dir, load_config=False, pipeline_instance=runtime)
    with TestClient(app) as client:
        response = call(client, "rich")
        assert response.status_code >= 400
        assert runtime.scripts["p"].model_dump(mode="json") == before


def test_rich_result_does_not_expose_live_frame(runtime):
    runtime.script_processor.refine_frame_to_rich.return_value = {"visual_description": "generated"}
    frame = runtime.refine_frame("p", "f")
    frame.visual_description = "response mutation"
    assert runtime.scripts["p"].frames[0].visual_description == "generated"


def in_other_thread(command):
    errors = []
    def run():
        try:
            command()
        except BaseException as exc:
            errors.append(exc)
    thread = Thread(target=run, daemon=True)
    thread.start()
    thread.join(timeout=3)
    assert not thread.is_alive(), "LLM and generator yields must not hold the storage lock"
    if errors:
        raise errors[0]


@pytest.mark.parametrize("kind", ["analyze", "rich", "prompt"])
@pytest.mark.parametrize("failure", ["close", "storage", "upstream", "concurrent_edit"])
def test_refinement_failures_preserve_saved_state_and_do_not_hold_the_lock(runtime, monkeypatch, kind, failure):
    before = runtime.read_committed_script("p").model_dump()
    def llm(*args, **kwargs):
        if failure == "close":
            in_other_thread(runtime.close)
        elif failure == "concurrent_edit":
            in_other_thread(lambda: runtime.update_frame("p", "f", expected_revision=runtime.read_committed_script("p").revision, visual_description="concurrent visual"))
        elif failure == "upstream":
            raise RuntimeError("synthetic upstream failure")
        return result(kind)
    setup_llm(runtime, kind, llm)
    if failure == "storage":
        monkeypatch.setattr(runtime._storage, "commit", Mock(side_effect=StorageError("disk full")))
    app = api.create_app(data_dir=runtime.data_dir, load_config=False, pipeline_instance=runtime)
    with TestClient(app) as client:
        response = call(client, kind)
        expected = 409 if failure == "concurrent_edit" else 502 if failure == "upstream" else 503
        assert response.status_code == expected, response.text
        if failure != "concurrent_edit":
            assert runtime.scripts["p"].model_dump() == before
        else:
            assert runtime.read_committed_script("p").frames[0].visual_description == "concurrent visual"


@pytest.mark.parametrize("kind", ["analyze", "rich", "prompt"])
@pytest.mark.parametrize("change", ["global_used", "unrelated_series_field", "series_prompt"])
def test_dependency_checks_track_only_effective_shared_inputs(runtime, kind, change):
    if change == "global_used":
        character = runtime.series_store["s"].characters.pop()
        runtime.library_store.characters.append(character)
        runtime._commit_storage("series.json", "library_assets.json")
    def llm(*args, **kwargs):
        if change == "global_used":
            runtime.update_asset_attributes("p", "c", "character", {"description": "global changed"})
        elif change == "unrelated_series_field":
            runtime.update_series("s", {"title": "unrelated title"})
        else:
            prompt_config = runtime.series_store["s"].prompt_config.model_copy(deep=True)
            prompt_config.storyboard_extraction = "new extraction policy"
            prompt_config.storyboard_polish = "new polish policy"
            runtime.update_series("s", {"prompt_config": prompt_config})
        return result(kind)
    setup_llm(runtime, kind, llm)
    app = api.create_app(data_dir=runtime.data_dir, load_config=False, pipeline_instance=runtime)
    with TestClient(app) as client:
        response = call(client, kind)
        conflict = (change == "global_used" and kind != "prompt") or (change == "series_prompt" and kind != "rich")
        assert response.status_code == (409 if conflict else 200), response.text


@pytest.mark.parametrize("invalid", [
    None, [], {"visual_description": []}, {"duration": {}},
    {"camera_movement": {"primary": []}}, {"dialogue_structured": {"line": []}},
    {"blocking": {"description": "valid", "stage": ["invalid"]}},
])
def test_invalid_rich_output_is_502_without_dirty_memory_or_storage_failure(runtime, invalid):
    before = runtime.read_committed_script("p").model_dump()
    runtime.script_processor.refine_frame_to_rich.return_value = invalid
    app = api.create_app(data_dir=runtime.data_dir, load_config=False, pipeline_instance=runtime)
    with TestClient(app) as client:
        response = call(client, "rich")
        assert response.status_code == 502, response.text
        assert runtime.scripts["p"].model_dump() == before
        runtime.assert_storage_available()


def test_rich_mapping_preserves_existing_semantics_and_survives_reload(runtime):
    frame = runtime.scripts["p"].frames[0]
    frame.dialogue_instructions = "historical audio instructions"
    frame.image_url, frame.video_url, frame.audio_url = "images/old.png", "videos/old.mp4", "audio/old.mp3"
    runtime._save_data()
    runtime.script_processor.refine_frame_to_rich.return_value = {
        "visual_description": "shared walks", "duration": 6, "shot_size": "close", "camera_angle": "low",
        "camera_movement": {"primary": "push_in", "speed": "slow"},
        "dialogue_structured": {"speaker": "shared", "line": "hello", "emotion": "calm", "delivery": "soft"},
        "blocking": {"description": "center", "stage": [{"ref": "shared"}]},
        "audio_note": {"sfx": "rain"}, "lighting": {"description": "warm light"}, "transition_hint": "cut",
    }
    response = runtime.refine_frame("p", "f")
    assert response.visual_description == "shared[character1:shared] walks"
    assert response.dialogue == "hello" and response.speaker == "shared"
    assert response.dialogue_instructions == "historical audio instructions"
    from src.apps.comic_gen.audio import _effective_instructions
    assert _effective_instructions(response) == "情绪：calm；演绎：soft"
    assert "warm light" in response.assembled_prompt and "shared" in response.assembled_prompt
    assert response.blocking.stage[0].zone == "center" and response.blocking.stage[0].depth == "mid"
    assert response.image_url == "images/old.png" and response.audio_url == "audio/old.mp3" and response.video_url == "videos/old.mp4"
    expected = runtime.read_committed_script("p").model_dump()
    runtime.close()
    restored = module.ComicGenPipeline(data_dir=runtime.data_dir, script_processor=Mock())
    try:
        assert restored.read_committed_script("p").model_dump() == expected
    finally:
        restored.close()


def test_empty_rich_fields_do_not_clear_existing_values(runtime):
    before = runtime.read_committed_script("p").frames[0]
    runtime.script_processor.refine_frame_to_rich.return_value = {
        "visual_description": "", "shot_size": None, "duration": 0,
        "camera_movement": {}, "dialogue_structured": {}, "blocking": {},
    }
    response = runtime.refine_frame("p", "f")
    assert response.visual_description == before.visual_description
    assert response.duration == before.duration
    assert response.camera_movement_structured == before.camera_movement_structured


@pytest.mark.parametrize("appears", [False, True])
def test_prompt_without_initial_target_returns_only_results_even_if_target_appears(runtime, appears):
    before = runtime.read_committed_script("p").revision
    def llm(*args):
        if appears:
            runtime.scripts["p"].frames.append(StoryboardFrame(id="missing", scene_id="", action_description="new frame"))
            runtime._save_data()
        return {"prompt_cn": "CN", "prompt_en": "EN"}
    runtime.script_processor.polish_storyboard_prompt.side_effect = llm
    response = runtime.refine_frame_prompt("p", "missing", "draft", [])
    assert response == {"prompt_cn": "CN", "prompt_en": "EN", "frame_updated": False}
    assert runtime.read_committed_script("p").revision == before + int(appears)
    assert all(frame.image_prompt is None for frame in runtime.scripts["p"].frames)


def batch_frames(runtime):
    runtime.scripts["p"].frames.append(StoryboardFrame(id="f2", scene_id="", action_description="second"))
    runtime._save_data()


@pytest.mark.parametrize("failure,code", [
    ("edit", "storage_conflict"), ("delete", "target_missing"),
    ("close", "storage_unavailable"), ("storage", "storage_unavailable"),
])
def test_batch_stops_after_conflict_or_missing_or_storage_error(runtime, monkeypatch, failure, code):
    batch_frames(runtime)
    def llm(*args):
        if failure == "edit":
            runtime.update_frame("p", "f", expected_revision=runtime.read_committed_script("p").revision, visual_description="user edit")
        elif failure == "delete":
            runtime.delete_project("p")
        elif failure == "close":
            runtime.close()
        return {"visual_description": "generated"}
    runtime.script_processor.refine_frame_to_rich.side_effect = llm
    if failure == "storage":
        monkeypatch.setattr(runtime._storage, "commit", Mock(side_effect=StorageError("disk full")))
    events = list(runtime.refine_batch_generator("p"))
    assert [event for event, data in events] == ["frame_refine_start", "batch_aborted"]
    assert events[-1][1]["code"] == code and events[-1][1]["total"] == 2
    assert events[-1][1]["success"] == 0
    assert runtime.script_processor.refine_frame_to_rich.call_count == 1


@pytest.mark.parametrize("failure", ["closed", "missing"])
def test_batch_initial_race_is_an_explicit_terminal_event(runtime, failure):
    generator = runtime.refine_batch_generator("p")
    if failure == "closed":
        runtime.close()
    else:
        runtime.delete_project("p")
    events = list(generator)
    assert len(events) == 1 and events[0][0] == "batch_aborted"
    assert events[0][1]["code"] == ("storage_unavailable" if failure == "closed" else "target_missing")
    runtime.script_processor.refine_frame_to_rich.assert_not_called()


def test_batch_keeps_initial_frame_list_yields_unlocked_and_continues_after_upstream_failure(runtime):
    batch_frames(runtime)
    runtime.script_processor.refine_frame_to_rich.side_effect = [RuntimeError("upstream failed"), {"visual_description": "second refined"}]
    stream = runtime.refine_batch_generator("p")
    first = next(stream)
    assert first[0] == "frame_refine_start"
    in_other_thread(lambda: runtime.add_frame("p", action_description="newly added after batch started"))
    events = [first, *stream]
    assert [name for name, data in events] == [
        "frame_refine_start", "frame_refine_error", "frame_refine_start", "frame_refine_complete", "batch_complete",
    ]
    assert events[-1][1] == {"total": 2, "success": 1, "failed": 1}
    assert runtime.script_processor.refine_frame_to_rich.call_count == 2


def test_http_batch_stream_reports_abort_without_batch_complete(runtime):
    batch_frames(runtime)
    def llm(*args):
        runtime.update_frame("p", "f", expected_revision=runtime.read_committed_script("p").revision, visual_description="user edit")
        return {"visual_description": "generated"}
    runtime.script_processor.refine_frame_to_rich.side_effect = llm
    app = api.create_app(data_dir=runtime.data_dir, load_config=False, pipeline_instance=runtime)
    with TestClient(app) as client:
        response = client.post("/projects/p/storyboard/refine_batch")
        assert response.status_code == 200
        assert "event: batch_aborted\n" in response.text
        assert "event: batch_complete\n" not in response.text
        payload = json.loads(response.text.split("event: batch_aborted\ndata: ", 1)[1].strip())
        assert payload["code"] == "storage_conflict"


@pytest.mark.parametrize("change,conflict", [
    ("unused_character", False), ("selected_image", False),
    ("selected_age", True), ("selected_scene", True),
])
def test_rich_dependency_projection_includes_prompt_assembly_inputs_only(runtime, change, conflict):
    from src.apps.comic_gen.models import Scene
    runtime.series_store["s"].characters.append(Character(id="unused", name="unused", description="before"))
    runtime.series_store["s"].scenes.append(Scene(id="scene", name="room", description="before"))
    runtime.scripts["p"].frames[0].scene_id = "scene"
    runtime._commit_storage("projects.json", "series.json")
    def llm(*args):
        if change == "unused_character":
            runtime.update_asset_attributes("p", "unused", "character", {"description": "after"})
        elif change == "selected_image":
            runtime.series_store["s"].characters[0].full_body_image_url = "new-image.png"
            runtime._save_series_data()
        elif change == "selected_age":
            runtime.update_asset_attributes("p", "c", "character", {"age": "old"})
        else:
            runtime.update_asset_attributes("p", "scene", "scene", {"description": "after"})
        return {"visual_description": "shared enters room"}
    runtime.script_processor.refine_frame_to_rich.side_effect = llm
    app = api.create_app(data_dir=runtime.data_dir, load_config=False, pipeline_instance=runtime)
    with TestClient(app) as client:
        response = call(client, "rich")
        assert response.status_code == (409 if conflict else 200), response.text


@pytest.mark.parametrize("kind,invalid", [
    ("analyze", None), ("analyze", {}), ("analyze", [{"action_summary": "valid"}, {"duration": []}]),
    ("prompt", None), ("prompt", []), ("prompt", {"prompt_cn": []}),
])
def test_other_invalid_upstream_results_are_502_and_do_not_publish_partial_results(runtime, kind, invalid):
    before = runtime.read_committed_script("p").model_dump()
    setup_llm(runtime, kind, lambda *args, **kwargs: invalid)
    app = api.create_app(data_dir=runtime.data_dir, load_config=False, pipeline_instance=runtime)
    with TestClient(app) as client:
        response = call(client, kind)
        assert response.status_code == 502, response.text
        assert runtime.scripts["p"].model_dump() == before
        runtime.assert_storage_available()


def test_analyze_retains_name_resolution_and_returns_detached_saved_script(runtime):
    from src.apps.comic_gen.models import Prop, Scene
    runtime.series_store["s"].scenes.append(Scene(id="scene", name="large room", description="room"))
    runtime.library_store.props.append(Prop(id="prop", name="Red Book", description="book"))
    runtime._commit_storage("series.json", "library_assets.json")
    def llm(text, entities, **kwargs):
        assert entities["characters"][0]["name"] == "shared"
        assert entities["props"][0]["name"] == "Red Book"
        # A provider must not own the dependency signature or resolver inputs.
        entities["characters"][0]["description"] = "provider mutation"
        return [{"action_summary": "new action", "scene_ref_name": "room",
                 "character_ref_names": ["SHARED"], "prop_ref_names": ["book"], "duration": 4}]
    runtime.script_processor.analyze_to_storyboard.side_effect = llm
    response = runtime.analyze_text_to_frames("p", "new text")
    frame = response.frames[0]
    assert frame.scene_id == "scene" and frame.character_ids == ["c"] and frame.prop_ids == ["prop"]
    assert frame.action_description == "new action" and frame.duration == 4
    revision = response.revision
    response.frames.clear()
    assert len(runtime.read_committed_script("p").frames) == 1
    assert runtime.read_committed_script("p").revision == revision


def test_prompt_does_not_let_provider_mutate_caller_assets_and_persists_bilingual_fields(runtime):
    assets = [{"id": "caller", "nested": {"name": "input"}}]
    def llm(raw, references, feedback, custom_prompt):
        references[0]["nested"]["name"] = "changed by provider"
        return {"prompt_cn": "中文", "prompt_en": "English"}
    runtime.script_processor.polish_storyboard_prompt.side_effect = llm
    response = runtime.refine_frame_prompt("p", "f", "draft", assets)
    assert response == {"prompt_cn": "中文", "prompt_en": "English", "frame_updated": True}
    assert assets[0]["nested"]["name"] == "input"
    frame = runtime.read_committed_script("p").frames[0]
    assert frame.image_prompt_cn == "中文" and frame.image_prompt_en == frame.image_prompt == "English"


@pytest.mark.parametrize("change", ["delete_frame", "close"])
def test_batch_race_after_start_stops_before_provider(runtime, change):
    stream = runtime.refine_batch_generator("p")
    assert next(stream)[0] == "frame_refine_start"
    in_other_thread(runtime.close if change == "close" else lambda: runtime.delete_frame("p", "f"))
    tail = list(stream)
    assert len(tail) == 1 and tail[0][0] == "batch_aborted"
    assert tail[0][1]["code"] == ("storage_unavailable" if change == "close" else "target_missing")
    assert tail[0][1]["failed"] == 1 and tail[0][1]["success"] == 0
    runtime.script_processor.refine_frame_to_rich.assert_not_called()


def test_http_batch_preflight_rejects_missing_or_unavailable_before_streaming(runtime):
    app = api.create_app(data_dir=runtime.data_dir, load_config=False, pipeline_instance=runtime)
    with TestClient(app) as client:
        assert client.post("/projects/missing/storyboard/refine_batch").status_code == 404
        runtime.close()
        assert client.post("/projects/p/storyboard/refine_batch").status_code == 503


def test_empty_batch_has_one_success_terminal_event(runtime):
    runtime.delete_frame("p", "f")
    assert list(runtime.refine_batch_generator("p")) == [("batch_complete", {"total": 0, "success": 0, "failed": 0})]
    runtime.script_processor.refine_frame_to_rich.assert_not_called()


@pytest.mark.parametrize("kind,empty", [
    ("prompt", {}), ("prompt", {"prompt_cn": "CN"}), ("prompt", {"prompt_en": "EN"}),
    ("prompt", {"prompt_cn": None, "prompt_en": "EN"}),
    ("prompt", {"prompt_cn": "", "prompt_en": ""}),
    ("prompt", {"prompt_cn": "  ", "prompt_en": "EN"}),
    ("rich", {}), ("rich", {"unexpected": "not a refinement"}),
    ("analyze", [{}]), ("analyze", [{"shot_size": "close"}]),
    ("analyze", [{"action_summary": ""}]),
    ("analyze", [{"action_summary": "  ", "action_description": "ignored fallback"}]),
])
def test_empty_or_incomplete_results_cannot_clear_saved_prompts_or_replace_frames(runtime, kind, empty):
    frame = runtime.scripts["p"].frames[0]
    frame.image_prompt_cn, frame.image_prompt_en, frame.image_prompt = "saved CN", "saved EN", "saved EN"
    runtime._save_data()
    before = runtime.read_committed_script("p").model_dump()
    setup_llm(runtime, kind, lambda *args, **kwargs: empty)
    app = api.create_app(data_dir=runtime.data_dir, load_config=False, pipeline_instance=runtime)
    with TestClient(app) as client:
        response = call(client, kind)
        assert response.status_code == 502, response.text
        assert runtime.scripts["p"].model_dump() == before
        runtime.assert_storage_available()
