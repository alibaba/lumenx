from src.models.seedance import SeedanceModel


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = str(payload)

    def json(self):
        return self._payload


def test_submit_task_includes_workflow_mapping(monkeypatch):
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        captured["timeout"] = timeout
        return FakeResponse({"id": "seedance-task-1"})

    monkeypatch.setattr("src.models.seedance.get_provider_base_url", lambda provider: "https://ark.example")
    monkeypatch.setattr("src.models.seedance.requests.post", fake_post)

    model = SeedanceModel({"api_key": "ark-test"})
    monkeypatch.setattr(
        model,
        "_resolve_vendor_image_input",
        lambda **kwargs: "https://example.com/input.png",
    )
    monkeypatch.setattr(
        model,
        "_resolve_vendor_video_inputs",
        lambda **kwargs: ["https://example.com/source.mp4"],
    )
    monkeypatch.setattr(
        model,
        "_resolve_vendor_audio_input",
        lambda **kwargs: "https://example.com/guide.wav",
    )

    task_id = model._submit_task(
        prompt="replace the umbrella with a neon parasol",
        model_name="doubao-seedance-2-0-260128",
        img_url="https://example.com/input.png",
        img_path=None,
        duration=5,
        resolution="480p",
        ratio="9:16",
        seed=7,
        generate_audio=False,
        watermark=True,
        camera_fixed=True,
        reference_audio_url="https://example.com/guide.wav",
        reference_video_urls=["https://example.com/source.mp4"],
        reference_mode="combo",
        workflow="edit",
        workflow_mode="object_edit",
    )

    assert task_id == "seedance-task-1"
    assert captured["url"] == "https://ark.example/contents/generations/tasks"
    assert captured["timeout"] == 30
    assert captured["headers"]["Authorization"] == "Bearer ark-test"
    assert captured["json"]["reference_mode"] == "combo"
    assert captured["json"]["workflow"] == "edit"
    assert captured["json"]["workflow_mode"] == "object_edit"
    assert captured["json"]["content"] == [
        {"type": "text", "text": "replace the umbrella with a neon parasol"},
        {
            "type": "image_url",
            "image_url": {"url": "https://example.com/input.png"},
        },
        {
            "type": "video_url",
            "video_url": {"url": "https://example.com/source.mp4"},
        },
        {
            "type": "audio_url",
            "audio_url": {"url": "https://example.com/guide.wav"},
        },
    ]


def test_submit_task_uses_reference_mode_to_filter_media(monkeypatch):
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["json"] = json
        return FakeResponse({"id": "seedance-task-2"})

    monkeypatch.setattr("src.models.seedance.get_provider_base_url", lambda provider: "https://ark.example")
    monkeypatch.setattr("src.models.seedance.requests.post", fake_post)

    model = SeedanceModel({"api_key": "ark-test"})
    monkeypatch.setattr(
        model,
        "_resolve_vendor_image_input",
        lambda **kwargs: "https://example.com/ignored.png",
    )
    monkeypatch.setattr(
        model,
        "_resolve_vendor_video_inputs",
        lambda **kwargs: ["https://example.com/source.mp4"],
    )
    monkeypatch.setattr(
        model,
        "_resolve_vendor_audio_input",
        lambda **kwargs: "https://example.com/ignored.wav",
    )

    model._submit_task(
        prompt="continue the action",
        model_name="doubao-seedance-2-0-260128",
        img_url="https://example.com/ignored.png",
        img_path=None,
        duration=5,
        resolution="720p",
        ratio="16:9",
        seed=None,
        generate_audio=True,
        watermark=False,
        camera_fixed=None,
        reference_audio_url="https://example.com/ignored.wav",
        reference_video_urls=["https://example.com/source.mp4"],
        reference_mode="video",
        workflow="standard",
        workflow_mode=None,
    )

    assert captured["json"]["reference_mode"] == "video"
    assert captured["json"]["workflow"] == "standard"
    assert "workflow_mode" not in captured["json"]
    assert captured["json"]["content"] == [
        {"type": "text", "text": "continue the action"},
        {
            "type": "video_url",
            "video_url": {"url": "https://example.com/source.mp4"},
        },
    ]
