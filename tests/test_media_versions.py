"""Fixed media versions use only synthetic files beneath the isolated root."""

import base64
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, replace
import hashlib
import os
import stat

import pytest

from src.utils import media_versions as module
from src.utils.json_storage import DirectoryWriterLock, StorageError
from src.utils.media_versions import MediaVersionError, freeze_media, read_verified_media


@pytest.fixture
def owner(tmp_path):
    root = tmp_path / "runtime"
    root.mkdir()
    (root / "uploads").mkdir()
    with DirectoryWriterLock(root) as lease:
        yield root, lease


def source(owner, contents=b"fixed image bytes", name="ref.png"):
    path = owner[0] / "uploads" / name
    path.write_bytes(contents)
    return path


def freeze(ref, owner, **kwargs):
    return freeze_media(
        ref, output_root=owner[0], writer_lock=owner[1],
        role=kwargs.pop("role", "reference_image"), ordinal=kwargs.pop("ordinal", 0),
        max_bytes=kwargs.pop("max_bytes", 1024), **kwargs,
    )


def read(version, owner, **kwargs):
    return read_verified_media(version, output_root=owner[0], max_bytes=kwargs.get("max_bytes", 1024))


def blob(owner, version):
    return owner[0] / "media_versions" / "sha256" / version.sha256[:2] / version.sha256


def test_managed_source_is_copied_and_detached_from_mutable_original(owner):
    original = source(owner)
    version = freeze("uploads/ref.png", owner, ordinal=2)
    assert version.sha256 == hashlib.sha256(b"fixed image bytes").hexdigest()
    assert version.size_bytes == len(b"fixed image bytes")
    assert version.role == "reference_image"
    assert version.ordinal == 2
    assert version.declared_media_type == "image/png"
    assert blob(owner, version).stat().st_ino != original.stat().st_ino
    original.write_bytes(b"different image")
    assert read(version, owner) == b"fixed image bytes"
    original.unlink()
    assert read(version, owner) == b"fixed image bytes"
    assert not list((owner[0] / "media_versions").glob(".stage-*"))
    with pytest.raises(FrozenInstanceError):
        version.role = "last_frame"


@pytest.mark.parametrize("ref", ["output/uploads/ref.png", "outputs/uploads/ref.png", "absolute"])
def test_existing_managed_path_forms_remain_explicitly_rooted(owner, ref):
    path = source(owner)
    assert read(freeze(str(path) if ref == "absolute" else ref, owner), owner) == path.read_bytes()


def test_same_content_reuses_blob_but_preserves_role_and_ordinal(owner):
    source(owner)
    first = freeze("uploads/ref.png", owner, role="first_frame")
    reference = freeze("uploads/ref.png", owner, ordinal=3)
    assert first.sha256 == reference.sha256
    assert first.role == "first_frame"
    assert reference.role == "reference_image" and reference.ordinal == 3
    assert list(blob(owner, first).parent.iterdir()) == [blob(owner, first)]


def test_image_data_uri_freezes_decoded_content(owner):
    data = b"inline image"
    uri = "data:image/png;base64," + base64.b64encode(data).decode("ascii")
    version = freeze(uri, owner)
    assert version.sha256 == hashlib.sha256(data).hexdigest()
    assert read(version, owner) == data


@pytest.mark.parametrize("ref", [
    "https://example.invalid/expiring.png?token=secret", "http://example.invalid/a.png",
    "oss://bucket/a.png", "lumenx/uploads/a.png", "blob:https://example.invalid/blob",
    "data:image/png;base64,%%%", "data:image/png,raw", "data:text/plain;base64,YQ==",
    "data:image/png;base64,", "data:image/png;base64,Y Q==",
])
def test_unsupported_or_malformed_references_fail_without_publishing(owner, ref):
    with pytest.raises(MediaVersionError):
        freeze(ref, owner)
    assert not (owner[0] / "media_versions").exists()


@pytest.mark.parametrize("limit", [0, -1, True, 1.5, None])
def test_explicit_positive_integer_limit_is_required(owner, limit):
    source(owner)
    with pytest.raises(MediaVersionError):
        freeze("uploads/ref.png", owner, max_bytes=limit)


def test_file_and_data_uri_size_limits_are_enforced(owner):
    source(owner, b"four")
    with pytest.raises(MediaVersionError):
        freeze("uploads/ref.png", owner, max_bytes=3)
    with pytest.raises(MediaVersionError):
        freeze("data:image/png;base64,Zm91cg==", owner, max_bytes=3)
    version = freeze("uploads/ref.png", owner, max_bytes=4)
    assert read(version, owner, max_bytes=4) == b"four"
    with pytest.raises(MediaVersionError):
        read(version, owner, max_bytes=3)


