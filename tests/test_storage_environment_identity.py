from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.apps.comic_gen.api import create_app
from src.utils.json_storage import DirectoryWriterLock, StorageConflictError, StorageError
from src.apps.comic_gen.pipeline import ComicGenPipeline
from src.utils import storage_identity as storage_identity_module
from src.utils.storage_identity import STORAGE_IDENTITY_FILENAME, initialize_storage_identity, load_storage_identity

TOKEN = "test-access-" * 4
UUID4_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")


class StubPipeline:
    def __init__(self, root: Path, identity: dict[str, object] | None = None):
        self.data_dir = root
        self.scripts = {}
        if identity is not None:
            self.storage_identity = identity
        self.project_reads = 0

    def assert_storage_available(self):
        return None

    def create_library_asset(self, *args, **kwargs):
        return None

    def read_committed_projects(self):
        self.project_reads += 1
        return []

    def close(self):
        return None


class FailingStoragePipeline(StubPipeline):
    fail_storage = False

    def assert_storage_available(self):
        if self.fail_storage:
            raise StorageError("storage closed for test")


def test_storage_identity_is_stable_across_restart(tmp_path):
    with DirectoryWriterLock(tmp_path) as lease:
        first = initialize_storage_identity(tmp_path, writer_lock=lease)
    with DirectoryWriterLock(tmp_path) as lease:
        second = initialize_storage_identity(tmp_path, writer_lock=lease)

    assert second == first
    assert first["schema_version"] == 1
    assert isinstance(first["storage_id"], str)
    assert UUID4_RE.match(first["storage_id"])


def test_different_roots_receive_different_storage_identities(tmp_path):
    with DirectoryWriterLock(tmp_path / "a") as lease_a:
        first = initialize_storage_identity(tmp_path / "a", writer_lock=lease_a)
    with DirectoryWriterLock(tmp_path / "b") as lease_b:
        second = initialize_storage_identity(tmp_path / "b", writer_lock=lease_b)

    assert first["storage_id"] != second["storage_id"]


@pytest.mark.parametrize(
    "contents",
    [
        "{",
        "[]",
        json.dumps({"schema_version": 1, "storage_id": "not-a-uuid"}),
        json.dumps({"schema_version": True, "storage_id": "00000000-0000-4000-8000-000000000000"}),
        json.dumps({"schema_version": 1, "storage_id": "00000000-0000-1000-8000-000000000000"}),
        json.dumps({"schema_version": 1, "storage_id": "123e4567-e89b-42d3-a456-426614174000".upper()}),
        json.dumps({
            "schema_version": 1,
            "storage_id": "00000000-0000-4000-8000-000000000000",
            "extra": True,
        }),
        '{"schema_version":1,"schema_version":1,"storage_id":"00000000-0000-4000-8000-000000000000"}',
        json.dumps({"schema_version": 1.0, "storage_id": "00000000-0000-4000-8000-000000000000"}),
    ],
)
def test_corrupt_or_noncanonical_identity_fails_closed_without_rotation(tmp_path, contents):
    path = tmp_path / "storage_identity.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents, encoding="utf-8")
    before = path.read_bytes()

    with DirectoryWriterLock(tmp_path) as lease:
        with pytest.raises(StorageError):
            initialize_storage_identity(tmp_path, writer_lock=lease)

    assert path.read_bytes() == before




