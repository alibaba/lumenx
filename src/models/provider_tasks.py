"""Provider receipts and explicit bindings for separate submit/query operations.

Receipts never infer missing historical scope, time, or query capability. A
resolved credential is a caller assertion about its binding, not verification
of a remote account's identity or permissions. Do not persist the credential.
"""

import base64
import binascii
import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass, field, fields, replace
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, ContextManager, Mapping, Optional, Protocol
from urllib.parse import urlsplit

from .provider_image_policy import ProviderImagePolicy, get_provider_image_policy
from ..utils.generation_intent import GenerationIntent
from ..utils.json_storage import DirectoryWriterLock
from ..utils.media_probe import (
    FrozenImageMetadata,
    FrozenMediaMetadata,
    MediaProbeError,
    probe_frozen_image,
    probe_frozen_media,
)
from ..utils.media_versions import MediaVersion, read_verified_media


_PROVIDER_FIRST_FRAME_MIMES = {"image/png", "image/jpeg", "image/webp", "image/bmp"}
_DATA_URI_TRANSPORT_BINDING = "data_uri_sha256_v1"
_PENDING_AV_TRANSPORT_BINDING = "frozen_av_sha256_v1"
_DASHSCOPE_OSS_TRANSPORT_BINDING = "dashscope_oss_sha256_v1"
_PROVIDER_PENDING_AV_MIMES = {
    "reference_video": {"video/mp4", "video/quicktime"},
    "reference_audio": {"audio/wav", "audio/mpeg"},
}
_PROVIDER_PENDING_AV_LIMITS = {
    "reference_video": 100 * 1024 * 1024,
    "reference_audio": 15 * 1024 * 1024,
}


class ProviderBindingError(ValueError):
    """A receipt or explicit credential binding cannot be used safely."""


class ProviderQueryError(RuntimeError):
    """Query failed to observe a task; this does not make the remote task FAILED."""

    def __init__(self, kind: str, *, status_code: Optional[int] = None):
        self.kind = kind
        self.status_code = status_code
        super().__init__(f"Provider query failed ({kind})")


class ProviderSubmitError(RuntimeError):
    """No usable receipt was observed; this does not prove a task was not created."""

    outcome = "unknown"

    def __init__(self, kind: str, *, status_code: Optional[int] = None):
        self.kind = kind
        self.status_code = status_code
        super().__init__(f"Provider submission outcome unknown ({kind})")


class ProviderFetchError(RuntimeError):
    """Result transfer failed; this does not change the remote task's status."""

    def __init__(self, kind: str, *, status_code: Optional[int] = None):
        self.kind = kind
        self.status_code = status_code
        super().__init__(f"Provider result fetch failed ({kind})")


class QueryCapability(str, Enum):
    UNKNOWN = "unknown"
    UNSUPPORTED = "unsupported"
    BY_TASK_ID = "by_task_id"


class ProviderImageQueryContract(str, Enum):
    WAN27_IMAGE = "dashscope-wan27-image-task-v1"
    GPT_IMAGE2_GENERATION = "mulerouter-gpt-image2-generation-task-v1"
    GPT_IMAGE2_EDIT = "mulerouter-gpt-image2-edit-task-v1"


class ProviderTaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"
    UNKNOWN = "unknown"


def _text(value: Any, label: str, *, optional: bool = False) -> None:
    if value is None and optional:
        return
    if (
        not isinstance(value, str) or not value or value.strip() != value
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise ProviderBindingError(f"Invalid {label}")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise ProviderBindingError(f"Invalid {label}") from None


def _record(value: Any, cls: type, required: set[str]) -> dict:
    allowed = {item.name for item in fields(cls)}
    if not isinstance(value, dict) or set(value) - allowed or required - set(value):
        # Neither unknown field names nor their values are safe diagnostic text.
        raise ProviderBindingError("Invalid provider receipt fields")
    return dict(value)


@dataclass(frozen=True)
class ProviderScope:
    provider: str
    backend: str
    gateway: str
    base_url: str
    region: Optional[str] = None
    workspace_id: Optional[str] = None
    account_identity: Optional[str] = None

    def __post_init__(self) -> None:
        for name in ("provider", "backend", "gateway", "base_url"):
            _text(getattr(self, name), name)
        for name in ("region", "workspace_id", "account_identity"):
            _text(getattr(self, name), name, optional=True)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict) -> "ProviderScope":
        return cls(**_record(value, cls, {"provider", "backend", "gateway", "base_url"}))


