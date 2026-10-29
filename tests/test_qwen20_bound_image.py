"""Qwen 2.0 strict synchronous transport through real requests and fake HTTP."""

from dataclasses import replace
import json
from pathlib import Path

import pytest
import requests

from src.models.image import WanxImageModel
from src.models.provider_tasks import (
    ProviderBindingError,
    ProviderFetchError,
    ProviderSubmitError,
)
from tests import test_wan27_bound_image as wan

VERSION = "provider-qwen20-t2i-bound-http-v1"
transport = wan.transport
credential = wan.credential


def completed(content=None):
    return {
        "output": {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "role": "assistant",
                        "content": content
                        if content is not None
                        else [{"image": wan.RESULT_URL}],
                    },
                }
            ]
        }
    }


def generate(tmp_path, credential, **changes):
    params = dict(
        model_name="qwen-image-2.0",
        size="576*1024",
        max_bytes=4096,
        prompt_extend=True,
        watermark=False,
        image_contract_version=VERSION,
    )
    params.update(changes)
    return WanxImageModel.generate_with_bound_http(
        wan.PROMPT, str(tmp_path / "private.png"), credential, **params
    )


@pytest.fixture(autouse=True)
def synchronous_queue(transport):
    transport["queue"] = [wan.response(completed()), wan.response(wan.PNG)]


@pytest.mark.parametrize("model", ["qwen-image-2.0", "qwen-image-2.0-pro"])
@pytest.mark.parametrize(
    "size", ["576*1024", "768*1024", "1024*1024", "1024*768", "1024*576"]
)
def test_explicit_sync_payload_and_anonymous_bounded_result(
    transport, credential, tmp_path, model, size
):
    output, elapsed = generate(
        tmp_path,
        credential,
        model_name=model,
        size=size,
        seed=0,
        negative_prompt="不要文字",
    )
    assert Path(output).read_bytes() == wan.PNG and elapsed >= 0
    assert len(transport["calls"]) == 2
    submit, download = [call[0] for call in transport["calls"]]
    assert (
        submit.method == "POST"
        and submit.url
        == wan.BASE + "/api/v1/services/aigc/multimodal-generation/generation"
    )
    assert json.loads(submit.body) == {
        "model": model,
        "input": {"messages": [{"role": "user", "content": [{"text": wan.PROMPT}]}]},
        "parameters": {
            "size": size,
            "n": 1,
            "seed": 0,
            "negative_prompt": "不要文字",
            "prompt_extend": True,
            "watermark": False,
        },
    }
    assert "X-DashScope-Async" not in submit.headers
    assert submit.headers["Authorization"] == "Bearer " + wan.SECRET
    assert submit.headers["X-DashScope-WorkSpace"] == "explicit-workspace"
    assert download.method == "GET" and download.url == wan.RESULT_URL
    assert (
        "Authorization" not in download.headers
        and "X-DashScope-WorkSpace" not in download.headers
    )
    for request, options in transport["calls"]:
        assert "Cookie" not in request.headers and "X-Ambient" not in request.headers
        assert (
            options["verify"] is True
            and options["proxies"] == {}
            and options["stream"] is True
        )
    assert all(
        not session.trust_env and session.auth is None
        for session in transport["sessions"]
    )
    assert all(response.raw.closed for response in transport["responses"])


def test_false_flags_and_empty_negative_are_not_overwritten(
    transport, tmp_path, credential
):
    generate(
        tmp_path, credential, prompt_extend=False, negative_prompt="", watermark=False
    )
    params = json.loads(transport["calls"][0][0].body)["parameters"]
    assert params == {
        "n": 1,
        "size": "576*1024",
        "prompt_extend": False,
        "watermark": False,
        "negative_prompt": "",
    }
    assert "seed" not in params


@pytest.mark.parametrize(
    "params",
    [
        {"model_name": "wan2.7-image"},
        {"model_name": "qwen-image-2.0-preview"},
        {"size": "511*512"},
        {"size": "2048*2049"},
        {"size": "001024*1024"},
        {"size": "1024x1024"},
        {"size": "1" * 5000 + "*1"},
        {"size": None},
        {"negative_prompt": "字" * 501},
        {"negative_prompt": True},
        {"negative_prompt": "bad\ud800"},
        {"prompt_extend": None},
        {"prompt_extend": 1},
        {"watermark": 0},
        {"seed": True},
        {"seed": -1},
        {"seed": 2147483648},
        {"max_bytes": 0},
        {"ref_image_paths": []},
        {"n": 1},
        {"mode": "i2i"},
        {"unknown": wan.SECRET},
        {"image_contract_version": None},
        {"image_contract_version": "unknown"},
    ],
)
def test_invalid_parameters_fail_before_http(transport, tmp_path, credential, params):
    with pytest.raises((ProviderBindingError, TypeError)):
        generate(tmp_path, credential, **params)
    assert transport["sessions"] == [] and not list(tmp_path.iterdir())


