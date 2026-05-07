from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.apps.comic_gen.models import Character, Prop, Scene, StoryboardFrame
from src.apps.comic_gen.pipeline import (
    ComicGenPipeline,
    _build_safe_storyboard_render_strategy,
    _get_asset_image_reference_url,
    _get_art_direction_reference_paths,
    _get_character_reference_url,
    _resolve_reference_path,
)


DEFAULT_FIXTURE_SLUG = "liuyi-that-day"
DEFAULT_FRAME_ORDERS = [2, 8, 15, 18]


def _repo_root() -> Path:
    return REPO_ROOT


def _read_manifest(fixture_slug: str) -> Dict[str, Any]:
    fixture_dirname = "六一那天" if fixture_slug in {DEFAULT_FIXTURE_SLUG, "六一那天"} else fixture_slug
    manifest_path = _repo_root() / "tests" / "fixtures" / "story_projects" / fixture_dirname / "project_manifest.json"
    with manifest_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _parse_frame_orders(value: str) -> List[int]:
    orders: List[int] = []
    for item in value.split(","):
        cleaned = item.strip()
        if not cleaned:
            continue
        orders.append(int(cleaned))
    return orders


def _model_to_dict(value: Any) -> Dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "dict"):
        return value.dict()
    return dict(value or {})


def _resolve_to_absolute_path(url: Optional[str]) -> Optional[str]:
    resolved = _resolve_reference_path(url)
    if not resolved:
        return None
    if resolved.startswith("http://") or resolved.startswith("https://"):
        return resolved
    if os.path.isabs(resolved):
        return str(Path(resolved).resolve())
    return str((_repo_root() / resolved).resolve())


def _path_status(path: Optional[str]) -> str:
    if not path:
        return "missing_reference"
    if path.startswith("http://") or path.startswith("https://"):
        return "remote_reference"
    return "ready" if Path(path).exists() else "missing_file"


def _asset_entry(role: str, asset: Character | Scene | Prop, source_url: Optional[str]) -> Dict[str, Any]:
    resolved_path = _resolve_to_absolute_path(source_url)
    entry: Dict[str, Any] = {
        "role": role,
        "asset_id": asset.id,
        "name": asset.name,
        "description": getattr(asset, "description", ""),
        "locked": bool(getattr(asset, "locked", False)),
        "selected_reference_url": source_url,
        "resolved_reference_path": resolved_path,
        "reference_status": _path_status(resolved_path),
    }
    if isinstance(asset, Character):
        entry["age"] = asset.age
        entry["clothing"] = asset.clothing
    if isinstance(asset, Scene):
        entry["time_of_day"] = asset.time_of_day
        entry["lighting_mood"] = asset.lighting_mood
    return entry


def _frame_lookup(frames: Iterable[StoryboardFrame]) -> Dict[int, StoryboardFrame]:
    lookup: Dict[int, StoryboardFrame] = {}
    for frame in frames:
        order = frame.story_beat_order
        if order is not None:
            lookup[int(order)] = frame
    return lookup


def _build_imagegen_prompt(frame: StoryboardFrame, composition_data: Dict[str, Any]) -> str:
    parts = [
        "使用 gpt-image2 生成单张 16:9 写实电影感静态分镜图。",
        "必须严格参考本帧 reference_paths 中的场景、人物、物品素材；不要引用不属于本帧的素材。",
        str(frame.image_prompt_cn or frame.image_prompt or "").strip(),
    ]
    quality_targets = composition_data.get("quality_targets") if isinstance(composition_data, dict) else None
    if quality_targets:
        parts.append("一致性要求：" + "；".join(str(item) for item in quality_targets))
    return "\n".join(part for part in parts if part)


