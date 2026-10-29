"""Neutral durable provider execution records shared by products."""

from typing import Any, Dict, Literal, Optional
import re

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_serializer, model_validator

from .wan27_image_contract import WAN27_IMAGE_EDIT_CONTRACT_VERSION

from .provider_tasks import (
    ProviderHandle, ProviderImageQueryContract, ProviderObservation, ProviderScope, ProviderTaskStatus,
)


def _validate_hash(value: Any, label: str) -> str:
    import re

    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError(f"Invalid provider artifact {label}")
    return value


def _valid_sync_artifact_path(relative_path: Any, media_kind: str | None) -> bool:
    if not isinstance(relative_path, str) or relative_path.startswith("/") or ".." in relative_path.split("/"):
        return False
    suffix = relative_path.rsplit(".", 1)[-1].lower() if "." in relative_path else ""
    if media_kind == "image":
        if relative_path.startswith("playground/"):
            return re.fullmatch(r"playground/images/[A-Za-z0-9_-][A-Za-z0-9._-]*\.png", relative_path) is not None
        if relative_path.startswith("assets/studio/"):
            return re.fullmatch(r"assets/studio/[A-Za-z0-9_-][A-Za-z0-9._-]*\.png", relative_path) is not None
        return relative_path.startswith("assets/atelier/") and suffix in {"png", "jpg", "jpeg", "webp", "bmp"}
    if media_kind == "audio":
        return relative_path.startswith("audio/atelier/") and suffix in {"mp3", "wav"}
    return False


def _validate_async_artifact_proof(value: Dict[str, Any]) -> Dict[str, Any]:
    expected = {"schema_version", "attempt_id", "handle", "relative_path", "sha256", "size_bytes"}
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError("Invalid provider artifact proof")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ValueError("Invalid provider artifact proof schema")
    attempt_id = value["attempt_id"]
    if not isinstance(attempt_id, str) or not attempt_id:
        raise ValueError("Invalid provider artifact attempt")
    handle = ProviderHandle.from_dict(value["handle"]).to_dict()
    if handle["schema_version"] != 1:
        raise ValueError("Legacy video proof requires a legacy provider handle")
    relative_path = value["relative_path"]
    if (
        not isinstance(relative_path, str)
        or relative_path.startswith("/")
        or ".." in relative_path.split("/")
        or not relative_path.startswith(("video/", "playground/"))
        or relative_path.rsplit(".", 1)[-1].lower() not in {"mp4", "mov", "webm"}
    ):
        raise ValueError("Invalid provider artifact path")
    sha256 = _validate_hash(value["sha256"], "hash")
    size_bytes = value["size_bytes"]
    if type(size_bytes) is not int or size_bytes <= 0:
        raise ValueError("Invalid provider artifact size")
    return {
        "schema_version": 1,
        "attempt_id": attempt_id,
        "handle": handle,
        "relative_path": relative_path,
        "sha256": sha256,
        "size_bytes": size_bytes,
    }


