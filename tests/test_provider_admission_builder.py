"""Products construct the same durable provider identity from frozen inputs."""

from dataclasses import replace

import pytest

from src.apps.comic_gen import provider_recovery as studio
from src.models import provider_recovery as core
from src.models.provider_tasks import PreparedMedia, ProviderScope
from src.utils.generation_intent import normalize_generation_intent
from src.utils.json_storage import DirectoryWriterLock
from src.utils.media_versions import freeze_media


def _params(seed_marker):
    params = {"duration": 5, "resolution": "1080p"}
    if seed_marker != "omitted":
        params["seed"] = seed_marker
    return params


@pytest.fixture
def inputs(tmp_path):
    lease = DirectoryWriterLock(tmp_path).acquire()
    image = tmp_path / "uploads/frame.png"
    image.parent.mkdir()
    image.write_bytes(b"\x89PNG\r\n\x1a\nshared-builder-fixture")
    version = freeze_media("uploads/frame.png", output_root=tmp_path, writer_lock=lease, role="first_frame", ordinal=0, max_bytes=core.MAX_PROVIDER_MEDIA_BYTES)
    prepared = PreparedMedia.from_media_version(version, output_root=tmp_path, max_bytes=core.MAX_PROVIDER_MEDIA_BYTES)
    kwargs = dict(
        provider_model_id="wan2.7-i2v", media_version=version, prepared_media=prepared,
        scope=ProviderScope(provider="dashscope", backend="dashscope", gateway="dashscope", base_url="https://dashscope.aliyuncs.com", region="cn-beijing"),
        credential_ref="original-credential", attempt_id="attempt-1",
    )
    yield tmp_path, lease, kwargs
    lease.close()


@pytest.mark.parametrize("seed_marker", ["omitted", -1, 0])
def test_neutral_builder_preserves_studio_record_and_submit_input(inputs, seed_marker):
    root, lease, kwargs = inputs
    params = _params(seed_marker)
    intent = normalize_generation_intent(
        {"model_id": "wan2.7-i2v", "mode": "i2v", "prompt": "Sunrise", "params": params},
        allowed_modes={"i2v"}, allowed_parameters=studio._ALLOWED_INTENT_PARAMS,
    )
    request = {
        "frame_id": "frame", "image_url": "uploads/frame.png", "model": "wan2.7-i2v",
        "prompt": "Sunrise", **params,
    }
    original_submission, original_record = studio.build_accepted_submission_and_record(
        request, output_root=root, writer_lock=lease, scope=kwargs["scope"],
        credential_ref=kwargs["credential_ref"], attempt_id=kwargs["attempt_id"],
    )
    submission, record = core.build_accepted_provider_submission_and_record(
        intent, **kwargs, mode_source="entry_default", admission_request_fields=tuple(request),
    )
    assert submission == original_submission
    assert record == original_record
    assert record.intent["sources"]["mode"] == "entry_default"
    assert "mode" not in record.intent["request_fields"]
    assert record.intent["admission_request_fields"] == sorted(request)
    assert "admission_request_fields" not in intent.to_dict()
    reconstructed = core.reconstruct_submission_from_record(record, output_root=root)
    assert reconstructed.media == submission.media
    assert reconstructed.intent.params == submission.intent.params
    if seed_marker == "omitted":
        assert "seed" not in record.intent["params"]
        assert "seed" not in reconstructed.intent.params
    else:
        assert record.intent["params"]["seed"] == seed_marker
        assert reconstructed.intent.params["seed"] == seed_marker


def test_neutral_builder_cannot_bind_a_different_media_version(inputs):
    _, _, kwargs = inputs
    intent = normalize_generation_intent(
        {"model_id": "wan2.7-i2v", "mode": "i2v", "prompt": "Sunrise", "params": _params("omitted")},
        allowed_modes={"i2v"}, allowed_parameters=studio._ALLOWED_INTENT_PARAMS,
    )
    kwargs["media_version"] = replace(kwargs["media_version"], sha256="a" * 64)
    with pytest.raises(core.ProviderRecoveryError, match="provider_media_version_mismatch"):
        core.build_accepted_provider_submission_and_record(intent, **kwargs)


def test_neutral_builder_has_no_studio_metadata_when_not_supplied(inputs):
    _, _, kwargs = inputs
    intent = normalize_generation_intent(
        {"model_id": "wan2.7-i2v", "mode": "i2v", "prompt": "Sunrise", "params": _params("omitted")},
        allowed_modes={"i2v"}, allowed_parameters=studio._ALLOWED_INTENT_PARAMS,
    )
    submission, record = core.build_accepted_provider_submission_and_record(intent, **kwargs)
    assert record.intent == intent.to_dict()
    assert submission.intent == intent
    core._verify_record_fingerprint(record)
