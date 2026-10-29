"""Opt-in HTTP submission: original scope, one POST, mandatory receipt writer."""

import base64
import hashlib
import importlib
import inspect
import json
from dataclasses import FrozenInstanceError, replace

import pytest
import requests

from src.config import Config
from src.models.factory import ModelFactory
from src.models.wanx import WanxModel
from src.utils.generation_intent import normalize_generation_intent
from src.utils.json_storage import StorageError, atomic_write_json, read_json
from src.utils.media_versions import MediaVersion
from src.utils.model_catalog import CatalogAccessor, load_generated_model_catalog


def test_real_factory_exposes_independent_submit():
    config = Config()
    config.config = {"model": {"name": "wanx"}}
    assert callable(getattr(ModelFactory.create_model(config), "submit", None))


@pytest.fixture
def contract():
    return importlib.import_module("src.models.provider_tasks")


@pytest.fixture
def scope(contract):
    return contract.ProviderScope(
        provider="dashscope", backend="dashscope", gateway="dashscope",
        base_url="https://dashscope.aliyuncs.com", region="cn-beijing",
        workspace_id="Original_Workspace", account_identity="original-account",
    )


@pytest.fixture
def credential(contract, scope):
    return contract.ResolvedCredential(scope, "credentials/original", "test-only-secret")


def intent_for(model_id="wan2.6-i2v-flash", **params):
    values = {"seed": 0, "prompt_extend": False, "negative_prompt": ""}
    if model_id.endswith("-flash"):
        values["audio"] = False
    values.update(params)
    return normalize_generation_intent(
        {"model_id": model_id, "mode": "i2v", "prompt": "海边日出", "params": values},
        catalog=CatalogAccessor(load_generated_model_catalog()),
        allowed_modes={"i2v"}, allowed_parameters={
            "duration", "resolution", "seed", "prompt_extend", "negative_prompt",
            "audio", "shot_type",
        },
    )


@pytest.fixture
def submission(contract, scope):
    media = contract.PreparedMedia(
        version=MediaVersion("a" * 64, 100, "first_frame", 0, "image/png"),
        url="https://media.invalid/frame.png?signature=private",
    )
    return contract.ProviderSubmission(
        intent=intent_for(), provider_model_id="wan2.6-i2v-flash", media=(media,),
        scope=scope, credential_ref="credentials/original",
    )


class Response:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


@pytest.fixture
def transport(monkeypatch):
    calls = []
    state = {"response": Response({"request_id": "request-1", "output": {
        "task_id": "task-1", "task_status": "PENDING",
    }})}

    def post(url, **kwargs):
        calls.append((url, kwargs))
        response = state["response"]
        if isinstance(response, Exception):
            raise response
        return response

    def forbidden(*args, **kwargs):
        pytest.fail("submit must not query, download, use SDK, or resolve current configuration")

    monkeypatch.setattr("src.models.wanx.requests.post", post)
    monkeypatch.setattr("src.models.wanx.requests.get", forbidden)
    monkeypatch.setattr("src.models.wanx.requests.Session.get", forbidden)
    monkeypatch.setattr("src.models.wanx.get_provider_base_url", forbidden)
    monkeypatch.setattr(WanxModel, "query", forbidden)
    monkeypatch.setattr(WanxModel, "generate", forbidden)
    monkeypatch.setattr(WanxModel, "_download_video", forbidden)
    monkeypatch.setattr(WanxModel, "api_key", property(forbidden))
    monkeypatch.setattr("src.models.wanx.VideoSynthesis.async_call", forbidden)
    monkeypatch.setattr("src.models.wanx.VideoSynthesis.wait", forbidden)
    return state, calls


@pytest.fixture
def model(transport):
    config = Config()
    config.config = {"model": {"name": "wanx", "params": {"seed": 777, "audio": True}}}
    return ModelFactory.create_model(config)


@pytest.mark.parametrize("model_id", ["wan2.6-i2v", "wan2.6-i2v-flash"])
def test_submit_preserves_frozen_intent_scope_and_receipt_before_return(
    monkeypatch, contract, model, submission, credential, transport, tmp_path, model_id,
):
    submission = replace(submission, intent=intent_for(model_id), provider_model_id=model_id)
    before = submission.intent.fingerprint
    monkeypatch.setenv("DASHSCOPE_API_KEY", "other-current-key")
    monkeypatch.setenv("DASHSCOPE_BASE_URL", "https://attacker.invalid")
    monkeypatch.setenv("DASHSCOPE_WORKSPACE_ID", "other-workspace")
    model.params = {"seed": 9999, "resolution": "1080P", "prompt_extend": True}
    monkeypatch.setattr(
        "src.utils.model_catalog.get_catalog_accessor", lambda: pytest.fail("catalog reread"),
    )
    path = tmp_path / "receipt.json"
    events = []

    def save(handle):
        assert len(transport[1]) == 1
        events.append("save")
        atomic_write_json(path, handle.to_dict())

    handle = model.submit(submission, credential=credential, on_submitted=save)
    events.append("returned")
    assert events == ["save", "returned"]
    assert contract.ProviderHandle.from_dict(read_json(path, dict)) == handle
    assert handle.scope == submission.scope
    assert handle.credential_ref == submission.credential_ref
    assert handle.provider_model_id == model_id
    assert handle.task_id == "task-1" and handle.request_id == "request-1"
    assert handle.schema_version == 1 and handle.submitted_at > 0
    assert handle.query_capability == contract.QueryCapability.BY_TASK_ID
    assert before == submission.intent.fingerprint
    url, options = transport[1][0]
    assert url == (
        submission.scope.base_url + "/api/v1/services/aigc/video-generation/video-synthesis"
    )
    assert options["allow_redirects"] is False and options["timeout"] == 120
    assert callable(options["auth"])
    assert options["headers"] == {
        "Authorization": f"Bearer {credential.secret}", "Content-Type": "application/json",
        "X-DashScope-Async": "enable", "X-DashScope-WorkSpace": "Original_Workspace",
    }
    parameters = {"duration": 5, "resolution": "720P", "seed": 0, "prompt_extend": False}
    if model_id.endswith("-flash"):
        parameters["audio"] = False
    assert options["json"] == {
        "model": model_id,
        "input": {
            "prompt": "海边日出", "img_url": submission.media[0].url, "negative_prompt": "",
        },
        "parameters": parameters,
    }
    assert credential.secret not in json.dumps(handle.to_dict())


@pytest.mark.parametrize("failure", [
    StorageError("disk full"), StorageError("sync", commit_uncertain=True),
    RuntimeError("writer failed"),
])
def test_writer_failure_is_original_exception_with_one_post(
    model, submission, credential, transport, failure,
):
    def save(handle):
        assert handle.task_id == "task-1"
        raise failure
    with pytest.raises(type(failure)) as raised:
        model.submit(submission, credential=credential, on_submitted=save)
    assert raised.value is failure
    assert len(transport[1]) == 1


def test_actual_atomic_writer_failure_leaves_prior_receipt_readable(
    monkeypatch, model, submission, credential, transport, tmp_path,
):
    path = tmp_path / "receipt.json"
    atomic_write_json(path, {"prior": True})
    def fail_replace(*args):
        raise OSError("full")
    monkeypatch.setattr("src.utils.json_storage.os.replace", fail_replace)
    with pytest.raises(StorageError):
        model.submit(
            submission, credential=credential,
            on_submitted=lambda h: atomic_write_json(path, h.to_dict()),
        )
    assert read_json(path, dict) == {"prior": True}
    assert len(transport[1]) == 1


@pytest.mark.parametrize("writer", [None, False, "writer", {}])
def test_invalid_writer_has_zero_http(contract, model, submission, credential, transport, writer):
    with pytest.raises(contract.ProviderBindingError):
        model.submit(submission, credential=credential, on_submitted=writer)
    assert transport[1] == []


def test_writer_argument_is_mandatory(model, submission, credential, transport):
    with pytest.raises(TypeError):
        model.submit(submission, credential=credential)
    assert transport[1] == []


@pytest.mark.parametrize("kind", [
    "async_function", "async_instance", "generator", "async_generator", "generator_instance",
])
def test_deferred_writers_reject_before_paid_post(
    contract, model, submission, credential, transport, kind,
):
    async def async_writer(handle):
        pytest.fail("async writer must not be scheduled")

    class AsyncWriter:
        async def __call__(self, handle):
            pytest.fail("async writer must not be scheduled")

    def generator_writer(handle):
        yield handle

    async def async_generator_writer(handle):
        yield handle

    class GeneratorWriter:
        def __call__(self, handle):
            yield handle

    writer = {
        "async_function": async_writer, "async_instance": AsyncWriter(),
        "generator": generator_writer, "async_generator": async_generator_writer,
        "generator_instance": GeneratorWriter(),
    }[kind]
    with pytest.raises(contract.ProviderBindingError):
        model.submit(submission, credential=credential, on_submitted=writer)
    assert transport[1] == []


