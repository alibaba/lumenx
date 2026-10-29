"""Portable Studio project package helpers.

This module intentionally stays below the pipeline/API layer. Callers provide a
committed project snapshot plus already-resolved asset layers, then commit the
returned Script under their own storage lock.
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
from dataclasses import dataclass
from pathlib import Path
from contextlib import contextmanager
from typing import Any, Callable, Iterator, Mapping

from pydantic import BaseModel, ValidationError

from ...utils.json_storage import DirectoryWriterLock
from ...core import project_package_media as package_media

from .audio import BGM_PRESETS
from .asset_image_models import AssetImageRequestRecord, studio_image_material_complete
from .models import AssetUnit, Character, DubSource, GenerationStatus, GlobalAssetLibrary, ImageAsset, ImageVariant, MotionReferenceReceipt, Prop, Scene, Script, Series, VideoTask, VideoVariant

PACKAGE_KIND = "lumenx.studio.project"
PACKAGE_SCHEMA_VERSION = 1
INERT_TASK_ERROR = "project_package_execution_not_copied"
INERT_MOTION_HISTORY_ERROR = "project_package_motion_history_not_copied"

PROJECT_ENTRY = "project.json"
MANIFEST_ENTRY = "manifest.json"
MEDIA_ENTRY_PREFIX = "media/"

DEFAULT_MAX_PACKAGE_BYTES = 256 * 1024 * 1024
DEFAULT_MAX_FILE_BYTES = 128 * 1024 * 1024
DEFAULT_MAX_FILES = 4096
DEFAULT_MAX_COMPRESSION_RATIO = 100

_ALLOWED_MEDIA_ROOTS = {
    "assets",
    "audio",
    "export",
    "storyboard",
    "uploads",
    "video",
    "video_inputs",
}
_REF_PREFIXES = (
    "/files/outputs/",
    "/files/output/",
    "/files/",
    "files/",
    "output/",
    "outputs/",
)
_MEDIA_STRING_KEYS = {
    "audio_url",
    "avatar_url",
    "bg_audio_source_video",
    "bg_audio_url",
    "bgm_url",
    "dubbed_video_url",
    "full_body_image_url",
    "headshot_image_url",
    "image_url",
    "last_frame_url",
    "merged_video_url",
    "preview_video_url",
    "rendered_image_url",
    "sfx_url",
    "source_audio_url",
    "three_view_image_url",
    "url",
    "video_url",
}
_MEDIA_LIST_KEYS = {
    "reference_image_urls",
    "reference_video_urls",
    "t2i_image_urls",
}
_TASK_PROVIDER_FIELDS = (
    "provider_name",
    "provider_task_id",
    "provider_request_id",
    "provider_execution",
    "motion_reference",
)
_COMPOSITION_REFERENCES = {
    "character_id": "characters", "character_ids": "characters",
    "scene_id": "scenes", "prop_id": "props", "prop_ids": "props",
    "frame_id": "frames", "task_id": "tasks",
    "characters": "characters", "scene": "scenes", "props": "props",
}
_SECRET_KEY_PARTS = ("api_key", "apikey", "authorization", "credential", "password", "secret", "session", "token")

_MEDIA_POLICY = package_media.MediaRootPolicy(
    allowed_roots=frozenset(_ALLOWED_MEDIA_ROOTS),
    ref_prefixes=_REF_PREFIXES,
    allowed_files=frozenset(preset["url"] for preset in BGM_PRESETS),
)
_METADATA_ENTRIES = {MANIFEST_ENTRY, PROJECT_ENTRY}


class ProjectPackageError(ValueError):
    """Raised when a project package cannot be safely built or imported."""


@dataclass(frozen=True)
class ProjectPackageArtifact:
    data: bytes
    manifest: dict[str, Any]


@dataclass(frozen=True)
class PreparedProjectImport:
    script: Script
    media: dict[str, str]
    manifest: dict[str, Any]
    id_map: dict[str, dict[str, str]]


@contextmanager
def _owned_writer_lock(data_root: Path, writer_lock: DirectoryWriterLock | None) -> Iterator[DirectoryWriterLock]:
    with package_media.owned_writer_lock(data_root, writer_lock, error=ProjectPackageError) as lease:
        yield lease


def build_package(
    script: Script,
    *,
    resolved_assets: GlobalAssetLibrary | Mapping[str, Any],
    data_dir: str | os.PathLike[str],
    source_storage_id: str,
    writer_lock: DirectoryWriterLock | None = None,
    source_series: Series | None = None,
    source_library: GlobalAssetLibrary | None = None,
    max_files: int = DEFAULT_MAX_FILES,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    max_package_bytes: int = DEFAULT_MAX_PACKAGE_BYTES,
) -> ProjectPackageArtifact:
    """Build a portable Studio project package from a committed project view."""

    if not isinstance(source_storage_id, str) or not source_storage_id:
        raise ProjectPackageError("source_storage_id is required")
    data_root = Path(data_dir).resolve()
    assets = _coerce_resolved_assets(resolved_assets)
    export_script = _make_export_script(script, assets)
    prepared_proofs = _preserve_prepared_images(
        script, export_script, source_series=source_series, source_library=source_library,
    )
    _validate_required_references(export_script)
    project_payload = {
        "script": _dump_model(export_script),
        "resolved_assets": _dump_model(_export_assets_from_script(export_script)),
    }
    _reject_unsafe_dynamic_content(project_payload)
    media_refs = _collect_media_refs(project_payload)
    if len(media_refs) > max_files:
        raise ProjectPackageError("Project package has too many media files")

    media_entries: list[dict[str, Any]] = []
    media_blobs: dict[str, bytes] = {}
    total_bytes = 0
    with _owned_writer_lock(data_root, writer_lock) as owned_lock:
        for logical_ref in media_refs:
            data = _read_owned_media(data_root, logical_ref, owned_lock, max_file_bytes)
            size_bytes = len(data)
            total_bytes += size_bytes
            if total_bytes > max_package_bytes:
                raise ProjectPackageError("Project package is too large")
            digest = hashlib.sha256(data).hexdigest()
            if logical_ref in prepared_proofs and prepared_proofs[logical_ref] != (digest, size_bytes):
                raise ProjectPackageError("Prepared image bytes differ from their proof")
            package_path = MEDIA_ENTRY_PREFIX + logical_ref
            media_entries.append(
                {
                    "logical_ref": logical_ref,
                    "package_path": package_path,
                    "sha256": digest,
                    "size_bytes": size_bytes,
                }
            )
            media_blobs[package_path] = data

    manifest = {
        "schema_version": PACKAGE_SCHEMA_VERSION,
        "kind": PACKAGE_KIND,
        "source": {
            "project_id": script.id,
            "project_revision": script.revision,
            "storage_id": source_storage_id,
            "title": script.title,
        },
        "media": media_entries,
    }
    package_io = io.BytesIO()
    try:
        with zipfile.ZipFile(package_io, "w", compression=zipfile.ZIP_STORED) as zf:
            zf.writestr(MANIFEST_ENTRY, _canonical_json(manifest))
            zf.writestr(PROJECT_ENTRY, _canonical_json(project_payload))
            for package_path, data in media_blobs.items():
                zf.writestr(package_path, data)
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        raise ProjectPackageError("Project package could not be written") from exc
    data = package_io.getvalue()
    if len(data) > max_package_bytes:
        raise ProjectPackageError("Project package is too large")
    return ProjectPackageArtifact(data=data, manifest=manifest)


def prepare_import(
    package_bytes: bytes,
    *,
    data_dir: str | os.PathLike[str],
    now: Callable[[], float] | None = None,
    writer_lock: DirectoryWriterLock | None = None,
    max_package_bytes: int = DEFAULT_MAX_PACKAGE_BYTES,
    max_files: int = DEFAULT_MAX_FILES,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    max_compression_ratio: int = DEFAULT_MAX_COMPRESSION_RATIO,
) -> PreparedProjectImport:
    """Validate a package and stage an independent imported Studio Script."""

    if not isinstance(package_bytes, (bytes, bytearray)):
        raise ProjectPackageError("Package data must be bytes")
    if len(package_bytes) > max_package_bytes:
        raise ProjectPackageError("Project package is too large")
    now_fn = now or time.time
    data_root = Path(data_dir).resolve()
    try:
        zf = zipfile.ZipFile(io.BytesIO(package_bytes))
    except zipfile.BadZipFile as exc:
        raise ProjectPackageError("Invalid project package zip") from exc

    with zf:
        _validate_zip_infos(
            zf,
            max_files=max_files,
            max_file_bytes=max_file_bytes,
            max_package_bytes=max_package_bytes,
            max_compression_ratio=max_compression_ratio,
        )
        try:
            manifest = _loads_strict(zf.read(MANIFEST_ENTRY), MANIFEST_ENTRY)
            project_payload = _loads_strict(zf.read(PROJECT_ENTRY), PROJECT_ENTRY)
        except KeyError as exc:
            raise ProjectPackageError("Project package is missing required metadata") from exc
        except (RuntimeError, zipfile.BadZipFile, UnicodeDecodeError, OSError) as exc:
            raise ProjectPackageError("Project package metadata could not be read") from exc
        _validate_manifest(manifest)
        if not isinstance(project_payload, dict):
            raise ProjectPackageError("Project package project payload is invalid")
        _reject_unknown_payload_fields(project_payload)
        try:
            source_script = Script.model_validate(project_payload.get("script"))
            source_assets = GlobalAssetLibrary.model_validate(project_payload.get("resolved_assets"))
        except ValidationError as exc:
            raise ProjectPackageError("Project package project payload is invalid") from exc
        # Validated execution history is not portable metadata or new authority.
        source_script.asset_image_request_scope_id = None
        source_script.asset_image_requests = []
        for frame in source_script.frames:
            frame.clear_image_request_for_copy()
        source_assets.asset_image_requests = []
        project_payload["script"] = _dump_model(source_script)
        project_payload["resolved_assets"] = _dump_model(source_assets)
        _reject_unsafe_dynamic_content(project_payload)

        media_entries = _validated_manifest_media(manifest, zf, max_files=max_files, max_file_bytes=max_file_bytes)
        manifest_refs = {entry["logical_ref"] for entry in media_entries}
        project_refs = set(_collect_media_refs(project_payload))
        if manifest_refs != project_refs:
            raise ProjectPackageError("Media manifest does not match project references")
        expected_entries = {MANIFEST_ENTRY, PROJECT_ENTRY} | {entry["package_path"] for entry in media_entries}
        if {info.filename for info in zf.infolist()} != expected_entries:
            raise ProjectPackageError("Unexpected package entry")

        new_project_id = _new_id("project")
        media_map = _build_import_media_map(media_entries, project_id=new_project_id)
        try:
            imported, id_map = _make_independent_script(
                source_script,
                source_assets,
                project_id=new_project_id,
                media_map=media_map,
                timestamp=float(now_fn()),
            )
        except (ValidationError, ProjectPackageError) as exc:
            if isinstance(exc, ProjectPackageError):
                raise
            raise ProjectPackageError("Project package project payload is invalid") from exc
        with _owned_writer_lock(data_root, writer_lock) as owned_lock:
            _stage_media(zf, media_entries, media_map=media_map, data_root=data_root, writer_lock=owned_lock)

    return PreparedProjectImport(script=imported, media=media_map, manifest=manifest, id_map=id_map)


def discard_prepared_import(
    prepared: PreparedProjectImport, *, data_dir: str | os.PathLike[str], writer_lock: DirectoryWriterLock,
) -> None:
    """Remove only this import's files after a *definite* metadata save failure.

    The caller must hold its lifecycle/storage lock. Never call this when a
    commit may already have become durable; those bytes may be referenced.
    """
    root = Path(data_dir).resolve()
    with _owned_writer_lock(root, writer_lock) as owned_lock:
        package_media.cleanup_imported_media(
            prepared.manifest["media"],
            media_map=prepared.media,
            import_project_id=prepared.script.id,
            data_root=root,
            writer_lock=owned_lock,
            policy=_MEDIA_POLICY,
            error=ProjectPackageError,
        )


def _dump_model(value: Any) -> dict[str, Any]:
    return value.model_dump(mode="json", exclude_none=True)


def _reject_unknown_model_fields(model: Any) -> None:
    payload = model.model_dump(mode="json", exclude_none=True)
    _reject_unknown_payload_fields_for_model(payload, type(model), "project")


def _reject_unknown_payload_fields(payload: Any) -> None:
    if not isinstance(payload, dict) or set(payload) != {"script", "resolved_assets"}:
        raise ProjectPackageError("Project package project payload is invalid")
    _reject_unknown_payload_fields_for_model(payload["script"], Script, "script")
    _reject_unknown_payload_fields_for_model(payload["resolved_assets"], GlobalAssetLibrary, "resolved_assets")


def _reject_unknown_payload_fields_for_model(value: Any, model_type: type[Any], label: str) -> None:
    if not isinstance(value, dict):
        raise ProjectPackageError(f"Unknown project package field: {label}")
    allowed = set(getattr(model_type, "model_fields", {}))
    extra = set(value) - allowed
    if extra:
        raise ProjectPackageError(f"Unknown project package field: {label}.{sorted(extra)[0]}")
    for field_name, field_info in getattr(model_type, "model_fields", {}).items():
        if field_name not in value:
            continue
        _reject_unknown_payload_fields_for_annotation(value[field_name], field_info.annotation, f"{label}.{field_name}")


def _reject_unknown_payload_fields_for_annotation(value: Any, annotation: Any, label: str) -> None:
    from pydantic import BaseModel
    from types import UnionType
    from typing import Any as TypingAny, get_args, get_origin

    origin = get_origin(annotation)
    args = get_args(annotation)
    if annotation is TypingAny:
        return
    if origin is list:
        child = args[0] if args else TypingAny
        if isinstance(value, list):
            for index, item in enumerate(value):
                _reject_unknown_payload_fields_for_annotation(item, child, f"{label}[{index}]")
        return
    if origin is dict or origin is Mapping:
        return
    if origin is not None and str(origin) == "typing.Union" or isinstance(annotation, UnionType):
        for child in args:
            if child is type(None):
                continue
            if isinstance(child, type) and issubclass(child, BaseModel) and isinstance(value, dict):
                _reject_unknown_payload_fields_for_model(value, child, label)
                return
        return
    if isinstance(annotation, type) and issubclass(annotation, BaseModel) and isinstance(value, dict):
        _reject_unknown_payload_fields_for_model(value, annotation, label)


def _coerce_resolved_assets(value: GlobalAssetLibrary | Mapping[str, Any]) -> GlobalAssetLibrary:
    if isinstance(value, GlobalAssetLibrary):
        return value.model_copy(deep=True)
    if isinstance(value, Mapping):
        try:
            return GlobalAssetLibrary.model_validate(dict(value))
        except ValidationError as exc:
            raise ProjectPackageError("resolved_assets is invalid") from exc
    raise ProjectPackageError("resolved_assets must be a GlobalAssetLibrary or mapping")


def _canonical_json(value: Any) -> str:
    return package_media.canonical_json(value, error=ProjectPackageError)


def _loads_strict(data: bytes, label: str) -> Any:
    return package_media.loads_strict(data, label, error=ProjectPackageError)


def _project_asset_closure(script: Script, available: GlobalAssetLibrary) -> GlobalAssetLibrary:
    """Materialize project-owned and referenced assets, not the whole library."""
    collections = ("characters", "scenes", "props")
    needed = {name: {asset.id for asset in getattr(script, name)} for name in collections}
    indexed = {name: {asset.id: asset for asset in getattr(available, name)} for name in collections}

    def composition_refs(value):
        if isinstance(value, dict):
            for key, child in value.items():
                collection = _COMPOSITION_REFERENCES.get(key)
                if collection in needed:
                    if isinstance(child, str):
                        needed[collection].add(child)
                    elif isinstance(child, list) and all(isinstance(item, str) for item in child):
                        needed[collection].update(child)
                    else:
                        raise ProjectPackageError(f"Unsupported composition reference: {key}")
                else:
                    composition_refs(child)
        elif isinstance(value, list):
            for child in value:
                composition_refs(child)

    for frame in script.frames:
        needed["scenes"].add(frame.scene_id)
        needed["characters"].update(frame.character_ids)
        needed["props"].update(frame.prop_ids)
        composition_refs(frame.composition_data)
    for task in script.video_tasks:
        if task.asset_id is not None:
            owners = [name for name in collections if task.asset_id in indexed[name]]
            if len(owners) != 1:
                raise ProjectPackageError("Missing or ambiguous packaged task asset")
            needed[owners[0]].add(task.asset_id)

    # A character variation keeps its base; motion variants keep the asset
    # owning their source image if that image is outside the current asset.
    image_owners: dict[str, set[tuple[str, str]]] = {}
    for name in collections:
        for asset in getattr(available, name):
            for node in _model_nodes(asset):
                if isinstance(node, ImageVariant):
                    image_owners.setdefault(node.id, set()).add((name, asset.id))
    pending = [(name, identity) for name in collections for identity in needed[name]]
    visited = set()
    while pending:
        name, identity = pending.pop()
        if (name, identity) in visited:
            continue
        visited.add((name, identity))
        asset = indexed[name].get(identity)
        if asset is None:
            raise ProjectPackageError(f"Missing packaged {name}: {identity}")
        dependencies = []
        if name == "characters" and asset.base_character_id:
            dependencies.append(("characters", asset.base_character_id))
        for node in _model_nodes(asset):
            if isinstance(node, VideoVariant) and node.source_image_id:
                dependencies.extend(image_owners.get(node.source_image_id, ()))
        for collection, linked_id in dependencies:
            needed[collection].add(linked_id)
            pending.append((collection, linked_id))
    return GlobalAssetLibrary.model_validate({
        name: [asset.model_copy(deep=True) for asset in getattr(available, name) if asset.id in needed[name]]
        for name in collections
    })


def _make_export_script(script: Script, assets: GlobalAssetLibrary) -> Script:
    export_script = script.model_copy(deep=True)
    available_ids = {asset.id for asset in [*assets.characters, *assets.scenes, *assets.props]}
    for task in export_script.video_tasks:
        if task.asset_id is None or task.asset_id in available_ids or not task.has_completed_motion_artifact():
            continue
        binding, record = task.motion_reference, task.provider_execution
        if binding is not None and record is not None and any(
            isinstance(receipt, MotionReferenceReceipt)
            and receipt.asset_type == binding.channel
            and (receipt.task_id, receipt.asset_id, receipt.owner_kind, receipt.owner_id,
                 receipt.client_request_id, receipt.attempt_id, receipt.execution_fingerprint)
            == (task.id, task.asset_id, binding.owner_kind, binding.owner_id,
                binding.client_request_id, record.attempt_id, record.fingerprint)
            for receipt in export_script.provider_request_receipts
        ):
            # A removed owner cannot travel with its completed unselected history.
            # Detach only this proven motion result in the independent copy.
            _strip_task_execution(task)
            task.asset_id = None
    assets = _project_asset_closure(export_script, assets)
    export_script.provider_request_receipts = []
    export_script.asset_image_request_scope_id = None
    export_script.asset_image_requests = []
    export_script.characters = [asset.model_copy(deep=True) for asset in assets.characters]
    export_script.scenes = [asset.model_copy(deep=True) for asset in assets.scenes]
    export_script.props = [asset.model_copy(deep=True) for asset in assets.props]
    export_script.merged_video_source = None
    for frame in export_script.frames:
        frame.clear_image_request_for_copy()
        frame.dialogue_audio_snapshot = None
        frame.dub_preview_source = _clean_dub_source(frame.dub_preview_source, {task.id for task in export_script.video_tasks})
        frame.dub_applied_source = _clean_dub_source(frame.dub_applied_source, {task.id for task in export_script.video_tasks})
    for task in export_script.video_tasks:
        _strip_task_execution(task)
    for asset in [*export_script.characters, *export_script.scenes, *export_script.props]:
        if isinstance(asset, Character):
            asset.reference_sheet_request_id = None
            asset.full_body_request_id = None
            asset.three_view_request_id = None
            asset.headshot_request_id = None
            asset.full_body_motion_request_id = None
            asset.head_shot_motion_request_id = None
        elif isinstance(asset, (Scene, Prop)):
            asset.image_request_id = None
            asset.motion_request_id = None
        if asset.status == GenerationStatus.PROCESSING:
            asset.status = "needs_confirmation"
        for task in getattr(asset, "video_assets", []) or []:
            _strip_task_execution(task)
    _reject_unknown_model_fields(export_script)
    return export_script


def _preserve_prepared_images(
    script: Script, exported: Script, *,
    source_series: Series | None, source_library: GlobalAssetLibrary | None,
) -> dict[str, tuple[str, int]]:
    """Carry proven frame, character, scene and prop images from their owner."""
    owner_views: list[tuple[str, str, Script | Series | GlobalAssetLibrary]] = [("script", script.id, script)]
    if source_series is not None:
        if source_series.id != script.series_id:
            raise ProjectPackageError("Package source series differs from the project relation")
        owner_views.append(("series", source_series.id, source_series))
    if source_library is not None:
        owner_views.append(("global", "library", source_library))
    actual_owners: dict[tuple[str, str], tuple[str, str]] = {}
    for kind, identity, view in owner_views:
        for asset_type, assets in (
            ("character", view.characters), ("scene", view.scenes), ("prop", view.props),
        ):
            for asset in assets:
                actual_owners.setdefault((asset_type, asset.id), (kind, identity))

    proofs: dict[str, tuple[str, int]] = {}
    variants = [value for value in _model_nodes(exported) if isinstance(value, ImageVariant)]
    records = ((kind, identity, saved) for kind, identity, view in owner_views for saved in view.asset_image_requests)
    for owner_kind, owner_id, saved in records:
        if saved.asset_type != "storyboard_frame" and (saved.asset_type, saved.channel) not in {
                ("character", "reference_sheet"), ("character", "three_view"), ("character", "headshot"),
                ("character", "full_body"),
                ("scene", "image"), ("prop", "image"),
        }:
            continue
        if not any(member.execution.prepared_artifact is not None for member in saved.members):
            continue
        try:
            record = AssetImageRequestRecord.model_validate(saved.model_dump())
        except (ValidationError, TypeError, ValueError) as exc:
            raise ProjectPackageError("Invalid prepared image history") from exc
        if (record.owner_kind, record.owner_id) != (owner_kind, owner_id):
            raise ProjectPackageError("Prepared image is stored at another owner")
        if record.asset_type == "storyboard_frame":
            if (record.owner_kind, record.owner_id, record.request_root_kind, record.request_root_id,
                    record.asset_type, record.channel) != ("script", script.id, "project", script.id,
                                                            "storyboard_frame", "image"):
                raise ProjectPackageError("Prepared frame image belongs to another owner")
            frames = [frame for frame in exported.frames if frame.id == record.asset_id]
            if len(frames) != 1:
                raise ProjectPackageError("Prepared frame image has no unique packaged frame")
            frame = frames[0]
            if frame.rendered_image_asset is None:
                frame.rendered_image_asset = ImageAsset.model_validate({})
            histories = [frame.rendered_image_asset.variants]
        else:
            label = "three-view image" if record.channel == "three_view" else "reference sheet"
            if record.channel == "headshot":
                label = "headshot image"
            elif record.channel == "full_body":
                label = "full-body image"
            elif record.asset_type in {"scene", "prop"}:
                label = f"{record.asset_type} image"
            # Episode > Series > Global, before checking the exported closure.
            # Shared requests may have been admitted from another project or a
            # standalone Series; their root is not the identity of this export.
            if actual_owners.get((record.asset_type, record.asset_id)) != (owner_kind, owner_id):
                if owner_kind == "script":
                    raise ProjectPackageError(f"Prepared {label} has lost its project owner")
                continue
            if record.asset_type in {"scene", "prop"}:
                assets = exported.scenes if record.asset_type == "scene" else exported.props
                targets = [asset for asset in assets if asset.id == record.asset_id]
                if not targets and owner_kind != "script":
                    continue
                if len(targets) != 1:
                    raise ProjectPackageError(f"Prepared {label} has no unique packaged target")
                target = targets[0]
                if target.image_asset is None:
                    target.image_asset = ImageAsset.model_validate({})
                histories = [target.image_asset.variants]
            else:
                characters = [asset for asset in exported.characters if asset.id == record.asset_id]
                if not characters and owner_kind != "script":
                    continue  # Unreferenced shared history stays in its source owner.
                if len(characters) != 1:
                    raise ProjectPackageError(f"Prepared {label} has no unique packaged character")
                character = characters[0]
                if record.channel == "three_view":
                    if character.three_view_asset is None:
                        character.three_view_asset = ImageAsset.model_validate({})
                    if character.three_views is None:
                        character.three_views = AssetUnit.model_validate({})
                    histories = [character.three_view_asset.variants, character.three_views.image_variants]
                elif record.channel == "headshot":
                    if character.headshot_asset is None:
                        character.headshot_asset = ImageAsset.model_validate({})
                    if character.head_shot is None:
                        character.head_shot = AssetUnit.model_validate({})
                    histories = [character.headshot_asset.variants, character.head_shot.image_variants]
                elif record.channel == "full_body":
                    if character.full_body_asset is None:
                        character.full_body_asset = ImageAsset.model_validate({})
                    if character.full_body is None:
                        character.full_body = AssetUnit.model_validate({})
                    histories = [character.full_body_asset.variants, character.full_body.image_variants]
                else:
                    if character.reference_sheet is None:
                        character.reference_sheet = AssetUnit.model_validate({})
                    histories = [character.reference_sheet.image_variants]
        for member in record.members:
            proof = member.execution.prepared_artifact
            if proof is None:
                continue
            output_ref = member.execution.output_ref
            if output_ref is None:
                raise ProjectPackageError("Prepared image has no managed output")
            expected = (proof["sha256"], proof["size_bytes"])
            if output_ref in proofs and proofs[output_ref] != expected:
                raise ProjectPackageError("Prepared image proofs disagree")
            proofs[output_ref] = expected
            matching = [value for value in variants if value.id == member.variant_id]
            if any(_normalize_media_ref(value.url) != output_ref for value in matching):
                raise ProjectPackageError("Variant identity refers to different media")
            for history in histories:
                if not any(value.id == member.variant_id for value in history):
                    # Preserve an existing media spelling for mirrored history; do
                    # not introduce a same-id/different-URL conflict during import.
                    variant = ImageVariant.model_validate(dict(
                        id=member.variant_id, url=matching[0].url if matching else output_ref,
                        created_at=record.created_at, prompt_used=(member.execution.intent["prompt"]
                            if studio_image_material_complete(member.execution) else None),
                    ))
                    history.append(variant)
                    variants.append(variant)
            # Existing history metadata and selection are deliberately untouched.
    return proofs


def _export_assets_from_script(script: Script) -> GlobalAssetLibrary:
    return GlobalAssetLibrary.model_validate({
        "characters": [asset.model_copy(deep=True) for asset in script.characters],
        "scenes": [asset.model_copy(deep=True) for asset in script.scenes],
        "props": [asset.model_copy(deep=True) for asset in script.props],
    })


def _strip_task_execution(task: VideoTask) -> None:
    motion_history = task.has_completed_motion_artifact() or (
        task.status == "needs_confirmation" and task.error == INERT_MOTION_HISTORY_ERROR
        and task.frame_id is None and bool(task.video_url)
        and all(getattr(task, field) is None for field in _TASK_PROVIDER_FIELDS)
    )
    for field in _TASK_PROVIDER_FIELDS:
        setattr(task, field, None)
    if task.status == "completed" and task.video_url:
        task.error = None
    elif motion_history:
        task.status = "needs_confirmation"
        task.error = INERT_MOTION_HISTORY_ERROR
    else:
        task.status = "needs_confirmation"
        task.error = INERT_TASK_ERROR
        task.video_url = None


def _clean_dub_source(value: Any, task_ids: set[str]) -> Any:
    if isinstance(value, DubSource) and value.task_id not in task_ids:
        return None
    return value


def _collect_media_refs(value: Any) -> list[str]:
    refs: set[str] = set()

    def visit(node: Any) -> None:
        if isinstance(node, Mapping):
            for child_key, child_value in node.items():
                if child_key in _MEDIA_STRING_KEYS and child_value:
                    refs.add(_normalize_media_ref(child_value))
                elif child_key in _MEDIA_LIST_KEYS:
                    if not isinstance(child_value, list):
                        raise ProjectPackageError(f"Media field must be a list: {child_key}")
                    for item in child_value:
                        if item:
                            refs.add(_normalize_media_ref(item))
                else:
                    visit(child_value)
        elif isinstance(node, list):
            for item in node:
                visit(item)

    visit(value)
    return sorted(refs)


def _reject_unsafe_dynamic_content(value: Any) -> None:
    def visit_dynamic(node: Any, key: str | None = None) -> None:
        if isinstance(node, Mapping):
            for child_key, child_value in node.items():
                lowered = str(child_key).lower()
                if any(part in lowered for part in _SECRET_KEY_PARTS) or child_key in _TASK_PROVIDER_FIELDS:
                    raise ProjectPackageError(f"Unsafe dynamic field: {child_key}")
                if child_key in _MEDIA_STRING_KEYS or child_key in _MEDIA_LIST_KEYS:
                    continue
                visit_dynamic(child_value, str(child_key))
        elif isinstance(node, list):
            for item in node:
                visit_dynamic(item, key)
        elif isinstance(node, float) and not math.isfinite(node):
            raise ProjectPackageError("Project package metadata is not strict JSON")
        elif isinstance(node, str):
            lowered = node.lower().strip()
            if "://" in lowered or lowered.startswith(("data:", "//")):
                raise ProjectPackageError("Unsafe dynamic field contains external reference")
            try:
                _normalize_media_ref(node)
            except ProjectPackageError:
                return
            raise ProjectPackageError("Unsafe dynamic field contains undeclared media reference")

    def visit_model(node: Any) -> None:
        if isinstance(node, BaseModel):
            for field in type(node).model_fields:
                child = getattr(node, field)
                if isinstance(child, Mapping):
                    visit_dynamic(child)
                elif isinstance(child, (BaseModel, list)):
                    visit_model(child)
        elif isinstance(node, list):
            for child in node:
                visit_model(child)

    # Known text fields are creator-authored prose, not executable references.
    # Only flexible JSON dictionaries require the extra metadata boundary.
    visit_model(Script.model_validate(value["script"]))
    visit_model(GlobalAssetLibrary.model_validate(value["resolved_assets"]))


def _normalize_media_ref(value: Any) -> str:
    return package_media.normalize_media_ref(value, policy=_MEDIA_POLICY, error=ProjectPackageError)


def _identity(value: os.stat_result) -> tuple[int, int]:
    return package_media.identity(value)


@contextmanager
def _media_parent(data_root: Path, ref: str, writer_lock: DirectoryWriterLock, *, create=False):
    with package_media.media_parent(
        data_root, ref, writer_lock, policy=_MEDIA_POLICY, create=create, error=ProjectPackageError,
    ) as result:
        yield result


def _read_owned_media(data_root: Path, ref: str, writer_lock: DirectoryWriterLock, max_bytes: int) -> bytes:
    return package_media.read_owned_media(
        data_root, ref, writer_lock, max_bytes, policy=_MEDIA_POLICY, error=ProjectPackageError,
    )


def _validate_zip_infos(
    zf: zipfile.ZipFile,
    *,
    max_files: int,
    max_file_bytes: int,
    max_package_bytes: int,
    max_compression_ratio: int,
) -> None:
    package_media.validate_zip_infos(
        zf,
        metadata_entries=_METADATA_ENTRIES,
        media_entry_prefix=MEDIA_ENTRY_PREFIX,
        max_files=max_files,
        max_file_bytes=max_file_bytes,
        max_package_bytes=max_package_bytes,
        max_compression_ratio=max_compression_ratio,
        error=ProjectPackageError,
    )


def _validate_zip_path(name: str) -> None:
    package_media.validate_zip_path(
        name, metadata_entries=_METADATA_ENTRIES, media_entry_prefix=MEDIA_ENTRY_PREFIX, error=ProjectPackageError,
    )


def _validate_manifest(manifest: Any) -> None:
    if not isinstance(manifest, dict):
        raise ProjectPackageError("Project package manifest must be an object")
    if type(manifest.get("schema_version")) is not int or manifest.get("schema_version") != PACKAGE_SCHEMA_VERSION:
        raise ProjectPackageError("Unsupported package schema")
    if manifest.get("kind") != PACKAGE_KIND:
        raise ProjectPackageError("Unsupported package kind")
    source = manifest.get("source")
    if not isinstance(source, dict):
        raise ProjectPackageError("Project package source metadata is invalid")
    if set(source) != {"project_id", "project_revision", "storage_id", "title"}:
        raise ProjectPackageError("Project package source metadata is invalid")
    if any(not isinstance(source[key], str) or not source[key] for key in ("project_id", "storage_id", "title")):
        raise ProjectPackageError("Project package source metadata is invalid")
    if type(source["project_revision"]) is not int or source["project_revision"] < 0:
        raise ProjectPackageError("Project package source metadata is invalid")
    if not isinstance(manifest.get("media"), list):
        raise ProjectPackageError("Project package media manifest is invalid")


def _validated_manifest_media(
    manifest: Mapping[str, Any],
    zf: zipfile.ZipFile,
    *,
    max_files: int,
    max_file_bytes: int,
) -> list[dict[str, Any]]:
    return package_media.validated_manifest_media(
        manifest,
        zf,
        policy=_MEDIA_POLICY,
        media_entry_prefix=MEDIA_ENTRY_PREFIX,
        max_files=max_files,
        max_file_bytes=max_file_bytes,
        metadata_entries=_METADATA_ENTRIES,
        error=ProjectPackageError,
    )


def _build_import_media_map(media_entries: list[dict[str, Any]], *, project_id: str) -> dict[str, str]:
    media_map = {
        entry["logical_ref"]: _target_ref_for_import(entry["logical_ref"], project_id)
        for entry in media_entries
    }
    if len(set(media_map.values())) != len(media_map):
        raise ProjectPackageError("Imported media target collision")
    return media_map


def _stage_media(
    zf: zipfile.ZipFile,
    media_entries: list[dict[str, Any]],
    *,
    media_map: Mapping[str, str],
    data_root: Path,
    writer_lock: DirectoryWriterLock,
) -> None:
    package_media.stage_media(
        zf, media_entries, media_map=media_map, data_root=data_root, writer_lock=writer_lock,
        policy=_MEDIA_POLICY, error=ProjectPackageError,
    )


def _unlink_created_media(parent_fd: int, name: str, identity: tuple[int, int]) -> None:
    package_media.unlink_created_media(parent_fd, name, identity)


def _target_ref_for_import(logical_ref: str, project_id: str) -> str:
    ref = _normalize_media_ref(logical_ref)
    if ref in _MEDIA_POLICY.allowed_files:
        return posixpath.join("audio", "imported", project_id, ref)
    root, rest = ref.split("/", 1)
    return posixpath.join(root, "imported", project_id, rest)


def _make_independent_script(
    source_script: Script,
    source_assets: GlobalAssetLibrary,
    *,
    project_id: str,
    media_map: Mapping[str, str],
    timestamp: float,
) -> tuple[Script, dict[str, dict[str, str]]]:
    _validate_required_references(_make_export_script(source_script, source_assets))
    id_map = _build_identity_map(source_script, source_assets, project_id=project_id)
    script = source_script.model_copy(deep=True)
    script.id = project_id
    script.revision = 0
    script.series_id = None
    script.episode_number = None
    script.provider_request_receipts = []
    script.asset_image_request_scope_id = None
    script.asset_image_requests = []
    script.created_at = timestamp
    script.updated_at = timestamp
    script.merged_video_source = None

    script.characters = [_remap_asset(asset, "characters", id_map, project_id, timestamp) for asset in source_assets.characters]
    script.scenes = [_remap_asset(asset, "scenes", id_map, project_id, timestamp) for asset in source_assets.scenes]
    script.props = [_remap_asset(asset, "props", id_map, project_id, timestamp) for asset in source_assets.props]

    for frame in script.frames:
        original_id = frame.id
        frame.clear_image_request_for_copy()
        frame.id = id_map["frames"][original_id]
        frame.scene_id = _require_map(frame.scene_id, id_map["scenes"], "scene")
        frame.character_ids = [_require_map(value, id_map["characters"], "character") for value in frame.character_ids]
        frame.prop_ids = [_require_map(value, id_map["props"], "prop") for value in frame.prop_ids]
        frame.selected_video_id = id_map["tasks"].get(frame.selected_video_id or "")
        frame.final_take_id = id_map["tasks"].get(frame.final_take_id or "")
        frame.dubbed_video_task_id = id_map["tasks"].get(frame.dubbed_video_task_id or "")
        frame.dub_preview_source = _remap_dub_source(frame.dub_preview_source, id_map["tasks"])
        frame.dub_applied_source = _remap_dub_source(frame.dub_applied_source, id_map["tasks"])
        frame.dialogue_audio_snapshot = None
        frame.composition_data = _remap_dynamic_refs(frame.composition_data, id_map)
        frame.updated_at = timestamp

    script.video_tasks = [
        _remap_task(task, project_id=project_id, id_map=id_map, timestamp=timestamp)
        for task in source_script.video_tasks
    ]
    _remap_variants(script, id_map)
    _validate_required_references(script)

    dumped = _dump_model(script)
    rewritten = _rewrite_media_refs(dumped, media_map)
    imported = Script.model_validate(rewritten)
    _validate_required_references(imported)
    return imported, id_map


def _build_identity_map(source_script: Script, source_assets: GlobalAssetLibrary, *, project_id: str) -> dict[str, dict[str, str]]:
    id_map: dict[str, dict[str, str]] = {
        "projects": {source_script.id: project_id},
        "frames": {frame.id: _new_id("frame") for frame in source_script.frames},
        "tasks": {},
        "characters": {asset.id: _new_id("character") for asset in source_assets.characters},
        "scenes": {asset.id: _new_id("scene") for asset in source_assets.scenes},
        "props": {asset.id: _new_id("prop") for asset in source_assets.props},
        "image_variants": {},
        "video_variants": {},
    }
    for task in _iter_all_tasks(source_script, source_assets):
        id_map["tasks"].setdefault(task.id, _new_id("task"))
    variants = {}
    for value in [*_model_nodes(source_script), *_model_nodes(source_assets)]:
        kind = "image_variants" if isinstance(value, ImageVariant) else "video_variants" if isinstance(value, VideoVariant) else None
        if isinstance(value, (ImageVariant, VideoVariant)) and kind is not None:
            key = (kind, value.id)
            if key in variants and variants[key] != value.url:
                raise ProjectPackageError("Variant identity refers to different media")
            variants[key] = value.url
            if value.id not in id_map[kind]:
                id_map[kind][value.id] = _new_id("variant")
    return id_map


def _model_nodes(value: Any):
    if isinstance(value, BaseModel):
        yield value
        for name in type(value).model_fields:
            yield from _model_nodes(getattr(value, name))
    elif isinstance(value, list):
        for item in value:
            yield from _model_nodes(item)


def _remap_variants(script: Script, id_map: Mapping[str, Mapping[str, str]]) -> None:
    images, videos = id_map["image_variants"], id_map["video_variants"]
    for value in _model_nodes(script):
        if isinstance(value, ImageVariant):
            value.id = images[value.id]
        elif isinstance(value, VideoVariant):
            value.id = videos[value.id]
            if value.source_image_id is not None:
                value.source_image_id = _require_map(value.source_image_id, images, "source image")
        elif isinstance(value, ImageAsset) and value.selected_id is not None:
            if value.selected_id not in {variant.id for variant in value.variants}:
                raise ProjectPackageError("Image selection is outside its asset")
            value.selected_id = _require_map(value.selected_id, images, "selected image")
        elif isinstance(value, AssetUnit):
            for field, variants, mapping in (
                ("selected_image_id", value.image_variants, images),
                ("selected_video_id", value.video_variants, videos),
            ):
                selected = getattr(value, field)
                if selected is not None:
                    if selected not in {variant.id for variant in variants}:
                        raise ProjectPackageError("Variant selection is outside its asset")
                    setattr(value, field, _require_map(selected, mapping, field))


def _iter_all_tasks(script: Script, assets: GlobalAssetLibrary) -> list[VideoTask]:
    tasks = list(script.video_tasks)
    for asset in [*assets.characters, *assets.scenes, *assets.props]:
        tasks.extend(getattr(asset, "video_assets", []) or [])
    return tasks


def _remap_asset(asset: Any, collection: str, id_map: dict[str, dict[str, str]], project_id: str, timestamp: float) -> Any:
    copied = asset.model_copy(deep=True)
    original_id = copied.id
    copied.id = id_map[collection][original_id]
    if collection == "characters":
        copied.reference_sheet_request_id = None
        copied.full_body_request_id = None
        copied.three_view_request_id = None
        copied.headshot_request_id = None
        copied.full_body_motion_request_id = None
        copied.head_shot_motion_request_id = None
        copied.base_character_id = id_map["characters"].get(copied.base_character_id or "")
    elif collection in {"scenes", "props"}:
        copied.image_request_id = None
        copied.motion_request_id = None
    if copied.status == GenerationStatus.PROCESSING:
        copied.status = "needs_confirmation"
    copied.video_assets = [
        _remap_task(task, project_id=project_id, id_map=id_map, timestamp=timestamp, asset_id=copied.id)
        for task in (getattr(copied, "video_assets", []) or [])
    ]
    return copied


def _remap_task(
    task: VideoTask,
    *,
    project_id: str,
    id_map: dict[str, dict[str, str]],
    timestamp: float,
    asset_id: str | None = None,
) -> VideoTask:
    copied = task.model_copy(deep=True)
    copied.id = id_map["tasks"][task.id]
    copied.project_id = project_id
    if copied.frame_id is not None:
        copied.frame_id = _require_map(copied.frame_id, id_map["frames"], "frame")
    if asset_id is not None:
        copied.asset_id = asset_id
    elif copied.asset_id:
        copied.asset_id = (
            id_map["characters"].get(copied.asset_id)
            or id_map["scenes"].get(copied.asset_id)
            or id_map["props"].get(copied.asset_id)
        )
        if copied.asset_id is None:
            raise ProjectPackageError("Missing packaged asset for video task")
    _strip_task_execution(copied)
    copied.created_at = timestamp
    return copied


def _require_map(value: str, mapping: Mapping[str, str], label: str) -> str:
    if value not in mapping:
        raise ProjectPackageError(f"Missing packaged {label}: {value}")
    return mapping[value]


def _remap_dub_source(value: Any, task_map: Mapping[str, str]) -> Any:
    if isinstance(value, DubSource):
        new_task_id = task_map.get(value.task_id)
        if new_task_id:
            return value.model_copy(update={"task_id": new_task_id})
        return None
    return value


def _remap_dynamic_refs(value: Any, id_map: Mapping[str, Mapping[str, str]]) -> Any:
    if isinstance(value, dict):
        result = {}
        for key, child in value.items():
            if key in _COMPOSITION_REFERENCES:
                mapping = id_map[_COMPOSITION_REFERENCES[key]]
                if isinstance(child, str):
                    child = _require_map(child, mapping, key)
                elif isinstance(child, list) and all(isinstance(item, str) for item in child):
                    child = [_require_map(item, mapping, key) for item in child]
                else:
                    raise ProjectPackageError(f"Unsupported composition reference: {key}")
                result[key] = child
            else:
                result[key] = _remap_dynamic_refs(child, id_map)
        return result
    if isinstance(value, list):
        return [_remap_dynamic_refs(item, id_map) for item in value]
    return value


def _validate_required_references(script: Script) -> None:
    for collection in (script.frames, script.video_tasks, script.characters, script.scenes, script.props):
        identities = [value.id for value in collection]
        if len(identities) != len(set(identities)):
            raise ProjectPackageError("Duplicate project entity identity")
    scene_ids = {scene.id for scene in script.scenes}
    character_ids = {character.id for character in script.characters}
    prop_ids = {prop.id for prop in script.props}
    task_ids = {task.id for task in script.video_tasks}
    tasks = {task.id: task for task in script.video_tasks}
    frame_ids = {frame.id for frame in script.frames}
    asset_ids = character_ids | scene_ids | prop_ids
    for frame in script.frames:
        if frame.scene_id not in scene_ids:
            raise ProjectPackageError(f"Missing packaged scene: {frame.scene_id}")
        missing_characters = [value for value in frame.character_ids if value not in character_ids]
        if missing_characters:
            raise ProjectPackageError(f"Missing packaged character: {missing_characters[0]}")
        missing_props = [value for value in frame.prop_ids if value not in prop_ids]
        if missing_props:
            raise ProjectPackageError(f"Missing packaged prop: {missing_props[0]}")
        selections = [frame.selected_video_id, frame.final_take_id, frame.dubbed_video_task_id]
        selections.extend(value.task_id for value in (frame.dub_preview_source, frame.dub_applied_source) if isinstance(value, DubSource))
        for value in selections:
            if value is None:
                continue
            if value not in tasks:
                raise ProjectPackageError(f"Missing packaged task: {value}")
            if tasks[value].frame_id != frame.id:
                raise ProjectPackageError("Selected take belongs to a different frame")
    for task in script.video_tasks:
        if task.project_id != script.id:
            raise ProjectPackageError("Video task points at a different project")
        if task.frame_id is not None and task.frame_id not in frame_ids:
            raise ProjectPackageError(f"Missing packaged frame: {task.frame_id}")
        if task.asset_id is not None and task.asset_id not in asset_ids:
            raise ProjectPackageError(f"Missing packaged asset: {task.asset_id}")
    for asset in [*script.characters, *script.scenes, *script.props]:
        for task in getattr(asset, "video_assets", []) or []:
            if task.id not in task_ids and task.frame_id is not None and task.frame_id not in frame_ids:
                raise ProjectPackageError(f"Missing packaged frame: {task.frame_id}")
            if task.asset_id is not None and task.asset_id != asset.id:
                raise ProjectPackageError(f"Missing packaged asset: {task.asset_id}")


def _rewrite_media_refs(value: Any, media_map: Mapping[str, str]) -> Any:
    if isinstance(value, dict):
        rewritten: dict[str, Any] = {}
        for child_key, child_value in value.items():
            if child_key in _MEDIA_STRING_KEYS and isinstance(child_value, str):
                rewritten[child_key] = _rewrite_media_value(child_value, media_map)
            elif child_key in _MEDIA_LIST_KEYS and isinstance(child_value, list):
                rewritten[child_key] = [
                    _rewrite_media_value(item, media_map) if isinstance(item, str) else item
                    for item in child_value
                ]
            else:
                rewritten[child_key] = _rewrite_media_refs(child_value, media_map)
        return rewritten
    if isinstance(value, list):
        return [_rewrite_media_refs(item, media_map) for item in value]
    return value


def _rewrite_media_value(value: str, media_map: Mapping[str, str]) -> str:
    if value == "":
        return value
    logical_ref = _normalize_media_ref(value)
    if logical_ref not in media_map:
        raise ProjectPackageError("Media manifest does not match project references")
    return media_map[logical_ref]


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"
