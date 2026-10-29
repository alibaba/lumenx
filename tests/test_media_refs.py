from pathlib import Path

from src.utils.media_refs import (
    classify_media_ref,
    is_remote_media_ref,
    is_stable_project_media_ref,
    resolve_local_media_path,
)


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def test_classify_local_relative_path():
    assert classify_media_ref("uploads/foo.png") == "local_path"


def test_classify_local_absolute_path_under_output():
    abs_path = str(_project_root() / "output" / "uploads" / "foo.png")
    assert classify_media_ref(abs_path, project_root=str(_project_root())) == "local_path"


def test_classify_oss_object_key(monkeypatch):
    monkeypatch.setenv("OSS_BASE_PATH", "stable-test-base")
    assert (
        classify_media_ref("stable-test-base/project_1/assets/foo.png")
        == "object_key"
    )


def test_classify_remote_url():
    assert classify_media_ref("https://example.com/a.png") == "remote_url"
    assert is_remote_media_ref("http://example.com/a.png")
    assert is_remote_media_ref("blob:https://example.com/abc")
    assert not is_stable_project_media_ref("blob:https://example.com/abc")


def test_classify_data_uri_is_not_stable_storage():
    value = "data:image/png;base64,AAAA"
    assert classify_media_ref(value) == "data_uri"
    assert not is_stable_project_media_ref(value)


def test_resolve_local_relative_path_to_absolute():
    resolved = resolve_local_media_path("uploads/foo.png", project_root=str(_project_root()))
    expected = str((_project_root() / "output" / "uploads" / "foo.png").resolve())
    assert resolved == expected


def test_resolve_local_absolute_path_under_output():
    input_path = str(_project_root() / "output" / "video" / "clip.mp4")
    expected = str((_project_root() / "output" / "video" / "clip.mp4").resolve())
    assert resolve_local_media_path(input_path, project_root=str(_project_root())) == expected


def test_resolve_uses_explicit_output_root_when_project_root_omitted(tmp_path):
    output_root = tmp_path / "isolated-output"

    assert resolve_local_media_path("uploads/foo.png", output_root=str(output_root)) == str(
        (output_root / "uploads" / "foo.png").resolve()
    )
