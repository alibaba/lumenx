"""Carry proven full-body images without copying execution authority.

Use the real API, Core, prepared proof, PNG decoder, negative final checkpoint,
new empty-credential app and public package routes. Only the external provider
transport is replaced. This is not a process-kill or real-provider acceptance.
"""

import base64
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from fastapi.testclient import TestClient

from src.apps.comic_gen.models import Character, Scene, StoryboardFrame
from src.apps.comic_gen.pipeline import ComicGenPipeline
from src.utils.json_storage import StorageError
from src.utils.runtime_environment import RuntimeEnvironment
from tests import test_project_package_prepared_headshot as portraits
from tests import test_project_package_prepared_three_view as packages
from tests import test_studio_headshot_core as headshot
from tests import test_studio_headshot_copy as copies
from tests import test_studio_qwen20_full_body as old_qwen
from tests import test_studio_three_view_boundaries as boundaries


runtime = headshot.runtime
transport = headshot.transport
first = headshot.three_view
gpt_sheet = headshot.gpt_sheet
KEY = "package-prepared-full-body-global"
PROMPT = "Keep the red coat and boots; show the complete standing character."
REFERENCE_ID = "three-first"
REFERENCE_REF = "uploads/three-upload-first.png"
CONTRACT = "provider-gpt-image2-edit-bound-v2"


@contextmanager
def _prepared_global_and_reopen(item, state, monkeypatch):
    # Reuse only the existing media/owner setup, never its headshot request.
    _, historical = portraits._setup_global(item)
    reference = (item.data_dir / REFERENCE_REF).read_bytes()
    assert reference != (item.data_dir / first.SELECTED_REF).read_bytes()
    gpt_sheet.queue_images(state, "http", count=1)
    original_commit = item._storage.commit
    failures = []

    def reject_final_projection(payload):
        records = packages._saved_records(payload, "global")
        matching = [value for value in records if value["client_request_id"] == KEY]
        if matching and matching[0]["members"][0]["execution"]["phase"] == "completed":
            failures.append(matching[0]["members"][0]["execution"]["attempt_id"])
            raise StorageError("Synthetic full-body final projection checkpoint interruption")
        return original_commit(payload)

    body = {
        "client_request_id": KEY, "asset_id": "c", "asset_type": "character",
        "generation_type": "full_body", "reference_variant_id": REFERENCE_ID,
        "reference_image_url": REFERENCE_REF, "apply_style": False,
        "prompt": PROMPT, "batch_size": 1,
    }
    app = first.app_for(item)
    with TestClient(app, raise_server_exceptions=False) as client:
        gpt_sheet.configure(app, "http")
        # Missing fixture media or unsupported admission is not a package RED.
        packages._read_only_export(client, item, state)
        assert not state["calls"] and len(state["queue"]) == 3
        monkeypatch.setattr(item._storage, "commit", reject_final_projection)
        admitted = client.post(first.PUBLIC, json=body)
        assert admitted.status_code == 200, admitted.text
        assert [row["status"] for row in admitted.json()["request"]["members"]] == ["pending"]
        assert len(failures) == 1
        disk = {name: json.loads(data) for name, data in packages._databases(item).items()}
        saved = packages._saved_records(disk, "global")
        assert len(saved) == 1 and saved[0]["id"] == admitted.json()["request"]["id"]
        execution = saved[0]["members"][0]["execution"]
        assert execution["schema_version"] == 3
        assert execution["phase"] == "observed"
        assert execution["handle"] is not None and execution["observation"]["status"] == "succeeded"
        assert execution["prepared_artifact"] is not None and execution["artifact"] is None
        assert failures == [execution["attempt_id"]]
        assert [(request.method, request.url) for request, _ in state["calls"]] == [
            ("POST", first.EDIT), ("GET", first.EDIT + "/" + gpt_sheet.http_task_id()), ("GET", gpt_sheet.IMAGE_URL),
        ]
        assert json.loads(state["calls"][0][0].body) == {
            "prompt": execution["intent"]["prompt"], "size": "1024x1536", "n": 1,
            "images": ["data:image/png;base64," + base64.b64encode(reference).decode()],
        }
        assert not state["queue"] and not state["cli_queue"] and not state["cli"]
    item.close()
    before_restart = packages._databases(item), packages._media(item), packages._calls(state)
    restored = ComicGenPipeline(
        data_dir=item.data_dir, runtime_environment=RuntimeEnvironment({}),
        recover_orphan_tasks=False, repair_series_bindings=False,
    )
    try:
        with TestClient(first.app_for(restored)) as client:
            assert (packages._databases(restored), packages._media(restored), packages._calls(state)) == before_restart
            assert not restored.asset_generation_tasks and not getattr(restored, "_asset_image_runs", {})
            owner = headshot.admission.owner(restored, "global")
            assert len(owner.asset_image_requests) == 1
            record = owner.asset_image_requests[0]
            assert record.model_dump(mode="json") == saved[0]
            assert (record.entrypoint, record.asset_type, record.channel) == (
                "studio.asset.full_body.v1", "character", "full_body",
            )
            assert (record.request_root_kind, record.request_root_id, record.owner_kind, record.owner_id) == (
                "project", "p", "global", "library",
            )
            assert record.base_prompt == PROMPT and len(record.members) == 1
            assert not restored.scripts["p"].asset_image_requests
            assert not restored.series_store["s"].asset_image_requests
            member = record.members[0]
            execution = member.execution
            assert execution.schema_version == 3
            assert execution.phase == "observed" and execution.artifact is None
            assert execution.handle is not None and execution.observation["status"] == "succeeded"
            assert execution.prepared_artifact is not None and execution.output_ref is not None
            assert execution.intent["intent_contract_version"] == CONTRACT
            assert execution.intent["model_id"] == first.MODELS[1] and execution.intent["mode"] == "i2i"
            assert execution.intent["params"] == {"size": "1024x1536"}
            assert len(execution.media) == 1
            assert execution.media[0]["sha256"] == hashlib.sha256(reference).hexdigest()
            boundaries.prove_paid_file(restored, execution)
            headshot.assert_provider_image_pixels(restored.data_dir / execution.output_ref)
            target = owner.characters[0]
            assert target.full_body_request_id == record.id
            assert target.full_body_asset is not None and target.full_body is not None
            assert historical.full_body_asset is not None and historical.full_body is not None
            assert target.full_body_asset.model_dump() == historical.full_body_asset.model_dump()
            assert target.full_body.model_dump() == historical.full_body.model_dump()
            assert target.full_body_image_url == historical.full_body_image_url
            assert all(member.variant_id not in {value.id for value in history} for history in (
                target.full_body_asset.variants, target.full_body.image_variants,
            ))
            snapshot = packages._databases(restored), packages._media(restored), packages._calls(state)
            observed = client.get(first.PUBLIC + "/" + KEY)
            assert observed.status_code == 200 and observed.json()["request"]["id"] == record.id
            assert observed.json()["request"]["members"][0]["status"] == "needs_confirmation"
            assert (packages._databases(restored), packages._media(restored), packages._calls(state)) == snapshot
            yield client, restored, record
    finally:
        restored.close()


def _assert_image_history(item, source, copied):
    def by_bytes(history):
        result = {
            hashlib.sha256((item.data_dir / value.url).read_bytes()).hexdigest(): value.model_dump(
                mode="json", exclude={"id", "url"},
            ) for value in history
        }
        assert len(result) == len(history)
        return result

    assert by_bytes(copied) == by_bytes(source)
    assert not {value.id for value in copied} & {value.id for value in source}


def _assert_imported_full_body(item, source, copied):
    assert copied.id != source.id and copied.full_body_request_id is None
    assert source.full_body_asset is not None and source.full_body is not None
    assert copied.full_body_asset is not None and copied.full_body is not None
    _assert_image_history(item, source.full_body_asset.variants, copied.full_body_asset.variants)
    _assert_image_history(item, source.full_body.image_variants, copied.full_body.image_variants)
    assert copied.full_body.selected_image_id == copied.full_body_asset.selected_id
    selected = next(value for value in copied.full_body.image_variants if value.id == copied.full_body.selected_image_id)
    old_selected = next(value for value in source.full_body.image_variants if value.id == source.full_body.selected_image_id)
    assert (item.data_dir / selected.url).read_bytes() == (item.data_dir / old_selected.url).read_bytes()
    # This fixture intentionally has an older legacy alias A and selected C.
    # Carry both faithfully rather than replacing the alias during export.
    assert source.full_body_image_url is not None and copied.full_body_image_url is not None
    assert (item.data_dir / copied.full_body_image_url).read_bytes() == (item.data_dir / source.full_body_image_url).read_bytes()
    motion, old_motion = copied.full_body.video_variants[0], source.full_body.video_variants[0]
    assert copied.full_body.selected_video_id == motion.id and motion.id != old_motion.id
    assert (item.data_dir / motion.url).read_bytes() == (item.data_dir / old_motion.url).read_bytes()
    assert motion.model_dump(exclude={"id", "url", "source_image_id"}) == old_motion.model_dump(
        exclude={"id", "url", "source_image_id"},
    )
    new_source = next(value for value in copied.full_body.image_variants if value.id == motion.source_image_id)
    old_source = next(value for value in source.full_body.image_variants if value.id == old_motion.source_image_id)
    assert (item.data_dir / new_source.url).read_bytes() == (item.data_dir / old_source.url).read_bytes()
    unit_fields = {"image_prompt", "video_prompt", "image_updated_at", "video_updated_at"}
    assert copied.full_body.model_dump(include=unit_fields) == source.full_body.model_dump(include=unit_fields)
    assert (copied.full_body_prompt, copied.full_body_updated_at) == (source.full_body_prompt, source.full_body_updated_at)
    copies.assert_imported_headshot(item, source, copied)
    assert source.three_views is not None and copied.three_views is not None
    assert source.three_view_asset is not None and copied.three_view_asset is not None
    assert source.reference_sheet is not None and copied.reference_sheet is not None
    _assert_image_history(item, source.three_views.image_variants, copied.three_views.image_variants)
    _assert_image_history(item, source.three_view_asset.variants, copied.three_view_asset.variants)
    _assert_image_history(item, source.reference_sheet.image_variants, copied.reference_sheet.image_variants)


