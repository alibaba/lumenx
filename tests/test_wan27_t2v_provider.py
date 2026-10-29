"""Wan 2.7 T2V durable provider contract."""

from contextlib import contextmanager
from dataclasses import replace
import hashlib
from threading import RLock
from types import SimpleNamespace

import pytest

from src.config import Config
from src.models.factory import ModelFactory
from src.models.provider_execution import ProviderExecutionRecord
from src.models.provider_recovery import (
    build_accepted_provider_submission_and_record,
    reconstruct_submission_from_record,
)
from src.models.provider_tasks import (
    PreparedMedia,
    ProviderBindingError,
    ProviderScope,
    ResolvedCredential,
)
from src.models.wanx import WanxModel
from src.utils.generation_intent import normalize_generation_intent
from src.utils.json_storage import DirectoryWriterLock
from src.utils.model_catalog import CatalogAccessor, load_generated_model_catalog
from src.utils.media_versions import MediaVersion


def _catalog():
    return CatalogAccessor(load_generated_model_catalog())


def _intent(**params):
    return normalize_generation_intent(
        {
            "model_id": "wan2.7-t2v",
            "mode": "t2v",
            "prompt": "A moonlit train crosses the sea",
            "params": params,
        },
        catalog=_catalog(),
        allowed_modes={"t2v"},
        allowed_parameters={
            "duration",
            "resolution",
            "ratio",
            "seed",
            "negative_prompt",
            "prompt_extend",
            "watermark",
        },
    )


def _scope():
    return ProviderScope(
        provider="dashscope",
        backend="dashscope",
        gateway="dashscope",
        base_url="https://dashscope-intl.aliyuncs.com",
        region="ap-southeast-1",
    )


def _credential(scope=None):
    bound_scope = scope or _scope()
    return ResolvedCredential(bound_scope, "credentials/wan27-t2v", "test-only-secret")


class _JsonResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def json(self):
        return self.payload


class _DownloadResponse:
    status_code = 200
    headers = {"Content-Length": "11"}

    def __init__(self):
        self.closed = False

    def iter_content(self, chunk_size):
        assert 0 < chunk_size <= 1024 * 1024
        yield b"hello "
        yield b"video"

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.closed = True


@pytest.fixture
def model(tmp_path):
    config = Config()
    config.config = {"model": {"name": "wanx", "output_root": str(tmp_path)}}
    return ModelFactory.create_model(config)


@pytest.fixture
def owner(tmp_path):
    root = tmp_path / "runtime"
    root.mkdir()
    (root / "video").mkdir()
    state = SimpleNamespace(
        root=root,
        lease=DirectoryWriterLock(root).acquire(),
        lock=RLock(),
        closed=False,
    )

    @contextmanager
    def guard():
        with state.lock:
            if state.closed:
                raise RuntimeError("closed")
            state.lease.assert_owned()
            yield

    state.guard = guard
    yield state
    state.closed = True
    state.lease.close()


def test_real_catalog_normalizes_wan27_t2v_defaults_and_scalar_edges():
    intent = _intent(
        duration=2,
        resolution="720P",
        ratio="3:4",
        seed=0,
        negative_prompt="",
        prompt_extend=False,
        watermark=False,
    )

    assert intent.model_id == "wan/wan2.7-video#t2v"
    assert intent.catalog_mode == "t2v"
    assert intent.params == {
        "duration": 2,
        "negative_prompt": "",
        "prompt_extend": False,
        "ratio": "3:4",
        "resolution": "720P",
        "seed": 0,
        "watermark": False,
    }

    defaults = _intent()
    assert defaults.params == {
        "duration": 5,
        "prompt_extend": True,
        "ratio": "16:9",
        "resolution": "1080P",
        "watermark": False,
    }


