"""Export endpoint must return a current merge, not a cached stale URL."""

from fastapi.testclient import TestClient

from src.apps.comic_gen import api
from tests.test_dub_source_provenance import runtime as dub_runtime

runtime = dub_runtime


def test_export_reruns_merge_when_existing_url_may_be_stale(runtime):
    item, state = runtime
    script = item.scripts["p"]
    prior = item.data_dir / "video/prior.mp4"
    prior.write_bytes(b"PRIOR-MERGE")
    script.merged_video_url = "video/prior.mp4"
    item._save_data()

    item.select_video_for_frame("p", "f", "B")

    app = api.create_app(data_dir=item.data_dir, load_config=False, pipeline_instance=item)
    with TestClient(app) as client:
        response = client.post("/projects/p/export", json={})
        assert response.status_code == 200, response.text
        exported_url = response.json()["url"]
        assert exported_url != "video/prior.mp4"
        assert exported_url == item.read_committed_script("p").merged_video_url
        assert state.merge_sources == [str(item.data_dir / "video/B.mp4")]
