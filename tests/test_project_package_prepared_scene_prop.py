"""Unprojected real prepared images survive independent copies.

Only external transport and a negative final storage checkpoint are substituted.
Close/new app is real; this is not a process-kill or real-provider test.
"""

from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import zipfile

from fastapi.testclient import TestClient
import pytest

from src.apps.comic_gen.pipeline import ComicGenPipeline
from src.utils.json_storage import StorageError
from src.utils.runtime_environment import RuntimeEnvironment
from tests import test_project_package_prepared_headshot as archive_helpers
from tests import test_project_package_prepared_three_view as packages
from tests import test_qwen20_studio_image as qwen
from tests import test_studio_core_image_admission as admission
from tests import test_studio_core_image_api as http
from tests import test_studio_full_body_core as bodies
from tests import test_studio_gpt_reference_sheet_core as gpt
from tests import test_studio_scene_prop_service as images
from tests.frozen_image_fixtures import rgb_png_bytes


runtime = admission.runtime
transport = gpt.transport
KEY = "package-prepared-existing-image"
CASES = (
    pytest.param("scene", "wan2.7-image", id="wan-scene"),
    pytest.param("prop", "wan2.7-image", id="wan-prop"),
    pytest.param("scene", "qwen-image-2.0", id="qwen-scene"),
    pytest.param("prop", "qwen-image-2.0", id="qwen-prop"),
    pytest.param("full_body", "wan2.7-image", id="wan-full-body-control"),
    pytest.param("full_body", "gpt-image-2", id="gpt-full-body"),
    pytest.param("scene", "gpt-image-2", id="gpt-scene"),
    pytest.param("prop", "gpt-image-2", id="gpt-prop"),
)


def _histories(target, kind):
    if kind == "full_body":
        return target["full_body_asset"]["variants"], target["full_body"]["image_variants"]
    return (target["image_asset"]["variants"],)


def _selected(target, kind):
    if kind == "full_body":
        return target["full_body_asset"]["selected_id"], target["full_body"]["selected_image_id"]
    return (target["image_asset"]["selected_id"],)


def _no_authority(group):
    assert group["asset_image_requests"] == []
    assert group.get("asset_image_request_scope_id") is None
    assert group.get("provider_request_receipts", []) == []
    for target in group["characters"]:
        assert all(target.get(field) is None for field in archive_helpers.POINTERS)
    for target in [*group["scenes"], *group["props"]]:
        assert target.get("image_request_id") is None


