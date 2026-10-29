"""Atelier project package service boundary.

The HTTP layer owns request validation. This service owns storage/media
coordination so package export/import keeps Atelier's committed-state and local
media guarantees in one place.
"""

from __future__ import annotations

from typing import Callable

from ...utils.json_storage import StorageError
from .models import AtelierProject
from .project_package import (
    AtelierProjectPackageArtifact,
    PreparedAtelierProjectImport,
    build_package,
    discard_prepared_import,
    prepare_import,
)
from .storage import AtelierStorage


class AtelierProjectPackageService:
    """Export/import independent Atelier project packages."""

    def __init__(self, storage: AtelierStorage, *, storage_id: str | Callable[[], str]) -> None:
        if callable(storage_id):
            self._storage_id_provider = storage_id
        elif isinstance(storage_id, str) and storage_id:
            self._storage_id_provider = lambda: storage_id
        else:
            raise ValueError("storage_id is required")
        self._storage = storage

    def export_project_package(self, project_id: str) -> AtelierProjectPackageArtifact:
        scope = self._storage._media_write_scope()
        with scope.storage_guard():
            project = self._storage.get_project(project_id)
            return build_package(
                project,
                data_dir=scope.data_dir,
                source_storage_id=self._storage_id(),
                writer_lock=scope.writer_lock,
            )

    def import_project_package(self, package_data: bytes) -> AtelierProject:
        scope = self._storage._media_write_scope()
        with scope.storage_guard():
            prepared = prepare_import(
                package_data,
                data_dir=scope.data_dir,
                target_storage_id=self._storage_id(),
                writer_lock=scope.writer_lock,
            )
            try:
                return self._storage.create_project(prepared.project)
            except StorageError as exc:
                if not exc.commit_uncertain:
                    self._discard_prepared_import(prepared, scope)
                raise
            except Exception:
                self._discard_prepared_import(prepared, scope)
                raise

    def _storage_id(self) -> str:
        storage_id = self._storage_id_provider()
        if not isinstance(storage_id, str) or not storage_id:
            raise ValueError("storage_id is required")
        return storage_id

    @staticmethod
    def _discard_prepared_import(prepared: PreparedAtelierProjectImport, scope) -> None:
        try:
            discard_prepared_import(
                prepared,
                data_dir=scope.data_dir,
                writer_lock=scope.writer_lock,
            )
        except Exception:
            # Preserve the primary storage/import error. A cleanup failure should
            # not turn a known commit failure into an unknown retry signal.
            pass
