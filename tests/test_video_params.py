"""Tests for model-adaptive video parameters feature.

Covers:
- VideoTask model new fields (models.py)
- CreateVideoTaskRequest new fields (models.py, re-exported by api.py)
- Pipeline routing of new params to Kling/Vidu adapters
- Hermeticity: importing/constructing the request model never touches
  the network or drags in the FastAPI app graph (regression guard for
  the T-B3b cold-import hang).
"""
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError


# ── models.py: VideoTask 新字段 ──────────────────────────────────────────

class TestVideoTaskModel:
    """Verify that VideoTask accepts the new Kling/Vidu fields."""

    def _make_task(self, **overrides):
        from src.apps.comic_gen.models import VideoTask
        defaults = dict(
            id="t-1",
            project_id="p-1",
            image_url="https://example.com/img.png",
            prompt="A cinematic shot",
        )
        defaults.update(overrides)
        return VideoTask(**defaults)

    def test_default_new_fields_are_none(self):
        task = self._make_task()
        assert task.mode is None
        assert task.sound is None
        assert task.cfg_scale is None
        assert task.vidu_audio is None
        assert task.movement_amplitude is None

    def test_kling_fields(self):
        task = self._make_task(mode="pro", sound="on", cfg_scale=0.7)
        assert task.mode == "pro"
        assert task.sound == "on"
        assert task.cfg_scale == pytest.approx(0.7)

    def test_vidu_fields(self):
        task = self._make_task(vidu_audio=True, movement_amplitude="large")
        assert task.vidu_audio is True
        assert task.movement_amplitude == "large"

    def test_all_fields_together(self):
        task = self._make_task(
            mode="std", sound="off", cfg_scale=0.3,
            vidu_audio=False, movement_amplitude="small",
        )
        assert task.mode == "std"
        assert task.sound == "off"
        assert task.cfg_scale == pytest.approx(0.3)
        assert task.vidu_audio is False
        assert task.movement_amplitude == "small"

    def test_backwards_compatible_without_new_fields(self):
        """Existing code that doesn't pass new fields should still work."""
        task = self._make_task(
            duration=10, seed=42, resolution="1080p",
            generate_audio=True, prompt_extend=False,
            negative_prompt="blurry", model="wan2.6-i2v",
            shot_type="multi", generation_mode="i2v",
        )
        assert task.duration == 10
        assert task.model == "wan2.6-i2v"
        # New fields default to None
        assert task.mode is None
        assert task.vidu_audio is None


# ── models.py: CreateVideoTaskRequest 新字段 ─────────────────────────────

class TestCreateVideoTaskRequest:
    """Verify the API request model accepts new params."""

    def _make_request(self, **overrides):
        # Import from the light models module — NOT api.py. Importing the
        # FastAPI app here was the root cause of the T-B3b cold-import hang
        # (whole pipeline/tts/oss graph pulled in just to build a DTO).
        from src.apps.comic_gen.models import CreateVideoTaskRequest
        defaults = dict(
            image_url="https://example.com/img.png",
            prompt="test prompt",
        )
        defaults.update(overrides)
        return CreateVideoTaskRequest(**defaults)

    def test_defaults(self):
        req = self._make_request()
        assert req.mode is None
        assert req.sound is None
        assert req.cfg_scale is None
        assert req.vidu_audio is None
        assert req.movement_amplitude is None

    def test_kling_params(self):
        req = self._make_request(mode="pro", sound="on", cfg_scale=0.8)
        assert req.mode == "pro"
        assert req.sound == "on"
        assert req.cfg_scale == pytest.approx(0.8)

    def test_vidu_params(self):
        req = self._make_request(vidu_audio=False, movement_amplitude="medium")
        assert req.vidu_audio is False
        assert req.movement_amplitude == "medium"


# ── 防回归：请求模型必须离线可构造、离线可导入（T-B3b 挂死根因守卫）─────

