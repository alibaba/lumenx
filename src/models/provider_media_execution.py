"""Private sync media execution and explicit async image operations.

This module consumes frozen schema-v2 sync or opt-in schema-v3 image records plus
an in-memory runtime credential authority. It writes only private scratch
artifacts. Product ledgers, public managed outputs, current catalog defaults,
and voice registries are out of scope for this layer.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import uuid
from typing import Callable, cast
from urllib.parse import urlsplit

from ..audio.tts import TTSProcessor
from ..utils.media_probe import (
    AV_FORMATS, IMAGE_FORMATS, FrozenMediaMetadata, MediaProbeError,
    probe_frozen_image, probe_image_file, probe_media_file,
)
from ..utils.system_check import get_ffmpeg_path
from ..utils.media_versions import MediaVersion, read_verified_media
from .mulerouter import MuleRouterImageModel
from .provider_binding import (
    ResolvedMuleRunProfileCredential,
    ResolvedMuleRunTokenCredential,
)
from .provider_execution import ProviderExecutionRecord
from .provider_media_intents import (
    image_intent_from_async_record_for_submit, image_intent_from_sync_record, prepared_tts_from_sync_record,
)
from .provider_media_runtime import MediaRuntimeAuthority, MediaRuntimeAuthorityError, validate_media_runtime_authority
from .provider_tasks import (
    ProviderArtifact, ProviderHandle, ProviderImageQueryContract, ProviderObservation,
    PreparedMedia, ResolvedCredential, validate_fetch_binding, validate_query_binding,
)
from .provider_recovery import (
    image_query_handle_from_record, observation_from_record,
    require_matching_artifact_proof, require_matching_handle,
)
from .bound_image_http import ImageReceiptWriter, download_private, validate_receipt_writer
from .wan27_image_http import query_bound_wan27_image, submit_bound_wan27_image
from .image import WanxImageModel
from .wan27_image_contract import (
    WAN27_IMAGE_CONTRACT_VERSION, WAN27_IMAGE_EDIT_CONTRACT_VERSION,
    WAN27_IMAGE_REFERENCE_MAX_BYTES, validate_wan27_image_reference,
)
from .qwen20_image_contract import (
    QWEN20_IMAGE_EDIT_CONTRACT_VERSION, QWEN20_IMAGE_CONTRACT_VERSIONS, QWEN20_IMAGE_REFERENCE_MAX_BYTES,
)
from .gpt_image2_edit_contract import GPT_IMAGE2_EDIT_CONTRACT_VERSIONS


_SAFE_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")
_IMAGE_REF_EXTENSIONS = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/bmp": ".bmp",
}


class ProviderMediaExecutionError(RuntimeError):
    """A sync media artifact cannot be executed or normalized safely."""


@dataclass(frozen=True)
class SyncArtifactBounds:
    image_max_bytes: int
    audio_max_bytes: int
    transcode_timeout_seconds: int = 30
    image_max_pixels: int = 16_777_216
    audio_max_duration_seconds: float = 600.0
    audio_duration_tolerance_seconds: float = 0.25

    def __post_init__(self) -> None:
        for name in ("image_max_bytes", "audio_max_bytes", "transcode_timeout_seconds", "image_max_pixels"):
            value = getattr(self, name)
            if type(value) is not int or value <= 0:
                raise ProviderMediaExecutionError(f"{name} must be a positive integer")
        for name in ("audio_max_duration_seconds", "audio_duration_tolerance_seconds"):
            value = getattr(self, name)
            if type(value) not in {int, float} or not math.isfinite(float(value)) or value <= 0:
                raise ProviderMediaExecutionError(f"{name} must be a positive finite number")


@dataclass(frozen=True)
class PrivateSyncArtifact:
    path: Path
    sha256: str
    size_bytes: int
    encoding: str
    media_kind: str


ImageModelFactory = Callable[..., object]
TTSProcessorFactory = Callable[..., object]


def execute_sync_artifact_to_private_file(
    record: ProviderExecutionRecord,
    authority: MediaRuntimeAuthority,
    *,
    output_root: str | Path,
    scratch_dir: str | Path,
    bounds: SyncArtifactBounds | None = None,
    max_bytes: int | None = None,
    image_model_factory: ImageModelFactory | None = None,
    tts_processor_factory: TTSProcessorFactory | None = None,
) -> PrivateSyncArtifact:
    """Execute a durable submitting sync artifact into a private normalized scratch file."""
    if not isinstance(record, ProviderExecutionRecord) or record.schema_version != 2:
        raise ProviderMediaExecutionError("sync execution requires a schema-v2 provider record")
    if record.phase != "submitting" or record.prepared_artifact is not None or record.artifact is not None:
        raise ProviderMediaExecutionError("sync execution requires a clean submitting provider record")
    bounds = _coerce_bounds(bounds=bounds, max_bytes=max_bytes)
    _validate_authority_matches_record(record, authority)
    scratch = _prepare_scratch_dir(scratch_dir)
    created: list[Path] = []
    try:
        if record.media_kind == "image":
            return _execute_image_record(
                record,
                authority,
                output_root=Path(output_root),
                scratch=scratch,
                bounds=bounds,
                image_model_factory=image_model_factory,
                created=created,
            )
        if record.media_kind == "audio":
            return _execute_audio_record(
                record,
                authority,
                scratch=scratch,
                bounds=bounds,
                tts_processor_factory=tts_processor_factory,
                created=created,
            )
        raise ProviderMediaExecutionError("unsupported sync media kind")
    except BaseException:
        _cleanup(created)
        raise


def submit_async_image(
    record: ProviderExecutionRecord,
    authority: MediaRuntimeAuthority,
    *,
    output_root: str | Path,
    scratch_dir: str | Path,
    on_submitted: ImageReceiptWriter,
    bounds: SyncArtifactBounds | None = None,
    max_bytes: int | None = None,
) -> ProviderHandle:
    """Submit one new image attempt; the caller owns its original paid claim.

    Only this operation materializes frozen generation inputs. It cannot query
    or publish. The caller must durably save the actual handle before returning
    None; callback failures remain failures and never authorize a new POST.
    """
    validate_receipt_writer(on_submitted)
    if not isinstance(record, ProviderExecutionRecord) or record.schema_version != 3 or record.phase != "submitting":
        raise ProviderMediaExecutionError("async image submission requires a clean schema-v3 submitting record")
    record = ProviderExecutionRecord.model_validate(record.model_dump(mode="json"))
    if _SAFE_ID.fullmatch(record.attempt_id) is None:
        raise ProviderMediaExecutionError("invalid async image attempt id")
    intent = image_intent_from_async_record_for_submit(record)
    bounds = _coerce_bounds(bounds=bounds, max_bytes=max_bytes)
    _validate_authority_matches_record(record, authority)
    credential = _resolved_credential(authority)
    scratch = _prepare_scratch_dir(scratch_dir)
    created: list[Path] = []

    def save_original(handle: ProviderHandle) -> object:
        require_matching_handle(record, handle)
        return on_submitted(handle)

    try:
        params = dict(intent.params)
        version = record.intent.get("intent_contract_version")
        if version == WAN27_IMAGE_EDIT_CONTRACT_VERSION:
            references = []
            limit = min(bounds.image_max_bytes, WAN27_IMAGE_REFERENCE_MAX_BYTES)
            for raw in record.media:
                media_version = MediaVersion(**raw)
                data = read_verified_media(media_version, output_root=Path(output_root), max_bytes=limit)
                metadata = probe_frozen_image(media_version, data, max_bytes=limit)
                validate_wan27_image_reference(metadata, len(data))
                references.append(PreparedMedia.from_verified_bytes(media_version, data, max_bytes=limit).url)
            return submit_bound_wan27_image(
                intent.prompt, credential, model_name=record.provider_model_id,
                on_submitted=save_original, image_contract_version=version, reference_images=references, **params,
            )
        references = _materialize_image_references(
            record, output_root=Path(output_root), scratch=scratch, bounds=bounds, created=created,
        )
        if version == WAN27_IMAGE_CONTRACT_VERSION:
            return submit_bound_wan27_image(
                intent.prompt, credential, model_name=record.provider_model_id,
                on_submitted=save_original, **params,
            )
        # Keep this opt-in route explicit even if later material profiles grow.
        if (
            record.provider_model_id != "gpt-image-2"
            or authority.scope.provider != "mulerouter"
            or authority.scope.backend != "mulerouter"
            or authority.scope.gateway != "mulerouter_http"
        ):
            raise ProviderMediaExecutionError("unsupported explicit async image submission")
        kwargs = _mulerouter_image_kwargs(intent, params, references, version)
        model = MuleRouterImageModel({"output_root": str(scratch)}, env={})
        return model.submit_with_bound_http(intent.prompt, credential, on_submitted=save_original, **kwargs)
    finally:
        _cleanup(created)


def query_async_image(
    handle: ProviderHandle, authority: MediaRuntimeAuthority,
) -> ProviderObservation:
    """Observe one saved image handle without reconstructing generation input."""
    if not isinstance(handle, ProviderHandle):
        raise ProviderMediaExecutionError("async image query requires an explicit image handle")
    handle = ProviderHandle.from_dict(handle.to_dict())
    validate_media_runtime_authority(authority)
    if authority.media_kind != "image" or authority.provider_model_id != handle.provider_model_id:
        raise ProviderMediaExecutionError("image query authority does not match original handle")
    credential = _resolved_credential(authority)
    validate_query_binding(handle, credential, media_kind="image")
    if handle.query_contract is ProviderImageQueryContract.WAN27_IMAGE:
        return query_bound_wan27_image(handle, credential=credential)
    if handle.query_contract in {
        ProviderImageQueryContract.GPT_IMAGE2_GENERATION, ProviderImageQueryContract.GPT_IMAGE2_EDIT,
    }:
        return MuleRouterImageModel.query_with_bound_http(handle, credential=credential)
    raise ProviderMediaExecutionError("unsupported explicit image query contract")


def fetch_async_image_to_private_file(
    record: ProviderExecutionRecord,
    authority: MediaRuntimeAuthority,
    *,
    scratch_dir: str | Path,
    bounds: SyncArtifactBounds | None = None,
    max_bytes: int | None = None,
) -> PrivateSyncArtifact:
    """Fetch one original successful observation into a private normalized PNG.

    No generation inputs, query, POST or managed publication are involved.
    Existing prepared proof is immutable. The caller retains the owned writer
    lock/storage guard and durable proof-before-publication responsibilities.
    """
    if not isinstance(record, ProviderExecutionRecord) or record.schema_version != 3 or record.phase != "observed":
        raise ProviderMediaExecutionError("async image fetch requires a schema-v3 observed record")
    record = ProviderExecutionRecord.model_validate(record.model_dump(mode="json"))
    handle = image_query_handle_from_record(record)
    observation = observation_from_record(record)
    validate_fetch_binding(handle, observation, media_kind="image")
    image_url = observation.image_url
    if not isinstance(image_url, str) or urlsplit(image_url).scheme != "https":
        raise ProviderMediaExecutionError("async image result requires HTTPS")
    _validate_authority_matches_record(record, authority)
    bounds = _coerce_bounds(bounds=bounds, max_bytes=max_bytes)
    scratch = _prepare_scratch_dir(scratch_dir)
    provider_path = _private_path(scratch, record.attempt_id, ".provider-image.jpg")
    final_path = _private_path(scratch, record.attempt_id, ".png")
    created = [provider_path, final_path]
    try:
        download_private(image_url, provider_path, bounds.image_max_bytes)
        result = _normalize_png(provider_path, final_path, scratch=scratch, bounds=bounds)
        # Validate the normalized bytes against original attempt/output/handle
        # and any existing proof, without mutating or publishing the record.
        assert isinstance(record.output_ref, str)
        artifact = ProviderArtifact(handle, record.output_ref, result.sha256, result.size_bytes)
        require_matching_artifact_proof(record, artifact, record.prepared_artifact)
    except BaseException:
        _cleanup(created)
        raise
    _cleanup([provider_path])
    return result


def _coerce_bounds(*, bounds: SyncArtifactBounds | None, max_bytes: int | None) -> SyncArtifactBounds:
    if bounds is not None and max_bytes is not None:
        raise ProviderMediaExecutionError("sync execution accepts either bounds or max_bytes")
    if bounds is not None:
        if not isinstance(bounds, SyncArtifactBounds):
            raise ProviderMediaExecutionError("sync execution requires explicit bounds")
        return bounds
    if max_bytes is None:
        raise ProviderMediaExecutionError("sync execution requires explicit bounds")
    if type(max_bytes) is not int or max_bytes <= 0:
        raise ProviderMediaExecutionError("max_bytes must be a positive integer")
    return SyncArtifactBounds(image_max_bytes=max_bytes, audio_max_bytes=max_bytes)


def _validate_authority_matches_record(record: ProviderExecutionRecord, authority: MediaRuntimeAuthority) -> None:
    try:
        validate_media_runtime_authority(authority)
    except MediaRuntimeAuthorityError as exc:
        raise ValueError("runtime authority is invalid") from exc
    if (
        authority.media_kind != record.media_kind
        or authority.provider_model_id != record.provider_model_id
        or authority.scope.to_dict() != record.scope
        or authority.credential_ref != record.credential_ref
    ):
        raise ValueError("runtime authority does not match provider record")


def _prepare_scratch_dir(scratch_dir: str | Path) -> Path:
    scratch = Path(scratch_dir)
    if scratch.exists() and scratch.is_symlink():
        raise ProviderMediaExecutionError("scratch directory must not be a symlink")
    scratch.mkdir(parents=True, exist_ok=True)
    if not scratch.is_dir():
        raise ProviderMediaExecutionError("scratch path must be a directory")
    try:
        os.chmod(scratch, 0o700)
    except OSError:
        pass
    return scratch.resolve()


def _private_path(scratch: Path, attempt_id: str, suffix: str) -> Path:
    if not isinstance(attempt_id, str) or _SAFE_ID.fullmatch(attempt_id) is None:
        raise ProviderMediaExecutionError("invalid sync artifact attempt id")
    return scratch / f"{attempt_id}-{uuid.uuid4().hex}{suffix}"


def _execute_image_record(
    record: ProviderExecutionRecord,
    authority: MediaRuntimeAuthority,
    *,
    output_root: Path,
    scratch: Path,
    bounds: SyncArtifactBounds,
    image_model_factory: ImageModelFactory | None,
    created: list[Path],
) -> PrivateSyncArtifact:
    intent = image_intent_from_sync_record(record)
    if record.intent.get("intent_contract_version") == WAN27_IMAGE_EDIT_CONTRACT_VERSION:
        raise ProviderMediaExecutionError("Wan Edit requires the async image execution path")
    params = dict(intent.params)
    version = record.intent.get("intent_contract_version")
    if version == QWEN20_IMAGE_EDIT_CONTRACT_VERSION:
        media_version = MediaVersion(**record.media[0])
        limit = min(bounds.image_max_bytes, QWEN20_IMAGE_REFERENCE_MAX_BYTES)
        data = read_verified_media(media_version, output_root=output_root, max_bytes=limit)
        probe_frozen_image(media_version, data, max_bytes=limit)
        params["reference_image"] = PreparedMedia.from_verified_bytes(media_version, data, max_bytes=limit).url
        ref_paths = []
    else:
        ref_paths = _materialize_image_references(record, output_root=output_root, scratch=scratch, bounds=bounds, created=created)
    provider_path = _private_path(scratch, record.attempt_id, ".provider-image.jpg")
    final_path = _private_path(scratch, record.attempt_id, ".png")
    created.extend([provider_path, final_path])
    factory = image_model_factory or _default_image_model_factory(authority)
    if version in (WAN27_IMAGE_CONTRACT_VERSION, *QWEN20_IMAGE_CONTRACT_VERSIONS):
        # The frozen contract was validated before any model or reference work.
        model = factory({"output_root": str(scratch)}, env={})
        if version in QWEN20_IMAGE_CONTRACT_VERSIONS:
            params["image_contract_version"] = version
        cast(WanxImageModel, model).generate_with_bound_http(
            intent.prompt, str(provider_path), _resolved_credential(authority),
            model_name=authority.provider_model_id, max_bytes=bounds.image_max_bytes, **params,
        )
    elif authority.scope.provider == "dashscope":
        credential = _resolved_credential(authority)
        model = factory(
            {"output_root": str(scratch if ref_paths else output_root)},
            env={"DASHSCOPE_API_KEY": credential.secret, "DASHSCOPE_BASE_URL": authority.scope.base_url.rstrip("/")},
        )
        kwargs = _dashscope_image_kwargs(intent, params, ref_paths, authority.provider_model_id)
        cast(WanxImageModel, model).generate(intent.prompt, str(provider_path), **kwargs)
    elif authority.scope.provider == "mulerouter":
        kwargs = _mulerouter_image_kwargs(intent, params, ref_paths, version)
        model = factory({"output_root": str(scratch if ref_paths else output_root)}, env={})
        if authority.scope.backend == "mulerouter":
            cast(MuleRouterImageModel, model).generate_with_bound_http(
                intent.prompt, str(provider_path), _resolved_credential(authority), **kwargs,
            )
        elif authority.scope.backend == "mulerun_cli":
            credential = authority.credential
            if not isinstance(credential, (ResolvedMuleRunTokenCredential, ResolvedMuleRunProfileCredential)):
                raise ProviderMediaExecutionError("unsupported MuleRun credential")
            cast(MuleRouterImageModel, model).generate_with_bound_cli(intent.prompt, str(provider_path), credential, **kwargs)
        else:
            raise ProviderMediaExecutionError("unsupported MuleRouter runtime")
    else:
        raise ProviderMediaExecutionError("unsupported image runtime")
    return _normalize_png(provider_path, final_path, scratch=scratch, bounds=bounds)


def _execute_audio_record(
    record: ProviderExecutionRecord,
    authority: MediaRuntimeAuthority,
    *,
    scratch: Path,
    bounds: SyncArtifactBounds,
    tts_processor_factory: TTSProcessorFactory | None,
    created: list[Path],
) -> PrivateSyncArtifact:
    prepared = prepared_tts_from_sync_record(record)
    credential = _resolved_credential(authority)
    provider_path = _private_path(scratch, record.attempt_id, ".provider-audio.wav")
    final_path = _private_path(scratch, record.attempt_id, ".mp3")
    created.extend([provider_path, final_path])
    factory = tts_processor_factory or TTSProcessor
    processor = factory(
        api_key=credential.secret,
        model=prepared.model,
        voice=prepared.voice,
        env={"DASHSCOPE_API_KEY": credential.secret, "DASHSCOPE_BASE_URL": authority.scope.base_url.rstrip("/")},
    )
    cast(TTSProcessor, processor).execute(prepared, str(provider_path))
    return _normalize_mp3(provider_path, final_path, scratch=scratch, bounds=bounds)


def _resolved_credential(authority: MediaRuntimeAuthority) -> ResolvedCredential:
    credential = authority.credential
    if not isinstance(credential, ResolvedCredential):
        raise ProviderMediaExecutionError("runtime authority requires a resolved secret credential")
    return credential


def _default_image_model_factory(authority: MediaRuntimeAuthority) -> ImageModelFactory:
    if authority.scope.provider == "dashscope":
        return WanxImageModel
    if authority.scope.provider == "mulerouter":
        return MuleRouterImageModel
    raise ProviderMediaExecutionError("unsupported image runtime")


def _dashscope_image_kwargs(intent, params: dict, ref_paths: list[str], provider_model_id: str) -> dict:
    allowed = {"size", "seed", "negative_prompt", "prompt_extend", "watermark"}
    _reject_unknown_params(params, allowed)
    kwargs: dict[str, object] = {"model_name": provider_model_id}
    if ref_paths:
        kwargs["ref_image_paths"] = ref_paths
    for key in allowed:
        if key in params:
            kwargs[key] = params[key]
    if intent.mode == "t2i" and ref_paths:
        raise ProviderMediaExecutionError("t2i image execution cannot include references")
    if intent.mode == "i2i" and not ref_paths:
        raise ProviderMediaExecutionError("i2i image execution requires frozen references")
    return kwargs


def _mulerouter_image_kwargs(intent, params: dict, ref_paths: list[str], image_contract_version: object) -> dict:
    marked_edit = image_contract_version in GPT_IMAGE2_EDIT_CONTRACT_VERSIONS
    allowed = {"size"} if marked_edit else {"size", "quality"}
    _reject_unknown_params(params, allowed)
    kwargs = {}
    if marked_edit:
        kwargs["image_contract_version"] = image_contract_version
    if ref_paths:
        kwargs["ref_image_paths"] = ref_paths
    for key in allowed:
        if key in params:
            kwargs[key] = params[key]
    if intent.mode == "t2i" and ref_paths:
        raise ProviderMediaExecutionError("t2i image execution cannot include references")
    if intent.mode == "i2i" and not ref_paths:
        raise ProviderMediaExecutionError("i2i image execution requires frozen references")
    return kwargs


def _reject_unknown_params(params: dict, allowed: set[str]) -> None:
    unknown = sorted(set(params) - allowed)
    if unknown:
        raise ValueError(f"unsupported sync media parameters: {', '.join(unknown)}")


def _materialize_image_references(
    record: ProviderExecutionRecord,
    *,
    output_root: Path,
    scratch: Path,
    bounds: SyncArtifactBounds,
    created: list[Path],
) -> list[str]:
    paths: list[str] = []
    for index, raw in enumerate(record.media):
        try:
            version = MediaVersion(**raw)
        except Exception as exc:
            raise ProviderMediaExecutionError("invalid frozen image media") from exc
        suffix = _IMAGE_REF_EXTENSIONS.get(version.declared_media_type or "")
        if suffix is None:
            raise ProviderMediaExecutionError("unsupported frozen image reference type")
        data = read_verified_media(version, output_root=output_root, max_bytes=bounds.image_max_bytes)
        try:
            probe_frozen_image(version, data)
        except MediaProbeError as exc:
            raise ProviderMediaExecutionError("frozen image reference is not decodable") from exc
        ref_path = _private_path(scratch, f"{record.attempt_id}-ref{index}", suffix)
        if ref_path.exists():
            raise ProviderMediaExecutionError("private reference path already exists")
        ref_path.write_bytes(data)
        os.chmod(ref_path, 0o600)
        created.append(ref_path)
        paths.append(str(ref_path))
    return paths


def _normalize_png(input_path: Path, output_path: Path, *, scratch: Path, bounds: SyncArtifactBounds) -> PrivateSyncArtifact:
    input_metadata = _validate_private_provider_input(
        input_path, scratch=scratch, max_bytes=bounds.image_max_bytes, media_kind="image", bounds=bounds,
    )
    _require_image_bounds(input_metadata, bounds)
    cmd = [
        _ffmpeg_executable(),
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
        "-xerror",
        "-err_detect",
        "explode",
        "-protocol_whitelist",
        "file",
        "-format_whitelist",
        IMAGE_FORMATS,
        "-i",
        str(input_path),
        "-frames:v",
        "1",
        "-f",
        "image2",
        "-fs",
        str(bounds.image_max_bytes),
        str(output_path),
    ]
    try:
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=bounds.transcode_timeout_seconds,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProviderMediaExecutionError("provider image output could not be converted to png") from exc
    if completed.returncode != 0:
        raise ProviderMediaExecutionError("provider image output could not be converted to png")
    if output_path.stat().st_size >= bounds.image_max_bytes:
        raise ProviderMediaExecutionError("normalized provider image output reached size boundary")
    try:
        output_metadata = probe_image_file(output_path)
        _require_image_bounds(output_metadata, bounds)
    except MediaProbeError as exc:
        raise ProviderMediaExecutionError("normalized provider image output is invalid") from exc
    return _private_result(output_path, media_kind="image", encoding="image/png", max_bytes=bounds.image_max_bytes)


def _normalize_mp3(input_path: Path, output_path: Path, *, scratch: Path, bounds: SyncArtifactBounds) -> PrivateSyncArtifact:
    input_metadata = cast(
        FrozenMediaMetadata,
        _validate_private_provider_input(
            input_path, scratch=scratch, max_bytes=bounds.audio_max_bytes, media_kind="audio", bounds=bounds,
        ),
    )
    _require_audio_bounds(input_metadata, bounds)
    cmd = [
        _ffmpeg_executable(),
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
        "-protocol_whitelist",
        "file",
        "-format_whitelist",
        AV_FORMATS["audio"],
        "-i",
        str(input_path),
        "-vn",
        "-codec:a",
        "libmp3lame",
        "-f",
        "mp3",
        "-fs",
        str(bounds.audio_max_bytes),
        str(output_path),
    ]
    try:
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=bounds.transcode_timeout_seconds,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProviderMediaExecutionError("provider audio output could not be converted to mp3") from exc
    if completed.returncode != 0:
        raise ProviderMediaExecutionError("provider audio output could not be converted to mp3")
    if output_path.stat().st_size >= bounds.audio_max_bytes:
        raise ProviderMediaExecutionError("normalized provider audio output reached size boundary")
    try:
        output_metadata = probe_media_file(output_path, "audio")
        _require_audio_bounds(output_metadata, bounds)
    except MediaProbeError as exc:
        raise ProviderMediaExecutionError("normalized provider audio output is invalid") from exc
    tolerance = float(bounds.audio_duration_tolerance_seconds)
    if abs(float(output_metadata.duration) - float(input_metadata.duration)) > tolerance:
        raise ProviderMediaExecutionError("normalized provider audio duration changed during transcode")
    return _private_result(output_path, media_kind="audio", encoding="audio/mpeg", max_bytes=bounds.audio_max_bytes)


def _validate_private_provider_input(path: Path, *, scratch: Path, max_bytes: int, media_kind: str, bounds: SyncArtifactBounds):
    candidate = Path(path)
    try:
        if candidate.is_symlink() or not candidate.resolve().is_relative_to(scratch.resolve()):
            raise ProviderMediaExecutionError("private artifact input is invalid")
        fd = os.open(candidate, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as exc:
        raise ProviderMediaExecutionError("private artifact input is invalid") from exc
    with os.fdopen(fd, "rb") as stream:
        metadata = os.fstat(stream.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size <= 0 or metadata.st_size > max_bytes:
            raise ProviderMediaExecutionError("private artifact input is invalid")
    try:
        if media_kind == "image":
            _probe_image_header_bounds(candidate, bounds)
            result = probe_image_file(candidate)
            _require_image_bounds(result, bounds)
            return result
        if media_kind == "audio":
            result = probe_media_file(candidate, "audio")
            _require_audio_bounds(result, bounds)
            return result
        raise ProviderMediaExecutionError("private artifact input is invalid")
    except MediaProbeError as exc:
        raise ProviderMediaExecutionError("private artifact input is invalid") from exc


def _probe_image_header_bounds(path: Path, bounds: SyncArtifactBounds) -> None:
    probe = str(Path(_ffmpeg_executable()).with_name("ffprobe"))
    try:
        result = subprocess.run(
            [
                probe,
                "-v",
                "error",
                "-show_entries",
                "stream=codec_type,width,height",
                "-of",
                "json",
                "-protocol_whitelist",
                "file",
                "-format_whitelist",
                IMAGE_FORMATS,
                "-i",
                str(path),
            ],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            check=True,
            timeout=20,
        )
        if len(result.stdout) > 64 * 1024:
            raise ProviderMediaExecutionError("provider image metadata exceeds limit")
        info = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, ValueError, RecursionError) as exc:
        raise ProviderMediaExecutionError("provider image metadata is invalid") from exc
    streams = info.get("streams") if isinstance(info, dict) else None
    if not isinstance(streams, list):
        raise ProviderMediaExecutionError("provider image metadata is invalid")
    stream = next((item for item in streams if isinstance(item, dict) and item.get("codec_type") == "video"), None)
    if stream is None:
        raise ProviderMediaExecutionError("provider image metadata is invalid")
    width, height = stream.get("width"), stream.get("height")
    if type(width) is not int or type(height) is not int or width <= 0 or height <= 0:
        raise ProviderMediaExecutionError("provider image metadata is invalid")
    if width * height > bounds.image_max_pixels:
        raise ProviderMediaExecutionError("provider image dimensions exceed bounds")


def _ffmpeg_executable() -> str:
    return get_ffmpeg_path() or shutil.which("ffmpeg") or "ffmpeg"


def _require_image_bounds(metadata, bounds: SyncArtifactBounds) -> None:
    if metadata.width * metadata.height > bounds.image_max_pixels:
        raise ProviderMediaExecutionError("provider image dimensions exceed bounds")


def _require_audio_bounds(metadata, bounds: SyncArtifactBounds) -> None:
    if float(metadata.duration) > float(bounds.audio_max_duration_seconds):
        raise ProviderMediaExecutionError("provider audio duration exceeds bounds")


def _private_result(path: Path, *, media_kind: str, encoding: str, max_bytes: int) -> PrivateSyncArtifact:
    if path.is_symlink():
        raise ProviderMediaExecutionError("private artifact was not created safely")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as exc:
        raise ProviderMediaExecutionError("private artifact was not created safely") from exc
    digest = hashlib.sha256()
    total = 0
    with os.fdopen(fd, "rb") as stream:
        metadata = os.fstat(stream.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size <= 0 or metadata.st_size > max_bytes:
            raise ProviderMediaExecutionError("private artifact exceeds bounds")
        for chunk in iter(lambda: stream.read(64 * 1024), b""):
            total += len(chunk)
            if total > max_bytes:
                raise ProviderMediaExecutionError("private artifact exceeds bounds")
            digest.update(chunk)
    if total <= 0:
        raise ProviderMediaExecutionError("private artifact exceeds bounds")
    return PrivateSyncArtifact(path=path, sha256=digest.hexdigest(), size_bytes=total, encoding=encoding, media_kind=media_kind)


def _cleanup(paths: list[Path]) -> None:
    for path in reversed(paths):
        try:
            if path.is_file() or path.is_symlink():
                path.unlink()
            elif path.is_dir():
                shutil.rmtree(path)
        except OSError:
            pass
