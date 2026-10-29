"""Bounded full-body T2I migration: real Studio owners, worker and legacy oracle.

Only provider image production is replaced. These REDs intentionally precede
production support for the independent character/full_body Core channel.
"""

from copy import deepcopy
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from src.apps.comic_gen import asset_image_execution as module
from src.apps.comic_gen.asset_image_models import AssetImageRequestRecord
from src.apps.comic_gen.models import AssetUnit, Character, ImageAsset, ImageVariant, Script, Series, VideoVariant
from src.utils.json_storage import StorageConflictError, StorageError
from tests import test_studio_core_image_admission as admission
from tests import test_studio_core_image_worker as worker
from tests.test_studio_core_image_models import REQUEST_ID, request_payload
from tests.test_studio_image_reparse_history import arrange_parser


runtime = admission.runtime
executions = worker.wan_executions
ENTRYPOINT = "studio.asset.full_body.v1"
MODELS = ("wan2.7-image", "wan2.7-image-pro", "wan/wan2.7-image#image", "wan/wan2.7-image-pro#image")
DEFAULT_PROMPT = (
    "Full body character design of Alice, concept art. red coat. Standing pose, "
    "neutral expression, no emotion, looking at viewer. Clean white background, "
    "isolated, no other objects, no scenery, simple background, high quality, masterpiece."
)


def full_body_command(**changes):
    return admission.command(generation_type="full_body", **changes)


def setup(item, route="local", *, uploaded_body=False):
    root = admission.setup(item, route)
    owner = admission.owner(item, route)
    target = owner.characters[0]
    body = ImageVariant(id="body-old", url="uploads/body.png", is_favorited=True,
                        is_uploaded_source=uploaded_body, upload_type="full_body" if uploaded_body else None)
    target.full_body_asset = ImageAsset(selected_id=body.id, variants=[body.model_copy(deep=True)])
    target.full_body = AssetUnit(selected_image_id=body.id, image_variants=[body],
                                 video_variants=[VideoVariant(id="motion", url="video/motion.mp4")],
                                 selected_video_id="motion", video_prompt="keep motion")
    target.full_body_image_url = body.url
    target.full_body_prompt = "previous base prompt"
    target.full_body_updated_at = 1
    target.reference_sheet = AssetUnit(selected_image_id="sheet-old", image_variants=[
        ImageVariant(id="sheet-old", url="uploads/sheet.png"),
    ])
    target.reference_sheet_request_id = REQUEST_ID
    target.image_url = "uploads/sheet.png"
    target.voice_id = "keep voice"
    item._commit_storage("projects.json", "series.json", "library_assets.json")
    return root, owner, target


def add_reference(target, kind):
    if kind == "base":
        target.base_character_id = "base-character"
    else:
        field = "three_view_asset" if kind == "three_view" else "headshot_asset"
        setattr(target, field, ImageAsset(variants=[ImageVariant(
            id="uploaded-reference", url=f"uploads/{kind}.png", is_uploaded_source=True,
        )]))


@pytest.mark.parametrize("model", MODELS[:2])
def test_full_body_history_accepts_its_own_exact_channel_tuple(model):
    payload = request_payload(model=model)
    payload.update(entrypoint=ENTRYPOINT, channel="full_body", base_prompt="保留角色设定")
    record = AssetImageRequestRecord.model_validate(payload)
    assert (record.entrypoint, record.asset_type, record.channel) == (ENTRYPOINT, "character", "full_body")
    assert record.model_dump()["base_prompt"] == "保留角色设定"
    assert "base_prompt" not in record.members[0].execution.intent
    for changes in ({"channel": "reference_sheet"}, {"asset_type": "scene"}, {"entrypoint": "studio.asset.reference_sheet.v1"}):
        with pytest.raises(ValueError):
            AssetImageRequestRecord.model_validate(payload | changes)
    for value in (None, "", "   "):
        with pytest.raises(ValueError):
            AssetImageRequestRecord.model_validate(payload | {"base_prompt": value})
    del payload["base_prompt"]
    with pytest.raises(ValueError):
        AssetImageRequestRecord.model_validate(payload)


