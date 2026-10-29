"""Small real raster fixtures for frozen provider-input contract tests."""

import hashlib
import struct
import zlib


def rgb_png_bytes(label: str = "frozen image", *, width: int = 240, height: int = 240) -> bytes:
    """Return a deterministic, opaque RGB PNG without optional image libraries."""
    def chunk(name: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + name + data + struct.pack(">I", zlib.crc32(name + data))

    pixel = hashlib.sha256(label.encode("utf-8")).digest()[:3]
    rows = (b"\0" + pixel * width) * height
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )
