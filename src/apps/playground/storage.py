"""Playground storage layer — JSON-file persistence for generation history and templates."""

import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, List, Optional

from .models import PlaygroundAttempt, PlaygroundGeneration, PlaygroundRequestReceipt, PlaygroundRequestTombstone, PlaygroundTemplate
from ...utils import get_logger
from ...utils.json_storage import CorruptStorageError, DirectoryWriterLock, StorageConflictError, StorageError, atomic_write_json, read_json

logger = get_logger(__name__)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class PlaygroundStorage:
    HISTORY_PATH = "output/playground_history.json"
    TEMPLATES_PATH = "output/playground_templates.json"

    def __init__(
        self,
        data_dir: str | os.PathLike[str] = "output",
        *,
        writer_lock: DirectoryWriterLock | None = None,
        runtime_check: Callable[[], None] | None = None,
    ):
        self.data_dir = Path(data_dir).expanduser().resolve()
        self.history_path = self.data_dir / "playground_history.json"
        self.templates_path = self.data_dir / "playground_templates.json"
        self._history: List[PlaygroundGeneration | PlaygroundRequestTombstone] = []
        self._templates: List[PlaygroundTemplate] = []
        self._lock = threading.RLock()
        self._write_error: Optional[StorageError] = None
        self._closed = False
        self._active_video_attempts: dict[tuple[str, str], object] = {}
        self._runtime_check = runtime_check
        self._owns_writer_lock = writer_lock is None
        self._writer_lock = writer_lock if writer_lock is not None else DirectoryWriterLock(self.data_dir)
        try:
            if self._writer_lock.data_dir != self.data_dir:
                raise StorageConflictError("Playground writer lock belongs to a different data directory")
            if self._owns_writer_lock:
                self._writer_lock.acquire()
            self._load()
        except BaseException:
            self.close()
            raise

    def claim_video_attempt_operation(self, generation_id: str, attempt_id: str, token: object) -> None:
        with self._lock:
            key = (generation_id, attempt_id)
            if key in self._active_video_attempts:
                raise StorageConflictError("Playground video attempt operation already running")
            self._active_video_attempts[key] = token

    def release_video_attempt_operation(self, generation_id: str, attempt_id: str, token: object) -> None:
        with self._lock:
            key = (generation_id, attempt_id)
            if self._active_video_attempts.get(key) is token:
                del self._active_video_attempts[key]

    def close(self) -> None:
        """Wait for writes, then release only a lease owned by this storage."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            if self._owns_writer_lock:
                self._writer_lock.close()

    # ------------------------------------------------------------------
    # Internal persistence
    # ------------------------------------------------------------------

    def _load(self) -> None:
        """Read both stores before replacing memory or clearing an uncertain commit."""
        with self._lock:
            self._require_runtime()
            history = self._load_history(str(self.history_path))
            templates = self._load_file(str(self.templates_path), PlaygroundTemplate)
            self._history = history
            self._templates = templates
            self._write_error = None

    @staticmethod
    def _load_file(path: str, model_cls):
        """Read a JSON array file and parse each element into *model_cls*."""
        raw = read_json(path, list)
        try:
            if not isinstance(raw, list):
                raise ValueError("expected an array of records")
            return [model_cls.model_validate(item) for item in raw]
        except (TypeError, ValueError) as exc:
            raise CorruptStorageError(f"Invalid Playground storage {path}: {exc}") from exc

    @staticmethod
    def _load_history(path: str) -> list[PlaygroundGeneration | PlaygroundRequestTombstone]:
        raw = read_json(path, list)
        try:
            if not isinstance(raw, list):
                raise ValueError("expected an array of records")
            records: list[PlaygroundGeneration | PlaygroundRequestTombstone] = []
            for item in raw:
                if isinstance(item, dict) and item.get("record_type") == "tombstone":
                    records.append(PlaygroundRequestTombstone.model_validate(item))
                else:
                    records.append(PlaygroundGeneration.model_validate(item))
            return records
        except (TypeError, ValueError) as exc:
            raise CorruptStorageError(f"Invalid Playground storage {path}: {exc}") from exc

    def _save_history(self, items: Optional[List[PlaygroundGeneration | PlaygroundRequestTombstone]] = None) -> None:
        with self._lock:
            candidate = self._history if items is None else items
            self._save_file(str(self.history_path), candidate)
            self._history = candidate

    def _save_templates(self, items: Optional[List[PlaygroundTemplate]] = None) -> None:
        with self._lock:
            candidate = self._templates if items is None else items
            self._save_file(str(self.templates_path), candidate)
            self._templates = candidate

    def _require_runtime(self) -> None:
        if self._closed:
            raise StorageError("Playground storage is closed")
        if self._runtime_check is not None:
            self._runtime_check()
        self._writer_lock.assert_owned()

    def _require_writable(self) -> None:
        self._require_runtime()
        if self._write_error is not None:
            raise StorageError(
                "Playground storage has an uncertain commit; reload storage or restart before writing",
                commit_uncertain=True,
            ) from self._write_error

    def require_writable(self) -> None:
        """Check persistence health before accepting an external generation call."""
        with self._lock:
            self._require_writable()

    def _save_file(self, path: str, items: list) -> None:
        with self._lock:
            self._require_writable()
            try:
                payload = [item.model_dump(mode="json") for item in items]
            except (TypeError, ValueError) as exc:
                raise StorageError(f"Cannot serialize Playground storage {path}: {exc}") from exc
            try:
                atomic_write_json(path, payload)
            except StorageError as exc:
                if exc.commit_uncertain:
                    self._write_error = exc
                raise

    # ------------------------------------------------------------------
    # History CRUD
    # ------------------------------------------------------------------

    def add_generation(self, gen: PlaygroundGeneration) -> None:
        """Append a generation record and persist."""
        with self._lock:
            self._require_writable()
            self._save_history([*self._history, gen.model_copy(deep=True)])

    def admit_generation(self, gen: PlaygroundGeneration) -> PlaygroundRequestReceipt:
        """Persist or replay a client-owned generation admission."""
        with self._lock:
            self._require_writable()
            if not gen.client_request_id or not gen.request_fingerprint:
                raise ValueError("Playground admission requires request identity")
            existing = self.read_request(gen.client_request_id)
            if existing is not None:
                fingerprint = existing.generation.request_fingerprint if existing.generation else (
                    existing.tombstone.request_fingerprint if existing.tombstone else None
                )
                if fingerprint != gen.request_fingerprint:
                    raise StorageConflictError("Playground request id was already used for a different request")
                return existing.model_copy(deep=True)
            self._save_history([*self._history, gen.model_copy(deep=True)])
            return PlaygroundRequestReceipt(replayed=False, request_status="accepted", generation=gen.model_copy(deep=True))

    def read_request(self, client_request_id: str) -> Optional[PlaygroundRequestReceipt]:
        with self._lock:
            for item in self._history:
                if isinstance(item, PlaygroundRequestTombstone):
                    if item.client_request_id == client_request_id:
                        return PlaygroundRequestReceipt(
                            replayed=True,
                            request_status="deleted",
                            tombstone=item.model_copy(deep=True),
                        )
                    continue
                if item.client_request_id == client_request_id:
                    return PlaygroundRequestReceipt(
                        replayed=True,
                        request_status="accepted",
                        generation=item.model_copy(deep=True),
                    )
            return None

    def get_generation(self, gen_id: str) -> Optional[PlaygroundGeneration]:
        """Look up a generation by its id."""
        with self._lock:
            for gen in self._history:
                if isinstance(gen, PlaygroundRequestTombstone):
                    continue
                if gen.id == gen_id:
                    return gen.model_copy(deep=True)
            return None

    def list_history(
        self, limit: int = 50, offset: int = 0
    ) -> List[PlaygroundGeneration]:
        """Return paginated history, newest first."""
        with self._lock:
            ordered = list(reversed(self._history))
            generations = [item for item in ordered if isinstance(item, PlaygroundGeneration)]
            return [gen.model_copy(deep=True) for gen in generations[offset : offset + limit]]

    @staticmethod
    def _is_terminal_status(status: str) -> bool:
        return status in {"completed", "failed", "cancelled"}

    @classmethod
    def _can_replace_status(cls, current: str, incoming: str) -> bool:
        if cls._is_terminal_status(current) and incoming != current:
            return False
        return True

    def _merge_worker_generation(self, existing: PlaygroundGeneration, gen: PlaygroundGeneration) -> PlaygroundGeneration:
        merged = existing.model_copy(deep=True)
        accepted_attempts = bool(merged.attempts)
        attempts = {attempt.attempt_id: attempt for attempt in merged.attempts}
        for incoming in gen.attempts:
            current = attempts.get(incoming.attempt_id)
            if current is not None:
                if current.ordinal != incoming.ordinal:
                    continue
                if self._can_replace_status(current.status, incoming.status):
                    current.status = incoming.status
                    current.error = incoming.error
                    if incoming.provider_execution is not None:
                        current.provider_execution = dict(incoming.provider_execution)
            elif not accepted_attempts:
                added_attempt = incoming.model_copy(deep=True)
                merged.attempts.append(added_attempt)
                attempts[added_attempt.attempt_id] = added_attempt
        accepted_attempt_ids = {attempt.attempt_id for attempt in merged.attempts}
        outputs = {output.id: output for output in merged.outputs}
        for incoming in gen.outputs:
            if incoming.id in outputs:
                outputs[incoming.id].saved_to_library |= incoming.saved_to_library
            elif incoming.attempt_id is None or not accepted_attempt_ids or incoming.attempt_id in accepted_attempt_ids:
                added = incoming.model_copy(deep=True)
                merged.outputs.append(added)
                outputs[added.id] = added
        if self._can_replace_status(merged.status, gen.status):
            merged.status = gen.status
            merged.error = gen.error
        return merged

    def claim_generation_for_processing(self, gen_id: str) -> Optional[PlaygroundGeneration]:
        """Persist a single pending->processing claim and return the accepted request.

        Repeated background callbacks and terminal records are explicit no-ops;
        only a saved processing claim may call a paid provider.
        """
        with self._lock:
            self._require_writable()
            for i, existing in enumerate(self._history):
                if isinstance(existing, PlaygroundRequestTombstone):
                    continue
                if existing.id == gen_id:
                    if existing.status != "pending":
                        return None
                    claimed = existing.model_copy(deep=True)
                    claimed.status = "processing"
                    claimed.error = None
                    for attempt in claimed.attempts:
                        if attempt.status == "pending":
                            attempt.status = "processing"
                    candidate = list(self._history)
                    candidate[i] = claimed.model_copy(deep=True)
                    self._save_history(candidate)
                    return claimed.model_copy(deep=True)
            return None

    def claim_generation_attempt_for_processing(self, gen_id: str, attempt_id: str) -> Optional[PlaygroundGeneration]:
        """Persist a single attempt pending->processing claim and return the updated generation.

        This is the only durable first-submit permit for Core Playground video
        attempts. It does not grant sibling attempts provider-submit authority.
        """
        with self._lock:
            self._require_writable()
            for i, existing in enumerate(self._history):
                if isinstance(existing, PlaygroundRequestTombstone):
                    continue
                if existing.id != gen_id:
                    continue
                if self._is_terminal_status(existing.status):
                    return None
                claimed = existing.model_copy(deep=True)
                target = next((attempt for attempt in claimed.attempts if attempt.attempt_id == attempt_id), None)
                if target is None or target.status != "pending":
                    return None
                claimed.status = "processing"
                claimed.error = None
                target.status = "processing"
                target.error = None
                candidate = list(self._history)
                candidate[i] = claimed.model_copy(deep=True)
                self._save_history(candidate)
                return claimed.model_copy(deep=True)
            return None

    def update_generation_attempt(
        self,
        generation_id: str,
        attempt_id: str,
        command: Callable[[PlaygroundGeneration, PlaygroundAttempt], None],
        *,
        require_processing: bool = False,
        expected_status: str | None = None,
        expected_provider_execution: dict | None = None,
    ) -> PlaygroundGeneration:
        """Atomically mutate one attempt without replacing sibling attempt records.

        The status/provider expectation is a per-attempt CAS guard. It
        prevents a stale executor from advancing a record that another executor
        already moved to a different irreversible state.
        """
        with self._lock:
            self._require_writable()
            for i, existing in enumerate(self._history):
                if isinstance(existing, PlaygroundRequestTombstone):
                    continue
                if existing.id != generation_id:
                    continue
                if require_processing and existing.status != "processing":
                    raise StorageConflictError(f"Generation {generation_id} is no longer processing")
                updated = existing.model_copy(deep=True)
                target = next((attempt for attempt in updated.attempts if attempt.attempt_id == attempt_id), None)
                if target is None:
                    raise StorageConflictError(f"Attempt {attempt_id} no longer exists")
                if expected_status is not None and target.status != expected_status:
                    raise StorageConflictError(f"Attempt {attempt_id} status changed")
                if expected_provider_execution is not None:
                    current = target.provider_execution
                    if not isinstance(current, dict) or current != expected_provider_execution:
                        raise StorageConflictError(f"Attempt {attempt_id} provider record changed")
                command(updated, target)
                candidate = list(self._history)
                candidate[i] = updated
                self._save_history(candidate)
                return updated.model_copy(deep=True)
            raise StorageConflictError(f"Generation {generation_id} no longer exists")

    def update_generation(self, gen: PlaygroundGeneration) -> None:
        """Persist worker-owned progress and append outputs without replacing user choices.

        Accepted request fields and existing output media are immutable here.
        An older worker snapshot cannot remove newer outputs or clear a library flag.
        """
        with self._lock:
            self._require_writable()
            for i, existing in enumerate(self._history):
                if isinstance(existing, PlaygroundRequestTombstone):
                    continue
                if existing.id == gen.id:
                    candidate = list(self._history)
                    candidate[i] = self._merge_worker_generation(existing, gen)
                    self._save_history(candidate)
                    return
            raise StorageConflictError(f"Generation {gen.id} no longer exists")

    def update_claimed_generation(self, gen: PlaygroundGeneration) -> bool:
        """Merge worker progress only while the stored row is still processing."""
        with self._lock:
            self._require_writable()
            for i, existing in enumerate(self._history):
                if isinstance(existing, PlaygroundRequestTombstone):
                    continue
                if existing.id == gen.id:
                    if existing.status != "processing":
                        return False
                    candidate = list(self._history)
                    candidate[i] = self._merge_worker_generation(existing, gen)
                    self._save_history(candidate)
                    return True
            raise StorageConflictError(f"Generation {gen.id} no longer exists")

    def assert_claimed_generation(self, gen_id: str) -> None:
        """Fail before paid work if the accepted worker no longer owns the row."""
        with self._lock:
            self._require_writable()
            for existing in self._history:
                if isinstance(existing, PlaygroundRequestTombstone):
                    continue
                if existing.id == gen_id:
                    if existing.status != "processing":
                        raise StorageConflictError(f"Generation {gen_id} is no longer processing")
                    return
            raise StorageConflictError(f"Generation {gen_id} no longer exists")

    def mark_output_saved(self, generation_id: str, output_id: str) -> None:
        """Update only the user-owned library flag against the current generation."""
        with self._lock:
            self._require_writable()
            for i, existing in enumerate(self._history):
                if isinstance(existing, PlaygroundRequestTombstone):
                    continue
                if existing.id == generation_id:
                    updated = existing.model_copy(deep=True)
                    for output in updated.outputs:
                        if output.id == output_id:
                            if output.saved_to_library:
                                return
                            output.saved_to_library = True
                            candidate = list(self._history)
                            candidate[i] = updated
                            self._save_history(candidate)
                            return
                    raise StorageConflictError(f"Output {output_id} no longer exists")
            raise StorageConflictError(f"Generation {generation_id} no longer exists")

    def delete_generation(self, gen_id: str) -> bool:
        """Remove a generation by id. Returns True if found and deleted."""
        with self._lock:
            self._require_writable()
            for i, gen in enumerate(self._history):
                if isinstance(gen, PlaygroundRequestTombstone):
                    continue
                if gen.id == gen_id:
                    if gen.client_request_id and gen.request_fingerprint:
                        tombstone = PlaygroundRequestTombstone(
                            client_request_id=gen.client_request_id,
                            request_contract_version=gen.request_contract_version,
                            request_fingerprint=gen.request_fingerprint,
                            generation_id=gen.id,
                            deleted_at=_utc_now(),
                            deleted_status=gen.status,
                        )
                        self._save_history(self._history[:i] + [tombstone] + self._history[i + 1:])
                    else:
                        self._save_history(self._history[:i] + self._history[i + 1:])
                    return True
            return False

    # ------------------------------------------------------------------
    # Template CRUD
    # ------------------------------------------------------------------

    def add_template(self, template: PlaygroundTemplate) -> None:
        """Append a template record and persist."""
        with self._lock:
            self._require_writable()
            self._save_templates([*self._templates, template.model_copy(deep=True)])

    def get_template(self, template_id: str) -> Optional[PlaygroundTemplate]:
        """Look up a template by its id."""
        with self._lock:
            for t in self._templates:
                if t.id == template_id:
                    return t.model_copy(deep=True)
            return None

    def list_templates(self) -> List[PlaygroundTemplate]:
        """Return all templates."""
        with self._lock:
            return [template.model_copy(deep=True) for template in self._templates]

    def update_template(self, template: PlaygroundTemplate) -> None:
        """Replace an existing template (matched by id) and persist."""
        with self._lock:
            self._require_writable()
            for i, existing in enumerate(self._templates):
                if existing.id == template.id:
                    candidate = list(self._templates)
                    candidate[i] = template.model_copy(deep=True)
                    self._save_templates(candidate)
                    return
            raise StorageConflictError(f"Template {template.id} no longer exists")

    def delete_template(self, template_id: str) -> bool:
        """Remove a template by id. Returns True if found and deleted."""
        with self._lock:
            self._require_writable()
            for i, t in enumerate(self._templates):
                if t.id == template_id:
                    self._save_templates(self._templates[:i] + self._templates[i + 1:])
                    return True
            return False
