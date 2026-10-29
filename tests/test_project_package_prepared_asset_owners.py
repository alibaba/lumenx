"""Real owner-local image histories must travel without execution authority.

The preservation assertions precede independent import and re-export checks.
A failed early assertion does not validate those later checks.
"""

import hashlib
import io
import json
import struct
import zipfile

from fastapi.testclient import TestClient
import pytest

from src.apps.comic_gen.models import AssetUnit, Character, ImageAsset, ImageVariant, Scene, Script, Series
from tests import test_storyboard_qwen20_core as frame_core
from tests.frozen_image_fixtures import rgb_png_bytes


runtime = frame_core.runtime
transport = frame_core.transport
MODEL = "qwen-image-2.0"


def _character(item, *, identity="c", name="Actual character", prefix="sheet"):
    relative = f"uploads/{prefix}-old.png"
    (item.data_dir / relative).write_bytes(rgb_png_bytes(prefix, width=16, height=16))
    old = ImageVariant.model_validate(dict(
        id=f"{prefix}-old", url=relative, prompt_used=f"{prefix} historical prompt",
        created_at=5.0, is_favorited=True,
    ))
    return Character.model_validate(dict(
        id=identity, name=name, description=f"{name} original description", image_url=relative,
        reference_sheet=AssetUnit.model_validate(dict(
            selected_image_id=old.id, image_variants=[old],
            image_prompt=f"{prefix} current author prompt", image_updated_at=3.0,
        )),
    ))


def _setup(item, owner_kind):
    target = _character(item)
    project = item.scripts["p"]
    project.characters = []
    project.model_settings.t2i_model = MODEL
    # Same asset string ID in a different kind must remain a different target.
    scene_path = "uploads/other-kind.png"
    (item.data_dir / scene_path).write_bytes(rgb_png_bytes("other kind", width=16, height=16))
    project.scenes.append(Scene.model_validate(dict(
        id="c", name="Same ID scene", description="Not the character", image_url=scene_path,
        image_asset=ImageAsset.model_validate(dict(selected_id="other-kind", variants=[
            ImageVariant.model_validate(dict(id="other-kind", url=scene_path, prompt_used="Scene history")),
        ])),
    )))
    if owner_kind == "script":
        project.characters.append(target)
    elif owner_kind == "series":
        item.series_store["s"] = Series.model_validate(dict(
            id="s", title="Shared series", characters=[target], episode_ids=["p"],
            created_at=1.0, updated_at=1.0,
        ))
        item.series_store["s"].model_settings.t2i_model = MODEL
        project.series_id = "s"
    else:
        assert owner_kind == "global"
        item.library_store.characters.append(target)
        # The exporting project did not admit this shared owner's request.
        item.scripts["q"] = Script.model_validate(dict(
            id="q", title="Other admitting project", original_text="Other script",
            created_at=1.0, updated_at=1.0,
        ))
        item.scripts["q"].model_settings.t2i_model = MODEL
    item._commit_storage("projects.json", "series.json", "library_assets.json")


def _owner(item, kind):
    project, series, library = item.read_committed_project_view("p")
    assert project is not None
    if kind == "script":
        return project
    if kind == "series":
        assert series is not None
        return series
    assert kind == "global"
    return library


def _root(kind):
    return ("series", "s") if kind == "series" else ("projects", "q" if kind == "global" else "p")


def _calls(transport):
    return [(request.method, request.url, request.body, dict(request.headers)) for request, _ in transport["calls"]]


def _png_files(item):
    return {str(path.relative_to(item.data_dir)): path.read_bytes() for path in item.data_dir.rglob("*.png")}


