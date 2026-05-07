import time
from pathlib import Path
from unittest.mock import patch

import pytest

from src.apps.comic_gen.models import (
    Character,
    CharacterPresenceEntry,
    Prop,
    Scene,
    Script,
    StoryAnalysis,
    StoryBeat,
    StoryboardFrame,
)
from src.apps.comic_gen.llm import ScriptProcessor
from src.apps.comic_gen.pipeline import ComicGenPipeline


FIXTURE_SAMPLE_500 = Path(__file__).parent / "fixtures" / "story_samples" / "mechanical_wife_500.txt"


@pytest.fixture
def pipeline(tmp_path):
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
    return instance


def _make_script_with_analysis() -> Script:
    now = time.time()
    character = Character(id="char-1", name="林夏", description="短发，深色风衣")
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
                chapter_order=1,
                chapter_title="第1章 废弃仓库",
                summary="林夏进入仓库调查，并注意到地上的红色纸鹤。",
                action_summary="林夏进入仓库调查，并注意到地上的红色纸鹤。",
                dialogue_excerpt="",
                storyboard_goal="先交代仓库空间，再突出纸鹤线索。",
                scene_id=scene.id,
                scene_name=scene.name,
                location_hint=scene.name,
                time_hint="夜晚",
                character_ids=[character.id],
                character_names=[character.name],
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
        id="script-1",
        title="测试项目",
        original_text="林夏穿着深色风衣走进废弃仓库，看见地上的红色纸鹤。",
        characters=[character],
        scenes=[scene],
        props=[prop],
        story_analysis=analysis,
        created_at=now,
        updated_at=now,
    )


def test_analyze_text_to_frames_passes_story_analysis_to_storyboard_llm(pipeline):
    script = _make_script_with_analysis()
    pipeline.scripts[script.id] = script
    captured = {}

    def fake_analyze_to_storyboard(text, entities_json, story_analysis):
        captured["text"] = text
        captured["entities_json"] = entities_json
        captured["story_analysis"] = story_analysis
        return [
            {
                "scene_ref_name": "废弃仓库",
                "character_ref_names": ["林夏"],
                "prop_ref_names": ["红色纸鹤"],
                "visual_atmosphere": "昏暗潮湿的仓库内部",
                "action_description": "林夏弯腰看向地上的红色纸鹤。",
                "shot_size": "中景",
                "camera_angle": "平视",
                "camera_movement": "静止",
                "dialogue": None,
                "speaker": None,
            }
        ]

    pipeline.script_processor.analyze_to_storyboard = fake_analyze_to_storyboard

    updated_script = pipeline.analyze_text_to_frames(script.id, script.original_text)

    assert captured["story_analysis"].summary == script.story_analysis.summary
    assert captured["story_analysis"].scene_beats[0].scene_name == "废弃仓库"
    assert updated_script.frames[0].scene_id == "scene-1"
    assert updated_script.frames[0].story_beat_id == "story_beat_001"
    assert updated_script.frames[0].character_ids == ["char-1"]
    assert updated_script.frames[0].prop_ids == ["prop-1"]


def test_analyze_text_to_frames_rebuilds_story_analysis_when_text_changes(pipeline):
    script = _make_script_with_analysis()
    pipeline.scripts[script.id] = script
    rebuilt_analysis = StoryAnalysis(summary="新的结构化摘要")
    captured = {}

    pipeline.script_processor.build_story_analysis = lambda text, characters, scenes, props: rebuilt_analysis

    def fake_analyze_to_storyboard(text, entities_json, story_analysis):
        captured["story_analysis"] = story_analysis
        return []

    pipeline.script_processor.analyze_to_storyboard = fake_analyze_to_storyboard

    with pytest.raises(RuntimeError, match="未返回任何帧数据"):
        pipeline.analyze_text_to_frames(script.id, "新的文本内容")

    assert captured["story_analysis"] is rebuilt_analysis


def test_update_story_beat_persists_editable_fields(pipeline):
    script = _make_script_with_analysis()
    pipeline.scripts[script.id] = script

    updated_script = pipeline.update_story_beat(
        script.id,
        "story_beat_001",
        action_summary="林夏放慢脚步，观察纸鹤与仓库入口的关系。",
        dialogue_excerpt="林夏：这里不对劲。",
        storyboard_goal="突出仓库空间、纸鹤线索与林夏的警觉状态。",
    )

    beat = updated_script.story_analysis.scene_beats[0]
    assert beat.action_summary == "林夏放慢脚步，观察纸鹤与仓库入口的关系。"
    assert beat.dialogue_excerpt == "林夏：这里不对劲。"
    assert beat.storyboard_goal == "突出仓库空间、纸鹤线索与林夏的警觉状态。"


