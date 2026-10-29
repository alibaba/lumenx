"""Carry proven headshot images without copying execution.

Only the inherited GPT transport and a negative completion checkpoint are
injected. The app, Core worker, prepared proof, PNG decoding, close/reopen and
public export/import/reexport stay real. This does not simulate a process kill.
"""

from contextlib import contextmanager
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import wave
import zipfile

from fastapi.testclient import TestClient

from src.apps.comic_gen.models import Character, Scene, StoryboardFrame
from src.apps.comic_gen.pipeline import ComicGenPipeline
from src.utils.json_storage import StorageError
from src.utils.runtime_environment import RuntimeEnvironment
from tests import test_project_package_prepared_three_view as packages
from tests import test_studio_headshot_core as headshot
from tests import test_studio_headshot_copy as copies
from tests import test_studio_three_view_boundaries as boundaries


runtime = headshot.runtime
transport = headshot.transport
first = headshot.three_view
gpt_sheet = headshot.gpt_sheet
KEY = "package-prepared-headshot-global"
POINTERS = (
    "reference_sheet_request_id", "full_body_request_id", "three_view_request_id",
    "headshot_request_id", "full_body_motion_request_id",
)


def _setup_global(item):
    before = headshot.setup_headshot(item, "global", first.MODELS[0])
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
    boundaries.commit_owners(item)
    target = headshot.admission.owner(item, "global").characters[0]
    assert not item.scripts["p"].characters and not item.series_store["s"].characters
    return before, target.model_copy(deep=True)


@contextmanager
def _prepared_global_and_reopen(item, state, monkeypatch):
    before, historical = _setup_global(item)
    gpt_sheet.queue_images(state, "http", count=1)
    original_commit = item._storage.commit
    failures = []

    def reject_final_projection(payload):
        records = packages._saved_records(payload, "global")
        matching = [value for value in records if value["client_request_id"] == KEY]
        if matching and matching[0]["members"][0]["execution"]["phase"] == "completed":
            failures.append(matching[0]["members"][0]["execution"]["attempt_id"])
            raise StorageError("Synthetic headshot final projection checkpoint interruption")
        return original_commit(payload)

    body = {
        "client_request_id": KEY, "asset_id": "c", "asset_type": "character",
        "generation_type": "headshot", "reference_variant_id": first.SELECTED_ID,
        "reference_image_url": first.SELECTED_REF, "apply_style": False,
        "prompt": headshot.PROMPT, "batch_size": 1,
    }
    with TestClient(first.app_for(item), raise_server_exceptions=False) as client:
        gpt_sheet.configure(client.app, "http")
        # This export must work before generation; missing old media is not RED.
        packages._read_only_export(client, item, state)
        assert not state["calls"] and len(state["queue"]) == 3
        monkeypatch.setattr(item._storage, "commit", reject_final_projection)
        admitted = client.post(first.PUBLIC, json=body)
        assert admitted.status_code == 200, admitted.text
        assert [row["status"] for row in admitted.json()["request"]["members"]] == ["pending"]
        assert len(failures) == 1
        headshot.assert_wire(state, before["reference"], headshot.PROMPT, strict=True)
        disk = {name: json.loads(data) for name, data in packages._databases(item).items()}
        saved = packages._saved_records(disk, "global")
        assert len(saved) == 1 and saved[0]["id"] == admitted.json()["request"]["id"]
        execution = saved[0]["members"][0]["execution"]
        assert execution["schema_version"] == 3
        assert execution["phase"] == "observed"
        assert execution["handle"] is not None and execution["observation"]["status"] == "succeeded"
        assert execution["prepared_artifact"] is not None and execution["artifact"] is None
        assert failures == [execution["attempt_id"]]
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
                "studio.asset.headshot.v1", "character", "headshot",
            )
            assert (record.request_root_kind, record.request_root_id, record.owner_kind, record.owner_id) == (
                "project", "p", "global", "library",
            )
            assert record.base_prompt == headshot.PROMPT and len(record.members) == 1
            assert not restored.scripts["p"].asset_image_requests
            assert not restored.series_store["s"].asset_image_requests
            member = record.members[0]
            execution = member.execution
            assert execution.schema_version == 3
            assert execution.phase == "observed" and execution.artifact is None
            assert execution.handle is not None and execution.observation["status"] == "succeeded"
            assert execution.prepared_artifact is not None and execution.output_ref is not None
            assert execution.intent["intent_contract_version"] == first.CONTRACT
            assert execution.intent["model_id"] == first.MODELS[1] and execution.intent["mode"] == "i2i"
            assert execution.intent["params"] == {"size": "1024x1024"}
            assert len(execution.media) == 1
            assert execution.media[0]["sha256"] == hashlib.sha256(before["reference"]).hexdigest()
            boundaries.prove_paid_file(restored, execution)
            headshot.assert_provider_image_pixels(restored.data_dir / execution.output_ref)
            target = owner.characters[0]
            assert target.headshot_request_id == record.id
            assert target.model_dump(mode="json", exclude={"status", "headshot_request_id"}) == historical.model_dump(
                mode="json", exclude={"status", "headshot_request_id"},
            )
            assert target.headshot_asset is not None and target.head_shot is not None
            assert all(member.variant_id not in {value.id for value in history} for history in (
                target.headshot_asset.variants, target.head_shot.image_variants,
            ))
            snapshot = packages._databases(restored), packages._media(restored), packages._calls(state)
            observed = client.get(first.PUBLIC + "/" + KEY)
            assert observed.status_code == 200 and observed.json()["request"]["id"] == record.id
            assert observed.json()["request"]["members"][0]["status"] == "needs_confirmation"
            assert (packages._databases(restored), packages._media(restored), packages._calls(state)) == snapshot
            yield client, restored, record
    finally:
        restored.close()


