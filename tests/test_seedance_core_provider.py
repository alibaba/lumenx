"""Standard Seedance HTTP contract: frozen bytes and one explicit paid request."""

import base64
from contextlib import contextmanager
from dataclasses import replace
import hashlib
import json
from threading import RLock

import pytest
import requests

from src.models.seedance_core_contract import (
    SEEDANCE_PROVIDER_CONTRACT, SEEDANCE_PROVIDER_MODELS, SEEDANCE_HTTP_ENDPOINTS,
    seedance_submission_payload, validate_seedance_intent, validate_seedance_scope,
)
from src.models.seedance_http import SeedanceHttpProvider
from src.models.provider_tasks import (
    PreparedMedia, ProviderBindingError, ProviderHandle,
    ProviderObservation, ProviderQueryError, ProviderScope, ProviderSubmission,
    ProviderSubmitError, ProviderTaskStatus, QueryCapability, ResolvedCredential,
)
from src.utils.generation_intent import GenerationIntent
from src.utils.json_storage import DirectoryWriterLock, StorageError
from src.utils.media_versions import MediaVersion
from tests.frozen_image_fixtures import rgb_png_bytes
from tests.test_wan3_image_validation import JPEG_240_BASE64, WEBP_240_BASE64, png_bytes


SCOPE = ProviderScope("mulerouter", "mulerouter", "mulerouter_http", "https://api.mulerouter.ai")
CREDENTIAL = ResolvedCredential(SCOPE, "credentials/seedance", "test-only-secret")
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MODES = ["t2v", "i2v", "r2v"]
ENDPOINTS = {
    "t2v": "/vendors/bytedance/v1/seedance-2.0/text-to-video/generation",
    "i2v": "/vendors/bytedance/v1/seedance-2.0/image-to-video/generation",
    "r2v": "/vendors/bytedance/v1/seedance-2.0/reference-to-video/generation",
}


def image(role="first_frame", ordinal=0, *, fmt="PNG", color="red", alpha=False):
    data = (base64.b64decode(JPEG_240_BASE64) if fmt == "JPEG" else
            base64.b64decode(WEBP_240_BASE64) if fmt == "WEBP" else
            png_bytes(20, 10, alpha=True) if alpha else rgb_png_bytes(color, width=20, height=10))
    mime = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp", "BMP": "image/bmp"}[fmt]
    version = MediaVersion(hashlib.sha256(data).hexdigest(), len(data), role, ordinal, mime)
    return PreparedMedia.from_verified_bytes(version, data, max_bytes=MAX_IMAGE_BYTES)


def submission(mode="t2v", *, media=None, **params):
    values = {"duration": 5, "resolution": "1080p", "aspect_ratio": "16:9", "audio": True}
    if mode == "t2v":
        values["watermark"] = False
    values.update(params)
    if media is None:
        media = () if mode == "t2v" else (image("first_frame" if mode == "i2v" else "reference_image"),)
    return ProviderSubmission(
        GenerationIntent(f"seedance/seedance-2.0-video#{mode}", mode, mode, " Original @image1 中文 ", values, {}, ()),
        f"seedance-2.0-{mode}", tuple(media), SCOPE, CREDENTIAL.credential_ref,
    )


def handle(mode="t2v"):
    return ProviderHandle(f"seedance-2.0-{mode}", "task-1", SCOPE, CREDENTIAL.credential_ref,
                          query_capability=QueryCapability.BY_TASK_ID, submitted_at=123.0)


def response(payload=None, *, status=200, content=None, headers=None):
    result = requests.Response()
    result.status_code = status
    result._content = json.dumps(payload).encode() if content is None else content
    result._content_consumed = True
    result.headers.update(headers or {})
    return result


@pytest.fixture
def transport(monkeypatch):
    state = {"calls": [], "response": response({"task_info": {"id": "task-1"}})}

    def send(session, request, **kwargs):
        state["calls"].append((session, request, kwargs))
        assert session.trust_env is False
        assert session.auth is None
        assert not session.cookies
        assert kwargs.get("allow_redirects") is False
        assert not kwargs.get("proxies")
        assert all(adapter.max_retries.total == 0 for adapter in session.adapters.values())
        value = state["response"]
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(requests.Session, "send", send)
    monkeypatch.setenv("HTTP_PROXY", "http://ambient.invalid")
    monkeypatch.setenv("HTTPS_PROXY", "http://ambient.invalid")
    monkeypatch.setenv("MULEROUTER_API_KEY", "ambient-secret")
    return state


