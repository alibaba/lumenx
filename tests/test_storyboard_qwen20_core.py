"""Real Studio frame HTTP → Core → Qwen transport, in the pre-import guard."""

import hashlib
import io
import json
import struct
import zipfile
from unittest.mock import Mock

from fastapi.testclient import TestClient
import pytest
import requests

from src.apps.comic_gen import pipeline as module
from src.apps.comic_gen.asset_image_execution import AssetImageRequestCommand
from src.apps.comic_gen.asset_image_models import AssetImageRequestRecord
from src.apps.comic_gen.models import Character, GenerationStatus, GlobalAssetLibrary, ImageAsset, ImageVariant, Scene, Script, Series, StoryboardFrame
from src.apps.comic_gen.project_package import build_package, prepare_import
from src.utils.json_storage import StorageConflictError, StorageError
from src.utils.runtime_environment import RuntimeEnvironment
from tests import test_studio_core_image_api as http
from tests import test_studio_core_image_worker as worker
from tests import test_studio_core_image_models as history
from tests.frozen_image_fixtures import rgb_png_bytes
from tests.test_qwen20_bound_image import completed
from tests.test_wan27_bound_image import response


MODELS = ("qwen-image-2.0", "qwen-image-2.0-pro", "qwen/qwen-image-2.0#image", "qwen/qwen-image-2.0-pro#image")
VERSION = "provider-qwen20-t2i-bound-http-v1"
PNG = rgb_png_bytes(width=16, height=16)


@pytest.fixture
def transport(monkeypatch):
    state = {"calls": [], "queue": [], "on_send": None}

    def send(session, request, **options):
        state["calls"].append((request, options))
        if state["on_send"]:
            state["on_send"](request)
        assert state["queue"], "unexpected external HTTP request"
        result = state["queue"].pop(0)
        if isinstance(result, Exception):
            raise result
        result.request, result.url = request, request.url
        return result

    # Both old requests.post and strict isolated Sessions retain real payload
    # preparation; only the external transport is replaced.
    monkeypatch.setattr(requests.sessions.Session, "send", send)
    # The legacy Session still consults netrc before send; no host credentials
    # are relevant to this synthetic transport, and the import guard blocks it.
    monkeypatch.setattr(requests.sessions, "get_netrc_auth", lambda *_: None)
    return state


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    for name in ("VideoGenerator", "AudioGenerator", "ExportManager"):
        monkeypatch.setattr(module, name, Mock())
    item = module.ComicGenPipeline(
        data_dir=tmp_path / "data", script_processor=Mock(),
        runtime_environment=RuntimeEnvironment({"DASHSCOPE_API_KEY": "synthetic-frame-test-key"}),
    )
    item.scripts["p"] = Script(
        id="p", title="project", original_text="script", created_at=1, updated_at=1,
        characters=[Character(id="c", name="Alice", description="red coat")],
        scenes=[Scene(id="s", name="Garden", description="quiet trees")],
        frames=[StoryboardFrame(
            id="f", scene_id="s", character_ids=["c"], action_description="walks",
            image_prompt="original prompt", image_url="uploads/old.png", rendered_image_url="uploads/old.png",
            rendered_image_asset=ImageAsset(selected_id="old", variants=[
                ImageVariant(id="old", url="uploads/old.png", prompt_used="old prompt", is_favorited=True),
            ]),
        )],
    )
    item.scripts["p"].model_settings.i2i_model = MODELS[0]
    item._commit_storage("projects.json", "series.json", "library_assets.json")
    path = item.data_dir / "uploads/old.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(PNG)
    yield item
    item.close()


def setup(item, model=MODELS[0]):
    item.scripts["p"].model_settings.i2i_model = model
    item._commit_storage("projects.json")


def command(item, **changes):
    return dict(client_request_id="frame-request", asset_id="f", asset_type="storyboard_frame",
                generation_type="all", prompt="Author prompt", expected_revision=item.read_committed_script("p").revision,
                composition_data={}, batch_size=1) | changes


def queue_image(transport):
    transport["queue"] = [response(completed()), response(PNG)]


