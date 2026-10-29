"""The paid HTTP entrance must preserve durable batch request identity."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from src.apps.comic_gen import api
from src.utils.json_storage import StorageError
from tests.test_studio_provider_recovery import FakeProvider, _admission_request
from tests.test_studio_provider_recovery_dispatcher import _make_runtime


URL = "/projects/p/provider_video_tasks"


@pytest.fixture
def entry(tmp_path, monkeypatch):
    runtime = _make_runtime(tmp_path, monkeypatch, FakeProvider())
    scheduled = []
    monkeypatch.setattr(api.BackgroundTasks, "add_task", lambda self, func, *args, **kwargs: scheduled.append((func, args, kwargs)))
    app = api.create_app(data_dir=runtime.data_dir, pipeline_instance=runtime, load_config=False, recover_orphan_tasks=False)
    with TestClient(app) as client:
        yield runtime, client, scheduled


def payload(**overrides):
    return {**_admission_request(), "client_request_id": "action-1", "batch_size": 3, **overrides}


def test_batch_entry_commits_all_members_before_scheduling_only_provider_submit(entry):
    runtime, client, scheduled = entry
    response = client.post(URL, json=payload())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["client_request_id"] == "action-1" and body["replayed"] is False
    assert body["reason"] is None
    assert len(body["task_ids"]) == 3
    assert [task["id"] for task in body["tasks"]] == body["task_ids"]
    assert all(task["provider_execution"]["phase"] == "accepted" for task in body["tasks"])
    assert len(runtime.read_committed_script("p").provider_request_receipts) == 1
    assert [(func, args) for func, args, _ in scheduled] == [
        (runtime.submit_provider_video_task, ("p", task_id)) for task_id in body["task_ids"]
    ]
    assert runtime.provider_recovery_runtime.provider.submits == []


def test_replay_uses_original_members_without_runtime_media_or_dispatch(entry):
    runtime, client, scheduled = entry
    first = client.post(URL, json=payload()).json()
    assert "task_ids" in first
    before = (runtime.data_dir / "projects.json").read_bytes()
    runtime.provider_recovery_runtime = None
    (runtime.data_dir / "uploads/source.png").unlink()
    scheduled.clear()
    response = client.post(URL, json=payload())
    assert response.status_code == 200
    assert response.json()["replayed"] is True
    assert response.json()["task_ids"] == first["task_ids"]
    assert scheduled == []
    assert (runtime.data_dir / "projects.json").read_bytes() == before


@pytest.mark.parametrize("changed", [{"prompt": "changed"}, {"batch_size": 2}, {"frame_id": "other"}, {"seed": None}])
def test_same_id_different_payload_returns_typed_conflict_without_new_work(entry, changed):
    runtime, client, scheduled = entry
    assert client.post(URL, json=payload()).status_code == 200
    runtime.provider_recovery_runtime = None
    scheduled.clear()
    response = client.post(URL, json=payload(**changed))
    assert response.status_code == 409
    assert response.json()["code"] == "provider_request_conflict"
    assert len(runtime.scripts["p"].video_tasks) == 3 and scheduled == []


@pytest.mark.parametrize("changed", [
    {"client_request_id": None}, {"client_request_id": "bad id"},
    {"batch_size": True}, {"batch_size": "2"}, {"batch_size": 0}, {"batch_size": 7},
])
def test_bad_identity_and_batch_cannot_create_tasks(entry, changed):
    runtime, client, scheduled = entry
    response = client.post(URL, json=payload(**changed))
    assert response.status_code == 422
    assert runtime.scripts["p"].video_tasks == [] and scheduled == []


def test_missing_identity_never_enters_legacy_paid_path(entry):
    runtime, client, scheduled = entry
    request = payload()
    del request["client_request_id"]
    assert client.post(URL, json=request).status_code == 422
    assert runtime.scripts["p"].video_tasks == [] and scheduled == []


@pytest.mark.parametrize("changed", [{"model": "kling-v2"}, {"surprise": True}, {"watermark": False}, {"generation_mode": "r2v"}])
def test_unsupported_receipt_request_never_falls_back(entry, changed):
    runtime, client, scheduled = entry
    response = client.post(URL, json=payload(**changed))
    assert response.status_code == 422
    assert response.json()["code"] == "provider_request_invalid"
    assert runtime.scripts["p"].video_tasks == [] and scheduled == []


def test_failed_atomic_save_schedules_nothing(entry, monkeypatch):
    runtime, client, scheduled = entry
    before = (runtime.data_dir / "projects.json").read_bytes()
    monkeypatch.setattr(runtime._storage, "commit", Mock(side_effect=StorageError("disk unavailable")))
    assert client.post(URL, json=payload()).status_code == 503
    assert (runtime.data_dir / "projects.json").read_bytes() == before
    assert runtime.scripts["p"].video_tasks == [] and scheduled == []


def test_deleted_task_replay_reports_tombstone_without_rebuilding(entry):
    runtime, client, scheduled = entry
    first = client.post(URL, json=payload()).json()
    assert "task_ids" in first
    runtime.scripts["p"].video_tasks.pop()
    runtime._commit_storage("projects.json")
    scheduled.clear()
    response = client.post(URL, json=payload())
    assert response.status_code == 200
    body = response.json()
    assert body["replayed"] and body["reason"] == "provider_request_deleted"
    assert body["task_ids"] == first["task_ids"] and len(body["tasks"]) == 2
    assert scheduled == []


def test_concurrent_same_request_only_one_response_grants_dispatch(entry):
    runtime, client, scheduled = entry
    previous = runtime.provider_recovery_runtime
    barrier = Barrier(2)

    def resolve():
        barrier.wait(timeout=3)
        return previous.resolve_secret()

    runtime.provider_recovery_runtime = replace(previous, secret_resolver=resolve)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: client.post(URL, json=payload()), range(2)))
    assert [response.status_code for response in results] == [200, 200]
    assert sorted(response.json()["replayed"] for response in results) == [False, True]
    assert results[0].json()["task_ids"] == results[1].json()["task_ids"]
    assert len(scheduled) == 3 and len(runtime.scripts["p"].video_tasks) == 3


def test_old_video_route_retains_legacy_contract_and_dispatch(entry):
    runtime, client, scheduled = entry
    response = client.post("/projects/p/video_tasks", json={**_admission_request(), "batch_size": 2})
    assert response.status_code == 200, response.text
    assert isinstance(response.json(), list) and len(response.json()) == 2
    assert all(task["provider_execution"] is None for task in response.json())
    assert all(func is api.process_video_task for func, _, _ in scheduled)
    assert runtime.scripts["p"].provider_request_receipts == []


def test_real_app_dispatches_submits_and_recovers_results_without_replay_payment(tmp_path, monkeypatch):
    import time
    from threading import Event

    from src.apps.comic_gen import provider_recovery_dispatcher as dispatch
    from src.models.provider_tasks import ProviderTaskStatus
    from src.models.wanx import WanxModel
    from tests.test_studio_provider_recovery import SECRET

    provider = FakeProvider()
    provider.query_status = ProviderTaskStatus.SUCCEEDED
    provider.query_video_url = "https://example.invalid/result.mp4"
    runtime = _make_runtime(tmp_path, monkeypatch, provider)
    runtime.provider_recovery_runtime = None
    monkeypatch.setenv("DASHSCOPE_API_KEY", SECRET)

    def submit(self, request, **kwargs):
        provider.next_task_id = f"remote-{len(provider.submits) + 1}"
        return provider.submit(request, **kwargs)

    monkeypatch.setattr(WanxModel, "submit", submit)
    monkeypatch.setattr(WanxModel, "query", lambda self, *args, **kwargs: provider.query(*args, **kwargs))
    monkeypatch.setattr(WanxModel, "fetch", lambda self, *args, **kwargs: provider.fetch(*args, **kwargs))
    cls = dispatch.StudioProviderRecoveryDispatcher
    monkeypatch.setattr(dispatch, "StudioProviderRecoveryDispatcher", lambda p: cls(p, interval_seconds=0.01))
    app = api.create_app(data_dir=runtime.data_dir, pipeline_instance=runtime, load_config=False, recover_orphan_tasks=True)
    with TestClient(app) as client:
        assert app.state.provider_recovery_runtime_owner == "managed"
        result = client.post(URL, json=payload(batch_size=2))
        assert result.status_code == 200, result.text
        ids = result.json()["task_ids"]
        assert len(provider.submits) == 2
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            tasks = runtime.read_committed_script("p").video_tasks
            if all(task.status == "completed" for task in tasks):
                break
            Event().wait(0.01)
        tasks = runtime.read_committed_script("p").video_tasks
        assert [task.id for task in tasks] == ids
        assert all(task.status == "completed" for task in tasks)
        assert all((runtime.data_dir / task.video_url).read_bytes() == provider.fetch_bytes for task in tasks)
        replay = client.post(URL, json=payload(batch_size=2))
        assert replay.status_code == 200 and replay.json()["replayed"]
        assert replay.json()["task_ids"] == ids
        assert all(task["status"] == "completed" for task in replay.json()["tasks"])
        assert len(provider.submits) == len(provider.queries) == len(provider.fetches) == 2
