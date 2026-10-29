"""Opt-in strict Wan2.7 sync receipts preserve legacy identities and execution."""

import asyncio
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from src.models import provider_media_execution as execution_module
from src.models import provider_media_runtime as runtime_module
from src.models.provider_execution import ProviderExecutionRecord
from src.models.provider_binding import ProviderCredentialStore
from src.utils.runtime_environment import RuntimeEnvironment
from src.models.provider_media_execution import execute_sync_artifact_to_private_file
from src.models.provider_media_intents import (
    build_accepted_sync_artifact_record,
    image_intent_from_sync_record,
    sync_artifact_proof_from_record,
    validate_sync_artifact_record_intent,
)
from src.models.provider_media_runtime import (
    MediaRuntimeAuthority,
    bind_media_runtime_authority,
    resolve_media_runtime_authority,
)
from src.models.provider_recovery import (
    _fingerprint,
    _record_fingerprint_payload,
    record_with_changes,
)
from src.models.provider_tasks import ProviderScope, ResolvedCredential
from src.utils.generation_intent import GenerationIntent
from tests.frozen_image_fixtures import rgb_png_bytes

VERSION = "provider-wan27-t2i-bound-http-v1"
SECRET = "synthetic-example-core-image-secret"
SCOPE = ProviderScope(
    "dashscope",
    "dashscope",
    "dashscope",
    "https://dashscope.aliyuncs.com",
    "cn-beijing",
)


def intent(model="wan2.7-image-pro", **changes):
    values: dict[str, Any] = dict(
        model_id=f"wan/{model}#image",
        catalog_mode="image",
        mode="t2i",
        prompt="A glass fox",
        params={"size": "1024*1024", "watermark": False, "seed": 0},
        sources={},
        request_fields=(),
    )
    values.update(changes)
    return GenerationIntent(**values)


def record(
    *,
    strict=True,
    frozen=None,
    model="wan2.7-image-pro",
    scope=SCOPE,
    output="assets/studio/attempt.png",
    **kwargs,
):
    if strict:
        kwargs.setdefault("image_contract_version", VERSION)
    return build_accepted_sync_artifact_record(
        intent=frozen or intent(model),
        provider_model_id=model,
        media_kind="image",
        media=[],
        scope=scope,
        credential_ref="credential-example",
        attempt_id="attempt",
        output_ref=output,
        **kwargs,
    )


def retag(value, tag, *, rehash=True):
    data = value.model_dump(mode="json")
    data["intent"]["intent_contract_version"] = tag
    result = ProviderExecutionRecord.model_validate(data)
    if rehash:
        result = record_with_changes(
            result, fingerprint=_fingerprint(_record_fingerprint_payload(result))
        )
    return result


def authority(scope=SCOPE, model="wan2.7-image-pro"):
    credential = ResolvedCredential(scope, "credential-example", SECRET)
    return MediaRuntimeAuthority(
        "image",
        model,
        scope,
        credential.credential_ref,
        "DASHSCOPE_API_KEY",
        credential,
    )


@pytest.mark.parametrize("model", ["wan2.7-image", "wan2.7-image-pro"])
def test_builder_freezes_explicit_contract_and_false_zero_without_changing_intent(
    model,
):
    frozen = intent(model)
    value = record(model=model, frozen=frozen)
    assert value.intent == {**frozen.to_dict(), "intent_contract_version": VERSION}
    assert value.fingerprint == _fingerprint(_record_fingerprint_payload(value))
    assert image_intent_from_sync_record(value).to_dict() == frozen.to_dict()
    assert ProviderExecutionRecord.model_validate_json(value.model_dump_json()) == value