@pytest.mark.parametrize("size", ["512*512", "2048*2048", "4096*1024"])
def test_total_pixels_not_individual_edge_limit(transport, tmp_path, credential, size):
    generate(
        tmp_path, credential, size=size, negative_prompt="字" * 500, seed=2147483647
    )
    assert json.loads(transport["calls"][0][0].body)["parameters"]["size"] == size


@pytest.mark.parametrize(
    "changes",
    [
        {"base_url": "https://evil.invalid"},
        {"base_url": wan.BASE + "//"},
        {"base_url": wan.BASE + ":443"},
        {"region": "us-east-1"},
        {"gateway": "dashscope_workspace"},
        {"account_identity": "other"},
    ],
)
def test_scope_mismatch_fails_before_http(transport, tmp_path, credential, changes):
    with pytest.raises(ProviderBindingError):
        generate(
            tmp_path, replace(credential, scope=replace(credential.scope, **changes))
        )
    assert transport["sessions"] == []


def test_singapore_trailing_slash_preserves_explicit_scope(
    transport, tmp_path, credential
):
    scope = replace(
        credential.scope,
        base_url="https://dashscope-intl.aliyuncs.com/",
        region="ap-southeast-1",
        workspace_id=None,
    )
    generate(tmp_path, replace(credential, scope=scope))
    request = transport["calls"][0][0]
    assert (
        request.url
        == "https://dashscope-intl.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
    )
    assert "X-DashScope-WorkSpace" not in request.headers


@pytest.mark.parametrize("status", [302, 303, 307, 308, 429, 500, 503])
def test_post_never_retries_redirects_or_leaks_remote_body(
    transport, tmp_path, credential, status
):
    transport["queue"][0] = wan.response(
        {"message": wan.SECRET + wan.PROMPT}, status, {"Location": wan.BASE + "/retry"}
    )
    with pytest.raises(ProviderSubmitError) as caught:
        generate(tmp_path, credential)
    assert len(transport["calls"]) == 1 and not list(tmp_path.iterdir())
    assert wan.SECRET not in str(caught.value) and wan.PROMPT not in str(caught.value)


@pytest.mark.parametrize(
    "body",
    [
        {"output": {}},
        {"output": {"task_id": "not-a-sync-result"}},
        completed([]),
        completed([{"image": wan.RESULT_URL}, {"image": wan.RESULT_URL}]),
        completed([{"image": "http://cdn.example.invalid/a.png"}]),
        completed([{"image": "file:///private/file"}]),
        completed([{"text": "no image"}]),
        completed([{"image": wan.RESULT_URL, "unknown": 1}]),
        {
            "output": {
                "choices": [
                    {
                        "message": {
                            "role": "user",
                            "content": [{"image": wan.RESULT_URL}],
                        }
                    }
                ]
            }
        },
        {"code": "remote-error", **completed()},
        b"not-json",
    ],
)
def test_invalid_sync_response_is_uncertain_without_download_or_retry(
    transport, tmp_path, credential, body
):
    transport["queue"][0] = wan.response(body)
    with pytest.raises(ProviderSubmitError):
        generate(tmp_path, credential)
    assert len(transport["calls"]) == 1 and not list(tmp_path.iterdir())


def test_text_before_image_and_multiple_choices_do_not_hide_extra_results(
    transport, tmp_path, credential
):
    transport["queue"][0] = wan.response(
        completed([{"text": "note"}, {"image": wan.RESULT_URL}])
    )
    generate(tmp_path, credential)
    assert len(transport["calls"]) == 2


