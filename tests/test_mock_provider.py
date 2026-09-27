"""T-B2 可靠性步骤 4：Mock provider（mock- 前缀路由 + 失败序列注入）。"""

import os

import pytest

from src.models.factory import ModelFactory
from src.models.mock import MockError, MockImageModel, MockModel, VIDEO_MAGIC, IMAGE_MAGIC


class TestFactoryRouting:

    def test_mock_prefix_routes_to_mock_model(self):
        model = ModelFactory.create_model({"model.name": "mock-video"})
        assert isinstance(model, MockModel)

    def test_mock_prefix_any_suffix(self):
        assert isinstance(ModelFactory.create_model({"model.name": "mock-image"}), MockModel)
        assert isinstance(ModelFactory.create_model({"model.name": "mock-anything-else"}), MockModel)

    def test_real_model_names_still_route(self):
        # wanx 分支（既有行为）要求 config['model'] 存在
        assert not isinstance(ModelFactory.create_model({"model.name": "wanx", "model": {}}), MockModel)
        from src.models.kling import KlingModel
        assert isinstance(ModelFactory.create_model({"model.name": "kling", "model": {}}), KlingModel)

    def test_unknown_model_still_raises(self):
        with pytest.raises(ValueError):
            ModelFactory.create_model({"model.name": "nonexistent-model"})


class TestMockVideoModel:

    def test_generate_writes_mock_file(self, tmp_path):
        out = tmp_path / "v.mp4"
        path, elapsed = MockModel({}).generate("一只猫在桥边走", str(out))
        assert path == str(out)
        assert os.path.exists(out)
        assert out.read_bytes().startswith(VIDEO_MAGIC)
        assert elapsed >= 0

    def test_generate_is_offline(self, tmp_path, monkeypatch):
        """mock 不允许触网：requests 一旦被调用立即失败。"""
        import requests
        def _boom(*a, **k):
            raise AssertionError("MockModel must not touch network")
        monkeypatch.setattr(requests, "post", _boom)
        monkeypatch.setattr(requests, "get", _boom)
        out = tmp_path / "v.mp4"
        MockModel({}).generate("p", str(out), img_url="https://example.com/x.png")
        assert out.exists()

    def test_failure_sequence_injection(self, tmp_path):
        model = MockModel({"fail_sequence": ["额度耗尽", "网络抖动"]})
        out = tmp_path / "v.mp4"

        with pytest.raises(MockError) as exc_info:
            model.generate("p1", str(out))
        assert "额度耗尽" in str(exc_info.value)

        with pytest.raises(MockError):
            model.generate("p2", str(out))

        # 序列耗尽后恢复正常
        path, _ = model.generate("p3", str(out))
        assert os.path.exists(out)


class TestMockImageModel:

    def test_generate_writes_mock_image(self, tmp_path):
        out = tmp_path / "img.png"
        path, elapsed = MockImageModel({}).generate("角色设定图", str(out))
        assert path == str(out)
        assert out.read_bytes().startswith(IMAGE_MAGIC)

    def test_failure_injection(self, tmp_path):
        model = MockImageModel({"fail_sequence": ["x"]})
        with pytest.raises(MockError):
            model.generate("p", str(tmp_path / "a.png"))
        path, _ = model.generate("p", str(tmp_path / "b.png"))
        assert os.path.exists(path)


# ---------------------------------------------------------------------------
# pipeline / assets 分发集成
# ---------------------------------------------------------------------------

class TestDispatchIntegration:

    def test_pipeline_video_task_dispatches_to_mock(self, tmp_path, monkeypatch):
        """process_video_task 对 mock- 模型走 MockModel，任务落到 completed。"""
        from unittest.mock import patch
        from src.apps.comic_gen.pipeline import ComicGenPipeline
        from src.apps.comic_gen.models import (
            Script, StoryboardFrame, VideoTask,
        )
        import time as _time

        with patch("src.apps.comic_gen.pipeline.ScriptProcessor"), \
             patch("src.apps.comic_gen.pipeline.AssetGenerator"), \
             patch("src.apps.comic_gen.pipeline.StoryboardGenerator"), \
             patch("src.apps.comic_gen.pipeline.VideoGenerator"), \
             patch("src.apps.comic_gen.pipeline.AudioGenerator"), \
             patch("src.apps.comic_gen.pipeline.ExportManager"):
            pm = ComicGenPipeline()
        pm.data_file = str(tmp_path / "projects.json")
        pm.series_data_file = str(tmp_path / "series.json")
        pm.library_data_file = str(tmp_path / "library_assets.json")
        pm.scripts = {}
        pm.series_store = {}

        now = _time.time()
        frame = StoryboardFrame(id="f1", scene_id="s1", action_description="测试帧")
        script = Script(
            id="p1", title="mock 冒烟", original_text="文本",
            frames=[frame], created_at=now, updated_at=now,
        )
        pm.scripts["p1"] = script

        out_dir = tmp_path / "video_out"
        monkeypatch.setattr(pm, "_download_temp_image", lambda url: None)
        # process_video_task 的输出目录是相对 cwd 的 output/video；改写输出路径
        # 通过直接调用底层：这里不改 cwd，而是构造 task 后 patch 输出目录。
        task = VideoTask(
            id="t1", project_id="p1", frame_id="f1", image_url="",
            prompt="测试", status="pending", model="mock-video",
        )
        script.video_tasks.append(task)

        # 输出路径 logic: os.path.join("output", "video", ...) — chdir 到 tmp
        monkeypatch.chdir(tmp_path)

        pm.process_video_task("p1", "t1")

        assert task.status == "completed"
        assert task.video_url and os.path.isabs(task.video_url) is False or os.path.exists(task.video_url)
        produced = tmp_path / "output" / "video" / "video_t1.mp4"
        assert produced.exists()
        assert produced.read_bytes().startswith(VIDEO_MAGIC)
