"""Strict Studio image request HTTP is a safe projection over retained receipts."""

from copy import deepcopy
from importlib import import_module
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from src.apps.comic_gen import api
from src.apps.comic_gen.asset_image_execution import AssetImageAdmission, AssetImageRequestCommand, AssetImageRequestNotFound
from src.apps.comic_gen.asset_image_models import AssetImageRequestRecord
from src.apps.comic_gen.pipeline import ProjectContextError
from src.utils.json_storage import StorageConflictError, StorageError
from tests import test_studio_core_image_admission as admission
from tests.studio_wan_http_harness import WanImageHttpHarness, assert_png
from tests.test_app_access_boundary import StubPipeline
from tests.test_studio_core_image_models import execution_payload, request_payload

STORAGE_ID = "11111111-1111-4111-8111-111111111111"
TOKEN = "fake-access-" * 4
real_runtime = admission.runtime


def receipt(route="project-local", *, phases=("accepted",)):
    raw = request_payload(route)
    raw["members"] = [
        {"variant_id": f"attempt-{index}", "execution": execution_payload(attempt=f"attempt-{index}", phase=phase)}
        for index, phase in enumerate(phases)
    ]
    return AssetImageRequestRecord.model_validate(raw)


def member_state(record, statuses=("pending",)) -> dict[str, Any]:
    members = []
    for member, status in zip(record.members, statuses, strict=True):
        item = {"variant_id": member.variant_id, "status": status, "reason": None}
        if status == "completed":
            item["output_ref"] = member.execution.output_ref
        members.append(item)
    return {"status": statuses[0], "progress": 0, "members": members}


def service_for(record):
    return SimpleNamespace(
        admit=Mock(return_value=AssetImageAdmission(record, False, None)),
        read_request=Mock(return_value=AssetImageAdmission(record, True, None)),
        request_state=Mock(return_value=member_state(record)),
        run_original_request=Mock(),
        abandon_pending_original_claim=Mock(),
        repair_local_request=Mock(side_effect=AssertionError("GET attempted repair")),
    )


def app_for(tmp_path: Path, service, **kwargs):
    pipeline = StubPipeline(tmp_path / "data")
    setattr(pipeline, "asset_image_service", service)
    setattr(pipeline, "storage_identity", {"schema_version": 1, "storage_id": STORAGE_ID})
    app = api.create_app(data_dir=pipeline.data_dir, load_config=False, pipeline_instance=pipeline, **kwargs)
    return app, pipeline


@pytest.fixture(params=[("project", "projects", "project-root", "project-local"), ("series", "series", "series-root", "standalone-series")])
def runtime(request, tmp_path, monkeypatch):
    root_kind, collection, root_id, route = request.param
    record = receipt(route)
    service = service_for(record)
    monkeypatch.setattr(api, "signed_response", Mock(side_effect=AssertionError("strict wire attempted URL signing")))
    app, pipeline = app_for(tmp_path, service)
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, service=service, record=record, pipeline=pipeline,
                              kind=root_kind, root_id=root_id, path=f"/{collection}/{root_id}/asset_image_requests")


def command(**changes):
    return {"client_request_id": "caller.request:1", "asset_id": "legacy-character", **changes}


def wire(result, state):
    return import_module("src.apps.comic_gen.asset_image_wire").asset_image_request_wire(result, state)


def test_post_first_admission_schedules_only_original_request_and_preserves_wire(runtime):
    response = runtime.client.post(runtime.path, json=command(seed=0, watermark=False))
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["schema_version"] == 1 and result["contract_version"] == "studio-asset-image-request-v1"
    assert result["replayed"] is False
    assert result["request"]["request_root_kind"] == runtime.kind
    assert "request_root_series_id" in result["request"]
    assert result["request"]["request_root_series_id"] is None
    assert result["request"]["members"] == [{"variant_id": "attempt-0", "attempt_id": "attempt-0", "status": "pending", "reason": None}]
    runtime.service.admit.assert_called_once()
    kind, identity, received = runtime.service.admit.call_args.args
    assert (kind, identity) == (runtime.kind, runtime.root_id)
    assert isinstance(received, AssetImageRequestCommand)
    assert received.model_dump(exclude_unset=True) == command(seed=0, watermark=False)
    runtime.service.run_original_request.assert_called_once_with(runtime.record.id)
    runtime.service.read_request.assert_not_called()
    runtime.service.repair_local_request.assert_not_called()