@dataclass(frozen=True)
class ProviderHandle:
    provider_model_id: str
    task_id: str
    scope: Optional[ProviderScope] = None
    credential_ref: Optional[str] = None
    request_id: Optional[str] = None
    query_capability: QueryCapability = QueryCapability.UNKNOWN
    schema_version: int = 1
    submitted_at: Optional[float] = None  # Unix seconds; None means historically unknown.
    query_contract: Optional[ProviderImageQueryContract] = None

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version not in {1, 2}:
            raise ProviderBindingError("Unsupported provider receipt schema")
        for name in ("provider_model_id", "task_id"):
            _text(getattr(self, name), name)
        for name in ("credential_ref", "request_id"):
            _text(getattr(self, name), name, optional=True)
        if self.scope is not None and not isinstance(self.scope, ProviderScope):
            raise ProviderBindingError("Invalid provider scope")
        if self.submitted_at is not None:
            try:
                valid_time = (
                    type(self.submitted_at) in (int, float)
                    and math.isfinite(self.submitted_at) and self.submitted_at >= 0
                )
            except OverflowError:
                valid_time = False
            if not valid_time:
                raise ProviderBindingError("Invalid submission time")
        try:
            capability = QueryCapability(self.query_capability)
        except (TypeError, ValueError):
            raise ProviderBindingError("Invalid query capability") from None
        object.__setattr__(self, "query_capability", capability)
        if self.schema_version == 1:
            if self.query_contract is not None:
                raise ProviderBindingError("Legacy receipt cannot carry an image query contract")
        else:
            if self.scope is None or not self.credential_ref or capability is not QueryCapability.BY_TASK_ID:
                raise ProviderBindingError("Image receipt requires original query authority")
            contract = validate_image_query_contract(self.query_contract, self.provider_model_id, self.scope)
            pattern = r"[A-Za-z0-9][A-Za-z0-9_-]{0,255}" if contract is ProviderImageQueryContract.WAN27_IMAGE else (
                r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
            )
            if re.fullmatch(pattern, self.task_id) is None:
                raise ProviderBindingError("Invalid image provider task ID")
            object.__setattr__(self, "query_contract", contract)

    def to_dict(self) -> dict:
        value = asdict(self)
        value["query_capability"] = self.query_capability.value
        if self.schema_version == 1:
            value.pop("query_contract")
        else:
            assert self.query_contract is not None
            value["query_contract"] = self.query_contract.value
        return value

    @classmethod
    def from_dict(cls, value: dict) -> "ProviderHandle":
        record = _record(value, cls, {"provider_model_id", "task_id"})
        if record.get("schema_version", 1) == 1 and "query_contract" in record:
            raise ProviderBindingError("Legacy receipt cannot carry an image query contract")
        if record.get("scope") is not None:
            record["scope"] = ProviderScope.from_dict(record["scope"])
        return cls(**record)