@pytest.mark.parametrize("entrypoint,kind,channel", (
    ("studio.asset.reference_sheet.v1", "character", "reference_sheet"),
    ("studio.asset.scene_prop.v1", "scene", "image"),
    ("studio.asset.scene_prop.v1", "prop", "image"),
))
def test_older_receipt_json_shape_stays_exact_and_cannot_adopt_full_body_base_prompt(entrypoint, kind, channel):
    payload = request_payload()
    payload.update(entrypoint=entrypoint, asset_type=kind, channel=channel)
    restored = AssetImageRequestRecord.model_validate_json(json.dumps(payload))
    assert restored.model_dump(mode="json") == payload
    assert json.loads(restored.model_dump_json()) == payload
    assert "base_prompt" not in restored.model_dump()
    with pytest.raises(ValueError):
        AssetImageRequestRecord.model_validate(payload | {"base_prompt": "authored for another channel"})


def test_character_roundtrip_retains_independent_full_body_pointer():
    target = Character.model_validate({
        "id": "c", "name": "Alice", "description": "red coat",
        "reference_sheet_request_id": REQUEST_ID,
        "full_body_request_id": "22222222-2222-4222-8222-222222222222",
    })
    saved = target.model_dump(mode="json")
    assert saved.get("full_body_request_id") == "22222222-2222-4222-8222-222222222222"
    assert saved["reference_sheet_request_id"] == REQUEST_ID
    assert Character.model_validate(saved).model_dump(mode="json") == saved


@pytest.mark.parametrize("route", admission.ROUTES)
def test_admission_reserves_actual_owner_and_only_full_body_pointer(runtime, route):
    root, owner, target = setup(runtime, route, uploaded_body=True)
    sheet = target.reference_sheet.model_dump()
    result = runtime.asset_image_service.admit(*root, full_body_command(batch_size=2, seed=0))
    record = result.request
    assert (record.entrypoint, record.channel) == (ENTRYPOINT, "full_body")
    assert len(record.members) == 2
    assert getattr(target, "full_body_request_id", None) == record.id
    assert target.reference_sheet_request_id == REQUEST_ID and target.reference_sheet.model_dump() == sheet
    assert owner.asset_image_requests == [record]
    assert runtime.asset_generation_tasks[record.id]["_core_image_request_id"] == record.id
    assert record.members[0].execution.intent["params"]["seed"] == 0
    assert record.members[0].execution.media == []


@pytest.mark.parametrize("model", MODELS)
def test_existing_sync_entry_routes_only_supported_full_body_models_to_core(runtime, executions, model):
    _, owner, _ = setup(runtime, uploaded_body=True)
    runtime.scripts["p"].model_settings.t2i_model = "wan2.6-t2i"
    runtime._commit_storage("projects.json")
    runtime._asset_image_service = worker.service(runtime, executions[0])
    runtime._generate_asset_image = Mock(side_effect=AssertionError("full_body bypassed Core receipt"))
    result = runtime.generate_asset("p", "c", "character", generation_type="full_body", model_name=model, apply_style=False)
    assert result.id == "p" and len(owner.asset_image_requests) == 1
    assert owner.asset_image_requests[0].channel == "full_body"
    assert len(executions[1]) == 1
    runtime._generate_asset_image.assert_not_called()


@pytest.mark.parametrize("reference", ("base", "three_view", "headshot"))
def test_reference_bearing_full_body_stays_legacy_even_if_source_file_is_missing(runtime, reference):
    _, owner, target = setup(runtime)
    add_reference(target, reference)
    runtime._commit_storage("projects.json")
    legacy = Mock(return_value=runtime.read_committed_script("p"))
    runtime._generate_asset_image = legacy
    runtime.generate_asset("p", "c", "character", generation_type="full_body", apply_style=False)
    legacy.assert_called_once()
    assert owner.asset_image_requests == []