@pytest.mark.parametrize("ack", [False, True, "saved"])
def test_writer_return_must_be_synchronous_none(model, submission, credential, transport, ack):
    with pytest.raises(TypeError, match="return None"):
        model.submit(submission, credential=credential, on_submitted=lambda h: ack)
    assert len(transport[1]) == 1


@pytest.mark.parametrize("kind", ["coroutine", "generator"])
def test_deferred_result_of_sync_wrapper_does_not_acknowledge_receipt(
    model, submission, credential, transport, kind,
):
    created = []

    async def later():
        pytest.fail("must not run a deferred receipt writer")

    def generate_later():
        yield "unconfirmed"

    def wrapper(handle):
        result = later() if kind == "coroutine" else generate_later()
        created.append(result)
        return result

    with pytest.raises(TypeError, match="return None"):
        model.submit(submission, credential=credential, on_submitted=wrapper)
    assert len(transport[1]) == 1
    state = inspect.getcoroutinestate if kind == "coroutine" else inspect.getgeneratorstate
    assert state(created[0]) in {inspect.CORO_CLOSED, inspect.GEN_CLOSED}


@pytest.mark.parametrize("response", [
    requests.Timeout("secret in transport"), requests.ConnectionError("secret in connection"),
    Response({}, 500), Response({"code": "InvalidApiKey", "message": "secret"}, 401),
    Response(ValueError("secret body")), Response([]), Response({"output": []}),
    Response({"output": {"task_id": "../other"}}), Response({"output": {"task_id": 7}}),
    Response({"output": {"task_status": "PENDING"}}), Response({}, 302), Response({}, 307),
])
def test_unknown_submission_never_retries_or_fabricates_failed(
    contract, model, submission, credential, transport, response,
):
    transport[0]["response"] = response
    saved = []
    with pytest.raises(contract.ProviderSubmitError) as raised:
        model.submit(submission, credential=credential, on_submitted=saved.append)
    assert raised.value.outcome == "unknown"
    assert "secret" not in str(raised.value)
    assert raised.value.__suppress_context__
    assert saved == [] and len(transport[1]) == 1


@pytest.mark.parametrize("status", [200, 201, 400, 500])
def test_usable_id_is_persisted_even_on_non200_or_bad_optional_diagnostics(
    model, submission, credential, transport, status,
):
    transport[0]["response"] = Response({
        "request_id": "bad\nsecret",
        "output": {"task_id": "accepted-task", "task_status": "NEW_STATE"},
        "message": "DO NOT ECHO PRIVATE BODY",
    }, status)
    saved = []
    handle = model.submit(submission, credential=credential, on_submitted=saved.append)
    assert saved == [handle] and handle.task_id == "accepted-task"
    assert handle.request_id is None
    assert len(transport[1]) == 1


@pytest.mark.parametrize("changes", [
    {"seed": -2}, {"seed": True}, {"seed": 2147483648}, {"resolution": "480p"},
    {"duration": 0}, {"duration": 16}, {"duration": 5.5}, {"audio": None}, {"audio": 0},
    {"ratio": "16:9"}, {"audio_url": "https://private.invalid/audio.mp3"}, {"unknown": False},
    {"prompt_extend": 0}, {"negative_prompt": None}, {"shot_type": "multi"},
])
def test_unsupported_or_invalid_frozen_params_reject_before_post(
    contract, model, submission, credential, transport, changes,
):
    intent = replace(submission.intent, params={**submission.intent.params, **changes})
    with pytest.raises(contract.ProviderBindingError):
        model.submit(
            replace(submission, intent=intent), credential=credential, on_submitted=lambda h: None,
        )
    assert transport[1] == []


@pytest.mark.parametrize("missing", ["duration", "resolution"])
def test_missing_cost_parameters_are_not_resolved_again(
    contract, model, submission, credential, transport, missing,
):
    params = dict(submission.intent.params)
    del params[missing]
    with pytest.raises(contract.ProviderBindingError):
        model.submit(replace(submission, intent=replace(submission.intent, params=params)),
                     credential=credential, on_submitted=lambda h: None)
    assert transport[1] == []


@pytest.mark.parametrize("changes", [
    {"provider_model_id": "happyhorse-1.1-i2v"}, {"provider_model_id": "wan3.0-video"},
    {"provider_model_id": "wan2.6-r2v"}, {"provider_model_id": "wan2.6-i2v"},
])
def test_unknown_route_or_mismatched_canonical_model_has_zero_post(
    contract, model, submission, credential, transport, changes,
):
    with pytest.raises(contract.ProviderBindingError):
        model.submit(
            replace(submission, **changes), credential=credential, on_submitted=lambda h: None,
        )
    assert transport[1] == []


