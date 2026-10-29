from __future__ import annotations

from contextlib import contextmanager
import hashlib
from threading import RLock
from types import SimpleNamespace

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


def _publish(owner, output_ref: str, content: bytes, *, media_kind: media.ManagedMediaKind):
    return media.store_media_stream(
        [content],
        output_ref=output_ref,
        output_root=owner.root,
        writer_lock=owner.lease,
        storage_guard=owner.guard,
        max_bytes=1024,
        media_kind=media_kind,
    )


@pytest.mark.parametrize(
    ("media_kind", "output_ref", "content"),
    [
        ("image", "assets/atelier/candidate-attempt.png", b"image bytes"),
        ("image", "playground/images/candidate-attempt.png", b"playground image bytes"),
        ("audio", "audio/atelier/candidate-attempt.mp3", b"audio bytes"),
    ],
)
def test_store_exists_and_verify_support_explicit_atelier_media_kinds(owner, media_kind, output_ref, content):
    relative, digest, size = _publish(owner, output_ref, content, media_kind=media_kind)

    assert relative == output_ref
    assert digest == hashlib.sha256(content).hexdigest()
    assert size == len(content)
    assert (owner.root / output_ref).read_bytes() == content
    assert media.managed_media_file_exists(
        output_ref,
        output_root=owner.root,
        writer_lock=owner.lease,
        storage_guard=owner.guard,
        media_kind=media_kind,
    ) is True
    assert media.verify_managed_media_file(
        output_ref,
        output_root=owner.root,
        writer_lock=owner.lease,
        storage_guard=owner.guard,
        expected_sha256=digest,
        expected_size=size,
        max_bytes=1024,
        media_kind=media_kind,
    ) == (output_ref, digest, size)


@pytest.mark.parametrize(
    ("media_kind", "output_ref"),
    [
        ("image", "audio/atelier/wrong.mp3"),
        ("audio", "assets/atelier/wrong.png"),
        ("image", "assets/atelier/metadata.json"),
        ("audio", "audio/atelier/metadata.json"),
        ("image", "assets/atelier/file.jpg"),
        ("audio", "audio/atelier/file.wav"),
        ("image", "assets/atelier/nested/file.png"),
        ("audio", "audio/atelier/../file.mp3"),
        ("image", "/tmp/file.png"),
        ("image", "playground/file.png"),
        ("image", "playground/images/nested/file.png"),
        ("image", "playground/images/../file.png"),
        ("image", "playground/images/.hidden.png"),
        ("image", "playground/images/file.jpg"),
        ("image", "uploads/file.png"),
    ],
)
def test_explicit_atelier_media_kind_rejects_cross_kind_metadata_and_unsafe_paths(owner, media_kind, output_ref):
    with pytest.raises(media.MediaVersionError):
        _publish(owner, output_ref, b"bytes", media_kind=media_kind)


@pytest.mark.parametrize(
    ("media_kind", "output_ref"),
    [
        ("image", "assets/atelier/file.png"),
        ("image", "playground/images/file.png"),
        ("audio", "audio/atelier/file.mp3"),
    ],
)
def test_on_prepared_runs_before_final_publish_for_explicit_media_kind(owner, media_kind, output_ref):
    seen = []

    def on_prepared(relative, digest, size):
        seen.append((relative, digest, size, (owner.root / output_ref).exists()))

    relative, digest, size = media.store_media_stream(
        [b"prepared bytes"],
        output_ref=output_ref,
        output_root=owner.root,
        writer_lock=owner.lease,
        storage_guard=owner.guard,
        max_bytes=1024,
        on_prepared=on_prepared,
        media_kind=media_kind,
    )

    assert seen == [(output_ref, digest, size, False)]
    assert relative == output_ref
    assert (owner.root / output_ref).read_bytes() == b"prepared bytes"


@pytest.mark.parametrize(
    ("media_kind", "output_ref", "content"),
    [
        ("image", "assets/atelier/no-overwrite.png", b"original image"),
        ("image", "playground/images/no-overwrite.png", b"original playground image"),
        ("audio", "audio/atelier/no-overwrite.mp3", b"original audio"),
    ],
)
def test_explicit_atelier_media_kind_preserves_no_overwrite_and_proof_checks(owner, media_kind, output_ref, content):
    relative, digest, size = _publish(owner, output_ref, content, media_kind=media_kind)

    with pytest.raises(media.MediaVersionError, match="already exists"):
        _publish(owner, output_ref, b"different bytes", media_kind=media_kind)
    assert (owner.root / output_ref).read_bytes() == content

    with pytest.raises(media.MediaVersionError, match="content differs"):
        media.verify_managed_media_file(
            relative,
            output_root=owner.root,
            writer_lock=owner.lease,
            storage_guard=owner.guard,
            expected_sha256=hashlib.sha256(b"wrong").hexdigest(),
            expected_size=size,
            max_bytes=1024,
            media_kind=media_kind,
        )
    with pytest.raises(media.MediaVersionError, match="size differs"):
        media.verify_managed_media_file(
            relative,
            output_root=owner.root,
            writer_lock=owner.lease,
            storage_guard=owner.guard,
            expected_sha256=digest,
            expected_size=size + 1,
            max_bytes=1024,
            media_kind=media_kind,
        )


def test_explicit_atelier_media_kind_rejects_symlink_destination(owner):
    target = owner.root / "assets" / "atelier"
    target.mkdir(parents=True)
    (target / "file.png").symlink_to(owner.root / "outside.png")

    with pytest.raises(media.MediaVersionError, match="regular file"):
        media.managed_media_file_exists(
            "assets/atelier/file.png",
            output_root=owner.root,
            writer_lock=owner.lease,
            storage_guard=owner.guard,
            media_kind="image",
        )


def test_default_store_and_delete_stay_video_only(owner):
    relative, digest, size = media.store_media_stream(
        [b"video bytes"],
        output_ref="video/result.mp4",
        output_root=owner.root,
        writer_lock=owner.lease,
        storage_guard=owner.guard,
        max_bytes=1024,
    )

    assert relative == "video/result.mp4"
    assert media.verify_managed_media_file(
        relative,
        output_root=owner.root,
        writer_lock=owner.lease,
        storage_guard=owner.guard,
        expected_sha256=digest,
        expected_size=size,
        max_bytes=1024,
    ) == (relative, digest, size)
    assert media.delete_verified_managed_media_file(
        relative,
        output_root=owner.root,
        writer_lock=owner.lease,
        storage_guard=owner.guard,
        expected_sha256=digest,
        expected_size=size,
        max_bytes=1024,
    ) == "deleted"

    image_relative, image_digest, image_size = media.store_media_stream(
        [b"image bytes"],
        output_ref="assets/atelier/not-deletable.png",
        output_root=owner.root,
        writer_lock=owner.lease,
        storage_guard=owner.guard,
        max_bytes=1024,
        media_kind="image",
    )
    with pytest.raises(media.MediaVersionError, match="Deletion requires"):
        media.delete_verified_managed_media_file(
            image_relative,
            output_root=owner.root,
            writer_lock=owner.lease,
            storage_guard=owner.guard,
            expected_sha256=image_digest,
            expected_size=image_size,
            max_bytes=1024,
        )


def test_writer_lifecycle_guard_applies_to_explicit_media_kind(owner):
    owner.closed = True
    with pytest.raises(StorageError, match="Runtime is closed"):
        _publish(owner, "assets/atelier/file.png", b"bytes", media_kind="image")
