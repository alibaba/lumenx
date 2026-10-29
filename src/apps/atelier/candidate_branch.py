"""Create independent editable drafts from completed Atelier candidates."""

from __future__ import annotations

import hashlib
from pathlib import Path
import time
import uuid
from typing import Any, Callable, Iterable, Literal

from pydantic import BaseModel, ConfigDict, Field

from ...core import project_package_media as package_media
from ...audio.tts import PreparedTTS
from ...models.provider_media_intents import image_intent_from_sync_record, prepared_tts_from_sync_record
from ...models.provider_recovery import ProviderRecoveryError, _generation_intent_from_record
from ...utils.json_storage import StorageConflictError
from ...utils.media_versions import MediaVersion, MediaVersionError, read_verified_media
from .generation_draft import (
    AudioGenerationDraftCommand,
    GenerationDraftCommand,
    ImageGenerationDraftCommand,
    OrderedGenerationDraftCommand,
)
from .generation_selection import (
    _is_imported_inert_candidate,
    _is_imported_inert_media_candidate,
    _verify_completed_media_artifact,
)
from .media_upload import DEFAULT_MAX_BYTES, ManagedUpload
from .candidate_branch_types import BranchRecipeStatus, BranchSource
from .models import (
    AtelierGenerationAttempt,
    AtelierMediaGenerationAttempt,
    AtelierNode,
    AtelierProject,
)
from .nodes import MAX_COORDINATE_ABS, NodeCommandAck
from .storage import AtelierStorage

_READ_MEDIA_POLICY = package_media.MediaRootPolicy(
    allowed_roots=frozenset({"assets", "audio", "playground", "video"}),
)


class CandidateBranchNotFound(ValueError):
    """The requested candidate is not live on the requested node."""


class CandidateBranchNotSelectable(ValueError):
    """The requested candidate cannot be branched safely."""


class BranchCandidateCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    expected_revision: int = Field(..., ge=0, strict=True)


class _PreparedBranch:
    def __init__(
        self,
        *,
        draft_id: str,
        draft: AtelierNode,
        refs: list[AtelierNode],
        node_ids: list[str],
        uploads: list[ManagedUpload],
    ) -> None:
        self.draft_id = draft_id
        self.draft = draft
        self.refs = refs
        self.node_ids = node_ids
        self.uploads = uploads


