"""Physical export deletion must consult real committed cross-product stores."""
from fastapi.testclient import TestClient
import pytest

from src.apps.comic_gen.api import create_app
from src.apps.comic_gen.models import StoryboardFrame
from src.apps.playground.models import PlaygroundGeneration, PlaygroundOutput, PlaygroundTemplate
from tests.test_atelier_export_delete_storage import BYTES, completed_project


@pytest.mark.parametrize("reference_owner", ["studio", "playground_input", "playground_output", "playground_template"])
def test_actual_composition_guard_protects_committed_external_references(tmp_path, reference_owner):
    app = create_app(data_dir=tmp_path, load_config=False, recover_orphan_tasks=False)
    with TestClient(app) as client:
        media = tmp_path / "video" / "atelier_seq_job1.mp4"
        media.parent.mkdir(exist_ok=True)
        media.write_bytes(BYTES)
        app.state.atelier_storage.create_project(completed_project())
        reference = "/files/videos/atelier_seq_job1.mp4"
        playground = app.state.playground_storage
        if reference_owner == "studio":
            pipeline = app.state.pipeline
            script = pipeline.create_project("Consumes historical export", "Local fixture", skip_analysis=True)
            with pipeline._save_lock:
                pipeline.scripts[script.id].frames = [StoryboardFrame(
                    id="frame1", scene_id="", character_ids=[], action_description="Stored video", video_url=reference,
                )]
                pipeline._save_data()
            committed = next(item for item in pipeline.read_committed_projects() if item.id == script.id)
            assert committed.frames[0].video_url == reference
        elif reference_owner == "playground_template":
            playground.add_template(PlaygroundTemplate(
                id="template1", name="Saved remix", prompt="Remix", created_at="2026-10-11", updated_at="2026-10-11",
                default_parameters={"source_video": reference},
            ))
        else:
            playground.add_generation(PlaygroundGeneration(
                id="generation1", mode="v2v", model_id="local-fixture", prompt="Stored remix", status="completed", created_at="2026-10-11",
                input_media=[reference] if reference_owner == "playground_input" else [],
                outputs=[PlaygroundOutput(id="out1", media_path=reference, media_type="video")] if reference_owner == "playground_output" else [],
            ))

        response = client.request("DELETE", "/atelier/projects/p1/exports/export1", json={
            "expected_revision": 0, "client_request_id": "delete-with-reference",
        })

        assert response.status_code == 200, response.text
        receipt = response.json()["export_record"]["deletion"]
        assert receipt["file_status"] == "blocked"
        assert receipt["error_code"] == "has_external_reference"
        assert media.read_bytes() == BYTES


def test_actual_composition_guard_allows_unreferenced_verified_export(tmp_path):
    app = create_app(data_dir=tmp_path, load_config=False, recover_orphan_tasks=False)
    with TestClient(app) as client:
        media = tmp_path / "video" / "atelier_seq_job1.mp4"
        media.parent.mkdir(exist_ok=True)
        media.write_bytes(BYTES)
        app.state.atelier_storage.create_project(completed_project())

        response = client.request("DELETE", "/atelier/projects/p1/exports/export1", json={
            "expected_revision": 0, "client_request_id": "delete-unreferenced",
        })

        assert response.status_code == 200, response.text
        assert response.json()["export_record"]["deletion"]["file_status"] == "deleted"
        assert not media.exists()
