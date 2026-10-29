"""Scratch cleanup is scoped to its original directory and preserves failures."""
import os

import pytest

from src.utils.private_media_directory import OwnedPrivateMediaDirectory


def test_retains_only_chosen_file_and_does_not_follow_child_link(tmp_path):
    directory = OwnedPrivateMediaDirectory(prefix="lumenx-test-private-")
    assert directory.path == directory.path.resolve()
    assert directory.path.stat().st_mode & 0o777 == 0o700
    (directory.path / "verified.png").write_bytes(b"already verified by caller")
    (directory.path / "raw.tmp").write_bytes(b"unverified")
    (directory.path / "nested").mkdir()
    (directory.path / "nested" / "raw.tmp").write_bytes(b"unverified")
    external = tmp_path / "sentinel"
    external.write_text("untouched")
    (directory.path / "link").symlink_to(external)
    directory.cleanup(keep_name="verified.png")
    assert [item.name for item in directory.path.iterdir()] == ["verified.png"]
    assert external.read_text() == "untouched"
    directory.cleanup()  # Closed ownership is never reused to delete retained bytes.
    assert (directory.path / "verified.png").exists()


def test_normal_success_removes_directory():
    directory = OwnedPrivateMediaDirectory(prefix="lumenx-test-private-")
    (directory.path / "file").write_bytes(b"temporary")
    directory.cleanup()
    assert not directory.path.exists()


@pytest.mark.parametrize("replacement", ["directory", "symlink", "absent"])
def test_changed_root_leaves_both_original_and_other_directory(tmp_path, replacement):
    directory = OwnedPrivateMediaDirectory(prefix="lumenx-test-private-")
    (directory.path / "verified.png").write_bytes(b"keep")
    original = directory.path.with_name(directory.path.name + "-moved")
    directory.path.rename(original)
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    (foreign / "unrelated.txt").write_text("keep")
    if replacement == "directory":
        foreign.rename(directory.path)
        foreign = directory.path
    elif replacement == "symlink":
        directory.path.symlink_to(foreign, target_is_directory=True)
    with pytest.raises(OSError):
        directory.assert_owned()
    directory.cleanup()
    assert (original / "verified.png").read_bytes() == b"keep"
    assert (foreign / "unrelated.txt").read_text() == "keep"
    with pytest.raises(OSError, match="closed"):
        directory.assert_owned()


def test_cleanup_error_does_not_mask_original_exception(monkeypatch):
    directory = OwnedPrivateMediaDirectory(prefix="lumenx-test-private-")
    (directory.path / "raw.tmp").write_bytes(b"raw")
    unlink = os.unlink
    failures = []
    def denied(path, *args, **kwargs):
        if path == "raw.tmp" and kwargs.get("dir_fd") is not None:
            failures.append(path)
            raise PermissionError("cleanup denied")
        return unlink(path, *args, **kwargs)
    monkeypatch.setattr(os, "unlink", denied)
    with pytest.raises(RuntimeError, match="original"):
        try:
            raise RuntimeError("original")
        finally:
            directory.cleanup()
    assert failures == ["raw.tmp"]
    assert (directory.path / "raw.tmp").exists()


def test_parent_alias_is_resolved_before_return(monkeypatch, tmp_path):
    import tempfile
    parent = tmp_path / "real"
    parent.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(parent, target_is_directory=True)
    mkdtemp = tempfile.mkdtemp
    monkeypatch.setattr(tempfile, "mkdtemp", lambda **kwargs: mkdtemp(dir=alias, **kwargs))
    directory = OwnedPrivateMediaDirectory(prefix="lumenx-test-private-")
    assert directory.path.parent == parent.resolve()
    directory.cleanup()
    assert not directory.path.exists()
