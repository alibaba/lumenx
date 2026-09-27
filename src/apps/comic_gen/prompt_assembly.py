"""
Storyboard Schema v2 — Prompt Assembly & Dialogue-TTS Sync.

Pure functions with no side effects (no I/O, no pipeline/api imports).
"""
from typing import Dict, List, Optional, Tuple

from .models import (
    CameraMovementData,
    Character,
    Scene,
    StoryboardFrame,
)


MOVEMENT_MAP = {
    "static": "固定机位",
    "push_in": "推镜头(Dolly In)",
    "pull_out": "拉镜头(Dolly Out)",
    "pan_left": "左摇(Pan Left)",
    "pan_right": "右摇(Pan Right)",
    "tilt_up": "上摇(Tilt Up)",
    "tilt_down": "下摇(Tilt Down)",
    "orbit": "环绕(Orbit)",
    "follow": "跟随(Follow)",
    "crane_up": "升镜(Crane Up)",
    "crane_down": "降镜(Crane Down)",
    "handheld": "手持(Handheld)",
    "zoom_in": "变焦推(Zoom In)",
    "zoom_out": "变焦拉(Zoom Out)",
}

SPEED_MAP = {"slow": "缓慢", "normal": "", "fast": "快速"}


def _movement_type_to_text(
    primary: str,
    secondary: Optional[str],
    speed: str,
) -> str:
    speed_prefix = SPEED_MAP.get(speed, "")
    primary_text = f"{speed_prefix}{MOVEMENT_MAP.get(primary, primary)}"
    if secondary:
        secondary_text = MOVEMENT_MAP.get(secondary, secondary)
        return f"{primary_text}，同时{secondary_text}"
    return primary_text


def _get_character_appearance_keywords(
    character_ids: List[str],
    characters: List[Character],
) -> str:
    parts = []
    for cid in character_ids:
        char = next((c for c in characters if c.id == cid), None)
        if not char:
            continue
        desc_parts = [char.name]
        if char.age:
            desc_parts.append(char.age)
        if char.gender:
            desc_parts.append(char.gender)
        if char.clothing:
            desc_parts.append(char.clothing)
        elif char.description:
            snippet = char.description[:60]
            desc_parts.append(snippet)
        parts.append("，".join(desc_parts))
    return "；".join(parts)


def inject_reference_tags(
    text: str,
    frame: "StoryboardFrame",
    characters: List[Character],
    scenes: Optional[List[Scene]] = None,
) -> str:
    """Inject [characterN:name] tags into visual description.

    HappyHorse R2V uses `characterN` as a generic reference-image slot
    identifier — it does NOT distinguish characters from scenes/props.
    character1 = first ref image, character2 = second, etc.

    Numbering order: characters first (by frame.character_ids order),
    then scene (if present). All use [characterN:name] format.
    """
    import re

    if not text:
        return text

    # Skip if text already contains asset tags
    if re.search(r"\[character\d+:", text):
        return text

    # Global slot counter — characters first, then scene
    slot = 1

    # Inject character tags — numbered by frame.character_ids order
    for cid in frame.character_ids:
        char = next((c for c in characters if c.id == cid), None)
        if not char or not char.name:
            continue
        tag = f"{char.name}[character{slot}:{char.name}]"
        pos = text.find(char.name)
        if pos >= 0:
            text = text[:pos] + tag + text[pos + len(char.name):]
            slot += 1

    # Inject scene tag — continues the global numbering
    if scenes and frame.scene_id:
        scene = next((s for s in scenes if s.id == frame.scene_id), None)
        if scene and scene.name:
            pos = text.find(scene.name)
            if pos >= 0:
                tag = f"{scene.name}[character{slot}:{scene.name}]"
                text = text[:pos] + tag + text[pos + len(scene.name):]

    return text


