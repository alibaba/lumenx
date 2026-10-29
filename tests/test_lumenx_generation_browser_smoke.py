"""Harness-level tests for the isolated generation browser smoke launcher."""

from __future__ import annotations

import hashlib
import importlib.util
import socket
import subprocess
import time
import wave
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.test_atelier_generation_app import seed

BASE = "/atelier/projects/p/agent/loops"
REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "docs/agents/tools/lumenx_generation_browser_smoke.py"


def load_smoke_module():
    spec = importlib.util.spec_from_file_location("lumenx_generation_browser_smoke", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_static_root(tmp_path: Path) -> Path:
    static_root = tmp_path / "static"
    static_root.mkdir(parents=True, exist_ok=True)
    (static_root / "index.html").write_text("<!doctype html><html><body>smoke</body></html>", encoding="utf-8")
    return static_root




def write_real_wan3_av_fixtures(root: Path) -> tuple[Path, Path]:
    from src.utils import media_probe

    uploads = root / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    video = uploads / "ref.mp4"
    audio = uploads / "ref.wav"
    ffmpeg = media_probe.get_ffmpeg_path() or "ffmpeg"
    subprocess.run(
        [
            ffmpeg,
            "-v",
            "error",
            "-nostdin",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=320x240:r=24:d=1",
            "-pix_fmt",
            "yuv420p",
            str(video),
        ],
        capture_output=True,
        check=True,
        timeout=20,
    )
    with wave.open(str(audio), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(b"\0\0" * 16000)
    return video, audio


def seed_wan3_r2v_project(app, output_root: Path) -> None:
    from src.apps.atelier.models import AtelierNode, AtelierProject

    app.state.atelier_storage.create_project(
        AtelierProject(
            id="p",
            title="Wan3 R2V smoke",
            created_at=1,
            updated_at=1,
            nodes=[
                AtelierNode(id="source-video", project_id="p", type="video", media_urls=["uploads/ref.mp4"]),
                AtelierNode(id="source-audio", project_id="p", type="audio", media_urls=["uploads/ref.wav"]),
                AtelierNode(
                    id="video",
                    project_id="p",
                    type="video",
                    data={"reference_node_ids": ["source-video", "source-audio"]},
                ),
            ],
        )
    )


def wan3_r2v_request() -> dict:
    return {
        "contract_version": "ordered-media-v1",
        "expected_revision": 0,
        "client_request_id": "wan3-r2v-av-smoke",
        "model_id": "wan/wan3.0-video#video",
        "mode": "r2v",
        "prompt": "Use the managed reference video and audio safely.",
        "media": [
            {"role": "reference_video", "ordinal": 0, "node_id": "source-video", "media_url": "uploads/ref.mp4"},
            {"role": "reference_audio", "ordinal": 0, "node_id": "source-audio", "media_url": "uploads/ref.wav"},
        ],
        "params": {
            "duration": 5,
            "resolution": "1080P",
            "ratio": "16:9",
            "seed": 0,
            "prompt_extend": False,
            "watermark": False,
            "audio": False,
        },
    }


def wait_for_counts(evidence, expected: dict[str, int], timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        counts = evidence.payload["fake_provider"]["counts"]
        if all(counts.get(key) == value for key, value in expected.items()):
            return
        time.sleep(0.02)
    assert evidence.payload["fake_provider"]["counts"] == expected

def evidence_payload(plan: str) -> dict:
    return {
        "fake_provider": {"counts": {"submit": 0, "query": 0, "fetch": 0}, "events": []},
        "fake_model_planner": {
            "enabled": plan != "disabled",
            "plan": plan,
            "counts": {"init": 0, "chat_result": 0},
            "events": [],
        },
        "blocked_sockets": [],
    }


@pytest.mark.parametrize(
    ("plan", "expected_tool", "expected_status"),
    [
        ("read", "canvas.readProject", "completed"),
        ("canvas", "canvas.createVideoNode", "waiting_approval"),
        ("generation", "generation.createVideoCandidates", "waiting_approval"),
    ],
)
def test_generation_browser_smoke_fake_model_plan_runs_real_http_claim_settle_chain(tmp_path, monkeypatch, plan, expected_tool, expected_status):
    smoke = load_smoke_module()

    def fake_fixture(self):
        self.provider_root.mkdir(parents=True, exist_ok=True)
        fixture = self.provider_root / "fake-wan-color-3s.mp4"
        fixture.write_bytes(b"fake-wan-fixture")
        return fixture

    monkeypatch.setattr(smoke.FakeWanProvider, "_ensure_fixture", fake_fixture)
    data_root = tmp_path / "data"
    data_root.mkdir()
    evidence = smoke.Evidence(tmp_path / "evidence.json", evidence_payload(plan))

    from src.apps.comic_gen import llm_adapter as llm_adapter_module

    original_adapter = llm_adapter_module.LLMAdapter
    try:
        app = smoke.create_smoke_app(data_root, write_static_root(tmp_path), evidence, fake_model_plan=plan)
        with TestClient(app) as http:
            seed(app, data_root / "output")
            response = http.post(BASE, json={
                "expected_revision": 0,
                "client_turn_id": f"smoke-{plan}",
                "user_message": "Run browser smoke fake model plan.",
                "selected_node_id": "video",
                "planner": "model_adapter",
                "max_iterations": 1,
            })
    finally:
        llm_adapter_module.LLMAdapter = original_adapter

    assert response.status_code == 200, response.text
    ack = response.json()
    assert ack["turn"]["status"] == expected_status
    assert ack["turn"]["loop_start_context"]["planner"] == "model_adapter"
    assert ack["turn"]["planner_invocations"][0]["provider_id"] == "lumenx-smoke-fake-planner"
    assert ack["turn"]["planner_invocations"][0]["status"] == "settled"
    assert ack["turn"]["planner_invocations"][0]["outcome"] == "success"
    assert ack["turn"]["planner_invocations"][0]["usage"] == {
        "prompt_tokens": 7,
        "completion_tokens": 5,
        "total_tokens": 12,
    }
    assert [call["tool_name"] for call in ack["turn"]["tool_calls"]] == [expected_tool]
    model_counts = evidence.payload["fake_model_planner"]["counts"]
    assert model_counts["init"] >= 1
    assert model_counts["chat_result"] == 1
    assert evidence.payload["fake_model_planner"]["events"][-1]["plan"] == plan
    assert evidence.payload["fake_provider"]["counts"] == {"submit": 0, "query": 0, "fetch": 0}
    assert "fixture-loop-secret" not in response.text


def test_generation_browser_smoke_fake_generation_arguments_follow_selected_node_context():
    smoke = load_smoke_module()
    content = smoke.build_fake_model_plan("generation", [{"role": "user", "content": '{"selected_node_snapshot":{"id":"clip","reference_node_ids":["img"]},"nodes":[{"id":"img","media_urls":["uploads/seed.png"]}]}'}])

    assert '"tool_name": "generation.createVideoCandidates"' in content
    assert '"node_id": "clip"' in content
    assert '"media_url": "uploads/seed.png"' in content


@pytest.fixture
def smoke_with_transport_bytes(monkeypatch):
    """HTTP/ledger tests use bounded bytes; real browser harness creates FFmpeg media."""
    smoke = load_smoke_module()

    def fixture(self):
        self.provider_root.mkdir(parents=True, exist_ok=True)
        path = self.provider_root / "transport.mp4"
        path.write_bytes(b"fake-transport-result")
        return path

    monkeypatch.setattr(smoke.FakeWanProvider, "_ensure_fixture", fixture)
    return smoke


def test_opt_in_wan3_harness_routes_only_known_models_and_original_scopes(tmp_path, smoke_with_transport_bytes):
    from dataclasses import replace

    smoke = smoke_with_transport_bytes
    data_root = tmp_path / "data"
    data_root.mkdir()
    evidence = smoke.Evidence(tmp_path / "evidence.json", evidence_payload("disabled"))
    app = smoke.create_smoke_app(data_root, write_static_root(tmp_path), evidence, fake_wan3=True)
    with TestClient(app):
        selector = app.state.provider_runtime_selector
        classic = selector.for_admission(provider_model_id="wan2.7-i2v", gateway="dashscope").runtime
        workspace = selector.for_admission(provider_model_id="wan3.0-video", gateway="dashscope_workspace").runtime
        assert classic is not None and workspace is not None
        assert workspace.scope.workspace_id == "smoke-workspace"
        assert selector.for_admission(provider_model_id="wan3.0-video-prime", gateway="dashscope_workspace").runtime is workspace
        assert selector.classic_compatibility().runtime is classic
        assert selector.for_scope(classic.scope).runtime is classic
        assert selector.for_scope(workspace.scope).runtime is workspace
        assert selector.for_scope(replace(workspace.scope, workspace_id="other")).runtime is None
        for model, gateway in [
            ("wan3.0-video", "dashscope"),
            ("wan2.7-i2v", "dashscope_workspace"),
            ("unknown", "dashscope_workspace"),
        ]:
            assert selector.for_admission(provider_model_id=model, gateway=gateway).runtime is None
    assert evidence.payload["fake_wan3"] is True
    assert evidence.payload["fake_provider_scopes"]["workspace"] == workspace.scope.to_dict()


def test_default_harness_does_not_enable_wan3(tmp_path, smoke_with_transport_bytes):
    smoke = smoke_with_transport_bytes
    data_root = tmp_path / "data"
    data_root.mkdir()
    evidence = smoke.Evidence(tmp_path / "evidence.json", evidence_payload("disabled"))
    app = smoke.create_smoke_app(data_root, write_static_root(tmp_path), evidence)
    with TestClient(app):
        result = app.state.provider_runtime_selector.for_admission(
            provider_model_id="wan3.0-video", gateway="dashscope_workspace",
        )
        assert result.runtime is None



def test_fake_wan3_harness_patches_temporary_upload_only_during_lifespan(tmp_path, smoke_with_transport_bytes):
    smoke = smoke_with_transport_bytes
    from src.models import provider_upload

    original = provider_upload.upload_dashscope_temporary_media
    data_root = tmp_path / "data"
    data_root.mkdir()
    evidence = smoke.Evidence(tmp_path / "evidence.json", evidence_payload("disabled"))
    default_app = smoke.create_smoke_app(data_root, write_static_root(tmp_path), evidence)
    with TestClient(default_app):
        assert provider_upload.upload_dashscope_temporary_media is original
    assert provider_upload.upload_dashscope_temporary_media is original

    wan_root = tmp_path / "wan3-data"
    wan_root.mkdir()
    wan_evidence = smoke.Evidence(tmp_path / "wan3-evidence.json", evidence_payload("disabled"))
    wan_app = smoke.create_smoke_app(wan_root, write_static_root(tmp_path / "wan3-static"), wan_evidence, fake_wan3=True)
    with TestClient(wan_app):
        assert provider_upload.upload_dashscope_temporary_media is not original
    assert provider_upload.upload_dashscope_temporary_media is original


def test_fake_wan3_harness_blocks_nonloopback_socket_without_relaxing_guard(tmp_path):
    smoke = load_smoke_module()
    evidence = smoke.Evidence(tmp_path / "evidence.json", evidence_payload("disabled"))
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    try:
        smoke.install_socket_guard(evidence)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            with pytest.raises(RuntimeError, match="blocks outbound network"):
                sock.connect(("203.0.113.10", 443))
    finally:
        socket.socket.connect = original_connect
        socket.socket.connect_ex = original_connect_ex
    assert evidence.payload["blocked_sockets"]




def test_fake_wan3_harness_rejects_overlapping_upload_patch_lifetimes(tmp_path, smoke_with_transport_bytes):
    smoke = smoke_with_transport_bytes
    from src.models import provider_upload

    original = provider_upload.upload_dashscope_temporary_media
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    first = smoke.create_smoke_app(first_root, write_static_root(tmp_path / "first-static"), smoke.Evidence(tmp_path / "first.json", evidence_payload("disabled")), fake_wan3=True)
    second = smoke.create_smoke_app(second_root, write_static_root(tmp_path / "second-static"), smoke.Evidence(tmp_path / "second.json", evidence_payload("disabled")), fake_wan3=True)
    with TestClient(first):
        assert provider_upload.upload_dashscope_temporary_media is not original
        with pytest.raises(RuntimeError, match="temporary upload is already active"):
            with TestClient(second):
                pass
        assert provider_upload.upload_dashscope_temporary_media is not original
    assert provider_upload.upload_dashscope_temporary_media is original


def test_fake_wan3_uploader_rejects_wrong_scope_or_role_mime_pair(tmp_path):
    smoke = load_smoke_module()
    from src.models.provider_tasks import ProviderScope, ResolvedCredential
    from src.models.provider_upload import ProviderUploadError
    from src.utils.media_versions import MediaVersion

    data = b"upload-bytes"
    digest = hashlib.sha256(data).hexdigest()
    good_scope = ProviderScope(
        provider="dashscope",
        backend="dashscope",
        gateway="dashscope_workspace",
        base_url=smoke.SMOKE_WORKSPACE_BASE_URL,
        region=smoke.SMOKE_WORKSPACE_REGION,
        workspace_id=smoke.SMOKE_WORKSPACE_ID,
    )
    wrong_scope = ProviderScope(
        provider="dashscope",
        backend="dashscope",
        gateway="dashscope_workspace",
        base_url=smoke.SMOKE_WORKSPACE_BASE_URL,
        region=smoke.SMOKE_WORKSPACE_REGION,
        workspace_id="other",
    )
    uploader = smoke.FakeDashScopeUploader(smoke.Evidence(tmp_path / "evidence.json", evidence_payload("disabled")))
    with pytest.raises(ProviderUploadError, match="scope_invalid"):
        uploader(
            scope=wrong_scope,
            provider_model_id="wan3.0-video",
            credential=ResolvedCredential(wrong_scope, "cred", "secret"),
            version=MediaVersion(digest, len(data), "reference_video", 0, "video/mp4"),
            data=data,
        )
    with pytest.raises(ProviderUploadError, match="media_type_invalid"):
        uploader(
            scope=good_scope,
            provider_model_id="wan3.0-video",
            credential=ResolvedCredential(good_scope, "cred", "secret"),
            version=MediaVersion(digest, len(data), "reference_video", 0, "audio/wav"),
            data=data,
        )


def test_fake_wan3_harness_uploads_managed_r2v_av_before_fake_provider_submission(tmp_path, monkeypatch, smoke_with_transport_bytes):
    smoke = smoke_with_transport_bytes
    from src.models import provider_upload

    def fail_network(*_args, **_kwargs):
        raise provider_upload.requests.RequestException("real temporary upload network was not faked")

    monkeypatch.setattr(provider_upload.requests, "get", fail_network)
    monkeypatch.setattr(provider_upload.requests, "post", fail_network)
    data_root = tmp_path / "data"
    output_root = data_root / "output"
    data_root.mkdir()
    video_path, audio_path = write_real_wan3_av_fixtures(output_root)
    expected_hashes = {
        "reference_video": hashlib.sha256(video_path.read_bytes()).hexdigest(),
        "reference_audio": hashlib.sha256(audio_path.read_bytes()).hexdigest(),
    }
    evidence = smoke.Evidence(tmp_path / "evidence.json", evidence_payload("disabled"))
    app = smoke.create_smoke_app(data_root, write_static_root(tmp_path), evidence, fake_wan3=True)
    with TestClient(app) as client:
        seed_wan3_r2v_project(app, output_root)
        created = client.post("/atelier/projects/p/nodes/video/video_candidates", json=wan3_r2v_request())
        assert created.status_code == 200, created.text
        ack = created.json()
        receipt = ack["receipt"]
        attempt_id = ack["attempt"]["attempt_id"]
        assert app.state.atelier_generation_dispatcher.wait_for_idle(3), "queued submit did not become idle"
        for _ in range(2):
            current = client.get(f"/atelier/projects/p/generation/attempts/{attempt_id}")
            assert current.status_code == 200, current.text
            attempt = current.json()["attempt"]
            if attempt["status"] == "completed":
                break
            advanced = client.post(
                f"/atelier/projects/p/generation/attempts/{attempt_id}/continue",
                json={
                    "candidate_id": attempt["candidate_id"],
                    "expected_attempt_revision": attempt["attempt_revision"],
                    "execution_fingerprint": attempt["provider_execution"]["fingerprint"],
                },
            )
            assert advanced.status_code == 200, advanced.text
        assert evidence.payload["fake_provider"]["counts"] == {"submit": 1, "query": 1, "fetch": 1}
        readback = client.get("/atelier/projects/p/generation/requests/wan3-r2v-av-smoke")
        assert readback.status_code == 200, readback.text
        assert readback.json()["receipt"] == receipt
        replay = client.post("/atelier/projects/p/nodes/video/video_candidates", json=wan3_r2v_request())
        assert replay.status_code == 200 and replay.json()["replayed"] is True
        assert replay.json()["receipt"] == receipt
        assert evidence.payload["fake_provider"]["counts"] == {"submit": 1, "query": 1, "fetch": 1}

    upload = evidence.payload.get("fake_upload", {})
    assert upload.get("counts") == {"upload": 2}
    events = upload.get("events", [])
    assert [event["role"] for event in events] == ["reference_video", "reference_audio"]
    assert [event["ordinal"] for event in events] == [0, 0]
    assert {event["sha256"] for event in events} == set(expected_hashes.values())
    assert all(event["scope"]["gateway"] == "dashscope_workspace" for event in events)
    assert all(event["scope"]["workspace_id"] == "smoke-workspace" for event in events)
    assert all(event["provider_model_id"] == "wan3.0-video" for event in events)
    assert len({event["credential_ref"] for event in events}) == 1
    assert len({event["key"] for event in events}) == 2
    submitted = [event for event in evidence.payload["fake_provider"]["events"] if event["kind"] == "submit"]
    assert len(submitted) == 1
    assert all(event["credential_ref"] == submitted[0]["request_summary"]["credential_ref"] for event in events)
    media = submitted[0]["request_summary"]["media"]
    assert [item["transport_binding"] for item in media] == ["dashscope_oss_sha256_v1", "dashscope_oss_sha256_v1"]
    assert [item["url"] for item in media] == [f"oss://{event['key']}" for event in events]
    assert all(item["sha256"] == expected_hashes[item["role"]] for item in media)


def test_wan3_smoke_studio_batch_keeps_distinct_handles_and_replay_never_resubmits(tmp_path, monkeypatch, smoke_with_transport_bytes):
    from src.apps.comic_gen.models import Script, StoryboardFrame
    from tests.test_studio_ordered_provider_entry import payload

    smoke = smoke_with_transport_bytes
    data_root = tmp_path / "data"
    data_root.mkdir()
    evidence = smoke.Evidence(tmp_path / "evidence.json", evidence_payload("disabled"))
    app = smoke.create_smoke_app(data_root, write_static_root(tmp_path), evidence, fake_wan3=True)
    with TestClient(app) as client:
        pipeline = app.state.pipeline
        pipeline.scripts["p"] = Script(
            id="p", title="smoke", original_text="quiet", created_at=1, updated_at=1,
            frames=[StoryboardFrame(id="frame", scene_id="scene")],
        )
        pipeline._commit_storage("projects.json")
        monkeypatch.setattr(smoke.time, "time", lambda: 1791700000.0)
        url = "/projects/p/provider_video_tasks"
        request = payload(batch_size=3)
        created = client.post(url, json=request)
        assert created.status_code == 200, created.text
        assert evidence.payload["fake_provider"]["counts"]["submit"] == 3
        submitted = [event for event in evidence.payload["fake_provider"]["events"] if event["kind"] == "submit"]
        assert len({event["task_id"] for event in submitted}) == 3
        for event in submitted:
            assert event["request_summary"]["scope"]["gateway"] == "dashscope_workspace"
            assert event["request_summary"]["intent"]["params"]["audio"] is False
            assert event["request_summary"]["intent"]["params"]["seed"] == 0
        replay = client.post(url, json=request)
        assert replay.status_code == 200 and replay.json()["replayed"] is True
        assert evidence.payload["fake_provider"]["counts"]["submit"] == 3
        assert app.state.provider_recovery_dispatcher.is_running
        for task in created.json()["tasks"]:
            record = task["provider_execution"]
            identity = {"expected_attempt_id": record["attempt_id"], "expected_fingerprint": record["fingerprint"]}
            repeated = client.post(url + "/" + task["id"] + "/continue", json=identity)
            assert repeated.status_code == 409
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            tasks = pipeline.read_committed_script("p").video_tasks
            if all(task.status == "completed" for task in tasks):
                break
            time.sleep(0.02)
        assert evidence.payload["fake_provider"]["counts"] == {"submit": 3, "query": 3, "fetch": 3}
        tasks = pipeline.read_committed_script("p").video_tasks
        assert all(task.status == "completed" for task in tasks)
        assert len({task.video_url for task in tasks}) == 3
        dispatcher = app.state.provider_recovery_dispatcher
    assert not dispatcher.is_running


@pytest.mark.parametrize("mode", ["t2v", "i2v", "r2v"])
def test_playground_browser_harness_keeps_video_admission_and_execution_on_fake_transport(
    tmp_path, monkeypatch, smoke_with_transport_bytes, mode,
):
    import base64
    from tests.test_wan3_image_validation import JPEG_240_BASE64
    from src.apps.playground import service as playground_service

    def real_runtime_forbidden(*args, **kwargs):
        raise AssertionError("Harness must not construct a real Playground provider")

    monkeypatch.setattr(playground_service, "build_provider_runtime_for_admission", real_runtime_forbidden)
    smoke = smoke_with_transport_bytes
    data_root = tmp_path / "data"
    data_root.mkdir()
    evidence = smoke.Evidence(tmp_path / "evidence.json", evidence_payload("disabled"))
    app = smoke.create_smoke_app(data_root, write_static_root(tmp_path), evidence)
    with TestClient(app) as http:
        payload = {
            "client_request_id": f"playground-smoke-{mode}",
            "mode": mode,
            "model_id": f"wan2.7-{mode}",
            "prompt": "Synthetic local video",
            "parameters": {"duration": 5, "resolution": "1080p" if mode == "i2v" else "1080P", "seed": 0, "watermark": False},
            "batch_size": 2,
        }
        if mode in {"i2v", "r2v"}:
            frame = data_root / "output" / "uploads" / "frame.jpg"
            frame.parent.mkdir(parents=True, exist_ok=True)
            frame.write_bytes(base64.b64decode(JPEG_240_BASE64))
            payload["input_media"] = ["uploads/frame.jpg"]
            if mode == "r2v":
                payload["input_media"].append("uploads/frame.jpg")
                payload["parameters"]["aspect_ratio"] = "3:4"
        response = http.post("/playground/generate", json=payload)
        assert response.status_code == 200, response.text
        deadline = time.monotonic() + 5
        while True:
            receipt = http.get(f"/playground/requests/{payload['client_request_id']}").json()
            generation = receipt["generation"]
            if generation["status"] not in {"pending", "processing"} or time.monotonic() >= deadline:
                break
            time.sleep(0.02)
        assert generation["status"] == "completed", generation
        assert len(generation["outputs"]) == 2
        assert all(item["status"] == "completed" for item in generation["attempts"])
        for output in generation["outputs"]:
            media = data_root / "output" / output["media_path"]
            assert media.read_bytes() == b"fake-transport-result"
            assert http.get(f"/files/{output['media_path']}").content == b"fake-transport-result"
        assert http.post("/playground/generate", json=payload).json()["replayed"] is True
        assert app.state.playground_video_recovery_dispatcher.wait_for_idle(5)
        assert evidence.payload["fake_provider"]["counts"] == {"submit": 2, "query": 2, "fetch": 2}
        if mode == "r2v":
            submitted = [item for item in evidence.payload["fake_provider"]["events"] if item["kind"] == "submit"]
            for event in submitted:
                request = event["request_summary"]
                assert request["intent"]["params"]["ratio"] == "3:4"
                assert [item["role"] for item in request["media"]] == ["reference_image", "reference_image"]
                assert [item["ordinal"] for item in request["media"]] == [0, 1]


def test_playground_image_http_batch_completes_with_video_dispatcher_running(tmp_path, monkeypatch, smoke_with_transport_bytes):
    """Hold Qwen image output in the factory while video scans both members."""
    from concurrent.futures import ThreadPoolExecutor
    import threading
    from src.models import provider_media_execution as media_core

    smoke = smoke_with_transport_bytes
    data_root = tmp_path / "data"
    data_root.mkdir()
    evidence = smoke.Evidence(tmp_path / "evidence.json", evidence_payload("disabled"))
    app = smoke.create_smoke_app(data_root, write_static_root(tmp_path), evidence, fake_media=True)
    entered = threading.Event()
    release = threading.Event()
    with TestClient(app) as http:
        original_write_image = getattr(media_core.WanxImageModel, "_write_image")

        def blocked_write_image(self, prompt, output, **kwargs):
            entered.set()
            if not release.wait(5):
                raise AssertionError("Image transport was not released")
            return original_write_image(self, prompt, output, **kwargs)

        monkeypatch.setattr(media_core.WanxImageModel, "_write_image", blocked_write_image)
        payload = {
            "client_request_id": "image-with-live-video-dispatcher",
            "mode": "t2i",
            "model_id": "qwen-image-2.0",
            "prompt": "Synthetic image batch with concurrent video recovery",
            "parameters": {"size": "1280*1280", "seed": 0, "watermark": False},
            "batch_size": 2,
        }
        dispatcher = app.state.playground_video_recovery_dispatcher
        assert dispatcher.is_running
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(http.post, "/playground/generate", json=payload)
            try:
                assert entered.wait(5), "Real HTTP image execution did not enter transport"
                before = http.get("/playground/requests/" + payload["client_request_id"]).json()["generation"]
                assert [a["provider_execution"]["phase"] for a in before["attempts"]] == ["submitting", "accepted"]
                for _ in range(3):
                    assert dispatcher.run_once() is None
                after = http.get("/playground/requests/" + payload["client_request_id"]).json()["generation"]
                assert after == before
            finally:
                release.set()
            response = pending.result(timeout=10)
        assert response.status_code == 200, response.text
        generation = http.get("/playground/requests/" + payload["client_request_id"]).json()["generation"]
        assert generation["status"] == "completed", generation
        assert len(generation["outputs"]) == 2
        assert [a["status"] for a in generation["attempts"]] == ["completed", "completed"]
        for output in generation["outputs"]:
            assert output["media_type"] == "image"
            assert http.get("/files/" + output["media_path"]).content.startswith(b"\x89PNG")
        assert evidence.payload["fake_provider"]["counts"] == {"submit": 0, "query": 0, "fetch": 0}
        assert len(evidence.payload["fake_media"]["events"]) == 2
    assert not dispatcher.is_running
