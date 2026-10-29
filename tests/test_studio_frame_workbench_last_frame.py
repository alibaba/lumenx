import io
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.apps.comic_gen import api
from src.apps.comic_gen.models import Script, StoryboardFrame
from src.apps.comic_gen.pipeline import ComicGenPipeline
from src.apps.comic_gen.project_package import build_package, prepare_import
from src.utils.json_storage import StorageError
from tests.test_project_package import _resolved_assets


def _runtime(tmp_path: Path) -> ComicGenPipeline:
    return ComicGenPipeline(data_dir=tmp_path / "data", recover_orphan_tasks=False, warmup_demucs=False)


def _write_media(runtime: ComicGenPipeline, rel: str, data: bytes = b"image-bytes") -> str:
    path = runtime.data_dir / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return rel


def _seed_project(runtime: ComicGenPipeline, *, last_frame_url: str | None = None) -> Script:
    frame = StoryboardFrame(
        id="frame-1",
        scene_id="scene-1",
        action_description="original action",
        workbench_tab_mode="t2i_i2v",
        t2i_image_urls=["storyboard/first.png"],
        t2i_selected_index=0,
        workbench_generate_count=2,
        last_frame_url=last_frame_url,
    )
    script = Script(
        id="project-1",
        title="Project",
        original_text="text",
        frames=[frame],
        created_at=1,
        updated_at=2,
    )
    runtime.scripts[script.id] = script
    runtime._commit_storage("projects.json")
    return runtime.read_committed_script(script.id)


def _app(runtime: ComicGenPipeline):
    return api.create_app(data_dir=runtime.data_dir, load_config=False, pipeline_instance=runtime, warmup_demucs=False)


def test_http_workbench_persists_preserves_and_clears_last_frame_url(tmp_path):
    runtime = _runtime(tmp_path)
    closing_ref = _write_media(runtime, "storyboard/closing.png")
    _seed_project(runtime)

    app = _app(runtime)
    with TestClient(app) as client:
        set_response = client.patch(
            "/projects/project-1/frames/frame-1/workbench",
            json={"last_frame_url": closing_ref, "workbench_tab_mode": "direct_r2v"},
        )
        assert set_response.status_code == 200, set_response.text
        assert set_response.json()["last_frame_url"] == closing_ref
        assert set_response.json()["workbench_tab_mode"] == "direct_r2v"

        read_response = client.get("/projects/project-1")
        assert read_response.status_code == 200, read_response.text
        assert read_response.json()["frames"][0]["last_frame_url"] == closing_ref

        preserve_response = client.patch(
            "/projects/project-1/frames/frame-1/workbench",
            json={"workbench_generate_count": 4},
        )
        assert preserve_response.status_code == 200, preserve_response.text
        assert preserve_response.json()["last_frame_url"] == closing_ref
        assert preserve_response.json()["workbench_generate_count"] == 4

        clear_response = client.patch(
            "/projects/project-1/frames/frame-1/workbench",
            json={"last_frame_url": "   "},
        )
        assert clear_response.status_code == 200, clear_response.text
        assert clear_response.json()["last_frame_url"] is None

        set_again = client.patch(
            "/projects/project-1/frames/frame-1/workbench",
            json={"last_frame_url": closing_ref},
        )
        assert set_again.status_code == 200, set_again.text
        null_clear = client.patch(
            "/projects/project-1/frames/frame-1/workbench",
            json={"last_frame_url": None},
        )
        assert null_clear.status_code == 200, null_clear.text
        assert null_clear.json()["last_frame_url"] is None


def test_direct_workbench_last_frame_round_trips_to_disk(tmp_path):
    runtime = _runtime(tmp_path)
    closing_ref = _write_media(runtime, "uploads/closing.webp")
    _seed_project(runtime)

    updated = runtime.update_frame_workbench("project-1", "frame-1", last_frame_url=closing_ref)
    assert updated is not None
    assert updated.last_frame_url == closing_ref

    data_dir = runtime.data_dir
    runtime.close()
    reopened = ComicGenPipeline(data_dir=data_dir, recover_orphan_tasks=False, warmup_demucs=False)
    try:
        committed = reopened.read_committed_script("project-1")
        assert committed.frames[0].last_frame_url == closing_ref
    finally:
        reopened.close()


