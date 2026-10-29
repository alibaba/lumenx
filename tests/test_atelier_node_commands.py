from __future__ import annotations

import pytest

from src.apps.atelier.nodes import (
    BatchLayoutCommand,
    CreateNodeCommand,
    CreateRegionWrapCommand,
    BatchLayoutPatch,
    LayoutPatch,
    MoveNodeCommand,
    NodeBodyPatch,
    NodeTextPatch,
    RegionAppearancePatch,
    RegionMembersCommand,
)
from src.apps.atelier.service import AtelierService
from src.apps.atelier.storage import AtelierStorage
from src.utils.json_storage import StorageConflictError


class Sequencer:
    def __init__(self):
        self.now = 10.0
        self.ids = 0

    def clock(self) -> float:
        self.now += 1.0
        return self.now

    def id_factory(self) -> str:
        self.ids += 1
        return f"id-{self.ids}"


def make_service(tmp_path) -> tuple[AtelierService, Sequencer]:
    seq = Sequencer()
    return AtelierService(AtelierStorage(tmp_path), clock=seq.clock, id_factory=seq.id_factory), seq


def test_create_inert_node_generates_id_status_and_rejects_runtime_authority_fields(tmp_path):
    service, _seq = make_service(tmp_path)
    project = service.create_project(title="Canvas")

    ack = service.create_node(
        project.id,
        CreateNodeCommand(
            expected_revision=0,
            type="comment",
            title="Beat",
            prompt="Try this",
            x=12,
            y=24,
            width=300,
            height=180,
        ),
    )

    assert ack.node_ids == ["id-2"]
    node = ack.project.nodes[0]
    assert node.type == "comment"
    assert node.status == "completed"
    assert node.prompt == "Try this"
    assert node.data == {"body": "Try this"}
    assert ack.project.revision == 1
    draft = service.create_node(project.id, CreateNodeCommand(expected_revision=1, type="image"))
    assert draft.project.nodes[1].status == "draft"
    with pytest.raises(ValueError):
        CreateNodeCommand(expected_revision=2, type="text")
    with pytest.raises(ValueError):
        CreateNodeCommand(expected_revision=2, node_id="client")
    with pytest.raises(ValueError):
        CreateNodeCommand(expected_revision=2, media_urls=["x.png"])
    with pytest.raises(ValueError):
        CreateNodeCommand(expected_revision=2, x=True)
    with pytest.raises(ValueError):
        CreateNodeCommand(expected_revision=2, x=10_000_001)
    with pytest.raises(ValueError):
        CreateNodeCommand(expected_revision=2, width=100_001)


def test_text_body_layout_noop_and_body_type_guard(tmp_path):
    service, _seq = make_service(tmp_path)
    project = service.create_project(title="Canvas")
    created = service.create_node(project.id, CreateNodeCommand(expected_revision=0, type="idea", title="A"))
    node_id = created.node_ids[0]
    original_updated_at = created.project.updated_at

    nooped = service.update_node_text(project.id, node_id, NodeTextPatch(expected_revision=1, title="A"))
    assert nooped.project.revision == 1
    assert nooped.project.updated_at == original_updated_at
    assert nooped.node_ids == [node_id]

    text = service.update_node_text(project.id, node_id, NodeTextPatch(expected_revision=1, title="B", prompt="P"))
    assert text.project.revision == 2
    assert text.project.nodes[0].title == "B"

    body = service.update_node_body(project.id, node_id, NodeBodyPatch(expected_revision=2, body="Long body"))
    assert body.project.revision == 3
    assert body.project.nodes[0].data["body"] == "Long body"

    image = service.create_node(project.id, CreateNodeCommand(expected_revision=3, type="image"))
    with pytest.raises(ValueError, match="body"):
        service.update_node_body(project.id, image.node_ids[0], NodeBodyPatch(expected_revision=4, body="no"))

    layout = service.update_node_layout(project.id, node_id, LayoutPatch(expected_revision=4, x=1.5))
    assert layout.project.revision == 5
    assert layout.project.nodes[0].x == 1.5
    assert layout.project.nodes[0].y == 0
    assert layout.project.nodes[0].width == 320
    assert layout.project.nodes[0].data["body"] == "Long body"
    assert layout.project.nodes[0].prompt == "Long body"
    with pytest.raises(StorageConflictError):
        service.update_node_text(project.id, node_id, NodeTextPatch(expected_revision=4, title="late"))


