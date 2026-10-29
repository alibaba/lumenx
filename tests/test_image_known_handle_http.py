"""Opt-in HTTP image submit/query boundaries; no Studio or media success claims.

Only external Session.send supplies protocol results. The Session factory
instruments ambient state, and legacy routing is fail-fast. Callback JSON bytes
exercise ordering, not Studio transaction durability or a process restart.
No image is downloaded or decoded by these new interfaces.
"""

from dataclasses import replace
import json
import os
from typing import Any

import pytest
import requests

from src.models import bound_image_http as bound
from src.models import mulerouter, provider_tasks as tasks, wan27_image_http as wan
from src.models.gpt_image2_edit_contract import (
    GPT_IMAGE2_EDIT_CONTRACT_VERSION, GPT_IMAGE2_EDIT_SIZES_CONTRACT_VERSION,
)
from src.utils.json_storage import StorageError
from tests.frozen_image_fixtures import rgb_png_bytes
from tests.test_wan27_bound_image import response

SYNTHETIC_IMAGE_CREDENTIAL = "synthetic-original-image-secret"
GPT_ID = "e547b8d2-71d8-4c26-9129-c77cfa151e12"
WAN_ID = "wan_original-123"
IMAGE_URL = "https://images.example.invalid/original.png?signature=synthetic"
PROMPT = "Original author image prompt"
OPERATIONS = ("wan", "gpt_generation", "gpt_edit")


@pytest.fixture
def transport(monkeypatch):
    original_session = requests.Session
    state = {"queue": [], "calls": [], "sessions": [], "responses": [], "on_send": None}

    def session():
        item = original_session()
        item.auth = ("ambient-user", "ambient-password")
        item.cookies.set("ambient", "cookie")
        item.headers["X-Ambient"] = "ambient-header"
        state["sessions"].append(item)
        return item

    def send(_session, prepared, **kwargs):
        state["calls"].append((prepared, kwargs))
        if state["on_send"] is not None:
            state["on_send"](prepared)
        assert state["queue"], f"Unexpected extra HTTP operation: {prepared.method}"
        item = state["queue"].pop(0)
        if isinstance(item, BaseException):
            raise item
        item.request, item.url = prepared, prepared.url
        state["responses"].append(item)
        return item

    def forbidden(*_args, **_kwargs):
        raise AssertionError("New bound image operation used ambient or legacy routing")

    monkeypatch.setattr(requests, "Session", session)
    monkeypatch.setattr(requests.sessions.Session, "send", send)
    monkeypatch.setattr(requests.sessions, "get_netrc_auth", forbidden)
    monkeypatch.setattr(mulerouter.MuleRouterImageModel, "_use_cli_backend", forbidden)
    monkeypatch.setattr(mulerouter.MuleRouterImageModel, "_get_base_url_for_model", forbidden)
    monkeypatch.setenv("HTTPS_PROXY", "https://ambient.invalid:444")
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", "/synthetic/nonexistent-ca")
    return state


def credential(operation):
    scope = tasks.ProviderScope(
        "dashscope", "dashscope", "dashscope", "https://dashscope.aliyuncs.com",
        region="cn-beijing", workspace_id="original-workspace",
    ) if operation == "wan" else tasks.ProviderScope(
        "mulerouter", "mulerouter", "mulerouter_http", "https://api.mulerouter.ai",
    )
    return tasks.ResolvedCredential(scope, "credential/original", SYNTHETIC_IMAGE_CREDENTIAL)


