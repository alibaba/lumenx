from __future__ import annotations

import json

import pytest

from src.apps.atelier.models import AtelierAudioTrack, AtelierNode, AtelierProject, AtelierSequenceEntry
from src.apps.atelier.node_history import restore_nodes as apply_restore_nodes
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
        return f"rid-{self.ids}"


def make_service(tmp_path) -> tuple[AtelierService, Sequencer]:
    seq = Sequencer()
    return AtelierService(AtelierStorage(tmp_path), clock=seq.clock, id_factory=seq.id_factory), seq


def seed_project(service: AtelierService) -> AtelierProject:
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
                    "reference_node_ids": ["source", "audio"],
                    "reference_video_urls": ["source.mp4", "standalone.mp4"],
                    "reference_audio_urls": ["audio.mp3"],
                },
            ),
            AtelierNode(
                id="source",
                project_id="p1",
                type="video",
                media_urls=["source.mp4"],
                status="completed",
                data={
                    "parent_node_id": "target",
                    "reference_role": "video_reference_image",
                    "region_id": "region",
                    "candidates": [
                        {"id": "c1", "status": "completed", "media_url": "candidate.mp4", "video_url": "candidate.mp4"}
                    ],
                },
            ),
            AtelierNode(
                id="audio",
                project_id="p1",
                type="audio",
                media_urls=["audio.mp3"],
                status="completed",
            ),
            AtelierNode(
                id="region",
                project_id="p1",
                type="region",
                status="completed",
                data={"color": "#111111", "collapsed": False},
            ),
        ],
        sequence=[AtelierSequenceEntry(parentId="source", id="clip-source"), AtelierSequenceEntry(parentId="target", id="clip-target")],
        audio_tracks=[AtelierAudioTrack(id="track-source", node_id="source"), AtelierAudioTrack(id="track-audio", node_id="audio")],
    )
    return service.storage.create_project(project)


def by_id(project: AtelierProject) -> dict[str, AtelierNode]:
    return {node.id: node for node in project.nodes}


def test_delete_receipt_cleans_references_and_restore_replays_exact_owned_state(tmp_path):
    service, _seq = make_service(tmp_path)
    project = seed_project(service)

    deleted = service.delete_nodes(project.id, expected_revision=0, node_ids=["source", "audio"])

    assert deleted.receipt_id == "rid-1"
    assert deleted.node_ids == ["source", "audio"]
    assert [node.id for node in deleted.project.nodes] == ["target", "region"]
    target = by_id(deleted.project)["target"]
    assert target.data["reference_node_ids"] == []
    assert target.data["reference_video_urls"] == ["standalone.mp4"]
    assert deleted.project.sequence == [AtelierSequenceEntry(parentId="target", id="clip-target")]
    assert deleted.project.audio_tracks == []
    receipt = deleted.project.node_delete_receipts[0]
    assert receipt.base_revision == 0
    assert receipt.restore_base_revision is None
    assert receipt.node_snapshots[0].original_index == 1
    assert receipt.node_snapshots[0].reference_media_urls == ["source.mp4", "candidate.mp4"]

    restored = service.restore_nodes(project.id, expected_revision=1, receipt_id=deleted.receipt_id)

    assert restored.restored_node_ids == ["source", "audio"]
    assert restored.skips == []
    assert [node.id for node in restored.project.nodes] == ["target", "source", "audio", "region"]
    restored_nodes = by_id(restored.project)
    assert restored_nodes["target"].data["reference_node_ids"] == ["source", "audio"]
    assert restored_nodes["target"].data["reference_video_urls"] == ["source.mp4", "standalone.mp4"]
    assert restored_nodes["source"].data["candidates"][0]["media_url"] == "candidate.mp4"
    assert restored.project.sequence == [AtelierSequenceEntry(parentId="source", id="clip-source"), AtelierSequenceEntry(parentId="target", id="clip-target")]
    assert restored.project.audio_tracks == [AtelierAudioTrack(id="track-source", node_id="source"), AtelierAudioTrack(id="track-audio", node_id="audio")]
    consumed = restored.project.node_delete_receipts[0]
    assert consumed.restore_base_revision == 1
    assert consumed.restored_at is not None

    with pytest.raises(StorageConflictError, match="already restored"):
        service.restore_nodes(project.id, expected_revision=2, receipt_id=deleted.receipt_id)



