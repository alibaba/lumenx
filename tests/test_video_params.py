"""Tests for model-adaptive video parameters feature.

Covers:
- VideoTask model new fields (models.py)
- CreateVideoTaskRequest new fields (api.py)
- Pipeline routing of new params to Kling/Vidu adapters
"""
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


# ── api.py: CreateVideoTaskRequest 新字段 ────────────────────────────────

class TestCreateVideoTaskRequest:
    """Verify the API request model accepts new params."""

    def _make_request(self, **overrides):
        import sys, importlib
        # 需要直接 import api 模块中的 request model
        from src.apps.comic_gen.api import CreateVideoTaskRequest
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


# ── kling.py: generate() 接受并传入 sound / cfg_scale ───────────────────

class TestKlingModelParams:
    """Verify Kling adapter correctly includes new params in the request body."""

    def test_sound_and_cfg_scale_in_body(self, monkeypatch):
        from src.models.kling import KlingModel

        model = KlingModel({
            "access_key": "test_ak",
            "secret_key": "test_sk",
        })

        captured_body = {}

        class FakeResponse:
            status_code = 200
            def raise_for_status(self): pass
            def json(self):
                return {"code": 0, "data": {"task_id": "fake-task-id"}}

        def mock_post(url, headers=None, json=None, timeout=None):
            captured_body.update(json or {})
            return FakeResponse()

        # 只 mock 提交阶段，让它在 poll 阶段直接报错退出
        import requests
        monkeypatch.setattr(requests, "post", mock_post)

        # generate 会在 poll 阶段 sleep + get，
        # 我们只需要验证提交的 body 包含新字段即可
        class FakePollResponse:
            status_code = 200
            def raise_for_status(self): pass
            def json(self):
                return {
                    "code": 0,
                    "data": {
                        "task_status": "succeed",
                        "task_result": {"videos": [{"url": "https://example.com/video.mp4"}]},
                    },
                }

        class FakeVideoContent:
            content = b"fake video bytes"

        call_count = {"n": 0}
        def mock_get(url, headers=None, timeout=None):
            call_count["n"] += 1
            if "image2video" in url or "text2video" in url:
                return FakePollResponse()
            return FakeVideoContent()

        monkeypatch.setattr(requests, "get", mock_get)
        monkeypatch.setattr(
            "src.models.kling.download_url_to_file",
            lambda url, output_path, timeout=None: Path(output_path).write_bytes(b"video"),
        )
        monkeypatch.setattr("time.sleep", lambda x: None)

        import tempfile, os
        with tempfile.TemporaryDirectory() as tmpdir:
            out_path = os.path.join(tmpdir, "out.mp4")
            model.generate(
                prompt="test", output_path=out_path,
                img_url="https://example.com/img.png",
                mode="pro", sound="on", cfg_scale=0.6,
            )

        assert captured_body.get("sound") == "on"
        assert captured_body.get("cfg_scale") == pytest.approx(0.6)
        assert captured_body.get("mode") == "pro"

    def test_sound_omitted_when_none(self, monkeypatch):
        from src.models.kling import KlingModel

        model = KlingModel({"access_key": "ak", "secret_key": "sk"})

        captured_body = {}

        class FakeResponse:
            status_code = 200
            def raise_for_status(self): pass
            def json(self):
                return {"code": 0, "data": {"task_id": "t1"}}

        class FakePoll:
            status_code = 200
            def raise_for_status(self): pass
            def json(self):
                return {"code": 0, "data": {"task_status": "succeed", "task_result": {"videos": [{"url": "http://x.mp4"}]}}}

        class FakeDL:
            content = b"bytes"

        import requests
        monkeypatch.setattr(requests, "post", lambda *a, **kw: (captured_body.update(kw.get("json", {})), FakeResponse())[1])
        monkeypatch.setattr(requests, "get", lambda *a, **kw: FakePoll() if "image2video" in a[0] else FakeDL())
        monkeypatch.setattr(
            "src.models.kling.download_url_to_file",
            lambda url, output_path, timeout=None: Path(output_path).write_bytes(b"video"),
        )
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
        monkeypatch.setattr(
            "src.models.vidu.download_url_to_file",
            lambda url, output_path, timeout=None: Path(output_path).write_bytes(b"video"),
        )
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
