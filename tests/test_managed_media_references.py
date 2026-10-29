"""Keep cross-product media reference checks equivalent through shared extraction."""

from pathlib import Path

import pytest

from src.apps.atelier.export_history import payload_contains_managed_ref


@pytest.mark.parametrize("reference", [
    "video/clip.mp4", "output/video/clip.mp4", "outputs/video/clip.mp4",
    "/video/clip.mp4", "/files/videos/clip.mp4", "files/videos/clip.mp4",
    "/files/video/clip.mp4", "files/video/clip.mp4",
    "/files/outputs/videos/clip.mp4", "files/outputs/videos/clip.mp4",
    "/files/output/video/clip.mp4", "files/output/video/clip.mp4",
    "/files/outputs/video/clip.mp4", "files/outputs/video/clip.mp4",
    "http://localhost:17177/files/videos/clip.mp4?token=fixture#part",
    "https://fixture.invalid/files/videos/%63lip.mp4",
    "  video/clip.mp4  ",
])
def test_nested_payload_matches_existing_video_aliases(tmp_path, reference):
    payload = {"unrelated": "video/other.mp4", "nested": [{"takes": (None, reference)}]}
    assert payload_contains_managed_ref(payload, "video/clip.mp4", tmp_path)


@pytest.mark.parametrize("reference", [
    "assets/portrait.png", "/assets/portrait.png", "/files/assets/portrait.png",
    "files/assets/portrait.png", "/files/outputs/assets/portrait.png",
    "files/outputs/assets/portrait.png",
])
def test_asset_aliases_reuse_managed_path_identity(tmp_path, reference):
    assert payload_contains_managed_ref(reference, "assets/portrait.png", tmp_path)


@pytest.mark.parametrize("payload", [
    None, 0, False, [], {}, "", "video/other.mp4",
    "https://fixture.invalid/other/clip.mp4", "blob:video/clip.mp4",
    "data:video/clip.mp4", "lumenx/video/clip.mp4",
    {"video/clip.mp4": "video/other.mp4"},
])
def test_nonmatching_or_nonmanaged_values_do_not_retain_media(tmp_path, payload):
    assert not payload_contains_managed_ref(payload, "video/clip.mp4", tmp_path)


@pytest.mark.parametrize("target", ["", "https://fixture.invalid/clip.mp4", "video/../../outside.mp4"])
def test_unmanaged_target_never_matches_even_identical_values(tmp_path, target):
    assert not payload_contains_managed_ref({"value": target}, target, tmp_path)


def test_absolute_path_matches_only_within_original_data_root(tmp_path):
    own_root, other_root = tmp_path / "a", tmp_path / "b"
    own_path = own_root / "video" / "clip.mp4"
    other_path = other_root / "video" / "clip.mp4"
    assert payload_contains_managed_ref(str(own_path), "video/clip.mp4", own_root)
    assert not payload_contains_managed_ref(str(other_path), "video/clip.mp4", own_root)


def test_symlink_identity_inside_root_matches_but_escape_does_not(tmp_path):
    root = tmp_path / "output"
    (root / "video").mkdir(parents=True)
    (root / "video" / "clip.mp4").write_bytes(b"fixture")
    (root / "video" / "alias.mp4").symlink_to(root / "video" / "clip.mp4")
    (root / "video" / "escape.mp4").symlink_to(tmp_path / "outside.mp4")
    assert payload_contains_managed_ref("video/alias.mp4", "video/clip.mp4", root)
    assert not payload_contains_managed_ref("video/escape.mp4", "video/escape.mp4", root)


def test_reference_query_does_not_create_missing_media(tmp_path):
    root = tmp_path / "nonexistent"
    assert payload_contains_managed_ref("audio/voice.wav", "audio/voice.wav", root)
    assert not Path(root).exists()


def test_atelier_compatibility_name_uses_shared_reference_checker(tmp_path):
    from src.utils.media_refs import payload_contains_managed_ref as shared_checker

    assert payload_contains_managed_ref is shared_checker
    assert shared_checker({"source": "/files/audio/voice.wav"}, "audio/voice.wav", tmp_path)
