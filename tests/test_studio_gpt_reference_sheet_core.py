"""Studio GPT reference sheets use the existing durable image request contract."""

from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import subprocess
import time
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4
import zipfile

import pytest
import requests
from fastapi.testclient import TestClient

from src.apps.atelier.models import AtelierProject
from src.apps.comic_gen import asset_image_execution as execution_module
from src.apps.comic_gen.asset_image_execution import AssetImageRequestCommand
from src.apps.comic_gen.asset_image_models import AssetImageRequestRecord
from src.apps.comic_gen.models import ArtDirection, AssetUnit, ImageAsset, ImageVariant, Scene, Prop, Series
from src.models import mulerouter
from src.models.provider_media_execution import execute_sync_artifact_to_private_file
from src.models.provider_media_intents import build_accepted_sync_artifact_record
from src.models.provider_media_runtime import bind_media_runtime_authority, mulerun_routes_from_snapshot
from src.models.provider_recovery import record_with_changes
from src.utils.generation_intent import normalize_generation_intent
from src.utils.runtime_environment import RuntimeEnvironment
from src.utils.json_storage import StorageError
from tests import test_studio_core_image_admission as admission
from tests import test_studio_core_image_api as http
from tests import test_studio_core_image_models as history
from tests import test_studio_core_image_worker as worker
from tests.frozen_image_fixtures import rgb_png_bytes
from tests.test_wan27_bound_image import response

runtime = admission.runtime
MODELS = ("gpt-image-2", "gpt-image/gpt-image-2#image")
# Synthetic runtime values; never read credentials from the developer environment.
HTTP_SECRET = uuid4().hex
CLI_TOKEN = uuid4().hex
IMAGE_URL = "https://files.example.invalid/gpt.png"
PNG = rgb_png_bytes(width=16, height=16)


@pytest.fixture
def transport(monkeypatch):
    state = {"queue": [], "calls": [], "cli": [], "cli_queue": [], "on_send": None}

    def send(_session, prepared, **kwargs):
        state["calls"].append((prepared, kwargs))
        if state["on_send"] is not None:
            state["on_send"](prepared)
        assert state["queue"], f"Unexpected transport: {prepared.method} {prepared.url}"
        result = state["queue"].pop(0)
        if isinstance(result, BaseException):
            raise result
        result.request = prepared
        result.url = prepared.url
        return result

    def run(cmd, **kwargs):
        assert cmd[0] == "mulerun" and "studio" in cmd and "run" in cmd
        home = Path(kwargs["env"]["HOME"]).resolve()
        assert home.is_dir() and home != Path.home().resolve()
        assert state["cli_queue"], "Unexpected MuleRun subprocess"
        state["cli"].append((cmd, kwargs, home))
        result = state["cli_queue"].pop(0)
        if isinstance(result, BaseException):
            raise result
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(result), stderr="")

    monkeypatch.setattr(requests.sessions.Session, "send", send)
    monkeypatch.setattr(requests.sessions, "get_netrc_auth", lambda *_args, **_kwargs: None)
    # Replace only this adapter's external boundary; ffmpeg/ffprobe remain real.
    monkeypatch.setattr(mulerouter, "subprocess", SimpleNamespace(run=run, TimeoutExpired=subprocess.TimeoutExpired))
    # Studio owns async polling; keep an existing per-test clock (B3 uses it
    # to prove deadlines) and give the legacy adapter the same clock.
    clock = execution_module.time
    if clock is time:
        started = time.time()
        elapsed = {"seconds": 0.0}

        def sleep(seconds):
            elapsed["seconds"] += seconds

        clock = SimpleNamespace(
            time=lambda: started + elapsed["seconds"],
            monotonic=lambda: elapsed["seconds"], sleep=sleep,
        )
        monkeypatch.setattr(execution_module, "time", clock)
    monkeypatch.setattr(mulerouter, "time", clock)
    return state


def setup(item, model=MODELS[0], route="local"):
    root = admission.setup(item, route)
    actual = item.series_store[root[1]] if root[0] == "series" else item.scripts[root[1]]
    actual.model_settings.t2i_model = model
    item._commit_storage("projects.json", "series.json", "library_assets.json")
    return root


def configure(app, kind):
    values = {"MULERUN_SITE": "sg", "MULERUN_STUDIO_SITE": "mulerouter"}
    if kind == "http":
        values["MULEROUTER_API_KEY"] = HTTP_SECRET
    elif kind == "token":
        values["MULERUN_TOKEN"] = CLI_TOKEN
    else:
        home = Path(app.state.runtime_environment.snapshot()["LUMENX_MULERUN_HOME"])
        profile = home / ".mulerun"
        profile.mkdir(parents=True, exist_ok=True)
        (profile / "session.json").write_text("synthetic-private-profile-v1")
    keys = {"DASHSCOPE_API_KEY", "MULEROUTER_API_KEY", "MULERUN_TOKEN"} - set(values)
    app.state.runtime_environment.update(values, remove_keys=keys, persist=lambda *_args: None)


def http_task_id(index=0):
    # The image HTTP receipt contract accepts UUIDs, unlike Wan's opaque IDs.
    return f"00000000-0000-4000-8000-{index + 1:012x}"


def queue_images(state, kind, count=2):
    for index in range(count):
        if kind == "http":
            state["queue"].extend([
                response({"task_info": {"id": http_task_id(index), "status": "pending"}}, status=202),
                response({"task_info": {"id": http_task_id(index), "status": "completed"}, "images": [IMAGE_URL]}),
            ])
        else:
            state["cli_queue"].append({"output": {"image_url": IMAGE_URL}})
        state["queue"].append(response(PNG))


def raw(**changes):
    return dict(client_request_id="gpt-sheet-request", asset_id="c", asset_type="character",
                generation_type="reference_sheet", apply_style=False, prompt="Author prompt", batch_size=2) | changes


