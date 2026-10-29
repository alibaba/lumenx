"""PixVerse workspace payloads preserve the four existing Core identities."""

from dataclasses import replace

import pytest

from src.models.pixverse_core_contract import (
    PIXVERSE_PROVIDER_CONTRACT,
    pixverse_submission_payload,
    validate_pixverse_image_metadata,
    validate_pixverse_intent,
)
from src.models.provider_tasks import ProviderBindingError, ProviderSubmission
from src.utils.media_probe import FrozenImageMetadata
from src.utils.media_versions import MediaVersion
from tests.test_vidu_core_provider import CLASSIC_SCOPE, SCOPE, _data_uri_image, _provider_image
from src.utils.generation_intent import GenerationIntent


CONTRACTS = [
    ("pixverse/pixverse-v6-it2v", "pixverse/pixverse/pixverse-v6-video#i2v", "i2v"),
    ("pixverse/pixverse-c1-it2v", "pixverse/pixverse-c1-video#i2v", "i2v"),
    ("pixverse/pixverse-c1-r2v", "pixverse/pixverse-c1-video#r2v", "r2v"),
    ("pixverse/pixverse-v5.6-r2v", "pixverse/pixverse-v5.6-r2v#r2v", "r2v"),
]
I2V, C1_I2V, C1_R2V, V56_R2V = [item[0] for item in CONTRACTS]


def intent(model=I2V, *, prompt="Keep the original prompt @image1 unchanged.", **params):
    canonical, mode = next((canonical, mode) for api, canonical, mode in CONTRACTS if api == model)
    return GenerationIntent(model_id=canonical, catalog_mode=mode, mode=mode,
                            prompt=prompt, params=params, sources={}, request_fields=())


def submission(model=I2V, *, media=None, **params):
    if media is None:
        role = "first_frame" if model in (I2V, C1_I2V) else "reference_image"
        media = (_provider_image(role, 0, "oss://test/pixverse/ref-0.png"),)
    return ProviderSubmission(intent=intent(model, **params), provider_model_id=model,
                              media=media, scope=SCOPE, credential_ref="credentials/pixverse")


@pytest.mark.parametrize("model,canonical,mode", CONTRACTS)
def test_pixverse_exact_identities_preserve_the_existing_v6_double_prefix(model, canonical, mode):
    assert PIXVERSE_PROVIDER_CONTRACT[model] == (canonical, mode)
    assert set(PIXVERSE_PROVIDER_CONTRACT) == {item[0] for item in CONTRACTS}


@pytest.mark.parametrize("model", [I2V, C1_I2V])
@pytest.mark.parametrize("resolution", ["360P", "540P", "720P", "1080P"])
def test_pixverse_i2v_exact_payload_retains_false_and_seed_zero(model, resolution):
    request = submission(model, duration=15, resolution=resolution, audio=False, watermark=False, seed=0)
    assert pixverse_submission_payload(request, require_transport_ready=True) == {
        "model": model,
        "input": {"prompt": request.intent.prompt,
                  "media": [{"type": "image_url", "url": "oss://test/pixverse/ref-0.png"}]},
        "parameters": {"duration": 15, "resolution": resolution,
                       "audio": False, "watermark": False, "seed": 0},
    }
    assert validate_pixverse_intent(request.intent) == {
        "duration": 15, "resolution": resolution, "audio": False, "watermark": False, "seed": 0,
    }


@pytest.mark.parametrize("model", [I2V, C1_I2V, C1_R2V, V56_R2V])
def test_pixverse_does_not_invent_optional_defaults(model):
    params = {"duration": 5, "resolution": "720P"}
    if model in (C1_R2V, V56_R2V):
        params["aspect_ratio"] = "16:9"
    result = pixverse_submission_payload(submission(model, **params))["parameters"]
    assert result == ({"duration": 5, "size": "1280*720"} if "aspect_ratio" in params else params)


