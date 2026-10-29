"""Server-owned Atelier node delete/restore receipts."""

from __future__ import annotations

from typing import Any, Callable

from pydantic import BaseModel, ConfigDict

from .models import (
    AtelierAudioTrack,
    AtelierNode,
    AtelierNodeDeleteAudioTracksChange,
    AtelierNodeDeleteDataChangeGroup,
    AtelierNodeDeleteFieldValue,
    AtelierNodeDeleteReceipt,
    AtelierNodeDeleteSequenceChange,
    AtelierNodeDeleteSnapshot,
    AtelierNodeDeleteSourceProof,
    AtelierNodeRestoreSkip,
    AtelierProject,
    AtelierSequenceEntry,
)
from .references import (
    PENDING_REFERENCE_FIELD,
    REFERENCE_URL_FIELDS,
    can_attach_atelier_reference,
    pending_reference_nodes,
    reference_media_urls,
    reference_parent_data,
    reference_strings,
    unlink_atelier_reference_data,
)
from ...utils.json_storage import StorageConflictError

BASE_REFERENCE_GROUP_FIELDS = ("reference_node_ids", *REFERENCE_URL_FIELDS)
REFERENCE_GROUP_FIELDS = ("reference_node_ids", PENDING_REFERENCE_FIELD, *REFERENCE_URL_FIELDS)
PARENT_GROUP_FIELDS = ("parent_node_id", "reference_role")
REGION_GROUP_FIELDS = ("region_id",)
ACTIVE_STATUSES = {
    "pending",
    "processing",
    "running",
    "submitting",
    "submitted",
    "observed",
    "accepted",
    "needs_confirmation",
    "unknown",
}
TERMINAL_STATUSES = {"completed", "failed", "canceled", "cancelled", "deleted"}
EXECUTION_EVIDENCE_KEYS = {
    "attempt_token",
    "attempt_count",
    "attempts",
    "runtime_snapshot",
    "generation_snapshot",
    "request_id",
    "task_id",
    "provider_task_id",
    "provider_request_id",
    "operation_id",
    "owner_submission_started",
    "storage_error",
    "retained_media_url",
    "retained_video_url",
    "tracking_stopped",
    "submitted_at",
    "attempt_started_at",
}


class DeleteNodesAck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project: AtelierProject
    receipt_id: str
    node_ids: list[str]


class RestoreNodesAck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project: AtelierProject
    receipt_id: str
    restored_node_ids: list[str]
    skips: list[AtelierNodeRestoreSkip]


