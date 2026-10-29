"""Independent copies retain portraits without execution ownership.

Uses the real Core headshot fixtures. Positive receipts and PNG proof are produced by the real
app/storage/worker using the inherited external GPT transport fixture. Reparse
replaces only the structured external LLM answer; rollback injects one negative
storage commit. No positive execution record is fabricated.
"""

from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import wave
import zipfile

import pytest
from fastapi.testclient import TestClient

from src.apps.comic_gen.models import Scene, Series, StoryboardFrame
from src.utils.json_storage import StorageError
from tests import test_studio_headshot_core as headshot
from tests import test_studio_three_view_copy_reparse as copies
from tests import test_project_package_prepared_three_view as packages


runtime = headshot.runtime
transport = headshot.transport
first = headshot.three_view
gpt_sheet = headshot.gpt_sheet


def generate(client, item, state, route, before):
    gpt_sheet.configure(client.app, "http")
    gpt_sheet.queue_images(state, "http", count=1)
    body = {
        "client_request_id": "headshot-copy-request", "asset_id": "c", "asset_type": "character",
        "generation_type": "headshot", "reference_variant_id": first.SELECTED_ID,
        "reference_image_url": first.SELECTED_REF, "prompt": headshot.PROMPT,
        "apply_style": False, "batch_size": 1,
    }
    response = client.post(first.PUBLIC, json=body)
    assert response.status_code == 200, response.text
    headshot.assert_wire(state, before["reference"], headshot.PROMPT, strict=True)
    target = headshot.assert_projection(item, route, before, headshot.PROMPT, headshot.PROMPT, normalized=True)
    owner = headshot.saved_owner(item, route)
    assert len(owner.asset_image_requests) == 1
    record = owner.asset_image_requests[0]
    assert record.id == response.json()["request"]["id"] == target.headshot_request_id
    assert (record.entrypoint, record.asset_type, record.channel) == ("studio.asset.headshot.v1", "character", "headshot")
    assert record.members[0].execution.phase == "completed"
    assert record.members[0].execution.artifact == record.members[0].execution.prepared_artifact
    return body, record.model_dump(mode="json"), target.model_dump(mode="json")


def saved_history(item):
    owners = [*item._load_data().values(), *item._load_series_data().values(), item._load_library_data()]
    return [record.model_dump(mode="json") for owner in owners for record in owner.asset_image_requests]


def assert_detached_copy(source, copied, *, same_id=False):
    assert (copied.id == source["id"]) is same_id
    assert copied.model_dump(mode="json") == dict(source, id=copied.id, headshot_request_id=None)


def assert_original_read_only(client, item, state, body, record, *, reason=None):
    files, media, calls = gpt_sheet.persisted(item), copies.png_bytes(item), len(state["calls"])
    observed = client.get(first.PUBLIC + "/" + body["client_request_id"])
    replay = client.post(first.PUBLIC, json=body)
    assert observed.status_code == replay.status_code == 200
    assert observed.json()["reason"] == replay.json()["reason"] == reason
    assert observed.json()["request"]["id"] == record["id"]
    assert observed.json()["request"] == replay.json()["request"] and replay.json()["replayed"] is True
    assert saved_history(item) == [record]
    assert gpt_sheet.persisted(item) == files and copies.png_bytes(item) == media
    assert len(state["calls"]) == calls and not state["queue"] and not state["cli"]