def assert_completed(item, transport, model, *, expected_prompt="Author prompt Characters: Alice (red coat).", size="1024*576"):
    saved = item.read_committed_script("p")
    frame = saved.frames[0]
    assert frame.status == "completed"
    assert len(saved.asset_image_requests) == 1
    record = saved.asset_image_requests[0]
    member = record.members[0]
    assert (record.entrypoint, record.asset_type, record.channel) == ("studio.storyboard.image.v1", "storyboard_frame", "image")
    assert (record.owner_kind, record.owner_id, record.request_root_kind) == ("script", "p", "project")
    assert frame.image_request_id == record.id
    assert member.execution.phase == "completed" and member.execution.intent["intent_contract_version"] == VERSION
    assert frame.rendered_image_asset.selected_id == member.variant_id
    assert frame.image_url == frame.rendered_image_url == member.execution.output_ref
    image_bytes = (item.data_dir / member.execution.output_ref).read_bytes()
    assert image_bytes[:8] == b"\x89PNG\r\n\x1a\n"
    assert struct.unpack(">II", image_bytes[16:24]) == (16, 16)
    assert member.execution.artifact["sha256"] == hashlib.sha256(image_bytes).hexdigest()
    original = next(v for v in frame.rendered_image_asset.variants if v.id == "old")
    assert original.is_favorited and original.prompt_used == "old prompt"
    assert len(transport["calls"]) == 2
    submit, fetch = [value[0] for value in transport["calls"]]
    assert submit.method == "POST" and submit.url.endswith("/services/aigc/multimodal-generation/generation")
    assert "X-DashScope-Async" not in submit.headers
    assert fetch.method == "GET" and "Authorization" not in fetch.headers
    payload = json.loads(submit.body)
    assert payload["model"] == ("qwen-image-2.0-pro" if "2.0-pro" in model else "qwen-image-2.0")
    assert payload["input"]["messages"][0]["content"] == [{"text": expected_prompt}]
    assert payload["parameters"] == {"size": size, "watermark": False, "prompt_extend": True, "n": 1}
    return record


@pytest.mark.parametrize("model", MODELS[:2])
def test_old_render_uses_real_synchronous_qwen_core_and_persists_frame(runtime, transport, model):
    setup(runtime, model)
    queue_image(transport)
    app = http.real_app(runtime)
    with TestClient(app) as client:
        http.enable_fake_key(app)
        result = client.post("/projects/p/storyboard/render", json={"frame_id": "f", "composition_data": {}, "prompt": "Author prompt"})
        assert result.status_code == 200, result.text
        assert result.json()["frames"][0]["status"] == "completed"
        assert_completed(runtime, transport, model)


@pytest.mark.parametrize("model", MODELS)
def test_durable_frame_http_admission_completion_and_same_key_are_one_call(runtime, transport, model):
    setup(runtime, model)
    raw = command(runtime)
    queue_image(transport)
    app = http.real_app(runtime)
    with TestClient(app) as client:
        http.enable_fake_key(app)
        result = client.post("/projects/p/asset_image_requests", json=raw)
        assert result.status_code == 200, result.text
        assert result.json()["request"]["members"][0]["status"] == "pending"
        record = assert_completed(runtime, transport, model)
        before = (runtime.data_dir / "projects.json").read_bytes()
        read = client.get("/projects/p/asset_image_requests/frame-request")
        replay = client.post("/projects/p/asset_image_requests", json=raw)
        assert read.status_code == replay.status_code == 200
        assert read.json()["request"]["id"] == replay.json()["request"]["id"] == record.id
        assert read.json()["request"]["members"][0]["status"] == "completed" and replay.json()["replayed"] is True
        assert read.json()["reason"] is None and replay.json()["reason"] is None
        assert (runtime.data_dir / "projects.json").read_bytes() == before
        assert len(transport["calls"]) == 2


def persisted(item):
    return {name: (item.data_dir / name).read_bytes() for name in ("projects.json", "series.json", "library_assets.json")}


def admit(item, **changes):
    return item.asset_image_service.admit("project", "p", AssetImageRequestCommand.model_validate(command(item, **changes)))