def test_delete_batch_receipt_preserves_requested_order_and_snapshot_source_proofs(tmp_path):
    service, _seq = make_service(tmp_path)
    project = AtelierProject(
        id="p1",
        title="Canvas",
        created_at=1.0,
        updated_at=1.0,
        nodes=[
            AtelierNode(id="a", project_id="p1"),
            AtelierNode(id="video", project_id="p1", type="video", data={"reference_node_ids": ["image"], "reference_image_urls": ["image.png"]}),
            AtelierNode(id="c", project_id="p1"),
            AtelierNode(id="image", project_id="p1", type="image", media_urls=["image.png"]),
        ],
    )
    service.storage.create_project(project)

    deleted = service.delete_nodes("p1", expected_revision=0, node_ids=["image", "video"])

    receipt = deleted.project.node_delete_receipts[0]
    assert receipt.node_ids == ["image", "video"]
    assert [snapshot.node.id for snapshot in receipt.node_snapshots] == ["image", "video"]
    assert [snapshot.original_index for snapshot in receipt.node_snapshots] == [3, 1]
    video_snapshot = next(snapshot for snapshot in receipt.node_snapshots if snapshot.node.id == "video")
    assert len(video_snapshot.source_proofs) == 1
    assert video_snapshot.source_proofs[0].source_node_id == "image"
    assert video_snapshot.source_proofs[0].urls == ["image.png"]


def test_restore_preserves_url_only_reference_when_proven_source_is_restored(tmp_path):
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
                data={"reference_video_urls": ["source.mp4"]},
            ),
            AtelierNode(
                id="source",
                project_id="p1",
                type="video",
                media_urls=["source.mp4"],
                status="completed",
            ),
        ],
    )
    service.storage.create_project(project)

    deleted = service.delete_nodes("p1", expected_revision=0, node_ids=["source"])

    assert by_id(deleted.project)["target"].data["reference_video_urls"] == []
    restored = service.restore_nodes("p1", expected_revision=1, receipt_id=deleted.receipt_id)

    assert restored.skips == []
    target = by_id(restored.project)["target"]
    assert "reference_node_ids" not in target.data
    assert target.data["reference_video_urls"] == ["source.mp4"]


def test_restore_nonadjacent_batch_uses_each_original_index(tmp_path):
    service, _seq = make_service(tmp_path)
    project = AtelierProject(
        id="p1",
        title="Canvas",
        created_at=1.0,
        updated_at=1.0,
        nodes=[AtelierNode(id=node_id, project_id="p1") for node_id in ["a", "b", "c", "d"]],
    )
    service.storage.create_project(project)

    deleted = service.delete_nodes("p1", expected_revision=0, node_ids=["d", "b"])
    assert [node.id for node in deleted.project.nodes] == ["a", "c"]

    restored = service.restore_nodes("p1", expected_revision=1, receipt_id=deleted.receipt_id)
    assert [node.id for node in restored.project.nodes] == ["a", "b", "c", "d"]


def test_restore_sanitizes_invalid_snapshot_references_with_explicit_skips(tmp_path):
    service, _seq = make_service(tmp_path)
    project = AtelierProject(
        id="p1",
        title="Canvas",
        created_at=1.0,
        updated_at=1.0,
        nodes=[
            AtelierNode(
                id="video",
                project_id="p1",
                type="video",
                data={"reference_node_ids": ["image"], "reference_image_urls": ["image.png", "standalone.png"]},
            ),
            AtelierNode(id="image", project_id="p1", type="image", media_urls=["image.png"]),
        ],
        sequence=[AtelierSequenceEntry(parentId="video", id="clip-video")],
    )
    service.storage.create_project(project)
    deleted = service.delete_nodes("p1", expected_revision=0, node_ids=["video"])
    service.delete_nodes("p1", expected_revision=1, node_ids=["image"])

    restored = service.restore_nodes("p1", expected_revision=2, receipt_id=deleted.receipt_id)

    video = by_id(restored.project)["video"]
    assert video.data["reference_node_ids"] == []
    assert video.data["reference_image_urls"] == ["standalone.png"]
    assert any(skip.scope == "node" and skip.node_id == "video" for skip in restored.skips)