def validate_image_query_contract(
    contract: Any, provider_model_id: str, scope: ProviderScope,
) -> ProviderImageQueryContract:
    """Validate one fixed existing HTTP operation without current configuration."""
    try:
        contract = ProviderImageQueryContract(contract)
    except (TypeError, ValueError):
        raise ProviderBindingError("Unsupported image query contract") from None
    if contract is ProviderImageQueryContract.WAN27_IMAGE:
        from .wan27_image_contract import WAN27_IMAGE_PROVIDER_MODELS, validate_wan27_image_scope

        if provider_model_id not in WAN27_IMAGE_PROVIDER_MODELS:
            raise ProviderBindingError("Image query contract does not match model")
        validate_wan27_image_scope(scope)
    else:
        from .gpt_image2_edit_contract import validate_gpt_image2_edit_scope

        if (
            provider_model_id != "gpt-image-2" or not isinstance(scope, ProviderScope)
            or scope.backend != "mulerouter" or scope.gateway != "mulerouter_http"
        ):
            raise ProviderBindingError("Image query contract requires GPT HTTP authority")
        validate_gpt_image2_edit_scope(scope)
    return contract


def image_query_path(handle: ProviderHandle) -> str:
    """Return a fixed path from an explicit v2 receipt, never a guessed route."""
    if (
        not isinstance(handle, ProviderHandle) or handle.schema_version != 2
        or not isinstance(handle.query_contract, ProviderImageQueryContract)
    ):
        raise ProviderBindingError("Image query requires a versioned image receipt")
    paths = {
        ProviderImageQueryContract.WAN27_IMAGE: "/api/v1/tasks",
        ProviderImageQueryContract.GPT_IMAGE2_GENERATION: "/vendors/openai/v1/gpt-image-2/generation",
        ProviderImageQueryContract.GPT_IMAGE2_EDIT: "/vendors/openai/v1/gpt-image-2/edit",
    }
    return paths[handle.query_contract] + "/" + handle.task_id


@dataclass(frozen=True)
class ResolvedCredential:
    scope: ProviderScope
    credential_ref: str
    secret: str = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.scope, ProviderScope):
            raise ProviderBindingError("Missing credential scope")
        _text(self.credential_ref, "credential reference")
        if (
            not isinstance(self.secret, str) or not self.secret or not self.secret.isascii()
            or any(ord(char) <= 32 or ord(char) >= 127 for char in self.secret)
        ):
            raise ProviderBindingError("Invalid credential")

    def to_dict(self) -> dict:
        """Only binding metadata may leave the credential resolution boundary."""
        return {"scope": self.scope.to_dict(), "credential_ref": self.credential_ref}


