"""Products publish the same verified open descriptor, never a reopened path."""
import hashlib
import os

import pytest

from src.utils import media_versions as media


def test_private_media_reader_keeps_verified_descriptor_after_path_replacement(tmp_path):
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    path = scratch / "image.png"
    original = b"verified original"
    path.write_bytes(original)
    with media.verified_private_media_file(path, scratch_root=scratch,
            expected_sha256=hashlib.sha256(original).hexdigest(), expected_size=len(original), max_bytes=128) as source:
        replacement = scratch / "replacement.png"
        replacement.write_bytes(b"replacement")
        replacement.replace(path)
        assert source.read() == original
    assert source.closed


@pytest.mark.parametrize("case", ["hash", "size", "kind", "symlink", "outside", "fifo", "oversize", "boolean_size"])
def test_private_media_reader_rejects_invalid_source_before_yield(tmp_path, case):
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    path = scratch / "image.png"
    data = b"private result"
    path.write_bytes(data)
    size = len(data)
    digest = hashlib.sha256(data).hexdigest()
    maximum = 128
    if case == "hash":
        digest = "0" * 64
    elif case == "size":
        size += 1
    elif case == "kind":
        path.unlink()
        path.mkdir()
    elif case == "symlink":
        link = scratch / "link.png"
        link.symlink_to(path)
        path = link
    elif case == "outside":
        path = tmp_path / "outside.png"
        path.write_bytes(data)
    elif case == "fifo":
        path.unlink()
        os.mkfifo(path)
    elif case == "oversize":
        maximum = 2
    elif case == "boolean_size":
        size = True
    with pytest.raises((media.MediaVersionError, OSError)):
        with media.verified_private_media_file(path, scratch_root=scratch,
                expected_sha256=digest, expected_size=size, max_bytes=maximum):
            pytest.fail("invalid private bytes escaped validation")