def submit(operation, tmp_path, writer, **changes):
    original = credential(operation)
    if operation == "wan":
        options: dict[str, Any] = {"model_name": "wan2.7-image", "size": "576*1024", "seed": 0, "watermark": False}
        options.update(changes)
        return wan.submit_bound_wan27_image(PROMPT, original, on_submitted=writer, **options)
    model = mulerouter.MuleRouterImageModel(
        {"output_root": str(tmp_path)}, env={"MULERUN_TOKEN": "synthetic-ignored-token"},
    )
    options = {"size": "1024x1536"}
    if operation == "gpt_edit":
        reference = tmp_path / "reference.png"
        reference.write_bytes(rgb_png_bytes(width=16, height=16))
        options.update(image_contract_version=GPT_IMAGE2_EDIT_SIZES_CONTRACT_VERSION,
                       ref_image_paths=[str(reference)])
    options.update(changes)
    return model.submit_with_bound_http(PROMPT, original, on_submitted=writer, **options)


def query(operation, handle, original=None):
    original = credential(operation) if original is None else original
    if operation == "wan":
        return wan.query_bound_wan27_image(handle, credential=original)
    return mulerouter.MuleRouterImageModel.query_with_bound_http(handle, credential=original)


def info(status="pending", task_id=GPT_ID):
    return {"id": task_id, "status": status, "created_at": "2026-10-25T00:00:00Z",
            "updated_at": "2026-10-25T00:00:00Z"}


def created(operation, status=None):
    if operation == "wan":
        return response({"request_id": "request_original", "output": {
            "task_id": WAN_ID, "task_status": "PENDING"}}, status=200 if status is None else status)
    return response({"task_info": info()}, status=202 if status is None else status)


def successful(operation) -> dict[str, Any]:
    if operation == "wan":
        return {"output": {"task_id": WAN_ID, "task_status": "SUCCEEDED", "choices": [
            {"message": {"role": "assistant", "content": [{"type": "image", "image": IMAGE_URL}]}}
        ]}}
    return {"task_info": info("completed"), "images": [IMAGE_URL]}


def format_handle(operation):
    # Query-negative controls construct only a format identity, never a paid receipt.
    original = credential(operation)
    contract = {
        "wan": tasks.ProviderImageQueryContract.WAN27_IMAGE,
        "gpt_generation": tasks.ProviderImageQueryContract.GPT_IMAGE2_GENERATION,
        "gpt_edit": tasks.ProviderImageQueryContract.GPT_IMAGE2_EDIT,
    }[operation]
    return tasks.ProviderHandle(
        schema_version=2, provider_model_id="wan2.7-image" if operation == "wan" else "gpt-image-2",
        task_id=WAN_ID if operation == "wan" else GPT_ID, scope=original.scope,
        credential_ref=original.credential_ref, query_capability=tasks.QueryCapability.BY_TASK_ID,
        query_contract=contract, submitted_at=1,
    )


