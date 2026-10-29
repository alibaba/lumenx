"""Scene/prop copying keeps creative media but cannot transfer execution rights."""

from copy import deepcopy
import io
import json
from unittest.mock import Mock
import zipfile

import pytest

from src.apps.comic_gen.asset_image_models import AssetImageRequestRecord
from src.apps.comic_gen.models import Prop, Scene, Script
from src.apps.comic_gen.project_package import build_package, prepare_import
from src.utils.json_storage import StorageError
from tests.test_studio_core_image_copy_inert import (
    loaded_history, seed_runtime,
)
from tests.test_studio_core_image_models import OTHER_REQUEST_ID, REQUEST_ID, SCOPE_ID, request_payload
from tests import test_studio_core_image_copy_inert as legacy_copy
from tests.test_studio_image_reparse_history import arrange_parser, disk_snapshot
from tests.test_project_package import _resolved_assets, _source_project


runtime = legacy_copy.runtime
KINDS = ("scene", "prop")
STATES = ("processing", "needs_confirmation", "completed")
ROUTES = ("promote-project", "promote-series", "fork-global", "import-series")


def image_asset(kind, *, state="processing", identity="image-target"):
    raw = dict(
        id=identity, name="Retained image", description="Retained description",
        image_request_id=REQUEST_ID, status=state, locked=True, starred=True,
        image_url="uploads/chosen.png", video_prompt="Independent video prompt",
        image_asset=dict(selected_id="chosen", variants=[
            dict(id="alternate", url="uploads/alternate.png", is_favorited=True),
            dict(id="chosen", url="uploads/chosen.png"),
        ]),
    )
    if kind == "prop":
        raw["audio_url"] = "audio/retained.wav"
    return (Scene if kind == "scene" else Prop).model_validate(raw)



def seed(item, kind, route, state):
    owner, _ = seed_runtime(item, route, state)
    asset = image_asset(kind, state=state)
    getattr(owner, kind + "s").append(asset)
    record = owner.asset_image_requests[0]
    raw = record.model_dump(mode="json")
    raw.update(id=OTHER_REQUEST_ID, client_request_id="scene-prop-copy",
               entrypoint="studio.asset.scene_prop.v1", asset_type=kind,
               asset_id=asset.id, channel="image")
    raw["members"][0]["variant_id"] = "copy-image-attempt"
    from tests.test_studio_core_image_models import execution_payload
    raw["members"][0]["execution"] = execution_payload(attempt="copy-image-attempt")
    asset.image_request_id = OTHER_REQUEST_ID
    owner.asset_image_requests.append(AssetImageRequestRecord.model_validate(raw))
    item._commit_storage("projects.json", "series.json", "library_assets.json")
    return owner, asset


def assert_copy(before, copied):
    expected = deepcopy(before)
    expected["id"] = copied.id
    expected["image_request_id"] = None
    if expected["status"] == "processing":
        expected["status"] = "needs_confirmation"
    assert copied.model_dump(mode="json") == expected


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("state", STATES)
@pytest.mark.parametrize("route", ROUTES)
def test_copy_clears_new_pointer_without_mutating_source_or_unrelated_history(runtime, kind, state, route):
    item, blocked = runtime
    owner, asset = seed(item, kind, route, state)
    before = asset.model_dump(mode="json")
    history = [record.model_dump(mode="json") for record in owner.asset_image_requests]
    if route.startswith("promote"):
        source_kind = "project" if route == "promote-project" else "series"
        copied = item.promote_asset_to_library(source_kind, source_kind + "-root", kind, asset.id)
    elif route == "fork-global":
        copied = item.fork_library_asset_to_project("project-root", kind, asset.id)
    else:
        target, imported, skipped = item.import_assets_from_series("target", "series-root", [asset.id])
        assert imported == [asset.id] and skipped == []
        copied = getattr(target, kind + "s")[0]
        assert target.asset_image_requests == [] and target.asset_image_request_scope_id is None
    assert copied.id != asset.id
    assert_copy(before, copied)
    assert asset.model_dump(mode="json") == before
    assert loaded_history(item) == history
    blocked.assert_not_called()


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("failure", (False, True))
def test_move_keeps_history_at_original_owner_and_rolls_back_failed_storage(runtime, monkeypatch, kind, failure):
    item, blocked = runtime
    owner, asset = seed(item, kind, "move", "processing")
    before = asset.model_dump(mode="json")
    history = [record.model_dump(mode="json") for record in owner.asset_image_requests]
    disk = disk_snapshot(item)
    command = {kind + "s": [{"local_id": asset.id, "action": "create_new_in_series"}]}
    if failure:
        monkeypatch.setattr(item._storage, "commit", Mock(side_effect=StorageError("copy write failure")))
        with pytest.raises(StorageError):
            item.reconcile_project_assets("project-root", command)
        assert disk_snapshot(item) == disk
        assert getattr(item.scripts["project-root"], kind + "s")[0].model_dump(mode="json") == before
    else:
        item.reconcile_project_assets("project-root", command)
        project, series = item._load_data()["project-root"], item._load_series_data()["series-root"]
        assert getattr(project, kind + "s") == []
        assert_copy(before, getattr(series, kind + "s")[0])
        assert project.asset_image_request_scope_id == SCOPE_ID
        assert series.asset_image_requests == [] and series.asset_image_request_scope_id is None
    assert loaded_history(item) == history
    blocked.assert_not_called()


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("cached", (False, True))
@pytest.mark.parametrize("same_id", (False, True))
def test_reparse_strips_parser_pointer_but_keeps_original_history(runtime, kind, cached, same_id):
    item, blocked = runtime
    owner, asset = seed(item, kind, "promote-project", "completed")
    reparse_revision = item.read_committed_script("project-root").revision
    history = [record.model_dump(mode="json") for record in owner.asset_image_requests]
    parsed = Script.model_validate(dict(id="parser-project", title="Replacement", original_text="Replacement story", created_at=2, updated_at=2))
    replacement = image_asset(kind, identity=asset.id if same_id else "fresh-asset")
    getattr(parsed, kind + "s").append(replacement)
    parsed.asset_image_request_scope_id = REQUEST_ID
    before = parsed.model_dump(mode="json")
    arrange_parser(item, parsed, cached)
    result = item.reparse_project("project-root", "Replacement story", expected_revision=reparse_revision)
    assert_copy(replacement.model_dump(mode="json"), getattr(result, kind + "s")[0])
    assert result.asset_image_request_scope_id == SCOPE_ID
    assert [record.model_dump(mode="json") for record in result.asset_image_requests] == history
    assert item._load_data()["project-root"].model_dump() == result.model_dump()
    assert parsed.model_dump(mode="json") == before
    blocked.assert_not_called()