def test_delete_restore_preserves_pending_reference_groups(tmp_path):
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

    deleted = service.delete_nodes("p1", expected_revision=0, node_ids=["placeholder"])
    target = by_id(deleted.project)["target"]
    assert target.data["reference_node_ids"] == []
    assert target.data["pending_reference_nodes"] == []

    restored = service.restore_nodes("p1", expected_revision=1, receipt_id=deleted.receipt_id)
    target = by_id(restored.project)["target"]
    assert target.data["reference_node_ids"] == ["placeholder"]
    assert target.data["pending_reference_nodes"] == [{"schema_version": 1, "source_node_id": "placeholder", "source_kind": "image", "status": "pending_media"}]
    assert restored.skips == []


def test_delete_blocks_video_task_id_without_terminal_node_status(tmp_path):
    service, _seq = make_service(tmp_path)
    project = AtelierProject(
        id="p1",
        title="Canvas",
        created_at=1.0,
        updated_at=1.0,
        nodes=[AtelierNode(id="tasked", project_id="p1", status="draft", video_task_id="task-1")],
    )
    service.storage.create_project(project)

    with pytest.raises(StorageConflictError, match="active"):
        service.delete_nodes("p1", expected_revision=0, node_ids=["tasked"])


def test_restore_preserves_absent_reference_fields_and_rejects_region_nesting(tmp_path):
    service, _seq = make_service(tmp_path)
    project = AtelierProject(
        id="p1",
        title="Canvas",
        created_at=1.0,
        updated_at=1.0,
        nodes=[
            AtelierNode(id="outer", project_id="p1", type="region", status="completed"),
            AtelierNode(id="inner", project_id="p1", type="region", status="completed", data={"region_id": "outer"}),
        ],
    )
    service.storage.create_project(project)
    deleted = service.delete_nodes("p1", expected_revision=0, node_ids=["inner"])

    restored = service.restore_nodes("p1", expected_revision=1, receipt_id=deleted.receipt_id)

    inner = by_id(restored.project)["inner"]
    assert "reference_node_ids" not in inner.data
    assert "region_id" not in inner.data
    assert any(skip.scope == "node" and skip.node_id == "inner" for skip in restored.skips)

def test_restore_grouped_cas_skips_reference_group_after_half_edit(tmp_path):
    service, _seq = make_service(tmp_path)
    project = seed_project(service)
    deleted = service.delete_nodes(project.id, expected_revision=0, node_ids=["source"])

    def mutate(project: AtelierProject) -> None:
        target = by_id(project)["target"]
        target.data["reference_audio_urls"] = ["audio.mp3", "new-audio.mp3"]

    service.storage.apply_project_command(project.id, expected_revision=1, command=mutate)

    restored = service.restore_nodes(project.id, expected_revision=2, receipt_id=deleted.receipt_id)

    target = by_id(restored.project)["target"]
    assert target.data["reference_node_ids"] == ["audio"]
    assert target.data["reference_video_urls"] == ["standalone.mp4"]
    assert target.data["reference_audio_urls"] == ["audio.mp3", "new-audio.mp3"]
    assert any(skip.scope == "data_group" and skip.node_id == "target" and skip.group == "references" for skip in restored.skips)


