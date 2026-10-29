"""Product-owned Atelier project CRUD service."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Callable, Final, Iterable

from .media_upload import ManagedUpload
from .models import AtelierProject
from .sequence_commands import (
    AUDIO_TRACKS_OMITTED,
    ReplaceSequenceCommand,
    SequencePreflightReport,
    preflight_saved_sequence as apply_preflight_saved_sequence,
    replace_sequence as apply_replace_sequence,
)
from .reference_commands import (
    AttachPendingReferenceCommand,
    AttachReferenceCommand,
    DetachReferenceCommand,
    attach_pending_reference as apply_attach_pending_reference,
    attach_reference as apply_attach_reference,
    detach_reference as apply_detach_reference,
)
from .nodes import (
    BatchLayoutCommand,
    CreateNodeCommand,
    CreateRegionWrapCommand,
    LayoutPatch,
    MoveNodeCommand,
    RegionAppearancePatch,
    NodeBodyPatch,
    NodeCommandAck,
    NodeTextPatch,
    RegionMembersCommand,
    create_node as apply_create_node,
    move_nodes as apply_move_nodes,
    set_region_members as apply_set_region_members,
    update_batch_layout as apply_update_batch_layout,
    update_body as apply_update_body,
    update_layout as apply_update_layout,
    update_region_appearance as apply_update_region_appearance,
    update_text as apply_update_text,
    wrap_region as apply_wrap_region,
)
from .node_history import DeleteNodesAck, RestoreNodesAck, delete_nodes as apply_delete_nodes, restore_nodes as apply_restore_nodes
from .storage import AtelierStorage
from .upload_commands import (
    CreateMediaNodeUpload,
    UploadCommandAck,
    UploadReferenceAttach,
    create_media_node as apply_create_media_node,
    find_upload_receipt,
    fingerprint_upload_request,
    replay_upload_receipt,
    upload_reference_attach as apply_upload_reference_attach,
)
from ...utils.json_storage import StorageConflictError, StorageError


@dataclass(frozen=True)
class _Omitted:
    pass


_OMITTED: Final = _Omitted()


class SourceProjectNotFound(ValueError):
    """Raised when an Atelier source Studio project reference is unavailable."""


class AtelierUploadReceiptNotFound(ValueError):
    """Raised when an upload receipt does not exist for a project."""


class AtelierService:
    """Trusted application service for minimal Atelier project CRUD."""

    def __init__(
        self,
        storage: AtelierStorage,
        *,
        source_project_exists: Callable[[str], bool] | None = None,
        clock: Callable[[], float] | None = None,
        id_factory: Callable[[], str] | None = None,
        media_uploader: Callable[..., ManagedUpload] | None = None,
        media_discarder: Callable[[ManagedUpload], None] | None = None,
    ):
        self.storage = storage
        self._source_project_exists = source_project_exists
        self._clock = clock or time.time
        self._id_factory = id_factory or (lambda: str(uuid.uuid4()))
        self._media_uploader = media_uploader
        self._media_discarder = media_discarder

    def list_projects(self) -> list[AtelierProject]:
        return self.storage.list_projects()

    def get_project(self, project_id: str) -> AtelierProject:
        return self.storage.get_project(project_id)

    def get_upload_receipt(self, project_id: str, client_request_id: str):
        project = self.storage.get_project(project_id)
        receipt = find_upload_receipt(project, client_request_id)
        if receipt is None:
            raise AtelierUploadReceiptNotFound("Atelier upload receipt not found")
        return receipt

    def get_upload_receipt_readback(self, project_id: str, client_request_id: str):
        project = self.storage.get_project(project_id)
        receipt = find_upload_receipt(project, client_request_id)
        if receipt is None:
            raise AtelierUploadReceiptNotFound("Atelier upload receipt not found")
        return project, receipt

    def create_project(
        self,
        *,
        title: str,
        description: str = "",
        source_project_id: str | None | _Omitted = _OMITTED,
    ) -> AtelierProject:
        title = _validate_required_text(title, "title")
        description = _validate_required_text(description, "description")
        source = (
            None
            if isinstance(source_project_id, _Omitted)
            else self._validate_source_value(source_project_id)
        )
        now = self._clock()
        project = AtelierProject(
            id=self._id_factory(),
            title=title,
            description=description,
            source_project_id=source,
            created_at=now,
            updated_at=now,
        )
        before_commit = (
            None if source is None else lambda: self._assert_source_exists(source)
        )
        return self.storage.create_project(project, before_commit=before_commit)

    def update_project(
        self,
        project_id: str,
        *,
        expected_revision: int,
        title: str | None | _Omitted = _OMITTED,
        description: str | None | _Omitted = _OMITTED,
        source_project_id: str | None | _Omitted = _OMITTED,
    ) -> AtelierProject:
        if type(expected_revision) is not int or expected_revision < 0:
            raise StorageConflictError("Invalid Atelier project revision")
        if title is not _OMITTED:
            title = _validate_required_text(title, "title")
        if description is not _OMITTED:
            description = _validate_required_text(description, "description")
        source = (
            _OMITTED
            if source_project_id is _OMITTED
            else self._validate_source_value(source_project_id)
        )

        def apply(project: AtelierProject) -> None:
            if source is not _OMITTED and source is not None:
                self._assert_source_exists(source)
            changed = False
            if title is not _OMITTED and project.title != title:
                project.title = title
                changed = True
            if description is not _OMITTED and project.description != description:
                project.description = description
                changed = True
            if source is not _OMITTED and project.source_project_id != source:
                project.source_project_id = source
                changed = True
            if changed:
                project.updated_at = self._clock()

        return self.storage.apply_project_command(
            project_id,
            expected_revision=expected_revision,
            command=apply,
        )


    def replace_sequence(self, project_id: str, command: ReplaceSequenceCommand) -> AtelierProject:
        if type(command.expected_revision) is not int or command.expected_revision < 0:
            raise StorageConflictError("Invalid Atelier project revision")
        media_scope = self.storage._media_write_scope()
        now = self._clock()

        def apply(project: AtelierProject) -> None:
            apply_replace_sequence(project, command, media_scope=media_scope, now=now)

        return self.storage.apply_project_command(
            project_id, expected_revision=command.expected_revision, command=apply
        )

    def preflight_sequence(
        self,
        project_id: str,
        *,
        entries,
        audio_tracks=AUDIO_TRACKS_OMITTED,
    ) -> SequencePreflightReport:
        project = self.storage.get_project(project_id)
        media_scope = self.storage._media_write_scope()
        resolved_audio_tracks = None if audio_tracks is AUDIO_TRACKS_OMITTED else audio_tracks
        return apply_preflight_saved_sequence(
            project,
            entries,
            media_scope=media_scope,
            audio_tracks=resolved_audio_tracks,
        )

    def create_node(self, project_id: str, command: CreateNodeCommand) -> NodeCommandAck:
        node_ids: list[str] = []

        def apply(project: AtelierProject) -> None:
            nonlocal node_ids
            node_ids = apply_create_node(
                project,
                command,
                node_id_factory=self._id_factory,
                now=self._clock(),
            )

        project = self.storage.apply_project_command(
            project_id, expected_revision=command.expected_revision, command=apply
        )
        return NodeCommandAck(project=project, node_ids=node_ids)

    def update_node_text(self, project_id: str, node_id: str, command: NodeTextPatch) -> NodeCommandAck:
        node_ids: list[str] = [node_id]

        def apply(project: AtelierProject) -> None:
            apply_update_text(project, node_id, command, now=self._clock())

        project = self.storage.apply_project_command(
            project_id, expected_revision=command.expected_revision, command=apply
        )
        return NodeCommandAck(project=project, node_ids=node_ids)

    def update_node_body(self, project_id: str, node_id: str, command: NodeBodyPatch) -> NodeCommandAck:
        node_ids: list[str] = [node_id]

        def apply(project: AtelierProject) -> None:
            apply_update_body(project, node_id, command, now=self._clock())

        project = self.storage.apply_project_command(
            project_id, expected_revision=command.expected_revision, command=apply
        )
        return NodeCommandAck(project=project, node_ids=node_ids)

    def update_node_layout(self, project_id: str, node_id: str, command: LayoutPatch) -> NodeCommandAck:
        if command.expected_revision is None:
            raise StorageConflictError("Invalid Atelier project revision")

        def apply(project: AtelierProject) -> None:
            apply_update_layout(project, node_id, command, now=self._clock())

        project = self.storage.apply_project_command(
            project_id, expected_revision=command.expected_revision, command=apply
        )
        return NodeCommandAck(project=project, node_ids=[node_id])

    def update_nodes_layout(self, project_id: str, command: BatchLayoutCommand) -> NodeCommandAck:
        node_ids = [update.node_id for update in command.updates if update.node_id is not None]

        def apply(project: AtelierProject) -> None:
            apply_update_batch_layout(project, command, now=self._clock())

        project = self.storage.apply_project_command(
            project_id, expected_revision=command.expected_revision, command=apply
        )
        return NodeCommandAck(project=project, node_ids=node_ids)

    def move_nodes(self, project_id: str, command: MoveNodeCommand) -> NodeCommandAck:
        def apply(project: AtelierProject) -> None:
            apply_move_nodes(project, command, now=self._clock())

        project = self.storage.apply_project_command(
            project_id, expected_revision=command.expected_revision, command=apply
        )
        return NodeCommandAck(project=project, node_ids=list(command.node_ids))

    def wrap_region(self, project_id: str, command: CreateRegionWrapCommand) -> NodeCommandAck:
        node_ids: list[str] = []

        def apply(project: AtelierProject) -> None:
            nonlocal node_ids
            node_ids = apply_wrap_region(
                project,
                command,
                node_id_factory=self._id_factory,
                now=self._clock(),
            )

        project = self.storage.apply_project_command(
            project_id, expected_revision=command.expected_revision, command=apply
        )
        return NodeCommandAck(project=project, node_ids=node_ids)

    def set_region_members(
        self, project_id: str, region_id: str, command: RegionMembersCommand
    ) -> NodeCommandAck:
        def apply(project: AtelierProject) -> None:
            apply_set_region_members(project, region_id, command, now=self._clock())

        project = self.storage.apply_project_command(
            project_id, expected_revision=command.expected_revision, command=apply
        )
        return NodeCommandAck(project=project, node_ids=[region_id])

    def update_region_appearance(
        self, project_id: str, region_id: str, command: RegionAppearancePatch
    ) -> NodeCommandAck:
        def apply(project: AtelierProject) -> None:
            apply_update_region_appearance(project, region_id, command, now=self._clock())

        project = self.storage.apply_project_command(
            project_id, expected_revision=command.expected_revision, command=apply
        )
        return NodeCommandAck(project=project, node_ids=[region_id])




    def attach_pending_reference(
        self, project_id: str, target_node_id: str, command: AttachPendingReferenceCommand
    ) -> NodeCommandAck:
        ack: NodeCommandAck | None = None

        def apply(project: AtelierProject) -> None:
            nonlocal ack
            ack = apply_attach_pending_reference(project, target_node_id, command, now=self._clock())

        project = self.storage.apply_project_command(
            project_id, expected_revision=command.expected_revision, command=apply
        )
        assert ack is not None
        return NodeCommandAck(project=project, node_ids=ack.node_ids)

    def attach_reference(
        self, project_id: str, target_node_id: str, command: AttachReferenceCommand
    ) -> NodeCommandAck:
        ack: NodeCommandAck | None = None

        def apply(project: AtelierProject) -> None:
            nonlocal ack
            ack = apply_attach_reference(
                project,
                target_node_id,
                command,
                node_id_factory=self._id_factory,
                now=self._clock(),
            )

        project = self.storage.apply_project_command(
            project_id, expected_revision=command.expected_revision, command=apply
        )
        assert ack is not None
        return NodeCommandAck(project=project, node_ids=ack.node_ids)

    def detach_reference(
        self, project_id: str, target_node_id: str, command: DetachReferenceCommand
    ) -> NodeCommandAck:
        ack: NodeCommandAck | None = None

        def apply(project: AtelierProject) -> None:
            nonlocal ack
            ack = apply_detach_reference(project, target_node_id, command, now=self._clock())

        project = self.storage.apply_project_command(
            project_id, expected_revision=command.expected_revision, command=apply
        )
        assert ack is not None
        return NodeCommandAck(project=project, node_ids=ack.node_ids)

    def create_media_node_upload(
        self,
        project_id: str,
        command: CreateMediaNodeUpload,
        chunks: Iterable[bytes],
        *,
        content_type: str | None,
    ) -> UploadCommandAck:
        return self._run_upload_transaction(
            project_id,
            command,
            chunks,
            content_type=content_type,
            operation="create_media_node",
            target_node_id=None,
        )

    def upload_reference_attach(
        self,
        project_id: str,
        target_node_id: str,
        command: UploadReferenceAttach,
        chunks: Iterable[bytes],
        *,
        content_type: str | None,
    ) -> UploadCommandAck:
        return self._run_upload_transaction(
            project_id,
            command,
            chunks,
            content_type=content_type,
            operation="upload_reference_attach",
            target_node_id=target_node_id,
        )

    def delete_nodes(self, project_id: str, *, expected_revision: int, node_ids: list[str]) -> DeleteNodesAck:
        if type(expected_revision) is not int or expected_revision < 0:
            raise StorageConflictError("Invalid Atelier project revision")
        receipt_id = self._id_factory()
        now = self._clock()
        ack: DeleteNodesAck | None = None

        def apply(project: AtelierProject) -> None:
            nonlocal ack
            ack = apply_delete_nodes(
                project,
                node_ids=node_ids,
                receipt_id_factory=lambda: receipt_id,
                now=now,
                base_revision=expected_revision,
            )

        project = self.storage.apply_project_command(project_id, expected_revision=expected_revision, command=apply)
        assert ack is not None
        return DeleteNodesAck(project=project, receipt_id=ack.receipt_id, node_ids=ack.node_ids)

    def restore_nodes(self, project_id: str, *, expected_revision: int, receipt_id: str) -> RestoreNodesAck:
        if type(expected_revision) is not int or expected_revision < 0:
            raise StorageConflictError("Invalid Atelier project revision")
        now = self._clock()
        ack: RestoreNodesAck | None = None

        def apply(project: AtelierProject) -> None:
            nonlocal ack
            ack = apply_restore_nodes(
                project,
                receipt_id=receipt_id,
                now=now,
                base_revision=expected_revision,
            )

        project = self.storage.apply_project_command(project_id, expected_revision=expected_revision, command=apply)
        assert ack is not None
        return RestoreNodesAck(project=project, receipt_id=ack.receipt_id, restored_node_ids=ack.restored_node_ids, skips=ack.skips)

    def delete_project(self, project_id: str, *, expected_revision: int) -> None:
        if type(expected_revision) is not int or expected_revision < 0:
            raise StorageConflictError("Invalid Atelier project revision")
        self.storage.delete_project(project_id, expected_revision=expected_revision)

    def close(self) -> None:
        self.storage.close()

    def _validate_source_value(self, source_project_id: str | None) -> str | None:
        if source_project_id is None:
            return None
        if type(source_project_id) is not str:
            raise ValueError("source_project_id must be a string or null")
        if not source_project_id.strip():
            raise ValueError("source_project_id must be non-empty")
        return source_project_id

    def _assert_source_exists(self, source_project_id: str) -> None:
        if self._source_project_exists is None:
            raise SourceProjectNotFound("Source project authority is unavailable")
        if not self._source_project_exists(source_project_id):
            raise SourceProjectNotFound("Source project not found")

    def _run_upload_transaction(
        self,
        project_id: str,
        command: CreateMediaNodeUpload | UploadReferenceAttach,
        chunks: Iterable[bytes],
        *,
        content_type: str | None,
        operation: str,
        target_node_id: str | None,
    ) -> UploadCommandAck:
        current = self.storage.get_project(project_id)
        existing = find_upload_receipt(current, command.client_request_id)
        if existing is None and current.revision != command.expected_revision:
            raise StorageConflictError("Atelier project changed; reload before saving")
        managed = self._upload_media(chunks, filename=command.filename, content_type=content_type)
        fingerprint = fingerprint_upload_request(
            command,
            operation=operation,  # type: ignore[arg-type]
            media=managed,
            target_node_id=target_node_id,
        )
        try:
            return self._commit_upload(project_id, command, managed, fingerprint, operation=operation, target_node_id=target_node_id)
        except StorageError as exc:
            if exc.commit_uncertain:
                raise
            self._discard_uploaded_media(managed)
            raise
        except Exception:
            self._discard_uploaded_media(managed)
            raise

    def _commit_upload(
        self,
        project_id: str,
        command: CreateMediaNodeUpload | UploadReferenceAttach,
        managed: ManagedUpload,
        fingerprint: str,
        *,
        operation: str,
        target_node_id: str | None,
    ) -> UploadCommandAck:
        receipt_id = self._id_factory()
        node_id = self._id_factory()
        now = self._clock()
        ack: UploadCommandAck | None = None

        def apply(project: AtelierProject) -> None:
            nonlocal ack
            if operation == "create_media_node":
                assert isinstance(command, CreateMediaNodeUpload)
                ack = apply_create_media_node(
                    project,
                    command,
                    managed=managed,
                    receipt_id=receipt_id,
                    node_id=node_id,
                    now=now,
                    request_fingerprint=fingerprint,
                )
                return
            assert isinstance(command, UploadReferenceAttach)
            assert target_node_id is not None
            ack = apply_upload_reference_attach(
                project,
                target_node_id,
                command,
                managed=managed,
                receipt_id=receipt_id,
                node_id=node_id,
                now=now,
                request_fingerprint=fingerprint,
            )

        project, replayed = self.storage.apply_upload_command(
            project_id,
            expected_revision=command.expected_revision,
            client_request_id=command.client_request_id,
            request_fingerprint=fingerprint,
            command=apply,
        )
        if replayed:
            existing = find_upload_receipt(project, command.client_request_id)
            assert existing is not None
            self._discard_uploaded_media(managed)
            return replay_upload_receipt(project, existing)
        assert ack is not None
        return UploadCommandAck(project=project, node_ids=ack.node_ids, receipt_id=ack.receipt_id)

    def _upload_media(self, chunks: Iterable[bytes], *, filename: str, content_type: str | None) -> ManagedUpload:
        if self._media_uploader is not None:
            return self._media_uploader(chunks, filename=filename, content_type=content_type)
        return self.storage.save_managed_upload(chunks, filename=filename, content_type=content_type)

    def _discard_uploaded_media(self, managed: ManagedUpload) -> None:
        try:
            for project in self.storage.list_projects():
                if _contains_value(project.model_dump(mode="json"), managed.url) or (
                    managed.poster_url is not None
                    and _contains_value(project.model_dump(mode="json"), managed.poster_url)
                ):
                    raise StorageError("Managed upload is referenced; cleanup is uncertain", commit_uncertain=True)
            if self._media_discarder is not None:
                self._media_discarder(managed)
                return
            self.storage.discard_managed_upload(managed)
        except StorageError as exc:
            if exc.commit_uncertain:
                raise
            raise StorageError("Managed upload cleanup failed; completion uncertain", commit_uncertain=True) from exc
        except Exception as exc:
            raise StorageError("Managed upload cleanup failed; completion uncertain", commit_uncertain=True) from exc


def _contains_value(value, needle: str) -> bool:
    if value == needle:
        return True
    if isinstance(value, dict):
        return any(_contains_value(item, needle) for item in value.values())
    if isinstance(value, list):
        return any(_contains_value(item, needle) for item in value)
    return False


def _validate_required_text(value: str | None, field_name: str) -> str:
    if value is None:
        raise ValueError(f"{field_name} cannot be null")
    if type(value) is not str:
        raise ValueError(f"{field_name} must be a string")
    return value
