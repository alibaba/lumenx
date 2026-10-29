"""Portable Atelier project package helpers.

This module prepares independent Atelier project copies without carrying active
execution authority. It owns Atelier graph projection; shared Core helpers own
JSON, ZIP, path, pinned media staging, and cleanup mechanics.
"""

from __future__ import annotations

import hashlib
import io
import math
import os
import posixpath
import time
import uuid
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

from pydantic import ValidationError

from ...core import project_package_media as package_media
from ...utils.json_storage import DirectoryWriterLock
from .candidate_branch_types import validate_branch_source
from .models import AtelierImportedAgentHistoryEntry, AtelierProject

PACKAGE_KIND = "lumenx.atelier.project"
PACKAGE_SCHEMA_VERSION = 1
PROJECT_ENTRY = "project.json"
MANIFEST_ENTRY = "manifest.json"
MEDIA_ENTRY_PREFIX = "media/"

DEFAULT_MAX_PACKAGE_BYTES = 256 * 1024 * 1024
DEFAULT_MAX_FILE_BYTES = 128 * 1024 * 1024
DEFAULT_MAX_FILES = 4096
DEFAULT_MAX_COMPRESSION_RATIO = 100

_ALLOWED_MEDIA_ROOTS = frozenset({"assets", "audio", "export", "storyboard", "uploads", "video", "video_inputs"})
_REF_PREFIXES = ("/files/outputs/", "/files/output/", "/files/", "files/", "output/", "outputs/")
_MEDIA_POLICY = package_media.MediaRootPolicy(allowed_roots=_ALLOWED_MEDIA_ROOTS, ref_prefixes=_REF_PREFIXES)
_METADATA_ENTRIES = {MANIFEST_ENTRY, PROJECT_ENTRY}
_MEDIA_STRING_KEYS = {
    "audio_url",
    "image_url",
    "media_url",
    "poster_url",
    "relative_path",
    "source_url",
    "thumbnail_url",
    "url",
    "video_url",
}
_MEDIA_LIST_KEYS = {
    "media_urls",
    "reference_audio_urls",
    "reference_image_urls",
    "reference_media_urls",
    "reference_video_urls",
}
_AUTHORITY_KEYS = {
    "agent_turns",
    "approval_record",
    "authorization_hash",
    "binding",
    "canvas_approval_binding",
    "canvas_approval_record",
    "credential_ref",
    "execution_contract",
    "generation_attempts",
    "generation_request_receipts",
    "handle",
    "node_delete_receipts",
    "node_upload_receipts",
    "preview_binding",
    "provider_execution",
    "provider_handle",
    "provider_request_id",
    "request_fingerprint",
    "request_id",
    "sequence_export_jobs",
    "task_id",
}
_STRIP_CANDIDATE_KEYS = {
    "agent_authorization_ref",
    "attempt_id",
    "attempt_revision",
    "authority",
    "attempt_token",
    "binding",
    "credential_ref",
    "execution_contract",
    "execution_fingerprint",
    "handle",
    "provider_execution",
    "provider_handle",
    "provider_request_id",
    "request_fingerprint",
    "request_id",
    "task_id",
}
_CANDIDATE_BUCKET_KEYS = ("candidates", "deleted_candidates")
_CANDIDATE_JUDGMENT_KEYS = {
    "schema_version",
    "id",
    "project_id",
    "node_id",
    "candidate_id",
    "media_kind",
    "verdict",
    "reason",
    "created_at",
    "supersedes_judgment_id",
}
_CANDIDATE_JUDGMENT_MEDIA_KINDS = frozenset({"image", "video", "audio"})
_CANDIDATE_JUDGMENT_VERDICTS = frozenset({"unreviewed", "keep", "reject"})
_MAX_CANDIDATE_JUDGMENTS = 4096
_MAX_CANDIDATE_JUDGMENT_REASON_BYTES = 8000

_SAFE_CANDIDATE_KEYS = {
    "created_at",
    "duration",
    "error_code",
    "height",
    "id",
    "media_url",
    "prompt",
    "status",
    "updated_at",
    "url",
    "video_url",
    "width",
}


class AtelierProjectPackageError(ValueError):
    """Raised when an Atelier project package cannot be safely built or imported."""


@dataclass(frozen=True)
class AtelierProjectPackageArtifact:
    data: bytes
    manifest: dict[str, Any]


@dataclass(frozen=True)
class PreparedAtelierProjectImport:
    project: AtelierProject
    media: dict[str, str]
    manifest: dict[str, Any]
    id_map: dict[str, dict[str, str]]


@contextmanager
def _owned_writer_lock(data_root: Path, writer_lock: DirectoryWriterLock | None) -> Iterator[DirectoryWriterLock]:
    with package_media.owned_writer_lock(data_root, writer_lock, error=AtelierProjectPackageError) as lease:
        yield lease


def build_package(
    project: AtelierProject,
    *,
    data_dir: str | os.PathLike[str],
    source_storage_id: str,
    writer_lock: DirectoryWriterLock | None = None,
    max_files: int = DEFAULT_MAX_FILES,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    max_package_bytes: int = DEFAULT_MAX_PACKAGE_BYTES,
) -> AtelierProjectPackageArtifact:
    if not isinstance(source_storage_id, str) or not source_storage_id:
        raise AtelierProjectPackageError("source_storage_id is required")
    data_root = Path(data_dir).resolve()
    sanitized = _make_export_payload(project)
    media_refs = _collect_media_refs(sanitized)
    if len(media_refs) > max_files:
        raise AtelierProjectPackageError("Atelier project package has too many media files")

    media_entries: list[dict[str, Any]] = []
    media_blobs: dict[str, bytes] = {}
    with _owned_writer_lock(data_root, writer_lock) as owned_lock:
        for ref in media_refs:
            data = package_media.read_owned_media(
                data_root,
                ref,
                owned_lock,
                max_file_bytes,
                policy=_MEDIA_POLICY,
                error=AtelierProjectPackageError,
            )
            digest = hashlib.sha256(data).hexdigest()
            package_path = MEDIA_ENTRY_PREFIX + ref
            media_entries.append({"logical_ref": ref, "package_path": package_path, "sha256": digest, "size_bytes": len(data)})
            media_blobs[package_path] = data

    manifest = {
        "schema_version": PACKAGE_SCHEMA_VERSION,
        "kind": PACKAGE_KIND,
        "source": {
            "project_id": project.id,
            "project_revision": project.revision,
            "storage_id": source_storage_id,
            "title": project.title,
        },
        "media": media_entries,
        "agent_history": _sanitize_agent_history(project),
    }
    _validate_branch_media_proofs(sanitized, manifest)
    payload = package_media.canonical_json(sanitized, error=AtelierProjectPackageError).encode("utf-8")
    manifest_payload = package_media.canonical_json(manifest, error=AtelierProjectPackageError).encode("utf-8")
    package_size = len(payload) + len(manifest_payload) + sum(len(blob) for blob in media_blobs.values())
    if package_size > max_package_bytes:
        raise AtelierProjectPackageError("Atelier project package is too large")

    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(MANIFEST_ENTRY, manifest_payload)
        zf.writestr(PROJECT_ENTRY, payload)
        for path, data in media_blobs.items():
            zf.writestr(path, data)
    return AtelierProjectPackageArtifact(data=out.getvalue(), manifest=manifest)