@pytest.mark.parametrize("model", [I2V, C1_I2V, C1_R2V, V56_R2V])
@pytest.mark.parametrize("missing", ["duration", "resolution"])
def test_pixverse_requires_frozen_duration_and_resolution(model, missing):
    params = {"duration": 5, "resolution": "720P"}
    if model in (C1_R2V, V56_R2V):
        params["aspect_ratio"] = "16:9"
    params.pop(missing)
    request = submission(model, **params)
    with pytest.raises(ProviderBindingError):
        validate_pixverse_intent(request.intent)
    with pytest.raises(ProviderBindingError):
        pixverse_submission_payload(request)


@pytest.mark.parametrize("model,resolution,sizes", [
    (C1_R2V, "720P", ["1280*720", "1108*832", "960*960", "832*1108", "720*1280"]),
    (C1_R2V, "1080P", ["1920*1080", "1664*1248", "1440*1440", "1248*1664", "1080*1920"]),
    (V56_R2V, "720P", ["1280*720", "1108*830", "960*960", "830*1108", "720*1280"]),
    (V56_R2V, "1080P", ["1920*1080", "1662*1246", "1440*1440", "1246*1662", "1080*1920"]),
])
@pytest.mark.parametrize("index,ratio", list(enumerate(["16:9", "4:3", "1:1", "3:4", "9:16"])))
def test_pixverse_r2v_exact_size_tables_keep_reference_order_and_send_size_only(model, resolution, sizes, index, ratio):
    media = tuple(_provider_image("reference_image", n, f"oss://test/pixverse/{n}.png") for n in range(7))
    request = submission(model, media=media, duration=8, resolution=resolution, aspect_ratio=ratio,
                         audio=False, watermark=False, seed=2147483647)
    result = pixverse_submission_payload(request, require_transport_ready=True)
    assert result["input"] == {
        "prompt": request.intent.prompt,
        "media": [{"type": "image_url", "url": item.url} for item in media],
    }
    assert result["parameters"] == {
        "duration": 8, "size": sizes[index], "audio": False, "watermark": False, "seed": 2147483647,
    }
    assert dict(request.intent.params)["resolution"] == resolution
    assert "aspect_ratio" in request.intent.params


@pytest.mark.parametrize("model,resolution,duration", [
    (C1_R2V, "720P", 1), (C1_R2V, "1080P", 15),
    (V56_R2V, "720P", 5), (V56_R2V, "720P", 8), (V56_R2V, "720P", 10),
    (V56_R2V, "1080P", 5), (V56_R2V, "1080P", 8),
])
def test_pixverse_r2v_valid_conditional_durations(model, resolution, duration):
    request = submission(model, duration=duration, resolution=resolution, aspect_ratio="16:9")
    assert pixverse_submission_payload(request)["parameters"]["duration"] == duration


@pytest.mark.parametrize("model,resolution,duration", [
    (C1_R2V, "720P", 0), (C1_R2V, "1080P", 16),
    (V56_R2V, "720P", 3), (V56_R2V, "720P", 9), (V56_R2V, "1080P", 10),
])
def test_pixverse_r2v_rejects_invalid_conditional_durations(model, resolution, duration):
    request = submission(model, duration=duration, resolution=resolution, aspect_ratio="16:9")
    with pytest.raises(ProviderBindingError):
        validate_pixverse_intent(request.intent)


@pytest.mark.parametrize("shot_type", ["single", "multi"])
def test_pixverse_shot_type_is_v6_i2v_only(shot_type):
    request = submission(duration=1, resolution="360P", shot_type=shot_type)
    assert pixverse_submission_payload(request)["parameters"]["shot_type"] == shot_type
    for model in (C1_I2V, C1_R2V, V56_R2V):
        params = {"duration": 5, "resolution": "720P", "shot_type": shot_type}
        if model in (C1_R2V, V56_R2V):
            params["aspect_ratio"] = "16:9"
        with pytest.raises(ProviderBindingError):
            pixverse_submission_payload(submission(model, **params))