@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("kind", ["http", "token", "profile"])
def test_real_http_gpt_sheet_admission_runs_original_core_and_bound_adapter(runtime, transport, model, kind):
    setup(runtime, model)
    queue_images(transport, kind)
    app = http.real_app(runtime)
    with TestClient(app) as client:
        configure(app, kind)
        accepted = client.post("/projects/p/asset_image_requests", json=raw())
        assert accepted.status_code == 200, accepted.text
        assert [m["status"] for m in accepted.json()["request"]["members"]] == ["pending", "pending"]
        saved = runtime.read_committed_script("p")
        record = saved.asset_image_requests[0]
        assert record.asset_type == "character" and record.channel == "reference_sheet"
        assert len(record.members) == 2
        for member in record.members:
            execution = member.execution
            assert execution.phase == "completed"
            assert execution.provider_model_id == "gpt-image-2"
            assert execution.intent["model_id"] == MODELS[1]
            assert "intent_contract_version" not in execution.intent
            assert execution.intent["params"] == {"size": "1024x1536"}
            assert execution.media == []
            content = (runtime.data_dir / execution.output_ref).read_bytes()
            assert content.startswith(b"\x89PNG\r\n\x1a\n")
            assert hashlib.sha256(content).hexdigest() == execution.artifact["sha256"]
        before = (runtime.data_dir / "projects.json").read_bytes()
        readback = client.get("/projects/p/asset_image_requests/gpt-sheet-request")
        replay = client.post("/projects/p/asset_image_requests", json=raw())
        assert readback.status_code == replay.status_code == 200
        assert readback.json()["reason"] is None
        assert [m["status"] for m in readback.json()["request"]["members"]] == ["completed", "completed"]
        assert (runtime.data_dir / "projects.json").read_bytes() == before
        assert not transport["queue"] and not transport["cli_queue"]
        if kind == "http":
            assert len(transport["calls"]) == 6 and transport["cli"] == []
            posts = [json.loads(request.body) for request, _ in transport["calls"] if request.method == "POST"]
            assert posts == [{"prompt": "Author prompt", "size": "1024x1536", "quality": "high", "n": 1}] * 2
        else:
            assert len(transport["calls"]) == len(transport["cli"]) == 2
            for cmd, kwargs, home in transport["cli"]:
                assert "openai/gpt-image-2/generation" in cmd and "--quality" not in cmd and "--images" not in cmd
                assert cmd[cmd.index("--size") + 1] == "1024x1536"
                assert ("--token" in cmd) is (kind == "token")
                assert not home.exists()
                assert kwargs["env"].get("MULEROUTER_API_KEY") is None


@pytest.mark.parametrize("failure", ["connection", "read-timeout", "429", "502", "503", "504"])
def test_bound_core_http_never_repeats_an_uncertain_paid_post(runtime, transport, tmp_path, failure):
    intent = normalize_generation_intent(
        {"model_id": MODELS[1], "mode": "t2i", "prompt": "Author prompt", "params": {"size": "1024x1024"}},
        allowed_modes={"t2i"}, allowed_parameters={"size"},
    )
    transport["queue"] = [requests.ConnectionError("synthetic response lost") if failure == "connection" else requests.ReadTimeout("synthetic response lost") if failure == "read-timeout" else response({}, status=int(failure)) for _ in range(3)]
    with bind_media_runtime_authority(RuntimeEnvironment({"MULEROUTER_API_KEY": HTTP_SECRET}), runtime._provider_credential_store, image_intent=intent) as authority:
        record = build_accepted_sync_artifact_record(
            intent=intent, provider_model_id=authority.provider_model_id, media_kind="image", media=[],
            scope=authority.scope, credential_ref=authority.credential_ref, attempt_id="gpt-uncertain",
            output_ref="assets/studio/gpt-uncertain.png",
        )
        record = record_with_changes(record, phase="submitting")
        with pytest.raises((requests.ConnectionError, requests.ReadTimeout, requests.HTTPError)):
            execute_sync_artifact_to_private_file(record, authority, output_root=runtime.data_dir, scratch_dir=tmp_path / "scratch", max_bytes=1024 * 1024)
    assert len(transport["calls"]) == 1
    assert transport["calls"][0][0].method == "POST" and transport["cli"] == []


def persisted(item):
    names = ("projects.json", "series.json", "library_assets.json", "provider_credentials.json")
    return {name: (item.data_dir / name).read_bytes() if (item.data_dir / name).exists() else None for name in names}


def command(**changes):
    return AssetImageRequestCommand.model_validate(raw(**changes))


def configure_http_runtime(item):
    item.runtime_environment.update({"MULEROUTER_API_KEY": HTTP_SECRET}, remove_keys=("DASHSCOPE_API_KEY",), persist=lambda *_: None)


@pytest.mark.parametrize("route", admission.ROUTES)
@pytest.mark.parametrize("model", MODELS)
def test_four_actual_owners_retain_gpt_history_and_safe_wire(runtime, transport, route, model):
    root = setup(runtime, model, route)
    queue_images(transport, "http")
    path = f"/{'projects' if root[0] == 'project' else 'series'}/{root[1]}/asset_image_requests"
    app = http.real_app(runtime)
    with TestClient(app) as client:
        configure(app, "http")
        result = client.post(path, json=raw())
        assert result.status_code == 200, result.text
        record = admission.owner(runtime, route).asset_image_requests[0]
        assert record.id == result.json()["request"]["id"]
        assert record.owner_kind == {"local": "script", "series": "series", "global": "global", "standalone": "series"}[route]
        assert record.owner_id == {"local": "p", "series": "s", "global": "library", "standalone": "s"}[route]
        assert admission.owner(runtime, route).characters[0].reference_sheet_request_id == record.id
        before = persisted(runtime)
        observed = client.get(path + "/gpt-sheet-request")
        assert observed.status_code == 200 and observed.json()["reason"] is None
        assert all(m["status"] == "completed" for m in observed.json()["request"]["members"])
        assert set(observed.json()["request"]) == set(result.json()["request"])
        assert "credential_ref" not in json.dumps(observed.json()) and HTTP_SECRET not in json.dumps(observed.json())
        assert persisted(runtime) == before


@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("ratio,size", [("9:16", "1024x1536"), ("3:4", "1024x1536"), ("1:1", "1024x1024"), ("4:3", "1536x1024"), ("16:9", "1536x1024")])
def test_existing_studio_sizes_and_quality_match_real_legacy_http_path(runtime, transport, model, ratio, size):
    setup(runtime, model)
    queue_images(transport, "http", count=2)
    app = http.real_app(runtime)
    with TestClient(app) as client:
        configure(app, "http")
        # The existing legacy prepare/generate path is unchanged by this slice.
        params = command(aspect_ratio=ratio, batch_size=1).model_dump(exclude={"client_request_id", "asset_id", "asset_type", "seed", "watermark", "expected_revision", "composition_data"})
        context = runtime._prepare_asset_image(runtime.read_committed_project_view("p"), "c", "character", params)
        runtime._generate_asset_image(context)
        assert runtime.read_committed_script("p").asset_image_requests == []
        result = client.post("/projects/p/asset_image_requests", json=raw(aspect_ratio=ratio, batch_size=1))
        assert result.status_code == 200, result.text
        posts = [json.loads(request.body) for request, _ in transport["calls"] if request.method == "POST"]
        assert posts == [{"prompt": "Author prompt", "size": size, "quality": "high", "n": 1}] * 2
        record = runtime.read_committed_script("p").asset_image_requests[0]
        assert record.members[0].execution.intent["params"] == {"size": size}