@dataclass(frozen=True)
class PreparedMedia:
    """Provider input bound to a content version plus an explicit transport shape.

    Plain HTTP(S) URLs remain legacy/unverified because this value does not fetch
    or sign them. Data-URI transports can only be created by classmethods that
    decode bounded bytes and match them to the fixed MediaVersion hash, size and
    declared MIME. A caller cannot mark an arbitrary URL as verified.
    """

    version: MediaVersion
    url: str = field(repr=False)
    transport_binding: str = field(default="unverified", init=False)
    frozen_bytes: bytes | None = field(default=None, init=False, repr=False, compare=False)
    frozen_metadata: FrozenMediaMetadata | None = field(default=None, init=False, repr=False, compare=False)
    frozen_image_metadata: FrozenImageMetadata | None = field(default=None, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        self._validate_version_record(self.version)
        _text(self.url, "prepared media URL")
        try:
            parsed = urlsplit(self.url)
            valid = (
                parsed.scheme in {"http", "https"} and parsed.hostname
                and not parsed.username and not parsed.password and not parsed.fragment
                and (parsed.port is None or parsed.port > 0)
                and not any(c.isspace() or 127 <= ord(c) <= 159 for c in self.url)
            )
        except ValueError:
            valid = False
        if not valid:
            raise ProviderBindingError("Invalid prepared media URL")

    @classmethod
    def from_media_version(
        cls, version: MediaVersion, *, output_root: str | Path, max_bytes: int,
    ) -> "PreparedMedia":
        """Read immutable managed bytes and prepare a strict base64 data URI."""
        cls._validate_supported_data_uri_version(version, max_bytes=max_bytes)
        data = read_verified_media(version, output_root=output_root, max_bytes=max_bytes)
        return cls.from_verified_bytes(version, data, max_bytes=max_bytes)

    @classmethod
    def from_verified_bytes(
        cls, version: MediaVersion, data: bytes, *, max_bytes: int,
    ) -> "PreparedMedia":
        """Bind bytes already verified by the caller against their MediaVersion."""
        cls._validate_supported_data_uri_version(version, max_bytes=max_bytes)
        cls._validate_bytes_match_version(version, data, max_bytes=max_bytes)
        encoded = base64.b64encode(data).decode("ascii")
        return cls._from_bound_data_uri(
            version, f"data:{version.declared_media_type};base64,{encoded}",
        )

    @classmethod
    def from_data_uri(
        cls, version: MediaVersion, data_uri: str, *, max_bytes: int,
    ) -> "PreparedMedia":
        """Decode and verify a provider data URI before marking it transport-bound."""
        cls._validate_supported_data_uri_version(version, max_bytes=max_bytes)
        _text(data_uri, "prepared media data URI")
        header, separator, encoded = data_uri.partition(",")
        expected_header = f"data:{version.declared_media_type};base64"
        if separator != "," or header != expected_header:
            raise ProviderBindingError("Invalid prepared media data URI")
        if any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in encoded):
            raise ProviderBindingError("Invalid prepared media data URI")
        if len(encoded) > 4 * ((max_bytes + 2) // 3):
            raise ProviderBindingError("Prepared media data URI exceeds max_bytes")
        try:
            data = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ProviderBindingError("Malformed prepared media data URI") from exc
        cls._validate_bytes_match_version(version, data, max_bytes=max_bytes)
        return cls._from_bound_data_uri(version, data_uri)

    @classmethod
    def from_pending_av_bytes(
        cls,
        version: MediaVersion,
        data: bytes,
        *,
        probe: Callable[[MediaVersion, bytes], FrozenMediaMetadata] | None = None,
    ) -> "PreparedMedia":
        """Bind fixed audio/video bytes that still need provider temporary upload.

        The object keeps the frozen bytes only as transient process memory. Durable
        records persist the MediaVersion identity and must reconstruct from the
        content-version store before upload.
        """
        cls._validate_supported_pending_av_version(version)
        cls._validate_bytes_match_version(
            version, data, max_bytes=_PROVIDER_PENDING_AV_LIMITS[version.role],
        )
        probe = probe_frozen_media if probe is None else probe
        if not callable(probe):
            raise ProviderBindingError("Prepared AV media requires a probe")
        try:
            metadata = probe(version, data)
        except (MediaProbeError, ValueError, TypeError) as exc:
            raise ProviderBindingError("Prepared AV media metadata is invalid") from exc
        if not isinstance(metadata, FrozenMediaMetadata):
            raise ProviderBindingError("Prepared AV media metadata is invalid")
        expected_kind = "video" if version.role == "reference_video" else "audio"
        if metadata.kind != expected_kind:
            raise ProviderBindingError("Prepared AV media metadata type mismatch")
        prepared = object.__new__(cls)
        object.__setattr__(prepared, "version", version)
        object.__setattr__(
            prepared,
            "url",
            f"frozen-av://sha256/{version.sha256}/{version.role}/{version.ordinal}",
        )
        object.__setattr__(prepared, "transport_binding", _PENDING_AV_TRANSPORT_BINDING)
        object.__setattr__(prepared, "frozen_bytes", bytes(data))
        object.__setattr__(prepared, "frozen_metadata", metadata)
        object.__setattr__(prepared, "frozen_image_metadata", None)
        return prepared

    @classmethod
    def from_verified_provider_url(
        cls,
        version: MediaVersion,
        *,
        upload_result: Any,
        metadata: FrozenMediaMetadata,
    ) -> "PreparedMedia":
        """Bind a provider-upload result without accepting arbitrary caller URLs."""
        cls._validate_supported_pending_av_version(version)
        if not isinstance(metadata, FrozenMediaMetadata):
            raise ProviderBindingError("Prepared AV media metadata is invalid")
        from .provider_upload import ProviderUploadResult

        if not isinstance(upload_result, ProviderUploadResult):
            raise ProviderBindingError("Provider upload result is not typed")
        expected_kind = "video" if version.role == "reference_video" else "audio"
        if metadata.kind != expected_kind:
            raise ProviderBindingError("Prepared AV media metadata type mismatch")
        result_version = getattr(upload_result, "version", None)
        result_url = getattr(upload_result, "url", None)
        result_key = getattr(upload_result, "key", None)
        if result_version != version:
            raise ProviderBindingError("Provider upload result does not match media version")
        if (
            not isinstance(result_url, str)
            or not isinstance(result_key, str)
            or result_url != f"oss://{result_key}"
            or not result_key
            or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in result_key)
        ):
            raise ProviderBindingError("Invalid provider upload result URL")
        prepared = object.__new__(cls)
        object.__setattr__(prepared, "version", version)
        object.__setattr__(prepared, "url", result_url)
        object.__setattr__(prepared, "transport_binding", _DASHSCOPE_OSS_TRANSPORT_BINDING)
        object.__setattr__(prepared, "frozen_bytes", None)
        object.__setattr__(prepared, "frozen_metadata", metadata)
        object.__setattr__(prepared, "frozen_image_metadata", None)
        return prepared

    @classmethod
    def from_verified_provider_image_url(
        cls,
        version: MediaVersion,
        *,
        upload_result: Any,
        data: bytes,
    ) -> "PreparedMedia":
        """Bind a provider-uploaded image URL to verified frozen image bytes."""
        from .provider_upload import ProviderUploadResult

        if not isinstance(upload_result, ProviderUploadResult):
            raise ProviderBindingError("Provider upload result is not typed")
        policy = get_provider_image_policy(upload_result.provider_model_id)
        cls._validate_supported_provider_image_version(version, policy)
        assert policy is not None
        cls._validate_bytes_match_version(version, data, max_bytes=policy.max_bytes)
        try:
            metadata = probe_frozen_image(version, data, max_bytes=policy.max_bytes)
        except (MediaProbeError, ValueError, TypeError) as exc:
            raise ProviderBindingError("Prepared image media metadata is invalid") from exc
        if not isinstance(metadata, FrozenImageMetadata) or metadata.kind != "image":
            raise ProviderBindingError("Prepared image media metadata is invalid")
        result_version = getattr(upload_result, "version", None)
        result_url = getattr(upload_result, "url", None)
        result_key = getattr(upload_result, "key", None)
        if result_version != version:
            raise ProviderBindingError("Provider upload result does not match media version")
        if (
            not isinstance(result_url, str)
            or not isinstance(result_key, str)
            or result_url != f"oss://{result_key}"
            or not result_key
            or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in result_key)
        ):
            raise ProviderBindingError("Invalid provider upload result URL")
        prepared = object.__new__(cls)
        object.__setattr__(prepared, "version", version)
        object.__setattr__(prepared, "url", result_url)
        object.__setattr__(prepared, "transport_binding", _DASHSCOPE_OSS_TRANSPORT_BINDING)
        object.__setattr__(prepared, "frozen_bytes", None)
        object.__setattr__(prepared, "frozen_metadata", None)
        object.__setattr__(prepared, "frozen_image_metadata", metadata)
        return prepared

    @staticmethod
    def _validate_version_record(version: MediaVersion) -> None:
        if not isinstance(version, MediaVersion):
            raise ProviderBindingError("Prepared media requires a content version")

    @classmethod
    def _validate_supported_data_uri_version(
        cls, version: MediaVersion, *, max_bytes: int,
    ) -> None:
        cls._validate_version_record(version)
        if type(max_bytes) is not int or max_bytes <= 0:
            raise ProviderBindingError("Prepared media max_bytes must be a positive integer")
        if version.size_bytes > max_bytes:
            raise ProviderBindingError("Prepared media exceeds max_bytes")
        if version.declared_media_type not in _PROVIDER_FIRST_FRAME_MIMES:
            raise ProviderBindingError("Unsupported prepared media type")

    @classmethod
    def _validate_supported_pending_av_version(cls, version: MediaVersion) -> None:
        cls._validate_version_record(version)
        if (
            version.role not in _PROVIDER_PENDING_AV_MIMES
            or version.declared_media_type not in _PROVIDER_PENDING_AV_MIMES[version.role]
            or version.size_bytes > _PROVIDER_PENDING_AV_LIMITS[version.role]
        ):
            raise ProviderBindingError("Unsupported prepared AV media type")

    @classmethod
    def _validate_supported_provider_image_version(
        cls, version: MediaVersion, policy: ProviderImagePolicy | None,
    ) -> None:
        cls._validate_version_record(version)
        if (
            policy is None
            or version.role != policy.role
            or version.declared_media_type not in policy.mime_suffixes
            or version.size_bytes > policy.max_bytes
        ):
            raise ProviderBindingError("Unsupported prepared image media type")

    @staticmethod
    def _validate_bytes_match_version(
        version: MediaVersion, data: bytes, *, max_bytes: int,
    ) -> None:
        if type(data) is not bytes or not data or len(data) > max_bytes:
            raise ProviderBindingError("Prepared media bytes do not match the fixed version")
        if len(data) != version.size_bytes:
            raise ProviderBindingError("Prepared media bytes do not match the fixed version")
        if hashlib.sha256(data).hexdigest() != version.sha256:
            raise ProviderBindingError("Prepared media bytes do not match the fixed version")

    @classmethod
    def _from_bound_data_uri(cls, version: MediaVersion, data_uri: str) -> "PreparedMedia":
        prepared = object.__new__(cls)
        object.__setattr__(prepared, "version", version)
        object.__setattr__(prepared, "url", data_uri)
        object.__setattr__(prepared, "transport_binding", _DATA_URI_TRANSPORT_BINDING)
        object.__setattr__(prepared, "frozen_bytes", None)
        object.__setattr__(prepared, "frozen_metadata", None)
        object.__setattr__(prepared, "frozen_image_metadata", None)
        return prepared


