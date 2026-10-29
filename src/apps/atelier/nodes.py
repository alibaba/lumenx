"""Typed Atelier node command DTOs and pure project operations."""

from __future__ import annotations

import math
from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .models import AtelierNode, AtelierProject

BODY_NODE_TYPES = {"idea", "comment"}
COMPLETED_CREATE_TYPES = {"comment", "region"}
MAX_COORDINATE_ABS = 10_000_000.0
MAX_SIZE = 100_000.0


class NodeCommandAck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project: AtelierProject
    node_ids: list[str]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _RevisionModel(_StrictModel):
    expected_revision: int = Field(..., ge=0, strict=True)


class CreateNodeCommand(_RevisionModel):
    type: Literal[
        "idea",
        "comment",
        "region",
        "image",
        "video",
        "audio",
        "draft",
        "seed",
        "plan",
        "workflow",
        "sequence",
    ] = "idea"
    title: str = ""
    prompt: str = ""
    body: str | None = None
    x: float = 0.0
    y: float = 0.0
    width: float = Field(320.0, gt=0)
    height: float = Field(180.0, gt=0)
    color: str = "#646cff"
    collapsed: bool = Field(False, strict=True)

    @field_validator("x", "y", mode="before")
    @classmethod
    def _coordinate(cls, value: Any) -> float:
        return _finite_number(value, limit=MAX_COORDINATE_ABS, name="coordinate")

    @field_validator("width", "height", mode="before")
    @classmethod
    def _size(cls, value: Any) -> float:
        return _finite_number(value, limit=MAX_SIZE, name="size", positive=True)

    @model_validator(mode="after")
    def _validate_type_scoped_fields(self) -> "CreateNodeCommand":
        if self.body is not None and self.type not in BODY_NODE_TYPES:
            raise ValueError("body is only supported for idea/comment nodes")
        if self.type != "region" and ({"color", "collapsed"} & self.model_fields_set):
            raise ValueError("color/collapsed are only supported for region nodes")
        return self


class NodeTextPatch(_RevisionModel):
    title: str | None = None
    prompt: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _reject_nulls(cls, value: Any) -> Any:
        if isinstance(value, dict):
            for key in ("title", "prompt"):
                if key in value and value[key] is None:
                    raise ValueError(f"{key} cannot be null")
        return value


class NodeBodyPatch(_RevisionModel):
    body: str


class _LayoutFields(BaseModel):
    x: float | None = None
    y: float | None = None
    width: float | None = Field(None, gt=0)
    height: float | None = Field(None, gt=0)
    region_id: str | None = None

    @field_validator("x", "y", mode="before")
    @classmethod
    def _coordinate(cls, value: Any) -> float | None:
        if value is None:
            raise ValueError("geometry values cannot be null")
        return _finite_number(value, limit=MAX_COORDINATE_ABS, name="coordinate")

    @field_validator("width", "height", mode="before")
    @classmethod
    def _size(cls, value: Any) -> float | None:
        if value is None:
            raise ValueError("geometry values cannot be null")
        return _finite_number(value, limit=MAX_SIZE, name="size", positive=True)


class LayoutPatch(_RevisionModel, _LayoutFields):
    pass


class BatchLayoutPatch(_StrictModel, _LayoutFields):
    node_id: str


class BatchLayoutCommand(_RevisionModel):
    updates: list[BatchLayoutPatch] = Field(..., min_length=1, max_length=100)

    @model_validator(mode="after")
    def _require_node_ids(self) -> "BatchLayoutCommand":
        seen: set[str] = set()
        for update in self.updates:
            if not update.node_id:
                raise ValueError("Batch layout updates require node_id")
            if update.node_id in seen:
                raise ValueError("Duplicate node_id in layout update")
            seen.add(update.node_id)
        return self


class RegionAppearancePatch(_RevisionModel):
    color: str | None = None
    collapsed: bool | None = Field(None, strict=True)

    @model_validator(mode="before")
    @classmethod
    def _reject_nulls(cls, value: Any) -> Any:
        if isinstance(value, dict):
            for key in ("color", "collapsed"):
                if key in value and value[key] is None:
                    raise ValueError(f"{key} cannot be null")
        return value


