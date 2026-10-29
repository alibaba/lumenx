"""Prepared frame package boundaries through real HTTP and Core execution."""

import hashlib
import io
import json
import struct
import zipfile

from fastapi.testclient import TestClient
import pytest

from src.apps.comic_gen.models import Character, ImageVariant
from tests import test_storyboard_qwen20_core as frame_core


runtime = frame_core.runtime
transport = frame_core.transport


def _calls(transport):
    return [
        (request.method, request.url, request.body, dict(request.headers))
        for request, _ in transport["calls"]
    ]


def _media(item, refs):
    return {
        ref: (item.data_dir / ref).read_bytes() if (item.data_dir / ref).exists() else None
        for ref in refs
    }


def _generate_frame(client, item, transport, *, change_prompt):
    frame_core.queue_image(transport)
    edits = []

    if change_prompt:
        def edit_while_posting(request):
            if request.method != "POST":
                return
            transport["on_send"] = None
            before = item.read_committed_script("p")
            execution = before.asset_image_requests[0].members[0].execution
            assert execution.phase == "submitting" and execution.prepared_artifact is None
            saved = item.update_frame(
                "p", "f", expected_revision=before.revision,
                image_prompt="New author text while rendering",
            )
            assert saved.revision > before.revision
            edits.append(saved.revision)

        transport["on_send"] = edit_while_posting

    admitted = client.post("/projects/p/asset_image_requests", json=frame_core.command(item))
    assert admitted.status_code == 200, admitted.text
    saved = item.read_committed_script("p")
    record = saved.asset_image_requests[0]
    assert record.id == admitted.json()["request"]["id"]
    assert (record.owner_kind, record.owner_id, record.asset_type, record.channel) == (
        "script", "p", "storyboard_frame", "image",
    )
    member = record.members[0]
    execution = member.execution
    proof = execution.prepared_artifact
    assert proof is not None
    data = (item.data_dir / execution.output_ref).read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    assert struct.unpack(">II", data[16:24]) == (16, 16)
    assert proof["sha256"] == hashlib.sha256(data).hexdigest()
    assert proof["size_bytes"] == len(data)
    assert proof["output_ref"] == proof["relative_path"] == execution.output_ref
    assert proof["attempt_id"] == execution.attempt_id == member.variant_id
    assert execution.output_ref == f"assets/studio/{member.variant_id}.png"
    assert [request.method for request, _ in transport["calls"]] == ["POST", "GET"]
    assert transport["queue"] == []
    if change_prompt:
        assert len(edits) == 1
        assert execution.phase == "submitting" and execution.artifact is None
        assert [variant.id for variant in saved.frames[0].rendered_image_asset.variants] == ["old"]
        assert saved.frames[0].rendered_image_asset.selected_id == "old"
    else:
        assert execution.phase == "completed" and execution.artifact == proof
        assert saved.frames[0].rendered_image_asset.selected_id == member.variant_id
    return record


@pytest.mark.parametrize("fault", ["same-size-corrupt", "missing-file", "id-other-url", "deleted-frame"])
def test_export_rejects_unpreservable_prepared_frame_without_changing_source(runtime, transport, fault):
    app = frame_core.http.real_app(runtime)
    with TestClient(app) as client:
        frame_core.http.enable_fake_key(app)
        record = _generate_frame(client, runtime, transport, change_prompt=True)
        member = record.members[0]
        path = runtime.data_dir / member.execution.output_ref
        if fault == "same-size-corrupt":
            # Controlled storage damage: a real prepared proof still refers to
            # this published file, but length alone cannot establish integrity.
            data = path.read_bytes()
            path.write_bytes(data[:-1] + bytes([data[-1] ^ 1]))
            assert path.stat().st_size == member.execution.prepared_artifact["size_bytes"]
        elif fault == "missing-file":
            # A storage-loss boundary, not a fake worker/proof.
            path.unlink()
        elif fault == "id-other-url":
            # Deliberate invariant violation, not claimed to be a normal UI path.
            runtime.scripts["p"].frames[0].rendered_image_asset.variants.append(
                ImageVariant.model_validate(dict(id=member.variant_id, url="uploads/old.png", prompt_used="Other history")),
            )
            runtime._commit_storage("projects.json")
        else:
            deleted = client.delete("/projects/p/frames/f")
            assert deleted.status_code == 200, deleted.text
            saved = runtime.read_committed_script("p")
            assert saved.frames == [] and saved.asset_image_requests[0].id == record.id

        databases = frame_core.persisted(runtime)
        source = runtime.read_committed_script("p").model_dump(mode="json")
        media = _media(runtime, ["uploads/old.png", member.execution.output_ref])
        calls = _calls(transport)
        exported = client.get("/projects/p/package/export")
        # Read-only assertions precede the expected first RED.
        assert frame_core.persisted(runtime) == databases
        assert runtime.read_committed_script("p").model_dump(mode="json") == source
        assert _media(runtime, media) == media and _calls(transport) == calls
        assert transport["queue"] == []
        assert exported.status_code == 422, exported.text[:500]
        assert exported.json()["code"] == "project_package_invalid"