@dataclass(frozen=True)
class ProviderSubmission:
    """Frozen transport input; not product acceptance, authorization, or F37 proof.

    Parameter omission is preserved as delegation to the provider. Product
    admission must freeze all known defaults and prove media/approval binding
    before migration; this value performs neither responsibility.
    """

    intent: GenerationIntent = field(repr=False)
    provider_model_id: str
    media: tuple[PreparedMedia, ...]
    scope: ProviderScope
    credential_ref: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.intent, GenerationIntent)
            or not isinstance(self.scope, ProviderScope)
        ):
            raise ProviderBindingError("Submission requires an intent and original scope")
        _text(self.provider_model_id, "provider model")
        _text(self.credential_ref, "credential reference")
        for name in ("model_id", "catalog_mode", "mode", "prompt", "rules_version"):
            if type(getattr(self.intent, name)) is not str:
                raise ProviderBindingError("Invalid intent text metadata")
        for name in ("preset_id", "preset_version"):
            value = getattr(self.intent, name)
            if value is not None and type(value) is not str:
                raise ProviderBindingError("Invalid intent preset metadata")
        for mapping in (self.intent.params, self.intent.sources):
            if not isinstance(mapping, Mapping) or any(type(key) is not str for key in mapping):
                raise ProviderBindingError("Invalid intent mappings")
        if any(
            type(value) not in (str, int, float, bool, type(None))
            for value in self.intent.params.values()
        ):
            raise ProviderBindingError("Intent parameters must be scalar values")
        if any(type(value) is not str for value in self.intent.sources.values()):
            raise ProviderBindingError("Invalid intent provenance")
        if not isinstance(self.intent.request_fields, (tuple, list)) or any(
            type(value) is not str for value in self.intent.request_fields
        ):
            raise ProviderBindingError("Invalid intent request fields")
        intent = replace(
            self.intent, params=MappingProxyType(dict(self.intent.params)),
            sources=MappingProxyType(dict(self.intent.sources)),
            request_fields=tuple(self.intent.request_fields),
        )
        try:
            json.dumps(intent.to_dict(), allow_nan=False, ensure_ascii=False).encode("utf-8")
        except (TypeError, ValueError, UnicodeEncodeError):
            raise ProviderBindingError("Invalid frozen intent") from None
        if not isinstance(self.media, (tuple, list)) or any(
            not isinstance(item, PreparedMedia) for item in self.media
        ):
            raise ProviderBindingError("Invalid prepared media collection")
        object.__setattr__(self, "intent", intent)
        object.__setattr__(self, "media", tuple(self.media))


