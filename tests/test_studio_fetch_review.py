from contextlib import contextmanager
import os
from types import SimpleNamespace
from threading import RLock

import pytest

from src.utils import media_versions as media
from src.utils.json_storage import DirectoryWriterLock, StorageError


@pytest.fixture
def owner(tmp_path):
    root = tmp_path / "runtime"
    root.mkdir()
    (root / "video").mkdir()
    state = SimpleNamespace(
        root=root, lease=DirectoryWriterLock(root).acquire(), lock=RLock(), closed=False,
    )

    @contextmanager
    def guard():
        with state.lock:
            if state.closed:
                raise StorageError("Runtime is closed")
            state.lease.assert_owned()
            yield

    state.guard = guard
    try:
        yield state
    finally:
        state.closed = True
        state.lease.close()


def test_store_media_stream_does_not_overwrite_unknown_final_created_after_prepare(owner):
    target = owner.root / "video/result.mp4"

    def on_prepared(_relative_path, _sha256, _size_bytes):
        target.write_bytes(b"unknown concurrent final")

    with pytest.raises((StorageError, media.MediaVersionError)):
        media.store_media_stream(
            [b"provider bytes"],
            output_ref="video/result.mp4",
            output_root=owner.root,
            writer_lock=owner.lease,
            storage_guard=owner.guard,
            max_bytes=1024,
            on_prepared=on_prepared,
        )

    assert target.read_bytes() == b"unknown concurrent final"


def test_store_media_stream_uses_atomic_no_overwrite_publish(owner, monkeypatch):
    target = owner.root / "video/result.mp4"
    real_link = os.link
    replace_called = {"value": False}

    def concurrent_link(src, dst, *, src_dir_fd=None, dst_dir_fd=None, follow_symlinks=True):
        target.write_bytes(b"unknown concurrent final")
        raise FileExistsError("destination appeared")

    def replace(*_args, **_kwargs):
        replace_called["value"] = True
        raise AssertionError("no_overwrite publish must not use replace")

    monkeypatch.setattr(os, "link", concurrent_link)
    monkeypatch.setattr(os, "replace", replace)

    with pytest.raises(media.MediaVersionError):
        media.store_media_stream(
            [b"provider bytes"],
            output_ref="video/result.mp4",
            output_root=owner.root,
            writer_lock=owner.lease,
            storage_guard=owner.guard,
            max_bytes=1024,
            publish_policy="no_overwrite",
        )

    assert target.read_bytes() == b"unknown concurrent final"
    assert replace_called["value"] is False

    monkeypatch.setattr(os, "link", real_link)


def test_managed_media_file_exists_does_not_treat_symlink_parent_as_missing(owner, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    video = owner.root / "video"
    video.rmdir()
    video.symlink_to(outside, target_is_directory=True)

    with pytest.raises(media.MediaVersionError):
        media.managed_media_file_exists(
            "video/result.mp4",
            output_root=owner.root,
            writer_lock=owner.lease,
            storage_guard=owner.guard,
        )