@pytest.mark.parametrize("route,operation", [
    ("local", "promote-project"), ("series", "promote-series"),
    ("global", "fork-global"), ("series", "import-series"),
], ids=["promote-project", "promote-series", "fork-global", "import-series"])
def test_public_headshot_copy_clears_only_copied_request(runtime, transport, route, operation):
    before = headshot.setup_headshot(runtime, route)
    if operation == "import-series":
        runtime.series_store["target"] = Series.model_validate({
            "id": "target", "title": "Target", "created_at": 1, "updated_at": 1,
        })
        runtime._commit_storage("series.json")
    with TestClient(first.app_for(runtime)) as client:
        body, record, source = generate(client, runtime, transport, route, before)
        media, calls = copies.png_bytes(runtime), len(transport["calls"])
        scope = runtime.scripts["p"].asset_image_request_scope_id
        if operation.startswith("promote"):
            response = client.post("/library/assets/promote", json={
                "source_kind": "project" if route == "local" else "series",
                "source_id": "p" if route == "local" else "s", "asset_type": "character", "asset_id": "c",
            })
            destination = runtime._load_library_data()
        elif operation == "fork-global":
            response = client.post("/projects/p/assets/fork_from_library", json={
                "asset_type": "character", "library_asset_id": "c",
            })
            destination = runtime._load_data()["p"]
        else:
            response = client.post("/series/target/assets/import", json={
                "source_series_id": "s", "asset_ids": ["c"],
            })
            destination = runtime._load_series_data()["target"]
        assert response.status_code == 200, response.text
        assert len(destination.characters) == 1 and destination.asset_image_requests == []
        assert_detached_copy(source, destination.characters[0])
        assert headshot.saved_owner(runtime, route).characters[0].model_dump(mode="json") == source
        assert runtime._load_data()["p"].asset_image_request_scope_id == scope
        assert copies.png_bytes(runtime) == media and len(transport["calls"]) == calls
        assert_original_read_only(client, runtime, transport, body, record)


@pytest.mark.parametrize("failure", [False, True], ids=["committed-move", "failed-move-rollback"])
def test_headshot_move_keeps_original_history_and_rolls_back_failure(runtime, transport, monkeypatch, failure):
    before = headshot.setup_headshot(runtime)
    runtime.scripts["p"].series_id = "s"
    runtime.series_store["s"].episode_ids = ["p"]
    runtime._commit_storage("projects.json", "series.json")
    with TestClient(first.app_for(runtime), raise_server_exceptions=False) as client:
        body, record, source = generate(client, runtime, transport, "local", before)
        files, media, calls = gpt_sheet.persisted(runtime), copies.png_bytes(runtime), len(transport["calls"])
        old_project = runtime._load_data()["p"].model_dump(mode="json")
        rejected = []
        if failure:
            original_commit = runtime._storage.commit

            def reject_move(payload):
                # Negative persistence fault only; positive receipts are untouched.
                if payload.get("projects.json", {}).get("p", {}).get("characters") == []:
                    rejected.append(True)
                    raise StorageError("synthetic headshot move commit failure")
                return original_commit(payload)

            monkeypatch.setattr(runtime._storage, "commit", reject_move)
        response = client.post("/projects/p/reconcile/apply", json={
            "characters": [{"local_id": "c", "action": "create_new_in_series"}],
        })
        if failure:
            assert response.status_code >= 500 and rejected == [True]
            assert gpt_sheet.persisted(runtime) == files
            assert runtime.scripts["p"].model_dump(mode="json") == old_project
            assert runtime._load_data()["p"].model_dump(mode="json") == old_project
            assert runtime.series_store["s"].characters == []
        else:
            assert response.status_code == 200, response.text
            project, series = runtime._load_data()["p"], runtime._load_series_data()["s"]
            assert project.characters == [] and len(series.characters) == 1
            assert project.asset_image_request_scope_id == old_project["asset_image_request_scope_id"]
            assert series.asset_image_request_scope_id is None and series.asset_image_requests == []
            assert_detached_copy(source, series.characters[0], same_id=True)
            assert_original_read_only(client, runtime, transport, body, record, reason="studio_image_request_owner_changed")
        assert saved_history(runtime) == [record]
        assert copies.png_bytes(runtime) == media and len(transport["calls"]) == calls