def _assert_no_authority(group):
    assert group["asset_image_requests"] == []
    assert group.get("asset_image_request_scope_id") is None
    assert group.get("provider_request_receipts", []) == []
    for character in group["characters"]:
        assert all(character.get(field) is None for field in POINTERS)


def _read_archive(archive):
    with zipfile.ZipFile(io.BytesIO(archive)) as package:
        entries = {name: package.read(name) for name in package.namelist()}
    return entries, json.loads(entries["project.json"]), json.loads(entries["manifest.json"])


def _assert_manifest_bytes(entries, manifest, item):
    for row in manifest["media"]:
        data = (item.data_dir / row["logical_ref"]).read_bytes()
        assert entries[row["package_path"]] == data
        assert (row["sha256"], row["size_bytes"]) == (hashlib.sha256(data).hexdigest(), len(data))


def _roundtrip(client, item, state, target, record, *, carry_prepared, excluded=()):
    databases, media, calls = packages._databases(item), packages._media(item), packages._calls(state)
    projects = json.loads(databases["projects.json"])
    assert target.headshot_asset is not None and target.head_shot is not None
    original = target.model_dump(mode="json", exclude_none=True)
    expected = deepcopy(original)
    for field in POINTERS:
        expected.pop(field, None)
    if expected["status"] == "processing":
        expected["status"] = "needs_confirmation"
    member = record.members[0]
    proof, ref = member.execution.prepared_artifact, member.execution.output_ref
    assert proof is not None and ref is not None
    data = media[ref]
    assert (hashlib.sha256(data).hexdigest(), len(data)) == (proof["sha256"], proof["size_bytes"])
    if carry_prepared:
        new_variant = {
            "id": member.variant_id, "url": ref, "created_at": record.created_at,
            "prompt_used": member.execution.intent["prompt"], "is_favorited": False, "is_uploaded_source": False,
        }
        expected["headshot_asset"]["variants"].append(deepcopy(new_variant))
        expected["head_shot"]["image_variants"].append(deepcopy(new_variant))

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
    entries, payload, manifest = _read_archive(archive)
    if carry_prepared:
        # Expected pre-fix RED: real prepared PNG exists but the package omits it.
        assert "media/" + ref in entries, "Proven headshot PNG omitted from project package"
        assert entries["media/" + ref] == data
    assert all("media/" + old_ref not in entries for old_ref in excluded)
    _assert_manifest_bytes(entries, manifest, item)
    for group in (payload["script"], payload["resolved_assets"]):
        _assert_no_authority(group)
        character = next(value for value in group["characters"] if value["id"] == "c")
        # Includes all old image metadata, prompts, selection, motion and audio.
        assert character == expected
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
    assert character.headshot_asset is not None
    assert copied.frames[0].id != "frame" and copied.frames[0].scene_id != "scene"
    assert copied.frames[0].character_ids == [character.id]
    _assert_no_authority(copied.model_dump(mode="json"))
    # Existing helper checks both histories, all variant metadata, favorites,
    # selected avatar, real MP4/WAV bytes and motion source-image ID remapping.
    copies.assert_imported_headshot(item, exported_target, character)
    if carry_prepared:
        matching = [value for value in character.headshot_asset.variants
                    if hashlib.sha256((item.data_dir / value.url).read_bytes()).hexdigest() == proof["sha256"]]
        assert len(matching) == 1 and matching[0].id != member.variant_id
        assert matching[0].id != character.headshot_asset.selected_id
        headshot.assert_provider_image_pixels(item.data_dir / matching[0].url)
    absent = client.get(f"/projects/{copied_id}/asset_image_requests/{KEY}")
    assert absent.status_code == 404

    reexported = packages._read_only_export(client, item, state, copied_id)
    entries, payload, manifest = _read_archive(reexported)
    _assert_manifest_bytes(entries, manifest, item)
    for variant in character.headshot_asset.variants:
        assert entries["media/" + variant.url] == (item.data_dir / variant.url).read_bytes()
    assert all("media/" + old_ref not in entries for old_ref in excluded)
    for group in (payload["script"], payload["resolved_assets"]):
        _assert_no_authority(group)
        exported = next(value for value in group["characters"] if value["id"] == character.id)
        assert exported == character.model_dump(mode="json", exclude_none=True)
    assert '"credential_ref"' not in json.dumps(payload) and '"prepared_artifact"' not in json.dumps(payload)
    unchanged()


