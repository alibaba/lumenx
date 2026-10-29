from __future__ import annotations

import pytest

from src.apps.atelier.models import AtelierNode, AtelierProject
from src.apps.atelier.reference_commands import AttachPendingReferenceCommand, AttachReferenceCommand, DetachReferenceCommand, attach_pending_reference
from src.apps.atelier.service import AtelierService
from src.apps.atelier.storage import AtelierStorage
from src.utils import json_storage
from src.utils.json_storage import StorageConflictError, StorageError


class Sequencer:
    def __init__(self):
        self.now = 100.0
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


def by_id(project: AtelierProject) -> dict[str, AtelierNode]:
    return {node.id: node for node in project.nodes}


def seed_project(service: AtelierService) -> AtelierProject:
    project = AtelierProject(
        id="p1",
        title="Canvas",
        created_at=1.0,
        updated_at=1.0,
        nodes=[
            AtelierNode(id="target-video", project_id="p1", type="video", data={}),
            AtelierNode(id="target-image", project_id="p1", type="image", data={}),
            AtelierNode(id="image", project_id="p1", type="image", media_urls=["uploads/image.png"], status="completed"),
            AtelierNode(id="audio", project_id="p1", type="audio", media_urls=["audio/sound.mp3"], status="completed"),
            AtelierNode(
                id="video",
                project_id="p1",
                type="video",
                media_urls=["video/stale.mp4"],
                status="completed",
                data={
                    "selected_candidate_id": "take-2",
                    "candidates": [
                        {"id": "take-1", "status": "completed", "video_url": "video/first.mp4"},
                        {"id": "take-2", "status": "completed", "media_url": "video/selected.mp4", "video_url": "video/selected.mp4"},
                    ],
                },
            ),
        ],
    )
    return service.storage.create_project(project)


def test_attach_existing_image_video_audio_references_and_dedupes(tmp_path):
    service, _seq = make_service(tmp_path)
    project = seed_project(service)

    image = service.attach_reference(
        project.id,
        "target-video",
        AttachReferenceCommand(expected_revision=0, source_node_id="image"),
    )
    assert image.node_ids == ["target-video", "image"]
    video_target = by_id(image.project)["target-video"]
    assert video_target.data["reference_node_ids"] == ["image"]
    assert video_target.data["reference_image_urls"] == ["uploads/image.png"]
    assert by_id(image.project)["image"].data == {
        "parent_node_id": "target-video",
        "reference_role": "video_reference_image",
    }

    video = service.attach_reference(
        project.id,
        "target-video",
        AttachReferenceCommand(expected_revision=1, source_node_id="video"),
    )
    assert video.node_ids == ["target-video", "video"]
    video_target = by_id(video.project)["target-video"]
    assert video_target.data["reference_node_ids"] == ["image", "video"]
    assert video_target.data["reference_video_urls"] == ["video/selected.mp4"]
    assert "video/stale.mp4" not in video_target.data["reference_video_urls"]

    audio = service.attach_reference(
        project.id,
        "target-video",
        AttachReferenceCommand(expected_revision=2, source_node_id="audio"),
    )
    assert audio.node_ids == ["target-video", "audio"]
    assert by_id(audio.project)["target-video"].data["reference_audio_urls"] == ["audio/sound.mp3"]

    duplicate = service.attach_reference(
        project.id,
        "target-video",
        AttachReferenceCommand(expected_revision=3, source_node_id="image"),
    )
    assert duplicate.project.revision == 3
    assert by_id(duplicate.project)["target-video"].data["reference_node_ids"] == ["image", "video", "audio"]

    image_target = service.attach_reference(
        project.id,
        "target-image",
        AttachReferenceCommand(expected_revision=3, source_node_id="image"),
    )
    assert by_id(image_target.project)["target-image"].data["reference_image_urls"] == ["uploads/image.png"]
    with pytest.raises(ValueError, match="Image generation only accepts image references"):
        service.attach_reference(project.id, "target-image", AttachReferenceCommand(expected_revision=4, source_node_id="audio"))


