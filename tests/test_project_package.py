import io
import json
import os
import stat
import zipfile
from pathlib import Path

import pytest

from src.apps.comic_gen.models import (
    Character,
    DubSource,
    GenerationStatus,
    GlobalAssetLibrary,
    ImageAsset,
    ImageVariant,
    ProviderExecutionRecord,
    ProviderRequestReceipt,
    Scene,
    Script,
    StoryboardFrame,
    VideoTask,
)
from src.apps.comic_gen.project_package import (
    ProjectPackageError,
    build_package,
    prepare_import,
)


def _write(root: Path, rel: str, data: bytes = b"data") -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _provider_execution() -> ProviderExecutionRecord:
    scope = {
        "provider": "dashscope",
        "backend": "dashscope",
        "gateway": "dashscope",
        "base_url": "https://dashscope.aliyuncs.com",
        "region": "cn-beijing",
        "workspace_id": None,
        "account_identity": None,
    }
    return ProviderExecutionRecord(
        attempt_id="attempt-1",
        phase="submitted",
        provider_model_id="wan2.6-i2v",
        intent={"model": "wan2.6-i2v"},
        media=[],
        scope=scope,
        credential_ref="private-ref",
        fingerprint="a" * 64,
        handle={
            "provider_model_id": "wan2.6-i2v",
            "task_id": "remote-task",
            "request_id": "remote-request",
            "scope": scope,
            "credential_ref": "private-ref",
            "query_capability": "by_task_id",
            "schema_version": 1,
            "submitted_at": 3,
        },
    )


def _source_project(root: Path) -> Script:
    for rel, data in {
        "storyboard/frame.png": b"frame",
        "storyboard/t2i.png": b"t2i",
        "video/take.mp4": b"take",
        "video/dub.mp4": b"dub",
        "audio/dialogue.mp3": b"audio",
        "assets/characters/hero.png": b"hero",
        "assets/scenes/room.png": b"room",
    }.items():
        _write(root, rel, data)
    frame = StoryboardFrame(
        id="frame-old",
        scene_id="series-scene",
        character_ids=["series-hero"],
        action_description="act",
        image_url="storyboard/frame.png",
        rendered_image_url="storyboard/frame.png",
        t2i_image_urls=["storyboard/t2i.png"],
        selected_video_id="task-done",
        final_take_id="task-done",
        dubbed_video_task_id="task-done",
        audio_url="audio/dialogue.mp3",
        dubbed_video_url="video/dub.mp4",
        dub_preview_source=DubSource(
            schema_version=1,
            task_id="task-done",
            video_url="video/take.mp4",
            audio_url="audio/dialogue.mp3",
            offset_ms=12,
        ),
        status=GenerationStatus.COMPLETED,
    )
    script = Script(
        id="project-old",
        title="Source",
        original_text="text",
        frames=[frame],
        created_at=1,
        updated_at=2,
        series_id="series-old",
        episode_number=3,
    )
    script.video_tasks = [
        VideoTask(
            id="task-done",
            project_id="project-old",
            frame_id="frame-old",
            image_url="storyboard/frame.png",
            prompt="prompt",
            status="completed",
            video_url="video/take.mp4",
            provider_name="dashscope",
            provider_task_id="remote-task",
            provider_request_id="remote-request",
            provider_execution=_provider_execution(),
            created_at=3,
        ),
        VideoTask(
            id="task-active",
            project_id="project-old",
            frame_id="frame-old",
            image_url="storyboard/frame.png",
            prompt="active",
            status="processing",
            provider_name="dashscope",
            provider_task_id="remote-active",
            provider_request_id="remote-active-request",
            provider_execution=_provider_execution(),
            created_at=4,
        ),
    ]
    script.provider_request_receipts = [
        ProviderRequestReceipt(
            request_contract_version="request-json-v1",
            entrypoint="studio.provider_video_tasks",
            client_request_id="action-1",
            frame_id="frame-old",
            request_fingerprint="b" * 64,
            execution_fingerprint="a" * 64,
            task_id="task-done",
            attempt_id="attempt-1",
            created_at=5,
        )
    ]
    return script