@pytest.mark.parametrize("field,value", [
    ("expected_revision", None), ("expected_revision", True), ("expected_revision", -1), ("expected_revision", 1.5),
    ("batch_size", 2), ("batch_size", True), ("prompt", None),
    ("composition_data", {"reference_image_urls": []}), ("composition_data", {"reference_image_url": ""}),
    ("composition_data", {"reference_image_urls": ["uploads/missing.png"]}),
    ("style_preset", None), ("reference_image_url", None), ("style_prompt", None), ("apply_style", True),
    ("negative_prompt", ""), ("model_name", MODELS[0]), ("aspect_ratio", "16:9"), ("seed", 0), ("watermark", False),
])
def test_invalid_frame_command_never_binds_or_writes(runtime, transport, monkeypatch, field, value):
    app = http.real_app(runtime)
    with TestClient(app) as client:
        http.enable_fake_key(app)
        before = persisted(runtime)
        binding = Mock(side_effect=AssertionError("invalid frame bound credentials"))
        monkeypatch.setattr("src.apps.comic_gen.asset_image_execution.bind_media_runtime_authority", binding)
        result = client.post("/projects/p/asset_image_requests", json=command(runtime, **{field: value}))
        assert result.status_code == 422, result.text
        assert persisted(runtime) == before and transport["calls"] == []
        binding.assert_not_called()


@pytest.mark.parametrize("field", ["expected_revision", "prompt"])
def test_frame_author_fields_cannot_be_omitted(runtime, transport, field):
    raw = command(runtime)
    raw.pop(field)
    with TestClient(http.real_app(runtime)) as client:
        before = persisted(runtime)
        result = client.post("/projects/p/asset_image_requests", json=raw)
        assert result.status_code == 422 and persisted(runtime) == before and transport["calls"] == []


@pytest.mark.parametrize("field", ["expected_revision", "composition_data"])
def test_frame_fields_do_not_widen_old_asset_commands(runtime, transport, field):
    raw = dict(client_request_id="old", asset_type="character", asset_id="c", generation_type="reference_sheet")
    raw[field] = None
    with TestClient(http.real_app(runtime)) as client:
        before = persisted(runtime)
        result = client.post("/projects/p/asset_image_requests", json=raw)
        assert result.status_code == 422 and persisted(runtime) == before and transport["calls"] == []


@pytest.mark.parametrize("source", ["char-legacy", "char-selected", "scene-selected", "series-char", "global-scene"])
def test_declared_reference_including_missing_files_is_not_pure_t2i(runtime, transport, source):
    project = runtime.scripts["p"]
    if source == "char-legacy":
        project.characters[0].image_url = "uploads/missing.png"
    elif source == "char-selected":
        project.characters[0].three_view_asset = ImageAsset(selected_id="ref", variants=[ImageVariant(id="ref", url="uploads/missing.png")])
    elif source == "scene-selected":
        project.scenes[0].image_asset = ImageAsset(selected_id="ref", variants=[ImageVariant(id="ref", url="https://example.invalid/reference.png")])
    elif source == "series-char":
        project.series_id = "series"
        char = project.characters.pop()
        char.full_body_image_url = "uploads/missing.png"
        runtime.series_store["series"] = Series(id="series", title="series", characters=[char], created_at=1, updated_at=1)
    else:
        scene = project.scenes.pop()
        scene.image_url = "uploads/missing.png"
        runtime.library_store.scenes.append(scene)
    runtime._commit_storage("projects.json", "series.json", "library_assets.json")
    with TestClient(http.real_app(runtime)) as client:
        before = persisted(runtime)
        result = client.post("/projects/p/asset_image_requests", json=command(runtime))
        assert result.status_code == 422, result.text
        assert persisted(runtime) == before and transport["calls"] == []


def test_stale_author_revision_never_borrows_fresh_project_revision(runtime, transport, monkeypatch):
    raw = command(runtime)
    runtime.update_frame("p", "f", expected_revision=raw["expected_revision"], image_prompt="new author input")
    binding = Mock(side_effect=AssertionError("stale frame bound credentials"))
    monkeypatch.setattr("src.apps.comic_gen.asset_image_execution.bind_media_runtime_authority", binding)
    with TestClient(http.real_app(runtime)) as client:
        before = persisted(runtime)
        result = client.post("/projects/p/asset_image_requests", json=raw)
        assert result.status_code == 409, result.text
        assert persisted(runtime) == before and transport["calls"] == []
        binding.assert_not_called()