def test_delete_rejects_active_or_uncertain_execution_but_allows_bare_generation_config(tmp_path):
    service, _seq = make_service(tmp_path)
    project = AtelierProject(
        id="p1",
        title="Canvas",
        created_at=1.0,
        updated_at=1.0,
        nodes=[
            AtelierNode(id="bare", project_id="p1", type="video", data={"generation": {"model": "m", "params": {}}}),
            AtelierNode(id="pending", project_id="p1", type="video", data={"candidates": [{"id": "c1", "status": "pending"}]}),
            AtelierNode(id="uncertain", project_id="p1", type="video", data={"deleted_candidates": [{"id": "c2", "tracking_stopped": True, "attempt_token": "tok"}]}),
            AtelierNode(id="completed-proof", project_id="p1", type="video", data={"candidates": [{"id": "c3", "status": "completed", "runtime_snapshot": {"attempt_token": "old", "output_path": "video/out.mp4"}}]}),
            AtelierNode(id="unknown-request", project_id="p1", type="video", data={"candidates": [{"id": "c4", "request_id": "req", "generation_snapshot": {"prompt": "p"}}]}),
            AtelierNode(id="failed-storage", project_id="p1", type="video", data={"candidates": [{"id": "c5", "status": "failed", "storage_error": True, "runtime_snapshot": {"attempt_token": "old"}}]}),
            AtelierNode(id="ledger-only", project_id="p1", type="video", data={"generation": {"request_receipts": {"req": {"payload": {"model": "m"}}}}}),
            AtelierNode(id="generation-active", project_id="p1", type="video", data={"generation": {"status": "processing", "attempt_token": "tok"}}),
        ],
    )
    service.storage.create_project(project)

    allowed = service.delete_nodes("p1", expected_revision=0, node_ids=["bare"])
    assert allowed.node_ids == ["bare"]
    completed = service.delete_nodes("p1", expected_revision=1, node_ids=["completed-proof"])
    assert completed.node_ids == ["completed-proof"]
    ledger = service.delete_nodes("p1", expected_revision=2, node_ids=["ledger-only"])
    assert ledger.node_ids == ["ledger-only"]

    with pytest.raises(StorageConflictError, match="active"):
        service.delete_nodes("p1", expected_revision=3, node_ids=["pending"])
    with pytest.raises(StorageConflictError, match="active"):
        service.delete_nodes("p1", expected_revision=3, node_ids=["uncertain"])
    with pytest.raises(StorageConflictError, match="active"):
        service.delete_nodes("p1", expected_revision=3, node_ids=["unknown-request"])
    with pytest.raises(StorageConflictError, match="active"):
        service.delete_nodes("p1", expected_revision=3, node_ids=["failed-storage"])
    with pytest.raises(StorageConflictError, match="active"):
        service.delete_nodes("p1", expected_revision=3, node_ids=["generation-active"])


def test_restore_rejects_live_id_collision_and_does_not_consume_receipt(tmp_path):
    service, _seq = make_service(tmp_path)
    project = seed_project(service)
    deleted = service.delete_nodes(project.id, expected_revision=0, node_ids=["source"])
    detached = deleted.project.model_copy(deep=True)
    detached.nodes.append(AtelierNode(id="source", project_id=project.id, type="idea"))

    with pytest.raises(StorageConflictError, match="conflict"):
        apply_restore_nodes(detached, receipt_id=deleted.receipt_id, now=200.0, base_revision=1)

    assert detached.node_delete_receipts[0].restored_at is None



def test_damaged_persisted_receipts_fail_closed_on_load(tmp_path):
    service, _seq = make_service(tmp_path)
    project = seed_project(service)
    deleted = service.delete_nodes(project.id, expected_revision=0, node_ids=["source"])
    path = tmp_path / "atelier_projects.json"

    raw = json.loads(path.read_text(encoding="utf-8"))
    service.close()
    raw[project.id]["node_delete_receipts"][0]["data_change_groups"][0]["before"].pop("reference_video_urls")
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(StorageError, match="data group fields"):
        AtelierStorage(tmp_path)

    # Restore the flat database shape and damage a different invariant.
    raw = {project.id: deleted.project.model_dump(mode="json")}
    raw[project.id]["node_delete_receipts"][0]["node_snapshots"][0]["source_proofs"] = [
        {"target_node_id": "wrong", "source_node_id": "audio", "source_type": "audio", "urls": ["audio.mp3"]}
    ]
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(StorageError, match="source proof owner"):
        AtelierStorage(tmp_path)


