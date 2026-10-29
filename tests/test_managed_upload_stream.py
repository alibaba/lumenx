from __future__ import annotations

from contextlib import contextmanager
from threading import RLock
from types import SimpleNamespace
import os

import pytest

from src.utils import media_versions as media
from src.utils.json_storage import DirectoryWriterLock, StorageError


@pytest.fixture
def owner(tmp_path):
    root = tmp_path / "runtime"
    root.mkdir()
    state = SimpleNamespace(root=root, lease=DirectoryWriterLock(root).acquire(), lock=RLock(), closed=False)

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


def test_store_upload_stream_publishes_only_under_uploads_without_overwrite(owner):
    relative, digest, size = media.store_upload_stream(
        [b"upload bytes"],
        output_ref="uploads/file.png",
        output_root=owner.root,
        writer_lock=owner.lease,
        storage_guard=owner.guard,
        max_bytes=1024,
    )

    assert relative == "uploads/file.png"
    assert size == len(b"upload bytes")
    assert (owner.root / relative).read_bytes() == b"upload bytes"
    assert len(digest) == 64
    with pytest.raises(media.MediaVersionError, match="already exists"):
        media.store_upload_stream(
            [b"new bytes"],
            output_ref="uploads/file.png",
            output_root=owner.root,
            writer_lock=owner.lease,
            storage_guard=owner.guard,
            max_bytes=1024,
        )
    assert (owner.root / relative).read_bytes() == b"upload bytes"


@pytest.mark.parametrize("output_ref", ["video/file.mp4", "uploads/nested/file.png", "uploads/../file.png", "/tmp/file.png"])
def test_store_upload_stream_rejects_non_upload_destinations(owner, output_ref):
    with pytest.raises(media.MediaVersionError):
        media.store_upload_stream(
            [b"bytes"],
            output_ref=output_ref,
            output_root=owner.root,
            writer_lock=owner.lease,
            storage_guard=owner.guard,
            max_bytes=1024,
        )


def test_existing_video_stream_entry_still_rejects_uploads(owner):
    with pytest.raises(media.MediaVersionError, match="managed video"):
        media.store_media_stream(
            [b"bytes"],
            output_ref="uploads/file.png",
            output_root=owner.root,
            writer_lock=owner.lease,
            storage_guard=owner.guard,
            max_bytes=1024,
        )


def test_store_upload_stream_uses_no_overwrite_link(owner, monkeypatch):
    target = owner.root / "uploads/file.png"
    real_link = os.link
    replace_called = {"value": False}

    def concurrent_link(src, dst, *, src_dir_fd=None, dst_dir_fd=None, follow_symlinks=True):
        target.parent.mkdir(exist_ok=True)
        target.write_bytes(b"concurrent")
        raise FileExistsError("destination appeared")

    def replace(*_args, **_kwargs):
        replace_called["value"] = True
        raise AssertionError("upload no_overwrite publish must not use replace")

    monkeypatch.setattr(os, "link", concurrent_link)
    monkeypatch.setattr(os, "replace", replace)

    with pytest.raises(media.MediaVersionError, match="already exists"):
        media.store_upload_stream(
            [b"bytes"],
            output_ref="uploads/file.png",
            output_root=owner.root,
            writer_lock=owner.lease,
            storage_guard=owner.guard,
            max_bytes=1024,
        )

    assert target.read_bytes() == b"concurrent"
    assert replace_called["value"] is False
    monkeypatch.setattr(os, "link", real_link)