@pytest.mark.parametrize("change", ["revision", "prompt", "presence", "frame"])
def test_frame_key_replay_never_accepts_changed_raw_identity(runtime, transport, change):
    raw = command(runtime)
    queue_image(transport)
    app = http.real_app(runtime)
    with TestClient(app) as client:
        http.enable_fake_key(app)
        assert client.post("/projects/p/asset_image_requests", json=raw).status_code == 200
        changed = dict(raw)
        if change == "presence":
            changed.pop("composition_data")
        else:
            changed[{"revision": "expected_revision", "prompt": "prompt", "frame": "asset_id"}[change]] = {
                "revision": runtime.read_committed_script("p").revision, "prompt": "changed", "frame": "other",
            }[change]
        before = persisted(runtime)
        assert client.post("/projects/p/asset_image_requests", json=changed).status_code == 409
        assert persisted(runtime) == before and len(transport["calls"]) == 2


@pytest.mark.parametrize("ratio,size", [("9:16", "576*1024"), ("3:4", "768*1024"), ("1:1", "1024*1024"), ("4:3", "1024*768"), ("16:9", "1024*576")])
def test_frame_uses_existing_i2i_model_and_size_without_asset_style_defaults(runtime, transport, ratio, size):
    project = runtime.scripts["p"]
    project.model_settings.storyboard_aspect_ratio = ratio
    project.model_settings.t2i_model = "wan2.7-image-pro"
    project.style_prompt = "MUST NOT append this root style"
    runtime._commit_storage("projects.json")
    queue_image(transport)
    app = http.real_app(runtime)
    with TestClient(app) as client:
        http.enable_fake_key(app)
        assert client.post("/projects/p/asset_image_requests", json=command(runtime)).status_code == 200
        record = assert_completed(runtime, transport, MODELS[0], size=size)
        assert record.base_prompt == "Author prompt"
        assert set(record.members[0].execution.intent["params"]) == {"size", "watermark", "prompt_extend"}


@pytest.mark.parametrize("prompt", ["", "Author prompt", "Already Alice (red coat)."])
def test_effective_prompt_does_not_invalidate_success_or_clean_reopen(runtime, transport, prompt):
    queue_image(transport)
    app = http.real_app(runtime)
    with TestClient(app) as client:
        http.enable_fake_key(app)
        result = client.post("/projects/p/asset_image_requests", json=command(runtime, prompt=prompt))
        assert result.status_code == 200
        expected = ("Storyboard Frame: walks. Characters: Alice (red coat). Location: Garden, quiet trees. Camera: Medium Shot."
                    if not prompt else prompt if "Alice (red coat)" in prompt else prompt + " Characters: Alice (red coat).")
        record = assert_completed(runtime, transport, MODELS[0], expected_prompt=expected)
        assert record.base_prompt == prompt
        assert client.get("/projects/p/asset_image_requests/frame-request").json()["reason"] is None
    with worker.restarted(runtime) as other:
        before = persisted(other)
        recovered = other.asset_image_service.read_request("project", "p", "frame-request")
        assert recovered.reason is None and other.asset_image_service.request_state(recovered.request)["status"] == "completed"
        assert other.asset_image_service.run_original_request(record.id) is None
        assert persisted(other) == before and len(transport["calls"]) == 2


@pytest.mark.parametrize("phase", ["accepted", "lost-response"])
def test_restart_retains_frame_unknown_without_new_provider_authority(runtime, transport, phase):
    original = admit(runtime)
    if phase == "lost-response":
        transport["queue"] = [requests.Timeout("synthetic lost response")]
        runtime.asset_image_service.run_original_request(original.request.id)
    count = len(transport["calls"])
    with worker.restarted(runtime) as other:
        other._recover_orphan_tasks()
        before = persisted(other)
        app = http.real_app(other)
        with TestClient(app) as client:
            result = client.get("/projects/p/asset_image_requests/frame-request")
            assert result.status_code == 200
            assert result.json()["request"]["members"][0]["status"] == "needs_confirmation"
            assert other.read_committed_script("p").frames[0].status == "needs_confirmation"
            assert other.asset_image_service.run_original_request(original.request.id) is None
            assert persisted(other) == before and len(transport["calls"]) == count


def test_original_claim_loses_credentials_before_transport_without_retry(runtime, transport):
    original = admit(runtime)
    runtime.runtime_environment.update({"DASHSCOPE_API_KEY": "different-synthetic-key"}, persist=lambda *_: None)
    result = runtime.asset_image_service.run_original_request(original.request.id)
    assert result is not None and result.request.members[0].execution.phase == "cancelled"
    assert transport["calls"] == []
    assert runtime.asset_image_service.run_original_request(original.request.id) is None