def test_build_story_analysis_inherits_scene_within_same_chapter(pipeline):
    processor = ScriptProcessor()
    character = Character(id="char-1", name="方奇", description="神经紧绷的男主")
    scene = Scene(id="scene-1", name="房间", description="科幻极简的纯白房间")
    prop = Prop(id="prop-1", name="茶杯", description="52度的茶杯")
    raw_blocks = [
        {
            "heading": "",
            "content": "房间里，方奇看着面前的茶杯。冷白灯压得人透不过气，璃光安静地站在桌边。",
            "chapter_title": "第1章 开场",
            "chapter_order": 1,
        },
        {
            "heading": "",
            "content": "方奇低声说：我是不是又回来了？璃光轻轻把茶杯往前推了推，语气依旧温柔。",
            "chapter_title": "第1章 开场",
            "chapter_order": 1,
        },
    ]

    with patch.object(processor, "_split_story_blocks", return_value=raw_blocks):
        analysis = processor.build_story_analysis("任意文本", [character], [scene], [prop])

    assert len(analysis.scene_beats) == 2
    assert analysis.scene_beats[0].scene_name == "房间"
    assert analysis.scene_beats[1].scene_name == "房间"
    assert analysis.scene_beats[0].chapter_title == "第1章 开场"
    assert analysis.scene_beats[1].chapter_order == 1
    assert "no_scene" not in analysis.scene_beats[1].quality_flags


def test_storyboard_story_analysis_format_includes_chapter_context(pipeline):
    processor = ScriptProcessor()
    script = _make_script_with_analysis()

    formatted = processor._format_story_analysis_for_storyboard(script.story_analysis)
    single = processor._format_single_story_beat_for_storyboard(script.story_analysis, "story_beat_001")

    assert "章节序号:1" in formatted
    assert "章节标题:第1章 废弃仓库" in formatted
    assert "章节序号：1" in single
    assert "章节标题：第1章 废弃仓库" in single


def test_resolve_story_beat_for_frame_falls_back_to_scene_and_character_matches(pipeline):
    story_analysis = StoryAnalysis(
        scene_beats=[
            StoryBeat(
                id="story_beat_001",
                order=1,
                title="第1场 · 审查室",
                scene_id="scene-1",
                scene_name="审查室",
                character_ids=["char-1", "char-2"],
                character_names=["林夏", "周沉"],
                prop_ids=["prop-1"],
                prop_names=["录音笔"],
            ),
            StoryBeat(
                id="story_beat_002",
                order=2,
                title="第2场 · 审查室门口",
                scene_id="scene-2",
                scene_name="审查室门口",
                character_ids=["char-1", "char-3"],
                character_names=["林夏", "安怜"],
                prop_ids=["prop-2"],
                prop_names=["电击器"],
            ),
        ]
    )
    frame_data = {
        "scene_ref_name": "审查室门口",
        "character_ref_names": ["林夏", "安怜"],
        "prop_ref_names": ["电击器"],
    }

    matched = pipeline._resolve_story_beat_for_frame(frame_data, story_analysis)

    assert matched is not None
    assert matched.id == "story_beat_002"


def test_resolve_story_beat_for_frame_emits_scoring_debug_logs(pipeline, caplog):
    story_analysis = StoryAnalysis(
        scene_beats=[
            StoryBeat(
                id="story_beat_001",
                order=1,
                title="第1场 · 审查室",
                scene_id="scene-1",
                scene_name="审查室",
                character_ids=["char-1"],
                character_names=["林夏"],
                prop_ids=["prop-1"],
                prop_names=["录音笔"],
            ),
            StoryBeat(
                id="story_beat_002",
                order=2,
                title="第2场 · 审查室门口",
                scene_id="scene-2",
                scene_name="审查室门口",
                character_ids=["char-1", "char-3"],
                character_names=["林夏", "安怜"],
                prop_ids=["prop-2"],
                prop_names=["电击器"],
            ),
        ]
    )
    frame_data = {
        "scene_ref_name": "审查室门口",
        "character_ref_names": ["林夏", "安怜"],
        "prop_ref_names": ["电击器"],
    }

    with caplog.at_level("DEBUG"):
        matched = pipeline._resolve_story_beat_for_frame(frame_data, story_analysis)

    assert matched is not None
    assert matched.id == "story_beat_002"
    assert "Story beat resolve candidate" in caplog.text
    assert "scene_match=exact(+5)" in caplog.text
    assert "Story beat resolve result" in caplog.text