def test_invalid_last_frame_url_rejects_before_any_workbench_mutation(tmp_path):
    runtime = _runtime(tmp_path)
    existing_ref = _write_media(runtime, "storyboard/existing.jpg")
    _seed_project(runtime, last_frame_url=existing_ref)
    before = runtime.read_committed_script("project-1").model_dump(mode="json")

    invalid_values = [
        "https://example.invalid/closing.png",
        "blob:https://example.invalid/closing",
        "data:image/png;base64,AAAA",
        "../outside.png",
        "storyboard/missing.png",
        "storyboard/not-image.txt",
        _write_media(runtime, "scratch/closing.png"),
    ]
    for value in invalid_values:
        with pytest.raises(ValueError):
            runtime.update_frame_workbench(
                "project-1",
                "frame-1",
                last_frame_url=value,
                workbench_tab_mode="direct_r2v",
                workbench_generate_count=6,
            )
        after = runtime.read_committed_script("project-1").model_dump(mode="json")
        assert after == before


def test_workbench_save_failure_rolls_back_memory_and_latches_runtime(tmp_path, monkeypatch):
    runtime = _runtime(tmp_path)
    closing_ref = _write_media(runtime, "storyboard/closing.png")
    _seed_project(runtime)

    def fail_commit(_payloads):
        raise StorageError("synthetic workbench save failure", commit_uncertain=False)

    monkeypatch.setattr(runtime._storage, "commit", fail_commit)
    with pytest.raises(StorageError):
        runtime.update_frame_workbench(
            "project-1",
            "frame-1",
            last_frame_url=closing_ref,
            workbench_tab_mode="direct_r2v",
        )

    assert runtime.scripts["project-1"].frames[0].last_frame_url is None
    assert runtime.scripts["project-1"].frames[0].workbench_tab_mode == "t2i_i2v"
    with pytest.raises(StorageError, match="restart"):
        runtime.assert_storage_available()


def test_workbench_route_rejects_closed_runtime_without_mutation(tmp_path):
    runtime = _runtime(tmp_path)
    closing_ref = _write_media(runtime, "storyboard/closing.png")
    _seed_project(runtime)
    app = _app(runtime)

    with TestClient(app) as client:
        runtime.close()
        response = client.patch(
            "/projects/project-1/frames/frame-1/workbench",
            json={"last_frame_url": closing_ref},
        )
    assert response.status_code == 503
    assert response.json()["code"] == "storage_unavailable"
    assert runtime.scripts["project-1"].frames[0].last_frame_url is None


def test_copy_frame_preserves_last_frame_url(tmp_path):
    runtime = _runtime(tmp_path)
    closing_ref = _write_media(runtime, "storyboard/closing.png")
    _seed_project(runtime, last_frame_url=closing_ref)

    copied = runtime.copy_frame("project-1", "frame-1")
    assert len(copied.frames) == 2
    assert copied.frames[1].id != "frame-1"
    assert copied.frames[1].last_frame_url == closing_ref


def test_project_package_collects_and_rewrites_last_frame_url_media(tmp_path):
    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    closing_ref = "storyboard/closing.png"
    (source_root / "storyboard").mkdir(parents=True, exist_ok=True)
    (source_root / closing_ref).write_bytes(b"closing")
    (source_root / "storyboard/frame.png").write_bytes(b"frame")
    (source_root / "storyboard/t2i.png").write_bytes(b"t2i")
    (source_root / "video").mkdir(parents=True, exist_ok=True)
    (source_root / "video/take.mp4").write_bytes(b"take")
    (source_root / "video/dub.mp4").write_bytes(b"dub")
    (source_root / "audio").mkdir(parents=True, exist_ok=True)
    (source_root / "audio/dialogue.mp3").write_bytes(b"audio")
    (source_root / "assets/characters").mkdir(parents=True, exist_ok=True)
    (source_root / "assets/characters/hero.png").write_bytes(b"hero")
    (source_root / "assets/scenes").mkdir(parents=True, exist_ok=True)
    (source_root / "assets/scenes/room.png").write_bytes(b"room")

    script = Script(
        id="project-old",
        title="Source",
        original_text="text",
        frames=[StoryboardFrame(id="frame-old", scene_id="series-scene", last_frame_url=closing_ref)],
        created_at=1,
        updated_at=2,
    )
    artifact = build_package(
        script,
        resolved_assets=_resolved_assets(),
        data_dir=source_root,
        source_storage_id="source-storage",
    )
    with zipfile.ZipFile(io.BytesIO(artifact.data)) as archive:
        names = set(archive.namelist())
    assert f"media/{closing_ref}" in names

    prepared = prepare_import(artifact.data, data_dir=target_root, now=lambda: 100.0)
    imported_ref = prepared.script.frames[0].last_frame_url
    assert imported_ref == prepared.media[closing_ref]
    assert (target_root / imported_ref).read_bytes() == b"closing"
