from pathlib import Path

from src.utils.media_refs import (
    classify_media_ref,
    is_remote_media_ref,
    is_stable_project_media_ref,
    output_media_ref,
    resolve_local_media_path,
)


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def test_classify_local_relative_path():
    assert classify_media_ref("uploads/foo.png") == "local_path"


def test_classify_local_relative_path_with_backslashes():
    assert classify_media_ref(r"output\uploads\foo.png") == "local_path"


def test_classify_local_absolute_path_under_output():
    abs_path = str(_project_root() / "output" / "uploads" / "foo.png")
    assert classify_media_ref(abs_path) == "local_path"


def test_classify_oss_object_key(monkeypatch):
    monkeypatch.setenv("OSS_BASE_PATH", "stable-test-base")
    assert (
        classify_media_ref("stable-test-base/project_1/assets/foo.png")
        == "object_key"
    )


def test_classify_object_key_from_generic_storage_base_path(monkeypatch):
    monkeypatch.delenv("OSS_BASE_PATH", raising=False)
    monkeypatch.setenv("OBJECT_STORAGE_BASE_PATH", "seedance-inputs")
    assert (
        classify_media_ref("seedance-inputs/project_1/assets/foo.png")
        == "object_key"
    )


def test_classify_object_key_with_backslashes(monkeypatch):
    monkeypatch.delenv("OSS_BASE_PATH", raising=False)
    monkeypatch.setenv("OBJECT_STORAGE_BASE_PATH", "seedance-inputs")
    assert (
        classify_media_ref(r"seedance-inputs\project_1\assets\foo.png")
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
    resolved = resolve_local_media_path("uploads/foo.png")
    expected = str((_project_root() / "output" / "uploads" / "foo.png").resolve())
    assert resolved == expected


def test_resolve_local_relative_path_with_backslashes_to_absolute():
    resolved = resolve_local_media_path(r"output\uploads\foo.png")
    expected = str((_project_root() / "output" / "uploads" / "foo.png").resolve())
    assert resolved == expected


def test_resolve_local_absolute_path_under_output():
    input_path = str(_project_root() / "output" / "video" / "clip.mp4")
    expected = str((_project_root() / "output" / "video" / "clip.mp4").resolve())
    assert resolve_local_media_path(input_path) == expected


def test_output_media_ref_normalizes_windows_separators():
    rel_path = output_media_ref(r"output\video\clip.mp4")
    assert rel_path == "video/clip.mp4"