class TestHermeticRequestModel:
    """The request-model path must never touch the network or the app graph.

    History: test_defaults lazily imported src.apps.comic_gen.api (the full
    FastAPI app, ~300 modules with import-time side effects). On a cold FS
    cache / offline box that import stalled for tens of seconds and looked
    like a hang. Guards below keep the construct path hermetic for good.
    """

    def test_construct_makes_no_network_calls(self, monkeypatch):
        """构造 CreateVideoTaskRequest 时禁止任何网络调用（构造层零 IO）。"""
        import socket

        def _boom(*args, **kwargs):
            raise AssertionError(
                f"network attempted during request-model construction: {args} {kwargs}"
            )

        monkeypatch.setattr(socket, "socket", _boom)
        monkeypatch.setattr(socket, "create_connection", _boom)
        monkeypatch.setattr(socket, "getaddrinfo", _boom)

        from src.apps.comic_gen.models import CreateVideoTaskRequest
        req = CreateVideoTaskRequest(image_url="https://x/img.png", prompt="p")
        assert req.model == "wan2.6-i2v"

    def test_models_import_is_offline_and_light(self):
        """子进程先屏蔽 socket 再 import models 并构造请求：
        1) 全程零网络调用；2) 不拖入 fastapi 应用图。subprocess.run 带
        timeout——未来若再挂死，这里失败并给出栈，而不是卡死整个套件。"""
        repo_root = Path(__file__).resolve().parents[1]
        child_code = (
            "import socket\n"
            "class _Blocked(socket.socket):\n"
            "    def __init__(self, *a, **k):\n"
            "        raise AssertionError('network attempted during import/construct')\n"
            "socket.socket = _Blocked\n"
            "def _boom(*a, **k):\n"
            "    raise AssertionError('network attempted during import/construct')\n"
            "socket.create_connection = _boom\n"
            "socket.getaddrinfo = _boom\n"
            "from src.apps.comic_gen.models import CreateVideoTaskRequest\n"
            "req = CreateVideoTaskRequest(image_url='https://x/i.png', prompt='p')\n"
            "assert req.model == 'wan2.6-i2v'\n"
            "import sys\n"
            "heavy = [m for m in ('fastapi', 'aiohttp', 'torch', 'dashscope') if m in sys.modules]\n"
            "print('HERMETIC_OK heavy=' + ','.join(heavy))\n"
        )
        proc = subprocess.run(
            [sys.executable, "-c", child_code],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=60,  # a real hang must fail the test, not freeze the suite
        )
        assert proc.returncode == 0, f"child failed:\n{proc.stdout}\n{proc.stderr}"
        marker = next(
            (ln for ln in proc.stdout.splitlines() if ln.startswith("HERMETIC_OK")),
            "",
        )
        assert marker, f"child did not report HERMETIC_OK:\n{proc.stdout}\n{proc.stderr}"
        heavy_part = marker.split("heavy=", 1)[1].strip()
        assert heavy_part == "", f"light import violated, dragged in: {heavy_part}"

    def test_api_reexport_compat(self):
        """api.py 仍可 `from ...api import CreateVideoTaskRequest`，且与 models 同一对象。"""
        from src.apps.comic_gen.api import CreateVideoTaskRequest as FromApi
        from src.apps.comic_gen.models import CreateVideoTaskRequest as FromModels
        assert FromApi is FromModels


# ── kling.py: generate() 接受并传入 sound / cfg_scale ───────────────────

