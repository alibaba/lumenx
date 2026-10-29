"""Dialogue commands use real AudioGenerator, with only the TTS transport replaced."""
import json
from pathlib import Path
from threading import Thread
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from src.apps.comic_gen import api, audio, pipeline as module
from src.apps.comic_gen.models import Character, CustomVoice, DialogueStructured, GenerationStatus, Script, Series, StoryboardFrame
from src.utils.json_storage import StorageConflictError, StorageError
from src.audio.tts import TTSProcessor


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    for name in ("AssetGenerator", "StoryboardGenerator", "VideoGenerator", "ExportManager"):
        monkeypatch.setattr(module, name, Mock())
    transport = Mock()
    # The real preparation contract stays active; only the final file-producing
    # transport call is fake, with an inspectable hook for A1 race tests.
    processor = TTSProcessor(model="cosyvoice-v3-flash")
    transport.prepare.side_effect = processor.prepare
    def execute(prepared, output_path):
        values = prepared.sdk_kwargs()
        return transport.transport_call(
            prepared.text, output_path, voice=prepared.voice,
            speech_rate=values.get("speech_rate"), pitch_rate=values.get("pitch_rate"),
            volume=values.get("volume"), instructions=values.get("instruction", values.get("instructions")),
            model_override=prepared.model, family_override=prepared.family,
        )
    transport.execute.side_effect = execute
    def synthesize(text, output_path, **kwargs):
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(f"audio:{text}:{transport.transport_call.call_count}".encode())
        return output_path, 0, "fake-request"
    transport.transport_call.side_effect = synthesize
    tts_constructor_calls = []

    def fake_tts_processor(**kwargs):
        tts_constructor_calls.append(kwargs)
        return transport

    monkeypatch.setattr(audio, "TTSProcessor", fake_tts_processor)
    item = module.ComicGenPipeline(data_dir=tmp_path / "data", script_processor=Mock())
    item._tts_constructor_calls = tts_constructor_calls
    item.scripts["p"] = Script(
        id="p", title="project", original_text="source", created_at=1, updated_at=1, series_id="s",
        characters=[Character(id="c", name="Hero", description="character", voice_id="voice", voice_speed=1.3, voice_pitch=0.8, voice_volume=37)],
        frames=[StoryboardFrame(id="f", scene_id="", character_ids=["c"], action_description="speaks", dialogue="hello")],
    )
    item.series_store["s"] = Series(id="s", title="series", created_at=1, updated_at=1, episode_ids=["p"])
    item._commit_storage("projects.json", "series.json", "library_assets.json")
    yield item
    item.close()


def move_speaker(runtime, source):
    if source != "episode":
        character = runtime.scripts["p"].characters.pop()
        (runtime.series_store["s"] if source == "series" else runtime.library_store).characters.append(character)
        runtime._commit_storage("projects.json", "series.json", "library_assets.json")


def invoke(client, entry):
    if entry == "batch":
        return client.post("/projects/p/dialogue_audio/batch")
    return client.post("/projects/p/frames/f/audio", json={})


def threaded(command):
    errors = []
    def run():
        try:
            command()
        except BaseException as exc:
            errors.append(exc)
    thread = Thread(target=run, daemon=True)
    thread.start()
    thread.join(3)
    assert not thread.is_alive(), "TTS must not hold the command lock"
    if errors:
        raise errors[0]


@pytest.mark.parametrize("source", ["episode", "series", "global"])
@pytest.mark.parametrize("entry", ["line", "batch"])
def test_shared_speaker_generation_uses_real_generator_and_persisted_audio(runtime, source, entry):
    move_speaker(runtime, source)
    app = api.create_app(data_dir=runtime.data_dir, load_config=False, pipeline_instance=runtime)
    with TestClient(app) as client:
        response = invoke(client, entry)
        assert response.status_code == 200, response.text
        frame = runtime.read_committed_script("p").frames[0]
        assert frame.audio_url and frame.dialogue_voice_id == "voice" and frame.dialogue_text_hash
        assert (runtime.data_dir / frame.audio_url).read_bytes().startswith(b"audio:hello:")
        args = runtime.audio_generator.tts.transport_call.call_args.kwargs
        assert (args["speech_rate"], args["pitch_rate"], args["volume"]) == (1.3, 0.8, 37)
        assert runtime._tts_constructor_calls[-1] == {"env": runtime.environment_snapshot()}
        if entry == "batch":
            assert response.json()["_batch_stats"] == {"generated": 1, "skipped": 0, "failed": 0, "no_voice": 0}
        if source != "episode":
            assert runtime.read_committed_script("p").characters == []
    runtime.audio_generator.tts.transport_call.assert_called_once()