@pytest.mark.parametrize("reference", ("base", "three_view", "headshot"))
def test_direct_core_admission_rejects_reference_sources_before_binding_or_storage(runtime, monkeypatch, reference):
    root, _, target = setup(runtime)
    add_reference(target, reference)
    runtime._commit_storage("projects.json")
    before = deepcopy(runtime._committed)
    binder = Mock(side_effect=AssertionError("reference-bearing request reached T2I authority"))
    monkeypatch.setattr(module, "bind_media_runtime_authority", binder)
    with pytest.raises(ValueError, match="studio_image_reference_not_supported"):
        runtime.asset_image_service.admit(*root, full_body_command())
    binder.assert_not_called()
    assert runtime._committed == before and runtime.asset_generation_tasks == {}


@pytest.mark.parametrize("reference", (None, "full_body", "three_view", "headshot", "base"))
def test_real_legacy_generator_identifies_only_three_view_headshot_or_base_as_input(runtime, monkeypatch, reference):
    _, _, target = setup(runtime, uploaded_body=reference == "full_body")
    if reference in {"base", "three_view", "headshot"}:
        add_reference(target, reference)
        path = (runtime.data_dir / "assets/characters/base-character_fullbody.png" if reference == "base"
                else runtime.data_dir / f"uploads/{reference}.png")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(worker.PNG)
    calls = []
    def generate(prompt, output_path, **kwargs):
        calls.append((prompt, kwargs))
        Path(output_path).write_bytes(worker.PNG)
    monkeypatch.setattr(runtime.asset_generator, "_generate", generate)
    runtime.asset_generator.generate_character(target.model_copy(deep=True), generation_type="full_body", positive_prompt="", model_name="wan2.7-image")
    assert len(calls) == 1
    prompt, options = calls[0]
    if reference in {None, "full_body"}:
        assert options["ref_image_path"] is None
        assert options["model_name"] == "wan2.7-image" and prompt == DEFAULT_PROMPT
    else:
        assert options["ref_image_path"] == str(path)
        assert options["model_name"] == "wan2.6-image"
        assert prompt.startswith("STRICTLY MAINTAIN the SAME character appearance")
    assert options["size"] == "576*1024"


@pytest.mark.parametrize("params,expected_prompt,size", [
    ({"apply_style": False}, DEFAULT_PROMPT, "576*1024"),
    ({"apply_style": True, "style_preset": "Ink"}, DEFAULT_PROMPT + ", Ink style", "576*1024"),
    ({"apply_style": True, "prompt": "Authored pose", "style_prompt": "soft light"}, "Authored pose, soft light", "576*1024"),
    ({"apply_style": True, "prompt": "Authored pose, soft light", "style_prompt": "soft light", "aspect_ratio": "1:1"}, "Authored pose, soft light", "1024*1024"),
])
def test_frozen_intent_matches_real_legacy_full_body_prompt_style_and_size(runtime, monkeypatch, params, expected_prompt, size):
    root, _, target = setup(runtime)
    context = runtime._prepare_asset_image(runtime._read_asset_image_view(*root), "c", "character", {"generation_type": "full_body", **params})
    calls = []
    def generate(prompt, output_path, **kwargs):
        calls.append((prompt, kwargs))
        Path(output_path).write_bytes(worker.PNG)
    monkeypatch.setattr(runtime.asset_generator, "_generate", generate)
    runtime.asset_generator.generate_character(target.model_copy(deep=True), **context["options"])
    assert calls[0][0] == expected_prompt and calls[0][1]["size"] == size
    record = runtime.asset_image_service.admit(*root, full_body_command(**params)).request
    intent = record.members[0].execution.intent
    assert intent["prompt"] == expected_prompt
    assert intent["params"]["size"] == size and intent["mode"] == "t2i"
    assert record.members[0].execution.media == []


