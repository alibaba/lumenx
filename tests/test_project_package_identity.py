"""A copied project keeps local selections but owns new variant identities."""

import pytest

from src.apps.comic_gen.models import AssetUnit, ImageVariant, VideoVariant
from src.apps.comic_gen.project_package import ProjectPackageError, build_package, prepare_import
from tests.test_project_package import _source_project, _resolved_assets


def test_variant_identities_and_source_selection_are_copied_consistently(tmp_path):
    source = _source_project(tmp_path / "source")
    assets = _resolved_assets()
    unit = AssetUnit(
        selected_image_id="v1", selected_video_id="motion-1",
        image_variants=[ImageVariant(id="v1", url="assets/characters/hero.png")],
        video_variants=[VideoVariant(id="motion-1", source_image_id="v1", url="video/take.mp4")],
    )
    assets.characters[0].reference_sheet = unit
    package = build_package(source, resolved_assets=assets, data_dir=tmp_path / "source", source_storage_id="s")
    imported = prepare_import(package.data, data_dir=tmp_path / "target").script
    character = imported.characters[0]
    copied = character.reference_sheet
    image = copied.image_variants[0]
    video = copied.video_variants[0]
    assert image.id != "v1" and video.id != "motion-1"
    assert copied.selected_image_id == video.source_image_id == image.id
    assert copied.selected_video_id == video.id
    assert character.full_body_asset.selected_id == character.full_body_asset.variants[0].id == image.id
    assert assets.characters[0].reference_sheet == unit


@pytest.mark.parametrize("collection", ["frames", "video_tasks", "characters", "scenes"])
def test_duplicate_entity_ids_are_rejected_before_export(tmp_path, collection):
    source = _source_project(tmp_path / "source")
    assets = _resolved_assets()
    owner = source if collection in {"frames", "video_tasks"} else assets
    values = getattr(owner, collection)
    values.append(values[0].model_copy(deep=True))
    with pytest.raises(ProjectPackageError, match="Duplicate"):
        build_package(source, resolved_assets=assets, data_dir=tmp_path / "source", source_storage_id="s")


@pytest.mark.parametrize("selection", ["selected_video_id", "final_take_id", "dubbed_video_task_id", "dub_preview_source"])
def test_selected_take_must_belong_to_its_frame(tmp_path, selection):
    source = _source_project(tmp_path / "source")
    assets = _resolved_assets()
    source.frames.append(source.frames[0].model_copy(update={
        "id": "other-frame", "selected_video_id": None, "final_take_id": None,
        "dubbed_video_task_id": None, "dub_preview_source": None,
    }, deep=True))
    source.video_tasks.append(source.video_tasks[0].model_copy(update={"id": "other-take", "frame_id": "other-frame"}, deep=True))
    if selection == "dub_preview_source":
        source.frames[0].dub_preview_source = source.frames[0].dub_preview_source.model_copy(update={"task_id": "other-take"})
    else:
        setattr(source.frames[0], selection, "other-take")
    with pytest.raises(ProjectPackageError, match="different frame"):
        build_package(source, resolved_assets=assets, data_dir=tmp_path / "source", source_storage_id="s")


@pytest.mark.parametrize("replacement", [None, b"changed media"])
def test_definite_failure_cleanup_removes_only_verified_import_files(tmp_path, replacement):
    from pathlib import Path
    from src.apps.comic_gen.project_package import discard_prepared_import
    from src.utils.json_storage import DirectoryWriterLock

    source = _source_project(tmp_path / "source")
    package = build_package(source, resolved_assets=_resolved_assets(), data_dir=tmp_path / "source", source_storage_id="s")
    target = tmp_path / "target"
    with DirectoryWriterLock(target).acquire() as lease:
        prepared = prepare_import(package.data, data_dir=target, writer_lock=lease)
        paths = [target / value for value in prepared.media.values()]
        unrelated = target / "video" / "another-project.mp4"
        unrelated.parent.mkdir(parents=True, exist_ok=True)
        unrelated.write_bytes(b"another project")
        if replacement is not None:
            paths[0].write_bytes(replacement)
            with pytest.raises(ProjectPackageError, match="changed"):
                discard_prepared_import(prepared, data_dir=target, writer_lock=lease)
            assert paths[0].read_bytes() == replacement
        else:
            discard_prepared_import(prepared, data_dir=target, writer_lock=lease)
            assert not any(path.exists() for path in paths)
        assert unrelated.read_bytes() == b"another project"
        assert all((tmp_path / "source" / Path(ref)).is_file() for ref in prepared.media)
