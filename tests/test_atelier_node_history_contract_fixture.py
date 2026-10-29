"""Backend-produced nonempty delete/restore contract shared with TypeScript."""

import json
from pathlib import Path

from src.apps.atelier.models import AtelierAudioTrack, AtelierNode, AtelierProject, AtelierSequenceEntry
from src.apps.atelier.service import AtelierService
from src.apps.atelier.storage import AtelierStorage


def test_shared_node_history_fixture_is_the_actual_service_contract(tmp_path):
    def node(node_id, kind, data=None, media=None):
        return AtelierNode(id=node_id, project_id="history-project", type=kind,
                           status="completed", data=data or {}, media_urls=media or [],
                           created_at=10, updated_at=10)

    project = AtelierProject(
        id="history-project", title="Receipt contract", created_at=10, updated_at=10,
        nodes=[
            node("target", "video", {"reference_node_ids": ["image", "other", "audio"],
                 "reference_image_urls": ["uploads/image.png", "uploads/other.png", "standalone.png"],
                 "reference_audio_urls": ["audio/sound.mp3"], "region_id": "region"}),
            node("image", "image", {"parent_node_id": "target", "reference_role": "video_reference_image"}, ["uploads/image.png"]),
            node("clip", "video", {"reference_node_ids": ["image", "other"],
                 "reference_image_urls": ["uploads/image.png", "uploads/other.png"],
                 "candidates": [{"id": "take", "status": "completed", "media_url": "video/take.mp4"}]}, ["video/take.mp4"]),
            node("other", "image", {"parent_node_id": "clip", "reference_role": "video_reference_image"}, ["uploads/other.png"]),
            node("audio", "audio", {"parent_node_id": "target", "reference_role": "video_reference_audio"}, ["audio/sound.mp3"]),
            node("region", "region", {"color": "#646cff", "collapsed": False}),
        ],
        sequence=[AtelierSequenceEntry(parentId="clip", candidateId="take", id="clip-entry")],
        audio_tracks=[AtelierAudioTrack(id="audio-track", node_id="audio")],
    )
    project.agent_policy.updated_at = 10
    with_storage = AtelierStorage(tmp_path)
    try:
        with_storage.create_project(project)
        times = iter([20.0, 30.0])
        service = AtelierService(with_storage, clock=lambda: next(times), id_factory=lambda: "delete-receipt")
        deleted = service.delete_nodes(project.id, expected_revision=0, node_ids=["clip", "audio", "image", "region"])
        restored = service.restore_nodes(project.id, expected_revision=1, receipt_id=deleted.receipt_id)
        actual = {"deleted": deleted.model_dump(mode="json"), "restored": restored.model_dump(mode="json")}
    finally:
        with_storage.close()
    fixture = Path(__file__).resolve().parents[1] / "frontend/src/__tests__/fixtures/atelier-node-history.json"
    assert actual == json.loads(fixture.read_text())
