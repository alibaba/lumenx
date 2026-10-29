"""App-owned Script Editor document storage.

The document editor stores sidecar JSON under the current Studio data_dir. It
must not read or migrate the legacy ~/.tron project folder implicitly.
"""

from __future__ import annotations

import re
import threading
import uuid
from contextlib import nullcontext
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ...utils.json_storage import (
    StorageConflictError,
    StorageError,
    atomic_write_json,
    read_json,
    serialize_json,
)

_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_SCHEMA_VERSION = 1
_EMPTY_DOCUMENT = {"type": "doc", "content": []}


class UnsafeDocumentId(ValueError):
    """Raised when a route token cannot safely be used as one path segment."""


class DocumentProjectNotFound(ValueError):
    """Raised when a document is requested for a missing Studio project."""


class DocumentNotFound(ValueError):
    """Raised when an operation requires an existing document."""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_id(value: str, label: str) -> str:
    if not isinstance(value, str) or not _SAFE_ID_RE.fullmatch(value):
        raise UnsafeDocumentId(f"Invalid {label}")
    return value


def _snapshot_id(created_at: str) -> str:
    safe_time = re.sub(r"[^A-Za-z0-9]", "", created_at)
    return f"{safe_time}-{uuid.uuid4().hex[:8]}"


def _json_size(value: dict[str, Any]) -> int:
    return len(serialize_json(value).encode("utf-8"))