@pytest.mark.parametrize("structured", [False, True])
def test_speaker_name_fallback_and_structured_dialogue_are_shared(runtime, structured):
    move_speaker(runtime, "series")
    frame = runtime.scripts["p"].frames[0]
    frame.character_ids = ["missing"]
    if structured:
        frame.dialogue = "legacy"
        frame.dialogue_structured = DialogueStructured(speaker="HERO", line="structured text", emotion="calm")
    else:
        frame.speaker = "hero"
    runtime._save_data()
    runtime.generate_dialogue_line("p", "f")
    call = runtime.audio_generator.tts.transport_call.call_args
    assert call is not None
    assert call.args[0] == ("structured text" if structured else "hello")
    assert call.kwargs["instructions"] == ("情绪：calm" if structured else None)


def test_local_speaker_masks_series_and_global_same_id(runtime):
    runtime.series_store["s"].characters.append(Character(id="c", name="Series Hero", description="series", voice_id="series-voice"))
    runtime.library_store.characters.append(Character(id="c", name="Global Hero", description="global", voice_id="global-voice"))
    runtime._commit_storage("series.json", "library_assets.json")
    runtime.generate_dialogue_line("p", "f", speed=1.7, pitch=1.1, volume=0, instructions="explicit")
    args = runtime.audio_generator.tts.transport_call.call_args.kwargs
    assert args["voice"] == "voice" and args["speech_rate"] == 1.7 and args["pitch_rate"] == 1.1 and args["volume"] == 0
    assert args["instructions"] == "explicit"


def add_custom_voice(runtime):
    runtime.series_store["other"] = Series(id="other", title="other", created_at=1, updated_at=1,
        custom_voices=[CustomVoice(id="voice", label="custom", origin="clone", target_model="cosyvoice-v3.5-plus", family="cosyvoice")])
    runtime._save_series_data()


def test_custom_voice_in_another_series_is_frozen_and_forwarded(runtime):
    add_custom_voice(runtime)
    runtime.generate_dialogue_line("p", "f")
    args = runtime.audio_generator.tts.transport_call.call_args.kwargs
    assert args["model_override"] == "cosyvoice-v3.5-plus" and args["family_override"] == "cosyvoice"


@pytest.mark.parametrize("entry", ["line", "batch"])
@pytest.mark.parametrize("change", ["edit", "delete_frame", "delete_project", "voice", "custom_voice", "close", "storage"])
def test_late_audio_cannot_modify_changed_or_unavailable_state(runtime, monkeypatch, entry, change):
    move_speaker(runtime, "series")
    add_custom_voice(runtime)
    if entry == "batch":
        runtime.scripts["p"].frames.append(StoryboardFrame(id="f2", scene_id="", character_ids=["c"], action_description="next", dialogue="second"))
        runtime._save_data()
    before = runtime.read_committed_script("p").model_dump()
    calls = []
    def synthesize(text, output_path, **kwargs):
        assert runtime.scripts["p"].frames[0].status == GenerationStatus.PENDING
        path = Path(output_path)
        path.write_bytes(b"paid result retained")
        calls.append(path)
        if change == "edit":
            threaded(lambda: runtime.update_frame("p", "f", expected_revision=runtime.read_committed_script("p").revision, dialogue="new user dialogue"))
        elif change == "delete_frame":
            threaded(lambda: runtime.delete_frame("p", "f"))
        elif change == "delete_project":
            threaded(lambda: runtime.delete_project("p"))
        elif change == "voice":
            threaded(lambda: runtime.bind_voice("p", "c", "new-voice", "New"))
        elif change == "custom_voice":
            def update():
                runtime.series_store["other"].custom_voices[0].target_model = "new-model"
                runtime._save_series_data()
            threaded(update)
        elif change == "close":
            threaded(runtime.close)
        return output_path, 0, "fake"
    runtime.audio_generator.tts.transport_call.side_effect = synthesize
    if change == "storage":
        monkeypatch.setattr(runtime._storage, "commit", Mock(side_effect=StorageError("disk full")))
    app = api.create_app(data_dir=runtime.data_dir, load_config=False, pipeline_instance=runtime)
    with TestClient(app) as client:
        response = invoke(client, entry)
        status = 404 if change == "delete_project" else 503 if change in {"close", "storage"} else 409
        assert response.status_code in ({404, 409} if change == "delete_frame" else {status}), response.text
        assert len(calls) == 1 and calls[0].read_bytes() == b"paid result retained"
        if change in {"close", "storage"}:
            assert runtime.scripts["p"].model_dump() == before
        elif change == "edit":
            assert runtime.scripts["p"].frames[0].dialogue == "new user dialogue"
        if change not in {"delete_frame", "delete_project"}:
            assert runtime.scripts["p"].frames[0].audio_url is None
    assert runtime.audio_generator.tts.transport_call.call_count == 1