def test_region_wrap_reparent_rejects_nesting_and_moves_members_relative(tmp_path):
    service, _seq = make_service(tmp_path)
    project = service.create_project(title="Canvas")
    first = service.create_node(project.id, CreateNodeCommand(expected_revision=0, title="A", x=10, y=10))
    second = service.create_node(project.id, CreateNodeCommand(expected_revision=1, title="B", x=20, y=20))

    wrapped = service.wrap_region(
        project.id,
        CreateRegionWrapCommand(
            expected_revision=2,
            title="Group",
            color="#123abc",
            collapsed=True,
            member_node_ids=[first.node_ids[0]],
            x=0,
            y=0,
            width=500,
            height=400,
        ),
    )
    region_id = wrapped.node_ids[0]
    region = next(node for node in wrapped.project.nodes if node.id == region_id)
    child = next(node for node in wrapped.project.nodes if node.id == first.node_ids[0])
    assert region.type == "region"
    assert region.status == "completed"
    assert region.data == {"color": "#123abc", "collapsed": True}
    assert child.data["region_id"] == region_id

    reparented = service.set_region_members(
        project.id,
        region_id,
        RegionMembersCommand(expected_revision=3, member_node_ids=[second.node_ids[0]]),
    )
    first_child = next(node for node in reparented.project.nodes if node.id == first.node_ids[0])
    second_child = next(node for node in reparented.project.nodes if node.id == second.node_ids[0])
    assert "region_id" not in first_child.data
    assert second_child.data["region_id"] == region_id

    with pytest.raises(ValueError, match="Regions cannot contain regions"):
        service.set_region_members(project.id, region_id, RegionMembersCommand(expected_revision=4, member_node_ids=[region_id]))

    moved = service.move_nodes(project.id, MoveNodeCommand(expected_revision=4, node_ids=[region_id], dx=5, dy=-2))
    moved_region = next(node for node in moved.project.nodes if node.id == region_id)
    moved_member = next(node for node in moved.project.nodes if node.id == second.node_ids[0])
    untouched = next(node for node in moved.project.nodes if node.id == first.node_ids[0])
    assert (moved_region.x, moved_region.y) == (5, -2)
    assert (moved_member.x, moved_member.y) == (25, 18)
    assert (untouched.x, untouched.y) == (10, 10)

    nooped = service.move_nodes(project.id, MoveNodeCommand(expected_revision=5, node_ids=[region_id], dx=0, dy=0))
    assert nooped.project.revision == 5


def test_batch_geometry_and_region_id_null_are_atomic_and_ordered(tmp_path):
    service, _seq = make_service(tmp_path)
    project = service.create_project(title="Canvas")
    first = service.create_node(project.id, CreateNodeCommand(expected_revision=0, title="A"))
    second = service.create_node(project.id, CreateNodeCommand(expected_revision=1, title="B"))
    region = service.wrap_region(project.id, CreateRegionWrapCommand(expected_revision=2, member_node_ids=[first.node_ids[0]]))

    detached = service.update_nodes_layout(
        project.id,
        BatchLayoutCommand(
            expected_revision=3,
            updates=[
                BatchLayoutPatch(node_id=first.node_ids[0], x=4, y=5, width=210, height=110, region_id=None),
                BatchLayoutPatch(node_id=second.node_ids[0], x=2, y=3, width=200, height=100, region_id=region.node_ids[0]),
            ],
        ),
    )
    assert detached.node_ids == [first.node_ids[0], second.node_ids[0]]
    by_id = {node.id: node for node in detached.project.nodes}
    assert "region_id" not in by_id[first.node_ids[0]].data
    assert by_id[second.node_ids[0]].data["region_id"] == region.node_ids[0]

    with pytest.raises(ValueError):
        BatchLayoutCommand(
            expected_revision=4,
            updates=[BatchLayoutPatch(node_id=first.node_ids[0], expected_revision=None, x=1)],
        )
    with pytest.raises(ValueError):
        service.update_nodes_layout(
            project.id,
            BatchLayoutCommand(
                expected_revision=4,
                updates=[BatchLayoutPatch(node_id=first.node_ids[0], x=float("nan"), y=0, width=1, height=1)],
            ),
        )
    assert service.get_project(project.id).revision == 4


