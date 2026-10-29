"""Asset container commands preserve current selections and committed state.

Run only through scripts/run_tests_isolated.py. All media and JSON are synthetic;
component constructors and OSS transport are replaced, while pipeline commands,
Pydantic models, transactions and the upload HTTP route remain real.
"""

import json
from unittest.mock import Mock
from concurrent.futures import ThreadPoolExecutor
from threading import Event, current_thread
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.apps.comic_gen import api, pipeline as module
from src.apps.comic_gen.models import AssetUnit, Character, ImageAsset, ImageVariant, Prop, Scene, Script, Series, VideoVariant
from src.utils.json_storage import StorageError


CHANNELS = [
    ("full_body", "full_body", "full_body_asset"),
    ("head_shot", "headshot", "headshot_asset"),
    ("three_views", "three_view", "three_view_asset"),
]


@pytest.fixture
def project(tmp_path, monkeypatch):
    for name in ("AssetGenerator", "StoryboardGenerator", "VideoGenerator", "AudioGenerator", "ExportManager"):
        monkeypatch.setattr(module, name, Mock())
    pipeline = module.ComicGenPipeline(data_dir=tmp_path / "data", script_processor=Mock())
    uploads = pipeline.data_dir / "uploads"
    uploads.mkdir()
    for name in ("first", "second"):
        (uploads / f"{name}.png").write_bytes(f"synthetic-{name}".encode())
    script = Script(
        id="project", title="project", original_text="synthetic script", created_at=1, updated_at=1,
        characters=[Character(id="character", name="character", description="original")],
        scenes=[Scene(id="scene", name="scene", description="original")],
        props=[Prop(id="prop", name="prop", description="original")],
    )
    pipeline.scripts[script.id] = script
    pipeline._save_data()
    yield pipeline, script
    pipeline.close()


def saved(project):
    pipeline, script = project
    return json.loads((pipeline.data_dir / "projects.json").read_text())[script.id]


def upload(project, kind, channel, *, name="first", description=None):
    pipeline, script = project
    return pipeline.add_uploaded_asset_variant(
        script.id, kind, kind, channel, f"uploads/{name}.png", description,
    )


def sheet(project):
    pipeline, script = project
    character = script.characters[0]
    character.reference_sheet = AssetUnit(
        image_variants=[
            ImageVariant(id="first", url="uploads/first.png"),
            ImageVariant(id="second", url="uploads/second.png"),
        ],
        selected_image_id="first", image_updated_at=10,
    )
    character.image_url = "uploads/first.png"
    pipeline._save_data()
    return character


@pytest.mark.parametrize("kind", ["scene", "prop"])
def test_valid_scene_and_prop_uploads_write_their_real_image_asset(project, kind):
    result = upload(project, kind, "image")
    item = (result.scenes if kind == "scene" else result.props)[0]
    assert item.image_asset.selected_id == item.image_asset.variants[0].id
    assert item.image_asset.variants[0].url == item.image_url == "uploads/first.png"
    assert item.image_asset.variants[0].is_uploaded_source is True
    stored = saved(project)["scenes" if kind == "scene" else "props"][0]
    assert stored["image_asset"]["variants"][0]["url"] == "uploads/first.png"


@pytest.mark.parametrize("kind", ["character", "scene", "prop"])
def test_invalid_upload_type_does_not_mutate_live_description(project, kind):
    pipeline, script = project
    before = script.model_dump()
    disk_before = (pipeline.data_dir / "projects.json").read_bytes()
    with pytest.raises(ValueError, match="upload_type"):
        upload(project, kind, "not-a-channel", description="rejected request")
    assert script.model_dump() == before
    assert (pipeline.data_dir / "projects.json").read_bytes() == disk_before


def test_rejected_upload_cannot_leak_into_a_later_successful_save(project):
    pipeline, script = project
    with pytest.raises(ValueError, match="upload_type"):
        upload(project, "character", "not-a-channel", description="rejected request")
    pipeline.update_project_style(script.id, "later unrelated edit")
    assert saved(project)["characters"][0]["description"] == "original"


@pytest.mark.parametrize("kind", ["character", "scene", "prop"])
def test_http_invalid_upload_type_is_rejected_before_file_or_oss_side_effects(project, monkeypatch, kind):
    pipeline, script = project
    uploader = Mock(is_configured=False)
    uploader.upload_image.return_value = None
    monkeypatch.setattr(api, "OSSImageUploader", Mock(return_value=uploader))
    uploads = pipeline.data_dir / "uploads"
    before = set(uploads.iterdir())
    app = api.create_app(data_dir=pipeline.data_dir, load_config=False, pipeline_instance=pipeline)
    with TestClient(app) as client:
        response = client.post(
            f"/projects/{script.id}/assets/{kind}/{kind}/upload",
            params={"upload_type": "not-a-channel", "description": "rejected request"},
            files={"file": ("test.png", b"synthetic upload", "image/png")},
        )
        assert response.status_code == 400, response.text
        uploader.upload_image.assert_not_called()
        assert set(uploads.iterdir()) == before
        assert script.characters[0].description == "original"


@pytest.mark.parametrize("channel", [None, "reference_sheet"])
def test_reference_sheet_selection_updates_canonical_state_and_disk(project, channel):
    pipeline, script = project
    character = sheet(project)
    pipeline.select_asset_variant(script.id, character.id, "character", "second", channel)
    assert character.reference_sheet.selected_image_id == "second"
    assert character.image_url == "uploads/second.png"
    assert saved(project)["characters"][0]["reference_sheet"]["selected_image_id"] == "second"


@pytest.mark.parametrize("channel", [None, "reference_sheet"])
def test_reference_sheet_favorite_updates_the_visible_variant(project, channel):
    pipeline, script = project
    character = sheet(project)
    pipeline.toggle_variant_favorite(script.id, character.id, "character", "second", True, channel)
    assert character.reference_sheet.image_variants[1].is_favorited is True
    assert character.reference_sheet.selected_image_id == "first"
    assert saved(project)["characters"][0]["reference_sheet"]["image_variants"][1]["is_favorited"] is True


@pytest.mark.parametrize("deleted,expected", [("first", "second"), ("second", "first"), ("both", None)])
def test_reference_sheet_delete_preserves_a_valid_current_selection(project, deleted, expected):
    pipeline, script = project
    character = sheet(project)
    targets = ["first", "second"] if deleted == "both" else [deleted]
    for target in targets:
        pipeline.delete_asset_variant(script.id, character.id, "character", target)
    remaining = character.reference_sheet.image_variants
    assert not set(targets).intersection(v.id for v in remaining)
    assert character.reference_sheet.selected_image_id == expected
    assert character.image_url == (f"uploads/{expected}.png" if expected else None)
    assert saved(project)["characters"][0]["reference_sheet"]["selected_image_id"] == expected


@pytest.mark.parametrize("upload_channel,action_channel,legacy_field", CHANNELS)
def test_existing_legacy_upload_channels_keep_their_two_representations(project, upload_channel, action_channel, legacy_field):
    _, script = project
    upload(project, "character", upload_channel)
    unit = getattr(script.characters[0], upload_channel)
    legacy = getattr(script.characters[0], legacy_field)
    assert unit.selected_image_id == legacy.selected_id
    assert unit.image_variants[0].id == legacy.variants[0].id
    assert unit.image_variants[0].url == legacy.variants[0].url == "uploads/first.png"
    assert unit.image_variants[0].upload_type == upload_channel


@pytest.mark.parametrize("upload_channel,action_channel,legacy_field", CHANNELS)
@pytest.mark.parametrize("action", ["select", "favorite", "delete"])
def test_legacy_management_keeps_uploaded_asset_unit_and_image_asset_in_sync(
    project, upload_channel, action_channel, legacy_field, action,
):
    pipeline, script = project
    upload(project, "character", upload_channel)
    upload(project, "character", upload_channel, name="second")
    character = script.characters[0]
    unit = getattr(character, upload_channel)
    legacy = getattr(character, legacy_field)
    target = legacy.variants[0].id
    if action == "select":
        pipeline.select_asset_variant(script.id, character.id, "character", target, action_channel)
        assert unit.selected_image_id == legacy.selected_id == target
    elif action == "favorite":
        pipeline.toggle_variant_favorite(script.id, character.id, "character", target, True, action_channel)
        assert unit.image_variants[0].is_favorited is legacy.variants[0].is_favorited is True
    else:
        pipeline.delete_asset_variant(script.id, character.id, "character", target)
        assert target not in {v.id for v in unit.image_variants}
        assert target not in {v.id for v in legacy.variants}


def manage(project, action, kind="character", variant_id="first", channel=None):
    pipeline, script = project
    if action == "upload":
        return upload(project, kind, "full_body" if kind == "character" else "image", name="second")
    if action == "select":
        return pipeline.select_asset_variant(script.id, kind, kind, variant_id, channel)
    if action == "favorite":
        return pipeline.toggle_variant_favorite(script.id, kind, kind, variant_id, True, channel)
    return pipeline.delete_asset_variant(script.id, kind, kind, variant_id)


@pytest.mark.parametrize("upload_channel,action_channel,legacy_field", CHANNELS)
@pytest.mark.parametrize("representation", ["unit", "legacy"])
@pytest.mark.parametrize("action", ["select", "favorite", "delete"])
def test_each_single_legacy_schema_remains_manageable(project, upload_channel, action_channel, legacy_field, representation, action):
    pipeline, script = project
    upload(project, "character", upload_channel)
    upload(project, "character", upload_channel, name="second")
    character = script.characters[0]
    unit, legacy = getattr(character, upload_channel), getattr(character, legacy_field)
    target = legacy.variants[0].id
    setattr(character, legacy_field if representation == "unit" else upload_channel, None)
    pipeline._save_data()
    manage(project, action, variant_id=target, channel=action_channel)
    remaining = unit.image_variants if representation == "unit" else legacy.variants
    selected = unit.selected_image_id if representation == "unit" else legacy.selected_id
    if action == "select":
        assert selected == target
    elif action == "favorite":
        assert remaining[0].is_favorited is True
    else:
        assert target not in {v.id for v in remaining}


@pytest.mark.parametrize("action", ["select", "favorite", "delete"])
def test_static_selection_preserves_motion_history_and_its_timestamp(project, action):
    pipeline, _ = project
    character = sheet(project)
    unit = character.reference_sheet
    unit.video_variants = [VideoVariant(id="motion", url="uploads/old.mp4", source_image_id="first")]
    unit.selected_video_id, unit.video_updated_at = "motion", 20
    pipeline._save_data()
    motion = [v.model_dump() for v in unit.video_variants]
    manage(project, action, variant_id="second" if action == "select" else "first")
    assert [v.model_dump() for v in unit.video_variants] == motion
    assert (unit.selected_video_id, unit.video_updated_at) == ("motion", 20)
    assert (unit.image_updated_at == 10) is (action == "favorite")
    assert (pipeline.data_dir / "uploads/first.png").exists()


@pytest.mark.parametrize("action", ["select", "favorite", "delete"])
def test_missing_variant_does_not_commit_or_poison_storage(project, monkeypatch, action):
    pipeline, script = project
    sheet(project)
    before, disk = script.model_dump(), (pipeline.data_dir / "projects.json").read_bytes()
    commit = Mock(wraps=pipeline._storage.commit)
    monkeypatch.setattr(pipeline._storage, "commit", commit)
    with pytest.raises(ValueError, match="Variant"):
        manage(project, action, variant_id="missing")
    assert script.model_dump() == before
    assert (pipeline.data_dir / "projects.json").read_bytes() == disk
    commit.assert_not_called()
    pipeline.assert_storage_available()


@pytest.mark.parametrize("action", ["select", "favorite"])
def test_unknown_explicit_channel_cannot_fall_back_to_another_container(project, action):
    pipeline, script = project
    upload(project, "character", "full_body")
    target = script.characters[0].full_body_asset.variants[0].id
    before = script.model_dump()
    with pytest.raises(ValueError, match="generation_type"):
        manage(project, action, variant_id=target, channel="typo")
    assert script.model_dump() == before
    pipeline.assert_storage_available()


def move_owner(project, owner, kind):
    pipeline, script = project
    field = {"character": "characters", "scene": "scenes", "prop": "props"}[kind]
    item = getattr(script, field).pop()
    if owner == "series":
        script.series_id = "series"
        pipeline.series_store["series"] = Series(
            id="series", title="series", episode_ids=[script.id], created_at=1, updated_at=1,
            **{field: [item]},
        )
    else:
        getattr(pipeline.library_store, field).append(item)
    pipeline._commit_storage("projects.json", "series.json", "library_assets.json")
    return item, field


@pytest.mark.parametrize("owner", ["series", "global"])
@pytest.mark.parametrize("kind", ["character", "scene", "prop"])
@pytest.mark.parametrize("action", ["upload", "select", "favorite", "delete"])
def test_shared_asset_commands_persist_only_the_owning_aggregate(project, owner, kind, action):
    pipeline, script = project
    upload(project, kind, "full_body" if kind == "character" else "image")
    item, field = move_owner(project, owner, kind)
    container = item.full_body_asset if kind == "character" else item.image_asset
    target = container.variants[0].id
    if action == "select":
        container.selected_id = None
        if kind == "character":
            item.full_body.selected_image_id = None
        pipeline._save_after_asset_mutation(owner)
    names = ("projects.json", "series.json", "library_assets.json")
    before = {name: (pipeline.data_dir / name).read_bytes() for name in names}
    result = manage(project, action, kind, target)
    assert result.model_dump(mode="json") == saved(project)
    changed = {name for name in names if (pipeline.data_dir / name).read_bytes() != before[name]}
    assert changed == {"series.json" if owner == "series" else "library_assets.json"}
    stored = json.loads((pipeline.data_dir / next(iter(changed))).read_text())
    if owner == "series":
        stored = stored["series"]
    payload = stored[field][0]["full_body_asset" if kind == "character" else "image_asset"]
    if action == "upload":
        assert len(payload["variants"]) == 2
    elif action == "select":
        assert payload["selected_id"] == target
    elif action == "favorite":
        assert payload["variants"][0]["is_favorited"] is True
    else:
        assert payload["variants"] == []


@pytest.mark.parametrize("action", ["upload", "select", "favorite", "delete"])
@pytest.mark.parametrize("owner", ["script", "series", "global"])
def test_failed_command_restores_committed_aggregate_and_closes_runtime(project, monkeypatch, action, owner):
    pipeline, script = project
    sheet(project)
    if owner != "script":
        move_owner(project, owner, "character")
    before = {name: value for name, value in pipeline._committed.items()}
    monkeypatch.setattr(pipeline._storage, "commit", Mock(side_effect=StorageError("disk full")))
    with pytest.raises(StorageError):
        manage(project, action, variant_id="second")
    assert pipeline._storage_payloads(tuple(before)) == before
    with pytest.raises(StorageError):
        pipeline.assert_storage_available()
    assert (pipeline.data_dir / "uploads/second.png").exists()


@pytest.mark.parametrize("state", ["closed", "failed"])
@pytest.mark.parametrize("action", ["upload", "select", "favorite", "delete"])
def test_unavailable_runtime_rejects_before_any_mutation(project, state, action):
    pipeline, script = project
    sheet(project)
    before = script.model_dump()
    if state == "closed":
        pipeline.close()
    else:
        pipeline._storage_failed = StorageError("previous failure")
    with pytest.raises(StorageError):
        manage(project, action, variant_id="second")
    assert script.model_dump() == before


@pytest.mark.parametrize("action", ["select", "favorite"])
def test_noop_returns_committed_state_without_increment_or_write(project, monkeypatch, action):
    pipeline, script = project
    character = sheet(project)
    character.reference_sheet.image_variants[0].is_favorited = True
    pipeline._save_data()
    before = saved(project)
    # A different long operation may own unsaved live fields.
    script.title = "not committed"
    commit = Mock(wraps=pipeline._storage.commit)
    monkeypatch.setattr(pipeline._storage, "commit", commit)
    result = manage(project, action)
    assert result.model_dump(mode="json") == before
    commit.assert_not_called()


def test_selection_and_legacy_mirrors_survive_restart(project):
    pipeline, script = project
    sheet(project)
    upload(project, "character", "full_body")
    manage(project, "select", variant_id="second", channel="reference_sheet")
    manage(project, "favorite", variant_id="second")
    manage(project, "delete", variant_id="first")
    expected = saved(project)
    pipeline.close()
    restored = module.ComicGenPipeline(data_dir=pipeline.data_dir, script_processor=Mock())
    try:
        assert restored.read_committed_script(script.id).model_dump(mode="json") == expected
    finally:
        restored.close()


def test_http_rechecks_target_after_io_without_holding_the_command_lock(project, monkeypatch):
    pipeline, script = project
    uploader = Mock(is_configured=False)
    uploaded = []
    def upload_then_remove(path):
        uploaded.append(path)
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(pipeline.delete_character, script.id, "character").result(timeout=3)
        return None
    uploader.upload_image.side_effect = upload_then_remove
    monkeypatch.setattr(api, "OSSImageUploader", Mock(return_value=uploader))
    app = api.create_app(data_dir=pipeline.data_dir, load_config=False, pipeline_instance=pipeline)
    with TestClient(app) as client:
        response = client.post(
            f"/projects/{script.id}/assets/character/character/upload", params={"upload_type": "full_body"},
            files={"file": ("test.png", b"already uploaded", "image/png")},
        )
        assert response.status_code == 400
        assert script.characters == []
        assert len(uploaded) == 1
        assert Path(uploaded[0]).read_bytes() == b"already uploaded"


@pytest.mark.parametrize("owner", ["series", "global"])
@pytest.mark.parametrize("action", ["upload", "select", "favorite", "delete"])
def test_http_ack_includes_updated_shared_asset_and_its_source(project, monkeypatch, owner, action):
    pipeline, script = project
    sheet(project)
    move_owner(project, owner, "character")
    uploader = Mock(is_configured=False)
    uploader.upload_image.return_value = None
    monkeypatch.setattr(api, "OSSImageUploader", Mock(return_value=uploader))
    app = api.create_app(data_dir=pipeline.data_dir, load_config=False, pipeline_instance=pipeline)
    with TestClient(app) as client:
        if action == "upload":
            response = client.post(
                f"/projects/{script.id}/assets/character/character/upload", params={"upload_type": "full_body"},
                files={"file": ("test.png", b"new image", "image/png")},
            )
        else:
            response = client.post(f"/projects/{script.id}/assets/variant/{action}", json={
                "asset_type": "character", "asset_id": "character", "variant_id": "second",
                **({"is_favorited": True} if action == "favorite" else {}),
            })
        assert response.status_code == 200, response.text
        asset = response.json()["characters"][0]
        assert asset["source"] == owner
        assert asset == client.get(f"/projects/{script.id}").json()["characters"][0]
        if action == "upload":
            assert asset["full_body_asset"]["variants"][0]["is_uploaded_source"] is True
        elif action == "select":
            assert asset["reference_sheet"]["selected_image_id"] == "second"
        elif action == "favorite":
            assert asset["reference_sheet"]["image_variants"][1]["is_favorited"] is True
        else:
            assert [v["id"] for v in asset["reference_sheet"]["image_variants"]] == ["first"]


def test_http_owner_change_during_upload_is_a_conflict_without_mutating_the_new_owner(project, monkeypatch):
    pipeline, script = project
    uploader = Mock(is_configured=False)
    def move_during_upload(path):
        move_owner(project, "global", "character")
        return None
    uploader.upload_image.side_effect = move_during_upload
    monkeypatch.setattr(api, "OSSImageUploader", Mock(return_value=uploader))
    app = api.create_app(data_dir=pipeline.data_dir, load_config=False, pipeline_instance=pipeline)
    with TestClient(app) as client:
        response = client.post(
            f"/projects/{script.id}/assets/character/character/upload", params={"upload_type": "full_body"},
            files={"file": ("test.png", b"new image", "image/png")},
        )
        assert response.status_code == 409, response.text
        assert pipeline.library_store.characters[0].full_body.image_variants == []
        pipeline.assert_storage_available()


def test_http_missing_target_is_rejected_before_upload_io(project, monkeypatch):
    pipeline, script = project
    uploader = Mock(is_configured=False)
    monkeypatch.setattr(api, "OSSImageUploader", Mock(return_value=uploader))
    before = set((pipeline.data_dir / "uploads").iterdir())
    app = api.create_app(data_dir=pipeline.data_dir, load_config=False, pipeline_instance=pipeline)
    with TestClient(app) as client:
        response = client.post(
            f"/projects/{script.id}/assets/character/missing/upload", params={"upload_type": "full_body"},
            files={"file": ("test.png", b"new image", "image/png")},
        )
        assert response.status_code == 400
        assert set((pipeline.data_dir / "uploads").iterdir()) == before
        uploader.upload_image.assert_not_called()


def test_http_save_failure_returns_503_and_preserves_uploaded_media(project, monkeypatch):
    pipeline, script = project
    uploader = Mock(is_configured=False)
    uploader.upload_image.return_value = None
    monkeypatch.setattr(api, "OSSImageUploader", Mock(return_value=uploader))
    before = set((pipeline.data_dir / "uploads").iterdir())
    monkeypatch.setattr(pipeline._storage, "commit", Mock(side_effect=StorageError("disk full")))
    app = api.create_app(data_dir=pipeline.data_dir, load_config=False, pipeline_instance=pipeline)
    with TestClient(app) as client:
        response = client.post(
            f"/projects/{script.id}/assets/character/character/upload", params={"upload_type": "full_body"},
            files={"file": ("test.png", b"recoverable image", "image/png")},
        )
        assert response.status_code == 503, response.text
        remaining = set((pipeline.data_dir / "uploads").iterdir()) - before
        assert len(remaining) == 1
        assert next(iter(remaining)).read_bytes() == b"recoverable image"
        assert pipeline.scripts[script.id].characters[0].full_body.image_variants == []


@pytest.mark.parametrize("action", ["upload", "select", "favorite", "delete"])
def test_command_rechecks_closed_runtime_after_waiting_for_lock(project, monkeypatch, action):
    pipeline, script = project
    sheet(project)
    before = script.model_dump()
    lock, attempted, owner = pipeline._save_lock, Event(), current_thread()
    class ObserveLock:
        def __enter__(self):
            if current_thread() is not owner:
                attempted.set()
            lock.acquire()
            return self
        def __exit__(self, *args):
            lock.release()
    monkeypatch.setattr(pipeline, "_save_lock", ObserveLock())
    with ThreadPoolExecutor(max_workers=1) as pool:
        with lock:
            pending = pool.submit(manage, project, action, variant_id="second")
            assert attempted.wait(3)
            pipeline.close()
        with pytest.raises(StorageError):
            pending.result(timeout=3)
    assert script.model_dump() == before


def test_legacy_upload_does_not_promote_to_or_replace_the_canonical_sheet(project):
    _, script = project
    character = sheet(project)
    before = character.reference_sheet.model_dump()
    upload(project, "character", "full_body", name="second")
    assert character.reference_sheet.model_dump() == before
    assert character.image_url == "uploads/first.png"
    assert character.full_body_image_url == "uploads/second.png"
    manage(project, "delete", variant_id="first")
    manage(project, "delete", variant_id="second")
    assert character.image_url == character.full_body_image_url == "uploads/second.png"