@pytest.mark.parametrize("change", ["shared_description", "series_title", "custom_label", "bound_rate"])
def test_explicit_line_does_not_conflict_on_unused_shared_fields(runtime, change):
    move_speaker(runtime, "series")
    add_custom_voice(runtime)
    synthesize = runtime.audio_generator.tts.transport_call.side_effect
    def modified(*args, **kwargs):
        if change == "shared_description":
            runtime.update_asset_attributes("p", "c", "character", {"description": "new visual"})
        elif change == "series_title":
            runtime.update_series("s", {"title": "new title"})
        elif change == "custom_label":
            runtime.series_store["other"].custom_voices[0].label = "new label"
            runtime._save_series_data()
        else:
            runtime.update_voice_params("p", "c", speed=1.9, pitch=0.8, volume=37)
        return synthesize(*args, **kwargs)
    runtime.audio_generator.tts.transport_call.side_effect = modified
    result = runtime.generate_dialogue_line("p", "f", speed=1.2)
    assert result.frames[0].audio_url


@pytest.mark.parametrize("failure", ["none", "conflict", "storage"])
def test_new_audio_never_overwrites_a_previous_committed_file(runtime, monkeypatch, failure):
    first = runtime.generate_dialogue_line("p", "f")
    first_path = runtime.data_dir / first.frames[0].audio_url
    original = first_path.read_bytes()
    synthesize = runtime.audio_generator.tts.transport_call.side_effect
    def second(*args, **kwargs):
        value = synthesize(*args, **kwargs)
        if failure == "conflict":
            runtime.update_frame("p", "f", expected_revision=runtime.read_committed_script("p").revision, dialogue="changed")
        return value
    runtime.audio_generator.tts.transport_call.side_effect = second
    if failure == "storage":
        monkeypatch.setattr(runtime._storage, "commit", Mock(side_effect=StorageError("disk full")))
    if failure != "none":
        with pytest.raises(StorageError):
            runtime.generate_dialogue_line("p", "f")
    else:
        result = runtime.generate_dialogue_line("p", "f")
        assert result.frames[0].audio_url != first.frames[0].audio_url
    assert first_path.read_bytes() == original
    paths = [Path(call.args[1]) for call in runtime.audio_generator.tts.transport_call.call_args_list]
    assert len(set(paths)) == 2 and all(path.exists() for path in paths)


def test_batch_failed_result_is_not_counted_as_generated(runtime):
    runtime.scripts["p"].frames.append(StoryboardFrame(id="f2", scene_id="", character_ids=["c"], action_description="next", dialogue="second"))
    runtime._save_data()
    synthesize = runtime.audio_generator.tts.transport_call.side_effect
    def sometimes(text, path, **kwargs):
        if text == "hello":
            raise RuntimeError("provider unavailable")
        return synthesize(text, path, **kwargs)
    runtime.audio_generator.tts.transport_call.side_effect = sometimes
    app = api.create_app(data_dir=runtime.data_dir, load_config=False, pipeline_instance=runtime)
    with TestClient(app) as client:
        response = invoke(client, "batch")
        assert response.status_code == 200
        assert response.json()["_batch_stats"] == {"generated": 1, "skipped": 0, "failed": 1, "no_voice": 0}
        frames = runtime.read_committed_script("p").frames
        assert frames[0].audio_error and frames[0].status == GenerationStatus.FAILED
        assert frames[1].audio_url


def test_batch_skips_existing_audio_and_keeps_frozen_membership(runtime, monkeypatch):
    runtime.generate_dialogue_line("p", "f")
    runtime.scripts["p"].frames.append(StoryboardFrame(id="f2", scene_id="", character_ids=["c"], action_description="next", dialogue="second"))
    runtime._save_data()
    runtime.audio_generator.tts.transport_call.reset_mock()
    original = module.prepared_dialogue_is_stale
    changed = False
    def stale(frame, speaker):
        nonlocal changed
        if not changed:
            changed = True
            runtime.scripts["p"].frames.append(StoryboardFrame(id="late", scene_id="", character_ids=["c"], action_description="late", dialogue="late"))
            runtime._save_data()
        return original(frame, speaker)
    monkeypatch.setattr(module, "prepared_dialogue_is_stale", stale)
    app = api.create_app(data_dir=runtime.data_dir, load_config=False, pipeline_instance=runtime)
    with TestClient(app) as client:
        response = invoke(client, "batch")
        assert response.status_code == 200, response.text
        assert response.json()["_batch_stats"] == {"generated": 1, "skipped": 1, "failed": 0, "no_voice": 0}
        assert runtime.read_committed_script("p").frames[-1].audio_url is None
    assert runtime.audio_generator.tts.transport_call.call_count == 1


