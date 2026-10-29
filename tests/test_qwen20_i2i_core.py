"""Single-reference Qwen Edit keeps the existing synchronous Core boundaries."""

from dataclasses import asdict
import base64
import hashlib
import json
from types import SimpleNamespace

import pytest
import requests

from src.models import provider_media_execution as execution
from src.models import provider_media_intents as intents, provider_recovery as recovery, provider_tasks as tasks
from src.models.provider_binding import ProviderCredentialStore
from src.models.provider_execution import ProviderExecutionRecord
from src.models.provider_media_runtime import bind_media_runtime_authority, resolve_media_runtime_authority
from src.models.qwen20_image_contract import QWEN20_IMAGE_CONTRACT_VERSION
from src.utils.json_storage import atomic_write_json
from src.utils.media_versions import freeze_media
from src.utils.runtime_environment import RuntimeEnvironment
from tests import test_provider_async_image_operations as operations
from tests import test_qwen20_bound_image as wire
from tests import test_qwen20_core_execution as qwen
from tests.frozen_image_fixtures import rgb_png_bytes

VERSION = "provider-qwen20-i2i-bound-http-v1"
MAX_REFERENCE = 10 * 1024 * 1024
owned = operations.owned
transport = operations.transport


def context(owned, *, model="qwen-image-2.0", original=None, t2i=False):
    source = owned.root / "uploads" / "reference.png"
    source.parent.mkdir(exist_ok=True)
    original = original if original is not None else rgb_png_bytes("accepted Qwen reference", width=256, height=256)
    source.write_bytes(original)
    version = freeze_media("uploads/reference.png", output_root=owned.root, writer_lock=owned.lease,
                           role="reference_image", ordinal=0, max_bytes=max(len(original), operations.LIMIT))
    frozen = qwen.intent(model, mode="t2i" if t2i else "i2i", params={
        "size": "1280*1280", "negative_prompt": "不要文字或多余肢体",
        "prompt_extend": False, "watermark": False, "seed": 0,
    })
    contract = QWEN20_IMAGE_CONTRACT_VERSION if t2i else VERSION
    environment = RuntimeEnvironment({"DASHSCOPE_API_KEY": operations.SYNTHETIC_IMAGE_CREDENTIAL,
                                      "DASHSCOPE_WORKSPACE_ID": "original-workspace"})
    store = ProviderCredentialStore(owned.root, writer_lock=owned.lease, runtime_lock=owned.lock)
    with bind_media_runtime_authority(environment, store, image_intent=frozen, image_contract_version=contract) as authority:
        accepted = intents.build_accepted_sync_artifact_record(
            intent=frozen, provider_model_id=model, media_kind="image", media=[] if t2i else [asdict(version)],
            scope=authority.scope, credential_ref=authority.credential_ref, attempt_id="qwen-edit",
            output_ref="assets/studio/qwen-edit.png", image_contract_version=contract,
        )
    record = recovery.record_with_changes(accepted, phase="submitting")
    with owned.guard():
        atomic_write_json(owned.root / "execution.json", record.model_dump(mode="json"))
    return SimpleNamespace(accepted=accepted, record=record, authority=authority, source=source,
                           version=version, original=original, environment=environment, intent=frozen)


@pytest.mark.parametrize("model", ["qwen-image-2.0", "qwen-image-2.0-pro"])
def test_qwen_edit_uses_frozen_reference_and_original_sync_parameters(model, owned, tmp_path, transport):
    ctx = context(owned, model=model)
    ctx.source.write_bytes(rgb_png_bytes("author changed source"))
    reloaded = ProviderExecutionRecord.model_validate_json((owned.root / "execution.json").read_text())
    assert reloaded == ctx.record and reloaded.schema_version == 2 and reloaded.handle is None
    store = ProviderCredentialStore(owned.root, writer_lock=owned.lease, runtime_lock=owned.lock)
    transport["queue"].extend([wire.wan.response(wire.completed()), wire.wan.response(rgb_png_bytes("Qwen result"))])
    with resolve_media_runtime_authority(
        ctx.environment, store, media_kind="image", provider_model_id=model,
        scope=ctx.authority.scope, credential_ref=ctx.authority.credential_ref, image_contract_version=VERSION,
    ) as authority:
        artifact = execution.execute_sync_artifact_to_private_file(
            reloaded, authority, output_root=owned.root, scratch_dir=tmp_path / "execute", max_bytes=operations.LIMIT,
        )
    submit, download = [call for call, _ in transport["calls"]]
    assert (submit.method, submit.url) == (
        "POST", "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation",
    )
    assert json.loads(submit.body) == {
        "model": model,
        "input": {"messages": [{"role": "user", "content": [
            {"image": "data:image/png;base64," + base64.b64encode(ctx.original).decode("ascii")},
            {"text": ctx.intent.prompt},
        ]}]},
        "parameters": {"n": 1, **ctx.intent.params},
    }
    assert submit.headers["X-DashScope-WorkSpace"] == "original-workspace"
    assert "X-DashScope-Async" not in submit.headers
    assert download.method == "GET" and download.url == wire.wan.RESULT_URL
    assert all(key not in download.headers for key in ("Authorization", "Cookie", "X-DashScope-WorkSpace"))
    assert artifact.encoding == "image/png" and artifact.sha256 == hashlib.sha256(artifact.path.read_bytes()).hexdigest()
    assert reloaded.output_ref is not None
    proof = intents.sync_artifact_proof_from_record(
        reloaded, relative_path=reloaded.output_ref, sha256=artifact.sha256, size_bytes=artifact.size_bytes,
    )
    assert proof["schema_version"] == 2 and "handle" not in proof
    assert reloaded.media[0]["sha256"] == hashlib.sha256(ctx.original).hexdigest()
    assert reloaded.intent["intent_contract_version"] == VERSION and reloaded == ctx.record
    assert not transport["queue"] and all(not options["proxies"] for _, options in transport["calls"])
    assert not list((tmp_path / "execute").glob("*ref*"))


