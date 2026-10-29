"""The legacy harness substitutes media factories while keeping execution real."""
import pytest
from fastapi.testclient import TestClient

from src.apps.atelier.models import AtelierNode, AtelierProject
from src.models import provider_media_execution as core
from src.utils.media_probe import probe_image_file, probe_media_file
from tests.test_lumenx_generation_browser_smoke import load_smoke_module, write_static_root


@pytest.mark.parametrize("kind", ["image", "audio"])
def test_media_browser_transport_runs_once_and_restores_factories(tmp_path, monkeypatch, kind):
    smoke = load_smoke_module()
    # No video is requested by this journey. Avoid the unrelated video fixture's
    # subprocess cwd override; image/audio still run the real decoding path.
    unused_video = tmp_path / "unused-video.mp4"
    unused_video.write_bytes(b"not-used")
    monkeypatch.setattr(smoke.FakeWanProvider, "_ensure_fixture", lambda _: unused_video)
    evidence = smoke.Evidence(tmp_path / "evidence.json", {})
    data = tmp_path / "data"
    data.mkdir()
    before = (core.WanxImageModel, core.TTSProcessor)
    app = smoke.create_smoke_app(data, write_static_root(tmp_path), evidence, fake_media=True)
    with TestClient(app) as client:
        app.state.atelier_storage.create_project(AtelierProject(
            id="p", title="Synthetic media", created_at=1, updated_at=1,
            nodes=[AtelierNode(id="media", project_id="p", type=kind)],
        ))
        payload = {"expected_revision": 0, "client_request_id": "browser-media"}
        if kind == "image":
            payload.update(model_id="qwen/qwen-image-2.0#image", mode="t2i", prompt="Synthetic blue tile", params={"seed": 0, "watermark": False}, batch_size=1)
        else:
            payload.update(voice="longanyang", text="Synthetic tone, not real speech.", params={"volume": 0})
        endpoint = f"/atelier/projects/p/nodes/media/{kind}_candidates"
        response = client.post(endpoint, json=payload)
        assert response.status_code == 200, response.text
        assert app.state.atelier_generation_dispatcher.wait_for_idle(10)
        result = client.get("/atelier/projects/p/generation/requests/browser-media").json()
        attempt = result["members"][0]["attempt"]
        assert attempt["status"] == "completed", attempt
        media = data / "output" / attempt["output_ref"]
        assert (probe_image_file(media) if kind == "image" else probe_media_file(media, "audio")).kind == kind
        assert result["project"]["nodes"][0]["media_urls"] == []
        assert client.post(endpoint, json=payload).json()["replayed"]
        assert app.state.atelier_generation_dispatcher.wait_for_idle(5)
        assert evidence.payload["fake_media"]["counts"][kind] == 1
        event = evidence.payload["fake_media"]["events"][0]
        if kind == "image":
            assert event["model"] == "qwen-image-2.0"
            assert event["seed"] == 0 and event["watermark"] is False
        else:
            assert event["voice"] == "longanyang" and event["volume"] == 0
    assert (core.WanxImageModel, core.TTSProcessor) == before
    assert evidence.payload["fake_media_restored_at"] >= evidence.payload["fake_media_injected_at"]


def test_media_transport_rejects_overlapping_patch_and_restores_after_exception(tmp_path):
    smoke = load_smoke_module()
    evidence = smoke.Evidence(tmp_path / "evidence.json", {})
    before = (core.WanxImageModel, core.TTSProcessor)
    with pytest.raises(ValueError, match="test interruption"):
        with smoke.fake_media_transport(evidence):
            with pytest.raises(RuntimeError, match="already active"):
                with smoke.fake_media_transport(evidence):
                    pytest.fail("overlapping patch acquired")
            raise ValueError("test interruption")
    assert (core.WanxImageModel, core.TTSProcessor) == before
