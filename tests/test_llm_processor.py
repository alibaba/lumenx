from src.apps.comic_gen.llm import ScriptProcessor
from src.apps.comic_gen.models import Character, Prop, Scene


class _FakeLLMAdapter:
    last_messages = None
    last_response_format = None

    def __init__(self):
        self.is_configured = True

    def chat(self, messages, model=None, response_format=None):
        _FakeLLMAdapter.last_messages = messages
        _FakeLLMAdapter.last_response_format = response_format
        return """
        {
          "characters": [
            {
              "id": "char_001",
              "name": "林夏",
              "description": "短发，身形清瘦，手里拿着手电",
              "age": "28",
              "gender": "女",
              "clothing": "深色防水外套",
              "visual_weight": 5
            }
          ],
          "scenes": [
            {
              "id": "scene_001",
              "name": "废弃仓库",
              "description": "昏暗潮湿的旧仓库，散落木箱和断裂电线",
              "visual_weight": 4
            }
          ],
          "props": [
            {
              "id": "prop_001",
              "name": "红色纸鹤",
              "description": "被雨水打湿的红色纸鹤"
            }
          ]
        }
        """


class _EmptyLLMAdapter:
    def __init__(self):
        self.is_configured = True

    def chat(self, messages, model=None, response_format=None):
        return """
        {
          "characters": [],
          "scenes": [],
          "props": []
        }
        """


class _PartialLLMAdapter:
    def __init__(self):
        self.is_configured = True

    def chat(self, messages, model=None, response_format=None):
        return """
        {
          "characters": [
            {
              "id": "char_001",
              "name": "林夏",
              "description": "短发，身形清瘦，行动谨慎",
              "age": "28",
              "gender": "女",
              "clothing": "深色风衣",
              "visual_weight": 5
            }
          ],
          "scenes": [],
          "props": []
        }
        """


class _BrokenJsonLLMAdapter:
    def __init__(self):
        self.is_configured = True

    def chat(self, messages, model=None, response_format=None):
        return '{"characters": ['


def test_parse_novel_requests_json_object_and_structured_messages(monkeypatch):
    monkeypatch.setattr("src.apps.comic_gen.llm_adapter.LLMAdapter", _FakeLLMAdapter)

    processor = ScriptProcessor()
    script = processor.parse_novel(
        "测试项目",
        "林夏走进废弃仓库，看见地上的红色纸鹤。",
    )

    assert len(script.characters) == 1
    assert len(script.scenes) == 1
    assert len(script.props) == 1
    assert _FakeLLMAdapter.last_response_format == {"type": "json_object"}
    assert _FakeLLMAdapter.last_messages[0]["role"] == "system"
    assert "尽力提取" in _FakeLLMAdapter.last_messages[1]["content"]
    assert script.story_analysis.summary
    assert len(script.story_analysis.scene_beats) >= 1
    assert script.story_analysis.scene_beats[0].character_names == ["林夏"]
    assert script.story_analysis.character_presence[0].character_name == "林夏"
    assert script.generation_metadata["novel_parse"]["source"] == "llm"
    assert script.generation_metadata["novel_parse"]["degraded"] is False


def test_parse_novel_falls_back_when_llm_returns_empty_entities(monkeypatch):
    monkeypatch.setattr("src.apps.comic_gen.llm_adapter.LLMAdapter", _EmptyLLMAdapter)

    processor = ScriptProcessor()
    script = processor.parse_novel(
        "空实体回退",
        "林夏穿着深色风衣走进废弃仓库，看见地上的红色纸鹤。门口的守卫正盯着她手里的手机。",
    )

    character_names = {character.name for character in script.characters}
    scene_names = {scene.name for scene in script.scenes}
    prop_names = {prop.name for prop in script.props}

    assert "林夏" in character_names
    assert any("仓库" in name for name in scene_names)
    assert any("纸鹤" in name for name in prop_names)
    assert any("手机" in name for name in prop_names)
    assert script.story_analysis.summary
    assert len(script.story_analysis.scene_beats) >= 1
    assert any("林夏" in beat.character_names for beat in script.story_analysis.scene_beats)
    assert any(entry.character_name == "林夏" for entry in script.story_analysis.character_presence)
    novel_meta = script.generation_metadata["novel_parse"]
    assert novel_meta["source"] == "llm"
    assert novel_meta["degraded"] is True
    assert any(item.startswith("characters=") for item in novel_meta["filled_categories"])
    assert any(item.startswith("scenes=") for item in novel_meta["filled_categories"])
    assert any(item.startswith("props=") for item in novel_meta["filled_categories"])