def package_fixture(tmp_path, kind, state):
    root = tmp_path / "source"
    source = _source_project(root)
    assets = _resolved_assets()
    asset = image_asset(kind, state=state, identity="series-scene" if kind == "scene" else "series-prop")
    for path in ("uploads/chosen.png", "uploads/alternate.png", "audio/retained.wav"):
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(path.encode())
    setattr(assets, kind + "s", [asset])
    if kind == "prop":
        source.frames[0].prop_ids = [asset.id]
    raw = request_payload()
    raw.update(entrypoint="studio.asset.scene_prop.v1", asset_type=kind, channel="image",
               owner_id=source.id, request_root_id=source.id, asset_id=asset.id)
    source.asset_image_requests = [AssetImageRequestRecord.model_validate(raw)]
    source.asset_image_request_scope_id = SCOPE_ID
    return source, assets, asset


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("state", STATES)
@pytest.mark.parametrize("older_package", (False, True))
def test_package_roundtrip_and_older_import_keep_media_without_pointer(tmp_path, kind, state, older_package):
    source, assets, asset = package_fixture(tmp_path, kind, state)
    before = source.model_dump(), assets.model_dump()
    artifact = build_package(source, resolved_assets=assets, data_dir=tmp_path / "source", source_storage_id="source")
    with zipfile.ZipFile(io.BytesIO(artifact.data)) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    payload = json.loads(entries["project.json"])
    for container in (payload["script"], payload["resolved_assets"]):
        copied = container[kind + "s"][0]
        assert copied.get("image_request_id") is None
        assert copied["status"] == ("needs_confirmation" if state == "processing" else state)
        if older_package:
            copied["image_request_id"] = REQUEST_ID
            copied["status"] = state
    if older_package:
        payload["script"]["asset_image_request_scope_id"] = SCOPE_ID
        payload["script"]["asset_image_requests"] = [source.asset_image_requests[0].model_dump(mode="json")]
        entries["project.json"] = json.dumps(payload).encode()
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, value in entries.items():
                archive.writestr(name, value)
        data = buffer.getvalue()
    else:
        data = artifact.data
    imported = prepare_import(data, data_dir=tmp_path / "target").script
    copied = getattr(imported, kind + "s")[0]
    assert copied.id != asset.id and copied.image_request_id is None
    assert copied.status == ("needs_confirmation" if state == "processing" else state)
    assert copied.starred and copied.locked
    assert imported.asset_image_request_scope_id is None and imported.asset_image_requests == []
    assert copied.image_asset.selected_id == copied.image_asset.variants[1].id
    assert copied.image_asset.variants[0].is_favorited
    assert [(tmp_path / "target" / variant.url).read_bytes() for variant in copied.image_asset.variants] == [b"uploads/alternate.png", b"uploads/chosen.png"]
    assert (source.model_dump(), assets.model_dump()) == before
