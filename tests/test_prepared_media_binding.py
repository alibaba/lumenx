"""Frozen first-frame transport binding for provider submit experiments."""

import base64
import binascii
import hashlib

import pytest

from src.models.provider_tasks import PreparedMedia, ProviderBindingError
from src.utils.json_storage import DirectoryWriterLock
from src.utils.media_versions import MediaVersion, MediaVersionError, freeze_media

MAX_WAN_FIRST_FRAME_BYTES = 20 * 1024 * 1024


def _freeze(root, data: bytes, *, suffix: str = ".png") -> MediaVersion:
    source = root / "uploads" / f"first-frame{suffix}"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(data)
    with DirectoryWriterLock(root) as lease:
        return freeze_media(
            "uploads/first-frame" + suffix,
            output_root=root,
            writer_lock=lease,
            role="first_frame",
            ordinal=0,
            max_bytes=MAX_WAN_FIRST_FRAME_BYTES,
        )


def _payload_bytes(url: str) -> bytes:
    header, separator, encoded = url.partition(",")
    assert header == "data:image/png;base64" and separator == ","
    return base64.b64decode(encoded, validate=True)


def test_from_media_version_binds_to_frozen_bytes_not_mutable_source(tmp_path):
    original = b"fixed first frame bytes"
    version = _freeze(tmp_path, original)
    (tmp_path / "uploads" / "first-frame.png").write_bytes(b"mutable source changed")

    prepared = PreparedMedia.from_media_version(
        version, output_root=tmp_path, max_bytes=MAX_WAN_FIRST_FRAME_BYTES,
    )

    assert prepared.transport_binding == "data_uri_sha256_v1"
    assert prepared.version == version
    assert _payload_bytes(prepared.url) == original
    assert "fixed first frame bytes" not in repr(prepared)
    assert "base64" not in repr(prepared)


def test_from_verified_bytes_rejects_any_content_not_matching_version():
    data = b"known frozen bytes"
    version = MediaVersion(
        hashlib.sha256(data).hexdigest(), len(data), "first_frame", 0, "image/png",
    )

    with pytest.raises(ProviderBindingError):
        PreparedMedia.from_verified_bytes(
            version, b"known frozen bytes changed", max_bytes=MAX_WAN_FIRST_FRAME_BYTES,
        )


def test_direct_constructor_does_not_trust_data_uri_from_callers():
    data_uri = "data:image/png;base64," + base64.b64encode(b"unbound").decode("ascii")
    version = MediaVersion(
        hashlib.sha256(b"unbound").hexdigest(), 7, "first_frame", 0, "image/png",
    )

    with pytest.raises(ProviderBindingError):
        PreparedMedia(version=version, url=data_uri)


def test_from_data_uri_validates_hash_size_and_mime_before_marking_bound():
    data = b"bound by content identity"
    version = MediaVersion(
        hashlib.sha256(data).hexdigest(), len(data), "first_frame", 0, "image/png",
    )
    data_uri = "data:image/png;base64," + base64.b64encode(data).decode("ascii")

    prepared = PreparedMedia.from_data_uri(version, data_uri, max_bytes=MAX_WAN_FIRST_FRAME_BYTES)

    assert prepared.transport_binding == "data_uri_sha256_v1"
    assert prepared.url == data_uri


@pytest.mark.parametrize("data_uri", [
    "data:image/png;base64,not-base64!!",
    "data:image/jpeg;base64," + base64.b64encode(b"png bytes").decode("ascii"),
    "data:image/png," + base64.b64encode(b"png bytes").decode("ascii"),
])
def test_from_data_uri_rejects_malformed_or_mismatched_payload(data_uri):
    data = b"png bytes"
    version = MediaVersion(
        hashlib.sha256(data).hexdigest(), len(data), "first_frame", 0, "image/png",
    )

    with pytest.raises(ProviderBindingError):
        PreparedMedia.from_data_uri(version, data_uri, max_bytes=MAX_WAN_FIRST_FRAME_BYTES)