class AtelierCandidateBranchService:
    """Materialize a completed candidate as a source-backed draft only."""

    def __init__(
        self,
        storage: AtelierStorage,
        *,
        clock: Callable[[], float] = time.time,
        id_factory: Callable[[], str] | None = None,
        upload_saver: Callable[..., ManagedUpload] | None = None,
    ) -> None:
        self.storage = storage
        self._clock = clock
        self._id_factory = id_factory or (lambda: uuid.uuid4().hex)
        self._upload_saver = upload_saver

    def branch_from_candidate(
        self,
        project_id: str,
        node_id: str,
        candidate_id: str,
        command: BranchCandidateCommand,
    ) -> NodeCommandAck:
        current = self.storage.get_project(project_id)
        if current.revision != command.expected_revision:
            raise StorageConflictError("Atelier project changed; reload before saving")
        source_node, candidate = _find_live_candidate(current, node_id, candidate_id)
        prepared = self._prepare(current, source_node, candidate)

        def apply(project: AtelierProject) -> None:
            latest_node, latest_candidate = _find_live_candidate(project, node_id, candidate_id)
            if latest_node.type != source_node.type or latest_candidate != candidate:
                raise CandidateBranchNotSelectable("Atelier candidate changed before branch commit")
            project.nodes.append(prepared.draft)
            project.nodes.extend(prepared.refs)
            project.updated_at = prepared.draft.updated_at

        try:
            project = self.storage.apply_project_command(
                project_id,
                expected_revision=command.expected_revision,
                command=apply,
            )
        except Exception as exc:
            if not getattr(exc, "commit_uncertain", False):
                self._discard_uploads(prepared.uploads)
            raise
        return NodeCommandAck(project=project, node_ids=prepared.node_ids)

    def _prepare(self, project: AtelierProject, node: AtelierNode, candidate: dict[str, Any]) -> _PreparedBranch:
        media_kind = _candidate_kind(node, candidate)
        media_url = _candidate_url(candidate, media_kind)
        attempt = _matching_attempt(project, node, candidate)
        imported = _is_imported_candidate(self.storage, project, node, candidate)
        output = self._copy_output(project, media_kind, media_url, candidate, attempt, imported=imported)
        ref_uploads: list[ManagedUpload] = []
        try:
            draft_id = self._id_factory()
            recipe_status: BranchRecipeStatus = "unavailable_legacy"
            draft_payload: dict[str, Any] | None = None
            prompt = ""
            refs: list[AtelierNode] = []
            ref_media: list[dict[str, Any]] = []
            if attempt is None:
                recipe_status = "unavailable_imported" if imported else "unavailable_legacy"
            else:
                try:
                    recipe_status, prompt, draft_payload, refs, ref_media, ref_uploads = self._recover_recipe(project, attempt)
                except _UnavailableInputs:
                    recipe_status = "unavailable_inputs"
                    draft_payload = None
                    prompt = ""
                    refs = []
                    ref_media = []
                    ref_uploads = []

            now = self._clock()
            branch_source = BranchSource(
                schema_version=1,
                origin_project_id=project.id,
                origin_node_id=node.id,
                origin_candidate_id=str(candidate["id"]),
                media_kind=media_kind,
                media_url=output.url,
                media_sha256=output.sha256,
                media_size_bytes=output.size_bytes,
                recipe_status=recipe_status,
            ).model_dump(mode="json")
            data: dict[str, Any] = {"branch_source": branch_source}
            if recipe_status == "available" and draft_payload is not None:
                data["generation_draft"] = draft_payload
                _attach_reference_data(data, refs, ref_media)
            draft = AtelierNode.model_validate({
                "id": draft_id,
                "project_id": project.id,
                "type": media_kind,
                "title": f"Branch from {node.title or node.type}",
                "prompt": prompt if recipe_status == "available" else "",
                "status": "draft",
                "x": _branch_x(node.x, node.width),
                "y": node.y,
                "width": node.width,
                "height": node.height,
                "media_urls": [],
                "data": data,
                "created_at": now,
                "updated_at": now,
            })
            for ref in refs:
                ref.data = {**ref.data, "parent_node_id": draft_id, "reference_role": f"{media_kind}_reference_{ref.type}"}
            return _PreparedBranch(
                draft_id=draft_id,
                draft=draft,
                refs=refs,
                node_ids=[draft_id, *[ref.id for ref in refs]],
                uploads=[output, *ref_uploads],
            )
        except Exception:
            self._discard_uploads([output, *ref_uploads])
            raise

    def _recover_recipe(
        self,
        project: AtelierProject,
        attempt: AtelierGenerationAttempt | AtelierMediaGenerationAttempt,
    ) -> tuple[BranchRecipeStatus, str, dict[str, Any] | None, list[AtelierNode], list[dict[str, Any]], list[ManagedUpload]]:
        record = attempt.provider_execution
        if len(record.media) > 20:
            return "unsupported", "", None, [], [], []
        try:
            if isinstance(attempt, AtelierMediaGenerationAttempt):
                if attempt.media_kind == "image":
                    intent = image_intent_from_sync_record(record)
                    refs, media, uploads = self._materialize_frozen_media(project, record.media)
                    try:
                        command = ImageGenerationDraftCommand.model_validate({
                            "expected_revision": 0,
                            "media_kind": "image",
                            "prompt": intent.prompt,
                            "model_id": intent.model_id,
                            "mode": intent.mode,
                            "params": dict(intent.params),
                            "media": media,
                            "batch_size": 1,
                        })
                    except ValueError:
                        self._discard_uploads(uploads)
                        return "unsupported", "", None, [], [], []
                    return "available", intent.prompt, command.draft_payload(), refs, media, uploads
                prepared = prepared_tts_from_sync_record(record)
                try:
                    command = AudioGenerationDraftCommand.model_validate({
                        "expected_revision": 0,
                        "media_kind": "audio",
                        "prompt": prepared.text,
                        "voice": prepared.voice,
                        "params": _tts_params(prepared),
                    })
                except ValueError:
                    return "unsupported", "", None, [], [], []
                return "available", prepared.text, command.draft_payload(), [], [], []
            intent = _generation_intent_from_record(record.intent)
            refs, media, uploads = self._materialize_frozen_media(project, record.media)
            if attempt.input_contract_version == "ordered-media-v1":
                try:
                    command = OrderedGenerationDraftCommand.model_validate({
                        "expected_revision": 0,
                        "contract_version": "ordered-media-v1",
                        "prompt": intent.prompt,
                        "model_id": intent.model_id,
                        "mode": intent.mode,
                        "params": dict(intent.params),
                        "media": media,
                        "batch_size": 1,
                    })
                except ValueError:
                    self._discard_uploads(uploads)
                    return "unsupported", "", None, [], [], []
                return "available", intent.prompt, command.draft_payload(), refs, media, uploads
            try:
                command = GenerationDraftCommand.model_validate({
                    "expected_revision": 0,
                    "prompt": intent.prompt,
                    "model_id": intent.model_id,
                    "mode": intent.mode,
                    "params": dict(intent.params),
                })
            except ValueError:
                self._discard_uploads(uploads)
                return "unsupported", "", None, [], [], []
            return "available", intent.prompt, command.draft_payload(), refs, media, uploads
        except _UnavailableInputs:
            raise
        except (ValueError, ProviderRecoveryError):
            return "unsupported", "", None, [], [], []

    def _materialize_frozen_media(self, project: AtelierProject, media: list[dict[str, Any]]) -> tuple[list[AtelierNode], list[dict[str, Any]], list[ManagedUpload]]:
        refs: list[AtelierNode] = []
        draft_media: list[dict[str, Any]] = []
        uploads: list[ManagedUpload] = []
        try:
            for raw in media:
                try:
                    version = MediaVersion(**raw)
                    payload = read_verified_media(version, output_root=self.storage.data_dir, max_bytes=_max_bytes_for_version(version))
                except (MediaVersionError, OSError, ValueError) as exc:
                    raise _UnavailableInputs from exc
                managed = self._save_upload([payload], filename=_filename_for_version(version), content_type=version.declared_media_type)
                uploads.append(managed)
                node_id = self._id_factory()
                node = AtelierNode.model_validate({
                    "id": node_id,
                    "project_id": project.id,
                    "type": _kind_for_role(version.role),
                    "title": f"Branch reference {version.role}",
                    "prompt": "",
                    "status": "completed",
                    "x": 0.0,
                    "y": 0.0,
                    "width": 320.0,
                    "height": 180.0,
                    "media_urls": [managed.url],
                    "data": {},
                    "created_at": self._clock(),
                    "updated_at": self._clock(),
                })
                refs.append(node)
                draft_media.append({"role": version.role, "ordinal": version.ordinal, "node_id": node_id, "media_url": managed.url})
        except Exception:
            self._discard_uploads(uploads)
            raise
        return refs, draft_media, uploads

    def _copy_output(
        self,
        project: AtelierProject,
        media_kind: Literal["image", "video", "audio"],
        media_url: str,
        candidate: dict[str, Any],
        attempt: AtelierGenerationAttempt | AtelierMediaGenerationAttempt | None,
        *,
        imported: bool,
    ) -> ManagedUpload:
        proof = attempt.provider_execution.artifact if attempt is not None else None
        if proof is not None:
            if isinstance(attempt, AtelierMediaGenerationAttempt):
                _verify_media_candidate(self.storage, attempt, candidate)
            if proof["relative_path"] != media_url:
                raise CandidateBranchNotSelectable("Atelier generation candidate output does not match artifact proof")
            payload = self._read_proven_file(media_url, proof["sha256"], proof["size_bytes"], media_kind=media_kind)
        elif imported:
            payload = self._read_unproven_import(media_url, project_id=project.id, media_kind=media_kind)
        else:
            payload = self._read_owned_media(media_url, media_kind=media_kind)
        return self._save_upload([payload], filename=f"candidate{_extension(media_url, media_kind)}", content_type=None)

    def _read_proven_file(self, relative_path: str, expected_sha256: str, expected_size: int, *, media_kind: Literal["image", "video", "audio"]) -> bytes:
        payload = self._read_owned_media(relative_path, media_kind=media_kind)
        if len(payload) != expected_size or hashlib.sha256(payload).hexdigest() != expected_sha256:
            raise CandidateBranchNotSelectable("Atelier generation candidate completed proof is invalid")
        return payload

    def _read_unproven_import(self, relative_path: str, *, project_id: str, media_kind: Literal["image", "video", "audio"]) -> bytes:
        if not relative_path.startswith((f"assets/imported/{project_id}/", f"audio/imported/{project_id}/", f"video/imported/{project_id}/")):
            raise CandidateBranchNotFound("Atelier imported media candidate is not a safe copied artifact")
        return self._read_owned_media(relative_path, media_kind=media_kind)

    def _read_owned_media(self, relative_path: str, *, media_kind: Literal["image", "video", "audio"]) -> bytes:
        scope = self.storage._media_write_scope()
        return package_media.read_owned_media(
            scope.data_dir,
            relative_path,
            scope.writer_lock,
            DEFAULT_MAX_BYTES[media_kind],
            policy=_READ_MEDIA_POLICY,
            error=CandidateBranchNotSelectable,
        )

    def _save_upload(self, chunks: Iterable[bytes], *, filename: str, content_type: str | None) -> ManagedUpload:
        if self._upload_saver is not None:
            return self._upload_saver(chunks, filename=filename, content_type=content_type)
        return self.storage.save_managed_upload(chunks, filename=filename, content_type=content_type)

    def _discard_uploads(self, uploads: list[ManagedUpload]) -> None:
        first: Exception | None = None
        for managed in uploads:
            try:
                self.storage.discard_managed_upload(managed)
            except Exception as exc:
                first = first or exc
        if first is not None:
            raise first


class _UnavailableInputs(Exception):
    pass


def _find_live_candidate(project: AtelierProject, node_id: str, candidate_id: str) -> tuple[AtelierNode, dict[str, Any]]:
    node = next((item for item in project.nodes if item.id == node_id), None)
    if node is None or node.project_id != project.id or node.type not in {"image", "video", "audio"}:
        raise CandidateBranchNotFound("Atelier generation candidate not found")
    data = node.data if isinstance(node.data, dict) else {}
    live_values = data.get("candidates", [])
    deleted_values = data.get("deleted_candidates", [])
    if not isinstance(live_values, list) or not isinstance(deleted_values, list):
        raise CandidateBranchNotSelectable("Atelier generation candidate history is malformed")
    _validate_candidate_members(live_values)
    _validate_candidate_members(deleted_values)
    if any(candidate.get("id") == candidate_id for candidate in deleted_values):
        raise CandidateBranchNotFound("Atelier generation candidate is hidden")
    matches = [dict(candidate) for candidate in live_values if candidate.get("id") == candidate_id]
    if not matches:
        raise CandidateBranchNotFound("Atelier generation candidate not found")
    if len(matches) != 1:
        raise CandidateBranchNotSelectable("Atelier generation candidate identity is ambiguous")
    candidate = matches[0]
    if candidate.get("status") != "completed":
        raise CandidateBranchNotSelectable("Atelier generation candidate is not completed")
    _candidate_url(candidate, _candidate_kind(node, candidate))
    return node, candidate