@pytest.mark.parametrize("edit", ["prompt", "selection", "character", "reference", "model", "style", "owner", "replace-frame", "replace-project", "delete-frame"])
def test_late_paid_result_cannot_overwrite_changed_frame_or_inputs(runtime, transport, edit):
    queue_image(transport)
    def change(request):
        if request.method != "POST":
            return
        transport["on_send"] = None
        project = runtime.scripts["p"]
        frame = project.frames[0]
        if edit == "prompt":
            runtime.update_frame("p", "f", expected_revision=project.revision, image_prompt="new author prompt")
        elif edit == "selection":
            frame.rendered_image_asset.variants.append(ImageVariant(id="manual", url="uploads/manual.png"))
            frame.rendered_image_asset.selected_id = "manual"
        elif edit == "character":
            project.characters[0].description = "blue coat"
        elif edit == "reference":
            project.characters[0].image_url = "uploads/missing.png"
        elif edit == "model":
            project.model_settings.i2i_model = MODELS[1]
        elif edit == "style":
            project.style_prompt = "new style"
        elif edit == "owner":
            runtime.series_store["other"] = Series(id="other", title="other", created_at=1, updated_at=1)
            project.series_id = "other"
        elif edit == "replace-frame":
            project.frames[0] = frame.model_copy(deep=True)
        elif edit == "replace-project":
            runtime.scripts["p"] = project.model_copy(deep=True)
        else:
            project.frames.clear()
        runtime._commit_storage("projects.json", "series.json")
    transport["on_send"] = change
    original = admit(runtime)
    runtime.asset_image_service.run_original_request(original.request.id)
    record = runtime.read_committed_script("p").asset_image_requests[0]
    assert record.members[0].execution.phase == "submitting"
    assert record.members[0].execution.prepared_artifact is not None
    assert (runtime.data_dir / record.members[0].execution.output_ref).is_file()
    project = runtime.read_committed_script("p")
    if project.frames:
        assert all(v.id != record.members[0].variant_id for v in project.frames[0].rendered_image_asset.variants)
        if edit == "prompt":
            assert project.frames[0].image_prompt == "new author prompt"
        if edit == "selection":
            assert project.frames[0].rendered_image_asset.selected_id == "manual"
    assert len(transport["calls"]) == 2


def test_unrelated_title_and_voice_write_does_not_invalidate_frame(runtime, transport):
    queue_image(transport)
    def change(request):
        if request.method == "POST":
            transport["on_send"] = None
            runtime.scripts["p"].title = "new unrelated title"
            runtime.scripts["p"].characters[0].voice_id = "new voice"
            runtime._commit_storage("projects.json")
    transport["on_send"] = change
    original = admit(runtime)
    runtime.asset_image_service.run_original_request(original.request.id)
    assert_completed(runtime, transport, MODELS[0])


def test_prepared_local_file_repairs_after_save_failure_without_provider_or_credentials(runtime, transport, monkeypatch):
    original = admit(runtime)
    queue_image(transport)
    commit = runtime._storage.commit
    def fail_completion(payloads):
        if any(member["execution"]["phase"] == "completed" for owner in payloads.get("projects.json", {}).values()
               for record in owner.get("asset_image_requests", []) for member in record["members"]):
            raise StorageError("synthetic completion disk failure")
        return commit(payloads)
    monkeypatch.setattr(runtime._storage, "commit", fail_completion)
    with pytest.raises(StorageError, match="completion disk failure"):
        runtime.asset_image_service.run_original_request(original.request.id)
    raw = json.loads((runtime.data_dir / "projects.json").read_bytes())["p"]
    saved = raw["asset_image_requests"][0]["members"][0]["execution"]
    assert saved["phase"] == "submitting" and saved["prepared_artifact"] is not None
    before_image = (runtime.data_dir / saved["output_ref"]).read_bytes()
    with worker.restarted(runtime) as other:
        other._recover_orphan_tasks()
        recovered = other.asset_image_service.read_request("project", "p", "frame-request")
        assert recovered.reason is None and other.asset_image_service.request_state(recovered.request)["status"] == "completed"
        assert (other.data_dir / saved["output_ref"]).read_bytes() == before_image
        frame = other.read_committed_script("p").frames[0]
        assert frame.rendered_image_asset.selected_id == original.request.members[0].variant_id
        assert other.asset_image_service.run_original_request(original.request.id) is None
    assert len(transport["calls"]) == 2