def test_missing_voice_is_counted_without_calling_tts(runtime):
    runtime.scripts["p"].characters[0].voice_id = None
    runtime._save_data()
    app = api.create_app(data_dir=runtime.data_dir, load_config=False, pipeline_instance=runtime)
    with TestClient(app) as client:
        response = invoke(client, "batch")
        assert response.json()["_batch_stats"] == {"generated": 0, "skipped": 0, "failed": 0, "no_voice": 1}
    runtime.audio_generator.tts.transport_call.assert_not_called()


@pytest.mark.parametrize("target", ["project", "frame"])
def test_missing_target_is_404_before_generation(runtime, target):
    app = api.create_app(data_dir=runtime.data_dir, load_config=False, pipeline_instance=runtime)
    with TestClient(app) as client:
        path = "/projects/missing/frames/f/audio" if target == "project" else "/projects/p/frames/missing/audio"
        assert client.post(path, json={}).status_code == 404
    runtime.audio_generator.tts.transport_call.assert_not_called()


def test_result_is_detached_and_survives_restart(runtime):
    result = runtime.generate_dialogue_line("p", "f")
    expected = runtime.read_committed_script("p").model_dump()
    result.frames[0].audio_url = "response mutation"
    assert runtime.scripts["p"].model_dump() == expected
    runtime.close()
    restored = module.ComicGenPipeline(data_dir=runtime.data_dir, script_processor=Mock())
    try:
        assert restored.read_committed_script("p").model_dump() == expected
    finally:
        restored.close()


@pytest.mark.parametrize("parameter,value", [("speed", 1.8), ("pitch", 1.2), ("volume", 65)])
def test_batch_rejects_bound_parameter_change_during_tts(runtime, parameter, value):
    move_speaker(runtime, "series")
    synthesize = runtime.audio_generator.tts.transport_call.side_effect

    def changed(*args, **kwargs):
        result = synthesize(*args, **kwargs)
        params = {"speed": 1.3, "pitch": 0.8, "volume": 37, parameter: value}
        threaded(lambda: runtime.update_voice_params("p", "c", **params))
        return result

    runtime.audio_generator.tts.transport_call.side_effect = changed
    with pytest.raises(StorageConflictError):
        runtime.generate_dialogue_audio_batch("p")
    assert runtime.read_committed_script("p").frames[0].audio_url is None
    path = Path(runtime.audio_generator.tts.transport_call.call_args.args[1])
    assert path.exists()


@pytest.mark.parametrize("entry", ["line", "batch"])
def test_closed_runtime_rejects_before_mutation_or_tts(runtime, entry):
    before = runtime.scripts["p"].model_dump()
    runtime.close()
    with pytest.raises(StorageError):
        if entry == "batch":
            runtime.generate_dialogue_audio_batch("p")
        else:
            runtime.generate_dialogue_line("p", "f")
    assert runtime.scripts["p"].model_dump() == before
    runtime.audio_generator.tts.transport_call.assert_not_called()


def test_empty_dialogue_is_a_detached_noop_without_new_revision(runtime):
    runtime.update_frame("p", "f", expected_revision=runtime.read_committed_script("p").revision, dialogue="")
    before = runtime.read_committed_script("p").model_dump()
    result = runtime.generate_dialogue_line("p", "f")
    assert result.model_dump() == before
    result.title = "caller edit"
    assert runtime.read_committed_script("p").model_dump() == before
    runtime.audio_generator.tts.transport_call.assert_not_called()


def test_batch_retries_previous_failure_even_when_old_audio_hash_matches(runtime):
    first = runtime.generate_dialogue_line("p", "f")
    first_path = runtime.data_dir / first.frames[0].audio_url
    synthesize = runtime.audio_generator.tts.transport_call.side_effect
    runtime.audio_generator.tts.transport_call.side_effect = RuntimeError("temporary failure")
    failed = runtime.generate_dialogue_line("p", "f")
    assert failed.frames[0].audio_error and failed.frames[0].audio_url == first.frames[0].audio_url
    runtime.audio_generator.tts.transport_call.side_effect = synthesize
    result, stats, errors = runtime.generate_dialogue_audio_batch("p")
    assert errors == []
    assert stats == {"generated": 1, "skipped": 0, "failed": 0, "no_voice": 0}
    assert result.frames[0].audio_url != first.frames[0].audio_url
    assert first_path.exists() and not result.frames[0].audio_error