def test_identity_rejects_entry_replaced_between_stat_and_open(tmp_path, monkeypatch):
    original = {"schema_version": 1, "storage_id": "00000000-0000-4000-8000-000000000000"}
    replacement = {"schema_version": 1, "storage_id": "11111111-1111-4111-8111-111111111111"}
    path = tmp_path / STORAGE_IDENTITY_FILENAME
    path.write_text(json.dumps(original), encoding="utf-8")
    monkeypatch.setattr(storage_identity_module, "_directory_fd_ops_supported", lambda: True)
    real_open = storage_identity_module.os.open
    replaced = False

    def racing_open(file, flags, mode=0o777, *, dir_fd=None):
        nonlocal replaced
        if file == STORAGE_IDENTITY_FILENAME and dir_fd is not None and not replaced:
            replaced = True
            path.unlink()
            path.write_text(json.dumps(replacement), encoding="utf-8")
        return real_open(file, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(storage_identity_module.os, "open", racing_open)
    with DirectoryWriterLock(tmp_path) as lease:
        with pytest.raises(StorageError):
            initialize_storage_identity(tmp_path, writer_lock=lease)

    assert replaced
    assert json.loads(path.read_text(encoding="utf-8")) == replacement


def test_identity_rejects_entry_removed_between_stat_and_open_without_rotation(tmp_path, monkeypatch):
    original = {"schema_version": 1, "storage_id": "00000000-0000-4000-8000-000000000000"}
    path = tmp_path / STORAGE_IDENTITY_FILENAME
    path.write_text(json.dumps(original), encoding="utf-8")
    monkeypatch.setattr(storage_identity_module, "_directory_fd_ops_supported", lambda: True)
    real_open = storage_identity_module.os.open
    removed = False

    def racing_open(file, flags, mode=0o777, *, dir_fd=None):
        nonlocal removed
        if file == STORAGE_IDENTITY_FILENAME and dir_fd is not None and not removed:
            removed = True
            path.unlink()
        return real_open(file, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(storage_identity_module.os, "open", racing_open)
    with DirectoryWriterLock(tmp_path) as lease:
        with pytest.raises(StorageError):
            initialize_storage_identity(tmp_path, writer_lock=lease)

    assert removed
    assert not path.exists()


def test_identity_publish_fails_when_root_path_is_replaced_before_return(tmp_path, monkeypatch):
    root = tmp_path / "data"
    original_root = tmp_path / "data-original"
    with DirectoryWriterLock(root) as lease:
        monkeypatch.setattr(storage_identity_module, "_directory_fd_ops_supported", lambda: True)
        real_link = storage_identity_module.os.link
        swapped = False

        def racing_link(src, dst, *, src_dir_fd=None, dst_dir_fd=None, follow_symlinks=True):
            nonlocal swapped
            if src_dir_fd is not None and dst_dir_fd is not None and not swapped:
                swapped = True
                root.rename(original_root)
                root.mkdir()
            return real_link(
                src,
                dst,
                src_dir_fd=src_dir_fd,
                dst_dir_fd=dst_dir_fd,
                follow_symlinks=follow_symlinks,
            )

        monkeypatch.setattr(storage_identity_module.os, "link", racing_link)
        with pytest.raises(StorageConflictError):
            initialize_storage_identity(root, writer_lock=lease)

    assert swapped
    assert not (root / STORAGE_IDENTITY_FILENAME).exists()
    assert (original_root / STORAGE_IDENTITY_FILENAME).exists()


def test_identity_initializes_and_loads_when_directory_fd_ops_are_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr(storage_identity_module, "_directory_fd_ops_supported", lambda: False)

    with DirectoryWriterLock(tmp_path) as lease:
        first = initialize_storage_identity(tmp_path, writer_lock=lease)
        assert load_storage_identity(tmp_path, writer_lock=lease) == first

    with DirectoryWriterLock(tmp_path) as lease:
        second = initialize_storage_identity(tmp_path, writer_lock=lease)

    assert second == first


def test_identity_fallback_does_not_overwrite_existing_entry(tmp_path, monkeypatch):
    monkeypatch.setattr(storage_identity_module, "_directory_fd_ops_supported", lambda: False)
    existing = {"schema_version": 1, "storage_id": "00000000-0000-4000-8000-000000000000"}
    path = tmp_path / STORAGE_IDENTITY_FILENAME
    path.write_text(json.dumps(existing), encoding="utf-8")

    with DirectoryWriterLock(tmp_path) as lease:
        assert initialize_storage_identity(tmp_path, writer_lock=lease) == existing

    assert json.loads(path.read_text(encoding="utf-8")) == existing


def test_identity_fallback_rejects_replaced_root_path_without_writing_new_root(tmp_path, monkeypatch):
    monkeypatch.setattr(storage_identity_module, "_directory_fd_ops_supported", lambda: False)
    root = tmp_path / "data"
    original_root = tmp_path / "data-original"

    with DirectoryWriterLock(root) as lease:
        root.rename(original_root)
        root.mkdir()
        with pytest.raises(StorageConflictError):
            initialize_storage_identity(root, writer_lock=lease)

    assert not (root / STORAGE_IDENTITY_FILENAME).exists()
    assert not (original_root / STORAGE_IDENTITY_FILENAME).exists()


def test_identity_fallback_publish_failure_preserves_absence_for_retry(tmp_path, monkeypatch):
    monkeypatch.setattr(storage_identity_module, "_directory_fd_ops_supported", lambda: False)
    real_link = storage_identity_module.os.link
    attempted = False

    def failing_link(src, dst, *args, **kwargs):
        nonlocal attempted
        if Path(dst).name == STORAGE_IDENTITY_FILENAME and not attempted:
            attempted = True
            raise OSError("simulated publish failure")
        return real_link(src, dst, *args, **kwargs)

    monkeypatch.setattr(storage_identity_module.os, "link", failing_link)
    with DirectoryWriterLock(tmp_path) as lease:
        with pytest.raises(StorageError):
            initialize_storage_identity(tmp_path, writer_lock=lease)

    assert attempted
    assert not (tmp_path / STORAGE_IDENTITY_FILENAME).exists()

    monkeypatch.setattr(storage_identity_module.os, "link", real_link)
    with DirectoryWriterLock(tmp_path) as lease:
        assert initialize_storage_identity(tmp_path, writer_lock=lease)["schema_version"] == 1


def test_identity_fallback_publish_race_does_not_overwrite_existing_identity(tmp_path, monkeypatch):
    monkeypatch.setattr(storage_identity_module, "_directory_fd_ops_supported", lambda: False)
    existing = {"schema_version": 1, "storage_id": "00000000-0000-4000-8000-000000000000"}
    final_path = tmp_path / STORAGE_IDENTITY_FILENAME
    real_link = storage_identity_module.os.link

    def racing_link(src, dst, *args, **kwargs):
        if Path(dst).name == STORAGE_IDENTITY_FILENAME and not final_path.exists():
            final_path.write_text(json.dumps(existing), encoding="utf-8")
        return real_link(src, dst, *args, **kwargs)

    monkeypatch.setattr(storage_identity_module.os, "link", racing_link)
    with DirectoryWriterLock(tmp_path) as lease:
        with pytest.raises(StorageError):
            initialize_storage_identity(tmp_path, writer_lock=lease)

    assert json.loads(final_path.read_text(encoding="utf-8")) == existing


def test_comic_pipeline_initializes_storage_identity_and_reuses_it_on_restart(tmp_path):
    data_root = tmp_path / "data"
    first_pipeline = ComicGenPipeline(data_dir=data_root, warmup_demucs=False)
    try:
        first = first_pipeline.storage_identity
    finally:
        first_pipeline.close()

    second_pipeline = ComicGenPipeline(data_dir=data_root, warmup_demucs=False)
    try:
        assert second_pipeline.storage_identity == first
        assert load_storage_identity(data_root, writer_lock=second_pipeline._writer_lock) == first
    finally:
        second_pipeline.close()


def test_create_app_runtime_environment_uses_real_pipeline_identity(tmp_path):
    data_root = tmp_path / "data"
    app = create_app(data_dir=data_root, user_data_dir=tmp_path / "user", load_config=False, warmup_demucs=False)

    with TestClient(app) as client:
        response = client.get("/runtime/environment")
        assert response.status_code == 200
        identity = response.json()
        assert identity == app.state.pipeline.storage_identity
        assert response.headers["cache-control"] == "no-store"

    with DirectoryWriterLock(data_root) as lease:
        assert load_storage_identity(data_root, writer_lock=lease) == identity


def test_identity_symlink_is_rejected_without_touching_target(tmp_path):
    target = tmp_path / "outside.json"
    target.write_text(json.dumps({"schema_version": 1, "storage_id": "00000000-0000-4000-8000-000000000000"}), encoding="utf-8")
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "storage_identity.json").symlink_to(target)
    before = target.read_bytes()

    with DirectoryWriterLock(tmp_path / "data") as lease:
        with pytest.raises(StorageError):
            initialize_storage_identity(tmp_path / "data", writer_lock=lease)

    assert target.read_bytes() == before
    assert (tmp_path / "data" / "storage_identity.json").is_symlink()


def test_identity_requires_matching_live_writer_lock(tmp_path):
    lease = DirectoryWriterLock(tmp_path).acquire()
    lease.close()
    with pytest.raises(StorageConflictError):
        initialize_storage_identity(tmp_path, writer_lock=lease)

    with DirectoryWriterLock(tmp_path / "other") as other:
        with pytest.raises(StorageConflictError):
            initialize_storage_identity(tmp_path, writer_lock=other)


def test_load_identity_reader_also_rejects_closed_writer(tmp_path):
    with DirectoryWriterLock(tmp_path) as lease:
        identity = initialize_storage_identity(tmp_path, writer_lock=lease)
        assert load_storage_identity(tmp_path, writer_lock=lease) == identity
        lease.close()
        with pytest.raises(StorageConflictError):
            load_storage_identity(tmp_path, writer_lock=lease)


def make_app(tmp_path: Path, pipeline: StubPipeline, **kwargs):
    return create_app(
        data_dir=pipeline.data_dir,
        user_data_dir=tmp_path / "user",
        load_config=False,
        pipeline_instance=pipeline,
        **kwargs,
    )




def test_environment_header_match_allows_product_route(tmp_path):
    data_root = tmp_path / "data"
    app = create_app(data_dir=data_root, user_data_dir=tmp_path / "user", load_config=False, warmup_demucs=False)

    with TestClient(app) as client:
        storage_id = app.state.pipeline.storage_identity["storage_id"]
        calls = 0
        original = app.state.pipeline.read_committed_projects

        def counted_read_committed_projects():
            nonlocal calls
            calls += 1
            return original()

        app.state.pipeline.read_committed_projects = counted_read_committed_projects
        response = client.get("/projects/", headers={"X-LumenX-Environment": storage_id})

    assert response.status_code == 200
    assert calls == 1


def test_environment_header_mismatch_blocks_before_product_route_without_leaking_current_id(tmp_path):
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    with DirectoryWriterLock(root_a) as lease:
        old_identity = initialize_storage_identity(root_a, writer_lock=lease)
    app = create_app(data_dir=root_b, user_data_dir=tmp_path / "user", load_config=False, warmup_demucs=False)

    with TestClient(app) as client:
        current_id = app.state.pipeline.storage_identity["storage_id"]
        calls = 0
        original = app.state.pipeline.read_committed_projects

        def counted_read_committed_projects():
            nonlocal calls
            calls += 1
            return original()

        app.state.pipeline.read_committed_projects = counted_read_committed_projects
        response = client.get("/projects/", headers={"X-LumenX-Environment": old_identity["storage_id"]})

    assert response.status_code == 409
    assert response.json()["code"] == "environment_changed"
    assert current_id not in json.dumps(response.json())
    assert calls == 0


def test_environment_header_malformed_rejects_product_route(tmp_path):
    app = create_app(data_dir=tmp_path / "data", user_data_dir=tmp_path / "user", load_config=False, warmup_demucs=False)

    with TestClient(app) as client:
        response = client.get("/projects/", headers={"X-LumenX-Environment": "123E4567-E89B-42D3-A456-426614174000"})

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_environment"


def test_environment_header_does_not_bypass_remote_auth_or_leak_identity(tmp_path):
    identity = {"schema_version": 1, "storage_id": "00000000-0000-4000-8000-000000000000"}
    app = make_app(
        tmp_path,
        StubPipeline(tmp_path / "data", identity),
        access_mode="remote",
        access_token=TOKEN,
        access_allowed_origins=["https://studio.example"],
    )

    with TestClient(app, base_url="https://studio.example") as client:
        response = client.get(
            "/projects/",
            headers={"X-LumenX-Environment": "11111111-1111-4111-8111-111111111111"},
        )

    assert response.status_code == 401
    assert response.json()["code"] == "access_required"
    assert identity["storage_id"] not in json.dumps(response.json())


def test_environment_header_is_allowed_in_access_preflight(tmp_path):
    app = make_app(
        tmp_path,
        StubPipeline(tmp_path / "data", {"schema_version": 1, "storage_id": "00000000-0000-4000-8000-000000000000"}),
        access_mode="remote",
        access_token=TOKEN,
        access_allowed_origins=["https://studio.example"],
    )

    with TestClient(app, base_url="https://studio.example") as client:
        response = client.options(
            "/projects/",
            headers={
                "Origin": "https://studio.example",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "X-LumenX-Environment",
            },
        )

    assert response.status_code == 200
    assert "X-LumenX-Environment" in response.headers["access-control-allow-headers"]


def test_environment_header_storage_failure_returns_typed_503_without_route_call(tmp_path):
    identity = {"schema_version": 1, "storage_id": "00000000-0000-4000-8000-000000000000"}
    pipeline = FailingStoragePipeline(tmp_path / "data", identity)
    app = make_app(tmp_path, pipeline)

    with TestClient(app) as client:
        pipeline.fail_storage = True
        response = client.get("/projects/", headers={"X-LumenX-Environment": identity["storage_id"]})

    assert response.status_code == 503
    assert response.json()["code"] == "storage_unavailable"
    assert "storage_id" not in json.dumps(response.json())
    assert pipeline.project_reads == 0


def test_environment_header_storage_conflict_returns_typed_409_without_identity_leak(tmp_path):
    class ConflictingStoragePipeline(StubPipeline):
        fail_storage = False

        def assert_storage_available(self):
            if self.fail_storage:
                raise StorageConflictError("storage owner changed")

    identity = {"schema_version": 1, "storage_id": "00000000-0000-4000-8000-000000000000"}
    pipeline = ConflictingStoragePipeline(tmp_path / "data", identity)
    app = make_app(tmp_path, pipeline)

    with TestClient(app) as client:
        pipeline.fail_storage = True
        response = client.get("/projects/", headers={"X-LumenX-Environment": identity["storage_id"]})

    assert response.status_code == 409
    assert response.json()["code"] == "storage_conflict"
    assert identity["storage_id"] not in json.dumps(response.json())
    assert pipeline.project_reads == 0


def test_runtime_environment_requires_initialized_identity(tmp_path):
    root = tmp_path / "data"
    app = make_app(tmp_path, StubPipeline(root))
    with TestClient(app) as client:
        response = client.get("/runtime/environment")
    assert response.status_code == 503
    assert response.json()["code"] == "storage_unavailable"


def test_runtime_environment_is_protected_and_not_leaked_by_health_or_session(tmp_path):
    identity = {"schema_version": 1, "storage_id": "00000000-0000-4000-8000-000000000000"}
    root = tmp_path / "data"
    app = make_app(
        tmp_path,
        StubPipeline(root, identity),
        access_mode="remote",
        access_token=TOKEN,
        access_allowed_origins=["https://studio.example"],
    )
    with TestClient(app, base_url="https://studio.example") as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert "storage_id" not in json.dumps(health.json())

        session = client.get("/auth/session")
        assert session.status_code == 200
        assert session.json() == {"auth_required": True, "authenticated": False}

        blocked = client.get("/runtime/environment")
        assert blocked.status_code == 401
        assert blocked.json()["code"] == "access_required"

        login = client.post("/auth/session", json={"token": TOKEN}, headers={"Origin": "https://studio.example"})
        assert login.status_code == 200
        allowed = client.get("/runtime/environment")
        assert allowed.status_code == 200
        assert allowed.json() == identity
        assert allowed.headers["cache-control"] == "no-store"