@pytest.mark.parametrize("ref", ["uploads/../projects.json", "../outside.png", "projects.json", "uploads/./ref.png"])
def test_traversal_and_internal_metadata_are_not_media(owner, ref):
    source(owner)
    (owner[0] / "projects.json").write_text("private metadata")
    with pytest.raises(MediaVersionError):
        freeze(ref, owner)


def test_absolute_file_outside_selected_root_is_rejected(owner, tmp_path):
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"outside")
    with pytest.raises(MediaVersionError):
        freeze(str(outside), owner)


@pytest.mark.parametrize("location", ["file", "parent"])
def test_source_symlinks_are_rejected_even_when_target_is_inside_root(owner, location):
    source(owner)
    if location == "file":
        (owner[0] / "uploads" / "link.png").symlink_to("ref.png")
        ref = "uploads/link.png"
    else:
        (owner[0] / "assets").symlink_to("uploads", target_is_directory=True)
        ref = "assets/ref.png"
    with pytest.raises(MediaVersionError):
        freeze(ref, owner)


def test_directories_and_fifo_are_rejected_without_blocking(owner):
    (owner[0] / "uploads" / "directory.png").mkdir()
    os.mkfifo(owner[0] / "uploads" / "fifo.png")
    for name in ("directory.png", "fifo.png"):
        with pytest.raises(MediaVersionError):
            freeze(f"uploads/{name}", owner)


@pytest.mark.parametrize("level", ["store", "algorithm", "shard", "blob"])
def test_version_tree_symlinks_are_never_followed_or_overwritten(owner, tmp_path, level):
    path = source(owner)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    parts = ["media_versions", "sha256", digest[:2], digest]
    index = ["store", "algorithm", "shard", "blob"].index(level)
    target = owner[0].joinpath(*parts[:index + 1])
    target.parent.mkdir(parents=True, exist_ok=True)
    outside = tmp_path / "outside"
    if level == "blob":
        outside.write_bytes(b"outside")
    else:
        outside.mkdir()
    target.symlink_to(outside, target_is_directory=level != "blob")
    with pytest.raises((MediaVersionError, StorageError)):
        freeze("uploads/ref.png", owner)
    assert target.is_symlink()
    if level != "blob":
        assert list(outside.iterdir()) == []
    else:
        assert outside.read_bytes() == b"outside"


def test_corrupt_or_missing_fixed_blob_never_falls_back_to_source(owner):
    source(owner)
    version = freeze("uploads/ref.png", owner)
    blob(owner, version).write_bytes(b"x" * version.size_bytes)
    with pytest.raises(MediaVersionError):
        read(version, owner)
    with pytest.raises(MediaVersionError):
        freeze("uploads/ref.png", owner)
    assert blob(owner, version).read_bytes() == b"x" * version.size_bytes
    blob(owner, version).unlink()
    with pytest.raises(MediaVersionError):
        read(version, owner)


def test_read_returns_verified_immutable_bytes_not_a_path_to_reopen(owner):
    source(owner)
    version = freeze("uploads/ref.png", owner)
    result = read(version, owner)
    blob(owner, version).write_bytes(b"tampered afterwards")
    assert isinstance(result, bytes)
    assert result == b"fixed image bytes"


def test_lease_must_own_this_root_and_remain_open(owner, tmp_path):
    source(owner)
    other = tmp_path / "other"
    with DirectoryWriterLock(other) as other_lease:
        with pytest.raises((MediaVersionError, StorageError)):
            freeze_media("uploads/ref.png", output_root=owner[0], writer_lock=other_lease,
                         role="reference_image", ordinal=0, max_bytes=1024)
    owner[1].close()
    with pytest.raises(StorageError):
        freeze("uploads/ref.png", owner)
    assert not (owner[0] / "media_versions").exists()


def test_replaced_root_cannot_reuse_a_lease_for_the_old_directory(owner):
    root, lease = owner
    root.rename(root.with_name("old-runtime"))
    root.mkdir()
    (root / ".lumenx-writer.lock").write_bytes(b"")
    (root / "uploads").mkdir()
    source(owner)
    with pytest.raises((MediaVersionError, StorageError)):
        freeze("uploads/ref.png", owner)


def test_lease_is_checked_after_copy_and_publication(owner, monkeypatch):
    source(owner)
    actual_link = module.os.link
    def close_after_link(*args, **kwargs):
        result = actual_link(*args, **kwargs)
        owner[1].close()
        return result
    monkeypatch.setattr(module.os, "link", close_after_link)
    with pytest.raises(StorageError):
        freeze("uploads/ref.png", owner)


def test_source_change_while_copying_is_rejected(owner, monkeypatch):
    original = source(owner)
    actual_read = module.os.read
    changed = False
    def change_after_read(fd, size):
        nonlocal changed
        data = actual_read(fd, size)
        if data and not changed:
            changed = True
            original.write_bytes(b"changed source")
        return data
    monkeypatch.setattr(module.os, "read", change_after_read)
    with pytest.raises(MediaVersionError):
        freeze("uploads/ref.png", owner)
    assert not list((owner[0] / "media_versions").rglob("[0-9a-f]" * 64))