def test_audio_commit_preserves_existing_non_audio_media_and_selections(runtime):
    frame = runtime.scripts["p"].frames[0]
    frame.video_url = "video/selected.mp4"
    frame.selected_video_id = "selected-take"
    frame.dubbed_video_url = "video/previous-dub.mp4"
    frame.preview_video_url = "video/preview.mp4"
    runtime._save_data()
    result = runtime.generate_dialogue_line("p", "f").frames[0]
    assert result.audio_url
    assert result.video_url == "video/selected.mp4"
    assert result.selected_video_id == "selected-take"
    assert result.dubbed_video_url == "video/previous-dub.mp4"
    assert result.preview_video_url == "video/preview.mp4"


@pytest.mark.parametrize("entry", ["line", "batch"])
def test_exact_speaker_name_wins_over_an_earlier_partial_match(runtime, entry):
    frame = runtime.scripts["p"].frames[0]
    frame.character_ids = []
    frame.speaker = "Anna"
    runtime.scripts["p"].characters = [
        Character(id="partial", name="Annabelle", description="", voice_id="partial-voice"),
        Character(id="exact", name="Anna", description="", voice_id="exact-voice"),
    ]
    runtime._save_data()
    app = api.create_app(data_dir=runtime.data_dir, load_config=False, pipeline_instance=runtime)
    with TestClient(app) as client:
        assert invoke(client, entry).status_code == 200
    assert runtime.audio_generator.tts.transport_call.call_args.kwargs["voice"] == "exact-voice"


@pytest.mark.parametrize("entry", ["line", "batch"])
@pytest.mark.parametrize("blank", ["speaker", "character"])
def test_blank_names_never_pick_an_arbitrary_voice(runtime, entry, blank):
    frame = runtime.scripts["p"].frames[0]
    frame.character_ids = []
    frame.speaker = "   " if blank == "speaker" else "unknown"
    if blank == "character":
        runtime.scripts["p"].characters[0].name = "   "
    runtime._save_data()
    app = api.create_app(data_dir=runtime.data_dir, load_config=False, pipeline_instance=runtime)
    with TestClient(app) as client:
        response = invoke(client, entry)
        assert response.status_code == (200 if entry == "batch" else 422)
        if entry == "batch":
            assert response.json()["_batch_stats"]["no_voice"] == 1
    runtime.audio_generator.tts.transport_call.assert_not_called()


@pytest.mark.parametrize("payload", [
    {"speed": "NaN"}, {"speed": "Infinity"}, {"pitch": "-Infinity"}, {"instructions": "bad\ud800"},
])
def test_invalid_json_generation_input_is_rejected_without_tts_or_storage_failure(runtime, payload):
    before = runtime.read_committed_script("p").model_dump()
    app = api.create_app(data_dir=runtime.data_dir, load_config=False, pipeline_instance=runtime)
    with TestClient(app) as client:
        response = client.post(
            "/projects/p/frames/f/audio", content=json.dumps(payload),
            headers={"content-type": "application/json"},
        )
        assert response.status_code == 422
        runtime.assert_storage_available()
        assert runtime.read_committed_script("p").model_dump() == before
    runtime.audio_generator.tts.transport_call.assert_not_called()


@pytest.mark.parametrize("entry", ["line", "batch"])
def test_invalid_tts_error_output_does_not_poison_storage(runtime, entry):
    if entry == "batch":
        runtime.scripts["p"].frames.append(StoryboardFrame(
            id="f2", scene_id="", character_ids=["c"], action_description="next", dialogue="second",
        ))
        runtime._save_data()
    before = runtime.read_committed_script("p").frames[0].model_dump()
    synthesize = runtime.audio_generator.tts.transport_call.side_effect

    def invalid_first(text, path, **kwargs):
        if text == "hello":
            raise RuntimeError("bad provider error\ud800")
        return synthesize(text, path, **kwargs)

    runtime.audio_generator.tts.transport_call.side_effect = invalid_first
    app = api.create_app(data_dir=runtime.data_dir, load_config=False, pipeline_instance=runtime)
    with TestClient(app) as client:
        response = invoke(client, entry)
        assert response.status_code == (200 if entry == "batch" else 502), response.text
        runtime.assert_storage_available()
        assert runtime.read_committed_script("p").frames[0].model_dump() == before
        if entry == "batch":
            assert response.json()["_batch_stats"] == {"generated": 1, "skipped": 0, "failed": 1, "no_voice": 0}