def test_untagged_legacy_fingerprint_and_unsupported_frozen_values_survive():
    frozen = intent(
        params={
            "size": "legacy-size",
            "negative_prompt": "keep",
            "prompt_extend": False,
            "seed": -1,
        }
    )
    value = record(strict=False, frozen=frozen)
    assert (
        value.intent == frozen.to_dict()
        and "intent_contract_version" not in value.intent
    )
    expected = dict(
        attempt_id="attempt",
        provider_model_id="wan2.7-image-pro",
        intent=frozen.to_dict(),
        media=[],
        scope=SCOPE.to_dict(),
        credential_ref="credential-example",
        execution_kind="sync_artifact",
        media_kind="image",
        output_ref="assets/studio/attempt.png",
    )
    assert value.fingerprint == _fingerprint(expected)
    assert image_intent_from_sync_record(value).params == frozen.params


@pytest.mark.parametrize("tag", [None, "", "unknown", 1, False])
def test_unknown_or_null_marker_rejects_even_with_valid_fingerprint(tag):
    with pytest.raises(ValueError):
        validate_sync_artifact_record_intent(retag(record(strict=False), tag))
    with pytest.raises(ValueError):
        record(image_contract_version=tag)


def test_marker_tamper_without_rehash_rejects():
    with pytest.raises(ValueError, match="fingerprint"):
        validate_sync_artifact_record_intent(
            retag(record(strict=False), VERSION, rehash=False)
        )


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"size": "1024*1024"},
        {"watermark": False},
        {"size": "1024*1024", "watermark": False, "negative_prompt": ""},
        {"size": "1024*1024", "watermark": False, "negative_prompt": "bad"},
        {"size": "1024*1024", "watermark": False, "prompt_extend": False},
        {"size": "1024*1024", "watermark": False, "seed": -1},
        {"size": "1024*1024", "watermark": False, "seed": True},
        {"size": "1024*1024", "watermark": False, "seed": None},
        {"size": "1024*1024", "watermark": False, "seed": 2147483648},
        {"size": "1024*1024", "watermark": 0},
        {"size": "128*128", "watermark": False},
        {"size": "1024*1024", "watermark": False, "n": 1},
    ],
)
def test_strict_params_fail_before_binding(params):
    with pytest.raises(ValueError):
        record(frozen=intent(params=params))
    state, env, store = binding_fixture()
    with pytest.raises(ValueError):
        with bind_media_runtime_authority(
            env,
            store,
            image_intent=intent(params=params),
            image_contract_version=VERSION,
        ):
            pytest.fail("invalid intent bound")
    assert state.calls == []


@pytest.mark.parametrize(
    "changes",
    [
        {"mode": "i2i"},
        {"catalog_mode": "video"},
        {"model_id": "wan/wan2.7-image#image"},
        {"prompt": ""},
        {"prompt": "x" * 5001},
    ],
)
def test_strict_identity_and_prompt_are_not_reinterpreted(changes):
    with pytest.raises(ValueError):
        record(frozen=intent(**changes))


def test_optional_seed_remains_absent_and_load_never_adds_defaults():
    frozen = intent(params={"size": "768*1024", "watermark": False})
    value = record(frozen=frozen)
    assert image_intent_from_sync_record(value).params == frozen.params
    data = value.model_dump(mode="json")
    del data["intent"]["params"]["watermark"]
    bad = ProviderExecutionRecord.model_validate(data)
    bad = record_with_changes(
        bad, fingerprint=_fingerprint(_record_fingerprint_payload(bad))
    )
    with pytest.raises(ValueError):
        image_intent_from_sync_record(bad)


def binding_fixture(values=None):
    state = SimpleNamespace(calls=[], snapshots=0)

    def snapshot():
        state.snapshots += 1
        return {"DASHSCOPE_API_KEY": SECRET, **(values or {})}

    def bind(scope, secret, **kwargs):
        state.calls.append((scope, secret, kwargs))
        return ResolvedCredential(scope, "credential-example", secret)

    def resolve(scope, ref, secret, **kwargs):
        assert ref == "credential-example"
        return bind(scope, secret, **kwargs)

    return (
        state,
        cast(RuntimeEnvironment, SimpleNamespace(snapshot=snapshot)),
        cast(
            ProviderCredentialStore, SimpleNamespace(bind=bind, resolve_binding=resolve)
        ),
    )


