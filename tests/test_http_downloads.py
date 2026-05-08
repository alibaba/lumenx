import pytest

from src.utils.http_downloads import (
    DownloadTooLargeError,
    download_url_to_bytes,
    download_url_to_file,
)


class FakeResponse:
    def __init__(self, content: bytes, status_code: int = 200, headers=None):
        self.content = content
        self.status_code = status_code
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size=8192):
        for index in range(0, len(self.content), chunk_size):
            yield self.content[index : index + chunk_size]


class FakeSession:
    def __init__(self, response: FakeResponse):
        self.response = response
        self.calls = []

    def get(self, url, stream=None, timeout=None, verify=None):
        self.calls.append(
            {
                "url": url,
                "stream": stream,
                "timeout": timeout,
                "verify": verify,
            }
        )
        return self.response


def test_download_url_to_file_streams_with_certificate_verification(tmp_path):
    session = FakeSession(FakeResponse(b"video-bytes"))
    output_path = tmp_path / "clip.mp4"

    download_url_to_file("https://cdn.example.com/clip.mp4", output_path, session=session)

    assert output_path.read_bytes() == b"video-bytes"
    assert session.calls == [
        {
            "url": "https://cdn.example.com/clip.mp4",
            "stream": True,
            "timeout": (30, 180),
            "verify": True,
        }
    ]
    assert not (tmp_path / "clip.mp4.tmp").exists()


def test_download_url_to_bytes_streams_and_returns_headers():
    session = FakeSession(FakeResponse(b"image-bytes", headers={"Content-Type": "image/png"}))

    content, headers = download_url_to_bytes(
        "https://cdn.example.com/reference.png",
        session=session,
    )

    assert content == b"image-bytes"
    assert headers["Content-Type"] == "image/png"
    assert session.calls[0]["stream"] is True
    assert session.calls[0]["verify"] is True


def test_download_url_to_file_enforces_max_bytes(tmp_path):
    session = FakeSession(FakeResponse(b"0123456789"))

    with pytest.raises(DownloadTooLargeError):
        download_url_to_file(
            "https://cdn.example.com/too-large.mp4",
            tmp_path / "too-large.mp4",
            session=session,
            max_bytes=5,
        )

    assert not (tmp_path / "too-large.mp4").exists()
    assert not (tmp_path / "too-large.mp4.tmp").exists()