@pytest.mark.parametrize("kind,model", CASES)
def test_existing_prepared_images_export_import_reexport_without_projection(
    runtime, transport, monkeypatch, kind, model,
):
    # The shared transport replaces only external I/O and supplies the Studio
    # polling clock; the Wan request/parser/normalizer remain real.
    if kind == "full_body":
        _, _, target = bodies.setup(runtime)
        refs = ("uploads/body.png", "uploads/sheet.png")
        video = runtime.data_dir / "video/motion.mp4"
        video.parent.mkdir(parents=True, exist_ok=True)
        video.write_bytes((Path(__file__).parent / "fixtures/studio-motion-smoke/old.mp4").read_bytes())
    else:
        _, target = images.setup(runtime, kind, selected=True)
        refs = ("uploads/old.png",)
    for ref in refs:
        path = runtime.data_dir / ref
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(rgb_png_bytes("original " + ref, width=16, height=16))
    runtime.scripts["p"].model_settings.t2i_model = model
    runtime._commit_storage("projects.json", "series.json", "library_assets.json")
    collection = "characters" if kind == "full_body" else kind + "s"
    pointer = "full_body_request_id" if kind == "full_body" else "image_request_id"
    body = {
        "client_request_id": KEY, "asset_id": "c",
        "asset_type": "character" if kind == "full_body" else kind,
        "generation_type": "full_body" if kind == "full_body" else "all",
        "prompt": "Preserve this prepared image", "apply_style": False, "batch_size": 1,
    }
    if model.startswith("qwen"):
        qwen.queue_images(transport)
    elif model.startswith("gpt-image"):
        gpt.queue_images(transport, "http", count=1)
    else:
        images.queue_success(transport, 1)
    original_commit = runtime._storage.commit
    failures = []

    def reject_final_projection(payload):
        saved = payload.get("projects.json", {}).get("p", {}).get("asset_image_requests", [])
        matching = [row for row in saved if row["client_request_id"] == KEY]
        if matching and matching[0]["members"][0]["execution"]["phase"] == "completed":
            failures.append(matching[0]["members"][0]["execution"]["attempt_id"])
            raise StorageError("Synthetic image final projection checkpoint interruption")
        return original_commit(payload)

    with TestClient(http.real_app(runtime), raise_server_exceptions=False) as client:
        if model.startswith("gpt-image"):
            gpt.configure(client.app, "http")
        else:
            http.enable_fake_key(client.app)
        packages._read_only_export(client, runtime, transport)
        assert not transport["calls"] and not transport["cli"]
        monkeypatch.setattr(runtime._storage, "commit", reject_final_projection)
        accepted = client.post("/projects/p/asset_image_requests", json=body)
        assert accepted.status_code == 200, accepted.text
        assert [row["status"] for row in accepted.json()["request"]["members"]] == ["pending"]
        disk = json.loads((runtime.data_dir / "projects.json").read_bytes())["p"]
        assert len(disk["asset_image_requests"]) == 1
        original_record = disk["asset_image_requests"][0]
        execution = original_record["members"][0]["execution"]
        assert original_record["id"] == accepted.json()["request"]["id"]
        expected_schema, expected_phase = (2, "submitting") if model.startswith("qwen") else (3, "observed")
        assert (execution["schema_version"], execution["phase"]) == (expected_schema, expected_phase)
        assert execution["artifact"] is None
        if expected_schema == 3:
            assert execution["handle"] is not None and execution["observation"]["status"] == "succeeded"
        assert execution["prepared_artifact"] is not None
        assert failures == [execution["attempt_id"]]
        assert sum(request.method == "POST" for request, _ in transport["calls"]) == 1
        assert not transport["queue"] and not transport["cli"] and not transport["cli_queue"]
    runtime.close()
    before_open = packages._databases(runtime), packages._media(runtime), packages._calls(transport)
    restored = ComicGenPipeline(
        data_dir=runtime.data_dir, runtime_environment=RuntimeEnvironment({}),
        recover_orphan_tasks=False, repair_series_bindings=False,
    )
    try:
        with TestClient(http.real_app(restored)) as client:
            assert (packages._databases(restored), packages._media(restored), packages._calls(transport)) == before_open
            assert not restored.asset_generation_tasks and not getattr(restored, "_asset_image_runs", {})
            record = restored.scripts["p"].asset_image_requests[0]
            assert record.model_dump(mode="json") == original_record
            assert (record.owner_kind, record.owner_id, record.request_root_kind, record.request_root_id) == (
                "script", "p", "project", "p",
            )
            assert (record.asset_type, record.channel) == (body["asset_type"], "full_body" if kind == "full_body" else "image")
            member = record.members[0]
            ref, proof = member.execution.output_ref, member.execution.prepared_artifact
            assert ref is not None and proof is not None
            data = (restored.data_dir / ref).read_bytes()
            assert (hashlib.sha256(data).hexdigest(), len(data)) == (proof["sha256"], proof["size_bytes"])
            saved_target = getattr(restored.scripts["p"], collection)[0].model_dump(mode="json", exclude_none=True)
            assert saved_target[pointer] == record.id
            assert all(member.variant_id not in {value["id"] for value in history} for history in _histories(saved_target, kind))
            expected = deepcopy(saved_target)
            for field in (*archive_helpers.POINTERS, "image_request_id"):
                expected.pop(field, None)
            if expected["status"] == "processing":
                expected["status"] = "needs_confirmation"
            for history in _histories(expected, kind):
                history.append({
                    "id": member.variant_id, "url": ref, "created_at": record.created_at,
                    "prompt_used": member.execution.intent["prompt"],
                    "is_favorited": False, "is_uploaded_source": False,
                })
            old_selection = _selected(saved_target, kind)
            databases, media, calls = packages._databases(restored), packages._media(restored), packages._calls(transport)
            old_projects = json.loads(databases["projects.json"])
            archive = packages._read_only_export(client, restored, transport)
            entries, payload, manifest = archive_helpers._read_archive(archive)
            # Intended RED: the proven file is not reachable after receipt stripping.
            assert "media/" + ref in entries, "Proven scene/prop PNG omitted from project package"
            assert entries["media/" + ref] == data
            archive_helpers._assert_manifest_bytes(entries, manifest, restored)
            for group in (payload["script"], payload["resolved_assets"]):
                _no_authority(group)
                exported = next(value for value in group[collection] if value["id"] == "c")
                assert exported == expected
                assert _selected(exported, kind) == old_selection
            assert '"prepared_artifact"' not in json.dumps(payload)
            assert '"credential_ref"' not in json.dumps(payload)
            # Old-format load path: insert only the ORIGINAL actual saved receipt.
            # prepare_import must validate it before clearing copied execution.
            payload["script"]["asset_image_requests"] = [deepcopy(original_record)]
            payload["script"]["asset_image_request_scope_id"] = record.request_root_scope_id
            next(value for value in payload["script"][collection] if value["id"] == "c")[pointer] = record.id
            entries["project.json"] = json.dumps(payload).encode()
            stream = io.BytesIO()
            with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_STORED) as packed:
                for name, content in entries.items():
                    packed.writestr(name, content)
            imported = client.post("/projects/package/import", content=stream.getvalue(), headers={"content-type": "application/zip"})
            assert imported.status_code == 200, imported.text
            copied_id = imported.json()["id"]
            assert copied_id not in old_projects
            copied = restored.read_committed_script(copied_id)
            assert copied is not None and copied.series_id is None
            _no_authority(copied.model_dump(mode="json"))
            copied_target = getattr(copied, collection)[0].model_dump(mode="json", exclude_none=True)
            assert copied_target["id"] != "c"
            for history, selected in zip(_histories(copied_target, kind), _selected(copied_target, kind), strict=True):
                paid = [value for value in history if hashlib.sha256((restored.data_dir / value["url"]).read_bytes()).hexdigest() == proof["sha256"]]
                assert len(paid) == 1 and paid[0]["id"] != member.variant_id
                assert paid[0]["id"] != selected
                old = next(value for value in history if value["id"] == selected)
                assert old["is_favorited"] is True
                assert (restored.data_dir / old["url"]).read_bytes() == media["uploads/body.png" if kind == "full_body" else "uploads/old.png"]
            absent = client.get(f"/projects/{copied_id}/asset_image_requests/{KEY}")
            assert absent.status_code == 404
            reexport = packages._read_only_export(client, restored, transport, copied_id)
            copied_entries, copied_payload, copied_manifest = archive_helpers._read_archive(reexport)
            archive_helpers._assert_manifest_bytes(copied_entries, copied_manifest, restored)
            assert any(row["sha256"] == proof["sha256"] and copied_entries[row["package_path"]] == data for row in copied_manifest["media"])
            for group in (copied_payload["script"], copied_payload["resolved_assets"]):
                _no_authority(group)
            current = packages._databases(restored)
            assert all(current[name] == content for name, content in databases.items() if name != "projects.json")
            assert all(json.loads(current["projects.json"])[identity] == value for identity, value in old_projects.items())
            assert all((restored.data_dir / old_ref).read_bytes() == content for old_ref, content in media.items())
            assert restored.scripts["p"].asset_image_requests[0].model_dump(mode="json") == original_record
            assert packages._calls(transport) == calls and not transport["queue"] and not transport["cli_queue"]
    finally:
        restored.close()
