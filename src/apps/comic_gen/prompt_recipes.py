"""Reusable prompt recipes for image and video generation.

This module folds prompt-writing patterns distilled from multiple public
Seedance prompt libraries into a local, provider-agnostic recipe layer.
"""

from __future__ import annotations

from typing import Iterable, List, Sequence

from .models import Character, Prop, Scene

PHOTOREAL_MARKERS: Sequence[str] = (
    "写实",
    "真人",
    "实拍",
    "真实",
    "摄影",
    "photoreal",
    "photographic",
    "live-action",
    "cinematic",
    "realistic",
)

STYLIZED_MARKERS: Sequence[str] = (
    "动漫",
    "二次元",
    "插画",
    "卡通",
    "漫画",
    "anime",
    "cartoon",
    "illustration",
    "stylized",
    "cel shading",
)


def _clean_fragment(text: str | None) -> str:
    if not text:
        return ""
    return " ".join(text.replace("\n", " ").split()).strip(" ,.;")


def _sentence_parts(parts: Iterable[str | None]) -> str:
    cleaned = [_clean_fragment(part) for part in parts if _clean_fragment(part)]
    if not cleaned:
        return ""
    return ". ".join(cleaned) + "."


def infer_style_mode(*texts: str | None) -> str:
    merged = " ".join(_clean_fragment(text) for text in texts if _clean_fragment(text)).lower()
    if not merged:
        return "neutral"

    has_photoreal = any(marker.lower() in merged for marker in PHOTOREAL_MARKERS)
    has_stylized = any(marker.lower() in merged for marker in STYLIZED_MARKERS)

    if has_photoreal and not has_stylized:
        return "photoreal"
    if has_stylized and not has_photoreal:
        return "stylized"
    return "neutral"


def merge_negative_prompt(*parts: str | None) -> str:
    tokens: List[str] = []
    seen = set()

    for part in parts:
        if not part:
            continue
        for token in part.split(","):
            cleaned = _clean_fragment(token)
            key = cleaned.lower()
            if cleaned and key not in seen:
                seen.add(key)
                tokens.append(cleaned)

    return ", ".join(tokens)


def build_style_guardrails(*texts: str | None, asset_kind: str = "character") -> tuple[str, str]:
    style_mode = infer_style_mode(*texts)

    if style_mode == "photoreal":
        if asset_kind == "character":
            return (
                "live-action human subject, photorealistic facial structure, realistic skin texture, real-world anatomy, natural fabric response",
                "anime, cartoon, illustration, cel shading, chibi, doll-like face, exaggerated proportions",
            )
        return (
            "photorealistic lighting, grounded materials, believable lens response, cinematic production design",
            "anime, cartoon, illustration, cel shading, exaggerated proportions",
        )

    if style_mode == "stylized":
        return (
            "stylized illustration language, intentional shape design, cohesive art direction, clear silhouette rhythm",
            "photorealistic pores, live-action skin texture, uncanny realism",
        )

    if asset_kind == "character":
        return (
            "cohesive visual language, readable silhouette, grounded proportions, clear identity consistency",
            "",
        )

    return (
        "cohesive visual language, grounded materials, clear spatial logic",
        "",
    )


def build_storyboard_continuity_hint(
    *,
    scene_name: str | None = None,
    previous_action: str | None = None,
    next_action: str | None = None,
) -> str:
    parts: List[str] = [
        "Keep the same character identity, hairstyle, costume details, prop placement, and lighting logic as the surrounding frames",
        "Preserve the same scene geography and time-space continuity unless the shot explicitly changes location",
    ]
    if scene_name:
        parts.append(f"This frame belongs to the ongoing scene: {scene_name}")
    if previous_action:
        parts.append(f"Previous beat continuity anchor: {previous_action}")
    if next_action:
        parts.append(f"Next beat continuity anchor: {next_action}")
    return _sentence_parts(parts)


def _character_identity_bits(character: Character) -> List[str]:
    bits: List[str] = []
    if character.description:
        bits.append(character.description)
    if character.age:
        bits.append(f"Age impression: {character.age}")
    if character.gender:
        bits.append(f"Gender presentation: {character.gender}")
    if character.clothing:
        bits.append(f"Signature outfit: {character.clothing}")
    return bits


