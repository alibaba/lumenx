"""Wan3 validates frozen image bytes before provider submission."""

from __future__ import annotations

import binascii
import base64
import hashlib
import struct
import zlib

import pytest

from src.models.provider_tasks import PreparedMedia, ProviderBindingError, ProviderScope, ProviderSubmission
from src.models.wanx import WanxModel
from src.utils.generation_intent import GenerationIntent
from src.utils.media_versions import MediaVersion
from src.utils import media_probe


WAN3_SCOPE = ProviderScope(
    provider="dashscope",
    backend="dashscope",
    gateway="dashscope_workspace",
    base_url="https://original-workspace.cn-beijing.maas.aliyuncs.com",
    region="cn-beijing",
    workspace_id="original-workspace",
)
WAN2_SCOPE = ProviderScope(
    provider="dashscope",
    backend="dashscope",
    gateway="dashscope",
    base_url="https://dashscope.aliyuncs.com",
    region="cn-beijing",
)
JPEG_240_BASE64 = (
    "/9j//gAQTGF2YzYyLjI4LjEwMgD/2wBDAAgEBAQEBAUFBQUFBQYGBgYGBgYGBgYGBgYHBwcICAgHBwcGBgcHCAgI"
    "CAkJCQgICAgJCQoKCgwMCwsODg4RERT/xABMAAEBAAAAAAAAAAAAAAAAAAAABwEBAQAAAAAAAAAAAAAAAAAAAAUQ"
    "AQAAAAAAAAAAAAAAAAAAAAARAQAAAAAAAAAAAAAAAAAAAAD/wAARCADwAPADARIAAhIAAxIA/9oADAMBAAIRAxEA"
    "PwCWisJYAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAA//2Q=="
)
WEBP_240_BASE64 = (
    "UklGRtAAAABXRUJQVlA4IMQAAACwDwCdASrwAPAAPpFIoU0lpCMiICgAsBIJaW7hdqlwAABPYB77ZOQ99snIe+2T"
    "kRA8nIe+2TnERcnIe+2Yy09snIe/AEA99snIfQvpdrxcnI/55OQ99soGXi5OQ99uZfp7ZOQ+A/S7Xi5OREDych77"
    "ZOcRFych77ZjLT2ych78AQD32ycKAAD+/4DT/8ikWMrf9T7+AfRZ8imHDMOGYcMw4Zhwnk+c1/IvlBv5F8oN/Ivl"
    "Bv5F8oN/IvlBv5F8oN+AAAAA"
)


def _chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", binascii.crc32(kind + payload) & 0xFFFFFFFF)
    )


def png_bytes(width: int, height: int, *, alpha: bool = False) -> bytes:
    color_type = 6 if alpha else 2
    pixel = b"\x12\x34\x56\x00" if alpha else b"\x12\x34\x56"
    rows = b"".join(b"\x00" + pixel * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk("IHDR".encode(), struct.pack(">IIBBBBB", width, height, 8, color_type, 0, 0, 0))
        + _chunk(b"IDAT", zlib.compress(rows, level=9))
        + _chunk(b"IEND", b"")
    )


def png16_alpha_bytes(width: int, height: int) -> bytes:
    pixel = b"\x12\x34\x56\x78\x9a\xbc\xff\xff"
    rows = b"".join(b"\x00" + pixel * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 16, 6, 0, 0, 0))
        + _chunk(b"IDAT", zlib.compress(rows, level=9))
        + _chunk(b"IEND", b"")
    )


def indexed_png_bytes(width: int, height: int, *, transparent: bool = False) -> bytes:
    rows = b"".join(b"\x00" + b"\x00" * width for _ in range(height))
    chunks = [
        _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 3, 0, 0, 0)),
        _chunk(b"PLTE", b"\x12\x34\x56"),
    ]
    if transparent:
        chunks.append(_chunk(b"tRNS", b"\x00"))
    chunks.extend([
        _chunk(b"IDAT", zlib.compress(rows, level=9)),
        _chunk(b"IEND", b""),
    ])
    return b"\x89PNG\r\n\x1a\n" + b"".join(chunks)


def corrupt_png_bytes(width: int, height: int) -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + _chunk(b"IDAT", b"x\x9c\x00")
        + _chunk(b"IEND", b"")
    )