def build_audit_pack(
    pipeline: ComicGenPipeline,
    fixture_slug: str,
    frame_orders: List[int],
    include_style_references: bool = False,
) -> Dict[str, Any]:
    manifest = _read_manifest(fixture_slug)
    script = pipeline.import_fixture_story_project(fixture_slug)
    frames_by_order = _frame_lookup(script.frames)
    scenes_by_id = {scene.id: scene for scene in script.scenes}
    characters_by_id = {character.id: character for character in script.characters}
    props_by_id = {prop.id: prop for prop in script.props}
    style_config = script.art_direction.style_config if script.art_direction else None
    style_reference_paths = [
        _resolve_to_absolute_path(path) for path in _get_art_direction_reference_paths(style_config)
    ] if include_style_references else []

    exported_frames: List[Dict[str, Any]] = []
    for order in frame_orders:
        frame = frames_by_order.get(order)
        if not frame:
            raise ValueError(f"Frame order not found: {order}")

        composition_data = dict(frame.composition_data or {})
        reference_assets: List[Dict[str, Any]] = []

        scene = scenes_by_id.get(frame.scene_id)
        if scene:
            reference_assets.append(_asset_entry("scene", scene, _get_asset_image_reference_url(scene)))

        for character_id in frame.character_ids or []:
            character = characters_by_id.get(character_id)
            if character:
                reference_assets.append(_asset_entry("character", character, _get_character_reference_url(character)))

        for prop_id in frame.prop_ids or []:
            prop = props_by_id.get(prop_id)
            if prop:
                reference_assets.append(_asset_entry("prop", prop, _get_asset_image_reference_url(prop)))

        reference_paths = [
            entry["resolved_reference_path"]
            for entry in reference_assets
            if entry["reference_status"] in {"ready", "remote_reference"}
        ]
        for style_path in style_reference_paths:
            if style_path and style_path not in reference_paths:
                reference_paths.append(style_path)

        recommended_render_strategy = _build_safe_storyboard_render_strategy(
            frame=frame,
            scene=scene,
            characters=script.characters,
            props=script.props,
            prompt=frame.image_prompt_cn or frame.image_prompt or "",
            ref_image_paths=reference_paths,
            model_name=script.model_settings.i2i_model,
        )

        exported_frames.append(
            {
                "frame_id": frame.id,
                "story_beat_order": frame.story_beat_order,
                "story_beat_title": frame.story_beat_title,
                "scene_id": frame.scene_id,
                "character_ids": list(frame.character_ids or []),
                "prop_ids": list(frame.prop_ids or []),
                "image_prompt": frame.image_prompt_cn or frame.image_prompt,
                "codex_imagegen_prompt": _build_imagegen_prompt(frame, composition_data),
                "composition_data": composition_data,
                "reference_policy": {
                    "use_current_frame_asset_refs_only": True,
                    "continuity_lock": False,
                    "include_style_references": include_style_references,
                    "include_video_audio_refs": False,
                },
                "recommended_render_strategy": recommended_render_strategy,
                "reference_assets": reference_assets,
                "codex_imagegen_reference_paths": reference_paths,
                "missing_reference_assets": [
                    {
                        "role": entry["role"],
                        "asset_id": entry["asset_id"],
                        "name": entry["name"],
                        "reference_status": entry["reference_status"],
                    }
                    for entry in reference_assets
                    if entry["reference_status"] not in {"ready", "remote_reference"}
                ],
            }
        )

    return {
        "package_type": "codex_imagegen_handoff_audit",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "fixture_slug": fixture_slug,
        "project_id": script.id,
        "project_title": script.title,
        "target_image_model": (
            manifest.get("model_settings", {}).get("openai_image_edit_model")
            or manifest.get("model_settings", {}).get("openai_image_model")
            or script.model_settings.i2i_model
        ),
        "scope": "静态分镜生图交接包；视频、剪辑、配音不在本包范围。",
        "asset_library_counts": {
            "characters": len(script.characters),
            "scenes": len(script.scenes),
            "props": len(script.props),
            "frames": len(script.frames),
        },
        "frame_orders": frame_orders,
        "frames": exported_frames,
    }


def _markdown_table_row(values: List[Any]) -> str:
    escaped = [str(value if value is not None else "").replace("\n", "<br>") for value in values]
    return "| " + " | ".join(escaped) + " |"