def _resolved_assets() -> GlobalAssetLibrary:
    return GlobalAssetLibrary(
        characters=[
            Character(
                id="series-hero",
                name="Hero",
                description="series hero",
                image_url="assets/characters/hero.png",
                full_body_asset=ImageAsset(
                    selected_id="v1",
                    variants=[ImageVariant(id="v1", url="assets/characters/hero.png")],
                ),
            )
        ],
        scenes=[
            Scene(
                id="series-scene",
                name="Room",
                description="series room",
                image_url="assets/scenes/room.png",
                image_asset=ImageAsset(
                    selected_id="s1",
                    variants=[ImageVariant(id="s1", url="assets/scenes/room.png")],
                ),
            )
        ],
        props=[],
    )


def test_package_roundtrip_materializes_assets_and_strips_execution_authority(tmp_path):
    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    source = _source_project(source_root)
    source_dump = source.model_dump(mode="json")

    artifact = build_package(
        source,
        resolved_assets=_resolved_assets(),
        data_dir=source_root,
        source_storage_id="source-storage",
    )
    prepared = prepare_import(artifact.data, data_dir=target_root, now=lambda: 100.0)
    imported = prepared.script

    assert source.model_dump(mode="json") == source_dump
    assert imported.id != source.id
    assert imported.revision == 0
    assert imported.series_id is None and imported.episode_number is None
    assert imported.provider_request_receipts == []
    assert imported.characters and imported.characters[0].id != "series-hero"
    assert imported.scenes and imported.scenes[0].id != "series-scene"
    frame = imported.frames[0]
    assert frame.id != "frame-old"
    assert frame.scene_id == imported.scenes[0].id
    assert frame.character_ids == [imported.characters[0].id]
    done = next(task for task in imported.video_tasks if task.prompt == "prompt")
    active = next(task for task in imported.video_tasks if task.prompt == "active")
    assert done.id != "task-done" and done.project_id == imported.id and done.frame_id == frame.id
    assert done.status == "completed" and done.video_url == prepared.media["video/take.mp4"]
    assert done.provider_name is None and done.provider_task_id is None and done.provider_execution is None
    assert active.status == "needs_confirmation"
    assert active.error == "project_package_execution_not_copied"
    assert active.video_url is None and active.provider_execution is None and active.provider_task_id is None
    assert frame.selected_video_id == done.id
    assert frame.final_take_id == done.id
    assert frame.dubbed_video_task_id == done.id
    assert frame.dub_preview_source.task_id == done.id
    assert frame.dub_preview_source.video_url == prepared.media["video/take.mp4"]
    assert frame.dub_preview_source.audio_url == prepared.media["audio/dialogue.mp3"]
    assert frame.audio_url == prepared.media["audio/dialogue.mp3"]
    assert imported.characters[0].image_url == prepared.media["assets/characters/hero.png"]
    assert (target_root / prepared.media["video/take.mp4"]).read_bytes() == b"take"
    assert (target_root / prepared.media["assets/characters/hero.png"]).read_bytes() == b"hero"


def test_repeated_import_allocates_new_independent_identities(tmp_path):
    artifact = build_package(
        _source_project(tmp_path / "source"),
        resolved_assets=_resolved_assets(),
        data_dir=tmp_path / "source",
        source_storage_id="source-storage",
    )

    first = prepare_import(artifact.data, data_dir=tmp_path / "target-a", now=lambda: 1.0).script
    second = prepare_import(artifact.data, data_dir=tmp_path / "target-b", now=lambda: 2.0).script

    assert first.id != second.id
    assert {frame.id for frame in first.frames}.isdisjoint({frame.id for frame in second.frames})
    assert {task.id for task in first.video_tasks}.isdisjoint({task.id for task in second.video_tasks})


def test_export_rejects_remote_and_missing_media(tmp_path):
    source = _source_project(tmp_path / "source")
    source.frames[0].image_url = "https://example.invalid/frame.png"
    with pytest.raises(ProjectPackageError, match="Remote media cannot be packaged"):
        build_package(source, resolved_assets=_resolved_assets(), data_dir=tmp_path / "source", source_storage_id="s")

    source = _source_project(tmp_path / "missing")
    (tmp_path / "missing" / "video" / "take.mp4").unlink()
    with pytest.raises(ProjectPackageError, match="Package media path is unavailable or unsafe"):
        build_package(source, resolved_assets=_resolved_assets(), data_dir=tmp_path / "missing", source_storage_id="s")


def test_import_rejects_path_escape_duplicate_entries_and_hash_damage(tmp_path):
    artifact = build_package(
        _source_project(tmp_path / "source"),
        resolved_assets=_resolved_assets(),
        data_dir=tmp_path / "source",
        source_storage_id="source-storage",
    )

    with zipfile.ZipFile(io.BytesIO(artifact.data)) as original:
        manifest = json.loads(original.read("manifest.json"))
        project = original.read("project.json")
        media_entries = [(info.filename, original.read(info.filename)) for info in original.infolist() if info.filename.startswith("media/")]

    bad = io.BytesIO()
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        zf.writestr("project.json", project)
        zf.writestr("../evil", b"x")
    with pytest.raises(ProjectPackageError, match="Unsafe package path"):
        prepare_import(bad.getvalue(), data_dir=tmp_path / "target")

    duplicate = io.BytesIO()
    with zipfile.ZipFile(duplicate, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        zf.writestr("manifest.json", json.dumps(manifest))
        zf.writestr("project.json", project)
    with pytest.raises(ProjectPackageError, match="Duplicate package entry"):
        prepare_import(duplicate.getvalue(), data_dir=tmp_path / "target")

    damaged = io.BytesIO()
    with zipfile.ZipFile(damaged, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        zf.writestr("project.json", project)
        first = True
        for name, data in media_entries:
            zf.writestr(name, (b"X" + data[1:]) if first else data)
            first = False
    with pytest.raises(ProjectPackageError, match="hash mismatch"):
        prepare_import(damaged.getvalue(), data_dir=tmp_path / "target")


def test_rejects_source_and_package_symlinks(tmp_path):
    source_root = tmp_path / "source"
    source = _source_project(source_root)
    (source_root / "storyboard" / "frame.png").unlink()
    (source_root / "storyboard" / "frame.png").symlink_to(source_root / "video" / "take.mp4")
    with pytest.raises(ProjectPackageError, match="Package media path is unavailable or unsafe"):
        build_package(source, resolved_assets=_resolved_assets(), data_dir=source_root, source_storage_id="s")

    normal = _source_project(tmp_path / "normal")
    artifact = build_package(normal, resolved_assets=_resolved_assets(), data_dir=tmp_path / "normal", source_storage_id="s")
    with zipfile.ZipFile(io.BytesIO(artifact.data)) as original:
        manifest = json.loads(original.read("manifest.json"))
        project = original.read("project.json")

    symlink_info = zipfile.ZipInfo("media/video/take.mp4")
    symlink_info.external_attr = (stat.S_IFLNK | 0o777) << 16
    package = io.BytesIO()
    with zipfile.ZipFile(package, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        zf.writestr("project.json", project)
        zf.writestr(symlink_info, b"video/take.mp4")
    with pytest.raises(ProjectPackageError, match="Unsafe package path"):
        prepare_import(package.getvalue(), data_dir=tmp_path / "target")


def test_import_rejects_compression_bomb_and_unsupported_version(tmp_path):
    artifact = build_package(
        _source_project(tmp_path / "source"),
        resolved_assets=_resolved_assets(),
        data_dir=tmp_path / "source",
        source_storage_id="source-storage",
    )
    with zipfile.ZipFile(io.BytesIO(artifact.data)) as original:
        manifest = json.loads(original.read("manifest.json"))
        project = original.read("project.json")

    manifest["schema_version"] = 99
    unsupported = io.BytesIO()
    with zipfile.ZipFile(unsupported, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        zf.writestr("project.json", project)
    with pytest.raises(ProjectPackageError, match="Unsupported package schema"):
        prepare_import(unsupported.getvalue(), data_dir=tmp_path / "target")

    bomb = io.BytesIO()
    manifest["schema_version"] = 1
    manifest["media"] = [{"logical_ref": "video/take.mp4", "package_path": "media/video/take.mp4", "sha256": "0" * 64, "size_bytes": 1000}]
    with zipfile.ZipFile(bomb, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        zf.writestr("project.json", project)
        zf.writestr("media/video/take.mp4", b"x" * 1000)
    with pytest.raises(ProjectPackageError, match="compression ratio"):
        prepare_import(bomb.getvalue(), data_dir=tmp_path / "target", max_compression_ratio=2)


def test_export_payload_is_sanitized_before_import(tmp_path):
    source_root = tmp_path / "source"
    source = _source_project(source_root)

    artifact = build_package(
        source,
        resolved_assets=_resolved_assets(),
        data_dir=source_root,
        source_storage_id="source-storage",
    )

    assert b"private-ref" not in artifact.data
    assert b"remote-task" not in artifact.data
    assert b"provider_execution" not in artifact.data
    with zipfile.ZipFile(io.BytesIO(artifact.data)) as zf:
        project = json.loads(zf.read("project.json"))
    assert project["script"].get("provider_request_receipts") == []
    assert all("provider_execution" not in task for task in project["script"]["video_tasks"])
    assert all("provider_task_id" not in task for task in project["script"]["video_tasks"])


def test_import_rejects_manifest_missing_project_media_ref(tmp_path):
    artifact = build_package(
        _source_project(tmp_path / "source"),
        resolved_assets=_resolved_assets(),
        data_dir=tmp_path / "source",
        source_storage_id="source-storage",
    )
    with zipfile.ZipFile(io.BytesIO(artifact.data)) as original:
        manifest = json.loads(original.read("manifest.json"))
        project = original.read("project.json")
        media_entries = [(info.filename, original.read(info.filename)) for info in original.infolist() if info.filename.startswith("media/")]
    manifest["media"] = [entry for entry in manifest["media"] if entry["logical_ref"] != "video/take.mp4"]

    package = io.BytesIO()
    with zipfile.ZipFile(package, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        zf.writestr("project.json", project)
        for name, data in media_entries:
            if name != "media/video/take.mp4":
                zf.writestr(name, data)
    with pytest.raises(ProjectPackageError, match="Media manifest does not match project references"):
        prepare_import(package.getvalue(), data_dir=tmp_path / "target")


def test_duplicate_task_id_keeps_one_identity_across_project_and_asset_mirror(tmp_path):
    source_root = tmp_path / "source"
    source = _source_project(source_root)
    assets = _resolved_assets()
    mirrored = source.video_tasks[0].model_copy(deep=True)
    mirrored.asset_id = "series-hero"
    assets.characters[0].video_assets = [mirrored]

    artifact = build_package(source, resolved_assets=assets, data_dir=source_root, source_storage_id="s")
    imported = prepare_import(artifact.data, data_dir=tmp_path / "target", now=lambda: 1.0).script

    top_task = next(task for task in imported.video_tasks if task.prompt == "prompt")
    asset_task = imported.characters[0].video_assets[0]
    assert asset_task.id == top_task.id
    assert asset_task.project_id == imported.id
    assert asset_task.frame_id == imported.frames[0].id
    assert asset_task.asset_id == imported.characters[0].id


def test_rejects_missing_required_asset_references(tmp_path):
    source = _source_project(tmp_path / "source")
    assets = _resolved_assets()
    assets.scenes = []
    with pytest.raises(ProjectPackageError, match="Missing packaged scene"):
        build_package(source, resolved_assets=assets, data_dir=tmp_path / "source", source_storage_id="s")


def test_rejects_strict_json_metadata_and_unexpected_entries(tmp_path):
    artifact = build_package(
        _source_project(tmp_path / "source"),
        resolved_assets=_resolved_assets(),
        data_dir=tmp_path / "source",
        source_storage_id="source-storage",
    )
    with zipfile.ZipFile(io.BytesIO(artifact.data)) as original:
        manifest = json.loads(original.read("manifest.json"))
        project = original.read("project.json")
        original_media_entries = [
            (info.filename, original.read(info.filename))
            for info in original.infolist()
            if info.filename.startswith("media/")
        ]

    duplicate_key = io.BytesIO()
    with zipfile.ZipFile(duplicate_key, "w") as zf:
        zf.writestr("manifest.json", '{"schema_version":1,"schema_version":1,"kind":"lumenx.studio.project","source":{},"media":[]}')
        zf.writestr("project.json", project)
    with pytest.raises(ProjectPackageError, match="Duplicate JSON key"):
        prepare_import(duplicate_key.getvalue(), data_dir=tmp_path / "target")

    nan_manifest = dict(manifest)
    nan_manifest["source"] = {**manifest["source"], "project_revision": float("nan")}
    nan_package = io.BytesIO()
    with zipfile.ZipFile(nan_package, "w") as zf:
        zf.writestr("manifest.json", json.dumps(nan_manifest))
        zf.writestr("project.json", project)
    with pytest.raises(ProjectPackageError, match="not strict JSON"):
        prepare_import(nan_package.getvalue(), data_dir=tmp_path / "target")

    bool_schema = dict(manifest)
    bool_schema["schema_version"] = True
    bool_package = io.BytesIO()
    with zipfile.ZipFile(bool_package, "w") as zf:
        zf.writestr("manifest.json", json.dumps(bool_schema))
        zf.writestr("project.json", project)
    with pytest.raises(ProjectPackageError, match="Unsupported package schema"):
        prepare_import(bool_package.getvalue(), data_dir=tmp_path / "target")

    extra = io.BytesIO()
    with zipfile.ZipFile(extra, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        zf.writestr("project.json", project)
        for name, data in original_media_entries:
            zf.writestr(name, data)
        zf.writestr("media/unlisted.bin", b"x")
    with pytest.raises(ProjectPackageError, match="Unexpected package entry"):
        prepare_import(extra.getvalue(), data_dir=tmp_path / "target")


def test_rejects_secret_dynamic_fields(tmp_path):
    source = _source_project(tmp_path / "source")
    source.frames[0].composition_data = {"api_key": "secret"}
    with pytest.raises(ProjectPackageError, match="Unsafe dynamic field"):
        build_package(source, resolved_assets=_resolved_assets(), data_dir=tmp_path / "source", source_storage_id="s")


def test_roundtrip_carries_and_rewrites_merged_video_url(tmp_path):
    source_root = tmp_path / "source"
    source = _source_project(source_root)
    _write(source_root, "export/final.mp4", b"final")
    source.merged_video_url = "export/final.mp4"

    artifact = build_package(source, resolved_assets=_resolved_assets(), data_dir=source_root, source_storage_id="s")
    prepared = prepare_import(artifact.data, data_dir=tmp_path / "target", now=lambda: 1.0)

    assert prepared.script.merged_video_url == prepared.media["export/final.mp4"]
    assert (tmp_path / "target" / prepared.media["export/final.mp4"]).read_bytes() == b"final"


def test_import_rejects_unknown_future_project_fields_instead_of_dropping(tmp_path):
    artifact = build_package(
        _source_project(tmp_path / "source"),
        resolved_assets=_resolved_assets(),
        data_dir=tmp_path / "source",
        source_storage_id="s",
    )
    with zipfile.ZipFile(io.BytesIO(artifact.data)) as original:
        manifest = json.loads(original.read("manifest.json"))
        project = json.loads(original.read("project.json"))
        media_entries = [(info.filename, original.read(info.filename)) for info in original.infolist() if info.filename.startswith("media/")]
    project["script"]["future_creative_field"] = "must-not-drop"

    package = io.BytesIO()
    with zipfile.ZipFile(package, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        zf.writestr("project.json", json.dumps(project))
        for name, data in media_entries:
            zf.writestr(name, data)
    with pytest.raises(ProjectPackageError, match="Unknown project package field"):
        prepare_import(package.getvalue(), data_dir=tmp_path / "target")


def test_structured_refs_remap_while_audio_instructions_preserve_creator_text(tmp_path):
    source_root = tmp_path / "source"
    source = _source_project(source_root)
    source.frames[0].composition_data = {"prompt_tags": {"characters": ["series-hero"], "scene": "series-scene"}}
    source.frames[0].dialogue_audio_options.instructions = "character:series-hero scene:series-scene"

    artifact = build_package(source, resolved_assets=_resolved_assets(), data_dir=source_root, source_storage_id="s")
    imported = prepare_import(artifact.data, data_dir=tmp_path / "target", now=lambda: 1.0).script

    assert imported.frames[0].dialogue_audio_options.instructions == source.frames[0].dialogue_audio_options.instructions
    frame = imported.frames[0]
    assert frame.composition_data["prompt_tags"]["characters"] == [imported.characters[0].id]
    assert frame.composition_data["prompt_tags"]["scene"] == imported.scenes[0].id


def test_import_cleans_partial_media_write_on_failure(tmp_path, monkeypatch):
    artifact = build_package(
        _source_project(tmp_path / "source"),
        resolved_assets=_resolved_assets(),
        data_dir=tmp_path / "source",
        source_storage_id="s",
    )
    writes = []
    original_write = os.write

    def failing_write(fd, data):
        writes.append(fd)
        original_write(fd, data[:1])
        raise OSError("synthetic write failure")

    monkeypatch.setattr(os, "write", failing_write)
    target_root = tmp_path / "target"
    with pytest.raises(ProjectPackageError):
        prepare_import(artifact.data, data_dir=target_root, now=lambda: 1.0)
    assert len(writes) == 1
    assert not [path for path in target_root.rglob("*") if path.is_file() and path.name != ".lumenx-writer.lock"]
