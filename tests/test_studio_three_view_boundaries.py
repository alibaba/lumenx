"""Real three-view HTTP boundaries; no replacement of admission or Core workers.

External Session.send/CLI transport and adapter polling sleep reuse the first
slice's fixture. Held POSTs pause only that external transport. Object swaps are
controlled same-ID collaborator writes, not manufactured provider receipts.
"""

import base64
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import threading

import pytest
import requests
from fastapi.testclient import TestClient

from src.apps.comic_gen.models import AssetUnit, ImageAsset, ImageVariant
from src.apps.comic_gen.pipeline import ComicGenPipeline
from src.utils.json_storage import StorageError
from src.utils.runtime_environment import RuntimeEnvironment
from tests import test_studio_three_view_core as first
from tests.frozen_image_fixtures import rgb_png_bytes


runtime = first.runtime
transport = first.transport
THREE_VIEW_FIELDS = {
    "three_view_asset", "three_views", "three_view_image_url",
    "three_view_prompt", "three_view_updated_at", "three_view_request_id",
}
REPLACEMENTS = (
    ("local", "root"), ("series", "root"), ("global", "root"),
    ("series", "owner"), ("global", "owner"),
    ("local", "target"), ("series", "target"), ("global", "target"),
)


def body(**changes):
    return first.raw() | changes


def current_target(item, route):
    return first.admission.owner(item, route).characters[0]


def current_record(item, route):
    records = first.admission.owner(item, route).asset_image_requests
    assert len(records) == 1
    return records[0]


def commit_owners(item):
    item._commit_storage("projects.json", "series.json", "library_assets.json")


def seed_three_view_history(item, route, *, count=12, selected=True):
    """More than ten unfavorited candidates plus a saved favorite and motion."""
    target = current_target(item, route)
    variants = []
    for index in range(count):
        ref = f"uploads/three-view-old-{index}.png"
        (item.data_dir / ref).write_bytes(rgb_png_bytes(str(index), width=16, height=16))
        variants.append({
            "id": f"three-view-old-{index}", "url": ref, "created_at": index + 1,
            "prompt_used": f"old prompt {index}", "is_favorited": index == 0,
        })
    chosen = variants[-1]["id"] if selected else None
    target.three_view_asset = ImageAsset.model_validate({"selected_id": chosen, "variants": variants})
    target.three_views = AssetUnit.model_validate({
        "selected_image_id": chosen, "image_variants": variants,
        "image_prompt": "Existing three-view prompt", "image_updated_at": 2,
        "selected_video_id": "three-view-motion-old",
        "video_variants": [{"id": "three-view-motion-old", "url": "video/three-view-history.mp4",
                            "created_at": 1, "source_image_id": variants[-1]["id"], "is_favorited": True}],
        "video_prompt": "Existing three-view motion", "video_updated_at": 3,
    })
    target.three_view_image_url = variants[-1]["url"] if selected else None
    target.three_view_prompt = "Existing three-view prompt"
    target.three_view_updated_at = 2
    commit_owners(item)
    return deepcopy(target.three_view_asset.model_dump(mode="json"))


def image_fields(target):
    return target.model_dump(mode="json", include=THREE_VIEW_FIELDS)


def saved_files(item):
    folder = item.data_dir / "media_versions"
    return {
        path.relative_to(folder).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in folder.rglob("*") if path.is_file()
    } if folder.exists() else {}


def paid_posts(state):
    return [request for request, _ in state["calls"] if request.method == "POST"]


def prove_paid_file(item, execution):
    proof = execution.prepared_artifact
    assert proof is not None
    content = (item.data_dir / execution.output_ref).read_bytes()
    assert len(content) == proof["size_bytes"]
    assert hashlib.sha256(content).hexdigest() == proof["sha256"]
    decoded = first.probe_image_file(item.data_dir / execution.output_ref)
    assert (decoded.width, decoded.height) == (16, 16)
    return content


@pytest.mark.parametrize("route,replacement", REPLACEMENTS)
def test_held_paid_post_does_not_project_into_same_id_replacement(runtime, transport, route, replacement):
    selected, protected = first.setup(runtime, route, first.MODELS[1])
    seed_three_view_history(runtime, route, count=2)
    first.gpt_sheet.queue_images(transport, "http", count=1)
    queued = list(transport["queue"])
    started, release = threading.Event(), threading.Event()
    captured = {}

    def held_post(request):
        if request.method != "POST":
            return
        transport["on_send"] = None
        assert request.url == first.EDIT
        started.set()
        assert release.wait(timeout=10), "Test did not release its own paid POST"

    transport["on_send"] = held_post
    with TestClient(first.app_for(runtime)) as client:
        first.gpt_sheet.configure(client.app, "http")
        raw = body()
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(client.post, first.PUBLIC, json=raw)
            try:
                assert started.wait(timeout=5), "Actual HTTP did not reach the external POST"
                with runtime._save_lock:
                    before_owner = first.admission.owner(runtime, route)
                    before_target = before_owner.characters[0]
                    before_root = runtime.scripts["p"]
                    pending = current_record(runtime, route)
                    assert pending.members[0].execution.phase == "submitting"
                    expected_images = image_fields(before_target)
                    captured.update(original_record=pending, original_record_value=pending.model_dump(mode="json"))
                    if replacement == "root":
                        runtime.scripts["p"] = before_root.model_copy(deep=True)
                        assert runtime.scripts["p"] is not before_root
                        assert runtime.scripts["p"].model_dump() == before_root.model_dump()
                    elif replacement == "owner":
                        if route == "series":
                            runtime.series_store["s"] = before_owner.model_copy(deep=True)
                        else:
                            runtime.library_store = before_owner.model_copy(deep=True)
                        assert first.admission.owner(runtime, route) is not before_owner
                        assert first.admission.owner(runtime, route).model_dump() == before_owner.model_dump()
                    else:
                        before_owner.characters[0] = before_target.model_copy(deep=True)
                        assert before_owner.characters[0] is not before_target
                        assert before_owner.characters[0].model_dump() == before_target.model_dump()
                    commit_owners(runtime)
                    captured.update(owner=first.admission.owner(runtime, route),
                                    owner_value=first.admission.owner(runtime, route).model_dump(mode="json"),
                                    root=runtime.scripts["p"], root_value=runtime.scripts["p"].model_dump(mode="json"),
                                    target=current_target(runtime, route), target_value=current_target(runtime, route).model_dump(mode="json"),
                                    disk=first.gpt_sheet.persisted(runtime), media=saved_files(runtime))
            finally:
                release.set()
            accepted = future.result(timeout=15)
        assert accepted.status_code == 200, accepted.text
        record = current_record(runtime, route)
        execution = record.members[0].execution
        assert execution.schema_version == 3 and execution.artifact is None
        # Project root and receipt owner differ for shared characters. Replacing
        # only that root does not give a different aggregate ownership of the ACK.
        replaced_receipt_owner = (route == "local" and replacement == "root") or replacement == "owner"
        assert (captured["owner"] is not before_owner) is replaced_receipt_owner
        if replaced_receipt_owner:
            assert record is not pending
            assert execution.phase == "submitting"
            assert execution.handle is execution.observation is execution.prepared_artifact is None
            assert pending.model_dump(mode="json") == captured["original_record_value"]
            assert captured["owner"].model_dump(mode="json") == captured["owner_value"]
            assert first.gpt_sheet.persisted(runtime) == captured["disk"]
            assert not (runtime.data_dir / execution.output_ref).exists()
            assert [(r.method, r.url) for r, _ in transport["calls"]] == [("POST", first.EDIT)]
            assert transport["queue"] == queued[1:]
        else:
            assert record is pending
            assert execution.phase == "observed"
            assert execution.handle is not None and execution.handle["task_id"] == first.gpt_sheet.http_task_id()
            assert execution.observation is not None and execution.observation["status"] == "succeeded"
            assert execution.observation["source_handle"] == execution.handle
            prove_paid_file(runtime, execution)
            assert execution.prepared_artifact["handle"] == execution.handle
            assert execution.prepared_artifact["attempt_id"] == execution.attempt_id
            assert execution.prepared_artifact["output_ref"] == execution.output_ref
            first.assert_real_edit_wire(transport, selected)
            if replacement == "root":
                assert route in {"series", "global"}
                assert runtime.scripts["p"] is captured["root"]
                assert runtime.scripts["p"].model_dump(mode="json") == captured["root_value"]
                assert first.gpt_sheet.persisted(runtime)["projects.json"] == captured["disk"]["projects.json"]
        assert len(paid_posts(transport)) == 1
        assert json.loads(paid_posts(transport)[0].body) == {
            "prompt": first.PROMPT, "size": "1024x1024", "n": 1,
            "images": ["data:image/png;base64," + base64.b64encode(selected).decode()],
        }
        assert current_target(runtime, route) is captured["target"]
        assert captured["target"].model_dump(mode="json") == captured["target_value"]
        assert image_fields(current_target(runtime, route)) == expected_images
        assert current_target(runtime, route).model_dump(mode="json", include=first.PROTECTED_FIELDS) == protected
        assert saved_files(runtime) == captured["media"]
        before, calls = first.gpt_sheet.persisted(runtime), len(transport["calls"])
        read = client.get(first.PUBLIC + "/" + raw["client_request_id"])
        replay = client.post(first.PUBLIC, json=raw)
        assert read.status_code == replay.status_code == 200
        assert replay.json()["replayed"] is True and read.json()["request"] == replay.json()["request"]
        assert read.json()["request"]["members"][0]["status"] == "needs_confirmation"
        assert runtime.asset_image_service.run_original_request(record.id) is None
        assert first.gpt_sheet.persisted(runtime) == before and len(transport["calls"]) == calls
        assert not getattr(runtime, "_asset_image_runs", {})
        assert len(paid_posts(transport)) == 1 and not transport["cli"]
        assert transport["queue"] == (queued[1:] if replaced_receipt_owner else [])


