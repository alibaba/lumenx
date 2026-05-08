from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.utils.image_payload_budget import (  # noqa: E402
    DEFAULT_CODEX_IMAGEGEN_HANDOFF_MAX_REFERENCE_BYTES,
    DEFAULT_CODEX_IMAGEGEN_JPEG_QUALITY,
    DEFAULT_CODEX_IMAGEGEN_MAX_SIDE,
    DEFAULT_CODEX_IMAGEGEN_MIN_JPEG_QUALITY,
    DEFAULT_CODEX_IMAGEGEN_MIN_SIDE,
    prepare_image_references_for_payload,
)
from src.utils.codex_imagegen_handoff import (  # noqa: E402
    recommend_codex_imagegen_mode_from_reference_items,
    select_codex_handoff_mode,
)

LIUYI_V2_FRAME_17_REFERENCES = [
    "tests/fixtures/story_projects/六一那天_v2/references/scenes/"
    "liuyi_scene_2026_ward_v2/liuyi_scene_2026_ward_v2_childrens_day_daylight.png",
    "tests/fixtures/story_projects/六一那天_v2/references/characters/"
    "liuyi_char_xiaoqi_adult_v2/liuyi_char_xiaoqi_adult_v2_full_body.png",
    "tests/fixtures/story_projects/六一那天_v2/references/characters/"
    "liuyi_char_boy_v2/liuyi_char_boy_v2_full_body.png",
    "tests/fixtures/story_projects/六一那天_v2/references/characters/"
    "liuyi_char_boy_father_v2/liuyi_char_boy_father_v2_full_body.png",
    "tests/fixtures/story_projects/六一那天_v2/references/props/"
    "liuyi_prop_white_bear_v2/liuyi_prop_white_bear_v2_usage_view.png",
    "tests/fixtures/story_projects/六一那天_v2/references/props/"
    "liuyi_prop_childrens_day_balloons_v2/liuyi_prop_childrens_day_balloons_v2_usage_view.png",
]

REFERENCE_PRESETS: Dict[str, List[str]] = {
    "liuyi-v2-frame-17": LIUYI_V2_FRAME_17_REFERENCES,
}

SAFE_REFS_ONLY_MODE = "safe_refs_only"
TWO_STAGE_HIGH_CONSISTENCY_MODE = "two_stage_high_consistency"
STAGE_1_IDENTITY = "stage_1_identity"
STAGE_2_SCENE_LIGHT = "stage_2_scene_light"
STAGE_1_RESULT_NAME = "stage_1_identity_result.png"


def _repo_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return REPO_ROOT / path


def _safe_path_component(value: str, fallback: str = "frame") -> str:
    cleaned = "".join(
        char if char.isalnum() or char in {"-", "_"} else "_"
        for char in str(value or "").strip()
    ).strip("_")
    return cleaned or fallback


def _load_frame_spec(path_value: str) -> Dict[str, Any]:
    if not path_value:
        return {}
    path = _repo_path(path_value)
    if not path.exists():
        raise FileNotFoundError(f"Frame handoff spec was not found: {path}")
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError(f"Frame handoff spec must be a JSON object: {path}")
    data["_spec_path"] = str(path.resolve())
    return data


def _extract_spec_references(spec: Dict[str, Any]) -> List[str]:
    references = spec.get("references") or spec.get("reference_images") or []
    if not isinstance(references, list):
        raise ValueError("Frame handoff spec references must be a list.")
    normalized: List[str] = []
    for item in references:
        if isinstance(item, str):
            normalized.append(item)
        elif isinstance(item, dict) and isinstance(item.get("path"), str):
            normalized.append(item["path"])
    return normalized


def _extract_spec_reference_items(spec: Dict[str, Any]) -> List[Dict[str, Any]]:
    references = spec.get("references") or spec.get("reference_images") or []
    if not isinstance(references, list):
        raise ValueError("Frame handoff spec references must be a list.")
    normalized: List[Dict[str, Any]] = []
    for item in references:
        if isinstance(item, str):
            normalized.append({"path": item, "role": _infer_reference_role(item)})
        elif isinstance(item, dict) and isinstance(item.get("path"), str):
            entry = dict(item)
            entry["role"] = str(entry.get("role") or _infer_reference_role(entry["path"]))
            normalized.append(entry)
    return normalized