def test_legacy_render_unknown_is_not_a_successful_script_ack(runtime, transport):
    transport["queue"] = [requests.Timeout("synthetic lost response")]
    app = http.real_app(runtime)
    with TestClient(app) as client:
        http.enable_fake_key(app)
        result = client.post("/projects/p/storyboard/render", json={"frame_id": "f", "prompt": "Author prompt"})
        assert result.status_code == 502, result.text
        saved = runtime.read_committed_script("p")
        assert saved.frames[0].status == "needs_confirmation" and len(saved.asset_image_requests) == 1
    assert len(transport["calls"]) == 1


@pytest.mark.parametrize("busy", ["legacy", "core"])
def test_core_and_legacy_frame_claims_are_mutually_exclusive(runtime, transport, busy):
    if busy == "legacy":
        runtime._storyboard_image_runs = {("p", "f"): object()}
        before = persisted(runtime)
        with pytest.raises(StorageConflictError, match="busy"):
            admit(runtime)
        assert persisted(runtime) == before
    else:
        original = admit(runtime)
        view = runtime.read_committed_project_view("p")
        context = runtime._prepare_storyboard_image(view, "f", {"reference_image_urls": []}, "Author prompt", 1)
        with pytest.raises(StorageConflictError, match="already generating"):
            runtime._generate_storyboard_image(context)
        assert runtime.asset_image_service.read_request("project", "p", "frame-request").request.id == original.request.id
    assert transport["calls"] == []


@pytest.mark.parametrize("has_image", [True, False])
def test_frame_copy_and_reparse_preserve_history_without_execution_pointer(runtime, transport, monkeypatch, has_image):
    original = admit(runtime)
    if not has_image:
        frame = runtime.scripts["p"].frames[0]
        frame.image_url = frame.rendered_image_url = None
        frame.rendered_image_asset = ImageAsset()
        runtime._commit_storage("projects.json")
    copied = runtime.copy_frame("p", "f")
    clone = next(frame for frame in copied.frames if frame.id != "f")
    assert clone.image_request_id is None and clone.status == ("completed" if has_image else "pending")
    if has_image:
        assert clone.rendered_image_asset.variants[0].id == "old"
    parsed = runtime.read_committed_script("p").model_copy(deep=True)
    parsed.frames = [parsed.frames[0]]
    monkeypatch.setattr(runtime, "_parse_project_snapshot", lambda *_: parsed)
    replaced = runtime.reparse_project("p", "new script", expected_revision=runtime.read_committed_script("p").revision)
    assert replaced.frames[0].image_request_id is None
    assert replaced.frames[0].status == ("completed" if has_image else "pending")
    assert replaced.asset_image_requests[0].id == original.request.id
    assert runtime.asset_image_service.run_original_request(original.request.id).reason == "studio_image_request_target_changed"
    assert transport["calls"] == []