def _generate(client, item, transport, kind, *, identity="c", key, batch_size, interrupt_at=None):
    assert transport["queue"] == []
    root, root_id = _root(kind)
    successful_transports = batch_size if interrupt_at is None else interrupt_at
    for index in range(successful_transports):
        transport["queue"].extend([
            frame_core.response(frame_core.completed()),
            frame_core.response(rgb_png_bytes(f"{key} member {index}", width=16, height=16)),
        ])
    calls_before = len(transport["calls"])
    posts = []
    changed = []

    def change_author_during_actual_post(request):
        if request.method != "POST":
            return
        posts.append(request)
        if interrupt_at is None or len(posts) != interrupt_at:
            return
        transport["on_send"] = None
        record = next(value for value in _owner(item, kind).asset_image_requests if value.client_request_id == key)
        assert [member.execution.phase for member in record.members] == (
            ["completed"] * (interrupt_at - 1) + ["submitting"] + ["accepted"] * (batch_size - interrupt_at)
        )
        description = f"Explicit author revision during {key}"
        if kind == "series":
            item.update_series_asset_attributes("s", identity, "character", {"description": description})
        else:
            item.update_asset_attributes(root_id, identity, "character", {"description": description})
        changed.append(description)

    transport["on_send"] = change_author_during_actual_post
    admitted = client.post(f"/{root}/{root_id}/asset_image_requests", json={
        "client_request_id": key, "asset_id": identity, "asset_type": "character",
        "generation_type": "reference_sheet", "model_name": MODEL,
        "prompt": f"Frozen requested prompt for {key}", "negative_prompt": "",
        "apply_style": False, "batch_size": batch_size,
    })
    transport["on_send"] = None
    assert admitted.status_code == 200, admitted.text
    record = next(value for value in _owner(item, kind).asset_image_requests if value.id == admitted.json()["request"]["id"])
    assert (record.owner_kind, record.owner_id, record.asset_type, record.channel) == (
        kind, "library" if kind == "global" else "s" if kind == "series" else "p", "character", "reference_sheet",
    )
    assert (record.request_root_kind, record.request_root_id) == ("series" if kind == "series" else "project", root_id)
    phases = ["completed"] * batch_size if interrupt_at is None else (
        ["completed"] * (interrupt_at - 1) + ["submitting"] + ["accepted"] * (batch_size - interrupt_at)
    )
    assert [member.execution.phase for member in record.members] == phases
    assert [request.method for request, _ in transport["calls"][calls_before:]] == ["POST", "GET"] * successful_transports
    assert len(posts) == successful_transports and transport["queue"] == []
    target = next(value for value in _owner(item, kind).characters if value.id == identity)
    if interrupt_at is not None:
        assert changed == [target.description]
    unit = target.reference_sheet
    assert unit is not None
    for member, phase in zip(record.members, phases, strict=True):
        execution = member.execution
        assert execution.output_ref is not None
        path = item.data_dir / execution.output_ref
        if phase == "accepted":
            assert execution.prepared_artifact is None and execution.artifact is None and not path.exists()
            continue
        proof = execution.prepared_artifact
        assert proof is not None
        data = path.read_bytes()
        assert data[:8] == b"\x89PNG\r\n\x1a\n" and struct.unpack(">II", data[16:24]) == (16, 16)
        assert (hashlib.sha256(data).hexdigest(), len(data)) == (proof["sha256"], proof["size_bytes"])
        assert execution.artifact == (proof if phase == "completed" else None)
        present = member.variant_id in {value.id for value in unit.image_variants}
        assert present is (phase == "completed")
    before, calls = frame_core.persisted(item), _calls(transport)
    read = client.get(f"/{root}/{root_id}/asset_image_requests/{key}")
    assert read.status_code == 200 and read.json()["request"]["id"] == record.id
    assert frame_core.persisted(item) == before and _calls(transport) == calls
    return record


def _read_only_export(client, item, transport, project_id):
    before, media, calls = frame_core.persisted(item), _png_files(item), _calls(transport)
    response = client.get(f"/projects/{project_id}/package/export")
    assert response.status_code == 200, response.text
    assert frame_core.persisted(item) == before and _png_files(item) == media and _calls(transport) == calls
    return response.content