@pytest.mark.parametrize("cached", [False, True], ids=["parse", "cached-preview"])
def test_headshot_reparse_same_id_clears_pointer_without_adopting_parser_object(runtime, transport, monkeypatch, cached):
    before = headshot.setup_headshot(runtime)
    with TestClient(first.app_for(runtime)) as client:
        body, record, source = generate(client, runtime, transport, "local", before)
        project = runtime._load_data()["p"]
        parsed = project.model_copy(deep=True)
        parsed.original_text = "New story with the same character identity"
        parser_snapshot = parsed.model_dump(mode="json")
        parser_calls = []

        def answer(title, text, custom_extraction_prompt):
            parser_calls.append((title, text, custom_extraction_prompt))
            return parsed

        monkeypatch.setattr(runtime.script_processor, "parse_novel", answer)
        if cached:
            preview = client.post("/projects/p/extract_preview", json={"text": parsed.original_text})
            assert preview.status_code == 200, preview.text
        media, calls = copies.png_bytes(runtime), len(transport["calls"])
        response = client.put("/projects/p/reparse", json={
            "text": parsed.original_text, "expected_revision": project.revision,
        })
        assert response.status_code == 200, response.text
        assert parser_calls == [(project.title, parsed.original_text, project.prompt_config.entity_extraction)]
        assert parsed.model_dump(mode="json") == parser_snapshot
        saved = runtime._load_data()["p"]
        assert saved.asset_image_request_scope_id == project.asset_image_request_scope_id
        assert saved.original_text == parsed.original_text and saved.revision > project.revision
        assert_detached_copy(source, saved.characters[0], same_id=True)
        assert copies.png_bytes(runtime) == media and len(transport["calls"]) == calls
        assert_original_read_only(client, runtime, transport, body, record, reason="studio_image_request_target_changed")


def setup_package(item):
    before = headshot.setup_headshot(item)
    # Preserve the existing motion/audio metadata using real tiny media fixtures.
    video = (Path(__file__).parent / "fixtures/studio-motion-smoke/old.mp4").read_bytes()
    for ref in ("video/history.mp4", "video/face-motion-old.mp4"):
        path = item.data_dir / ref
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(video)
    audio = item.data_dir / "audio/face-voice-old.wav"
    audio.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(audio), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(8000)
        stream.writeframes(b"\x00\x00" * 80)
    item.scripts["p"].scenes = [Scene.model_validate({
        "id": "scene", "name": "Package scene", "description": "Synthetic",
    })]
    item.scripts["p"].frames = [StoryboardFrame.model_validate({
        "id": "frame", "scene_id": "scene", "character_ids": ["c"], "action_description": "A portrait",
    })]
    item._commit_storage("projects.json")
    return before


def assert_imported_headshot(item, source, copied):
    assert copied.id != source.id and copied.headshot_request_id is None
    assert copied.head_shot is not None and copied.headshot_asset is not None
    expected = {
        hashlib.sha256((item.data_dir / value.url).read_bytes()).hexdigest(): value.model_dump(mode="json", exclude={"id", "url"})
        for value in source.headshot_asset.variants
    }
    assert len(expected) == len(source.headshot_asset.variants)
    for history in (copied.head_shot.image_variants, copied.headshot_asset.variants):
        observed = {
            hashlib.sha256((item.data_dir / value.url).read_bytes()).hexdigest(): value.model_dump(mode="json", exclude={"id", "url"})
            for value in history
        }
        assert len(history) == len(expected) and observed == expected
        assert not {value.id for value in history} & {value.id for value in source.headshot_asset.variants}
    assert copied.head_shot.selected_image_id == copied.headshot_asset.selected_id
    selected = next(value for value in copied.head_shot.image_variants if value.id == copied.head_shot.selected_image_id)
    assert copied.headshot_image_url == copied.avatar_url == selected.url
    assert (item.data_dir / selected.url).read_bytes() == (item.data_dir / source.headshot_image_url).read_bytes()
    motion, old_motion = copied.head_shot.video_variants[0], source.head_shot.video_variants[0]
    assert copied.head_shot.selected_video_id == motion.id and motion.id != old_motion.id
    assert (item.data_dir / motion.url).read_bytes() == (item.data_dir / old_motion.url).read_bytes()
    assert motion.audio_url is not None and old_motion.audio_url is not None
    assert (item.data_dir / motion.audio_url).read_bytes() == (item.data_dir / old_motion.audio_url).read_bytes()
    assert motion.model_dump(exclude={"id", "url", "audio_url", "source_image_id"}) == old_motion.model_dump(
        exclude={"id", "url", "audio_url", "source_image_id"})
    unit_fields = {"image_prompt", "video_prompt", "image_updated_at", "video_updated_at"}
    assert copied.head_shot.model_dump(include=unit_fields) == source.head_shot.model_dump(include=unit_fields)
    assert (copied.headshot_prompt, copied.headshot_updated_at) == (source.headshot_prompt, source.headshot_updated_at)
    new_source = next(value for value in copied.head_shot.image_variants if value.id == motion.source_image_id)
    old_source = next(value for value in source.head_shot.image_variants if value.id == old_motion.source_image_id)
    assert (item.data_dir / new_source.url).read_bytes() == (item.data_dir / old_source.url).read_bytes()
    assert copied.is_consistent == source.is_consistent


