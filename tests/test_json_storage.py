"""Fault and ownership regressions for JSON persistence primitives."""

from __future__ import annotations

import errno
import os
import subprocess
import sys
from pathlib import Path

import pytest

from src.utils import json_storage
from src.utils.json_storage import (
    CorruptStorageError,
    DirectoryWriterLock,
    StorageConflictError,
    StorageError,
    atomic_write_json,
    read_json,
)


def test_read_defaults_only_for_missing_file(tmp_path):
    path = tmp_path / "projects.json"
    assert read_json(path, dict) == {}
    assert not path.exists()
    path.write_text('{"title": "作品", "enabled": false}', encoding="utf-8")
    assert read_json(path, dict) == {"title": "作品", "enabled": False}


@pytest.mark.parametrize(
    "contents", ["", "{", "null trailing", "NaN", "Infinity", "1e999", '{"id": 1, "id": 2}']
)
def test_corrupt_json_is_preserved_instead_of_defaulted(tmp_path, contents):
    path = tmp_path / "projects.json"
    path.write_text(contents, encoding="utf-8")
    with pytest.raises(CorruptStorageError):
        read_json(path, dict)
    assert path.read_text(encoding="utf-8") == contents


def test_invalid_utf8_is_corrupt_storage(tmp_path):
    path = tmp_path / "projects.json"
    path.write_bytes(b"\xff")
    with pytest.raises(CorruptStorageError):
        read_json(path, dict)
    assert path.read_bytes() == b"\xff"


def test_read_io_failure_is_not_a_missing_database(tmp_path, monkeypatch):
    def denied(*args, **kwargs):
        raise PermissionError("denied")

    monkeypatch.setattr(Path, "open", denied)
    with pytest.raises(StorageError, match="read"):
        read_json(tmp_path / "projects.json", dict)


def test_atomic_write_round_trip_leaves_no_temporary_file(tmp_path):
    path = tmp_path / "nested" / "projects.json"
    atomic_write_json(path, {"title": "作品", "revision": 1})
    assert read_json(path, dict) == {"title": "作品", "revision": 1}
    assert list(path.parent.iterdir()) == [path]


def test_json_serialization_is_available_without_touching_storage():
    contents = json_storage.serialize_json({"title": "作品", "enabled": False})
    assert '"作品"' in contents
    assert '"enabled": false' in contents
    with pytest.raises(StorageError):
        json_storage.serialize_json({1: "first", "1": "second"})


def test_text_restore_preserves_original_utf8_and_newline_bytes(tmp_path):
    path = tmp_path / "projects.json"
    original = b'{\r\n  "revision" : 1\r\n}\r\n'
    json_storage.atomic_write_text(path, original.decode("utf-8"))
    assert path.read_bytes() == original
    assert read_json(path, dict) == {"revision": 1}


@pytest.mark.parametrize(
    "value",
    [
        {"bad": object()},
        {"bad": float("nan")},
        {"bad": float("inf")},
        {1: "first", "1": "second"},
        {"bad": "\ud800"},
    ],
)
def test_serialization_failure_keeps_old_file(tmp_path, value):
    path = tmp_path / "projects.json"
    path.write_text('{"revision": 1}', encoding="utf-8")
    with pytest.raises(StorageError) as error:
        atomic_write_json(path, value)
    assert not error.value.commit_uncertain
    assert path.read_text(encoding="utf-8") == '{"revision": 1}'
    assert list(tmp_path.iterdir()) == [path]


def test_serialization_failure_does_not_create_parent(tmp_path):
    path = tmp_path / "missing" / "projects.json"
    with pytest.raises(StorageError):
        atomic_write_json(path, object())
    assert not path.parent.exists()


def test_replace_failure_keeps_old_file_and_removes_temp(tmp_path, monkeypatch):
    path = tmp_path / "projects.json"
    path.write_text('{"revision": 1}', encoding="utf-8")

    def fail_replace(*args):
        raise OSError(errno.EIO, "replace failed")

    monkeypatch.setattr(json_storage.os, "replace", fail_replace)
    with pytest.raises(StorageError) as error:
        atomic_write_json(path, {"revision": 2})
    assert not error.value.commit_uncertain
    assert read_json(path, dict) == {"revision": 1}
    assert list(tmp_path.iterdir()) == [path]


