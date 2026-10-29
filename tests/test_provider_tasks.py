"""Read existing provider tasks through the real factory, never generation."""

import importlib
import json
from dataclasses import replace

import pytest
import requests

from src.config import Config
from src.models.factory import ModelFactory
from src.models.wanx import WanxModel


def test_factory_adapter_exposes_independent_query():
    config = Config()
    config.config = {"model": {"name": "wanx"}}
    assert callable(getattr(ModelFactory.create_model(config), "query", None))


@pytest.fixture
def contract():
    assert hasattr(WanxModel, "query"), "WanxModel needs independent query capability"
    return importlib.import_module("src.models.provider_tasks")


@pytest.fixture
def scope(contract):
    return contract.ProviderScope(
        provider="dashscope", backend="dashscope", gateway="dashscope_workspace",
        base_url="https://llm-original.cn-beijing.maas.aliyuncs.com",
        region="cn-beijing", workspace_id="llm-original", account_identity="account-original",
    )


@pytest.fixture
def handle(contract, scope):
    return contract.ProviderHandle(
        scope=scope, credential_ref="credentials/original-v1", provider_model_id="wan3.0-video",
        task_id="task-123", request_id="submit-request-1",
        query_capability=contract.QueryCapability.BY_TASK_ID,
    )


@pytest.fixture
def credential(contract, handle):
    return contract.ResolvedCredential(
        scope=handle.scope, credential_ref=handle.credential_ref, secret="test-only-original-key",
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
    state = {"response": Response({"request_id": "query-request-2", "output": {
        "task_id": "task-123", "task_status": "RUNNING",
    }})}

    def get(url, **kwargs):
        calls.append((url, kwargs))
        response = state["response"]
        if isinstance(response, Exception):
            raise response
        return response

    def forbidden(*args, **kwargs):
        pytest.fail("query must not generate, submit, download, or read current credentials/routes")

    monkeypatch.setattr("src.models.wanx.requests.get", get)
    monkeypatch.setattr("src.models.wanx.requests.post", forbidden)
    monkeypatch.setattr("src.models.wanx.requests.Session.get", forbidden)
    monkeypatch.setattr("src.models.wanx.get_provider_base_url", forbidden)
    monkeypatch.setattr(WanxModel, "generate", forbidden)
    monkeypatch.setattr(WanxModel, "_download_video", forbidden)
    monkeypatch.setattr(WanxModel, "api_key", property(forbidden))
    monkeypatch.setattr("src.models.wanx.VideoSynthesis.async_call", forbidden)
    monkeypatch.setattr("src.models.wanx.VideoSynthesis.wait", forbidden)
    return state, calls


@pytest.fixture
def model(transport):
    config = Config()
    config.config = {"model": {"name": "wanx", "params": {"model_name": "unrelated-model"}}}
    result = ModelFactory.create_model(config)
    assert isinstance(result, WanxModel)
    return result


@pytest.mark.parametrize("provider_model", ["wan2.6-i2v", "happyhorse-1.1-i2v", "wan3.0-video"])
def test_factory_queries_original_scope_once_without_current_config(
    monkeypatch, model, handle, credential, transport, provider_model,
):
    monkeypatch.setenv("DASHSCOPE_BASE_URL", "https://other.invalid")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "wrong-current-key")
    monkeypatch.setenv("DASHSCOPE_WORKSPACE_ID", "another-workspace")
    result = model.query(replace(handle, provider_model_id=provider_model), credential=credential)

    assert result.status.value == "running"
    assert result.task_id == handle.task_id
    assert result.request_id == "query-request-2"
    assert result.source_handle == replace(handle, provider_model_id=provider_model)
    assert len(transport[1]) == 1
    url, captured_options = transport[1][0]
    options = dict(captured_options)
    assert callable(options.pop("auth"))
    assert url == handle.scope.base_url + "/api/v1/tasks/task-123"
    assert options == {
        "headers": {"Authorization": "Bearer test-only-original-key"},
        "timeout": 30, "allow_redirects": False,
    }


@pytest.mark.parametrize("raw, normalized", [
    ("PENDING", "pending"), ("RUNNING", "running"), ("SUCCEEDED", "succeeded"),
    ("FAILED", "failed"), ("CANCELED", "canceled"), ("UNKNOWN", "unknown"),
    ("NEW_PROVIDER_STATE", "unknown"),
])
def test_remote_states_do_not_conflate_unknown_with_failed(
    model, handle, credential, transport, raw, normalized,
):
    transport[0]["response"] = Response({"request_id": "query-1", "output": {
        "task_status": raw, "video_url": "https://media.example.invalid/result.mp4?signature=test",
        "code": "Provider.Error", "message": "DO NOT RETAIN THIS RAW DIAGNOSTIC",
        "unneeded": "DO NOT RETAIN",
    }})
    result = model.query(handle, credential=credential)
    assert result.status.value == normalized
    assert result.raw_status == raw
    assert result.source_handle == handle
    assert result.error_code == "Provider.Error"
    assert result.video_url is not None if raw == "SUCCEEDED" else result.video_url is None
    assert "DO NOT RETAIN" not in repr(result.to_dict())
    assert len(transport[1]) == 1


