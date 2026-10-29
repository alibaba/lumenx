"""/files serves managed media without exposing output-root internals."""

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.apps.comic_gen.api import create_app


PNG_BYTES = b"\x89PNG\r\n\x1a\nmedia"
MP4_BYTES = b"0123456789abcdef"
MP3_BYTES = b"ID3media"


class StubPipeline:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.scripts = {}
        self.closed = False

    def create_library_asset(self, *args, **kwargs):
        return None

    def read_committed_projects(self):
        return []

    def close(self):
        self.closed = True


def write_file(root: Path, relative: str, data: bytes) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def make_client(root: Path) -> TestClient:
    pipeline = StubPipeline(root)
    app = create_app(data_dir=root, load_config=False, pipeline_instance=pipeline)
    return TestClient(app)


def test_files_serves_managed_media_and_legacy_aliases_with_range(tmp_path):
    write_file(tmp_path, "uploads/ref.png", PNG_BYTES)
    write_file(tmp_path, "video/out.mp4", MP4_BYTES)
    write_file(tmp_path, "audio/line.mp3", MP3_BYTES)
    write_file(tmp_path, "assets/hero.png", PNG_BYTES)

    with make_client(tmp_path) as client:
        assert client.get("/files/uploads/ref.png").content == PNG_BYTES
        assert client.get("/files/audio/line.mp3").content == MP3_BYTES
        assert client.get("/files/outputs/assets/hero.png").content == PNG_BYTES
        assert client.get("/files/videos/out.mp4").content == MP4_BYTES

        ranged = client.get("/files/video/out.mp4", headers={"Range": "bytes=0-3"})
        assert ranged.status_code == 206
        assert ranged.content == b"0123"
        assert ranged.headers["content-range"].startswith("bytes 0-3/")


def test_files_rejects_output_root_internal_state_and_hidden_files(tmp_path):
    for name in (
        "projects.json",
        "series.json",
        "library.json",
        "config.json",
        ".env",
        "uploads/.hidden.png",
        "projects.json.tmp",
        "projects.json.lock",
        ".projects.json.swp",
    ):
        write_file(tmp_path, name, b"secret")

    with make_client(tmp_path) as client:
        for name in (
            "projects.json",
            "series.json",
            "library.json",
            "config.json",
            ".env",
            "uploads/.hidden.png",
            "projects.json.tmp",
            "projects.json.lock",
            ".projects.json.swp",
        ):
            response = client.get(f"/files/{name}")
            assert response.status_code in {403, 404}, name
            assert response.content != b"secret"


def test_files_rejects_encoded_hidden_and_traversal_paths(tmp_path):
    write_file(tmp_path, "uploads/.hidden.png", PNG_BYTES)
    write_file(tmp_path, "projects.json", b"secret")

    with make_client(tmp_path) as client:
        encoded_hidden = client.get("/files/uploads/%2ehidden.png")
        assert encoded_hidden.status_code in {403, 404}
        assert encoded_hidden.content != PNG_BYTES

        traversal = client.get("/files/uploads/%2e%2e/projects.json")
        assert traversal.status_code in {403, 404}
        assert traversal.content != b"secret"


def test_files_rejects_symlink_escape_even_with_media_suffix(tmp_path):
    outside = tmp_path.parent / f"outside-{tmp_path.name}.png"
    outside.write_bytes(b"outside-secret")
    link = tmp_path / "uploads" / "linked.png"
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.symlink(outside, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is not supported")

    with make_client(tmp_path) as client:
        response = client.get("/files/uploads/linked.png")
        assert response.status_code in {403, 404}
        assert response.content != b"outside-secret"


def make_symlink(target: Path, link: Path) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is not supported")


def assert_not_exposed(client: TestClient, url: str, secret: bytes) -> None:
    response = client.get(url)
    assert response.status_code in {403, 404}, url
    assert response.content != secret


def test_files_rejects_same_root_json_disguised_by_media_symlink(tmp_path):
    secret = b'{"project":"secret"}'
    target = write_file(tmp_path, "projects.json", secret)
    make_symlink(target, tmp_path / "uploads" / "leak.png")

    with make_client(tmp_path) as client:
        assert_not_exposed(client, "/files/uploads/leak.png", secret)


def test_files_rejects_same_root_hidden_media_disguised_by_symlink(tmp_path):
    hidden = write_file(tmp_path, "uploads/.hidden.png", PNG_BYTES)
    make_symlink(hidden, tmp_path / "uploads" / "visible.png")

    with make_client(tmp_path) as client:
        assert_not_exposed(client, "/files/uploads/visible.png", PNG_BYTES)


def test_files_rejects_internal_state_through_legacy_alias_symlinks(tmp_path):
    secret = b'{"series":"secret"}'
    target = write_file(tmp_path, "series.json", secret)
    alias_links = {
        "/files/outputs/uploads/leak.png": tmp_path / "uploads" / "leak.png",
        "/files/videos/leak.mp4": tmp_path / "video" / "leak.mp4",
        "/files/outputs/videos/leak.mp4": tmp_path / "video" / "leak-output.mp4",
        "/files/assets/leak.png": tmp_path / "assets" / "leak.png",
        "/files/outputs/assets/leak.png": tmp_path / "assets" / "leak-output.png",
        "/files/playground/leak.png": tmp_path / "playground" / "leak.png",
    }
    for link in alias_links.values():
        make_symlink(target, link)

    with make_client(tmp_path) as client:
        for url in alias_links:
            assert_not_exposed(client, url, secret)


def test_files_allows_same_root_symlink_to_managed_media(tmp_path):
    target = write_file(tmp_path, "assets/hero.png", PNG_BYTES)
    make_symlink(target, tmp_path / "uploads" / "hero-link.png")

    with make_client(tmp_path) as client:
        assert client.get("/files/uploads/hero-link.png").content == PNG_BYTES


def test_svg_media_response_cannot_become_an_active_same_origin_document(tmp_path):
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>fetch("/config/env")</script></svg>'
    write_file(tmp_path, "uploads/drawing.svg", svg)
    with make_client(tmp_path) as client:
        response = client.get("/files/uploads/drawing.svg")
        assert response.status_code == 200
        assert response.content == svg
        assert response.headers.get("content-security-policy") == "sandbox"
        assert response.headers.get("x-content-type-options") == "nosniff"