@pytest.mark.parametrize("older_package", [False, True], ids=["export-roundtrip", "independent-older-import"])
def test_headshot_package_clears_pointer_at_each_boundary_and_preserves_media(runtime, transport, older_package):
    before = setup_package(runtime)
    with TestClient(first.app_for(runtime)) as client:
        # Keep fixture/media errors separate from a new pointer-clear RED.
        packages._read_only_export(client, runtime, transport)
        body, record, _ = generate(client, runtime, transport, "local", before)
        source = headshot.saved_owner(runtime, "local").characters[0]
        old_project = runtime._load_data()["p"].model_dump(mode="json")
        media, calls = packages._media(runtime), packages._calls(transport)
        archive = packages._read_only_export(client, runtime, transport)
        with zipfile.ZipFile(io.BytesIO(archive)) as package:
            entries = {name: package.read(name) for name in package.namelist()}
        payload = json.loads(entries["project.json"])
        for group in (payload["script"], payload["resolved_assets"]):
            character = next(value for value in group["characters"] if value["id"] == "c")
            if older_package:
                # Reintroduce the real original pointer, independently of exporter.
                character["headshot_request_id"] = record["id"]
            else:
                assert character.get("headshot_request_id") is None
                assert group["asset_image_requests"] == []
            assert character["headshot_asset"] == source.headshot_asset.model_dump(mode="json", exclude_none=True)
            assert character["head_shot"] == source.head_shot.model_dump(mode="json", exclude_none=True)
            assert character["headshot_image_url"] == character["avatar_url"] == source.headshot_image_url
        for value in source.headshot_asset.variants:
            assert entries["media/" + value.url] == media[value.url]
        if older_package:
            payload["script"]["asset_image_request_scope_id"] = old_project["asset_image_request_scope_id"]
            payload["script"]["asset_image_requests"] = [deepcopy(record)]
            entries["project.json"] = json.dumps(payload).encode()
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as package:
                for name, value in entries.items():
                    package.writestr(name, value)
            archive = buffer.getvalue()
        else:
            assert payload["script"].get("asset_image_request_scope_id") is None
            assert '"credential_ref"' not in json.dumps(payload)
        imported = client.post("/projects/package/import", content=archive, headers={"content-type": "application/zip"})
        assert imported.status_code == 200, imported.text
        copied_project = runtime._load_data()[imported.json()["id"]]
        assert copied_project.id != "p" and copied_project.asset_image_request_scope_id is None
        assert copied_project.asset_image_requests == [] and copied_project.provider_request_receipts == []
        assert_imported_headshot(runtime, source, copied_project.characters[0])
        absent = client.get(f"/projects/{copied_project.id}/asset_image_requests/{body['client_request_id']}")
        assert absent.status_code == 404
        assert runtime._load_data()["p"].model_dump(mode="json") == old_project
        assert all((runtime.data_dir / ref).read_bytes() == data for ref, data in media.items())
        assert packages._calls(transport) == calls
        assert_original_read_only(client, runtime, transport, body, record)