def bmp_bytes(width: int, height: int) -> bytes:
    row_stride = ((width * 3 + 3) // 4) * 4
    row = b"\x12\x34\x56" * width + b"\x00" * (row_stride - width * 3)
    pixel_data = row * height
    file_size = 14 + 40 + len(pixel_data)
    return (
        b"BM"
        + struct.pack("<IHHI", file_size, 0, 0, 54)
        + struct.pack("<IiiHHIIiiII", 40, width, height, 1, 24, 0, len(pixel_data), 2835, 2835, 0, 0)
        + pixel_data
    )


def media(data: bytes, *, role="first_frame", mime="image/png") -> PreparedMedia:
    version = MediaVersion(hashlib.sha256(data).hexdigest(), len(data), role, 0, mime)
    return PreparedMedia.from_verified_bytes(version, data, max_bytes=20 * 1024 * 1024)


def wan3_request(item: PreparedMedia) -> ProviderSubmission:
    return ProviderSubmission(
        intent=GenerationIntent(
            model_id="wan/wan3.0-video#video",
            catalog_mode="video",
            mode="i2v",
            prompt="A still frame becomes a video",
            params={
                "duration": 5,
                "resolution": "1080P",
                "ratio": "adaptive",
                "prompt_extend": True,
                "watermark": False,
                "audio": True,
            },
            sources={},
            request_fields=(),
        ),
        provider_model_id="wan3.0-video",
        media=(item,),
        scope=WAN3_SCOPE,
        credential_ref="credential-a",
    )


def wan2_request(item: PreparedMedia) -> ProviderSubmission:
    return ProviderSubmission(
        intent=GenerationIntent(
            model_id="wan/wan2.6-i2v-flash#i2v",
            catalog_mode="i2v",
            mode="i2v",
            prompt="legacy path",
            params={"duration": 5, "resolution": "720P", "prompt_extend": False, "audio": False},
            sources={},
            request_fields=(),
        ),
        provider_model_id="wan2.6-i2v-flash",
        media=(item,),
        scope=WAN2_SCOPE,
        credential_ref="credential-a",
    )


def test_probe_frozen_image_reads_exact_png_metadata():
    data = png_bytes(240, 240)
    version = MediaVersion(hashlib.sha256(data).hexdigest(), len(data), "first_frame", 0, "image/png")

    metadata = media_probe.probe_frozen_image(version, data)

    assert metadata.width == 240
    assert metadata.height == 240
    assert metadata.has_alpha is False
    assert "png_pipe" in metadata.format_names


@pytest.mark.parametrize("item", [
    media(png_bytes(1, 240)),
    media(png_bytes(240, 1)),
    media(png_bytes(240, 2000)),
    media(png_bytes(240, 240, alpha=True)),
    media(png16_alpha_bytes(240, 240)),
    media(indexed_png_bytes(240, 240, transparent=True)),
    media(png_bytes(240, 240), mime="image/jpeg"),
    media(corrupt_png_bytes(240, 240), mime="image/png"),
    media(b"not a decodable image", mime="image/png"),
])
def test_wan3_rejects_invalid_frozen_image_bytes_before_post(item):
    with pytest.raises(ProviderBindingError):
        WanxModel.validate_submission(wan3_request(item))


def test_wan3_accepts_valid_nontransparent_png_at_minimum_size():
    assert WanxModel.validate_submission(wan3_request(media(png_bytes(240, 240)))) is None


@pytest.mark.parametrize(("data", "mime"), [
    (png_bytes(240, 240), "image/png"),
    (base64.b64decode(JPEG_240_BASE64), "image/jpeg"),
    (base64.b64decode(WEBP_240_BASE64), "image/webp"),
    (bmp_bytes(240, 240), "image/bmp"),
], ids=["png", "jpeg", "webp", "bmp"])
def test_wan3_accepts_all_documented_decodable_image_formats(data, mime):
    assert WanxModel.validate_submission(wan3_request(media(data, mime=mime))) is None


def test_wan3_accepts_opaque_indexed_png_without_trns_chunk():
    assert WanxModel.validate_submission(wan3_request(media(indexed_png_bytes(240, 240)))) is None


def test_frozen_bmp_requires_bmp_codec_identity(monkeypatch):
    data = bmp_bytes(240, 240)
    version = MediaVersion(hashlib.sha256(data).hexdigest(), len(data), "first_frame", 0, "image/bmp")

    monkeypatch.setattr(
        media_probe,
        "probe_image_file",
        lambda *_args, **_kwargs: media_probe.FrozenImageMetadata(
            kind="image", width=240, height=240, format_names=("image2",), codec_name="mjpeg",
        ),
    )

    with pytest.raises(media_probe.MediaProbeError):
        media_probe.probe_frozen_image(version, data)


def test_wan2_legacy_validation_does_not_probe_frozen_image(monkeypatch):
    monkeypatch.setattr(
        "src.models.wanx.probe_frozen_image",
        lambda *_args, **_kwargs: pytest.fail("Wan2 must not use Wan3 image probe"),
        raising=False,
    )
    fake = media(b"legacy fake image bytes", mime="image/png")
    assert WanxModel.validate_submission(wan2_request(fake)) is None