@pytest.mark.parametrize("field,value", [
    ("negative_prompt", "negative"), ("negative_prompt", " "), ("seed", 0), ("watermark", False),
    ("watermark", True), ("quality", "high"), ("prompt_extend", True),
    ("reference_image_url", ""), ("reference_image_url", "uploads/missing.png"),
    ("model_name", "openai/gpt-image-2#image"), ("model_name", "gpt-image-2-extra"),
])
def test_unsupported_gpt_fields_fail_before_binding_or_storage(runtime, transport, monkeypatch, field, value):
    setup(runtime)
    app = http.real_app(runtime)
    with TestClient(app) as client:
        configure(app, "http")
        before = persisted(runtime)
        binder = Mock(side_effect=AssertionError("invalid GPT input bound authority"))
        monkeypatch.setattr(execution_module, "bind_media_runtime_authority", binder)
        result = client.post("/projects/p/asset_image_requests", json=raw(**{field: value}))
        assert result.status_code == 422, result.text
        binder.assert_not_called()
        assert persisted(runtime) == before and not transport["calls"] and not transport["cli"]


@pytest.mark.parametrize("patch", [
    {"MULERUN_STUDIO_SITE": "mulerouter", "MULEROUTER_SITE": "mulerun"},
    {"MULERUN_STUDIO_SITE": "mulerun"}, {"MULEROUTER_SITE": "mulerun"},
    {"MULEROUTER_API_BASE_URL": "https://one.invalid", "MULEROUTER_BASE_URL": "https://two.invalid"},
    {"MULERUN_API_BASE_URL": "https://one.invalid", "MULERUN_BASE_URL": "https://two.invalid"},
    {"MULEROUTER_BASE_URL": "https://custom-http.invalid"},
])
def test_ambiguous_or_unsupported_gpt_runtime_is_rejected_before_binding(runtime, transport, monkeypatch, patch):
    setup(runtime)
    app = http.real_app(runtime)
    with TestClient(app) as client:
        configure(app, "http")
        app.state.runtime_environment.update(patch, persist=lambda *_: None)
        before = persisted(runtime)
        binder = Mock(side_effect=AssertionError("ambiguous runtime bound authority"))
        monkeypatch.setattr(execution_module, "bind_media_runtime_authority", binder)
        result = client.post("/projects/p/asset_image_requests", json=raw())
        assert result.status_code == 422, result.text
        binder.assert_not_called()
        assert persisted(runtime) == before and not transport["calls"] and not transport["cli"]


@pytest.mark.parametrize("negative", [None, ""])
@pytest.mark.parametrize("apply_style", [True, False])
def test_root_negative_is_not_used_but_positive_keeps_existing_precedence(runtime, transport, negative, apply_style):
    setup(runtime, route="series")
    runtime.series_store["s"].art_direction = ArtDirection(selected_style_id="custom", style_config={"positive_prompt": "style-positive", "negative_prompt": "root-negative"})
    runtime._commit_storage("series.json")
    queue_images(transport, "http", count=1)
    app = http.real_app(runtime)
    with TestClient(app) as client:
        configure(app, "http")
        result = client.post("/projects/p/asset_image_requests", json=raw(batch_size=1, negative_prompt=negative, apply_style=apply_style))
        assert result.status_code == 200, result.text
        record = runtime.series_store["s"].asset_image_requests[0]
        prompt = record.members[0].execution.intent["prompt"]
        assert prompt.count("Author prompt") == 1 and prompt.count("style-positive") == int(apply_style)
        assert "root-negative" not in prompt
        assert record.members[0].execution.intent["params"] == {"size": "1024x1536"}


@pytest.mark.parametrize("kind", ["http", "token", "profile"])
@pytest.mark.parametrize("complete_first", [False, True])
def test_unknown_gpt_member_stops_unstarted_siblings_without_losing_success(runtime, transport, kind, complete_first):
    setup(runtime)
    target = runtime.scripts["p"].characters[0]
    target.reference_sheet.image_variants = [ImageVariant(id="chosen", url="uploads/chosen.png", is_favorited=True)]
    target.reference_sheet.selected_image_id = "chosen"
    target.image_url = "uploads/chosen.png"
    runtime._commit_storage("projects.json")
    if complete_first:
        queue_images(transport, kind, count=1)
    if kind == "http":
        transport["queue"].append(requests.ConnectionError("synthetic unknown result"))
    else:
        transport["cli_queue"].append(subprocess.TimeoutExpired(["mulerun"], 360))
    app = http.real_app(runtime)
    with TestClient(app) as client:
        configure(app, kind)
        accepted = client.post("/projects/p/asset_image_requests", json=raw())
        assert accepted.status_code == 200, accepted.text
        current = runtime.read_committed_script("p")
        record = current.asset_image_requests[0]
        assert [m.execution.phase for m in record.members] == (["completed", "submitting"] if complete_first else ["submitting", "cancelled"])
        assert current.characters[0].status == "needs_confirmation"
        assert current.characters[0].reference_sheet.selected_image_id == "chosen"
        assert current.characters[0].reference_sheet.image_variants[0].is_favorited
        if complete_first:
            execution = record.members[0].execution
            assert execution.artifact == execution.prepared_artifact
            assert hashlib.sha256((runtime.data_dir / execution.output_ref).read_bytes()).hexdigest() == execution.artifact["sha256"]
        before = persisted(runtime)
        calls = (len(transport["calls"]), len(transport["cli"]))
        read = client.get("/projects/p/asset_image_requests/gpt-sheet-request")
        replay = client.post("/projects/p/asset_image_requests", json=raw())
        assert read.status_code == replay.status_code == 200 and persisted(runtime) == before
        assert (len(transport["calls"]), len(transport["cli"])) == calls
        assert runtime.get_asset_generation_task_status(record.id)["status"] == "needs_confirmation"
        assert sum(request.method == "POST" for request, _ in transport["calls"]) == ((2 if complete_first else 1) if kind == "http" else 0)
        assert len(transport["cli"]) == ((2 if complete_first else 1) if kind != "http" else 0)
    with worker.restarted(runtime) as other:
        assert other.asset_image_service.run_original_request(record.id) is None
        restored = other.asset_image_service.read_request("project", "p", "gpt-sheet-request")
        assert other.asset_image_service.request_state(restored.request)["status"] == "needs_confirmation"
        assert (len(transport["calls"]), len(transport["cli"])) == calls