def test_from_data_uri_checks_encoded_length_before_decode(monkeypatch):
    data = b"small"
    version = MediaVersion(
        hashlib.sha256(data).hexdigest(), len(data), "first_frame", 0, "image/png",
    )
    limit = len(data)
    too_long_encoded = "A" * (4 * ((limit + 2) // 3) + 4)

    def fail_decode(*args, **kwargs):
        raise AssertionError("oversize data URI must be rejected before base64 decode")

    monkeypatch.setattr("src.models.provider_tasks.base64.b64decode", fail_decode)
    with pytest.raises(ProviderBindingError):
        PreparedMedia.from_data_uri(
            version, "data:image/png;base64," + too_long_encoded, max_bytes=limit,
        )


def test_binding_rejects_unsupported_or_unbounded_media_metadata():
    data = b"gif bytes"
    gif = MediaVersion(
        hashlib.sha256(data).hexdigest(), len(data), "first_frame", 0, "image/gif",
    )
    too_large = MediaVersion(
        hashlib.sha256(data).hexdigest(), len(data), "first_frame", 0, "image/png",
    )

    with pytest.raises(ProviderBindingError):
        PreparedMedia.from_verified_bytes(gif, data, max_bytes=MAX_WAN_FIRST_FRAME_BYTES)
    with pytest.raises(ProviderBindingError):
        PreparedMedia.from_verified_bytes(too_large, data, max_bytes=1)


def test_same_length_wrong_bytes_rejects_by_hash():
    original = b"same length bytes A"
    changed = b"same length bytes B"
    assert len(original) == len(changed)
    version = MediaVersion(
        hashlib.sha256(original).hexdigest(), len(original), "first_frame", 0, "image/png",
    )

    with pytest.raises(ProviderBindingError):
        PreparedMedia.from_verified_bytes(
            version, changed, max_bytes=MAX_WAN_FIRST_FRAME_BYTES,
        )


def test_correct_hash_with_wrong_size_metadata_rejects_by_size():
    data = b"hash matches but size metadata does not"
    version = MediaVersion(
        hashlib.sha256(data).hexdigest(), len(data) + 1, "first_frame", 0, "image/png",
    )

    with pytest.raises(ProviderBindingError):
        PreparedMedia.from_verified_bytes(
            version, data, max_bytes=MAX_WAN_FIRST_FRAME_BYTES,
        )


def test_from_media_version_rejects_tampered_fixed_blob(tmp_path):
    original = b"fixed blob before tamper"
    version = _freeze(tmp_path, original)
    stored = tmp_path / "media_versions" / "sha256" / version.sha256[:2] / version.sha256
    stored.write_bytes(b"fixed blob after  tamper")
    assert stored.stat().st_size == version.size_bytes

    with pytest.raises(MediaVersionError):
        PreparedMedia.from_media_version(
            version, output_root=tmp_path, max_bytes=MAX_WAN_FIRST_FRAME_BYTES,
        )


def test_constructor_cannot_forge_transport_binding_label():
    data = b"unverified url bytes"
    version = MediaVersion(
        hashlib.sha256(data).hexdigest(), len(data), "first_frame", 0, "image/png",
    )

    with pytest.raises(TypeError):
        PreparedMedia(
            version=version,
            url="https://media.invalid/frame.png",
            transport_binding="data_uri_sha256_v1",
        )


def test_malformed_base64_error_does_not_echo_payload():
    data = b"png bytes"
    version = MediaVersion(
        hashlib.sha256(data).hexdigest(), len(data), "first_frame", 0, "image/png",
    )
    encoded = "PRIVATE_NOT_BASE64"

    with pytest.raises(ProviderBindingError) as raised:
        PreparedMedia.from_data_uri(
            version, "data:image/png;base64," + encoded, max_bytes=MAX_WAN_FIRST_FRAME_BYTES,
        )

    assert encoded not in str(raised.value)
    assert isinstance(raised.value.__cause__, (binascii.Error, type(None)))