@pytest.mark.parametrize("mode", MODES)
def test_seedance_exact_contract_and_payload(mode):
    req = submission(mode, seed=0, audio=False)
    assert SEEDANCE_PROVIDER_CONTRACT[req.provider_model_id] == (req.intent.model_id, mode)
    assert SEEDANCE_PROVIDER_MODELS == frozenset(f"seedance-2.0-{m}" for m in MODES)
    assert SEEDANCE_HTTP_ENDPOINTS[req.provider_model_id] == ENDPOINTS[mode]
    expected = {"prompt": req.intent.prompt, "duration": 5, "resolution": "1080p",
                "aspect_ratio": "16:9", "generate_audio": False, "seed": 0}
    if mode == "t2v":
        expected["watermark"] = False
    elif mode == "i2v":
        expected["image"] = req.media[0].url
    else:
        expected["images"] = [req.media[0].url]
    assert seedance_submission_payload(req) == expected
    assert SeedanceHttpProvider.validate_submission(req) is None


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("missing", ["duration", "resolution", "aspect_ratio", "audio"])
def test_seedance_missing_frozen_parameter_is_rejected(mode, missing):
    req = submission(mode)
    values = dict(req.intent.params)
    values.pop(missing)
    with pytest.raises(ProviderBindingError):
        validate_seedance_intent(replace(req.intent, params=values))


def test_seedance_t2v_requires_frozen_watermark():
    req = submission()
    values = dict(req.intent.params)
    values.pop("watermark")
    with pytest.raises(ProviderBindingError):
        validate_seedance_intent(replace(req.intent, params=values))


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("param,value", [
    ("duration", -1), ("duration", True), ("duration", 3), ("duration", 16),
    ("resolution", "480p"), ("resolution", "1080P"), ("aspect_ratio", "adaptive"),
    ("aspect_ratio", "21:9"), ("audio", 0), ("seed", True), ("seed", -2),
    ("seed", 4294967296), ("negative_prompt", "no"), ("generate_audio", True),
    ("use_fast", False), ("web_search", False), ("camera_fixed", False),
])
def test_seedance_invalid_or_expanded_parameters_are_rejected(mode, param, value):
    with pytest.raises(ProviderBindingError):
        seedance_submission_payload(submission(mode, **{param: value}))


@pytest.mark.parametrize("mode", ["i2v", "r2v"])
@pytest.mark.parametrize("value", [False, True])
def test_seedance_watermark_only_t2v(mode, value):
    with pytest.raises(ProviderBindingError):
        seedance_submission_payload(submission(mode, watermark=value))


@pytest.mark.parametrize("seed", [-1, 0, 4294967295])
@pytest.mark.parametrize("duration", [4, 15])
@pytest.mark.parametrize("ratio", ["16:9", "9:16", "1:1", "4:3", "3:4"])
def test_seedance_supported_scalar_boundaries_are_preserved(seed, duration, ratio):
    result = seedance_submission_payload(submission(seed=seed, duration=duration, aspect_ratio=ratio, watermark=True))
    assert result["seed"] == seed
    assert result["duration"] == duration
    assert result["aspect_ratio"] == ratio
    assert result["watermark"] is True


@pytest.mark.parametrize("mutation", [
    {"provider": "dashscope"}, {"backend": "mulerun_cli"}, {"gateway": "mulerun_token"},
    {"region": "cn-beijing"}, {"workspace_id": "workspace"}, {"account_identity": "account"},
    {"base_url": "https://api.mulerouter.ai/"}, {"base_url": "https://api.mulerun.com"},
    {"base_url": "http://api.mulerouter.ai"}, {"base_url": "https://api.mulerouter.ai:443"},
    {"base_url": "https://user@api.mulerouter.ai"}, {"base_url": "HTTPS://api.mulerouter.ai"},
])
def test_seedance_scope_rejected_before_any_http(mutation, transport):
    req = replace(submission(), scope=replace(SCOPE, **mutation))
    with pytest.raises(ProviderBindingError):
        validate_seedance_scope(req.scope)
    with pytest.raises(ProviderBindingError):
        SeedanceHttpProvider().submit(req, credential=replace(CREDENTIAL, scope=req.scope), on_submitted=lambda _: None)
    assert transport["calls"] == []


@pytest.mark.parametrize("field,value", [
    ("provider_model_id", "bytedance/seedance-2.0"), ("provider_model_id", "seedance-2.0-fast-t2v"),
    ("model_id", "seedance/seedance-2.0-video#r2v"), ("mode", "r2v"), ("catalog_mode", "r2v"),
])
def test_seedance_identity_mismatches_rejected(field, value):
    req = submission()
    req = replace(req, **{field: value}) if field == "provider_model_id" else replace(req, intent=replace(req.intent, **{field: value}))
    with pytest.raises(ProviderBindingError):
        seedance_submission_payload(req)


