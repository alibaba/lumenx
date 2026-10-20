"""End-to-end pipeline smoke tests.

Migrated from src/apps/comic_gen/test_pipeline.py (a legacy print-style
script that sat outside pytest's testpaths and was never collected).
Covers the five top-level pipeline steps with mocked generators:
create_project → generate_assets → generate_storyboard →
generate_video → generate_audio.
"""

import time
import uuid
import pytest
from unittest.mock import patch

from src.apps.comic_gen.models import (
    Script, Character, Scene, StoryboardFrame, GenerationStatus,
)
from src.apps.comic_gen.pipeline import ComicGenPipeline


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def pipeline(tmp_path):
    """Create a pipeline with temp data files, bypassing real IO."""
    with patch("src.apps.comic_gen.pipeline.ScriptProcessor"), \
         patch("src.apps.comic_gen.pipeline.AssetGenerator"), \
         patch("src.apps.comic_gen.pipeline.StoryboardGenerator"), \
         patch("src.apps.comic_gen.pipeline.VideoGenerator"), \
         patch("src.apps.comic_gen.pipeline.AudioGenerator"), \
         patch("src.apps.comic_gen.pipeline.ExportManager"):
        p = ComicGenPipeline()
    p.data_file = str(tmp_path / "projects.json")
    p.series_data_file = str(tmp_path / "series.json")
    p.library_data_file = str(tmp_path / "library_assets.json")
    p.scripts = {}
    p.series_store = {}
    return p


def _make_parsed_script() -> Script:
    """Build the Script that a real ScriptProcessor.parse_novel would return
    for the original smoke-test novel text."""
    now = time.time()
    alex = Character(id=str(uuid.uuid4()), name="Alex",
                     description="An explorer in the ruins")
    luna = Character(id=str(uuid.uuid4()), name="Luna",
                     description="A mysterious girl")
    ruins = Scene(id=str(uuid.uuid4()), name="Ancient Ruins",
                  description="Crumbling stone halls")
    frame = StoryboardFrame(
        id=f"frame_{uuid.uuid4().hex[:8]}",
        scene_id=ruins.id,
        character_ids=[alex.id],
        action_description="Alex enters the ancient ruins",
        dialogue="Who's there?",
    )
    return Script(
        id=str(uuid.uuid4()),
        title="The Ancient Ruins",
        original_text="Alex entered the ancient ruins. Suddenly, Luna appeared.",
        characters=[alex, luna],
        scenes=[ruins],
        frames=[frame],
        created_at=now,
        updated_at=now,
    )


@pytest.fixture
def project(pipeline):
    """A created project, as after Step 1 of the smoke flow."""
    parsed = _make_parsed_script()
    pipeline.script_processor.parse_novel.return_value = parsed
    return pipeline.create_project("The Ancient Ruins", parsed.original_text)


# ---------------------------------------------------------------------------
# Step 1: Create Project
# ---------------------------------------------------------------------------

class TestCreateProject:
    def test_project_registered_and_persisted(self, pipeline, project):
        assert project.id in pipeline.scripts
        assert project.workflow_mode == "i2v_legacy"
        pipeline.script_processor.parse_novel.assert_called_once()

    def test_parsed_entities_present(self, project):
        assert len(project.characters) == 2
        assert len(project.scenes) == 1
        assert len(project.frames) == 1


# ---------------------------------------------------------------------------
# Step 2: Generate Assets
# ---------------------------------------------------------------------------

class TestGenerateAssets:
    def test_all_assets_completed(self, pipeline, project):
        script = pipeline.generate_assets(project.id)
        assert all(c.status == GenerationStatus.COMPLETED for c in script.characters)
        assert all(s.status == GenerationStatus.COMPLETED for s in script.scenes)

    def test_generators_called_per_asset(self, pipeline, project):
        pipeline.generate_assets(project.id)
        assert pipeline.asset_generator.generate_character.call_count == 2
        assert pipeline.asset_generator.generate_scene.call_count == 1

    def test_unknown_script_raises(self, pipeline):
        with pytest.raises(ValueError, match="Script not found"):
            pipeline.generate_assets("no-such-id")


# ---------------------------------------------------------------------------
# Step 3: Generate Storyboard
# ---------------------------------------------------------------------------

class TestGenerateStoryboard:
    def test_storyboard_generator_receives_resolved_assets(self, pipeline, project):
        pipeline.storyboard_generator.generate_storyboard.side_effect = \
            lambda script, **kwargs: script
        result = pipeline.generate_storyboard(project.id)
        assert result is project
        _, kwargs = pipeline.storyboard_generator.generate_storyboard.call_args
        assert [c.id for c in kwargs["characters"]] == [c.id for c in project.characters]
        assert [s.id for s in kwargs["scenes"]] == [s.id for s in project.scenes]


# ---------------------------------------------------------------------------
# Step 4: Generate Video
# ---------------------------------------------------------------------------

class TestGenerateVideo:
    def test_frames_receive_video_urls(self, pipeline, project):
        def fake_generate(script):
            for frame in script.frames:
                frame.video_url = f"http://example.com/{frame.id}.mp4"
            return script

        pipeline.video_generator.generate_video.side_effect = fake_generate
        result = pipeline.generate_video(project.id)
        assert all(f.video_url for f in result.frames)
        pipeline.video_generator.generate_video.assert_called_once_with(project)


# ---------------------------------------------------------------------------
# Step 5: Generate Audio
# ---------------------------------------------------------------------------

class TestGenerateAudio:
    def test_dialogue_sfx_and_bgm_generated(self, pipeline, project):
        pipeline.generate_audio(project.id)
        # One frame with dialogue + speaker → one dialogue call
        assert pipeline.audio_generator.generate_dialogue.call_count == 1
        # One frame with an action description → one SFX call
        assert pipeline.audio_generator.generate_sfx.call_count == 1
        # BGM is generated for every frame
        assert pipeline.audio_generator.generate_bgm.call_count == 1
        # No video yet → no video-to-audio SFX
        pipeline.audio_generator.generate_sfx_from_video.assert_not_called()

    def test_dialogue_uses_speaker_voice_settings(self, pipeline, project):
        pipeline.generate_audio(project.id)
        frame = project.frames[0]
        speaker = next(c for c in project.characters if c.id == frame.character_ids[0])
        pipeline.audio_generator.generate_dialogue.assert_called_once_with(
            frame, speaker,
            speed=speaker.voice_speed,
            pitch=speaker.voice_pitch,
            volume=speaker.voice_volume,
        )