@pytest.mark.parametrize("kind", ["http", "token", "profile"])
@pytest.mark.parametrize("changed", [False, True])
def test_original_gpt_authority_keeps_runtime_and_detects_changed_credentials(runtime, transport, kind, changed):
    setup(runtime)
    app = http.real_app(runtime)
    with TestClient(app):
        configure(app, kind)
        ack = runtime.asset_image_service.admit("project", "p", command(batch_size=1))
        if changed:
            if kind == "profile":
                home = Path(app.state.runtime_environment.snapshot()["LUMENX_MULERUN_HOME"])
                (home / ".mulerun/session.json").write_text("changed-profile")
            else:
                key = "MULEROUTER_API_KEY" if kind == "http" else "MULERUN_TOKEN"
                app.state.runtime_environment.update({key: "changed-synthetic-secret"}, persist=lambda *_: None)
        else:
            queue_images(transport, kind, count=1)
        # A newly available higher-priority HTTP key cannot reroute a CLI receipt.
        if kind != "http":
            app.state.runtime_environment.update({"MULEROUTER_API_KEY": HTTP_SECRET}, persist=lambda *_: None)
        result = runtime.asset_image_service.run_original_request(ack.request.id)
        assert result.request.members[0].execution.phase == ("cancelled" if changed else "completed")
        if changed:
            assert not transport["calls"] and not transport["cli"]
        else:
            assert len(transport["calls"]) == (3 if kind == "http" else 1)
            assert len(transport["cli"]) == (0 if kind == "http" else 1)


@pytest.mark.parametrize("change", ["negative-presence", "model-alias", "style", "batch"])
def test_same_gpt_key_never_accepts_a_changed_raw_command(runtime, transport, change):
    setup(runtime)
    configure_http_runtime(runtime)
    runtime.asset_image_service.admit("project", "p", command())
    altered = {"negative-presence": {"negative_prompt": None}, "model-alias": {"model_name": MODELS[1]}, "style": {"apply_style": True}, "batch": {"batch_size": 1}}[change]
    before = persisted(runtime)
    with pytest.raises(admission.StorageConflictError, match="payload_conflict"):
        runtime.asset_image_service.admit("project", "p", command(**altered))
    assert persisted(runtime) == before and not transport["calls"] and not transport["cli"]


@pytest.mark.parametrize("change", ["tag", "alias", "provider", "params", "size", "sources", "request-fields", "scope", "http-base", "preset", "full-body-without-prompt", "mixed-member"])
def test_gpt_history_rejects_rehashed_unsupported_shapes(runtime, change):
    setup(runtime)
    configure_http_runtime(runtime)
    payload = runtime.asset_image_service.admit("project", "p", command()).request.model_dump(mode="json")
    record = payload["members"][0]["execution"]
    if change == "tag":
        record["intent"]["intent_contract_version"] = None
    elif change == "alias":
        record["intent"]["model_id"] = "openai/gpt-image-2#image"
    elif change == "provider":
        record["provider_model_id"] = "qwen-image-2.0"
    elif change == "params":
        record["intent"]["params"]["quality"] = "high"
    elif change == "size":
        record["intent"]["params"]["size"] = "auto"
    elif change == "sources":
        record["intent"]["sources"]["params.size"] = "catalog"
    elif change == "request-fields":
        record["intent"]["request_fields"].remove("params.size")
    elif change == "scope":
        record["scope"]["gateway"] = "mulerun_token"
    elif change == "http-base":
        record["scope"]["base_url"] = "https://wrong.invalid"
    elif change == "preset":
        record["intent"]["preset_id"] = "not-from-this-entry"
    elif change == "full-body-without-prompt":
        payload.update(entrypoint="studio.asset.full_body.v1", channel="full_body", base_prompt=None)
    else:
        record["intent"]["prompt"] = "another-member-prompt"
    history.rehash(record)
    with pytest.raises(ValueError):
        AssetImageRequestRecord.model_validate(payload)


@pytest.mark.parametrize("kind,generation_type", [("scene", "reference_sheet"), ("prop", "full_body"), ("character", "all")])
def test_gpt_t2i_rejects_mismatched_or_compound_channels(runtime, transport, kind, generation_type):
    setup(runtime)
    if kind == "scene":
        runtime.scripts["p"].scenes = [Scene(id="c", name="S", description="S")]
    elif kind == "prop":
        runtime.scripts["p"].props = [Prop(id="c", name="P", description="P")]
    runtime._commit_storage("projects.json")
    app = http.real_app(runtime)
    with TestClient(app) as client:
        configure(app, "http")
        before = persisted(runtime)
        body = raw(asset_type=kind, generation_type=generation_type)
        result = client.post("/projects/p/asset_image_requests", json=body)
        assert result.status_code == 422 and persisted(runtime) == before
        assert not transport["calls"] and not transport["cli"]