def test_nonflash_audio_switch_is_not_silently_ignored(
    contract, model, submission, credential, transport,
):
    value = replace(
        submission, provider_model_id="wan2.6-i2v", intent=intent_for("wan2.6-i2v", audio=False),
    )
    with pytest.raises(contract.ProviderBindingError):
        model.submit(value, credential=credential, on_submitted=lambda h: None)
    assert transport[1] == []


@pytest.mark.parametrize("role", [
    "reference_image", "last_frame", "reference_video", "reference_audio",
])
def test_media_roles_cannot_be_silently_reinterpreted(
    contract, model, submission, credential, transport, role,
):
    media = replace(submission.media[0], version=replace(submission.media[0].version, role=role))
    with pytest.raises(contract.ProviderBindingError):
        model.submit(
            replace(submission, media=(media,)), credential=credential, on_submitted=lambda h: None,
        )
    assert transport[1] == []


@pytest.mark.parametrize("count", [0, 2])
def test_exactly_one_first_frame_required(
    contract, model, submission, credential, transport, count,
):
    with pytest.raises(contract.ProviderBindingError):
        model.submit(replace(submission, media=submission.media * count), credential=credential,
                     on_submitted=lambda h: None)
    assert transport[1] == []


@pytest.mark.parametrize("changes", [
    {"credential_ref": "other"}, {"scope": None},
])
def test_missing_or_mismatched_binding_has_zero_post(
    contract, model, submission, credential, transport, changes,
):
    with pytest.raises(contract.ProviderBindingError):
        model.submit(
            replace(submission, **changes), credential=credential, on_submitted=lambda h: None,
        )
    assert transport[1] == []


@pytest.mark.parametrize("change", [
    {"base_url": "https://attacker.invalid"}, {"workspace_id": "bad workspace"},
    {"region": "wrong-region"}, {"gateway": "trial"}, {"provider": "aliyun"},
])
def test_matching_but_invalid_scope_still_rejected(
    contract, model, submission, credential, transport, change,
):
    with pytest.raises(contract.ProviderBindingError):
        scope = replace(submission.scope, **change)
        model.submit(replace(submission, scope=scope), credential=replace(credential, scope=scope),
                     on_submitted=lambda h: None)
    assert transport[1] == []


@pytest.mark.parametrize("url", [
    "file:///tmp/input", "oss://unbound/input", "https://u:p@media.invalid/x",
    "https://media.invalid:bad/x", "https://media.invalid/a\x00b",
])
def test_prepared_url_has_explicit_limited_transport_shape(contract, submission, url):
    with pytest.raises(contract.ProviderBindingError):
        replace(submission.media[0], url=url)


def test_submit_can_send_verified_data_uri_without_fetching_current_media(
    contract, model, submission, credential, transport,
):
    data = b"provider payload first frame"
    version = MediaVersion(
        hashlib.sha256(data).hexdigest(), len(data), "first_frame", 0, "image/png",
    )
    media = contract.PreparedMedia.from_verified_bytes(
        version, data, max_bytes=20 * 1024 * 1024,
    )

    model.submit(
        replace(submission, media=(media,)), credential=credential, on_submitted=lambda h: None,
    )

    img_url = transport[1][0][1]["json"]["input"]["img_url"]
    assert img_url.startswith("data:image/png;base64,")
    assert base64.b64decode(img_url.partition(",")[2], validate=True) == data


def test_invalid_data_uri_binding_has_zero_http(contract, model, submission, credential, transport):
    data = b"expected first frame"
    version = MediaVersion(
        hashlib.sha256(data).hexdigest(), len(data), "first_frame", 0, "image/png",
    )
    bad_uri = "data:image/png;base64," + base64.b64encode(b"different bytes").decode("ascii")

    with pytest.raises(contract.ProviderBindingError):
        contract.PreparedMedia.from_data_uri(version, bad_uri, max_bytes=20 * 1024 * 1024)

    assert transport[1] == []


def test_submission_snapshots_mutable_mappings_and_labels_transport_unverified(submission):
    params, sources = dict(submission.intent.params), dict(submission.intent.sources)
    media = list(submission.media)
    frozen = replace(
        submission, intent=replace(submission.intent, params=params, sources=sources), media=media,
    )
    params["seed"] = 123
    sources["model_id"] = "changed"
    media.clear()
    assert frozen.intent.params["seed"] == 0 and frozen.intent.sources["model_id"] == "request"
    assert len(frozen.media) == 1
    assert frozen.media[0].transport_binding == "unverified"
    assert "signature=private" not in repr(frozen)
    with pytest.raises(TypeError):
        frozen.intent.params["seed"] = 123
    with pytest.raises(FrozenInstanceError):
        frozen.credential_ref = "other"


