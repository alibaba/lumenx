"""Qwen reference sheets share Studio identity, ownership and original claims."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import io
import json
from unittest.mock import Mock
import zipfile

from fastapi.testclient import TestClient
import pytest
import requests

from src.apps.comic_gen import asset_image_execution as execution_module
from src.apps.comic_gen.asset_image_models import AssetImageRequestRecord
from src.apps.comic_gen.models import ArtDirection, Prop, Scene, Series
from src.apps.comic_gen.pipeline import ComicGenPipeline
from src.apps.comic_gen.project_package import build_package, prepare_import
from src.models.provider_recovery import record_with_changes
from src.utils.json_storage import StorageConflictError, StorageError
from tests import test_qwen20_core_execution as core
from tests import test_studio_core_image_admission as admission
from tests import test_studio_core_image_api as http
from tests import test_studio_core_image_copy_inert as copying
from tests import test_studio_core_image_models as history
from tests import test_studio_core_image_worker as worker
from tests.frozen_image_fixtures import rgb_png_bytes
from tests.test_qwen20_bound_image import completed
from tests.test_wan27_bound_image import response

runtime = admission.runtime
bound_transport = worker.bound_transport
executions = worker.executions
MODELS = (*core.MODELS, *(f"qwen/{model}#image" for model in core.MODELS))
RATIOS = {
    "9:16": "576*1024", "3:4": "768*1024", "1:1": "1024*1024",
    "4:3": "1024*768", "16:9": "1024*576",
}


def setup(item, route="local", model="qwen-image-2.0"):
    root = admission.setup(item, route)
    actual = item.series_store[root[1]] if root[0] == "series" else item.scripts[root[1]]
    actual.model_settings.t2i_model = model
    item._commit_storage("projects.json", "series.json")
    return root


def raw_request(route="project-local", model="qwen-image-2.0"):
    value = history.request_payload(route)
    value["members"] = [{"variant_id": "attempt", "execution": core.record(model).model_dump(mode="json")}]
    return value


def queue_images(transport, count=1):
    transport["queue"] = [item for _ in range(count) for item in (
        response(completed()), response(rgb_png_bytes(width=16, height=16)),
    )]


def posts(transport):
    return [json.loads(request.body) for request, _ in transport["calls"] if request.method == "POST"]


def style(negative="style-negative", positive="style-positive"):
    return ArtDirection(selected_style_id="custom", style_config={
        "name": "Custom", "positive_prompt": positive, "negative_prompt": negative,
    })


@pytest.mark.parametrize("route", history.ROUTES)
@pytest.mark.parametrize("model", core.MODELS)
def test_qwen_history_loads_at_the_real_owner_without_live_defaults(tmp_path, monkeypatch, route, model):
    data = history.payloads(route)
    value = raw_request(route, model)
    history.owner_payload(data, route)["asset_image_requests"] = [value]
    monkeypatch.setattr("src.models.provider_media_runtime.get_catalog_accessor", Mock(side_effect=AssertionError("history consulted catalog")))
    monkeypatch.setattr("requests.sessions.Session.request", Mock(side_effect=AssertionError("history made network request")))
    projects, series, library = history.save_and_reload(tmp_path, data)
    owner = {"script": projects["project-root"], "series": series["series-root"], "global": library}[value["owner_kind"]]
    assert owner.asset_image_requests[0].model_dump(mode="json") == value


@pytest.mark.parametrize("asset_type,channel,entrypoint", [
    ("scene", "image", "studio.asset.reference_sheet.v1"),
    ("prop", "image", "studio.asset.full_body.v1"),
])
def test_valid_qwen_member_cannot_escape_closed_studio_tuples(asset_type, channel, entrypoint):
    raw = raw_request()
    # Prove the member is valid before testing the enclosing business tuple.
    AssetImageRequestRecord.model_validate(raw)
    raw.update(asset_type=asset_type, channel=channel, entrypoint=entrypoint)
    with pytest.raises(ValueError, match="Unsupported Studio image entrypoint"):
        AssetImageRequestRecord.model_validate(raw)


@pytest.mark.parametrize("change", ["missing-marker", "wan-marker", "other-model", "negative-too-long"])
def test_qwen_history_still_rejects_rehashed_invalid_contract(change):
    raw = raw_request()
    AssetImageRequestRecord.model_validate(raw)
    execution = raw["members"][0]["execution"]
    if change == "missing-marker":
        execution["intent"].pop("intent_contract_version")
    elif change == "wan-marker":
        execution["intent"]["intent_contract_version"] = history.WAN27_IMAGE_CONTRACT_VERSION
    elif change == "other-model":
        execution["provider_model_id"] = "qwen-image-2.0-pro"
    else:
        execution["intent"]["params"]["negative_prompt"] = "字" * 501
    history.rehash(execution)
    with pytest.raises(ValueError):
        AssetImageRequestRecord.model_validate(raw)


@pytest.mark.parametrize("route", admission.ROUTES)
@pytest.mark.parametrize("model", MODELS)
def test_qwen_admission_freezes_one_owner_profile_and_default_parameters(runtime, route, model):
    root = setup(runtime, route, model)
    result = runtime.asset_image_service.admit(*root, admission.command(seed=0, batch_size=2))
    record = result.request
    assert record.entrypoint == "studio.asset.reference_sheet.v1" and record.channel == "reference_sheet"
    assert admission.owner(runtime, route).characters[0].reference_sheet_request_id == record.id
    assert admission.owner(runtime, route).asset_image_requests == [record]
    assert len(record.members) == 2
    expected_model = "qwen-image-2.0-pro" if "2.0-pro" in model else "qwen-image-2.0"
    for member in record.members:
        execution = member.execution
        assert execution.intent["model_id"] == f"qwen/{expected_model}#image"
        assert execution.provider_model_id == expected_model
        assert execution.intent["intent_contract_version"] == core.VERSION
        assert execution.intent["params"] == {
            "size": "576*1024", "watermark": False, "seed": 0,
            "prompt_extend": True, "negative_prompt": "",
        }
    assert len(runtime.asset_generation_tasks) == 1 and not result.replayed
    assert record.model_dump(mode="json").keys() == history.request_payload().keys()


@pytest.mark.parametrize("model", core.MODELS)
@pytest.mark.parametrize("ratio,size", RATIOS.items())
def test_existing_aspect_choices_remain_exact_and_seed_is_not_filled(runtime, model, ratio, size):
    root = setup(runtime)
    result = runtime.asset_image_service.admit(*root, admission.command(model_name=model, aspect_ratio=ratio))
    params = result.request.members[0].execution.intent["params"]
    assert params["size"] == size and "seed" not in params


@pytest.mark.parametrize("route", admission.ROUTES)
@pytest.mark.parametrize("apply_style", [True, False])
def test_template_and_root_style_negative_are_combined_exactly_once(runtime, route, apply_style):
    root = setup(runtime, route)
    actual = runtime.series_store[root[1]] if root[0] == "series" else runtime.scripts[root[1]]
    actual.art_direction = style()
    runtime._commit_storage("projects.json", "series.json")
    result = runtime.asset_image_service.admit(*root, admission.command(
        prompt="template-positive", negative_prompt="template-negative", apply_style=apply_style,
    ))
    intent = result.request.members[0].execution.intent
    assert intent["params"]["negative_prompt"] == ("template-negative, style-negative" if apply_style else "template-negative")
    assert intent["prompt"].count("template-positive") == 1
    assert intent["prompt"].count("style-positive") == int(apply_style)


@pytest.mark.parametrize("route", ["series", "global"])
def test_effective_style_comes_from_request_root_inheritance_not_asset_owner(runtime, route):
    root = setup(runtime, route)
    runtime.scripts["p"].series_id = "s"
    runtime.series_store["s"].art_direction = style("series-negative", "series-positive")
    runtime._commit_storage("projects.json", "series.json")
    result = runtime.asset_image_service.admit(*root, admission.command(negative_prompt="template", apply_style=True))
    intent = result.request.members[0].execution.intent
    assert intent["params"]["negative_prompt"] == "template, series-negative"
    assert intent["prompt"].count("series-positive") == 1


@pytest.mark.parametrize("negative", ["字" * 500, "😀" * 500])
def test_negative_500_codepoints_survives_without_truncation(runtime, negative):
    root = setup(runtime)
    result = runtime.asset_image_service.admit(*root, admission.command(negative_prompt=negative))
    assert result.request.members[0].execution.intent["params"]["negative_prompt"] == negative


@pytest.mark.parametrize("template,global_negative", [("字" * 501, ""), ("😀" * 499, "x")])
def test_effective_negative_over_500_rejects_before_binding_or_commit(runtime, monkeypatch, template, global_negative):
    root = setup(runtime)
    runtime.scripts["p"].art_direction = style(global_negative)
    runtime._commit_storage("projects.json")
    before = deepcopy(runtime._committed)
    binder = Mock(side_effect=AssertionError("invalid negative bound credentials"))
    monkeypatch.setattr(execution_module, "bind_media_runtime_authority", binder)
    with pytest.raises(ValueError, match="negative_prompt.*500"):
        runtime.asset_image_service.admit(*root, admission.command(negative_prompt=template, apply_style=True))
    assert before == runtime._committed and not runtime.asset_generation_tasks
    binder.assert_not_called()


@pytest.mark.parametrize("kind,generation_type", [("scene", "full_body"), ("prop", "reference_sheet")])
def test_qwen_other_channels_remain_outside_strict_admission(runtime, monkeypatch, kind, generation_type):
    root = setup(runtime)
    if kind != "character":
        getattr(runtime.scripts["p"], kind + "s").append((Scene if kind == "scene" else Prop)(id="c", name="C", description=""))
        runtime._commit_storage("projects.json")
    before = deepcopy(runtime._committed)
    binder = Mock(side_effect=AssertionError("unsupported channel bound credentials"))
    monkeypatch.setattr(execution_module, "bind_media_runtime_authority", binder)
    with pytest.raises(ValueError):
        runtime.asset_image_service.admit(*root, admission.command(asset_type=kind, generation_type=generation_type))
    assert not ComicGenPipeline._is_core_asset_image(kind, generation_type, "qwen-image-2.0")
    assert runtime._committed == before and not runtime.asset_generation_tasks
    binder.assert_not_called()


@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("kind,generation_type", [
    ("character", "three_view"), ("character", "headshot"),
    ("character", "all"),
])
def test_qwen_aliases_route_only_supported_derived_channels_through_core(model, kind, generation_type):
    assert ComicGenPipeline._is_core_asset_image(kind, generation_type, model) is (generation_type == "three_view")


@pytest.mark.parametrize("reference", ["", "uploads/source.png"])
def test_qwen_reference_url_never_silently_becomes_t2i(runtime, monkeypatch, reference):
    root = setup(runtime)
    binder = Mock(side_effect=AssertionError("reference bound credentials"))
    monkeypatch.setattr(execution_module, "bind_media_runtime_authority", binder)
    with pytest.raises(ValueError, match="reference_not_supported"):
        runtime.asset_image_service.admit(*root, admission.command(reference_image_url=reference))
    assert not runtime.asset_generation_tasks
    binder.assert_not_called()


@pytest.mark.parametrize("route", admission.ROUTES)
def test_qwen_replay_before_current_defaults_and_raw_changes_conflict(runtime, monkeypatch, route):
    root = setup(runtime, route)
    service = runtime.asset_image_service
    command = admission.command()
    first = service.admit(*root, command)
    actual = runtime.series_store[root[1]] if root[0] == "series" else runtime.scripts[root[1]]
    actual.model_settings.t2i_model = "wan2.7-image"
    runtime._commit_storage("projects.json", "series.json")
    monkeypatch.setattr(runtime, "_prepare_asset_image", Mock(side_effect=AssertionError("replay prepared")))
    monkeypatch.setattr(execution_module, "bind_media_runtime_authority", Mock(side_effect=AssertionError("replay bound")))
    assert service.admit(*root, command).request == first.request
    for changes in ({"negative_prompt": ""}, {"negative_prompt": None}, {"model_name": "qwen-image-2.0"}, {"generation_type": "full_body"}):
        with pytest.raises(StorageConflictError):
            service.admit(*root, admission.command(**changes))


def test_qwen_concurrent_same_key_binds_once(runtime, monkeypatch):
    root = setup(runtime, "global")
    binder = Mock(wraps=execution_module.bind_media_runtime_authority)
    monkeypatch.setattr(execution_module, "bind_media_runtime_authority", binder)
    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(lambda _: runtime.asset_image_service.admit(*root, admission.command()), range(2)))
    assert sorted(item.replayed for item in result) == [False, True]
    assert result[0].request.id == result[1].request.id and binder.call_count == 1


@pytest.mark.parametrize("route", admission.ROUTES)
def test_qwen_failed_commit_does_not_publish_claim(runtime, monkeypatch, route):
    root = setup(runtime, route)
    before = deepcopy(runtime._committed)
    monkeypatch.setattr(runtime._storage, "commit", Mock(side_effect=StorageError("injected write failure")))
    with pytest.raises(StorageError):
        runtime.asset_image_service.admit(*root, admission.command())
    assert runtime._committed == before and not runtime.asset_generation_tasks
    assert not getattr(runtime, "_asset_image_runs", {})


@pytest.mark.parametrize("route", admission.ROUTES)
@pytest.mark.parametrize("model", core.MODELS)
def test_real_http_runs_original_qwen_bound_adapter_and_readonly_recovery(runtime, bound_transport, monkeypatch, route, model):
    root = setup(runtime, route, model)
    queue_images(bound_transport, 2)
    path = f"/{'projects' if root[0] == 'project' else 'series'}/{root[1]}/asset_image_requests"
    raw = admission.command(batch_size=2, seed=0, negative_prompt="template").model_dump(exclude_unset=True)
    with TestClient(http.real_app(runtime)) as client:
        http.enable_fake_key(client.app)
        accepted = client.post(path, json=raw)
        assert accepted.status_code == 200, accepted.text
        record = admission.owner(runtime, route).asset_image_requests[0]
        assert record.id == accepted.json()["request"]["id"]
        assert record.members[0].execution.intent["intent_contract_version"] == core.VERSION
        payloads = posts(bound_transport)
        assert len(payloads) == 2 and all(payload["model"] == model for payload in payloads)
        assert all(payload["parameters"] == {"size": "576*1024", "n": 1, "seed": 0,
                   "watermark": False, "prompt_extend": True, "negative_prompt": "template"} for payload in payloads)
        submitted = [request for request, _ in bound_transport["calls"] if request.method == "POST"]
        assert all(request.url.endswith("/multimodal-generation/generation") and "X-DashScope-Async" not in request.headers for request in submitted)
        before = {name: (runtime.data_dir / name).read_bytes() for name in ("projects.json", "series.json", "library_assets.json")}
        read = client.get(path + "/request-1")
        assert read.status_code == 200 and read.json()["replayed"]
        assert all(member["status"] == "completed" for member in read.json()["request"]["members"])
        assert all(member.execution.prepared_artifact == member.execution.artifact for member in record.members)
        assert all((runtime.data_dir / member.execution.output_ref).read_bytes().startswith(b"\x89PNG") for member in record.members)
        client.app.state.runtime_environment.update({}, remove_keys=("DASHSCOPE_API_KEY",), persist=lambda *_: None)
        monkeypatch.setattr(execution_module, "bind_media_runtime_authority", Mock(side_effect=AssertionError("replay rebound")))
        replay = client.post(path, json=raw)
        assert replay.status_code == 200 and replay.json()["request"] == read.json()["request"]
        assert before == {name: (runtime.data_dir / name).read_bytes() for name in before}
    count = len(bound_transport["calls"])
    with worker.restarted(runtime) as other:
        service = other.asset_image_service
        assert service.run_original_request(record.id) is None
        recovered = service.read_request(*root, "request-1")
        assert service.request_state(recovered.request)["status"] == "completed"
        assert service.admit(*root, admission.command(batch_size=2, seed=0, negative_prompt="template")).replayed
    assert len(bound_transport["calls"]) == count


@pytest.mark.parametrize("phase", ["accepted", "submitting"])
def test_qwen_restart_without_proof_never_gains_paid_authority(runtime, executions, phase):
    execute, calls, _ = executions
    root = setup(runtime)
    ack = runtime.asset_image_service.admit(*root, admission.command())
    member = runtime.scripts["p"].asset_image_requests[0].members[0]
    member.execution = record_with_changes(member.execution, phase=phase)
    runtime._commit_storage("projects.json")
    with worker.restarted(runtime) as other:
        service = worker.service(other, execute)
        assert service.run_original_request(ack.request.id) is None
        assert service.admit(*root, admission.command()).replayed
        result = service.repair_request(*root, "request-1")
        assert service.request_state(result.request)["status"] == "needs_confirmation"
    assert calls == []


def test_qwen_original_worker_does_not_reselect_after_model_catalog_environment_changes(runtime, executions, monkeypatch):
    execute, calls, _ = executions
    root = setup(runtime)
    service = worker.service(runtime, execute)
    original = service.admit(*root, admission.command(negative_prompt="frozen"))
    runtime.scripts["p"].model_settings.t2i_model = "wan2.7-image-pro"
    runtime.runtime_environment.update({"DASHSCOPE_BASE_URL": "https://changed.invalid"}, persist=lambda *_: None)
    runtime._commit_storage("projects.json")
    monkeypatch.setattr("src.models.provider_media_runtime.get_catalog_accessor", Mock(side_effect=AssertionError("worker consulted catalog")))
    result = service.run_original_request(original.request.id)
    assert result.request.members[0].execution.phase == "completed"
    assert calls[0][0].intent == original.request.members[0].execution.intent
    assert calls[0][1].scope.to_dict() == original.request.members[0].execution.scope


def test_qwen_secret_change_does_not_rebind_original_authority(runtime, executions):
    execute, calls, _ = executions
    root = setup(runtime)
    service = worker.service(runtime, execute)
    original = service.admit(*root, admission.command())
    runtime.runtime_environment.update({"DASHSCOPE_API_KEY": "synthetic-replacement"}, persist=lambda *_: None)
    result = service.run_original_request(original.request.id)
    assert calls == [] and result.request.members[0].execution.phase == "accepted"
    assert service.request_state(result.request)["status"] == "needs_confirmation"


@pytest.mark.parametrize("route", admission.ROUTES)
def test_existing_task_entrypoint_routes_qwen_reference_sheet_into_same_core(runtime, bound_transport, route):
    root = setup(runtime, route)
    queue_images(bound_transport)
    generate = runtime.generate_series_asset if root[0] == "series" else runtime.create_asset_generation_task
    result, task_id = generate(root[1], "c", "character", generation_type="reference_sheet", apply_style=False, negative_prompt="template")
    assert result.id == root[1]
    record = admission.owner(runtime, route).asset_image_requests[0]
    assert record.id == task_id
    runtime.process_asset_generation_task(task_id)
    runtime.process_asset_generation_task(task_id)
    assert len(posts(bound_transport)) == 1
    assert runtime.get_asset_generation_task_status(task_id)["status"] == "completed"


@pytest.mark.parametrize("model", MODELS)
def test_sync_pipeline_failure_retains_qwen_receipt_without_legacy_fallback(runtime, bound_transport, model):
    setup(runtime)
    queue_images(bound_transport, 2)
    bound_transport["queue"].insert(0, requests.Timeout("synthetic lost response"))
    result = runtime.generate_asset("p", "c", "character", generation_type="reference_sheet", apply_style=False, model_name=model, batch_size=3)
    record = runtime.scripts["p"].asset_image_requests[0]
    # Reference-sheet batches keep the other originally authorized candidates;
    # scene/prop and full_body have a different existing stop policy.
    assert [member.execution.phase for member in record.members] == ["submitting", "completed", "completed"]
    assert result.characters[0].status == "needs_confirmation"
    assert runtime.asset_image_service.request_state(record)["status"] == "needs_confirmation"
    assert len(posts(bound_transport)) == 3
    assert runtime.asset_image_service.run_original_request(record.id) is None


@pytest.mark.parametrize("operation,route", [
    ("promote-project", "local"), ("promote-series", "standalone"),
    ("fork-global", "global"), ("import-series", "standalone"),
])
def test_qwen_copy_entrypoints_keep_history_at_origin_without_new_authority(runtime, monkeypatch, operation, route):
    root = setup(runtime, route)
    original = runtime.asset_image_service.admit(*root, admission.command())
    source = admission.owner(runtime, route).characters[0]
    before = source.model_dump(mode="json")
    blocked = Mock(side_effect=AssertionError("copy attempted provider execution"))
    monkeypatch.setattr(execution_module, "bind_media_runtime_authority", blocked)
    monkeypatch.setattr(runtime.asset_image_service, "_executor", blocked)
    if operation == "promote-project":
        copied = runtime.promote_asset_to_library("project", "p", "character", "c")
    elif operation == "promote-series":
        copied = runtime.promote_asset_to_library("series", "s", "character", "c")
    elif operation == "fork-global":
        copied = runtime.fork_library_asset_to_project("p", "character", "c")
    else:
        runtime.series_store["target"] = Series(id="target", title="Target", created_at=1, updated_at=1)
        runtime._commit_storage("series.json")
        target, imported, skipped = runtime.import_assets_from_series("target", "s", ["c"])
        assert imported == ["c"] and skipped == []
        copied = target.characters[0]
        assert target.asset_image_request_scope_id is None and target.asset_image_requests == []
    copying.assert_copy(before, copied)
    assert source.model_dump(mode="json") == before
    assert copying.loaded_history(runtime) == [original.request.model_dump(mode="json")]
    blocked.assert_not_called()


def test_qwen_package_export_and_import_strip_execution_authority(tmp_path):
    source, assets = copying.package_source(tmp_path, "processing")
    raw = raw_request()
    raw.update(owner_id=source.id, request_root_id=source.id, asset_id=assets.characters[0].id)
    source.asset_image_requests = [AssetImageRequestRecord.model_validate(raw)]
    before = source.model_dump()
    artifact = build_package(source, resolved_assets=assets, data_dir=tmp_path / "source", source_storage_id="storage")
    with zipfile.ZipFile(io.BytesIO(artifact.data)) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    payload = json.loads(entries["project.json"])
    assert payload["script"]["asset_image_requests"] == []
    assert payload["resolved_assets"]["asset_image_requests"] == []
    assert source.model_dump() == before
    # Older/external packages may still carry a valid frozen record. Import
    # strips its authority independently, before it becomes a new project.
    payload["script"]["asset_image_requests"] = [raw]
    payload["script"]["asset_image_request_scope_id"] = history.SCOPE_ID
    for char in payload["resolved_assets"]["characters"]:
        char["reference_sheet_request_id"] = history.REQUEST_ID
        char["status"] = "processing"
    entries["project.json"] = json.dumps(payload).encode()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    imported = prepare_import(buffer.getvalue(), data_dir=tmp_path / "target").script
    assert imported.id != source.id and imported.asset_image_requests == []
    assert imported.asset_image_request_scope_id is None
    assert imported.characters[0].reference_sheet_request_id is None
    assert imported.characters[0].status == "needs_confirmation"
    assert (tmp_path / "target" / imported.characters[0].reference_sheet.image_variants[0].url).read_bytes() == b"hero"