def test_bound_query_retry_is_safe_without_repeating_paid_submit(runtime, transport):
    setup(runtime)
    queue_images(transport, "http", count=1)
    transport["queue"].insert(1, response({}, status=503))
    app = http.real_app(runtime)
    with TestClient(app) as client:
        configure(app, "http")
        result = client.post("/projects/p/asset_image_requests", json=raw(batch_size=1))
        assert result.status_code == 200
        service = runtime.asset_image_service
        saved = runtime.read_committed_script("p").asset_image_requests[0]
        original = saved.members[0].execution.model_copy(deep=True)
        assert original.schema_version == 3 and original.phase == "submitted"
        assert original.handle is not None and original.handle["task_id"] == http_task_id()
        assert original.observation is None and original.prepared_artifact is None and original.artifact is None
        unit = runtime.read_committed_script("p").characters[0].reference_sheet
        assert unit is not None
        author_before = unit.model_dump(exclude={"image_variants"})
        history_before = deepcopy(unit.image_variants)
        assert [r.method for r, _ in transport["calls"]] == ["POST", "GET"]
        assert service.run_original_request(saved.id) is None
        assert [r.method for r, _ in transport["calls"]] == ["POST", "GET"]
        assert service.recovery_member_state(saved.id, original.attempt_id) == "query"

        # B3 recovery is one explicit operation per call; the failed original
        # worker neither repeats the paid POST nor owns a background retry loop.
        queried = service.recover_member_once(saved.id, original.attempt_id, action="query")
        observed = queried.request.members[0].execution
        assert observed.phase == "observed" and observed.handle == original.handle
        assert observed.observation is not None
        assert observed.observation["status"] == "succeeded"
        assert observed.observation["source_handle"] == original.handle
        assert observed.prepared_artifact is None and observed.artifact is None
        assert [r.method for r, _ in transport["calls"]] == ["POST", "GET", "GET"]
        assert service.recovery_member_state(saved.id, original.attempt_id) == "fetch"
        service.recover_member_once(saved.id, original.attempt_id, action="fetch")
        completed = runtime.read_committed_script("p").asset_image_requests[0].members[0].execution
        assert completed.phase == "completed" and completed.handle == original.handle
        assert completed.fingerprint == original.fingerprint
        assert completed.credential_ref == original.credential_ref and completed.scope == original.scope
        assert completed.artifact == completed.prepared_artifact and completed.artifact is not None
        assert completed.artifact["handle"] == original.handle
        assert completed.artifact["attempt_id"] == original.attempt_id
        assert completed.output_ref is not None
        content = (runtime.data_dir / completed.output_ref).read_bytes()
        assert content.startswith(b"\x89PNG\r\n\x1a\n")
        assert (hashlib.sha256(content).hexdigest(), len(content)) == (completed.artifact["sha256"], completed.artifact["size_bytes"])
        final_unit = runtime.read_committed_script("p").characters[0].reference_sheet
        assert final_unit is not None and final_unit.model_dump(exclude={"image_variants"}) == author_before
        assert final_unit.image_variants[:-1] == history_before
        assert final_unit.image_variants[-1].id == saved.members[0].variant_id
        assert final_unit.image_variants[-1].url == completed.output_ref
    assert [r.method for r, _ in transport["calls"]] == ["POST", "GET", "GET", "GET"]
    original_query_url = original.scope["base_url"] + "/vendors/openai/v1/gpt-image-2/generation/" + http_task_id()
    assert [(r.method, r.url) for r, _ in transport["calls"][1:]] == [
        ("GET", original_query_url), ("GET", original_query_url), ("GET", IMAGE_URL),
    ]
    assert not transport["queue"] and not transport["cli"]


def test_legacy_dynamic_http_keeps_existing_retry_behavior(runtime, transport):
    queue_images(transport, "http", count=1)
    transport["queue"].insert(0, response({}, status=503))
    model = mulerouter.MuleRouterImageModel({"output_root": str(runtime.data_dir)}, env={"MULEROUTER_API_KEY": HTTP_SECRET})
    model.generate("legacy", str(runtime.data_dir / "legacy.png"), size="576*1024")
    assert [r.method for r, _ in transport["calls"]] == ["POST", "POST", "GET", "GET"]


def test_gpt_admission_save_failure_never_publishes_claim_or_calls_provider(runtime, transport, monkeypatch):
    setup(runtime)
    configure_http_runtime(runtime)
    before = persisted(runtime)
    monkeypatch.setattr(runtime._storage, "commit", Mock(side_effect=StorageError("synthetic save failure")))
    with pytest.raises(StorageError, match="save failure"):
        runtime.asset_image_service.admit("project", "p", command())
    # Credential binding is a separate existing private store; business data rolls back.
    assert all(persisted(runtime)[key] == before[key] for key in ("projects.json", "series.json", "library_assets.json"))
    assert runtime.scripts["p"].characters[0].reference_sheet_request_id is None
    assert not runtime.asset_generation_tasks and not getattr(runtime, "_asset_image_runs", {})
    assert not transport["calls"] and not transport["cli"]


@pytest.mark.parametrize("kind", ["http", "token", "profile"])
@pytest.mark.parametrize("selected", [True, False])
def test_saved_prepared_gpt_file_repairs_after_restart_without_authority_or_provider(runtime, transport, monkeypatch, kind, selected):
    setup(runtime)
    old_variant = ImageVariant(
        id="old", url="uploads/old-gpt.png", is_favorited=True, prompt_used="Old author prompt",
        is_uploaded_source=False, upload_type=None,
    )
    target = runtime.scripts["p"].characters[0]
    target.reference_sheet = AssetUnit.model_validate({
        "image_variants": [old_variant], "selected_image_id": "old" if selected else None,
        "image_prompt": "Original author", "video_prompt": "Independent motion draft",
        "image_updated_at": 1, "video_updated_at": 2,
    })
    target.image_url = old_variant.url if selected else None
    author_before = target.reference_sheet.model_dump(exclude={"image_variants"})
    target_before = (target.name, target.description, target.image_url)
    old_path = runtime.data_dir / old_variant.url
    old_path.parent.mkdir(parents=True, exist_ok=True)
    old_path.write_bytes(PNG)
    runtime._commit_storage("projects.json")
    queue_images(transport, kind, count=1)
    app = http.real_app(runtime)
    with TestClient(app):
        configure(app, kind)
        ack = runtime.asset_image_service.admit("project", "p", command(batch_size=1))
        commit = runtime._storage.commit

        def fail_completion(payload):
            requests_on_disk = payload.get("projects.json", {}).get("p", {}).get("asset_image_requests", [])
            if requests_on_disk and requests_on_disk[0]["members"][0]["execution"]["phase"] == "completed":
                raise StorageError("synthetic completed checkpoint failure")
            return commit(payload)

        monkeypatch.setattr(runtime._storage, "commit", fail_completion)
        with pytest.raises(StorageError, match="completed checkpoint"):
            runtime.asset_image_service.run_original_request(ack.request.id)
        raw_execution = worker.disk_request(runtime, "local")["members"][0]["execution"]
        assert raw_execution["artifact"] is None
        if kind == "http":
            assert raw_execution["schema_version"] == 3 and raw_execution["phase"] == "observed"
            assert raw_execution["handle"]["task_id"] == http_task_id()
            assert raw_execution["observation"]["status"] == "succeeded"
            assert raw_execution["observation"]["source_handle"] == raw_execution["handle"]
        else:
            assert raw_execution["schema_version"] == 2 and raw_execution["phase"] == "submitting"
        proof = deepcopy(raw_execution["prepared_artifact"])
        original_bytes = (runtime.data_dir / raw_execution["output_ref"]).read_bytes()
        assert hashlib.sha256(original_bytes).hexdigest() == proof["sha256"]
        assert len(original_bytes) == proof["size_bytes"]
        assert proof["attempt_id"] == raw_execution["attempt_id"]
        assert proof["output_ref"] == raw_execution["output_ref"]
        if kind == "http":
            assert proof["handle"] == raw_execution["handle"]
    calls = (len(transport["calls"]), len(transport["cli"]))
    with worker.restarted(runtime) as other:
        blocked = Mock(side_effect=AssertionError("local repair attempted external execution"))
        monkeypatch.setattr(other, "oss_uploader", blocked)
        service = execution_module.StudioAssetImageService(other, executor=blocked, authority_resolver=blocked)
        assert service.run_original_request(ack.request.id) is None
        other._asset_image_service = service
        other._recover_orphan_tasks()
        restored = service.read_request("project", "p", "gpt-sheet-request")
        member = restored.request.members[0]
        assert member.execution.phase == "completed" and restored.reason is None
        assert member.execution.artifact == member.execution.prepared_artifact == proof
        assert (other.data_dir / member.execution.output_ref).read_bytes() == original_bytes
        target = other.scripts["p"].characters[0]
        unit = target.reference_sheet
        assert unit is not None
        assert unit.image_variants[0] == old_variant
        assert [variant.id for variant in unit.image_variants] == ["old", member.variant_id]
        assert unit.image_variants[1].url == member.execution.output_ref
        assert old_path.read_bytes() == PNG
        if kind == "http":
            assert unit.model_dump(exclude={"image_variants"}) == author_before
            assert (target.name, target.description, target.image_url) == target_before
            assert member.execution.handle == raw_execution["handle"]
            assert member.execution.observation == raw_execution["observation"]
        else:
            assert unit.selected_image_id == ("old" if selected else member.variant_id)
        before = persisted(other)
        service.repair_request("project", "p", "gpt-sheet-request")
        assert persisted(other) == before
        blocked.assert_not_called()
    assert (len(transport["calls"]), len(transport["cli"])) == calls


