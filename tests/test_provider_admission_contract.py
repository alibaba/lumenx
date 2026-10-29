"""Product admission can check the actual adapter contract without submitting."""

import hashlib
from dataclasses import replace

import pytest

from src.models.provider_tasks import (
    PreparedMedia,
    ProviderBindingError,
    ProviderScope,
    ProviderSubmission,
)
from src.models.wanx import WanxModel
from src.utils.generation_intent import normalize_generation_intent
from src.utils.media_versions import MediaVersion


@pytest.fixture
def request_value(monkeypatch):
    intent = normalize_generation_intent(
        {"model_id": "wan2.6-i2v-flash", "mode": "i2v", "prompt": "Sea at sunrise", "params": {
            "duration": 5, "resolution": "720p", "seed": 0,
            "audio": False, "prompt_extend": False,
        }},
        allowed_modes={"i2v"},
        allowed_parameters={"duration", "resolution", "seed", "audio", "prompt_extend"},
    )
    data = b"synthetic image bytes; not a decoding assertion"
    media = PreparedMedia.from_verified_bytes(
        MediaVersion(hashlib.sha256(data).hexdigest(), len(data), "first_frame", 0, "image/png"),
        data,
        max_bytes=1024,
    )
    request = ProviderSubmission(
        intent, "wan2.6-i2v-flash", (media,),
        ProviderScope("dashscope", "dashscope", "dashscope", "https://dashscope.aliyuncs.com", "cn-beijing"),
        "synthetic-binding-ref",
    )

    def forbidden(*args, **kwargs):
        pytest.fail("admission validation must not submit, query, fetch, or read current defaults")

    for name in ("submit", "query", "fetch", "generate"):
        monkeypatch.setattr(WanxModel, name, forbidden)
    monkeypatch.setattr(WanxModel, "api_key", property(forbidden))
    monkeypatch.setattr("src.models.wanx.requests.post", forbidden)
    monkeypatch.setattr("src.models.wanx.requests.get", forbidden)
    monkeypatch.setattr("src.models.wanx.requests.Session.get", forbidden)
    monkeypatch.setattr("src.models.wanx.get_provider_base_url", forbidden)
    monkeypatch.setattr("src.utils.generation_intent.get_catalog_accessor", forbidden)
    return request


def test_valid_frozen_request_checks_without_changing_its_provenance(request_value):
    before = request_value.intent.to_dict()
    assert WanxModel.validate_submission(request_value) is None
    assert request_value.intent.to_dict() == before
    assert request_value.intent.params["seed"] == 0
    assert request_value.intent.params["audio"] is False


def test_explicit_random_seed_is_validated_without_changing_frozen_intent(request_value):
    intent = replace(request_value.intent, params={**request_value.intent.params, "seed": -1})
    request = replace(request_value, intent=intent)
    assert WanxModel.validate_submission(request) is None
    assert request.intent.params["seed"] == -1


@pytest.mark.parametrize("changes", [
    {"resolution": "480p"}, {"seed": -2}, {"audio_url": "https://example.test/audio.wav"},
    {"duration": 20}, {"shot_type": "multi"}, {"unknown": False},
])
def test_unsupported_paid_request_is_rejected_before_product_admission(request_value, changes):
    intent = replace(request_value.intent, params={**request_value.intent.params, **changes})
    with pytest.raises(ProviderBindingError):
        WanxModel.validate_submission(replace(request_value, intent=intent))


@pytest.mark.parametrize("scope_changes", [
    {"region": "cn-hangzhou"}, {"base_url": "https://example.test"}, {"backend": "classic"},
])
def test_wrong_runtime_scope_is_rejected_without_network(request_value, scope_changes):
    with pytest.raises(ProviderBindingError):
        WanxModel.validate_submission(replace(request_value, scope=replace(request_value.scope, **scope_changes)))


def test_mismatched_model_and_missing_first_frame_are_rejected(request_value):
    for request in (
        replace(request_value, provider_model_id="wan2.6-i2v"),
        replace(request_value, media=()),
    ):
        with pytest.raises(ProviderBindingError):
            WanxModel.validate_submission(request)


def test_reference_image_cannot_be_reinterpreted_as_a_first_frame(request_value):
    first_frame = request_value.media[0]
    reference_image = PreparedMedia.from_data_uri(
        replace(first_frame.version, role="reference_image"),
        first_frame.url,
        max_bytes=1024,
    )
    with pytest.raises(ProviderBindingError):
        WanxModel.validate_submission(replace(request_value, media=(reference_image,)))


def test_non_submission_has_a_typed_boundary_error():
    with pytest.raises(ProviderBindingError):
        WanxModel.validate_submission({})