class DocumentStore:
    def __init__(
        self,
        *,
        data_dir: str | Path,
        writer_lock: Any | None,
        lock: threading.RLock | None,
        runtime_check: Callable[[], None],
        project_exists: Callable[[str], Any],
        mark_storage_failed: Callable[[StorageError], None] | None = None,
    ):
        self.data_dir = Path(data_dir).expanduser().resolve()
        self.root = self.data_dir / "documents"
        self.writer_lock = writer_lock
        self.lock = lock
        self.runtime_check = runtime_check
        self.project_exists = project_exists
        self.mark_storage_failed = mark_storage_failed

    def _guard(self):
        return self.lock if self.lock is not None else nullcontext()

    def _check_runtime(self) -> None:
        self.runtime_check()
        if self.writer_lock is not None:
            self.writer_lock.assert_owned()

    def _record_storage_failure(self, error: StorageError) -> None:
        if self.mark_storage_failed is not None:
            self.mark_storage_failed(error)

    def _ensure_project(self, project_id: str) -> str:
        project_id = _safe_id(project_id, "project_id")
        try:
            self.project_exists(project_id)
        except ValueError as exc:
            raise DocumentProjectNotFound("Project not found") from exc
        return project_id

    def ensure_project(self, project_id: str) -> dict[str, Any]:
        with self._guard():
            self._check_runtime()
            project_id = self._ensure_project(project_id)
            return {"project_id": project_id}

    def _root_dir(self) -> Path:
        if self.root.is_symlink():
            raise StorageError(f"Document storage root cannot be a symlink: {self.root}")
        root = self.root.resolve()
        try:
            root.relative_to(self.data_dir)
        except ValueError as exc:
            raise StorageError(f"Document storage root escaped data directory: {self.root}") from exc
        return root

    def _project_dir(self, project_id: str) -> Path:
        root = self._root_dir()
        raw_path = self.root / project_id
        if raw_path.is_symlink():
            raise StorageError(f"Document storage path cannot be a symlink: {raw_path}")
        path = raw_path.resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise UnsafeDocumentId("Invalid project_id") from exc
        return path

    @staticmethod
    def _validate_envelope(value: Any, *, project_id: str, snapshot: bool = False) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise StorageError("Invalid document envelope")
        schema_version = value.get("schema_version")
        if type(schema_version) is not int or schema_version != _SCHEMA_VERSION:
            raise StorageError("Unsupported document schema")
        if value.get("project_id") != project_id:
            raise StorageError("Document project does not match owner")
        revision = value.get("revision")
        if type(revision) is not int or revision < 0:
            raise StorageError("Invalid document revision")
        content = value.get("content")
        if not isinstance(content, dict):
            raise StorageError("Invalid document content")
        if "updated_at" not in value:
            raise StorageError("Invalid document updated_at")
        updated_at = value.get("updated_at")
        if updated_at is not None and not isinstance(updated_at, str):
            raise StorageError("Invalid document updated_at")
        if snapshot:
            if type(value.get("snapshot_id")) is not str or type(value.get("created_at")) is not str:
                raise StorageError("Invalid document snapshot")
        # Validate finite JSON and duplicate-key-safe serialization before returning.
        serialize_json(value)
        return deepcopy(value)

    def _validate_write_envelope(self, value: dict[str, Any], *, project_id: str, snapshot: bool = False) -> dict[str, Any]:
        try:
            return self._validate_envelope(value, project_id=project_id, snapshot=snapshot)
        except StorageConflictError:
            raise
        except StorageError as exc:
            self._record_storage_failure(exc)
            raise

    def _default_envelope(self, project_id: str) -> dict[str, Any]:
        return {
            "project_id": project_id,
            "revision": 0,
            "updated_at": None,
            "content": deepcopy(_EMPTY_DOCUMENT),
        }

    def _stored_default_envelope(self, project_id: str) -> dict[str, Any]:
        return {
            "schema_version": _SCHEMA_VERSION,
            "project_id": project_id,
            "revision": 0,
            "updated_at": None,
            "content": deepcopy(_EMPTY_DOCUMENT),
        }

    @staticmethod
    def _public_envelope(value: dict[str, Any]) -> dict[str, Any]:
        return {
            "project_id": value["project_id"],
            "revision": value["revision"],
            "updated_at": value["updated_at"],
            "content": deepcopy(value["content"]),
        }

    def _document_path(self, project_id: str) -> Path:
        return self._project_dir(project_id) / "document.json"

    def _history_dir(self, project_id: str) -> Path:
        path = self._project_dir(project_id) / "history"
        if path.is_symlink():
            raise StorageError(f"Document history path cannot be a symlink: {path}")
        return path

    def _read_snapshot_envelope(self, project_id: str, path: Path) -> dict[str, Any]:
        value = self._validate_envelope(read_json(path, lambda: None), project_id=project_id, snapshot=True)
        if value["snapshot_id"] != path.stem:
            raise StorageError("Document snapshot id does not match filename")
        return value

    def _read_document_envelope(self, project_id: str) -> dict[str, Any]:
        path = self._document_path(project_id)
        if path.is_symlink():
            raise StorageError(f"Document file cannot be a symlink: {path}")
        value = read_json(path, lambda: None)
        if value is None:
            if path.exists():
                raise StorageError("Invalid null document storage")
            return self._stored_default_envelope(project_id)
        return self._validate_envelope(value, project_id=project_id)

    def _write_json(self, path: Path, value: dict[str, Any]) -> None:
        try:
            atomic_write_json(path, value)
        except StorageConflictError:
            raise
        except StorageError as exc:
            self._record_storage_failure(exc)
            raise

    def load(self, project_id: str) -> dict[str, Any]:
        with self._guard():
            self._check_runtime()
            project_id = self._ensure_project(project_id)
            return self._public_envelope(self._read_document_envelope(project_id))

    def save(
        self,
        project_id: str,
        content: dict[str, Any],
        *,
        expected_revision: int,
        create_snapshot: bool = False,
    ) -> dict[str, Any]:
        with self._guard():
            self._check_runtime()
            project_id = self._ensure_project(project_id)
            current = self._read_document_envelope(project_id)
            if current["revision"] != expected_revision:
                raise StorageConflictError("Document changed; reload before saving")
            if create_snapshot and current["revision"] > 0:
                self._create_snapshot_from_current(project_id, current)
            candidate = {
                "schema_version": _SCHEMA_VERSION,
                "project_id": project_id,
                "revision": current["revision"] + int(current["content"] != content),
                "updated_at": _utc_now_iso(),
                "content": deepcopy(content),
            }
            candidate = self._validate_write_envelope(candidate, project_id=project_id)
            self._write_json(self._document_path(project_id), candidate)
            return self._public_envelope(candidate)

    def _create_snapshot_from_current(self, project_id: str, current: dict[str, Any]) -> dict[str, Any]:
        if current["revision"] == 0:
            raise DocumentNotFound("No document to snapshot")
        created_at = _utc_now_iso()
        snapshot_id = _snapshot_id(created_at)
        snapshot = {
            **deepcopy(current),
            "snapshot_id": snapshot_id,
            "created_at": created_at,
        }
        snapshot = self._validate_write_envelope(snapshot, project_id=project_id, snapshot=True)
        path = self._history_dir(project_id) / f"{snapshot_id}.json"
        self._write_json(path, snapshot)
        return self._snapshot_info(project_id, path, snapshot)

    def create_snapshot(self, project_id: str) -> dict[str, Any]:
        with self._guard():
            self._check_runtime()
            project_id = self._ensure_project(project_id)
            current = self._read_document_envelope(project_id)
            return self._create_snapshot_from_current(project_id, current)

    def list_snapshots(self, project_id: str) -> list[dict[str, Any]]:
        with self._guard():
            self._check_runtime()
            project_id = self._ensure_project(project_id)
            history = self._history_dir(project_id)
            if not history.exists():
                return []
            if not history.is_dir():
                raise StorageError(f"Document history is not a directory: {history}")
            snapshots = []
            for path in history.iterdir():
                if path.suffix != ".json" or not path.is_file():
                    continue
                if path.is_symlink():
                    raise StorageError(f"Document snapshot cannot be a symlink: {path}")
                value = self._read_snapshot_envelope(project_id, path)
                snapshots.append(self._snapshot_info(project_id, path, value))
            snapshots.sort(key=lambda item: item["created_at"], reverse=True)
            return snapshots

    def restore(self, project_id: str, snapshot_id: str, *, expected_revision: int) -> dict[str, Any]:
        with self._guard():
            self._check_runtime()
            project_id = self._ensure_project(project_id)
            snapshot_id = _safe_id(snapshot_id, "snapshot_id")
            current = self._read_document_envelope(project_id)
            if current["revision"] != expected_revision:
                raise StorageConflictError("Document changed; reload before restoring")
            path = self._history_dir(project_id) / f"{snapshot_id}.json"
            if path.is_symlink():
                raise StorageError(f"Document snapshot cannot be a symlink: {path}")
            if not path.exists():
                raise DocumentNotFound("Snapshot not found")
            snapshot = self._read_snapshot_envelope(project_id, path)
            restored = {
                "schema_version": _SCHEMA_VERSION,
                "project_id": project_id,
                "revision": current["revision"] + 1,
                "updated_at": _utc_now_iso(),
                "content": deepcopy(snapshot["content"]),
            }
            restored = self._validate_write_envelope(restored, project_id=project_id)
            self._write_json(self._document_path(project_id), restored)
            return self._public_envelope(restored)

    def _sidecar_path(self, project_id: str, filename: str) -> Path:
        return self._project_dir(project_id) / filename

    @staticmethod
    def _validate_revision(value: Any, label: str) -> int:
        if type(value) is not int or value < 0:
            raise StorageError(f"Invalid {label}")
        return value

    def _validate_sidecar_envelope(
        self,
        value: Any,
        *,
        project_id: str,
        payload_key: str,
    ) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise StorageError("Invalid document sidecar envelope")
        schema_version = value.get("schema_version")
        if type(schema_version) is not int or schema_version != _SCHEMA_VERSION:
            raise StorageError("Unsupported document sidecar schema")
        if value.get("project_id") != project_id:
            raise StorageError("Document sidecar project does not match owner")
        self._validate_revision(value.get("revision"), "document sidecar revision")
        if "source_document_revision" not in value:
            raise StorageError("Invalid source document revision")
        source_revision = value.get("source_document_revision")
        if source_revision is not None:
            self._validate_revision(source_revision, "source document revision")
        if "updated_at" not in value:
            raise StorageError("Invalid document sidecar updated_at")
        updated_at = value.get("updated_at")
        if updated_at is not None and not isinstance(updated_at, str):
            raise StorageError("Invalid document sidecar updated_at")
        payload = value.get(payload_key)
        if not isinstance(payload, dict):
            raise StorageError("Invalid document sidecar payload")
        serialize_json(value)
        return deepcopy(value)

    def _validate_write_sidecar_envelope(
        self,
        value: dict[str, Any],
        *,
        project_id: str,
        payload_key: str,
    ) -> dict[str, Any]:
        try:
            return self._validate_sidecar_envelope(value, project_id=project_id, payload_key=payload_key)
        except StorageConflictError:
            raise
        except StorageError as exc:
            self._record_storage_failure(exc)
            raise

    def _default_derivation_envelope(self, project_id: str) -> dict[str, Any]:
        return {
            "schema_version": _SCHEMA_VERSION,
            "project_id": project_id,
            "revision": 0,
            "updated_at": None,
            "source_document_revision": None,
            "data": {},
        }

    def _default_shot_blocks_envelope(self, project_id: str) -> dict[str, Any]:
        return {
            "schema_version": _SCHEMA_VERSION,
            "project_id": project_id,
            "revision": 0,
            "updated_at": None,
            "source_document_revision": None,
            "blocks": {},
        }

    def _read_sidecar_envelope(self, project_id: str, filename: str, payload_key: str, default: dict[str, Any]) -> dict[str, Any]:
        path = self._sidecar_path(project_id, filename)
        if path.is_symlink():
            raise StorageError(f"Document sidecar cannot be a symlink: {path}")
        value = read_json(path, lambda: None)
        if value is None:
            if path.exists():
                raise StorageError("Invalid null document sidecar storage")
            return deepcopy(default)
        return self._validate_sidecar_envelope(value, project_id=project_id, payload_key=payload_key)

    @staticmethod
    def _public_derivation(value: dict[str, Any]) -> dict[str, Any]:
        return {
            "project_id": value["project_id"],
            "revision": value["revision"],
            "updated_at": value["updated_at"],
            "source_document_revision": value["source_document_revision"],
            "data": deepcopy(value["data"]),
        }

    @staticmethod
    def _public_shot_blocks(value: dict[str, Any]) -> dict[str, Any]:
        return {
            "project_id": value["project_id"],
            "revision": value["revision"],
            "updated_at": value["updated_at"],
            "source_document_revision": value["source_document_revision"],
            "blocks": deepcopy(value["blocks"]),
        }

    def _assert_source_document_revision(self, project_id: str, source_document_revision: int) -> dict[str, Any]:
        current_document = self._read_document_envelope(project_id)
        if current_document["revision"] != source_document_revision:
            raise StorageConflictError("Document changed; reload before syncing sidecar data")
        return current_document

    def load_derivation(self, project_id: str) -> dict[str, Any]:
        with self._guard():
            self._check_runtime()
            project_id = self._ensure_project(project_id)
            current = self._read_sidecar_envelope(
                project_id,
                "derivation.json",
                "data",
                self._default_derivation_envelope(project_id),
            )
            return self._public_derivation(current)

    def save_derivation(
        self,
        project_id: str,
        data: dict[str, Any],
        *,
        expected_revision: int,
        source_document_revision: int,
    ) -> dict[str, Any]:
        with self._guard():
            self._check_runtime()
            project_id = self._ensure_project(project_id)
            self._assert_source_document_revision(project_id, source_document_revision)
            current = self._read_sidecar_envelope(
                project_id,
                "derivation.json",
                "data",
                self._default_derivation_envelope(project_id),
            )
            if current["revision"] != expected_revision:
                raise StorageConflictError("Derivation changed; reload before saving")
            candidate = {
                "schema_version": _SCHEMA_VERSION,
                "project_id": project_id,
                "revision": current["revision"] + int(
                    current["data"] != data or current["source_document_revision"] != source_document_revision
                ),
                "updated_at": _utc_now_iso(),
                "source_document_revision": source_document_revision,
                "data": deepcopy(data),
            }
            candidate = self._validate_write_sidecar_envelope(candidate, project_id=project_id, payload_key="data")
            self._write_json(self._sidecar_path(project_id, "derivation.json"), candidate)
            return self._public_derivation(candidate)

    def load_shot_blocks(self, project_id: str) -> dict[str, Any]:
        with self._guard():
            self._check_runtime()
            project_id = self._ensure_project(project_id)
            current = self._read_sidecar_envelope(
                project_id,
                "shot_blocks.json",
                "blocks",
                self._default_shot_blocks_envelope(project_id),
            )
            return self._public_shot_blocks(current)

    def confirm_shot_block(
        self,
        project_id: str,
        shot_id: str,
        block: dict[str, Any],
        *,
        expected_revision: int,
        source_document_revision: int,
    ) -> dict[str, Any]:
        with self._guard():
            self._check_runtime()
            project_id = self._ensure_project(project_id)
            shot_id = _safe_id(shot_id, "shot_id")
            self._assert_source_document_revision(project_id, source_document_revision)
            current = self._read_sidecar_envelope(
                project_id,
                "shot_blocks.json",
                "blocks",
                self._default_shot_blocks_envelope(project_id),
            )
            if current["revision"] != expected_revision:
                raise StorageConflictError("Shot blocks changed; reload before confirming")
            confirmed = {
                **deepcopy(block),
                "shot_id": shot_id,
                "status": "confirmed",
                "source_document_revision": source_document_revision,
                "confirmed_at": _utc_now_iso(),
            }
            blocks = deepcopy(current["blocks"])
            blocks[shot_id] = confirmed
            candidate = {
                "schema_version": _SCHEMA_VERSION,
                "project_id": project_id,
                "revision": current["revision"] + int(
                    current["blocks"].get(shot_id) != confirmed
                    or current["source_document_revision"] != source_document_revision
                ),
                "updated_at": _utc_now_iso(),
                "source_document_revision": source_document_revision,
                "blocks": blocks,
            }
            candidate = self._validate_write_sidecar_envelope(candidate, project_id=project_id, payload_key="blocks")
            self._write_json(self._sidecar_path(project_id, "shot_blocks.json"), candidate)
            result = deepcopy(confirmed)
            result["revision"] = candidate["revision"]
            result["source_document_revision"] = source_document_revision
            return result

    @staticmethod
    def _snapshot_info(project_id: str, path: Path, value: dict[str, Any]) -> dict[str, Any]:
        return {
            "project_id": project_id,
            "snapshot_id": value["snapshot_id"],
            "timestamp": value["snapshot_id"],
            "created_at": value["created_at"],
            "revision": value["revision"],
            "size_bytes": _json_size(value),
        }