def _roundtrip(client, item, transport, source_target, carried_members, *, excluded_refs=(), excluded_names=()):
    source_databases = frame_core.persisted(item)
    source_projects = json.loads(source_databases["projects.json"])
    source_media, source_calls = _png_files(item), _calls(transport)
    old_unit = source_target.reference_sheet
    assert old_unit is not None and old_unit.selected_image_id is not None
    existing = {value.id: value.model_dump(mode="json", exclude_none=True) for value in old_unit.image_variants}
    expected = {value.id: (value.url, source_media[value.url], value.prompt_used) for value in old_unit.image_variants}
    for member in carried_members:
        proof = member.execution.prepared_artifact
        assert proof is not None
        relative = member.execution.output_ref
        assert relative is not None
        data = source_media[relative]
        assert (hashlib.sha256(data).hexdigest(), len(data)) == (proof["sha256"], proof["size_bytes"])
        expected[member.variant_id] = (relative, data, member.execution.intent["prompt"])

    def source_unchanged():
        current = frame_core.persisted(item)
        assert current["series.json"] == source_databases["series.json"]
        assert current["library_assets.json"] == source_databases["library_assets.json"]
        projects = json.loads(current["projects.json"])
        assert all(projects[identity] == value for identity, value in source_projects.items())
        assert all((item.data_dir / relative).read_bytes() == data for relative, data in source_media.items())
        assert _calls(transport) == source_calls and transport["queue"] == []

    archive = _read_only_export(client, item, transport, "p")
    source_unchanged()
    with zipfile.ZipFile(io.BytesIO(archive)) as package:
        manifest = json.loads(package.read("manifest.json"))
        payload = json.loads(package.read("project.json"))
        for relative, data, _ in expected.values():
            # For a prepared-but-unprojected member this is the intended RED.
            assert "media/" + relative in package.namelist(), "Owner-local paid image was omitted"
            assert package.read("media/" + relative) == data
            proof = next(value for value in manifest["media"] if value["logical_ref"] == relative)
            assert (proof["sha256"], proof["size_bytes"]) == (hashlib.sha256(data).hexdigest(), len(data))
        assert all("media/" + relative not in package.namelist() for relative in excluded_refs)

    for group in (payload["script"], payload["resolved_assets"]):
        assert group["asset_image_requests"] == []
        assert not ({value["name"] for value in group["characters"]} & set(excluded_names))
        target = next(value for value in group["characters"] if value["id"] == "c")
        assert target["name"] == source_target.name and target["description"] == source_target.description
        assert target["image_url"] == source_target.image_url
        assert target.get("reference_sheet_request_id") is None
        assert target.get("full_body_request_id") is None and target.get("three_view_request_id") is None
        unit = target["reference_sheet"]
        assert unit["selected_image_id"] == old_unit.selected_image_id and unit["image_prompt"] == old_unit.image_prompt
        assert len(unit["image_variants"]) == len(expected)
        assert {value["id"] for value in unit["image_variants"]} == set(expected)
        for variant in unit["image_variants"]:
            relative, _, prompt = expected[variant["id"]]
            assert (variant["url"], variant["prompt_used"]) == (relative, prompt)
            if variant["id"] in existing:
                assert variant == existing[variant["id"]]
        scene = next(value for value in group["scenes"] if value["id"] == "c")
        assert scene["name"] == "Same ID scene"
        assert [value["id"] for value in scene["image_asset"]["variants"]] == ["other-kind"]
    assert payload["script"].get("asset_image_request_scope_id") is None
    assert payload["script"]["provider_request_receipts"] == []

    imported = client.post("/projects/package/import", content=archive, headers={"content-type": "application/zip"})
    assert imported.status_code == 200, imported.text
    copied_id = imported.json()["id"]
    assert copied_id not in source_projects
    source_unchanged()  # The projects map now legitimately contains one more project.
    copied = item.read_committed_script(copied_id)
    assert copied is not None and copied.series_id is None
    assert copied.asset_image_request_scope_id is None and copied.asset_image_requests == []
    assert copied.provider_request_receipts == [] and copied.video_tasks == []
    target = next(value for value in copied.characters if value.name == source_target.name)
    scene = next(value for value in copied.scenes if value.name == "Same ID scene")
    assert target.id != scene.id and target.id != "c" and scene.id != "c"
    assert target.description == source_target.description
    assert target.reference_sheet_request_id is None and target.full_body_request_id is None and target.three_view_request_id is None
    unit = target.reference_sheet
    assert unit is not None and len(unit.image_variants) == len(expected)
    assert unit.image_prompt == old_unit.image_prompt
    assert scene.image_asset is not None
    assert len(scene.image_asset.variants) == 1 and scene.image_asset.variants[0].prompt_used == "Scene history"
    old_bytes = expected[old_unit.selected_image_id][1]
    expected_by_hash = {hashlib.sha256(data).hexdigest(): (old_id, data, prompt) for old_id, (_, data, prompt) in expected.items()}
    assert len(expected_by_hash) == len(expected)
    for variant in unit.image_variants:
        data = (item.data_dir / variant.url).read_bytes()
        original_id, original_data, prompt = expected_by_hash[hashlib.sha256(data).hexdigest()]
        assert data == original_data and variant.prompt_used == prompt and variant.id != original_id
        if data == old_bytes:
            assert unit.selected_image_id == variant.id and variant.is_favorited
            assert target.image_url == variant.url
        else:
            assert unit.selected_image_id != variant.id
    assert not ({value.name for value in copied.characters} & set(excluded_names))
    reexported = _read_only_export(client, item, transport, copied_id)
    source_unchanged()
    with zipfile.ZipFile(io.BytesIO(reexported)) as package:
        manifest = json.loads(package.read("manifest.json"))
        payload = json.loads(package.read("project.json"))
        for variant in unit.image_variants:
            data = (item.data_dir / variant.url).read_bytes()
            assert package.read("media/" + variant.url) == data
            proof = next(value for value in manifest["media"] if value["logical_ref"] == variant.url)
            assert (proof["sha256"], proof["size_bytes"]) == (hashlib.sha256(data).hexdigest(), len(data))
        target = next(value for value in payload["script"]["characters"] if value["id"] == target.id)
        assert target["reference_sheet"]["selected_image_id"] == unit.selected_image_id
        assert len(target["reference_sheet"]["image_variants"]) == len(expected)
        assert target.get("reference_sheet_request_id") is None
        assert payload["script"].get("asset_image_request_scope_id") is None
        assert payload["script"]["asset_image_requests"] == [] and payload["script"]["provider_request_receipts"] == []
    source_unchanged()


