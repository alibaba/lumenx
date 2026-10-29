"""Pure Atelier reference ownership helpers.

These helpers mirror the fixed Atelier candidate's reference semantics without
storage, media resolution, HTTP, provider, or filesystem dependencies.
"""

from __future__ import annotations

from typing import Any

from .models import AtelierNode

REFERENCE_URL_FIELDS = ("reference_image_urls", "reference_video_urls", "reference_audio_urls")
PENDING_REFERENCE_FIELD = "pending_reference_nodes"
PENDING_REFERENCE_STATUS = "pending_media"
PENDING_REFERENCE_SCHEMA_VERSION = 1


def reference_strings(value: Any) -> list[str]:
    """Return non-empty string list values, de-duplicated in encounter order."""

    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(item for item in value if isinstance(item, str) and item))


def pending_reference_record(source: AtelierNode) -> dict[str, Any]:
    if source.type not in {"image", "video", "audio"}:
        raise ValueError("Pending reference source kind is invalid")
    return {
        "schema_version": PENDING_REFERENCE_SCHEMA_VERSION,
        "source_node_id": source.id,
        "source_kind": source.type,
        "status": PENDING_REFERENCE_STATUS,
    }


def pending_reference_nodes(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            continue
        source_id = item.get("source_node_id")
        source_kind = item.get("source_kind")
        if (
            item.get("schema_version") != PENDING_REFERENCE_SCHEMA_VERSION
            or not isinstance(source_id, str)
            or not source_id
            or source_kind not in {"image", "video", "audio"}
            or item.get("status") != PENDING_REFERENCE_STATUS
            or source_id in seen
        ):
            continue
        seen.add(source_id)
        result.append({
            "schema_version": PENDING_REFERENCE_SCHEMA_VERSION,
            "source_node_id": source_id,
            "source_kind": source_kind,
            "status": PENDING_REFERENCE_STATUS,
        })
    return result


def pending_reference_source_ids(data: dict[str, Any] | None) -> list[str]:
    return [item["source_node_id"] for item in pending_reference_nodes((data or {}).get(PENDING_REFERENCE_FIELD))]


def is_pending_reference_to(data: dict[str, Any] | None, source_id: str) -> bool:
    return source_id in pending_reference_source_ids(data)


def has_pending_references(data: dict[str, Any] | None) -> bool:
    return bool(pending_reference_source_ids(data))


def remove_pending_reference(data: dict[str, Any], source_id: str) -> dict[str, Any]:
    updated = dict(data)
    if PENDING_REFERENCE_FIELD in updated:
        updated[PENDING_REFERENCE_FIELD] = [
            item for item in pending_reference_nodes(updated.get(PENDING_REFERENCE_FIELD))
            if item["source_node_id"] != source_id
        ]
    return updated


def can_attach_atelier_reference(target: AtelierNode, source: AtelierNode) -> bool:
    """Whether source can be attached to target using Atelier reference semantics."""

    return target.id != source.id and target.project_id == source.project_id and (
        target.type == "video" and source.type in {"image", "video", "audio"}
        or target.type == source.type == "image"
    )


def reference_media_urls(source: AtelierNode) -> list[str]:
    """Collect source media URLs plus completed candidate media aliases."""

    urls = reference_strings(source.media_urls)
    data = source.data or {}
    candidates = _candidate_list(data.get("candidates")) + _candidate_list(data.get("deleted_candidates"))
    for candidate in candidates:
        if isinstance(candidate, dict) and candidate.get("status") == "completed":
            urls += reference_strings([candidate.get("media_url"), candidate.get("video_url")])
    return list(dict.fromkeys(urls))


def canonical_atelier_reference_data(
    target: AtelierNode,
    nodes: list[AtelierNode],
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return normalized reference fields for target without mutating inputs.

    Identity evidence comes from reference_node_ids and source parent hints.
    Media kind is derived from source node type, not filename suffixes.
    """

    source_data = target.data if data is None else data
    refs: dict[str, Any] = {
        field: reference_strings(source_data[field])
        for field in REFERENCE_URL_FIELDS
        if field in source_data
    }
    pending = pending_reference_nodes(source_data.get(PENDING_REFERENCE_FIELD))
    ids = reference_strings(source_data.get("reference_node_ids"))
    sources = [
        source
        for source in nodes
        if can_attach_atelier_reference(target, source)
        and (source.id in ids or source.data.get("parent_node_id") == target.id)
    ]
    source_by_id = {source.id: source for source in sources}
    pending = [
        item for item in pending
        if item["source_node_id"] in source_by_id
        and source_by_id[item["source_node_id"]].type == item["source_kind"]
    ]
    refs["reference_node_ids"] = list(
        dict.fromkeys(
            [source_id for source_id in ids if source_id in source_by_id]
            + [item["source_node_id"] for item in pending]
            + [source.id for source in sources]
        )
    )
    if PENDING_REFERENCE_FIELD in source_data or pending:
        refs[PENDING_REFERENCE_FIELD] = pending
    for source in sources:
        if source.type == "image":
            continue
        known = set(reference_media_urls(source))
        misplaced = [url for url in refs.get("reference_image_urls", []) if url in known]
        if misplaced:
            refs["reference_image_urls"] = [
                url for url in refs["reference_image_urls"] if url not in known
            ]
            field = f"reference_{source.type}_urls"
            refs[field] = list(dict.fromkeys(refs.get(field, []) + misplaced))
    return refs


def unlink_atelier_reference_data(
    target: AtelierNode,
    nodes: list[AtelierNode],
    sources: list[AtelierNode],
    url: str | None = None,
) -> dict[str, Any]:
    """Return target.data with requested source references removed.

    Shared URLs are retained only when a remaining source still explicitly
    attached to this target proves ownership of that URL.
    """

    refs = canonical_atelier_reference_data(target, nodes)
    removed_ids = {source.id for source in sources}
    removed_urls = {value for source in sources for value in reference_media_urls(source)}
    if url:
        removed_urls.add(url)
    refs["reference_node_ids"] = [source_id for source_id in refs["reference_node_ids"] if source_id not in removed_ids]
    if PENDING_REFERENCE_FIELD in refs:
        refs[PENDING_REFERENCE_FIELD] = [item for item in refs[PENDING_REFERENCE_FIELD] if item["source_node_id"] not in removed_ids]
    retained = {
        value
        for source in nodes
        if source.id in refs["reference_node_ids"]
        for value in reference_media_urls(source)
    }
    for field in REFERENCE_URL_FIELDS:
        if field in refs:
            refs[field] = [value for value in refs[field] if value not in removed_urls or value in retained]
    return {**target.data, **refs}


def reference_parent_data(
    source: AtelierNode,
    nodes: list[AtelierNode],
    removed_target_id: str,
) -> dict[str, str | None]:
    """Return parent hint fields after removed_target_id no longer owns source."""

    parent = next(
        (
            target
            for target in nodes
            if target.id != removed_target_id
            and can_attach_atelier_reference(target, source)
            and source.id in reference_strings(target.data.get("reference_node_ids"))
        ),
        None,
    )
    return {
        "parent_node_id": parent.id if parent else None,
        "reference_role": f"{parent.type}_reference_{source.type}" if parent else None,
    }


def _candidate_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []
