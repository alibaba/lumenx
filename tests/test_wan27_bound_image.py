"""Strict, unconnected Wan2.7 T2I primitive; all HTTP stays in fake adapters."""

import base64
from dataclasses import replace
import io
import json
from pathlib import Path
import time

import pytest
import requests
from requests.adapters import BaseAdapter

from src.models.image import WanxImageModel
from src.models.provider_tasks import (
    ProviderBindingError, ProviderFetchError, ProviderQueryError, ProviderScope,
    ProviderSubmitError, ResolvedCredential,
)

SECRET = "synthetic-example-bound-secret"
PROMPT = "synthetic-private-prompt"
RESULT_URL = "https://cdn.example.invalid/image.png?signature=synthetic-private-url"
BASE = "https://dashscope.aliyuncs.com"
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a7WQAAAAASUVORK5CYII=")


def task(status="PENDING", task_id="task_123-abc", **fields):
    return {"output": {"task_id": task_id, "task_status": status, **fields}}


def succeeded(content=None):
    return task("SUCCEEDED", choices=[{"message": {"role": "assistant", "content":
        content if content is not None else [{"type": "image", "image": RESULT_URL}]}}])


class ResponseStream(io.BytesIO):
    def release_conn(self):
        self.close()


def response(body, status=200, headers=None):
    result = requests.Response()
    result.status_code = status
    result.headers.update(headers or {})
    result.raw = ResponseStream(body if isinstance(body, bytes) else json.dumps(body).encode())
    return result


@pytest.fixture
def credential():
    return ResolvedCredential(ProviderScope(
        provider="dashscope", backend="dashscope", gateway="dashscope", base_url=BASE,
        region="cn-beijing", workspace_id="explicit-workspace",
    ), "credential/original", SECRET)


@pytest.fixture
def transport(monkeypatch):
    original_session = requests.Session
    state = {"calls": [], "sessions": [], "responses": [], "queue": [
        response(task()), response(succeeded()), response(PNG),
    ]}

    class Adapter(BaseAdapter):
        def send(self, request, stream=False, timeout=None, verify=True, cert=None, proxies=None):
            state["calls"].append((request, {"stream": stream, "timeout": timeout, "verify": verify, "cert": cert, "proxies": proxies}))
            if not state["queue"]:
                pytest.fail("Unexpected extra HTTP request or retry")
            result = state["queue"].pop(0)
            if isinstance(result, Exception):
                raise result
            result.request = request
            result.url = request.url
            state["responses"].append(result)
            return result

        def close(self):
            pass

    def session():
        instance = original_session()
        instance.auth = ("ambient-user", "ambient-password")
        instance.cookies.set("ambient", "cookie")
        instance.headers["X-Ambient"] = "ambient-header"
        instance.mount("https://", Adapter())
        instance.mount("http://", Adapter())
        state["sessions"].append(instance)
        return instance

    def forbidden(*args, **kwargs):
        pytest.fail("Legacy provider/config/netrc path must not run")

    monkeypatch.setattr(requests, "Session", session)
    monkeypatch.setattr(requests.sessions, "get_netrc_auth", forbidden)
    monkeypatch.setattr(WanxImageModel, "generate", forbidden)
    monkeypatch.setattr(WanxImageModel, "_require_api_key", forbidden)
    monkeypatch.setattr(WanxImageModel, "_download_image", forbidden)
    monkeypatch.setattr(time, "sleep", lambda seconds: None)
    monkeypatch.setenv("HTTPS_PROXY", "https://ambient-proxy.invalid:8899")
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", "/synthetic/nonexistent-ca")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "ambient-key")
    return state


def generate(tmp_path, credential, **kwargs):
    options = {"model_name": "wan2.7-image", "size": "576*1024", "max_bytes": 4096}
    options.update(kwargs)
    return WanxImageModel.generate_with_bound_http(
        PROMPT, str(tmp_path / "private.png"), credential, **options,
    )


@pytest.mark.parametrize("model", ["wan2.7-image", "wan2.7-image-pro"])
@pytest.mark.parametrize("size", ["576*1024", "768*1024", "1024*1024", "1024*768", "1024*576"])
def test_explicit_payload_and_isolated_anonymous_result(transport, tmp_path, credential, model, size):
    output, elapsed = generate(tmp_path, credential, model_name=model, size=size, seed=0, watermark=False)
    assert Path(output).read_bytes() == PNG
    assert elapsed >= 0
    assert len(transport["calls"]) == 3
    submit, query, download = [entry[0] for entry in transport["calls"]]
    assert submit.method == "POST"
    assert submit.url == BASE + "/api/v1/services/aigc/image-generation/generation"
    assert json.loads(submit.body) == {
        "model": model, "input": {"messages": [{"role": "user", "content": [{"text": PROMPT}]}]},
        "parameters": {"size": size, "n": 1, "seed": 0, "watermark": False},
    }
    assert submit.headers["X-DashScope-Async"] == "enable"
    assert query.url == BASE + "/api/v1/tasks/task_123-abc"
    for prepared in [submit, query]:
        assert prepared.headers["Authorization"] == "Bearer " + SECRET
        assert prepared.headers["X-DashScope-WorkSpace"] == "explicit-workspace"
    assert download.url == RESULT_URL
    assert "Authorization" not in download.headers
    assert "X-DashScope-WorkSpace" not in download.headers
    assert download.headers["Accept-Encoding"] == "identity"
    assert len(transport["sessions"]) == 3
    for session in transport["sessions"]:
        assert session.trust_env is False and session.auth is None
    for prepared, options in transport["calls"]:
        assert "Cookie" not in prepared.headers and "X-Ambient" not in prepared.headers
        assert options["verify"] is True and options["proxies"] == {}
    assert all(item.raw.closed for item in transport["responses"])


@pytest.mark.parametrize("extra", [
    {"negative_prompt": ""}, {"negative_prompt": "do not alter"}, {"prompt_extend": False},
    {"ref_image_paths": []}, {"ref_image_path": None}, {"mode": "i2i"}, {"n": 1},
    {"thinking_mode": False}, {"unknown": SECRET},
])
def test_unsupported_arguments_fail_before_client(transport, tmp_path, credential, extra):
    with pytest.raises((TypeError, ProviderBindingError)):
        generate(tmp_path, credential, **extra)
    assert transport["sessions"] == []


@pytest.mark.parametrize("options", [
    {"model_name": "wan2.7-image-preview"}, {"model_name": "wan2.6-t2i"},
    {"model_name": "qwen-image-2.0"}, {"size": "768*767"}, {"size": "4096*4096"},
    {"size": "8192*512"}, {"size": "001024*1024"}, {"size": "1024x1024"},
    {"size": "2K"}, {"size": "1" * 5000 + "*1"}, {"size": None},
    {"seed": True}, {"seed": -1}, {"seed": 2147483648}, {"seed": 1.5},
    {"watermark": 0}, {"watermark": None}, {"max_bytes": True}, {"max_bytes": 0},
])
def test_invalid_parameters_fail_before_client(transport, tmp_path, credential, options):
    with pytest.raises(ProviderBindingError):
        generate(tmp_path, credential, **options)
    assert transport["sessions"] == []


@pytest.mark.parametrize("changes", [
    {"provider": "other"}, {"backend": "other"}, {"gateway": "dashscope_workspace"},
    {"region": "us-east-1", "base_url": "https://dashscope-us.aliyuncs.com"},
    {"region": None}, {"account_identity": "account"}, {"base_url": BASE + "//"},
    {"base_url": BASE + ":443"}, {"base_url": BASE + "/api/v1"},
    {"base_url": BASE.replace("https", "HTTPS")}, {"base_url": "https://evil.invalid"},
])
def test_scope_is_exact_before_client(transport, tmp_path, credential, changes):
    credential = replace(credential, scope=replace(credential.scope, **changes))
    with pytest.raises(ProviderBindingError):
        generate(tmp_path, credential)
    assert transport["sessions"] == []