@pytest.mark.parametrize(
    "fault",
    ["oversized", "declared_oversized", "truncated", "compressed", "nan", "deep"],
)
def test_metadata_is_bounded_before_parse(transport, tmp_path, credential, fault):
    body, headers = json.dumps(completed()).encode(), {}
    if fault == "oversized":
        body += b" " * (256 * 1024)
    elif fault == "declared_oversized":
        headers["Content-Length"] = str(256 * 1024 + 1)
    elif fault == "truncated":
        headers["Content-Length"] = str(len(body) + 1)
    elif fault == "compressed":
        headers["Content-Encoding"] = "gzip"
    elif fault == "nan":
        body = body[:-1] + b', "extra": NaN}'
    else:
        body = b"[" * 2000 + b"0" + b"]" * 2000
    transport["queue"][0] = wan.response(body, headers=headers)
    with pytest.raises(ProviderSubmitError):
        generate(tmp_path, credential)
    assert len(transport["calls"]) == 1 and not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    "body,headers,status",
    [
        (b"x" * 4097, {}, 200),
        (b"small", {"Content-Length": "4097"}, 200),
        (b"small", {"Content-Length": "6"}, 200),
        (b"small", {"Content-Encoding": "gzip"}, 200),
        (b"", {"Location": "https://other.invalid"}, 302),
    ],
    ids=[
        "stream-over-bound",
        "declared-over-bound",
        "truncated",
        "compressed",
        "redirect",
    ],
)
def test_download_failure_is_bounded_and_cleans_private_file(
    transport, tmp_path, credential, body, headers, status
):
    transport["queue"][1] = wan.response(body, status, headers)
    with pytest.raises(ProviderFetchError):
        generate(tmp_path, credential)
    assert len(transport["calls"]) == 2 and not list(tmp_path.iterdir())


def test_submit_network_error_is_not_retried(transport, tmp_path, credential):
    transport["queue"][0] = requests.Timeout(wan.SECRET)
    with pytest.raises(ProviderSubmitError) as caught:
        generate(tmp_path, credential)
    assert len(transport["calls"]) == 1 and wan.SECRET not in str(caught.value)


def test_existing_output_survives_without_submit(transport, tmp_path, credential):
    (tmp_path / "private.png").write_bytes(b"existing")
    with pytest.raises(ProviderBindingError):
        generate(tmp_path, credential)
    assert (
        transport["calls"] == []
        and (tmp_path / "private.png").read_bytes() == b"existing"
    )


@pytest.mark.parametrize("model", ["qwen-image-2.0", "qwen-image-2.0-pro"])
def test_real_core_dispatch_normalizes_actual_bound_response_and_proof(
    transport, tmp_path, credential, model
):
    from src.models.provider_media_execution import (
        execute_sync_artifact_to_private_file,
    )
    from src.models.provider_media_intents import (
        sync_artifact_proof_from_record,
        validate_sync_artifact_record_intent,
    )
    from src.models.provider_media_runtime import MediaRuntimeAuthority
    from src.models.provider_recovery import record_with_changes
    from tests.test_qwen20_core_execution import record
    from tests.frozen_image_fixtures import rgb_png_bytes

    transport["queue"][1] = wan.response(rgb_png_bytes(width=16, height=16))
    saved = record_with_changes(
        record(model, scope=credential.scope, credential_ref=credential.credential_ref),
        phase="submitting",
    )
    authority = MediaRuntimeAuthority(
        "image",
        model,
        credential.scope,
        credential.credential_ref,
        "DASHSCOPE_API_KEY",
        credential,
    )
    result = execute_sync_artifact_to_private_file(
        saved,
        authority,
        output_root=tmp_path,
        scratch_dir=tmp_path / "scratch",
        max_bytes=1024 * 1024,
    )
    assert (
        result.path.read_bytes().startswith(b"\x89PNG") and len(transport["calls"]) == 2
    )
    proof = sync_artifact_proof_from_record(
        saved,
        relative_path=saved.output_ref,
        sha256=result.sha256,
        size_bytes=result.size_bytes,
    )
    complete = record_with_changes(
        saved, phase="completed", prepared_artifact=proof, artifact=proof
    )
    assert (
        complete.fingerprint == saved.fingerprint
        and validate_sync_artifact_record_intent(complete) == "image"
    )
    assert not (tmp_path / "assets").exists()


@pytest.mark.parametrize(
    "body",
    [
        {"output": {"choices": completed()["output"]["choices"] * 2}},
        {
            "output": {
                "choices": [
                    {**completed()["output"]["choices"][0], "finish_reason": "length"}
                ]
            }
        },
    ],
)
def test_multiple_choices_or_incomplete_result_never_publish(
    transport, tmp_path, credential, body
):
    transport["queue"][0] = wan.response(body)
    with pytest.raises(ProviderSubmitError):
        generate(tmp_path, credential)
    assert len(transport["calls"]) == 1 and not list(tmp_path.iterdir())


@pytest.mark.parametrize("prompt", ["", "  ", "bad\ud800", "x" * 16385])
def test_prompt_validation_is_explicit_local_bound(
    transport, tmp_path, credential, prompt
):
    with pytest.raises(ProviderBindingError):
        WanxImageModel.generate_with_bound_http(
            prompt,
            str(tmp_path / "private.png"),
            credential,
            model_name="qwen-image-2.0",
            size="1024*1024",
            max_bytes=4096,
            image_contract_version=VERSION,
            prompt_extend=True,
        )
    assert transport["calls"] == []
