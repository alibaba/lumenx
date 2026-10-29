import importlib
import sys
import types
from pathlib import Path

import pytest
from src.audio.tts import PreparedTTS

from src.apps.comic_gen.llm import ScriptProcessor
from src.apps.comic_gen.models import (
    Character,
    ImageAsset,
    ImageVariant,
    Scene,
    Script,
    StoryboardFrame,
)


PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    b"\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\nIDATx\x9cc\xf8\x0f\x00\x01\x01\x01\x00"
    b"\x18\xdd\x8d\xb0\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _config(output_root: Path, child: str):
    return {
        "output_root": str(output_root),
        "output_dir": str(output_root / child),
        "model": {"output_root": str(output_root)},
    }


def _install_fake_runtime_modules(monkeypatch, captured):
    oss_module = types.ModuleType("src.utils.oss_utils")

    def is_object_key(_value, *, env=None):
        return False

    class OSSImageUploader:
        is_configured = False

        def upload_file(self, *_args, **_kwargs):
            return None

    oss_module.is_object_key = is_object_key
    oss_module.OSSImageUploader = OSSImageUploader
    monkeypatch.setitem(sys.modules, "src.utils.oss_utils", oss_module)

    image_module = types.ModuleType("src.models.image")

    class ImageGenModel:
        pass

    class WanxImageModel:
        def __init__(self, config):
            self.config = config
            captured.setdefault("image_model_configs", []).append(config)

        def generate(self, _prompt, output_path, **kwargs):
            captured.setdefault("image_generate_calls", []).append((Path(output_path), kwargs))
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            Path(output_path).write_bytes(PNG_BYTES)
            return output_path, 0.0

    image_module.ImageGenModel = ImageGenModel
    image_module.WanxImageModel = WanxImageModel
    monkeypatch.setitem(sys.modules, "src.models.image", image_module)

    wanx_module = types.ModuleType("src.models.wanx")

    class WanxModel:
        def __init__(self, config):
            self.config = config
            captured.setdefault("video_model_configs", []).append(config)

        def generate(self, **kwargs):
            captured.setdefault("video_generate_calls", []).append(kwargs)
            Path(kwargs["output_path"]).parent.mkdir(parents=True, exist_ok=True)
            Path(kwargs["output_path"]).write_bytes(b"video")
            return kwargs["output_path"], 0.0

    wanx_module.WanxModel = WanxModel
    monkeypatch.setitem(sys.modules, "src.models.wanx", wanx_module)

    mule_module = types.ModuleType("src.models.mulerouter")

    class MuleRouterImageModel:
        def __init__(self, config):
            captured.setdefault("mule_configs", []).append(config)

        def generate(self, _prompt, output_path, **kwargs):
            captured.setdefault("mule_generate_calls", []).append((Path(output_path), kwargs))
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            Path(output_path).write_bytes(PNG_BYTES)
            return output_path, 0.0

    mule_module.MuleRouterImageModel = MuleRouterImageModel
    monkeypatch.setitem(sys.modules, "src.models.mulerouter", mule_module)

    tts_module = types.ModuleType("src.audio.tts")

    class TTSProcessor:
        def synthesize(self, _text, output_path, **_kwargs):
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            Path(output_path).write_bytes(b"audio")

        @staticmethod
        def list_voices():
            return {}

    tts_module.TTSProcessor = TTSProcessor
    tts_module.PreparedTTS = PreparedTTS
    monkeypatch.setitem(sys.modules, "src.audio.tts", tts_module)


def _fresh_import(module_name):
    sys.modules.pop(module_name, None)
    return importlib.import_module(module_name)


def test_assets_use_injected_output_root_for_urls_refs_and_mulerouter(monkeypatch, tmp_path):
    captured = {}
    _install_fake_runtime_modules(monkeypatch, captured)
    AssetGenerator = _fresh_import("src.apps.comic_gen.assets").AssetGenerator

    output_root = tmp_path / "owned-output"
    uploaded_ref = output_root / "assets" / "characters" / "uploaded.png"
    uploaded_ref.parent.mkdir(parents=True)
    uploaded_ref.write_bytes(PNG_BYTES)

    generator = AssetGenerator(_config(output_root, "assets"))
    scene = Scene(id="scene-1", name="Room", description="A quiet room")

    result = generator.generate_scene(
        scene,
        batch_size=1,
        model_name="gpt-image-2",
    )

    assert captured["mule_configs"] == [{"output_root": str(output_root)}]
    assert result.image_url.startswith("assets/scenes/")
    assert not result.image_url.startswith("../")
    assert (output_root / result.image_url).exists()
    assert captured["mule_generate_calls"][0][0].is_relative_to(output_root)

    character = Character(
        id="char-1",
        name="Lin",
        description="short hair",
        headshot_asset=ImageAsset(
            selected_id="uploaded",
            variants=[
                ImageVariant(
                    id="uploaded",
                    url="assets/characters/uploaded.png",
                    is_uploaded_source=True,
                )
            ],
        ),
    )

    generator.generate_character(
        character,
        generation_type="full_body",
        prompt="full body",
        batch_size=1,
        model_name="gpt-image-2",
        i2i_model_name="gpt-image-2",
    )

    ref_path = captured["mule_generate_calls"][-1][1]["ref_image_path"]
    assert Path(ref_path) == uploaded_ref


def test_storyboard_and_video_resolve_refs_against_injected_output_root(monkeypatch, tmp_path):
    captured = {}
    _install_fake_runtime_modules(monkeypatch, captured)
    StoryboardGenerator = _fresh_import("src.apps.comic_gen.storyboard").StoryboardGenerator
    VideoGenerator = _fresh_import("src.apps.comic_gen.video").VideoGenerator

    output_root = tmp_path / "owned-output"
    char_ref = output_root / "assets" / "characters" / "hero.png"
    frame_ref = output_root / "storyboard" / "frame.png"
    char_ref.parent.mkdir(parents=True)
    frame_ref.parent.mkdir(parents=True)
    char_ref.write_bytes(PNG_BYTES)
    frame_ref.write_bytes(PNG_BYTES)

    character = Character(
        id="char-1",
        name="Lin",
        description="short hair",
        full_body_asset=ImageAsset(
            selected_id="full",
            variants=[ImageVariant(id="full", url="assets/characters/hero.png")],
        ),
    )
    scene = Scene(id="scene-1", name="Room", description="A quiet room")
    frame = StoryboardFrame(
        id="frame-1",
        scene_id="scene-1",
        character_ids=["char-1"],
        action_description="Lin looks up",
        image_url="storyboard/frame.png",
    )

    storyboard = StoryboardGenerator(_config(output_root, "storyboard"))
    rendered = storyboard.generate_frame(frame, [character], scene)

    assert rendered.rendered_image_url.startswith("storyboard/")
    assert not rendered.rendered_image_url.startswith("../")
    assert (output_root / rendered.rendered_image_url).exists()
    assert captured["image_generate_calls"][0][0].is_relative_to(output_root)
    assert captured["image_generate_calls"][0][1]["ref_image_paths"] == [str(char_ref.resolve())]

    video = VideoGenerator(_config(output_root, "video"))
    video_frame = StoryboardFrame(
        id="frame-video",
        scene_id="scene-1",
        action_description="Lin walks",
        image_url="storyboard/frame.png",
    )
    clip = video.generate_clip(video_frame)

    assert clip.video_url.startswith("video/")
    assert not clip.video_url.startswith("../")
    assert (output_root / clip.video_url).exists()
    assert Path(captured["video_generate_calls"][0]["output_path"]).is_relative_to(output_root)
    assert captured["video_generate_calls"][0]["img_path"] == str(frame_ref.resolve())
    assert captured["video_generate_calls"][0]["img_url"] is None


def test_audio_and_export_return_urls_relative_to_injected_output_root(monkeypatch, tmp_path):
    captured = {}
    _install_fake_runtime_modules(monkeypatch, captured)
    AudioGenerator = _fresh_import("src.apps.comic_gen.audio").AudioGenerator
    ExportManager = _fresh_import("src.apps.comic_gen.export").ExportManager

    output_root = tmp_path / "owned-output"

    frame = StoryboardFrame(
        id="frame-1",
        scene_id="scene-1",
        action_description="Lin speaks",
        dialogue="hello",
    )
    character = Character(
        id="char-1",
        name="Lin",
        description="short hair",
        voice_id="voice-1",
    )

    audio = AudioGenerator(_config(output_root, "audio"))
    audio.generate_dialogue(frame, character)
    with pytest.raises(NotImplementedError):
        audio.generate_sfx(frame)
    with pytest.raises(NotImplementedError):
        audio.generate_sfx_from_video(frame.model_copy(update={"video_url": "video/clip.mp4"}))

    assert frame.audio_url
    assert not frame.audio_url.startswith("../")
    assert (output_root / frame.audio_url).exists()
    assert frame.sfx_url is None
    assert not list((output_root / "audio" / "sfx").glob("*.mp3"))

    script = Script(
        id="script-1",
        title="Demo",
        original_text="text",
        frames=[frame],
        created_at=1.0,
        updated_at=1.0,
    )
    export_url = ExportManager(_config(output_root, "export")).render_project(
        script,
        {"format": "mp4"},
    )

    assert export_url.startswith("export/")
    assert not export_url.startswith("../")
    assert (output_root / export_url).exists()


def test_script_processor_resolves_vision_images_against_injected_output_root(tmp_path):
    output_root = tmp_path / "owned-output"
    image = output_root / "storyboard" / "frame.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(PNG_BYTES)

    processor = ScriptProcessor.__new__(ScriptProcessor)
    processor.output_root = str(output_root)

    resolved = processor._resolve_image_for_vision("storyboard/frame.png")

    assert resolved.startswith("data:image/png;base64,")
