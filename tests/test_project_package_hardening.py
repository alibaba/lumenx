import io
import json
import os
import zipfile
from pathlib import Path

import pytest

from src.apps.comic_gen.models import (
    AssetUnit,
    Character,
    DubSource,
    GlobalAssetLibrary,
    ImageVariant,
    Scene,
    Script,
    StoryboardFrame,
    VideoTask,
)
from src.apps.comic_gen.project_package import ProjectPackageError, build_package, prepare_import
from src.utils.json_storage import DirectoryWriterLock, StorageConflictError


def _write(root: Path, rel: str, data: bytes = b"data") -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _project(root: Path) -> tuple[Script, GlobalAssetLibrary]:
    for rel, data in {
        "storyboard/frame.png": b"frame",
        "video/take.mp4": b"take",
        "audio/dialogue.mp3": b"audio",
        "assets/characters/hero.png": b"hero",
        "assets/scenes/room.png": b"room",
    }.items():
        _write(root, rel, data)
    frame = StoryboardFrame(
        id="frame-old",
        scene_id="scene-old",
        character_ids=["hero-old"],
        image_url="storyboard/frame.png",
        rendered_image_url="storyboard/frame.png",
        audio_url="audio/dialogue.mp3",
        selected_video_id="task-old",
        final_take_id="task-old",
        dubbed_video_task_id="task-old",
        dub_preview_source=DubSource(
            schema_version=1,
            task_id="task-old",
            video_url="video/take.mp4",
            audio_url="audio/dialogue.mp3",
            offset_ms=0,
        ),
    )
    script = Script(
        id="project-old",
        title="Source",
        original_text="plain story",
        frames=[frame],
        video_tasks=[
            VideoTask(
                id="task-old",
                project_id="project-old",
                frame_id="frame-old",
                image_url="storyboard/frame.png",
                prompt="prompt",
                status="completed",
                video_url="video/take.mp4",
            )
        ],
        created_at=1,
        updated_at=2,
    )
    assets = GlobalAssetLibrary(
        characters=[
            Character(
                id="hero-old",
                name="Hero",
                description="hero",
                image_url="assets/characters/hero.png",
                reference_sheet=AssetUnit(
                    selected_image_id="hero-image",
                    image_variants=[ImageVariant(id="hero-image", url="assets/characters/hero.png")],
                ),
            )
        ],
        scenes=[
            Scene(
                id="scene-old",
                name="Room",
                description="room",
                image_url="assets/scenes/room.png",
            )
        ],
        props=[],
    )
    return script, assets


def _artifact(root: Path) -> bytes:
    script, assets = _project(root)
    return build_package(script, resolved_assets=assets, data_dir=root, source_storage_id="storage").data


def _swap_project_dir(path: Path, outside: Path) -> None:
    saved = path.with_name(path.name + "-old")
    path.rename(saved)
    outside.mkdir(parents=True, exist_ok=True)
    path.symlink_to(outside, target_is_directory=True)


def test_import_data_root_swap_does_not_write_outside(tmp_path, monkeypatch):
    package = _artifact(tmp_path / "source")
    target = tmp_path / "target"
    outside = tmp_path / "outside-root"
    original_open = os.open
    swapped = {"done": False}

    def swapping_open(path, flags, mode=0o777, *, dir_fd=None):
        if not swapped["done"] and path == "assets" and dir_fd is not None:
            swapped["done"] = True
            _swap_project_dir(target, outside)
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", swapping_open)
    with pytest.raises((ProjectPackageError, StorageConflictError)):
        prepare_import(package, data_dir=target, now=lambda: 1.0)
    assert swapped["done"]
    assert not [path for path in outside.rglob("*") if path.is_file()]


def test_import_parent_directory_swap_does_not_write_outside(tmp_path, monkeypatch):
    package = _artifact(tmp_path / "source")
    target = tmp_path / "target"
    outside = tmp_path / "outside-parent"
    original_open = os.open
    swapped = {"done": False}

    def swapping_open(path, flags, mode=0o777, *, dir_fd=None):
        if not swapped["done"] and path == "hero.png" and dir_fd is not None:
            swapped["done"] = True
            imported_roots = list((target / "assets" / "imported").iterdir())
            assert imported_roots
            _swap_project_dir(imported_roots[0], outside)
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", swapping_open)
    with pytest.raises(ProjectPackageError):
        prepare_import(package, data_dir=target, now=lambda: 1.0)
    assert swapped["done"]
    assert not [path for path in outside.rglob("*") if path.is_file()]


def test_export_parent_directory_swap_does_not_read_outside(tmp_path, monkeypatch):
    source = tmp_path / "source"
    outside = tmp_path / "outside-export"
    script, assets = _project(source)
    _write(outside, "frame.png", b"outside")
    original_open = os.open
    swapped = {"done": False}

    def swapping_open(path, flags, mode=0o777, *, dir_fd=None):
        if not swapped["done"] and path == "frame.png" and dir_fd is not None:
            swapped["done"] = True
            storyboard = source / "storyboard"
            saved = source / "storyboard-old"
            storyboard.rename(saved)
            storyboard.symlink_to(outside, target_is_directory=True)
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", swapping_open)
    with pytest.raises(ProjectPackageError):
        build_package(script, resolved_assets=assets, data_dir=source, source_storage_id="storage")
    assert swapped["done"]