@pytest.mark.parametrize("has_image", [True, False])
def test_frame_package_keeps_media_but_removes_receipts_and_execution_pointer(runtime, transport, tmp_path, has_image):
    original = admit(runtime)
    if not has_image:
        frame = runtime.scripts["p"].frames[0]
        frame.image_url = frame.rendered_image_url = None
        frame.rendered_image_asset = ImageAsset()
        runtime._commit_storage("projects.json")
    source = runtime.read_committed_script("p")
    assets = GlobalAssetLibrary(characters=source.characters, scenes=source.scenes)
    package = build_package(source, resolved_assets=assets, data_dir=runtime.data_dir, source_storage_id="source", writer_lock=runtime._writer_lock)
    with zipfile.ZipFile(io.BytesIO(package.data)) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
        payload = json.loads(archive.read("project.json"))
    assert payload["script"]["frames"][0].get("image_request_id") is None
    assert payload["script"]["asset_image_requests"] == []
    imported = prepare_import(package.data, data_dir=tmp_path / "target")
    assert imported.script.frames[0].image_request_id is None and imported.script.asset_image_requests == []
    assert imported.script.frames[0].status == ("completed" if has_image else "pending")
    if has_image:
        assert imported.script.frames[0].rendered_image_asset.variants[0].id != "old"
    # An older producer may include a pointer even without retained receipts.
    payload["script"]["frames"][0]["image_request_id"] = original.request.id
    payload["script"]["frames"][0]["status"] = "processing"
    entries["project.json"] = json.dumps(payload).encode()
    altered = io.BytesIO()
    with zipfile.ZipFile(altered, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    imported_old = prepare_import(altered.getvalue(), data_dir=tmp_path / "old-producer-target")
    assert imported_old.script.frames[0].image_request_id is None
    assert imported_old.script.frames[0].status == ("completed" if has_image else "pending")
    assert runtime.read_committed_script("p").asset_image_requests[0].id == original.request.id
    assert transport["calls"] == []


def test_frame_history_cannot_claim_series_owner_or_multiple_members(runtime):
    raw = admit(runtime).request.model_dump(mode="json")
    AssetImageRequestRecord.model_validate(raw)
    changed = dict(raw, request_root_kind="series", owner_kind="series", request_root_series_id=None)
    with pytest.raises(ValueError, match="storyboard"):
        AssetImageRequestRecord.model_validate(changed)
    changed = dict(raw, members=[raw["members"][0], raw["members"][0]])
    with pytest.raises(ValueError, match="storyboard"):
        AssetImageRequestRecord.model_validate(changed)


@pytest.mark.parametrize("params", [{"negative_prompt": ""}, {"seed": 0}, {"watermark": True}, {"prompt_extend": False}])
def test_frame_history_cannot_rehash_unsupported_parameter_policy(runtime, params):
    raw = admit(runtime).request.model_dump(mode="json")
    execution = raw["members"][0]["execution"]
    execution["intent"]["params"].update(params)
    history.rehash(execution)
    with pytest.raises(ValueError, match="storyboard"):
        AssetImageRequestRecord.model_validate(raw)


@pytest.mark.parametrize("kind", ["series", "missing-frame", "other-model"])
def test_strict_frame_owner_and_model_are_checked_before_binding(runtime, transport, monkeypatch, kind):
    path = "/projects/p/asset_image_requests"
    raw = command(runtime)
    if kind == "series":
        runtime.series_store["p"] = Series(id="p", title="series", created_at=1, updated_at=1)
        runtime._commit_storage("series.json")
        path = "/series/p/asset_image_requests"
    elif kind == "missing-frame":
        raw["asset_id"] = "missing"
    else:
        runtime.scripts["p"].model_settings.i2i_model = "wan2.7-image"
        runtime._commit_storage("projects.json")
        raw = command(runtime)
    binding = Mock(side_effect=AssertionError("unsupported frame bound credentials"))
    monkeypatch.setattr("src.apps.comic_gen.asset_image_execution.bind_media_runtime_authority", binding)
    with TestClient(http.real_app(runtime)) as client:
        before = persisted(runtime)
        result = client.post(path, json=raw)
        assert result.status_code == (404 if kind == "missing-frame" else 422), result.text
        assert persisted(runtime) == before and transport["calls"] == []
        binding.assert_not_called()


def test_admission_save_failure_has_no_claim_or_provider_call(runtime, transport, monkeypatch):
    before = persisted(runtime)
    monkeypatch.setattr(runtime._storage, "commit", Mock(side_effect=StorageError("synthetic admission failure")))
    with pytest.raises(StorageError, match="admission failure"):
        admit(runtime)
    assert persisted(runtime) == before
    assert runtime.scripts["p"].frames[0].image_request_id is None
    assert runtime.asset_generation_tasks == {} and not getattr(runtime, "_asset_image_runs", {})
    assert transport["calls"] == []


def test_new_reference_before_original_worker_is_rejected_without_transport(runtime, transport):
    original = admit(runtime)
    runtime.scripts["p"].characters[0].image_url = "uploads/missing.png"
    runtime._commit_storage("projects.json")
    result = runtime.asset_image_service.run_original_request(original.request.id)
    assert result.reason == "studio_image_request_inputs_changed"
    assert result.request.members[0].execution.phase == "cancelled"
    assert transport["calls"] == []


def test_repair_and_legacy_frame_claims_are_mutually_exclusive(runtime, transport, monkeypatch):
    original = admit(runtime)
    queue_image(transport)
    with monkeypatch.context() as patch:
        # A completed provider result is durably prepared, but projection is
        # deliberately interrupted at its existing boundary.
        patch.setattr(runtime.asset_image_service, "_complete_member", lambda *_: None)
        runtime.asset_image_service.run_original_request(original.request.id)
    with worker.restarted(runtime) as other:
        token = object()
        other._storyboard_image_runs = {("p", "f"): token}
        before = persisted(other)
        result = other.asset_image_service.repair_request("project", "p", "frame-request")
        assert result.request.members[0].execution.phase == "submitting"
        assert persisted(other) == before and other._storyboard_image_runs[("p", "f")] is token
        del other._storyboard_image_runs[("p", "f")]
        complete = other.asset_image_service._complete_member
        def competing_legacy(*args):
            context = other._prepare_storyboard_image(other.read_committed_project_view("p"), "f", {}, "Author prompt", 1)
            with pytest.raises(StorageConflictError, match="already generating"):
                other._generate_storyboard_image(context)
            return complete(*args)
        monkeypatch.setattr(other.asset_image_service, "_complete_member", competing_legacy)
        recovered = other.asset_image_service.repair_request("project", "p", "frame-request")
        assert recovered.reason is None and recovered.request.members[0].execution.phase == "completed"
        assert not other._asset_image_runs and not other._storyboard_image_runs
    assert len(transport["calls"]) == 2


def test_public_frame_wire_exposes_only_the_existing_safe_receipt(runtime, transport):
    queue_image(transport)
    app = http.real_app(runtime)
    with TestClient(app) as client:
        http.enable_fake_key(app)
        raw = command(runtime)
        accepted = client.post("/projects/p/asset_image_requests", json=raw)
        observed = client.get("/projects/p/asset_image_requests/frame-request")
        assert accepted.status_code == observed.status_code == 200
        admission, readback = accepted.json(), observed.json()
        assert admission["request"]["members"][0]["status"] == "pending"
        assert readback["request"]["members"][0]["status"] == "completed"
        assert readback["reason"] is None
        assert set(readback["request"]) == {
            "id", "entrypoint", "request_contract_version", "client_request_id", "request_fingerprint",
            "request_root_kind", "request_root_id", "request_root_scope_id", "request_root_series_id",
            "owner_kind", "owner_id", "asset_type", "asset_id", "channel", "members",
        }
        assert set(readback["request"]["members"][0]) == {"variant_id", "attempt_id", "status", "reason", "output_ref"}
        fixture = {"body": raw, "admission": admission, "readback": readback}
        serialized = json.dumps(fixture, sort_keys=True)
        assert "fake-studio-image-key" not in serialized and "credential_ref" not in serialized
        print("STORYBOARD_FRAME_WIRE=" + serialized)


@pytest.mark.parametrize("state", ["processing", "needs_confirmation"])
@pytest.mark.parametrize("has_image", [True, False])
def test_independent_frame_copy_has_no_unrecoverable_execution_state(runtime, state, has_image):
    original = admit(runtime)
    frame = runtime.scripts["p"].frames[0]
    frame.status = GenerationStatus.PROCESSING if state == "processing" else state
    if not has_image:
        frame.image_url = frame.rendered_image_url = None
        frame.rendered_image_asset = ImageAsset()
    runtime._commit_storage("projects.json")
    result = runtime.copy_frame("p", "f")
    copied = next(value for value in result.frames if value.id != "f")
    assert copied.image_request_id is None
    assert copied.status == ("completed" if has_image else "pending")
    source = next(value for value in result.frames if value.id == "f")
    assert source.image_request_id == original.request.id and source.status == state


@pytest.mark.parametrize("state", [GenerationStatus.PROCESSING, "needs_confirmation"])
def test_legacy_frame_without_core_pointer_keeps_copy_status(runtime, state):
    runtime.scripts["p"].frames[0].status = state
    runtime._commit_storage("projects.json")
    result = runtime.copy_frame("p", "f")
    copied = next(value for value in result.frames if value.id != "f")
    assert copied.image_request_id is None and copied.status == state


def test_frame_copy_recognizes_retained_selected_variant_without_legacy_url(runtime):
    admit(runtime)
    frame = runtime.scripts["p"].frames[0]
    frame.image_url = frame.rendered_image_url = None
    runtime._commit_storage("projects.json")
    result = runtime.copy_frame("p", "f")
    copied = next(value for value in result.frames if value.id != "f")
    assert copied.image_request_id is None and copied.status == "completed"
    assert copied.rendered_image_asset.selected_id == "old"