@pytest.mark.parametrize("fmt", ["PNG", "JPEG", "WEBP"])
def test_seedance_frozen_image_formats_allow_alpha_without_invented_geometry_rule(fmt):
    item = image(fmt=fmt, alpha=fmt == "PNG")
    assert seedance_submission_payload(submission("i2v", media=[item]))["image"] == item.url


def test_seedance_reference_order_and_intentional_later_duplicates_preserved():
    first = image("reference_image", 0)
    second = image("reference_image", 1, color="blue")
    later = image("reference_image", 2)
    result = seedance_submission_payload(submission("r2v", media=[first, second, later]))
    assert result["images"] == [first.url, second.url, first.url]
    assert "reference_images" not in result


@pytest.mark.parametrize("mode,count", [("t2v", 1), ("i2v", 0), ("i2v", 2), ("r2v", 0), ("r2v", 10)])
def test_seedance_media_counts_rejected(mode, count):
    role = "first_frame" if mode == "i2v" else "reference_image"
    with pytest.raises(ProviderBindingError):
        seedance_submission_payload(submission(mode, media=[image(role, 0 if mode == "i2v" else n) for n in range(count)]))


@pytest.mark.parametrize("kind", ["remote", "mime", "role", "order", "hash", "size", "decode", "binding", "oversize"])
def test_seedance_unverified_or_invalid_image_rejected(kind):
    item = image()
    if kind == "remote":
        item = PreparedMedia(item.version, "https://images.invalid/a.png")
    elif kind == "mime":
        item = image(fmt="BMP")
    elif kind == "role":
        item = image("reference_video")
    elif kind == "order":
        object.__setattr__(item.version, "ordinal", 1)
    elif kind in {"hash", "size", "oversize"}:
        change = {"sha256": "a" * 64} if kind == "hash" else {"size_bytes": MAX_IMAGE_BYTES + 1 if kind == "oversize" else item.version.size_bytes + 1}
        object.__setattr__(item, "version", replace(item.version, **change))
    elif kind == "decode":
        data = b"not-an-image"
        item = PreparedMedia.from_verified_bytes(replace(item.version, size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest()), data, max_bytes=MAX_IMAGE_BYTES)
    else:
        object.__setattr__(item, "transport_binding", "unverified")
    with pytest.raises(ProviderBindingError):
        seedance_submission_payload(submission("i2v", media=[item]))


@pytest.mark.parametrize("mode", MODES)
def test_seedance_submit_once_saves_receipt_and_never_queries(mode, transport):
    seen = []
    req = submission(mode)
    result = SeedanceHttpProvider().submit(req, credential=CREDENTIAL, on_submitted=seen.append)
    assert seen == [result]
    assert result.provider_model_id == req.provider_model_id
    assert result.scope == SCOPE
    assert result.credential_ref == CREDENTIAL.credential_ref
    assert result.query_capability is QueryCapability.BY_TASK_ID
    assert result.submitted_at is not None
    assert len(transport["calls"]) == 1
    _, sent, kwargs = transport["calls"][0]
    assert sent.method == "POST"
    assert sent.url == SCOPE.base_url + ENDPOINTS[mode]
    assert sent.headers["Authorization"] == "Bearer test-only-secret"
    assert "Cookie" not in sent.headers
    assert json.loads(sent.body) == seedance_submission_payload(req)
    assert kwargs["timeout"] == 120


@pytest.mark.parametrize("fault", [429, 500, 502, 503, 504, 302, "timeout", "disconnect", "json", "missing"])
def test_seedance_ambiguous_submit_is_never_retried(fault, transport):
    if fault == "timeout":
        transport["response"] = requests.Timeout("private-secret https://private.invalid")
    elif fault == "disconnect":
        transport["response"] = requests.ConnectionError("private-secret")
    elif fault == "json":
        transport["response"] = response(content=b"private-secret invalid json")
    else:
        transport["response"] = response({}, status=fault if type(fault) is int else 200)
    with pytest.raises(ProviderSubmitError) as caught:
        SeedanceHttpProvider().submit(submission(), credential=CREDENTIAL, on_submitted=lambda _: None)
    assert len(transport["calls"]) == 1
    assert "private" not in str(caught.value)
    assert caught.value.outcome == "unknown"


