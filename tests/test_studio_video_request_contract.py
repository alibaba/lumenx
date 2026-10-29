"""Studio video task HTTP admission must not coerce request intent."""
import json
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from src.apps.comic_gen import api, pipeline as module
from src.apps.comic_gen.models import Script, VideoTask


@dataclass
class RecordingPipeline:
    calls: list[dict[str, Any]] = field(default_factory=list)
    processed: list[tuple[str, str]] = field(default_factory=list)

    def assert_storage_available(self):
        return None

    def create_library_asset(self, *args, **kwargs):  # pragma: no cover - app wiring only
        raise AssertionError("library writer should not be used by video request tests")

    def close(self):
        return None

    @property
    def data_dir(self):
        return None

    def create_video_task(self, **kwargs):
        self.calls.append(kwargs)
        index = len(self.calls)
        task = VideoTask(
            id=f"task-{index}",
            project_id=kwargs["script_id"],
            frame_id=kwargs.get("frame_id"),
            image_url=kwargs["image_url"],
            prompt=kwargs["prompt"],
            duration=kwargs["duration"],
            seed=kwargs.get("seed"),
            resolution=kwargs["resolution"],
            generate_audio=kwargs["generate_audio"],
            audio_url=kwargs.get("audio_url"),
            prompt_extend=kwargs["prompt_extend"],
            negative_prompt=kwargs.get("negative_prompt"),
            model=kwargs["model"],
            shot_type=kwargs["shot_type"],
            generation_mode=kwargs["generation_mode"],
            reference_video_urls=list(kwargs.get("reference_video_urls") or []),
            reference_image_urls=list(kwargs.get("reference_image_urls") or []),
            ratio=kwargs.get("ratio"),
            watermark=kwargs.get("watermark"),
            mode=kwargs.get("mode"),
            sound=kwargs.get("sound"),
            cfg_scale=kwargs.get("cfg_scale"),
            vidu_audio=kwargs.get("vidu_audio"),
            movement_amplitude=kwargs.get("movement_amplitude"),
            workbench_tab=kwargs.get("workbench_tab"),
        )
        script = Script(
            id=kwargs["script_id"],
            title="Project",
            original_text="text",
            video_tasks=[task],
            created_at=1,
            updated_at=1,
        )
        return script, task.id

    def process_video_task(self, script_id: str, task_id: str):
        self.processed.append((script_id, task_id))


@pytest.fixture
def runtime(tmp_path):
    pipeline = RecordingPipeline()
    app = api.create_app(data_dir=tmp_path / "data", load_config=False, pipeline_instance=pipeline, warmup_demucs=False)
    with TestClient(app) as client:
        yield pipeline, client


def post_payload(client: TestClient, payload: dict[str, Any], path: str = "/projects/p/video_tasks"):
    return client.post(
        path,
        content=json.dumps(payload, allow_nan=True),
        headers={"Content-Type": "application/json"},
    )


def valid_payload(**overrides):
    payload = {
        "image_url": "uploads/source.png",
        "prompt": "make it move",
        "duration": 5,
        "seed": 0,
        "resolution": "720p",
        "generate_audio": False,
        "audio_url": None,
        "prompt_extend": False,
        "negative_prompt": None,
        "batch_size": 1,
        "model": "wan2.7-i2v",
        "shot_type": "single",
        "generation_mode": "i2v",
        "reference_video_urls": ["video/ref.mp4"],
        "mode": "std",
        "sound": "on",
        "cfg_scale": 0.5,
        "vidu_audio": False,
        "movement_amplitude": "auto",
        "reference_image_urls": ["uploads/ref.png"],
        "ratio": "16:9",
        "watermark": False,
        "workbench_tab": "t2i_i2v",
    }
    payload.update(overrides)
    return payload