def assemble_prompt(
    frame: StoryboardFrame,
    characters: List[Character],
) -> str:
    """Assemble the final generation prompt from structured fields + visual description.

    Priority: subject/action first → scene/lighting → camera → style/constraints.
    """
    parts: List[str] = []

    # 1. Visual description (core narrative)
    if frame.visual_description:
        parts.append(frame.visual_description)

    # 2. Lighting supplement
    if frame.lighting and frame.lighting.description:
        if not frame.visual_description or frame.lighting.description not in frame.visual_description:
            parts.append(frame.lighting.description)

    # 3. Camera movement
    if frame.camera_movement_structured:
        cm: CameraMovementData = frame.camera_movement_structured
        movement_text = cm.description or _movement_type_to_text(
            cm.primary, cm.secondary, cm.speed
        )
        parts.append(movement_text)

    # 4. Shot size + angle (only if not already covered)
    joined = " ".join(parts)
    angle_shot = []
    if frame.shot_size and frame.shot_size not in joined:
        angle_shot.append(frame.shot_size)
    if frame.camera_angle and frame.camera_angle not in joined:
        angle_shot.append(frame.camera_angle)
    if angle_shot:
        parts.append("，".join(angle_shot))

    # 5. Character appearance keywords
    char_desc = _get_character_appearance_keywords(frame.character_ids, characters)
    if char_desc:
        parts.append(f"角色：{char_desc}")

    if not parts:
        return frame.action_description or ""

    assembled = "。".join(p.rstrip("。，.") for p in parts if p) + "。"
    return assembled


def enrich_prompt_with_dialogue(
    prompt: str,
    frame: "StoryboardFrame",
) -> str:
    """Append a natural-language speaking cue to the video prompt.

    Video models (e.g. HappyHorse) need explicit lip/mouth action in the
    visual description to generate mouth movement.  We derive a short
    Chinese sentence from the frame's dialogue and weave it into the tail
    of the prompt so it reads as part of the visual narrative — NOT as a
    separate labelled metadata section.

    Returns the original prompt unmodified when there is no dialogue.
    """
    line = None
    speaker = None
    emotion = None

    if frame.dialogue_structured and frame.dialogue_structured.line:
        line = frame.dialogue_structured.line.strip()
        speaker = frame.dialogue_structured.speaker
        emotion = frame.dialogue_structured.emotion
    elif frame.dialogue:
        line = frame.dialogue.strip()
        speaker = frame.speaker

    if not line:
        return prompt

    # Build a visual speaking cue
    subject = speaker or "角色"
    emotion_hint = f"，表情{emotion}" if emotion else ""
    cue = f"{subject}张嘴说话{emotion_hint}，台词：「{line}」"

    # Append to prompt with separator
    clean = prompt.rstrip("。，. ")
    return f"{clean}。{cue}"


def sync_dialogue_to_tts(frame: StoryboardFrame) -> None:
    """Sync dialogue_structured emotion/delivery into dialogue_instructions,
    and keep legacy dialogue/speaker fields in sync."""
    if not frame.dialogue_structured:
        return

    ds = frame.dialogue_structured
    # Sync legacy fields
    frame.dialogue = ds.line
    frame.speaker = ds.speaker

    # Build TTS instructions from emotion + delivery
    instr_parts = []
    if ds.emotion:
        instr_parts.append(f"情绪：{ds.emotion}")
    if ds.delivery:
        instr_parts.append(f"演绎：{ds.delivery}")

    if instr_parts:
        frame.dialogue_instructions = "；".join(instr_parts)


def assemble_r2v_prompt_with_locks(
    base_prompt: str,
    references: List["ReferenceLock"],
) -> "Tuple[str, Dict[str, str]]":
    """R2V prompt 组装路径：基础 prompt + Seedance 锁定行块 + @图片N 别名表。

    委托 consistency.py（五分型锁定行 / 别名系统, T-V2 SS3.1#2 重写实现）。
    返回 (prompt, alias_map)；alias_map 随 task 持久化便于审计（上游职责）。
    """
    from .consistency import assemble_r2v_prompt

    return assemble_r2v_prompt(base_prompt, references)
