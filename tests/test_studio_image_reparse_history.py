"""Reparse replaces creative entities without deleting the existing image ledger."""

from copy import deepcopy
from unittest.mock import Mock

import pytest

from src.apps.comic_gen import pipeline as module
from src.apps.comic_gen.models import GlobalAssetLibrary, Script, Series
from src.utils.json_storage import StorageError
from tests.test_studio_core_image_models import (
    OTHER_REQUEST_ID,
    OTHER_SCOPE_ID,
    execution_payload,
    payloads,
    request_payload,
)

TEXT = "Replacement story"
STATES = ("processing", "needs_confirmation", "pending", "completed", "failed")
FILES = ("projects.json", "series.json", "library_assets.json")


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    for name in ("AssetGenerator", "StoryboardGenerator", "VideoGenerator", "AudioGenerator", "ExportManager"):
        monkeypatch.setattr(module, name, Mock())
    item = module.ComicGenPipeline(data_dir=tmp_path / "data", script_processor=Mock())
    blocked = Mock(side_effect=AssertionError("reparse attempted image execution"))
    monkeypatch.setattr("requests.sessions.Session.request", blocked)
    monkeypatch.setattr("src.models.provider_media_execution.execute_sync_artifact_to_private_file", blocked)
    item._enqueue_asset_image = blocked
    yield item, blocked
    blocked.assert_not_called()
    item.close()


def seed(item, route="project-local", *, history=True):
    data = payloads(route)
    owner = {
        "project-local": data["projects.json"]["project-root"],
        "project-series": data["series.json"]["series-root"],
        "project-global": data["library_assets.json"],
    }[route]
    owner["asset_image_requests"][0]["members"] = [
        {"variant_id": f"attempt-{index}", "execution": execution_payload(attempt=f"attempt-{index}", phase=phase)}
        for index, phase in enumerate(("accepted", "submitting", "completed", "cancelled"))
    ]
    if not history:
        owner["asset_image_requests"] = []
        data["projects.json"]["project-root"]["asset_image_request_scope_id"] = None
        owner["characters"][0]["reference_sheet_request_id"] = None
    item.scripts = {key: Script.model_validate(value) for key, value in data["projects.json"].items()}
    item.series_store = {key: Series.model_validate(value) for key, value in data["series.json"].items()}
    item.library_store = GlobalAssetLibrary.model_validate(data["library_assets.json"])
    item._commit_storage(*FILES)
    return item.read_committed_script("project-root")


def parser_result(*, character_id="fresh-character", state="pending"):
    raw = request_payload()
    raw.update(
        id=OTHER_REQUEST_ID,
        request_root_scope_id=OTHER_SCOPE_ID,
        client_request_id="parser-request",
        asset_id=character_id,
    )
    return Script.model_validate({
        "id": "project-root", "title": "Parser title", "original_text": TEXT,
        "created_at": 2, "updated_at": 2,
        "asset_image_request_scope_id": OTHER_SCOPE_ID,
        "asset_image_requests": [raw],
        "characters": [{
            "id": character_id, "name": "New character", "description": "New description",
            "reference_sheet_request_id": OTHER_REQUEST_ID, "status": state,
            "image_url": "uploads/parser-retained.png",
        }],
        "scenes": [{"id": "fresh-scene", "name": "New scene", "description": "New scene", "status": state}],
        "props": [{"id": "fresh-prop", "name": "New prop", "description": "New prop", "status": state}],
    })


def arrange_parser(item, value, cached):
    item.script_processor.parse_novel.return_value = value
    if cached:
        item.extract_preview("project-root", TEXT)


def disk_snapshot(item):
    return {name: (item.data_dir / name).read_bytes() for name in FILES}


