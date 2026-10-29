"""Typed Atelier reference attach/detach commands."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .models import AtelierNode, AtelierProject
from .nodes import NodeCommandAck
from .references import (
    PENDING_REFERENCE_FIELD,
    can_attach_atelier_reference,
    canonical_atelier_reference_data,
    pending_reference_nodes,
    pending_reference_record,
    reference_media_urls,
    reference_parent_data,
    reference_strings,
    remove_pending_reference,
    unlink_atelier_reference_data,
)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _RevisionModel(_StrictModel):
    expected_revision: int = Field(..., ge=0, strict=True)


class AttachReferenceCommand(_RevisionModel):
    source_node_id: str = Field(..., min_length=1)
    source_candidate_id: str | None = Field(None, min_length=1)


class AttachPendingReferenceCommand(_RevisionModel):
    source_node_id: str = Field(..., min_length=1)


class DetachReferenceCommand(_RevisionModel):
    reference_url: str | None = Field(None, min_length=1)
    source_node_id: str | None = Field(None, min_length=1)

    @model_validator(mode="after")
    def _reference_identity_is_required(self) -> "DetachReferenceCommand":
        if self.reference_url is not None and not self.reference_url.strip():
            raise ValueError("Reference URL must be non-empty")
        if self.source_node_id is None and self.reference_url is None:
            raise ValueError("A source node id or reference URL is required")
        if self.source_node_id is not None and not self.source_node_id.strip():
            raise ValueError("Source node id must be non-empty")
        return self


def attach_pending_reference(
    project: AtelierProject,
    target_node_id: str,
    command: AttachPendingReferenceCommand,
    *,
    now: float,
) -> NodeCommandAck:
    target = _find_node(project, target_node_id, "Target node not found")
    source = _find_node(project, command.source_node_id, "Source node not found")
    _validate_target(target)
    if target.id == source.id:
        raise ValueError("A node cannot reference itself")
    if not can_attach_atelier_reference(target, source):
        raise ValueError("Unsupported reference source kind or self reference")
    if reference_media_urls(source):
        raise ValueError("Source node already has media; use resolved attach")

    target_data = dict(target.data or {})
    pending = pending_reference_nodes(target_data.get(PENDING_REFERENCE_FIELD))
    if any(item["source_node_id"] == source.id for item in pending):
        raise ValueError("Source node is already pending for this target")
    if source.id in reference_strings(target_data.get("reference_node_ids")) and not any(item["source_node_id"] == source.id for item in pending):
        raise ValueError("Source node is already attached as a resolved reference")

    ids = reference_strings(target_data.get("reference_node_ids"))
    ids.append(source.id)
    target_data["reference_node_ids"] = list(dict.fromkeys(ids))
    target_data[PENDING_REFERENCE_FIELD] = [*pending, pending_reference_record(source)]
    target_data.update(canonical_atelier_reference_data(target, project.nodes, target_data))

    source_data = {
        **(source.data or {}),
        "parent_node_id": target.id,
        "reference_role": f"{target.type}_reference_{source.type}",
    }

    changed = False
    if target.data != target_data:
        target.data = target_data
        target.updated_at = now
        changed = True
    if source.data != source_data:
        source.data = source_data
        source.updated_at = now
        changed = True
    if changed:
        project.updated_at = now
    return NodeCommandAck(project=project, node_ids=[target.id, source.id])


def attach_reference(
    project: AtelierProject,
    target_node_id: str,
    command: AttachReferenceCommand,
    *,
    node_id_factory,
    now: float,
) -> NodeCommandAck:
    target = _find_node(project, target_node_id, "Target node not found")
    source = _find_node(project, command.source_node_id, "Source node not found")
    _validate_target(target)
    if target.id == source.id and command.source_candidate_id is None:
        raise ValueError("A node cannot reference itself")
    if target.type == "image" and source.type != "image":
        raise ValueError("Image generation only accepts image references")

    source_url, reference_candidate = _resolve_source_url(source, command.source_candidate_id)
    source_node = source
    added_node: AtelierNode | None = None
    if reference_candidate is not None:
        source_node, added_node = _materialize_candidate_reference(
            project,
            source,
            reference_candidate,
            command.source_candidate_id or "",
            source_url,
            node_id_factory=node_id_factory,
            now=now,
        )
    if source_node.id == target.id:
        raise ValueError("A node cannot reference itself")
    if not can_attach_atelier_reference(target, source_node):
        raise ValueError("Unsupported reference source kind or self reference")

    proposed_nodes = [*project.nodes, added_node] if added_node is not None else list(project.nodes)
    target_data = remove_pending_reference(dict(target.data or {}), source_node.id)
    ids = reference_strings(target_data.get("reference_node_ids"))
    ids.append(source_node.id)
    target_data["reference_node_ids"] = ids
    target_data.update(canonical_atelier_reference_data(target, proposed_nodes, target_data))
    field = f"reference_{source_node.type}_urls"
    target_data[field] = list(dict.fromkeys([*reference_strings(target_data.get(field)), source_url]))

    current_parent = next(
        (
            node
            for node in proposed_nodes
            if node.id == (source_node.data or {}).get("parent_node_id")
            and can_attach_atelier_reference(node, source_node)
        ),
        target,
    )
    source_data = {
        **(source_node.data or {}),
        "parent_node_id": current_parent.id,
        "reference_role": f"{current_parent.type}_reference_{source_node.type}",
    }

    changed = False
    if added_node is not None:
        project.nodes.append(added_node)
        changed = True
    if target.data != target_data:
        target.data = target_data
        target.updated_at = now
        changed = True
    if source_node.data != source_data:
        source_node.data = source_data
        source_node.updated_at = now
        changed = True
    if changed:
        project.updated_at = now
    return NodeCommandAck(project=project, node_ids=[target.id, source_node.id])


def detach_reference(project: AtelierProject, target_node_id: str, command: DetachReferenceCommand, *, now: float) -> NodeCommandAck:
    target = _find_node(project, target_node_id, "Target node not found")
    _validate_target(target)
    source_node_id = command.source_node_id
    if source_node_id is not None:
        source = _find_node(project, source_node_id, "Source node not found")
        if not can_attach_atelier_reference(target, source):
            raise ValueError("Unsupported reference source kind or self reference")

    ids = reference_strings((target.data or {}).get("reference_node_ids"))
    sources = [
        source
        for source in project.nodes
        if can_attach_atelier_reference(target, source)
        and (source.id in ids or (source.data or {}).get("parent_node_id") == target.id)
        and (
            source.id == source_node_id
            if source_node_id is not None
            else command.reference_url in reference_media_urls(source)
        )
    ]
    if source_node_id is not None and not sources:
        return NodeCommandAck(project=project, node_ids=[target.id])

    target_data = unlink_atelier_reference_data(
        target,
        project.nodes,
        sources,
        None if source_node_id is not None else command.reference_url,
    )
    changed = False
    touched = [target.id]
    if target.data != target_data:
        target.data = target_data
        target.updated_at = now
        changed = True
    for source in sources:
        touched.append(source.id)
        if (source.data or {}).get("parent_node_id") == target.id:
            source_data = {**(source.data or {}), **reference_parent_data(source, project.nodes, target.id)}
            if source.data != source_data:
                source.data = source_data
                source.updated_at = now
                changed = True
    if changed:
        project.updated_at = now
    return NodeCommandAck(project=project, node_ids=list(dict.fromkeys(touched)))


def _validate_target(target: AtelierNode) -> None:
    if target.type not in {"image", "video"}:
        raise ValueError("Target node must be an image or video / draft node")


def _find_node(project: AtelierProject, node_id: str, message: str) -> AtelierNode:
    for node in project.nodes:
        if node.id == node_id:
            return node
    raise ValueError(message)


def _resolve_source_url(source: AtelierNode, source_candidate_id: str | None) -> tuple[str, dict[str, Any] | None]:
    if source_candidate_id is not None:
        if source.type not in {"image", "video", "audio"}:
            raise ValueError(f"Source node type '{source.type}' cannot be used as a reference")
        candidates = _candidate_list((source.data or {}).get("candidates"))
        candidate = next((item for item in candidates if item.get("id") == source_candidate_id), None)
        if candidate is None:
            raise ValueError("Source candidate not found")
        if candidate.get("status") != "completed":
            raise ValueError("Source candidate has no completed take to reference")
        if candidate.get("media_kind", source.type) != source.type:
            raise ValueError("Source candidate media kind must match its parent node type")
        url = candidate.get("media_url") or candidate.get("video_url")
        if not isinstance(url, str) or not url.strip():
            raise ValueError("Source candidate has no completed take to reference")
        return url, candidate

    if source.type in {"image", "audio"}:
        urls = reference_strings(source.media_urls)
        if not urls:
            raise ValueError(f"Source {source.type} node has no media URL; select a take first")
        return urls[0], None
    if source.type == "video":
        data = source.data or {}
        raw_candidates = data.get("candidates")
        if isinstance(raw_candidates, list) and raw_candidates:
            candidates = _candidate_list(raw_candidates)
            selected_id = data.get("selected_candidate_id")
            picked = None
            if selected_id:
                picked = next((item for item in candidates if item.get("id") == selected_id and item.get("status") == "completed"), None)
            if picked is None:
                picked = next((item for item in candidates if item.get("status") == "completed"), None)
            url = (picked or {}).get("media_url") or (picked or {}).get("video_url")
            if not isinstance(url, str) or not url.strip():
                raise ValueError("Source draft has no completed take to reference. Generate or pick a take first.")
            return url, None
        urls = reference_strings(source.media_urls)
        if urls:
            return urls[0], None
        raise ValueError("Source draft has no completed take to reference. Generate or pick a take first.")
    raise ValueError(f"Source node type '{source.type}' cannot be used as a reference")


def _materialize_candidate_reference(
    project: AtelierProject,
    source: AtelierNode,
    candidate: dict[str, Any],
    candidate_id: str,
    source_url: str,
    *,
    node_id_factory,
    now: float,
) -> tuple[AtelierNode, AtelierNode | None]:
    origin = {"node_id": source.id, "candidate_id": candidate_id}
    existing = next(
        (
            node
            for node in project.nodes
            if (node.data or {}).get("reference_origin") == origin
            and node.type == source.type
            and node.media_urls == [source_url]
        ),
        None,
    )
    if existing is not None:
        return existing, None
    width, height = {"image": (240.0, 240.0), "video": (320.0, 240.0), "audio": (260.0, 80.0)}[source.type]
    data = {
        "reference_origin": origin,
        **{
            key: deepcopy(candidate[key])
            for key in ("filename", "duration", "poster_url", "width", "height")
            if key in candidate
        },
    }
    node = AtelierNode(
        id=node_id_factory(),
        project_id=project.id,
        type=source.type,
        title=f"{source.title} · {candidate.get('label') or candidate_id}",
        status="completed",
        x=source.x + 40,
        y=source.y + 40,
        width=width,
        height=height,
        media_urls=[source_url],
        data=data,
        created_at=now,
        updated_at=now,
    )
    return node, node


def _candidate_list(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []
