"""Pure TTS generation preview normalizes commands without admission or I/O."""

import pytest
from pydantic import ValidationError

from src.apps.atelier.models import AtelierNode, AtelierProject
from src.apps.atelier.tts_commands import TTSCandidateCommand, normalize_tts_candidate_request
from src.apps.atelier.tts_preview import build_tts_generation_preview
from src.audio import tts as tts_module


def command(**changes):
    payload = {
        "expected_revision": 0,
        "client_request_id": "tts-request",
        "voice": "longanyang",
        "text": "旁白从废墟中升起。",
        "params": {},
    }
    payload.update(changes)
    return TTSCandidateCommand(**payload)


def project():
    return AtelierProject(
        id="p",
        title="Canvas",
        created_at=1.0,
        updated_at=1.0,
        nodes=[
            AtelierNode(id="audio", project_id="p", type="audio"),
            AtelierNode(id="image", project_id="p", type="image"),
            AtelierNode(id="foreign", project_id="other", type="audio"),
        ],
    )


def test_cosy_tts_preview_freezes_builtin_voice_snapshot_without_io(monkeypatch, tmp_path):
    monkeypatch.setattr(tts_module.TTSProcessor, "execute", lambda *_a, **_k: pytest.fail("preview executed TTS"))
    cmd = command(params={"speech_rate": 0.5, "pitch_rate": 2.0, "volume": 0, "instructions": "低声"})

    prepared = normalize_tts_candidate_request(cmd)
    preview = build_tts_generation_preview(project(), "audio", cmd)

    assert prepared.family == "cosyvoice"
    assert preview.public_record == {
        "schema_version": 1,
        "preview_contract_version": "atelier-tts-generation-preview-v1",
        "entrypoint": "atelier.provider_audio.v1",
        "project_id": "p",
        "node_id": "audio",
        "raw_request": {
            "client_request_id": "tts-request",
            "voice": "longanyang",
            "text": "旁白从废墟中升起。",
            "params": {"speech_rate": 0.5, "pitch_rate": 2.0, "volume": 0, "instructions": "低声"},
        },
        "prepared_tts": {
            "rules_version": "tts-parameters-v1",
            "family": "cosyvoice",
            "parameters": {
                "model": "cosyvoice-v3-flash",
                "voice": "longanyang",
                "text": "旁白从废墟中升起。",
                "speech_rate": 0.5,
                "pitch_rate": 2.0,
                "volume": 0,
                "instruction": "低声",
            },
            "adjustments": [],
            "unsupported": [],
        },
    }
    assert preview.preview_fingerprint == build_tts_generation_preview(project(), "audio", cmd).preview_fingerprint
    assert list(tmp_path.iterdir()) == []


def test_qwen_tts_preview_omitted_defaults_do_not_become_unsupported_controls():
    cmd = command(voice="Cherry", params={"instructions": "温柔但坚定"})

    preview = build_tts_generation_preview(project(), "audio", cmd)

    assert preview.prepared.family == "qwen3"
    assert preview.public_record["prepared_tts"]["parameters"] == {
        "model": "qwen3-tts-instruct-flash",
        "voice": "Cherry",
        "text": "旁白从废墟中升起。",
        "language_type": "Chinese",
        "stream": False,
        "instructions": "温柔但坚定",
        "optimize_instructions": True,
    }
    assert preview.public_record["prepared_tts"]["unsupported"] == []


@pytest.mark.parametrize("params", [
    {"speech_rate": 0.49},
    {"speech_rate": 2.01},
    {"pitch_rate": 0.49},
    {"pitch_rate": 2.01},
    {"volume": -1},
    {"volume": 101},
    {"volume": False},
    {"speech_rate": None},
    {"pitch_rate": {"value": 1}},
    {"volume": [50]},
    {"speech_rate": float("nan")},
    {"pitch_rate": float("inf")},
    {"unknown": 1},
])
def test_tts_command_rejects_unknown_null_object_bool_or_out_of_range_params(params):
    with pytest.raises((ValidationError, ValueError)):
        cmd = command(params=params)
        normalize_tts_candidate_request(cmd)


@pytest.mark.parametrize("changes", [
    {"text": "   "},
    {"voice": "custom-clone"},
    {"voice": ""},
    {"params": {"instructions": []}},
])
def test_tts_command_rejects_blank_text_custom_voice_and_bad_instructions(changes):
    with pytest.raises((ValidationError, ValueError)):
        cmd = command(**changes)
        normalize_tts_candidate_request(cmd)


@pytest.mark.parametrize("params", [
    {"speech_rate": 1.0},
    {"pitch_rate": 1.0},
    {"volume": 50},
])
def test_qwen_tts_rejects_explicit_transport_unsupported_controls(params):
    with pytest.raises(ValueError, match="unsupported"):
        normalize_tts_candidate_request(command(voice="Cherry", params=params))


def test_tts_preview_snapshot_is_detached_from_live_voice_catalog(monkeypatch):
    preview = build_tts_generation_preview(project(), "audio", command()).public_record

    monkeypatch.setitem(tts_module.VOICES["longanyang"], "model", "mutated-model")
    monkeypatch.setitem(tts_module.VOICES["longanyang"], "family", "qwen3")

    assert preview["prepared_tts"]["parameters"]["model"] == "cosyvoice-v3-flash"
    assert preview["prepared_tts"]["family"] == "cosyvoice"


def test_tts_preview_requires_owned_audio_target_node():
    with pytest.raises(ValueError, match="audio node"):
        build_tts_generation_preview(project(), "image", command())
    with pytest.raises(ValueError, match="audio node"):
        build_tts_generation_preview(project(), "missing", command())
    with pytest.raises(ValueError, match="current project"):
        build_tts_generation_preview(project(), "foreign", command())


def test_tts_preview_fingerprint_changes_with_identity_fields():
    base = build_tts_generation_preview(project(), "audio", command()).preview_fingerprint
    assert base != build_tts_generation_preview(project(), "audio", command(text="另一句台词")).preview_fingerprint
    assert base != build_tts_generation_preview(project(), "audio", command(voice="Cherry")).preview_fingerprint
    assert base != build_tts_generation_preview(project(), "audio", command(params={"volume": 0})).preview_fingerprint