@pytest.mark.parametrize("historical_metadata", [False, True])
def test_completed_frame_history_keeps_favorite_selection_and_existing_metadata(
    runtime, transport, historical_metadata,
):
    app = frame_core.http.real_app(runtime)
    with TestClient(app) as client:
        frame_core.http.enable_fake_key(app)
        record = _generate_frame(client, runtime, transport, change_prompt=False)
        member = record.members[0]
        favorite = client.post("/projects/p/assets/variant/favorite", json={
            "asset_id": "f", "asset_type": "storyboard_frame", "variant_id": member.variant_id,
            "is_favorited": True,
        })
        assert favorite.status_code == 200, favorite.text
        if historical_metadata:
            # Compatibility fixture: existing metadata for the same URL/bytes
            # is not evidence of a different image, and is not ours to replace.
            unit = runtime.scripts["p"].frames[0].rendered_image_asset
            historical = next(value for value in unit.variants if value.id == member.variant_id)
            historical.prompt_used = "Preserved historical description"
            historical.created_at = 12.0
            runtime._commit_storage("projects.json")
        source = runtime.read_committed_script("p")
        unit = source.frames[0].rendered_image_asset
        expected = unit.model_dump(mode="json", exclude_none=True)
        assert len(unit.variants) == 2 and unit.selected_id == member.variant_id
        assert next(value for value in unit.variants if value.id == member.variant_id).is_favorited
        databases, calls = frame_core.persisted(runtime), _calls(transport)
        media = _media(runtime, ["uploads/old.png", member.execution.output_ref])
        exported = client.get("/projects/p/package/export")
        assert exported.status_code == 200, exported.text
        assert frame_core.persisted(runtime) == databases and _calls(transport) == calls
        assert runtime.read_committed_script("p").model_dump(mode="json") == source.model_dump(mode="json")
        assert _media(runtime, media) == media and transport["queue"] == []
        with zipfile.ZipFile(io.BytesIO(exported.content)) as package:
            payload = json.loads(package.read("project.json"))["script"]
            assert payload["frames"][0]["rendered_image_asset"] == expected
            assert payload["frames"][0]["image_prompt"] == source.frames[0].image_prompt
            assert payload["frames"][0].get("image_request_id") is None
            assert payload["asset_image_requests"] == []
            data = package.read("media/" + member.execution.output_ref)
            assert data == media[member.execution.output_ref]
            assert hashlib.sha256(data).hexdigest() == member.execution.prepared_artifact["sha256"]


def test_shadowed_global_character_proof_cannot_become_same_id_local_frame_history(runtime, transport):
    runtime.library_store.characters.append(Character.model_validate(dict(id="f", name="Global original", description="library owner")))
    runtime.scripts["p"].model_settings.t2i_model = "qwen-image-2.0"
    runtime._commit_storage("projects.json", "library_assets.json")
    frame_core.queue_image(transport)
    shadowed = []

    def add_local_owner_during_post(request):
        if request.method != "POST":
            return
        transport["on_send"] = None
        pending = runtime.library_store.asset_image_requests[0]
        assert (pending.owner_kind, pending.owner_id, pending.asset_type) == ("global", "library", "character")
        assert pending.members[0].execution.phase == "submitting"
        # Controlled ownership transition through a genuine committed project;
        # the original external request/worker and global receipt stay real.
        runtime.scripts["p"].characters.append(Character.model_validate(dict(id="f", name="Local shadow", description="local owner")))
        runtime._commit_storage("projects.json")
        shadowed.append(pending.id)

    transport["on_send"] = add_local_owner_during_post
    app = frame_core.http.real_app(runtime)
    with TestClient(app) as client:
        frame_core.http.enable_fake_key(app)
        admitted = client.post("/projects/p/asset_image_requests", json={
            "client_request_id": "global-shadow-during-submit", "asset_id": "f", "asset_type": "character",
            "generation_type": "reference_sheet", "model_name": "qwen-image-2.0",
            "prompt": "Global reference sheet", "negative_prompt": "", "apply_style": False, "batch_size": 1,
        })
        assert admitted.status_code == 200, admitted.text
        assert shadowed == [admitted.json()["request"]["id"]]
        source, _, library = runtime.read_committed_project_view("p")
        assert source.asset_image_requests == []
        record = library.asset_image_requests[0]
        member = record.members[0]
        proof = member.execution.prepared_artifact
        assert proof is not None and member.execution.phase == "submitting"
        assert member.execution.artifact is None
        assert record.asset_id == source.frames[0].id == "f"
        assert source.frames[0].rendered_image_asset.selected_id == "old"
        assert [value.id for value in source.frames[0].rendered_image_asset.variants] == ["old"]
        local = next(value for value in source.characters if value.id == "f")
        assert local.name == "Local shadow" and not local.reference_sheet.image_variants
        data = (runtime.data_dir / member.execution.output_ref).read_bytes()
        assert hashlib.sha256(data).hexdigest() == proof["sha256"] and len(data) == proof["size_bytes"]
        assert [request.method for request, _ in transport["calls"]] == ["POST", "GET"]
        databases, calls = frame_core.persisted(runtime), _calls(transport)
        media = _media(runtime, ["uploads/old.png", member.execution.output_ref])
        exported = client.get("/projects/p/package/export")
        assert exported.status_code == 200, exported.text
        assert frame_core.persisted(runtime) == databases and _calls(transport) == calls
        assert _media(runtime, media) == media and transport["queue"] == []
        with zipfile.ZipFile(io.BytesIO(exported.content)) as package:
            assert "media/" + member.execution.output_ref not in package.namelist()
            payload = json.loads(package.read("project.json"))
            frame = payload["script"]["frames"][0]
            assert [value["id"] for value in frame["rendered_image_asset"]["variants"]] == ["old"]
            for group in (payload["script"], payload["resolved_assets"]):
                copied = next(value for value in group["characters"] if value["id"] == "f")
                assert copied["name"] == "Local shadow" and copied["reference_sheet"]["image_variants"] == []
                assert group["asset_image_requests"] == []
