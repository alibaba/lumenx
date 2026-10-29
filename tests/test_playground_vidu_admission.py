"""Playground Vidu durable provider admission."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from unittest.mock import Mock
from pathlib import Path
from typing import Any, cast
import struct
import uuid
import zlib

import pytest

from src.apps.playground.models import GenerateRequest, PlaygroundAttempt, PlaygroundGeneration, PlaygroundMode
from src.apps.playground.service import PlaygroundService
from src.apps.playground.storage import PlaygroundStorage
from src.apps.playground.vidu_video_admission import ViduVideoAdmission, admitted_vidu_video_generation
from src.models.provider_binding import ProviderCredentialStore
from src.models.provider_runtime import ProviderRecoveryRuntime
from src.models.provider_tasks import ProviderScope
from src.utils.generation_intent import IntentValidationError
from src.utils.json_storage import DirectoryWriterLock
from src.utils.model_catalog import CatalogAccessor
from tests.frozen_image_fixtures import rgb_png_bytes
from tests.test_wan3_image_validation import WEBP_240_BASE64, png_bytes

import base64


SECRET = "sk-test-" + "v" * 44
WORKSPACE_SCOPE = ProviderScope(
    "dashscope",
    "dashscope",
    "dashscope_workspace",
    "https://vidu-space.cn-beijing.maas.aliyuncs.com",
    "cn-beijing",
    workspace_id="vidu-space",
)
WRONG_REGION_SCOPE = ProviderScope(
    "dashscope",
    "dashscope",
    "dashscope_workspace",
    "https://vidu-space.ap-southeast-1.maas.aliyuncs.com",
    "ap-southeast-1",
    workspace_id="vidu-space",
)
WRONG_HOST_SCOPE = ProviderScope(
    "dashscope",
    "dashscope",
    "dashscope_workspace",
    "https://other-space.cn-beijing.maas.aliyuncs.com",
    "cn-beijing",
    workspace_id="vidu-space",
)

CANONICAL_BY_LEGACY = {
    "viduq3-pro-i2v": "vidu/viduq3-pro-video#i2v",
    "viduq3-turbo-i2v": "vidu/viduq3-turbo-video#i2v",
    "viduq3-pro-r2v": "vidu/viduq3-pro-video#r2v",
    "viduq3-turbo-r2v": "vidu/viduq3-turbo-video#r2v",
}
API_BY_CANONICAL = {
    "vidu/viduq3-pro-video#i2v": "vidu/viduq3-pro_img2video",
    "vidu/viduq3-turbo-video#i2v": "vidu/viduq3-turbo_img2video",
    "vidu/viduq3-pro-video#r2v": "vidu/viduq3-mix_reference2video",
    "vidu/viduq3-turbo-video#r2v": "vidu/viduq3-turbo_reference2video",
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


def _catalog(*, override_api: dict[str, str] | None = None, override_gateway: str | None = None) -> CatalogAccessor:
    modes: dict[str, dict[str, Any]] = {}
    override_api = override_api or {}
    for canonical, provider_model_id in API_BY_CANONICAL.items():
        mode = canonical.rsplit("#", 1)[1]
        is_r2v = mode == "r2v"
        params: dict[str, Any] = {
            "resolution": {"options": ["540P", "720P", "1080P"], "default": "720P"},
            "audio": {"default": False},
            "watermark": {"default": False},
            "seed": True,
        }
        if is_r2v:
            params["aspectRatio"] = {
                "options": ["16:9", "4:3", "1:1", "3:4", "9:16"],
                "default": "16:9",
            }
            if provider_model_id == "vidu/viduq3-mix_reference2video":
                params["resolution"] = {"options": ["720P", "1080P"], "default": "720P"}
        modes[canonical] = {
            "id": canonical,
            "mode": mode,
            "capabilities": [mode],
            "duration": {"type": "slider", "min": 1, "max": 16, "step": 1, "default": 5},
            "params": params,
            "runtime": {
                "dashscope": {
                    "gateway": override_gateway or "dashscope_workspace",
                    "api_model_id": override_api.get(canonical, provider_model_id),
                }
            },
        }
    return CatalogAccessor({
        "modes": modes,
        "compat": {"legacy_model_ids": CANONICAL_BY_LEGACY},
        "defaults": {"canonical_model_settings": {}},
    })


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
    credentials = ProviderCredentialStore(tmp_path, writer_lock=storage._writer_lock, runtime_lock=storage._lock)
    admission = ViduVideoAdmission(
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
    credentials = ProviderCredentialStore(tmp_path, writer_lock=storage._writer_lock, runtime_lock=storage._lock)
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
    base = rgb_png_bytes("vidu-large", width=16, height=16)
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
    request_id: str = "vidu-request",
    mode: PlaygroundMode = PlaygroundMode.I2V,
    model_id: str = "viduq3-pro-i2v",
    input_media: list[str] | None = None,
    parameters: dict[str, object] | None = None,
    negative_prompt: str | None = None,
    batch_size: int = 2,
) -> PlaygroundGeneration:
    request = GenerateRequest(
        client_request_id=request_id,
        mode=mode,
        model_id=model_id,
        prompt="A precise Vidu Playground request.",
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
def test_vidu_predicate_routes_eight_known_ids_even_when_mode_is_wrong(model_id: str) -> None:
    assert admitted_vidu_video_generation(PlaygroundMode.I2V, model_id)
    assert admitted_vidu_video_generation(PlaygroundMode.R2V, model_id)
    assert admitted_vidu_video_generation(PlaygroundMode.T2V, model_id)
    assert not admitted_vidu_video_generation(PlaygroundMode.I2V, "vidu/viduq3-pro_reference2video")


def test_vidu_i2v_admission_freezes_one_first_frame_with_exact_api_model_and_defaults(tmp_path: Path) -> None:
    provider = RuntimeProvider(_runtime())
    frame = _write_image(tmp_path, "first.webp", data=base64.b64decode(WEBP_240_BASE64))
    generation = _generation(tmp_path, input_media=[frame])
    lease, storage, admission = _admitter(tmp_path, provider)
    try:
        admission.admit(generation)

        assert provider.calls == [("vidu/viduq3-pro_img2video", "dashscope_workspace")]
        records = [_record(attempt.provider_execution) for attempt in generation.attempts]
        assert records[0]["provider_model_id"] == "vidu/viduq3-pro_img2video"
        assert records[0]["scope"] == WORKSPACE_SCOPE.to_dict()
        assert records[0]["intent"]["model_id"] == "vidu/viduq3-pro-video#i2v"
        assert records[0]["intent"]["mode"] == "i2v"
        assert records[0]["intent"]["params"] == {
            "audio": False,
            "duration": 5,
            "resolution": "720P",
            "watermark": False,
        }
        assert records[0]["media"][0]["role"] == "first_frame"
        assert records[0]["media"][0]["ordinal"] == 0
        assert records[0]["media"][0]["declared_media_type"] == "image/webp"
        assert records[1]["media"][0]["sha256"] == records[0]["media"][0]["sha256"]
    finally:
        storage.close()
        lease.close()


def test_vidu_i2v_preserves_explicit_false_and_seed_zero(tmp_path: Path) -> None:
    provider = RuntimeProvider(_runtime())
    frame = _write_image(tmp_path, "first.png")
    generation = _generation(
        tmp_path,
        model_id="vidu/viduq3-turbo-video#i2v",
        input_media=[frame],
        parameters={"duration": 16, "resolution": "1080P", "audio": False, "watermark": False, "seed": 0},
        batch_size=1,
    )
    lease, storage, admission = _admitter(tmp_path, provider)
    try:
        admission.admit(generation)

        record = _record(generation.attempts[0].provider_execution)
        assert provider.calls == [("vidu/viduq3-turbo_img2video", "dashscope_workspace")]
        assert record["intent"]["params"] == {
            "audio": False,
            "duration": 16,
            "resolution": "1080P",
            "seed": 0,
            "watermark": False,
        }
    finally:
        storage.close()
        lease.close()


def test_vidu_r2v_admission_preserves_reference_order_and_mix_size(tmp_path: Path) -> None:
    provider = RuntimeProvider(_runtime())
    first = _write_image(tmp_path, "ref-0.png", data=rgb_png_bytes("ref-0", width=600, height=600))
    second = _write_image(tmp_path, "ref-1.png", data=png_bytes(600, 600, alpha=True))
    generation = _generation(
        tmp_path,
        mode=PlaygroundMode.R2V,
        model_id="viduq3-pro-r2v",
        input_media=[first, second],
        parameters={"duration": 5, "resolution": "720P", "audio": True, "watermark": False, "ratio": "9:16"},
        batch_size=1,
    )
    lease, storage, admission = _admitter(tmp_path, provider)
    try:
        admission.admit(generation)

        record = _record(generation.attempts[0].provider_execution)
        assert provider.calls == [("vidu/viduq3-mix_reference2video", "dashscope_workspace")]
        assert record["intent"]["model_id"] == "vidu/viduq3-pro-video#r2v"
        assert record["intent"]["params"] == {
            "aspect_ratio": "9:16",
            "audio": True,
            "duration": 5,
            "resolution": "720P",
            "watermark": False,
        }
        assert [(item["role"], item["ordinal"]) for item in record["media"]] == [
            ("reference_image", 0),
            ("reference_image", 1),
        ]
        assert record["media"][0]["sha256"] != record["media"][1]["sha256"]
    finally:
        storage.close()
        lease.close()


def test_vidu_r2v_admission_uses_production_catalog_aspect_ratio_defaults_and_explicit_aspect_ratio(
    tmp_path: Path,
) -> None:
    provider = RuntimeProvider(_runtime())
    first = _write_image(tmp_path, "prod-ref-0.png", data=rgb_png_bytes("prod-ref-0", width=600, height=600))
    second = _write_image(tmp_path, "prod-ref-1.png", data=rgb_png_bytes("prod-ref-1", width=600, height=600))
    lease = DirectoryWriterLock(tmp_path)
    lease.acquire()
    storage = PlaygroundStorage(tmp_path, writer_lock=lease)
    credentials = ProviderCredentialStore(tmp_path, writer_lock=storage._writer_lock, runtime_lock=storage._lock)
    admission = ViduVideoAdmission(
        output_root=tmp_path,
        writer_lock=storage._writer_lock,
        runtime_lock=storage._lock,
        runtime_provider=provider,
        credential_store=credentials,
    )
    try:
        default_generation = _generation(
            tmp_path,
            request_id="vidu-prod-default-r2v",
            mode=PlaygroundMode.R2V,
            model_id="viduq3-pro-r2v",
            input_media=[first],
            parameters={},
            batch_size=1,
        )
        explicit_generation = _generation(
            tmp_path,
            request_id="vidu-prod-explicit-r2v",
            mode=PlaygroundMode.R2V,
            model_id="viduq3-turbo-r2v",
            input_media=[first, second],
            parameters={"aspect_ratio": "4:3", "resolution": "720P"},
            batch_size=1,
        )

        admission.admit(default_generation)
        admission.admit(explicit_generation)

        default_record = _record(default_generation.attempts[0].provider_execution)
        explicit_record = _record(explicit_generation.attempts[0].provider_execution)
        assert default_record["provider_model_id"] == "vidu/viduq3-mix_reference2video"
        assert default_record["intent"]["params"]["aspect_ratio"] == "16:9"
        assert default_record["intent"]["params"]["resolution"] == "720P"
        assert explicit_record["provider_model_id"] == "vidu/viduq3-turbo_reference2video"
        assert explicit_record["intent"]["params"]["aspect_ratio"] == "4:3"
        assert explicit_record["intent"]["params"]["resolution"] == "720P"
    finally:
        storage.close()
        lease.close()


def test_vidu_admission_accepts_over_20mb_png_within_50mb_policy(tmp_path: Path) -> None:
    provider = RuntimeProvider(_runtime())
    frame = _write_image(tmp_path, "large.png", data=_large_png_bytes())
    generation = _generation(tmp_path, input_media=[frame], batch_size=1)
    lease, storage, admission = _admitter(tmp_path, provider)
    try:
        admission.admit(generation)

        record = _record(generation.attempts[0].provider_execution)
        assert record["media"][0]["size_bytes"] > 20 * 1024 * 1024
        assert record["media"][0]["size_bytes"] < 50 * 1024 * 1024
    finally:
        storage.close()
        lease.close()


@pytest.mark.parametrize(
    "generation",
    [
        lambda tmp_path: _generation(tmp_path, mode=PlaygroundMode.T2V, model_id="viduq3-pro-i2v"),
        lambda tmp_path: _generation(tmp_path, mode=PlaygroundMode.I2V, model_id="viduq3-pro-r2v"),
        lambda tmp_path: _generation(tmp_path, input_media=[], parameters={"duration": 5}),
        lambda tmp_path: _generation(tmp_path, input_media=["missing.png"]),
    ],
)
def test_vidu_invalid_mode_or_media_fails_before_runtime_credentials_or_receipt(tmp_path: Path, generation) -> None:
    provider = RuntimeProvider(_runtime())
    lease, storage, admission = _admitter(tmp_path, provider)
    try:
        with pytest.raises(IntentValidationError):
            admission.admit(generation(tmp_path))
        assert provider.calls == []
        assert not (tmp_path / "provider_credentials.json").exists()
    finally:
        storage.close()
        lease.close()


@pytest.mark.parametrize(
    "parameters",
    [
        {"duration": 5, "negative_prompt": "unsupported"},
        {"duration": 5, "movement_amplitude": "auto"},
        {"duration": 5, "viduAudio": True},
        {"duration": 5, "resolution": "540P"},
        {"duration": 5, "ratio": "16:9", "aspect_ratio": "4:3"},
    ],
)
def test_vidu_rejects_unsupported_params_and_mix_540p_before_runtime(tmp_path: Path, parameters) -> None:
    provider = RuntimeProvider(_runtime())
    ref = _write_image(tmp_path, "ref.png")
    generation = _generation(
        tmp_path,
        mode=PlaygroundMode.R2V,
        model_id="viduq3-pro-r2v",
        input_media=[ref],
        parameters=parameters,
        negative_prompt="bad anatomy" if "negative_prompt" in parameters else None,
    )
    lease, storage, admission = _admitter(tmp_path, provider)
    try:
        with pytest.raises(IntentValidationError):
            admission.admit(generation)
        assert provider.calls == []
        assert not (tmp_path / "provider_credentials.json").exists()
    finally:
        storage.close()
        lease.close()


def test_vidu_rejects_invalid_image_probe_before_runtime(tmp_path: Path) -> None:
    provider = RuntimeProvider(_runtime())
    alpha_bad_ratio = _write_image(tmp_path, "bad-ratio.png", data=png_bytes(100, 500, alpha=True))
    lease, storage, admission = _admitter(tmp_path, provider)
    try:
        with pytest.raises(IntentValidationError):
            admission.admit(_generation(tmp_path, input_media=[alpha_bad_ratio]))
        assert provider.calls == []
        assert not (tmp_path / "provider_credentials.json").exists()
    finally:
        storage.close()
        lease.close()


def test_vidu_runtime_scope_is_validated_before_credentials_are_bound(tmp_path: Path) -> None:
    provider = RuntimeProvider(_runtime(WRONG_REGION_SCOPE))
    frame = _write_image(tmp_path, "first.png")
    lease, storage, admission = _admitter(tmp_path, provider)
    try:
        with pytest.raises(IntentValidationError):
            admission.admit(_generation(tmp_path, input_media=[frame]))
        assert provider.calls == [("vidu/viduq3-pro_img2video", "dashscope_workspace")]
        assert not (tmp_path / "provider_credentials.json").exists()
    finally:
        storage.close()
        lease.close()


def test_vidu_runtime_scope_rejects_foreign_workspace_host_before_credentials_are_bound(
    tmp_path: Path,
) -> None:
    provider = RuntimeProvider(_runtime(WRONG_HOST_SCOPE))
    frame = _write_image(tmp_path, "first.png")
    lease, storage, admission = _admitter(tmp_path, provider)
    try:
        with pytest.raises(IntentValidationError):
            admission.admit(_generation(tmp_path, input_media=[frame]))
        assert provider.calls == [("vidu/viduq3-pro_img2video", "dashscope_workspace")]
        assert not (tmp_path / "provider_credentials.json").exists()
    finally:
        storage.close()
        lease.close()


def test_vidu_catalog_api_id_must_match_canonical_contract(tmp_path: Path) -> None:
    provider = RuntimeProvider(_runtime())
    frame = _write_image(tmp_path, "first.png")
    bad_catalog = _catalog(
        override_api={"vidu/viduq3-pro-video#i2v": "vidu/viduq3-turbo_img2video"}
    )
    lease, storage, admission = _admitter(tmp_path, provider, catalog=bad_catalog)
    try:
        with pytest.raises(IntentValidationError):
            admission.admit(_generation(tmp_path, input_media=[frame]))
        assert provider.calls == []
    finally:
        storage.close()
        lease.close()


def test_vidu_service_replay_returns_existing_receipt_without_refreezing_or_new_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = RuntimeProvider(_runtime())
    frame = _write_image(tmp_path, "first.png")
    monkeypatch.setattr(
        "src.apps.playground.vidu_video_admission.get_catalog_accessor",
        lambda: _catalog(),
    )
    lease, storage, service = _service(tmp_path, provider)
    try:
        request = GenerateRequest(
            client_request_id="vidu-replay",
            mode=PlaygroundMode.I2V,
            model_id="viduq3-pro-i2v",
            prompt="A precise Vidu Playground request.",
            negative_prompt=None,
            input_media=[frame],
            parameters={"duration": 5},
            batch_size=1,
        )
        first = service.admit_generation(request)
        assert first.generation is not None
        original_record = _record(first.generation.attempts[0].provider_execution)
        original_credential_ref = original_record["credential_ref"]
        original_sha = original_record["media"][0]["sha256"]

        source = tmp_path / frame
        source.write_bytes(rgb_png_bytes("mutated", width=512, height=512))
        replay = service.admit_generation(request)
        assert replay.generation is not None

        replay_record = _record(replay.generation.attempts[0].provider_execution)
        assert provider.calls == [("vidu/viduq3-pro_img2video", "dashscope_workspace")]
        assert replay_record["credential_ref"] == original_credential_ref
        assert replay_record["media"][0]["sha256"] == original_sha
    finally:
        storage.close()
        lease.close()


@pytest.mark.parametrize("scope", [
    replace(WORKSPACE_SCOPE, base_url="http://vidu-space.cn-beijing.maas.aliyuncs.com"),
    replace(WORKSPACE_SCOPE, base_url="https://vidu-space.cn-beijing.maas.aliyuncs.com:443"),
    replace(WORKSPACE_SCOPE, base_url="https://vidu-space.cn-beijing.maas.aliyuncs.com/path"),
    replace(WORKSPACE_SCOPE, base_url="https://user@vidu-space.cn-beijing.maas.aliyuncs.com"),
    replace(WORKSPACE_SCOPE, base_url="https://vidu-space.cn-beijing.maas.aliyuncs.com?query=1"),
    replace(WORKSPACE_SCOPE, base_url="https://vidu-space.cn-beijing.maas.aliyuncs.com#fragment"),
    replace(WORKSPACE_SCOPE, account_identity="foreign-account"),
    replace(WORKSPACE_SCOPE, base_url="https://vidu-space.cn-beijing.maas.aliyuncs.com:invalid"),
    replace(WORKSPACE_SCOPE, base_url="HTTPS://vidu-space.cn-beijing.maas.aliyuncs.com"),
    replace(WORKSPACE_SCOPE, base_url="https://vidu-space.cn-beijing.maas.aliyuncs.com//"),
])
def test_vidu_workspace_scope_rejects_invalid_origin_before_reading_secret(tmp_path, scope):
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


@pytest.mark.parametrize("suffix", ["", "/"])
def test_vidu_workspace_scope_accepts_original_https_origin_and_optional_slash(tmp_path, suffix):
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