def test_attach_pending_reference_persists_graph_only_placeholder_and_resolved_attach_clears_it(tmp_path):
    service, _seq = make_service(tmp_path)
    project = AtelierProject(
        id="p1",
        title="Canvas",
        created_at=1.0,
        updated_at=1.0,
        nodes=[
            AtelierNode(id="target-image", project_id="p1", type="image", data={}),
            AtelierNode(id="placeholder", project_id="p1", type="image", data={}),
        ],
    )
    service.storage.create_project(project)

    pending = service.attach_pending_reference(
        "p1",
        "target-image",
        AttachPendingReferenceCommand(expected_revision=0, source_node_id="placeholder"),
    )

    nodes = by_id(pending.project)
    assert pending.node_ids == ["target-image", "placeholder"]
    assert nodes["target-image"].data == {
        "reference_node_ids": ["placeholder"],
        "pending_reference_nodes": [{
            "schema_version": 1,
            "source_node_id": "placeholder",
            "source_kind": "image",
            "status": "pending_media",
        }],
    }
    assert nodes["placeholder"].data == {
        "parent_node_id": "target-image",
        "reference_role": "image_reference_image",
    }
    assert "reference_image_urls" not in nodes["target-image"].data

    def fill(project: AtelierProject) -> None:
        by_id(project)["placeholder"].media_urls = ["uploads/placeholder.png"]

    service.storage.apply_project_command("p1", expected_revision=1, command=fill)
    resolved = service.attach_reference(
        "p1",
        "target-image",
        AttachReferenceCommand(expected_revision=2, source_node_id="placeholder"),
    )

    target = by_id(resolved.project)["target-image"]
    assert target.data["reference_node_ids"] == ["placeholder"]
    assert target.data["reference_image_urls"] == ["uploads/placeholder.png"]
    assert target.data.get("pending_reference_nodes", []) == []


def test_attach_pending_reference_validates_kind_project_duplicates_and_resolved_source(tmp_path):
    service, _seq = make_service(tmp_path)
    project = AtelierProject(
        id="p1",
        title="Canvas",
        created_at=1.0,
        updated_at=1.0,
        nodes=[
            AtelierNode(id="video-target", project_id="p1", type="video", data={}),
            AtelierNode(id="image-target", project_id="p1", type="image", data={}),
            AtelierNode(id="placeholder", project_id="p1", type="image"),
            AtelierNode(id="audio", project_id="p1", type="audio"),
            AtelierNode(id="resolved", project_id="p1", type="image", media_urls=["uploads/resolved.png"]),
        ],
    )
    service.storage.create_project(project)

    with pytest.raises(ValueError, match="cannot reference itself"):
        service.attach_pending_reference("p1", "image-target", AttachPendingReferenceCommand(expected_revision=0, source_node_id="image-target"))
    with pytest.raises(ValueError, match="Unsupported reference"):
        service.attach_pending_reference("p1", "image-target", AttachPendingReferenceCommand(expected_revision=0, source_node_id="audio"))
    with pytest.raises(ValueError, match="already has media"):
        service.attach_pending_reference("p1", "video-target", AttachPendingReferenceCommand(expected_revision=0, source_node_id="resolved"))

    service.attach_pending_reference("p1", "video-target", AttachPendingReferenceCommand(expected_revision=0, source_node_id="placeholder"))
    with pytest.raises(ValueError, match="already pending"):
        service.attach_pending_reference("p1", "video-target", AttachPendingReferenceCommand(expected_revision=1, source_node_id="placeholder"))


def test_attach_pending_reference_rejects_cross_project_source_without_storage_normalization():
    project = AtelierProject(
        id="p1",
        title="Canvas",
        created_at=1.0,
        updated_at=1.0,
        nodes=[
            AtelierNode(id="target", project_id="p1", type="video"),
            AtelierNode(id="foreign", project_id="other", type="image"),
        ],
    )

    with pytest.raises(ValueError, match="Unsupported reference"):
        attach_pending_reference(
            project,
            "target",
            AttachPendingReferenceCommand(expected_revision=0, source_node_id="foreign"),
            now=2.0,
        )