@pytest.mark.parametrize("operation", OPERATIONS)
def test_submit_returns_only_after_saved_callback_and_query_uses_saved_original(operation, tmp_path, transport):
    transport["queue"] = [created(operation), response(successful(operation))]
    saved_path = tmp_path / "saved-handle.json"
    writes = []

    def save(handle):
        assert [call.method for call, _ in transport["calls"]] == ["POST"]
        with saved_path.open("w") as stream:
            json.dump(handle.to_dict(), stream)
            stream.flush()
            os.fsync(stream.fileno())
        writes.append(handle.to_dict())

    def before_send(prepared):
        if prepared.method == "GET":
            assert json.loads(saved_path.read_text()) == writes[0]

    transport["on_send"] = before_send
    returned = submit(operation, tmp_path, save)
    assert len(writes) == 1 and len(transport["calls"]) == 1
    saved = tasks.ProviderHandle.from_dict(json.loads(saved_path.read_text()))
    assert returned == saved
    reference_bytes = None
    if operation == "gpt_edit":
        reference = tmp_path / "reference.png"
        reference_bytes = reference.read_bytes()
        reference.unlink()
    observed = query(operation, saved)
    assert observed.status is tasks.ProviderTaskStatus.SUCCEEDED
    assert observed.task_id == saved.task_id and observed.source_handle == saved
    assert observed.image_url == IMAGE_URL and observed.video_url is None
    calls = transport["calls"]
    assert [call.method for call, _ in calls] == ["POST", "GET"]
    expected_post = ("/api/v1/services/aigc/image-generation/generation" if operation == "wan"
                     else "/vendors/openai/v1/gpt-image-2/" + ("edit" if operation == "gpt_edit" else "generation"))
    assert calls[0][0].url == credential(operation).scope.base_url + expected_post
    assert calls[1][0].url == credential(operation).scope.base_url + tasks.image_query_path(saved)
    payload = json.loads(calls[0][0].body)
    if operation == "wan":
        assert payload == {"model": "wan2.7-image", "input": {"messages": [
            {"role": "user", "content": [{"text": PROMPT}]}]},
            "parameters": {"size": "576*1024", "n": 1, "watermark": False, "seed": 0}}
        assert calls[0][0].headers["X-DashScope-Async"] == "enable"
        assert all(call.headers["X-DashScope-WorkSpace"] == "original-workspace" for call, _ in calls)
    elif operation == "gpt_generation":
        assert payload == {"prompt": PROMPT, "size": "1024x1536", "quality": "high", "n": 1}
    else:
        import base64
        assert isinstance(reference_bytes, bytes)
        assert payload == {"prompt": PROMPT, "size": "1024x1536", "n": 1, "images": [
            "data:image/png;base64," + base64.b64encode(reference_bytes).decode()
        ]}
    for call, options in calls:
        assert call.headers["Authorization"] == "Bearer " + SYNTHETIC_IMAGE_CREDENTIAL
        assert "Cookie" not in call.headers and "X-Ambient" not in call.headers
        assert call.headers["Accept-Encoding"] == "identity"
        assert options["allow_redirects"] is False and not options["proxies"]
    assert calls[0][1]["timeout"] == (10, 120) and calls[1][1]["timeout"] == (10, 30)
    assert all(not session.trust_env and session.auth is None for session in transport["sessions"])
    assert all(item.raw.closed for item in transport["responses"])
    assert not transport["queue"]


@pytest.mark.parametrize("operation", OPERATIONS)
@pytest.mark.parametrize("failure", ["storage", "non_none"])
def test_callback_failure_never_returns_a_handle_or_enters_query(operation, failure, tmp_path, transport):
    transport["queue"] = [created(operation), response(successful(operation))]
    seen = []
    failure_object = StorageError("synthetic durable callback failure")

    def save(handle):
        seen.append(handle)
        if failure == "storage":
            raise failure_object
        return False

    with pytest.raises(StorageError if failure == "storage" else TypeError) as caught:
        submit(operation, tmp_path, save)
    if failure == "storage":
        assert caught.value is failure_object
    assert len(seen) == 1 and [call.method for call, _ in transport["calls"]] == ["POST"]
    assert len(transport["queue"]) == 1 and transport["responses"][0].raw.closed


@pytest.mark.parametrize("operation", ["wan", "gpt_generation"])
@pytest.mark.parametrize("deferred", ["async", "generator"])
def test_deferred_receipt_writer_is_rejected_before_post(operation, deferred, tmp_path, transport):
    async def later(_handle):
        return None

    def generator(_handle):
        yield None

    with pytest.raises(ValueError, match="synchronous"):
        submit(operation, tmp_path, later if deferred == "async" else generator)
    assert transport["calls"] == []


@pytest.mark.parametrize("returned", ["coroutine", "generator"])
def test_sync_wrapper_cannot_defer_durable_write(returned, tmp_path, transport):
    transport["queue"] = [created("gpt_generation"), response(successful("gpt_generation"))]
    deferred = []
    bodies = []

    async def later():
        bodies.append("later")

    def generator():
        bodies.append("generator")
        yield None

    def wrapper(_handle):
        value = later() if returned == "coroutine" else generator()
        deferred.append(value)
        return value

    try:
        with pytest.raises(TypeError, match="return None synchronously"):
            submit("gpt_generation", tmp_path, wrapper)
        assert len(deferred) == 1 and not bodies
        frame = deferred[0].cr_frame if returned == "coroutine" else deferred[0].gi_frame
        assert frame is None
        assert [call.method for call, _ in transport["calls"]] == ["POST"]
        assert len(transport["queue"]) == 1
    finally:
        for value in deferred:
            value.close()