@pytest.mark.parametrize(
    "values,region,workspace",
    [
        ({}, "cn-beijing", None),
        (
            {"DASHSCOPE_BASE_URL": "https://dashscope-intl.aliyuncs.com/"},
            "ap-southeast-1",
            None,
        ),
        (
            {"DASHSCOPE_WORKSPACE_ID": "workspace-example"},
            "cn-beijing",
            "workspace-example",
        ),
        (
            {
                "DASHSCOPE_WORKSPACE": "workspace-example",
                "DASHSCOPE_WORKSPACE_ID": "workspace-example",
            },
            "cn-beijing",
            "workspace-example",
        ),
    ],
)
def test_strict_binding_uses_one_snapshot_and_preserves_classic_workspace(
    values, region, workspace, monkeypatch
):
    state, env, store = binding_fixture(values)
    monkeypatch.setattr(
        runtime_module,
        "get_catalog_accessor",
        lambda: pytest.fail("strict binding reread catalog"),
    )
    with bind_media_runtime_authority(
        env, store, image_intent=intent(), image_contract_version=VERSION
    ) as bound:
        assert bound.scope.region == region and bound.scope.workspace_id == workspace
        assert isinstance(bound.credential, ResolvedCredential)
        assert (
            bound.source_key == "DASHSCOPE_API_KEY"
            and bound.credential.secret == SECRET
        )
    assert state.snapshots == 1 and len(state.calls) == 1


@pytest.mark.parametrize(
    "values",
    [
        {
            "DASHSCOPE_BASE_URL": "https://ws.cn-beijing.maas.aliyuncs.com",
            "DASHSCOPE_WORKSPACE_ID": "ws",
            "DASHSCOPE_REGION": "cn-beijing",
        },
        {"DASHSCOPE_BASE_URL": "https://dashscope-us.aliyuncs.com"},
        {"DASHSCOPE_BASE_URL": "https://dashscope.aliyuncs.com//"},
        {"DASHSCOPE_BASE_URL": "HTTPS://dashscope.aliyuncs.com"},
        {"DASHSCOPE_BASE_URL": "https://dashscope.aliyuncs.com:443"},
        {"DASHSCOPE_REGION": "ap-southeast-1"},
        {"DASHSCOPE_ACCOUNT_ID": "account-example"},
        {"DASHSCOPE_ACCOUNT_IDENTITY": "account-example"},
        {"DASHSCOPE_WORKSPACE": "first", "DASHSCOPE_WORKSPACE_ID": "second"},
        {"DASHSCOPE_WORKSPACE_ID": "bad\nheader"},
    ],
)
def test_strict_scope_rejects_before_binding(values):
    state, env, store = binding_fixture(values)
    with pytest.raises(ValueError):
        with bind_media_runtime_authority(
            env, store, image_intent=intent(), image_contract_version=VERSION
        ):
            pytest.fail("invalid scope bound")
    assert state.calls == []


@pytest.mark.parametrize("tag", [None, "", "unknown"])
def test_binding_and_resolving_unknown_opt_in_cannot_fall_back(tag):
    state, env, store = binding_fixture()
    with pytest.raises(ValueError):
        with bind_media_runtime_authority(
            env, store, image_intent=intent(), image_contract_version=tag
        ):
            pytest.fail("unknown contract bound")
    with pytest.raises(ValueError):
        with resolve_media_runtime_authority(
            env,
            store,
            media_kind="image",
            provider_model_id="wan2.7-image-pro",
            scope=SCOPE,
            credential_ref="credential-example",
            image_contract_version=tag,
        ):
            pytest.fail("unknown contract resolved")
    assert state.calls == []