def _validate_candidate_members(values: list[Any]) -> None:
    for candidate in values:
        if not isinstance(candidate, dict):
            raise CandidateBranchNotSelectable("Atelier generation candidate history is malformed")
        candidate_id = candidate.get("id")
        if not isinstance(candidate_id, str) or not candidate_id.strip():
            raise CandidateBranchNotSelectable("Atelier generation candidate history is malformed")


def _candidate_kind(node: AtelierNode, candidate: dict[str, Any]) -> Literal["image", "video", "audio"]:
    kind = candidate.get("media_kind", node.type)
    if kind not in {"image", "video", "audio"} or kind != node.type:
        raise CandidateBranchNotSelectable("Atelier generation candidate kind does not match owner")
    return kind


def _candidate_url(candidate: dict[str, Any], media_kind: str) -> str:
    value = candidate.get("media_url") if media_kind in {"image", "audio"} else candidate.get("video_url") or candidate.get("media_url")
    if not isinstance(value, str) or not value:
        raise CandidateBranchNotSelectable("Atelier generation candidate has no completed media")
    return value


def _matching_attempt(
    project: AtelierProject,
    node: AtelierNode,
    candidate: dict[str, Any],
) -> AtelierGenerationAttempt | AtelierMediaGenerationAttempt | None:
    attempt_id = candidate.get("attempt_id")
    fingerprint = candidate.get("execution_fingerprint")
    for attempt in project.generation_attempts:
        if attempt.attempt_id != attempt_id or attempt.node_id != node.id or attempt.candidate_id != candidate.get("id"):
            continue
        if isinstance(attempt, AtelierMediaGenerationAttempt) and fingerprint != attempt.provider_execution.fingerprint:
            continue
        if attempt.status != "completed" or attempt.output_ref != _candidate_url(candidate, _candidate_kind(node, candidate)):
            raise CandidateBranchNotSelectable("Atelier generation attempt is not completed")
        return attempt
    return None