def test_parse_novel_keeps_llm_characters_and_backfills_missing_categories(monkeypatch):
    monkeypatch.setattr("src.apps.comic_gen.llm_adapter.LLMAdapter", _PartialLLMAdapter)

    processor = ScriptProcessor()
    script = processor.parse_novel(
        "按类补齐",
        "林夏穿着深色风衣走进废弃仓库，看见地上的红色纸鹤。",
    )

    assert script.characters[0].name == "林夏"
    assert script.characters[0].clothing == "深色风衣"
    assert any("仓库" in scene.name for scene in script.scenes)
    assert any("纸鹤" in prop.name for prop in script.props)
    assert len(script.story_analysis.scene_beats) >= 1
    assert any("纸鹤" in point for point in script.story_analysis.plot_points)
    novel_meta = script.generation_metadata["novel_parse"]
    assert novel_meta["source"] == "llm"
    assert novel_meta["degraded"] is True
    assert set(novel_meta["filled_categories"]) == {"scenes=1", "props=1"}


def test_parse_novel_uses_fallback_on_invalid_json(monkeypatch):
    monkeypatch.setattr("src.apps.comic_gen.llm_adapter.LLMAdapter", _BrokenJsonLLMAdapter)

    processor = ScriptProcessor()
    script = processor.parse_novel(
        "坏 JSON 回退",
        "叶墨在昏暗的卧室里被疯狂震动的手机惊醒。",
    )

    assert any(character.name == "叶墨" for character in script.characters)
    assert any("卧室" in scene.name for scene in script.scenes)
    assert any("手机" in prop.name for prop in script.props)
    assert any("卧室" in beat.title or "卧室" in (beat.scene_name or "") for beat in script.story_analysis.scene_beats)
    novel_meta = script.generation_metadata["novel_parse"]
    assert novel_meta["source"] == "heuristic_fallback"
    assert novel_meta["degraded"] is True
    assert "JSON" in novel_meta["reason"]


def test_parse_novel_fallback_handles_dense_chapter_novel(monkeypatch):
    monkeypatch.setattr("src.apps.comic_gen.llm_adapter.LLMAdapter", _EmptyLLMAdapter)

    processor = ScriptProcessor()
    text = """
    第1章 合租条例

    方奇刚回到客厅，桌上的手机突然震动。璃光端着茶杯从厨房出来，歪头看着他。
    “今晚不要出门。”她轻声说。
    林小悠在沙发后冒头：“审查室那边刚来通知，U盘不能外带。”

    第2章 夜审

    凌晨的审查室冷得像冰窖。安怜把电击器放在金属桌边，示意方奇坐下。
    璃光把项链扣回脖颈，站在门口看着他。
    """.strip()

    script = processor.parse_novel("长篇章节回退", text)

    character_names = {character.name for character in script.characters}
    scene_names = {scene.name for scene in script.scenes}
    prop_names = {prop.name for prop in script.props}

    assert {"方奇", "璃光", "林小悠", "安怜"}.issubset(character_names)
    assert {"客厅", "厨房", "审查室"}.issubset(scene_names)
    assert {"手机", "茶杯", "U盘", "电击器", "项链"}.issubset(prop_names)
    assert len(script.story_analysis.scene_beats) == 2
    assert script.story_analysis.scene_beats[0].chapter_order == 1
    assert script.story_analysis.scene_beats[0].chapter_title == "第1章 合租条例"
    assert "方奇" in script.story_analysis.scene_beats[0].character_names
    assert script.story_analysis.scene_beats[1].scene_name == "审查室"


def test_fallback_character_aliases_merge_named_and_descriptor_forms():
    processor = ScriptProcessor()
    text = """
    璃光把茶杯放到桌上，银发少女安静地看向方奇。
    “别出去。”少女轻声说。
    方奇盯着璃光的银发，最终没有再回嘴。
    """.strip()

    script = processor.create_draft_script("角色别名合并", text)
    characters = {character.name: character for character in script.characters}

    assert "璃光" in characters
    assert "方奇" in characters
    assert "银发少女" not in characters
    assert "少女" not in characters
    assert "银发少女" in set(characters["璃光"].aliases)
    assert any("璃光" in beat.character_names for beat in script.story_analysis.scene_beats)