def test_resolve_keeps_original_scope_ref_despite_current_config_and_catalog(
    monkeypatch,
):
    original = replace(SCOPE, workspace_id="original-workspace")
    state, env, store = binding_fixture(
        {
            "DASHSCOPE_BASE_URL": "https://invalid.example",
            "DASHSCOPE_WORKSPACE_ID": "changed",
        }
    )
    monkeypatch.setattr(
        runtime_module,
        "get_catalog_accessor",
        lambda: pytest.fail("resolve read catalog"),
    )
    with resolve_media_runtime_authority(
        env,
        store,
        media_kind="image",
        provider_model_id="wan2.7-image-pro",
        scope=original,
        credential_ref="credential-example",
        image_contract_version=VERSION,
    ) as bound:
        assert bound.scope == original
    assert state.snapshots == 1 and state.calls[0][0] == original


@pytest.mark.parametrize(
    "output",
    [
        "assets/studio/attempt.png",
        "assets/atelier/attempt.png",
        "playground/images/attempt.png",
    ],
)
@pytest.mark.parametrize("strict", [False, True])
def test_dispatch_uses_frozen_marker_only_and_reuses_real_png_proof(
    tmp_path, monkeypatch, output, strict
):
    calls = []

    class FakeModel:
        def __init__(self, config, *, env):
            calls.append(("init", env))

        def generate(self, prompt, path, **kwargs):
            assert not strict
            calls.append(("legacy", kwargs))
            Path(path).write_bytes(rgb_png_bytes(width=16, height=16))

        def generate_with_bound_http(self, prompt, path, credential, **kwargs):
            assert strict and credential == authority().credential
            calls.append(("bound", kwargs))
            Path(path).write_bytes(rgb_png_bytes(width=16, height=16))

    monkeypatch.setattr(
        runtime_module,
        "get_catalog_accessor",
        lambda: pytest.fail("execution read catalog"),
    )
    submitting = record_with_changes(
        record(strict=strict, output=output), phase="submitting"
    )
    result = execute_sync_artifact_to_private_file(
        submitting,
        authority(),
        output_root=tmp_path,
        scratch_dir=tmp_path / "scratch",
        max_bytes=1024 * 1024,
        image_model_factory=FakeModel,
    )
    assert result.encoding == "image/png" and result.path.read_bytes().startswith(
        b"\x89PNG"
    )
    assert calls[1][0] == ("bound" if strict else "legacy")
    assert calls[1][1]["seed"] == 0 and calls[1][1]["watermark"] is False
    if strict:
        assert calls[0] == ("init", {})
        assert calls[1][1] == {
            "model_name": "wan2.7-image-pro",
            "size": "1024*1024",
            "watermark": False,
            "seed": 0,
            "max_bytes": 1024 * 1024,
        }
    proof = sync_artifact_proof_from_record(
        submitting,
        relative_path=output,
        sha256=result.sha256,
        size_bytes=result.size_bytes,
    )
    completed = record_with_changes(
        submitting, phase="completed", prepared_artifact=proof, artifact=proof
    )
    assert completed.fingerprint == submitting.fingerprint
    assert validate_sync_artifact_record_intent(completed) == "image"


@pytest.mark.parametrize(
    "changes",
    [
        {"region": "us-east-1", "base_url": "https://dashscope-us.aliyuncs.com"},
        {
            "gateway": "dashscope_workspace",
            "workspace_id": "ws",
            "base_url": "https://ws.cn-beijing.maas.aliyuncs.com",
        },
        {"account_identity": "example-account"},
        {"workspace_id": "bad\nheader"},
        {"base_url": "https://dashscope.aliyuncs.com//"},
        {"region": "ap-southeast-1"},
    ],
)
def test_invalid_saved_strict_scope_never_resolves_or_executes(changes, tmp_path):
    with pytest.raises(ValueError):
        record(scope=replace(SCOPE, **changes))
    try:
        scope = replace(SCOPE, **changes)
    except ValueError:
        return  # The immutable scope constructor already rejected the malformed header.
    state, env, store = binding_fixture()
    with pytest.raises(ValueError):
        with resolve_media_runtime_authority(
            env,
            store,
            media_kind="image",
            provider_model_id="wan2.7-image-pro",
            scope=scope,
            credential_ref="credential-example",
            image_contract_version=VERSION,
        ):
            pytest.fail("invalid saved scope resolved")
    assert not state.calls


@pytest.mark.parametrize(
    "model", ["qwen-image-2.0", "qwen-image-2.0-pro", "gpt-image-2", "wan2.6-image"]
)
def test_new_contract_cannot_relabel_other_provider_models(model):
    with pytest.raises(ValueError):
        record(model=model)
    state, env, store = binding_fixture()
    with pytest.raises(ValueError):
        with bind_media_runtime_authority(
            env, store, image_intent=intent(model), image_contract_version=VERSION
        ):
            pytest.fail("unsupported model bound")
    assert not state.calls


def test_strict_failure_has_no_legacy_fallback_or_public_artifact(tmp_path):
    calls = []

    class FailingModel:
        def __init__(self, config, *, env):
            assert env == {}

        def generate(self, *args, **kwargs):
            pytest.fail("strict failure fell back to legacy")

        def generate_with_bound_http(self, prompt, path, credential, **kwargs):
            calls.append(path)
            Path(path).write_bytes(b"partial")
            raise RuntimeError("synthetic uncertain provider response")

    value = record_with_changes(record(), phase="submitting")
    with pytest.raises(RuntimeError, match="synthetic uncertain"):
        execute_sync_artifact_to_private_file(
            value,
            authority(),
            output_root=tmp_path,
            scratch_dir=tmp_path / "scratch",
            max_bytes=1024,
            image_model_factory=FailingModel,
        )
    assert len(calls) == 1 and not Path(calls[0]).exists()
    assert (
        value.phase == "submitting"
        and value.artifact is None
        and value.prepared_artifact is None
    )


@pytest.mark.parametrize("tag", [None, "unknown"])
def test_invalid_marked_record_fails_before_factory(tag, tmp_path):
    value = record_with_changes(retag(record(strict=False), tag), phase="submitting")
    with pytest.raises(ValueError):
        execute_sync_artifact_to_private_file(
            value,
            authority(),
            output_root=tmp_path,
            scratch_dir=tmp_path / "scratch",
            max_bytes=1024,
            image_model_factory=lambda *args, **kwargs: pytest.fail(
                "invalid contract built client"
            ),
        )


@pytest.mark.parametrize("interrupt_type", [asyncio.CancelledError, KeyboardInterrupt])
@pytest.mark.parametrize("phase", ["provider", "normalization"])
def test_interruption_cleans_only_owned_private_files(
    tmp_path, monkeypatch, interrupt_type, phase
):
    interruption = interrupt_type("synthetic interruption")
    calls = []
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    unrelated = scratch / "unrelated.txt"
    unrelated.write_bytes(b"preserve existing scratch contents")

    class InterruptingModel:
        def __init__(self, config, *, env):
            assert env == {}

        def generate(self, *args, **kwargs):
            pytest.fail("interrupted strict execution fell back to legacy")

        def generate_with_bound_http(self, prompt, path, credential, **kwargs):
            calls.append(Path(path))
            Path(path).write_bytes(rgb_png_bytes(width=16, height=16))
            if phase == "provider":
                raise interruption

    def interrupt_normalization(input_path, output_path, **kwargs):
        assert input_path.read_bytes().startswith(b"\x89PNG")
        calls.append(output_path)
        output_path.write_bytes(b"partial normalized image")
        raise interruption

    if phase == "normalization":
        monkeypatch.setattr(execution_module, "_normalize_png", interrupt_normalization)
    value = record_with_changes(record(), phase="submitting")
    with pytest.raises(interrupt_type) as raised:
        execute_sync_artifact_to_private_file(
            value,
            authority(),
            output_root=tmp_path,
            scratch_dir=scratch,
            max_bytes=1024 * 1024,
            image_model_factory=InterruptingModel,
        )
    assert raised.value is interruption
    assert len(calls) == (1 if phase == "provider" else 2)
    assert value.phase == "submitting"
    assert value.prepared_artifact is None and value.artifact is None
    assert all(not path.exists() for path in calls)
    assert list(scratch.iterdir()) == [unrelated]
    assert unrelated.read_bytes() == b"preserve existing scratch contents"
