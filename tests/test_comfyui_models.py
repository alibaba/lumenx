import os

import pytest

from src.models.comfyui_image import ComfyUIImageModel
from src.models.comfyui_video import ComfyUIVideoModel


class FakeComfyUI:
    """Fake client replacing ComfyUIClient inside the model adapters."""

    def __init__(self, base_url=None, protocol=None):
        self.protocol = protocol or "zealman"
        self.base_url = base_url or "http://comfyui.test"
        self.submitted = []
        self.uploaded = []

    def submit_workflow_task(self, workflow_id=None, parameters=None, upload_files=None, workflow=None):
        self.submitted.append(
            {
                "workflow_id": workflow_id,
                "parameters": parameters,
                "upload_files": upload_files,
                "workflow": workflow,
            }
        )
        return "task-1"

    def wait_for_task_completion(self, task_id, timeout=600, poll_interval=5):
        return {
            "status": "completed",
            "results": [
                {"type": "image", "raw": {"filename": "gen.png", "subfolder": "", "type": "output"}},
                {"type": "video", "raw": {"filename": "gen.mp4", "subfolder": "", "type": "output"}},
            ],
        }

    def upload_file(self, file_path, file_type="input"):
        self.uploaded.append((file_path, file_type))
        return {"filename": os.path.basename(file_path), "subfolder": "", "type": "input"}

    def download_file(self, filename, output_path, file_type="output", subfolder=""):
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        with open(output_path, "wb") as fh:
            fh.write(b"fake-media")
        return True

    def find_workflow_by_pattern(self, patterns):
        return patterns[0]

    def health_check(self):
        return True


@pytest.fixture
def fake_client(monkeypatch):
    fake = FakeComfyUI()
    monkeypatch.setattr("src.models.comfyui_image.ComfyUIClient", lambda **kw: fake)
    monkeypatch.setattr("src.models.comfyui_video.ComfyUIClient", lambda **kw: fake)
    return fake


class TestComfyUIImageModel:
    def test_generate_t2i(self, fake_client, tmp_path):
        model = ComfyUIImageModel({})
        out = tmp_path / "img.png"

        path, duration = model.generate(
            "a cinematic scene",
            str(out),
            model_name="comfyui-wan2.2-t2i",
            size="1024*1024",
        )

        assert path == str(out)
        assert os.path.exists(out)
        assert duration >= 0
        submitted = fake_client.submitted[-1]
        assert submitted["workflow_id"] == "C16-短剧文生图专用-支持场景-角色"
        assert submitted["parameters"]["size"] == "1024*1024"

    def test_generate_i2i_uploads_reference(self, fake_client, tmp_path):
        model = ComfyUIImageModel({})
        ref = tmp_path / "ref.png"
        ref.write_bytes(b"ref")
        out = tmp_path / "img2.png"

        model.generate(
            "same character",
            str(out),
            ref_image_path=str(ref),
            model_name="comfyui-wan2.2-i2i",
        )

        submitted = fake_client.submitted[-1]
        assert submitted["workflow_id"] == "B13-千问角色一键多角度_multiple_character_angles-v1.0"
        assert "reference_image_0" in submitted["upload_files"]
        assert submitted["upload_files"]["reference_image_0"] == str(ref)


class TestComfyUIVideoModel:
    def test_generate_i2v(self, fake_client, tmp_path):
        model = ComfyUIVideoModel({})
        out = tmp_path / "clip.mp4"
        image = tmp_path / "frame.png"
        image.write_bytes(b"frame")

        path, duration = model.generate(
            "camera pans left",
            str(out),
            img_path=str(image),
            model_name="comfyui-wan2.2-i2v",
            duration=5,
            seed=123,
        )

        assert path == str(out)
        assert os.path.exists(out)
        submitted = fake_client.submitted[-1]
        assert submitted["workflow_id"] == "G03-图生视频-Wan2.2SmoothMix"
        assert submitted["parameters"]["prompt"] == "camera pans left"
        assert submitted["parameters"]["duration"] == 5
        assert submitted["parameters"]["seed"] == 123

    def test_generate_r2v_uses_reference_video_workflow(self, fake_client, tmp_path):
        model = ComfyUIVideoModel({})
        out = tmp_path / "r2v.mp4"
        image = tmp_path / "frame.png"
        image.write_bytes(b"frame")
        ref_video = tmp_path / "ref.mp4"
        ref_video.write_bytes(b"ref-video")

        model.generate(
            "mirror the reference motion",
            str(out),
            img_path=str(image),
            model_name="comfyui-wan2.2-r2v",
            generation_mode="r2v",
            ref_video_urls=[str(ref_video)],
        )

        submitted = fake_client.submitted[-1]
        assert submitted["workflow_id"] == "P02-动作迁移-Wan2.2Animate角色迁移"
        assert submitted["parameters"]["reference_videos"] == ["ref.mp4"]

    def test_ltx_workflow_override(self, fake_client, tmp_path):
        model = ComfyUIVideoModel({})
        out = tmp_path / "ltx.mp4"
        image = tmp_path / "frame.png"
        image.write_bytes(b"frame")

        model.generate(
            "fast clip",
            str(out),
            img_path=str(image),
            model_name="comfyui-ltx2.3-i2v",
        )

        assert fake_client.submitted[-1]["workflow_id"] == "H17-文图生视频-LTX2.3全面优化版"