@pytest.mark.parametrize("batch_size,authored_prompt", ((1, None), (2, None), (1, "Authored pose")))
def test_worker_projects_both_full_body_representations_without_touching_sheet_or_motion(runtime, executions, batch_size, authored_prompt):
    root, _, target = setup(runtime)
    sheet = target.reference_sheet.model_dump(mode="json")
    motion = target.full_body.video_variants[0].model_dump()
    service = worker.service(runtime, executions[0])
    params = {"prompt": authored_prompt, "apply_style": True, "style_prompt": "soft light"} if authored_prompt else {}
    accepted = service.admit(*root, full_body_command(batch_size=batch_size, **params))
    result = service.run_original_request(accepted.request.id)
    assert result is not None
    assert result.reason is None and all(member.execution.phase == "completed" for member in result.request.members)
    ids = [member.variant_id for member in result.request.members]
    assert {v.id for v in target.full_body.image_variants} == {*ids, "body-old"}
    assert {v.id for v in target.full_body_asset.variants} == {*ids, "body-old"}
    selected = ids[0] if batch_size == 1 else "body-old"
    assert target.full_body.selected_image_id == target.full_body_asset.selected_id == selected
    expected_url = next(v.url for v in target.full_body_asset.variants if v.id == selected)
    assert target.full_body_image_url == expected_url and target.image_url == "uploads/sheet.png"
    assert target.full_body_prompt == (authored_prompt or DEFAULT_PROMPT) and target.full_body_updated_at > 1
    assert target.is_consistent is False
    assert target.reference_sheet.model_dump(mode="json") == sheet and target.reference_sheet_request_id == REQUEST_ID
    assert target.full_body.video_variants[0].model_dump() == motion
    assert target.full_body.selected_video_id == "motion" and target.full_body.video_prompt == "keep motion"
    assert target.voice_id == "keep voice"
    service.run_original_request(result.request.id)
    service.repair_request(*root, "request-1")
    assert len(executions[1]) == batch_size
    for member in result.request.members:
        worker.assert_png(runtime.data_dir / member.execution.output_ref)
    assert len(executions.posts) == len(executions.queries) == len(executions.downloads) == batch_size


def test_same_client_key_cannot_cross_channels(runtime):
    root, owner, _ = setup(runtime)
    service = runtime.asset_image_service
    service.admit(*root, admission.command())
    with pytest.raises(StorageConflictError, match="payload_conflict"):
        service.admit(*root, full_body_command())
    assert len(owner.asset_image_requests) == 1


@pytest.mark.parametrize("route", ("local", "series", "global"))
def test_full_body_authored_prompt_is_consistent_in_both_saved_views(runtime, executions, route):
    root, _, target = setup(runtime, route)
    target.full_body.image_prompt = "previous asset-unit prompt"
    runtime._commit_storage("projects.json", "series.json", "library_assets.json")
    service = worker.service(runtime, executions[0])
    accepted = service.admit(*root, full_body_command(prompt="New authored pose", apply_style=True, style_prompt="soft light"))
    result = service.run_original_request(accepted.request.id)
    assert all(member.execution.phase == "completed" for member in result.request.members)
    assert target.full_body.image_prompt == target.full_body_prompt == "New authored pose"
    with worker.restarted(runtime) as restored:
        saved = admission.owner(restored, route).characters[0]
        assert saved.full_body.image_prompt == saved.full_body_prompt == "New authored pose"
        assert saved.full_body.video_prompt == "keep motion"


def test_old_sheet_receipt_still_replays_without_new_preparation_or_authority(runtime, monkeypatch):
    root, owner, target = setup(runtime)
    service = runtime.asset_image_service
    original = service.admit(*root, admission.command())
    add_reference(target, "headshot")
    runtime.scripts["p"].model_settings.t2i_model = "not-current"
    runtime._commit_storage("projects.json")
    monkeypatch.setattr(module, "bind_media_runtime_authority", Mock(side_effect=AssertionError("replay rebound authority")))
    monkeypatch.setattr(runtime, "_prepare_asset_image", Mock(side_effect=AssertionError("replay prepared current inputs")))
    replay = service.admit(*root, admission.command())
    assert replay.replayed and replay.request == original.request
    assert len(owner.asset_image_requests) == 1