@pytest.mark.parametrize("payload", [
    [], {}, {"output": None}, {"output": []}, {"output": {}},
    {"output": {"task_status": 0}}, {"output": {"task_status": ""}},
    {"output": {"task_status": "SUCCEEDED"}},
    {"output": {"task_status": "SUCCEEDED", "video_url": "javascript:alert(1)"}},
    {"output": {"task_status": "SUCCEEDED", "video_url": "https://user:pass@media.invalid/v"}},
    pytest.param({"output": {
        "task_status": "SUCCEEDED", "video_url": "https://media.invalid/a\x00b.mp4",
    }}, id="output-url-control-character"),
    pytest.param({"output": {
        "task_status": "SUCCEEDED", "video_url": "https://media.invalid:invalid/result.mp4",
    }}, id="output-url-invalid-port"),
    {"output": {"task_status": "RUNNING", "task_id": "different-task"}},
    {"request_id": [], "output": {"task_status": "RUNNING"}},
    ValueError("RAW JSON MUST NOT APPEAR"),
])
def test_malformed_query_is_protocol_error_not_failed_task(
    contract, model, handle, credential, transport, payload,
):
    transport[0]["response"] = Response(payload)
    with pytest.raises(contract.ProviderQueryError) as raised:
        model.query(handle, credential=credential)
    assert raised.value.kind == "protocol"
    assert "RAW JSON" not in str(raised.value)
    assert len(transport[1]) == 1


@pytest.mark.parametrize("status", [301, 302, 307, 308, 401, 403, 404, 429, 500])
def test_http_failure_is_one_query_without_redirect_or_resubmission(
    contract, model, handle, credential, transport, status,
):
    transport[0]["response"] = Response({"message": credential.secret}, status)
    with pytest.raises(contract.ProviderQueryError) as raised:
        model.query(handle, credential=credential)
    assert raised.value.kind == "http"
    assert raised.value.status_code == status
    assert credential.secret not in str(raised.value)
    assert len(transport[1]) == 1


@pytest.mark.parametrize("failure", [requests.Timeout, requests.ConnectionError])
def test_network_failure_is_unknown_observation_not_remote_failure(
    contract, model, handle, credential, transport, failure,
):
    transport[0]["response"] = failure("RAW TRANSPORT SECRET")
    with pytest.raises(contract.ProviderQueryError) as raised:
        model.query(handle, credential=credential)
    assert raised.value.kind == "transport"
    assert "RAW TRANSPORT" not in str(raised.value)
    assert raised.value.__suppress_context__
    assert len(transport[1]) == 1


def test_real_requests_preparation_does_not_replace_bound_bearer_with_netrc(
    monkeypatch, model, handle, credential,
):
    prepared = []
    netrc_reads = []

    def ambient_netrc(url):
        netrc_reads.append(url)
        return "unrelated-account", "unrelated-secret"

    def send(session, request, **kwargs):
        prepared.append(request)
        return Response({"output": {"task_status": "RUNNING"}})

    # Exercise requests' actual header/auth preparation; replace only I/O.
    monkeypatch.setattr("src.models.wanx.requests.get", requests.api.get)
    monkeypatch.setattr("requests.sessions.get_netrc_auth", ambient_netrc)
    monkeypatch.setattr("requests.sessions.Session.send", send)
    model.query(handle, credential=credential)

    assert netrc_reads == []
    assert len(prepared) == 1
    assert prepared[0].headers["Authorization"] == f"Bearer {credential.secret}"


@pytest.mark.parametrize("change", [
    {"scope": None}, {"credential_ref": None}, {"query_capability": "unknown"},
    {"query_capability": "unsupported"},
])
def test_incomplete_or_unqueryable_history_has_zero_http(
    contract, model, handle, credential, transport, change,
):
    with pytest.raises(contract.ProviderBindingError):
        model.query(replace(handle, **change), credential=credential)
    assert transport[1] == []