@pytest.mark.parametrize("owner_kind", ["script", "series", "global"])
def test_partial_reference_batch_carries_actual_owner_completed_and_prepared_only(runtime, transport, owner_kind):
    _setup(runtime, owner_kind)
    app = frame_core.http.real_app(runtime)
    with TestClient(app) as client:
        frame_core.http.enable_fake_key(app)
        record = _generate(client, runtime, transport, owner_kind, key=f"carry-{owner_kind}", batch_size=3, interrupt_at=2)
        target = next(value for value in _owner(runtime, owner_kind).characters if value.id == "c")
        unit = target.reference_sheet
        assert unit is not None and unit.selected_image_id == "sheet-old"
        assert {value.id for value in unit.image_variants} == {"sheet-old", record.members[0].variant_id}
        assert record.members[1].variant_id not in {value.id for value in unit.image_variants}
        _roundtrip(client, runtime, transport, target, record.members[:2], excluded_refs=[record.members[2].execution.output_ref])
        assert [request.method for request, _ in transport["calls"]] == ["POST", "GET"] * 2


def test_completed_global_history_is_not_duplicated_and_unused_prepared_owner_stays_outside_closure(runtime, transport):
    _setup(runtime, "global")
    runtime.library_store.characters.append(_character(runtime, identity="unused", name="Unreferenced global character", prefix="unused"))
    runtime._commit_storage("library_assets.json")
    app = frame_core.http.real_app(runtime)
    with TestClient(app) as client:
        frame_core.http.enable_fake_key(app)
        completed = _generate(client, runtime, transport, "global", key="completed-global", batch_size=2)
        unused = _generate(client, runtime, transport, "global", identity="unused", key="unused-global", batch_size=1, interrupt_at=1)
        target = next(value for value in _owner(runtime, "global").characters if value.id == "c")
        _roundtrip(client, runtime, transport, target, completed.members,
                   excluded_refs=[unused.members[0].execution.output_ref, "uploads/unused-old.png"],
                   excluded_names=["Unreferenced global character"])
        assert [request.method for request, _ in transport["calls"]] == ["POST", "GET"] * 3


