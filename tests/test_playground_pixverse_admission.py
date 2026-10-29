"""Playground PixVerse durable provider admission."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast
from unittest.mock import Mock
import struct
import uuid
import zlib

import pytest

from src.apps.playground.models import (
    GenerateRequest,
    PlaygroundAttempt,
    PlaygroundGeneration,
    PlaygroundMode,
)
from src.apps.playground.service import PlaygroundService
from src.apps.playground.storage import PlaygroundStorage
from src.apps.playground.provider_video_execution import is_core_video_generation
from src.apps.playground.pixverse_video_admission import (
    PixVerseVideoAdmission,
    admitted_pixverse_video_generation,
)
from src.models.provider_binding import ProviderCredentialStore
from src.models.provider_runtime import ProviderRecoveryRuntime
from src.models.provider_tasks import ProviderScope
from src.utils.generation_intent import IntentValidationError
from src.utils.json_storage import DirectoryWriterLock
from src.utils.model_catalog import CatalogAccessor, load_generated_model_catalog
from tests.frozen_image_fixtures import rgb_png_bytes
from tests.test_wan3_image_validation import WEBP_240_BASE64, png_bytes

import base64


SECRET = "sk-test-" + "v" * 44
WORKSPACE_SCOPE = ProviderScope(
    "dashscope",
    "dashscope",
    "dashscope_workspace",
    "https://pixverse-space.cn-beijing.maas.aliyuncs.com",
    "cn-beijing",
    workspace_id="pixverse-space",
)
WRONG_REGION_SCOPE = ProviderScope(
    "dashscope",
    "dashscope",
    "dashscope_workspace",
    "https://pixverse-space.ap-southeast-1.maas.aliyuncs.com",
    "ap-southeast-1",
    workspace_id="pixverse-space",
)
WRONG_HOST_SCOPE = ProviderScope(
    "dashscope",
    "dashscope",
    "dashscope_workspace",
    "https://other-space.cn-beijing.maas.aliyuncs.com",
    "cn-beijing",
    workspace_id="pixverse-space",
)

CANONICAL_BY_LEGACY = {
    "pixverse/pixverse-v6-video": "pixverse/pixverse/pixverse-v6-video#i2v",
    "pixverse-c1-i2v": "pixverse/pixverse-c1-video#i2v",
    "pixverse-c1-r2v": "pixverse/pixverse-c1-video#r2v",
    "pixverse-v5.6-r2v": "pixverse/pixverse-v5.6-r2v#r2v",
}
API_BY_CANONICAL = {
    "pixverse/pixverse/pixverse-v6-video#i2v": "pixverse/pixverse-v6-it2v",
    "pixverse/pixverse-c1-video#i2v": "pixverse/pixverse-c1-it2v",
    "pixverse/pixverse-c1-video#r2v": "pixverse/pixverse-c1-r2v",
    "pixverse/pixverse-v5.6-r2v#r2v": "pixverse/pixverse-v5.6-r2v",
}


class RuntimeProvider:
    def __init__(self, runtime):
        self.runtime = runtime
        self.calls: list[tuple[str, str]] = []

    def __call__(self, *, provider_model_id: str, gateway: str):
        self.calls.append((provider_model_id, gateway))
        if self.runtime is None:
            raise RuntimeError("missing provider runtime")
        return self.runtime


def _catalog() -> CatalogAccessor:
    return CatalogAccessor(load_generated_model_catalog())


def _runtime(scope: ProviderScope = WORKSPACE_SCOPE):
    return ProviderRecoveryRuntime(scope, lambda: SECRET, provider=None)


def _admitter(
    tmp_path: Path,
    runtime_provider: RuntimeProvider,
    *,
    catalog: CatalogAccessor | None = None,
):
    lease = DirectoryWriterLock(tmp_path)
    lease.acquire()
    storage = PlaygroundStorage(tmp_path, writer_lock=lease)
    credentials = ProviderCredentialStore(
        tmp_path, writer_lock=storage._writer_lock, runtime_lock=storage._lock
    )
    admission = PixVerseVideoAdmission(
        output_root=tmp_path,
        writer_lock=storage._writer_lock,
        runtime_lock=storage._lock,
        runtime_provider=runtime_provider,
        credential_store=credentials,
        catalog=catalog or _catalog(),
    )
    return lease, storage, admission


def _service(
    tmp_path: Path,
    runtime_provider: RuntimeProvider,
):
    lease = DirectoryWriterLock(tmp_path)
    lease.acquire()
    storage = PlaygroundStorage(tmp_path, writer_lock=lease)
    credentials = ProviderCredentialStore(
        tmp_path, writer_lock=storage._writer_lock, runtime_lock=storage._lock
    )
    service = PlaygroundService(
        storage,
        data_dir=tmp_path,
        video_runtime_provider=runtime_provider,
        provider_credential_store=credentials,
    )
    return lease, storage, service


def _record(value: dict[str, Any] | None) -> dict[str, Any]:
    assert value is not None
    return cast(dict[str, Any], value)


def _write_image(
    tmp_path: Path,
    name: str,
    *,
    width: int = 512,
    height: int = 512,
    data: bytes | None = None,
) -> str:
    path = tmp_path / "playground" / "uploads" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if data is not None else rgb_png_bytes(name, width=width, height=height))
    return f"playground/uploads/{name}"


def _large_png_bytes() -> bytes:
    base = rgb_png_bytes("pixverse-large", width=16, height=16)
    marker = base.rfind(b"\x00\x00\x00\x00IEND")
    assert marker > 0
    payload = b"x" * (21 * 1024 * 1024)
    chunk = (
        struct.pack(">I", len(payload))
        + b"tEXt"
        + payload
        + struct.pack(">I", zlib.crc32(b"tEXt" + payload))
    )
    return base[:marker] + chunk + base[marker:]


def _generation(
    tmp_path: Path,
    *,
    request_id: str = "pixverse-request",
    mode: PlaygroundMode = PlaygroundMode.I2V,
    model_id: str = "pixverse-c1-i2v",
    input_media: list[str] | None = None,
    parameters: dict[str, object] | None = None,
    negative_prompt: str | None = None,
    batch_size: int = 2,
) -> PlaygroundGeneration:
    request = GenerateRequest(
        client_request_id=request_id,
        mode=mode,
        model_id=model_id,
        prompt="A precise PixVerse Playground request.",
        negative_prompt=negative_prompt,
        input_media=input_media,
        parameters=parameters if parameters is not None else {},
        batch_size=batch_size,
    )
    return PlaygroundGeneration(
        schema_version=1,
        id=str(uuid.uuid4()),
        client_request_id=request.client_request_id,
        request_contract_version="request-identity-v1",
        request_fingerprint="2" * 64,
        mode=request.mode,
        model_id=request.model_id,
        prompt=request.prompt,
        negative_prompt=request.negative_prompt,
        input_media=request.input_media or [],
        parameters=request.parameters or {},
        batch_size=request.batch_size or 1,
        outputs=[],
        attempts=[
            PlaygroundAttempt(
                attempt_id=f"{request.client_request_id}-{index}",
                ordinal=index,
                status="pending",
                error=None,
                provider_execution=None,
            )
            for index in range(request.batch_size or 1)
        ],
        status="pending",
        error=None,
        created_at=datetime.now(timezone.utc).isoformat(),
    )


@pytest.mark.parametrize("model_id", [*CANONICAL_BY_LEGACY, *CANONICAL_BY_LEGACY.values()])
def test_exact_pixverse_id_is_routed_to_core_even_with_wrong_video_mode(tmp_path, model_id):
    for mode in (PlaygroundMode.I2V, PlaygroundMode.R2V, PlaygroundMode.T2V, PlaygroundMode.V2V):
        assert admitted_pixverse_video_generation(mode, model_id)
        assert is_core_video_generation(_generation(tmp_path, mode=mode, model_id=model_id))
    assert not admitted_pixverse_video_generation(PlaygroundMode.I2V, "pixverse/pixverse-v6-r2v")


@pytest.mark.parametrize("model_id", [*CANONICAL_BY_LEGACY, *CANONICAL_BY_LEGACY.values()])
def test_actual_catalog_aliases_freeze_defaults_and_one_record_per_member(
    tmp_path, monkeypatch, model_id: str
):
    from src.apps.playground import pixverse_video_admission as module

    normalizer = Mock(wraps=module.normalize_generation_intent)
    monkeypatch.setattr(module, "normalize_generation_intent", normalizer)
    canonical = CANONICAL_BY_LEGACY.get(model_id, model_id)
    mode = PlaygroundMode(canonical.rsplit("#", 1)[1])
    provider = RuntimeProvider(_runtime())
    frame = _write_image(tmp_path, "first.webp", data=base64.b64decode(WEBP_240_BASE64))
    generation = _generation(tmp_path, model_id=model_id, mode=mode, input_media=[frame])
    lease, storage, admission = _admitter(tmp_path, provider)
    try:
        admission.admit(generation)
        assert normalizer.call_count == 1
        assert provider.calls == [(API_BY_CANONICAL[canonical], "dashscope_workspace")]
        records = [_record(item.provider_execution) for item in generation.attempts]
        assert len(records) == 2
        assert records[0]["attempt_id"] != records[1]["attempt_id"]
        assert records[0]["intent"]["model_id"] == canonical
        assert records[0]["provider_model_id"] == API_BY_CANONICAL[canonical]
        assert records[0]["scope"] == WORKSPACE_SCOPE.to_dict()
        expected = {"duration": 5, "resolution": "720P", "audio": False, "watermark": False}
        if mode == PlaygroundMode.R2V:
            expected["aspect_ratio"] = "16:9"
        if canonical == "pixverse/pixverse/pixverse-v6-video#i2v":
            expected["shot_type"] = "single"
        assert records[0]["intent"]["params"] == expected
        assert all(
            records[0]["intent"]["sources"][f"params.{key}"] == "catalog" for key in expected
        )
        assert records[0]["media"][0]["role"] == (
            "first_frame" if mode == PlaygroundMode.I2V else "reference_image"
        )
        assert records[0]["media"][0]["declared_media_type"] == "image/webp"
        assert records[0]["media"][0]["sha256"] == records[1]["media"][0]["sha256"]
    finally:
        storage.close()
        lease.close()


def test_r2v_order_false_zero_and_alpha_extreme_aspect_remain_intact(tmp_path):
    provider = RuntimeProvider(_runtime())
    first = _write_image(tmp_path, "wide-alpha.png", data=png_bytes(1000, 10, alpha=True))
    second = _write_image(tmp_path, "second.png")
    generation = _generation(
        tmp_path,
        mode=PlaygroundMode.R2V,
        model_id="pixverse-v5.6-r2v",
        input_media=[second, first],
        parameters={"duration": 10, "ratio": "4:3", "seed": 0, "watermark": False, "audio": False},
    )
    lease, storage, admission = _admitter(tmp_path, provider)
    try:
        admission.admit(generation)
        record = _record(generation.attempts[0].provider_execution)
        assert record["intent"]["params"] == {
            "duration": 10,
            "resolution": "720P",
            "aspect_ratio": "4:3",
            "seed": 0,
            "watermark": False,
            "audio": False,
        }
        assert [item["ordinal"] for item in record["media"]] == [0, 1]
        assert [item["role"] for item in record["media"]] == ["reference_image", "reference_image"]
        import hashlib

        assert [item["sha256"] for item in record["media"]] == [
            hashlib.sha256((tmp_path / ref).read_bytes()).hexdigest() for ref in [second, first]
        ]
    finally:
        storage.close()
        lease.close()


@pytest.mark.parametrize(
    "mode,model_id,parameters,negative",
    [
        (PlaygroundMode.T2V, "pixverse-c1-i2v", {}, None),
        (PlaygroundMode.V2V, "pixverse-c1-i2v", {}, None),
        (PlaygroundMode.R2V, "pixverse-c1-i2v", {}, None),
        (PlaygroundMode.I2V, "pixverse-c1-r2v", {}, None),
        (PlaygroundMode.I2V, "pixverse-c1-i2v", {"shot_type": "multi"}, None),
        (PlaygroundMode.I2V, "pixverse-c1-i2v", {"negative_prompt": ""}, None),
        (PlaygroundMode.I2V, "pixverse-c1-i2v", {}, "negative"),
        (PlaygroundMode.I2V, "pixverse-c1-i2v", {"seed": -1}, None),
        (PlaygroundMode.I2V, "pixverse-c1-i2v", {"seed": 2147483648}, None),
        (PlaygroundMode.R2V, "pixverse-v5.6-r2v", {"duration": 10, "resolution": "1080P"}, None),
        (PlaygroundMode.R2V, "pixverse-c1-r2v", {"ratio": "4:3", "aspectRatio": "3:4"}, None),
    ],
)
def test_invalid_request_fails_before_freeze_runtime_binding_or_receipt(
    tmp_path, monkeypatch, mode, model_id, parameters, negative
):
    freeze = Mock(side_effect=AssertionError("invalid request must not freeze media"))
    monkeypatch.setattr("src.apps.playground.pixverse_video_admission.freeze_media", freeze)
    provider = RuntimeProvider(_runtime())
    lease, storage, service = _service(tmp_path, provider)
    try:
        with pytest.raises(IntentValidationError):
            service.admit_generation(
                GenerateRequest(
                    client_request_id="invalid",
                    mode=mode,
                    model_id=model_id,
                    prompt="test",
                    input_media=["missing.png"],
                    parameters=parameters,
                    negative_prompt=negative,
                    batch_size=1,
                )
            )
        assert freeze.call_count == 0
        assert provider.calls == []
        assert storage.read_request("invalid") is None
        assert not (tmp_path / "provider_credentials.json").exists()
    finally:
        storage.close()
        lease.close()


@pytest.mark.parametrize(
    "mode,model_id,count",
    [
        (PlaygroundMode.I2V, "pixverse-c1-i2v", 0),
        (PlaygroundMode.I2V, "pixverse-c1-i2v", 2),
        (PlaygroundMode.R2V, "pixverse-c1-r2v", 0),
        (PlaygroundMode.R2V, "pixverse-c1-r2v", 8),
    ],
)
def test_invalid_media_count_rejects_before_runtime(tmp_path, mode, model_id, count):
    provider = RuntimeProvider(_runtime())
    frame = _write_image(tmp_path, "frame.png")
    lease, storage, admission = _admitter(tmp_path, provider)
    try:
        with pytest.raises(IntentValidationError):
            admission.admit(
                _generation(tmp_path, mode=mode, model_id=model_id, input_media=[frame] * count)
            )
        assert provider.calls == []
        assert not (tmp_path / "provider_credentials.json").exists()
    finally:
        storage.close()
        lease.close()


@pytest.mark.parametrize("bad_image", ["over-size", "over-edge", "wrong-content", "truncated"])
def test_actual_image_bytes_reject_before_runtime(tmp_path, bad_image):
    images = {
        "over-size": lambda: _large_png_bytes(),
        "over-edge": lambda: rgb_png_bytes("edge", width=10001, height=2),
        "wrong-content": lambda: b"GIF89a" + b"0" * 40,
        "truncated": lambda: rgb_png_bytes("cut")[:40],
    }
    frame = _write_image(tmp_path, "frame.png", data=images[bad_image]())
    provider = RuntimeProvider(_runtime())
    lease, storage, admission = _admitter(tmp_path, provider)
    try:
        with pytest.raises(IntentValidationError):
            admission.admit(_generation(tmp_path, input_media=[frame]))
        assert provider.calls == []
        assert not (tmp_path / "provider_credentials.json").exists()
    finally:
        storage.close()
        lease.close()


@pytest.mark.parametrize("scope", [
    WRONG_REGION_SCOPE,
    WRONG_HOST_SCOPE,
    replace(WORKSPACE_SCOPE, base_url="http://pixverse-space.cn-beijing.maas.aliyuncs.com"),
    replace(WORKSPACE_SCOPE, base_url="https://pixverse-space.cn-beijing.maas.aliyuncs.com:443"),
    replace(WORKSPACE_SCOPE, base_url="https://pixverse-space.cn-beijing.maas.aliyuncs.com/path"),
    replace(WORKSPACE_SCOPE, base_url="https://user@pixverse-space.cn-beijing.maas.aliyuncs.com"),
    replace(WORKSPACE_SCOPE, base_url="https://pixverse-space.cn-beijing.maas.aliyuncs.com?query=1"),
    replace(WORKSPACE_SCOPE, base_url="https://pixverse-space.cn-beijing.maas.aliyuncs.com#fragment"),
    replace(WORKSPACE_SCOPE, account_identity="foreign-account"),
    replace(WORKSPACE_SCOPE, base_url="https://pixverse-space.cn-beijing.maas.aliyuncs.com:invalid"),
    replace(WORKSPACE_SCOPE, base_url="HTTPS://pixverse-space.cn-beijing.maas.aliyuncs.com"),
    replace(WORKSPACE_SCOPE, base_url="https://pixverse-space.cn-beijing.maas.aliyuncs.com//"),
])
def test_workspace_scope_validation_precedes_secret_resolution_and_binding(tmp_path, scope):
    secret = Mock(side_effect=AssertionError("invalid scope must not read secret"))
    provider = RuntimeProvider(ProviderRecoveryRuntime(scope, secret, provider=None))
    frame = _write_image(tmp_path, "frame.png")
    lease, storage, admission = _admitter(tmp_path, provider)
    try:
        with pytest.raises(IntentValidationError):
            admission.admit(_generation(tmp_path, input_media=[frame]))
        secret.assert_not_called()
        assert not (tmp_path / "provider_credentials.json").exists()
    finally:
        storage.close()
        lease.close()


@pytest.mark.parametrize(
    "key,value", [("api_model_id", "pixverse/pixverse-v6-it2v"), ("gateway", "dashscope")]
)
def test_catalog_identity_mismatch_rejects_before_runtime(tmp_path, key, value):
    data = load_generated_model_catalog()
    data["modes"]["pixverse/pixverse-c1-video#i2v"]["runtime"]["dashscope"][key] = value
    provider = RuntimeProvider(_runtime())
    lease, storage, admission = _admitter(tmp_path, provider, catalog=CatalogAccessor(data))
    try:
        with pytest.raises(IntentValidationError):
            admission.admit(_generation(tmp_path, input_media=["missing.png"]))
        assert provider.calls == []
    finally:
        storage.close()
        lease.close()


def test_original_request_replays_after_source_removed_without_reread_rebind_or_submit(
    tmp_path, monkeypatch
):
    provider = RuntimeProvider(_runtime())
    frame = _write_image(tmp_path, "frame.png")
    lease, storage, service = _service(tmp_path, provider)
    try:
        request = GenerateRequest(
            client_request_id="pixverse-replay",
            mode=PlaygroundMode.I2V,
            model_id="pixverse-c1-i2v",
            prompt="Original prompt",
            negative_prompt=None,
            input_media=[frame],
            parameters={"seed": 0},
            batch_size=2,
        )
        first = service.admit_generation(request)
        assert first.generation is not None
        assert all(attempt.provider_execution is not None for attempt in first.generation.attempts)
        (tmp_path / frame).unlink()
        fail = Mock(side_effect=AssertionError("replay must only read original receipt"))
        monkeypatch.setattr("src.apps.playground.pixverse_video_admission.freeze_media", fail)
        monkeypatch.setattr(
            "src.apps.playground.pixverse_video_admission.get_catalog_accessor", fail
        )
        monkeypatch.setattr(ProviderCredentialStore, "bind", fail)
        replay = service.admit_generation(request)
        assert replay.generation == first.generation
        assert provider.calls == [("pixverse/pixverse-c1-it2v", "dashscope_workspace")]
        fail.assert_not_called()
    finally:
        storage.close()
        lease.close()


@pytest.mark.parametrize(
    "mode,model_id,parameters,count",
    [
        (
            PlaygroundMode.I2V,
            "pixverse/pixverse-v6-video",
            {"duration": 15, "resolution": "1080P", "shot_type": "multi", "seed": 0},
            1,
        ),
        (
            PlaygroundMode.R2V,
            "pixverse-c1-r2v",
            {"duration": 15, "resolution": "1080P", "ratio": "3:4"},
            7,
        ),
    ],
)
def test_explicit_model_features_and_media_upper_bounds_are_preserved(
    tmp_path, mode, model_id, parameters, count
):
    provider = RuntimeProvider(_runtime())
    image = _write_image(tmp_path, "edge.png", width=10000, height=1)
    lease, storage, admission = _admitter(tmp_path, provider)
    try:
        generation = _generation(
            tmp_path,
            mode=mode,
            model_id=model_id,
            input_media=[image] * count,
            parameters=parameters,
            batch_size=1,
        )
        admission.admit(generation)
        record = _record(generation.attempts[0].provider_execution)
        assert len(record["media"]) == count
        expected = {**parameters, "audio": False, "watermark": False}
        if "ratio" in expected:
            expected["aspect_ratio"] = expected.pop("ratio")
        assert record["intent"]["params"] == expected
    finally:
        storage.close()
        lease.close()


@pytest.mark.parametrize("suffix", ["", "/"])
def test_pixverse_workspace_scope_accepts_original_https_origin_and_optional_slash(tmp_path, suffix):
    scope = replace(WORKSPACE_SCOPE, base_url=WORKSPACE_SCOPE.base_url + suffix)
    provider = RuntimeProvider(_runtime(scope))
    frame = _write_image(tmp_path, "frame.png")
    lease, storage, admission = _admitter(tmp_path, provider)
    try:
        generation = _generation(tmp_path, input_media=[frame], batch_size=1)
        admission.admit(generation)
        record = _record(generation.attempts[0].provider_execution)
        assert record["scope"] == scope.to_dict()
    finally:
        storage.close()
        lease.close()