def test_detach_pending_reference_removes_graph_relation_and_parent_hint(tmp_path):
    service, _seq = make_service(tmp_path)
    project = AtelierProject(
        id="p1",
        title="Canvas",
        created_at=1.0,
        updated_at=1.0,
        nodes=[
            AtelierNode(
                id="target",
                project_id="p1",
                type="video",
                data={
                    "reference_node_ids": ["placeholder"],
                    "pending_reference_nodes": [{"schema_version": 1, "source_node_id": "placeholder", "source_kind": "image", "status": "pending_media"}],
                },
            ),
            AtelierNode(id="placeholder", project_id="p1", type="image", data={"parent_node_id": "target", "reference_role": "video_reference_image"}),
        ],
    )
    service.storage.create_project(project)

    detached = service.detach_reference("p1", "target", DetachReferenceCommand(expected_revision=0, source_node_id="placeholder"))

    nodes = by_id(detached.project)
    assert detached.node_ids == ["target", "placeholder"]
    assert nodes["target"].data["reference_node_ids"] == []
    assert nodes["target"].data["pending_reference_nodes"] == []
    assert nodes["placeholder"].data == {"parent_node_id": None, "reference_role": None}


def test_attach_candidate_materializes_completed_take_without_runtime_authority_and_reuses(tmp_path):
    service, _seq = make_service(tmp_path)
    project = AtelierProject(
        id="p1",
        title="Canvas",
        created_at=1.0,
        updated_at=1.0,
        nodes=[
            AtelierNode(id="target", project_id="p1", type="video", x=10, y=20),
            AtelierNode(
                id="source",
                project_id="p1",
                type="video",
                title="Shot",
                x=100,
                y=200,
                data={
                    "candidates": [
                        {
                            "id": "take",
                            "status": "completed",
                            "media_url": "video/take.mp4",
                            "video_url": "video/take.mp4",
                            "attempt_token": "secret-runtime",
                            "provider_task_id": "provider-runtime",
                            "filename": "take.mp4",
                            "duration": 3.5,
                        }
                    ],
                    "deleted_candidates": [
                        {"id": "deleted", "status": "completed", "media_url": "video/deleted.mp4"}
                    ],
                },
            ),
        ],
    )
    service.storage.create_project(project)

    attached = service.attach_reference(
        "p1",
        "target",
        AttachReferenceCommand(expected_revision=0, source_node_id="source", source_candidate_id="take"),
    )

    assert attached.node_ids == ["target", "id-1"]
    nodes = by_id(attached.project)
    ref = nodes["id-1"]
    assert ref.type == "video"
    assert ref.status == "completed"
    assert ref.media_urls == ["video/take.mp4"]
    assert ref.data == {
        "reference_origin": {"node_id": "source", "candidate_id": "take"},
        "filename": "take.mp4",
        "duration": 3.5,
        "parent_node_id": "target",
        "reference_role": "video_reference_video",
    }
    assert "attempt_token" not in ref.data
    assert nodes["source"].data["candidates"][0]["attempt_token"] == "secret-runtime"
    assert nodes["target"].data["reference_node_ids"] == ["id-1"]
    assert nodes["target"].data["reference_video_urls"] == ["video/take.mp4"]

    reused = service.attach_reference(
        "p1",
        "target",
        AttachReferenceCommand(expected_revision=1, source_node_id="source", source_candidate_id="take"),
    )
    assert reused.project.revision == 1
    assert [node.id for node in reused.project.nodes if node.data.get("reference_origin") == {"node_id": "source", "candidate_id": "take"}] == ["id-1"]

    with pytest.raises(ValueError, match="Source candidate not found"):
        service.attach_reference("p1", "target", AttachReferenceCommand(expected_revision=1, source_node_id="source", source_candidate_id="deleted"))


def test_attach_rejects_damaged_or_active_candidates_without_media_fallback_and_preserves_revision(tmp_path):
    service, _seq = make_service(tmp_path)
    project = AtelierProject(
        id="p1",
        title="Canvas",
        created_at=1.0,
        updated_at=1.0,
        nodes=[
            AtelierNode(id="target", project_id="p1", type="video"),
            AtelierNode(
                id="video",
                project_id="p1",
                type="video",
                media_urls=["video/fallback.mp4"],
                data={"candidates": [{"id": "active", "status": "running", "video_url": "video/active.mp4"}]},
            ),
            AtelierNode(
                id="broken",
                project_id="p1",
                type="video",
                media_urls=["video/fallback2.mp4"],
                data={"candidates": [{"id": "broken", "status": "completed"}]},
            ),
        ],
    )
    service.storage.create_project(project)

    with pytest.raises(ValueError, match="no completed take"):
        service.attach_reference("p1", "target", AttachReferenceCommand(expected_revision=0, source_node_id="video"))
    with pytest.raises(ValueError, match="no completed take"):
        service.attach_reference("p1", "target", AttachReferenceCommand(expected_revision=0, source_node_id="broken"))
    assert service.get_project("p1").revision == 0


