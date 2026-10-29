"""The shared image adapter must preserve explicit frozen input semantics."""

from itertools import permutations

import pytest

from src.models import image as image_module
from src.models.image import WanxImageModel


@pytest.mark.parametrize("ordered", list(permutations(["first.png", "second.png", "third.png"])))
def test_image_reference_deduplication_keeps_first_occurrence_order(monkeypatch, ordered, tmp_path):
    model = WanxImageModel({}, env={"DASHSCOPE_API_KEY": "test-placeholder"})
    calls = []

    def generate(**kwargs):
        calls.append(kwargs)
        return "https://example.invalid/generated.png"

    monkeypatch.setattr(model, "_generate_dashscope_image_http", generate)
    monkeypatch.setattr(model, "_download_image", lambda *args: None)
    model.generate(
        "Ordered subjects", str(tmp_path / "result.png"), model_name="wan2.7-image",
        ref_image_path=ordered[0], ref_image_paths=[*ordered, ordered[1]],
    )
    assert len(calls) == 1
    assert calls[0]["ref_image_paths"] == list(ordered)


@pytest.mark.parametrize("seed", [0, -1, 17, None])
def test_modern_image_http_preserves_explicit_seed_zero(monkeypatch, seed):
    model = WanxImageModel({}, env={"DASHSCOPE_API_KEY": "test-placeholder"})
    requests = []

    class Response:
        status_code = 200
        text = "test response"

        def __init__(self, body):
            self.body = body

        def json(self):
            return self.body

    def post(url, **kwargs):
        requests.append(kwargs["json"])
        return Response({"output": {"task_id": "example-image-task"}})

    monkeypatch.setattr(image_module.requests, "post", post)
    monkeypatch.setattr(image_module.requests, "get", lambda *args, **kwargs: Response({
        "output": {"task_status": "SUCCEEDED", "choices": [
            {"message": {"content": [{"image": "https://example.invalid/generated.png"}]}}
        ]}
    }))
    monkeypatch.setattr(image_module.time, "sleep", lambda _: None)
    result = model._generate_dashscope_image_http(
        "Subject", "qwen-image-plus", seed=seed, prompt_extend=False, watermark=False,
    )
    assert result == "https://example.invalid/generated.png"
    assert len(requests) == 1
    parameters = requests[0]["parameters"]
    if seed is None:
        assert "seed" not in parameters
    else:
        assert parameters["seed"] == seed
    assert parameters["prompt_extend"] is False
    assert parameters["watermark"] is False