def test_singapore_and_single_trailing_slash_preserve_original_endpoint(transport, tmp_path, credential):
    base = "https://dashscope-intl.aliyuncs.com"
    credential = replace(credential, scope=replace(credential.scope, base_url=base + "/", region="ap-southeast-1", workspace_id=None))
    generate(tmp_path, credential, model_name="wan2.7-image-pro", size="4096*4096", seed=2147483647, watermark=True)
    assert transport["calls"][0][0].url.startswith(base + "/api/v1/")
    assert "X-DashScope-WorkSpace" not in transport["calls"][0][0].headers


@pytest.mark.parametrize("prompt", ["", "  ", "x" * 5001, None, "bad\ud800"])
def test_prompt_is_not_truncated_or_coerced(transport, tmp_path, credential, prompt):
    with pytest.raises(ProviderBindingError):
        WanxImageModel.generate_with_bound_http(prompt, str(tmp_path / "private.png"), credential,
            model_name="wan2.7-image", size="1024*1024", max_bytes=4096)
    assert transport["sessions"] == []


@pytest.mark.parametrize("status", [302, 303, 307, 308, 429, 500, 503])
def test_create_never_retries_or_follows_redirect(transport, tmp_path, credential, status):
    transport["queue"][0] = response({"message": SECRET + PROMPT}, status, {"Location": BASE + "/repeat"})
    with pytest.raises(ProviderSubmitError) as caught:
        generate(tmp_path, credential)
    assert len(transport["calls"]) == 1
    assert SECRET not in str(caught.value) and PROMPT not in str(caught.value)
    assert not (tmp_path / "private.png").exists()


@pytest.mark.parametrize("failure", [requests.ConnectionError(SECRET), requests.Timeout(PROMPT), b"not-json"])
def test_create_transport_or_protocol_failure_is_sanitized(transport, tmp_path, credential, failure):
    transport["queue"][0] = response(failure) if isinstance(failure, bytes) else failure
    with pytest.raises(ProviderSubmitError) as caught:
        generate(tmp_path, credential)
    assert len(transport["calls"]) == 1 and not caught.value.__cause__
    assert SECRET not in str(caught.value) and PROMPT not in str(caught.value)


@pytest.mark.parametrize("task_id", [None, "", "../task", "task?key=x", "a/b", "a#b", "a%2Fb", "a b", "x" * 257, 42])
def test_unsafe_task_id_never_forms_query(transport, tmp_path, credential, task_id):
    transport["queue"][0] = response(task(task_id=task_id))
    with pytest.raises(ProviderSubmitError):
        generate(tmp_path, credential)
    assert len(transport["calls"]) == 1


@pytest.mark.parametrize("status", ["UNKNOWN", "unknown", "succeeded", "FAILED", "CANCELED", "NEW", None])
def test_nonpending_query_states_are_not_retried(transport, tmp_path, credential, status):
    transport["queue"][1] = response(task(status, message=SECRET + RESULT_URL))
    with pytest.raises(ProviderQueryError) as caught:
        generate(tmp_path, credential)
    assert len(transport["calls"]) == 2
    assert SECRET not in str(caught.value) and RESULT_URL not in str(caught.value)


@pytest.mark.parametrize("failure", [response(task(), 307, {"Location": BASE + "/other"}), response({}, 503), response(b"bad"), response({"output": []}), response(task("SUCCEEDED", task_id="different")), requests.ConnectionError(SECRET)])
def test_query_fails_closed_without_resubmit(transport, tmp_path, credential, failure):
    transport["queue"][1] = failure
    with pytest.raises(ProviderQueryError):
        generate(tmp_path, credential)
    assert [item[0].method for item in transport["calls"]] == ["POST", "GET"]


