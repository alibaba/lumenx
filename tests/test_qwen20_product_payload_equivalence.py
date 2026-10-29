"""Three real product admissions preserve the same supported Qwen T2I intent."""

import hashlib
import json

from fastapi.testclient import TestClient
import pytest

from src.apps.atelier.models import AtelierProject
from src.utils.media_probe import probe_image_file
from tests import test_studio_core_image_admission as studio
from tests import test_studio_core_image_api as studio_http
from tests import test_wan27_bound_image as wire
from tests.frozen_image_fixtures import rgb_png_bytes
from tests.test_qwen20_bound_image import completed


runtime = studio.runtime
transport = wire.transport
MODEL_IDS = (
    "qwen-image-2.0", "qwen-image-2.0-pro",
    "qwen/qwen-image-2.0#image", "qwen/qwen-image-2.0-pro#image",
)
VERSION = "provider-qwen20-t2i-bound-http-v1"


def completed_records(client, runtime):
    studio_receipt = client.get("/projects/p/asset_image_requests/same-studio")
    assert studio_receipt.status_code == 200
    assert all(item["status"] == "completed" for item in studio_receipt.json()["request"]["members"])
    atelier_receipt = client.get("/atelier/projects/atelier-equivalence/generation/requests/same-atelier")
    assert atelier_receipt.status_code == 200
    assert all(item["attempt"]["status"] == "completed" for item in atelier_receipt.json()["members"])
    playground_receipt = client.get("/playground/requests/same-playground")
    assert playground_receipt.status_code == 200
    assert playground_receipt.json()["generation"]["status"] == "completed"
    assert len(playground_receipt.json()["generation"]["outputs"]) == 2
    return {
        "studio": [member.execution.model_dump(mode="json")
                   for member in runtime.scripts["p"].asset_image_requests[0].members],
        "atelier": [member["attempt"]["provider_execution"]
                    for member in atelier_receipt.json()["members"]],
        "playground": [attempt["provider_execution"]
                       for attempt in playground_receipt.json()["generation"]["attempts"]],
    }


@pytest.mark.parametrize("model_id", MODEL_IDS)
@pytest.mark.parametrize("negative,seed,watermark", [
    ("", 0, False), ("保留原始负向提示词", 2147483647, True),
])
def test_three_http_entries_emit_equal_payloads_without_sharing_product_state(
    runtime, transport, model_id, negative, seed, watermark,
):
    # Reuse setup helpers, but keep app routes, product services, the factory,
    # Core state transitions, Qwen adapter and artifact publication real.
    studio.setup(runtime, "local")
    prompt = "A red coat under soft studio light"
    params = {
        "size": "1024*1024", "seed": seed, "watermark": watermark,
        "prompt_extend": True, "negative_prompt": negative,
    }
    image_bytes = rgb_png_bytes(width=16, height=16)
    transport["queue"] = [item for _ in range(6) for item in (
        wire.response(completed()), wire.response(image_bytes),
    )]
    requests = [
        ("studio", "/projects/p/asset_image_requests", {
            "client_request_id": "same-studio", "asset_id": "c",
            "model_name": model_id, "prompt": prompt, "apply_style": False,
            "aspect_ratio": "1:1", "batch_size": 2, "seed": seed,
            "watermark": watermark, "negative_prompt": negative,
        }),
        ("atelier", "/atelier/projects/atelier-equivalence/nodes/image/image_candidates", {
            "client_request_id": "same-atelier", "expected_revision": 0,
            "mode": "t2i", "model_id": model_id, "prompt": prompt,
            "params": params, "media": [], "batch_size": 2,
        }),
        ("playground", "/playground/generate", {
            "client_request_id": "same-playground", "mode": "t2i",
            "model_id": model_id, "prompt": prompt, "batch_size": 2,
            "parameters": {key: value for key, value in params.items() if key != "negative_prompt"},
            "negative_prompt": negative,
        }),
    ]
    app = studio_http.real_app(runtime)
    with TestClient(app) as client:
        studio_http.enable_fake_key(app)
        app.state.atelier_storage.create_project(AtelierProject.model_validate({
            "id": "atelier-equivalence", "title": "Independent image intent",
            "created_at": 1, "updated_at": 1,
            "nodes": [{"id": "image", "project_id": "atelier-equivalence", "type": "image"}],
        }))
        product_payloads = {}
        for product, path, body in requests:
            before = len(transport["calls"])
            accepted = client.post(path, json=body)
            assert accepted.status_code == 200, (product, accepted.text)
            assert app.state.atelier_generation_dispatcher.wait_for_idle(10)
            actual = transport["calls"][before:]
            assert [request.method for request, _ in actual] == ["POST", "GET", "POST", "GET"], product
            product_payloads[product] = [json.loads(request.body)
                                         for request, _ in actual if request.method == "POST"]
            assert all(request.url.endswith("/multimodal-generation/generation")
                       and "X-DashScope-Async" not in request.headers
                       for request, _ in actual if request.method == "POST"), product

        expected_model = "qwen-image-2.0-pro" if "2.0-pro" in model_id else "qwen-image-2.0"
        expected_payload = {
            "model": expected_model,
            "input": {"messages": [{"role": "user", "content": [{"text": prompt}]}]},
            "parameters": {**params, "n": 1},
        }
        assert product_payloads == {name: [expected_payload, expected_payload]
                                    for name in ("studio", "atelier", "playground")}
        records = completed_records(client, runtime)
        output_refs = []
        output_hashes = []
        for product, members in records.items():
            assert len(members) == 2, product
            for record in members:
                assert record["phase"] == "completed", product
                assert record["intent"]["intent_contract_version"] == VERSION, product
                assert record["intent"]["model_id"] == f"qwen/{expected_model}#image", product
                assert record["intent"]["prompt"] == prompt, product
                assert record["intent"]["params"] == params, product
                assert record["artifact"] == record["prepared_artifact"], product
                output = runtime.data_dir / record["output_ref"]
                probe = probe_image_file(output)
                assert (probe.codec_name, probe.width, probe.height) == ("png", 16, 16), product
                # Core normalizes the downloaded PNG before publishing it.
                actual_hash = hashlib.sha256(output.read_bytes()).hexdigest()
                assert record["artifact"]["sha256"] == actual_hash, product
                output_hashes.append(actual_hash)
                output_refs.append(record["output_ref"])
        assert len(set(output_refs)) == 6
        assert len(set(output_hashes)) == 1
        assert app.state.atelier_storage.get_project("atelier-equivalence").nodes[0].media_urls == []
        # An empty Studio reference sheet selects its first result; Atelier
        # still requires explicit adoption. Shared execution does not merge
        # these deliberately different product choice rules.
        assert runtime.scripts["p"].characters[0].reference_sheet.selected_image_id == records["studio"][0]["attempt_id"]

        # Each product owns its receipt; losing current credentials cannot turn
        # one replay into a newly normalized or newly paid request.
        app.state.runtime_environment.update({}, remove_keys=("DASHSCOPE_API_KEY",), persist=lambda *_: None)
        for product, path, body in requests:
            replay = client.post(path, json=body)
            assert replay.status_code == 200 and replay.json()["replayed"], (product, replay.text)
        assert app.state.atelier_generation_dispatcher.wait_for_idle(5)
        assert len(transport["calls"]) == 12
        assert transport["queue"] == []