def test_seedance_receipt_on_non_200_saved_and_writer_failure_propagates(transport):
    transport["response"] = response({"task_info": {"id": "accepted-task"}}, status=502)
    def save(receipt):
        assert receipt.task_id == "accepted-task"
        raise StorageError("persistence unavailable")
    with pytest.raises(StorageError):
        SeedanceHttpProvider().submit(submission(), credential=CREDENTIAL, on_submitted=save)
    assert len(transport["calls"]) == 1


@pytest.mark.parametrize("bad_id", ["", "../task", "task?x=1", "task/one", "a" * 257, 23, None])
def test_seedance_bad_submit_receipt_never_acknowledged(bad_id, transport):
    transport["response"] = response({"task_info": {"id": bad_id}})
    with pytest.raises(ProviderSubmitError):
        SeedanceHttpProvider().submit(submission(), credential=CREDENTIAL, on_submitted=lambda _: pytest.fail("bad receipt"))
    assert len(transport["calls"]) == 1


@pytest.mark.parametrize("raw,expected", [("pending", "pending"), ("queued", "pending"), ("running", "running"), ("processing", "running"), ("failed", "failed"), ("vendor_new_state", "unknown")])
def test_seedance_query_preserves_status_without_guessing(raw, expected, transport):
    transport["response"] = response({"task_info": {"id": "task-1", "status": raw, "error": {"detail": "secret provider body"}}})
    original = handle("r2v")
    observed = SeedanceHttpProvider().query(original, credential=CREDENTIAL)
    assert observed.status.value == expected
    assert observed.raw_status == raw
    assert observed.source_handle == original
    assert "secret" not in repr(observed)
    assert len(transport["calls"]) == 1
    assert transport["calls"][0][1].method == "GET"
    assert transport["calls"][0][1].url == SCOPE.base_url + ENDPOINTS["r2v"] + "/task-1"


@pytest.mark.parametrize("status", ["completed", "succeeded"])
@pytest.mark.parametrize("videos", ["https://cdn.invalid/out.mp4", ["https://cdn.invalid/out.mp4"]])
def test_seedance_query_single_top_level_video(status, videos, transport):
    transport["response"] = response({"task_info": {"id": "task-1", "status": status}, "videos": videos})
    observed = SeedanceHttpProvider().query(handle(), credential=CREDENTIAL)
    assert observed.status is ProviderTaskStatus.SUCCEEDED
    assert observed.video_url == "https://cdn.invalid/out.mp4"


@pytest.mark.parametrize("payload", [
    {}, {"task_info": []}, {"task_info": {"status": "running"}},
    {"task_info": {"id": "other", "status": "running"}},
    {"task_info": {"id": "task-1", "status": "bad status"}},
    *[{"task_info": {"id": "task-1", "status": "completed"}, "videos": value} for value in [[], ["https://a.invalid", "https://b.invalid"], [{"url": "https://a.invalid"}], None, "file:///secret", ""]],
    {"task_info": {"id": "task-1", "status": "completed"}, "result": {"videos": ["https://a.invalid"]}},
])
def test_seedance_query_bad_protocol_is_observation_error(payload, transport):
    transport["response"] = response(payload)
    with pytest.raises(ProviderQueryError):
        SeedanceHttpProvider().query(handle(), credential=CREDENTIAL)
    assert len(transport["calls"]) == 1


@pytest.mark.parametrize("mutation", [{"task_id": "../bad"}, {"provider_model_id": "seedance-2.0-fast-t2v"}, {"query_capability": QueryCapability.UNKNOWN}, {"credential_ref": "different"}, {"scope": replace(SCOPE, base_url="https://api.mulerun.com")}])
def test_seedance_query_invalid_binding_zero_io(mutation, transport):
    with pytest.raises(ProviderBindingError):
        SeedanceHttpProvider().query(replace(handle(), **mutation), credential=CREDENTIAL)
    assert transport["calls"] == []


@pytest.mark.parametrize("fault", [500, "transport", "json"])
def test_seedance_query_failures_do_not_change_task_status(fault, transport):
    transport["response"] = requests.ConnectionError("secret") if fault == "transport" else response({}, status=500) if fault == 500 else response(content=b"secret")
    with pytest.raises(ProviderQueryError) as caught:
        SeedanceHttpProvider().query(handle(), credential=CREDENTIAL)
    assert "secret" not in str(caught.value)
    assert len(transport["calls"]) == 1