def test_detach_reference_is_idempotent_and_retains_shared_url_until_last_owner(tmp_path):
    service, _seq = make_service(tmp_path)
    project = AtelierProject(
        id="p1",
        title="Canvas",
        created_at=1.0,
        updated_at=1.0,
        nodes=[
            AtelierNode(
                id="target",
                project_id="p1",
                type="video",
                data={"reference_node_ids": ["a", "b"], "reference_image_urls": ["shared.png"]},
            ),
            AtelierNode(id="other-target", project_id="p1", type="video", data={"reference_node_ids": ["b"]}),
            AtelierNode(id="a", project_id="p1", type="image", media_urls=["shared.png"], data={"parent_node_id": "target", "reference_role": "video_reference_image"}),
            AtelierNode(id="b", project_id="p1", type="image", media_urls=["shared.png"], data={"parent_node_id": "target", "reference_role": "video_reference_image"}),
        ],
    )
    service.storage.create_project(project)

    first = service.detach_reference(
        "p1",
        "target",
        DetachReferenceCommand(expected_revision=0, reference_url="shared.png", source_node_id="a"),
    )
    nodes = by_id(first.project)
    assert first.node_ids == ["target", "a"]
    assert nodes["target"].data["reference_node_ids"] == ["b"]
    assert nodes["target"].data["reference_image_urls"] == ["shared.png"]
    assert nodes["a"].data == {"parent_node_id": None, "reference_role": None}
    assert nodes["b"].data["parent_node_id"] == "target"

    absent = service.detach_reference(
        "p1",
        "target",
        DetachReferenceCommand(expected_revision=1, reference_url="shared.png", source_node_id="a"),
    )
    assert absent.project.revision == 1
    assert absent.node_ids == ["target"]

    second = service.detach_reference(
        "p1",
        "target",
        DetachReferenceCommand(expected_revision=1, reference_url="shared.png"),
    )
    nodes = by_id(second.project)
    assert second.node_ids == ["target", "b"]
    assert nodes["target"].data["reference_node_ids"] == []
    assert nodes["target"].data["reference_image_urls"] == []
    assert nodes["b"].data == {"parent_node_id": "other-target", "reference_role": "video_reference_image"}


def test_malformed_nonempty_candidate_array_never_falls_back_to_media_urls(tmp_path):
    service, _seq = make_service(tmp_path)
    project = AtelierProject(
        id="p1",
        title="Canvas",
        created_at=1.0,
        updated_at=1.0,
        nodes=[
            AtelierNode(id="target", project_id="p1", type="video"),
            AtelierNode(
                id="broken",
                project_id="p1",
                type="video",
                media_urls=["video/fallback.mp4"],
                data={"candidates": [None, "broken"]},
            ),
        ],
    )
    service.storage.create_project(project)

    with pytest.raises(ValueError, match="no completed take"):
        service.attach_reference("p1", "target", AttachReferenceCommand(expected_revision=0, source_node_id="broken"))

    assert service.get_project("p1").revision == 0


def test_attach_own_completed_take_materializes_source_without_self_loop(tmp_path):
    service, _seq = make_service(tmp_path)
    project = AtelierProject(
        id="p1",
        title="Canvas",
        created_at=1.0,
        updated_at=1.0,
        nodes=[
            AtelierNode(
                id="target",
                project_id="p1",
                type="video",
                title="Self",
                data={"candidates": [{"id": "take", "status": "completed", "video_url": "video/self.mp4"}]},
            ),
        ],
    )
    service.storage.create_project(project)

    attached = service.attach_reference(
        "p1",
        "target",
        AttachReferenceCommand(expected_revision=0, source_node_id="target", source_candidate_id="take"),
    )

    nodes = by_id(attached.project)
    assert attached.node_ids == ["target", "id-1"]
    assert nodes["id-1"].data["reference_origin"] == {"node_id": "target", "candidate_id": "take"}
    assert nodes["target"].data["reference_node_ids"] == ["id-1"]
    assert nodes["target"].data["reference_video_urls"] == ["video/self.mp4"]