def build_character_prompt(
    character: Character,
    shot: str = "full_body",
    *,
    strict_reference: bool = False,
) -> str:
    identity = _character_identity_bits(character)
    shared_constraints = [
        "One character only",
        "clear anatomy",
        "clean silhouette",
        "sharp facial identity",
        "no text",
        "no watermark",
    ]

    if strict_reference:
        shared_constraints.insert(
            0,
            "Strictly preserve the same face, hairstyle, skin tone, body proportions, clothing silhouette, and signature accessories as the reference image",
        )

    if shot == "three_view":
        return _sentence_parts(
            [
                f"Character turnaround sheet for {character.name}",
                *identity,
                "Front view, side view, and back view in one sheet",
                "Full body standing pose with neutral expression",
                "Consistent wardrobe details across every angle",
                "studio reference sheet, clean white background, evenly lit",
                ", ".join(shared_constraints),
            ]
        )

    if shot == "headshot":
        return _sentence_parts(
            [
                f"Close-up character portrait of {character.name}",
                *identity,
                "Head and shoulders framing, looking toward camera, neutral expression",
                "high detail on eyes, skin texture, hairstyle, and signature accessories",
                "simple uncluttered background, portrait lighting",
                ", ".join(shared_constraints),
            ]
        )

    if shot == "expression_sheet":
        return _sentence_parts(
            [
                f"Character expression reference sheet for {character.name}",
                *identity,
                "One 4K authoring board with a primary neutral full-body anchor, front/side/back mini views, and a row of facial expressions",
                "Include neutral, gentle smile, worried, holding back tears, determined, and softly relieved expressions",
                "Keep the same face, hairstyle, age impression, body proportions, clothing silhouette, and signature accessories across every panel",
                "clean studio reference layout, simple background, evenly lit",
                ", ".join(shared_constraints),
            ]
        )

    return _sentence_parts(
        [
            f"Full body character design sheet of {character.name}",
            *identity,
            "Standing neutral pose, looking toward camera",
            "full outfit visible from head to toe",
            "isolated studio setup, clean white background, no scenery, no props",
            ", ".join(shared_constraints),
        ]
    )


def build_scene_prompt(scene: Scene) -> str:
    time_of_day = f"Time of day: {scene.time_of_day}" if scene.time_of_day else ""
    lighting_mood = f"Lighting mood: {scene.lighting_mood}" if scene.lighting_mood else ""
    return _sentence_parts(
        [
            f"Scene concept art for {scene.name}",
            scene.description,
            time_of_day,
            lighting_mood,
            "Establishing shot with clear foreground, midground, and background separation",
            "environment only unless the scene description explicitly requires figures",
            "coherent architecture, grounded materials, cinematic depth, no text, no watermark",
        ]
    )


def build_prop_prompt(prop: Prop) -> str:
    return _sentence_parts(
        [
            f"Hero prop concept render of {prop.name}",
            prop.description,
            "Single object only, centered composition",
            "isolated studio background, no hands, no people, no extra objects",
            "clear material definition, surface detail, readable silhouette, no text, no watermark",
        ]
    )


STORYBOARD_POLISH_RECIPE = """
Use this image-prompt structure in order:
1. Asset grounding: identify which asset appears as Image X and keep identity / design consistent.
2. Core staging: who is where, in what pose or action, and what the viewer should notice first.
3. Camera and composition: shot size, angle, depth layering, framing, foreground/background balance.
4. Light and atmosphere: time of day, color temperature, weather, haze, practical lights, mood.
5. Texture and fidelity: fabric, skin, materials, environment details that are visible in a single still frame.
6. Guardrails: no invented plot twists, no extra characters, no contradictory actions, no style keyword dumping.
Keep the result concise, visual, and directly usable for image generation.
""".strip()


VIDEO_POLISH_RECIPE = """
Use a compact Seedance-friendly structure:
1. Subject and opening frame state.
2. Environment and mood.
3. Motion timeline: beat 1 -> beat 2 -> beat 3.
4. Camera language: shot size, angle, movement path, and movement speed.
5. Light / texture / style cues that will actually be visible in motion.
6. Consistency constraints: keep the same identity, background logic, and physical motion continuity.
Prefer one dominant action line, explicit verbs, and precise tempo words such as slowly, subtly, sharply, or abruptly.
Avoid long adjective piles, contradictory motion, or changing the scene into a different location unless the draft explicitly asks for it.
""".strip()


R2V_POLISH_RECIPE = """
Use a reference-video-friendly structure:
1. Scene setup and emotional baseline.
2. Cast blocking with character1 / character2 / character3 only.
3. Action timeline: each character's movement, gaze, gesture, and interaction order.
4. Dialogue, if present, in the format characterN says: "..."
5. Camera behavior and pacing.
6. Continuity constraints: preserve the same identity, costume, screen direction, and interaction logic from the reference clips.
Keep the wording direct and production-ready, without extra explanation.
""".strip()