class TestKlingModelParams:
    """Verify Kling adapter correctly includes new params in the request body."""

    def test_sound_and_cfg_scale_in_body(self, monkeypatch):
        """2.0 请求体语义: sound→settings.audio, cfg_scale→settings.cfg_scale,
        mode(std/pro)→settings.resolution。"""
        from src.models.kling import KlingModel

        model = KlingModel({"api_key": "test-key"})

        captured_body = {}

        class FakeResponse:
            status_code = 200
            def raise_for_status(self): pass
            def json(self):
                return {"code": 0, "data": {"id": "fake-task-id"}}

        def mock_post(url, headers=None, json=None, timeout=None):
            captured_body.update(json or {})
            return FakeResponse()

        import requests
        monkeypatch.setattr(requests, "post", mock_post)

        class FakePollResponse:
            status_code = 200
            def raise_for_status(self): pass
            def json(self):
                return {
                    "code": 0,
                    "data": [{
                        "status": "succeeded",
                        "outputs": [{"type": "video", "url": "https://example.com/video.mp4"}],
                    }],
                }

        class FakeVideoContent:
            content = b"fake video bytes"

        def mock_get(url, headers=None, timeout=None):
            if "/tasks" in url:
                return FakePollResponse()
            return FakeVideoContent()

        monkeypatch.setattr(requests, "get", mock_get)
        monkeypatch.setattr("time.sleep", lambda x: None)

        import tempfile, os
        with tempfile.TemporaryDirectory() as tmpdir:
            out_path = os.path.join(tmpdir, "out.mp4")
            model.generate(
                prompt="test", output_path=out_path,
                img_url="https://example.com/img.png",
                mode="pro", sound="on", cfg_scale=0.6,
            )

        settings = captured_body["settings"]
        assert settings.get("audio") == "on"
        assert settings.get("cfg_scale") == pytest.approx(0.6)
        assert settings.get("resolution") == "1080p"
        # 2.0 三层结构: 顶层不应再有旧版扁平字段
        assert "sound" not in captured_body
        assert "mode" not in captured_body
        assert "cfg_scale" not in captured_body

    def test_sound_omitted_when_none(self, monkeypatch):
        """2.0 语义: 不传 sound/cfg_scale 时, settings 无 cfg_scale,
        audio 落到显式默认 off, 顶层无旧版扁平字段。"""
        from src.models.kling import KlingModel

        model = KlingModel({"api_key": "test-key"})

        captured_body = {}

        class FakeResponse:
            status_code = 200
            def raise_for_status(self): pass
            def json(self):
                return {"code": 0, "data": {"id": "t1"}}

        class FakePoll:
            status_code = 200
            def raise_for_status(self): pass
            def json(self):
                return {"code": 0, "data": [{
                    "status": "succeeded",
                    "outputs": [{"type": "video", "url": "http://x.mp4"}],
                }]}

        class FakeDL:
            content = b"bytes"

        import requests
        monkeypatch.setattr(requests, "post", lambda *a, **kw: (captured_body.update(kw.get("json", {})), FakeResponse())[1])
        monkeypatch.setattr(requests, "get", lambda *a, **kw: FakePoll() if "/tasks" in a[0] else FakeDL())
        monkeypatch.setattr("time.sleep", lambda x: None)

        import tempfile, os
        with tempfile.TemporaryDirectory() as tmpdir:
            model.generate(
                prompt="test", output_path=os.path.join(tmpdir, "o.mp4"),
                img_url="https://example.com/img.png",
                # 不传 sound / cfg_scale
            )

        assert "sound" not in captured_body
        assert "cfg_scale" not in captured_body
        settings = captured_body["settings"]
        assert "cfg_scale" not in settings
        assert settings["audio"] == "off"


# ── vidu.py: generate() 透传 audio / movement_amplitude ─────────────────

class TestViduModelParams:
    """Verify Vidu adapter passes audio and movement_amplitude into the body."""

    def test_audio_and_movement_in_body(self, monkeypatch):
        from src.models.vidu import ViduModel

        model = ViduModel({"api_key": "test_key"})

        captured_body = {}

        class FakePostResp:
            status_code = 200
            def json(self):
                return {"task_id": "vidu-task-1"}

        class FakePollResp:
            status_code = 200
            def json(self):
                return {"state": "success", "creations": [{"url": "https://example.com/v.mp4"}]}

        class FakeDL:
            content = b"video"

        import requests
        def mock_post(url, headers=None, json=None, timeout=None):
            captured_body.update(json or {})
            return FakePostResp()

        call_idx = {"n": 0}
        def mock_get(url, headers=None, timeout=None):
            call_idx["n"] += 1
            if "tasks" in url:
                return FakePollResp()
            return FakeDL()

        monkeypatch.setattr(requests, "post", mock_post)
        monkeypatch.setattr(requests, "get", mock_get)
        monkeypatch.setattr("time.sleep", lambda x: None)

        import tempfile, os
        with tempfile.TemporaryDirectory() as tmpdir:
            model.generate(
                prompt="test vidu",
                output_path=os.path.join(tmpdir, "v.mp4"),
                img_url="https://example.com/img.png",
                audio=False,
                movement_amplitude="large",
                seed=123,
            )

        assert captured_body.get("audio") is False
        assert captured_body.get("movement_amplitude") == "large"
        assert captured_body.get("seed") == 123