@pytest.mark.parametrize("lower,higher", [("series", "script"), ("global", "series")])
def test_shadowed_owner_prepared_image_cannot_attach_to_same_id_visible_asset(runtime, transport, lower, higher):
    _setup(runtime, lower)
    app = frame_core.http.real_app(runtime)
    with TestClient(app) as client:
        frame_core.http.enable_fake_key(app)
        retained = _generate(client, runtime, transport, lower, key=f"hidden-{lower}", batch_size=1, interrupt_at=1)
        visible = _character(runtime, name="Visible higher owner", prefix="higher")
        # A controlled committed ownership transition, not a fake receipt or
        # executor: the lower owner's real paid history remains in its store.
        if higher == "script":
            runtime.scripts["p"].characters.append(visible)
        else:
            runtime.series_store["s"] = Series.model_validate(dict(
                id="s", title="Higher series", characters=[visible], episode_ids=["p"],
                created_at=1.0, updated_at=1.0,
            ))
            runtime.scripts["p"].series_id = "s"
        runtime._commit_storage("projects.json", "series.json")
        source, series, library = runtime.read_committed_project_view("p")
        assert source is not None
        resolved = runtime._resolve_asset_layers(source, series, library)
        actual = next(value for value in resolved["characters"] if value.id == "c")
        assert actual.name == visible.name
        _roundtrip(client, runtime, transport, actual, [],
                   excluded_refs=[retained.members[0].execution.output_ref, "uploads/sheet-old.png"],
                   excluded_names=["Actual character"])
        assert [request.method for request, _ in transport["calls"]] == ["POST", "GET"]


def test_deleted_local_character_prepared_image_rejects_incomplete_export_without_source_changes(runtime, transport):
    _setup(runtime, "script")
    app = frame_core.http.real_app(runtime)
    with TestClient(app) as client:
        frame_core.http.enable_fake_key(app)
        # Remove the frame's reference through the real edit API first. A
        # dangling frame reference must not be mistaken for paid-image safety.
        current = runtime.read_committed_script("p")
        assert current is not None
        detached = client.post("/projects/p/frames/update", json={
            "frame_id": "f", "expected_revision": current.revision, "character_ids": [],
        })
        assert detached.status_code == 200, detached.text
        assert detached.json()["frames"][0]["character_ids"] == []
        _read_only_export(client, runtime, transport, "p")
        assert transport["calls"] == [] and transport["queue"] == []

        record = _generate(client, runtime, transport, "script", key="deleted-local-owner", batch_size=1, interrupt_at=1)
        member = record.members[0]
        proof = member.execution.prepared_artifact
        assert member.execution.phase == "submitting" and proof is not None and member.execution.artifact is None
        assert member.execution.output_ref is not None
        before = runtime.read_committed_script("p")
        assert before is not None and before.frames[0].character_ids == []
        source_before = before.model_dump(mode="json")
        databases_before, media, calls = frame_core.persisted(runtime), _png_files(runtime), _calls(transport)
        assert member.execution.output_ref in media and "uploads/sheet-old.png" in media

        deleted = client.delete("/projects/p/characters/c")
        assert deleted.status_code == 200, deleted.text
        assert deleted.json()["characters"] == []
        saved = runtime.read_committed_script("p")
        assert saved is not None
        assert saved.model_dump(mode="json") == dict(source_before, characters=[], revision=before.revision + 1)
        assert saved.asset_image_requests[0].model_dump(mode="json") == record.model_dump(mode="json")
        databases = frame_core.persisted(runtime)
        assert databases["projects.json"] != databases_before["projects.json"]
        assert databases["series.json"] == databases_before["series.json"]
        assert databases["library_assets.json"] == databases_before["library_assets.json"]
        assert _png_files(runtime) == media and _calls(transport) == calls and transport["queue"] == []
        assert [request.method for request, _ in transport["calls"]] == ["POST", "GET"]

        source = saved.model_dump(mode="json")
        exported = client.get("/projects/p/package/export")
        # These checks precede the expected first RED at HTTP 200 instead of
        # 422, proving a rejected/omitting export has no additional side effect.
        assert frame_core.persisted(runtime) == databases
        assert runtime.read_committed_script("p").model_dump(mode="json") == source
        assert _png_files(runtime) == media and _calls(transport) == calls and transport["queue"] == []
        assert exported.status_code == 422, exported.text[:500]
        assert exported.json()["code"] == "project_package_invalid"
        assert "Prepared reference sheet" in exported.json()["detail"]
        assert not exported.content.startswith(b"PK")