@dataclass(frozen=True)
class ProviderObservation:
    task_id: str
    status: ProviderTaskStatus
    raw_status: str
    request_id: Optional[str] = None
    video_url: Optional[str] = field(default=None, repr=False)
    error_code: Optional[str] = None
    # Missing historical provenance stays missing. This is an application-side
    # binding, not proof that arbitrary caller-created observations are trusted.
    source_handle: Optional[ProviderHandle] = None
    image_url: Optional[str] = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.image_url is not None or (
            isinstance(self.source_handle, ProviderHandle) and self.source_handle.schema_version == 2
        ):
            validate_image_observation_for_handle(self, self.source_handle)

    def to_dict(self) -> dict:
        value = asdict(self)
        value["status"] = self.status.value
        if self.source_handle is not None:
            value["source_handle"] = self.source_handle.to_dict()
        if self.source_handle is None or self.source_handle.schema_version == 1:
            value.pop("image_url")
        return value


@dataclass(frozen=True)
class ProviderArtifact:
    """Downloaded bytes, without claiming input roles or verified media encoding."""

    handle: ProviderHandle
    relative_path: str
    sha256: str
    size_bytes: int

    def to_dict(self) -> dict:
        return {
            "handle": self.handle.to_dict(), "relative_path": self.relative_path,
            "sha256": self.sha256, "size_bytes": self.size_bytes,
        }


def validate_result_url(value: Any) -> None:
    """Validate URL syntax without restricting provider-selected CDN hosts."""
    _text(value, "result URL")
    try:
        parsed = urlsplit(value)
        valid = (
            parsed.scheme in {"http", "https"} and parsed.hostname
            and not parsed.username and not parsed.password and not parsed.fragment
            and (parsed.port is None or parsed.port > 0)
            and not any(c.isspace() or 127 <= ord(c) <= 159 for c in value)
        )
    except ValueError:
        valid = False
    if not valid:
        raise ProviderBindingError("Invalid result URL")


def image_status_from_raw(handle: ProviderHandle, raw_status: str) -> ProviderTaskStatus:
    """Normalize only the statuses declared by the saved image query contract."""
    image_query_path(handle)
    if not isinstance(raw_status, str) or re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", raw_status) is None:
        raise ProviderBindingError("Invalid image task status")
    if handle.query_contract is ProviderImageQueryContract.WAN27_IMAGE:
        statuses = {"PENDING": ProviderTaskStatus.PENDING, "RUNNING": ProviderTaskStatus.RUNNING,
                    "SUCCEEDED": ProviderTaskStatus.SUCCEEDED, "FAILED": ProviderTaskStatus.FAILED,
                    "CANCELED": ProviderTaskStatus.CANCELED}
    else:
        statuses = {"pending": ProviderTaskStatus.PENDING, "queued": ProviderTaskStatus.PENDING,
                    "processing": ProviderTaskStatus.RUNNING, "running": ProviderTaskStatus.RUNNING,
                    "completed": ProviderTaskStatus.SUCCEEDED, "succeeded": ProviderTaskStatus.SUCCEEDED,
                    "failed": ProviderTaskStatus.FAILED}
    return statuses.get(raw_status, ProviderTaskStatus.UNKNOWN)


def validate_image_observation_for_handle(
    observation: ProviderObservation, handle: Optional[ProviderHandle],
) -> None:
    """Validate observation identity; adapters must verify the provider ID echo."""
    if (
        not isinstance(handle, ProviderHandle) or handle.schema_version != 2
        or not isinstance(observation, ProviderObservation)
        or observation.source_handle != handle or observation.task_id != handle.task_id
        or not isinstance(observation.status, ProviderTaskStatus) or observation.video_url is not None
    ):
        raise ProviderBindingError("Invalid image observation binding")
    _text(observation.task_id, "response task ID")
    _text(observation.raw_status, "image task status")
    _text(observation.request_id, "image query request ID", optional=True)
    _text(observation.error_code, "image task error code", optional=True)
    if observation.status is not image_status_from_raw(handle, observation.raw_status):
        raise ProviderBindingError("Image observation status does not match query contract")
    if observation.status is ProviderTaskStatus.SUCCEEDED:
        validate_result_url(observation.image_url)
    elif observation.image_url is not None:
        raise ProviderBindingError("An unfinished image observation cannot carry a result URL")


def validate_fetch_binding(
    handle: ProviderHandle, observation: ProviderObservation, *, media_kind: str = "video",
) -> None:
    if (
        not isinstance(handle, ProviderHandle) or handle.scope is None
        or not handle.credential_ref or handle.query_capability != QueryCapability.BY_TASK_ID
        or not isinstance(observation, ProviderObservation)
        or observation.source_handle != handle or observation.task_id != handle.task_id
        or observation.status is not ProviderTaskStatus.SUCCEEDED
    ):
        raise ProviderBindingError("Fetch requires a successful observation of the original receipt")
    if media_kind == "image":
        validate_image_observation_for_handle(observation, handle)
    elif media_kind == "video" and handle.schema_version == 1 and observation.image_url is None:
        validate_result_url(observation.video_url)
    else:
        raise ProviderBindingError("Fetch media kind does not match provider receipt")


def validate_query_binding(
    handle: ProviderHandle, credential: ResolvedCredential, *, media_kind: str = "video",
) -> ProviderScope:
    if not isinstance(handle, ProviderHandle) or handle.scope is None:
        raise ProviderBindingError("Query requires the original provider scope")
    if handle.query_capability != QueryCapability.BY_TASK_ID:
        raise ProviderBindingError("Receipt does not declare query by task ID")
    if (media_kind, handle.schema_version) not in {("video", 1), ("image", 2)}:
        raise ProviderBindingError("Query media kind does not match provider receipt")
    if not isinstance(credential, ResolvedCredential) or not handle.credential_ref:
        raise ProviderBindingError("Query requires an explicit resolved credential binding")
    if credential.scope != handle.scope or credential.credential_ref != handle.credential_ref:
        raise ProviderBindingError("Credential binding does not match the original receipt")
    return handle.scope


class QueryableProvider(Protocol):
    def query(
        self, handle: ProviderHandle, *, credential: ResolvedCredential,
    ) -> ProviderObservation:
        """Observe one existing task without submitting work or fetching media."""
        ...


class SubmittableProvider(Protocol):
    def submit(
        self, request: ProviderSubmission, *, credential: ResolvedCredential,
        on_submitted: Callable[[ProviderHandle], None],
    ) -> ProviderHandle:
        """Submit once and persist the receipt before returning; never query/fetch."""
        ...


class FetchableProvider(Protocol):
    def fetch(
        self, handle: ProviderHandle, observation: ProviderObservation, *,
        output_root: Path, output_ref: str, writer_lock: DirectoryWriterLock,
        storage_guard: Callable[[], ContextManager[None]], max_bytes: int,
        on_prepared: Optional[Callable[[ProviderArtifact], None]] = None,
        publish_policy: str = "replace",
    ) -> ProviderArtifact:
        """Download an observed result without submitting, querying, or attaching it."""
        ...
