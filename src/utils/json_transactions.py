"""Small undo journal for commands that associate several local JSON files.

The caller owns the directory writer lease and a thread lock for the entire
mutation/commit. A durable committed marker is the decision point. Recovery is
performed before loading domain objects. This is not an external-job journal.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Iterable

from .json_storage import (
    CorruptStorageError,
    StorageError,
    atomic_write_json,
    atomic_write_text,
    read_json,
    serialize_json,
)

logger = logging.getLogger(__name__)


class JsonFileTransaction:
    def __init__(self, root: str | Path, filenames: Iterable[str]):
        self.root = Path(root).resolve()
        self.filenames = frozenset(filenames)
        if not self.filenames or any(Path(n).name != n or n.startswith('.') for n in self.filenames):
            raise ValueError('Transaction filenames must be plain owned filenames')
        self.journal_path = self.root / '.lumenx-storage-transaction.json'
        self._failed = False

    def _path(self, name: str) -> Path:
        if name not in self.filenames:
            raise CorruptStorageError(f'Unowned transaction file: {name}')
        path = self.root / name
        if path.is_symlink():
            raise CorruptStorageError(f'Symlink is not writable storage: {path}')
        return path

    def _sync_root(self) -> None:
        if os.name == 'nt':
            return  # Same Windows durability limitation as atomic_write_json.
        descriptor = os.open(self.root, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _remove_journal(self) -> None:
        self.journal_path.unlink(missing_ok=True)
        self._sync_root()

    def _validate_journal(self, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict) or set(value) != {'version', 'phase', 'before'}:
            raise CorruptStorageError('Invalid storage transaction journal')
        if type(value['version']) is not int or value['version'] != 1 or value['phase'] not in ('prepared', 'committed'):
            raise CorruptStorageError('Unsupported storage transaction journal')
        before = value['before']
        if not isinstance(before, dict) or not before:
            raise CorruptStorageError('Empty or invalid transaction before images')
        # Validate EVERY record before any recovery mutation occurs.
        for name, contents in before.items():
            self._path(name)
            if contents is not None:
                if not isinstance(contents, str):
                    raise CorruptStorageError(f'Invalid transaction before image: {name}')
                try:
                    # Serialization rejects non-finite values. Reject duplicate
                    # keys as well; recovery must never silently lose a field.
                    def unique(pairs):
                        result = {}
                        for key, item in pairs:
                            if key in result:
                                raise ValueError('duplicate key')
                            result[key] = item
                        return result
                    serialize_json(json.loads(contents, object_pairs_hook=unique))
                    contents.encode('utf-8')
                except (ValueError, UnicodeError, RecursionError, StorageError) as exc:
                    raise CorruptStorageError(f'Invalid transaction before image: {name}') from exc
        return value

    def recover(self) -> None:
        """Undo prepared writes or retain committed writes; failures stop startup."""
        if self.journal_path.is_symlink():
            raise CorruptStorageError('Storage transaction journal cannot be a symlink')
        journal = read_json(self.journal_path, lambda: None)
        if journal is None:
            # A literal null is corrupt, not an absent journal.
            if self.journal_path.exists():
                raise CorruptStorageError('Invalid null storage transaction journal')
            return
        journal = self._validate_journal(journal)
        try:
            if journal['phase'] == 'prepared':
                for name, contents in journal['before'].items():
                    path = self._path(name)
                    if contents is None:
                        path.unlink(missing_ok=True)
                        self._sync_root()
                    else:
                        atomic_write_text(path, contents)
            self._remove_journal()
        except (OSError, StorageError) as exc:
            self._failed = True
            raise StorageError(f'Storage recovery incomplete; restart after correcting storage: {exc}', commit_uncertain=True) from exc

    def _backup(self, before: dict[str, str | None]) -> None:
        backup_dir = self.root / '.storage-backups-v1'
        if backup_dir.is_symlink():
            raise CorruptStorageError('Storage backup directory cannot be a symlink')
        for name, contents in before.items():
            target = backup_dir / name
            if target.is_symlink():
                raise CorruptStorageError('Storage backup file cannot be a symlink')
            if target.exists():
                if not target.is_file():
                    raise CorruptStorageError(f'Storage backup must be a regular file: {target}')
                read_json(target, lambda: None)
            elif contents is not None:
                atomic_write_text(target, contents)
        self._sync_root()

    def commit(self, values: dict[str, Any]) -> None:
        """Persist one command; retain initial files and recoverable undo images."""
        if self._failed:
            raise StorageError('Storage is unavailable until restart', commit_uncertain=True)
        if not values:
            return
        # Complete validation/serialization BEFORE touching the journal/data.
        for name, value in values.items():
            self._path(name)
            serialize_json(value)
        self.recover()
        before = {}
        try:
            for name in values:
                path = self._path(name)
                read_json(path, lambda: None)  # Reject corrupt source, never overwrite it.
                before[name] = path.read_bytes().decode('utf-8') if path.exists() else None
            self._backup(before)
            journal = {'version': 1, 'phase': 'prepared', 'before': before}
            atomic_write_json(self.journal_path, journal)
            for name, value in values.items():
                atomic_write_json(self._path(name), value)
            journal['phase'] = 'committed'
            try:
                atomic_write_json(self.journal_path, journal)
            except StorageError as exc:
                if exc.commit_uncertain:
                    # Its replacement is visible. Never undo a potentially
                    # committed decision or permit another write in-process.
                    self._failed = True
                raise
        except (StorageError, OSError, UnicodeError) as exc:
            if self._failed:
                raise
            try:
                self.recover()
            except StorageError as recovery_error:
                self._failed = True
                raise StorageError(f'Save and rollback failed; restart required: {recovery_error}', commit_uncertain=True) from exc
            raise StorageError(f'Save failed; previous files restored: {exc}') from exc
        # The command is durable. Cleanup is optional and MUST NOT undo it.
        try:
            self._remove_journal()
        except OSError:
            logger.warning('Committed transaction retained for cleanup on next startup', exc_info=True)