@pytest.mark.parametrize("bad", [
    {"duration": 0}, {"duration": 16}, {"duration": True}, {"duration": 5.0}, {"duration": None},
    {"resolution": "720p"}, {"resolution": "4K"}, {"resolution": None},
    {"audio": 0}, {"watermark": 1}, {"audio": None}, {"seed": True}, {"seed": 0.0},
    {"seed": -1}, {"seed": 2147483648}, {"negative_prompt": "no"},
    {"shot_type": "auto"}, {"shot_type": None}, {"aspect_ratio": "16:9"},
    {"size": "1280*720"}, {"mode": "i2v"}, {"ref_name": "image1"},
])
def test_pixverse_i2v_rejects_unsupported_or_wrong_typed_parameters(bad):
    request = submission(**{"duration": 5, "resolution": "720P", **bad})
    with pytest.raises(ProviderBindingError):
        validate_pixverse_intent(request.intent)
    with pytest.raises(ProviderBindingError):
        pixverse_submission_payload(request)


@pytest.mark.parametrize("bad", [
    {"aspect_ratio": None}, {"aspect_ratio": False}, {"aspect_ratio": "21:9"},
    {"aspect_ratio": "3:2"}, {"resolution": "540P"}, {"resolution": "360P"},
])
@pytest.mark.parametrize("model", [C1_R2V, V56_R2V])
def test_pixverse_r2v_stays_within_existing_product_resolution_and_ratio_boundary(model, bad):
    request = submission(model, **{"duration": 5, "resolution": "720P", "aspect_ratio": "16:9", **bad})
    with pytest.raises(ProviderBindingError):
        pixverse_submission_payload(request)
    with pytest.raises(ProviderBindingError):
        pixverse_submission_payload(submission(model, duration=5, resolution="720P"))


@pytest.mark.parametrize("model,limit", [(I2V, 5000), (C1_I2V, 5000), (C1_R2V, 5000), (V56_R2V, 2048)])
def test_pixverse_prompt_limits_preserve_original_text(model, limit):
    params = {"duration": 5, "resolution": "720P"}
    if model in (C1_R2V, V56_R2V):
        params["aspect_ratio"] = "16:9"
    prompt = " 空" + "字" * (limit - 3) + " "
    assert len(prompt) == limit
    assert pixverse_submission_payload(submission(model, prompt=prompt, **params))["input"]["prompt"] == prompt
    for invalid in ("", "   ", "字" * (limit + 1)):
        with pytest.raises(ProviderBindingError):
            pixverse_submission_payload(submission(model, prompt=invalid, **params))


def test_pixverse_pure_contract_rejects_wrong_identity_mode_and_scope():
    request = submission(duration=5, resolution="720P")
    for bad in (
        replace(request, provider_model_id="pixverse/pixverse-v6-r2v"),
        replace(request, intent=replace(request.intent, model_id="pixverse/pixverse-v6-video#i2v")),
        replace(request, intent=replace(request.intent, mode="r2v")),
        replace(request, intent=replace(request.intent, catalog_mode="r2v")),
        replace(request, scope=CLASSIC_SCOPE),
        replace(request, scope=replace(SCOPE, region="ap-southeast-1")),
        replace(request, scope=replace(SCOPE, workspace_id="other-space")),
        replace(request, scope=replace(SCOPE, provider="pixverse")),
        replace(request, scope=replace(SCOPE, backend="vendor")),
        replace(request, scope=replace(SCOPE, base_url="https://example.com")),
    ):
        with pytest.raises(ProviderBindingError):
            pixverse_submission_payload(bad)