def _roundtrip(client, item, state, target, record, *, carry_prepared, excluded=()):
    databases, media, calls = packages._databases(item), packages._media(item), packages._calls(state)
    projects = json.loads(databases["projects.json"])
    assert target.full_body_asset is not None and target.full_body is not None
    expected = deepcopy(target.model_dump(mode="json", exclude_none=True))
    for field in portraits.POINTERS:
        expected.pop(field, None)
    if expected["status"] == "processing":
        expected["status"] = "needs_confirmation"
    member = record.members[0]
    proof, ref = member.execution.prepared_artifact, member.execution.output_ref
    assert proof is not None and ref is not None
    data = media[ref]
    assert (hashlib.sha256(data).hexdigest(), len(data)) == (proof["sha256"], proof["size_bytes"])
    if carry_prepared:
        variant = {
            "id": member.variant_id, "url": ref, "created_at": record.created_at,
            "prompt_used": member.execution.intent["prompt"], "is_favorited": False, "is_uploaded_source": False,
        }
        expected["full_body_asset"]["variants"].append(deepcopy(variant))
        expected["full_body"]["image_variants"].append(deepcopy(variant))

    def unchanged():
        current = packages._databases(item)
        assert current.keys() == databases.keys()
        assert all(current[name] == data for name, data in databases.items() if name != "projects.json")
        current_projects = json.loads(current["projects.json"])
        assert all(current_projects[identity] == value for identity, value in projects.items())
        assert all((item.data_dir / old_ref).read_bytes() == old_data for old_ref, old_data in media.items())
        assert packages._calls(state) == calls and not state["queue"] and not state["cli_queue"] and not state["cli"]
        assert headshot.saved_owner(item, "global").asset_image_requests[0].model_dump(mode="json") == record.model_dump(mode="json")

    archive = packages._read_only_export(client, item, state)
    unchanged()
    entries, payload, manifest = portraits._read_archive(archive)
    if carry_prepared:
        # Expected target RED: the real Core PNG is omitted by the old allowlist.
        assert "media/" + ref in entries, "Proven full-body PNG omitted from project package"
        assert entries["media/" + ref] == data
    assert all("media/" + old_ref not in entries for old_ref in excluded)
    portraits._assert_manifest_bytes(entries, manifest, item)
    for group in (payload["script"], payload["resolved_assets"]):
        portraits._assert_no_authority(group)
        assert next(value for value in group["characters"] if value["id"] == "c") == expected
    assert '"credential_ref"' not in json.dumps(payload) and '"prepared_artifact"' not in json.dumps(payload)
    exported_target = Character.model_validate(expected)

    imported = client.post("/projects/package/import", content=archive, headers={"content-type": "application/zip"})
    assert imported.status_code == 200, imported.text
    copied_id = imported.json()["id"]
    assert copied_id not in projects
    unchanged()
    copied = item.read_committed_script(copied_id)
    assert copied is not None and copied.series_id is None
    assert copied.asset_image_requests == [] and copied.provider_request_receipts == [] and copied.video_tasks == []
    assert len(copied.characters) == 1
    character = copied.characters[0]
    assert character.full_body_asset is not None
    assert copied.frames[0].id != "frame" and copied.frames[0].scene_id != "scene"
    assert copied.frames[0].character_ids == [character.id]
    portraits._assert_no_authority(copied.model_dump(mode="json"))
    _assert_imported_full_body(item, exported_target, character)
    if carry_prepared:
        matching = [value for value in character.full_body_asset.variants
                    if hashlib.sha256((item.data_dir / value.url).read_bytes()).hexdigest() == proof["sha256"]]
        assert len(matching) == 1 and matching[0].id != member.variant_id
        assert matching[0].id != character.full_body_asset.selected_id
        headshot.assert_provider_image_pixels(item.data_dir / matching[0].url)
    assert client.get(f"/projects/{copied_id}/asset_image_requests/{KEY}").status_code == 404

    reexported = packages._read_only_export(client, item, state, copied_id)
    entries, payload, manifest = portraits._read_archive(reexported)
    portraits._assert_manifest_bytes(entries, manifest, item)
    for variant in character.full_body_asset.variants:
        assert entries["media/" + variant.url] == (item.data_dir / variant.url).read_bytes()
    assert all("media/" + old_ref not in entries for old_ref in excluded)
    for group in (payload["script"], payload["resolved_assets"]):
        portraits._assert_no_authority(group)
        assert next(value for value in group["characters"] if value["id"] == character.id) == character.model_dump(
            mode="json", exclude_none=True,
        )
    assert '"credential_ref"' not in json.dumps(payload) and '"prepared_artifact"' not in json.dumps(payload)
    unchanged()