def test_file_fsync_failure_keeps_old_file_and_removes_temp(tmp_path, monkeypatch):
    path = tmp_path / "projects.json"
    path.write_text('{"revision": 1}', encoding="utf-8")

    def fail_fsync(*args):
        raise OSError(errno.ENOSPC, "disk full")

    monkeypatch.setattr(json_storage.os, "fsync", fail_fsync)
    with pytest.raises(StorageError) as error:
        atomic_write_json(path, {"revision": 2})
    assert not error.value.commit_uncertain
    assert read_json(path, dict) == {"revision": 1}
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("stage", ["write", "flush"])
def test_partial_write_or_flush_failure_keeps_old_file(tmp_path, monkeypatch, stage):
    path = tmp_path / "projects.json"
    path.write_text('{"revision": 1}', encoding="utf-8")
    original_fdopen = json_storage.os.fdopen

    class FailingStream:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.stream.close()

        def write(self, value):
            self.stream.write(value[:4])
            if stage == "write":
                raise OSError(errno.ENOSPC, "partial write")
            self.stream.write(value[4:])

        def flush(self):
            if stage == "flush":
                raise OSError(errno.ENOSPC, "flush failed")
            self.stream.flush()

        def fileno(self):
            return self.stream.fileno()

    monkeypatch.setattr(
        json_storage.os,
        "fdopen",
        lambda *args, **kwargs: FailingStream(original_fdopen(*args, **kwargs)),
    )
    with pytest.raises(StorageError) as error:
        atomic_write_json(path, {"revision": 2})
    assert not error.value.commit_uncertain
    assert read_json(path, dict) == {"revision": 1}
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.skipif(os.name == "nt", reason="Windows stdlib does not support directory fsync")
def test_directory_fsync_failure_reports_visible_but_uncertain_commit(tmp_path, monkeypatch):
    path = tmp_path / "projects.json"
    atomic_write_json(path, {"revision": 1})
    original_fsync = json_storage.os.fsync
    calls = []

    def fail_second_fsync(fd):
        calls.append(fd)
        if len(calls) == 2:
            raise OSError(errno.EIO, "directory sync failed")
        original_fsync(fd)

    monkeypatch.setattr(json_storage.os, "fsync", fail_second_fsync)
    with pytest.raises(StorageError, match="durability uncertain") as error:
        atomic_write_json(path, {"revision": 2})
    assert error.value.commit_uncertain
    assert read_json(path, dict) == {"revision": 2}
    assert list(tmp_path.iterdir()) == [path]


def test_writer_lock_lifetime_and_same_object_idempotency(tmp_path):
    directory = tmp_path / "database"
    owner = DirectoryWriterLock(directory)
    assert not directory.exists()
    assert owner.acquire() is owner
    assert owner.acquire() is owner
    other = DirectoryWriterLock(directory)
    with pytest.raises(StorageConflictError):
        other.acquire()
    owner.close()
    owner.close()
    with other as acquired:
        assert acquired is other
    assert owner.acquire() is owner
    owner.close()


def test_lock_file_survives_release_without_inode_replacement(tmp_path):
    with DirectoryWriterLock(tmp_path):
        files = list(tmp_path.iterdir())
        assert len(files) == 1
        lock_path = files[0]
        inode = lock_path.stat().st_ino
    assert lock_path.exists()
    with DirectoryWriterLock(tmp_path):
        assert lock_path.stat().st_ino == inode


def test_lock_directory_permission_error_is_not_an_ownership_conflict(tmp_path, monkeypatch):
    owner = DirectoryWriterLock(tmp_path)

    def denied(*args, **kwargs):
        raise PermissionError(errno.EACCES, "denied")

    monkeypatch.setattr(Path, "open", denied)
    with pytest.raises(StorageError) as error:
        owner.acquire()
    assert not isinstance(error.value, StorageConflictError)
    owner.close()


def _run_probe(tmp_path, contents, *arguments):
    script = tmp_path / "lock_probe.py"
    script.write_text(contents, encoding="utf-8")
    helper = Path(os.environ["LUMENX_REPO_ROOT"]) / "scripts/test_support/isolated_child.py"
    return subprocess.run(
        [sys.executable, "-S", str(helper), str(script), *map(str, arguments)],
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )


def test_second_process_cannot_acquire_owned_directory(tmp_path):
    directory = tmp_path / "database"
    with DirectoryWriterLock(directory):
        result = _run_probe(
            tmp_path,
            "import sys\n"
            "from src.utils.json_storage import DirectoryWriterLock, StorageConflictError\n"
            "try:\n"
            "    DirectoryWriterLock(sys.argv[1]).acquire()\n"
            "except StorageConflictError:\n"
            "    print('conflict')\n"
            "else:\n"
            "    raise AssertionError('second writer was allowed')\n",
            directory,
        )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "conflict"


def test_abnormal_process_exit_releases_writer_lock(tmp_path):
    directory = tmp_path / "database"
    result = _run_probe(
        tmp_path,
        "import os, sys\n"
        "from src.utils.json_storage import DirectoryWriterLock\n"
        "owner = DirectoryWriterLock(sys.argv[1]).acquire()\n"
        "os._exit(23)\n",
        directory,
    )
    assert result.returncode == 23, result.stderr
    with DirectoryWriterLock(directory):
        pass


def test_shared_owner_must_still_hold_its_lease(tmp_path):
    owner = DirectoryWriterLock(tmp_path)
    with pytest.raises(StorageConflictError):
        owner.assert_owned()
    owner.acquire()
    owner.assert_owned()
    owner.close()
    with pytest.raises(StorageConflictError):
        owner.assert_owned()
