import base64
import time
from pathlib import Path
from types import SimpleNamespace

from src.apps.comic_gen.models import Character, Scene, Script, StoryboardFrame
from src.apps.comic_gen.pipeline import ComicGenPipeline
from src.models.wanx import WanxModel


PNG_1X1_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4//8/AwAI/AL+"
    "X2VINQAAAABJRU5ErkJggg=="
)


def _write_output_png(rel_path: str) -> str:
    file_path = Path("output") / rel_path
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_bytes(base64.b64decode(PNG_1X1_BASE64))
    return str(file_path)


def _build_pipeline(script: Script, wanx_model: WanxModel) -> ComicGenPipeline:
    pipeline = ComicGenPipeline.__new__(ComicGenPipeline)
    pipeline.scripts = {script.id: script}
    pipeline._save_data = lambda: None
    pipeline._kling_model = None
    pipeline._vidu_model = None
    pipeline.video_generator = SimpleNamespace(model=wanx_model)
    pipeline.get_script = lambda script_id: pipeline.scripts.get(script_id)
    return pipeline


def test_local_only_pipeline_flow_without_oss(monkeypatch):
    """
    End-to-end backend check for local-only mode:
    - local uploaded/generated/storyboard refs stay as stable project refs
    - video task snapshots local input under output/video_inputs
    - DashScope I2V media prep works without OSS via temp oss:// URL + resolve header
    """
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    for key in (
        "ALIBABA_CLOUD_ACCESS_KEY_ID",
        "ALIBABA_CLOUD_ACCESS_KEY_SECRET",
        "OSS_BUCKET_NAME",
        "OSS_ENDPOINT",
        "KLING_ACCESS_KEY",
        "KLING_SECRET_KEY",
        "VIDU_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)

    _write_output_png("uploads/local_only_uploaded.png")
    _write_output_png("assets/scenes/local_only_generated.png")
    _write_output_png("storyboard/local_only_frame.png")

    now = time.time()
    character = Character(
        id="char-local",
        name="Local Hero",
        description="A character from local-only flow",
        image_url="uploads/local_only_uploaded.png",
    )
    scene = Scene(
        id="scene-local",
        name="Local Scene",
        description="A scene generated in local-only flow",
        image_url="assets/scenes/local_only_generated.png",
    )
    frame = StoryboardFrame(
        id="frame-local",
        scene_id=scene.id,
        character_ids=[character.id],
        prop_ids=[],
        rendered_image_url="storyboard/local_only_frame.png",
    )
    script = Script(
        id="script-local-only",
        title="Local-Only",
        original_text="demo",
        characters=[character],
        scenes=[scene],
        frames=[frame],
        created_at=now,
        updated_at=now,
    )

    captured = {}

    wanx_model = WanxModel({"params": {}})

    def fake_create_dashscope_temp_url(local_path: str, model_name: str) -> str:
        captured["temp_local_path"] = local_path
        captured["temp_model_name"] = model_name
        return "oss://dashscope-temp/local-only/frame.png"

    def fake_generate_wan_i2v_http(
        *,
        prompt: str,
        img_url: str,
        model_name: str = "wan2.6-i2v",
        resolution: str = "720P",
        # ratio was added to _generate_wan_i2v_http after Phase 2 catalog
        # changes; mocks must accept it or the call site fails.
        ratio=None,
        duration: int = 5,
        prompt_extend: bool = True,
        negative_prompt: str = None,
        audio_url: str = None,
        watermark: bool = False,
        seed: int = None,
        shot_type: str = "single",
        extra_headers=None,
    ) -> str:
        captured["img_url"] = img_url
        captured["model_name"] = model_name
        captured["headers"] = dict(extra_headers or {})
        return "https://example.com/local-only-video.mp4"

    def fake_download_video(_url: str, output_path: str):
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(b"video")

    monkeypatch.setattr(wanx_model, "_create_dashscope_temp_url", fake_create_dashscope_temp_url)
    monkeypatch.setattr(wanx_model, "_generate_wan_i2v_http", fake_generate_wan_i2v_http)
    monkeypatch.setattr(wanx_model, "_download_video", fake_download_video)

    pipeline = _build_pipeline(script, wanx_model)

    _, task_id = pipeline.create_video_task(
        script_id=script.id,
        image_url=frame.rendered_image_url,
        prompt="Pan and zoom on the character",
        model="wan2.6-i2v",
    )
    task = next(t for t in script.video_tasks if t.id == task_id)

    assert task.image_url.startswith("video_inputs/")
    assert (Path("output") / task.image_url).exists()

    pipeline.process_video_task(script.id, task_id)

    assert task.status == "completed"
    # Windows 下 pipeline 用 os.path.join 拼出反斜杠, 断言前归一化为 POSIX
    assert Path(task.video_url).as_posix().startswith("video/video_")

    assert captured["img_url"] == "oss://dashscope-temp/local-only/frame.png"
    assert captured["model_name"] == "wan2.6-i2v"
    assert captured["temp_model_name"] == "wan2.6-i2v"
    assert captured["headers"]["X-DashScope-OssResourceResolve"] == "enable"
    assert captured["temp_local_path"].startswith(str(Path.cwd()))

    # Stable project refs remain local refs; request-side transforms are not persisted.
    assert script.characters[0].image_url == "uploads/local_only_uploaded.png"
    assert script.scenes[0].image_url == "assets/scenes/local_only_generated.png"
    assert script.frames[0].rendered_image_url == "storyboard/local_only_frame.png"