def prepare_import(
    package_data: bytes,
    *,
    data_dir: str | os.PathLike[str],
    target_storage_id: str,
    writer_lock: DirectoryWriterLock | None = None,
    now_fn: Callable[[], float] | None = None,
    id_factory: Callable[[str], str] | None = None,
    max_files: int = DEFAULT_MAX_FILES,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    max_package_bytes: int = DEFAULT_MAX_PACKAGE_BYTES,
    max_compression_ratio: int = DEFAULT_MAX_COMPRESSION_RATIO,
) -> PreparedAtelierProjectImport:
    if not isinstance(target_storage_id, str) or not target_storage_id:
        raise AtelierProjectPackageError("target_storage_id is required")
    data_root = Path(data_dir).resolve()
    now = float((now_fn or time.time)())
    allocator = _IdAllocator(id_factory)
    try:
        with zipfile.ZipFile(io.BytesIO(package_data)) as zf:
            package_media.validate_zip_infos(
                zf,
                metadata_entries=_METADATA_ENTRIES,
                media_entry_prefix=MEDIA_ENTRY_PREFIX,
                max_files=max_files,
                max_file_bytes=max_file_bytes,
                max_package_bytes=max_package_bytes,
                max_compression_ratio=max_compression_ratio,
                error=AtelierProjectPackageError,
            )
            manifest = package_media.loads_strict(zf.read(MANIFEST_ENTRY), MANIFEST_ENTRY, error=AtelierProjectPackageError)
            project_payload = package_media.loads_strict(zf.read(PROJECT_ENTRY), PROJECT_ENTRY, error=AtelierProjectPackageError)
            _validate_manifest(manifest)
            _validate_manifest_agent_history(manifest)
            _validate_manifest_source_matches_project(manifest, project_payload)
            _reject_serialized_authority(project_payload)
            _validate_payload_graph(project_payload)
            media_entries = package_media.validated_manifest_media(
                manifest,
                zf,
                policy=_MEDIA_POLICY,
                media_entry_prefix=MEDIA_ENTRY_PREFIX,
                max_files=max_files,
                max_file_bytes=max_file_bytes,
                metadata_entries=_METADATA_ENTRIES,
                error=AtelierProjectPackageError,
            )
            manifest_refs = {entry["logical_ref"] for entry in media_entries}
            project_refs = set(_collect_media_refs(project_payload))
            if manifest_refs != project_refs:
                raise AtelierProjectPackageError("Media manifest does not match project references")
            _validate_branch_media_proofs(project_payload, manifest)
            expected = {MANIFEST_ENTRY, PROJECT_ENTRY} | {entry["package_path"] for entry in media_entries}
            if {info.filename for info in zf.infolist()} != expected:
                raise AtelierProjectPackageError("Unexpected package entry")
            project_id = allocator.new("project")
            media_map = _build_import_media_map(media_entries, project_id=project_id)
            imported, id_map = _make_independent_project(
                project_payload,
                manifest=manifest,
                project_id=project_id,
                media_map=media_map,
                allocator=allocator,
                now=now,
            )
            with _owned_writer_lock(data_root, writer_lock) as owned_lock:
                try:
                    package_media.stage_media(
                        zf,
                        media_entries,
                        media_map=media_map,
                        data_root=data_root,
                        writer_lock=owned_lock,
                        policy=_MEDIA_POLICY,
                        error=AtelierProjectPackageError,
                    )
                except AtelierProjectPackageError:
                    raise
                except Exception as exc:
                    raise AtelierProjectPackageError("Atelier import media could not be staged") from exc
    except AtelierProjectPackageError:
        raise
    except (KeyError, RuntimeError, zipfile.BadZipFile, OSError, ValidationError) as exc:
        raise AtelierProjectPackageError("Atelier project package is invalid") from exc
    return PreparedAtelierProjectImport(project=imported, media=media_map, manifest=manifest, id_map=id_map)


def discard_prepared_import(
    prepared: PreparedAtelierProjectImport,
    *,
    data_dir: str | os.PathLike[str],
    writer_lock: DirectoryWriterLock,
) -> None:
    root = Path(data_dir).resolve()
    with _owned_writer_lock(root, writer_lock) as owned_lock:
        package_media.cleanup_imported_media(
            prepared.manifest["media"],
            media_map=prepared.media,
            import_project_id=prepared.project.id,
            data_root=root,
            writer_lock=owned_lock,
            policy=_MEDIA_POLICY,
            error=AtelierProjectPackageError,
        )


class _IdAllocator:
    def __init__(self, factory: Callable[[str], str] | None = None) -> None:
        self._factory = factory or (lambda kind: f"{kind}-{uuid.uuid4().hex[:12]}")
        self._seen: dict[str, int] = {}

    def new(self, kind: str) -> str:
        base = self._factory(kind)
        count = self._seen.get(base, 0)
        self._seen[base] = count + 1
        return base if count == 0 else f"{base}-{count}"


def _normalize_media_ref(value: Any) -> str:
    return package_media.normalize_media_ref(value, policy=_MEDIA_POLICY, error=AtelierProjectPackageError)


def _make_export_payload(project: AtelierProject) -> dict[str, Any]:
    _validate_project_graph(project)
    payload = project.model_dump(mode="json")
    payload["nodes"] = [_sanitize_node_payload(node) for node in payload.get("nodes", [])]
    payload["exports"] = [_sanitize_export_payload(export) for export in payload.get("exports", [])]
    payload["candidate_judgments"] = _sanitize_candidate_judgments(
        payload.get("candidate_judgments") or [],
        payload=payload,
        project_id=project.id,
        allow_orphans=True,
    )
    payload["agent_compaction_summary"] = None
    payload["compacted_through_turn_id"] = None
    for field in (
        "agent_turns",
        "generation_attempts",
        "generation_request_receipts",
        "node_delete_receipts",
        "node_upload_receipts",
        "sequence_export_jobs",
    ):
        payload[field] = []
    payload.pop("import_provenance", None)
    _reject_serialized_authority(payload)
    _validate_payload_graph(payload)
    return payload