def test_held_paid_post_accepts_unrelated_voice_edit_on_the_same_target(runtime, transport):
    selected, _ = first.setup(runtime, "global", first.MODELS[1])
    seed_three_view_history(runtime, "global", count=2)
    first.gpt_sheet.queue_images(transport, "http", count=1)

    def edit_voice(request):
        if request.method != "POST":
            return
        transport["on_send"] = None
        with runtime._save_lock:
            current_target(runtime, "global").voice_id = "new-author-voice"
            commit_owners(runtime)

    transport["on_send"] = edit_voice
    with TestClient(first.app_for(runtime)) as client:
        first.gpt_sheet.configure(client.app, "http")
        accepted = client.post(first.PUBLIC, json=body())
        assert accepted.status_code == 200, accepted.text
        first.assert_real_edit_wire(transport, selected)
        record = current_record(runtime, "global")
        assert record.members[0].execution.phase == "completed"
        target = current_target(runtime, "global")
        assert target.voice_id == "new-author-voice"
        assert target.three_view_asset.selected_id == record.members[0].variant_id


@pytest.mark.parametrize("batch_size,has_selection", [(1, True), (4, True), (4, False)])
def test_batch_keeps_selection_favorites_and_more_than_ten_candidates(runtime, transport, batch_size, has_selection):
    selected, protected = first.setup(runtime, "series", first.MODELS[1])
    old = seed_three_view_history(runtime, "series", selected=has_selection)
    previous_motion = current_target(runtime, "series").three_views.model_dump(mode="json", include={
        "selected_video_id", "video_variants", "video_prompt", "video_updated_at",
    })
    first.gpt_sheet.queue_images(transport, "http", count=batch_size)
    with TestClient(first.app_for(runtime)) as client:
        first.gpt_sheet.configure(client.app, "http")
        accepted = client.post(first.PUBLIC, json=body(batch_size=batch_size))
        assert accepted.status_code == 200, accepted.text
        record = current_record(runtime, "series")
        assert all(member.execution.phase == "completed" for member in record.members)
        target = current_target(runtime, "series")
        ids = [member.variant_id for member in record.members]
        expected = ids[0] if batch_size == 1 or not has_selection else old["selected_id"]
        assert target.three_view_asset.selected_id == target.three_views.selected_image_id == expected
        assert len(target.three_view_asset.variants) == len(target.three_views.image_variants) == 12 + batch_size
        old_by_id = {variant["id"]: variant for variant in old["variants"]}
        assert {variant.id: variant.model_dump(mode="json") for variant in target.three_view_asset.variants
                if variant.id in old_by_id} == old_by_id
        assert {variant.id: variant.model_dump(mode="json") for variant in target.three_views.image_variants
                if variant.id in old_by_id} == old_by_id
        assert target.model_dump(mode="json", include=first.PROTECTED_FIELDS) == protected
        assert target.three_views.model_dump(mode="json", include=set(previous_motion)) == previous_motion
        assert len(paid_posts(transport)) == batch_size and len(transport["calls"]) == batch_size * 3
        assert not transport["cli"] and not transport["queue"]
        assert all(member.execution.media[0]["sha256"] == hashlib.sha256(selected).hexdigest() for member in record.members)
        for member in record.members:
            assert member.execution.artifact == member.execution.prepared_artifact
            prove_paid_file(runtime, member.execution)


