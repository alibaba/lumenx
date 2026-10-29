"""Independent result downloads use fake HTTP and synthetic managed roots only."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import FrozenInstanceError, replace
import hashlib
import importlib
import os
import stat
from threading import Barrier, Event, RLock
from types import SimpleNamespace

import pytest
import requests

from src.config import Config
from src.models.factory import ModelFactory
from src.models.wanx import WanxModel
from src.utils import media_versions as media
from src.utils.json_storage import DirectoryWriterLock, StorageError


ORIGINAL_QUERY = WanxModel.query
RESULT_URL = "https://cdn.example.invalid/result.mp4?signature=private-output"


def test_real_factory_exposes_independent_fetch():
    config = Config()
    config.config = {"model": {"name": "wanx"}}
    assert callable(getattr(ModelFactory.create_model(config), "fetch", None))


@pytest.fixture
def contract():
    return importlib.import_module("src.models.provider_tasks")


@pytest.fixture
def handle(contract):
    return contract.ProviderHandle(
        provider_model_id="wan2.6-i2v", task_id="original-task",
        scope=contract.ProviderScope(
            provider="dashscope", backend="dashscope", gateway="dashscope",
            base_url="https://dashscope.aliyuncs.com", region="cn-beijing",
            workspace_id="original-workspace", account_identity="original-account",
        ),
        credential_ref="credentials/original", request_id="original-submit",
        query_capability=contract.QueryCapability.BY_TASK_ID,
    )


@pytest.fixture
def observation(contract, handle):
    return contract.ProviderObservation(
        task_id=handle.task_id, status=contract.ProviderTaskStatus.SUCCEEDED,
        raw_status="SUCCEEDED", video_url=RESULT_URL, source_handle=handle,
    )


@pytest.fixture
def owner(tmp_path):
    root = tmp_path / "runtime"
    root.mkdir()
    (root / "video").mkdir()
    state = SimpleNamespace(root=root, lease=DirectoryWriterLock(root).acquire(),
                            lock=RLock(), closed=False)

    @contextmanager
    def guard():
        with state.lock:
            if state.closed:
                raise StorageError("Runtime is closed")
            state.lease.assert_owned()
            yield

    state.guard = guard

    def close():
        with state.lock:
            state.closed = True
            state.lease.close()

    state.close = close
    yield state
    close()


class Response:
    def __init__(self, chunks=None, *, status_code=200, headers=None):
        self.chunks = [b"first ", b"second"] if chunks is None else chunks
        self.status_code = status_code
        self.headers = {} if headers is None else headers
        self.closed = False

    def iter_content(self, chunk_size):
        assert 0 < chunk_size <= 1024 * 1024
        yield from self.chunks

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


@pytest.fixture
def transport(monkeypatch, owner):
    state = {"response": Response(), "calls": []}

    def send(session, request, **kwargs):
        assert not owner.lock._is_owned(), "HTTP must not hold the runtime storage lock"
        state["calls"].append((session, request, kwargs))
        value = state["response"]
        if isinstance(value, Exception):
            raise value
        return value

    def forbidden(*args, **kwargs):
        pytest.fail("fetch must not submit/query/generate or resolve current credentials/routes")

    monkeypatch.setattr("requests.sessions.Session.send", send)
    monkeypatch.setattr("src.models.wanx.requests.get", forbidden)
    monkeypatch.setattr("src.models.wanx.requests.post", forbidden)
    monkeypatch.setattr("src.models.wanx.get_provider_base_url", forbidden)
    monkeypatch.setattr(WanxModel, "query", forbidden)
    monkeypatch.setattr(WanxModel, "submit", forbidden)
    monkeypatch.setattr(WanxModel, "generate", forbidden)
    monkeypatch.setattr(WanxModel, "_download_video", forbidden)
    monkeypatch.setattr(WanxModel, "api_key", property(forbidden))
    monkeypatch.setattr("src.models.wanx.VideoSynthesis.async_call", forbidden)
    monkeypatch.setattr("src.models.wanx.VideoSynthesis.wait", forbidden)
    return state


@pytest.fixture
def model(transport):
    config = Config()
    config.config = {"model": {"name": "wanx", "params": {"model_name": "unrelated"}}}
    return ModelFactory.create_model(config)


def fetch(model, handle, observation, owner, **kwargs):
    options = dict(output_root=owner.root, output_ref="video/result.mp4",
                   writer_lock=owner.lease, storage_guard=owner.guard, max_bytes=1024)
    options.update(kwargs)
    return model.fetch(handle, observation, **options)


def temps(owner):
    return list(owner.root.rglob(".fetch-*.tmp"))


def test_fetch_returns_complete_immutable_artifact_without_input_role(
    model, handle, observation, owner, transport,
):
    result = fetch(model, handle, observation, owner)
    assert (owner.root / result.relative_path).read_bytes() == b"first second"
    assert result.handle == handle
    assert result.relative_path == "video/result.mp4"
    assert result.sha256 == hashlib.sha256(b"first second").hexdigest()
    assert result.size_bytes == 12
    assert not hasattr(result, "role")
    assert not (owner.root / "media_versions").exists()
    assert not temps(owner)
    assert len(transport["calls"]) == 1
    assert transport["response"].closed
    assert RESULT_URL not in repr(result)
    with pytest.raises(FrozenInstanceError):
        result.size_bytes = 99


def test_fetch_on_prepared_runs_after_stage_fsync_before_publish(
    model, handle, observation, owner, transport,
):
    final = owner.root / "video/result.mp4"
    seen = []

    def on_prepared(artifact):
        assert owner.lock._is_owned()
        assert not final.exists()
        assert artifact.handle == handle
        assert artifact.relative_path == "video/result.mp4"
        assert artifact.sha256 == hashlib.sha256(b"first second").hexdigest()
        assert artifact.size_bytes == 12
        seen.append(artifact)

    result = fetch(model, handle, observation, owner, on_prepared=on_prepared)

    assert seen == [result]
    assert final.read_bytes() == b"first second"
    assert len(transport["calls"]) == 1


def test_fetch_on_prepared_failure_prevents_final_publish(
    model, handle, observation, owner, transport,
):
    final = owner.root / "video/result.mp4"

    def on_prepared(_artifact):
        assert not final.exists()
        raise StorageError("metadata save failed")

    with pytest.raises(StorageError, match="metadata save failed"):
        fetch(model, handle, observation, owner, on_prepared=on_prepared)

    assert not final.exists()
    assert len(transport["calls"]) == 1


@pytest.mark.parametrize("bad_return", [object(), (item for item in [None])])
def test_fetch_refuses_deferred_on_prepared_return(
    model, handle, observation, owner, transport, bad_return,
):
    with pytest.raises(ValueError):
        fetch(model, handle, observation, owner, on_prepared=lambda _artifact: bad_return)

    assert not (owner.root / "video/result.mp4").exists()


def test_actual_request_is_anonymous_get_without_environment_auth_or_redirects(
    monkeypatch, model, handle, observation, owner, transport,
):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "ambient-provider-secret")
    monkeypatch.setenv("HTTPS_PROXY", "http://ambient-user:ambient-password@proxy.invalid")

    def forbidden_netrc(*args):
        pytest.fail("anonymous download must not read .netrc")

    monkeypatch.setattr("requests.sessions.get_netrc_auth", forbidden_netrc)
    fetch(model, handle, observation, owner)
    session, request, options = transport["calls"][0]
    assert session.trust_env is False
    assert session.get_adapter(RESULT_URL).max_retries.total == 0
    assert request.method == "GET" and request.url == RESULT_URL
    assert not {"authorization", "proxy-authorization", "cookie", "x-dashscope-workspace"} & {
        key.lower() for key in request.headers
    }
    assert options["allow_redirects"] is False
    assert options["stream"] is True
    assert options["proxies"] == {}
    assert all(0 < value <= 60 for value in options["timeout"])


def test_real_query_source_flows_into_fetch_without_another_query(
    monkeypatch, contract, model, handle, owner, transport,
):
    query_calls = []

    def query_get(url, **kwargs):
        query_calls.append(url)
        return SimpleNamespace(status_code=200, json=lambda: {"output": {
            "task_id": handle.task_id, "task_status": "SUCCEEDED", "video_url": RESULT_URL,
        }})

    monkeypatch.setattr("src.models.wanx.requests.get", query_get)
    credential = contract.ResolvedCredential(handle.scope, handle.credential_ref, "test-only-key")
    observed = ORIGINAL_QUERY(model, handle, credential=credential)
    assert observed.source_handle == handle
    artifact = fetch(model, handle, observed, owner)
    assert artifact.handle == handle
    assert len(query_calls) == len(transport["calls"]) == 1


@pytest.mark.parametrize("change", [
    {"source_handle": None}, {"task_id": "other"},
    {"status": "succeeded"}, {"status": "failed"}, {"status": "unknown"},
    {"video_url": None}, {"video_url": "file:///private/video.mp4"},
    {"video_url": "https://user:secret@cdn.invalid/video"},
    {"video_url": "https://cdn.invalid:bad/video"}, {"video_url": "https://cdn.invalid:0/v"},
    {"video_url": "https://cdn.invalid/v\x00.mp4"},
    {"video_url": "https://cdn.invalid/v\x85.mp4"}, {"video_url": "https://cdn.invalid/v#fragment"},
])
def test_bad_observation_is_rejected_before_http_or_file_changes(
    contract, model, handle, observation, owner, transport, change,
):
    with pytest.raises(contract.ProviderBindingError):
        fetch(model, handle, replace(observation, **change), owner)
    assert transport["calls"] == []
    assert not list((owner.root / "video").iterdir())


@pytest.mark.parametrize("field", ["task", "scope", "credential", "model", "missing_scope", "capability"])
def test_source_receipt_binding_cannot_cross_task_or_account(
    contract, model, handle, observation, owner, transport, field,
):
    changed = {
        "task": {"task_id": "another-task"},
        "scope": {"scope": replace(handle.scope, account_identity="other-account")},
        "credential": {"credential_ref": "credentials/other"},
        "model": {"provider_model_id": "other-model"},
        "missing_scope": {"scope": None},
        "capability": {"query_capability": contract.QueryCapability.UNKNOWN},
    }[field]
    source = replace(handle, **changed)
    with pytest.raises(contract.ProviderBindingError):
        fetch(model, handle, replace(observation, source_handle=source), owner)
    assert transport["calls"] == []


@pytest.mark.parametrize("change", [{"scope": None}, {"credential_ref": None}, {"query_capability": "unknown"}])
def test_incomplete_handle_is_not_repaired_from_current_environment(
    contract, model, handle, observation, owner, transport, change,
):
    handle = replace(handle, **change)
    with pytest.raises(contract.ProviderBindingError):
        fetch(model, handle, replace(observation, source_handle=handle), owner)
    assert transport["calls"] == []


@pytest.mark.parametrize("ref", ["../escape.mp4", "video/../escape.mp4", "projects.json", "/video/out.mp4", "video//x.mp4"])
def test_invalid_destination_has_zero_http(model, handle, observation, owner, transport, ref):
    with pytest.raises(ValueError):
        fetch(model, handle, observation, owner, output_ref=ref)
    assert transport["calls"] == []


@pytest.mark.parametrize("limit", [0, -1, True, None, 1.5])
def test_explicit_positive_limit_is_required(model, handle, observation, owner, transport, limit):
    with pytest.raises(ValueError):
        fetch(model, handle, observation, owner, max_bytes=limit)
    assert transport["calls"] == []


def test_wrong_or_closed_writer_has_zero_http(model, handle, observation, owner, transport, tmp_path):
    other = tmp_path / "other"
    with DirectoryWriterLock(other) as lease:
        with pytest.raises((StorageError, ValueError)):
            fetch(model, handle, observation, owner, writer_lock=lease)
    owner.close()
    with pytest.raises(StorageError):
        fetch(model, handle, observation, owner)
    assert transport["calls"] == []


@pytest.mark.parametrize("location", ["parent", "target"])
def test_symlink_destination_has_zero_http(
    model, handle, observation, owner, transport, tmp_path, location,
):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "result.mp4").write_bytes(b"outside")
    if location == "parent":
        (owner.root / "video").rmdir()
        (owner.root / "video").symlink_to(outside, target_is_directory=True)
    else:
        (owner.root / "video/result.mp4").symlink_to(outside / "result.mp4")
    with pytest.raises(ValueError):
        fetch(model, handle, observation, owner)
    assert (outside / "result.mp4").read_bytes() == b"outside"
    assert transport["calls"] == []


@pytest.mark.parametrize("status", [206, 301, 302, 307, 308, 401, 403, 404, 500])
def test_http_error_has_no_redirect_retry_or_output_replacement(
    contract, model, handle, observation, owner, transport, status,
):
    final = owner.root / "video/result.mp4"
    final.write_bytes(b"old paid video")
    transport["response"] = Response(status_code=status)
    with pytest.raises(contract.ProviderFetchError) as error:
        fetch(model, handle, observation, owner)
    assert error.value.kind == "http" and error.value.status_code == status
    assert len(transport["calls"]) == 1
    assert final.read_bytes() == b"old paid video"
    assert not temps(owner)
    assert transport["response"].closed


@pytest.mark.parametrize("phase", ["connect", "stream"])
def test_transport_failure_keeps_old_media_and_sanitizes_diagnostics(
    contract, model, handle, observation, owner, transport, phase,
):
    final = owner.root / "video/result.mp4"
    final.write_bytes(b"old paid video")
    failure = requests.ConnectionError("RAW SECRET " + RESULT_URL)

    def interrupted():
        yield b"partial"
        raise failure

    transport["response"] = failure if phase == "connect" else Response(interrupted())
    with pytest.raises(contract.ProviderFetchError) as error:
        fetch(model, handle, observation, owner)
    assert error.value.kind == "transport"
    assert "RAW SECRET" not in str(error.value) and RESULT_URL not in str(error.value)
    assert error.value.__suppress_context__
    assert final.read_bytes() == b"old paid video"
    assert not temps(owner)
    assert len(transport["calls"]) == 1


@pytest.mark.parametrize("chunks,headers", [
    ([], {}), ([b"too", b"large"], {}), ([b"12345"], {"Content-Length": "1"}),
    ([b"12"], {"Content-Length": "4"}), ([b"12"], {"Content-Length": "9999"}),
    ([b"12"], {"Content-Length": "nonsense"}),
])
def test_empty_oversized_or_truncated_download_never_replaces_old_media(
    contract, model, handle, observation, owner, transport, chunks, headers,
):
    final = owner.root / "video/result.mp4"
    final.write_bytes(b"old")
    transport["response"] = Response(chunks, headers=headers)
    with pytest.raises((contract.ProviderFetchError, media.MediaVersionError)):
        fetch(model, handle, observation, owner, max_bytes=4)
    assert final.read_bytes() == b"old"
    assert not temps(owner)


@pytest.mark.parametrize("fault", ["write", "file_sync", "replace", "directory_sync"])
def test_storage_failures_preserve_committed_media_and_report_commit_point(
    monkeypatch, model, handle, observation, owner, transport, fault,
):
    final = owner.root / "video/result.mp4"
    final.write_bytes(b"old")
    actual_sync = media.os.fsync
    actual_replace = media.os.replace
    published = False

    def replace_file(*args, **kwargs):
        nonlocal published
        if fault == "replace":
            raise OSError("replace failed")
        result = actual_replace(*args, **kwargs)
        published = True
        return result

    def sync(fd):
        directory = stat.S_ISDIR(os.fstat(fd).st_mode)
        if fault == "file_sync" and not directory or fault == "directory_sync" and published and directory:
            raise OSError("sync failed")
        return actual_sync(fd)

    monkeypatch.setattr(media.os, "replace", replace_file)
    monkeypatch.setattr(media.os, "fsync", sync)
    if fault == "write":
        monkeypatch.setattr(media.os, "write", lambda *args: (_ for _ in ()).throw(OSError("full")))
    with pytest.raises(StorageError) as error:
        fetch(model, handle, observation, owner)
    assert error.value.commit_uncertain is (fault == "directory_sync")
    assert final.read_bytes() == (b"first second" if published else b"old")
    assert not temps(owner)
    assert transport["response"].closed


def test_network_iteration_is_outside_guard_and_close_prevents_late_writes(
    model, handle, observation, owner, transport,
):
    final = owner.root / "video/result.mp4"
    final.write_bytes(b"old paid video")
    waiting, resume = Event(), Event()

    def chunks():
        assert not owner.lock._is_owned()
        yield b"early"
        assert not owner.lock._is_owned()
        waiting.set()
        assert resume.wait(5)
        yield b"late"

    transport["response"] = Response(chunks())
    with ThreadPoolExecutor(max_workers=2) as pool:
        task = pool.submit(fetch, model, handle, observation, owner)
        assert waiting.wait(5)
        close_task = pool.submit(owner.close)
        try:
            close_task.result(timeout=2)
            staged = temps(owner)
            assert len(staged) == 1 and staged[0].read_bytes() == b"early"
        finally:
            resume.set()
        with pytest.raises(StorageError):
            task.result(timeout=5)
    assert staged[0].read_bytes() == b"early", "closed runtime must not write or clean another owner's stage"
    assert final.read_bytes() == b"old paid video"
    assert transport["response"].closed


@pytest.mark.parametrize("swap", ["root", "parent"])
def test_directory_swap_during_network_wait_does_not_publish_into_wrong_root(
    model, handle, observation, owner, transport, tmp_path, swap,
):
    original = owner.root / "video/result.mp4"
    original.write_bytes(b"old")
    moved = tmp_path / "moved"

    def chunks():
        yield b"early"
        if swap == "root":
            owner.root.rename(moved)
            owner.root.mkdir()
            (owner.root / "video").mkdir()
        else:
            (owner.root / "video").rename(moved)
            (owner.root / "video").mkdir()
        (owner.root / "video/result.mp4").write_bytes(b"new owner")
        yield b"late"

    transport["response"] = Response(chunks())
    with pytest.raises((StorageError, ValueError)):
        fetch(model, handle, observation, owner)
    assert original.read_bytes() == b"new owner"
    old_final = moved / ("video/result.mp4" if swap == "root" else "result.mp4")
    assert old_final.read_bytes() == b"old"
    old_stages = list(moved.rglob(".fetch-*.tmp"))
    assert len(old_stages) == 1 and old_stages[0].read_bytes() == b"early"


def test_same_handle_can_be_fetched_again_without_submitting(model, handle, observation, owner, transport):
    first = fetch(model, handle, observation, owner)
    transport["response"] = Response([b"first second"])
    second = fetch(model, handle, observation, owner)
    assert second == first
    assert len(transport["calls"]) == 2
    assert not temps(owner)


def test_response_close_error_does_not_mask_primary_storage_failure(
    monkeypatch, model, handle, observation, owner, transport,
):
    final = owner.root / "video/result.mp4"
    final.write_bytes(b"old paid video")

    def close():
        raise requests.ConnectionError("RAW PRIVATE " + RESULT_URL)

    transport["response"].close = close
    monkeypatch.setattr(media.os, "write", lambda *args: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(StorageError) as failure:
        fetch(model, handle, observation, owner)
    assert not failure.value.commit_uncertain
    assert "RAW PRIVATE" not in str(failure.value)
    assert final.read_bytes() == b"old paid video"
    assert not temps(owner)


def test_concurrent_downloads_use_distinct_stages_and_complete_replacements(
    monkeypatch, model, handle, observation, owner, transport,
):
    rendezvous = Barrier(2)
    stages = []

    def chunks():
        assert not owner.lock._is_owned()
        rendezvous.wait(timeout=5)
        stages.append({path.name for path in temps(owner)})
        rendezvous.wait(timeout=5)
        yield b"complete shared video"

    def send(session, request, **options):
        assert not owner.lock._is_owned()
        transport["calls"].append((session, request, options))
        return Response(chunks())

    monkeypatch.setattr("requests.sessions.Session.send", send)
    with ThreadPoolExecutor(max_workers=2) as pool:
        tasks = [pool.submit(fetch, model, handle, observation, owner) for _ in range(2)]
        artifacts = [task.result(timeout=5) for task in tasks]
    assert all(len(names) == 2 for names in stages)
    assert artifacts[0] == artifacts[1]
    assert (owner.root / "video/result.mp4").read_bytes() == b"complete shared video"
    assert not temps(owner)
    assert len(transport["calls"]) == 2


def test_publish_holds_same_guard_as_close(
    monkeypatch, model, handle, observation, owner,
):
    actual_replace = media.os.replace
    close_attempted, close_finished = Event(), Event()
    with ThreadPoolExecutor(max_workers=1) as pool:
        closing = []

        def close():
            close_attempted.set()
            owner.close()
            close_finished.set()

        def publish(*args, **kwargs):
            assert owner.lock._is_owned()
            closing.append(pool.submit(close))
            assert close_attempted.wait(2)
            assert not close_finished.wait(0.05)
            return actual_replace(*args, **kwargs)

        monkeypatch.setattr(media.os, "replace", publish)
        artifact = fetch(model, handle, observation, owner)
        closing[0].result(timeout=2)
    assert close_finished.is_set()
    assert (owner.root / artifact.relative_path).read_bytes() == b"first second"


def test_close_after_last_chunk_still_prevents_publish(model, handle, observation, owner, transport):
    final = owner.root / "video/result.mp4"
    final.write_bytes(b"old")

    def chunks():
        yield b"complete but uncommitted"
        owner.close()

    transport["response"] = Response(chunks())
    with pytest.raises(StorageError):
        fetch(model, handle, observation, owner)
    assert final.read_bytes() == b"old"
    assert len(temps(owner)) == 1
    assert temps(owner)[0].read_bytes() == b"complete but uncommitted"


@pytest.mark.parametrize("guard", [None, False, "not a guard"])
def test_missing_guard_is_rejected_before_http(model, handle, observation, owner, transport, guard):
    with pytest.raises(ValueError):
        fetch(model, handle, observation, owner, storage_guard=guard)
    assert transport["calls"] == []


def test_secure_publication_is_explicitly_unsupported_on_windows(
    monkeypatch, model, handle, observation, owner, transport,
):
    monkeypatch.setattr(media, "_PLATFORM", "win32")
    with pytest.raises(media.MediaVersionError, match="macOS|Linux"):
        fetch(model, handle, observation, owner)
    assert transport["calls"] == []


def test_short_writes_are_completed_within_guard(
    monkeypatch, model, handle, observation, owner, transport,
):
    actual_write = media.os.write
    writes = []

    def short_write(fd, value):
        assert owner.lock._is_owned()
        writes.append(len(value))
        return actual_write(fd, value[:2])

    monkeypatch.setattr(media.os, "write", short_write)
    artifact = fetch(model, handle, observation, owner, output_ref="video/nested/result.mp4")
    assert len(writes) > 2
    assert (owner.root / artifact.relative_path).read_bytes() == b"first second"


def test_replaced_temporary_name_is_not_published_or_removed_as_our_file(
    model, handle, observation, owner, transport,
):
    final = owner.root / "video/result.mp4"
    final.write_bytes(b"old")

    def chunks():
        yield b"early"
        stage = temps(owner)[0]
        stage.rename(stage.with_suffix(".moved"))
        stage.write_bytes(b"belongs to another operation")
        yield b"late"

    transport["response"] = Response(chunks())
    with pytest.raises(StorageError):
        fetch(model, handle, observation, owner)
    assert final.read_bytes() == b"old"
    assert temps(owner)[0].read_bytes() == b"belongs to another operation"


def test_compressed_body_is_rejected_before_content_length_can_be_misinterpreted(
    contract, model, handle, observation, owner, transport,
):
    transport["response"] = Response([b"decoded"], headers={"Content-Encoding": "gzip"})
    with pytest.raises(contract.ProviderFetchError, match="protocol"):
        fetch(model, handle, observation, owner)
    assert not (owner.root / "video/result.mp4").exists()
    assert not temps(owner)


@pytest.mark.parametrize("ref", [
    "playground/history.json", "video/manifest.json", "assets/characters/character.json",
    "uploads/user-document.txt", "video/.lumenx-writer.lock",
])
def test_download_cannot_overwrite_managed_metadata_or_nonvideo_files(
    model, handle, observation, owner, transport, ref,
):
    target = owner.root / ref
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b'{"must":"remain unchanged"}')
    with pytest.raises(media.MediaVersionError):
        fetch(model, handle, observation, owner, output_ref=ref)
    assert transport["calls"] == []
    assert target.read_bytes() == b'{"must":"remain unchanged"}'


@pytest.mark.parametrize("prefix", ["video", "playground"])
@pytest.mark.parametrize("suffix", [".mp4", ".mov", ".webm"])
def test_download_accepts_only_explicit_managed_video_destinations(
    model, handle, observation, owner, prefix, suffix,
):
    ref = f"{prefix}/attempt-1/result{suffix}"
    artifact = fetch(model, handle, observation, owner, output_ref=ref)
    assert artifact.relative_path == ref
    assert (owner.root / ref).read_bytes() == b"first second"


@pytest.mark.parametrize("phase", ["between_chunks", "before_publish"])
def test_same_inode_same_size_stage_rewrite_is_not_returned_with_original_hash(
    model, handle, observation, owner, transport, phase,
):
    final = owner.root / "video/result.mp4"
    final.write_bytes(b"old paid video")

    def chunks():
        yield b"early"
        staged = temps(owner)[0]
        before = staged.stat()
        with staged.open("r+b") as stream:
            stream.write(b"WRONG")
            stream.flush()
            os.fsync(stream.fileno())
        assert staged.stat().st_ino == before.st_ino
        assert staged.stat().st_size == before.st_size
        if phase == "between_chunks":
            yield b"late"

    transport["response"] = Response(chunks())
    with pytest.raises(StorageError):
        fetch(model, handle, observation, owner)
    assert final.read_bytes() == b"old paid video"

def test_verify_managed_media_file_rejects_path_replaced_during_hash(monkeypatch, owner):
    target = owner.root / "video/result.mp4"
    target.write_bytes(b"first second")
    expected_hash = hashlib.sha256(b"first second").hexdigest()
    actual_chunks = media._chunks
    replaced = {"value": False}

    def replacing_chunks(fd):
        yield from actual_chunks(fd)
        if not replaced["value"]:
            replaced["value"] = True
            target.unlink()
            target.write_bytes(b"first second")

    monkeypatch.setattr(media, "_chunks", replacing_chunks)

    with pytest.raises(StorageError):
        media.verify_managed_media_file(
            "video/result.mp4",
            output_root=owner.root,
            writer_lock=owner.lease,
            storage_guard=owner.guard,
            expected_sha256=expected_hash,
            expected_size=len(b"first second"),
            max_bytes=1024,
        )


def test_verify_managed_media_file_opens_final_with_nonblocking(monkeypatch, owner):
    target = owner.root / "video/result.mp4"
    target.write_bytes(b"first second")
    expected_hash = hashlib.sha256(b"first second").hexdigest()
    actual_open = media.os.open
    final_flags = []

    def recording_open(path, flags, mode=0o777, *, dir_fd=None):
        if path == "result.mp4":
            final_flags.append(flags)
        return actual_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(media.os, "open", recording_open)

    media.verify_managed_media_file(
        "video/result.mp4",
        output_root=owner.root,
        writer_lock=owner.lease,
        storage_guard=owner.guard,
        expected_sha256=expected_hash,
        expected_size=len(b"first second"),
        max_bytes=1024,
    )

    assert final_flags
    assert all(flags & os.O_NONBLOCK for flags in final_flags)


def test_fetch_rejects_async_prepared_callback_before_http(model, handle, observation, owner, transport):
    async def on_prepared(_artifact):
        return None

    with pytest.raises(ValueError):
        fetch(model, handle, observation, owner, on_prepared=on_prepared)

    assert transport["calls"] == []


def test_fetch_rejects_callable_async_prepared_callback_before_http(model, handle, observation, owner, transport):
    class AsyncCallback:
        async def __call__(self, _artifact):
            return None

    with pytest.raises(ValueError):
        fetch(model, handle, observation, owner, on_prepared=AsyncCallback())

    assert transport["calls"] == []


def test_fetch_closes_coroutine_returned_from_prepared_callback(
    model, handle, observation, owner, transport,
):
    closed = {"value": False}

    class Deferred:
        def __await__(self):
            if False:
                yield None
            return None

        def close(self):
            closed["value"] = True

    with pytest.raises(ValueError):
        fetch(model, handle, observation, owner, on_prepared=lambda _artifact: Deferred())

    assert closed["value"] is True
    assert len(transport["calls"]) == 1