@pytest.mark.parametrize("raw", [command(), command(prompt=None), command(prompt=""), command(apply_style=False, seed=0, watermark=False)])
def test_request_field_presence_reaches_admission_unchanged(runtime, raw):
    response = runtime.client.post(runtime.path, json=raw)
    assert response.status_code == 200, response.text
    received = runtime.service.admit.call_args.args[2]
    assert received.model_dump(exclude_unset=True) == raw


def test_post_replay_never_schedules_worker(runtime):
    runtime.service.admit.return_value = AssetImageAdmission(runtime.record, True, "studio_image_request_target_changed")
    response = runtime.client.post(runtime.path, json=command())
    assert response.status_code == 200, response.text
    assert response.json()["replayed"] is True
    assert response.json()["reason"] == "studio_image_request_target_changed"
    runtime.service.run_original_request.assert_not_called()
    runtime.service.repair_local_request.assert_not_called()


def test_get_only_reads_original_receipt_and_state(runtime):
    response = runtime.client.get(runtime.path + "/caller.request:1")
    assert response.status_code == 200, response.text
    assert response.json()["replayed"] is True
    runtime.service.read_request.assert_called_once_with(runtime.kind, runtime.root_id, "caller.request:1")
    runtime.service.request_state.assert_called_once_with(runtime.record)
    runtime.service.admit.assert_not_called()
    runtime.service.run_original_request.assert_not_called()
    runtime.service.repair_local_request.assert_not_called()


@pytest.mark.parametrize("method", ("post", "get"))
@pytest.mark.parametrize("error,status,code", [
    (AssetImageRequestNotFound("studio_image_request_not_found"), 404, "studio_image_request_not_found"),
    (StorageConflictError("studio_image_request_payload_conflict"), 409, "storage_conflict"),
    (StorageError("synthetic storage failure"), 503, "storage_unavailable"),
    (ProjectContextError("missing root", not_found=True), 404, None),
    (ProjectContextError("invalid target"), 422, None),
    (ValueError("studio_image_model_not_supported"), 422, None),
])
def test_domain_errors_keep_http_distinctions_and_never_start_work(runtime, method, error, status, code):
    operation = runtime.service.admit if method == "post" else runtime.service.read_request
    operation.side_effect = error
    response = (runtime.client.post(runtime.path, json=command()) if method == "post" else
                runtime.client.get(runtime.path + "/caller.request:1"))
    assert response.status_code == status, response.text
    if code:
        assert response.json()["code"] == code
    runtime.service.run_original_request.assert_not_called()
    runtime.service.repair_local_request.assert_not_called()


@pytest.mark.parametrize("field,value", [
    ("client_request_id", "invalid key"), ("client_request_id", None),
    ("asset_id", 42), ("asset_type", "scene"), ("generation_type", "all"),
    ("batch_size", True), ("batch_size", "1"), ("batch_size", 0),
    ("seed", None), ("seed", True), ("seed", -1), ("seed", 2147483648),
    ("watermark", 0), ("apply_style", "false"), ("prompt", ["bad"]),
    ("prompt_extend", False), ("unknown", "must not be ignored"),
    ("seed", float("nan")), ("batch_size", float("inf")),
])
def test_strict_shape_is_rejected_before_admission(runtime, field, value):
    response = runtime.client.post(runtime.path, content=json.dumps(command(**{field: value})), headers={"Content-Type": "application/json"})
    assert response.status_code == 422, response.text
    assert all("input" not in item for item in response.json()["detail"])
    runtime.service.admit.assert_not_called()
    runtime.service.run_original_request.assert_not_called()