@pytest.mark.parametrize("change", ("description", "base", "three_view", "headshot", "selection"))
def test_late_full_body_result_cannot_overwrite_changed_inputs_or_new_reference(runtime, executions, change):
    root, _, target = setup(runtime)
    execute = executions[0]
    changed = {}
    def after_submit(*args, **kwargs):
        result = execute(*args, **kwargs)
        if change == "description":
            target.description = "new description"
        elif change == "selection":
            target.full_body_asset.selected_id = None
            target.full_body.selected_image_id = None
        else:
            add_reference(target, change)
        runtime._commit_storage("projects.json")
        changed.update(target.model_dump(mode="json"))
        return result
    service = worker.service(runtime, after_submit)
    accepted = service.admit(*root, full_body_command())
    result = service.run_original_request(accepted.request.id)
    assert result.reason is not None
    assert target.model_dump(mode="json") == changed
    replay = service.admit(*root, full_body_command())
    assert replay.replayed and replay.request.id == accepted.request.id and replay.reason is not None
    service.repair_request(*root, "request-1")
    assert target.model_dump(mode="json") == changed and len(executions[1]) == 1


@pytest.mark.parametrize("operation", (
    "promote-project", "promote-series", "fork-global", "import-series", "move", "reparse", "cached-reparse",
))
def test_copy_and_reparse_clear_full_body_authority_while_preserving_original_receipt(runtime, operation):
    route = ("standalone" if operation in {"promote-series", "import-series"}
             else "global" if operation == "fork-global" else "local")
    root, owner, target = setup(runtime, route)
    if operation == "move":
        runtime.scripts["p"].series_id = "s"
        runtime._commit_storage("projects.json")
    accepted = runtime.asset_image_service.admit(*root, full_body_command())
    history = deepcopy(owner.asset_image_requests)
    if operation.startswith("promote"):
        copied = runtime.promote_asset_to_library(root[0], root[1], "character", "c")
        assert copied.id != target.id and runtime.library_store.asset_image_requests == []
    elif operation == "fork-global":
        copied = runtime.fork_library_asset_to_project("p", "character", "c")
        assert copied.id != target.id and runtime.scripts["p"].asset_image_requests == []
    elif operation == "import-series":
        runtime.series_store["target"] = Series(id="target", title="Target", created_at=1, updated_at=1)
        runtime._commit_storage("series.json")
        copied_series, imported, skipped = runtime.import_assets_from_series("target", "s", ["c"])
        assert imported == ["c"] and skipped == [] and copied_series.asset_image_requests == []
        copied = copied_series.characters[0]
    elif operation == "move":
        runtime.reconcile_project_assets("p", {"characters": [{"local_id": "c", "action": "create_new_in_series"}]})
        copied = runtime.series_store["s"].characters[0]
        assert runtime.scripts["p"].characters == [] and runtime.series_store["s"].asset_image_requests == []
    else:
        reparse_revision = runtime.read_committed_script("p").revision
        parsed = Script(id="parsed", title="Replacement", original_text="Replacement story", created_at=2, updated_at=2,
                        characters=[target.model_copy(deep=True)])
        arrange_parser(runtime, parsed, False)
        if operation == "cached-reparse":
            runtime.extract_preview("p", "Replacement story")
            runtime.script_processor.parse_novel = Mock(side_effect=AssertionError("cached reparse called parser"))
        copied = runtime.reparse_project("p", "Replacement story", expected_revision=reparse_revision).characters[0]
        assert getattr(parsed.characters[0], "full_body_request_id", None) == accepted.request.id
    assert getattr(copied, "full_body_request_id", None) is None
    assert copied.reference_sheet_request_id is None and copied.status == "needs_confirmation"
    assert copied.full_body_asset.model_dump() == target.full_body_asset.model_dump()
    assert worker.disk_request(runtime, route) == history[0].model_dump(mode="json")
    assert list(runtime.asset_generation_tasks) == [accepted.request.id]
    assert runtime.asset_image_service.read_request(*root, "request-1").request.id == accepted.request.id