def test_fallback_character_filter_removes_state_words():
    processor = ScriptProcessor()
    text = """
    方奇直勾勾地看着门口，平静地开口。
    璃光歪了歪头，把茶杯轻轻推到他手边。
    """.strip()

    script = processor.create_draft_script("角色去噪", text)
    character_names = {character.name for character in script.characters}

    assert "方奇" in character_names
    assert "璃光" in character_names
    assert {"直勾勾", "平静", "歪了"}.isdisjoint(character_names)


def test_fallback_character_filters_sample_noise_words():
    processor = ScriptProcessor()
    text = """
    【通关游戏，进入真正的完美结局后，便可离开这个世界。】
    璃光微微倾身，把茶杯推到方奇手边。
    方奇看着璃光，忽然听见一声脆响。
    他此时身体僵硬，嘴角勉强勾起。
    “不是，你干嘛啊？”
    他惨叫着摔倒在地，谁腿被打断不疼啊。
    而此时笑得温柔可人的银发少女仍站在门口。
    """.strip()

    script = processor.create_draft_script("500行样本角色去噪", text)
    characters = {character.name: character for character in script.characters}
    character_names = set(characters)

    assert {"方奇", "璃光"}.issubset(character_names)
    assert {"此时", "一声", "游戏", "干嘛", "勾起", "谁腿被", "着摔倒在"}.isdisjoint(character_names)
    assert "银发少女" not in character_names


def test_fallback_character_alias_ignores_narrative_fragment_near_alias():
    processor = ScriptProcessor()
    text = """
    方奇呆呆地看着她。
    璃光微微倾身，将茶杯又往前推了半寸。
    而此时笑得温柔可人的银发少女仍盯着方奇。
    """.strip()

    script = processor.create_draft_script("别名不挂叙事碎片", text)
    characters = {character.name: character for character in script.characters}

    assert "璃光" in characters
    assert "此时" not in characters
    assert "银发少女" not in characters


def test_fallback_scene_normalization_strips_descriptive_prefixes():
    processor = ScriptProcessor()
    text = """
    方奇冲向着门口，伸手去抓门把手。
    璃光站在昏暗房间里，安静地看着他。
    """.strip()

    entities = processor._fallback_extract_entities(text)
    scene_names = {scene["name"] for scene in entities["scenes"]}

    assert {"门口", "房间"}.issubset(scene_names)
    assert "着门口" not in scene_names
    assert "昏暗房间" not in scene_names


def test_fallback_prop_normalization_strips_action_prefixes():
    processor = ScriptProcessor()
    text = """
    璃光端起茶杯递到方奇面前。
    方奇接过杯子，仍旧没有放松警惕。
    """.strip()

    entities = processor._fallback_extract_entities(text)
    prop_names = {prop["name"] for prop in entities["props"]}

    assert {"茶杯", "杯子"}.issubset(prop_names)
    assert "端起茶杯" not in prop_names
    assert "接过杯子" not in prop_names


def test_create_draft_script_uses_fallback_entities_for_long_form_text():
    processor = ScriptProcessor()
    text = """
    第1章 便利店

    林夏推开便利店的玻璃门，周沉已经站在货架尽头，手里捏着录音笔。
    她压低声音：“纸鹤是你留下的吗？”

    第2章 天台

    夜风掀起两人的衣角，周沉把湿透的纸鹤放在护栏上。
    """.strip()

    script = processor.create_draft_script("草稿项目", text)

    assert {character.name for character in script.characters} >= {"林夏", "周沉"}
    assert {scene.name for scene in script.scenes} >= {"便利店", "天台"}
    assert {prop.name for prop in script.props} >= {"录音笔", "纸鹤"}
    assert len(script.story_analysis.scene_beats) == 2
    assert script.story_analysis.scene_beats[0].chapter_title == "第1章 便利店"
    novel_meta = script.generation_metadata["novel_parse"]
    assert novel_meta["source"] == "heuristic_draft"
    assert novel_meta["degraded"] is True


def test_build_story_analysis_keeps_line_dense_long_text_in_reasonable_beat_range():
    processor = ScriptProcessor()
    characters = [
        Character(id="char-1", name="方奇", description="神经紧绷的男主"),
        Character(id="char-2", name="璃光", description="银发机械妻"),
    ]
    scenes = [
        Scene(id="scene-1", name="客厅", description="狭小但整洁的出租屋客厅"),
        Scene(id="scene-2", name="厨房", description="灯光偏冷的开放式厨房"),
    ]
    lines = []
    for chapter in range(1, 4):
        lines.append(f"第{chapter}章 夜巡{chapter}")
        lines.append("")
        for line_no in range(1, 71):
            if line_no % 25 == 0:
                lines.append("与此同时，璃光从厨房走到客厅门口，继续盯着方奇。")
            else:
                lines.append(f"方奇在客厅核对第{chapter}-{line_no}份记录，璃光在厨房回应他的追问。")
        lines.append("")

    analysis = processor.build_story_analysis("\n".join(lines).strip(), characters, scenes, [])

    assert len(analysis.scene_beats) <= 18
    assert analysis.scene_beats[0].chapter_title == "第1章 夜巡1"
    assert any(beat.scene_name in {"客厅", "厨房"} for beat in analysis.scene_beats)


