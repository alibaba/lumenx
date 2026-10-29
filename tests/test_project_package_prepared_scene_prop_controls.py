"""Typed shared owners and conflicts around genuine image proofs.

External transport and a negative completed-write checkpoint are substituted;
actual admission/Core/proof/close/reopen/export remain real. No process-kill claim.
"""

from contextlib import contextmanager
import hashlib
import json

from fastapi.testclient import TestClient
import pytest

from src.apps.comic_gen.models import Prop, Scene, StoryboardFrame
from src.apps.comic_gen.pipeline import ComicGenPipeline
from src.models.wan27_image_contract import WAN27_IMAGE_CONTRACT_VERSION
from src.utils.json_storage import StorageError
from src.utils.runtime_environment import RuntimeEnvironment
from tests import test_project_package_prepared_headshot as archive_helpers
from tests import test_project_package_prepared_three_view as packages
from tests import test_studio_core_image_admission as admission
from tests import test_studio_core_image_api as http
from tests import test_studio_gpt_reference_sheet_core as gpt
from tests import test_studio_scene_prop_service as images
from tests import test_studio_three_view_boundaries as boundaries
from tests.frozen_image_fixtures import rgb_png_bytes


runtime = admission.runtime
transport = gpt.transport


def _snapshot(item, state):
    return packages._databases(item), packages._media(item), packages._calls(state)


def _export(client, item, state):
    archive = packages._read_only_export(client, item, state)
    entries, payload, manifest = archive_helpers._read_archive(archive)
    archive_helpers._assert_manifest_bytes(entries, manifest, item)
    return entries, payload


@contextmanager
def _prepared(item, state, monkeypatch, kind, route="local"):
    images.setup(item, kind, route, selected=True)
    path = item.data_dir / "uploads/old.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(rgb_png_bytes("original " + kind, width=16, height=16))
    monkeypatch.setattr("src.models.wan27_image_http.time.sleep", lambda _seconds: None)
    images.queue_success(state, 1)
    key = f"package-control-{kind}-{route}"
    original_commit = item._storage.commit
    failures = []

    def reject_completion(payload):
        matches = [value for value in packages._saved_records(payload, route) if value["client_request_id"] == key]
        if matches and matches[0]["members"][0]["execution"]["phase"] == "completed":
            failures.append(matches[0]["members"][0]["execution"]["attempt_id"])
            raise StorageError("Synthetic prepared ownership completion checkpoint interruption")
        return original_commit(payload)

    with TestClient(http.real_app(item), raise_server_exceptions=False) as client:
        # create_app(load_config=False) has its own environment; configure it.
        http.enable_fake_key(client.app)
        packages._read_only_export(client, item, state)
        assert not state["calls"] and len(state["queue"]) == 3
        monkeypatch.setattr(item._storage, "commit", reject_completion)
        accepted = client.post("/projects/p/asset_image_requests", json={
            "client_request_id": key, "asset_id": "c", "asset_type": kind,
            "generation_type": "all", "apply_style": False, "batch_size": 1,
            "prompt": "Prepared ownership control " + kind,
        })
        assert accepted.status_code == 200, accepted.text
        assert [value["status"] for value in accepted.json()["request"]["members"]] == ["pending"]
        disk = {name: json.loads(content) for name, content in packages._databases(item).items()}
        saved = packages._saved_records(disk, route)
        assert len(saved) == 1 and saved[0]["id"] == accepted.json()["request"]["id"]
        execution = saved[0]["members"][0]["execution"]
        assert execution["schema_version"] == 3 and execution["phase"] == "observed"
        assert execution["artifact"] is None
        handle = execution["handle"]
        assert handle is not None and handle["task_id"] == images.http.task()["output"]["task_id"]
        assert handle["scope"] == execution["scope"]
        assert handle["query_contract"] == "dashscope-wan27-image-task-v1"
        observation = execution["observation"]
        assert observation is not None and observation["status"] == "succeeded"
        assert observation["source_handle"] == handle
        original_bytes = (item.data_dir / execution["output_ref"]).read_bytes()
        assert original_bytes
        assert execution["prepared_artifact"] == {
            "schema_version": 3, "contract_version": "provider-async-image-artifact-v1",
            "attempt_id": execution["attempt_id"], "handle": handle, "media_kind": "image",
            "relative_path": execution["output_ref"], "output_ref": execution["output_ref"],
            "sha256": hashlib.sha256(original_bytes).hexdigest(), "size_bytes": len(original_bytes),
        }
        assert failures == [execution["attempt_id"]]
        assert [request.method for request, _ in state["calls"]] == ["POST", "GET", "GET"]
        assert json.loads(state["calls"][0][0].body)["model"] == "wan2.7-image"
        assert not state["queue"] and not state["cli"] and not state["cli_queue"]
    item.close()
    before_open = _snapshot(item, state)
    restored = ComicGenPipeline(
        data_dir=item.data_dir, runtime_environment=RuntimeEnvironment({}),
        recover_orphan_tasks=False, repair_series_bindings=False,
    )
    try:
        with TestClient(http.real_app(restored)) as client:
            assert _snapshot(restored, state) == before_open
            assert not restored.asset_generation_tasks and not getattr(restored, "_asset_image_runs", {})
            owner = admission.owner(restored, route)
            record = owner.asset_image_requests[0]
            assert record.model_dump(mode="json") == saved[0]
            assert (record.entrypoint, record.asset_type, record.channel) == ("studio.asset.scene_prop.v1", kind, "image")
            assert (record.owner_kind, record.owner_id) == {
                "local": ("script", "p"), "series": ("series", "s"), "global": ("global", "library"),
            }[route]
            assert (record.request_root_kind, record.request_root_id) == ("project", "p")
            member = record.members[0]
            proof, ref = member.execution.prepared_artifact, member.execution.output_ref
            assert proof is not None and ref is not None
            assert member.execution.intent["intent_contract_version"] == WAN27_IMAGE_CONTRACT_VERSION
            data = (restored.data_dir / ref).read_bytes()
            assert data == original_bytes
            assert (hashlib.sha256(data).hexdigest(), len(data)) == (proof["sha256"], proof["size_bytes"])
            target = getattr(owner, kind + "s")[0]
            assert target.image_asset is not None and target.image_request_id == record.id
            assert target.image_asset.selected_id == "old"
            assert member.variant_id not in {value.id for value in target.image_asset.variants}
            yield client, restored, record
            assert admission.owner(restored, route).asset_image_requests[0].model_dump(mode="json") == saved[0]
            assert (restored.data_dir / ref).read_bytes() == data
            assert packages._calls(state) == before_open[2]
            assert not state["queue"] and not state["cli_queue"]
    finally:
        restored.close()


@pytest.mark.parametrize("kind,route", (
    pytest.param("scene", "global", id="scene-global-local-shadow"),
    pytest.param("prop", "series", id="prop-series-local-shadow"),
))
def test_shared_prepared_image_obeys_closure_and_typed_higher_owner(runtime, transport, monkeypatch, kind, route):
    with _prepared(runtime, transport, monkeypatch, kind, route) as (client, item, record):
        member = record.members[0]
        ref = member.execution.output_ref
        assert ref is not None
        lower = getattr(admission.owner(item, route), kind + "s")[0]
        original_lower = lower.model_dump(mode="json")
        # Shared inventory alone does not make an asset part of this project.
        entries, payload = _export(client, item, transport)
        assert payload["script"][kind + "s"] == payload["resolved_assets"][kind + "s"] == []
        assert "media/" + ref not in entries and "media/uploads/old.png" not in entries

        if kind == "prop":
            item.scripts["p"].scenes = [Scene.model_validate({
                "id": "carrier-scene", "name": "Carrier scene", "description": "Frame owner",
            })]
        item.scripts["p"].frames = [StoryboardFrame.model_validate({
            "id": "frame", "scene_id": "c" if kind == "scene" else "carrier-scene",
            "character_ids": [], "prop_ids": ["c"] if kind == "prop" else [],
            "action_description": "Reference the existing shared asset",
        })]
        boundaries.commit_owners(item)
        # The very same genuine record is carried once its actual owner is needed.
        entries, payload = _export(client, item, transport)
        assert entries["media/" + ref] == (item.data_dir / ref).read_bytes()
        for group in (payload["script"], payload["resolved_assets"]):
            target = next(value for value in group[kind + "s"] if value["id"] == "c")
            assert target["name"] == lower.name and target.get("image_request_id") is None
            assert target["image_asset"]["selected_id"] == "old"
            variants = target["image_asset"]["variants"]
            assert {value["id"] for value in variants} == {"old", member.variant_id}
            assert next(value for value in variants if value["id"] == member.variant_id)["url"] == ref

        higher_ref = f"uploads/higher-{kind}.png"
        (item.data_dir / higher_ref).write_bytes(rgb_png_bytes("higher " + kind, width=16, height=16))
        model = Scene if kind == "scene" else Prop
        higher = model.model_validate({
            "id": "c", "name": "Higher independent " + kind, "description": "Keep the new owner's authored asset",
            "image_url": higher_ref, "video_prompt": "Higher owner motion",
            "image_asset": {"selected_id": "higher", "variants": [
                {"id": "higher", "url": higher_ref, "is_favorited": True, "created_at": 2},
            ]},
        })
        getattr(item.scripts["p"], kind + "s").append(higher)
        boundaries.commit_owners(item)
        source, series, library = item.read_committed_project_view("p")
        assert source is not None
        visible = item._resolve_asset_layers(source, series, library)[kind + "s"]
        assert len(visible) == 1 and visible[0].name == higher.name
        entries, payload = _export(client, item, transport)
        for group in (payload["script"], payload["resolved_assets"]):
            assert group[kind + "s"] == [higher.model_dump(mode="json", exclude_none=True)]
        assert entries["media/" + higher_ref] == (item.data_dir / higher_ref).read_bytes()
        assert "media/" + ref not in entries and "media/uploads/old.png" not in entries
        assert getattr(admission.owner(item, route), kind + "s")[0].model_dump(mode="json") == original_lower


def test_prepared_scene_rejects_duplicate_visible_targets_before_any_merge(runtime, transport, monkeypatch):
    with _prepared(runtime, transport, monkeypatch, "scene") as (client, item, _record):
        duplicate = item.scripts["p"].scenes[0].model_copy(deep=True)
        duplicate.name = "Another authored scene with the same identity"
        item.scripts["p"].scenes.append(duplicate)
        boundaries.commit_owners(item)
        committed = item.read_committed_script("p")
        assert committed is not None and len(committed.scenes) == 2
        assert [value.id for value in committed.scenes] == ["c", "c"]
        before = _snapshot(item, transport)
        rejected = client.get("/projects/p/package/export")
        assert rejected.status_code == 422, rejected.text
        assert rejected.json() == {
            "code": "project_package_invalid", "detail": "Prepared scene image has no unique packaged target",
        }
        assert _snapshot(item, transport) == before


def test_prepared_prop_rejects_same_variant_id_with_different_managed_url(runtime, transport, monkeypatch):
    with _prepared(runtime, transport, monkeypatch, "prop") as (client, item, record):
        target = item.scripts["p"].props[0]
        assert target.image_asset is not None
        member = record.members[0]
        old = target.image_asset.variants[0]
        old.id = member.variant_id
        target.image_asset.selected_id = old.id
        assert old.url == target.image_url == "uploads/old.png"
        assert old.url != member.execution.output_ref
        boundaries.commit_owners(item)
        committed = item.read_committed_script("p")
        assert committed is not None and committed.props[0].image_asset is not None
        assert committed.props[0].image_asset.selected_id == member.variant_id
        before = _snapshot(item, transport)
        rejected = client.get("/projects/p/package/export")
        assert rejected.status_code == 422, rejected.text
        assert rejected.json() == {
            "code": "project_package_invalid", "detail": "Variant identity refers to different media",
        }
        assert _snapshot(item, transport) == before