def test_video_candidate_selection_falls_back_from_bad_selected_to_first_completed(tmp_path):
    service, _seq = make_service(tmp_path)
    project = AtelierProject(
        id="p1",
        title="Canvas",
        created_at=1.0,
        updated_at=1.0,
        nodes=[
            AtelierNode(id="target", project_id="p1", type="video"),
            AtelierNode(
                id="source",
                project_id="p1",
                type="video",
                data={
                    "selected_candidate_id": "missing",
                    "candidates": [
                        {"id": "active", "status": "running", "video_url": "video/active.mp4"},
                        {"id": "done", "status": "completed", "video_url": "video/done.mp4"},
                    ],
                },
            ),
        ],
    )
    service.storage.create_project(project)

    attached = service.attach_reference("p1", "target", AttachReferenceCommand(expected_revision=0, source_node_id="source"))

    assert by_id(attached.project)["target"].data["reference_video_urls"] == ["video/done.mp4"]


def test_explicit_active_candidate_is_rejected(tmp_path):
    service, _seq = make_service(tmp_path)
    project = AtelierProject(
        id="p1",
        title="Canvas",
        created_at=1.0,
        updated_at=1.0,
        nodes=[
            AtelierNode(id="target", project_id="p1", type="video"),
            AtelierNode(
                id="source",
                project_id="p1",
                type="video",
                data={"candidates": [{"id": "active", "status": "running", "video_url": "video/active.mp4"}]},
            ),
        ],
    )
    service.storage.create_project(project)

    with pytest.raises(ValueError, match="no completed take"):
        service.attach_reference(
            "p1",
            "target",
            AttachReferenceCommand(expected_revision=0, source_node_id="source", source_candidate_id="active"),
        )


def test_unrelated_source_same_url_does_not_block_detach_url_clear(tmp_path):
    service, _seq = make_service(tmp_path)
    project = AtelierProject(
        id="p1",
        title="Canvas",
        created_at=1.0,
        updated_at=1.0,
        nodes=[
            AtelierNode(
                id="target",
                project_id="p1",
                type="video",
                data={"reference_node_ids": ["a"], "reference_image_urls": ["shared.png"]},
            ),
            AtelierNode(id="a", project_id="p1", type="image", media_urls=["shared.png"], data={"parent_node_id": "target", "reference_role": "video_reference_image"}),
            AtelierNode(id="unrelated", project_id="p1", type="image", media_urls=["shared.png"]),
        ],
    )
    service.storage.create_project(project)

    detached = service.detach_reference("p1", "target", DetachReferenceCommand(expected_revision=0, reference_url="shared.png"))

    target = by_id(detached.project)["target"]
    assert target.data["reference_node_ids"] == []
    assert target.data["reference_image_urls"] == []
    assert by_id(detached.project)["unrelated"].data == {}


def test_reference_command_old_revision_and_write_failure_are_atomic(tmp_path, monkeypatch):
    service, _seq = make_service(tmp_path)
    project = seed_project(service)

    service.attach_reference(project.id, "target-video", AttachReferenceCommand(expected_revision=0, source_node_id="image"))
    with pytest.raises(StorageConflictError):
        service.detach_reference(project.id, "target-video", DetachReferenceCommand(expected_revision=0, reference_url="uploads/image.png"))

    original_write = json_storage.atomic_write_json

    def fail_write(path, payload):
        if str(path).endswith("atelier_projects.json"):
            raise StorageError("boom")
        return original_write(path, payload)

    monkeypatch.setattr("src.apps.atelier.storage.atomic_write_json", fail_write)
    with pytest.raises(StorageError):
        service.detach_reference(project.id, "target-video", DetachReferenceCommand(expected_revision=1, reference_url="uploads/image.png"))

    current = service.get_project(project.id)
    assert current.revision == 1
    assert by_id(current)["target-video"].data["reference_image_urls"] == ["uploads/image.png"]