def _validate_sync_artifact_proof(value: Dict[str, Any]) -> Dict[str, Any]:
    expected = {
        "schema_version", "contract_version", "attempt_id", "media_kind",
        "relative_path", "output_ref", "sha256", "size_bytes",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError("Invalid sync artifact proof")
    if type(value["schema_version"]) is not int or value["schema_version"] != 2:
        raise ValueError("Invalid sync artifact proof schema")
    if value["contract_version"] != "provider-sync-artifact-v1":
        raise ValueError("Invalid sync artifact proof schema")
    attempt_id = value["attempt_id"]
    if not isinstance(attempt_id, str) or not attempt_id:
        raise ValueError("Invalid sync artifact attempt")
    media_kind = value["media_kind"]
    relative_path = value["relative_path"]
    output_ref = value["output_ref"]
    if media_kind not in {"image", "audio"} or output_ref != relative_path or not _valid_sync_artifact_path(relative_path, media_kind):
        raise ValueError("Invalid sync artifact path")
    sha256 = _validate_hash(value["sha256"], "hash")
    size_bytes = value["size_bytes"]
    if type(size_bytes) is not int or size_bytes <= 0:
        raise ValueError("Invalid sync artifact size")
    return {
        "schema_version": 2,
        "contract_version": "provider-sync-artifact-v1",
        "attempt_id": attempt_id,
        "media_kind": media_kind,
        "relative_path": relative_path,
        "output_ref": output_ref,
        "sha256": sha256,
        "size_bytes": size_bytes,
    }


def _valid_async_image_path(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(
        r"(?:playground/images|assets/studio|assets/atelier)/[A-Za-z0-9_-][A-Za-z0-9._-]*\.png", value,
    ) is not None


def _validate_async_image_artifact_proof(value: Dict[str, Any]) -> Dict[str, Any]:
    expected = {
        "schema_version", "contract_version", "attempt_id", "handle", "media_kind",
        "relative_path", "output_ref", "sha256", "size_bytes",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError("Invalid async image artifact proof")
    if (
        type(value["schema_version"]) is not int or value["schema_version"] != 3
        or value["contract_version"] != "provider-async-image-artifact-v1"
        or value["media_kind"] != "image"
    ):
        raise ValueError("Invalid async image artifact proof schema")
    if not isinstance(value["attempt_id"], str) or not value["attempt_id"].strip():
        raise ValueError("Invalid async image artifact attempt")
    handle = ProviderHandle.from_dict(value["handle"])
    if handle.schema_version != 2:
        raise ValueError("Async image proof requires a versioned image handle")
    if value["relative_path"] != value["output_ref"] or not _valid_async_image_path(value["output_ref"]):
        raise ValueError("Invalid async image artifact path")
    _validate_hash(value["sha256"], "hash")
    if type(value["size_bytes"]) is not int or value["size_bytes"] <= 0:
        raise ValueError("Invalid async image artifact size")
    return {**value, "handle": handle.to_dict()}


def _image_observation(raw: Dict[str, Any], handle: ProviderHandle) -> ProviderObservation:
    expected = {"task_id", "status", "raw_status", "request_id", "video_url", "image_url", "error_code", "source_handle"}
    if not isinstance(raw, dict) or set(raw) != expected:
        raise ValueError("Invalid async image observation fields")
    source_handle = ProviderHandle.from_dict(raw["source_handle"])
    if source_handle != handle:
        raise ValueError("Async image observation handle does not match record")
    return ProviderObservation(
        **{**raw, "status": ProviderTaskStatus(raw["status"]), "source_handle": source_handle},
    )


class ProviderExecutionRecord(BaseModel):
    """Durable provider receipt shared by explicit product execution contracts.

    The record stores non-secret execution identity only. Product code may use
    it to query/fetch an already-submitted provider task, but it is not an
    authorization to submit again.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(1, description="Provider execution record schema")
    execution_kind: Literal["sync_artifact", "async_artifact"] | None = Field(None, description="Versioned artifact execution discriminator")
    media_kind: Literal["image", "audio"] | None = Field(None, description="Versioned artifact output media kind")
    output_ref: str | None = Field(None, description="Versioned artifact managed output reference")
    attempt_id: str = Field(..., description="Opaque product attempt id")
    phase: str = Field(..., description="accepted | cancelled | submitting | submitted | observed | completed")
    provider_model_id: str = Field(..., description="Provider model id used at submission")
    intent: Dict[str, Any] = Field(default_factory=dict, description="Frozen generation intent")
    media: list[Dict[str, Any]] = Field(default_factory=list, description="Frozen input media versions")
    scope: Dict[str, Any] = Field(default_factory=dict, description="Original provider scope")
    credential_ref: str = Field(..., description="Opaque private credential binding reference")
    fingerprint: str = Field(..., description="Secret-free hash of intent, media, scope and credential reference")
    handle: Optional[Dict[str, Any]] = Field(None, description="Original provider handle, once observed")
    observation: Optional[Dict[str, Any]] = Field(None, description="Last query observation")
    prepared_artifact: Optional[Dict[str, Any]] = Field(
        None, description="Prepared fetched artifact proof saved before final file publication",
    )
    artifact: Optional[Dict[str, Any]] = Field(None, description="Fetched managed artifact")

    @field_validator("schema_version", mode="before")
    @classmethod
    def _schema_version(cls, value: int) -> int:
        if type(value) is not int or value not in {1, 2, 3}:
            raise ValueError("Unsupported provider execution schema")
        return value

    @field_validator("phase")
    @classmethod
    def _phase(cls, value: str) -> str:
        if value not in {"accepted", "cancelled", "submitting", "submitted", "observed", "completed"}:
            raise ValueError("Invalid provider execution phase")
        return value

    @field_validator("scope")
    @classmethod
    def _scope(cls, value: Dict[str, Any]) -> Dict[str, Any]:
        return ProviderScope.from_dict(value).to_dict()

    @field_validator("handle")
    @classmethod
    def _handle(cls, value: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if value is None:
            return None
        return ProviderHandle.from_dict(value).to_dict()

    @field_validator("fingerprint")
    @classmethod
    def _fingerprint(cls, value: str) -> str:
        import re

        if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
            raise ValueError("Invalid provider execution fingerprint")
        return value

    @field_validator("prepared_artifact", "artifact")
    @classmethod
    def _artifact_proof(cls, value: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if value is None:
            return None
        schema = value.get("schema_version") if isinstance(value, dict) else None
        if type(schema) is not int:
            raise ValueError("Invalid provider artifact proof schema")
        if schema == 1:
            return _validate_async_artifact_proof(value)
        if schema == 2:
            return _validate_sync_artifact_proof(value)
        if schema == 3:
            return _validate_async_image_artifact_proof(value)
        raise ValueError("Invalid provider artifact proof schema")

    @model_serializer(mode="wrap")
    def _serialize_provider_execution(self, handler):
        data = handler(self)
        if self.schema_version == 1:
            data.pop("execution_kind", None)
            data.pop("media_kind", None)
            data.pop("output_ref", None)
        return data

    @model_validator(mode="after")
    def _schema_matches_execution_kind(self):
        if self.schema_version == 1:
            if self.execution_kind is not None or self.media_kind is not None or self.output_ref is not None:
                raise ValueError("Legacy provider execution cannot carry sync artifact fields")
        elif self.schema_version == 2:
            if self.execution_kind != "sync_artifact" or self.media_kind not in {"image", "audio"}:
                raise ValueError("Sync provider execution requires media kind")
        elif self.execution_kind != "async_artifact" or self.media_kind != "image":
            raise ValueError("Async image execution requires its explicit media kind")
        return self

    @model_validator(mode="after")
    def _async_image_state_is_strict(self):
        if self.schema_version != 3:
            return self
        if not self.attempt_id.strip() or not _valid_async_image_path(self.output_ref):
            raise ValueError("Invalid async image execution identity or output path")
        results = (self.handle, self.observation, self.prepared_artifact, self.artifact)
        if self.phase in {"accepted", "cancelled", "submitting"}:
            if any(value is not None for value in results):
                raise ValueError("Unsubmitted async image execution cannot contain a provider result")
            return self
        if self.handle is None or ProviderHandle.from_dict(self.handle).schema_version != 2:
            raise ValueError("Async image execution requires its original image handle")
        if self.phase == "submitted":
            if any(value is not None for value in results[1:]):
                raise ValueError("Submitted async image execution cannot contain an observation or proof")
            return self
        if self.observation is None:
            raise ValueError("Observed async image execution requires an observation")
        observation = _image_observation(self.observation, ProviderHandle.from_dict(self.handle))
        if self.prepared_artifact is not None or self.artifact is not None:
            if observation.status is not ProviderTaskStatus.SUCCEEDED:
                raise ValueError("Async image proof requires a successful observation")
        if self.phase == "completed":
            if self.prepared_artifact is None or self.artifact is None:
                raise ValueError("Completed async image execution requires prepared durable artifact proof")
            if self.prepared_artifact != self.artifact:
                raise ValueError("Async image artifact proof changed after prepare")
        elif self.artifact is not None:
            raise ValueError("Uncompleted async image execution cannot contain a published artifact")
        return self

    @model_validator(mode="after")
    def _sync_artifact_state_is_strict(self):
        if self.schema_version != 2:
            return self
        if self.handle is not None:
            raise ValueError("Sync artifact execution cannot contain a provider handle")
        if self.observation is not None:
            raise ValueError("Sync artifact execution cannot contain provider observation")
        if self.phase not in {"accepted", "cancelled", "submitting", "completed"}:
            raise ValueError("Invalid sync artifact phase")
        if self.output_ref is None:
            raise ValueError("Sync artifact execution requires output ref")
        if not _valid_sync_artifact_path(self.output_ref, self.media_kind):
            raise ValueError("Invalid sync artifact output path")
        if self.phase == "accepted" and (self.prepared_artifact is not None or self.artifact is not None):
            raise ValueError("An accepted sync artifact execution cannot already contain artifact proof")
        if self.phase == "completed":
            if self.prepared_artifact is None or self.artifact is None:
                raise ValueError("Completed sync artifact execution requires prepared durable artifact proof")
            if self.prepared_artifact != self.artifact:
                raise ValueError("Sync artifact proof changed after prepare")
        elif self.prepared_artifact is not None and self.artifact is not None and self.prepared_artifact != self.artifact:
            raise ValueError("Sync artifact proof changed after prepare")
        return self

    @model_validator(mode="after")
    def _cancelled_has_no_provider_result(self):
        if self.phase == "cancelled" and any(value is not None for value in (
            self.handle, self.observation, self.prepared_artifact, self.artifact,
        )):
            raise ValueError("Cancelled provider execution cannot contain a provider result")
        return self

    @model_validator(mode="after")
    def _artifact_proof_matches_record(self):
        for value in (self.prepared_artifact, self.artifact):
            if value is None:
                continue
            if value["attempt_id"] != self.attempt_id:
                raise ValueError("Provider artifact attempt does not match record")
            if value["schema_version"] != self.schema_version:
                raise ValueError("Provider artifact proof schema does not match record")
            if self.schema_version in {1, 3}:
                if self.handle is None or value["handle"] != ProviderHandle.from_dict(self.handle).to_dict():
                    raise ValueError("Provider artifact handle does not match record")
            if self.schema_version in {2, 3}:
                if value["media_kind"] != self.media_kind:
                    raise ValueError("Sync artifact media kind does not match record")
                if value["output_ref"] != self.output_ref or value["relative_path"] != self.output_ref:
                    raise ValueError("Sync artifact output does not match record")
        return self

    @model_validator(mode="after")
    def _handle_matches_record(self):
        if self.handle is not None:
            handle = ProviderHandle.from_dict(self.handle)
            if self.schema_version == 1 and handle.schema_version != 1:
                raise ValueError("Legacy execution requires a legacy provider handle")
            if self.schema_version == 3 and self.intent.get("mode") is not None:
                wan_edit = (handle.query_contract is ProviderImageQueryContract.WAN27_IMAGE and (
                    self.intent.get("intent_contract_version") == WAN27_IMAGE_EDIT_CONTRACT_VERSION
                    or ("intent_contract_version" not in self.intent and self.intent["mode"] == "i2i")))
                expected_mode = "i2i" if handle.query_contract is ProviderImageQueryContract.GPT_IMAGE2_EDIT or wan_edit else "t2i"
                if self.intent["mode"] != expected_mode:
                    raise ValueError("Image handle query contract does not match frozen operation")
            if (
                handle.provider_model_id != self.provider_model_id
                or handle.scope != ProviderScope.from_dict(self.scope)
                or handle.credential_ref != self.credential_ref
            ):
                raise ValueError("Provider handle does not match execution record")
        return self
