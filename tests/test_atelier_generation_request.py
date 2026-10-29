"""Atelier admission preserves request identity before parameter defaults."""

import pytest
from pydantic import ValidationError

from src.apps.atelier.generation_commands import (
    CreateVideoCandidateCommand,
    ManualVideoCandidateBatchCommand,
    fingerprint_video_candidate_request,
    normalize_video_candidate_request,
)


def payload(**updates):
    return {
        "expected_revision": 3,
        "client_request_id": "request-1",
        "model_id": "wan/wan2.7-video#i2v",
        "mode": "i2v",
        "prompt": "Slow sunrise",
        "first_frame": {"node_id": "image-1", "media_url": "uploads/frame.png"},
        **updates,
    }


def test_identity_excludes_revision_and_request_id_but_includes_target_and_raw_defaults():
    a = CreateVideoCandidateCommand(**payload())
    b = CreateVideoCandidateCommand(**payload(expected_revision=90, client_request_id="request-2"))
    fingerprint = fingerprint_video_candidate_request(a, node_id="video-1")
    assert fingerprint_video_candidate_request(b, node_id="video-1") == fingerprint
    assert fingerprint_video_candidate_request(a, node_id="video-2") != fingerprint
    explicit = CreateVideoCandidateCommand(**payload(params={"duration": 5, "resolution": "1080p"}))
    assert normalize_video_candidate_request(explicit).params == normalize_video_candidate_request(a).params
    assert fingerprint_video_candidate_request(explicit, node_id="video-1") != fingerprint
    empty = CreateVideoCandidateCommand(**payload(params={}))
    assert fingerprint_video_candidate_request(empty, node_id="video-1") != fingerprint


def test_same_core_defaults_and_request_provenance_without_studio_frame_owner():
    command = CreateVideoCandidateCommand(**payload(params={"seed": 12, "watermark": False}))
    intent = normalize_video_candidate_request(command)
    assert intent.model_id == "wan/wan2.7-video#i2v"
    assert intent.params == {"duration": 5, "resolution": "1080p", "seed": 12, "watermark": False}
    assert intent.sources["params.duration"] == "catalog"
    assert intent.sources["params.watermark"] == "request"
    assert "params.watermark" in intent.request_fields
    assert not hasattr(command, "frame_id")


@pytest.mark.parametrize("updates", [
    {"expected_revision": True}, {"expected_revision": "3"},
    {"client_request_id": "request with spaces"}, {"scope": {}},
    {"provider_execution": {}}, {"credential_ref": "caller-choice"},
    {"prompt": None}, {"mode": "r2v"},
    {"params": None}, {"params": {"audio": True}},
    {"params": {"ratio": "16:9"}}, {"params": {"shot_type": "single"}},
    {"params": {"duration": None}}, {"params": {"duration": True}},
    {"params": {"duration": 1}}, {"params": {"duration": 16}},
    {"params": {"resolution": "480p"}}, {"params": {"seed": -2}},
    {"params": {"prompt_extend": "false"}}, {"params": {"watermark": 0}},
    {"first_frame": {"node_id": "image-1", "media_url": "uploads/frame.png", "scope": {}}},
    {"first_frame": {"node_id": " ", "media_url": "uploads/frame.png"}},
    {"first_frame": None},
])
def test_unsupported_or_ambiguous_input_is_rejected(updates):
    with pytest.raises((ValidationError, ValueError)):
        normalize_video_candidate_request(CreateVideoCandidateCommand(**payload(**updates)))


@pytest.mark.parametrize("model", ["wan/wan2.6-i2v#i2v", "wan2.6-i2v", "wan2.7-r2v"])
def test_first_admission_slice_does_not_imply_other_catalog_models_are_executable(model):
    with pytest.raises(ValueError):
        normalize_video_candidate_request(CreateVideoCandidateCommand(**payload(model_id=model)))


def test_legacy_model_alias_normalizes_but_keeps_distinct_original_request_identity():
    canonical = CreateVideoCandidateCommand(**payload())
    legacy = CreateVideoCandidateCommand(**payload(model_id="wan2.7-i2v"))
    assert normalize_video_candidate_request(canonical) == normalize_video_candidate_request(legacy)
    assert fingerprint_video_candidate_request(canonical, node_id="v") != fingerprint_video_candidate_request(legacy, node_id="v")


def test_unpaired_unicode_cannot_enter_a_request_receipt():
    with pytest.raises(ValueError):
        fingerprint_video_candidate_request(CreateVideoCandidateCommand(**payload(prompt="\ud800")), node_id="v")


def test_manual_batch_size_is_strict_and_participates_in_original_identity():
    omitted = ManualVideoCandidateBatchCommand(**payload())
    explicit_one = ManualVideoCandidateBatchCommand(**payload(batch_size=1))
    batch_two = ManualVideoCandidateBatchCommand(**payload(batch_size=2))

    old_single = CreateVideoCandidateCommand(**payload())
    assert fingerprint_video_candidate_request(omitted, node_id="video-1") == fingerprint_video_candidate_request(old_single, node_id="video-1")
    assert fingerprint_video_candidate_request(explicit_one, node_id="video-1") != fingerprint_video_candidate_request(omitted, node_id="video-1")
    assert fingerprint_video_candidate_request(batch_two, node_id="video-1") != fingerprint_video_candidate_request(explicit_one, node_id="video-1")

    for value in [0, 7, True, "2", 2.0]:
        with pytest.raises(ValidationError):
            ManualVideoCandidateBatchCommand(**payload(batch_size=value))