@pytest.mark.parametrize("replacement", ["contents", "symlink"])
def test_temporary_replacement_before_publication_cannot_return_a_version(owner, monkeypatch, replacement):
    source(owner)
    actual_link = module.os.link
    def swap_before_link(temporary, target, **kwargs):
        stage = owner[0] / "media_versions" / temporary
        if replacement == "contents":
            stage.write_bytes(b"x" * len(b"fixed image bytes"))
        else:
            stage.unlink()
            stage.symlink_to(owner[0] / "uploads" / "ref.png")
        return actual_link(temporary, target, **kwargs)
    monkeypatch.setattr(module.os, "link", swap_before_link)
    with pytest.raises(MediaVersionError):
        freeze("uploads/ref.png", owner)


def test_parent_directory_swap_does_not_redirect_open_file_to_other_content(owner, tmp_path, monkeypatch):
    source(owner)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "ref.png").write_bytes(b"outside")
    actual_open = module.os.open
    swapped = False
    def swap_parent(path, flags, *args, **kwargs):
        nonlocal swapped
        if path == "ref.png" and not swapped:
            swapped = True
            (owner[0] / "uploads").rename(owner[0] / "original-uploads")
            (owner[0] / "uploads").symlink_to(outside, target_is_directory=True)
        return actual_open(path, flags, *args, **kwargs)
    monkeypatch.setattr(module.os, "open", swap_parent)
    version = freeze("uploads/ref.png", owner)
    assert read(version, owner) == b"fixed image bytes"


@pytest.mark.parametrize("fault", [
    "write", "file_sync", "publish", "published_file_sync", "directory_sync_after_publish",
])
def test_publication_failures_are_explicit_and_never_return_success(owner, monkeypatch, fault):
    path = source(owner)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    actual_fsync = module.os.fsync
    actual_link = module.os.link
    published = False
    def failing_link(*args, **kwargs):
        nonlocal published
        if fault == "publish":
            raise OSError("cannot publish")
        result = actual_link(*args, **kwargs)
        published = True
        return result
    def failing_sync(fd):
        is_directory = stat.S_ISDIR(os.fstat(fd).st_mode)
        if (fault == "file_sync" and not is_directory) or (
            fault == "directory_sync_after_publish" and published and is_directory
        ) or (
            fault == "published_file_sync" and published and not is_directory
        ):
            raise OSError("cannot sync")
        return actual_fsync(fd)
    with monkeypatch.context() as patch:
        patch.setattr(module.os, "link", failing_link)
        patch.setattr(module.os, "fsync", failing_sync)
        if fault == "write":
            patch.setattr(module.os, "write", lambda *args: (_ for _ in ()).throw(OSError("disk full")))
        with pytest.raises(StorageError) as error:
            freeze("uploads/ref.png", owner)
    after_publication = fault in {"published_file_sync", "directory_sync_after_publish"}
    assert error.value.commit_uncertain is after_publication
    final = owner[0] / "media_versions" / "sha256" / digest[:2] / digest
    assert final.exists() is after_publication
    assert path.read_bytes() == b"fixed image bytes"


def test_new_directory_sync_failure_is_not_swallowed(owner, monkeypatch):
    source(owner)
    def fail(fd):
        raise OSError("cannot sync new directory")
    monkeypatch.setattr(module.os, "fsync", fail)
    with pytest.raises(StorageError) as error:
        freeze("uploads/ref.png", owner)
    assert not error.value.commit_uncertain


def test_concurrent_identical_ingestion_reuses_one_complete_blob(owner):
    source(owner)
    with ThreadPoolExecutor(max_workers=4) as pool:
        versions = list(pool.map(lambda i: freeze("uploads/ref.png", owner, ordinal=i), range(4)))
    assert len({version.sha256 for version in versions}) == 1
    assert [version.ordinal for version in versions] == [0, 1, 2, 3]
    assert read(versions[0], owner) == b"fixed image bytes"


def test_windows_is_explicitly_unsupported_before_touching_media(owner, monkeypatch):
    monkeypatch.setattr(module, "_PLATFORM", "win32")
    with pytest.raises(MediaVersionError, match="macOS|Linux"):
        freeze("uploads/ref.png", owner)
    assert not (owner[0] / "media_versions").exists()


@pytest.mark.parametrize("changes", [
    {"sha256": "../escape"}, {"size_bytes": -1}, {"size_bytes": True},
    {"role": "unknown"}, {"ordinal": -1}, {"ordinal": True}, {"schema_version": 2},
])
def test_untrusted_version_metadata_cannot_select_another_file(owner, changes):
    source(owner)
    version = freeze("uploads/ref.png", owner)
    with pytest.raises(MediaVersionError):
        read(replace(version, **changes), owner)
