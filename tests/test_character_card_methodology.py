"""T-B6: 角色卡三分离（persona/image/voice + 三锚点）模型测试。

边界原则：持久化 tolerant——老项目数据没有新字段必须照常加载；
内容规范（image.prompt 禁人名、必写族裔/年代/地域）warning 级不硬 fail。
（方法论来源 shuohao-skills Apache-2.0 / ai-short-drama MIT，只抄规范语义。）
"""
import json

from src.apps.comic_gen.models import (
    Character,
    ImageCard,
    PersonaProfile,
    Scene,
    VoiceCard,
    check_image_prompt_rules,
)


class TestTolerantPersistence:
    def test_old_project_data_without_new_fields_loads(self):
        """老项目 JSON 无新字段 → 全部解析为 None，绝不炸。"""
        old = Character.model_validate({"id": "c1", "name": "梁宸", "description": "主角"})
        assert old.persona_profile is None
        assert old.image_card is None
        assert old.voice_card is None
        assert old.visual_anchor is None
        assert old.performance_anchor is None
        assert old.voice_anchor is None

    def test_old_scene_data_loads(self):
        scene = Scene.model_validate({"id": "s1", "name": "桥边", "description": "夜"})
        assert scene.image_card is None
        assert scene.visual_anchor is None

    def test_full_card_round_trip(self):
        """全量三分离 dump → load 语义无损。"""
        c = Character(
            id="c2", name="沈雨晴", description="女主",
            persona_profile=PersonaProfile(gender="女", age_range="24", identity="店员"),
            image_card=ImageCard(prompt="p", negative_prompt="blurry", tags=["portrait"]),
            voice_card=VoiceCard(timbre="清亮", accent="南方口音"),
            visual_anchor="黑长发/围裙", performance_anchor="搓衣角", voice_anchor="清亮女声",
        )
        r = Character.model_validate(json.loads(c.model_dump_json()))
        assert r.persona_profile.identity == "店员"
        assert r.image_card.negative_prompt == "blurry"
        assert r.voice_card.accent == "南方口音"
        assert r.voice_anchor == "清亮女声"


class TestImagePromptRules:
    def test_empty_prompt_is_not_a_violation(self):
        """未填写 ≠ 违规（tolerant），空/None 不产生 warning。"""
        assert check_image_prompt_rules(None, "梁宸") == []
        assert check_image_prompt_rules("", "梁宸") == []
        assert check_image_prompt_rules("   ", None) == []

    def test_name_in_prompt_warns(self):
        """image.prompt 出现人名 → warning（用外观描述替代人名）。"""
        ws = check_image_prompt_rules("portrait of 梁宸 standing on a bridge", "梁宸")
        assert any("出现角色名" in w and "梁宸" in w for w in ws)

    def test_missing_anchors_warn_with_categories(self):
        """缺族裔/年代/地域 → warning 且指明缺哪几类。"""
        ws = check_image_prompt_rules("a young woman standing on a bridge at night", "沈雨晴")
        assert len(ws) == 1
        assert "缺少定位词" in ws[0]
        for cat in ("族裔", "年代", "地域"):
            assert cat in ws[0]

    def test_complete_prompt_passes_clean(self):
        good = "portrait of a young East Asian woman, 2020s, southern China city, night bridge"
        assert check_image_prompt_rules(good, "沈雨晴") == []

    def test_era_decade_pattern_matches_1920s(self):
        """英文年代可以用 1920s 这类数字写法。"""
        p = "an elderly Asian ferryman, 1920s, riverside village"
        assert check_image_prompt_rules(p, "老周") == []

    def test_validator_never_raises_on_bad_prompt(self):
        """校验器 warning 级：坏 prompt 照常构造实例（warning 走日志）。"""
        c = Character(id="c3", name="梁宸", description="x",
                      image_card=ImageCard(prompt="portrait of 梁宸"))
        assert c.image_card.prompt == "portrait of 梁宸"