def test_accepted_gpt_request_reopens_as_unknown_without_claim_or_execution(runtime, transport):
    setup(runtime)
    configure_http_runtime(runtime)
    ack = runtime.asset_image_service.admit("project", "p", command())
    with worker.restarted(runtime) as other:
        app = http.real_app(other)
        with TestClient(app) as client:
            configure(app, "http")
            before = persisted(other)
            read = client.get("/projects/p/asset_image_requests/gpt-sheet-request")
            replay = client.post("/projects/p/asset_image_requests", json=raw())
            assert read.status_code == replay.status_code == 200
            assert all(m["status"] == "needs_confirmation" for m in read.json()["request"]["members"])
            assert replay.json()["replayed"] is True
            assert other.asset_image_service.run_original_request(ack.request.id) is None
            assert not other.asset_generation_tasks and persisted(other) == before
    assert not transport["calls"] and not transport["cli"]


@pytest.mark.parametrize("change", ["selection", "name", "pointer", "root-relation", "owner-deleted"])
def test_late_real_gpt_result_preserves_evidence_without_overwriting_new_owner_state(runtime, transport, change):
    setup(runtime)
    queue_images(transport, "http", count=1)
    edited = None

    def change_target(prepared):
        nonlocal edited
        if prepared.url != IMAGE_URL:
            return
        target = runtime.scripts["p"].characters[0]
        if change == "selection":
            target.reference_sheet.image_variants = [ImageVariant(id="manual", url="uploads/manual.png", is_favorited=True)]
            target.reference_sheet.selected_image_id = "manual"
            target.image_url = "uploads/manual.png"
        elif change == "name":
            target.name = "manually edited"
        elif change == "pointer":
            target.reference_sheet_request_id = None
        elif change == "root-relation":
            runtime.scripts["p"].series_id = "s"
        else:
            del runtime.scripts["p"]
        runtime._commit_storage("projects.json")
        edited = None if change == "owner-deleted" else deepcopy(runtime.scripts["p"].model_dump())

    transport["on_send"] = change_target
    app = http.real_app(runtime)
    with TestClient(app) as client:
        configure(app, "http")
        result = client.post("/projects/p/asset_image_requests", json=raw(batch_size=1))
        assert result.status_code == 200, result.text
        if change == "owner-deleted":
            assert "p" not in runtime.scripts
        else:
            saved = runtime.read_committed_script("p")
            execution = saved.asset_image_requests[0].members[0].execution
            assert execution.schema_version == 3 and execution.phase == "observed"
            assert execution.handle is not None and execution.handle["task_id"] == http_task_id()
            assert execution.observation is not None and execution.observation["status"] == "succeeded"
            assert execution.observation["source_handle"] == execution.handle
            assert execution.prepared_artifact is not None and execution.artifact is None
            assert execution.prepared_artifact["handle"] == execution.handle
            assert execution.prepared_artifact["attempt_id"] == execution.attempt_id
            assert execution.output_ref is not None
            assert execution.prepared_artifact["output_ref"] == execution.output_ref
            assert hashlib.sha256((runtime.data_dir / execution.output_ref).read_bytes()).hexdigest() == execution.prepared_artifact["sha256"]
            assert saved.characters[0].model_dump() == edited["characters"][0]
            before = persisted(runtime)
            restored = runtime.asset_image_service.repair_request("project", "p", "gpt-sheet-request")
            assert restored.reason is not None and persisted(runtime) == before
    assert len(transport["calls"]) == 3 and not transport["cli"]


def test_reference_sheet_existing_uploaded_inputs_and_base_character_do_not_enable_i2i(runtime, transport):
    setup(runtime)
    target = runtime.scripts["p"].characters[0]
    target.base_character_id = "existing-base"
    for slot in ("full_body_asset", "three_view_asset", "headshot_asset"):
        setattr(target, slot, ImageAsset(variants=[ImageVariant(id=slot, url=f"uploads/{slot}.png", is_uploaded_source=True)], selected_id=slot))
    runtime._commit_storage("projects.json")
    queue_images(transport, "http", count=1)
    app = http.real_app(runtime)
    with TestClient(app) as client:
        configure(app, "http")
        result = client.post("/projects/p/asset_image_requests", json=raw(batch_size=1))
        assert result.status_code == 200, result.text
        member = runtime.read_committed_script("p").asset_image_requests[0].members[0].execution
        assert member.phase == "completed" and member.intent["mode"] == "t2i" and member.media == []
        assert "images" not in json.loads(transport["calls"][0][0].body)