def test_restart_repairs_only_proven_full_body_projection_without_provider_execution(runtime, executions, monkeypatch):
    root, _, target = setup(runtime)
    target.full_body.image_prompt = "previous asset-unit prompt"
    runtime._commit_storage("projects.json")
    service = worker.service(runtime, executions[0])
    accepted = service.admit(*root, full_body_command(prompt="Authored pose", apply_style=True, style_prompt="soft light"))
    commit = runtime._storage.commit
    interrupted_completion = False
    def interrupted(payload):
        nonlocal interrupted_completion
        rows = payload.get("projects.json", {}).get("p", {}).get("asset_image_requests", [])
        if rows and rows[0]["members"][0]["execution"]["phase"] == "completed":
            interrupted_completion = True
            raise StorageError("full body completed projection interrupted")
        return commit(payload)
    monkeypatch.setattr(runtime._storage, "commit", interrupted)
    with pytest.raises(StorageError):
        service.run_original_request(accepted.request.id)
    assert interrupted_completion and len(executions[1]) == 1
    assert len(executions.posts) == len(executions.queries) == len(executions.downloads) == 1
    saved_before_restart = worker.disk_request(runtime, "local")["members"][0]["execution"]
    assert saved_before_restart["phase"] == "observed" and saved_before_restart["prepared_artifact"] is not None
    with worker.restarted(runtime) as restored:
        forbidden = Mock(side_effect=AssertionError("local repair accessed provider"))
        repaired_service = module.StudioAssetImageService(restored, executor=forbidden, authority_resolver=forbidden)
        repaired = repaired_service.repair_request(*root, "request-1")
        saved = restored.scripts["p"].characters[0]
        assert saved.full_body_asset is not None and saved.full_body is not None
        assert repaired.request.members[0].execution.phase == "completed"
        # Restart repair adds proven history only, preserving user selection and authored fields.
        assert saved.full_body_asset.selected_id == saved.full_body.selected_image_id == "body-old"
        assert saved.full_body.image_prompt == "previous asset-unit prompt"
        assert saved.full_body_prompt == "previous base prompt"
        restored_id = repaired.request.members[0].variant_id
        assert any(v.id == restored_id for v in saved.full_body_asset.variants)
        assert any(v.id == restored_id for v in saved.full_body.image_variants)
        assert repaired.request.members[0].execution.intent["prompt"] == "Authored pose, soft light"
        assert saved.reference_sheet.model_dump() == target.reference_sheet.model_dump()
        forbidden.assert_not_called()


def test_new_core_history_keeps_selected_and_completed_variants_beyond_legacy_limit(runtime, executions):
    root, _, target = setup(runtime)
    target.full_body_asset.variants[0].is_favorited = False
    target.full_body.image_variants[0].is_favorited = False
    for index in range(12):
        variant = ImageVariant(id=f"retained-{index}", url=f"uploads/{index}.png", created_at=index + 1)
        target.full_body_asset.variants.append(variant.model_copy(deep=True))
        target.full_body.image_variants.append(variant)
    runtime._commit_storage("projects.json")
    service = worker.service(runtime, executions[0])
    accepted = service.admit(*root, full_body_command(batch_size=2))
    result = service.run_original_request(accepted.request.id)
    expected = {"body-old", *(f"retained-{index}" for index in range(12)), *(m.variant_id for m in result.request.members)}
    assert {v.id for v in target.full_body_asset.variants} == expected
    assert {v.id for v in target.full_body.image_variants} == expected
    assert target.full_body_asset.selected_id == target.full_body.selected_image_id == "body-old"
    assert target.full_body_image_url == "uploads/body.png"


@pytest.mark.parametrize("mirror_failure", (False, True))
def test_full_body_optional_mirror_follows_local_completion_and_never_runs_on_repair(runtime, executions, monkeypatch, mirror_failure):
    root, _, target = setup(runtime)
    service = worker.service(runtime, executions[0])
    accepted = service.admit(*root, full_body_command(batch_size=2))
    calls = []
    def upload(payload, **options):
        saved = service.read_request(*root, "request-1")
        completed = [m for m in saved.request.members if m.execution.phase == "completed"]
        assert len(completed) == len(calls) + 1
        calls.append((payload, options))
        if mirror_failure:
            raise RuntimeError("optional mirror unavailable")
        return "optional-cloud-copy.png"
    monkeypatch.setattr(runtime, "oss_uploader", lambda: Mock(upload_bytes=upload))
    result = service.run_original_request(accepted.request.id)
    assert len(calls) == len(executions[1]) == 2
    assert all(m.execution.phase == "completed" for m in result.request.members)
    assert target.full_body_image_url == "uploads/body.png"
    for (payload, options), member in zip(calls, result.request.members, strict=True):
        assert payload == (runtime.data_dir / member.execution.output_ref).read_bytes()
        worker.assert_png(runtime.data_dir / member.execution.output_ref)
        assert options["sub_path"] == "assets/characters"
        assert options["custom_filename"] == f"{member.execution.attempt_id}.png"
    monkeypatch.setattr(runtime, "oss_uploader", Mock(side_effect=AssertionError("repair acquired optional mirror")))
    service.repair_request(*root, "request-1")
    assert service.admit(*root, full_body_command(batch_size=2)).replayed


