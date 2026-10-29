"""Versioned Edit sizes preserve the original square contract and frozen inputs."""

import base64
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from uuid import uuid4

import pytest
import requests

from src.models import provider_media_runtime
from src.models.provider_execution import ProviderExecutionRecord
from src.models.provider_media_intents import validate_sync_artifact_record_intent
from src.models.provider_media_runtime import resolve_media_runtime_authority
from src.models.provider_recovery import record_with_changes
from src.models.provider_tasks import ProviderScope
from tests import test_gpt_image2_edit_core as core
from tests.test_gpt_image2_edit_core import (
    BASE, EDIT, IMAGE_URL, INPUT, MODELS, PROMPT, VERSION,
    assert_png, binding, execute, freeze, intent, queue_success, record,
)


SIZES_VERSION = "provider-gpt-image2-edit-bound-v2"
SIZES = ("1024x1024", "1536x1024", "1024x1536")
owned = core.owned
transport = core.transport


@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("size", SIZES)
@pytest.mark.parametrize("kind", ("http", "token", "profile"))
def test_sizes_survive_serialization_original_authority_and_exact_transport(
    owned, tmp_path, transport, monkeypatch, model, size, kind,
):
    _, reference = freeze(owned)
    value = intent(model, params={"size": size})
    with binding(owned, tmp_path, kind, value, contract_version=SIZES_VERSION) as (authority, runtime, home, _):
        accepted = record(value, authority, [reference], contract_version=SIZES_VERSION)
        original = accepted.model_dump_json()
        restored = ProviderExecutionRecord.model_validate_json(original)
        assert restored.model_dump_json() == original
        assert restored.intent["intent_contract_version"] == SIZES_VERSION
        assert restored.intent["params"] == {"size": size}
        assert validate_sync_artifact_record_intent(restored) == "image"
    if kind != "http":
        runtime.update({"MULEROUTER_API_KEY": uuid4().hex}, persist=lambda *_: None)

    def no_current_catalog():
        pytest.fail("Restoring a frozen size must not consult the current catalog")

    monkeypatch.setattr(provider_media_runtime, "get_catalog_accessor", no_current_catalog)
    with resolve_media_runtime_authority(
        runtime, owned.store, media_kind="image", provider_model_id=restored.provider_model_id,
        scope=ProviderScope.from_dict(restored.scope), credential_ref=restored.credential_ref,
        image_contract_version=restored.intent["intent_contract_version"],
        mulerun_profile_home=home if kind == "profile" else None,
    ) as recovered:
        assert recovered.public_identity() == authority.public_identity()
        queue_success(transport, kind)
        assert_png(execute(owned, restored, recovered, tmp_path))
    if kind == "http":
        assert [(call.method, call.url) for call, _ in transport["calls"]] == [
            ("POST", BASE + EDIT), ("GET", BASE + EDIT + "/edit-task"), ("GET", IMAGE_URL),
        ]
        assert json.loads(transport["calls"][0][0].body) == {
            "prompt": PROMPT, "size": size, "n": 1,
            "images": ["data:image/png;base64," + base64.b64encode(INPUT).decode()],
        }
        assert not transport["cli"]
    else:
        assert len(transport["cli"]) == 1
        sent = transport["cli"][0]
        assert "openai/gpt-image-2/edit" in sent["argv"]
        assert sent["argv"][sent["argv"].index("--size") + 1] == size
        assert "--quality" not in sent["argv"] and sent["ref_bytes"] == [INPUT]
        assert [(call.method, call.url) for call, _ in transport["calls"]] == [("GET", IMAGE_URL)]
    assert not transport["queue"] and not transport["cli_queue"]
    assert accepted.model_dump_json() == restored.model_dump_json() == original


@pytest.mark.parametrize("params", [
    {"size": None}, {"size": "1024*1536"}, {"size": " 1024x1536"},
    {"size": 1536}, {"size": True}, {"size": ""}, {"size": "auto"},
    {"size": "2048x2048"}, {}, {"size": "1024x1536", "quality": None},
    {"size": "1024x1536", "mask": None}, {"size": "1024x1536", "negative_prompt": ""},
    {"size": "1024x1536", "n": 1}, {"size": "1024x1536", "format": "png"},
])
def test_sizes_reject_unsupported_raw_presence_before_binding(
    owned, tmp_path, transport, monkeypatch, params,
):
    value = replace(intent(), params=params)

    def unexpected_bind(*_args, **_kwargs):
        pytest.fail("Invalid size profile reached credential storage")

    monkeypatch.setattr(owned.store, "bind", unexpected_bind)
    with pytest.raises(ValueError):
        with binding(owned, tmp_path, "http", value, contract_version=SIZES_VERSION):
            pytest.fail("Unsupported size parameters were admitted")
    assert not (owned.root / "provider_credentials.json").exists()
    assert not transport["calls"] and not transport["cli"]


@pytest.mark.parametrize("size", SIZES[1:])
def test_original_version_still_rejects_rectangles(owned, tmp_path, transport, size):
    with pytest.raises(ValueError):
        with binding(owned, tmp_path, "http", intent(params={"size": size}), contract_version=VERSION):
            pytest.fail("The original square contract was silently widened")
    assert not (owned.root / "provider_credentials.json").exists()
    assert not transport["calls"] and not transport["cli"]


@pytest.mark.parametrize("mutation", ("none", "two", "role"))
def test_sizes_keep_one_frozen_reference(owned, tmp_path, transport, mutation):
    _, reference = freeze(owned)
    media = {"none": [], "two": [reference, replace(reference, ordinal=1)],
             "role": [replace(reference, role="first_frame")]}[mutation]
    value = intent(params={"size": "1024x1536"})
    with binding(owned, tmp_path, "http", value, contract_version=SIZES_VERSION) as (authority, *_):
        with pytest.raises(ValueError):
            record(value, authority, media, contract_version=SIZES_VERSION)
    assert not transport["calls"] and not transport["cli"]


def test_rectangular_unknown_submit_does_not_repeat_or_fallback(owned, tmp_path, transport):
    _, reference = freeze(owned)
    value = intent(params={"size": "1024x1536"})
    transport["queue"].append(requests.Timeout("synthetic response lost"))
    with binding(owned, tmp_path, "http", value, contract_version=SIZES_VERSION) as (authority, *_):
        accepted = record(value, authority, [reference], contract_version=SIZES_VERSION)
        original = accepted.model_dump_json()
        with pytest.raises(requests.Timeout):
            execute(owned, accepted, authority, tmp_path)
        assert accepted.model_dump_json() == original
    assert [(call.method, call.url) for call, _ in transport["calls"]] == [("POST", BASE + EDIT)]
    assert not transport["cli"] and not transport["queue"]


def test_rehashed_old_version_cannot_acquire_rectangular_semantics(owned, tmp_path, transport):
    _, reference = freeze(owned)
    with binding(owned, tmp_path, "http", intent()) as (authority, *_):
        accepted = record(intent(), authority, [reference])
        raw = deepcopy(accepted.intent)
        raw["params"]["size"] = "1024x1536"
        altered = record_with_changes(accepted, intent=raw)
        identity = {key: getattr(altered, key) for key in (
            "attempt_id", "provider_model_id", "intent", "media", "scope", "credential_ref",
            "execution_kind", "media_kind", "output_ref",
        )}
        digest = hashlib.sha256((json.dumps(identity, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()).hexdigest()
        altered = record_with_changes(altered, fingerprint=digest)
        with pytest.raises(ValueError):
            execute(owned, altered, authority, tmp_path)
    assert not transport["calls"] and not transport["cli"]
