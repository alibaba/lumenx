"""Exact opt-in GPT Edit contract through real Core and adapter code.

Only external Session.send and this adapter's external CLI process are replaced.
"""

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict, replace
import base64
import hashlib
import io
import json
from pathlib import Path
import subprocess
from threading import RLock
import time
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
import requests

from src.models import mulerouter, provider_media_runtime
from src.models.mulerouter import MuleRouterImageModel
from src.models.provider_binding import (
    MuleRunCliRouteIdentity, ProviderCredentialResolutionError, ProviderCredentialStore,
    ResolvedMuleRunTokenCredential,
)
from src.models.provider_execution import ProviderExecutionRecord
from src.models.provider_media_execution import ProviderMediaExecutionError, execute_sync_artifact_to_private_file
from src.models.provider_media_intents import (
    build_accepted_sync_artifact_record, validate_sync_artifact_record_intent,
)
from src.models.provider_media_runtime import bind_media_runtime_authority, resolve_media_runtime_authority
from src.models.provider_recovery import record_with_changes
from src.models.provider_tasks import ProviderScope, ResolvedCredential
from src.utils.generation_intent import GenerationIntent, normalize_generation_intent
from src.utils.json_storage import DirectoryWriterLock
from src.utils.media_probe import probe_image_file
from src.utils.media_versions import freeze_media
from src.utils.runtime_environment import RuntimeEnvironment
from tests.frozen_image_fixtures import rgb_png_bytes

VERSION = "provider-gpt-image2-edit-bound-v1"
MODELS = ("gpt-image-2", "gpt-image/gpt-image-2#image")
BASE = "https://api.mulerouter.ai"
EDIT = "/vendors/openai/v1/gpt-image-2/edit"
IMAGE_URL = "https://files.example.invalid/edit.png"
PROMPT = "Preserve the face; turn the figure to profile."
INPUT = rgb_png_bytes("edit source", width=16, height=16)
OUTPUT = rgb_png_bytes("edited output", width=16, height=16)
LIMIT = 1024 * 1024


class ResponseStream(io.BytesIO):
    def release_conn(self):
        self.close()


def response(value, status=200):
    result = requests.Response()
    result.status_code = status
    result.raw = ResponseStream(value if isinstance(value, bytes) else json.dumps(value).encode())
    return result