@pytest.mark.parametrize("completed_first", [False, True])
def test_unknown_batch_stops_later_paid_members_and_keeps_completed_proof(runtime, transport, completed_first):
    first.setup(runtime, "local", first.MODELS[1])
    old = seed_three_view_history(runtime, "local")
    if completed_first:
        first.gpt_sheet.queue_images(transport, "http", count=1)
    transport["queue"].append(requests.ConnectionError("synthetic response lost after submit"))
    raw = body(batch_size=4)
    with TestClient(first.app_for(runtime)) as client:
        first.gpt_sheet.configure(client.app, "http")
        accepted = client.post(first.PUBLIC, json=raw)
        assert accepted.status_code == 200, accepted.text
        record = current_record(runtime, "local")
        phases = [member.execution.phase for member in record.members]
        assert phases == (["completed", "submitting", "cancelled", "cancelled"] if completed_first
                          else ["submitting", "cancelled", "cancelled", "cancelled"])
        target = current_target(runtime, "local")
        assert target.status == "needs_confirmation"
        assert target.three_view_asset.selected_id == target.three_views.selected_image_id == old["selected_id"]
        assert len(target.three_view_asset.variants) == 12 + int(completed_first)
        if completed_first:
            prove_paid_file(runtime, record.members[0].execution)
        assert all(member.execution.prepared_artifact is None for member in record.members[int(completed_first):])
        count = len(transport["calls"])
        assert len(paid_posts(transport)) == 1 + int(completed_first)
        before = first.gpt_sheet.persisted(runtime)
        read = client.get(first.PUBLIC + "/" + raw["client_request_id"])
        replay = client.post(first.PUBLIC, json=raw)
        assert read.status_code == replay.status_code == 200
        assert replay.json()["replayed"] is True
        assert first.gpt_sheet.persisted(runtime) == before and len(transport["calls"]) == count
    runtime.close()
    # A new real pipeline/app may inspect history; it owns no original paid claim.
    reopened = ComicGenPipeline(data_dir=runtime.data_dir, runtime_environment=RuntimeEnvironment({}))
    try:
        with TestClient(first.app_for(reopened)) as client:
            before = first.gpt_sheet.persisted(reopened)
            read = client.get(first.PUBLIC + "/" + raw["client_request_id"])
            replay = client.post(first.PUBLIC, json=raw)
            assert read.status_code == replay.status_code == 200
            assert replay.json()["replayed"] is True
            assert any(member["status"] == "needs_confirmation" for member in read.json()["request"]["members"])
            assert first.gpt_sheet.persisted(reopened) == before and len(transport["calls"]) == count
            assert not reopened.asset_generation_tasks and not getattr(reopened, "_asset_image_runs", {})
    finally:
        reopened.close()


def test_frozen_source_bytes_survive_path_change_and_read_only_replay(runtime, transport):
    selected, _ = first.setup(runtime, "local", first.MODELS[1])
    replacement = rgb_png_bytes("same source path, new bytes", width=16, height=16)
    assert replacement != selected
    first.gpt_sheet.queue_images(transport, "http", count=2)

    def replace_original_path(request):
        if request.method != "POST":
            return
        transport["on_send"] = None
        (runtime.data_dir / first.SELECTED_REF).write_bytes(replacement)

    transport["on_send"] = replace_original_path
    with TestClient(first.app_for(runtime)) as client:
        first.gpt_sheet.configure(client.app, "http")
        raw = body()
        first_result = client.post(first.PUBLIC, json=raw)
        assert first_result.status_code == 200, first_result.text
        record = current_record(runtime, "local")
        assert record.members[0].execution.phase == "completed"
        assert record.members[0].execution.media[0]["sha256"] == hashlib.sha256(selected).hexdigest()
        assert json.loads(paid_posts(transport)[0].body)["images"] == [
            "data:image/png;base64," + base64.b64encode(selected).decode(),
        ]
        frozen_before, before = saved_files(runtime), first.gpt_sheet.persisted(runtime)
        read = client.get(first.PUBLIC + "/" + raw["client_request_id"])
        replay = client.post(first.PUBLIC, json=raw)
        assert read.status_code == replay.status_code == 200 and replay.json()["replayed"] is True
        assert saved_files(runtime) == frozen_before and first.gpt_sheet.persisted(runtime) == before
        assert len(paid_posts(transport)) == 1
        second = client.post(first.PUBLIC, json=body(client_request_id="three-view-explicit-new-request"))
        assert second.status_code == 200, second.text
        records = runtime.read_committed_script("p").asset_image_requests
        assert len(records) == 2
        assert [item.members[0].execution.media[0]["sha256"] for item in records] == [
            hashlib.sha256(selected).hexdigest(), hashlib.sha256(replacement).hexdigest(),
        ]
        assert json.loads(paid_posts(transport)[1].body)["images"] == [
            "data:image/png;base64," + base64.b64encode(replacement).decode(),
        ]
        assert len(paid_posts(transport)) == 2 and len(transport["calls"]) == 6