def test_build_story_analysis_supports_urban_long_form_and_relationships():
    processor = ScriptProcessor()
    characters = [
        Character(id="char-1", name="林夏", description="短发，深色风衣"),
        Character(id="char-2", name="周沉", description="黑色夹克，神情克制"),
    ]
    scenes = [
        Scene(id="scene-1", name="便利店", description="凌晨仍亮着灯的街角便利店"),
        Scene(id="scene-2", name="天台", description="高楼夜风猎猎的旧天台"),
    ]
    props = [
        Prop(id="prop-1", name="录音笔", description="银色录音笔"),
        Prop(id="prop-2", name="纸鹤", description="沾着雨水的纸鹤"),
    ]
    text = """
    第1场：便利店
    林夏推开便利店的玻璃门，冷风一起灌进来。周沉站在货架尽头，盯着她手里的录音笔。
    林夏低声说：“别再绕了，纸鹤是不是你放的？”

    第2场：天台
    夜风卷起两人的衣角，周沉把那只湿透的纸鹤放在护栏上。林夏逼近一步，录音笔的红灯还亮着。
    周沉皱眉：“真相一旦说出来，你也回不去了。”
    """.strip()

    analysis = processor.build_story_analysis(text, characters, scenes, props)

    assert len(analysis.scene_beats) == 2
    assert analysis.scene_beats[0].action_summary
    assert "纸鹤" in analysis.scene_beats[0].dialogue_excerpt
    assert analysis.scene_beats[1].storyboard_goal
    assert analysis.character_relationships
    relationship = analysis.character_relationships[0]
    assert {relationship.source_character_name, relationship.target_character_name} == {"林夏", "周沉"}
    assert relationship.co_scene_count == 2


def test_build_story_analysis_handles_costume_drama_sample():
    processor = ScriptProcessor()
    characters = [
        Character(id="char-1", name="沈清辞", description="素色披风，眉眼冷静"),
        Character(id="char-2", name="陆砚", description="玄色长袍，神情沉稳"),
    ]
    scenes = [Scene(id="scene-1", name="沈府后院", description="回廊与月洞门相连的后院")]
    props = [Prop(id="prop-1", name="灯笼", description="旧式宫灯")]
    text = """
    第1场：沈府后院
    沈清辞提着灯笼穿过回廊，陆砚一直守在月洞门外，没有出声。
    “你还敢来？”她把灯笼抬高，光正好照在陆砚肩头。
    """.strip()

    analysis = processor.build_story_analysis(text, characters, scenes, props)

    assert len(analysis.scene_beats) == 1
    beat = analysis.scene_beats[0]
    assert beat.scene_name == "沈府后院"
    assert "灯笼" in beat.action_summary
    assert "你还敢来" in beat.dialogue_excerpt
    assert beat.storyboard_goal


def test_build_story_analysis_handles_dialogue_script_lines():
    processor = ScriptProcessor()
    characters = [
        Character(id="char-1", name="林夏", description="短发，眼神锋利"),
        Character(id="char-2", name="周沉", description="沉默克制"),
    ]
    scenes = [Scene(id="scene-1", name="审讯室", description="冷白灯下的狭小审讯室")]
    props = [Prop(id="prop-1", name="录音笔", description="桌上的银色录音笔")]
    text = """
    1-1 审讯室 [夜] [内]
    人物：林夏，周沉
    △ 日光灯轻微闪烁，林夏把录音笔推到桌面中央。
    林夏（冷静）：你昨晚为什么回仓库？
    周沉（克制）：因为有人比你更早拿走了纸鹤。
    """.strip()

    analysis = processor.build_story_analysis(text, characters, scenes, props)

    assert len(analysis.scene_beats) == 1
    beat = analysis.scene_beats[0]
    assert "录音笔" in beat.action_summary
    assert "林夏" in beat.dialogue_excerpt
    assert "周沉" in beat.dialogue_excerpt
    assert beat.storyboard_goal