def _is_imported_candidate(storage: AtelierStorage, project: AtelierProject, node: AtelierNode, candidate: dict[str, Any]) -> bool:
    if node.type == "video":
        return _is_imported_inert_candidate(storage, project, candidate)
    return _is_imported_inert_media_candidate(storage, project, node, candidate)


def _verify_media_candidate(storage: AtelierStorage, attempt: AtelierMediaGenerationAttempt, candidate: dict[str, Any]) -> None:
    try:
        _verify_completed_media_artifact(storage, attempt, candidate)
    except Exception as exc:
        raise CandidateBranchNotSelectable("Atelier media generation completed proof is invalid") from exc


def _attach_reference_data(data: dict[str, Any], refs: list[AtelierNode], media: list[dict[str, Any]]) -> None:
    if not refs:
        return
    data["reference_node_ids"] = [ref.id for ref in refs]
    for ref in refs:
        field = f"reference_{ref.type}_urls"
        data[field] = [*data.get(field, []), *ref.media_urls]
    if data["generation_draft"].get("schema_version") == 1:
        return
    data["generation_draft"]["media"] = media


def _tts_params(prepared: PreparedTTS) -> dict[str, Any]:
    values = prepared.sdk_kwargs()
    return {key: values[key] for key in ("speech_rate", "pitch_rate", "volume", "instructions") if key in values}


def _kind_for_role(role: str) -> Literal["image", "video", "audio"]:
    if role in {"first_frame", "last_frame", "reference_image"}:
        return "image"
    if role == "reference_video":
        return "video"
    if role == "reference_audio":
        return "audio"
    raise _UnavailableInputs


def _filename_for_version(version: MediaVersion) -> str:
    media_type = version.declared_media_type or ""
    ext = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/webp": ".webp",
        "image/gif": ".gif",
        "video/mp4": ".mp4",
        "video/quicktime": ".mov",
        "audio/mpeg": ".mp3",
        "audio/wav": ".wav",
        "audio/mp4": ".m4a",
        "audio/ogg": ".ogg",
    }.get(media_type, ".bin")
    return f"reference-{version.role}-{version.ordinal}{ext}"


def _max_bytes_for_version(version: MediaVersion) -> int:
    return DEFAULT_MAX_BYTES[_kind_for_role(version.role)]


def _extension(ref: str, media_kind: str) -> str:
    suffix = Path(ref).suffix.lower()
    if suffix:
        return suffix
    return {"image": ".png", "audio": ".mp3", "video": ".mp4"}[media_kind]


def _branch_x(x: float, width: float) -> float:
    return max(-MAX_COORDINATE_ABS, min(MAX_COORDINATE_ABS, x + width + 80.0))