@pytest.mark.parametrize("same_url", [False, True])
def test_duplicate_selected_reference_id_rejects_before_freeze_and_binding(runtime, transport, same_url):
    first.setup(runtime, "local", first.MODELS[1])
    target = current_target(runtime, "local")
    original = next(variant for variant in target.full_body_asset.variants if variant.id == first.SELECTED_ID)
    duplicate = original.model_copy(deep=True)
    if not same_url:
        duplicate.url = "uploads/first-b.png"
    target.full_body_asset.variants.append(duplicate)
    commit_owners(runtime)
    # Supply a valid external answer if wrongly admitted so a failure is the
    # product's 200/side effect, not an empty fake transport queue.
    first.gpt_sheet.queue_images(transport, "http", count=1)
    with TestClient(first.app_for(runtime)) as client:
        first.gpt_sheet.configure(client.app, "http")
        before, frozen = first.gpt_sheet.persisted(runtime), saved_files(runtime)
        result = client.post(first.PUBLIC, json=body())
        assert result.status_code == 422, result.text
        assert first.gpt_sheet.persisted(runtime) == before and saved_files(runtime) == frozen
        assert not transport["calls"] and not transport["cli"]
        assert not runtime.asset_generation_tasks and not getattr(runtime, "_asset_image_runs", {})


@pytest.mark.parametrize("field,value", [
    ("aspect_ratio", None), ("aspect_ratio", "1:1"),
    ("seed", None), ("seed", 0), ("watermark", None), ("watermark", False),
    ("negative_prompt", " "), ("negative_prompt", "blur"),
    ("quality", None), ("quality", "high"), ("n", 1), ("prompt_extend", False),
    ("reference_variant_id", None), ("reference_variant_id", ""), ("reference_variant_id", "first-b"),
    ("reference_image_url", None), ("reference_image_url", ""), ("reference_image_url", "uploads/first-b.png"),
    ("model_name", "wan2.7-image"), ("model_name", "openai/gpt-image-2#image"),
    ("batch_size", 0), ("batch_size", True), ("prompt", " "),
])
def test_strict_author_presence_rejects_before_freeze_binding_or_paid_transport(runtime, transport, field, value):
    first.setup(runtime, "local", first.MODELS[1])
    first.gpt_sheet.queue_images(transport, "http", count=1)
    with TestClient(first.app_for(runtime)) as client:
        first.gpt_sheet.configure(client.app, "http")
        before, frozen = first.gpt_sheet.persisted(runtime), saved_files(runtime)
        result = client.post(first.PUBLIC, json=body(**{field: value}))
        assert result.status_code == 422, result.text
        assert first.gpt_sheet.persisted(runtime) == before and saved_files(runtime) == frozen
        assert not transport["calls"] and not transport["cli"]
        assert not runtime.asset_generation_tasks and not getattr(runtime, "_asset_image_runs", {})


@pytest.mark.parametrize("saved_model", ["wan2.7-image", "openai/gpt-image-2#image"])
def test_explicit_gpt_cannot_override_an_ineligible_saved_i2i_model(runtime, transport, saved_model):
    first.setup(runtime, "local", saved_model)
    first.gpt_sheet.queue_images(transport, "http", count=1)
    with TestClient(first.app_for(runtime)) as client:
        first.gpt_sheet.configure(client.app, "http")
        before, frozen = first.gpt_sheet.persisted(runtime), saved_files(runtime)
        result = client.post(first.PUBLIC, json=body(model_name=first.MODELS[1]))
        assert result.status_code == 422, result.text
        assert first.gpt_sheet.persisted(runtime) == before and saved_files(runtime) == frozen
        assert not transport["calls"] and not transport["cli"]


