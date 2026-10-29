from pathlib import Path

from src.models import mulerouter
from src.models.mulerouter import MuleRouterVideoModel


PRIMARY_URL = "https://cdn.example.com/primary.png"
SECOND_URL = "https://cdn.example.com/second.png"


def _fake_download(_url: str, output_path: str) -> str:
    Path(output_path).write_bytes(b"fake video")
    return output_path


def test_seedance_r2v_http_does_not_duplicate_primary_reference(monkeypatch, tmp_path):
    captured = {}

    def fake_submit(_base_url, api_path, body, *, env=None):
        captured["api_path"] = api_path
        captured["body"] = body
        captured["env"] = env
        return "task-1"

    monkeypatch.setattr(mulerouter, "_submit_task", fake_submit)
    monkeypatch.setattr(mulerouter, "_poll_task", lambda *_args, **_kwargs: {"videos": [{"url": "https://cdn.example.com/out.mp4"}]})
    monkeypatch.setattr(mulerouter, "_download_file", _fake_download)

    model = MuleRouterVideoModel({"output_root": str(tmp_path)}, env={"MULEROUTER_API_KEY": "fake-key"})
    monkeypatch.setattr(model, "_use_cli_backend", lambda: False)

    output_path = tmp_path / "out-http.mp4"
    model.generate(
        prompt="A quiet street at blue hour",
        output_path=str(output_path),
        img_url=PRIMARY_URL,
        generation_mode="r2v",
        ref_image_urls=[PRIMARY_URL, SECOND_URL, PRIMARY_URL],
        duration=5,
        resolution="1080p",
        aspect_ratio="16:9",
        seed=0,
        watermark=False,
    )

    assert output_path.read_bytes() == b"fake video"
    assert captured["api_path"].endswith("/reference-to-video/generation")
    assert captured["body"]["reference_images"] == [PRIMARY_URL, SECOND_URL, PRIMARY_URL]
    assert captured["body"]["seed"] == 0
    assert captured["body"]["watermark"] is False


def test_seedance_r2v_cli_does_not_duplicate_primary_reference(monkeypatch, tmp_path):
    captured = {}

    def fake_run(endpoint, args, timeout=0, *, env=None, authority=None):
        captured["endpoint"] = endpoint
        captured["args"] = args
        captured["timeout"] = timeout
        captured["env"] = env
        captured["authority"] = authority
        return {"videos": [{"url": "https://cdn.example.com/out.mp4"}]}

    monkeypatch.setattr(mulerouter, "_run_mulerun_studio", fake_run)
    monkeypatch.setattr(mulerouter, "_download_file", _fake_download)

    model = MuleRouterVideoModel({"output_root": str(tmp_path)}, env={"MULERUN_TOKEN": "fake-token"})

    output_path = tmp_path / "out-cli.mp4"
    model._generate_via_cli(
        prompt="A quiet street at blue hour",
        output_path=str(output_path),
        img_url=PRIMARY_URL,
        generation_mode="r2v",
        ref_image_urls=[PRIMARY_URL, SECOND_URL, PRIMARY_URL],
        duration=5,
        resolution="1080p",
        seed=0,
        watermark=False,
    )

    assert output_path.read_bytes() == b"fake video"
    assert captured["endpoint"].endswith("/reference-to-video")
    assert captured["args"][captured["args"].index("--image") + 1] == PRIMARY_URL
    assert [
        captured["args"][index + 1].split("=", 1)[1]
        for index, value in enumerate(captured["args"])
        if value == "--extra" and captured["args"][index + 1].startswith("reference_image=")
    ] == [SECOND_URL, PRIMARY_URL]
    assert "--seed" in captured["args"]
    assert captured["args"][captured["args"].index("--seed") + 1] == "0"