class MoveNodeCommand(_RevisionModel):
    node_ids: list[str] = Field(..., min_length=1, max_length=100)
    dx: float = 0.0
    dy: float = 0.0

    @field_validator("dx", "dy", mode="before")
    @classmethod
    def _delta(cls, value: Any) -> float:
        return _finite_number(value, limit=MAX_COORDINATE_ABS, name="move delta")

    @field_validator("node_ids")
    @classmethod
    def _unique_node_ids(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("Duplicate node_ids")
        return value


class CreateRegionWrapCommand(_RevisionModel):
    title: str = ""
    color: str = "#646cff"
    collapsed: bool = Field(False, strict=True)
    member_node_ids: list[str] = Field(default_factory=list, max_length=100)
    x: float = 0.0
    y: float = 0.0
    width: float = Field(320.0, gt=0)
    height: float = Field(180.0, gt=0)

    @field_validator("x", "y", mode="before")
    @classmethod
    def _coordinate(cls, value: Any) -> float:
        return _finite_number(value, limit=MAX_COORDINATE_ABS, name="coordinate")

    @field_validator("width", "height", mode="before")
    @classmethod
    def _size(cls, value: Any) -> float:
        return _finite_number(value, limit=MAX_SIZE, name="size", positive=True)

    @field_validator("member_node_ids")
    @classmethod
    def _unique_members(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("Duplicate member_node_ids")
        return value


class RegionMembersCommand(_RevisionModel):
    member_node_ids: list[str] = Field(default_factory=list, max_length=100)

    @field_validator("member_node_ids")
    @classmethod
    def _unique_members(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("Duplicate member_node_ids")
        return value


def create_node(project: AtelierProject, command: CreateNodeCommand, *, node_id_factory: Callable[[], str], now: float) -> list[str]:
    node_id = node_id_factory()
    prompt = command.prompt
    data: dict[str, Any] = {}
    if command.type in BODY_NODE_TYPES:
        body = command.body if command.body is not None else command.prompt
        prompt = body
        data["body"] = body
    if command.type == "region":
        data["color"] = command.color
        data["collapsed"] = command.collapsed
    node = AtelierNode(
        id=node_id,
        project_id=project.id,
        type=command.type,
        title=command.title,
        prompt=prompt,
        status="completed" if command.type in COMPLETED_CREATE_TYPES else "draft",
        x=command.x,
        y=command.y,
        width=command.width,
        height=command.height,
        data=data,
        created_at=now,
        updated_at=now,
    )
    project.nodes.append(node)
    project.updated_at = now
    return [node_id]


def update_text(project: AtelierProject, node_id: str, command: NodeTextPatch, *, now: float) -> tuple[list[str], bool]:
    node = _find_node(project, node_id)
    changed = False
    if command.title is not None and node.title != command.title:
        node.title = command.title
        changed = True
    if command.prompt is not None and node.prompt != command.prompt:
        node.prompt = command.prompt
        if node.type in BODY_NODE_TYPES:
            node.data["body"] = command.prompt
        changed = True
    if changed:
        _touch(project, node, now)
    return [node_id], changed


def update_body(project: AtelierProject, node_id: str, command: NodeBodyPatch, *, now: float) -> tuple[list[str], bool]:
    node = _find_node(project, node_id)
    if node.type not in BODY_NODE_TYPES:
        raise ValueError("body is only supported for idea/comment nodes")
    if node.data.get("body") == command.body and node.prompt == command.body:
        return [node_id], False
    node.data["body"] = command.body
    node.prompt = command.body
    _touch(project, node, now)
    return [node_id], True


def update_layout(project: AtelierProject, node_id: str, command: LayoutPatch, *, now: float) -> tuple[list[str], bool]:
    node = _find_node(project, node_id)
    changed = _apply_layout(project, node, command)
    if changed:
        _touch(project, node, now)
    return [node_id], changed


def update_batch_layout(project: AtelierProject, command: BatchLayoutCommand, *, now: float) -> tuple[list[str], bool]:
    changed = False
    ordered_ids = [update.node_id for update in command.updates if update.node_id is not None]
    for update in command.updates:
        node = _find_node(project, update.node_id or "")
        if _apply_layout(project, node, update):
            node.updated_at = now
            changed = True
    if changed:
        project.updated_at = now
    return ordered_ids, changed


def wrap_region(project: AtelierProject, command: CreateRegionWrapCommand, *, node_id_factory: Callable[[], str], now: float) -> list[str]:
    region_id = node_id_factory()
    _validate_region_members(project, command.member_node_ids)
    _clear_region_membership(project, command.member_node_ids)
    region = AtelierNode(
        id=region_id,
        project_id=project.id,
        type="region",
        title=command.title,
        status="completed",
        x=command.x,
        y=command.y,
        width=command.width,
        height=command.height,
        data={"color": command.color, "collapsed": command.collapsed},
        created_at=now,
        updated_at=now,
    )
    project.nodes.append(region)
    for member_id in command.member_node_ids:
        member = _find_node(project, member_id)
        member.data["region_id"] = region_id
        member.updated_at = now
    project.updated_at = now
    return [region_id]


def set_region_members(project: AtelierProject, region_id: str, command: RegionMembersCommand, *, now: float) -> tuple[list[str], bool]:
    _find_region(project, region_id)
    _validate_region_members(project, command.member_node_ids)
    changed = False
    current = {node.id for node in project.nodes if node.data.get("region_id") == region_id}
    desired = set(command.member_node_ids)
    if current == desired:
        return [region_id], False
    for node in project.nodes:
        if node.data.get("region_id") == region_id and node.id not in desired:
            node.data.pop("region_id", None)
            node.updated_at = now
            changed = True
    _clear_region_membership(project, command.member_node_ids, except_region_id=region_id)
    for member_id in command.member_node_ids:
        member = _find_node(project, member_id)
        if member.data.get("region_id") != region_id:
            member.data["region_id"] = region_id
            member.updated_at = now
            changed = True
    if changed:
        project.updated_at = now
    return [region_id], changed



def update_region_appearance(project: AtelierProject, region_id: str, command: RegionAppearancePatch, *, now: float) -> tuple[list[str], bool]:
    region = _find_region(project, region_id)
    changed = False
    if command.color is not None and region.data.get("color") != command.color:
        region.data["color"] = command.color
        changed = True
    if command.collapsed is not None and region.data.get("collapsed") != command.collapsed:
        region.data["collapsed"] = command.collapsed
        changed = True
    if changed:
        _touch(project, region, now)
    return [region_id], changed

def move_nodes(project: AtelierProject, command: MoveNodeCommand, *, now: float) -> tuple[list[str], bool]:
    if command.dx == 0 and command.dy == 0:
        for node_id in command.node_ids:
            _find_node(project, node_id)
        return list(command.node_ids), False
    member_ids_to_move: set[str] = set()
    for node_id in command.node_ids:
        node = _find_node(project, node_id)
        if _is_region(node):
            member_ids_to_move.update(_members_of_region(project, node.id))
    unique_targets: list[str] = []
    seen: set[str] = set()
    for node_id in [*command.node_ids, *member_ids_to_move]:
        if node_id not in seen:
            seen.add(node_id)
            unique_targets.append(node_id)
    changed = False
    for node_id in unique_targets:
        node = _find_node(project, node_id)
        new_x = node.x + command.dx
        new_y = node.y + command.dy
        _validate_coordinate(new_x, "coordinate")
        _validate_coordinate(new_y, "coordinate")
        if new_x != node.x or new_y != node.y:
            changed = True
    if not changed:
        return list(command.node_ids), False
    for node_id in unique_targets:
        node = _find_node(project, node_id)
        new_x = node.x + command.dx
        new_y = node.y + command.dy
        if new_x != node.x or new_y != node.y:
            node.x = new_x
            node.y = new_y
            node.updated_at = now
    project.updated_at = now
    return list(command.node_ids), True


def _find_node(project: AtelierProject, node_id: str) -> AtelierNode:
    for node in project.nodes:
        if node.id == node_id:
            return node
    raise ValueError("Atelier node not found")


def _find_region(project: AtelierProject, node_id: str) -> AtelierNode:
    node = _find_node(project, node_id)
    if not _is_region(node):
        raise ValueError("Atelier region not found")
    return node


def _is_region(node: AtelierNode) -> bool:
    return node.type == "region"


def _members_of_region(project: AtelierProject, region_id: str) -> list[str]:
    return [node.id for node in project.nodes if node.data.get("region_id") == region_id]


def _validate_region_members(project: AtelierProject, member_node_ids: list[str]) -> None:
    for member_id in member_node_ids:
        node = _find_node(project, member_id)
        if _is_region(node):
            raise ValueError("Regions cannot contain regions")


def _clear_region_membership(project: AtelierProject, member_node_ids: list[str], *, except_region_id: str | None = None) -> None:
    members = set(member_node_ids)
    if not members:
        return
    for node in project.nodes:
        if node.data.get("region_id") == except_region_id:
            continue
        if node.id in members:
            node.data.pop("region_id", None)


def _apply_layout(project: AtelierProject, node: AtelierNode, patch: LayoutPatch) -> bool:
    changed = False
    fields = patch.model_fields_set
    if "x" in fields and patch.x is not None and node.x != patch.x:
        node.x = patch.x
        changed = True
    if "y" in fields and patch.y is not None and node.y != patch.y:
        node.y = patch.y
        changed = True
    if "width" in fields and patch.width is not None and node.width != patch.width:
        node.width = patch.width
        changed = True
    if "height" in fields and patch.height is not None and node.height != patch.height:
        node.height = patch.height
        changed = True
    if "region_id" in fields:
        if patch.region_id is None:
            if "region_id" in node.data:
                node.data.pop("region_id", None)
                changed = True
        else:
            region = _find_region(project, patch.region_id)
            if region.id == node.id or _is_region(node):
                raise ValueError("Regions cannot contain regions")
            if node.data.get("region_id") != patch.region_id:
                node.data["region_id"] = patch.region_id
                changed = True
    if changed:
        _validate_node_geometry(node)
    return changed


def _touch(project: AtelierProject, node: AtelierNode, now: float) -> None:
    node.updated_at = now
    project.updated_at = now


def _validate_node_geometry(node: AtelierNode) -> None:
    _validate_coordinate(node.x, "coordinate")
    _validate_coordinate(node.y, "coordinate")
    _validate_size(node.width)
    _validate_size(node.height)


def _validate_coordinate(value: float, name: str) -> None:
    _finite_number(value, limit=MAX_COORDINATE_ABS, name=name)


def _validate_size(value: float) -> None:
    _finite_number(value, limit=MAX_SIZE, name="size", positive=True)


def _finite_number(value: Any, *, limit: float, name: str, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    if abs(value) > limit:
        raise ValueError(f"{name} must be within bounds")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} must be within bounds")
    if positive and number <= 0:
        raise ValueError(f"{name} must be positive")
    return number
