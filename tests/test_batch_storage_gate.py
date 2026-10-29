"""Real generator loops must stop when their owning storage becomes unavailable."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from src.apps.comic_gen.audio import AudioGenerator
from src.apps.comic_gen.models import Character, GenerationStatus, StoryboardFrame
from src.apps.comic_gen.storyboard import StoryboardGenerator
from src.apps.comic_gen.video import VideoGenerator
from src.utils.json_storage import StorageError
from src.utils.oss_utils import OSSImageUploader


KINDS = ("storyboard_frame", "storyboard_batch", "dialogue", "video_clip", "motion_ref")


def build_generator(kind, tmp_path, monkeypatch, *, check=None, result_hook=None):
    frame = StoryboardFrame(
        id="first", scene_id="scene", action_description="test", dialogue="hello",
        image_url="https://media.example.invalid/input.png",
    )
    second = StoryboardFrame(id="second", scene_id="scene", action_description="next")
    char = Character(id="speaker", name="Speaker", description="test", voice_id="test-voice")
    leaf = (
        "storyboard" if kind.startswith("storyboard") else "audio" if kind == "dialogue" else "video"
    )
    config = {"output_root": str(tmp_path), "output_dir": str(tmp_path / leaf)}
    kwargs = {"before_generation": check} if check is not None else {}
    calls = []

    def provider(prompt=None, output_path=None, *args, **kw):
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"paid result retained")
        calls.append(path)
        if result_hook is not None:
            result_hook()
        return str(path), 0

    if kind.startswith("storyboard"):
        generator = StoryboardGenerator(config, **kwargs)
        monkeypatch.setattr(generator.model, "generate", provider)
        if kind == "storyboard_batch":
            script = SimpleNamespace(title="test", frames=[frame, second], characters=[], scenes=[])
            invoke = lambda: generator.generate_storyboard(script)
        else:
            invoke = lambda: generator.generate_frame(frame, [], None, batch_size=3)
    elif kind == "dialogue":
        generator = AudioGenerator(config, **kwargs)
        monkeypatch.setattr(generator.tts, "synthesize", provider)
        invoke = lambda: generator.generate_dialogue(frame, char)
    else:
        generator = VideoGenerator(config, **kwargs)
        monkeypatch.setattr(generator.model, "generate", provider)
        invoke = (
            (lambda: generator.generate_clip(frame)) if kind == "video_clip"
            else (lambda: generator.generate_i2v(frame.image_url, "test"))
        )
    monkeypatch.setattr("src.utils.oss_utils.OSSImageUploader._instance", None)
    return invoke, calls, frame, second


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("failure_stage", ["before", "after", "provider"])
def test_storage_gate_stops_submission_or_result_writeback(
    tmp_path, monkeypatch, kind, failure_stage,
):
    failure = StorageError("runtime no longer writable", commit_uncertain=True)
    state = {"failed": failure_stage == "before"}

    def check():
        if state["failed"]:
            raise failure

    def result_hook():
        if failure_stage == "provider":
            raise failure
        state["failed"] = True

    invoke, calls, frame, second = build_generator(
        kind, tmp_path, monkeypatch, check=check, result_hook=result_hook,
    )
    with pytest.raises(StorageError) as raised:
        invoke()
    assert raised.value is failure
    assert len(calls) == (0 if failure_stage == "before" else 1)
    for path in calls:
        assert path.read_bytes() == b"paid result retained"
    assert frame.status != GenerationStatus.COMPLETED
    assert frame.image_url == "https://media.example.invalid/input.png"
    assert frame.rendered_image_asset.variants == []
    assert frame.video_url is None
    assert frame.audio_url is None
    assert frame.dialogue_text_hash is None
    assert second.image_url is None


@pytest.mark.parametrize("kind", KINDS)
def test_standalone_generator_still_runs_without_storage_hook(tmp_path, monkeypatch, kind):
    invoke, calls, frame, second = build_generator(kind, tmp_path, monkeypatch)
    result = invoke()
    expected_count = 3 if kind == "storyboard_frame" else 2 if kind == "storyboard_batch" else 1
    assert len(calls) == expected_count
    assert all(path.read_bytes() == b"paid result retained" for path in calls)
    if kind == "motion_ref":
        assert result["video_url"]
    else:
        assert frame.status == GenerationStatus.COMPLETED
    if kind == "storyboard_batch":
        assert second.status == GenerationStatus.COMPLETED


@pytest.mark.parametrize("kind", ["storyboard_frame", "dialogue", "video_clip"])
def test_non_storage_generation_failure_keeps_failed_frame_behavior(tmp_path, monkeypatch, kind):
    def failed_provider():
        raise ValueError("provider rejected request")

    invoke, calls, frame, _ = build_generator(
        kind, tmp_path, monkeypatch, result_hook=failed_provider,
    )
    invoke()
    assert len(calls) == 1
    assert frame.status == GenerationStatus.FAILED


@pytest.mark.parametrize("kind", ["storyboard_batch", "video_clip", "motion_ref"])
@pytest.mark.parametrize("failure_stage", ["upload", "after_upload"])
def test_optional_upload_cannot_swallow_storage_failure(tmp_path, monkeypatch, kind, failure_stage):
    failure = StorageError("runtime failed during upload", commit_uncertain=True)
    state = {"failed": False}
    uploads = []

    def check():
        if state["failed"]:
            raise failure

    def upload(self, path, **kwargs):
        uploads.append(Path(path))
        if failure_stage == "upload":
            raise failure
        state["failed"] = True
        return "mirrored/result"

    invoke, calls, frame, second = build_generator(kind, tmp_path, monkeypatch, check=check)
    monkeypatch.setattr(OSSImageUploader, "is_configured", property(lambda self: True))
    monkeypatch.setattr(OSSImageUploader, "upload_file", upload)
    with pytest.raises(StorageError) as raised:
        invoke()
    assert raised.value is failure
    assert len(calls) == 1
    assert len(uploads) == 1
    assert calls[0].read_bytes() == b"paid result retained"
    assert frame.image_url != "mirrored/result"
    assert frame.video_url != "mirrored/result"
    assert second.image_url is None


@pytest.mark.parametrize("method", ["generate_sfx", "generate_sfx_from_video", "generate_bgm"])
def test_placeholder_audio_generation_is_explicitly_unsupported_without_file_writes(tmp_path, method):
    frame = StoryboardFrame(
        id="first", scene_id="scene", action_description="test", video_url="video/source.mp4",
    )
    output_dir = tmp_path / "audio"
    generator = AudioGenerator({"output_root": str(tmp_path), "output_dir": str(output_dir)})

    with pytest.raises(NotImplementedError):
        getattr(generator, method)(frame)

    assert frame.sfx_url is None
    assert not hasattr(frame, "bgm_url")
    assert frame.status != GenerationStatus.COMPLETED
    assert not list(output_dir.rglob("*.mp3"))


@pytest.mark.parametrize("method", ["generate_sfx", "generate_sfx_from_video", "generate_bgm"])
def test_placeholder_audio_generation_preserves_closed_storage_gate(tmp_path, method):
    failure = StorageError("audio storage closed")
    frame = StoryboardFrame(
        id="first", scene_id="scene", action_description="test", video_url="video/source.mp4",
    )
    output_dir = tmp_path / "audio"
    def closed_storage():
        raise failure

    generator = AudioGenerator(
        {"output_root": str(tmp_path), "output_dir": str(output_dir)},
        before_generation=closed_storage,
    )

    with pytest.raises(StorageError) as raised:
        getattr(generator, method)(frame)

    assert raised.value is failure
    assert frame.sfx_url is None
    assert not hasattr(frame, "bgm_url")
    assert frame.status != GenerationStatus.COMPLETED
    assert not list(output_dir.rglob("*.mp3"))