def delete_nodes(
    project: AtelierProject,
    *,
    node_ids: list[str],
    receipt_id_factory: Callable[[], str],
    now: float,
    base_revision: int,
) -> DeleteNodesAck:
    ordered_ids = _validate_node_ids(node_ids)
    nodes_by_id = _nodes_by_id(project)
    missing = [node_id for node_id in ordered_ids if node_id not in nodes_by_id]
    if missing:
        raise StorageConflictError("Atelier node not found")
    deleted = [nodes_by_id[node_id] for node_id in ordered_ids]
    for node in deleted:
        _assert_delete_safe(node)

    deleted_ids = set(ordered_ids)
    remaining = [node for node in project.nodes if node.id not in deleted_ids]
    receipt_id = receipt_id_factory()
    original_indexes = {node.id: index for index, node in enumerate(project.nodes)}
    receipt = AtelierNodeDeleteReceipt(
        receipt_id=receipt_id,
        project_id=project.id,
        base_revision=base_revision,
        deleted_at=now,
        node_ids=ordered_ids,
        node_snapshots=[
            AtelierNodeDeleteSnapshot(
                node=nodes_by_id[node_id].model_copy(deep=True),
                original_index=original_indexes[node_id],
                reference_media_urls=reference_media_urls(nodes_by_id[node_id]),
                referenced_source_ids=reference_strings((nodes_by_id[node_id].data or {}).get("reference_node_ids")),
                source_proofs=_outgoing_source_proofs(nodes_by_id[node_id], project.nodes),
            )
            for node_id in ordered_ids
        ],
    )

    changes: dict[str, dict[str, Any]] = {}
    change_groups: list[AtelierNodeDeleteDataChangeGroup] = []
    for node in remaining:
        if _references_deleted_source(node, deleted, project.nodes):
            after_data = unlink_atelier_reference_data(node, project.nodes, deleted)
            before, after = _reference_group_pair(node.data, after_data)
            if before != after:
                change_groups.append(_change_group(node, "references", before, after, _source_proofs(node, deleted)))
                changes[node.id] = {**node.data, **after_data}

    view = [node.model_copy(deep=True, update={"data": changes.get(node.id, node.data)}) for node in remaining]
    for node in remaining:
        current_data = changes.get(node.id, node.data)
        if current_data.get("parent_node_id") in deleted_ids:
            before = _field_group(current_data, PARENT_GROUP_FIELDS)
            after_values = reference_parent_data(node.model_copy(update={"data": current_data}), view, str(current_data.get("parent_node_id")))
            after_data = {**current_data, **after_values}
            after = _field_group(after_data, PARENT_GROUP_FIELDS)
            if before != after:
                change_groups.append(_change_group(node, "parent", before, after, _source_proofs(node, deleted)))
                changes[node.id] = after_data

    for node in remaining:
        current_data = changes.get(node.id, node.data)
        if current_data.get("region_id") in deleted_ids:
            before = _field_group(current_data, REGION_GROUP_FIELDS)
            after_data = dict(current_data)
            after_data.pop("region_id", None)
            after = _field_group(after_data, REGION_GROUP_FIELDS)
            change_groups.append(_change_group(node, "region", before, after, []))
            changes[node.id] = after_data

    receipt.data_change_groups = change_groups
    kept_sequence = [entry for entry in project.sequence if entry.parentId not in deleted_ids]
    if kept_sequence != project.sequence:
        receipt.sequence_change = AtelierNodeDeleteSequenceChange(
            before=list(project.sequence), after=kept_sequence, removed_node_ids=ordered_ids
        )
    kept_audio = [track for track in project.audio_tracks if track.node_id not in deleted_ids]
    if kept_audio != project.audio_tracks:
        receipt.audio_tracks_change = AtelierNodeDeleteAudioTracksChange(
            before=list(project.audio_tracks), after=kept_audio, removed_node_ids=ordered_ids
        )

    for node in remaining:
        if node.id in changes:
            node.data = changes[node.id]
            node.updated_at = now
    project.nodes = remaining
    project.sequence = kept_sequence
    project.audio_tracks = kept_audio
    project.node_delete_receipts.append(receipt)
    project.updated_at = now
    return DeleteNodesAck(project=project, receipt_id=receipt_id, node_ids=ordered_ids)


def restore_nodes(
    project: AtelierProject,
    *,
    receipt_id: str,
    now: float,
    base_revision: int,
) -> RestoreNodesAck:
    receipt = next((item for item in project.node_delete_receipts if item.receipt_id == receipt_id), None)
    if receipt is None:
        raise StorageConflictError("Atelier delete receipt not found")
    if receipt.restored_at is not None:
        raise StorageConflictError("Atelier delete receipt already restored")
    live_ids = {node.id for node in project.nodes}
    restore_ids = [snapshot.node.id for snapshot in receipt.node_snapshots]
    if any(node_id in live_ids for node_id in restore_ids):
        raise StorageConflictError("Atelier restore node id conflict")

    skips: list[AtelierNodeRestoreSkip] = []
    restored_nodes, snapshot_skips = _sanitized_snapshots(project, receipt)
    skips.extend(snapshot_skips)
    for node in sorted(restored_nodes, key=lambda item: _snapshot_index(receipt, item.id)):
        index = min(_snapshot_index(receipt, node.id), len(project.nodes))
        project.nodes.insert(index, node)

    by_id = _nodes_by_id(project)
    for group in receipt.data_change_groups:
        node = by_id.get(group.node_id)
        if node is None:
            skips.append(AtelierNodeRestoreSkip(scope="data_group", reason="target node missing", node_id=group.node_id, group=group.group))
            continue
        if _field_group(node.data, tuple(group.after.keys())) != group.after:
            skips.append(AtelierNodeRestoreSkip(scope="data_group", reason="current value changed", node_id=group.node_id, group=group.group))
            continue
        partial = False
        if group.group == "references":
            before = _sanitize_reference_group(node, group.before, project.nodes, group.source_proofs)
            partial = before != group.before
        elif group.group == "parent":
            before = _sanitize_parent_group(node, group.before, project.nodes)
        elif group.group == "region":
            before = _sanitize_region_group(node, group.before, project.nodes)
        else:
            before = group.before
        if before is None:
            skips.append(AtelierNodeRestoreSkip(scope="data_group", reason="restored target invalid", node_id=group.node_id, group=group.group))
            continue
        if partial:
            skips.append(AtelierNodeRestoreSkip(scope="data_group", reason="invalid references omitted", node_id=group.node_id, group=group.group))
        node.data = _apply_field_group_values(node.data, before)
        node.updated_at = now

    if receipt.sequence_change is not None:
        if project.sequence == receipt.sequence_change.after:
            sanitized = _sanitize_sequence(receipt.sequence_change.before, _nodes_by_id(project))
            if sanitized != receipt.sequence_change.before:
                skips.append(AtelierNodeRestoreSkip(scope="sequence", reason="invalid timeline refs omitted"))
            project.sequence = sanitized
        else:
            skips.append(AtelierNodeRestoreSkip(scope="sequence", reason="current value changed"))
    if receipt.audio_tracks_change is not None:
        if project.audio_tracks == receipt.audio_tracks_change.after:
            sanitized = _sanitize_audio_tracks(receipt.audio_tracks_change.before, _nodes_by_id(project))
            if sanitized != receipt.audio_tracks_change.before:
                skips.append(AtelierNodeRestoreSkip(scope="audio_tracks", reason="invalid timeline refs omitted"))
            project.audio_tracks = sanitized
        else:
            skips.append(AtelierNodeRestoreSkip(scope="audio_tracks", reason="current value changed"))

    receipt.restored_at = now
    receipt.restore_base_revision = base_revision
    receipt.last_restore_skips = skips
    project.updated_at = now
    return RestoreNodesAck(project=project, receipt_id=receipt_id, restored_node_ids=restore_ids, skips=skips)