def test_old_reference_receipt_without_pending_group_loads_and_restores(tmp_path):
    service, seq = make_service(tmp_path)
    project = seed_project(service)

    deleted = service.delete_nodes(project.id, expected_revision=0, node_ids=["source", "audio"])
    group = deleted.project.node_delete_receipts[0].data_change_groups[0]
    assert group.group == "references"
    assert "pending_reference_nodes" not in group.before
    assert "pending_reference_nodes" not in group.after
    service.close()

    restarted = AtelierService(AtelierStorage(tmp_path), clock=seq.clock, id_factory=seq.id_factory)
    try:
        restored = restarted.restore_nodes(project.id, expected_revision=1, receipt_id=deleted.receipt_id)
    finally:
        restarted.close()

    target = by_id(restored.project)["target"]
    assert target.data["reference_node_ids"] == ["source", "audio"]
    assert target.data["reference_video_urls"] == ["source.mp4", "standalone.mp4"]
    assert target.data["reference_audio_urls"] == ["audio.mp3"]
    assert restored.skips == []


def test_mixed_reference_receipt_field_sets_fail_closed_on_load(tmp_path):
    service, _seq = make_service(tmp_path)
    project = seed_project(service)
    service.delete_nodes(project.id, expected_revision=0, node_ids=["source", "audio"])
    path = tmp_path / "atelier_projects.json"

    raw = json.loads(path.read_text(encoding="utf-8"))
    service.close()
    raw[project.id]["node_delete_receipts"][0]["data_change_groups"][0]["before"]["pending_reference_nodes"] = {
        "exists": False,
        "value": None,
    }
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(StorageError, match="data group fields"):
        AtelierStorage(tmp_path)


def test_restore_write_failure_leaves_receipt_unconsumed_and_restart_safe(tmp_path, monkeypatch):
    service, _seq = make_service(tmp_path)
    project = seed_project(service)
    deleted = service.delete_nodes(project.id, expected_revision=0, node_ids=["source"])
    real_write = json_storage.atomic_write_json
    calls = 0

    def fail_on_restore(path, payload):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise StorageError("boom", commit_uncertain=False)
        return real_write(path, payload)

    monkeypatch.setattr("src.apps.atelier.storage.atomic_write_json", fail_on_restore)

    with pytest.raises(StorageError, match="boom"):
        service.restore_nodes(project.id, expected_revision=1, receipt_id=deleted.receipt_id)

    current = service.get_project(project.id)
    assert "source" not in by_id(current)
    assert current.node_delete_receipts[0].restored_at is None

    restored = service.restore_nodes(project.id, expected_revision=1, receipt_id=deleted.receipt_id)
    assert "source" in by_id(restored.project)


def test_storage_validation_rejects_unconsumed_receipt_overlap_but_allows_consumed_history(tmp_path):
    service, _seq = make_service(tmp_path)
    project = seed_project(service)
    deleted = service.delete_nodes(project.id, expected_revision=0, node_ids=["source"])
    restored = service.restore_nodes(project.id, expected_revision=1, receipt_id=deleted.receipt_id)
    assert by_id(restored.project)["source"]

    raw = (tmp_path / "atelier_projects.json").read_text(encoding="utf-8")
    assert '"node_delete_receipts"' in raw

    def unconsume(project: AtelierProject) -> None:
        project.node_delete_receipts[0].restored_at = None
        project.node_delete_receipts[0].restore_base_revision = None

    with pytest.raises(StorageError, match="overlaps live"):
        service.storage.apply_project_command(project.id, expected_revision=2, command=unconsume)