@pytest.mark.parametrize("change", [
    {"credential_ref": "different-key-slot"},
    {"scope_field": "workspace_id", "value": "llm-other"},
    {"scope_field": "region", "value": "us-east-1"},
    {"scope_field": "account_identity", "value": "different-account"},
    {"scope_field": "base_url", "value": "https://dashscope.aliyuncs.com"},
])
def test_mismatched_explicit_binding_has_zero_http(
    contract, model, handle, credential, transport, change,
):
    if "scope_field" in change:
        bound = replace(credential, scope=replace(credential.scope, **{
            change["scope_field"]: change["value"],
        }))
    else:
        bound = replace(credential, **change)
    with pytest.raises(contract.ProviderBindingError):
        model.query(handle, credential=bound)
    assert transport[1] == []


def test_missing_credential_has_zero_http(contract, model, handle, transport):
    with pytest.raises(contract.ProviderBindingError):
        model.query(handle, credential=None)
    assert transport[1] == []


@pytest.mark.parametrize("url", [
    "http://dashscope.aliyuncs.com", "https://attacker.invalid",
    "https://dashscope.aliyuncs.com.attacker.invalid", "https://127.0.0.1",
    "https://dashscope.aliyuncs.com@attacker.invalid",
    "https://user:pass@dashscope.aliyuncs.com", "https://dashscope.aliyuncs.com:444",
    "https://dashscope.aliyuncs.com/api/v1", "https://dashscope.aliyuncs.com/../other",
    "https://dashscope.aliyuncs.com?target=evil", "https://dashscope.aliyuncs.com#fragment",
    "https://dashscope.aliyuncs.com\\@attacker.invalid", "https://dashscope.aliyuncs.com.",
    "https://dashscope.aliyuncs.com\n", "https://dashscope.aliyuncs.com/%2fother",
    "https://trial.cn-beijing.maas.aliyuncs.com", "https://any.aliyuncs.com",
])
def test_disallowed_receipt_url_is_rejected_before_bearer_leaves_process(
    contract, model, handle, credential, transport, url,
):
    with pytest.raises(contract.ProviderBindingError):
        bad_scope = replace(handle.scope, base_url=url)
        model.query(
            replace(handle, scope=bad_scope), credential=replace(credential, scope=bad_scope),
        )
    assert transport[1] == []


@pytest.mark.parametrize("task_id", ["../other", "a/b", "a%2Fb", "a?x=y", "a#z", "a\\b", "a\n", ""])
def test_task_id_cannot_change_query_path(contract, model, handle, credential, transport, task_id):
    with pytest.raises(contract.ProviderBindingError):
        model.query(replace(handle, task_id=task_id), credential=credential)
    assert transport[1] == []


@pytest.mark.parametrize("host, region", [
    ("dashscope.aliyuncs.com", "cn-beijing"),
    ("dashscope-intl.aliyuncs.com", "ap-southeast-1"),
    ("dashscope-us.aliyuncs.com", "us-east-1"),
    ("cn-hongkong.dashscope.aliyuncs.com", "cn-hongkong"),
])
def test_official_classic_scope_queries_without_workspace_inference(
    contract, model, handle, credential, transport, host, region,
):
    original = replace(handle.scope, gateway="dashscope", base_url=f"https://{host}/",
                       region=region, workspace_id=None, account_identity=None)
    model.query(replace(handle, scope=original), credential=replace(credential, scope=original))
    assert transport[1][0][0] == f"https://{host}/api/v1/tasks/task-123"
    assert "X-DashScope-WorkSpace" not in transport[1][0][1]["headers"]


@pytest.mark.parametrize("workspace", [None, "Workspace_Original-1", "trial"])
def test_real_requests_preserves_classic_workspace_and_original_gateway(
    monkeypatch, model, handle, credential, workspace,
):
    scope = replace(handle.scope, gateway="dashscope", base_url="https://dashscope.aliyuncs.com",
                    workspace_id=workspace)
    prepared = []
    netrc_reads = []
    def ambient_netrc(url):
        netrc_reads.append(url)
        return "other-account", "other-key"
    def send(session, request, **kwargs):
        prepared.append(request)
        assert kwargs["allow_redirects"] is False
        return Response({"output": {"task_status": "RUNNING"}})
    monkeypatch.setenv("DASHSCOPE_WORKSPACE_ID", "other-workspace")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "other-key")
    monkeypatch.setenv("DASHSCOPE_BASE_URL", "https://trial.cn-beijing.maas.aliyuncs.com")
    monkeypatch.setattr("src.models.wanx.requests.get", requests.api.get)
    monkeypatch.setattr("requests.sessions.get_netrc_auth", ambient_netrc)
    monkeypatch.setattr("requests.sessions.Session.send", send)

    model.query(replace(handle, scope=scope), credential=replace(credential, scope=scope))

    assert netrc_reads == []
    assert len(prepared) == 1
    assert prepared[0].method == "GET"
    assert prepared[0].url == "https://dashscope.aliyuncs.com/api/v1/tasks/task-123"
    assert prepared[0].headers["Authorization"] == f"Bearer {credential.secret}"
    assert prepared[0].headers.get("X-DashScope-WorkSpace") == workspace