def _validate_node_ids(node_ids: list[str]) -> list[str]:
    if not node_ids:
        raise StorageConflictError("Atelier node_ids cannot be empty")
    if any(type(node_id) is not str or not node_id for node_id in node_ids):
        raise StorageConflictError("Invalid Atelier node id")
    if len(set(node_ids)) != len(node_ids):
        raise StorageConflictError("Duplicate Atelier node id")
    return list(node_ids)


def _nodes_by_id(project: AtelierProject) -> dict[str, AtelierNode]:
    return {node.id: node for node in project.nodes}


def _field_group(data: dict[str, Any], fields: tuple[str, ...]) -> dict[str, AtelierNodeDeleteFieldValue]:
    return {
        field: AtelierNodeDeleteFieldValue(exists=field in data, value=data.get(field))
        for field in fields
    }


def _reference_group_pair(
    before_data: dict[str, Any],
    after_data: dict[str, Any],
) -> tuple[dict[str, AtelierNodeDeleteFieldValue], dict[str, AtelierNodeDeleteFieldValue]]:
    fields = REFERENCE_GROUP_FIELDS if (PENDING_REFERENCE_FIELD in before_data or PENDING_REFERENCE_FIELD in after_data) else BASE_REFERENCE_GROUP_FIELDS
    return _field_group(before_data, fields), _field_group(after_data, fields)


def _change_group(node, group, before, after, proofs):
    return AtelierNodeDeleteDataChangeGroup(
        node_id=node.id,
        group=group,
        before=before,
        after=after,
        source_proofs=proofs,
    )


def _apply_field_group_values(data: dict[str, Any], values: dict[str, AtelierNodeDeleteFieldValue]) -> dict[str, Any]:
    updated = dict(data)
    for field, snapshot in values.items():
        if snapshot.exists:
            updated[field] = snapshot.value
        else:
            updated.pop(field, None)
    return updated


def _references_deleted_source(target: AtelierNode, deleted: list[AtelierNode], nodes: list[AtelierNode]) -> bool:
    deleted_ids = {node.id for node in deleted}
    removed_urls = {url for node in deleted for url in reference_media_urls(node)}
    refs = set(reference_strings((target.data or {}).get("reference_node_ids")))
    if refs & deleted_ids:
        return True
    if any(url in removed_urls for field in REFERENCE_URL_FIELDS for url in reference_strings((target.data or {}).get(field))):
        return True
    return any(source.id in deleted_ids and source.data.get("parent_node_id") == target.id for source in nodes)


def _source_proofs(target: AtelierNode, sources: list[AtelierNode]) -> list[AtelierNodeDeleteSourceProof]:
    proofs = []
    target_data = target.data or {}
    target_source_ids = set(reference_strings(target_data.get("reference_node_ids")))
    target_urls = {
        url
        for field in REFERENCE_URL_FIELDS
        for url in reference_strings(target_data.get(field))
    }
    for source in sources:
        urls = reference_media_urls(source)
        if (
            source.id in target_source_ids
            or (source.data or {}).get("parent_node_id") == target.id
            or bool(target_urls & set(urls))
        ):
            proofs.append(AtelierNodeDeleteSourceProof(target_node_id=target.id, source_node_id=source.id, source_type=source.type, urls=urls))
    return proofs