@pytest.mark.parametrize("source", [
    "no-selection", "missing-selection", "empty", "remote", "inline", "oss", "missing-file", "corrupt-file",
])
def test_selected_reference_must_resolve_to_one_valid_managed_local_image(runtime, transport, source):
    first.setup(runtime, "local", first.MODELS[1])
    target = current_target(runtime, "local")
    selected = next(variant for variant in target.full_body_asset.variants if variant.id == first.SELECTED_ID)
    raw = body()
    if source == "no-selection":
        target.full_body_asset.selected_id = None
    elif source == "missing-selection":
        target.full_body_asset.selected_id = "does-not-exist"
    elif source == "corrupt-file":
        (runtime.data_dir / selected.url).write_bytes(b"synthetic non-image bytes")
    else:
        selected.url = {
            "empty": "", "remote": "https://files.example.invalid/source.png",
            "inline": "data:image/png;base64," + base64.b64encode(first.gpt_sheet.PNG).decode(),
            "oss": "lumenx/assets/remote-only.png", "missing-file": "uploads/not-present.png",
        }[source]
        raw["reference_image_url"] = selected.url
    commit_owners(runtime)
    first.gpt_sheet.queue_images(transport, "http", count=1)
    with TestClient(first.app_for(runtime)) as client:
        first.gpt_sheet.configure(client.app, "http")
        before, frozen = first.gpt_sheet.persisted(runtime), saved_files(runtime)
        result = client.post(first.PUBLIC, json=raw)
        assert result.status_code == 422, result.text
        assert first.gpt_sheet.persisted(runtime) == before
        if source != "corrupt-file":
            assert saved_files(runtime) == frozen
        # Invalid bytes may have been copied before decoding. They cannot bind
        # authority, create an accepted receipt, or trigger any paid request.
        assert not transport["calls"] and not transport["cli"]
        assert not runtime.asset_generation_tasks and not getattr(runtime, "_asset_image_runs", {})


@pytest.mark.parametrize("authority", ["http", "token", "profile"])
def test_real_http_keeps_the_original_authority_and_frozen_edit_input(runtime, transport, monkeypatch, authority):
    selected, _ = first.setup(runtime, "global", first.MODELS[1])
    first.gpt_sheet.queue_images(transport, authority, count=2)
    observed_cli = []
    external_run = first.gpt_sheet.mulerouter.subprocess.run

    def inspect_external_cli(argv, **options):
        # This is the same external subprocess replacement, with the temporary
        # input read while it still exists. No product executor is substituted.
        refs = json.loads(argv[argv.index("--images") + 1])
        observed_cli.append((list(argv), refs, [Path(ref).read_bytes() for ref in refs]))
        return external_run(argv, **options)

    monkeypatch.setattr(first.gpt_sheet.mulerouter.subprocess, "run", inspect_external_cli)
    with TestClient(first.app_for(runtime)) as client:
        first.gpt_sheet.configure(client.app, authority)
        accepted = client.post(first.PUBLIC, json=body(batch_size=2))
        assert accepted.status_code == 200, accepted.text
        record = current_record(runtime, "global")
        executions = [member.execution for member in record.members]
        assert all(execution.phase == "completed" for execution in executions)
        assert all(execution.intent["intent_contract_version"] == first.CONTRACT for execution in executions)
        assert all(execution.intent["params"] == {"size": "1024x1024"} for execution in executions)
        assert all(execution.scope["gateway"] == {
            "http": "mulerouter_http", "token": "mulerun_token", "profile": "mulerun_profile",
        }[authority] for execution in executions)
        assert executions[0].scope == executions[1].scope
        assert executions[0].credential_ref == executions[1].credential_ref
        assert executions[0].media == executions[1].media
        if authority == "http":
            assert not observed_cli and not transport["cli"]
            assert len(paid_posts(transport)) == 2 and len(transport["calls"]) == 6
            assert [json.loads(request.body) for request in paid_posts(transport)] == [{
                "prompt": first.PROMPT, "size": "1024x1024", "n": 1,
                "images": ["data:image/png;base64," + base64.b64encode(selected).decode()],
            }] * 2
        else:
            assert len(observed_cli) == len(transport["cli"]) == 2
            for argv, refs, data in observed_cli:
                assert "openai/gpt-image-2/edit" in argv
                assert "--quality" not in argv
                assert argv[argv.index("--size") + 1] == "1024x1024"
                assert ("--token" in argv) is (authority == "token")
                assert data == [selected] and len(refs) == 1
                assert all(not Path(ref).exists() for ref in refs)
            assert [(request.method, request.url) for request, _ in transport["calls"]] == [
                ("GET", first.gpt_sheet.IMAGE_URL), ("GET", first.gpt_sheet.IMAGE_URL),
            ]
        assert not transport["queue"] and not transport["cli_queue"]