def test_sync_full_body_entry_does_not_report_success_when_original_worker_is_uncertain(runtime, executions):
    import requests
    from src.apps.comic_gen.pipeline import ProjectGenerationError
    setup(runtime)
    def lost_ack(request):
        if request.method == "POST":
            raise requests.ConnectionError("provider acknowledgement lost")
        raise AssertionError("unknown submit attempted a query or download")
    executions.before_http = lost_ack
    runtime._asset_image_service = worker.service(runtime, executions[0])
    runtime._generate_asset_image = Mock(side_effect=AssertionError("uncertain request fell back"))
    with pytest.raises(ProjectGenerationError, match="not confirmed"):
        runtime.generate_asset("p", "c", "character", generation_type="full_body", apply_style=False)
    assert len(executions.posts) == 1 and not executions.queries and not executions.downloads
    assert len(runtime.scripts["p"].asset_image_requests) == 1
    member = runtime.scripts["p"].asset_image_requests[0].members[0].execution
    assert member.schema_version == 3 and member.phase == "submitting" and member.handle is None
    runtime._generate_asset_image.assert_not_called()


@pytest.mark.parametrize("older_package", (False, True))
def test_full_body_package_roundtrip_keeps_creative_content_without_authority(runtime, tmp_path, older_package):
    import io
    import zipfile
    from src.apps.comic_gen.models import GlobalAssetLibrary
    from src.apps.comic_gen.project_package import build_package, prepare_import
    root, _, target = setup(runtime)
    accepted = runtime.asset_image_service.admit(*root, full_body_command())
    for path in ("uploads/body.png", "uploads/sheet.png", "video/motion.mp4"):
        output = runtime.data_dir / path
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(path.encode())
    source = runtime.read_committed_script("p")
    assets = GlobalAssetLibrary(characters=[target.model_copy(deep=True)])
    before = source.model_dump(), assets.model_dump()
    artifact = build_package(source, resolved_assets=assets, data_dir=runtime.data_dir,
                             source_storage_id="source", writer_lock=runtime._writer_lock)
    with zipfile.ZipFile(io.BytesIO(artifact.data)) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    payload = json.loads(entries["project.json"])
    for container in (payload["script"], payload["resolved_assets"]):
        copied = container["characters"][0]
        assert copied.get("full_body_request_id") is None
        assert copied["status"] == "needs_confirmation"
        if older_package:
            copied["full_body_request_id"] = accepted.request.id
            copied["status"] = "processing"
    if older_package:
        entries["project.json"] = json.dumps(payload).encode()
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, value in entries.items():
                archive.writestr(name, value)
        data = buffer.getvalue()
    else:
        data = artifact.data
    destination = tmp_path / "imported"
    imported = prepare_import(data, data_dir=destination).script
    copied = imported.characters[0]
    assert copied.id != target.id and copied.full_body_request_id is None
    assert copied.reference_sheet_request_id is None and copied.status == "needs_confirmation"
    assert imported.asset_image_requests == [] and imported.asset_image_request_scope_id is None
    assert copied.full_body_asset.variants[0].is_favorited
    assert (destination / copied.full_body_image_url).read_bytes() == b"uploads/body.png"
    assert (destination / copied.full_body.video_variants[0].url).read_bytes() == b"video/motion.mp4"
    assert (source.model_dump(), assets.model_dump()) == before