@pytest.mark.parametrize("method", ("post", "get"))
def test_access_and_environment_gate_replay_before_service(tmp_path, method):
    record = receipt()
    service = service_for(record)
    service.admit.return_value = AssetImageAdmission(record, True, None)
    app, _ = app_for(tmp_path, service, access_mode="remote", access_token=TOKEN)
    with TestClient(app) as client:
        def send(headers):
            path = "/projects/project-root/asset_image_requests"
            return client.post(path, json=command(), headers=headers) if method == "post" else client.get(path + "/caller.request:1", headers=headers)
        assert send({}).status_code == 401
        bad = {"Authorization": f"Bearer {TOKEN}", "X-LumenX-Environment": "22222222-2222-4222-8222-222222222222"}
        assert send(bad).status_code == 409
        service.admit.assert_not_called()
        service.read_request.assert_not_called()
        response = send({"Authorization": f"Bearer {TOKEN}", "X-LumenX-Environment": STORAGE_ID})
        assert response.status_code == 200, response.text
        service.run_original_request.assert_not_called()


def test_wire_only_exposes_ordered_public_identity_and_verified_completed_output():
    record = receipt(phases=("accepted", "submitting", "accepted", "completed", "cancelled"))
    state = member_state(record, ("pending", "processing", "needs_confirmation", "completed", "cancelled"))
    state["members"][2]["output_ref"] = record.members[2].execution.output_ref
    state["members"][0]["credential_ref"] = "must-not-leak"
    result = wire(AssetImageAdmission(record, True, None), state)
    public = result["request"]
    assert set(public) == {"id", "entrypoint", "request_contract_version", "client_request_id", "request_fingerprint",
                           "request_root_kind", "request_root_id", "request_root_scope_id", "request_root_series_id",
                           "owner_kind", "owner_id", "asset_type", "asset_id", "channel", "members"}
    assert [item["attempt_id"] for item in public["members"]] == [f"attempt-{i}" for i in range(5)]
    for i, member in enumerate(public["members"]):
        assert member["variant_id"] == member["attempt_id"]
        assert ("output_ref" in member) is (i == 3)
    assert public["members"][3]["output_ref"] == "assets/studio/attempt-3.png"
    encoded = json.dumps(result)
    for hidden in ("frozen-credential", "must-not-leak", "dashscope.aliyuncs.com", "input_guard", "prepared_artifact", "projection_guard"):
        assert hidden not in encoded


@pytest.mark.parametrize("damage", ("empty", "reverse", "duplicate", "wrong-variant", "unknown-status", "missing-output", "wrong-output", "reason-shape"))
def test_wire_rejects_state_that_does_not_match_the_retained_request(damage):
    record = receipt(phases=("completed", "accepted"))
    state = member_state(record, ("completed", "pending"))
    members = state["members"]
    if damage == "empty":
        members.clear()
    elif damage == "reverse":
        members.reverse()
    elif damage == "duplicate":
        members[1] = deepcopy(members[0])
    elif damage == "wrong-variant":
        members[0]["variant_id"] = "someone-else"
    elif damage == "unknown-status":
        members[0]["status"] = "failed"
    elif damage == "missing-output":
        del members[0]["output_ref"]
    elif damage == "wrong-output":
        members[0]["output_ref"] = "https://invalid.example/result.png"
    else:
        members[0]["reason"] = {"error": "raw provider error"}
    with pytest.raises(StorageError):
        wire(AssetImageAdmission(record, True, None), state)


@pytest.mark.parametrize("method", ("post", "get"))
def test_receipt_removed_during_state_read_remains_missing_without_work(runtime, method):
    runtime.service.request_state.side_effect = AssetImageRequestNotFound("studio_image_request_not_found")
    response = (runtime.client.post(runtime.path, json=command()) if method == "post" else
                runtime.client.get(runtime.path + "/caller.request:1"))
    assert response.status_code == 404, response.text
    assert response.json()["code"] == "studio_image_request_not_found"
    runtime.service.run_original_request.assert_not_called()
    runtime.service.repair_local_request.assert_not_called()


@pytest.fixture
def real_executor(monkeypatch):
    # The actual app, admission, async Core and private normalization stay real.
    return WanImageHttpHarness(monkeypatch)


def real_app(item):
    return api.create_app(data_dir=item.data_dir, load_config=False, pipeline_instance=item)


def enable_fake_key(app):
    app.state.runtime_environment.update({"DASHSCOPE_API_KEY": "fake-studio-image-key"}, persist=lambda *_: None)