@pytest.mark.parametrize("field", [
    "model_id", "catalog_mode", "mode", "prompt", "rules_version", "preset_id", "preset_version",
])
def test_mutable_intent_metadata_cannot_enter_frozen_submission(contract, submission, field):
    with pytest.raises(contract.ProviderBindingError):
        replace(submission, intent=replace(submission.intent, **{field: ["mutable"]}))


@pytest.mark.parametrize("gateway", ["dashscope", "dashscope_workspace"])
def test_real_requests_has_one_post_no_netrc_or_redirect(
    monkeypatch, model, submission, credential, gateway,
):
    if gateway == "dashscope_workspace":
        scope = replace(submission.scope, gateway=gateway, workspace_id="llm-original",
                        base_url="https://llm-original.cn-beijing.maas.aliyuncs.com")
        submission = replace(submission, scope=scope)
        credential = replace(credential, scope=scope)
    prepared, netrc = [], []
    def send(session, request, **kwargs):
        assert kwargs["allow_redirects"] is False
        prepared.append(request)
        return Response({"output": {"task_id": "task-real-prepare"}})
    monkeypatch.setattr("src.models.wanx.requests.post", requests.api.post)
    monkeypatch.setattr(
        "requests.sessions.get_netrc_auth", lambda url: netrc.append(url) or ("other", "key"),
    )
    monkeypatch.setattr("requests.sessions.Session.send", send)
    model.submit(submission, credential=credential, on_submitted=lambda h: None)
    assert netrc == [] and len(prepared) == 1
    assert prepared[0].method == "POST"
    assert prepared[0].headers["Authorization"] == f"Bearer {credential.secret}"
    assert prepared[0].headers.get("X-DashScope-WorkSpace") == (
        submission.scope.workspace_id if gateway == "dashscope" else None
    )
    assert json.loads(prepared[0].body)["parameters"]["seed"] == 0


@pytest.mark.parametrize("duration,resolution,seed", [(2, "1080p", 2147483647), (15, "720p", 0)])
def test_supported_boundaries_and_explicit_false_watermark_survive(
    model, submission, credential, transport, duration, resolution, seed,
):
    intent = intent_for(duration=duration, resolution=resolution, seed=seed)
    # The official field exists; current catalog omits it. This deliberately
    # tests the adapter contract without modifying or claiming catalog parity.
    intent = replace(intent, params={**intent.params, "watermark": False})
    model.submit(
        replace(submission, intent=intent), credential=credential, on_submitted=lambda h: None,
    )
    params = transport[1][0][1]["json"]["parameters"]
    assert params["duration"] == duration and params["resolution"] == resolution.upper()
    assert params["seed"] == seed and params["watermark"] is False


def test_explicit_random_seed_is_omitted_from_http_payload_but_zero_survives(
    model, submission, credential, transport,
):
    random_intent = intent_for(seed=-1, duration=5, resolution="720p")
    model.submit(
        replace(submission, intent=random_intent), credential=credential, on_submitted=lambda h: None,
    )
    random_params = transport[1][-1][1]["json"]["parameters"]
    assert "seed" not in random_params

    zero_intent = intent_for(seed=0, duration=5, resolution="720p")
    model.submit(
        replace(submission, intent=zero_intent), credential=credential, on_submitted=lambda h: None,
    )
    zero_params = transport[1][-1][1]["json"]["parameters"]
    assert zero_params["seed"] == 0


def test_omitted_flags_and_random_seed_remain_delegated(model, submission, credential, transport):
    intent = replace(submission.intent, params={"duration": 5, "resolution": "720p"})
    model.submit(
        replace(submission, intent=intent), credential=credential, on_submitted=lambda h: None,
    )
    assert transport[1][0][1]["json"]["parameters"] == {"duration": 5, "resolution": "720P"}


def test_no_credential_has_zero_http(contract, model, submission, transport):
    with pytest.raises(contract.ProviderBindingError):
        model.submit(submission, credential=None, on_submitted=lambda h: None)
    assert transport[1] == []


def test_success_does_not_log_sensitive_prompt_url_or_response(
    model, submission, credential, transport, caplog,
):
    transport[0]["response"] = Response({
        "output": {"task_id": "task-1"}, "message": "PRIVATE_RESPONSE",
    })
    model.submit(submission, credential=credential, on_submitted=lambda h: None)
    assert "PRIVATE_RESPONSE" not in caplog.text and "signature=private" not in caplog.text
    assert credential.secret not in caplog.text and submission.intent.prompt not in caplog.text