def _outgoing_source_proofs(target: AtelierNode, nodes: list[AtelierNode]) -> list[AtelierNodeDeleteSourceProof]:
    proofs = []
    source_ids = set(reference_strings((target.data or {}).get("reference_node_ids")))
    source_ids.update(node.id for node in nodes if (node.data or {}).get("parent_node_id") == target.id)
    for source_id in source_ids:
        source = next((node for node in nodes if node.id == source_id), None)
        if source is not None and can_attach_atelier_reference(target, source):
            proofs.append(
                AtelierNodeDeleteSourceProof(
                    target_node_id=target.id,
                    source_node_id=source.id,
                    source_type=source.type,
                    urls=reference_media_urls(source),
                )
            )
    proofs.sort(key=lambda proof: proof.source_node_id)
    return proofs


def _snapshot_index(receipt: AtelierNodeDeleteReceipt, node_id: str) -> int:
    return next(snapshot.original_index for snapshot in receipt.node_snapshots if snapshot.node.id == node_id)


def _sanitized_snapshots(project: AtelierProject, receipt: AtelierNodeDeleteReceipt) -> tuple[list[AtelierNode], list[AtelierNodeRestoreSkip]]:
    live = list(project.nodes) + [snapshot.node for snapshot in receipt.node_snapshots]
    restored = []
    skips: list[AtelierNodeRestoreSkip] = []
    for snapshot in receipt.node_snapshots:
        node = snapshot.node.model_copy(deep=True)
        if node.project_id != project.id:
            raise StorageConflictError("Atelier delete receipt project mismatch")
        original_data = dict(node.data or {})
        data = dict(original_data)
        data = _sanitize_reference_data(node, data, live, set(receipt.node_ids), snapshot.source_proofs)
        if node.type == "region" and data.get("region_id") is not None:
            data.pop("region_id", None)
        elif data.get("region_id") and not _valid_region_id(data.get("region_id"), live):
            data.pop("region_id", None)
        parent_group = _sanitize_parent_group(node, _field_group(data, PARENT_GROUP_FIELDS), live)
        if parent_group is None:
            data = _apply_field_group_values(data, {field: AtelierNodeDeleteFieldValue(exists=False) for field in PARENT_GROUP_FIELDS})
        else:
            data = _apply_field_group_values(data, parent_group)
        if data != original_data:
            skips.append(AtelierNodeRestoreSkip(scope="node", node_id=node.id, reason="invalid snapshot fields omitted"))
        node.data = data
        restored.append(node)
    return restored, skips


def _sanitize_reference_data(
    node: AtelierNode,
    data: dict[str, Any],
    nodes: list[AtelierNode],
    same_batch: set[str],
    proofs: list[AtelierNodeDeleteSourceProof],
) -> dict[str, Any]:
    updated = dict(data)
    ids = []
    for source_id in reference_strings(updated.get("reference_node_ids")):
        source = next((item for item in nodes if item.id == source_id), None)
        if source is not None and can_attach_atelier_reference(node, source):
            ids.append(source_id)
    if "reference_node_ids" in updated or ids:
        updated["reference_node_ids"] = list(dict.fromkeys(ids))
    if PENDING_REFERENCE_FIELD in updated:
        by_id = {item.id: item for item in nodes}
        updated[PENDING_REFERENCE_FIELD] = [
            item for item in pending_reference_nodes(updated.get(PENDING_REFERENCE_FIELD))
            if item["source_node_id"] in ids
            and item["source_node_id"] in by_id
            and by_id[item["source_node_id"]].type == item["source_kind"]
        ]
    proved_by_source = {proof.source_node_id: set(proof.urls) for proof in proofs}
    valid_proved_urls: set[str] = set()
    invalid_proved_urls: set[str] = set()
    for source_id, urls in proved_by_source.items():
        source = next((item for item in nodes if item.id == source_id), None)
        if source is not None and can_attach_atelier_reference(node, source):
            valid_proved_urls.update(urls)
        else:
            invalid_proved_urls.update(urls)
    valid_urls = {
        url
        for source in nodes
        if source.id in ids
        for url in reference_media_urls(source)
    } | {url for source_id, urls in proved_by_source.items() if source_id in ids for url in urls} | valid_proved_urls
    for field in REFERENCE_URL_FIELDS:
        if field in updated:
            updated[field] = [
                url
                for url in reference_strings(updated.get(field))
                if url in valid_urls or url not in invalid_proved_urls
            ]
    return updated


def _sanitize_reference_group(
    node: AtelierNode,
    before: dict[str, AtelierNodeDeleteFieldValue],
    nodes: list[AtelierNode],
    proofs: list[AtelierNodeDeleteSourceProof],
) -> dict[str, AtelierNodeDeleteFieldValue]:
    data = _apply_field_group_values({}, before)
    sanitized = _sanitize_reference_data(node, data, nodes, set(), proofs)
    return _field_group(sanitized, tuple(before.keys()))


def _sanitize_parent_group(node: AtelierNode, before: dict[str, AtelierNodeDeleteFieldValue], nodes: list[AtelierNode]) -> dict[str, AtelierNodeDeleteFieldValue] | None:
    parent_id = before.get("parent_node_id")
    if parent_id and parent_id.exists and parent_id.value is not None:
        parent = next((item for item in nodes if item.id == parent_id.value), None)
        if parent is None or not can_attach_atelier_reference(parent, node):
            return None
    return before


def _sanitize_region_group(node: AtelierNode, before: dict[str, AtelierNodeDeleteFieldValue], nodes: list[AtelierNode]) -> dict[str, AtelierNodeDeleteFieldValue] | None:
    region = before.get("region_id")
    if region and region.exists and region.value is not None and not _valid_region_id(region.value, nodes):
        return None
    return before


def _valid_region_id(region_id: Any, nodes: list[AtelierNode]) -> bool:
    region = next((node for node in nodes if node.id == region_id), None)
    return bool(region and region.type == "region" and not region.data.get("region_id"))


def _sanitize_sequence(entries: list[AtelierSequenceEntry], nodes: dict[str, AtelierNode]) -> list[AtelierSequenceEntry]:
    return [entry for entry in entries if isinstance(entry, AtelierSequenceEntry) and entry.parentId in nodes]


def _sanitize_audio_tracks(tracks: list[AtelierAudioTrack], nodes: dict[str, AtelierNode]) -> list[AtelierAudioTrack]:
    return [track for track in tracks if isinstance(track, AtelierAudioTrack) and track.node_id in nodes]


def _assert_delete_safe(node: AtelierNode) -> None:
    if _status_active(node.status) or (node.video_task_id and str(node.status).lower() not in TERMINAL_STATUSES):
        raise StorageConflictError("Atelier node has active execution")
    data = node.data or {}
    for candidate in _candidate_list(data.get("candidates")) + _candidate_list(data.get("deleted_candidates")):
        if _record_active(candidate):
            raise StorageConflictError("Atelier node has active execution")
    generation = data.get("generation")
    if isinstance(generation, dict) and _generation_active(generation):
        raise StorageConflictError("Atelier node has active execution")


def _candidate_list(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _status_active(value: Any) -> bool:
    return isinstance(value, str) and value.lower() in ACTIVE_STATUSES


def _record_active(value: dict[str, Any]) -> bool:
    status = value.get("status")
    if _status_active(status):
        return True
    if isinstance(status, str) and status.lower() in TERMINAL_STATUSES:
        return bool(value.get("storage_error") or value.get("tracking_stopped") or _nested_active(value))
    return _has_execution_evidence(value) or _nested_active(value)


def _generation_active(generation: dict[str, Any]) -> bool:
    status = generation.get("status")
    if _status_active(status):
        return True
    if isinstance(status, str) and status.lower() in TERMINAL_STATUSES:
        return bool(generation.get("storage_error") or generation.get("tracking_stopped") or _nested_active(generation))
    if _has_execution_evidence(generation):
        return True
    receipts = generation.get("request_receipts")
    if isinstance(receipts, dict):
        return any(isinstance(value, dict) and _record_active(value) for value in receipts.values())
    return False


def _nested_active(value: dict[str, Any]) -> bool:
    snapshot_keys = {"runtime_snapshot", "generation_snapshot", "params", "payload"}
    for key, item in value.items():
        if key in snapshot_keys:
            continue
        if isinstance(item, dict) and _record_active(item):
            return True
        if isinstance(item, list) and any(isinstance(child, dict) and _record_active(child) for child in item):
            return True
    return False


def _has_execution_evidence(value: dict[str, Any]) -> bool:
    for key in EXECUTION_EVIDENCE_KEYS:
        if key in value and value.get(key) not in (None, False, [], {}, 0):
            return True
    return False