@pytest.mark.parametrize("route", admission.ROUTES)
def test_gpt_original_media_copies_without_execution_authority(runtime, transport, route):
    root = setup(runtime, route=route)
    queue_images(transport, "http", count=1)
    app = http.real_app(runtime)
    with TestClient(app) as client:
        configure(app, "http")
        path = f"/{'projects' if root[0] == 'project' else 'series'}/{root[1]}/asset_image_requests"
        assert client.post(path, json=raw(batch_size=1)).status_code == 200
        owner = admission.owner(runtime, route)
        before = owner.characters[0].model_dump()
        request = owner.asset_image_requests[0].model_dump()
        if route == "local":
            copied = runtime.promote_asset_to_library("project", "p", "character", "c")
        elif route == "global":
            copied = runtime.fork_library_asset_to_project("p", "character", "c")
        elif route == "standalone":
            copied = runtime.promote_asset_to_library("series", "s", "character", "c")
        else:
            runtime.series_store["destination"] = Series(id="destination", title="Destination", created_at=1, updated_at=1)
            runtime._commit_storage("series.json")
            destination, imported, skipped = runtime.import_assets_from_series("destination", "s", ["c"])
            assert imported == ["c"] and skipped == []
            copied = destination.characters[0]
        expected = dict(before, id=copied.id, reference_sheet_request_id=None)
        assert copied.model_dump() == expected and copied.id != "c"
        assert owner.characters[0].model_dump() == before and owner.asset_image_requests[0].model_dump() == request
    assert len(transport["calls"]) == 3 and not transport["cli"]


def test_gpt_project_package_strips_receipt_and_pointer_but_retains_original_png(runtime, transport):
    setup(runtime)
    queue_images(transport, "http", count=1)
    app = http.real_app(runtime)
    with TestClient(app) as client:
        configure(app, "http")
        assert client.post("/projects/p/asset_image_requests", json=raw(batch_size=1)).status_code == 200
        original = runtime.read_committed_script("p").model_dump()
        artifact = runtime.export_project_package("p")
        with zipfile.ZipFile(io.BytesIO(artifact.data)) as archive:
            project = json.loads(archive.read("project.json"))
            assert project["script"]["asset_image_requests"] == []
            assert project["script"].get("asset_image_request_scope_id") is None
            assert project["script"]["characters"][0].get("reference_sheet_request_id") is None
            assert HTTP_SECRET not in archive.read("project.json").decode()
        imported = runtime.import_project_package(artifact.data)
        assert imported.id != "p" and not imported.asset_image_requests and imported.asset_image_request_scope_id is None
        character = imported.characters[0]
        assert character.reference_sheet_request_id is None
        source_execution = original["asset_image_requests"][0]["members"][0]["execution"]
        assert (runtime.data_dir / character.image_url).read_bytes() == (runtime.data_dir / source_execution["output_ref"]).read_bytes()
        assert runtime.read_committed_script("p").model_dump() == original
    assert len(transport["calls"]) == 3 and not transport["cli"]


@pytest.mark.parametrize("kind", ["token", "profile"])
def test_original_cli_routes_survive_later_alias_changes_without_rerouting(runtime, transport, kind):
    setup(runtime)
    queue_images(transport, kind, count=1)
    app = http.real_app(runtime)
    with TestClient(app):
        configure(app, kind)
        app.state.runtime_environment.update({"MULERUN_SITE": "cn", "MULERUN_API_BASE_URL": "https://login.invalid/", "MULERUN_BASE_URL": "https://login.invalid", "MULEROUTER_API_BASE_URL": "https://studio.invalid/", "MULEROUTER_BASE_URL": "https://studio.invalid"}, persist=lambda *_: None)
        ack = runtime.asset_image_service.admit("project", "p", command(batch_size=1))
        app.state.runtime_environment.update({"MULERUN_API_BASE_URL": "https://changed.invalid", "MULERUN_STUDIO_SITE": "changed", "MULEROUTER_API_KEY": HTTP_SECRET}, persist=lambda *_: None)
        result = runtime.asset_image_service.run_original_request(ack.request.id)
        assert result.request.members[0].execution.phase == "completed"
        cmd = transport["cli"][0][0]
        indexes = [index for index, value in enumerate(cmd) if value == "--base-url"]
        assert [cmd[index + 1] for index in indexes] == ["https://login.invalid", "https://studio.invalid"]
        assert "changed" not in cmd and not any(request.method == "POST" for request, _ in transport["calls"])


def test_missing_private_gpt_credentials_cannot_fall_back_to_host_home(runtime, transport):
    setup(runtime)
    app = http.real_app(runtime)
    with TestClient(app) as client:
        app.state.runtime_environment.update({}, remove_keys=("DASHSCOPE_API_KEY", "MULEROUTER_API_KEY", "MULERUN_TOKEN"), persist=lambda *_: None)
        before = persisted(runtime)
        result = client.post("/projects/p/asset_image_requests", json=raw())
        assert result.status_code == 422 and persisted(runtime) == before
        assert "profile" in result.text
        assert result.json()["detail"] == "Invalid MuleRun profile"
    assert not transport["calls"] and not transport["cli"]


def test_one_runtime_snapshot_freezes_gpt_routes_profile_and_credentials(runtime, transport, monkeypatch):
    setup(runtime)
    app = http.real_app(runtime)
    with TestClient(app):
        configure(app, "token")
        original_snapshot = app.state.runtime_environment.snapshot
        snapshot = dict(original_snapshot())
        calls = []

        def change_after_snapshot():
            calls.append(True)
            return snapshot if len(calls) == 1 else dict(snapshot, MULEROUTER_API_KEY=HTTP_SECRET, MULERUN_TOKEN="changed")

        monkeypatch.setattr(app.state.runtime_environment, "snapshot", change_after_snapshot)
        ack = runtime.asset_image_service.admit("project", "p", command(batch_size=1))
        assert len(calls) == 1
        assert ack.request.members[0].execution.scope["gateway"] == "mulerun_token"
    assert not transport["calls"] and not transport["cli"]


@pytest.mark.parametrize("snapshot,expected", [
    ({}, ("sg", "https://api.mulerun.com", "mulerouter", "https://api.mulerouter.ai")),
    ({"MULERUN_SITE": "cn", "MULERUN_BASE_URL": "https://login.invalid/", "MULEROUTER_BASE_URL": "https://studio.invalid/"}, ("cn", "https://login.invalid", "mulerouter", "https://studio.invalid")),
    ({"MULERUN_STUDIO_SITE": " MULEROUTER ", "MULEROUTER_SITE": "mulerouter"}, ("sg", "https://api.mulerun.com", "mulerouter", "https://api.mulerouter.ai")),
])
def test_shared_route_assembly_retains_existing_default_and_alias_values(snapshot, expected):
    route = mulerun_routes_from_snapshot(snapshot)
    assert (route.login_site, route.login_base_url, route.studio_site, route.studio_base_url) == expected


