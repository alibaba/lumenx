"""Sequence entries may target an unselected completed candidate."""

from __future__ import annotations

from fastapi.testclient import TestClient

from src.apps.atelier.models import AtelierProject
from src.apps.atelier.storage import AtelierStorage
from src.apps.comic_gen.api import create_app
from tests.test_atelier_sequence_commands_api import _make_clip


def test_sequence_save_and_reopen_can_use_unselected_candidate_without_changing_selection_or_judgment(tmp_path):
    _make_clip(tmp_path / "video" / "take-a.mp4", color="red", frequency=440)
    _make_clip(tmp_path / "video" / "take-b.mp4", color="blue", frequency=660)
    storage = AtelierStorage(tmp_path)
    storage.create_project(
        AtelierProject.model_validate(
            {
                "id": "p1",
                "title": "Unselected sequence candidate",
                "created_at": 1.0,
                "updated_at": 1.0,
                "nodes": [
                    {
                        "id": "video-1",
                        "project_id": "p1",
                        "type": "video",
                        "status": "completed",
                        "media_urls": ["video/take-a.mp4"],
                        "data": {
                        "selected_candidate_id": "take-a",
                        "candidates": [
                            {
                                "id": "take-a",
                                "media_kind": "video",
                                "status": "completed",
                                "video_url": "video/take-a.mp4",
                            },
                            {
                                "id": "take-b",
                                "media_kind": "video",
                                "status": "completed",
                                "video_url": "video/take-b.mp4",
                            },
                        ],
                        },
                    }
                ],
                "candidate_judgments": [
                    {
                        "id": "judgment-b",
                        "project_id": "p1",
                        "node_id": "video-1",
                        "candidate_id": "take-b",
                        "media_kind": "video",
                        "verdict": "keep",
                        "reason": "Use in sequence without adopting",
                        "created_at": 2.0,
                    }
                ],
            }
        )
    )
    storage.close()
    app = create_app(data_dir=tmp_path, load_config=False, recover_orphan_tasks=False)

    with TestClient(app) as client:
        saved = client.put(
            "/atelier/projects/p1/sequence",
            json={
                "expected_revision": 0,
                "entries": [
                    {
                        "id": "clip-b",
                        "parentId": "video-1",
                        "candidateId": "take-b",
                        "trimStart": 0,
                        "trimEnd": 0.4,
                    }
                ],
            },
        )
        assert saved.status_code == 200, saved.text

        reopened = client.get("/atelier/projects/p1")
        assert reopened.status_code == 200, reopened.text
        project = reopened.json()
        node = project["nodes"][0]
        assert node["data"]["selected_candidate_id"] == "take-a"
        assert node["media_urls"] == ["video/take-a.mp4"]
        assert project["candidate_judgments"] == [
            {
                "schema_version": 1,
                "id": "judgment-b",
                "project_id": "p1",
                "node_id": "video-1",
                "candidate_id": "take-b",
                "media_kind": "video",
                "verdict": "keep",
                "reason": "Use in sequence without adopting",
                "created_at": 2.0,
            }
        ]
        assert project["sequence"] == [
            {
                "parentId": "video-1",
                "candidateId": "take-b",
                "id": "clip-b",
                "trimStart": 0.0,
                "trimEnd": 0.4,
                "gain": 1.0,
                "transition": {"kind": "cut", "duration": 0.0},
            }
        ]
