"""Public motion commands retain author input and the original paid execution.

The real app, Studio service, Core runtime and Wanx adapter run here. External
HTTP transport is the only provider substitute. Storage/lock fault injection
is explicitly marked where it establishes a crash or concurrency fixture.
Video bytes are synthetic transport evidence, not decoded video acceptance.
"""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import json
import re
import threading
import uuid

import pytest
import requests
from fastapi.testclient import TestClient

from src.apps.comic_gen import api
from src.apps.comic_gen.models import ProviderRequestReceipt, Scene, Script
from src.apps.comic_gen.pipeline import ComicGenPipeline
from src.utils.json_storage import StorageError
from tests import test_studio_motion_durable as motion_fixtures
from tests.test_studio_motion_durable import (
    COMMAND,
    PATH as LEGACY_PATH,
    RESULT,
    SUBMIT,
    TASK,
    VIDEO,
    app_for,
    move_owner,
    real_extraction_transport,
)


runtime = motion_fixtures.runtime
transport = motion_fixtures.transport


PUBLIC = "/projects/project/motion_ref_requests"
ORIGINAL = PUBLIC + "/motion-request"
RECOVER = ORIGINAL + "/recover"
QUERY = {"asset_id": "hero", "asset_type": "full_body", "batch_size": 1, "has_audio": False}


