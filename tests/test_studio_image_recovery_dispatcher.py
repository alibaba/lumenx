"""Prepared B4 regressions: real receipts/owners and one shared recovery worker.

NOT_RUN. External image HTTP is the existing bounded transport double; actual
admission, Core adapters, JSON transactions, PNG handling and app lifespans stay
real. Close/reopen and an explicit worker interruption are not OS crash tests.
"""

from copy import deepcopy
import json
from pathlib import Path
from threading import Event, Thread
import time

import pytest
import requests
from fastapi.testclient import TestClient

from src.apps.comic_gen import api, asset_image_execution as execution
from src.apps.comic_gen import provider_recovery_dispatcher as dispatch
from src.apps.comic_gen.models import Character, Scene, Script, StoryboardFrame
from src.utils.json_storage import StorageError
from tests import test_studio_image_known_handle_recovery as images
from tests import test_studio_core_image_admission as admission
from tests import test_studio_gpt_reference_sheet_core as gpt
from tests import test_studio_provider_recovery as video
from tests.test_studio_provider_recovery_dispatcher import FakeClock
from tests.test_wan27_bound_image import response

runtime = images.runtime
transport = images.transport
waiting_clock = images.waiting_clock


def dispatcher(item, **kwargs):
    return dispatch.StudioProviderRecoveryDispatcher(item, **{
        "interval_seconds": 0.01, "transient_backoff_seconds": 60, **kwargs,
    })


def identity(saved):
    return saved["id"], saved["members"][0]["execution"]["attempt_id"]


def saved_request(item, saved):
    filename = {"script": "projects.json", "series": "series.json", "global": "library_assets.json"}[saved["owner_kind"]]
    raw = json.loads((item.data_dir / filename).read_text())
    owner = raw if saved["owner_kind"] == "global" else raw[saved["owner_id"]]
    return next(record for record in owner["asset_image_requests"] if record["id"] == saved["id"])


def owner_bytes(item):
    return {name: (item.data_dir / name).read_bytes()
            for name in ("projects.json", "series.json", "library_assets.json")}


def selected_reference_sheet(item):
    unit = item.scripts["p"].characters[0].reference_sheet
    assert unit is not None
    return unit.selected_image_id


def configure_gpt_environment(monkeypatch):
    for key in ("DASHSCOPE_API_KEY", "DASHSCOPE_WORKSPACE", "MULERUN_TOKEN",
                "MULERUN_API_BASE_URL", "MULERUN_BASE_URL", "MULEROUTER_BASE_URL"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("MULEROUTER_API_KEY", gpt.HTTP_SECRET)
    monkeypatch.setenv("MULERUN_SITE", "sg")
    monkeypatch.setenv("MULERUN_STUDIO_SITE", "mulerouter")


def add_video(item, provider):
    source = item.data_dir / "uploads/source.png"
    source.write_bytes(images.PNG)
    root = item.scripts["p"]
    root.scenes.append(Scene.model_validate({'id': "scene", 'name': "Room", 'description': "quiet", 'image_url': "uploads/source.png"}))
    root.characters.append(Character.model_validate({'id': "char", 'name': "Hero", 'description': "red coat", 'image_url': "uploads/source.png"}))
    root.frames.append(StoryboardFrame.model_validate({'id': "frame", 'scene_id': "scene"}))
    item._commit_storage("projects.json")
    video._install_provider(item, provider)
    item.provider_runtime_selector = api._InjectedProviderRuntimeSelector(item.provider_recovery_runtime)
    _, task_id = item.admit_provider_video_task("p", video._admission_request())
    item.submit_provider_video_task("p", task_id)
    provider.submits.clear()
    return task_id


@pytest.mark.parametrize("route", ["local", "series", "global"])
@pytest.mark.parametrize("operation", images.OPERATIONS)
def test_each_real_owner_recovers_once_despite_multiple_episode_views(runtime, transport, route, operation):
    images.setup(runtime, operation, route)
    runtime.scripts["q"] = Script.model_validate({'id': "q", 'title': "Second episode", 'original_text': "Synthetic", 'created_at': 1, 'updated_at': 1, 'series_id': "s" if route == "series" else None})
    if route == "series":
        runtime.series_store["s"].episode_ids.append("q")
    runtime._commit_storage("projects.json", "series.json")
    with images.client_for(runtime, operation) as client:
        saved = images.unknown(client, runtime, transport, operation, route=route)
    with images.reopened(runtime) as other, images.client_for(other, operation):
        worker = dispatcher(other)
        before = admission.owner(other, route).characters[0]
        unit = before.three_views if operation == "edit" else before.reference_sheet
        assert unit is not None
        selected, prompt, motion = unit.selected_image_id, unit.image_prompt, unit.video_prompt
        transport["queue"].extend([images.observation(operation), response(images.PNG)])
        assert worker.run_once() == ("image_queried", *identity(saved))
        assert worker.run_once() == ("image_fetched", *identity(saved))
        assert worker.run_once() is None
        finished = saved_request(other, saved)
        assert finished["owner_kind"] == {"local": "script", "series": "series", "global": "global"}[route]
        assert finished["members"][0]["execution"]["handle"] == saved["members"][0]["execution"]["handle"]
        images.assert_png(other, finished["members"][0]["execution"])
        after = admission.owner(other, route).characters[0]
        result_unit = after.three_views if operation == "edit" else after.reference_sheet
        assert result_unit is not None
        assert (result_unit.selected_image_id, result_unit.image_prompt, result_unit.video_prompt) == (selected, prompt, motion)
        assert len(other.scripts["q"].asset_image_requests) == 0
        if route != "local":
            assert not other.scripts["p"].asset_image_requests
    assert images.counts(transport) == (1, 2, 1) and not transport["queue"]


def test_committed_receipts_are_detached_and_reject_duplicate_or_foreign_owner(runtime, transport):
    images.setup(runtime, route="global")
    with images.client_for(runtime, "generation") as client:
        saved = images.unknown(client, runtime, transport, route="global")
        service = runtime.asset_image_service
        before = deepcopy(runtime._committed)
        snapshots = service.read_committed_requests()
        assert [record.id for record in snapshots] == [saved["id"]]
        snapshots[0].members.clear()
        assert runtime._committed == before
        raw = runtime._committed["library_assets.json"]["asset_image_requests"]
        raw.append(deepcopy(raw[0]))
        with pytest.raises(StorageError, match="Duplicate retained"):
            service.read_committed_requests()
        raw.pop()
        raw[0]["owner_id"] = "not-the-library"
        with pytest.raises(StorageError, match="Invalid retained"):
            service.read_committed_requests()
        runtime._committed = before
    assert images.counts(transport) == (1, 1, 0)


def test_one_scan_family_keeps_same_asset_id_distinct_in_all_three_owners(runtime, transport):
    target = images.setup(runtime)
    runtime.series_store["s"].characters.append(target.model_copy(deep=True))
    runtime.library_store.characters.append(target.model_copy(deep=True))
    runtime.series_store["s"].model_settings.t2i_model = "gpt-image-2"
    for key, series_id in (("global-view", None), ("shared-view", "s")):
        runtime.scripts[key] = Script.model_validate({'id': key, 'title': key, 'original_text': "Synthetic", 'created_at': 1, 'updated_at': 1, 'series_id': series_id})
        runtime.scripts[key].model_settings.t2i_model = "gpt-image-2"
    runtime.series_store["s"].episode_ids = ["shared-view"]
    runtime._commit_storage("projects.json", "series.json", "library_assets.json")
    saved = []
    task_ids = ["b174971e-7708-4ffa-90f6-1d9213155101", "b174971e-7708-4ffa-90f6-1d9213155102",
                "b174971e-7708-4ffa-90f6-1d9213155103"]
    with images.client_for(runtime, "generation"):
        for root, root_id, task_id in zip(("project", "series", "project"), ("p", "s", "global-view"), task_ids):
            command = execution.AssetImageRequestCommand.model_validate(images.body())
            admitted = runtime.asset_image_service.admit(root, root_id, command)
            transport["queue"].extend([images.receipt("generation", task_id), requests.ConnectionError("original query unavailable")])
            runtime.asset_image_service.run_original_request(admitted.request.id)
            saved.append(saved_request(runtime, admitted.request.model_dump(mode="json")))
    with images.reopened(runtime) as other, images.client_for(other, "generation"):
        records = other.asset_image_service.read_committed_requests()
        assert {(record.owner_kind, record.owner_id) for record in records} == {
            ("script", "p"), ("series", "s"), ("global", "library"),
        }
        assert len(records) == 3 and all(record.asset_id == "c" for record in records)

        def reply_to_original(request):
            assert request.method == "GET", "dispatcher attempted a fresh submission"
            if request.url == images.IMAGE:
                transport["queue"].append(response(images.PNG))
            else:
                task_id = request.url.rsplit("/", 1)[-1]
                assert task_id in task_ids
                transport["queue"].append(images.observation("generation", task_id=task_id))

        transport["on_send"] = reply_to_original
        try:
            worker = dispatcher(other)
            results = [worker.run_once() for _ in range(6)]
            assert set(results) == {("image_queried", *identity(record)) for record in saved} | {
                ("image_fetched", *identity(record)) for record in saved}
            assert worker.run_once() is None
            assert not other.scripts["global-view"].asset_image_requests
            assert not other.scripts["shared-view"].asset_image_requests
            for record in saved:
                final = saved_request(other, record)
                assert final["members"][0]["execution"]["phase"] == "completed"
                images.assert_png(other, final["members"][0]["execution"])
        finally:
            transport["on_send"] = None
    assert images.counts(transport) == (3, 6, 3) and not transport["queue"]


class OriginalWorkerInterrupted(BaseException):
    """Synthetic control-flow interruption after a real durable handle callback."""


def test_reopen_never_submits_accepted_sibling_or_lost_ack_request(runtime, transport):
    target = images.setup(runtime)
    runtime.scripts["p"].characters.append(target.model_copy(update={"id": "lost-ack"}, deep=True))
    runtime._commit_storage("projects.json")
    with images.client_for(runtime, "generation"):
        service = runtime.asset_image_service
        first = service.admit("project", "p", execution.AssetImageRequestCommand.model_validate(images.body(batch_size=2)))
        transport["queue"].extend([images.receipt("generation"), OriginalWorkerInterrupted()])
        with pytest.raises(OriginalWorkerInterrupted):
            service.run_original_request(first.request.id)
        saved = images.disk(runtime)
        assert [member["execution"]["phase"] for member in saved["members"]] == ["submitted", "accepted"]
        lost = service.admit("project", "p", execution.AssetImageRequestCommand.model_validate({
            **images.body(), "client_request_id": "b4.lost.ack", "asset_id": "lost-ack",
        }))
        transport["queue"].append(requests.ConnectionError("synthetic lost submission ACK"))
        service.run_original_request(lost.request.id)
        lost_before = saved_request(runtime, lost.request.model_dump(mode="json"))
        assert lost_before["members"][0]["execution"]["phase"] == "submitting"
        assert lost_before["members"][0]["execution"]["handle"] is None
    with images.reopened(runtime) as other, images.client_for(other, "generation"):
        transport["queue"].extend([images.observation("generation"), response(images.PNG)])
        worker = dispatcher(other)
        assert worker.run_once() == ("image_queried", *identity(saved))
        assert worker.run_once() == ("image_fetched", *identity(saved))
        assert worker.run_once() is None
        result = saved_request(other, saved)
        assert result["members"][1] == saved["members"][1]
        assert saved_request(other, lost_before) == lost_before
        assert len(other.scripts["p"].asset_image_requests) == 2
        assert other.asset_generation_tasks == {}
    assert images.counts(transport) == (2, 2, 1) and not transport["queue"]


def test_original_worker_claim_blocks_recovery_until_its_real_result_finishes(runtime, transport):
    images.setup(runtime)
    entered, release = Event(), Event()
    errors = []
    thread = None
    with images.client_for(runtime, "generation"):
        accepted = runtime.asset_image_service.admit("project", "p", execution.AssetImageRequestCommand.model_validate(images.body()))
        transport["queue"].extend([images.receipt("generation"), images.observation("generation"), response(images.PNG)])

        def blocked(request):
            if request.method == "GET" and request.url != images.IMAGE:
                entered.set()
                assert release.wait(10), "original image query was not released"

        def original():
            try:
                runtime.asset_image_service.run_original_request(accepted.request.id)
            except BaseException as exc:
                errors.append(exc)

        transport["on_send"] = blocked
        thread = Thread(target=original)
        try:
            thread.start()
            assert entered.wait(3)
            claim = next(iter(runtime._asset_image_runs.values()))
            worker = dispatcher(runtime)
            for _ in range(4):
                assert worker.run_once() is None
            assert next(iter(runtime._asset_image_runs.values())) is claim
            assert images.counts(transport) == (1, 1, 0)
        finally:
            release.set()
            thread.join(timeout=5)
            transport["on_send"] = None
        assert not thread.is_alive() and not errors
        assert worker.run_once() is None
        assert not runtime._asset_image_runs
        assert images.disk(runtime)["members"][0]["execution"]["phase"] == "completed"
    assert images.counts(transport) == (1, 1, 1)


def test_repeated_wakes_do_not_overlap_image_query_or_skip_backoff(runtime, transport):
    images.setup(runtime)
    with images.client_for(runtime, "generation") as client:
        saved = images.unknown(client, runtime, transport)
    with images.reopened(runtime) as other, images.client_for(other, "generation"):
        entered, release = Event(), Event()
        transport["queue"].append(images.observation("generation", pending=True))

        def blocked(request):
            if request.method == "GET":
                entered.set()
                assert release.wait(10), "recovery query was not released"

        transport["on_send"] = blocked
        worker = dispatcher(other)
        try:
            worker.start()
            assert entered.wait(3)
            for _ in range(20):
                worker.wake()
            assert images.counts(transport) == (1, 2, 0)
            release.set()
            assert worker.wait_for_idle(5)
            worker.request_stop()
            worker.join(5)
            assert not worker.is_running
            assert saved_request(other, saved)["members"][0]["execution"]["phase"] == "observed"
            assert not other._asset_image_runs
        finally:
            release.set()
            worker.close(5)
            transport["on_send"] = None
    assert images.counts(transport) == (1, 2, 0)


def test_transient_image_query_has_backoff_but_success_can_fetch_immediately(runtime, transport):
    images.setup(runtime)
    with images.client_for(runtime, "generation") as client:
        saved = images.unknown(client, runtime, transport)
    with images.reopened(runtime) as other, images.client_for(other, "generation"):
        clock = FakeClock()
        worker = dispatcher(other, transient_backoff_seconds=10, time_fn=clock)
        transport["queue"].append(requests.ConnectionError("synthetic query interruption"))
        assert worker.run_once() == ("image_transient", *identity(saved))
        assert worker.run_once() is None
        assert saved_request(other, saved) == saved
        clock.advance(10.1)
        transport["queue"].extend([images.observation("generation"), response(images.PNG)])
        assert worker.run_once() == ("image_queried", *identity(saved))
        assert worker.run_once() == ("image_fetched", *identity(saved))
        assert worker.run_once() is None
    assert images.counts(transport) == (1, 3, 1)


def test_claim_acquired_after_scan_returns_busy_without_false_completion(runtime, transport, monkeypatch):
    images.setup(runtime)
    with images.client_for(runtime, "generation") as client:
        saved = images.unknown(client, runtime, transport)
        service = runtime.asset_image_service
        original = service.recover_member_once
        token = object()
        owner_key = ("script", "p", "character", "c")

        def acquire_before_step(*args, **kwargs):
            with runtime._save_lock:
                runtime._asset_image_runs[owner_key] = token
            return original(*args, **kwargs)

        monkeypatch.setattr(service, "recover_member_once", acquire_before_step)
        worker = dispatcher(runtime)
        try:
            assert worker.run_once() == ("image_busy", *identity(saved))
            assert runtime._asset_image_runs[owner_key] is token
            assert saved_request(runtime, saved) == saved
            assert worker.run_once() is None
        finally:
            if runtime._asset_image_runs.get(owner_key) is token:
                del runtime._asset_image_runs[owner_key]
    assert images.counts(transport) == (1, 1, 0)


def test_failed_image_fetch_is_delayed_and_does_not_requery_or_report_completion(runtime, transport):
    images.setup(runtime)
    with images.client_for(runtime, "generation") as client:
        saved = images.unknown(client, runtime, transport)
    with images.reopened(runtime) as other, images.client_for(other, "generation"):
        clock = FakeClock()
        worker = dispatcher(other, transient_backoff_seconds=10, time_fn=clock)
        transport["queue"].extend([images.observation("generation"), requests.ConnectionError("synthetic image download unavailable")])
        assert worker.run_once() == ("image_queried", *identity(saved))
        assert worker.run_once() == ("image_error", *identity(saved))
        assert worker.run_once() is None
        observed = saved_request(other, saved)["members"][0]["execution"]
        assert observed["phase"] == "observed" and observed["artifact"] is None
        assert not (other.data_dir / observed["output_ref"]).exists()
        clock.advance(10.1)
        transport["queue"].append(response(images.PNG))
        assert worker.run_once() == ("image_fetched", *identity(saved))
        assert worker.run_once() is None
    assert images.counts(transport) == (1, 2, 2)


def test_mixed_running_video_and_images_are_fair_across_transient_queries(runtime, transport):
    target = images.setup(runtime)
    runtime.scripts["p"].characters.append(target.model_copy(update={"id": "second-image"}, deep=True))
    runtime._commit_storage("projects.json")
    with images.client_for(runtime, "generation") as client:
        first = images.unknown(client, runtime, transport)
        transport["queue"].extend([images.receipt("generation", "b174971e-7708-4ffa-90f6-1d92131551cc"),
                                    requests.ConnectionError("second original query unavailable")])
        command = execution.AssetImageRequestCommand.model_validate({
            **images.body(), "client_request_id": "b4.second.image", "asset_id": "second-image",
        })
        accepted = runtime.asset_image_service.admit("project", "p", command)
        runtime.asset_image_service.run_original_request(accepted.request.id)
        second = saved_request(runtime, accepted.request.model_dump(mode="json"))
    with images.reopened(runtime) as other, images.client_for(other, "generation"):
        provider = video.FakeProvider()
        task_id = add_video(other, provider)
        clock = FakeClock()
        worker = dispatcher(other, transient_backoff_seconds=10, time_fn=clock)
        transport["queue"].extend([requests.ConnectionError("first recovery temporarily unavailable"),
                                   images.observation("generation", pending=True,
                                                      task_id="b174971e-7708-4ffa-90f6-1d92131551cc")])
        assert worker.run_once() == ("queried", "p", task_id)
        assert worker.run_once() == ("image_transient", *identity(first))
        clock.advance(10.1)
        assert worker.run_once() == ("queried", "p", task_id)
        assert worker.run_once() == ("image_queried", *identity(second))
        assert len(provider.queries) == 2 and not provider.submits
        assert saved_request(other, first)["members"][0]["execution"]["phase"] == "submitted"
        assert saved_request(other, second)["members"][0]["execution"]["phase"] == "observed"
        assert {key[0] for key in worker._last_attempt_at} == {"image", "video"}
    assert images.counts(transport) == (2, 4, 0) and not transport["queue"]


@pytest.mark.parametrize("stop_on_classification", [1, 2])
def test_stop_after_selection_prevents_new_image_network(runtime, transport, monkeypatch, stop_on_classification):
    images.setup(runtime)
    with images.client_for(runtime, "generation") as client:
        saved = images.unknown(client, runtime, transport)
        worker = dispatcher(runtime)
        original = runtime.asset_image_service.recovery_member_state
        classifications = []
        before = owner_bytes(runtime)

        def stop_after_classification(*args):
            result = original(*args)
            classifications.append(args)
            if len(classifications) == stop_on_classification:
                worker.request_stop()
            return result

        monkeypatch.setattr(runtime.asset_image_service, "recovery_member_state", stop_after_classification)
        assert worker.run_once() is None
        assert worker._stop.is_set()
        assert classifications == [identity(saved)] * stop_on_classification
        assert saved_request(runtime, saved) == saved
        assert owner_bytes(runtime) == before
    assert images.counts(transport) == (1, 1, 0)


@pytest.mark.parametrize("loss", ["intent", "current_defaults", "credentials"])
def test_original_handle_recovery_does_not_reconstruct_changed_inputs(runtime, transport, loss):
    images.setup(runtime)
    with images.client_for(runtime, "generation") as client:
        saved = images.unknown(client, runtime, transport)
        if loss == "intent":
            runtime.scripts["p"].asset_image_requests[0].members[0].execution.intent = {}
        if loss == "current_defaults":
            runtime.scripts["p"].model_settings.t2i_model = "not-a-current-model"
        runtime._commit_storage("projects.json")
    with images.reopened(runtime) as other, images.client_for(other, "generation", configure=loss != "credentials"):
        clock = FakeClock()
        worker = dispatcher(other, transient_backoff_seconds=10, time_fn=clock)
        original = saved_request(other, saved)
        if loss == "credentials":
            assert worker.run_once() == ("image_error", *identity(saved))
            assert worker.run_once() is None
            assert saved_request(other, saved) == original
            assert images.counts(transport) == (1, 1, 0)
        else:
            transport["queue"].extend([images.observation("generation"), response(images.PNG)])
            assert worker.run_once() == ("image_queried", *identity(saved))
            expected = "image_needs_confirmation" if loss == "intent" else "image_fetched"
            assert worker.run_once() == (expected, *identity(saved))
            assert worker.run_once() is None
            after = saved_request(other, saved)["members"][0]["execution"]
            assert after["handle"] == original["members"][0]["execution"]["handle"]
            assert after["intent"] == original["members"][0]["execution"]["intent"]
            images.assert_png(other, after)
            assert selected_reference_sheet(other) == "old"


def test_prepared_image_repairs_without_credentials_or_network(runtime, transport, monkeypatch):
    images.setup(runtime)
    saved, content = images._prepared_repair_image(runtime, transport, monkeypatch)
    before_calls = images.counts(transport)
    with images.reopened(runtime) as other, images.client_for(other, "generation", configure=False):
        worker = dispatcher(other)
        assert worker.run_once() == ("image_repaired", *identity(saved))
        assert worker.run_once() is None
        after = saved_request(other, saved)["members"][0]["execution"]
        assert after["phase"] == "completed"
        assert images.assert_png(other, after) == content
        assert selected_reference_sheet(other) == "old"
    assert images.counts(transport) == before_calls


def test_completed_history_skips_idle_proof_reads_but_unfinished_and_public_reads_verify(runtime, transport, monkeypatch):
    images.setup(runtime)
    saved, content = images._prepared_repair_image(runtime, transport, monkeypatch)
    before_calls = images.counts(transport)
    with images.reopened(runtime) as other, images.client_for(other, "generation", configure=False):
        execution_before = saved["members"][0]["execution"]
        assert execution_before["phase"] == "observed" and execution_before["prepared_artifact"] is not None
        reference = execution_before["output_ref"]
        path = other.data_dir / reference
        proof_reads = []
        real_verify = execution.media_versions.verify_managed_media_file

        def verify(output_ref, **kwargs):
            proof_reads.append(output_ref)
            return real_verify(output_ref, **kwargs)

        monkeypatch.setattr(execution.media_versions, "verify_managed_media_file", verify)
        worker = dispatcher(other)
        before = owner_bytes(other)
        damaged = content[:-1] + bytes([content[-1] ^ 1])
        path.write_bytes(damaged)
        # A retained proof is insufficient: an unfinished file is still read
        # and rejected when its bytes no longer match that proof.
        assert worker.run_once() is None
        assert proof_reads and set(proof_reads) == {reference}
        assert owner_bytes(other) == before and saved_request(other, saved) == saved
        assert path.read_bytes() == damaged and selected_reference_sheet(other) == "old"

        path.write_bytes(content)
        proof_reads.clear()
        assert worker.run_once() == ("image_repaired", *identity(saved))
        assert proof_reads and set(proof_reads) == {reference}
        finished = saved_request(other, saved)["members"][0]["execution"]
        assert finished["phase"] == "completed"
        assert finished["artifact"] == finished["prepared_artifact"] == execution_before["prepared_artifact"]
        assert finished["handle"] == execution_before["handle"]
        assert path.read_bytes() == content and selected_reference_sheet(other) == "old"

        stable = owner_bytes(other)
        proof_reads.clear()
        for _ in range(3):
            assert worker.run_once() is None
        assert proof_reads == []
        assert owner_bytes(other) == stable
        # The optimization belongs only to dispatch selection. Explicit public
        # recovery state must continue to validate completed image bytes.
        assert other.asset_image_service.recovery_member_state(*identity(saved)) == "terminal"
        assert proof_reads and set(proof_reads) == {reference}
        assert owner_bytes(other) == stable and not other._asset_image_runs
    assert images.counts(transport) == before_calls and not transport["queue"]


@pytest.mark.parametrize("remote_status", ["failed", "canceled"])
def test_terminal_image_observation_is_not_polled_again(runtime, transport, remote_status):
    operation = "wan" if remote_status == "canceled" else "generation"
    images.setup(runtime, operation)
    with images.client_for(runtime, operation) as client:
        saved = images.unknown(client, runtime, transport, operation)
    with images.reopened(runtime) as other, images.client_for(other, operation):
        transport["queue"].append(response({"output": {"task_id": images.TASK, "task_status": "CANCELED"}})
                                   if remote_status == "canceled" else images.observation("generation", failed=True))
        worker = dispatcher(other, transient_backoff_seconds=0)
        assert worker.run_once() == ("image_queried", *identity(saved))
        assert worker.run_once() is None
        after = saved_request(other, saved)["members"][0]["execution"]
        assert after["phase"] == "observed" and after["observation"]["status"] == remote_status
        assert after["artifact"] is None and after["handle"] == saved["members"][0]["execution"]["handle"]
    assert images.counts(transport) == (1, 2, 0)


def test_schema2_receipt_never_enters_image_dispatch(runtime, transport):
    fixture = Path(__file__).with_name("fixtures") / "studio_image_schema2_http_unknown.json"
    raw = json.loads(fixture.read_bytes())
    runtime.scripts = {"p": Script.model_validate(raw["p"])}
    runtime._commit_storage("projects.json", "series.json", "library_assets.json")
    before = owner_bytes(runtime)
    assert dispatcher(runtime).run_once() is None
    assert owner_bytes(runtime) == before and not transport["calls"]


def test_image_dispatch_propagates_storage_failure_and_stops_owned_worker(runtime, transport, monkeypatch):
    images.setup(runtime)
    with images.client_for(runtime, "generation") as client:
        saved = images.unknown(client, runtime, transport)
    with images.reopened(runtime) as other, images.client_for(other, "generation"):
        before = owner_bytes(other)
        transport["queue"].append(images.observation("generation"))

        def failed_commit(_payload):
            raise StorageError("synthetic image observer commit failed")

        monkeypatch.setattr(other._storage, "commit", failed_commit)
        worker = dispatcher(other)
        try:
            worker.start()
            worker.join(5)
            assert not worker.is_running
            assert worker._stop.is_set()
            assert owner_bytes(other) == before
            assert saved_request(other, saved)["members"][0]["execution"]["phase"] == "submitted"
        finally:
            worker.close(5)
    assert images.counts(transport) == (1, 2, 0)


def test_gpt_only_managed_app_reopen_recovers_with_the_existing_dispatcher(runtime, transport, monkeypatch):
    images.setup(runtime)
    with images.client_for(runtime, "generation") as client:
        saved = images.unknown(client, runtime, transport)
    configure_gpt_environment(monkeypatch)
    transport["queue"].extend([images.observation("generation"), response(images.PNG)])
    original = dispatch.StudioProviderRecoveryDispatcher
    monkeypatch.setattr(dispatch, "StudioProviderRecoveryDispatcher", lambda item: original(item, interval_seconds=0.01))
    app = api.create_app(data_dir=runtime.data_dir, user_data_dir=runtime.data_dir.parent / "user", load_config=False,
                         recover_orphan_tasks=True)
    with TestClient(app):
        other = app.state.pipeline
        worker = app.state.provider_recovery_dispatcher
        # A classic compatibility runtime may exist without DashScope credentials.
        assert other is not runtime
        assert other.provider_runtime_selector is app.state.provider_runtime_selector
        assert app.state.runtime_environment.snapshot().get("DASHSCOPE_API_KEY") is None
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if saved_request(other, saved)["members"][0]["execution"]["phase"] == "completed":
                break
            Event().wait(0.01)
        after = saved_request(other, saved)["members"][0]["execution"]
        assert after["phase"] == "completed" and after["handle"] == saved["members"][0]["execution"]["handle"]
        images.assert_png(other, after)
        assert selected_reference_sheet(other) == "old"
    assert other._closed and not worker.is_running
    assert not hasattr(app.state, "provider_recovery_dispatcher")
    assert images.counts(transport) == (1, 2, 1) and not transport["queue"]


def test_explicit_image_runtime_does_not_require_a_video_selector(runtime, transport):
    images.setup(runtime)
    with images.client_for(runtime, "generation") as client:
        saved = images.unknown(client, runtime, transport)
    with images.reopened(runtime) as other:
        assert other.runtime_environment is not None
        other.runtime_environment.update({
            "MULEROUTER_API_KEY": gpt.HTTP_SECRET, "MULERUN_SITE": "sg", "MULERUN_STUDIO_SITE": "mulerouter",
        }, persist=lambda *_args: None)
        assert other.provider_recovery_runtime is None and other.provider_runtime_selector is None
        worker = dispatcher(other)
        transport["queue"].extend([images.observation("generation"), response(images.PNG)])
        assert worker.run_once() == ("image_queried", *identity(saved))
        assert worker.run_once() == ("image_fetched", *identity(saved))
        assert worker.run_once() is None
        assert saved_request(other, saved)["members"][0]["execution"]["phase"] == "completed"
    assert images.counts(transport) == (1, 2, 1)


@pytest.mark.parametrize("stage", ["query", "fetch"])
def test_late_image_network_cannot_write_after_shutdown_and_same_root_reopen(runtime, transport, monkeypatch, stage):
    images.setup(runtime)
    with images.client_for(runtime, "generation") as client:
        saved = images.unknown(client, runtime, transport)
        if stage == "fetch":
            transport["queue"].append(images.observation("generation"))
            runtime.asset_image_service.recover_member_once(*identity(saved))
    configure_gpt_environment(monkeypatch)
    entered, release = Event(), Event()
    transport["queue"].append(images.observation("generation") if stage == "query" else response(images.PNG))

    def held(request):
        if request.method == "GET" and ((request.url == images.IMAGE) == (stage == "fetch")):
            entered.set()
            assert release.wait(10), "late image response was not released"

    cls = dispatch.StudioProviderRecoveryDispatcher
    real_join = cls.join
    joins = []

    def short_join(worker, timeout=None):
        joins.append((timeout, worker._pipeline._closed))
        return real_join(worker, timeout=0.01)

    monkeypatch.setattr(cls, "join", short_join)
    transport["on_send"] = held
    old_app = api.create_app(data_dir=runtime.data_dir, user_data_dir=runtime.data_dir.parent / "user", load_config=False,
                             recover_orphan_tasks=True)
    worker = None
    try:
        with TestClient(old_app):
            worker = old_app.state.provider_recovery_dispatcher
            assert entered.wait(3)
        assert joins == [(5.0, True)]
        assert worker.is_running
        new_app = api.create_app(data_dir=runtime.data_dir, user_data_dir=runtime.data_dir.parent / "user", load_config=False,
                                 recover_orphan_tasks=False)
        with TestClient(new_app):
            other = new_app.state.pipeline
            other.scripts["p"].title = "New app owns this root"
            other._commit_storage("projects.json")
            before = owner_bytes(other)
            reference = saved["members"][0]["execution"]["output_ref"]
            assert not (other.data_dir / reference).exists()
            release.set()
            real_join(worker, timeout=5)
            assert not worker.is_running
            assert owner_bytes(other) == before
            assert not (other.data_dir / reference).exists()
            assert other.scripts["p"].title == "New app owns this root"
            assert selected_reference_sheet(other) == "old"
            assert not other._closed
    finally:
        release.set()
        if worker is not None:
            real_join(worker, timeout=5)
        transport["on_send"] = None
    assert images.counts(transport) == ((1, 2, 0) if stage == "query" else (1, 2, 1))