def test_only_pending_running_continue_and_text_items_are_allowed(transport, tmp_path, credential):
    content = [{"type": "text", "text": "description"}, {"type": "image", "image": RESULT_URL}]
    transport["queue"] = [response(task()), response(task()), response(task("RUNNING")), response(succeeded(content)), response(PNG)]
    generate(tmp_path, credential)
    assert [item[0].method for item in transport["calls"]] == ["POST", "GET", "GET", "GET", "GET"]


@pytest.mark.parametrize("content", [[], [{"type": "text", "text": "only text"}], [{"type": "image", "image": RESULT_URL}] * 2,
    [{"type": "image", "image": 42}], [{"type": "image", "image": "http://cdn.example.invalid/plain.png"}],
    [{"type": "image", "image": "https://name:pass@cdn.example.invalid/out"}],
    [{"type": "other", "image": RESULT_URL}], [{"image": RESULT_URL}], ["bad"],
    [{"type": "text", "text": SECRET, "image": RESULT_URL}],
])
def test_exactly_one_typed_https_image_is_required(transport, tmp_path, credential, content):
    transport["queue"][1] = response(succeeded(content))
    with pytest.raises(ProviderQueryError):
        generate(tmp_path, credential)
    assert len(transport["calls"]) == 2


def test_multiple_choices_cannot_hide_additional_image(transport, tmp_path, credential):
    payload = succeeded()
    payload["output"]["choices"] *= 2
    transport["queue"][1] = response(payload)
    with pytest.raises(ProviderQueryError):
        generate(tmp_path, credential)
    assert len(transport["calls"]) == 2


@pytest.mark.parametrize("body,headers,status", [
    (b"", {}, 200), (b"x" * 4097, {}, 200), (PNG, {"Content-Length": "4097"}, 200),
    (PNG, {"Content-Length": "bad"}, 200), (PNG, {"Content-Length": str(len(PNG) + 1)}, 200),
    (PNG, {"Content-Length": "1"}, 200), (PNG, {"Content-Encoding": "gzip"}, 200),
    (PNG, {"Location": RESULT_URL + "&again=yes"}, 307), (PNG, {}, 503),
])
def test_download_boundaries_cleanup_without_resubmission(transport, tmp_path, credential, body, headers, status):
    transport["queue"][2] = response(body, status, headers)
    with pytest.raises(ProviderFetchError):
        generate(tmp_path, credential)
    assert len(transport["calls"]) == 3
    assert not list(tmp_path.iterdir())
    assert all(item.raw.closed for item in transport["responses"])


def test_partial_stream_failure_cleans_file_and_hides_provider_text(transport, tmp_path, credential):
    class BrokenStream(ResponseStream):
        def read(self, *args):
            if self.tell():
                raise requests.ConnectionError(SECRET + RESULT_URL)
            return super().read(*args)
    transport["queue"][2].raw = BrokenStream(b"partial")
    with pytest.raises(ProviderFetchError) as caught:
        generate(tmp_path, credential)
    assert not list(tmp_path.iterdir()) and len(transport["calls"]) == 3
    assert SECRET not in str(caught.value) and RESULT_URL not in str(caught.value)


def test_existing_output_is_preserved_before_submit(transport, tmp_path, credential):
    existing = tmp_path / "private.png"
    existing.write_bytes(b"existing")
    with pytest.raises(ProviderBindingError):
        generate(tmp_path, credential)
    assert existing.read_bytes() == b"existing" and transport["sessions"] == []


@pytest.mark.parametrize("status", [[], {}, 42, True])
@pytest.mark.parametrize("stage", ["submit", "query"])
def test_bad_status_types_use_sanitized_protocol_errors(transport, tmp_path, credential, status, stage):
    transport["queue"][0 if stage == "submit" else 1] = response(task(status))
    with pytest.raises(ProviderSubmitError if stage == "submit" else ProviderQueryError):
        generate(tmp_path, credential)
    assert len(transport["calls"]) == (1 if stage == "submit" else 2)