def _higher_local_character(item, lower):
    variants = []
    for index in range(2):
        ref = f"uploads/higher-headshot-{index}.png"
        (item.data_dir / ref).write_bytes(headshot.rgb_png_bytes(f"higher portrait {index}", width=16, height=16))
        variants.append({
            "id": f"higher-headshot-{index}", "url": ref, "created_at": index + 1,
            "prompt_used": f"Higher portrait {index}", "is_favorited": index == 0,
            "is_uploaded_source": True, "upload_type": "head_shot",
        })
    assert lower.head_shot is not None
    motion = lower.head_shot.video_variants[0].model_dump(mode="json")
    motion.update(id="higher-face-motion", source_image_id=variants[0]["id"])
    return Character.model_validate({
        "id": "c", "name": "Visible higher owner", "description": "Independent portrait choice",
        "headshot_asset": {"selected_id": variants[1]["id"], "variants": variants},
        "head_shot": {
            "selected_image_id": variants[1]["id"], "image_variants": variants,
            "image_prompt": "Higher portrait prompt", "image_updated_at": 2,
            "video_variants": [motion], "selected_video_id": motion["id"],
            "video_prompt": "Keep this motion", "video_updated_at": 1,
        },
        "headshot_image_url": variants[1]["url"], "avatar_url": variants[1]["url"],
        "image_url": variants[1]["url"], "headshot_prompt": "Higher portrait prompt", "headshot_updated_at": 2,
    })


def test_global_prepared_headshot_survives_export_import_reexport(runtime, transport, monkeypatch):
    with _prepared_global_and_reopen(runtime, transport, monkeypatch) as (client, item, record):
        source, series, library = item.read_committed_project_view("p")
        assert source is not None and source.frames[0].character_ids == ["c"]
        actual = next(value for value in item._resolve_asset_layers(source, series, library)["characters"] if value.id == "c")
        target = headshot.saved_owner(item, "global").characters[0]
        assert actual.model_dump() == target.model_dump()
        _roundtrip(client, item, transport, target, record, carry_prepared=True)


def test_global_prepared_headshot_does_not_attach_to_same_id_local_owner(runtime, transport, monkeypatch):
    with _prepared_global_and_reopen(runtime, transport, monkeypatch) as (client, item, record):
        lower = headshot.saved_owner(item, "global").characters[0]
        assert lower.headshot_asset is not None
        visible = _higher_local_character(item, lower)
        # Controlled collaborator metadata only: retain the real lower receipt.
        item.scripts["p"].characters.append(visible)
        boundaries.commit_owners(item)
        source, series, library = item.read_committed_project_view("p")
        assert source is not None and source.frames[0].character_ids == ["c"]
        actual = next(value for value in item._resolve_asset_layers(source, series, library)["characters"] if value.id == "c")
        assert actual.model_dump() == visible.model_dump()
        _roundtrip(client, item, transport, actual, record, carry_prepared=False, excluded=[
            record.members[0].execution.output_ref,
            *[value.url for value in lower.headshot_asset.variants],
        ])
        assert headshot.saved_owner(item, "global").characters[0].model_dump() == lower.model_dump()
        boundaries.prove_paid_file(item, record.members[0].execution)