def _higher_local_character(item, lower):
    variants = []
    for index in range(2):
        ref = f"uploads/higher-body-{index}.png"
        (item.data_dir / ref).write_bytes(headshot.rgb_png_bytes(f"higher body {index}", width=16, height=16))
        variants.append({
            "id": f"higher-body-{index}", "url": ref, "created_at": index + 1,
            "prompt_used": f"Higher body {index}", "is_favorited": index == 0,
            "is_uploaded_source": True, "upload_type": "full_body",
        })
    assert lower.full_body is not None
    visible = lower.model_dump(mode="json")
    for field in portraits.POINTERS:
        visible[field] = None
    motion = lower.full_body.video_variants[0].model_dump(mode="json")
    motion.update(id="higher-body-motion", source_image_id=variants[0]["id"])
    visible.update(name="Visible higher owner", status="completed", full_body_prompt="Higher body prompt",
                   full_body_image_url=variants[0]["url"])
    visible["full_body_asset"] = {"selected_id": variants[1]["id"], "variants": variants}
    visible["full_body"].update(
        selected_image_id=variants[1]["id"], image_variants=variants, image_prompt="Higher body prompt",
        video_variants=[motion], selected_video_id=motion["id"],
    )
    return Character.model_validate(visible)


def test_global_prepared_full_body_survives_export_import_reexport(runtime, transport, monkeypatch):
    with _prepared_global_and_reopen(runtime, transport, monkeypatch) as (client, item, record):
        source, series, library = item.read_committed_project_view("p")
        assert source is not None and source.frames[0].character_ids == ["c"]
        actual = next(value for value in item._resolve_asset_layers(source, series, library)["characters"] if value.id == "c")
        target = headshot.saved_owner(item, "global").characters[0]
        assert actual.model_dump() == target.model_dump()
        _roundtrip(client, item, transport, target, record, carry_prepared=True)