def test_analyze_story_beat_to_frames_replaces_only_target_beat_frames(pipeline):
    script = _make_script_with_analysis()
    script.frames = [
        StoryboardFrame(
            id="frame-old-1",
            scene_id="scene-1",
            story_beat_id="story_beat_001",
            story_beat_title="第1场 · 废弃仓库",
            story_beat_order=1,
            chapter_order=1,
            chapter_title="第1章 废弃仓库",
            action_description="旧分镜",
        ),
        StoryboardFrame(
            id="frame-other",
            scene_id="scene-1",
            story_beat_id="story_beat_999",
            story_beat_title="其他场次",
            story_beat_order=9,
            chapter_order=9,
            chapter_title="第9章 其他",
            action_description="保留分镜",
        ),
    ]
    pipeline.scripts[script.id] = script
    captured = {}

    def fake_analyze_to_storyboard(text, entities_json, story_analysis, target_beat_id=None):
        captured["target_beat_id"] = target_beat_id
        return [
            {
                "story_beat_id": "story_beat_001",
                "story_beat_title": "第1场 · 废弃仓库",
                "scene_ref_name": "废弃仓库",
                "character_ref_names": ["林夏"],
                "prop_ref_names": ["红色纸鹤"],
                "visual_atmosphere": "仓库深处传来冷色灯光",
                "action_description": "林夏沿着纸鹤留下的水痕继续向前。",
                "shot_size": "中景",
                "camera_angle": "平视",
                "camera_movement": "缓慢推进",
                "dialogue": "这里还有人来过。",
                "speaker": "林夏",
            },
            {
                "story_beat_id": "story_beat_001",
                "story_beat_title": "第1场 · 废弃仓库",
                "scene_ref_name": "废弃仓库",
                "character_ref_names": ["林夏"],
                "prop_ref_names": [],
                "visual_atmosphere": "货架阴影压迫感更强",
                "action_description": "林夏抬头看向货架尽头，停住脚步。",
                "shot_size": "特写",
                "camera_angle": "低机位",
                "camera_movement": "静止",
                "dialogue": None,
                "speaker": None,
            },
        ]

    pipeline.script_processor.analyze_to_storyboard = fake_analyze_to_storyboard

    updated_script = pipeline.analyze_story_beat_to_frames(script.id, "story_beat_001")

    assert captured["target_beat_id"] == "story_beat_001"
    assert [frame.story_beat_id for frame in updated_script.frames] == ["story_beat_001", "story_beat_001", "story_beat_999"]
    assert updated_script.frames[0].action_description.startswith("林夏沿着纸鹤")
    assert updated_script.frames[0].chapter_order == 1
    assert updated_script.frames[0].chapter_title == "第1章 废弃仓库"
    assert updated_script.frames[1].chapter_order == 1
    assert updated_script.frames[1].chapter_title == "第1章 废弃仓库"
    assert updated_script.frames[2].action_description == "保留分镜"
    assert updated_script.frames[2].chapter_order == 9
    assert updated_script.frames[2].chapter_title == "第9章 其他"


def test_build_story_analysis_groups_chapter_dense_text_with_quality_flags(pipeline):
    processor = ScriptProcessor()
    characters = [
        Character(id="char-1", name="方奇", description="神经紧绷的男主"),
        Character(id="char-2", name="璃光", description="外表温顺的机械妻"),
    ]
    scenes = [
        Scene(id="scene-1", name="客厅", description="狭小但整洁的出租屋客厅"),
        Scene(id="scene-2", name="审查室", description="冷白灯下的封闭审查室"),
    ]
    props = [
        Prop(id="prop-1", name="手机", description="不断震动的手机"),
        Prop(id="prop-2", name="电击器", description="黑色电击器"),
    ]
    text = """
    第1章 合租条例

    方奇刚回到客厅，桌上的手机突然震动。璃光站在沙发旁盯着他。

    第2章 夜审

    凌晨的审查室冷得像冰窖。

    尾声
    """.strip()

    analysis = processor.build_story_analysis(text, characters, scenes, props)

    assert len(analysis.scene_beats) == 3
    assert analysis.scene_beats[0].chapter_title == "第1章 合租条例"
    assert analysis.scene_beats[0].chapter_order == 1
    assert analysis.scene_beats[0].scene_name == "客厅"
    assert analysis.scene_beats[0].quality_flags == []
    assert analysis.scene_beats[1].chapter_title == "第2章 夜审"
    assert analysis.scene_beats[1].scene_name == "审查室"
    assert "title_only" in analysis.scene_beats[2].quality_flags
    assert "no_characters" in analysis.scene_beats[2].quality_flags
    assert analysis.scene_beats[2].scene_name == "审查室"
    assert "no_scene" not in analysis.scene_beats[2].quality_flags
    assert "over_segmented" in analysis.scene_beats[2].quality_flags


