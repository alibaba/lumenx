import base64
from pathlib import Path

import pytest

from src.models.kling import KlingModel
from src.models.mulerouter import MuleRouterImageModel, MuleRouterVideoModel
from src.models.wanx import WanxModel
from src.utils.media_refs import classify_media_ref, resolve_local_media_path
from src.utils.provider_media import resolve_media_input


PNG_RED_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAAC0lEQVR4nGP4zwAAAgEBAADV"
    "Ll1HAAAAAElFTkSuQmCC"
)
PNG_BLUE_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAAC0lEQVR4nGNkYPgPAAEDAQD9"
    "glLEAAAAAElFTkSuQmCC"
)


def _write_png(output_root: Path, rel_path: str, encoded: str) -> Path:
    path = output_root / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(base64.b64decode(encoded))
    return path


def test_resolver_uses_explicit_output_root_for_same_relative_ref(tmp_path):
    root_a = tmp_path / "runtime-a"
    root_b = tmp_path / "runtime-b"
    _write_png(root_a, "uploads/ref.png", PNG_RED_BASE64)
    _write_png(root_b, "uploads/ref.png", PNG_BLUE_BASE64)

    resolved_a = resolve_media_input(
        "uploads/ref.png",
        model_name="kling-v1",
        backend="vendor",
        modality="image",
        uploader=None,
        output_root=str(root_a),
    )
    resolved_b = resolve_media_input(
        "uploads/ref.png",
        model_name="kling-v1",
        backend="vendor",
        modality="image",
        uploader=None,
        output_root=str(root_b),
    )

    assert resolved_a.value == PNG_RED_BASE64
    assert resolved_b.value == PNG_BLUE_BASE64


def test_bound_output_root_rejects_absolute_path_outside_root(tmp_path):
    output_root = tmp_path / "runtime-output"
    outside = _write_png(tmp_path / "outside-output", "uploads/ref.png", PNG_RED_BASE64)

    assert classify_media_ref(str(outside), output_root=str(output_root)) == "unknown"
    assert resolve_local_media_path(str(outside), output_root=str(output_root)) is None

    with pytest.raises(ValueError, match="requires local file|Unsupported media reference"):
        resolve_media_input(
            str(outside),
            model_name="kling-v1",
            backend="vendor",
            modality="image",
            uploader=None,
            output_root=str(output_root),
        )


def test_bound_output_root_does_not_fall_back_to_cwd_output(tmp_path, monkeypatch):
    cwd_output = tmp_path / "cwd" / "output"
    runtime_output = tmp_path / "runtime-output"
    _write_png(cwd_output, "uploads/ref.png", PNG_RED_BASE64)
    _write_png(runtime_output, "uploads/ref.png", PNG_BLUE_BASE64)
    monkeypatch.chdir(tmp_path / "cwd")

    resolved = resolve_media_input(
        "uploads/ref.png",
        model_name="kling-v1",
        backend="vendor",
        modality="image",
        uploader=None,
        output_root=str(runtime_output),
    )

    assert resolved.value == PNG_BLUE_BASE64


def test_model_adapters_pass_their_configured_output_root(monkeypatch, tmp_path):
    output_root = tmp_path / "instance-output"
    _write_png(output_root, "uploads/ref.png", PNG_RED_BASE64)
    captured = []

    class FakeResolved:
        value = PNG_RED_BASE64
        headers = {}

    def fake_resolve_media_input(ref, **kwargs):
        captured.append(kwargs.get("output_root"))
        return FakeResolved()

    monkeypatch.setattr("src.models.kling.resolve_media_input", fake_resolve_media_input)
    monkeypatch.setattr("src.models.wanx.resolve_media_input", fake_resolve_media_input)

    KlingModel({"output_root": str(output_root)})._resolve_vendor_image_input(
        img_path="uploads/ref.png",
        model_name="kling-v1",
    )
    WanxModel({"output_root": str(output_root)})._resolve_sdk_image_input(
        model_name="kling-v1",
        img_path="uploads/ref.png",
        img_url=None,
        uploader=None,
    )

    assert captured == [str(output_root), str(output_root)]


def test_mulerouter_models_resolve_relative_refs_against_configured_output_root(tmp_path):
    output_root = tmp_path / "seedance-root"
    _write_png(output_root, "uploads/ref.png", PNG_BLUE_BASE64)

    video_model = MuleRouterVideoModel({"output_root": str(output_root)})
    image_model = MuleRouterImageModel({"output_root": str(output_root)})

    video_body = video_model._build_i2v_body(
        prompt="demo",
        img_url="uploads/ref.png",
        img_path=None,
        duration=5,
        resolution="1080p",
        aspect_ratio="16:9",
        seed=None,
        watermark=False,
    )
    image_body = image_model._build_edit_body(
        prompt="demo",
        ref_image_paths=["uploads/ref.png"],
        size="1024x1024",
        quality="high",
        n=1,
    )

    assert video_body["image"].endswith(PNG_BLUE_BASE64)
    assert image_body["image"].endswith(PNG_BLUE_BASE64)


def test_mulerouter_bound_inputs_are_not_silently_dropped(tmp_path):
    import pytest
    outside = tmp_path / 'other' / 'ref.png'
    outside.parent.mkdir()
    outside.write_bytes(b'image')
    model = MuleRouterVideoModel({'output_root': str(tmp_path / 'selected')})
    with pytest.raises(ValueError, match='outside this runtime'):
        model._resolve_local_image_path(img_path=str(outside))
    with pytest.raises(ValueError, match='outside this runtime'):
        model._resolve_image_input(img_path=str(outside))