@pytest.mark.parametrize(
    "field,value",
    [
        ("duration", True),
        ("duration", "5"),
        ("seed", False),
        ("seed", "0"),
        ("batch_size", True),
        ("batch_size", "1"),
        ("generate_audio", 0),
        ("generate_audio", "false"),
        ("prompt_extend", 1),
        ("prompt_extend", "false"),
        ("vidu_audio", 0),
        ("vidu_audio", "false"),
        ("watermark", 0),
        ("watermark", "false"),
        ("cfg_scale", True),
        ("cfg_scale", "0.5"),
        ("cfg_scale", float("nan")),
        ("cfg_scale", float("inf")),
    ],
)
def test_video_task_request_rejects_scalar_coercion_before_admission(runtime, field, value):
    pipeline, client = runtime

    response = post_payload(client, valid_payload(**{field: value}))

    assert response.status_code == 422, response.text
    assert pipeline.calls == []
    assert pipeline.processed == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("image_url", 123),
        ("prompt", ["move"]),
        ("model", 123),
        ("sound", True),
        ("reference_video_urls", ["ok.mp4", 3]),
        ("reference_image_urls", [False]),
        ("generation_mode", "t2v"),
        ("workbench_tab", "assembly"),
        ("batch_size", 0),
        ("batch_size", 7),
    ],
)
def test_video_task_request_rejects_shape_and_enum_mismatches_before_admission(runtime, field, value):
    pipeline, client = runtime

    response = post_payload(client, valid_payload(**{field: value}))

    assert response.status_code == 422, response.text
    assert pipeline.calls == []
    assert pipeline.processed == []


def test_video_task_request_preserves_existing_valid_wire_values(runtime):
    pipeline, client = runtime

    response = post_payload(client, valid_payload(seed=-1, batch_size=2))

    assert response.status_code == 200, response.text
    assert len(response.json()) == 2
    assert len(pipeline.calls) == 2
    assert pipeline.calls[0] == pipeline.calls[1]
    call = pipeline.calls[0]
    assert call["duration"] == 5
    assert call["seed"] == -1
    assert call["generate_audio"] is False
    assert call["prompt_extend"] is False
    assert call["vidu_audio"] is False
    assert call["watermark"] is False
    assert call["audio_url"] is None
    assert call["negative_prompt"] is None
    assert call["sound"] == "on"
    assert call["reference_video_urls"] == ["video/ref.mp4"]
    assert call["reference_image_urls"] == ["uploads/ref.png"]
    assert pipeline.processed == [("p", "task-1"), ("p", "task-2")]


def test_non_video_validation_uses_default_fastapi_error_shape(runtime):
    pipeline, client = runtime

    response = client.patch(
        "/projects/p/frames/f/workbench",
        content=json.dumps({"workbench_generate_count": "bad"}),
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 422, response.text
    assert response.json()["detail"][0]["input"] == "bad"
    assert pipeline.calls == []
    assert pipeline.processed == []


@pytest.mark.parametrize("cfg_scale", [0, 1])
def test_video_task_request_accepts_integer_cfg_scale_endpoints(runtime, cfg_scale):
    pipeline, client = runtime

    response = post_payload(client, valid_payload(cfg_scale=cfg_scale))

    assert response.status_code == 200, response.text
    assert pipeline.calls[0]["cfg_scale"] == float(cfg_scale)


def test_valid_request_reaches_real_pipeline_create_without_provider_processing(tmp_path, monkeypatch):
    for name in ("AssetGenerator", "StoryboardGenerator", "VideoGenerator", "AudioGenerator", "ExportManager"):
        monkeypatch.setattr(module, name, Mock())
    item = module.ComicGenPipeline(data_dir=tmp_path / "data", script_processor=Mock(), warmup_demucs=False)
    script = Script(id="p", title="Project", original_text="text", created_at=1, updated_at=1)
    item.scripts[script.id] = script
    item._save_data()
    processed = []

    def record_process(active_pipeline, script_id, task_id):
        processed.append((active_pipeline is item, script_id, task_id))

    monkeypatch.setattr(api, "process_video_task", record_process)
    app = api.create_app(data_dir=item.data_dir, load_config=False, pipeline_instance=item, warmup_demucs=False)
    try:
        with TestClient(app) as client:
            response = post_payload(client, valid_payload(image_url="http://example.test/source.png", batch_size=1))
            assert response.status_code == 200, response.text
            task_id = response.json()[0]["id"]
            saved = item.read_committed_script("p")
            assert [task.id for task in saved.video_tasks] == [task_id]
            assert saved.video_tasks[0].status == "pending"
            assert processed == [(True, "p", task_id)]
    finally:
        item.close()