@pytest.mark.parametrize("upload_channel,action_channel,legacy_field", CHANNELS)
@pytest.mark.parametrize("source", ["unit", "legacy"])
def test_selection_reconciles_only_the_selected_candidate_when_legacy_histories_differ(
    project, upload_channel, action_channel, legacy_field, source,
):
    pipeline, script = project
    upload(project, "character", upload_channel)
    upload(project, "character", upload_channel, name="second")
    character = script.characters[0]
    unit, legacy = getattr(character, upload_channel), getattr(character, legacy_field)
    selected_id = legacy.variants[0].id
    if source == "legacy":
        unit.image_variants = unit.image_variants[1:]
    else:
        legacy.variants = legacy.variants[1:]
    pipeline._save_data()

    manage(project, "select", variant_id=selected_id, channel=action_channel)

    assert unit.selected_image_id == legacy.selected_id == selected_id
    assert character.image_url == "uploads/first.png"
    assert {v.id for v in unit.image_variants} == {v.id for v in legacy.variants}
    unit_selected = next(v for v in unit.image_variants if v.id == selected_id)
    legacy_selected = next(v for v in legacy.variants if v.id == selected_id)
    assert unit_selected == legacy_selected
    assert unit_selected is not legacy_selected


@pytest.mark.parametrize("upload_channel,action_channel,legacy_field", CHANNELS)
@pytest.mark.parametrize("unit_ids,unit_current,legacy_ids,legacy_current,expected", [
    (["deleted", "unit-only"], "deleted", ["deleted", "legacy-only"], "deleted", "unit-only"),
    (["deleted"], "deleted", ["deleted", "keep"], "keep", "keep"),
    (["deleted", "keep", "unit-tail"], "keep", ["deleted", "legacy-only"], "legacy-only", "keep"),
    (["deleted", "keep", "unit-tail"], "keep", ["deleted", "keep"], "keep", "keep"),
    (["deleted"], "deleted", ["deleted", "legacy-tail"], "missing", "legacy-tail"),
    (["deleted"], "deleted", ["deleted"], "deleted", None),
])
def test_delete_chooses_once_for_different_legacy_histories(
    project, upload_channel, action_channel, legacy_field,
    unit_ids, unit_current, legacy_ids, legacy_current, expected,
):
    pipeline, script = project
    character = script.characters[0]
    variants = lambda identities: [ImageVariant(id=identity, url=f"uploads/{identity}.png") for identity in identities]
    unit = AssetUnit(
        image_variants=variants(unit_ids), selected_image_id=unit_current, image_updated_at=10,
        video_variants=[VideoVariant(id="motion", url="uploads/motion.mp4", source_image_id="deleted")],
        selected_video_id="motion", video_updated_at=20,
    )
    legacy = ImageAsset(variants=variants(legacy_ids), selected_id=legacy_current)
    setattr(character, upload_channel, unit)
    setattr(character, legacy_field, legacy)
    unrelated = {
        name: getattr(character, name).model_dump() for name in ("reference_sheet", "full_body", "head_shot", "three_views")
        if name != upload_channel and getattr(character, name) is not None
    }
    pipeline._save_data()

    manage(project, "delete", variant_id="deleted")

    assert unit.selected_image_id == legacy.selected_id == expected
    assert character.image_url == (f"uploads/{expected}.png" if expected else None)
    assert {v.id for v in unit.image_variants} == (set(unit_ids) - {"deleted"}) | ({expected} if expected else set())
    assert {v.id for v in legacy.variants} == (set(legacy_ids) - {"deleted"}) | ({expected} if expected else set())
    assert unit.selected_video_id == "motion" and unit.video_updated_at == 20
    assert [v.id for v in unit.video_variants] == ["motion"]
    assert unit.video_variants[0].source_image_id == "deleted"
    if expected == unit_current:
        assert unit.image_updated_at == 10
    assert unrelated == {name: getattr(character, name).model_dump() for name in unrelated}
    if expected:
        assert next(v for v in unit.image_variants if v.id == expected) is not next(v for v in legacy.variants if v.id == expected)
        revision = script.revision
        manage(project, "select", variant_id=expected, channel=action_channel)
        assert script.revision == revision