def test_wan27_t2v_record_reconstructs_and_submit_uses_no_media_original_scope(
    model, monkeypatch, tmp_path,
):
    scope = _scope()
    credential = _credential(scope)
    intent = _intent(
        duration=15,
        resolution="1080P",
        ratio="16:9",
        seed=-1,
        negative_prompt="flat lighting",
        prompt_extend=False,
        watermark=False,
    )

    submission, record = build_accepted_provider_submission_and_record(
        intent,
        provider_model_id="wan2.7-t2v",
        media=[],
        scope=scope,
        credential_ref=credential.credential_ref,
        attempt_id="attempt-t2v",
    )
    persisted = ProviderExecutionRecord.model_validate_json(record.model_dump_json())
    recovered = reconstruct_submission_from_record(persisted, output_root=tmp_path)
    assert recovered == submission
    assert recovered.media == ()

    calls = []

    def post(url, **kwargs):
        calls.append((url, kwargs))
        return _JsonResponse({"request_id": "request-t2v", "output": {"task_id": "task-t2v"}})

    monkeypatch.setattr("src.models.wanx.requests.post", post)
    monkeypatch.setattr("src.models.wanx.requests.get", lambda *_args, **_kwargs: pytest.fail("submit must not query"))
    monkeypatch.setattr(WanxModel, "generate", lambda *_args, **_kwargs: pytest.fail("submit must not generate"))

    saved = []
    handle = model.submit(recovered, credential=credential, on_submitted=saved.append)

    assert saved == [handle]
    assert handle.provider_model_id == "wan2.7-t2v"
    assert handle.scope == scope
    assert handle.credential_ref == credential.credential_ref
    assert len(calls) == 1
    assert calls[0][0] == "https://dashscope-intl.aliyuncs.com/api/v1/services/aigc/video-generation/video-synthesis"
    assert calls[0][1]["json"] == {
        "model": "wan2.7-t2v",
        "input": {
            "prompt": "A moonlit train crosses the sea",
            "negative_prompt": "flat lighting",
        },
        "parameters": {
            "duration": 15,
            "resolution": "1080P",
            "ratio": "16:9",
            "prompt_extend": False,
            "watermark": False,
        },
    }


def test_wan27_t2v_rejects_any_media_or_unsupported_audio_and_shot_controls():
    scope = _scope()
    data = b"fake image bytes"
    version = MediaVersion(hashlib.sha256(data).hexdigest(), len(data), "first_frame", 0, "image/png")
    prepared = PreparedMedia.from_verified_bytes(version, data, max_bytes=1024)
    submission = replace(
        build_accepted_provider_submission_and_record(
            _intent(duration=5, resolution="1080P", ratio="16:9"),
            provider_model_id="wan2.7-t2v",
            media=[],
            scope=scope,
            credential_ref="credentials/wan27-t2v",
            attempt_id="attempt-t2v",
        )[0],
        media=(prepared,),
    )

    with pytest.raises(ProviderBindingError):
        WanxModel.validate_submission(submission)

    for param in ("audio", "shot_type"):
        bad = replace(submission, media=(), intent=replace(submission.intent, params={
            **submission.intent.params,
            param: False if param == "audio" else "single",
        }))
        with pytest.raises(ProviderBindingError):
            WanxModel.validate_submission(bad)


def test_wan27_t2v_query_and_fetch_reuse_original_scope_without_resubmitting(
    model, owner, monkeypatch,
):
    scope = _scope()
    credential = _credential(scope)
    submission = build_accepted_provider_submission_and_record(
        _intent(duration=5, resolution="1080P", ratio="16:9"),
        provider_model_id="wan2.7-t2v",
        media=[],
        scope=scope,
        credential_ref=credential.credential_ref,
        attempt_id="attempt-t2v",
    )[0]
    post_calls = []
    query_calls = []
    fetch_calls = []
    response = _DownloadResponse()

    def post(url, **kwargs):
        post_calls.append((url, kwargs))
        return _JsonResponse({"request_id": "request-t2v", "output": {"task_id": "task-t2v"}})

    def get(url, **kwargs):
        query_calls.append((url, kwargs))
        return _JsonResponse({
            "request_id": "query-t2v",
            "output": {
                "task_id": "task-t2v",
                "task_status": "SUCCEEDED",
                "video_url": "https://cdn.example.invalid/t2v.mp4?signature=private",
            },
        })

    def download(session, url, **kwargs):
        fetch_calls.append((url, kwargs, dict(session.headers)))
        return response

    monkeypatch.setattr("src.models.wanx.requests.post", post)
    monkeypatch.setattr("src.models.wanx.requests.get", get)
    monkeypatch.setattr("src.models.wanx.requests.Session.get", download)

    handle = model.submit(submission, credential=credential, on_submitted=lambda _handle: None)
    observation = model.query(handle, credential=credential)
    artifact = model.fetch(
        handle,
        observation,
        output_root=owner.root,
        output_ref="video/t2v.mp4",
        writer_lock=owner.lease,
        storage_guard=owner.guard,
        max_bytes=1024,
    )

    assert len(post_calls) == 1
    assert len(query_calls) == 1
    assert query_calls[0][0] == "https://dashscope-intl.aliyuncs.com/api/v1/tasks/task-t2v"
    assert query_calls[0][1]["headers"]["Authorization"] == "Bearer test-only-secret"
    assert len(fetch_calls) == 1
    assert fetch_calls[0][0] == "https://cdn.example.invalid/t2v.mp4?signature=private"
    assert response.closed is True
    assert (owner.root / artifact.relative_path).read_bytes() == b"hello video"
    assert artifact.handle == handle
