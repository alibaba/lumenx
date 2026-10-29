"""Real ordered admission responses shared with frontend contract tests."""

import json
from pathlib import Path

from src.apps.atelier.generation_admission import AtelierGenerationAdmissionService
from src.apps.atelier.models import AtelierAgentPolicy, AtelierNode, AtelierProject
from src.apps.atelier.storage import AtelierStorage
from src.models.provider_tasks import ResolvedCredential
from tests.test_atelier_ordered_generation_admission import PNG, SCOPE, command, image


FIXTURE_DIR = Path(__file__).resolve().parents[1] / "frontend/src/__tests__/fixtures"


def build_ordered_generation_payloads(root):
    store = AtelierStorage(root)
    try:
        (root / "uploads").mkdir(parents=True)
        for name in ("image", "last"):
            (root / "uploads" / f"{name}.png").write_bytes(PNG)
        store.create_project(AtelierProject(
            id="p", title="Ordered media contract", created_at=1.0, updated_at=1.0,
            agent_policy=AtelierAgentPolicy(updated_at=1.0),
            nodes=[AtelierNode(
                id=name, project_id="p", type="image", created_at=1.0, updated_at=1.0,
                media_urls=[f"uploads/{name}.png"],
            ) for name in ("image", "last")] + [AtelierNode(
                id="video", project_id="p", type="video", created_at=1.0, updated_at=1.0,
                data={"reference_node_ids": ["image", "last"]},
            )],
        ))
        ids = iter(["attempt-1", "candidate-1", "attempt-2", "candidate-2", "attempt-3", "candidate-3"])
        admission = AtelierGenerationAdmissionService(
            store, clock=lambda: 10.0, id_factory=lambda: next(ids),
            credential_provider=lambda **_kwargs: ResolvedCredential(SCOPE, "wan3-key", "fixture-secret"),
            server_storage_identity="fixture-storage",
        )
        text_request = command(params={"audio": False, "seed": 0, "duration": -1})
        accepted = admission.admit("p", "video", text_request)
        batch_request = command(
            expected_revision=1, client_request_id="ordered-batch", batch_size=2,
            mode="i2v", media=[image("first_frame"), image("last_frame", "last")],
            params={"audio": False, "watermark": False, "prompt_extend": False, "seed": -1},
        )
        batch = admission.admit("p", "video", batch_request)
        return {
            "t2v": accepted.model_dump(mode="json"),
            "batch": batch.model_dump(mode="json"),
            "batch-replayed": admission.read_request("p", "ordered-batch").model_dump(mode="json"),
            "requests": {
                "t2v": text_request.model_dump(mode="json", exclude_unset=True),
                "batch": batch_request.model_dump(mode="json", exclude_unset=True),
            },
        }
    finally:
        store.close()


def test_ordered_generation_wire_fixtures_match_backend(tmp_path):
    payloads = build_ordered_generation_payloads(tmp_path / "store")
    for name, payload in payloads.items():
        filename = f"atelier-ordered-generation-{name}.json"
        text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        # Output remains inside test isolation; promoting a fixture is an explicit file edit.
        generated = tmp_path / filename
        generated.write_text(text)
        print(f"Generated fixture: {generated}")
        assert "fixture-secret" not in text
    for name, payload in payloads.items():
        assert json.loads((FIXTURE_DIR / f"atelier-ordered-generation-{name}.json").read_text()) == payload