@pytest.mark.parametrize("operation,fault", [
    ("wan", "timeout"), ("gpt_generation", "503"),
    ("gpt_generation", "alternate_id"), ("gpt_edit", "invalid_uuid"),
])
def test_unknown_submission_never_retries_or_calls_writer(operation, fault, tmp_path, transport):
    bad = (requests.ReadTimeout("synthetic-private-transport-detail") if fault == "timeout"
           else response({}, status=503) if fault == "503"
           else response({"id": GPT_ID}, status=202) if fault == "alternate_id"
           else response({"task_info": info(task_id="synthetic-not-uuid")}, status=202))
    transport["queue"] = [bad]
    writes = []
    with pytest.raises(tasks.ProviderSubmitError) as caught:
        submit(operation, tmp_path, writes.append)
    assert caught.value.outcome == "unknown" and "synthetic-private" not in str(caught.value)
    assert len(transport["calls"]) == 1 and not writes


@pytest.mark.parametrize("contract,size", [
    (GPT_IMAGE2_EDIT_CONTRACT_VERSION, "1024x1024"),
    (GPT_IMAGE2_EDIT_SIZES_CONTRACT_VERSION, "1536x1024"),
])
def test_explicit_edit_contract_keeps_existing_square_and_landscape_sizes(contract, size, tmp_path, transport):
    transport["queue"] = [created("gpt_edit")]
    writes = []
    handle = submit("gpt_edit", tmp_path, writes.append, image_contract_version=contract, size=size)
    assert writes == [handle] and handle.query_contract is tasks.ProviderImageQueryContract.GPT_IMAGE2_EDIT
    prepared = transport["calls"][0][0]
    assert prepared.url.endswith("/gpt-image-2/edit")
    payload = json.loads(prepared.body)
    assert payload["size"] == size and set(payload) == {"prompt", "size", "n", "images"}
    assert payload["n"] == 1 and len(transport["calls"]) == 1


def test_usable_id_on_unusual_http_is_retained_as_defensive_behavior_only(tmp_path, transport):
    transport["queue"] = [created("gpt_generation", status=503)]
    writes = []
    returned = submit("gpt_generation", tmp_path, lambda handle: writes.append(handle))
    assert writes == [returned] and len(transport["calls"]) == 1
    assert returned.task_id == GPT_ID


@pytest.mark.parametrize("operation,change", [
    ("wan", "wrong"), ("gpt_generation", "missing"), ("gpt_edit", "wrong"),
])
def test_query_requires_actual_same_id_echo(operation, change, transport):
    body = successful(operation)
    owner, field = (body["output"], "task_id") if operation == "wan" else (body["task_info"], "id")
    if change == "missing":
        del owner[field]
    else:
        owner[field] = "another-task" if operation == "wan" else "ff47b8d2-71d8-4c26-9129-c77cfa151e12"
    transport["queue"] = [response(body)]
    with pytest.raises(tasks.ProviderQueryError, match="protocol"):
        query(operation, format_handle(operation))
    assert [call.method for call, _ in transport["calls"]] == ["GET"]


def test_declared_aliases_and_unknown_statuses_do_not_invent_terminal_results(transport):
    expected = {
        "pending": "pending", "queued": "pending", "processing": "running", "running": "running",
        "completed": "succeeded", "succeeded": "succeeded", "failed": "failed",
        "cancelled": "unknown", "canceled": "unknown", "success": "unknown", "error": "unknown",
    }
    handle = format_handle("gpt_generation")
    for raw, normalized in expected.items():
        body: dict[str, Any] = {"task_info": info(raw)}
        if normalized == "succeeded":
            body["images"] = [IMAGE_URL]
        transport["queue"].append(response(body))
        observed = query("gpt_generation", handle)
        assert observed.status.value == normalized and observed.raw_status == raw
        assert observed.task_id == GPT_ID and observed.source_handle == handle
    assert len(transport["calls"]) == len(expected)
    assert all(call.method == "GET" for call, _ in transport["calls"])