@pytest.mark.parametrize("workspace", ["", "one\r\nX-Other: value", "one\ttwo", "one two", "llm-中文", "llm-\u0085-id"])
def test_unsafe_classic_workspace_is_rejected_before_http(
    contract, model, handle, credential, transport, workspace,
):
    with pytest.raises(contract.ProviderBindingError):
        scope = replace(handle.scope, gateway="dashscope", base_url="https://dashscope.aliyuncs.com",
                        workspace_id=workspace)
        model.query(replace(handle, scope=scope), credential=replace(credential, scope=scope))
    assert transport[1] == []


@pytest.mark.parametrize("change", [
    {"workspace_id": None}, {"workspace_id": "other-workspace"},
    {"gateway": "dashscope_workspace"},
])
def test_classic_workspace_requires_the_exact_credential_binding(
    contract, model, handle, credential, transport, change,
):
    scope = replace(handle.scope, gateway="dashscope", base_url="https://dashscope.aliyuncs.com")
    bound = replace(credential, scope=replace(scope, **change))
    with pytest.raises(contract.ProviderBindingError):
        model.query(replace(handle, scope=scope), credential=bound)
    assert transport[1] == []


def test_classic_workspace_header_cannot_authorize_a_workspace_gateway_host(
    contract, model, handle, credential, transport,
):
    scope = replace(handle.scope, gateway="dashscope")
    with pytest.raises(contract.ProviderBindingError):
        model.query(replace(handle, scope=scope), credential=replace(credential, scope=scope))
    assert transport[1] == []


@pytest.mark.parametrize("changes", [
    {"provider": "kling"}, {"backend": "vendor"}, {"gateway": "unknown"},
    {"workspace_id": None}, {"workspace_id": "llm-other"}, {"region": None},
    {"region": "us-east-1"},
])
def test_invalid_scope_remains_invalid_even_with_matching_binding(
    contract, model, handle, credential, transport, changes,
):
    with pytest.raises(contract.ProviderBindingError):
        invalid = replace(handle.scope, **changes)
        model.query(replace(handle, scope=invalid), credential=replace(credential, scope=invalid))
    assert transport[1] == []


def test_receipt_roundtrip_retains_original_nonsecret_scope(contract, handle, credential):
    stored = json.loads(json.dumps(handle.to_dict()))
    assert stored["schema_version"] == 1
    assert stored["submitted_at"] is None
    assert stored["scope"]["account_identity"] == "account-original"
    assert contract.ProviderHandle.from_dict(stored) == handle
    assert credential.secret not in repr(credential)
    assert credential.secret not in json.dumps(credential.to_dict())
    assert "secret" not in credential.to_dict()
    assert credential.secret not in json.dumps(stored)


def test_legacy_receipt_does_not_gain_scope_time_or_capability(contract):
    legacy = contract.ProviderHandle.from_dict({
        "provider_model_id": "wan2.6-i2v", "task_id": "legacy-task",
    })
    assert legacy.scope is None
    assert legacy.credential_ref is None
    assert legacy.submitted_at is None
    assert legacy.query_capability == contract.QueryCapability.UNKNOWN


@pytest.mark.parametrize("where, key", [
    ("handle", "api_key"), ("handle", "secret"), ("scope", "api_key"),
    ("scope", "secret"), ("handle", "unexpected"),
])
def test_receipt_deserialization_rejects_secret_or_unknown_fields(contract, handle, where, key):
    raw = handle.to_dict()
    target = raw if where == "handle" else raw["scope"]
    target[key] = "DO NOT ECHO THIS SECRET"
    with pytest.raises(contract.ProviderBindingError) as raised:
        contract.ProviderHandle.from_dict(raw)
    assert "DO NOT ECHO" not in str(raised.value)


@pytest.mark.parametrize("field, value", [
    ("schema_version", 2), ("schema_version", True), ("submitted_at", float("nan")),
    pytest.param("submitted_at", 10 ** 1000, id="unrepresentable-submission-time"),
    ("query_capability", "invented"),
])
def test_receipt_rejects_unsupported_schema_and_invalid_values(contract, handle, field, value):
    raw = handle.to_dict()
    raw[field] = value
    with pytest.raises(contract.ProviderBindingError):
        contract.ProviderHandle.from_dict(raw)