def test_seedance_fetch_is_anonymous_bounded_and_publishes_after_proof(tmp_path, transport):
    transport["response"] = response(content=b"video", headers={"Content-Length": "5"})
    original = handle()
    observed = ProviderObservation(original.task_id, ProviderTaskStatus.SUCCEEDED, "completed", video_url="https://cdn.invalid/out.mp4?private=signature", source_handle=original)
    lock = RLock()
    @contextmanager
    def guard():
        with lock:
            yield
    with DirectoryWriterLock(tmp_path) as lease:
        proofs = []
        def prepared(artifact):
            assert not (tmp_path / "video/result.mp4").exists()
            proofs.append(artifact)
        artifact = SeedanceHttpProvider().fetch(original, observed, output_root=tmp_path, output_ref="video/result.mp4", writer_lock=lease, storage_guard=guard, max_bytes=8, on_prepared=prepared)
    assert proofs == [artifact]
    assert (tmp_path / artifact.relative_path).read_bytes() == b"video"
    _, sent, _ = transport["calls"][0]
    assert "Authorization" not in sent.headers
    assert "Cookie" not in sent.headers
    assert sent.headers["Accept-Encoding"] == "identity"


def test_seedance_fetch_rejects_bad_scope_before_network(tmp_path, transport):
    original = replace(handle(), scope=replace(SCOPE, base_url="https://api.mulerun.com"))
    observed = ProviderObservation(original.task_id, ProviderTaskStatus.SUCCEEDED, "completed", video_url="https://cdn.invalid/out.mp4", source_handle=original)
    with pytest.raises(ProviderBindingError):
        SeedanceHttpProvider().fetch(original, observed, output_root=tmp_path, output_ref="video/result.mp4", writer_lock=None, storage_guard=None, max_bytes=8)
    assert transport["calls"] == []


def test_seedance_failed_observation_does_not_echo_bound_secret_in_diagnostics(transport):
    transport["response"] = response({"task_info": {"id": "task-1", "status": "failed", "error": {"code": CREDENTIAL.secret, "detail": CREDENTIAL.secret}}, "request_id": CREDENTIAL.secret})
    observed = SeedanceHttpProvider().query(handle(), credential=CREDENTIAL)
    assert CREDENTIAL.secret not in repr(observed)
    assert observed.error_code == "provider_failed"


def test_seedance_receipt_diagnostic_does_not_echo_bound_secret(transport):
    transport["response"] = response({"task_info": {"id": "task-1"}, "request_id": CREDENTIAL.secret})
    saved = []
    receipt = SeedanceHttpProvider().submit(submission(), credential=CREDENTIAL, on_submitted=saved.append)
    assert saved == [receipt]
    assert receipt.request_id is None


@pytest.mark.parametrize("kind", ["async", "generator", "callable_async"])
def test_seedance_deferred_writer_rejected_before_paid_request(kind, transport):
    async def async_writer(receipt):
        pass
    def generator_writer(receipt):
        yield receipt
    class AsyncWriter:
        async def __call__(self, receipt):
            pass
    writer = {"async": async_writer, "generator": generator_writer, "callable_async": AsyncWriter()}[kind]
    with pytest.raises(ProviderBindingError):
        SeedanceHttpProvider().submit(submission(), credential=CREDENTIAL, on_submitted=writer)
    assert transport["calls"] == []


def test_seedance_wrapper_returning_deferred_writer_not_acknowledged(transport):
    async def pending():
        pass
    with pytest.raises(TypeError, match="synchronously"):
        SeedanceHttpProvider().submit(submission(), credential=CREDENTIAL, on_submitted=lambda _: pending())
    assert len(transport["calls"]) == 1


def test_seedance_mismatched_credential_and_invalid_parameters_zero_http(transport):
    provider = SeedanceHttpProvider()
    with pytest.raises(ProviderBindingError):
        provider.submit(submission(), credential=replace(CREDENTIAL, credential_ref="other"), on_submitted=lambda _: None)
    with pytest.raises(ProviderBindingError):
        provider.submit(submission(duration=-1), credential=CREDENTIAL, on_submitted=lambda _: None)
    assert transport["calls"] == []


def test_seedance_nine_images_accepted_and_twenty_mib_product_limit_is_exact():
    from tests.test_vidu_image_transport import large_png
    items = [image("reference_image", i) for i in range(9)]
    assert seedance_submission_payload(submission("r2v", media=items))["images"] == [item.url for item in items]
    data = large_png(MAX_IMAGE_BYTES)
    version = MediaVersion(hashlib.sha256(data).hexdigest(), len(data), "first_frame", 0, "image/png")
    prepared = PreparedMedia.from_verified_bytes(version, data, max_bytes=MAX_IMAGE_BYTES)
    assert seedance_submission_payload(submission("i2v", media=[prepared]))["image"] == prepared.url