@pytest.fixture
def transport(monkeypatch):
    state = {"queue": [], "calls": [], "cli": [], "cli_queue": []}

    def send(_session, prepared, **options):
        state["calls"].append((prepared, options))
        assert state["queue"], f"Unexpected external request: {prepared.method} {prepared.url}"
        item = state["queue"].pop(0)
        if isinstance(item, BaseException):
            raise item
        item.request = prepared
        item.url = prepared.url
        return item

    def run(argv, **options):
        assert argv[0] == "mulerun" and "studio" in argv and "run" in argv
        assert state["cli_queue"], "Unexpected external CLI invocation"
        home = Path(options["env"]["HOME"]).resolve()
        assert home.is_dir() and home != Path.home().resolve()
        refs = json.loads(argv[argv.index("--images") + 1]) if "--images" in argv else []
        state["cli"].append({"argv": argv, "options": options,
                             "ref_bytes": [Path(ref).read_bytes() for ref in refs], "home": home})
        item = state["cli_queue"].pop(0)
        if isinstance(item, BaseException):
            raise item
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(item), stderr="")

    monkeypatch.setattr(requests.sessions.Session, "send", send)
    monkeypatch.setattr(requests.sessions, "get_netrc_auth", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(mulerouter, "subprocess", SimpleNamespace(run=run, TimeoutExpired=subprocess.TimeoutExpired))
    monkeypatch.setattr(mulerouter, "time", SimpleNamespace(time=time.time, sleep=lambda _seconds: None))
    return state


@pytest.fixture
def owned(tmp_path):
    root = tmp_path / "output"
    root.mkdir()
    with DirectoryWriterLock(root) as lease:
        yield SimpleNamespace(root=root, lease=lease,
                              store=ProviderCredentialStore(root, writer_lock=lease, runtime_lock=RLock()))


def intent(model=MODELS[0], *, mode="i2i", params=None):
    return normalize_generation_intent(
        {"model_id": model, "mode": mode, "prompt": PROMPT,
         "params": {"size": "1024x1024"} if params is None else params},
        allowed_modes={"t2i", "i2i"}, allowed_parameters={"size"},
    )


def freeze(owned, content=INPUT):
    source = owned.root / "uploads" / "source.png"
    source.parent.mkdir(exist_ok=True)
    source.write_bytes(content)
    version = freeze_media("uploads/source.png", output_root=owned.root,
                           writer_lock=owned.lease, role="reference_image", ordinal=0, max_bytes=LIMIT)
    return source, version


def environment(kind, tmp_path):
    routes = MuleRunCliRouteIdentity(1, "sg", "https://api.mulerun.com", "mulerouter", BASE)
    secret = uuid4().hex + uuid4().hex
    home = tmp_path / "source-profile"
    values = {}
    if kind == "http":
        values["MULEROUTER_API_KEY"] = secret
    elif kind == "token":
        values["MULERUN_TOKEN"] = secret
    else:
        profile = home / ".mulerun"
        profile.mkdir(parents=True)
        (profile / "session.json").write_text(secret)
        values["LUMENX_MULERUN_HOME"] = str(home)
    return RuntimeEnvironment(values), routes, home, secret


@contextmanager
def binding(owned, tmp_path, kind, value, *, marked=True, contract_version=VERSION):
    runtime, routes, home, secret = environment(kind, tmp_path)
    options: dict[str, Any] = {"image_contract_version": contract_version} if marked else {}
    with bind_media_runtime_authority(runtime, owned.store, image_intent=value,
                                      mulerun_routes=routes,
                                      mulerun_profile_home=home if kind == "profile" else None,
                                      **options) as authority:
        yield authority, runtime, home, secret


def record(value, authority, versions=(), *, marked=True, contract_version=VERSION):
    options = {"image_contract_version": contract_version} if marked else {}
    return build_accepted_sync_artifact_record(
        intent=value, provider_model_id=authority.provider_model_id, media_kind="image",
        media=[asdict(version) for version in versions], scope=authority.scope,
        credential_ref=authority.credential_ref, attempt_id="edit-attempt",
        output_ref="assets/studio/edit-attempt.png", **options,
    )


def queue_success(state, kind):
    if kind == "http":
        state["queue"].extend([
            response({"task_info": {"id": "edit-task"}}, 202),
            response({"task_info": {"status": "completed", "output": {"image_url": IMAGE_URL}}}),
        ])
    else:
        state["cli_queue"].append({"output": {"image_url": IMAGE_URL}})
    state["queue"].append(response(OUTPUT))


def execute(owned, accepted, authority, tmp_path):
    return execute_sync_artifact_to_private_file(
        record_with_changes(accepted, phase="submitting"), authority,
        output_root=owned.root, scratch_dir=tmp_path / "scratch", max_bytes=LIMIT,
    )


def assert_png(result):
    data = result.path.read_bytes()
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    assert result.sha256 == hashlib.sha256(data).hexdigest()
    assert result.size_bytes == len(data)
    assert result.encoding == "image/png" and result.media_kind == "image"
    decoded = probe_image_file(result.path)
    assert (decoded.width, decoded.height) == (16, 16)


def assert_http_edit(state):
    assert [(call.method, call.url) for call, _ in state["calls"]] == [
        ("POST", BASE + EDIT), ("GET", BASE + EDIT + "/edit-task"), ("GET", IMAGE_URL),
    ]
    body = json.loads(state["calls"][0][0].body)
    assert body == {"prompt": PROMPT, "size": "1024x1024", "n": 1,
                    "images": ["data:image/png;base64," + base64.b64encode(INPUT).decode()]}


@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("kind", ("http", "token", "profile"))
def test_marked_edit_runs_real_core_factory_and_bound_transport(owned, tmp_path, transport, model, kind):
    _, version = freeze(owned)
    value = intent(model)
    assert value.model_id == MODELS[1] and value.params == {"size": "1024x1024"}
    with binding(owned, tmp_path, kind, value) as (authority, _runtime, _home, _secret):
        accepted = record(value, authority, [version])
        assert accepted.schema_version == 2 and accepted.intent["intent_contract_version"] == VERSION
        assert validate_sync_artifact_record_intent(accepted) == "image"
        queue_success(transport, kind)
        result = execute(owned, accepted, authority, tmp_path)
        assert_png(result)
        if kind == "http":
            assert_http_edit(transport)
            assert not transport["cli"]
        else:
            assert len(transport["cli"]) == 1
            sent = transport["cli"][0]
            assert "openai/gpt-image-2/edit" in sent["argv"]
            assert "--quality" not in sent["argv"]
            assert sent["ref_bytes"] == [INPUT]
            assert sent["argv"][sent["argv"].index("--size") + 1] == "1024x1024"
            assert [(call.method, call.url) for call, _ in transport["calls"]] == [("GET", IMAGE_URL)]
    assert not transport["queue"] and not transport["cli_queue"]


def test_marker_bound_adapter_wire_reaches_session_without_new_core_admission(tmp_path, transport):
    """The first wire RED must not stop at an unsupported Core marker."""
    path = tmp_path / "reference.png"
    path.write_bytes(INPUT)
    credential = ResolvedCredential(ProviderScope("mulerouter", "mulerouter", "mulerouter_http", BASE),
                                    "adapter-wire", uuid4().hex)
    queue_success(transport, "http")
    MuleRouterImageModel({"output_root": str(tmp_path)}, env={}).generate_with_bound_http(
        PROMPT, str(tmp_path / "output.png"), credential, image_contract_version=VERSION,
        size="1024x1024", ref_image_paths=[str(path)],
    )
    assert_http_edit(transport)


@pytest.mark.parametrize("mutation", ("replace", "remove"))
def test_original_path_changes_do_not_change_frozen_reference(owned, tmp_path, transport, mutation):
    source, version = freeze(owned)
    value = intent()
    with binding(owned, tmp_path, "http", value) as (authority, *_):
        accepted = record(value, authority, [version])
        if mutation == "replace":
            source.write_bytes(rgb_png_bytes("replacement", width=16, height=16))
        else:
            source.unlink()
        queue_success(transport, "http")
        assert_png(execute(owned, accepted, authority, tmp_path))
        assert_http_edit(transport)


@pytest.mark.parametrize("fault", ("missing", "corrupt", "undecodable"))
def test_bad_frozen_blob_fails_before_external_calls(owned, tmp_path, transport, fault):
    _, version = freeze(owned, b"not a PNG" if fault == "undecodable" else INPUT)
    value = intent()
    with binding(owned, tmp_path, "http", value) as (authority, *_):
        accepted = record(value, authority, [version])
        blob = owned.root / "media_versions" / "sha256" / version.sha256[:2] / version.sha256
        if fault == "missing":
            blob.unlink()
        elif fault == "corrupt":
            blob.write_bytes(b"x" * version.size_bytes)
        with pytest.raises((ValueError, ProviderMediaExecutionError)):
            execute(owned, accepted, authority, tmp_path)
        assert not transport["calls"] and not transport["cli"]


@pytest.mark.parametrize("extra", [
    {"quality": "high"}, {"quality": "medium"}, {"quality": "low"}, {"quality": None},
    {"seed": 0}, {"seed": None}, {"negative_prompt": "avoid blur"}, {"negative_prompt": None},
    {"watermark": False}, {"watermark": None}, {"prompt_extend": False},
    {"n": 1}, {"mask": None}, {"format": "png"}, {"unknown": None},
])
@pytest.mark.parametrize("kind", ("http", "profile"))
def test_new_parameters_reject_before_credential_bind(owned, tmp_path, transport, monkeypatch, extra, kind):
    value = intent()
    value = replace(value, params={**value.params, **extra},
                    sources={**value.sources, **{f"params.{key}": "request" for key in extra}},
                    request_fields=(*value.request_fields, *(f"params.{key}" for key in extra)))
    before = {str(path.relative_to(owned.root)): path.read_bytes()
              for path in owned.root.rglob("*") if path.is_file()}

    def unexpected_bind(*_args, **_kwargs):
        pytest.fail("Invalid new parameters reached the credential/profile binding store")

    monkeypatch.setattr(owned.store, "bind", unexpected_bind)
    monkeypatch.setattr(owned.store, "bind_mulerun_profile", unexpected_bind)
    with pytest.raises(ValueError):
        with binding(owned, tmp_path, kind, value):
            pytest.fail("Invalid new parameters were accepted")
    after = {str(path.relative_to(owned.root)): path.read_bytes()
             for path in owned.root.rglob("*") if path.is_file()}
    assert before == after and not transport["calls"] and not transport["cli"]


@pytest.mark.parametrize("size", (None, "1024*1024", "1536x1024", 1024, True, ""))
def test_exact_size_does_not_coerce_into_supported_value(owned, tmp_path, transport, size):
    value = replace(intent(), params={"size": size})
    with pytest.raises(ValueError):
        with binding(owned, tmp_path, "http", value):
            pytest.fail("Invalid exact Edit size was accepted")
    assert not (owned.root / "provider_credentials.json").exists()
    assert not transport["calls"] and not transport["cli"]


@pytest.mark.parametrize("mutation", ("none", "two", "role", "ordinal", "mime"))
def test_record_rejects_reference_cardinality_and_identity(owned, tmp_path, transport, mutation):
    _, version = freeze(owned)
    versions = {"none": [], "two": [version, replace(version, ordinal=1)],
                "role": [replace(version, role="first_frame")],
                "ordinal": [replace(version, ordinal=1)],
                "mime": [replace(version, declared_media_type="video/mp4")]}[mutation]
    value = intent()
    with binding(owned, tmp_path, "http", value) as (authority, *_):
        with pytest.raises(ValueError):
            record(value, authority, versions)
    assert not transport["calls"] and not transport["cli"]


@pytest.mark.parametrize("fault", ("connection", "timeout", "429", "502", "503", "504"))
def test_marked_http_attempt_never_reposts_after_uncertain_submit(owned, tmp_path, transport, fault):
    _, version = freeze(owned)
    if fault == "connection":
        transport["queue"].append(requests.ConnectionError("synthetic connection lost"))
    elif fault == "timeout":
        transport["queue"].append(requests.Timeout("synthetic timeout"))
    else:
        transport["queue"].append(response({}, int(fault)))
    value = intent()
    with binding(owned, tmp_path, "http", value) as (authority, *_):
        accepted = record(value, authority, [version])
        before = accepted.model_dump(mode="json")
        with pytest.raises(requests.RequestException):
            execute(owned, accepted, authority, tmp_path)
        assert accepted.model_dump(mode="json") == before
    assert [(call.method, call.url) for call, _ in transport["calls"]] == [("POST", BASE + EDIT)]
    assert not transport["cli"] and not transport["queue"]


def test_get_retry_does_not_repeat_marked_post(owned, tmp_path, transport):
    _, version = freeze(owned)
    value = intent()
    queue_success(transport, "http")
    transport["queue"].insert(1, response({}, 503))
    with binding(owned, tmp_path, "http", value) as (authority, *_):
        assert_png(execute(owned, record(value, authority, [version]), authority, tmp_path))
    assert [call.method for call, _ in transport["calls"]] == ["POST", "GET", "GET", "GET"]
    assert transport["calls"][1][0].url == transport["calls"][2][0].url == BASE + EDIT + "/edit-task"


@pytest.mark.parametrize("kind", ("http", "token", "profile"))
def test_restore_uses_original_authority_without_catalog_or_route_reselection(owned, tmp_path, transport, monkeypatch, kind):
    _, version = freeze(owned)
    value = intent()
    with binding(owned, tmp_path, kind, value) as (authority, runtime, home, _secret):
        accepted = record(value, authority, [version])
    # A newly available HTTP key must not replace an original CLI identity.
    if kind != "http":
        runtime.update({"MULEROUTER_API_KEY": uuid4().hex}, persist=lambda *_: None)

    def catalog_forbidden():
        pytest.fail("Recovery read the current catalog")

    monkeypatch.setattr(provider_media_runtime, "get_catalog_accessor", catalog_forbidden)
    with resolve_media_runtime_authority(
        runtime, owned.store, media_kind="image", provider_model_id=accepted.provider_model_id,
        scope=ProviderScope.from_dict(accepted.scope), credential_ref=accepted.credential_ref,
        image_contract_version=VERSION, mulerun_profile_home=home if kind == "profile" else None,
    ) as restored:
        assert restored.scope.to_dict() == accepted.scope and restored.credential_ref == accepted.credential_ref
        assert restored.scope.gateway == {"http": "mulerouter_http", "token": "mulerun_token", "profile": "mulerun_profile"}[kind]
        queue_success(transport, kind)
        assert_png(execute(owned, accepted, restored, tmp_path))


@pytest.mark.parametrize("kind", ("http", "token", "profile"))
def test_changed_original_credentials_do_not_fallback(owned, tmp_path, transport, kind):
    value = intent()
    with binding(owned, tmp_path, kind, value) as (authority, runtime, home, _secret):
        scope, ref = authority.scope, authority.credential_ref
    if kind == "profile":
        (home / ".mulerun" / "session.json").write_text(uuid4().hex)
    else:
        key = "MULEROUTER_API_KEY" if kind == "http" else "MULERUN_TOKEN"
        runtime.update({key: uuid4().hex}, persist=lambda *_: None)
    with pytest.raises(ProviderCredentialResolutionError):
        with resolve_media_runtime_authority(
            runtime, owned.store, media_kind="image", provider_model_id="gpt-image-2",
            scope=scope, credential_ref=ref, image_contract_version=VERSION,
            mulerun_profile_home=home if kind == "profile" else None,
        ):
            pytest.fail("Changed original credential was silently rebound")
    assert not transport["calls"] and not transport["cli"]


def test_marked_admission_uses_one_environment_snapshot(owned):
    class Once(RuntimeEnvironment):
        calls = 0

        def snapshot(self):
            self.calls += 1
            assert self.calls == 1, "Admission read a second configuration snapshot"
            return super().snapshot()

    runtime = Once({"MULEROUTER_API_KEY": uuid4().hex})
    with bind_media_runtime_authority(runtime, owned.store, image_intent=intent(), image_contract_version=VERSION):
        pass
    assert runtime.calls == 1


@pytest.mark.parametrize("change", ("raw_extra", "quality", "source", "request_fields", "mode", "model", "version"))
def test_rehashed_marked_record_cannot_bypass_contract(owned, tmp_path, transport, change):
    _, version = freeze(owned)
    value = intent()
    with binding(owned, tmp_path, "http", value) as (authority, *_):
        accepted = record(value, authority, [version])
        raw = deepcopy(accepted.intent)
        if change == "raw_extra":
            raw["unknown"] = None
        elif change == "quality":
            raw["params"]["quality"] = None
        elif change == "source":
            raw["sources"]["params.quality"] = "catalog"
        elif change == "request_fields":
            raw["request_fields"].append("params.quality")
        elif change == "mode":
            raw["mode"] = "t2i"
        elif change == "model":
            raw["model_id"] = "wan/wan2.7-image-pro#image"
        else:
            raw["intent_contract_version"] = "provider-gpt-image2-edit-bound-v3"
        altered = record_with_changes(accepted, intent=raw)
        # Rehash independently to prove schema validation, not just hash rejection.
        identity = {key: getattr(altered, key) for key in (
            "attempt_id", "provider_model_id", "intent", "media", "scope", "credential_ref",
            "execution_kind", "media_kind", "output_ref",
        )}
        digest = hashlib.sha256((json.dumps(identity, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()).hexdigest()
        altered = record_with_changes(altered, fingerprint=digest)
        with pytest.raises(ValueError):
            execute(owned, altered, authority, tmp_path)
        assert not transport["calls"] and not transport["cli"]


@pytest.mark.parametrize("model,mode", [("wan/wan2.7-image-pro#image", "i2i"),
                                       ("qwen/qwen-image-2.0#image", "i2i"),
                                       ("openai/gpt-image-2#image", "i2i"), (MODELS[1], "t2i")])
def test_marker_cannot_claim_other_model_or_mode(owned, tmp_path, transport, model, mode):
    value = replace(intent(), model_id=model, mode=mode)
    with pytest.raises(ValueError):
        with binding(owned, tmp_path, "http", value):
            pytest.fail("Edit marker admitted another model or mode")
    assert not (owned.root / "provider_credentials.json").exists()
    assert not transport["calls"] and not transport["cli"]


@pytest.mark.parametrize("mode", ("t2i", "i2i"))
def test_legacy_payload_retains_quality_and_historical_wire(owned, tmp_path, transport, mode):
    value = normalize_generation_intent(
        {"model_id": MODELS[0], "mode": mode, "prompt": PROMPT, "params": {"size": "1024x1024"}},
        allowed_modes={"t2i", "i2i"}, allowed_parameters={"size", "quality"},
    )
    assert value.params["quality"] == "high"
    versions = [freeze(owned)[1]] if mode == "i2i" else []
    with binding(owned, tmp_path, "http", value, marked=False) as (authority, *_):
        accepted = record(value, authority, versions, marked=False)
        original = accepted.model_dump_json()
        assert "intent_contract_version" not in accepted.intent
        assert ProviderExecutionRecord.model_validate_json(original).model_dump_json() == original
        queue_success(transport, "http")
        assert_png(execute(owned, accepted, authority, tmp_path))
        body = json.loads(transport["calls"][0][0].body)
        expected = {"prompt": PROMPT, "size": "1024x1024", "quality": "high", "n": 1}
        if mode == "i2i":
            expected["image"] = "data:image/png;base64," + base64.b64encode(INPUT).decode()
        assert body == expected and "images" not in body
        assert accepted.model_dump_json() == original


@pytest.mark.parametrize("kind", ("token", "profile"))
def test_cli_failure_never_falls_back_to_http(owned, tmp_path, transport, kind):
    _, version = freeze(owned)
    value = intent()
    transport["cli_queue"].append(subprocess.TimeoutExpired("mulerun", 300))
    with binding(owned, tmp_path, kind, value) as (authority, *_):
        with pytest.raises(RuntimeError):
            execute(owned, record(value, authority, [version]), authority, tmp_path)
    assert len(transport["cli"]) == 1 and not transport["calls"]


@pytest.mark.parametrize("fault", ("absent", "missing"))
def test_marked_adapter_never_silently_falls_back_without_resolved_reference(tmp_path, transport, fault):
    credential = ResolvedCredential(ProviderScope("mulerouter", "mulerouter", "mulerouter_http", BASE),
                                    "missing-ref", uuid4().hex)
    refs = [] if fault == "absent" else [str(tmp_path / "missing.png")]
    with pytest.raises(ValueError):
        MuleRouterImageModel({"output_root": str(tmp_path)}, env={}).generate_with_bound_http(
            PROMPT, str(tmp_path / "output.png"), credential, image_contract_version=VERSION,
            size="1024x1024", ref_image_paths=refs,
        )
    assert not transport["calls"] and not transport["cli"]


# Fixed bytes specified independently of the record builder; never recompute expected
# fingerprints with the implementation under test.
LEGACY_GOLDENS = {'t2i': {'fingerprint': '7b0050c2fbe9c334df62159af80f6a98ab73de6fc485505fff72041ae9ff3a20', 'canonical_json': '{"artifact":null,"attempt_id":"legacy-golden","credential_ref":"legacy-credential","execution_kind":"sync_artifact","fingerprint":"7b0050c2fbe9c334df62159af80f6a98ab73de6fc485505fff72041ae9ff3a20","handle":null,"intent":{"catalog_mode":"image","mode":"t2i","model_id":"openai/gpt-image-2#image","params":{"quality":"high","size":"1024x1024"},"preset_id":null,"preset_version":null,"prompt":"historical edit","request_fields":["model_id","mode","prompt","params.size","params.quality"],"rules_version":"generation-parameters-v1","scope":"parameters-only","sources":{"mode":"request","model_id":"request","params.quality":"request","params.size":"request","prompt":"request"}},"media":[],"media_kind":"image","observation":null,"output_ref":"assets/atelier/legacy.png","phase":"accepted","prepared_artifact":null,"provider_model_id":"gpt-image-2","schema_version":2,"scope":{"account_identity":null,"backend":"mulerouter","base_url":"https://api.mulerouter.ai","gateway":"mulerouter_http","provider":"mulerouter","region":null,"workspace_id":null}}'}, 'i2i': {'fingerprint': '9357df30f9a0e3fafb93b40e5e1c829083a504d7d96994c8339ab77302a5f547', 'canonical_json': '{"artifact":null,"attempt_id":"legacy-golden","credential_ref":"legacy-credential","execution_kind":"sync_artifact","fingerprint":"9357df30f9a0e3fafb93b40e5e1c829083a504d7d96994c8339ab77302a5f547","handle":null,"intent":{"catalog_mode":"image","mode":"i2i","model_id":"openai/gpt-image-2#image","params":{"quality":"high","size":"1024x1024"},"preset_id":null,"preset_version":null,"prompt":"historical edit","request_fields":["model_id","mode","prompt","params.size","params.quality"],"rules_version":"generation-parameters-v1","scope":"parameters-only","sources":{"mode":"request","model_id":"request","params.quality":"request","params.size":"request","prompt":"request"}},"media":[{"declared_media_type":"image/png","ordinal":0,"role":"reference_image","schema_version":1,"sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","size_bytes":123}],"media_kind":"image","observation":null,"output_ref":"assets/atelier/legacy.png","phase":"accepted","prepared_artifact":null,"provider_model_id":"gpt-image-2","schema_version":2,"scope":{"account_identity":null,"backend":"mulerouter","base_url":"https://api.mulerouter.ai","gateway":"mulerouter_http","provider":"mulerouter","region":null,"workspace_id":null}}'}}


@pytest.mark.parametrize("mode", ("t2i", "i2i"))
def test_unmarked_legacy_json_and_fingerprint_match_fixed_golden(mode):
    value = GenerationIntent(
        model_id="openai/gpt-image-2#image", catalog_mode="image", mode=mode, prompt="historical edit",
        params={"size": "1024x1024", "quality": "high"},
        sources={"model_id": "request", "mode": "request", "prompt": "request",
                 "params.size": "request", "params.quality": "request"},
        request_fields=("model_id", "mode", "prompt", "params.size", "params.quality"),
    )
    media = [{"sha256": "a" * 64, "size_bytes": 123, "role": "reference_image", "ordinal": 0,
              "declared_media_type": "image/png", "schema_version": 1}] if mode == "i2i" else []
    accepted = build_accepted_sync_artifact_record(
        intent=value, provider_model_id="gpt-image-2", media_kind="image", media=media,
        scope=ProviderScope("mulerouter", "mulerouter", "mulerouter_http", BASE),
        credential_ref="legacy-credential", attempt_id="legacy-golden", output_ref="assets/atelier/legacy.png",
    )
    assert accepted.fingerprint == LEGACY_GOLDENS[mode]["fingerprint"]
    canonical = json.dumps(accepted.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    assert canonical == LEGACY_GOLDENS[mode]["canonical_json"]
    restored = ProviderExecutionRecord.model_validate_json(accepted.model_dump_json())
    assert validate_sync_artifact_record_intent(restored) == "image"
    assert "intent_contract_version" not in restored.intent and restored.intent["params"]["quality"] == "high"


def test_missing_resolved_size_fails_before_binding(owned, tmp_path, transport):
    value = replace(intent(), params={}, sources={"model_id": "request", "mode": "request", "prompt": "request"},
                    request_fields=("model_id", "mode", "prompt"))
    with pytest.raises(ValueError):
        with binding(owned, tmp_path, "http", value):
            pytest.fail("Edit contract accepted an unresolved size")
    assert not (owned.root / "provider_credentials.json").exists()
    assert not transport["calls"] and not transport["cli"]


@pytest.mark.parametrize("provider_model,scope", [
    ("wan2.7-image-pro", ProviderScope("mulerouter", "mulerouter", "mulerouter_http", BASE)),
    ("gpt-image-2", ProviderScope("dashscope", "dashscope", "dashscope", "https://dashscope.aliyuncs.com", "cn-beijing")),
    ("gpt-image-2", ProviderScope("mulerouter", "mulerouter", "unknown", BASE)),
])
def test_exact_contract_rejects_model_scope_mismatch_without_binding(provider_model, scope):
    media = [{"sha256": "a" * 64, "size_bytes": 123, "role": "reference_image", "ordinal": 0,
              "declared_media_type": "image/png", "schema_version": 1}]
    with pytest.raises(ValueError):
        build_accepted_sync_artifact_record(
            intent=intent(), provider_model_id=provider_model, media_kind="image", media=media,
            scope=scope, credential_ref="no-store-read", attempt_id="bad-scope", output_ref="assets/studio/bad.png",
            image_contract_version=VERSION,
        )


def test_marked_cli_rejects_reference_lost_during_final_resolution(owned, tmp_path, transport, monkeypatch):
    reference = tmp_path / "reference.png"
    reference.write_bytes(INPUT)
    model = MuleRouterImageModel({}, env={})
    original = model._resolve_local_image_path
    resolutions = 0

    def remove_before_final_resolution(path):
        nonlocal resolutions
        resolutions += 1
        if resolutions == 2:
            reference.unlink()
        return original(path)

    monkeypatch.setattr(model, "_resolve_local_image_path", remove_before_final_resolution)
    queue_success(transport, "token")
    with binding(owned, tmp_path, "token", intent()) as (authority, *_):
        assert isinstance(authority.credential, ResolvedMuleRunTokenCredential)
        with pytest.raises(ValueError):
            model.generate_with_bound_cli(
                PROMPT, str(tmp_path / "output.png"), authority.credential,
                image_contract_version=VERSION, size="1024x1024", ref_image_paths=[str(reference)],
            )
    assert resolutions == 2 and not reference.exists()
    assert not transport["calls"] and not transport["cli"]