def render_markdown(pack: Dict[str, Any]) -> str:
    lines = [
        f"# {pack['project_title']} Codex 生图交接包 / 审计包",
        "",
        f"- 项目 ID：`{pack['project_id']}`",
        f"- 目标模型：`{pack['target_image_model']}`",
        f"- 范围：{pack['scope']}",
        (
            f"- 素材库规模：角色 {pack['asset_library_counts']['characters']}，"
            f"场景 {pack['asset_library_counts']['scenes']}，物品 {pack['asset_library_counts']['props']}，"
            f"分镜 {pack['asset_library_counts']['frames']}"
        ),
        "- 参考策略：只使用当前帧场景/人物/物品素材；连续性前后帧参考关闭；视频、音频引用关闭。",
        "",
    ]

    for frame in pack["frames"]:
        lines.extend(
            [
                f"## 镜头 {int(frame['story_beat_order']):02d}：{frame['story_beat_title']}",
                "",
                f"- Frame ID：`{frame['frame_id']}`",
                f"- Scene：`{frame['scene_id']}`",
                f"- Characters：`{', '.join(frame['character_ids']) or '无'}`",
                f"- Props：`{', '.join(frame['prop_ids']) or '无'}`",
                "",
                "### 静帧提示词",
                "",
                "```text",
                str(frame["image_prompt"] or "").strip(),
                "```",
                "",
                "### Codex imagegen 提示词",
                "",
                "```text",
                str(frame["codex_imagegen_prompt"] or "").strip(),
                "```",
                "",
                "### 实际参考图片路径",
                "",
            ]
        )
        if frame["codex_imagegen_reference_paths"]:
            for index, path in enumerate(frame["codex_imagegen_reference_paths"], start=1):
                lines.append(f"{index}. `{path}`")
        else:
            lines.append("无可用参考图片路径。")
        render_strategy = frame.get("recommended_render_strategy")
        if render_strategy:
            lines.extend(
                [
                    "",
                    "### 推荐生成策略",
                    "",
                    f"- 模式：`{render_strategy['mode']}`",
                    f"- 直接多参考图 edit：`{render_strategy['direct_multi_reference_edit_allowed']}`",
                    f"- 触发原因：`{', '.join(render_strategy['reason_codes'])}`",
                    f"- 基础阶段：`{render_strategy['base_stage']['model_mode']}` / `{render_strategy['base_stage']['reference_policy']}`",
                    f"- 暂不直连参考图数量：{render_strategy['omitted_reference_count']}",
                    "- 后续：基础构图通过后，再用单参考图局部 edit 分别校准人物身份、服装和道具。",
                ]
            )
        lines.extend(
            [
                "",
                "### 绑定资产明细",
                "",
                _markdown_table_row(["类型", "资产 ID", "名称", "状态", "实际路径"]),
                _markdown_table_row(["---", "---", "---", "---", "---"]),
            ]
        )
        for entry in frame["reference_assets"]:
            lines.append(
                _markdown_table_row(
                    [
                        entry["role"],
                        f"`{entry['asset_id']}`",
                        entry["name"],
                        entry["reference_status"],
                        f"`{entry['resolved_reference_path']}`" if entry["resolved_reference_path"] else "",
                    ]
                )
            )
        if frame["missing_reference_assets"]:
            lines.extend(["", "### 缺失参考图", ""])
            for missing in frame["missing_reference_assets"]:
                lines.append(f"- `{missing['asset_id']}` {missing['name']}：{missing['reference_status']}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def write_audit_pack(pack: Dict[str, Any], output_dir: Path) -> Dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "codex_imagegen_audit_frames_02_08_15_18.json"
    markdown_path = output_dir / "codex_imagegen_audit_frames_02_08_15_18.md"
    with json_path.open("w", encoding="utf-8") as handle:
        json.dump(pack, handle, ensure_ascii=False, indent=2)
    with markdown_path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(render_markdown(pack))
    return {
        "json": str(json_path.resolve()),
        "markdown": str(markdown_path.resolve()),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Export Codex gpt-image2 storyboard reference audit pack.")
    parser.add_argument("--fixture-slug", default=DEFAULT_FIXTURE_SLUG)
    parser.add_argument("--frames", default=",".join(str(order) for order in DEFAULT_FRAME_ORDERS))
    parser.add_argument(
        "--output-dir",
        default=str(_repo_root() / "output" / "codex_image_audit" / DEFAULT_FIXTURE_SLUG),
    )
    parser.add_argument("--include-style-references", action="store_true")
    args = parser.parse_args()

    os.chdir(_repo_root())
    pipeline = ComicGenPipeline()
    frame_orders = _parse_frame_orders(args.frames)
    pack = build_audit_pack(
        pipeline,
        fixture_slug=args.fixture_slug,
        frame_orders=frame_orders,
        include_style_references=args.include_style_references,
    )
    written = write_audit_pack(pack, Path(args.output_dir))
    print(json.dumps(written, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
