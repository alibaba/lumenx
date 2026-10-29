"""Real application HTTP and image decoding anchor the branch client contract."""

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from src.apps.atelier.candidate_branch import AtelierCandidateBranchService
from src.apps.atelier.generation_commands import IMAGE_PARAMETERS
from src.apps.atelier.models import AtelierProject
from src.apps.atelier.project_package import build_package, prepare_import
from src.apps.comic_gen.api import create_app
from src.models import provider_recovery
from src.utils.generation_intent import normalize_generation_intent
from tests.frozen_image_fixtures import rgb_png_bytes
from tests.test_atelier_media_generation_ledger import _project_payload


def test_candidate_branch_fixture_matches_actual_http_and_managed_media(tmp_path, monkeypatch):
    image = rgb_png_bytes("branch-http-wire")
    raw = _project_payload(media_kind="image")
    raw["agent_policy"]["updated_at"] = 1.0
    raw["nodes"][0]["prompt"] = "Current parent text must not replace frozen text"
    record = raw["generation_attempts"][0]["provider_execution"]
    record["intent"] = normalize_generation_intent({"model_id": "wan/wan2.7-image-pro#image",
        "mode": "t2i", "prompt": "Original frozen image prompt", "params": {"seed": 0, "watermark": False}},
        allowed_modes={"t2i"}, allowed_parameters=IMAGE_PARAMETERS).to_dict()
    record["provider_model_id"] = "wan2.7-image-pro"
    record["fingerprint"] = provider_recovery._fingerprint({key: record[key] for key in (
        "attempt_id", "provider_model_id", "intent", "media", "scope", "credential_ref", "execution_kind", "media_kind", "output_ref")})
    raw["nodes"][0]["data"]["candidates"][0]["execution_fingerprint"] = record["fingerprint"]
    raw["generation_request_receipts"][0]["execution_fingerprint"] = record["fingerprint"]
    for name in ("prepared_artifact", "artifact"):
        record[name]["sha256"] = hashlib.sha256(image).hexdigest()
        record[name]["size_bytes"] = len(image)
    output = tmp_path / record["artifact"]["relative_path"]
    output.parent.mkdir(parents=True)
    output.write_bytes(image)
    source = AtelierProject.model_validate(raw)
    copied = prepare_import(build_package(source, data_dir=tmp_path, source_storage_id="source").data,
        data_dir=tmp_path, target_storage_id="copy", id_factory=lambda kind: f"copy-{kind}", now_fn=lambda: 20)
    (tmp_path / "atelier_projects.json").write_text(json.dumps({source.id: source.model_dump(mode="json"),
        copied.project.id: copied.project.model_dump(mode="json")}))
    # Only deterministic IDs/clocks are injected; the HTTP command, storage,
    # proof checks, image decoder, file publication and reload are real.
    upload_ids = iter(["wire-provider-source", "wire-imported-source"])
    monkeypatch.setattr("src.apps.atelier.media_upload.uuid", SimpleNamespace(uuid4=lambda: SimpleNamespace(hex=next(upload_ids))))
    app = create_app(data_dir=tmp_path, load_config=False, recover_orphan_tasks=False)
    wire = {}
    with TestClient(app) as client:
        for label, project_id, node_id, candidate_id, revision, status in [
            ("provider", "p1", "image-node", "candidate-image", 1, "available"),
            ("imported", "copy-project", "copy-node", "copy-candidate", 0, "unavailable_imported"),
        ]:
            app.state.atelier_candidate_branch = AtelierCandidateBranchService(app.state.atelier_storage,
                clock=lambda: 50, id_factory=lambda: f"branch-{label}")
            before = client.get(f"/atelier/projects/{project_id}")
            assert before.status_code == 200, before.text
            request = {"expected_revision": revision}
            route = f"/atelier/projects/{project_id}/nodes/{node_id}/candidates/{candidate_id}/branch"
            response = client.post(route, json=request)
            assert response.status_code == 200, response.text
            ack = response.json()
            branch = next(node for node in ack["project"]["nodes"] if node["id"] == ack["node_ids"][0])
            assert branch["data"]["branch_source"]["recipe_status"] == status
            media = client.get(f'/files/{branch["data"]["branch_source"]["media_url"]}')
            assert media.status_code == 200 and media.content == image
            assert ack["project"]["nodes"][:-1] == before.json()["nodes"]
            assert ack["project"]["generation_attempts"] == before.json()["generation_attempts"]
            assert ack["project"]["generation_request_receipts"] == before.json()["generation_request_receipts"]
            after = client.get(f"/atelier/projects/{project_id}")
            assert after.json() == ack["project"]
            stale = client.post(route, json=request)
            assert stale.status_code == 409 and stale.json()["code"] == "storage_conflict"
            wire[label] = {"before": before.json(), "request": request, "ack": ack}
    actual = tmp_path / "actual-branch-wire.json"
    actual.write_text(json.dumps(wire, indent=2, ensure_ascii=False) + "\n")
    fixture = Path(__file__).resolve().parents[1] / "frontend/src/__tests__/fixtures/atelier-candidate-branch-wire.json"
    assert wire == json.loads(fixture.read_text())
