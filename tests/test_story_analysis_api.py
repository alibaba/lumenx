import time
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import src.apps.comic_gen.api as api_module
from src.apps.comic_gen.models import Character, CharacterPresenceEntry, Prop, Scene, Script, StoryAnalysis, StoryBeat
from src.apps.comic_gen.pipeline import ComicGenPipeline


@pytest.fixture
def client_with_pipeline(tmp_path, monkeypatch):
    with patch("src.apps.comic_gen.pipeline.ScriptProcessor"), \
         patch("src.apps.comic_gen.pipeline.AssetGenerator"), \
         patch("src.apps.comic_gen.pipeline.StoryboardGenerator"), \
         patch("src.apps.comic_gen.pipeline.VideoGenerator"), \
         patch("src.apps.comic_gen.pipeline.AudioGenerator"), \
         patch("src.apps.comic_gen.pipeline.ExportManager"):
        instance = ComicGenPipeline()

    instance.data_file = str(tmp_path / "projects.json")
    instance.series_data_file = str(tmp_path / "series.json")
    instance.scripts = {}
    instance.series_store = {}
    monkeypatch.setattr(api_module, "pipeline", instance)

    return TestClient(api_module.app), instance


def _make_script() -> Script:
    now = time.time()
    character = Character(id="char-1", name="林夏", description="短发，深色风衣")
    partner = Character(id="char-2", name="周沉", description="黑色夹克，神情克制")
    scene = Scene(id="scene-1", name="废弃仓库", description="昏暗潮湿的旧仓库")
    prop = Prop(id="prop-1", name="红色纸鹤", description="被雨水打湿的红色纸鹤")
    analysis = StoryAnalysis(
        summary="林夏夜探废弃仓库，在现场发现关键线索纸鹤。",
        plot_points=["林夏进入仓库调查。", "她发现了地上的红色纸鹤。"],
        scene_beats=[
            StoryBeat(
                id="story_beat_001",
                order=1,
                title="第1场 · 废弃仓库",
                summary="林夏进入仓库调查，并注意到地上的红色纸鹤。",
                action_summary="林夏进入仓库调查，并注意到地上的红色纸鹤。",
                dialogue_excerpt="林夏：这里不对劲。",
                storyboard_goal="先交代仓库空间，再突出纸鹤线索。",
                scene_id=scene.id,
                scene_name=scene.name,
                location_hint=scene.name,
                time_hint="夜晚",
                character_ids=[character.id, partner.id],
                character_names=[character.name, partner.name],
                prop_ids=[prop.id],
                prop_names=[prop.name],
                source_excerpt="林夏穿着深色风衣走进废弃仓库，看见地上的红色纸鹤。",
                storyboard_focus="仓库空间、林夏动作、纸鹤线索同时入镜。",
            )
        ],
        character_presence=[
            CharacterPresenceEntry(
                character_id=character.id,
                character_name=character.name,
                scene_beat_ids=["story_beat_001"],
                scene_titles=["第1场 · 废弃仓库"],
                mention_count=2,
                highlights=["林夏进入仓库调查，并注意到地上的红色纸鹤。"],
            )
        ],
    )
    return Script(
        id="script-api-1",
        title="测试项目",
        original_text="林夏穿着深色风衣走进废弃仓库，看见地上的红色纸鹤。",
        characters=[character, partner],
        scenes=[scene],
        props=[prop],
        story_analysis=analysis,
        created_at=now,
        updated_at=now,
    )


def test_get_story_analysis_returns_structured_payload(client_with_pipeline):
    client, pipeline = client_with_pipeline
    script = _make_script()
    pipeline.scripts[script.id] = script

    response = client.get(f"/projects/{script.id}/story_analysis")

    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"] == script.story_analysis.summary
    assert payload["scene_beats"][0]["action_summary"] == "林夏进入仓库调查，并注意到地上的红色纸鹤。"


def test_update_story_beat_endpoint_persists_fields(client_with_pipeline):
    client, pipeline = client_with_pipeline
    script = _make_script()
    pipeline.scripts[script.id] = script

    response = client.put(
        f"/projects/{script.id}/story_analysis/beats/story_beat_001",
        json={
            "action_summary": "林夏沿着水痕继续向仓库深处推进。",
            "dialogue_excerpt": "林夏：先别出声。",
            "storyboard_goal": "锁定纸鹤线索，保持空间连续性。",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    beat = payload["story_analysis"]["scene_beats"][0]
    assert beat["action_summary"] == "林夏沿着水痕继续向仓库深处推进。"
    assert beat["dialogue_excerpt"] == "林夏：先别出声。"
    assert beat["storyboard_goal"] == "锁定纸鹤线索，保持空间连续性。"


def test_analyze_story_beat_endpoint_replaces_target_frames(client_with_pipeline):
    client, pipeline = client_with_pipeline
    script = _make_script()
    pipeline.scripts[script.id] = script

    pipeline.script_processor.analyze_to_storyboard = lambda text, entities_json, story_analysis, target_beat_id=None: [
        {
            "story_beat_id": "story_beat_001",
            "story_beat_title": "第1场 · 废弃仓库",
            "scene_ref_name": "废弃仓库",
            "character_ref_names": ["林夏"],
            "prop_ref_names": ["红色纸鹤"],
            "visual_atmosphere": "仓库尽头透出冷白灯光",
            "action_description": "林夏沿着纸鹤留下的水痕继续向前。",
            "shot_size": "中景",
            "camera_angle": "平视",
            "camera_movement": "缓慢推进",
            "dialogue": "有人比我们先到。",
            "speaker": "林夏",
        }
    ]

    response = client.post(
        f"/projects/{script.id}/storyboard/analyze_beat",
        json={"beat_id": "story_beat_001"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["frames"][0]["story_beat_id"] == "story_beat_001"
    assert payload["frames"][0]["story_beat_title"] == "第1场 · 废弃仓库"
    assert payload["frames"][0]["action_description"] == "林夏沿着纸鹤留下的水痕继续向前。"
