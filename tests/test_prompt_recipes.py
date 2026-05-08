from src.apps.comic_gen.models import Character, Prop, Scene
from src.apps.comic_gen.prompt_recipes import (
    build_style_guardrails,
    build_storyboard_continuity_hint,
    build_character_prompt,
    build_prop_prompt,
    build_scene_prompt,
)


def test_build_full_body_character_prompt_uses_clean_sheet_constraints():
    character = Character(
        id="char-1",
        name="Lin",
        description="Slim build, short silver hair, sharp eyes",
        clothing="Dark trench coat with red lining",
    )

    prompt = build_character_prompt(character, "full_body")

    assert "Full body character design sheet of Lin." in prompt
    assert "Standing neutral pose" in prompt
    assert "clean white background" in prompt
    assert "One character only" in prompt


def test_build_three_view_prompt_enforces_reference_consistency():
    character = Character(
        id="char-2",
        name="Asha",
        description="Curly hair and a brass shoulder guard",
    )

    prompt = build_character_prompt(character, "three_view", strict_reference=True)

    assert "Front view, side view, and back view in one sheet" in prompt
    assert "Strictly preserve the same face" in prompt
    assert "Consistent wardrobe details across every angle" in prompt


def test_build_expression_sheet_prompt_requires_board_level_consistency():
    character = Character(
        id="char-3",
        name="Mina",
        description="Straight black hair, calm eyes, slim build",
        clothing="Soft white shirt and dark trousers",
    )

    prompt = build_character_prompt(character, "expression_sheet", strict_reference=True)

    assert "Character expression reference sheet for Mina." in prompt
    assert "One 4K authoring board" in prompt
    assert "front/side/back mini views" in prompt
    assert "row of facial expressions" in prompt
    assert "Strictly preserve the same face" in prompt


def test_build_scene_prompt_includes_time_and_lighting():
    scene = Scene(
        id="scene-1",
        name="Old Harbor",
        description="Wet wooden piers, stacked cargo crates, distant fog",
        time_of_day="blue hour",
        lighting_mood="cold mist with sparse tungsten lamps",
    )

    prompt = build_scene_prompt(scene)

    assert "Scene concept art for Old Harbor." in prompt
    assert "Time of day: blue hour." in prompt
    assert "Lighting mood: cold mist with sparse tungsten lamps." in prompt
    assert "Establishing shot" in prompt


def test_build_prop_prompt_isolates_single_object():
    prop = Prop(
        id="prop-1",
        name="Signal Lantern",
        description="A dented brass lantern with frosted blue glass",
    )

    prompt = build_prop_prompt(prop)

    assert "Hero prop concept render of Signal Lantern." in prompt
    assert "Single object only, centered composition." in prompt
    assert "no hands, no people, no extra objects" in prompt


def test_build_style_guardrails_for_realistic_characters_blocks_cartoon_rendering():
    positive, negative = build_style_guardrails("写实真人电影感", asset_kind="character")

    assert "live-action human subject" in positive
    assert "realistic skin texture" in positive
    assert "anime" in negative
    assert "cartoon" in negative


def test_build_storyboard_continuity_hint_mentions_scene_and_neighbors():
    hint = build_storyboard_continuity_hint(
        scene_name="Old Harbor",
        previous_action="Lin turns toward the pier lights",
        next_action="Lin steps onto the wet dock",
    )

    assert "Old Harbor" in hint
    assert "Previous beat continuity anchor" in hint
    assert "Next beat continuity anchor" in hint