@pytest.mark.parametrize("route", ("project-local", "project-series", "project-global"))
@pytest.mark.parametrize("cached", (False, True))
def test_reparse_preserves_original_history_and_scope_at_actual_owner(runtime, route, cached):
    item, _ = runtime
    original = seed(item, route)
    before = disk_snapshot(item)
    parsed = parser_result()
    parsed_before = deepcopy(parsed.model_dump(mode="json"))
    arrange_parser(item, parsed, cached)

    result = item.reparse_project("project-root", TEXT, expected_revision=original.revision)
    saved = item._load_data()["project-root"]
    assert saved.asset_image_request_scope_id == original.asset_image_request_scope_id
    assert saved.asset_image_requests == original.asset_image_requests
    assert saved.characters[0].id == "fresh-character"
    assert saved.characters[0].reference_sheet_request_id is None
    assert saved.characters[0].image_url == "uploads/parser-retained.png"
    assert saved.original_text == TEXT
    assert saved.series_id == original.series_id
    assert saved.revision == original.revision + 1
    assert result.model_dump() == saved.model_dump()
    assert parsed.model_dump(mode="json") == parsed_before
    assert {name: (item.data_dir / name).read_bytes() for name in FILES[1:]} == {name: before[name] for name in FILES[1:]}
    assert item._load_series_data()["series-root"].asset_image_requests == item.series_store["series-root"].asset_image_requests
    assert item._load_library_data().asset_image_requests == item.library_store.asset_image_requests
    item.script_processor.parse_novel.assert_called_once()
    if route == "project-local":
        assert all(request.asset_id != saved.characters[0].id for request in saved.asset_image_requests)
        assert [member.execution.phase for member in saved.asset_image_requests[0].members] == ["accepted", "submitting", "completed", "cancelled"]
        result.asset_image_requests[0].projection_guard = "e" * 64
        assert item.scripts["project-root"].asset_image_requests == original.asset_image_requests


@pytest.mark.parametrize("cached", (False, True))
def test_legacy_project_cannot_adopt_parser_history_scope_or_same_id_pointer(runtime, cached):
    item, _ = runtime
    original = seed(item, history=False)
    parsed = parser_result(character_id="legacy-character")
    arrange_parser(item, parsed, cached)
    result = item.reparse_project("project-root", TEXT, expected_revision=original.revision)
    assert result.asset_image_requests == []
    assert result.asset_image_request_scope_id is None
    assert result.characters[0].id == "legacy-character"
    assert result.characters[0].reference_sheet_request_id is None
    assert item._load_data()["project-root"].model_dump() == result.model_dump()


@pytest.mark.parametrize("state", STATES)
@pytest.mark.parametrize("cached", (False, True))
def test_replacement_asset_states_have_no_inherited_worker(runtime, state, cached):
    item, _ = runtime
    original = seed(item)
    parsed = parser_result(state=state)
    arrange_parser(item, parsed, cached)
    result = item.reparse_project("project-root", TEXT, expected_revision=original.revision)
    expected = "needs_confirmation" if state == "processing" else state
    assert [asset.status for asset in (*result.characters, *result.scenes, *result.props)] == [expected] * 3
    assert parsed.characters[0].status == state
    assert parsed.scenes[0].status == state
    assert parsed.props[0].status == state


@pytest.mark.parametrize("failure", ("parse", "storage", "cached-storage"))
def test_failed_reparse_preserves_complete_original_authority_and_snapshot(runtime, monkeypatch, failure):
    item, _ = runtime
    original = seed(item)
    before = disk_snapshot(item)
    if failure == "parse":
        item.script_processor.parse_novel.side_effect = RuntimeError("synthetic parser failure")
        expected = module.ProjectGenerationError
    else:
        arrange_parser(item, parser_result(state="processing"), failure == "cached-storage")
        monkeypatch.setattr(item._storage, "commit", Mock(side_effect=StorageError("synthetic storage failure")))
        expected = StorageError
    with pytest.raises(expected):
        item.reparse_project("project-root", TEXT, expected_revision=original.revision)
    assert disk_snapshot(item) == before
    assert item.scripts["project-root"].model_dump() == original.model_dump()
    assert item._load_data()["project-root"].model_dump() == original.model_dump()
