"""Pure image generation preview normalizes commands without admission or I/O."""

import pytest
from pydantic import ValidationError

from src.apps.atelier.generation_commands import (
    ManualImageCandidateBatchCommand,
    fingerprint_image_candidate_request,
    normalize_image_candidate_request,
)
from src.apps.atelier.image_preview import build_image_generation_preview
from src.apps.atelier.models import AtelierNode, AtelierProject
from src.utils.generation_intent import IntentValidationError


def image_command(**changes):
    payload = {
        "expected_revision": 0,
        "client_request_id": "image-request",
        "model_id": "wan/wan2.7-image-pro#image",
        "mode": "t2i",
        "prompt": "A quiet lunar garden",
        "media": [],
        "params": {},
    }
    payload.update(changes)
    return ManualImageCandidateBatchCommand(**payload)


def reference(node="ref", ordinal=0, media_url="uploads/ref.png"):
    return {"role": "reference_image", "ordinal": ordinal, "node_id": node, "media_url": media_url}


def project():
    return AtelierProject(
        id="p", title="Canvas", created_at=1.0, updated_at=1.0,
        nodes=[
            AtelierNode(
                id="target", project_id="p", type="image", data={"reference_node_ids": ["ref", "ref2", "audio"]},
            ),
            AtelierNode(id="ref", project_id="p", type="image", media_urls=["uploads/ref.png"]),
            AtelierNode(id="ref2", project_id="p", type="image", media_urls=["assets/atelier/ref2.webp"]),
            AtelierNode(id="loose", project_id="p", type="image", media_urls=["uploads/loose.png"]),
            AtelierNode(id="audio", project_id="p", type="audio", media_urls=["uploads/ref.wav"]),
            AtelierNode(id="foreign", project_id="other", type="image", media_urls=["uploads/foreign.png"]),
        ],
    )


def test_t2i_image_command_normalizes_explicit_params_without_media_or_io(monkeypatch):
    monkeypatch.setattr("src.apps.atelier.generation_preview.freeze_media", lambda *_a, **_k: pytest.fail("image preview froze bytes"))
    command = image_command(model_id="qwen/qwen-image-2.0#image", params={"seed": 0, "prompt_extend": False, "watermark": False, "size": "1024*1024"}, batch_size=3)

    intent = normalize_image_candidate_request(command)
    preview = build_image_generation_preview(project(), "target", command)

    assert intent.model_id == "qwen/qwen-image-2.0#image"
    assert intent.mode == "t2i"
    assert intent.params["seed"] == 0
    assert intent.params["prompt_extend"] is False
    assert intent.params["watermark"] is False
    assert intent.params["size"] == "1024*1024"
    assert preview.public_record["entrypoint"] == "atelier.provider_image.v1"
    assert preview.public_record["media"] == []
    assert preview.public_record["raw_request"]["batch_size"] == 3
    assert preview.preview_fingerprint == build_image_generation_preview(project(), "target", command).preview_fingerprint


@pytest.mark.parametrize("model_id", [
    "wan/wan2.7-image#image",
    "wan/wan2.7-image-pro#image",
    "qwen/qwen-image-2.0#image",
    "qwen/qwen-image-2.0-pro#image",
    "gpt-image/gpt-image-2#image",
])
def test_image_normalization_accepts_current_routable_image_models(model_id):
    intent = normalize_image_candidate_request(image_command(model_id=model_id))

    assert intent.model_id == model_id
    assert intent.catalog_mode == "image"


@pytest.mark.parametrize("model_id", [
    "wan/wan2.2-t2i-flash#t2i",
    "wan/wan2.5-i2i-preview#i2i",
    "wan/wan2.6-image#i2i",
])
def test_image_normalization_rejects_deprecated_or_nonroutable_catalog_entries(model_id):
    with pytest.raises(ValueError, match="runtime|active|image"):
        normalize_image_candidate_request(image_command(model_id=model_id, mode="t2i" if "t2i" in model_id else "i2i"))


def test_i2i_image_preview_binds_owned_image_refs_without_freezing_bytes(monkeypatch):
    monkeypatch.setattr("src.apps.atelier.generation_preview.freeze_media", lambda *_a, **_k: pytest.fail("image preview froze bytes"))
    command = image_command(mode="i2i", media=[reference(), reference("ref2", 1, "assets/atelier/ref2.webp")])

    preview = build_image_generation_preview(project(), "target", command)

    assert preview.intent.mode == "i2i"
    assert preview.public_record["media"] == [
        {"role": "reference_image", "ordinal": 0, "node_id": "ref", "media_url": "uploads/ref.png"},
        {"role": "reference_image", "ordinal": 1, "node_id": "ref2", "media_url": "assets/atelier/ref2.webp"},
    ]


@pytest.mark.parametrize("changes", [
    {"mode": "r2v"},
    {"model_id": "wan/wan3.0-video#video"},
    {"model_id": "unknown/image#image"},
    {"params": {"duration": 5}},
    {"params": {"seed": False}},
    {"batch_size": 0},
    {"batch_size": 7},
    {"batch_size": True},
])
def test_image_command_rejects_unsupported_model_mode_params_or_batch(changes):
    with pytest.raises((ValidationError, IntentValidationError, ValueError)):
        command = image_command(**changes)
        normalize_image_candidate_request(command)


@pytest.mark.parametrize("media", [
    [],
    [reference("loose", 0, "uploads/loose.png")],
    [reference("missing", 0, "uploads/ref.png")],
    [reference("audio", 0, "uploads/ref.wav")],
    [reference("ref", 0, "https://example.invalid/ref.png")],
    [reference("ref", 0, "uploads/ref.wav")],
    [reference("ref", 0, "uploads/ref.png"), reference("ref2", 0, "assets/atelier/ref2.webp")],
    [reference("ref2", 1, "assets/atelier/ref2.webp")],
])
def test_i2i_preview_rejects_missing_unowned_nonimage_or_noncanonical_refs(media):
    with pytest.raises(ValueError):
        build_image_generation_preview(project(), "target", image_command(mode="i2i", media=media))


def test_t2i_preview_rejects_media_and_wrong_target():
    with pytest.raises(ValueError, match="T2I"):
        build_image_generation_preview(project(), "target", image_command(media=[reference()]))
    with pytest.raises(ValueError, match="image node"):
        build_image_generation_preview(project(), "audio", image_command())


def test_image_preview_rejects_target_owned_by_another_project():
    with pytest.raises(ValueError, match="project"):
        build_image_generation_preview(project(), "foreign", image_command())


def test_image_request_fingerprint_preserves_omitted_vs_explicit_batch_one_and_params():
    omitted = image_command()
    explicit_batch = image_command(batch_size=1)
    explicit_false = image_command(params={"prompt_extend": False})

    assert fingerprint_image_candidate_request(omitted, node_id="target") != fingerprint_image_candidate_request(explicit_batch, node_id="target")
    assert fingerprint_image_candidate_request(omitted, node_id="target") != fingerprint_image_candidate_request(explicit_false, node_id="target")