@pytest.mark.parametrize("route", ["local", "standalone"])
@pytest.mark.parametrize("model", MODELS)
def test_original_http_task_entry_uses_same_gpt_receipt_without_legacy_fallback(runtime, transport, monkeypatch, route, model):
    root = setup(runtime, model, route)
    queue_images(transport, "http", count=2)
    blocked = Mock(side_effect=AssertionError("strict GPT task fell back to legacy"))
    monkeypatch.setattr(runtime, "_generate_asset_image", blocked)
    app = http.real_app(runtime)
    with TestClient(app) as client:
        configure(app, "http")
        body = raw()
        del body["client_request_id"]
        path = f"/{'projects' if root[0] == 'project' else 'series'}/{root[1]}/assets/generate"
        result = client.post(path, json=body)
        assert result.status_code == 200, result.text
        record = admission.owner(runtime, route).asset_image_requests[0]
        assert result.json()["_task_id"] == record.id
        assert len(record.members) == 2 and all(member.execution.phase == "completed" for member in record.members)
        runtime.process_asset_generation_task(record.id)
        assert runtime.get_asset_generation_task_status(record.id)["status"] == "completed"
        blocked.assert_not_called()
    assert sum(request.method == "POST" for request, _ in transport["calls"]) == 2


def test_original_gpt_task_rejects_explicit_negative_without_fallback(runtime, transport, monkeypatch):
    setup(runtime)
    configure_http_runtime(runtime)
    blocked = Mock(side_effect=AssertionError("unsupported GPT input fell back"))
    monkeypatch.setattr(runtime, "_generate_asset_image", blocked)
    before = persisted(runtime)
    with pytest.raises(ValueError, match="negative_prompt_not_supported"):
        runtime.create_asset_generation_task("p", "c", "character", generation_type="reference_sheet", negative_prompt="explicit unsupported", apply_style=False)
    blocked.assert_not_called()
    assert persisted(runtime) == before and not transport["calls"] and not transport["cli"]


@pytest.mark.parametrize("kind", ["http", "token"])
def test_empty_prompt_and_positive_style_keep_original_legacy_transport_meaning(runtime, transport, monkeypatch, kind):
    setup(runtime)
    discovery = None
    if kind == "token":
        # Supply only this adapter's executable discovery boundary. The real
        # token/routing checks remain active, and global shutil stays untouched.
        discovery = Mock(return_value="/synthetic-test-bin/mulerun")
        monkeypatch.setattr(mulerouter, "shutil", SimpleNamespace(which=discovery))
    runtime.scripts["p"].art_direction = ArtDirection(selected_style_id="custom", style_config={"positive_prompt": "style-positive", "negative_prompt": "unused-negative"})
    runtime._commit_storage("projects.json")
    queue_images(transport, kind, count=2)
    app = http.real_app(runtime)
    with TestClient(app) as client:
        configure(app, kind)
        params = command(prompt="", apply_style=True, batch_size=1).model_dump(exclude={"client_request_id", "asset_id", "asset_type", "seed", "watermark", "expected_revision", "composition_data"})
        context = runtime._prepare_asset_image(runtime.read_committed_project_view("p"), "c", "character", params)
        runtime._generate_asset_image(context)
        if kind == "token":
            assert discovery is not None
            discovery.assert_called_with("mulerun")
        result = client.post("/projects/p/asset_image_requests", json=raw(prompt="", apply_style=True, batch_size=1))
        assert result.status_code == 200, result.text
        if kind == "http":
            payloads = [json.loads(request.body) for request, _ in transport["calls"] if request.method == "POST"]
            assert payloads[0] == payloads[1] and payloads[0]["quality"] == "high"
            prompt = payloads[0]["prompt"]
        else:
            commands = [entry[0] for entry in transport["cli"]]
            assert commands[0] == commands[1]
            assert all("--quality" not in cmd for cmd in commands)
            prompt = commands[0][commands[0].index("--prompt") + 1]
        assert prompt.count("style-positive") == 1 and "unused-negative" not in prompt


@pytest.mark.parametrize("product", ["atelier", "playground"])
@pytest.mark.parametrize("unknown", [False, True])
def test_existing_other_products_keep_bound_gpt_submit_once_and_read_only_replay(runtime, transport, product, unknown):
    setup(runtime)
    if unknown:
        transport["queue"].append(response({}, status=503))
    else:
        queue_images(transport, "http", count=1)
    app = http.real_app(runtime)
    with TestClient(app) as client:
        configure(app, "http")
        if product == "atelier":
            app.state.atelier_storage.create_project(AtelierProject.model_validate({
                "id": "gpt-adjacent", "title": "Existing bound GPT", "created_at": 1, "updated_at": 1,
                "nodes": [{"id": "image", "project_id": "gpt-adjacent", "type": "image"}],
            }))
            path = "/atelier/projects/gpt-adjacent/nodes/image/image_candidates"
            read_path = "/atelier/projects/gpt-adjacent/generation/requests/gpt-adjacent-request"
            body = {"client_request_id": "gpt-adjacent-request", "expected_revision": 0, "mode": "t2i", "model_id": MODELS[1], "prompt": "Adjacent", "params": {"size": "1024x1024"}, "media": [], "batch_size": 1}
        else:
            path = "/playground/generate"
            read_path = "/playground/requests/gpt-adjacent-request"
            body = {"client_request_id": "gpt-adjacent-request", "mode": "t2i", "model_id": MODELS[1], "prompt": "Adjacent", "parameters": {"size": "1024x1024"}, "batch_size": 1}
        accepted = client.post(path, json=body)
        assert accepted.status_code == 200, accepted.text
        assert app.state.atelier_generation_dispatcher.wait_for_idle(10)
        read = client.get(read_path)
        assert read.status_code == 200, read.text
        attempt = read.json()["members"][0]["attempt"] if product == "atelier" else read.json()["generation"]["attempts"][0]
        assert attempt["status"] == ("needs_confirmation" if unknown else "completed")
        assert attempt["provider_execution"]["phase"] == ("submitting" if unknown else "completed")
        calls = len(transport["calls"])
        replay = client.post(path, json=body)
        assert replay.status_code == 200 and replay.json()["replayed"]
        assert app.state.atelier_generation_dispatcher.wait_for_idle(10)
        assert len(transport["calls"]) == calls == (1 if unknown else 3)
    assert sum(request.method == "POST" for request, _ in transport["calls"]) == 1
    assert not transport["cli"] and not transport["queue"]