def saved_files(item):
    return {
        str(path.relative_to(item.data_dir)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in item.data_dir.rglob("*") if path.is_file()
    }


def config_params(item, **changes):
    video = deepcopy(item.config.get("video") or {})
    video.setdefault("model", {}).setdefault("params", {}).update(changes)
    item.config["video"] = video


def preflight(client, **changes):
    response = client.get(PUBLIC, params={**QUERY, **changes})
    assert response.status_code == 200, response.text
    return response.json()


def command_for(client, **changes):
    query = {
        "asset_id": changes.get("asset_id", COMMAND["asset_id"]),
        "asset_type": changes.get("asset_type", COMMAND["asset_type"]),
        "batch_size": changes.get("batch_size", COMMAND["batch_size"]),
        "has_audio": bool(changes.get("audio_url")),
    }
    state = preflight(client, **query)
    return {**COMMAND, **changes, "expected_target": state["expected_target"]}


def expected_from(read):
    request = read["request"]
    return {
        "expected_attempt_id": request["attempt_id"],
        "expected_fingerprint": request["execution_fingerprint"],
    }


def original_request(client):
    response = client.get(ORIGINAL)
    assert response.status_code == 200, response.text
    return response.json()


def submit_existing(client, transport, *, status="RUNNING"):
    """Create a genuinely paid original through the already public old route."""
    transport["status"] = status
    response = client.post(LEGACY_PATH, json=COMMAND)
    assert response.status_code == 200, response.text
    return original_request(client)


def submit_count(transport):
    return sum(method == "POST" and url == SUBMIT for method, url, *_ in transport["calls"])


@pytest.mark.parametrize("owner", ["script", "series", "global"])
@pytest.mark.parametrize("source_kind", ["data_uri", "local_path"])
def test_public_preflight_captures_actual_owner_selected_source_without_side_effects(runtime, transport, owner, source_kind):
    item, image = runtime
    target = move_owner(item, owner)
    assert target.full_body is not None
    if source_kind == "local_path":
        path = item.data_dir / "uploads/selected.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(image)
        target.full_body.image_variants[0].url = "uploads/selected.png"
    # The public read must not guess from the project's unrelated model picker.
    item.scripts["project"].model_settings.i2v_model = "wan2.6-i2v"
    item._commit_storage("projects.json", "series.json", "library_assets.json")
    with TestClient(app_for(item)) as client:
        before = saved_files(item)
        response = client.get(PUBLIC, params=QUERY)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["project_id"] == "project"
        assert (body["asset_id"], body["asset_type"]) == ("hero", "full_body")
        assert body["eligible"] is True and body["reason"] is None
        assert body["model_id"] == "wan2.7-i2v"
        assert body["owner"] == {"kind": owner, "id": {"script": "project", "series": "series", "global": "library"}[owner]}
        assert body["selected_image_id"] == "selected-image"
        assert body["source_kind"] == source_kind
        assert re.fullmatch(r"[0-9a-f]{64}", body["expected_target"])
        assert response.headers.get("cache-control") == "no-store"
        assert preflight(client) == body
        assert "synthetic-motion-secret" not in response.text
        assert "data:image" not in response.text and "https://" not in response.text
        assert saved_files(item) == before
        assert transport["calls"] == []
        assert item.scripts["project"].video_tasks == []
        assert item.scripts["project"].provider_request_receipts == []


@pytest.mark.parametrize(("change", "reason"), [
    ("scene_no_selection", "selected_image_required"),
    ("batch", "batch_unsupported"),
    ("request_audio", "request_audio_present"),
    ("model", "model_unsupported"),
    ("config_audio", "configured_audio_present"),
    ("no_selection", "selected_image_required"),
    ("remote", "source_kind_unsupported"),
])
def test_strict_public_post_proves_ineligibility_without_legacy_fallback(runtime, transport, change, reason):
    item, _ = runtime
    target = item.scripts["project"].characters[0]
    assert target.full_body is not None
    patch = {}
    if change == "scene_no_selection":
        patch["asset_type"] = "scene"
        item.scripts["project"].scenes.append(Scene.model_validate({"id": "hero", "name": "Scene", "description": "Scene without a selected image"}))
    elif change == "batch":
        patch["batch_size"] = 2
    elif change == "request_audio":
        patch["audio_url"] = "https://audio.invalid/private-driving-audio.wav"
    elif change == "model":
        config_params(item, i2v_model_name="wan2.6-i2v")
    elif change == "config_audio":
        config_params(item, audio_url="https://audio.invalid/private-configured-audio.wav")
    elif change == "no_selection":
        target.full_body.selected_image_id = None
    elif change == "remote":
        target.full_body.image_variants[0].url = "https://media.invalid/remote.png"
    item._commit_storage("projects.json")
    with TestClient(app_for(item)) as client:
        query = {"asset_type": patch.get("asset_type", "full_body"), "batch_size": patch.get("batch_size", 1), "has_audio": bool(patch.get("audio_url"))}
        state = preflight(client, **query)
        assert state["eligible"] is False and state["reason"] == reason
        assert "private-driving" not in json.dumps(state) and "private-configured" not in json.dumps(state)
        before = saved_files(item)
        response = client.post(PUBLIC, json={**COMMAND, **patch, "expected_target": state["expected_target"]})
        assert response.status_code == 422, response.text
        assert response.json()["code"] == "motion_reference_not_eligible"
        assert response.json()["accepted"] is False
        assert response.json()["client_request_id"] == COMMAND["client_request_id"]
        assert response.json()["reason"] == reason
        assert saved_files(item) == before
        assert transport["calls"] == [] and item.video_generation_tasks == {}


@pytest.mark.parametrize("invalid", ["missing_key", "missing_stamp", "invalid_key", "invalid_stamp", "extra_field"])
def test_public_admission_requires_strict_identity_and_author_precondition(runtime, transport, invalid):
    item, _ = runtime
    body = {**COMMAND, "expected_target": "0" * 64}
    if invalid == "missing_key":
        body.pop("client_request_id")
    elif invalid == "missing_stamp":
        body.pop("expected_target")
    elif invalid == "invalid_key":
        body["client_request_id"] = "invalid key"
    elif invalid == "invalid_stamp":
        body["expected_target"] = "invalid"
    else:
        body["model"] = "wan2.7-i2v"
    with TestClient(app_for(item)) as client:
        before = saved_files(item)
        response = client.post(PUBLIC, json=body)
        assert response.status_code == 422, response.text
        assert response.json().get("accepted") is not False
        assert saved_files(item) == before and transport["calls"] == []


def test_public_admission_uses_actual_core_wire_and_returns_original_motion_envelope(runtime, transport):
    item, image = runtime
    with TestClient(app_for(item)) as client:
        body = command_for(client)
        admitted = client.post(PUBLIC, json=body)
        assert admitted.status_code == 200, admitted.text
        response = admitted.json()
        assert response["replayed"] is False
        assert set(response) >= {"task_id", "request", "task", "reason", "replayed"}
        assert response["request"]["entrypoint"] == "studio.motion_reference.v1"
        assert response["request"]["schema_version"] == 3
        assert "expected_target" not in response["request"]
        assert response["request"]["client_request_id"] == COMMAND["client_request_id"]
        read = original_request(client)
        task = read["task"]
        assert task["id"] == response["task_id"] == response["request"]["task_id"]
        assert task["frame_id"] is None and task["motion_reference"]["source_image_id"] == "selected-image"
        record = task["provider_execution"]
        assert record["phase"] == "completed" and record["prepared_artifact"] == record["artifact"]
        assert record["media"][0]["sha256"] == hashlib.sha256(image).hexdigest()
        assert (item.data_dir / record["artifact"]["relative_path"]).read_bytes() == VIDEO
        target = item.scripts["project"].characters[0]
        assert target.full_body is not None and target.full_body.selected_video_id == "selected-video"
        assert len(target.full_body.video_variants) == 2
        wire = next(call[2] for call in transport["calls"] if call[0] == "POST" and call[1] == SUBMIT)
        assert wire["model"] == "wan2.7-i2v"
        assert wire["input"]["prompt"] == COMMAND["prompt"]
        assert [media["type"] for media in wire["input"]["media"]] == ["first_frame"]
        assert wire["parameters"] == {"duration": 10, "resolution": "720P", "prompt_extend": True, "watermark": False}
        calls = list(transport["calls"])
        replay = client.post(LEGACY_PATH, json=COMMAND)
        assert replay.status_code == 200 and replay.json()["_replayed"] is True
        assert replay.json()["_task_id"] == response["task_id"]
        assert transport["calls"] == calls and submit_count(transport) == 1


@pytest.mark.parametrize("owner", ["script", "series", "global"])
@pytest.mark.parametrize("edit", ["description", "selection_aba"])
def test_author_stamp_rejects_committed_owner_edits_including_aba_before_admission(runtime, transport, owner, edit):
    item, _ = runtime
    target = move_owner(item, owner)
    assert target.full_body is not None
    with TestClient(app_for(item)) as client:
        body = command_for(client)
        if edit == "description":
            target.description = "A later author changed this coat"
            item._commit_storage("projects.json", "series.json", "library_assets.json")
        else:
            original = target.full_body.selected_image_id
            target.full_body.selected_image_id = None
            item._commit_storage("projects.json", "series.json", "library_assets.json")
            target.full_body.selected_image_id = original
            item._commit_storage("projects.json", "series.json", "library_assets.json")
        before = saved_files(item)
        response = client.post(PUBLIC, json=body)
        assert response.status_code == 409, response.text
        assert response.json()["code"] == "motion_reference_target_changed"
        assert response.json()["accepted"] is False
        assert response.json()["client_request_id"] == COMMAND["client_request_id"]
        assert preflight(client)["expected_target"] != body["expected_target"]
        assert saved_files(item) == before and transport["calls"] == []


@pytest.mark.parametrize(("name", "value"), [("prompt_extend", False), ("watermark", True), ("seed", 17)])
def test_author_stamp_rejects_effective_config_change_before_media_or_binding(runtime, transport, name, value):
    item, _ = runtime
    with TestClient(app_for(item)) as client:
        body = command_for(client)
        config_params(item, **{name: value})
        before = saved_files(item)
        response = client.post(PUBLIC, json=body)
        assert response.status_code == 409, response.text
        assert response.json()["code"] == "motion_reference_target_changed"
        assert response.json()["accepted"] is False
        assert saved_files(item) == before and transport["calls"] == []


@pytest.mark.parametrize("change", ["model", "owner_removed", "credential_removed"])
def test_existing_key_replay_precedes_current_eligibility_and_stale_stamp(runtime, transport, change):
    item, _ = runtime
    with TestClient(app_for(item)) as client:
        original = submit_existing(client, transport, status="SUCCEEDED")
        if change == "model":
            config_params(item, i2v_model_name="wan2.6-i2v", audio_url="https://audio.invalid/new.wav")
        elif change == "owner_removed":
            item.scripts["project"].characters.clear()
            item._commit_storage("projects.json")
        else:
            changed = client.post("/config/env", json={"DASHSCOPE_API_KEY": ""})
            assert changed.status_code == 200, changed.text
        before, calls = saved_files(item), list(transport["calls"])
        response = client.post(PUBLIC, json={**COMMAND, "expected_target": "0" * 64})
        assert response.status_code == 200, response.text
        assert response.json()["replayed"] is True
        assert response.json()["task_id"] == original["task_id"]
        assert response.json()["request"] == original["request"]
        assert saved_files(item) == before and transport["calls"] == calls


def test_identity_conflict_is_not_misreported_as_definite_zero_admission(runtime, transport):
    item, _ = runtime
    with TestClient(app_for(item)) as client:
        original = submit_existing(client, transport)
        config_params(item, i2v_model_name="wan2.6-i2v")
        before, calls = saved_files(item), list(transport["calls"])
        response = client.post(PUBLIC, json={**COMMAND, "duration": 15, "expected_target": "0" * 64})
        assert response.status_code == 409, response.text
        assert response.json().get("accepted") is not False
        assert original_request(client)["request"] == original["request"]
        assert saved_files(item) == before and transport["calls"] == calls


def test_public_replay_keeps_deleted_task_tombstone_without_recreating_work(runtime, transport):
    item, _ = runtime
    with TestClient(app_for(item)) as client:
        original = submit_existing(client, transport, status="SUCCEEDED")
        artifact = original["task"]["provider_execution"]["artifact"]
        item.delete_asset_video("project", "hero", "character", original["task_id"])
        before, calls = saved_files(item), list(transport["calls"])
        response = client.post(PUBLIC, json={**COMMAND, "expected_target": "0" * 64})
        assert response.status_code == 200, response.text
        assert response.json()["replayed"] is True
        assert response.json()["task"] is None and response.json()["reason"] == "task_deleted"
        recovery = client.post(RECOVER, json=expected_from(original))
        assert recovery.status_code == 404, recovery.text
        assert saved_files(item) == before and transport["calls"] == calls
        assert (item.data_dir / artifact["relative_path"]).read_bytes() == VIDEO


def test_public_recovery_after_restoring_original_credential_never_submits_again(runtime, transport):
    item, _ = runtime
    with TestClient(app_for(item)) as client:
        original = submit_existing(client, transport)
        identity = expected_from(original)
        record = deepcopy(original["task"]["provider_execution"])
        changed = client.post("/config/env", json={"DASHSCOPE_API_KEY": "synthetic-wrong-secret"})
        assert changed.status_code == 200, changed.text
        before_calls = list(transport["calls"])
        denied = client.post(RECOVER, json=identity)
        assert denied.status_code == 200, denied.text
        assert denied.json()["task"]["status"] == "needs_confirmation"
        assert denied.json()["reason"] == "provider_credentials_required"
        assert transport["calls"] == before_calls
        restored = client.post("/config/env", json={"DASHSCOPE_API_KEY": "synthetic-motion-secret"})
        assert restored.status_code == 200, restored.text
        # Restoring configuration and GET must not be mistaken for a recovery action.
        assert original_request(client)["task"]["status"] == "needs_confirmation"
        assert transport["calls"] == before_calls
        transport["status"] = "SUCCEEDED"
        response = client.post(RECOVER, json=identity)
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["request"] == original["request"]
        current = result["task"]["provider_execution"]
        assert current["phase"] == "completed"
        for field in ("attempt_id", "fingerprint", "credential_ref", "scope", "media"):
            assert current[field] == record[field]
        assert current["prepared_artifact"] == current["artifact"]
        assert (item.data_dir / current["artifact"]["relative_path"]).read_bytes() == VIDEO
        assert submit_count(transport) == 1


def test_get_original_receipt_is_read_only_even_when_provider_has_completed(runtime, transport):
    item, _ = runtime
    with TestClient(app_for(item)) as client:
        original = submit_existing(client, transport)
        transport["status"] = "SUCCEEDED"
        before, calls = saved_files(item), list(transport["calls"])
        assert original_request(client) == original
        assert original_request(client) == original
        assert saved_files(item) == before and transport["calls"] == calls


def test_lost_submit_ack_remains_unknown_and_public_recovery_cannot_resubmit(runtime, transport):
    item, _ = runtime
    transport["lose_submit"] = True
    with TestClient(app_for(item)) as client:
        original = submit_existing(client, transport)
        assert original["task"]["provider_execution"]["handle"] is None
        before, calls = saved_files(item), list(transport["calls"])
        response = client.post(RECOVER, json=expected_from(original))
        assert response.status_code == 409, response.text
        assert response.json()["code"] == "motion_reference_recover_not_allowed"
        replay = client.post(PUBLIC, json={**COMMAND, "expected_target": "0" * 64})
        assert replay.status_code == 200 and replay.json()["replayed"] is True
        assert original_request(client)["request"] == original["request"]
        assert saved_files(item) == before and transport["calls"] == calls
        assert submit_count(transport) == 1


def test_accepted_original_uses_existing_continue_and_only_then_public_recovery(runtime, transport):
    item, _ = runtime
    with TestClient(app_for(item)) as client:
        # Persist an unsubmitted crash fixture through the real existing admission
        # service. Public admission/dispatch is independently covered above.
        raw = {key: value for key, value in COMMAND.items() if key != "client_request_id"}
        raw["audio_url"] = None
        item.motion_reference_service.admit("project", raw, COMMAND["client_request_id"])
        original = original_request(client)
        before = saved_files(item)
        denied = client.post(RECOVER, json=expected_from(original))
        assert denied.status_code == 409, denied.text
        assert denied.json()["code"] == "motion_reference_recover_not_allowed"
        assert saved_files(item) == before and transport["calls"] == []
        continued = client.post(f"/projects/project/provider_video_tasks/{original['task_id']}/continue", json=expected_from(original))
        assert continued.status_code == 200, continued.text
        assert submit_count(transport) == 1
        assert [call[0] for call in transport["calls"]] == ["POST"]
        response = client.post(RECOVER, json=expected_from(original))
        assert response.status_code == 200, response.text
        assert response.json()["task"]["provider_execution"]["phase"] == "completed"
        assert response.json()["request"] == original["request"]
        assert submit_count(transport) == 1


def test_public_recovery_repairs_prepared_bytes_after_new_app_without_credentials(runtime, transport, monkeypatch):
    item, _ = runtime
    commit = item._storage.commit

    def crash_final_commit(payloads):
        tasks = payloads.get("projects.json", {}).get("project", {}).get("video_tasks", [])
        if any(task.get("provider_execution", {}).get("phase") == "completed" for task in tasks):
            raise StorageError("synthetic public recovery final commit crash")
        return commit(payloads)

    # This failpoint targets persisted prepared proof, not a replacement executor.
    monkeypatch.setattr(item._storage, "commit", crash_final_commit)
    with TestClient(app_for(item), raise_server_exceptions=False) as client:
        response = client.post(LEGACY_PATH, json=COMMAND)
        assert response.status_code == 200, response.text
    saved = json.loads((item.data_dir / "projects.json").read_text())["project"]
    old = saved["video_tasks"][0]
    proof = old["provider_execution"]["prepared_artifact"]
    assert proof is not None and old["provider_execution"]["artifact"] is None
    assert (item.data_dir / proof["relative_path"]).read_bytes() == VIDEO
    calls = list(transport["calls"])
    item.close()
    monkeypatch.delenv("DASHSCOPE_API_KEY")
    reopened = ComicGenPipeline(data_dir=item.data_dir, recover_orphan_tasks=True)
    try:
        with TestClient(app_for(reopened)) as client:
            original = original_request(client)
            assert original["task"]["provider_execution"]["artifact"] is None
            response = client.post(RECOVER, json=expected_from(original))
            assert response.status_code == 200, response.text
            result = response.json()
            assert result["request"] == original["request"]
            assert result["task"]["provider_execution"]["artifact"] == proof
            assert result["task"]["provider_execution"]["phase"] == "completed"
            assert (reopened.data_dir / proof["relative_path"]).read_bytes() == VIDEO
            assert transport["calls"] == calls
            again = client.post(RECOVER, json=expected_from(original))
            assert again.status_code == 200 and again.json()["task"] == result["task"]
            target = reopened.scripts["project"].characters[0]
            assert target.full_body is not None
            assert len(target.full_body.video_variants) == 2 and target.full_body.selected_video_id == "selected-video"
            assert transport["calls"] == calls
    finally:
        reopened.close()


@pytest.mark.parametrize("stage", ["query", "fetch"])
def test_concurrent_public_recovery_uses_existing_actual_operation_claim(runtime, transport, stage):
    item, _ = runtime
    started, release = threading.Event(), threading.Event()

    def hold_transport():
        started.set()
        assert release.wait(timeout=10), "Test did not release its own external transport"

    with TestClient(app_for(item)) as client:
        original = submit_existing(client, transport)
        transport["status"] = "SUCCEEDED"
        transport["on_" + stage] = hold_transport
        with ThreadPoolExecutor(max_workers=1) as pool:
            first = pool.submit(client.post, RECOVER, json=expected_from(original))
            try:
                assert started.wait(timeout=5), "Public recovery did not reach its original external transport"
                calls = list(transport["calls"])
                second = client.post(RECOVER, json=expected_from(original))
                assert second.status_code == 409, second.text
                assert second.json()["code"] == "provider_operation_running"
                assert transport["calls"] == calls
            finally:
                release.set()
            response = first.result(timeout=10)
            assert response.status_code == 200, response.text
            assert response.json()["task"]["provider_execution"]["phase"] == "completed"
        assert submit_count(transport) == 1


@pytest.mark.parametrize("field", ["expected_attempt_id", "expected_fingerprint"])
def test_stale_recovery_expected_identity_has_no_provider_or_storage_effect(runtime, transport, field):
    item, _ = runtime
    with TestClient(app_for(item)) as client:
        original = submit_existing(client, transport)
        body = expected_from(original)
        body[field] = "stale-attempt" if field == "expected_attempt_id" else "0" * 64
        before, calls = saved_files(item), list(transport["calls"])
        response = client.post(RECOVER, json=body)
        assert response.status_code == 409, response.text
        assert response.json()["code"] == "motion_reference_recover_not_allowed"
        assert saved_files(item) == before and transport["calls"] == calls


@pytest.mark.parametrize("corruption", ["missing_receipt", "duplicate_receipt", "changed_binding", "changed_record"])
def test_recovery_rejects_live_and_committed_authority_disagreement(runtime, transport, corruption):
    item, _ = runtime
    with TestClient(app_for(item)) as client:
        original = submit_existing(client, transport)
        script = item.scripts["project"]
        task = script.video_tasks[0]
        if corruption == "missing_receipt":
            script.provider_request_receipts.clear()
        elif corruption == "duplicate_receipt":
            script.provider_request_receipts.append(script.provider_request_receipts[0].model_copy(deep=True))
        elif corruption == "changed_binding":
            assert task.motion_reference is not None
            task.motion_reference = task.motion_reference.model_copy(update={"owner_id": "another-owner"})
        else:
            assert task.provider_execution is not None
            task.provider_execution = task.provider_execution.model_copy(update={"fingerprint": "0" * 64})
        # Deliberate live-only corruption must not become authority from its ID.
        before, calls = saved_files(item), list(transport["calls"])
        response = client.post(RECOVER, json=expected_from(original))
        assert response.status_code == 409, response.text
        assert response.json()["code"] == "motion_reference_recover_not_allowed"
        assert saved_files(item) == before and transport["calls"] == calls


@pytest.mark.parametrize("state", ["SUCCEEDED", "FAILED", "CANCELED"])
def test_terminal_public_recovery_is_local_and_does_not_requery_or_resubmit(runtime, transport, state):
    item, _ = runtime
    with TestClient(app_for(item)) as client:
        original = submit_existing(client, transport, status=state)
        before, calls = saved_files(item), list(transport["calls"])
        response = client.post(RECOVER, json=expected_from(original))
        assert response.status_code == 200, response.text
        assert response.json()["task"] == original["task"]
        assert response.json()["request"] == original["request"]
        assert saved_files(item) == before and transport["calls"] == calls


def test_public_recovery_preserves_already_succeeded_observation_without_requery(runtime, transport):
    item, _ = runtime
    with TestClient(app_for(item)) as client:
        original = submit_existing(client, transport)
        transport["status"] = "SUCCEEDED"

        def unavailable_fetch():
            raise requests.ConnectionError("synthetic unavailable paid download")

        transport["on_fetch"] = unavailable_fetch
        failed = client.post(RECOVER, json=expected_from(original))
        assert failed.status_code == 200, failed.text
        assert failed.json()["task"]["provider_execution"]["observation"]["status"] == "succeeded"
        transport.pop("on_fetch")
        # Provider now claims RUNNING; fetching the persisted success must not
        # query and replace the already observed successful result with it.
        transport["status"] = "RUNNING"
        calls = list(transport["calls"])
        response = client.post(RECOVER, json=expected_from(original))
        assert response.status_code == 200, response.text
        assert response.json()["task"]["provider_execution"]["phase"] == "completed"
        assert [call[:2] for call in transport["calls"][len(calls):]] == [("GET", RESULT)]
        assert submit_count(transport) == 1


@pytest.mark.parametrize("stage", ["query", "fetch"])
def test_recovery_rechecks_original_receipt_before_committing_a_late_result(runtime, transport, stage):
    item, _ = runtime
    with TestClient(app_for(item)) as client:
        original = submit_existing(client, transport)
        observed = {}

        def remove_live_receipt_during_transport():
            with item._save_lock:
                observed["saved"] = saved_files(item)
                record = item.read_committed_script("project").video_tasks[0].provider_execution
                assert record is not None
                observed["record"] = record.model_dump(mode="json")
                item.scripts["project"].provider_request_receipts.clear()

        transport["on_" + stage] = remove_live_receipt_during_transport
        transport["status"] = "SUCCEEDED"
        response = client.post(RECOVER, json=expected_from(original))
        assert response.status_code == 409, response.text
        assert response.json()["code"] == "motion_reference_recover_not_allowed"
        current = item.read_committed_script("project").video_tasks[0].provider_execution
        assert current is not None and current.model_dump(mode="json") == observed["record"]
        # Query may legitimately save its success before the fetch failpoint.
        # The fetch transport begins after Core creates its empty staging file;
        # cancellation must clean that temporary file without publishing bytes.
        staged = {
            path: digest for path, digest in observed["saved"].items()
            if re.fullmatch(r"video/\.fetch-[0-9a-f]{32}\.tmp", path)
        }
        assert len(staged) == (1 if stage == "fetch" else 0)
        assert all(digest == hashlib.sha256(b"").hexdigest() for digest in staged.values())
        persisted = {path: digest for path, digest in observed["saved"].items() if path not in staged}
        assert saved_files(item) == persisted
        assert current.prepared_artifact is None and current.artifact is None
        if stage == "query":
            assert not any(call[1] == RESULT for call in transport["calls"])
        assert submit_count(transport) == 1


@pytest.mark.parametrize("owner", ["script", "series", "global"])
def test_paid_public_recovery_keeps_changed_owner_inputs_as_unselected_history(runtime, transport, owner):
    item, _ = runtime
    target = move_owner(item, owner)
    assert target.full_body is not None
    with TestClient(app_for(item)) as client:
        original = submit_existing(client, transport)
        target.description = "A different later author intention"
        item._commit_storage("projects.json", "series.json", "library_assets.json")
        variants = [variant.model_dump(mode="json") for variant in target.full_body.video_variants]
        transport["status"] = "SUCCEEDED"
        response = client.post(RECOVER, json=expected_from(original))
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["request"] == original["request"]
        assert result["task"]["status"] == "needs_confirmation"
        assert result["task"]["provider_execution"]["phase"] == "completed"
        assert result["reason"] == "motion_reference_inputs_changed"
        proof = result["task"]["provider_execution"]["artifact"]
        assert (item.data_dir / proof["relative_path"]).read_bytes() == VIDEO
        assert [variant.model_dump(mode="json") for variant in target.full_body.video_variants] == variants
        assert target.full_body.selected_video_id == "selected-video"
        assert submit_count(transport) == 1


def test_public_recovery_after_real_reparse_keeps_paid_orphan_execution(runtime, transport, monkeypatch):
    item, _ = runtime
    extraction = real_extraction_transport(monkeypatch)
    with TestClient(app_for(item)) as client:
        original = submit_existing(client, transport)
        revision = client.get("/projects/project").json()["revision"]
        reparsed = client.put("/projects/project/reparse", json={
            "text": "A different story with no local entities", "expected_revision": revision,
        })
        assert reparsed.status_code == 200 and len(extraction) == 1, reparsed.text
        assert item.scripts["project"].characters == []
        transport["status"] = "SUCCEEDED"
        response = client.post(RECOVER, json=expected_from(original))
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["request"] == original["request"]
        assert result["task"]["provider_execution"]["phase"] == "completed"
        assert result["task"]["status"] == "needs_confirmation"
        proof = result["task"]["provider_execution"]["artifact"]
        assert (item.data_dir / proof["relative_path"]).read_bytes() == VIDEO
        assert item.scripts["project"].characters == [] and submit_count(transport) == 1


def test_new_public_commands_keep_existing_environment_and_remote_access_boundaries(runtime, transport):
    item, _ = runtime
    app = api.create_app(
        data_dir=item.data_dir, user_data_dir=item.data_dir.parent / "user", load_config=False,
        pipeline_instance=item, access_mode="remote", access_token="synthetic-access-token-32-characters-long",
        access_allowed_origins=["https://studio.example"],
    )
    with TestClient(app, base_url="https://studio.example") as client:
        token_header = {"Authorization": "Bearer synthetic-access-token-32-characters-long"}
        assert client.get("/runtime/environment", headers=token_header).status_code == 200
        before = saved_files(item)
        for method, path, kwargs in [
            ("get", PUBLIC, {"params": QUERY}),
            ("post", PUBLIC, {"json": {**COMMAND, "expected_target": "0" * 64}}),
            ("post", RECOVER, {"json": {"expected_attempt_id": "original", "expected_fingerprint": "0" * 64}}),
        ]:
            response = getattr(client, method)(path, **kwargs)
            assert response.status_code == 401, response.text
        headers = {**token_header, "X-LumenX-Environment": str(uuid.uuid4())}
        response = client.post(PUBLIC, json={**COMMAND, "expected_target": "0" * 64}, headers=headers)
        assert response.status_code == 409, response.text
        assert response.json()["code"] == "environment_changed"
        assert response.json().get("accepted") is not False
        assert saved_files(item) == before and transport["calls"] == []


def test_public_preflight_does_not_require_or_bind_credentials(runtime, transport):
    item, _ = runtime
    with TestClient(app_for(item)) as client:
        original = preflight(client)
        cleared = client.post("/config/env", json={"DASHSCOPE_API_KEY": ""})
        assert cleared.status_code == 200, cleared.text
        before = saved_files(item)
        assert preflight(client) == original
        assert saved_files(item) == before and transport["calls"] == []
        assert item.scripts["project"].video_tasks == []
        assert item.scripts["project"].provider_request_receipts == []


@pytest.mark.parametrize("newer_phase", ["completed", "succeeded"])
def test_actual_recovery_claim_does_not_regress_a_later_success(runtime, transport, monkeypatch, newer_phase):
    item, _ = runtime
    reached, release = threading.Event(), threading.Event()
    first_gate = threading.Lock()
    held_once = False
    actual_query = item.query_provider_video_task

    def hold_before_actual_query(*args, **kwargs):
        # Inject timing only: both requests still run the production primitive.
        nonlocal held_once
        with first_gate:
            hold = not held_once
            held_once = True
        if hold:
            reached.set()
            assert release.wait(timeout=10), "Test did not release its own claim boundary"
        return actual_query(*args, **kwargs)

    with TestClient(app_for(item)) as client:
        original = submit_existing(client, transport)
        monkeypatch.setattr(item, "query_provider_video_task", hold_before_actual_query)
        transport["status"] = "SUCCEEDED"
        with ThreadPoolExecutor(max_workers=1) as pool:
            older = pool.submit(client.post, RECOVER, json=expected_from(original))
            try:
                assert reached.wait(timeout=5), "Recovery did not reach its real query boundary"
                if newer_phase == "succeeded":
                    def unavailable_fetch():
                        raise requests.ConnectionError("synthetic succeeded but unfetched result")

                    transport["on_fetch"] = unavailable_fetch
                newer = client.post(RECOVER, json=expected_from(original))
                assert newer.status_code == 200, newer.text
                newer_record = newer.json()["task"]["provider_execution"]
                if newer_phase == "completed":
                    assert newer_record["phase"] == "completed"
                else:
                    assert newer_record["observation"]["status"] == "succeeded"
                    assert newer_record["artifact"] is None
                    transport.pop("on_fetch")
                calls = list(transport["calls"])
                # An obsolete QUERY here would destroy the saved success.
                transport["status"] = "RUNNING"
            finally:
                release.set()
            response = older.result(timeout=10)
            assert response.status_code == 200, response.text
            result = response.json()
            assert result["task"]["provider_execution"]["phase"] == "completed"
            assert result["request"] == original["request"]
            later_calls = [call[:2] for call in transport["calls"][len(calls):]]
            assert later_calls == ([] if newer_phase == "completed" else [("GET", RESULT)])
        assert submit_count(transport) == 1
        target = item.scripts["project"].characters[0]
        assert target.full_body is not None
        assert len(target.full_body.video_variants) == 2
        assert target.full_body.selected_video_id == "selected-video"


def test_recovery_revalidates_receipt_between_query_and_actual_fetch_claim(runtime, transport, monkeypatch):
    item, _ = runtime
    actual_fetch = item.fetch_provider_video_task
    observed = {}

    def change_receipt_before_actual_fetch(*args, **kwargs):
        # The query has released its claim. Change only the live receipt at the
        # next primitive boundary; do not replace either production operation.
        with item._save_lock:
            assert item._video_task_runs == {}
            record = item.read_committed_script("project").video_tasks[0].provider_execution
            assert record is not None and record.observation["status"] == "succeeded"
            observed["record"] = record.model_dump(mode="json")
            observed["saved"] = saved_files(item)
            item.scripts["project"].provider_request_receipts.clear()
        return actual_fetch(*args, **kwargs)

    with TestClient(app_for(item)) as client:
        original = submit_existing(client, transport)
        monkeypatch.setattr(item, "fetch_provider_video_task", change_receipt_before_actual_fetch)
        transport["status"] = "SUCCEEDED"
        response = client.post(RECOVER, json=expected_from(original))
        assert response.status_code == 409, response.text
        assert response.json()["code"] == "motion_reference_recover_not_allowed"
        assert saved_files(item) == observed["saved"]
        current = item.read_committed_script("project").video_tasks[0].provider_execution
        assert current is not None and current.model_dump(mode="json") == observed["record"]
        assert not any(call[1] == RESULT for call in transport["calls"])
        assert submit_count(transport) == 1


@pytest.mark.parametrize("history", [False, True])
@pytest.mark.parametrize("damage", ["missing", "corrupt"])
def test_public_completed_recovery_verifies_paid_file_without_network_or_projection(runtime, transport, history, damage):
    item, _ = runtime
    target = item.scripts["project"].characters[0]
    assert target.full_body is not None
    if history:
        def later_author_edit():
            with item._save_lock:
                target.description = "Later author inputs must not adopt this completed history"
                item._commit_storage("projects.json")

        transport["on_query"] = later_author_edit
    with TestClient(app_for(item)) as client:
        original = submit_existing(client, transport, status="SUCCEEDED")
        assert original["task"]["provider_execution"]["phase"] == "completed"
        assert original["task"]["status"] == ("needs_confirmation" if history else "completed")
        original_record = deepcopy(original["task"]["provider_execution"])
        proof = original_record["artifact"]
        path = item.data_dir / proof["relative_path"]
        if damage == "missing":
            path.unlink()
        else:
            path.write_bytes(b"synthetic damaged paid file")
        variants = [variant.model_dump(mode="json") for variant in target.full_body.video_variants]
        calls = list(transport["calls"])
        response = client.post(RECOVER, json=expected_from(original))
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["request"] == original["request"]
        assert result["task"]["provider_execution"] == original_record
        assert result["task"]["status"] == "needs_confirmation"
        assert result["reason"] == "provider_fetch_artifact_" + ("missing" if damage == "missing" else "invalid")
        assert transport["calls"] == calls
        assert [variant.model_dump(mode="json") for variant in target.full_body.video_variants] == variants
        assert target.full_body.selected_video_id == "selected-video"
        if damage == "missing":
            assert not path.exists()
        else:
            assert path.read_bytes() == b"synthetic damaged paid file"


@pytest.mark.parametrize("damage", ["missing", "corrupt"])
def test_public_prepared_recovery_preserves_proof_and_checks_actual_file(runtime, transport, monkeypatch, damage):
    item, _ = runtime
    actual_commit = item._storage.commit

    def crash_final_commit(payloads):
        tasks = payloads.get("projects.json", {}).get("project", {}).get("video_tasks", [])
        if any(task.get("provider_execution", {}).get("phase") == "completed" for task in tasks):
            raise StorageError("synthetic prepared-only public recovery fixture")
        return actual_commit(payloads)

    monkeypatch.setattr(item._storage, "commit", crash_final_commit)
    with TestClient(app_for(item), raise_server_exceptions=False) as client:
        response = client.post(LEGACY_PATH, json=COMMAND)
        assert response.status_code == 200, response.text
    saved = json.loads((item.data_dir / "projects.json").read_text())["project"]
    record = saved["video_tasks"][0]["provider_execution"]
    proof = record["prepared_artifact"]
    assert proof is not None and record["artifact"] is None
    path = item.data_dir / proof["relative_path"]
    assert path.read_bytes() == VIDEO
    if damage == "missing":
        path.unlink()
    else:
        path.write_bytes(b"synthetic corrupt prepared file")
    calls = list(transport["calls"])
    item.close()
    reopened = ComicGenPipeline(data_dir=item.data_dir, recover_orphan_tasks=True)
    try:
        with TestClient(app_for(reopened)) as client:
            original = original_request(client)
            response = client.post(RECOVER, json=expected_from(original))
            assert response.status_code == 200, response.text
            result = response.json()
            current = result["task"]["provider_execution"]
            assert result["request"] == original["request"]
            assert current["prepared_artifact"] == proof
            assert current["attempt_id"] == record["attempt_id"]
            assert current["fingerprint"] == record["fingerprint"]
            if damage == "missing":
                assert current["phase"] == "completed" and current["artifact"] == proof
                assert path.read_bytes() == VIDEO
                assert [call[:2] for call in transport["calls"][len(calls):]] == [("GET", RESULT)]
            else:
                assert current["artifact"] is None and current["phase"] != "completed"
                assert result["task"]["status"] == "needs_confirmation"
                assert result["reason"] == "provider_fetch_artifact_invalid"
                assert path.read_bytes() == b"synthetic corrupt prepared file"
                assert transport["calls"] == calls
            assert submit_count(transport) == 1
            assert not any(call[1] == TASK for call in transport["calls"][len(calls):])
    finally:
        reopened.close()


@pytest.mark.parametrize("other_identity", ["key", "project", "namespace"])
def test_public_recovery_does_not_adopt_another_public_receipt_identity(runtime, transport, other_identity):
    item, _ = runtime
    with TestClient(app_for(item)) as client:
        original = submit_existing(client, transport)
        path = RECOVER
        if other_identity == "key":
            path = PUBLIC + "/another-key/recover"
        elif other_identity == "project":
            item.scripts["another-project"] = Script.model_validate({
                "id": "another-project", "title": "Unrelated", "original_text": "Unrelated",
                "created_at": 1, "updated_at": 1,
            })
            item._commit_storage("projects.json")
            path = "/projects/another-project/motion_ref_requests/motion-request/recover"
        else:
            # Controlled, schema-valid persisted ordinary-video identity. Merely
            # sharing key/attempt/fingerprint must not make it a motion request.
            script = item.scripts["project"]
            task = script.video_tasks[0]
            task.motion_reference = None
            task.asset_id = None
            task.frame_id = "ordinary-frame"
            receipt = original["request"]
            script.provider_request_receipts = [ProviderRequestReceipt.model_validate({
                "entrypoint": "studio.provider_video.v1", "frame_id": "ordinary-frame",
                **{name: receipt[name] for name in (
                    "request_contract_version", "client_request_id", "request_fingerprint",
                    "created_at", "task_id", "attempt_id", "execution_fingerprint",
                )},
            })]
            item._commit_storage("projects.json")
        before, calls = saved_files(item), list(transport["calls"])
        response = client.post(path, json=expected_from(original))
        assert response.status_code == 404, response.text
        assert saved_files(item) == before and transport["calls"] == calls
        assert submit_count(transport) == 1