@pytest.mark.parametrize("route", admission.ROUTES)
def test_real_http_admission_worker_and_readonly_recovery_share_actual_owner(real_runtime, real_executor, route, monkeypatch):
    root_kind, root_id = admission.setup(real_runtime, route)
    calls = real_executor.records
    service = real_runtime.asset_image_service
    assert real_runtime.asset_image_service is service
    monkeypatch.setattr(service, "repair_request", Mock(side_effect=AssertionError("HTTP attempted repair")))
    monkeypatch.setattr(api, "signed_response", Mock(side_effect=AssertionError("strict wire attempted URL signing")))
    path = f"/{'projects' if root_kind == 'project' else 'series'}/{root_id}/asset_image_requests"
    raw = admission.command(batch_size=2, seed=0, watermark=False).model_dump(exclude_unset=True)
    app = real_app(real_runtime)
    with TestClient(app) as client:
        enable_fake_key(app)
        response = client.post(path, json=raw)
        assert response.status_code == 200, response.text
        first = response.json()
        assert first["replayed"] is False
        assert [m["status"] for m in first["request"]["members"]] == ["pending", "pending"]
        assert len(calls) == len(real_executor.posts) == len(real_executor.queries) == len(real_executor.downloads) == 2
        assert all(c.intent["params"]["seed"] == 0 and c.intent["params"]["watermark"] is False for c in calls)
        stored = admission.owner(real_runtime, route).asset_image_requests[0]
        assert first["request"]["owner_kind"] == stored.owner_kind
        assert first["request"]["owner_id"] == stored.owner_id
        assert first["request"]["id"] == stored.id
        assert [m["attempt_id"] for m in first["request"]["members"]] == [m.execution.attempt_id for m in stored.members]
        before = {name: (real_runtime.data_dir / name).read_bytes() for name in ("projects.json", "series.json", "library_assets.json")}
        recovered = client.get(path + "/request-1")
        assert recovered.status_code == 200, recovered.text
        assert recovered.json()["replayed"] is True
        for member in recovered.json()["request"]["members"]:
            assert member["status"] == "completed"
            assert member["output_ref"] == f"assets/studio/{member['attempt_id']}.png"
            assert_png(real_runtime.data_dir / member["output_ref"])
        assert before == {name: (real_runtime.data_dir / name).read_bytes() for name in before}
        app.state.runtime_environment.update({}, remove_keys=("DASHSCOPE_API_KEY",), persist=lambda *_: None)
        monkeypatch.setattr(real_runtime, "_prepare_asset_image", Mock(side_effect=AssertionError("replay prepared again")))
        monkeypatch.setattr(admission.api(), "bind_media_runtime_authority", Mock(side_effect=AssertionError("replay bound again")))
        replay = client.post(path, json=raw)
        assert replay.status_code == 200, replay.text
        assert replay.json()["replayed"] is True
        assert replay.json()["request"] == recovered.json()["request"]
        conflict = client.post(path, json={**raw, "prompt": None})
        assert conflict.status_code == 409 and conflict.json()["code"] == "storage_conflict"
        assert len(calls) == len(real_executor.posts) == len(real_executor.queries) == len(real_executor.downloads) == 2
        assert before == {name: (real_runtime.data_dir / name).read_bytes() for name in before}
        service.repair_request.assert_not_called()


@pytest.mark.parametrize("route", admission.ROUTES)
def test_real_restart_get_and_replay_preserve_unconfirmed_receipt_without_execution(real_runtime, route, monkeypatch):
    root_kind, root_id = admission.setup(real_runtime, route)
    first = real_runtime.asset_image_service.admit(root_kind, root_id, admission.command())
    real_runtime.close()
    other = admission.pipeline_module.ComicGenPipeline(data_dir=real_runtime.data_dir, script_processor=Mock())
    service = other.asset_image_service
    worker = Mock(side_effect=AssertionError("restart executed"))
    repair = Mock(side_effect=AssertionError("GET repaired"))
    monkeypatch.setattr(service, "run_original_request", worker)
    monkeypatch.setattr(service, "repair_request", repair)
    monkeypatch.setattr(admission.api(), "bind_media_runtime_authority", Mock(side_effect=AssertionError("restart bound")))
    path = f"/{'projects' if root_kind == 'project' else 'series'}/{root_id}/asset_image_requests"
    app = real_app(other)
    with TestClient(app) as client:
        before = {name: (other.data_dir / name).read_bytes() for name in ("projects.json", "series.json", "library_assets.json")}
        response = client.get(path + "/request-1")
        assert response.status_code == 200, response.text
        recovered = response.json()
        assert recovered["request"]["id"] == first.request.id
        assert recovered["request"]["members"][0]["status"] == "needs_confirmation"
        assert "output_ref" not in recovered["request"]["members"][0]
        replay = client.post(path, json=admission.command().model_dump(exclude_unset=True))
        assert replay.status_code == 200 and replay.json() == recovered
        missing = client.get(path + "/missing-key")
        assert missing.status_code == 404 and missing.json()["code"] == "studio_image_request_not_found"
        assert before == {name: (other.data_dir / name).read_bytes() for name in before}
        worker.assert_not_called()
        repair.assert_not_called()