def test_legal_original_text_with_https_roundtrips(tmp_path):
    source = tmp_path / "source"
    script, assets = _project(source)
    script.original_text = "角色在对白里提到 https://example.com 只是文本，不是媒体。"
    package = build_package(script, resolved_assets=assets, data_dir=source, source_storage_id="storage").data
    imported = prepare_import(package, data_dir=tmp_path / "target", now=lambda: 1.0).script
    assert imported.original_text == script.original_text


def test_dialogue_instructions_free_text_is_preserved(tmp_path):
    source = tmp_path / "source"
    script, assets = _project(source)
    script.frames[0].dialogue_instructions = "普通房间词和hero-oldness不应替换；hero-old 也是自由文本"
    package = build_package(script, resolved_assets=assets, data_dir=source, source_storage_id="storage").data
    imported = prepare_import(package, data_dir=tmp_path / "target", now=lambda: 1.0).script
    assert imported.frames[0].dialogue_instructions == script.frames[0].dialogue_instructions


def test_composition_structured_entity_refs_are_remapped(tmp_path):
    source = tmp_path / "source"
    script, assets = _project(source)
    script.frames[0].composition_data = {
        "scene_id": "scene-old",
        "character_ids": ["hero-old"],
        "prop_ids": [],
        "reference_image_urls": ["storyboard/frame.png"],
    }
    package = build_package(script, resolved_assets=assets, data_dir=source, source_storage_id="storage").data
    prepared = prepare_import(package, data_dir=tmp_path / "target", now=lambda: 1.0)
    composition = prepared.script.frames[0].composition_data
    assert composition["scene_id"] == prepared.script.scenes[0].id
    assert composition["character_ids"] == [prepared.script.characters[0].id]
    assert composition["reference_image_urls"] == [prepared.media["storyboard/frame.png"]]


def test_unknown_frame_task_and_variant_fields_are_rejected(tmp_path):
    package = _artifact(tmp_path / "source")
    with zipfile.ZipFile(io.BytesIO(package)) as original:
        manifest = json.loads(original.read("manifest.json"))
        project = json.loads(original.read("project.json"))
        media_entries = [(info.filename, original.read(info.filename)) for info in original.infolist() if info.filename.startswith("media/")]

    mutations = [
        lambda payload: payload["script"]["frames"][0].__setitem__("future_frame", "x"),
        lambda payload: payload["script"]["video_tasks"][0].__setitem__("future_task", "x"),
        lambda payload: payload["script"]["characters"][0]["reference_sheet"].setdefault("image_variants", []).append({"id": "future", "url": "assets/characters/hero.png", "future_variant": "x"}),
    ]
    for mutate in mutations:
        mutated = json.loads(json.dumps(project))
        mutate(mutated)
        package_io = io.BytesIO()
        with zipfile.ZipFile(package_io, "w") as zf:
            zf.writestr("manifest.json", json.dumps(manifest))
            zf.writestr("project.json", json.dumps(mutated))
            for name, data in media_entries:
                zf.writestr(name, data)
        with pytest.raises(ProjectPackageError, match="Unknown project package field"):
            prepare_import(package_io.getvalue(), data_dir=tmp_path / "target")


def test_empty_optional_image_url_is_not_rewritten_or_rejected(tmp_path):
    source = tmp_path / "source"
    script, assets = _project(source)
    script.frames[0].image_url = ""
    package = build_package(script, resolved_assets=assets, data_dir=source, source_storage_id="storage").data
    imported = prepare_import(package, data_dir=tmp_path / "target", now=lambda: 1.0).script
    assert imported.frames[0].image_url == ""


def test_dynamic_composition_hidden_media_or_authority_keys_rejected(tmp_path):
    source = tmp_path / "source"
    script, assets = _project(source)
    script.frames[0].composition_data = {"nested": {"media": "video/take.mp4"}}
    with pytest.raises(ProjectPackageError):
        build_package(script, resolved_assets=assets, data_dir=source, source_storage_id="storage")

    script, assets = _project(tmp_path / "source2")
    script.frames[0].composition_data = {"nested": {"credential_ref": "abc"}}
    with pytest.raises(ProjectPackageError, match="Unsafe dynamic field"):
        build_package(script, resolved_assets=assets, data_dir=tmp_path / "source2", source_storage_id="storage")


def test_writer_lock_must_match_data_root(tmp_path):
    package = _artifact(tmp_path / "source")
    with DirectoryWriterLock(tmp_path / "other") as wrong_lock:
        with pytest.raises(ProjectPackageError, match="Writer lease belongs"):
            prepare_import(package, data_dir=tmp_path / "target", writer_lock=wrong_lock)


def test_import_does_not_retain_a_descriptor_for_every_media_file(tmp_path, monkeypatch):
    package = _artifact(tmp_path / "source")

    def no_descriptor_duplication(_fd):
        raise OSError(24, "Too many open files")

    monkeypatch.setattr(os, "dup", no_descriptor_duplication)
    prepared = prepare_import(package, data_dir=tmp_path / "target")
    assert all((tmp_path / "target" / ref).is_file() for ref in prepared.media.values())