@pytest.mark.parametrize("route", admission.ROUTES)
def test_real_http_full_body_receipt_executes_once_and_recovers_by_original_channel(runtime, executions, monkeypatch, route):
    from fastapi.testclient import TestClient
    from tests.test_studio_core_image_api import real_app, enable_fake_key
    root, owner, target = setup(runtime, route, uploaded_body=True)
    service = worker.service(runtime, executions[0])
    runtime._asset_image_service = service
    path = f"/{'projects' if root[0] == 'project' else 'series'}/{root[1]}/asset_image_requests"
    raw = full_body_command(batch_size=2).model_dump(exclude_unset=True)
    app = real_app(runtime)
    with TestClient(app) as client:
        enable_fake_key(app)
        first = client.post(path, json=raw)
        assert first.status_code == 200, first.text
        initial = first.json()["request"]
        assert (initial["entrypoint"], initial["asset_type"], initial["channel"]) == (ENTRYPOINT, "character", "full_body")
        assert "base_prompt" not in initial
        assert len(executions[1]) == 2
        assert target.full_body_request_id == owner.asset_image_requests[0].id == initial["id"]
        recovered = client.get(path + "/request-1")
        assert recovered.status_code == 200, recovered.text
        assert all(m["status"] == "completed" for m in recovered.json()["request"]["members"])
        before = {name: (runtime.data_dir / name).read_bytes() for name in ("projects.json", "series.json", "library_assets.json")}
        app.state.runtime_environment.update({}, remove_keys=("DASHSCOPE_API_KEY",), persist=lambda *_: None)
        monkeypatch.setattr(runtime, "_prepare_asset_image", Mock(side_effect=AssertionError("replay prepared new inputs")))
        monkeypatch.setattr(module, "bind_media_runtime_authority", Mock(side_effect=AssertionError("replay rebound authority")))
        replay = client.post(path, json=raw)
        assert replay.status_code == 200 and replay.json()["replayed"] is True
        assert replay.json()["request"] == recovered.json()["request"]
        conflict = client.post(path, json=raw | {"generation_type": "reference_sheet"})
        assert conflict.status_code == 409, conflict.text
        assert len(executions[1]) == 2
        assert before == {name: (runtime.data_dir / name).read_bytes() for name in before}


def test_full_body_partial_batch_cancels_unstarted_members_without_claiming_completion(runtime, executions):
    from contextlib import contextmanager
    root, _, target = setup(runtime)
    service = worker.service(runtime, executions[0])
    resolver = service._authority_resolver
    seen = []
    @contextmanager
    def resolve(record):
        seen.append(record.attempt_id)
        if len(seen) == 2:
            raise ValueError("second authority unavailable before submission")
        with resolver(record) as authority:
            yield authority
    service._authority_resolver = resolve
    accepted = service.admit(*root, full_body_command(batch_size=3))
    result = service.run_original_request(accepted.request.id)
    assert [m.execution.phase for m in result.request.members] == ["completed", "cancelled", "cancelled"]
    assert service.request_state(result.request)["status"] == "cancelled"
    assert target.status == "needs_confirmation" and target.full_body_asset.selected_id == "body-old"
    assert len(executions[1]) == len(executions.posts) == len(executions.queries) == len(executions.downloads) == 1
    service.repair_request(*root, "request-1")
    assert service.admit(*root, full_body_command(batch_size=3)).replayed
    assert len(executions[1]) == 1


def test_blank_full_body_base_prompt_with_style_is_rejected_before_authority_binding(runtime, monkeypatch):
    root, owner, _ = setup(runtime)
    blocked = Mock(side_effect=AssertionError("invalid frozen prompt acquired authority"))
    monkeypatch.setattr(module, "bind_media_runtime_authority", blocked)
    monkeypatch.setattr(runtime, "_commit_storage", Mock(side_effect=AssertionError("invalid prompt persisted")))
    with pytest.raises(ValueError, match="base_prompt"):
        runtime.asset_image_service.admit(*root, full_body_command(prompt="   ", apply_style=True, style_prompt="cinematic"))
    assert owner.asset_image_requests == [] and runtime.asset_generation_tasks == {}
    blocked.assert_not_called()
