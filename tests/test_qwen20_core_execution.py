"""Opt-in Qwen T2I records do not reinterpret legacy Qwen or strict Wan history."""

from dataclasses import replace
from pathlib import Path

import pytest

from src.models import provider_media_runtime as runtime_module
from src.models.provider_execution import ProviderExecutionRecord
from src.models.provider_media_execution import execute_sync_artifact_to_private_file
from src.models.provider_media_intents import (
    build_accepted_sync_artifact_record,
    image_intent_from_sync_record,
    validate_sync_artifact_record_intent,
)
from src.models.provider_media_runtime import (
    bind_media_runtime_authority,
    resolve_media_runtime_authority,
)
from src.models.provider_recovery import (
    _fingerprint,
    _record_fingerprint_payload,
    record_with_changes,
)
from src.utils.generation_intent import normalize_generation_intent
from tests import test_wan27_core_execution as wan
from tests.frozen_image_fixtures import rgb_png_bytes

VERSION = "provider-qwen20-t2i-bound-http-v1"
MODELS = ["qwen-image-2.0", "qwen-image-2.0-pro"]
SIZES = ["576*1024", "768*1024", "1024*1024", "1024*768", "1024*576"]


def intent(model="qwen-image-2.0", **changes):
    return wan.intent(
        model,
        **{
            "model_id": f"qwen/{model}#image",
            "params": {
                "size": "576*1024",
                "watermark": False,
                "seed": 0,
                "prompt_extend": True,
                "negative_prompt": "不要文字",
            },
            **changes,
        },
    )


def record(model="qwen-image-2.0", strict=True, **changes):
    kwargs = dict(
        intent=intent(model),
        provider_model_id=model,
        media_kind="image",
        media=[],
        scope=wan.SCOPE,
        credential_ref="credential-example",
        attempt_id="attempt",
        output_ref="assets/studio/attempt.png",
    )
    if strict:
        kwargs["image_contract_version"] = VERSION
    kwargs.update(changes)
    return build_accepted_sync_artifact_record(**kwargs)


@pytest.mark.parametrize("model", MODELS)
def test_freezes_qwen_profile_and_roundtrips_without_filling_values(model, monkeypatch):
    value = record(model)
    assert value.intent == {
        **intent(model).to_dict(),
        "intent_contract_version": VERSION,
    }
    assert value.schema_version == 2 and value.execution_kind == "sync_artifact"
    assert value.fingerprint == _fingerprint(_record_fingerprint_payload(value))
    monkeypatch.setattr(
        runtime_module,
        "get_catalog_accessor",
        lambda: pytest.fail("restore consulted catalog"),
    )
    assert image_intent_from_sync_record(value).to_dict() == intent(model).to_dict()
    assert ProviderExecutionRecord.model_validate_json(value.model_dump_json()) == value


def test_existing_generic_qwen_and_wan_records_keep_their_shape():
    generic = record(
        strict=False, intent=intent(params={"size": "old-size", "seed": -1})
    )
    assert "intent_contract_version" not in generic.intent
    assert image_intent_from_sync_record(generic).params == {
        "size": "old-size",
        "seed": -1,
    }
    strict_wan = wan.record()
    assert strict_wan.intent["intent_contract_version"] == wan.VERSION
    assert validate_sync_artifact_record_intent(strict_wan) == "image"


@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("size", SIZES)
def test_current_catalog_preserves_five_studio_sizes_and_negative_prompt(model, size):
    value = normalize_generation_intent(
        {
            "model_id": model,
            "mode": "t2i",
            "prompt": "角色设定",
            "params": {
                "size": size,
                "negative_prompt": "文字",
                "prompt_extend": True,
                "watermark": False,
            },
        },
        allowed_modes={"t2i"},
        allowed_parameters={"size", "negative_prompt", "prompt_extend", "watermark"},
    )
    assert value.model_id == f"qwen/{model}#image" and value.params["size"] == size


@pytest.mark.parametrize(
    "field,value",
    [
        ("size", "1*1"),
        ("size", "2048*2049"),
        ("size", None),
        ("seed", None),
        ("seed", True),
        ("seed", -1),
        ("seed", 2147483648),
        ("negative_prompt", "字" * 501),
        ("negative_prompt", None),
        ("negative_prompt", False),
        ("watermark", 0),
        ("prompt_extend", 1),
        ("n", 1),
    ],
)
def test_bad_frozen_parameters_reject_even_after_rehash(field, value):
    raw = record(strict=False).model_dump(mode="json")
    raw["intent"]["params"][field] = value
    raw["intent"]["intent_contract_version"] = VERSION
    mutated = ProviderExecutionRecord.model_validate(raw)
    mutated = record_with_changes(
        mutated, fingerprint=_fingerprint(_record_fingerprint_payload(mutated))
    )
    with pytest.raises(ValueError):
        validate_sync_artifact_record_intent(mutated)


@pytest.mark.parametrize(
    "changes",
    [
        {"sources": {"params.unknown": "request"}},
        {"sources": {"model_id": "ambient"}},
        {"request_fields": ["mode", "mode"]},
        {"request_fields": ["params.unknown"]},
        {"preset_id": ""},
        {"preset_version": 3},
        {"preset_id": "bad\nidentity"},
    ],
)
def test_frozen_provenance_is_closed_even_with_recomputed_fingerprint(changes):
    raw = record(strict=False).model_dump(mode="json")
    raw["intent"].update(changes)
    raw["intent"]["intent_contract_version"] = VERSION
    value = ProviderExecutionRecord.model_validate(raw)
    value = record_with_changes(
        value, fingerprint=_fingerprint(_record_fingerprint_payload(value))
    )
    with pytest.raises(ValueError):
        validate_sync_artifact_record_intent(value)