def test_wan_query_keeps_exact_declared_statuses_and_workspace(transport):
    handle = format_handle("wan")
    expected = {"PENDING": "pending", "RUNNING": "running", "FAILED": "failed",
                "CANCELED": "canceled", "SUCCEEDED": "succeeded", "succeeded": "unknown"}
    for raw, normalized in expected.items():
        body = successful("wan") if raw == "SUCCEEDED" else {"output": {
            "task_id": WAN_ID, "task_status": raw}}
        transport["queue"].append(response(body))
        observed = query("wan", handle)
        assert observed.status.value == normalized and observed.raw_status == raw
        assert observed.source_handle == handle
    assert len(transport["calls"]) == len(expected)
    assert all(call.method == "GET" and call.headers["X-DashScope-WorkSpace"] == "original-workspace"
               for call, _ in transport["calls"])


@pytest.mark.parametrize("operation", ["wan", "gpt_generation"])
def test_query_failure_never_attempts_a_new_post(operation, transport):
    transport["queue"] = [requests.ReadTimeout("synthetic-private-query-detail")]
    with pytest.raises(tasks.ProviderQueryError) as caught:
        query(operation, format_handle(operation))
    assert caught.value.kind == "transport" and "synthetic-private" not in str(caught.value)
    assert [call.method for call, _ in transport["calls"]] == ["GET"]


@pytest.mark.parametrize("http_status,envelope", [(200, "nested"), (400, "nested"), (200, "top")])
def test_same_id_failed_envelopes_are_trusted_without_leaking_details(http_status, envelope, transport):
    body: dict[str, Any] = {"task_info": info("failed")}
    error = {"title": "Synthetic title", "detail": "synthetic-private-detail"}
    if envelope == "nested":
        body["task_info"]["error"] = error | {"code": 3001}
    else:
        body["error"] = error | {"error_code": 3001}
    transport["queue"] = [response(body, status=http_status)]
    observed = query("gpt_edit", format_handle("gpt_edit"))
    assert observed.status is tasks.ProviderTaskStatus.FAILED
    assert observed.error_code == "3001" and observed.image_url is None
    assert "synthetic-private-detail" not in json.dumps(observed.to_dict())
    assert [call.method for call, _ in transport["calls"]] == ["GET"]


@pytest.mark.parametrize("fault", ["generic", "running", "invalid_error"])
def test_http400_is_not_itself_a_remote_failed_task(fault, transport):
    body: dict[str, Any] = {"task_info": info("failed")}
    if fault == "generic":
        body = {"error": {"error_code": 2001}}
    elif fault == "running":
        body["task_info"] = info("processing") | {"error": {"code": 3001}}
    else:
        body["task_info"]["error"] = {"code": True}
    transport["queue"] = [response(body, status=400)]
    with pytest.raises(tasks.ProviderQueryError):
        query("gpt_generation", format_handle("gpt_generation"))
    assert len(transport["calls"]) == 1


@pytest.mark.parametrize("fault", ["nested", "multiple", "unsafe_url"])
def test_gpt_success_requires_one_top_level_https_image(fault, transport):
    body = successful("gpt_generation")
    if fault == "nested":
        del body["images"]
        body["task_info"]["output"] = {"image_url": IMAGE_URL}
    elif fault == "multiple":
        body["images"].append(IMAGE_URL)
    else:
        body["images"] = ["https://user:secret@images.example.invalid/image.png"]
    transport["queue"] = [response(body)]
    with pytest.raises(tasks.ProviderQueryError, match="protocol"):
        query("gpt_generation", format_handle("gpt_generation"))
    assert len(transport["calls"]) == 1