def test_existing_qwen_t2i_keeps_its_text_only_wire_and_record(owned, tmp_path, transport):
    ctx = context(owned, t2i=True)
    transport["queue"].extend([wire.wan.response(wire.completed()), wire.wan.response(rgb_png_bytes("T2I result"))])
    execution.execute_sync_artifact_to_private_file(
        ctx.record, ctx.authority, output_root=owned.root, scratch_dir=tmp_path / "t2i", max_bytes=operations.LIMIT,
    )
    assert json.loads(transport["calls"][0][0].body) == {
        "model": "qwen-image-2.0", "input": {"messages": [{"role": "user", "content": [{"text": ctx.intent.prompt}]}]},
        "parameters": {"n": 1, **ctx.intent.params},
    }
    assert ctx.record.media == [] and ctx.record.intent["intent_contract_version"] == QWEN20_IMAGE_CONTRACT_VERSION
    assert [call.method for call, _ in transport["calls"]] == ["POST", "GET"]
    assert ProviderExecutionRecord.model_validate_json((owned.root / "execution.json").read_text()) == ctx.record


def test_qwen_edit_reference_over_ten_mb_stops_before_post(owned, tmp_path, transport):
    ctx = context(owned, original=rgb_png_bytes("oversize") + bytes(MAX_REFERENCE))
    with pytest.raises(ValueError, match="exceeds max_bytes"):
        execution.execute_sync_artifact_to_private_file(
            ctx.record, ctx.authority, output_root=owned.root, scratch_dir=tmp_path / "oversize",
            max_bytes=2 * MAX_REFERENCE,
        )
    assert not transport["calls"] and not list((tmp_path / "oversize").iterdir())


def test_qwen_edit_requires_one_reference_and_cannot_become_async(owned, transport):
    ctx = context(owned)
    values = dict(intent=ctx.intent, provider_model_id=ctx.authority.provider_model_id,
                  scope=ctx.authority.scope, credential_ref=ctx.authority.credential_ref,
                  attempt_id="invalid-shape", output_ref="assets/studio/invalid-shape.png", image_contract_version=VERSION)
    for media in ([], [asdict(ctx.version), {**asdict(ctx.version), "ordinal": 1}]):
        with pytest.raises(ValueError, match="media"):
            intents.build_accepted_sync_artifact_record(media_kind="image", media=media, **values)
    with pytest.raises(ValueError, match="unsupported"):
        intents.build_accepted_async_image_record(media=[asdict(ctx.version)], **values)
    with pytest.raises(ValueError):
        qwen.record(intent=ctx.intent, media=[asdict(ctx.version)])
    assert not transport["calls"]


def test_qwen_sync_unknown_response_keeps_record_without_hidden_retry(owned, tmp_path, transport):
    ctx = context(owned)
    transport["queue"].append(requests.ReadTimeout("synthetic lost synchronous response"))
    with pytest.raises(tasks.ProviderSubmitError):
        execution.execute_sync_artifact_to_private_file(
            ctx.record, ctx.authority, output_root=owned.root, scratch_dir=tmp_path / "unknown", max_bytes=operations.LIMIT,
        )
    retained = ProviderExecutionRecord.model_validate_json((owned.root / "execution.json").read_text())
    assert retained == ctx.record and retained.phase == "submitting"
    assert retained.handle is None and retained.prepared_artifact is None and retained.artifact is None
    assert [call.method for call, _ in transport["calls"]] == ["POST"]
    assert not list((tmp_path / "unknown").iterdir()) and not transport["queue"]
