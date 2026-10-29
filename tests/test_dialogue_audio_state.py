"""Read-only audio freshness uses the real preparation path, never synthesis."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.apps.comic_gen import api
from src.apps.comic_gen.models import CustomVoice, Series, StoryboardFrame
from src.utils.json_storage import StorageError
from tests.test_dialogue_audio_parameters import line, move_speaker, runtime as audio_runtime

runtime = audio_runtime


def read(runtime):
    item, client, state = runtime
    before = {p.name: p.read_bytes() for p in item.data_dir.glob("*.json")}
    calls = len(state.calls)
    response = client.get("/projects/p/frames/f/audio/state")
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    assert len(state.calls) == calls
    assert before == {p.name: p.read_bytes() for p in item.data_dir.glob("*.json")}
    payload = response.json()
    assert payload["project_id"] == "p" and payload["frame_id"] == "f"
    expected = client.get("/projects/p").json()["read_versions"]
    assert payload["read_versions"] == expected
    return payload


def test_missing_audio_is_read_only_with_resolved_inheritance(runtime):
    result = read(runtime)
    assert result["freshness"] == "missing"
    assert result["options"] == {"speed": None, "pitch": None, "volume": None, "instructions": None}
    assert result["resolved_request"]["speed"] == 1.3
    assert result["sources"]["speed"] == "character_binding"
    assert result["prepared"]["parameters"]["speech_rate"] == 1.3
    assert result["historical_instructions"] is None


@pytest.mark.parametrize("source", ["episode", "series", "global"])
def test_shared_voice_changes_are_stale_without_new_payment(runtime, source):
    item, _, _ = runtime
    move_speaker(item, source)
    line(runtime)
    assert read(runtime)["freshness"] == "current"
    item.update_voice_params("p", "c", speed=1.8, pitch=0.8, volume=37)
    assert read(runtime)["freshness"] == "stale"


@pytest.mark.parametrize("voice,before,after", [("longanyang", 2.2, 2.8), ("Cherry", 1.3, 1.9)])
def test_actual_equivalence_remains_current(runtime, voice, before, after):
    item, _, _ = runtime
    character = item.scripts["p"].characters[0]
    character.voice_id = voice
    character.voice_speed = before
    item._save_data()
    line(runtime)
    item.update_voice_params("p", "c", speed=after, pitch=0.8, volume=37)
    assert read(runtime)["freshness"] == "current"


@pytest.mark.parametrize("change,reason", [("legacy", "snapshot_missing"), ("schema", "snapshot_version_unknown"),
    ("rules", "rules_version_unknown"), ("url", "snapshot_media_mismatch")])
def test_unverifiable_history_is_unknown(runtime, change, reason):
    item, _, _ = runtime
    line(runtime)
    frame = item.scripts["p"].frames[0]
    if change == "legacy":
        frame.dialogue_audio_snapshot = None
    elif change == "schema":
        frame.dialogue_audio_snapshot = StoryboardFrame.model_validate({**frame.model_dump(),
            "dialogue_audio_snapshot": {"schema_version": 20, "future_payload": {"keep": True}}}).dialogue_audio_snapshot
    elif change == "rules":
        frame.dialogue_audio_snapshot.prepared["rules_version"] = "future-rules"
    else:
        frame.audio_url = "audio/different.mp3"
    item._save_data()
    result = read(runtime)
    assert (result["freshness"], result["reason"]) == ("unknown", reason)


def test_custom_voice_in_other_series_is_checked_each_read(runtime):
    item, _, _ = runtime
    item.scripts["p"].characters[0].voice_id = "custom-voice"
    voice = CustomVoice(id="custom-voice", label="custom", origin="clone", target_model="cosyvoice-v3.5-plus", family="cosyvoice")
    item.series_store["other"] = Series(id="other", title="Other", created_at=1, updated_at=1, custom_voices=[voice])
    item._commit_storage("projects.json", "series.json")
    line(runtime)
    first = read(runtime)
    assert first["freshness"] == "current"
    voice.target_model = "cosyvoice-v3-flash"
    item._save_series_data()
    second = read(runtime)
    assert second["read_versions"] == first["read_versions"], "project vector does not cover another series"
    assert second["freshness"] == "stale"


@pytest.mark.parametrize("problem,reason", [("speaker", "speaker_missing"), ("voice", "voice_missing"),
    ("tts", "tts_unavailable"), ("text", "dialogue_missing"), ("failed", "generation_failed")])
def test_unavailable_inputs_are_explicit_without_synthesis(runtime, problem, reason):
    item, _, _ = runtime
    line(runtime)
    if problem == "speaker":
        item.scripts["p"].characters.clear()
    elif problem == "voice":
        item.scripts["p"].characters[0].voice_id = None
    elif problem == "tts":
        item.audio_generator.tts = None
    elif problem == "text":
        item.scripts["p"].frames[0].dialogue = ""
    else:
        item.scripts["p"].frames[0].audio_error = "previous attempt failed"
    item._save_data()
    result = read(runtime)
    assert result["freshness"] != "current"
    assert result["reason"] == reason


def test_unsupported_desired_option_reports_structured_problem_read_only(runtime):
    item, _, _ = runtime
    line(runtime, {"speed": 1.7})
    item.scripts["p"].characters[0].voice_id = "Cherry"
    item._save_data()
    result = read(runtime)
    assert result["freshness"] == "unknown"
    assert result["error"]["code"] == "unsupported_audio_parameters"
    assert result["error"]["fields"] == ["speed"]
    assert result["options"]["speed"] == 1.7


@pytest.mark.parametrize("path", ["/projects/missing/frames/f/audio/state", "/projects/p/frames/missing/audio/state"])
def test_missing_target_returns_404(runtime, path):
    response = runtime[1].get(path)
    assert response.status_code == 404
    assert response.json()["detail"] == ("Script not found" if "/projects/missing/" in path else "Frame not found")
    assert not runtime[2].calls


def test_read_returns_detached_state_and_obeys_storage_gate(runtime, monkeypatch):
    item, _, state = runtime
    payload = item.read_dialogue_audio_state("p", "f")
    payload["options"]["speed"] = 99
    payload["prepared"]["parameters"]["speech_rate"] = 99
    assert read(runtime)["options"]["speed"] is None
    item.close()
    monkeypatch.setattr(item.audio_generator.tts, "prepare", Mock(side_effect=AssertionError("closed cannot prepare")))
    with pytest.raises(StorageError):
        item.read_dialogue_audio_state("p", "f")
    assert not state.calls


def test_uncommitted_voice_edits_do_not_change_checked_audio(runtime):
    item, _, _ = runtime
    line(runtime)
    item.scripts["p"].characters[0].voice_speed = 1.9
    result = read(runtime)
    assert result["freshness"] == "current"
    assert result["resolved_request"]["speed"] == 1.3


def test_storage_failure_is_not_hidden_as_unknown(runtime):
    item, client, state = runtime
    item.close()
    response = client.get("/projects/p/frames/f/audio/state")
    assert response.status_code == 503
    assert "freshness" not in response.json()
    assert not state.calls


def test_oss_presentation_signing_does_not_rewrite_dialogue_options_or_actual_parameters(runtime, monkeypatch):
    item, client, state = runtime
    frame = item.scripts['p'].frames[0]
    frame.dialogue = 'lumenx/spoken-line.mp3'
    frame.dialogue_audio_options.instructions = 'lumenx/instruction.mp3'
    frame.dialogue_instructions = 'lumenx/historical-instruction.mp3'
    frame.audio_url = 'lumenx/actual-audio.mp3'
    item._save_data()
    before = {p.name: p.read_bytes() for p in item.data_dir.glob('*.json')}
    expected = item.read_dialogue_audio_state('p', 'f')
    signed = []

    def sign(value):
        signed.append(value)
        return 'https://signed.invalid/' + value

    def uploader(*, env, output_root):
        assert env is item.runtime_environment.snapshot()
        assert output_root == item.data_dir
        return SimpleNamespace(is_configured=True, sign_url_for_display=sign)

    monkeypatch.setattr(api, 'OSSImageUploader', uploader)
    response = client.get('/projects/p/frames/f/audio/state')
    assert response.status_code == 200
    data = response.json()
    assert data['audio_url'] == 'https://signed.invalid/lumenx/actual-audio.mp3'
    assert not state.calls and before == {p.name: p.read_bytes() for p in item.data_dir.glob('*.json')}
    assert data['dialogue'] == expected['dialogue']
    assert data['historical_instructions'] == 'lumenx/historical-instruction.mp3'
    assert data['options'] == expected['options']
    assert data['resolved_request'] == expected['resolved_request']
    assert data['prepared'] == expected['prepared']
    assert signed == ['lumenx/actual-audio.mp3']