def _infer_reference_role(path_value: str) -> str:
    normalized = str(path_value).replace("\\", "/").lower()
    if "/characters/" in normalized:
        return "character"
    if "/props/" in normalized:
        return "prop"
    if "/scenes/" in normalized:
        return "scene"
    if "/style/" in normalized:
        return "style"
    return "reference"


def _dedupe_reference_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    deduped: List[Dict[str, Any]] = []
    seen = set()
    for item in items:
        path = str(item.get("path") or "").strip()
        if not path or path in seen:
            continue
        seen.add(path)
        deduped.append(item)
    return deduped


def _normalize_handoff_mode(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"two_stage", "high_consistency", TWO_STAGE_HIGH_CONSISTENCY_MODE}:
        return TWO_STAGE_HIGH_CONSISTENCY_MODE
    return SAFE_REFS_ONLY_MODE


def _split_two_stage_reference_items(
    items: List[Dict[str, Any]],
) -> Dict[str, List[Dict[str, Any]]]:
    stage_1: List[Dict[str, Any]] = []
    stage_2: List[Dict[str, Any]] = []
    identity_roles = {"character", "prop", "key_prop", "identity", "continuity"}
    scene_roles = {"scene", "environment", "lighting", "style", "background"}

    for item in items:
        role = str(item.get("role") or "").strip().lower()
        stage_hint = str(item.get("stage") or "").strip().lower()
        if stage_hint in {STAGE_1_IDENTITY, "identity", "stage_1"}:
            stage_1.append(item)
        elif stage_hint in {STAGE_2_SCENE_LIGHT, "scene_light", "stage_2", "refine"}:
            stage_2.append(item)
        elif role in identity_roles:
            stage_1.append(item)
        elif role in scene_roles:
            stage_2.append(item)
        else:
            stage_1.append(item)

    if not stage_1:
        stage_1 = list(items)
    if not stage_2:
        stage_2 = [item for item in items if item not in stage_1] or list(items)
    return {
        STAGE_1_IDENTITY: _dedupe_reference_items(stage_1),
        STAGE_2_SCENE_LIGHT: _dedupe_reference_items(stage_2),
    }


def _extract_spec_prompt(spec: Dict[str, Any]) -> str:
    prompt = spec.get("prompt")
    if isinstance(prompt, str) and prompt.strip():
        return prompt.strip()
    prompt_file = spec.get("prompt_file")
    if isinstance(prompt_file, str) and prompt_file.strip():
        return _repo_path(prompt_file).read_text(encoding="utf-8").strip()
    return ""


def _policy_int(
    args_value: int | None,
    policy: Dict[str, Any],
    key: str,
    default: int,
) -> int:
    if args_value is not None:
        return int(args_value)
    raw_value = policy.get(key)
    if raw_value is None:
        return default
    return int(raw_value)


def _validate_handoff_max_total_bytes(value: int) -> int:
    if value <= 0:
        raise ValueError("Codex imagegen handoff max_total_bytes must be positive.")
    if value > DEFAULT_CODEX_IMAGEGEN_HANDOFF_MAX_REFERENCE_BYTES:
        raise ValueError(
            "Codex imagegen handoff max_total_bytes cannot exceed "
            f"{DEFAULT_CODEX_IMAGEGEN_HANDOFF_MAX_REFERENCE_BYTES} bytes. "
            "Split the frame into staged handoffs instead of raising the request budget."
        )
    return value


def _stage_prompt(stage_id: str, base_prompt: str) -> str:
    base = base_prompt.strip()
    if stage_id == STAGE_1_IDENTITY:
        lead = (
            "Stage 1: lock character identity and key prop continuity first. "
            "Prioritize faces, body proportions, hair, clothing, and important props. "
            "Keep the frame visually coherent even if the scene is simpler."
        )
    elif stage_id == STAGE_2_SCENE_LIGHT:
        lead = (
            "Stage 2: refine the scene, composition, and lighting after stage 1. "
            "Use the stage 1 result as the primary image reference. "
            "Preserve identity from stage 1 while improving environment, camera feel, and light."
        )
    else:
        lead = ""
    if base:
        return f"{lead}\n\n{base}" if lead else base
    return lead or "Use the safe reference images in this handoff package."