@pytest.mark.parametrize("model,count", [(I2V, 0), (I2V, 2), (C1_R2V, 0), (V56_R2V, 8)])
def test_pixverse_media_count_fails_closed(model, count):
    mode_is_i2v = model == I2V
    role = "first_frame" if mode_is_i2v else "reference_image"
    media = tuple(_provider_image(role, 0 if mode_is_i2v else n, f"oss://test/pixverse/{n}.png") for n in range(count))
    params = {"duration": 5, "resolution": "720P"}
    if model != I2V:
        params["aspect_ratio"] = "16:9"
    with pytest.raises(ProviderBindingError):
        pixverse_submission_payload(submission(model, media=media, **params))


def test_pixverse_rejects_wrong_role_order_mime_or_unverified_media():
    for image in (
        _provider_image("reference_image", 0, "oss://test/pixverse/wrong.png"),
        _provider_image("first_frame", 0, "oss://test/pixverse/wrong.bmp", mime="image/bmp"),
        _provider_image("first_frame", 0, "https://example.com/unverified.png"),
    ):
        with pytest.raises(ProviderBindingError):
            pixverse_submission_payload(submission(media=(image,), duration=5, resolution="720P"))
    media = tuple(_provider_image("reference_image", n, f"oss://test/pixverse/{n}.png") for n in (1, 0))
    with pytest.raises(ProviderBindingError):
        pixverse_submission_payload(submission(C1_R2V, media=media, duration=5, resolution="720P", aspect_ratio="16:9"))


def test_pixverse_pending_data_uri_probes_bytes_and_requires_upload_at_submit():
    image = _data_uri_image("first_frame", 0, width=20, height=300, alpha=True)
    request = submission(media=(image,), duration=5, resolution="720P")
    assert pixverse_submission_payload(request)["input"]["media"] == [{"type": "image_url", "url": image.url}]
    with pytest.raises(ProviderBindingError):
        pixverse_submission_payload(request, require_transport_ready=True)
    object.__setattr__(image, "url", image.url[:-5] + "xxxxx")
    with pytest.raises(ProviderBindingError):
        pixverse_submission_payload(request)


@pytest.mark.parametrize("width,height", [(1, 10000), (10000, 1), (10000, 10000)])
def test_pixverse_metadata_accepts_alpha_and_has_no_invented_aspect_ratio_limit(width, height):
    assert validate_pixverse_image_metadata(FrozenImageMetadata(kind="image", width=width, height=height, has_alpha=True)) is None


@pytest.mark.parametrize("metadata", [
    FrozenImageMetadata(kind="image", width=10001, height=1),
    FrozenImageMetadata(kind="image", width=1, height=10001),
    FrozenImageMetadata(kind="image", width=0, height=100),
    FrozenImageMetadata(kind="image", width=True, height=100),
    {"kind": "image", "width": 100, "height": 100}, None,
])
def test_pixverse_metadata_rejects_invalid_edge_or_untyped_shape(metadata):
    with pytest.raises(ProviderBindingError):
        validate_pixverse_image_metadata(metadata)


def test_pixverse_image_limit_is_20_mib_exactly():
    image = _provider_image("first_frame", 0, "oss://test/pixverse/large.webp", mime="image/webp")
    object.__setattr__(image, "version", MediaVersion("0" * 64, 20 * 1024 * 1024, "first_frame", 0, "image/webp"))
    request = submission(media=(image,), duration=5, resolution="720P")
    assert pixverse_submission_payload(request, require_transport_ready=True)["input"]["media"][0]["type"] == "image_url"
    object.__setattr__(image, "version", replace(image.version, size_bytes=image.version.size_bytes + 1))
    with pytest.raises(ProviderBindingError):
        pixverse_submission_payload(request)


def test_pixverse_contract_rejects_untyped_intent_submission_and_media():
    with pytest.raises(ProviderBindingError):
        validate_pixverse_intent({})
    with pytest.raises(ProviderBindingError):
        pixverse_submission_payload({})
    request = submission(duration=5, resolution="720P")
    object.__setattr__(request, "media", ({"url": "oss://test/untrusted.png"},))
    with pytest.raises(ProviderBindingError):
        pixverse_submission_payload(request)
