import pytest

from src.apps.comic_gen.models import GenerationStatus, Scene, StoryboardFrame
from src.apps.comic_gen.storyboard import StoryboardGenerator


def _make_frame() -> StoryboardFrame:
    return StoryboardFrame(
        id="frame-1",
        scene_id="scene-1",
        action_description="A child opens his eyes in a dim bedroom.",
    )


def _make_scene() -> Scene:
    return Scene(
        id="scene-1",
        name="卧室",
        description="昏暗卧室，暖色台灯亮着。",
    )


def test_generate_frame_marks_failed_without_raising_by_default(tmp_path):
    generator = StoryboardGenerator({"output_dir": str(tmp_path)})
    frame = _make_frame()
    scene = _make_scene()

    def boom(*args, **kwargs):
        raise RuntimeError("edit upstream broke")

    generator.model.generate = boom

    result = generator.generate_frame(frame, [], scene, prompt="test prompt", model_name="openai-image-edit")

    assert result.status == GenerationStatus.FAILED


def test_generate_frame_can_raise_real_error_for_interactive_render(tmp_path):
    generator = StoryboardGenerator({"output_dir": str(tmp_path)})
    frame = _make_frame()
    scene = _make_scene()

    def boom(*args, **kwargs):
        raise RuntimeError("edit upstream broke")

    generator.model.generate = boom

    with pytest.raises(RuntimeError, match="edit upstream broke"):
        generator.generate_frame(
            frame,
            [],
            scene,
            prompt="test prompt",
            model_name="openai-image-edit",
            raise_on_error=True,
        )

    assert frame.status == GenerationStatus.FAILED


def test_generate_frame_preserves_reference_order_for_openai_edit(tmp_path):
    generator = StoryboardGenerator({"output_dir": "output/storyboard/test-order"})
    frame = _make_frame()
    scene = _make_scene()
    ref_a = tmp_path / "ref-a.png"
    ref_b = tmp_path / "ref-b.png"
    ref_a.write_bytes(b"ref-a")
    ref_b.write_bytes(b"ref-b")
    captured = {}

    def fake_generate(prompt, output_path, ref_image_paths=None, **kwargs):
        captured["ref_image_paths"] = list(ref_image_paths or [])
        captured["output_path"] = output_path
        with open(output_path, "wb") as handle:
            handle.write(b"generated")
        return output_path, 0.1

    generator.model.generate = fake_generate

    result = generator.generate_frame(
        frame,
        [],
        scene,
        prompt="keep the same framing",
        model_name="openai-image-edit",
        ref_image_paths=[str(ref_a), str(ref_b), str(ref_a)],
    )

    assert captured["ref_image_paths"] == [str(ref_a), str(ref_b)]
    assert result.status == GenerationStatus.COMPLETED


def test_generate_frame_can_suppress_auto_reference_collection(tmp_path):
    generator = StoryboardGenerator({"output_dir": "output/storyboard/test-suppress"})
    frame = _make_frame()
    scene = _make_scene()
    scene.image_url = str(tmp_path / "scene.png")
    (tmp_path / "scene.png").write_bytes(b"scene")
    captured = {}

    def fake_generate(prompt, output_path, ref_image_paths=None, **kwargs):
        captured["ref_image_paths"] = list(ref_image_paths or [])
        captured["kwargs"] = kwargs
        with open(output_path, "wb") as handle:
            handle.write(b"generated")
        return output_path, 0.1

    generator.model.generate = fake_generate

    result = generator.generate_frame(
        frame,
        [],
        scene,
        prompt="keep the same framing",
        model_name="openai-image-edit",
        suppress_auto_references=True,
    )

    assert captured["ref_image_paths"] == []
    assert result.status == GenerationStatus.COMPLETED