def test_build_story_analysis_handles_dialogue_dense_multi_character_same_scene(pipeline):
    processor = ScriptProcessor()
    characters = [
        Character(id="char-1", name="林夏", description="短发，眼神锋利"),
        Character(id="char-2", name="周沉", description="沉默克制"),
        Character(id="char-3", name="安怜", description="冷静旁观者"),
    ]
    scenes = [
        Scene(id="scene-1", name="审查室", description="冷白灯下的狭小审查室"),
        Scene(id="scene-2", name="审查室门口", description="金属门外的狭窄缓冲区"),
    ]
    props = [
        Prop(id="prop-1", name="录音笔", description="银色录音笔"),
        Prop(id="prop-2", name="电击器", description="黑色电击器"),
    ]
    text = """
    第1章 夜谈

    第1场：审查室
    人物：林夏，周沉，安怜
    △ 日光灯轻微闪烁，林夏把录音笔推到桌面中央。
    林夏（冷静）：你昨晚为什么回仓库？
    周沉（克制）：因为有人比你更早拿走了纸鹤。
    安怜（平静）：先回答问题。

    第2场：审查室门口
    林夏推门走到审查室门口，安怜拿着电击器跟了出来。
    周沉隔着门缝低声道：“别信她。”
    """.strip()

    analysis = processor.build_story_analysis(text, characters, scenes, props)

    assert len(analysis.scene_beats) == 2
    first_beat = analysis.scene_beats[0]
    second_beat = analysis.scene_beats[1]
    assert first_beat.chapter_title == "第1章 夜谈"
    assert set(first_beat.character_names) == {"林夏", "周沉", "安怜"}
    assert "录音笔" in first_beat.prop_names
    assert "林夏" in first_beat.dialogue_excerpt
    assert "周沉" in first_beat.dialogue_excerpt
    assert second_beat.scene_name == "审查室门口"
    assert "安怜" in second_beat.character_names
    assert analysis.character_relationships
    top_pairs = {
        frozenset([edge.source_character_name, edge.target_character_name]): edge.co_scene_count
        for edge in analysis.character_relationships
    }
    assert top_pairs[frozenset(["林夏", "周沉"])] >= 2


def test_sample_500_fixture_cleans_entities_and_preserves_chapter_beat_mapping():
    text = FIXTURE_SAMPLE_500.read_text(encoding="utf-8-sig")

    script = ScriptProcessor().create_draft_script("前500行样本", text)

    character_names = {character.name for character in script.characters}
    scene_names = {scene.name for scene in script.scenes}
    prop_names = {prop.name for prop in script.props}
    beats = script.story_analysis.scene_beats

    assert character_names == {"方奇", "璃光"}
    assert not character_names.intersection({"轻盈", "温顺", "永远", "那我", "但这", "方奇赶紧"})
    assert scene_names == {"门口", "房间", "地下室", "楼道"}
    assert not scene_names.intersection({"着门口", "昏暗房间"})
    assert prop_names == {"茶杯", "杯子", "蛋糕", "轮椅"}
    assert not prop_names.intersection({"端起茶杯", "接过杯子"})

    assert len(beats) == 8
    assert beats[0].chapter_order == 1
    assert beats[0].chapter_title == "第1章 结局07-快乐永恒"
    assert beats[0].scene_name == "房间"
    assert beats[3].chapter_order == 2
    assert beats[3].chapter_title == "第2章 结局05-禁锢之椅"
    assert beats[3].scene_name == "门口"
    assert beats[7].chapter_order == 3
    assert beats[7].chapter_title == "第3章 结局12-笼中挚爱"
    assert beats[7].scene_name == "地下室"