def test_poll_budget_never_resubmits(transport, tmp_path, credential):
    transport["queue"] = [response(task()) for _ in range(61)]
    with pytest.raises(ProviderQueryError, match="timeout"):
        generate(tmp_path, credential)
    assert len(transport["calls"]) == 61
    assert sum(item[0].method == "POST" for item in transport["calls"]) == 1


def test_area_ratio_boundaries_and_maximum_prompt_remain_exact(transport, tmp_path, credential):
    WanxImageModel.generate_with_bound_http("界" * 5000, str(tmp_path / "private.png"), credential,
        model_name="wan2.7-image", size="3072*384", max_bytes=len(PNG))
    payload = json.loads(transport["calls"][0][0].body)
    assert payload["input"]["messages"][0]["content"] == [{"text": "界" * 5000}]
    assert "seed" not in payload["parameters"]


def test_multiple_choices_may_contain_text_and_one_image(transport, tmp_path, credential):
    payload = succeeded()
    payload["output"]["choices"].insert(0, {"message": {"role": "assistant", "content": [{"type": "text", "text": "description"}]}})
    transport["queue"][1] = response(payload)
    generate(tmp_path, credential)
    assert (tmp_path / "private.png").read_bytes() == PNG


@pytest.mark.parametrize("stage", ["submit", "query"])
@pytest.mark.parametrize("fault", ["oversized", "declared_oversized", "truncated", "bad_length", "compressed", "invalid_utf8"])
def test_metadata_json_is_bounded_before_parse(transport, tmp_path, credential, stage, fault):
    index = 0 if stage == "submit" else 1
    payload = task() if stage == "submit" else succeeded()
    body = json.dumps(payload).encode()
    headers = {}
    if fault == "oversized":
        body += b" " * (256 * 1024)
    elif fault == "declared_oversized":
        headers["Content-Length"] = str(256 * 1024 + 1)
    elif fault == "truncated":
        headers["Content-Length"] = str(len(body) + 1)
    elif fault == "bad_length":
        headers["Content-Length"] = "1.0"
    elif fault == "compressed":
        headers["Content-Encoding"] = "gzip"
    else:
        body = body[:-1] + b', "diagnostic": "\xff"}'
    transport["queue"][index] = response(body, headers=headers)
    with pytest.raises(ProviderSubmitError if stage == "submit" else ProviderQueryError) as caught:
        generate(tmp_path, credential)
    assert len(transport["calls"]) == index + 1
    assert SECRET not in str(caught.value) and RESULT_URL not in str(caught.value)
    assert all(item.raw.closed for item in transport["responses"])
    assert not list(tmp_path.iterdir())


def test_exact_metadata_limit_and_utf8_are_valid(transport, tmp_path, credential):
    payload = task(diagnostic="合成文本")
    body = json.dumps(payload, ensure_ascii=False).encode()
    body += b" " * (256 * 1024 - len(body))
    transport["queue"][0] = response(body, headers={"Content-Length": str(len(body))})
    generate(tmp_path, credential)
    for prepared, options in transport["calls"]:
        assert options["stream"] is True
        assert prepared.headers["Accept-Encoding"] == "identity"


@pytest.mark.parametrize("value", [b"NaN", b"Infinity", b"-Infinity", b"[" * 2000 + b"0" + b"]" * 2000])
@pytest.mark.parametrize("stage", ["submit", "query"])
def test_invalid_or_excessively_nested_json_is_a_protocol_error(transport, tmp_path, credential, value, stage):
    index = 0 if stage == "submit" else 1
    payload = task() if stage == "submit" else succeeded()
    body = json.dumps(payload).encode()[:-1] + b', "diagnostic": ' + value + b'}'
    transport["queue"][index] = response(body)
    with pytest.raises(ProviderSubmitError if stage == "submit" else ProviderQueryError):
        generate(tmp_path, credential)
    assert len(transport["calls"]) == index + 1
    assert all(item.raw.closed for item in transport["responses"])