def test_admission_storage_error_has_no_business_receipt_claim_or_paid_call(runtime, transport, monkeypatch):
    first.setup(runtime, "local", first.MODELS[1])
    first.gpt_sheet.queue_images(transport, "http", count=1)
    commit = runtime._storage.commit
    failures = []

    def fail_new_request(payload):
        raw = payload.get("projects.json", {}).get("p", {}).get("asset_image_requests", [])
        if raw:
            assert raw[0]["members"][0]["execution"]["phase"] == "accepted"
            failures.append("accepted")
            raise StorageError("synthetic three-view admission checkpoint failure")
        return commit(payload)

    with TestClient(first.app_for(runtime), raise_server_exceptions=False) as client:
        first.gpt_sheet.configure(client.app, "http")
        before = first.gpt_sheet.persisted(runtime)
        monkeypatch.setattr(runtime._storage, "commit", fail_new_request)
        result = client.post(first.PUBLIC, json=body())
        assert result.status_code == 503, result.text
        assert failures == ["accepted"]
        after = first.gpt_sheet.persisted(runtime)
        assert {key: after[key] for key in ("projects.json", "series.json", "library_assets.json")} == {
            key: before[key] for key in ("projects.json", "series.json", "library_assets.json")
        }
        # Binding may already have saved its separate private credential store.
        # It grants no business receipt or original process claim after failure.
        assert not runtime.asset_generation_tasks and not getattr(runtime, "_asset_image_runs", {})
        assert not transport["calls"] and not transport["cli"]


def test_paid_prepared_png_repairs_in_a_new_app_without_authority_or_resubmit(runtime, transport, monkeypatch):
    selected, protected = first.setup(runtime, "local", first.MODELS[1])
    seed_three_view_history(runtime, "local", count=2)
    target = current_target(runtime, "local")
    author = target.model_dump(mode="json", exclude={"three_view_asset", "three_views", "three_view_request_id", "status"})
    legacy = target.three_view_asset.model_dump(mode="json")
    modern = target.three_views.model_dump(mode="json")
    old_bytes = {v["url"]: (runtime.data_dir / v["url"]).read_bytes() for v in legacy["variants"] + modern["image_variants"]}
    first.gpt_sheet.queue_images(transport, "http", count=1)
    commit = runtime._storage.commit
    failures = []

    def fail_only_completion(payload):
        raw = payload.get("projects.json", {}).get("p", {}).get("asset_image_requests", [])
        if raw and raw[0]["members"][0]["execution"]["phase"] == "completed":
            failures.append("completed")
            raise StorageError("synthetic three-view completed checkpoint failure")
        return commit(payload)

    raw = body()
    with TestClient(first.app_for(runtime), raise_server_exceptions=False) as client:
        first.gpt_sheet.configure(client.app, "http")
        monkeypatch.setattr(runtime._storage, "commit", fail_only_completion)
        # An admission response can already be sent when BackgroundTasks hits
        # the completed checkpoint failure. Observe the real on-disk checkpoint.
        accepted = client.post(first.PUBLIC, json=raw)
        assert accepted.status_code == 200, accepted.text
        assert failures == ["completed"]
        saved = json.loads((runtime.data_dir / "projects.json").read_bytes())["p"]["asset_image_requests"][0]
        execution = saved["members"][0]["execution"]
        assert execution["schema_version"] == 3 and execution["phase"] == "observed" and execution["artifact"] is None
        assert execution["handle"]["task_id"] == first.gpt_sheet.http_task_id()
        assert execution["observation"]["status"] == "succeeded"
        assert execution["observation"]["source_handle"] == execution["handle"]
        proof = execution["prepared_artifact"]
        assert proof is not None
        assert proof["handle"] == execution["handle"] and proof["attempt_id"] == execution["attempt_id"]
        assert proof["output_ref"] == execution["output_ref"]
        paid_bytes = (runtime.data_dir / execution["output_ref"]).read_bytes()
        assert hashlib.sha256(paid_bytes).hexdigest() == proof["sha256"]
        assert len(paid_bytes) == proof["size_bytes"]
        first.assert_real_edit_wire(transport, selected)
    calls = len(transport["calls"])
    runtime.close()
    reopened = ComicGenPipeline(data_dir=runtime.data_dir, runtime_environment=RuntimeEnvironment({}), recover_orphan_tasks=True)
    try:
        app = first.app_for(reopened)
        with TestClient(app) as client:
            assert client.app is app
            assert not app.state.runtime_environment.snapshot().get("MULEROUTER_API_KEY")
            assert not app.state.runtime_environment.snapshot().get("MULERUN_TOKEN")
            restored = current_record(reopened, "local")
            target = current_target(reopened, "local")
            member, repaired = restored.members[0], restored.members[0].execution
            assert restored.id == saved["id"] and restored.request_fingerprint == saved["request_fingerprint"]
            assert (restored.entrypoint, restored.asset_type, restored.channel) == (first.ENTRYPOINT, "character", "three_view")
            assert (restored.request_root_kind, restored.request_root_id, restored.owner_kind, restored.owner_id) == ("project", "p", "script", "p")
            assert restored.base_prompt == first.PROMPT and len(restored.members) == 1
            assert repaired.schema_version == 3 and repaired.phase == "completed"
            assert repaired.provider_model_id == "gpt-image-2" and repaired.intent["model_id"] == first.MODELS[1]
            assert repaired.artifact == repaired.prepared_artifact == proof
            assert repaired.handle == execution["handle"] and repaired.observation == execution["observation"]
            assert repaired.fingerprint == execution["fingerprint"]
            assert repaired.scope == execution["scope"] and repaired.credential_ref == execution["credential_ref"]
            assert repaired.intent == execution["intent"] and repaired.media == execution["media"]
            assert repaired.intent["intent_contract_version"] == first.CONTRACT and repaired.intent["mode"] == "i2i"
            assert repaired.intent["prompt"] == first.PROMPT and repaired.intent["params"] == {"size": "1024x1024"}
            assert len(repaired.media) == 1
            assert (repaired.media[0]["role"], repaired.media[0]["ordinal"], repaired.media[0]["declared_media_type"]) == ("reference_image", 0, "image/png")
            assert repaired.media[0]["sha256"] == hashlib.sha256(selected).hexdigest()
            assert repaired.media[0]["size_bytes"] == len(selected)
            assert (reopened.data_dir / execution["output_ref"]).read_bytes() == paid_bytes
            assert target.three_view_request_id == restored.id
            assert len(reopened.scripts["p"].asset_image_requests) == 1
            assert not reopened.series_store["s"].asset_image_requests and not reopened.library_store.asset_image_requests
            assert target.model_dump(mode="json", exclude={"three_view_asset", "three_views", "three_view_request_id", "status"}) == author
            assert target.model_dump(mode="json", include=first.PROTECTED_FIELDS) == protected
            expected_variant = ImageVariant.model_validate({
                "id": member.variant_id, "url": repaired.output_ref,
                "created_at": restored.created_at, "prompt_used": repaired.intent["prompt"],
            }).model_dump(mode="json")
            for actual, previous, key in [
                (target.three_view_asset.model_dump(mode="json"), legacy, "variants"),
                (target.three_views.model_dump(mode="json"), modern, "image_variants"),
            ]:
                assert {k: v for k, v in actual.items() if k != key} == {k: v for k, v in previous.items() if k != key}
                assert actual[key][:-1] == previous[key]
                assert len(actual[key]) == len(previous[key]) + 1 and actual[key][-1] == expected_variant
            assert target.three_view_asset.selected_id == target.three_views.selected_image_id == legacy["selected_id"]
            assert {ref: (reopened.data_dir / ref).read_bytes() for ref in old_bytes} == old_bytes
            decoded = first.probe_image_file(reopened.data_dir / repaired.output_ref)
            assert (decoded.width, decoded.height) == (16, 16)
            before = first.gpt_sheet.persisted(reopened)
            read = client.get(first.PUBLIC + "/" + raw["client_request_id"])
            replay = client.post(first.PUBLIC, json=raw)
            assert read.status_code == replay.status_code == 200 and replay.json()["replayed"] is True
            assert read.json()["reason"] is None and read.json()["request"] == replay.json()["request"]
            assert read.json()["request"]["members"][0]["status"] == "completed"
            reopened.asset_image_service.repair_request("project", "p", raw["client_request_id"])
            assert reopened.asset_image_service.run_original_request(restored.id) is None
            assert first.gpt_sheet.persisted(reopened) == before and len(transport["calls"]) == calls
            assert len(paid_posts(transport)) == 1 and not transport["queue"] and not transport["cli"]
    finally:
        reopened.close()