def _write_handoff_files(
    *,
    output_dir: Path,
    manifest_path: Path,
    manifest: Dict[str, Any],
    prompt: str,
    project_slug: str,
    frame_id: str,
    mode: str = SAFE_REFS_ONLY_MODE,
    stage_id: str = "",
    stage_name: str = "",
    stage_order: int | None = None,
    requires_stage_result: bool = False,
) -> Dict[str, str]:
    prepared_entries = manifest.get("references", [])
    prepared_names = [
        Path(entry["prepared_path"]).name
        for entry in prepared_entries
        if isinstance(entry, dict) and entry.get("prepared_path")
    ]
    prompt_text = prompt.strip() or (
        "Use the safe reference images in this handoff package as visual continuity "
        "references. Generate one polished storyboard frame. Keep identities, clothing, "
        "props, lighting, and composition intent consistent. No text or watermark."
    )
    prompt_file = output_dir / "codex_imagegen_prompt.md"
    prompt_file.write_text(
        "\n".join(
            [
                "# Codex Imagegen Handoff Prompt",
                "",
                f"- Project: {project_slug or 'custom'}",
                f"- Frame: {frame_id or 'custom'}",
                f"- Handoff mode: {mode}",
                *( [f"- Stage: {stage_order}. {stage_name or stage_id}"] if stage_id else [] ),
                "- Reference policy: load only the safe reference files in this folder.",
                "- Do not attach raw source reference images to the Codex conversation.",
                *(
                    [
                        (
                            "- Stage 2 carry-over: attach the stage 1 result image "
                            f"`{STAGE_1_RESULT_NAME}` as the primary reference before these safe refs."
                        )
                    ]
                    if requires_stage_result
                    else []
                ),
                "",
                "## Safe References",
                *[f"- {name}" for name in prepared_names],
                "",
                "## Prompt",
                prompt_text,
                "",
            ]
        ),
        encoding="utf-8",
    )

    policy_file = output_dir / "handoff_policy.json"
    policy_payload = {
        "policy_version": 1,
        "mode": mode,
        "project_slug": project_slug,
        "frame_id": frame_id,
        "stage_id": stage_id or None,
        "stage_name": stage_name or None,
        "stage_order": stage_order,
        "safe_reference_manifest": str(manifest_path.resolve()),
        "prompt_file": str(prompt_file.resolve()),
        "max_total_bytes": manifest.get("max_total_bytes"),
        "reference_count": manifest.get("reference_count"),
        "total_source_bytes": manifest.get("total_source_bytes"),
        "total_prepared_bytes": manifest.get("total_prepared_bytes"),
        "estimated_base64_bytes": manifest.get("estimated_base64_bytes"),
        "use_prepared_paths_only": True,
        "never_attach_raw_references": True,
        "raw_source_paths_redacted": True,
        "requires_stage_result": requires_stage_result,
        "stage_result_expected_name": STAGE_1_RESULT_NAME if requires_stage_result else None,
    }
    policy_file.write_text(
        json.dumps(policy_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {
        "prompt_file": str(prompt_file.resolve()),
        "policy_file": str(policy_file.resolve()),
    }


def _build_stage_package(
    *,
    output_dir: Path,
    references: List[str],
    prompt: str,
    project_slug: str,
    frame_id: str,
    mode: str,
    stage_id: str = "",
    stage_name: str = "",
    stage_order: int | None = None,
    max_total_bytes: int,
    max_side: int,
    min_side: int,
    jpeg_quality: int,
    min_jpeg_quality: int,
    requires_stage_result: bool = False,
    stage_result_expected_name: str | None = None,
    stage_reference_role: str = "",
    parent_dir: Path | None = None,
) -> Dict[str, Any]:
    prepared_manifest = prepare_image_references_for_payload(
        [_repo_path(value) for value in references],
        output_dir,
        max_total_bytes=max_total_bytes,
        max_side=max_side,
        min_side=min_side,
        jpeg_quality=jpeg_quality,
        min_jpeg_quality=min_jpeg_quality,
    )
    manifest = _redact_source_paths_for_handoff(prepared_manifest)
    manifest.update(
        {
            "manifest_type": "codex_imagegen_safe_reference_pack",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "project_slug": project_slug,
            "frame_id": frame_id,
            "output_dir": str(output_dir.resolve()),
            "mode": mode,
            "stage_id": stage_id or None,
            "stage_name": stage_name or None,
            "stage_order": stage_order,
            "stage_reference_role": stage_reference_role or None,
            "requires_stage_result": requires_stage_result,
            "stage_result_expected_name": stage_result_expected_name,
            "parent_dir": str(parent_dir.resolve()) if parent_dir else None,
            "request_policy": {
                "first_principle": (
                    "A request can fail with 413 when aggregate encoded bytes exceed a gateway "
                    "body limit, even if each individual image is valid."
                ),
                "use_prepared_paths_only": True,
                "do_not_attach_source_paths_directly": True,
                "raw_source_paths_redacted": True,
                "codex_built_in_imagegen": True,
            },
        }
    )
    manifest_path = output_dir / "codex_safe_reference_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    handoff_files = _write_handoff_files(
        output_dir=output_dir,
        manifest_path=manifest_path,
        manifest=manifest,
        prompt=prompt,
        project_slug=project_slug,
        frame_id=frame_id,
        mode=mode,
        stage_id=stage_id,
        stage_name=stage_name,
        stage_order=stage_order,
        requires_stage_result=requires_stage_result,
    )
    manifest["handoff"] = {
        **handoff_files,
        "safe_reference_paths": [
            item["prepared_path"]
            for item in manifest.get("references", [])
            if isinstance(item, dict) and item.get("prepared_path")
        ],
        "raw_source_paths_redacted": True,
        "mode": mode,
        "stage_id": stage_id or None,
        "stage_name": stage_name or None,
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "manifest": str(manifest_path.resolve()),
        **handoff_files,
        **manifest,
    }


def _redact_source_paths_for_handoff(manifest: Dict[str, Any]) -> Dict[str, Any]:
    """Return the public handoff manifest without raw source image paths."""

    safe_manifest = dict(manifest)
    safe_references: List[Dict[str, Any]] = []
    for entry in manifest.get("references", []):
        if not isinstance(entry, dict):
            continue
        safe_entry = {
            key: value
            for key, value in entry.items()
            if key not in {"source_path"}
        }
        source_path = entry.get("source_path")
        if source_path:
            safe_entry["source_name"] = Path(str(source_path)).name
        safe_references.append(safe_entry)
    safe_manifest["references"] = safe_references
    safe_manifest["source_paths_redacted"] = True
    return safe_manifest


def _write_two_stage_runbook(
    *,
    output_dir: Path,
    project_slug: str,
    frame_id: str,
    stage_1: Dict[str, Any],
    stage_2: Dict[str, Any],
) -> Path:
    runbook_path = output_dir / "codex_two_stage_handoff_prompt.md"
    runbook_path.write_text(
        "\n".join(
            [
                "# Codex Two-Stage High-Consistency Handoff",
                "",
                f"- Project: {project_slug or 'custom'}",
                f"- Frame: {frame_id or 'custom'}",
                "- Mode: two_stage_high_consistency",
                "- Do not attach raw source reference images.",
                "",
                "## Stage 1: Identity + Key Props",
                f"- Prompt: {Path(stage_1['prompt_file']).name}",
                f"- Safe manifest: {Path(stage_1['manifest']).name}",
                "- Generate the identity-locked base image first.",
                f"- Save or refer to the result as `{STAGE_1_RESULT_NAME}`.",
                "",
                "## Stage 2: Scene + Light Refinement",
                f"- Prompt: {Path(stage_2['prompt_file']).name}",
                f"- Safe manifest: {Path(stage_2['manifest']).name}",
                f"- Attach `{STAGE_1_RESULT_NAME}` as the primary reference.",
                "- Then attach only the safe scene/light refs from stage 2.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return runbook_path


def _build_two_stage_package(
    *,
    output_dir: Path,
    reference_items: List[Dict[str, Any]],
    prompt: str,
    project_slug: str,
    frame_id: str,
    max_total_bytes: int,
    max_side: int,
    min_side: int,
    jpeg_quality: int,
    min_jpeg_quality: int,
    frame_spec: str | None,
) -> Dict[str, Any]:
    stage_items = _split_two_stage_reference_items(reference_items)
    stage_1_dir = output_dir / STAGE_1_IDENTITY
    stage_2_dir = output_dir / STAGE_2_SCENE_LIGHT
    stage_1 = _build_stage_package(
        output_dir=stage_1_dir,
        references=[str(item["path"]) for item in stage_items[STAGE_1_IDENTITY]],
        prompt=_stage_prompt(STAGE_1_IDENTITY, prompt),
        project_slug=project_slug,
        frame_id=frame_id,
        mode=TWO_STAGE_HIGH_CONSISTENCY_MODE,
        stage_id=STAGE_1_IDENTITY,
        stage_name="Identity + key props",
        stage_order=1,
        max_total_bytes=max_total_bytes,
        max_side=max_side,
        min_side=min_side,
        jpeg_quality=jpeg_quality,
        min_jpeg_quality=min_jpeg_quality,
        requires_stage_result=False,
        stage_reference_role="character_and_key_prop",
        parent_dir=output_dir,
    )
    stage_2 = _build_stage_package(
        output_dir=stage_2_dir,
        references=[str(item["path"]) for item in stage_items[STAGE_2_SCENE_LIGHT]],
        prompt=_stage_prompt(STAGE_2_SCENE_LIGHT, prompt),
        project_slug=project_slug,
        frame_id=frame_id,
        mode=TWO_STAGE_HIGH_CONSISTENCY_MODE,
        stage_id=STAGE_2_SCENE_LIGHT,
        stage_name="Scene + light refinement",
        stage_order=2,
        max_total_bytes=max_total_bytes,
        max_side=max_side,
        min_side=min_side,
        jpeg_quality=jpeg_quality,
        min_jpeg_quality=min_jpeg_quality,
        requires_stage_result=True,
        stage_result_expected_name=STAGE_1_RESULT_NAME,
        stage_reference_role="scene_and_lighting",
        parent_dir=output_dir,
    )
    runbook_path = _write_two_stage_runbook(
        output_dir=output_dir,
        project_slug=project_slug,
        frame_id=frame_id,
        stage_1=stage_1,
        stage_2=stage_2,
    )
    pack_manifest = {
        "manifest_type": "codex_imagegen_two_stage_handoff_pack",
        "policy_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": TWO_STAGE_HIGH_CONSISTENCY_MODE,
        "project_slug": project_slug,
        "frame_id": frame_id,
        "frame_spec": frame_spec,
        "output_dir": str(output_dir.resolve()),
        "max_total_bytes_per_stage": max_total_bytes,
        "never_attach_raw_references": True,
        "raw_source_paths_redacted": True,
        "stage_1_result_expected_name": STAGE_1_RESULT_NAME,
        "runbook": str(runbook_path.resolve()),
        "stages": [
            {
                "stage_id": STAGE_1_IDENTITY,
                "stage_order": 1,
                "goal": "人物与关键道具一致性锁定",
                "directory": str(stage_1_dir.resolve()),
                "safe_reference_manifest": stage_1["manifest"],
                "prompt_file": stage_1["prompt_file"],
                "reference_count": stage_1["reference_count"],
                "total_prepared_bytes": stage_1["total_prepared_bytes"],
                "estimated_base64_bytes": stage_1["estimated_base64_bytes"],
                "requires_stage_result": False,
            },
            {
                "stage_id": STAGE_2_SCENE_LIGHT,
                "stage_order": 2,
                "goal": "场景、构图与光影细化",
                "directory": str(stage_2_dir.resolve()),
                "safe_reference_manifest": stage_2["manifest"],
                "prompt_file": stage_2["prompt_file"],
                "reference_count": stage_2["reference_count"],
                "total_prepared_bytes": stage_2["total_prepared_bytes"],
                "estimated_base64_bytes": stage_2["estimated_base64_bytes"],
                "requires_stage_result": True,
                "stage_result_expected_name": STAGE_1_RESULT_NAME,
            },
        ],
    }
    pack_manifest_path = output_dir / "codex_two_stage_handoff_manifest.json"
    pack_manifest_path.write_text(
        json.dumps(pack_manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {
        "manifest": str(pack_manifest_path.resolve()),
        "runbook": str(runbook_path.resolve()),
        **pack_manifest,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Prepare Codex built-in imagegen safe refs and a reusable handoff package."
        )
    )
    parser.add_argument(
        "references",
        nargs="*",
        help="Reference image paths. Relative paths are resolved from the repo root.",
    )
    parser.add_argument(
        "--frame-spec",
        default="",
        help=(
            "JSON handoff spec with frame_id, project_slug, prompt, references, and policy. "
            "Explicit CLI references are appended after spec refs."
        ),
    )
    parser.add_argument(
        "--preset",
        choices=sorted(REFERENCE_PRESETS),
        help="Use a known reference set. Explicit references are appended after preset refs.",
    )
    parser.add_argument("--project-slug", default="", help="Optional project slug for handoff folder naming.")
    parser.add_argument(
        "--frame-id", default="", help="Optional frame id for output folder naming."
    )
    parser.add_argument("--prompt", default="", help="Optional Codex imagegen prompt text.")
    parser.add_argument("--prompt-file", default="", help="Optional prompt file path.")
    parser.add_argument(
        "--pack-mode",
        default="",
        help=(
            "Pack mode override: safe_refs_only or two_stage_high_consistency. "
            "Aliases: safe_direct, two_stage."
        ),
    )
    parser.add_argument(
        "--output-dir",
        default="",
        help=(
            "Output directory. Defaults to "
            "output/codex_imagegen_handoff/<project-slug>/<frame-or-preset>."
        ),
    )
    parser.add_argument(
        "--max-total-bytes",
        type=int,
        default=None,
    )
    parser.add_argument("--max-side", type=int, default=None)
    parser.add_argument("--min-side", type=int, default=None)
    parser.add_argument("--jpeg-quality", type=int, default=None)
    parser.add_argument(
        "--min-jpeg-quality",
        type=int,
        default=None,
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    frame_spec = _load_frame_spec(args.frame_spec)
    spec_policy = frame_spec.get("policy") or frame_spec.get("codex_imagegen_policy") or {}
    if not isinstance(spec_policy, dict):
        spec_policy = {}

    reference_items: List[Dict[str, Any]] = []
    reference_items.extend(_extract_spec_reference_items(frame_spec))
    if args.preset:
        reference_items.extend(
            {"path": value, "role": _infer_reference_role(value)} for value in REFERENCE_PRESETS[args.preset]
        )
    reference_items.extend({"path": value, "role": _infer_reference_role(value)} for value in args.references)
    reference_items = _dedupe_reference_items(reference_items)
    if not reference_items:
        raise SystemExit("No reference images were provided.")

    recommendation = recommend_codex_imagegen_mode_from_reference_items(reference_items, spec_policy)
    pack_selection = select_codex_handoff_mode(
        spec_policy,
        recommendation=recommendation,
        explicit_mode=args.pack_mode,
    )
    mode = pack_selection["mode"]

    project_slug = args.project_slug or str(frame_spec.get("project_slug") or "").strip()
    frame_or_preset = (
        args.frame_id
        or str(frame_spec.get("frame_id") or "").strip()
        or args.preset
        or "custom"
    )
    output_dir = (
        _repo_path(args.output_dir)
        if args.output_dir
        else REPO_ROOT
        / "output"
        / "codex_imagegen_handoff"
        / _safe_path_component(project_slug, "custom")
        / _safe_path_component(frame_or_preset, "frame")
        / (TWO_STAGE_HIGH_CONSISTENCY_MODE if mode == TWO_STAGE_HIGH_CONSISTENCY_MODE else "")
    )
    prompt = args.prompt.strip() or _extract_spec_prompt(frame_spec)
    if args.prompt_file:
        prompt = _repo_path(args.prompt_file).read_text(encoding="utf-8").strip()

    max_total_bytes = _policy_int(
        args.max_total_bytes,
        spec_policy,
        "max_total_bytes",
        DEFAULT_CODEX_IMAGEGEN_HANDOFF_MAX_REFERENCE_BYTES,
    )
    max_total_bytes = _validate_handoff_max_total_bytes(max_total_bytes)
    max_side = _policy_int(args.max_side, spec_policy, "max_side", DEFAULT_CODEX_IMAGEGEN_MAX_SIDE)
    min_side = _policy_int(args.min_side, spec_policy, "min_side", DEFAULT_CODEX_IMAGEGEN_MIN_SIDE)
    jpeg_quality = _policy_int(
        args.jpeg_quality,
        spec_policy,
        "jpeg_quality",
        DEFAULT_CODEX_IMAGEGEN_JPEG_QUALITY,
    )
    min_jpeg_quality = _policy_int(
        args.min_jpeg_quality,
        spec_policy,
        "min_jpeg_quality",
        DEFAULT_CODEX_IMAGEGEN_MIN_JPEG_QUALITY,
    )

    if mode == TWO_STAGE_HIGH_CONSISTENCY_MODE:
        manifest = _build_two_stage_package(
            output_dir=output_dir,
            reference_items=reference_items,
            prompt=prompt,
            project_slug=project_slug,
            frame_id=frame_or_preset,
            max_total_bytes=max_total_bytes,
            max_side=max_side,
            min_side=min_side,
            jpeg_quality=jpeg_quality,
            min_jpeg_quality=min_jpeg_quality,
            frame_spec=frame_spec.get("_spec_path"),
        )
    else:
        references = [_repo_path(item["path"]) for item in reference_items]
        prepared_manifest = prepare_image_references_for_payload(
            references,
            output_dir,
            max_total_bytes=max_total_bytes,
            max_side=max_side,
            min_side=min_side,
            jpeg_quality=jpeg_quality,
            min_jpeg_quality=min_jpeg_quality,
        )
        manifest = _redact_source_paths_for_handoff(prepared_manifest)
        manifest.update(
            {
                "manifest_type": "codex_imagegen_safe_reference_pack",
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "preset": args.preset,
                "frame_spec": frame_spec.get("_spec_path"),
                "project_slug": project_slug,
                "frame_id": frame_or_preset,
                "output_dir": str(output_dir.resolve()),
                "mode": mode,
                "request_policy": {
                    "first_principle": (
                        "A request can fail with 413 when aggregate encoded bytes exceed a gateway "
                        "body limit, even if each individual image is valid."
                    ),
                    "use_prepared_paths_only": True,
                    "do_not_attach_source_paths_directly": True,
                    "raw_source_paths_redacted": True,
                    "codex_built_in_imagegen": True,
                },
            }
        )

        manifest_path = output_dir / "codex_safe_reference_manifest.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        handoff_files = _write_handoff_files(
            output_dir=output_dir,
            manifest_path=manifest_path,
            manifest=manifest,
            prompt=prompt,
            project_slug=project_slug,
            frame_id=frame_or_preset,
            mode=mode,
        )
        manifest["handoff"] = {
            **handoff_files,
            "safe_reference_paths": [
                item["prepared_path"]
                for item in manifest.get("references", [])
                if isinstance(item, dict) and item.get("prepared_path")
            ],
            "raw_source_paths_redacted": True,
            "mode": mode,
        }
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        manifest = {
            **manifest,
            "manifest": str(manifest_path.resolve()),
            **handoff_files,
        }
    manifest["codex_imagegen_recommendation"] = recommendation
    manifest["codex_imagegen_handoff_plan"] = pack_selection
    manifest["auto_apply_recommendation"] = pack_selection["selection_source"] in {
        "recommendation_auto_apply",
        "explicit_auto",
    }
    print(
        json.dumps(
            manifest,
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