@pytest.mark.parametrize("field", ["size", "watermark", "prompt_extend"])
def test_required_values_are_not_inferred(field):
    params = dict(intent().params)
    del params[field]
    with pytest.raises(ValueError):
        record(intent=intent(params=params))


@pytest.mark.parametrize(
    "changes",
    [
        {"model_id": "qwen/qwen-image-2.0-pro#image"},
        {"model_id": "wan/wan2.7-image#image"},
        {"mode": "i2i"},
        {"catalog_mode": "video"},
        {"prompt": ""},
        {"prompt": "bad\ud800"},
    ],
)
def test_frozen_identity_cannot_be_reinterpreted(changes):
    with pytest.raises(ValueError):
        record(intent=intent(**changes))


@pytest.mark.parametrize(
    "model,version",
    [
        ("wan2.7-image", VERSION),
        ("qwen-image-2.0", wan.VERSION),
        ("qwen-image-2.0", None),
    ],
)
def test_profile_cannot_relabel_another_provider(model, version):
    with pytest.raises(ValueError):
        record(model=model, image_contract_version=version)


def test_binding_uses_exact_one_snapshot_without_live_catalog(monkeypatch):
    state, environment, store = wan.binding_fixture(
        {"DASHSCOPE_WORKSPACE_ID": "workspace"}
    )
    monkeypatch.setattr(
        runtime_module,
        "get_catalog_accessor",
        lambda: pytest.fail("strict bind consulted catalog"),
    )
    with bind_media_runtime_authority(
        environment, store, image_intent=intent(), image_contract_version=VERSION
    ) as bound:
        assert (
            bound.provider_model_id == "qwen-image-2.0"
            and bound.scope.workspace_id == "workspace"
        )
    assert state.snapshots == 1 and len(state.calls) == 1


def test_resolve_keeps_original_authority_when_current_settings_change(monkeypatch):
    state, environment, store = wan.binding_fixture(
        {"DASHSCOPE_BASE_URL": "https://changed.invalid"}
    )
    monkeypatch.setattr(
        runtime_module,
        "get_catalog_accessor",
        lambda: pytest.fail("resolve consulted catalog"),
    )
    with resolve_media_runtime_authority(
        environment,
        store,
        media_kind="image",
        provider_model_id="qwen-image-2.0",
        scope=wan.SCOPE,
        credential_ref="credential-example",
        image_contract_version=VERSION,
    ) as authority:
        assert authority.scope == wan.SCOPE
    assert state.snapshots == 1 and state.calls[0][0] == wan.SCOPE


@pytest.mark.parametrize(
    "changes",
    [
        {"region": "us-east-1"},
        {"base_url": "https://evil.invalid"},
        {"gateway": "dashscope_workspace"},
    ],
)
def test_saved_invalid_scope_never_gains_authority(changes):
    state, environment, store = wan.binding_fixture()
    with pytest.raises(ValueError):
        record(scope=replace(wan.SCOPE, **changes))
    with pytest.raises(ValueError):
        with resolve_media_runtime_authority(
            environment,
            store,
            media_kind="image",
            provider_model_id="qwen-image-2.0",
            scope=replace(wan.SCOPE, **changes),
            credential_ref="credential-example",
            image_contract_version=VERSION,
        ):
            pytest.fail("invalid saved scope resolved")
    assert not state.calls


@pytest.mark.parametrize("strict", [False, True])
def test_marker_alone_selects_transport_and_real_core_proof(tmp_path, strict):
    calls = []

    class Model:
        def __init__(self, config, *, env):
            calls.append(("init", env))

        def generate(self, prompt, path, **kwargs):
            assert not strict
            calls.append(("legacy", kwargs))
            Path(path).write_bytes(rgb_png_bytes(width=16, height=16))

        def generate_with_bound_http(self, prompt, path, credential, **kwargs):
            assert (
                strict
                and credential == wan.authority(model="qwen-image-2.0").credential
            )
            calls.append(("bound", kwargs))
            Path(path).write_bytes(rgb_png_bytes(width=16, height=16))

    value = record_with_changes(record(strict=strict), phase="submitting")
    result = execute_sync_artifact_to_private_file(
        value,
        wan.authority(model="qwen-image-2.0"),
        output_root=tmp_path,
        scratch_dir=tmp_path / "scratch",
        max_bytes=1024 * 1024,
        image_model_factory=Model,
    )
    assert result.encoding == "image/png" and result.path.read_bytes().startswith(
        b"\x89PNG"
    )
    assert calls[1][0] == ("bound" if strict else "legacy")
    if strict:
        assert (
            calls[0] == ("init", {})
            and calls[1][1]["image_contract_version"] == VERSION
        )
        assert (
            calls[1][1]["prompt_extend"] is True
            and calls[1][1]["negative_prompt"] == "不要文字"
        )


def test_strict_transport_failure_cleans_without_legacy_fallback(tmp_path):
    paths = []

    class Model:
        def __init__(self, config, *, env):
            assert env == {}

        def generate(self, *args, **kwargs):
            pytest.fail("fallback")

        def generate_with_bound_http(self, prompt, path, credential, **kwargs):
            paths.append(Path(path))
            Path(path).write_bytes(b"partial")
            raise RuntimeError("uncertain")

    with pytest.raises(RuntimeError, match="uncertain"):
        execute_sync_artifact_to_private_file(
            record_with_changes(record(), phase="submitting"),
            wan.authority(model="qwen-image-2.0"),
            output_root=tmp_path,
            scratch_dir=tmp_path / "scratch",
            max_bytes=1024,
            image_model_factory=Model,
        )
    assert len(paths) == 1 and all(not path.exists() for path in paths)
