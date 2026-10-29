"""Private cleanup keeps its own directory and preserves original save errors."""
import os

import pytest

from src.utils.json_storage import StorageError
from tests import test_studio_core_image_worker as worker

runtime = worker.runtime
executions = worker.wan_executions


@pytest.mark.parametrize("replacement", ["directory", "symlink"])
def test_replaced_scratch_is_not_cleaned_by_failed_original_worker(runtime, executions, monkeypatch, tmp_path, replacement):
    execute, calls, paths = executions
    root = worker.admission.setup(runtime, "local")
    svc = worker.service(runtime, execute)
    ack = svc.admit(*root, worker.admission.command())
    original_commit = runtime._storage.commit
    captured = {}

    def save(payload):
        rows = payload.get("projects.json", {}).get("p", {}).get("asset_image_requests", [])
        if rows and rows[0]["members"][0]["execution"]["phase"] == "observed" and rows[0]["members"][0]["execution"]["prepared_artifact"] is not None:
            scratch = paths[0].parent
            original = scratch.with_name(scratch.name + "-original")
            scratch.rename(original)
            replacement_root = tmp_path / "replacement" if replacement == "symlink" else scratch
            replacement_root.mkdir()
            (replacement_root / paths[0].name).write_bytes(worker.PNG)
            sentinel = replacement_root / "unrelated.txt"
            sentinel.write_text("must remain")
            if replacement == "symlink":
                scratch.symlink_to(replacement_root, target_is_directory=True)
            captured.update(original=original, sentinel=sentinel, replacement_root=replacement_root)
            raise StorageError("original prepared save failure")
        return original_commit(payload)

    monkeypatch.setattr(runtime._storage, "commit", save)
    with pytest.raises(StorageError, match="original prepared save failure"):
        svc.run_original_request(ack.request.id)
    assert len(calls) == len(executions.posts) == len(executions.queries) == len(executions.downloads) == 1
    worker.assert_png(captured["original"] / paths[0].name)
    assert captured["sentinel"].read_text() == "must remain"
    assert (captured["replacement_root"] / paths[0].name).read_bytes() == worker.PNG
    assert worker.disk_request(runtime, "local")["members"][0]["execution"]["phase"] == "observed"


def test_cleanup_failure_does_not_hide_original_storage_error(runtime, executions, monkeypatch):
    execute, calls, paths = executions
    root = worker.admission.setup(runtime, "local")

    def private(*args, **kwargs):
        result = execute(*args, **kwargs)
        (result.path.parent / "raw.tmp").write_bytes(b"unverified")
        return result

    svc = worker.service(runtime, private)
    ack = svc.admit(*root, worker.admission.command())
    original_commit = runtime._storage.commit

    def save(payload):
        rows = payload.get("projects.json", {}).get("p", {}).get("asset_image_requests", [])
        if rows and rows[0]["members"][0]["execution"]["phase"] == "observed" and rows[0]["members"][0]["execution"]["prepared_artifact"] is not None:
            raise StorageError("original prepared save failure")
        return original_commit(payload)

    unlink = os.unlink
    cleanup_failures = []
    def fail_unlink(path, *args, **kwargs):
        if os.fspath(path) == "raw.tmp" and kwargs.get("dir_fd") is not None:
            cleanup_failures.append(path)
            raise PermissionError("cleanup denied")
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(runtime._storage, "commit", save)
    monkeypatch.setattr(os, "unlink", fail_unlink)
    with pytest.raises(StorageError, match="original prepared save failure"):
        svc.run_original_request(ack.request.id)
    assert len(calls) == len(executions.posts) == len(executions.queries) == len(executions.downloads) == 1
    worker.assert_png(paths[0])
    assert cleanup_failures == ["raw.tmp"]