def _sanitize_node_payload(node: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(node)
    result["data"] = _sanitize_node_data(result.get("data") or {})
    result["media_urls"] = [_normalize_media_ref(item) for item in result.get("media_urls") or []]
    return result


def _sanitize_node_data(data: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    selected = data.get("selected_candidate_id")
    for key, value in data.items():
        if key == "candidates":
            result[key] = _sanitize_candidates(value, selected_candidate_id=selected)
        elif key == "deleted_candidates":
            result[key] = _sanitize_candidates(value, selected_candidate_id=None)
        elif key == "selected_candidate_id":
            result[key] = value
        elif key == "branch_source":
            result[key] = _sanitize_branch_source(value)
        elif key in {"reference_node_ids"}:
            result[key] = _string_list(value, key)
        elif key == "pending_reference_nodes":
            result[key] = _sanitize_pending_references(value)
        elif key in {"parent_node_id", "reference_role", "region_id"}:
            result[key] = value
        elif key in _MEDIA_LIST_KEYS:
            result[key] = [_normalize_media_ref(item) for item in _string_list(value, key)]
        elif key in _MEDIA_STRING_KEYS:
            result[key] = _normalize_media_ref(value)
        else:
            result[key] = _sanitize_dynamic(value, key)
    return result


def _sanitize_branch_source(value: Any) -> dict[str, Any]:
    try:
        return validate_branch_source(value).model_dump(mode="json")
    except (ValueError, TypeError) as exc:
        raise AtelierProjectPackageError("Atelier branch source is invalid") from exc


def _validate_branch_media_proofs(payload: Mapping[str, Any], manifest: Mapping[str, Any]) -> None:
    """Bind read-only provenance to the same media bytes verified by the ZIP layer."""
    for node in payload.get("nodes") or []:
        data = node.get("data") or {}
        if "branch_source" not in data:
            continue
        source = _sanitize_branch_source(data["branch_source"])
        entry = _media_entry_for_ref(manifest, source["media_url"])
        if entry["sha256"] != source["media_sha256"] or entry["size_bytes"] != source["media_size_bytes"]:
            raise AtelierProjectPackageError("Atelier branch source media proof does not match packaged media")


def _sanitize_pending_references(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise AtelierProjectPackageError("Atelier pending references must be a list")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            raise AtelierProjectPackageError("Atelier pending reference is invalid")
        if set(item) != {"schema_version", "source_node_id", "source_kind", "status"}:
            raise AtelierProjectPackageError("Atelier pending reference contains non-portable authority")
        source_id = item.get("source_node_id")
        source_kind = item.get("source_kind")
        if (
            item.get("schema_version") != 1
            or not isinstance(source_id, str)
            or not source_id
            or source_kind not in {"image", "video", "audio"}
            or item.get("status") != "pending_media"
            or source_id in seen
        ):
            raise AtelierProjectPackageError("Atelier pending reference is invalid")
        seen.add(source_id)
        result.append({"schema_version": 1, "source_node_id": source_id, "source_kind": source_kind, "status": "pending_media"})
    return result


def _sanitize_candidates(value: Any, *, selected_candidate_id: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise AtelierProjectPackageError("Atelier candidate list is invalid")
    result: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"]:
            raise AtelierProjectPackageError("Atelier candidate is invalid")
        status = item.get("status")
        media_value = item.get("video_url") or item.get("media_url") or item.get("url")
        if status != "completed" or not media_value:
            if selected_candidate_id == item["id"]:
                raise AtelierProjectPackageError("Selected Atelier candidate is not completed")
            continue
        candidate: dict[str, Any] = {}
        for key, child in item.items():
            if key in _STRIP_CANDIDATE_KEYS:
                continue
            if key in _MEDIA_STRING_KEYS:
                candidate[key] = _normalize_media_ref(child)
            elif key in _SAFE_CANDIDATE_KEYS:
                candidate[key] = _sanitize_dynamic(child, key)
            else:
                candidate[key] = _sanitize_dynamic(child, key)
        result.append(candidate)
    return result


def _sanitize_candidate_judgments(value: Any, *, payload: Mapping[str, Any], project_id: str, allow_orphans: bool) -> list[dict[str, Any]]:
    if value == []:
        return []
    if not isinstance(value, list):
        raise AtelierProjectPackageError("Atelier candidate judgments must be a list")
    if len(value) > _MAX_CANDIDATE_JUDGMENTS:
        raise AtelierProjectPackageError("Atelier candidate judgment history is too large")
    candidate_kinds = _candidate_kind_index(payload)
    result: list[dict[str, Any]] = []
    latest_by_candidate: dict[tuple[str, str], str] = {}
    seen_ids: set[str] = set()
    for item in value:
        judgment = _candidate_judgment_dict(item)
        key = _candidate_judgment_key(judgment, project_id=project_id)
        expected_kind = candidate_kinds.get(key)
        if expected_kind is None and allow_orphans:
            continue
        _validate_candidate_judgment_dict(judgment, project_id=project_id, expected_kind=expected_kind)
        if judgment["id"] in seen_ids:
            raise AtelierProjectPackageError("Duplicate Atelier candidate judgment id")
        seen_ids.add(judgment["id"])
        previous_id = latest_by_candidate.get(key)
        if judgment.get("supersedes_judgment_id") != previous_id:
            raise AtelierProjectPackageError("Atelier candidate judgment chain is invalid")
        latest_by_candidate[key] = judgment["id"]
        portable = {
            "schema_version": 1,
            "id": judgment["id"],
            "project_id": judgment["project_id"],
            "node_id": judgment["node_id"],
            "candidate_id": judgment["candidate_id"],
            "media_kind": judgment["media_kind"],
            "verdict": judgment["verdict"],
            "reason": judgment["reason"],
            "created_at": judgment["created_at"],
        }
        if judgment.get("supersedes_judgment_id") is not None:
            portable["supersedes_judgment_id"] = judgment["supersedes_judgment_id"]
        result.append(portable)
    return result


def _candidate_judgment_dict(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        dumped = value.model_dump(mode="json")
        if isinstance(dumped, dict):
            return dumped
    if isinstance(value, dict):
        return dict(value)
    raise AtelierProjectPackageError("Atelier candidate judgment is invalid")


def _candidate_judgment_key(judgment: Mapping[str, Any], *, project_id: str) -> tuple[str, str]:
    keys = set(judgment)
    if not keys <= _CANDIDATE_JUDGMENT_KEYS:
        raise AtelierProjectPackageError("Atelier candidate judgment contains non-portable authority")
    required = _CANDIDATE_JUDGMENT_KEYS - {"supersedes_judgment_id"}
    if not required <= keys:
        raise AtelierProjectPackageError("Atelier candidate judgment is invalid")
    if judgment.get("schema_version") != 1 or type(judgment.get("schema_version")) is not int:
        raise AtelierProjectPackageError("Atelier candidate judgment is invalid")
    for field in ("id", "project_id", "node_id", "candidate_id"):
        if not isinstance(judgment.get(field), str) or not judgment[field]:
            raise AtelierProjectPackageError("Atelier candidate judgment is invalid")
    if judgment["project_id"] != project_id:
        raise AtelierProjectPackageError("Atelier candidate judgment project_id does not match owner")
    supersedes = judgment.get("supersedes_judgment_id")
    if supersedes is not None and (not isinstance(supersedes, str) or not supersedes):
        raise AtelierProjectPackageError("Atelier candidate judgment chain is invalid")
    return judgment["node_id"], judgment["candidate_id"]


def _validate_candidate_judgment_dict(judgment: Mapping[str, Any], *, project_id: str, expected_kind: str | None) -> None:
    _candidate_judgment_key(judgment, project_id=project_id)
    media_kind = judgment.get("media_kind")
    if not isinstance(media_kind, str) or media_kind not in _CANDIDATE_JUDGMENT_MEDIA_KINDS:
        raise AtelierProjectPackageError("Atelier candidate judgment media_kind is invalid")
    if expected_kind is None:
        raise AtelierProjectPackageError("Atelier candidate judgment candidate is missing")
    if media_kind != expected_kind:
        raise AtelierProjectPackageError("Atelier candidate judgment media_kind does not match candidate")
    verdict = judgment.get("verdict")
    if not isinstance(verdict, str) or verdict not in _CANDIDATE_JUDGMENT_VERDICTS:
        raise AtelierProjectPackageError("Atelier candidate judgment verdict is invalid")
    reason = judgment.get("reason")
    if not isinstance(reason, str) or len(reason) > 2000:
        raise AtelierProjectPackageError("Atelier candidate judgment reason is invalid")
    try:
        encoded_reason = reason.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise AtelierProjectPackageError("Atelier candidate judgment reason is invalid") from exc
    if len(encoded_reason) > _MAX_CANDIDATE_JUDGMENT_REASON_BYTES:
        raise AtelierProjectPackageError("Atelier candidate judgment reason is invalid")
    created_at = judgment.get("created_at")
    if isinstance(created_at, bool) or not isinstance(created_at, (int, float)):
        raise AtelierProjectPackageError("Atelier candidate judgment created_at is invalid")
    try:
        created_at_value = float(created_at)
    except OverflowError as exc:
        raise AtelierProjectPackageError("Atelier candidate judgment created_at is invalid") from exc
    if created_at_value < 0 or not math.isfinite(created_at_value):
        raise AtelierProjectPackageError("Atelier candidate judgment created_at is invalid")


def _candidate_kind_index(payload: Mapping[str, Any]) -> dict[tuple[str, str], str]:
    values: dict[tuple[str, str], str] = {}
    for node in payload.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        node_id = node.get("id")
        if not isinstance(node_id, str) or not node_id:
            continue
        node_type = str(node.get("type") or "")
        data = node.get("data") or {}
        if not isinstance(data, dict):
            continue
        for bucket in _CANDIDATE_BUCKET_KEYS:
            candidates = data.get(bucket) or []
            if not isinstance(candidates, list):
                continue
            for candidate in candidates:
                if not isinstance(candidate, dict):
                    continue
                candidate_id = candidate.get("id")
                if not isinstance(candidate_id, str) or not candidate_id:
                    continue
                kind = _candidate_media_kind(node_type, candidate)
                key = (node_id, candidate_id)
                if key in values and values[key] != kind:
                    raise AtelierProjectPackageError("Atelier candidate media_kind is inconsistent")
                values[key] = kind
    return values


def _candidate_media_kind(node_type: str, candidate: Mapping[str, Any]) -> str:
    media_kind = candidate.get("media_kind")
    if media_kind in _CANDIDATE_JUDGMENT_MEDIA_KINDS:
        return media_kind
    if candidate.get("video_url") or node_type == "video":
        return "video"
    if node_type in _CANDIDATE_JUDGMENT_MEDIA_KINDS:
        return node_type
    raise AtelierProjectPackageError("Atelier candidate media_kind is invalid")


def _sanitize_export_payload(export: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(export)
    if result.get("video_url"):
        result["video_url"] = _normalize_media_ref(result["video_url"])
    result["publication_proof"] = None
    result["job_id"] = None
    result["snapshot_digest"] = None
    result["deletion"] = None
    result.pop("imported_proof", None)
    return _sanitize_dynamic(result, "export")


def _sanitize_dynamic(value: Any, key: str | None = None) -> Any:
    if key is not None and _is_authority_key(key):
        raise AtelierProjectPackageError(f"Hidden authority field is not portable: {key}")
    if isinstance(value, dict):
        return {str(child_key): _sanitize_dynamic(child_value, str(child_key)) for child_key, child_value in value.items()}
    if isinstance(value, list):
        return [_sanitize_dynamic(item, key) for item in value]
    if isinstance(value, str):
        return value
    return value


def _sanitize_agent_history(project: AtelierProject) -> list[dict[str, Any]]:
    history: list[dict[str, Any]] = []
    if project.import_provenance is not None:
        history.extend(entry.model_dump(mode="json") for entry in project.import_provenance.agent_history)
    graph_refs = _graph_reference_index(project.model_dump(mode="json"))
    for turn in project.agent_turns:
        messages, refs = _sanitize_turn_messages(turn.messages_history, graph_refs)
        if turn.user_message or turn.response or messages:
            history.append(
                {
                    "schema_version": 1,
                    "source_turn_id": turn.id,
                    "status": turn.status,
                    "user_message": turn.user_message,
                    "assistant_response": turn.response,
                    "messages": messages,
                    "created_at": turn.created_at,
                    "completed_at": turn.completed_at,
                    "source_refs": {item: item for item in sorted(refs)},
                    "target_refs": {},
                }
            )
    return history


def _sanitize_turn_messages(messages: list[dict[str, Any]], graph_refs: Mapping[str, Any]) -> tuple[list[dict[str, str]], set[str]]:
    result: list[dict[str, str]] = []
    refs: set[str] = set()
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        if role not in {"user", "assistant", "tool"}:
            continue
        item = {"role": role}
        name = message.get("name")
        if isinstance(name, str) and name:
            item["name"] = name
        content = message.get("content")
        if isinstance(content, str):
            item["content"] = content
        elif isinstance(content, dict):
            refs.update(_collect_structured_graph_refs(content, graph_refs))
            item["content"] = _public_message_content(content, name=name if isinstance(name, str) else None)
            status = content.get("status")
            if isinstance(status, str) and status:
                item["status"] = status
            error_code = content.get("public_error_code") or content.get("error_code") or content.get("code")
            if isinstance(error_code, str) and error_code:
                item["error_code"] = error_code
        elif content is None:
            item["content"] = ""
        else:
            item["content"] = str(content)
        result.append(item)
    return result, refs


def _public_message_content(content: Mapping[str, Any], *, name: str | None) -> str:
    summary = content.get("summary")
    if isinstance(summary, str):
        base = summary
    else:
        public_message = content.get("public_message")
        if isinstance(public_message, str):
            base = public_message
        else:
            status = content.get("status")
            if isinstance(status, str) and status:
                tool = name or "tool"
                base = f"{tool} {status}"
            else:
                base = "Tool result recorded"
    details: list[str] = []
    status = content.get("status")
    if isinstance(status, str) and status:
        details.append(f"status: {status}")
    error_code = content.get("public_error_code") or content.get("error_code") or content.get("code")
    if isinstance(error_code, str) and error_code:
        details.append(f"error: {error_code}")
    return f"{base} ({'; '.join(details)})" if details else base


def _graph_reference_index(payload: Mapping[str, Any]) -> dict[str, Any]:
    node_ids: set[str] = set()
    candidates_by_node: dict[str, set[str]] = {}
    candidate_to_nodes: dict[str, set[str]] = {}
    for node in payload.get("nodes") or []:
        if isinstance(node, dict) and isinstance(node.get("id"), str):
            node_id = node["id"]
            node_ids.add(node_id)
            candidates_by_node[node_id] = set()
            for candidate in (node.get("data") or {}).get("candidates") or []:
                if isinstance(candidate, dict) and isinstance(candidate.get("id"), str):
                    candidate_id = candidate["id"]
                    candidates_by_node[node_id].add(candidate_id)
                    candidate_to_nodes.setdefault(candidate_id, set()).add(node_id)
    return {"node_ids": node_ids, "candidates_by_node": candidates_by_node, "candidate_to_nodes": candidate_to_nodes}


_NODE_REF_KEYS = frozenset({"node_id", "parent_node_id", "parentId", "selected_node_id", "target_node_id", "source_node_id", "created_node_id"})
_CANDIDATE_REF_KEYS = frozenset({"candidate_id", "candidateId", "selected_candidate_id"})


def _collect_structured_graph_refs(value: Any, graph_refs: Mapping[str, Any]) -> set[str]:
    refs: set[str] = set()
    node_ids: set[str] = graph_refs["node_ids"]
    candidates_by_node: Mapping[str, set[str]] = graph_refs["candidates_by_node"]
    candidate_to_nodes: Mapping[str, set[str]] = graph_refs["candidate_to_nodes"]

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            node_ref = next((child for key, child in item.items() if key in _NODE_REF_KEYS and isinstance(child, str) and child in node_ids), None)
            candidate_ref = next((child for key, child in item.items() if key in _CANDIDATE_REF_KEYS and isinstance(child, str)), None)
            if node_ref is not None:
                refs.add(node_ref)
            if candidate_ref is not None:
                owner = node_ref
                if owner is None:
                    owners = candidate_to_nodes.get(candidate_ref, set())
                    owner = next(iter(owners)) if len(owners) == 1 else None
                if owner is not None and candidate_ref in candidates_by_node.get(owner, set()):
                    refs.add(f"{owner}:{candidate_ref}")
            for key, child in item.items():
                if _is_authority_key(str(key)):
                    continue
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    return refs


def _is_authority_key(key: str) -> bool:
    if key == "approval_mode":
        return False
    lowered = key.lower()
    return key in _AUTHORITY_KEYS or any(part in lowered for part in ("credential", "secret", "authorization", "approval", "binding", "provider", "request_fingerprint", "request_id"))


def _string_list(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise AtelierProjectPackageError(f"Atelier {label} must be a string list")
    return list(value)


def _collect_media_refs(value: Any) -> list[str]:
    refs: set[str] = set()

    def visit(node: Any, key: str | None = None) -> None:
        if isinstance(node, dict):
            for child_key, child_value in node.items():
                if child_key == "branch_source":
                    refs.add(_sanitize_branch_source(child_value)["media_url"])
                elif child_key in _MEDIA_LIST_KEYS:
                    for item in _string_list(child_value, child_key):
                        refs.add(_normalize_media_ref(item))
                elif child_key in _MEDIA_STRING_KEYS and child_value:
                    refs.add(_normalize_media_ref(child_value))
                else:
                    visit(child_value, str(child_key))
        elif isinstance(node, list):
            for item in node:
                visit(item, key)

    visit(value)
    return sorted(refs)


def _validate_project_graph(project: AtelierProject) -> None:
    if any(node.project_id != project.id for node in project.nodes):
        raise AtelierProjectPackageError("Atelier node project_id does not match owner")
    _validate_payload_graph(project.model_dump(mode="json"), allow_candidate_judgment_orphans=True)


def _validate_payload_graph(payload: Mapping[str, Any], *, allow_candidate_judgment_orphans: bool = False) -> None:
    nodes = payload.get("nodes") or []
    if not isinstance(nodes, list):
        raise AtelierProjectPackageError("Atelier nodes are invalid")
    node_ids: set[str] = set()
    candidates_by_node: dict[str, set[str]] = {}
    deleted_by_node: dict[str, set[str]] = {}
    node_types: dict[str, str] = {}
    for node in nodes:
        if not isinstance(node, dict) or not isinstance(node.get("id"), str) or not node["id"]:
            raise AtelierProjectPackageError("Atelier node is invalid")
        node_id = node["id"]
        if node_id in node_ids:
            raise AtelierProjectPackageError("Duplicate Atelier node id")
        node_ids.add(node_id)
        node_types[node_id] = str(node.get("type") or "")
        data = node.get("data") or {}
        if not isinstance(data, dict):
            raise AtelierProjectPackageError("Atelier node data is invalid")
        if "branch_source" in data:
            source = _sanitize_branch_source(data["branch_source"])
            if source["media_kind"] != node.get("type"):
                raise AtelierProjectPackageError("Atelier branch source media kind does not match node")
        for ref in data.get("reference_node_ids") or []:
            if ref not in node_ids and not any(isinstance(other, dict) and other.get("id") == ref for other in nodes):
                raise AtelierProjectPackageError("Atelier node reference is missing")
        pending_refs = data.get("pending_reference_nodes") or []
        if not isinstance(pending_refs, list):
            raise AtelierProjectPackageError("Atelier pending references must be a list")
        pending_ids: set[str] = set()
        for pending in pending_refs:
            if not isinstance(pending, dict) or set(pending) != {"schema_version", "source_node_id", "source_kind", "status"}:
                raise AtelierProjectPackageError("Atelier pending reference is invalid")
            source_id = pending.get("source_node_id")
            source_kind = pending.get("source_kind")
            source_node = next((other for other in nodes if isinstance(other, dict) and other.get("id") == source_id), None)
            if (
                pending.get("schema_version") != 1
                or not isinstance(source_id, str)
                or source_id in pending_ids
                or source_node is None
                or source_node.get("type") != source_kind
                or pending.get("status") != "pending_media"
                or source_id not in (data.get("reference_node_ids") or [])
            ):
                raise AtelierProjectPackageError("Atelier pending reference source is missing")
            pending_ids.add(source_id)
        parent = data.get("parent_node_id")
        if parent is not None and parent not in {item.get("id") for item in nodes if isinstance(item, dict)}:
            raise AtelierProjectPackageError("Atelier parent node reference is missing")
        region = data.get("region_id")
        if region is not None and node_types.get(region) != "region" and not any(isinstance(other, dict) and other.get("id") == region and other.get("type") == "region" for other in nodes):
            raise AtelierProjectPackageError("Atelier region reference is missing")
        candidates = data.get("candidates") or []
        deleted = data.get("deleted_candidates") or []
        if not isinstance(candidates, list) or not isinstance(deleted, list):
            raise AtelierProjectPackageError("Atelier candidate list is invalid")
        ids = {item["id"] for item in candidates if isinstance(item, dict) and isinstance(item.get("id"), str)}
        deleted_ids = {item["id"] for item in deleted if isinstance(item, dict) and isinstance(item.get("id"), str)}
        if len(ids) != len(candidates) or len(deleted_ids) != len(deleted) or ids & deleted_ids:
            raise AtelierProjectPackageError("Duplicate or invalid Atelier candidate id")
        candidates_by_node[node_id] = ids
        deleted_by_node[node_id] = deleted_ids
        selected = data.get("selected_candidate_id")
        if selected is not None and selected not in ids:
            raise AtelierProjectPackageError("Selected Atelier candidate is missing")
    for entry in payload.get("sequence") or []:
        parent = entry.get("parentId") if isinstance(entry, dict) else None
        candidate = entry.get("candidateId") if isinstance(entry, dict) else None
        if parent not in node_ids:
            raise AtelierProjectPackageError("Atelier sequence parent is missing")
        if candidate is not None:
            if candidate in deleted_by_node.get(parent, set()):
                raise AtelierProjectPackageError("Atelier sequence references deleted candidate")
            if candidate not in candidates_by_node.get(parent, set()):
                raise AtelierProjectPackageError("Atelier sequence candidate is missing")
    for track in payload.get("audio_tracks") or []:
        if not isinstance(track, dict) or track.get("node_id") not in node_ids:
            raise AtelierProjectPackageError("Atelier audio track node is missing")
    for export in payload.get("exports") or []:
        if not isinstance(export, dict):
            raise AtelierProjectPackageError("Atelier export is invalid")
        _validate_timeline_snapshot(
            export.get("timeline_snapshot"),
            node_ids=node_ids,
            candidates_by_node=candidates_by_node,
            deleted_by_node=deleted_by_node,
        )
        _validate_payload_graph({"nodes": nodes, "sequence": export.get("source_entries") or [], "audio_tracks": []})
    candidate_judgments = payload["candidate_judgments"] if "candidate_judgments" in payload else []
    _sanitize_candidate_judgments(
        candidate_judgments,
        payload=payload,
        project_id=payload.get("id", ""),
        allow_orphans=allow_candidate_judgment_orphans,
    )


def _validate_timeline_snapshot(
    value: Any,
    *,
    node_ids: set[str],
    candidates_by_node: Mapping[str, set[str]],
    deleted_by_node: Mapping[str, set[str]],
) -> None:
    if value is None:
        return
    if not isinstance(value, dict):
        raise AtelierProjectPackageError("Atelier timeline snapshot is invalid")
    clips = value.get("clips") or []
    if not isinstance(clips, list):
        raise AtelierProjectPackageError("Atelier timeline clips are invalid")
    for clip in clips:
        if not isinstance(clip, dict):
            raise AtelierProjectPackageError("Atelier timeline clip is invalid")
        clip_id = clip.get("clip_id")
        if clip_id is not None and (not isinstance(clip_id, str) or not clip_id):
            raise AtelierProjectPackageError("Atelier timeline clip id is invalid")
        parent = clip.get("parentId")
        if not isinstance(parent, str) or parent not in node_ids:
            raise AtelierProjectPackageError("Atelier timeline clip parent is missing")
        candidate = clip.get("candidateId")
        if candidate is not None:
            if not isinstance(candidate, str):
                raise AtelierProjectPackageError("Atelier timeline clip candidate is invalid")
            if candidate in deleted_by_node.get(parent, set()) or candidate not in candidates_by_node.get(parent, set()):
                raise AtelierProjectPackageError("Atelier timeline clip candidate is missing")
    audio_tracks = value.get("audio_tracks") or []
    if not isinstance(audio_tracks, list):
        raise AtelierProjectPackageError("Atelier timeline audio tracks are invalid")
    for track in audio_tracks:
        if not isinstance(track, dict):
            raise AtelierProjectPackageError("Atelier timeline audio track is invalid")
        track_id = track.get("id")
        if track_id is not None and (not isinstance(track_id, str) or not track_id):
            raise AtelierProjectPackageError("Atelier timeline audio track id is invalid")
        node_id = track.get("node_id")
        if node_id is not None and (not isinstance(node_id, str) or node_id not in node_ids):
            raise AtelierProjectPackageError("Atelier timeline audio track node is missing")


def _validate_manifest(manifest: Any) -> None:
    if not isinstance(manifest, dict):
        raise AtelierProjectPackageError("Atelier project package manifest must be an object")
    if type(manifest.get("schema_version")) is not int or manifest.get("schema_version") != PACKAGE_SCHEMA_VERSION:
        raise AtelierProjectPackageError("Unsupported Atelier package schema")
    if manifest.get("kind") != PACKAGE_KIND:
        raise AtelierProjectPackageError("Unsupported Atelier package kind")
    source = manifest.get("source")
    if not isinstance(source, dict) or set(source) != {"project_id", "project_revision", "storage_id", "title"}:
        raise AtelierProjectPackageError("Atelier package source metadata is invalid")
    if any(not isinstance(source[key], str) or not source[key] for key in ("project_id", "storage_id", "title")):
        raise AtelierProjectPackageError("Atelier package source metadata is invalid")
    if type(source["project_revision"]) is not int or source["project_revision"] < 0:
        raise AtelierProjectPackageError("Atelier package source metadata is invalid")
    if not isinstance(manifest.get("media"), list):
        raise AtelierProjectPackageError("Atelier package media manifest is invalid")
    if not isinstance(manifest.get("agent_history"), list):
        raise AtelierProjectPackageError("Atelier package agent history is invalid")


def _validate_manifest_agent_history(manifest: Mapping[str, Any]) -> None:
    for entry in manifest.get("agent_history") or []:
        try:
            history = AtelierImportedAgentHistoryEntry.model_validate(entry)
        except ValidationError as exc:
            raise AtelierProjectPackageError("Atelier package agent history is invalid") from exc
        _reject_history_refs(history.source_refs, "source_refs")
        _reject_history_refs(history.target_refs, "target_refs")


def _reject_history_refs(refs: Mapping[str, str], label: str) -> None:
    for key, value in refs.items():
        if _is_authority_key(key) or _is_authority_key(value):
            raise AtelierProjectPackageError(f"Atelier package agent history contains authority in {label}")


def _validate_manifest_source_matches_project(manifest: Mapping[str, Any], payload: Any) -> None:
    if not isinstance(payload, dict):
        raise AtelierProjectPackageError("Atelier package project payload is invalid")
    source = manifest["source"]
    if payload.get("id") != source["project_id"]:
        raise AtelierProjectPackageError("Atelier package source project_id does not match project payload")
    if payload.get("revision") != source["project_revision"]:
        raise AtelierProjectPackageError("Atelier package source revision does not match project payload")
    if payload.get("title") != source["title"]:
        raise AtelierProjectPackageError("Atelier package source title does not match project payload")


def _reject_serialized_authority(payload: Mapping[str, Any]) -> None:
    for field in ("agent_turns", "generation_attempts", "generation_request_receipts", "node_delete_receipts", "node_upload_receipts", "sequence_export_jobs"):
        if payload.get(field) not in (None, []):
            raise AtelierProjectPackageError(f"Serialized Atelier authority field is not portable: {field}")
    for field in ("agent_compaction_summary", "compacted_through_turn_id"):
        if payload.get(field) not in (None, ""):
            raise AtelierProjectPackageError(f"Serialized Atelier authority field is not portable: {field}")
    if payload.get("import_provenance") is not None:
        raise AtelierProjectPackageError("Serialized Atelier authority field is not portable: import_provenance")
    _reject_nested_authority(payload)


def _reject_nested_authority(value: Any, *, key: str | None = None) -> None:
    if key is not None and _is_authority_key(key) and value not in (None, "", [], {}):
        raise AtelierProjectPackageError(f"Serialized Atelier authority field is not portable: {key}")
    if isinstance(value, dict):
        for child_key, child_value in value.items():
            _reject_nested_authority(child_value, key=str(child_key))
    elif isinstance(value, list):
        for item in value:
            _reject_nested_authority(item)


def _build_import_media_map(media_entries: list[dict[str, Any]], *, project_id: str) -> dict[str, str]:
    return {entry["logical_ref"]: _target_ref_for_import(entry["logical_ref"], project_id) for entry in media_entries}


def _target_ref_for_import(logical_ref: str, project_id: str) -> str:
    ref = _normalize_media_ref(logical_ref)
    root, rest = ref.split("/", 1)
    return posixpath.join(root, "imported", project_id, rest)


def _make_independent_project(
    payload: Mapping[str, Any],
    *,
    manifest: Mapping[str, Any],
    project_id: str,
    media_map: Mapping[str, str],
    allocator: _IdAllocator,
    now: float,
) -> tuple[AtelierProject, dict[str, dict[str, str]]]:
    source = manifest["source"]
    id_map = _build_id_map(payload, project_id=project_id, allocator=allocator)
    rewritten = _rewrite_project(payload, id_map=id_map, media_map=media_map, manifest=manifest, now=now)
    rewritten["import_provenance"] = {
        "schema_version": 1,
        "source_project_id": source["project_id"],
        "source_storage_id": source["storage_id"],
        "imported_at": now,
        "agent_history": [_rewrite_agent_history(item, id_map) for item in manifest.get("agent_history", [])],
    }
    try:
        project = AtelierProject.model_validate(rewritten)
    except ValidationError as exc:
        raise AtelierProjectPackageError("Imported Atelier project is invalid") from exc
    _validate_project_graph(project)
    return project, id_map


def _build_id_map(payload: Mapping[str, Any], *, project_id: str, allocator: _IdAllocator) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {
        "projects": {payload["id"]: project_id},
        "nodes": {},
        "candidates": {},
        "candidate_judgments": {},
        "sequence": {},
        "audio_tracks": {},
        "exports": {},
    }
    for node in payload.get("nodes") or []:
        result["nodes"][node["id"]] = allocator.new("node")
        data = node.get("data") or {}
        for bucket in _CANDIDATE_BUCKET_KEYS:
            for candidate in data.get(bucket) or []:
                result["candidates"][f"{node['id']}:{candidate['id']}"] = allocator.new("candidate")
    for entry in payload.get("sequence") or []:
        if entry.get("id"):
            result["sequence"][entry["id"]] = allocator.new("clip")
    for track in payload.get("audio_tracks") or []:
        result["audio_tracks"][track["id"]] = allocator.new("audio-track")
    for export in payload.get("exports") or []:
        result["exports"][export["id"]] = allocator.new("export")
        for entry in export.get("source_entries") or []:
            if entry.get("id") and entry["id"] not in result["sequence"]:
                result["sequence"][entry["id"]] = allocator.new("clip")
        timeline = export.get("timeline_snapshot")
        if isinstance(timeline, dict):
            for clip in timeline.get("clips") or []:
                if isinstance(clip, dict) and clip.get("clip_id") and clip["clip_id"] not in result["sequence"]:
                    result["sequence"][clip["clip_id"]] = allocator.new("clip")
            for track in timeline.get("audio_tracks") or []:
                if isinstance(track, dict) and track.get("id") and track["id"] not in result["audio_tracks"]:
                    result["audio_tracks"][track["id"]] = allocator.new("audio-track")
    for judgment in payload.get("candidate_judgments") or []:
        result["candidate_judgments"][judgment["id"]] = allocator.new("judgment")
    return result


def _rewrite_project(payload: Mapping[str, Any], *, id_map: Mapping[str, Mapping[str, str]], media_map: Mapping[str, str], manifest: Mapping[str, Any], now: float) -> dict[str, Any]:
    project_id = next(iter(id_map["projects"].values()))
    result = dict(payload)
    result["id"] = project_id
    result["source_project_id"] = None
    result["revision"] = 0
    result["created_at"] = now
    result["updated_at"] = now
    result["nodes"] = [_rewrite_node(node, id_map=id_map, media_map=media_map, project_id=project_id, now=now) for node in payload.get("nodes") or []]
    result["sequence"] = [_rewrite_sequence_entry(entry, id_map=id_map) for entry in payload.get("sequence") or []]
    result["audio_tracks"] = [_rewrite_audio_track(track, id_map=id_map) for track in payload.get("audio_tracks") or []]
    result["exports"] = [_rewrite_export(export, id_map=id_map, media_map=media_map, project_id=project_id, manifest=manifest, now=now) for export in payload.get("exports") or []]
    result["candidate_judgments"] = [
        _rewrite_candidate_judgment(judgment, id_map=id_map, project_id=project_id)
        for judgment in payload.get("candidate_judgments") or []
    ]
    for field in ("agent_turns", "generation_attempts", "generation_request_receipts", "node_delete_receipts", "node_upload_receipts", "sequence_export_jobs"):
        result[field] = []
    result["agent_compaction_summary"] = None
    result["compacted_through_turn_id"] = None
    return result


def _rewrite_node(node: Mapping[str, Any], *, id_map: Mapping[str, Mapping[str, str]], media_map: Mapping[str, str], project_id: str, now: float) -> dict[str, Any]:
    result = dict(node)
    result["id"] = id_map["nodes"][node["id"]]
    result["project_id"] = project_id
    result["media_urls"] = [_rewrite_media_ref(item, media_map) for item in result.get("media_urls") or []]
    result["data"] = _rewrite_node_data(node["id"], result.get("data") or {}, id_map=id_map, media_map=media_map)
    result["created_at"] = now
    result["updated_at"] = now
    return result


def _rewrite_node_data(source_node_id: str, data: Mapping[str, Any], *, id_map: Mapping[str, Mapping[str, str]], media_map: Mapping[str, str]) -> dict[str, Any]:
    selected = data.get("selected_candidate_id")
    dynamic = {key: value for key, value in data.items() if key not in {*_CANDIDATE_BUCKET_KEYS, "selected_candidate_id", "branch_source"}}
    result = _rewrite_dynamic(dynamic, id_map=id_map, media_map=media_map, source_node_id=source_node_id)
    if "branch_source" in data:
        # Origin identities describe history, not graph edges in this new copy.
        source = _sanitize_branch_source(data["branch_source"])
        source["media_url"] = _rewrite_media_ref(source["media_url"], media_map)
        result["branch_source"] = _sanitize_branch_source(source)
    for bucket in _CANDIDATE_BUCKET_KEYS:
        candidates = data.get(bucket)
        if candidates is not None:
            result[bucket] = [_rewrite_candidate(source_node_id, item, id_map=id_map, media_map=media_map) for item in candidates]
    if selected is not None:
        result["selected_candidate_id"] = id_map["candidates"][f"{source_node_id}:{selected}"]
    return result


def _rewrite_candidate(source_node_id: str, candidate: Mapping[str, Any], *, id_map: Mapping[str, Mapping[str, str]], media_map: Mapping[str, str]) -> dict[str, Any]:
    if _is_portable_media_candidate(candidate):
        return {
            "schema_version": 2,
            "execution_contract": "atelier-provider-media-v2",
            "id": id_map["candidates"][f"{source_node_id}:{candidate['id']}"],
            "media_kind": candidate["media_kind"],
            "status": "completed",
            "media_url": _rewrite_media_ref(candidate["media_url"], media_map),
            "error_code": None,
            "authority": "imported",
            "attempt_id": None,
            "execution_fingerprint": None,
        }
    result = dict(candidate)
    result["id"] = id_map["candidates"][f"{source_node_id}:{candidate['id']}"]
    for key in _MEDIA_STRING_KEYS:
        if result.get(key):
            result[key] = _rewrite_media_ref(result[key], media_map)
    return result


def _rewrite_candidate_judgment(judgment: Mapping[str, Any], *, id_map: Mapping[str, Mapping[str, str]], project_id: str) -> dict[str, Any]:
    source_node_id = judgment["node_id"]
    source_candidate_id = judgment["candidate_id"]
    result = {
        "schema_version": 1,
        "id": id_map["candidate_judgments"][judgment["id"]],
        "project_id": project_id,
        "node_id": id_map["nodes"][source_node_id],
        "candidate_id": id_map["candidates"][f"{source_node_id}:{source_candidate_id}"],
        "media_kind": judgment["media_kind"],
        "verdict": judgment["verdict"],
        "reason": judgment["reason"],
        "created_at": judgment["created_at"],
    }
    supersedes = judgment.get("supersedes_judgment_id")
    if supersedes is not None:
        result["supersedes_judgment_id"] = id_map["candidate_judgments"][supersedes]
    return result


def _is_portable_media_candidate(candidate: Mapping[str, Any]) -> bool:
    return (
        candidate.get("media_kind") in {"image", "audio"}
        and candidate.get("status") == "completed"
        and isinstance(candidate.get("media_url"), str)
        and bool(candidate.get("media_url"))
    )


def _rewrite_sequence_entry(entry: Mapping[str, Any], *, id_map: Mapping[str, Mapping[str, str]]) -> dict[str, Any]:
    result = dict(entry)
    source_parent = entry["parentId"]
    result["parentId"] = id_map["nodes"][source_parent]
    if entry.get("candidateId") is not None:
        result["candidateId"] = id_map["candidates"][f"{source_parent}:{entry['candidateId']}"]
    if entry.get("id") is not None:
        result["id"] = id_map["sequence"][entry["id"]]
    return result


def _rewrite_audio_track(track: Mapping[str, Any], *, id_map: Mapping[str, Mapping[str, str]]) -> dict[str, Any]:
    result = dict(track)
    result["id"] = id_map["audio_tracks"][track["id"]]
    result["node_id"] = id_map["nodes"][track["node_id"]]
    return result


def _rewrite_export(export: Mapping[str, Any], *, id_map: Mapping[str, Mapping[str, str]], media_map: Mapping[str, str], project_id: str, manifest: Mapping[str, Any], now: float) -> dict[str, Any]:
    source = manifest["source"]
    result = dict(export)
    source_export_id = export["id"]
    export_id = id_map["exports"][source_export_id]
    result["id"] = export_id
    result["project_id"] = project_id
    result["video_url"] = _rewrite_media_ref(export["video_url"], media_map)
    result["filename"] = Path(result["video_url"]).name
    result["source_entries"] = [_rewrite_sequence_entry(entry, id_map=id_map) for entry in export.get("source_entries") or []]
    result["timeline_snapshot"] = _rewrite_timeline_snapshot(export.get("timeline_snapshot"), id_map=id_map, media_map=media_map)
    result["publication_proof"] = None
    result["job_id"] = None
    result["snapshot_digest"] = None
    result["deletion"] = None
    entry = _media_entry_for_ref(manifest, export["video_url"])
    result["imported_proof"] = {
        "schema_version": 1,
        "project_id": project_id,
        "export_id": export_id,
        "relative_path": result["video_url"],
        "sha256": entry["sha256"],
        "size_bytes": entry["size_bytes"],
        "source_project_id": source["project_id"],
        "source_export_id": source_export_id,
        "source_storage_id": source["storage_id"],
        "imported_at": now,
    }
    return result


def _rewrite_timeline_snapshot(value: Any, *, id_map: Mapping[str, Mapping[str, str]], media_map: Mapping[str, str]) -> Any:
    if value is None:
        return None
    if not isinstance(value, dict):
        return _rewrite_dynamic(value, id_map=id_map, media_map=media_map, source_node_id=None)
    result = {key: _rewrite_dynamic(child, id_map=id_map, media_map=media_map, source_node_id=None) for key, child in value.items() if key not in {"clips", "audio_tracks"}}
    clips = []
    for clip in value.get("clips") or []:
        if not isinstance(clip, dict):
            raise AtelierProjectPackageError("Atelier timeline clip is invalid")
        source_parent = clip.get("parentId")
        item = _rewrite_dynamic(dict(clip), id_map=id_map, media_map=media_map, source_node_id=None)
        if isinstance(item.get("clip_id"), str):
            item["clip_id"] = id_map["sequence"][item["clip_id"]]
        if isinstance(source_parent, str):
            item["parentId"] = id_map["nodes"][source_parent]
        if isinstance(item.get("candidateId"), str) and isinstance(source_parent, str):
            item["candidateId"] = id_map["candidates"][f"{source_parent}:{item['candidateId']}"]
        clips.append(item)
    result["clips"] = clips
    audio_tracks = []
    for track in value.get("audio_tracks") or []:
        if not isinstance(track, dict):
            raise AtelierProjectPackageError("Atelier timeline audio track is invalid")
        item = _rewrite_dynamic(dict(track), id_map=id_map, media_map=media_map, source_node_id=track.get("node_id") if isinstance(track.get("node_id"), str) else None)
        if isinstance(item.get("id"), str):
            item["id"] = id_map["audio_tracks"][item["id"]]
        if isinstance(item.get("node_id"), str):
            item["node_id"] = id_map["nodes"][track["node_id"]]
        audio_tracks.append(item)
    result["audio_tracks"] = audio_tracks
    return result


def _rewrite_dynamic(value: Any, *, id_map: Mapping[str, Mapping[str, str]], media_map: Mapping[str, str], source_node_id: str | None) -> Any:
    if isinstance(value, dict):
        result = {}
        for key, child in value.items():
            if key == "parent_node_id" and child is not None:
                result[key] = id_map["nodes"][child]
            elif key == "region_id" and child is not None:
                result[key] = id_map["nodes"][child]
            elif key == "reference_node_ids":
                result[key] = [id_map["nodes"][item] for item in child]
            elif key == "pending_reference_nodes":
                result[key] = [_rewrite_pending_reference(item, id_map=id_map) for item in child]
            elif key in _MEDIA_LIST_KEYS:
                result[key] = [_rewrite_media_ref(item, media_map) for item in child]
            elif key in _MEDIA_STRING_KEYS and child:
                result[key] = _rewrite_media_ref(child, media_map)
            elif key in {"node_id", "parentId"} and isinstance(child, str) and child in id_map["nodes"]:
                result[key] = id_map["nodes"][child]
            elif key in {"candidate_id", "candidateId"} and isinstance(child, str) and source_node_id is not None:
                result[key] = id_map["candidates"].get(f"{source_node_id}:{child}", child)
            else:
                result[key] = _rewrite_dynamic(child, id_map=id_map, media_map=media_map, source_node_id=source_node_id)
        return result
    if isinstance(value, list):
        return [_rewrite_dynamic(item, id_map=id_map, media_map=media_map, source_node_id=source_node_id) for item in value]
    return value


def _rewrite_pending_reference(item: Mapping[str, Any], *, id_map: Mapping[str, Mapping[str, str]]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "source_node_id": id_map["nodes"][item["source_node_id"]],
        "source_kind": item["source_kind"],
        "status": "pending_media",
    }


def _rewrite_agent_history(value: Mapping[str, Any], id_map: Mapping[str, Mapping[str, str]]) -> dict[str, Any]:
    entry = AtelierImportedAgentHistoryEntry.model_validate(value)
    source_refs = dict(entry.source_refs)
    target_refs: dict[str, str] = {}
    for source_id in source_refs:
        previous_target = entry.target_refs.get(source_id)
        target_refs[source_id] = _remap_known_identity(previous_target, id_map) or _remap_known_identity(source_id, id_map) or previous_target or source_id
    return entry.model_copy(update={"source_refs": source_refs, "target_refs": target_refs}).model_dump(mode="json")


def _remap_known_identity(value: str | None, id_map: Mapping[str, Mapping[str, str]]) -> str | None:
    if not isinstance(value, str):
        return None
    for mapping in id_map.values():
        if value in mapping:
            return mapping[value]
    return None


def _rewrite_media_ref(value: str, media_map: Mapping[str, str]) -> str:
    return media_map[_normalize_media_ref(value)]


def _media_entry_for_ref(manifest: Mapping[str, Any], ref: str) -> dict[str, Any]:
    logical = _normalize_media_ref(ref)
    for entry in manifest.get("media") or []:
        if entry.get("logical_ref") == logical:
            return entry
    raise AtelierProjectPackageError("Atelier export media proof is missing")