@pytest.mark.parametrize("failure", ["deleted-owner", "state-error", "wire-error", "response-error", "schedule-error"])
@pytest.mark.parametrize("route", ["local", "standalone"])
def test_first_admission_response_failure_releases_only_unstarted_claim(real_runtime, real_executor, monkeypatch, failure, route):
    from fastapi import BackgroundTasks
    root_kind, root_id = admission.setup(real_runtime, route)
    original_root = admission.owner(real_runtime, route).model_copy(deep=True)
    service = real_runtime.asset_image_service
    calls = real_executor.records
    read = service.request_state
    failure_seen = False
    receipt_before = []
    def state(record):
        nonlocal failure_seen
        if not failure_seen:
            failure_seen = True
            receipt_before.append(record.model_copy(deep=True))
            if failure == "deleted-owner":
                store = real_runtime.scripts if root_kind == "project" else real_runtime.series_store
                del store[root_id]
                store[root_id] = original_root.model_copy(deep=True)
                real_runtime._commit_storage("projects.json" if root_kind == "project" else "series.json")
            elif failure == "state-error":
                raise StorageError("synthetic state failure")
        return read(record)
    monkeypatch.setattr(service, "request_state", state)
    wire_builder, response_builder, schedule = api.asset_image_request_wire, api.JSONResponse, BackgroundTasks.add_task
    if failure == "wire-error":
        def wire_failure(*args, **kwargs):
            monkeypatch.setattr(api, "asset_image_request_wire", wire_builder)
            raise StorageError("synthetic wire failure")
        monkeypatch.setattr(api, "asset_image_request_wire", wire_failure)
    elif failure == "response-error":
        def response_failure(*args, **kwargs):
            monkeypatch.setattr(api, "JSONResponse", response_builder)
            raise StorageError("synthetic response failure")
        monkeypatch.setattr(api, "JSONResponse", response_failure)
    elif failure == "schedule-error":
        def schedule_failure(*args, **kwargs):
            monkeypatch.setattr(BackgroundTasks, "add_task", schedule)
            raise StorageError("synthetic schedule failure")
        monkeypatch.setattr(BackgroundTasks, "add_task", schedule_failure)
    app = real_app(real_runtime)
    path = f"/{'projects' if root_kind == 'project' else 'series'}/{root_id}/asset_image_requests"
    with TestClient(app) as client:
        enable_fake_key(app)
        response = client.post(path, json=admission.command().model_dump(exclude_unset=True))
        assert response.status_code == (404 if failure == "deleted-owner" else 503), response.text
        assert calls == [] and real_executor.http_calls == []
        assert real_runtime.asset_generation_tasks == {}
        assert real_runtime._asset_image_runs == {}
        if failure != "deleted-owner":
            retained = service.read_request(root_kind, root_id, "request-1").request
            assert retained == receipt_before[0]
            assert retained.members[0].execution.phase == "accepted"
            assert service.request_state(retained)["status"] == "needs_confirmation"
            replay = client.post(path, json=admission.command().model_dump(exclude_unset=True))
            assert replay.status_code == 200 and replay.json()["replayed"] is True
            assert calls == [] and real_executor.http_calls == [] and real_runtime.asset_generation_tasks == {}
        fresh = client.post(path, json=admission.command(client_request_id="explicit-new-request").model_dump(exclude_unset=True))
        assert fresh.status_code == 200 and fresh.json()["replayed"] is False
        assert len(calls) == len(real_executor.posts) == len(real_executor.queries) == len(real_executor.downloads) == 1


