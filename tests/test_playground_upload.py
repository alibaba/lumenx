"""Regression coverage for Playground media upload validation."""

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.apps.playground import api as playground_api


GIF_BYTES = b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!\xf9\x04\x01\x00\x00\x00\x00,"
MP4_BYTES = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2avc1mp41"


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(playground_api, "UPLOAD_DIR", str(tmp_path / "uploads"))
    app = FastAPI()
    app.include_router(playground_api.router, prefix="/playground")
    return TestClient(app)


def upload(client: TestClient, filename: str, content: bytes, mode: str, content_type: str):
    return client.post(
        "/playground/upload",
        data={"mode": mode},
        files={"file": (filename, content, content_type)},
    )


def test_accepts_image_for_image_mode_without_trusting_mime(client: TestClient):
    response = upload(client, "reference.gif", GIF_BYTES, "i2v", "video/mp4")

    assert response.status_code == 200
    path = Path(response.json()["path"])
    assert path.suffix == ".gif"
    assert path.read_bytes() == GIF_BYTES


def test_accepts_video_for_r2v(client: TestClient):
    response = upload(client, "reference.mp4", MP4_BYTES, "r2v", "image/png")

    assert response.status_code == 200
    assert Path(response.json()["path"]).suffix == ".mp4"


def test_rejects_extension_outside_allow_list(client: TestClient):
    response = upload(client, "payload.svg", b"<svg></svg>", "i2i", "image/svg+xml")

    assert response.status_code == 400
    assert "Unsupported file extension" in response.json()["detail"]


def test_rejects_extension_that_does_not_match_file_content(client: TestClient):
    response = upload(client, "payload.png", MP4_BYTES, "i2i", "image/png")

    assert response.status_code == 400
    assert "does not match" in response.json()["detail"]


def test_rejects_media_kind_not_supported_by_mode(client: TestClient):
    response = upload(client, "clip.mp4", MP4_BYTES, "i2v", "video/mp4")

    assert response.status_code == 400
    assert "does not accept video" in response.json()["detail"]


def test_rejects_upload_for_text_to_video_mode(client: TestClient):
    response = upload(client, "clip.mp4", MP4_BYTES, "t2v", "video/mp4")

    assert response.status_code == 400
    assert "does not accept uploaded media" in response.json()["detail"]


def test_rejects_oversized_upload_and_removes_partial_file(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.setattr(playground_api, "MAX_UPLOAD_BYTES", len(GIF_BYTES) + 1)
    response = upload(client, "large.gif", GIF_BYTES + b"overflow", "i2i", "image/gif")

    assert response.status_code == 413
    assert response.json()["detail"] == "File exceeds the 100 MiB upload limit."
    assert list((tmp_path / "uploads").glob("*")) == []