def test_region_and_child_selected_together_moves_child_once_and_bounds(tmp_path):
    service, _seq = make_service(tmp_path)
    project = service.create_project(title="Canvas")
    child = service.create_node(project.id, CreateNodeCommand(expected_revision=0, title="A", x=9_999_999, y=0))
    region = service.wrap_region(project.id, CreateRegionWrapCommand(expected_revision=1, member_node_ids=[child.node_ids[0]], x=0, y=0))

    moved = service.move_nodes(
        project.id,
        MoveNodeCommand(expected_revision=2, node_ids=[region.node_ids[0], child.node_ids[0]], dx=1, dy=0),
    )
    child_node = next(node for node in moved.project.nodes if node.id == child.node_ids[0])
    assert child_node.x == 10_000_000

    with pytest.raises(ValueError):
        service.move_nodes(
            project.id,
            MoveNodeCommand(expected_revision=3, node_ids=[child.node_ids[0]], dx=1, dy=0),
        )
    assert service.get_project(project.id).revision == 3


def test_region_appearance_patch_and_contract_holes_rejected(tmp_path):
    service, _seq = make_service(tmp_path)
    project = service.create_project(title="Canvas")
    child = service.create_node(project.id, CreateNodeCommand(expected_revision=0, title="A"))
    region = service.wrap_region(project.id, CreateRegionWrapCommand(expected_revision=1, member_node_ids=[child.node_ids[0]]))

    changed = service.update_region_appearance(
        project.id,
        region.node_ids[0],
        RegionAppearancePatch(expected_revision=2, color="#ffffff", collapsed=True),
    )
    region_node = next(node for node in changed.project.nodes if node.id == region.node_ids[0])
    assert region_node.data == {"color": "#ffffff", "collapsed": True}

    with pytest.raises(ValueError):
        service.update_region_appearance(
            project.id, child.node_ids[0], RegionAppearancePatch(expected_revision=3, color="#000000")
        )
    with pytest.raises(ValueError):
        LayoutPatch(expected_revision=3, x=None)
    with pytest.raises(ValueError):
        NodeBodyPatch(expected_revision=3, body="x", node_id=child.node_ids[0])


def test_node_commands_preserve_runtime_payload_fields(tmp_path):
    service, _seq = make_service(tmp_path)
    project = service.create_project(title="Canvas")
    created = service.create_node(service.get_project(project.id).id, CreateNodeCommand(expected_revision=0, type="image", title="A"))
    node_id = created.node_ids[0]

    def seed_runtime(project):
        node = next(item for item in project.nodes if item.id == node_id)
        node.status = "processing"
        node.media_urls = ["media/image.png"]
        node.video_task_id = "task-1"
        node.data.update({
            "candidates": [{"id": "c1", "url": "media/c1.png"}],
            "generation": {"provider": "fake", "attempt": 1},
            "reference_image_urls": ["media/ref.png"],
        })

    seeded = service.storage.apply_project_command(project.id, expected_revision=1, command=seed_runtime)
    before = seeded.nodes[0].model_dump(mode="json")

    after_layout = service.update_node_layout(project.id, node_id, LayoutPatch(expected_revision=2, x=99))
    layout_node = after_layout.project.nodes[0].model_dump(mode="json")
    assert layout_node["x"] == 99
    assert layout_node["updated_at"] != before["updated_at"]
    assert layout_node["media_urls"] == before["media_urls"]
    assert layout_node["video_task_id"] == before["video_task_id"]
    assert layout_node["data"] == before["data"]
    assert layout_node["status"] == before["status"]

    after_text = service.update_node_text(project.id, node_id, NodeTextPatch(expected_revision=3, title="B"))
    text_node = after_text.project.nodes[0].model_dump(mode="json")
    assert text_node["title"] == "B"
    assert text_node["data"] == before["data"]
    assert text_node["media_urls"] == before["media_urls"]

    region = service.wrap_region(project.id, CreateRegionWrapCommand(expected_revision=4, member_node_ids=[node_id]))
    member = next(node for node in region.project.nodes if node.id == node_id)
    assert member.data["candidates"] == before["data"]["candidates"]
    assert member.data["generation"] == before["data"]["generation"]
    assert member.data["reference_image_urls"] == before["data"]["reference_image_urls"]
    assert member.data["region_id"] == region.node_ids[0]


def test_batch_and_wrap_invalid_inputs_are_atomic(tmp_path):
    service, _seq = make_service(tmp_path)
    project = service.create_project(title="Canvas")
    first = service.create_node(project.id, CreateNodeCommand(expected_revision=0, title="A"))
    before = service.get_project(project.id).model_dump(mode="json")

    with pytest.raises(ValueError):
        service.update_nodes_layout(
            project.id,
            BatchLayoutCommand(
                expected_revision=1,
                updates=[
                    BatchLayoutPatch(node_id=first.node_ids[0], x=10),
                    BatchLayoutPatch(node_id="missing", y=20),
                ],
            ),
        )
    assert service.get_project(project.id).model_dump(mode="json") == before

    with pytest.raises(ValueError):
        service.update_nodes_layout(
            project.id,
            BatchLayoutCommand(
                expected_revision=1,
                updates=[BatchLayoutPatch(node_id=first.node_ids[0], x=10_000_001)],
            ),
        )
    assert service.get_project(project.id).model_dump(mode="json") == before

    with pytest.raises(ValueError):
        service.wrap_region(
            project.id,
            CreateRegionWrapCommand(expected_revision=1, member_node_ids=[first.node_ids[0], "missing"]),
        )
    assert service.get_project(project.id).model_dump(mode="json") == before


def test_node_command_write_failure_noop_zero_write_and_restart(tmp_path, monkeypatch):
    service, _seq = make_service(tmp_path)
    project = service.create_project(title="Canvas")
    created = service.create_node(project.id, CreateNodeCommand(expected_revision=0, title="A"))
    node_id = created.node_ids[0]
    before = service.get_project(project.id).model_dump(mode="json")

    from src.apps.atelier import storage as storage_module

    writes = 0
    original_write = storage_module.atomic_write_json

    def count_write(path, value):
        nonlocal writes
        writes += 1
        original_write(path, value)

    monkeypatch.setattr(storage_module, "atomic_write_json", count_write)
    nooped = service.update_node_text(project.id, node_id, NodeTextPatch(expected_revision=1, title="A"))
    assert nooped.project.model_dump(mode="json") == before
    assert writes == 0

    def fail_write(_path, _value):
        raise storage_module.StorageError("disk unavailable")

    monkeypatch.setattr(storage_module, "atomic_write_json", fail_write)
    with pytest.raises(storage_module.StorageError):
        service.update_node_text(project.id, node_id, NodeTextPatch(expected_revision=1, title="B"))
    assert service.get_project(project.id).model_dump(mode="json") == before

    service.close()
    restarted = AtelierService(AtelierStorage(tmp_path))
    assert restarted.get_project(project.id).model_dump(mode="json") == before


def test_concurrent_same_revision_node_commands_allow_one_success(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    service, _seq = make_service(tmp_path)
    project = service.create_project(title="Canvas")
    created = service.create_node(project.id, CreateNodeCommand(expected_revision=0, title="A"))
    node_id = created.node_ids[0]

    def change(title: str):
        return service.update_node_text(project.id, node_id, NodeTextPatch(expected_revision=1, title=title))

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda title: _capture(lambda: change(title)), ["B", "C"]))

    successes = [value for ok, value in results if ok]
    failures = [value for ok, value in results if not ok]
    assert len(successes) == 1
    assert len(failures) == 1
    assert isinstance(failures[0], StorageConflictError)
    assert service.get_project(project.id).revision == 2


def _capture(call):
    try:
        return True, call()
    except Exception as exc:  # noqa: BLE001 - tests assert exact type
        return False, exc


def test_create_color_collapsed_are_region_only_even_when_defaults_are_explicit(tmp_path):
    service, _seq = make_service(tmp_path)
    project = service.create_project(title="Canvas")

    normal = service.create_node(project.id, CreateNodeCommand(expected_revision=0, type="idea"))
    assert normal.project.nodes[0].data == {"body": ""}

    with pytest.raises(ValueError):
        CreateNodeCommand(expected_revision=1, type="idea", color="#646cff")
    with pytest.raises(ValueError):
        CreateNodeCommand(expected_revision=1, type="idea", collapsed=False)

    region = service.create_node(
        project.id,
        CreateNodeCommand(expected_revision=1, type="region", color="#646cff", collapsed=False),
    )
    assert region.project.nodes[1].data == {"color": "#646cff", "collapsed": False}