@pytest.mark.parametrize("phase", ["pending", "processing", "completed", "superseded", "legacy", "missing"])
def test_abandon_claim_checks_original_pending_identity_without_storage_or_provider(real_runtime, real_executor, monkeypatch, phase):
    root = admission.setup(real_runtime, "local")
    service = real_runtime.asset_image_service
    calls = real_executor.records
    record = service.admit(*root, admission.command()).request
    original_task = real_runtime.asset_generation_tasks[record.id]
    key = original_task["_owner_key"]
    token = original_task["_token"]
    if phase in {"processing", "completed"}:
        original_task["status"] = phase
    elif phase == "superseded":
        real_runtime._asset_image_runs[key] = object()
    elif phase == "legacy":
        original_task.pop("_core_image_request_id")
    elif phase == "missing":
        real_runtime.asset_generation_tasks.clear()
    before = {name: (real_runtime.data_dir / name).read_bytes() for name in ("projects.json", "series.json", "library_assets.json")}
    registry_before = dict(real_runtime._asset_image_runs)
    tasks_before = dict(real_runtime.asset_generation_tasks)
    real_runtime._storage_failed = StorageError("synthetic unavailable storage")
    monkeypatch.setattr(real_runtime, "_commit_storage", Mock(side_effect=AssertionError("cleanup persisted")))
    service.abandon_pending_original_claim(record.id)
    service.abandon_pending_original_claim(record.id)
    if phase == "pending":
        assert real_runtime.asset_generation_tasks == {} and key not in real_runtime._asset_image_runs
    else:
        assert real_runtime.asset_generation_tasks == tasks_before
        assert real_runtime._asset_image_runs == registry_before
    assert original_task["_token"] is token and calls == [] and real_executor.http_calls == []
    assert before == {name: (real_runtime.data_dir / name).read_bytes() for name in before}


def test_replay_response_failure_does_not_release_original_pending_worker(real_runtime, real_executor, monkeypatch):
    root = admission.setup(real_runtime, "local")
    service = real_runtime.asset_image_service
    app = real_app(real_runtime)
    calls = real_executor.records
    with TestClient(app) as client:
        enable_fake_key(app)
        original = service.admit(*root, admission.command()).request
        task = real_runtime.asset_generation_tasks[original.id]
        monkeypatch.setattr(service, "request_state", Mock(side_effect=StorageError("synthetic replay state failure")))
        response = client.post("/projects/p/asset_image_requests", json=admission.command().model_dump(exclude_unset=True))
        assert response.status_code == 503
        assert real_runtime.asset_generation_tasks[original.id] is task and task["status"] == "pending"
        assert real_runtime._asset_image_runs[task["_owner_key"]] is task["_token"]
        assert calls == [] and real_executor.http_calls == []


@pytest.mark.parametrize("failure", ["state", "wire", "response", "schedule"])
def test_cleanup_preserves_unexpected_response_preparation_exception(real_runtime, monkeypatch, failure):
    from fastapi import BackgroundTasks
    root = admission.setup(real_runtime, "local")
    service = real_runtime.asset_image_service
    original_error = RuntimeError("synthetic unexpected response failure")
    throw = Mock(side_effect=original_error)
    tasks = BackgroundTasks()
    if failure == "state":
        monkeypatch.setattr(service, "request_state", throw)
    elif failure == "wire":
        monkeypatch.setattr(api, "asset_image_request_wire", throw)
    elif failure == "response":
        monkeypatch.setattr(api, "JSONResponse", throw)
    else:
        monkeypatch.setattr(tasks, "add_task", throw)
    with pytest.raises(RuntimeError) as raised:
        api._admit_asset_image_request(*root, admission.command(), tasks, real_runtime)
    assert raised.value is original_error
    assert real_runtime.asset_generation_tasks == {} and real_runtime._asset_image_runs == {}
    assert tasks.tasks == []
    stored = real_runtime.scripts['p'].asset_image_requests
    assert len(stored) == 1 and stored[0].members[0].execution.phase == "accepted"
