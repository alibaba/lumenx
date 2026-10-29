"""Deleting export bytes requires their proof and the live storage lease."""

from contextlib import contextmanager
import hashlib
import os

import pytest

from src.utils import media_versions as media
from src.utils.json_storage import DirectoryWriterLock, StorageConflictError


@pytest.fixture
def owner(tmp_path):
    root = tmp_path / "output"
    root.mkdir()
    (root / "video").mkdir()
    with DirectoryWriterLock(root) as lease:
        yield root, lease


@contextmanager
def guard():
    yield


def remove(owner, **overrides):
    options = dict(
        output_root=owner[0], writer_lock=owner[1], storage_guard=guard,
        expected_sha256=hashlib.sha256(b"rendered bytes").hexdigest(),
        expected_size=len(b"rendered bytes"), max_bytes=1024,
    )
    options.update(overrides)
    return media.delete_verified_managed_media_file("video/atelier_seq_export.mp4", **options)


def source(owner):
    path = owner[0] / "video/atelier_seq_export.mp4"
    path.write_bytes(b"rendered bytes")
    return path


def test_delete_matches_proof_and_distinguishes_missing(owner):
    path = source(owner)
    blob = owner[0] / "media_versions/untouched"
    blob.parent.mkdir()
    blob.write_bytes(b"rendered bytes")
    assert remove(owner) == "deleted"
    assert not path.exists()
    assert blob.read_bytes() == b"rendered bytes"
    assert remove(owner) == "already_missing"


@pytest.mark.parametrize("overrides", [
    {"expected_sha256": "0" * 64}, {"expected_size": 1},
    {"expected_sha256": "invalid"}, {"expected_size": True}, {"max_bytes": 1},
])
def test_invalid_or_mismatched_proof_preserves_file(owner, overrides):
    path = source(owner)
    with pytest.raises(media.MediaVersionError):
        remove(owner, **overrides)
    assert path.read_bytes() == b"rendered bytes"


def test_missing_file_does_not_bypass_proof_validation(owner):
    with pytest.raises(media.MediaVersionError):
        remove(owner, expected_sha256="invalid")


@pytest.mark.parametrize("parent", [False, True])
def test_symlink_is_not_deleted_or_reported_missing(owner, tmp_path, parent):
    outside = tmp_path / "outside"
    outside.mkdir()
    target = outside / "atelier_seq_export.mp4"
    target.write_bytes(b"rendered bytes")
    if parent:
        (owner[0] / "video").rmdir()
        (owner[0] / "video").symlink_to(outside, target_is_directory=True)
    else:
        (owner[0] / "video/atelier_seq_export.mp4").symlink_to(target)
    with pytest.raises(media.MediaVersionError):
        remove(owner)
    assert target.read_bytes() == b"rendered bytes"


def test_closed_lease_or_guard_prevents_deletion(owner):
    path = source(owner)

    @contextmanager
    def closed_guard():
        raise StorageConflictError("runtime closed")
        yield

    with pytest.raises(StorageConflictError):
        remove(owner, storage_guard=closed_guard)
    owner[1].close()
    with pytest.raises(StorageConflictError):
        remove(owner)
    assert path.exists()


def test_path_replaced_during_verification_is_preserved(owner, monkeypatch):
    path = source(owner)
    original_chunks = media._chunks

    def replace_during_hash(fd):
        yield from original_chunks(fd)
        replacement = path.with_suffix(".replacement")
        replacement.write_bytes(b"rendered bytes")
        os.replace(replacement, path)

    monkeypatch.setattr(media, "_chunks", replace_during_hash)
    with pytest.raises(StorageConflictError):
        remove(owner)
    assert path.read_bytes() == b"rendered bytes"


def test_file_modified_during_verification_is_preserved(owner, monkeypatch):
    path = source(owner)
    original_chunks = media._chunks

    def modify_during_hash(fd):
        yield from original_chunks(fd)
        path.write_bytes(b"different data")

    monkeypatch.setattr(media, "_chunks", modify_during_hash)
    with pytest.raises(StorageConflictError):
        remove(owner)
    assert path.read_bytes() == b"different data"


def test_fsync_failure_after_unlink_is_not_reported_as_success(owner, monkeypatch):
    path = source(owner)
    calls = []

    def fail_sync(fd):
        calls.append(fd)
        raise OSError("fsync failed")

    monkeypatch.setattr(media.os, "fsync", fail_sync)
    with pytest.raises(OSError, match="fsync failed"):
        remove(owner)
    assert calls
    assert not path.exists()


def test_already_missing_retry_syncs_parent_before_reporting_fact(owner, monkeypatch):
    calls = []
    original_fsync = media.os.fsync

    def record_sync(fd):
        calls.append(os.fstat(fd).st_ino)
        original_fsync(fd)

    monkeypatch.setattr(media.os, "fsync", record_sync)
    assert remove(owner) == "already_missing"
    assert (owner[0] / "video").stat().st_ino in calls


def test_missing_directory_syncs_its_existing_parent(owner, monkeypatch):
    (owner[0] / "video").rmdir()
    calls = []
    original_fsync = media.os.fsync

    def record_sync(fd):
        calls.append(os.fstat(fd).st_ino)
        original_fsync(fd)

    monkeypatch.setattr(media.os, "fsync", record_sync)
    assert remove(owner) == "already_missing"
    assert owner[0].stat().st_ino in calls


def test_missing_file_in_replaced_parent_is_not_reported_absent(owner, monkeypatch):
    original_open = media.os.open
    swapped = False

    def swap_parent(path, flags, *args, **kwargs):
        nonlocal swapped
        if path == "atelier_seq_export.mp4" and not swapped:
            swapped = True
            (owner[0] / "video").rename(owner[0] / "retired-video")
            (owner[0] / "video").mkdir()
            source(owner)
            raise FileNotFoundError(2, "missing in old directory")
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(media.os, "open", swap_parent)
    with pytest.raises(StorageConflictError):
        remove(owner)
    assert (owner[0] / "video/atelier_seq_export.mp4").exists()


def test_delete_rejects_non_export_video_scope(owner):
    other = owner[0] / "playground/keep.mp4"
    other.parent.mkdir()
    other.write_bytes(b"rendered bytes")
    with pytest.raises(media.MediaVersionError):
        media.delete_verified_managed_media_file(
            "playground/keep.mp4", output_root=owner[0], writer_lock=owner[1],
            storage_guard=guard, expected_sha256=hashlib.sha256(other.read_bytes()).hexdigest(),
            expected_size=other.stat().st_size, max_bytes=1024,
        )
    assert other.exists()
