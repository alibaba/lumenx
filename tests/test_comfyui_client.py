import json
import os

import pytest

from src.models.comfyui_client import ComfyUIClient


class FakeResponse:
    def __init__(self, payload=None, status_code=200, content=b""):
        self._payload = payload
        self.status_code = status_code
        self.content = content

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size=8192):
        yield self.content


class FakeSession:
    def __init__(self):
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return self.respond("POST", url, kwargs)

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return self.respond("GET", url, kwargs)

    def respond(self, method, url, kwargs):
        raise NotImplementedError


def _make_client(fake_session, protocol="zealman"):
    client = ComfyUIClient(base_url="http://comfyui.test", protocol=protocol)
    client.session = fake_session
    return client


class TestZealmanProtocol:
    def test_health_check(self):
        session = FakeSession()

        def respond(method, url, kwargs):
            assert url == "http://comfyui.test/api/health"
            return FakeResponse({"success": True})

        session.respond = respond
        client = _make_client(session)
        assert client.health_check() is True

    def test_submit_workflow_task_returns_prompt_id(self):
        session = FakeSession()
        captured = {}

        def respond(method, url, kwargs):
            captured["url"] = url
            captured["payload"] = kwargs.get("json")
            return FakeResponse({"success": True, "prompt_id": "prompt-1"})

        session.respond = respond
        client = _make_client(session)

        task_id = client.submit_workflow_task(
            workflow_id="G03-图生视频-Wan2.2SmoothMix",
            parameters={"49:text": "hello"},
        )

        assert task_id == "prompt-1"
        assert captured["url"].endswith("/api/workflow/generate")
        assert captured["payload"]["workflow_id"] == "G03-图生视频-Wan2.2SmoothMix"
        assert captured["payload"]["input_values"] == {"49:text": "hello"}

    def test_get_task_status_completed(self):
        session = FakeSession()

        def respond(method, url, kwargs):
            assert "prompt_id" in kwargs.get("params", {})
            return FakeResponse(
                {
                    "success": True,
                    "pending": False,
                    "results": [{"type": "image", "url": "/output/x.png", "raw": {"filename": "x.png"}}],
                }
            )

        session.respond = respond
        client = _make_client(session)
        status = client.get_task_status("prompt-1")

        assert status["status"] == "completed"
        assert status["results"][0]["raw"]["filename"] == "x.png"

    def test_upload_file_returns_filename_dict(self, tmp_path):
        local_file = tmp_path / "ref.png"
        local_file.write_bytes(b"png-bytes")

        session = FakeSession()

        def respond(method, url, kwargs):
            assert url == "http://comfyui.test/api/comfy/upload/file"
            return FakeResponse({"success": True, "filename": "ref.png"})

        session.respond = respond
        client = _make_client(session)
        uploaded = client.upload_file(str(local_file))

        assert uploaded == {"filename": "ref.png", "subfolder": "", "type": "input"}

    def test_download_file_writes_bytes(self, tmp_path):
        session = FakeSession()

        def respond(method, url, kwargs):
            assert url == "http://comfyui.test/api/comfy/view"
            return FakeResponse(content=b"video-bytes")

        session.respond = respond
        client = _make_client(session)
        out = tmp_path / "out.mp4"

        assert client.download_file("out.mp4", str(out)) is True
        assert out.read_bytes() == b"video-bytes"


class TestStandardProtocol:
    def test_submit_uses_template_and_prompt_endpoint(self, tmp_path):
        session = FakeSession()
        captured = {}

        def respond(method, url, kwargs):
            captured["url"] = url
            captured["payload"] = kwargs.get("json")
            return FakeResponse({"prompt_id": "std-1"})

        session.respond = respond
        client = _make_client(session, protocol="standard")

        workflow = {
            "3": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": ""},
            },
            "10": {
                "class_type": "KSampler",
                "inputs": {"seed": 0},
            },
        }
        task_id = client.submit_workflow_task(
            workflow=workflow,
            parameters={"3:text": "hello world", "10:seed": 42},
        )

        assert task_id == "std-1"
        assert captured["url"].endswith("/prompt")
        prompt = captured["payload"]["prompt"]
        assert prompt["3"]["inputs"]["text"] == "hello world"
        assert prompt["10"]["inputs"]["seed"] == 42

    def test_get_task_status_from_history(self):
        session = FakeSession()

        def respond(method, url, kwargs):
            return FakeResponse(
                {
                    "std-1": {
                        "status": {"status_str": "success"},
                        "outputs": {
                            "9": {
                                "images": [
                                    {"filename": "gen.png", "subfolder": "", "type": "output"}
                                ]
                            }
                        },
                    }
                }
            )

        session.respond = respond
        client = _make_client(session, protocol="standard")
        status = client.get_task_status("std-1")

        assert status["status"] == "completed"
        assert status["results"][0]["type"] == "image"
        assert status["results"][0]["raw"]["filename"] == "gen.png"

    def test_get_task_status_pending_when_not_in_history(self):
        session = FakeSession()

        def respond(method, url, kwargs):
            return FakeResponse({}, status_code=404)

        session.respond = respond
        client = _make_client(session, protocol="standard")
        assert client.get_task_status("missing")["status"] == "running"

    def test_apply_input_values_dot_and_colon(self):
        workflow = {"5": {"class_type": "X", "inputs": {"text": "old"}}}
        result = ComfyUIClient._apply_input_values(
            workflow, {"5.text": "new", "5:seed": 7}
        )
        assert result["5"]["inputs"]["text"] == "new"
        assert result["5"]["inputs"]["seed"] == 7


class TestWorkflowDiscovery:
    def test_find_workflow_by_pattern(self):
        session = FakeSession()

        def respond(method, url, kwargs):
            return FakeResponse(
                {
                    "success": True,
                    "workflows": [
                        {"id": "G01-图生视频-Wan2.2", "name": "基础图生视频"},
                        {"id": "G03-图生视频-Wan2.2SmoothMix", "name": "SmoothMix"},
                    ],
                }
            )

        session.respond = respond
        client = _make_client(session)
        found = client.find_workflow_by_pattern(["G03"])
        assert found == "G03-图生视频-Wan2.2SmoothMix"