def test_query_rejects_foreign_binding_legacy_handle_and_cross_provider_without_http(transport):
    original = credential("gpt_generation")
    handle = format_handle("gpt_generation")
    foreign = [replace(original, credential_ref="credential/other"),
               replace(original, scope=replace(original.scope, base_url="https://elsewhere.invalid"))]
    for value in foreign:
        with pytest.raises(tasks.ProviderBindingError):
            query("gpt_generation", handle, value)
    legacy = tasks.ProviderHandle(provider_model_id="gpt-image-2", task_id=GPT_ID, scope=original.scope,
                                  credential_ref=original.credential_ref,
                                  query_capability=tasks.QueryCapability.BY_TASK_ID)
    with pytest.raises(tasks.ProviderBindingError):
        query("gpt_generation", legacy)
    with pytest.raises(tasks.ProviderBindingError):
        query("gpt_generation", format_handle("wan"), credential("wan"))
    assert not transport["calls"]


def test_new_gpt_arguments_cannot_fall_back_or_silently_drop_fields(tmp_path, transport):
    cases = [
        ("gpt_generation", {"ref_image_paths": ["uploads/ignored.png"]}),
        ("gpt_generation", {"n": 2}),
        ("gpt_generation", {"quality": None}),
        ("gpt_edit", {"quality": "high"}),
        ("gpt_edit", {"ref_image_paths": []}),
        ("gpt_edit", {"image_contract_version": GPT_IMAGE2_EDIT_CONTRACT_VERSION}),
    ]
    for operation, options in cases:
        with pytest.raises(ValueError):
            submit(operation, tmp_path, lambda _handle: None, **options)
    assert not transport["calls"]


@pytest.mark.parametrize("fault", ["duplicate_id", "oversized", "encoding"])
def test_new_json_reader_rejects_ambiguous_or_unbounded_envelopes(fault, transport):
    body = successful("gpt_generation")
    headers = {}
    if fault == "duplicate_id":
        body = (f'{{"task_info":{{"id":"{GPT_ID}","id":"{GPT_ID}","status":"pending"}}}}').encode()
    elif fault == "oversized":
        headers = {"Content-Length": str(256 * 1024 + 1)}
    else:
        headers = {"Content-Encoding": "gzip"}
    transport["queue"] = [response(body, headers=headers)]
    with pytest.raises(tasks.ProviderQueryError, match="protocol"):
        query("gpt_generation", format_handle("gpt_generation"))
    assert len(transport["calls"]) == 1 and transport["responses"][0].raw.closed


def test_old_json_wrapper_keeps_200_output_envelope_semantics(transport):
    transport["queue"] = [response({"output": {"result": "old"}}), response({"output": {}}, status=202)]
    assert bound.request_json("GET", "https://example.invalid/legacy", {}) == {"result": "old"}
    with pytest.raises(tasks.ProviderSubmitError) as caught:
        bound.request_json("POST", "https://example.invalid/legacy", {}, {})
    assert caught.value.kind == "http" and caught.value.status_code == 202
    assert [call.method for call, _ in transport["calls"]] == ["GET", "POST"]


@pytest.mark.parametrize("operation", OPERATIONS)
@pytest.mark.parametrize("raw_status", [None, False, 7, [], {}])
def test_non_string_query_status_is_protocol_error_without_resubmission(operation, raw_status, transport):
    body = successful(operation)
    owner, field = (body["output"], "task_status") if operation == "wan" else (body["task_info"], "status")
    owner[field] = raw_status
    transport["queue"] = [response(body)]
    with pytest.raises(tasks.ProviderQueryError) as caught:
        query(operation, format_handle(operation))
    assert caught.value.kind == "protocol"
    assert [call.method for call, _ in transport["calls"]] == ["GET"]