def test_global_prepared_full_body_does_not_attach_to_same_id_local_owner(runtime, transport, monkeypatch):
    with _prepared_global_and_reopen(runtime, transport, monkeypatch) as (client, item, record):
        lower = headshot.saved_owner(item, "global").characters[0]
        assert lower.full_body_asset is not None
        ref = record.members[0].execution.output_ref
        assert ref is not None
        visible = _higher_local_character(item, lower)
        # Controlled collaborator metadata only: retain the real lower receipt.
        item.scripts["p"].characters.append(visible)
        boundaries.commit_owners(item)
        source, series, library = item.read_committed_project_view("p")
        assert source is not None and source.frames[0].character_ids == ["c"]
        actual = next(value for value in item._resolve_asset_layers(source, series, library)["characters"] if value.id == "c")
        assert actual.model_dump() == visible.model_dump()
        _roundtrip(client, item, transport, actual, record, carry_prepared=False, excluded=[
            ref, *[value.url for value in lower.full_body_asset.variants],
        ])
        assert headshot.saved_owner(item, "global").characters[0].model_dump() == lower.model_dump()
        boundaries.prove_paid_file(item, record.members[0].execution)


def test_prepared_full_body_rejects_same_size_corruption_without_side_effects(runtime, transport, monkeypatch):
    with _prepared_global_and_reopen(runtime, transport, monkeypatch) as (client, item, record):
        execution = record.members[0].execution
        assert execution.output_ref is not None and execution.prepared_artifact is not None
        path = item.data_dir / execution.output_ref
        data = path.read_bytes()
        # Negative storage fault after the real proof exists; never forge proof.
        path.write_bytes(data[:-1] + bytes([data[-1] ^ 1]))
        assert path.stat().st_size == execution.prepared_artifact["size_bytes"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() != execution.prepared_artifact["sha256"]
        before = packages._databases(item), packages._media(item), packages._calls(transport)
        exported = client.get("/projects/p/package/export")
        assert (packages._databases(item), packages._media(item), packages._calls(transport)) == before
        assert not transport["queue"] and not transport["cli_queue"] and not transport["cli"]
        assert exported.status_code == 422, exported.text[:500]
        assert exported.json()["code"] == "project_package_invalid"


def test_existing_qwen_t2i_prepared_full_body_uses_same_read_only_preservation(runtime, transport, monkeypatch):
    key = "package-prepared-full-body-qwen-t2i"
    _, _, target = old_qwen.setup_qwen(runtime)
    # The old T2I helper intentionally has metadata-only old files. Materialize
    # them before preflight; a missing old file must not masquerade as our RED.
    for ref in ("uploads/body.png", "uploads/sheet.png"):
        path = runtime.data_dir / ref
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(headshot.rgb_png_bytes(ref, width=16, height=16))
    video = runtime.data_dir / "video/motion.mp4"
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_bytes((Path(__file__).parent / "fixtures/studio-motion-smoke/old.mp4").read_bytes())
    runtime.scripts["p"].scenes = [Scene.model_validate({"id": "scene", "name": "Scene", "description": "Synthetic"})]
    runtime.scripts["p"].frames = [StoryboardFrame.model_validate({
        "id": "frame", "scene_id": "scene", "character_ids": ["c"], "action_description": "Standing",
    })]
    boundaries.commit_owners(runtime)
    assert target.base_character_id is None
    assert target.three_view_asset is None or not target.three_view_asset.variants
    assert target.headshot_asset is None or not target.headshot_asset.variants
    assert target.full_body_asset is not None and target.full_body is not None
    assert target.full_body_asset.selected_id == target.full_body.selected_image_id == "body-old"
    original_commit = runtime._storage.commit
    failures = []

    def reject_final_projection(payload):
        matching = [value for value in packages._saved_records(payload, "local") if value["client_request_id"] == key]
        if matching and matching[0]["members"][0]["execution"]["phase"] == "completed":
            failures.append(matching[0]["members"][0]["execution"]["attempt_id"])
            raise StorageError("Synthetic legacy T2I final projection checkpoint interruption")
        return original_commit(payload)

    app = first.app_for(runtime)
    with TestClient(app, raise_server_exceptions=False) as client:
        old_qwen.http.enable_fake_key(app)
        packages._read_only_export(client, runtime, transport)
        assert not transport["calls"]
        old_qwen.qwen.queue_images(transport)
        monkeypatch.setattr(runtime._storage, "commit", reject_final_projection)
        admitted = client.post(first.PUBLIC, json={
            "client_request_id": key, "asset_id": "c", "asset_type": "character",
            "generation_type": "full_body", "prompt": "An authored old T2I standing pose",
            "apply_style": False, "batch_size": 1,
        })
        assert admitted.status_code == 200, admitted.text
        owner = headshot.saved_owner(runtime, "local")
        assert len(owner.asset_image_requests) == 1
        record = owner.asset_image_requests[0]
        assert record.id == admitted.json()["request"]["id"] and record.channel == "full_body"
        assert (record.owner_kind, record.owner_id) == ("script", "p")
        execution = record.members[0].execution
        assert failures == [execution.attempt_id]
        assert execution.phase == "submitting" and execution.artifact is None
        assert execution.prepared_artifact is not None and execution.output_ref is not None
        assert execution.intent["intent_contract_version"] == old_qwen.qwen.core.VERSION
        assert execution.intent["intent_contract_version"] != CONTRACT
        assert execution.intent["mode"] == "t2i" and execution.media == []
        assert execution.provider_model_id == "qwen-image-2.0"
        assert [request.method for request, _ in transport["calls"]] == ["POST", "GET"]
        assert transport["calls"][0][0].url.endswith("/multimodal-generation/generation")
        assert not transport["queue"] and not transport["cli_queue"] and not transport["cli"]
        data = boundaries.prove_paid_file(runtime, execution)
        headshot.assert_provider_image_pixels(runtime.data_dir / execution.output_ref)
        saved = owner.characters[0]
        assert saved.full_body_asset is not None and saved.full_body is not None
        assert [value.id for value in saved.full_body_asset.variants] == ["body-old"]
        assert [value.id for value in saved.full_body.image_variants] == ["body-old"]
        expected = saved.model_dump(mode="json", exclude_none=True)
        for field in portraits.POINTERS:
            expected.pop(field, None)
        if expected["status"] == "processing":
            expected["status"] = "needs_confirmation"
        variant = {
            "id": record.members[0].variant_id, "url": execution.output_ref, "created_at": record.created_at,
            "prompt_used": execution.intent["prompt"], "is_favorited": False, "is_uploaded_source": False,
        }
        expected["full_body_asset"]["variants"].append(deepcopy(variant))
        expected["full_body"]["image_variants"].append(deepcopy(variant))
    # A failed storage checkpoint fences this pipeline instance. Export must
    # use a new app, not the failed runtime or an artificially reset fence.
    runtime.close()
    before_restart = packages._databases(runtime), packages._media(runtime), packages._calls(transport)
    restored = ComicGenPipeline(
        data_dir=runtime.data_dir, runtime_environment=RuntimeEnvironment({}),
        recover_orphan_tasks=False, repair_series_bindings=False,
    )
    try:
        restored_app = first.app_for(restored)
        with TestClient(restored_app) as client:
            assert (packages._databases(restored), packages._media(restored), packages._calls(transport)) == before_restart
            assert not restored.asset_generation_tasks and not getattr(restored, "_asset_image_runs", {})
            restored_owner = headshot.saved_owner(restored, "local")
            assert len(restored_owner.asset_image_requests) == 1
            assert restored_owner.asset_image_requests[0].model_dump(mode="json") == record.model_dump(mode="json")
            observed = client.get(first.PUBLIC + "/" + key)
            assert observed.status_code == 200 and observed.json()["request"]["id"] == record.id
            assert observed.json()["request"]["members"][0]["status"] == "needs_confirmation"
            assert (packages._databases(restored), packages._media(restored), packages._calls(transport)) == before_restart
            archive = packages._read_only_export(client, restored, transport)
            entries, payload, manifest = portraits._read_archive(archive)
            assert "media/" + execution.output_ref in entries, "Proven old T2I full-body PNG omitted from project package"
            assert entries["media/" + execution.output_ref] == data
            portraits._assert_manifest_bytes(entries, manifest, restored)
            for group in (payload["script"], payload["resolved_assets"]):
                portraits._assert_no_authority(group)
                assert next(value for value in group["characters"] if value["id"] == "c") == expected
            assert '"credential_ref"' not in json.dumps(payload) and '"prepared_artifact"' not in json.dumps(payload)
            assert (packages._databases(restored), packages._media(restored), packages._calls(transport)) == before_restart
            assert not transport["queue"] and not transport["cli_queue"] and not transport["cli"]
    finally:
        restored.close()
