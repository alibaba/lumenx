from typing import Dict, Any, Iterable, List, Optional, Tuple
import json
import os
import re
import time
import uuid
import shutil
import subprocess
import threading
import platform
from pathlib import Path
from urllib.parse import quote
from .models import (
    DEFAULT_I2I_MODEL,
    DEFAULT_I2V_MODEL,
    DEFAULT_T2I_MODEL,
    Script,
    GenerationStatus,
    VideoTask,
    Character,
    Scene,
    Prop,
    StoryAnalysis,
    StoryBeat,
    StoryboardFrame,
    Series,
    PromptConfig,
    ModelSettings,
    CodexImagegenPolicy,
    ArtDirection,
    ImageAsset,
    ImageVariant,
    AssetUnit,
)
from .frame_crop_composition import compose_frame_crops_from_manifest, resolve_manifest_path
from .llm import ScriptProcessor
from .assets import AssetGenerator
from .prompt_recipes import build_storyboard_continuity_hint
from .storyboard import StoryboardGenerator
from .video import VideoGenerator
from .audio import AudioGenerator
from .export import ExportManager
from ...utils import get_logger
from ...utils.http_downloads import download_url_to_file
from ...utils.json_store import load_json_object_with_backup, save_json_object_atomic
from ...utils.media_refs import normalize_project_media_ref, output_media_ref
from ...utils.image_payload_budget import (
    DEFAULT_CODEX_IMAGEGEN_HANDOFF_MAX_REFERENCE_BYTES,
    DEFAULT_CODEX_IMAGEGEN_JPEG_QUALITY,
    DEFAULT_CODEX_IMAGEGEN_MAX_REFERENCE_BYTES,
    DEFAULT_CODEX_IMAGEGEN_MAX_SIDE,
    DEFAULT_CODEX_IMAGEGEN_MIN_JPEG_QUALITY,
    DEFAULT_CODEX_IMAGEGEN_MIN_SIDE,
    estimate_base64_payload_bytes,
    prepare_image_references_for_payload,
)
from ...utils.codex_imagegen_handoff import (
    build_codex_reference_recommendation_for_frame,
    build_codex_reference_preview,
    build_codex_handoff_plan,
    normalize_codex_recommendation_policy,
)
from ...utils.oss_utils import extract_object_key_from_url, is_object_key
from ...utils.provider_registry import resolve_provider_backend
from ...utils.runtime_config import get_output_root
from ...utils.system_check import get_ffmpeg_path, get_ffmpeg_install_instructions

logger = get_logger(__name__)
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
LOCAL_VIDEO_SMOKE_ENV = "LUMENX_LOCAL_VIDEO_SMOKE"

# --- Security helpers ---

# Allowed pattern for IDs used in file paths (UUID hex + hyphens)
_SAFE_ID_RE = re.compile(r"^[a-zA-Z0-9_\-]+$")
_SAFE_STORYBOARD_RENDER_STRATEGY_VERSION = 1
STORYBOARD_REFERENCE_PAYLOAD_POLICY_VERSION = 1
STORYBOARD_REFERENCE_MAX_BYTES_ENV = "LUMENX_STORYBOARD_REFERENCE_MAX_BYTES"
STORYBOARD_REFERENCE_MAX_SIDE_ENV = "LUMENX_STORYBOARD_REFERENCE_MAX_SIDE"
STORYBOARD_REFERENCE_MIN_SIDE_ENV = "LUMENX_STORYBOARD_REFERENCE_MIN_SIDE"
STORYBOARD_REFERENCE_ALWAYS_PREPARE_ENV = "LUMENX_STORYBOARD_REFERENCE_ALWAYS_PREPARE"
_MEDICAL_CONTEXT_KEYWORDS = (
    "医院",
    "病房",
    "病床",
    "病号",
    "病人",
    "患者",
    "肝病",
    "医生",
    "护士",
    "治疗",
    "住院",
    "输液",
    "medical",
    "hospital",
    "ward",
    "patient",
    "doctor",
    "treatment",
)
_MINOR_CONTEXT_KEYWORDS = (
    "小男孩",
    "小女孩",
    "儿童",
    "童年",
    "小学生",
    "未成年",
    "boy",
    "girl",
)
_VULNERABLE_PATIENT_KEYWORDS = (
    "病人",
    "患者",
    "病床",
    "病号服",
    "虚弱",
    "患病",
    "patient",
    "hospital patient",
    "illness",
)


def _write_local_video_smoke_placeholder(output_path: str) -> None:
    """Write a tiny local placeholder for browser/API smoke jobs."""
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_bytes(
        b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom"
        b"\x00\x00\x00\x1clumenx-local-video-smoke\n"
    )


def _validate_safe_id(value: str, label: str = "id") -> str:
    """Ensure a value is safe to embed in file paths / command args (UUID-like)."""
    if not value or not _SAFE_ID_RE.match(value):
        raise ValueError(f"Invalid {label}: contains unsafe characters")
    return value


def _safe_resolve_path(base_dir: str, untrusted_rel: str) -> str:
    """Resolve *untrusted_rel* under *base_dir* and ensure the result stays inside it.

    Prevents path-traversal attacks (e.g. ``../../etc/passwd``).
    Returns the resolved absolute path; raises ValueError on escape attempts.
    """
    base = os.path.realpath(base_dir)
    resolved = os.path.realpath(os.path.join(base, untrusted_rel))
    if not resolved.startswith(base + os.sep) and resolved != base:
        raise ValueError(f"Path escapes base directory: {untrusted_rel}")
    return resolved


def _get_output_root_path() -> Path:
    return get_output_root(project_root=_PROJECT_ROOT)


def _get_output_dir() -> str:
    return str(_get_output_root_path())


def _output_path(*parts: str) -> str:
    return str(_get_output_root_path().joinpath(*parts))


def _output_relative_ref(value: str) -> str:
    normalized = normalize_project_media_ref(value).lstrip("/")
    if normalized.startswith("output/"):
        return normalized[len("output/") :]
    if normalized.startswith("outputs/"):
        return normalized[len("outputs/") :]
    return normalized


def _safe_resolve_output_ref(value: str) -> str:
    return _safe_resolve_path(_get_output_dir(), _output_relative_ref(value))


def _safe_path_component(value: str, fallback: str = "item") -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_\-]+", "_", str(value or "").strip()).strip("_")
    return cleaned or fallback


def _env_int(name: str, default: int, *, minimum: int = 1) -> int:
    raw_value = (os.getenv(name) or "").strip()
    if not raw_value:
        return default
    try:
        parsed = int(raw_value)
    except ValueError:
        logger.warning("Invalid %s=%r; using default %s.", name, raw_value, default)
        return default
    if parsed < minimum:
        logger.warning("Invalid %s=%r; using default %s.", name, raw_value, default)
        return default
    return parsed


def _env_flag(name: str, default: bool = False) -> bool:
    raw_value = (os.getenv(name) or "").strip().lower()
    if not raw_value:
        return default
    return raw_value in {"1", "true", "yes", "on"}


def _resolve_reference_path(url: str | None) -> Optional[str]:
    if not url:
        return None
    if is_object_key(url) or url.startswith("http"):
        return url
    if os.path.isabs(url) and os.path.exists(url):
        return url
    potential_path = _safe_resolve_output_ref(url)
    if os.path.exists(potential_path):
        return potential_path
    return None


def _project_relative_path(path: str | Path) -> str:
    resolved = Path(path).resolve()
    try:
        return resolved.relative_to(_PROJECT_ROOT).as_posix()
    except ValueError:
        return resolved.as_posix()


def _ensure_output_path(path: str | Path) -> Path:
    resolved = Path(path).resolve()
    output_root = _get_output_root_path()
    try:
        resolved.relative_to(output_root)
    except ValueError as exc:
        raise ValueError(
            "Composed frame output must stay under configured output directory."
        ) from exc
    return resolved


def _ensure_project_path(path: str | Path, label: str) -> Path:
    resolved = Path(path).resolve()
    try:
        resolved.relative_to(_PROJECT_ROOT)
    except ValueError as exc:
        raise ValueError(f"{label} must stay inside the project workspace.") from exc
    return resolved


def _set_script_generation_metadata(
    script: Script,
    stage: str,
    source: str,
    degraded: bool,
    *,
    reason: str = "",
    details: Optional[Dict[str, Any]] = None,
) -> None:
    metadata = dict(script.generation_metadata or {})
    stage_meta: Dict[str, Any] = {
        "source": source,
        "degraded": degraded,
    }
    if reason:
        stage_meta["reason"] = reason
    if details:
        stage_meta.update(details)
    metadata[stage] = stage_meta
    script.generation_metadata = metadata


def _resolve_fixture_crop_manifest_path(script: Script, frame: StoryboardFrame) -> Optional[Path]:
    if not script.fixture_slug:
        return None
    match = re.search(r"frame_(\d+)", frame.id or "")
    if not match:
        return None

    fixture_dirname = _fixture_slug_to_dirname(script.fixture_slug)
    candidate = (
        _PROJECT_ROOT
        / "tests"
        / "fixtures"
        / "story_projects"
        / fixture_dirname
        / "generation_prompts"
        / f"frame_{int(match.group(1))}"
        / "crop_composition_manifest.json"
    )
    if candidate.exists():
        return candidate
    return None


def _get_selected_frame_reference(frame: StoryboardFrame) -> Optional[str]:
    if frame.rendered_image_asset and frame.rendered_image_asset.selected_id:
        selected_variant = next(
            (
                variant
                for variant in frame.rendered_image_asset.variants
                if variant.id == frame.rendered_image_asset.selected_id
            ),
            None,
        )
        if selected_variant and selected_variant.url:
            return selected_variant.url
    return frame.rendered_image_url or frame.image_url


def _get_selected_image_asset_url(asset: Optional[ImageAsset]) -> Optional[str]:
    if not asset or not asset.variants:
        return None
    if asset.selected_id:
        selected_variant = next(
            (variant for variant in asset.variants if variant.id == asset.selected_id), None
        )
        if selected_variant and selected_variant.url:
            return selected_variant.url
    return asset.variants[0].url if asset.variants[0].url else None


def _get_character_reference_url(character: Character) -> Optional[str]:
    return (
        _get_selected_image_asset_url(character.three_view_asset)
        or _get_selected_image_asset_url(character.full_body_asset)
        or _get_selected_image_asset_url(character.headshot_asset)
        or _get_selected_image_asset_url(character.expression_sheet_asset)
        or character.three_view_image_url
        or character.full_body_image_url
        or character.headshot_image_url
        or character.expression_sheet_image_url
        or character.avatar_url
        or character.image_url
    )


def _get_asset_image_reference_url(asset: Any) -> Optional[str]:
    return _get_selected_image_asset_url(getattr(asset, "image_asset", None)) or getattr(
        asset, "image_url", None
    )


def _normalize_style_reference_images(values: Any) -> List[str]:
    if not isinstance(values, list):
        return []

    normalized: List[str] = []
    for value in values:
        if not isinstance(value, str):
            continue
        cleaned = extract_object_key_from_url(value).strip()
        if cleaned:
            normalized.append(cleaned)
    return list(dict.fromkeys(normalized))


def _build_art_direction_style_prompt(style_config: Optional[Dict[str, Any]]) -> str:
    if not style_config:
        return ""

    parts = [
        str(style_config.get("positive_prompt", "") or "").strip(),
        str(style_config.get("moodboard_notes", "") or "").strip(),
    ]
    return ", ".join(part for part in parts if part)


def _get_art_direction_reference_paths(style_config: Optional[Dict[str, Any]]) -> List[str]:
    ref_image_paths: List[str] = []
    for value in _normalize_style_reference_images(
        (style_config or {}).get("reference_images", [])
    ):
        resolved = _resolve_reference_path(value)
        if resolved:
            ref_image_paths.append(resolved)
    return list(dict.fromkeys(ref_image_paths))


def _normalize_art_direction_style_config(style_config: Dict[str, Any]) -> Dict[str, Any]:
    normalized = dict(style_config or {})
    normalized["reference_images"] = _normalize_style_reference_images(
        normalized.get("reference_images", [])
    )
    normalized["moodboard_notes"] = str(normalized.get("moodboard_notes", "") or "").strip()
    return normalized


def _model_dump_compat(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "dict"):
        return value.dict()
    return value


def _contains_any_keyword(text: str, keywords: Tuple[str, ...]) -> bool:
    normalized = str(text or "").lower()
    return any(keyword.lower() in normalized for keyword in keywords)


def _join_context_parts(*values: Any) -> str:
    parts: List[str] = []
    for value in values:
        if isinstance(value, (list, tuple, set)):
            parts.extend(str(item or "") for item in value)
        elif value is not None:
            parts.append(str(value))
    return "\n".join(part for part in parts if part)


def _is_minor_character(character: Optional[Character]) -> bool:
    if not character:
        return False
    age_text = str(character.age or "")
    age_numbers = [int(value) for value in re.findall(r"\d+", age_text)]
    if age_numbers:
        return min(age_numbers) < 18

    context = _join_context_parts(
        character.name,
        character.aliases,
        character.description,
        character.clothing,
    )
    return _contains_any_keyword(context, _MINOR_CONTEXT_KEYWORDS)


def _is_vulnerable_patient_character(character: Optional[Character]) -> bool:
    if not character:
        return False
    context = _join_context_parts(
        character.name,
        character.aliases,
        character.description,
        character.clothing,
    )
    return _contains_any_keyword(context, _VULNERABLE_PATIENT_KEYWORDS)


def _uses_openai_image_edit_model(model_name: Optional[str]) -> bool:
    normalized = str(model_name or "").strip().lower().replace("_", "-")
    return normalized == DEFAULT_I2I_MODEL or normalized.startswith("gpt-image")


def _build_safe_storyboard_render_strategy(
    *,
    frame: StoryboardFrame,
    scene: Optional[Scene],
    characters: Iterable[Character],
    props: Iterable[Prop],
    prompt: str,
    ref_image_paths: List[str],
    model_name: Optional[str],
) -> Optional[Dict[str, Any]]:
    if not _uses_openai_image_edit_model(model_name):
        return None
    if len(ref_image_paths) < 1:
        return None

    characters_by_id = {character.id: character for character in characters}
    props_by_id = {prop.id: prop for prop in props}
    frame_characters = [
        characters_by_id.get(character_id) for character_id in frame.character_ids or []
    ]
    frame_props = [props_by_id.get(prop_id) for prop_id in frame.prop_ids or []]

    minor_character_ids = [
        character.id
        for character in frame_characters
        if character and _is_minor_character(character)
    ]
    vulnerable_patient_character_ids = [
        character.id
        for character in frame_characters
        if character and _is_vulnerable_patient_character(character)
    ]
    context_text = _join_context_parts(
        prompt,
        frame.image_prompt_cn,
        scene.name if scene else None,
        scene.description if scene else None,
        [character.description for character in frame_characters if character],
        [prop.description for prop in frame_props if prop],
    )

    has_medical_context = _contains_any_keyword(context_text, _MEDICAL_CONTEXT_KEYWORDS)
    has_minor_context = bool(minor_character_ids) or _contains_any_keyword(
        context_text, _MINOR_CONTEXT_KEYWORDS
    )
    has_patient_context = bool(vulnerable_patient_character_ids) or _contains_any_keyword(
        context_text,
        _VULNERABLE_PATIENT_KEYWORDS,
    )

    if not (has_medical_context and has_minor_context):
        return None

    reason_codes = ["openai_multi_reference_edit_risk"]
    if has_medical_context:
        reason_codes.append("medical_context")
    if has_minor_context:
        reason_codes.append("minor_context")
    if has_patient_context:
        reason_codes.append("patient_context")

    return {
        "strategy_version": _SAFE_STORYBOARD_RENDER_STRATEGY_VERSION,
        "mode": "staged_safe_storyboard",
        "reason_codes": reason_codes,
        "direct_multi_reference_edit_allowed": False,
        "base_stage": {
            "model_mode": "text_to_image",
            "reference_policy": "no_reference_images",
            "prompt_policy": "base_composition_first",
        },
        "follow_up_stages": [
            {
                "stage": "identity_refine",
                "model_mode": "image_edit",
                "reference_policy": "single_reference_per_pass",
                "notes": "Use only after the base composition is approved; refine one subject or one prop at a time.",
            },
            {
                "stage": "final_consistency_pass",
                "model_mode": "image_edit",
                "reference_policy": "single_reference_per_pass",
                "notes": "Use targeted localized edits for wardrobe or prop alignment only.",
            },
        ],
        "omitted_reference_count": len(ref_image_paths),
        "omitted_reference_assets": list(ref_image_paths),
        "detected_context": {
            "has_medical_context": has_medical_context,
            "has_minor_context": has_minor_context,
            "has_patient_context": has_patient_context,
            "minor_character_ids": minor_character_ids,
            "vulnerable_patient_character_ids": vulnerable_patient_character_ids,
        },
    }


def _reference_kind(path: str) -> str:
    if path.startswith(("http://", "https://")):
        return "remote_url"
    if is_object_key(path):
        return "object_key"
    return "local_file"


def _build_storyboard_reference_payload_preflight(
    *,
    script: Script,
    frame: StoryboardFrame,
    ref_image_paths: List[str],
    send_references: bool = True,
    prepare_references: bool = True,
) -> Tuple[List[str], Dict[str, Any]]:
    max_total_bytes = _env_int(
        STORYBOARD_REFERENCE_MAX_BYTES_ENV,
        DEFAULT_CODEX_IMAGEGEN_MAX_REFERENCE_BYTES,
        minimum=128 * 1024,
    )
    max_side = _env_int(
        STORYBOARD_REFERENCE_MAX_SIDE_ENV,
        DEFAULT_CODEX_IMAGEGEN_MAX_SIDE,
        minimum=256,
    )
    min_side = _env_int(
        STORYBOARD_REFERENCE_MIN_SIDE_ENV,
        DEFAULT_CODEX_IMAGEGEN_MIN_SIDE,
        minimum=128,
    )
    if min_side > max_side:
        min_side = max_side

    entries: List[Dict[str, Any]] = []
    local_paths: List[Path] = []
    local_indices: List[int] = []
    for index, ref_path in enumerate(ref_image_paths, start=1):
        kind = _reference_kind(ref_path)
        entry: Dict[str, Any] = {
            "index": index,
            "path": ref_path,
            "kind": kind,
            "source_bytes": None,
            "prepared_path": None,
            "prepared_bytes": None,
            "will_send": bool(send_references),
        }
        if kind == "local_file" and os.path.exists(ref_path):
            local_path = Path(ref_path)
            entry["source_bytes"] = local_path.stat().st_size
            local_paths.append(local_path)
            local_indices.append(index)
        elif kind == "local_file":
            entry["status"] = "missing_local_reference"
        else:
            entry["status"] = "deferred_to_provider_or_signed_url"
        entries.append(entry)

    total_source_bytes = sum(int(entry["source_bytes"] or 0) for entry in entries)
    should_prepare = (
        bool(local_paths)
        and send_references
        and prepare_references
        and (
            total_source_bytes > max_total_bytes
            or _env_flag(STORYBOARD_REFERENCE_ALWAYS_PREPARE_ENV, default=False)
        )
    )

    preflight: Dict[str, Any] = {
        "policy_version": STORYBOARD_REFERENCE_PAYLOAD_POLICY_VERSION,
        "status": "no_references",
        "send_references": bool(send_references),
        "max_total_bytes": max_total_bytes,
        "max_side": max_side,
        "min_side": min_side,
        "reference_count": len(ref_image_paths),
        "local_reference_count": len(local_paths),
        "remote_reference_count": len(ref_image_paths) - len(local_paths),
        "total_source_bytes": total_source_bytes,
        "estimated_source_base64_bytes": estimate_base64_payload_bytes(total_source_bytes),
        "total_prepared_bytes": total_source_bytes,
        "estimated_prepared_base64_bytes": estimate_base64_payload_bytes(total_source_bytes),
        "prepared": False,
        "fits_budget": total_source_bytes <= max_total_bytes,
        "request_ref_image_paths": list(ref_image_paths) if send_references else [],
        "references": entries,
    }

    if not ref_image_paths:
        return [], preflight
    if not send_references:
        preflight["status"] = "not_sent_staged_strategy"
        preflight["fits_budget"] = True
        preflight["request_ref_image_paths"] = []
        return [], preflight
    if not should_prepare:
        preflight["status"] = "within_budget"
        return list(ref_image_paths), preflight

    output_dir = (
        _get_output_root_path()
        / "reference_payloads"
        / "storyboard"
        / _safe_path_component(script.id, "script")
        / _safe_path_component(frame.id, "frame")
    )
    try:
        prepared_manifest = prepare_image_references_for_payload(
            local_paths,
            output_dir,
            max_total_bytes=max_total_bytes,
            max_side=max_side,
            min_side=min_side,
        )
    except Exception as exc:
        raise RuntimeError(
            "Storyboard reference payload exceeds the configured request budget and "
            "could not be reduced safely. Reduce reference count or use staged edits."
        ) from exc
    prepared_manifest_path = output_dir / "reference_payload_preflight_manifest.json"
    prepared_manifest_path.write_text(
        json.dumps(prepared_manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    prepared_by_source = {
        str(Path(item["source_path"]).resolve()): item
        for item in prepared_manifest.get("references", [])
    }
    prepared_paths = list(ref_image_paths)
    total_prepared_bytes = 0
    for entry, local_index in zip(
        (entries[index - 1] for index in local_indices),
        local_indices,
    ):
        prepared_entry = prepared_by_source.get(str(Path(entry["path"]).resolve()))
        if not prepared_entry:
            continue
        prepared_path = prepared_entry["prepared_path"]
        prepared_bytes = int(prepared_entry["prepared_bytes"])
        entry["prepared_path"] = prepared_path
        entry["prepared_bytes"] = prepared_bytes
        entry["status"] = "prepared_safe_reference"
        prepared_paths[local_index - 1] = prepared_path
        total_prepared_bytes += prepared_bytes

    non_local_bytes = sum(
        int(entry["source_bytes"] or 0) for entry in entries if entry["kind"] != "local_file"
    )
    total_request_bytes = total_prepared_bytes + non_local_bytes
    preflight.update(
        {
            "status": "prepared_safe_references",
            "prepared": True,
            "fits_budget": total_request_bytes <= max_total_bytes,
            "total_prepared_bytes": total_request_bytes,
            "estimated_prepared_base64_bytes": estimate_base64_payload_bytes(total_request_bytes),
            "prepared_manifest": str(prepared_manifest_path.resolve()),
            "request_ref_image_paths": prepared_paths,
            "references": entries,
        }
    )
    return prepared_paths, preflight


def _normalize_codex_imagegen_policy(value: Any) -> CodexImagegenPolicy:
    def _coerce_bool(raw: Any, default: bool) -> bool:
        if raw is None:
            return default
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, str):
            normalized = raw.strip().lower()
            if not normalized:
                return default
            return normalized in {"1", "true", "yes", "on"}
        return bool(raw)

    if isinstance(value, CodexImagegenPolicy):
        return value
    if not isinstance(value, dict):
        return CodexImagegenPolicy()
    max_total_bytes = int(
        value.get(
            "max_total_bytes",
            DEFAULT_CODEX_IMAGEGEN_HANDOFF_MAX_REFERENCE_BYTES,
        )
        or DEFAULT_CODEX_IMAGEGEN_HANDOFF_MAX_REFERENCE_BYTES
    )
    if max_total_bytes <= 0:
        max_total_bytes = DEFAULT_CODEX_IMAGEGEN_HANDOFF_MAX_REFERENCE_BYTES
    else:
        max_total_bytes = min(
            max_total_bytes,
            DEFAULT_CODEX_IMAGEGEN_HANDOFF_MAX_REFERENCE_BYTES,
        )
    return CodexImagegenPolicy(
        enabled=_coerce_bool(value.get("enabled"), True),
        mode=str(value.get("mode", "safe_refs_only") or "safe_refs_only"),
        max_total_bytes=max_total_bytes,
        max_side=int(
            value.get("max_side", DEFAULT_CODEX_IMAGEGEN_MAX_SIDE)
            or DEFAULT_CODEX_IMAGEGEN_MAX_SIDE
        ),
        min_side=int(
            value.get("min_side", DEFAULT_CODEX_IMAGEGEN_MIN_SIDE)
            or DEFAULT_CODEX_IMAGEGEN_MIN_SIDE
        ),
        jpeg_quality=int(
            value.get("jpeg_quality", DEFAULT_CODEX_IMAGEGEN_JPEG_QUALITY)
            or DEFAULT_CODEX_IMAGEGEN_JPEG_QUALITY
        ),
        min_jpeg_quality=int(
            value.get("min_jpeg_quality", DEFAULT_CODEX_IMAGEGEN_MIN_JPEG_QUALITY)
            or DEFAULT_CODEX_IMAGEGEN_MIN_JPEG_QUALITY
        ),
        never_attach_raw_references=_coerce_bool(
            value.get("never_attach_raw_references"),
            True,
        ),
        recommendation=normalize_codex_recommendation_policy(value.get("recommendation") or {}),
    )


_SHOT_BLOCK_RE = re.compile(
    r"###\s*镜头\s*(?P<number>\d+)\s*[｜|]\s*(?P<time>[^｜|\n]+)\s*[｜|]\s*(?P<title>[^\n]+)\n(?P<body>.*?)(?=\n###\s*镜头\s*\d+\s*[｜|]|\Z)",
    re.S,
)
_SECTION_RE_TEMPLATE = r"\*\*{name}\*\*\s*\n(?P<content>.*?)(?=\n\*\*[^*\n]+\*\*|\Z)"


def _extract_markdown_section(body: str, name: str) -> str:
    pattern = _SECTION_RE_TEMPLATE.format(name=re.escape(name))
    match = re.search(pattern, body or "", re.S)
    if not match:
        return ""
    return match.group("content").strip()


def _strip_markdown_lines(value: str) -> str:
    lines = []
    for raw_line in str(value or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("- "):
            line = line[2:].strip()
        lines.append(line)
    return "\n".join(lines).strip()


def _fixture_slug_to_dirname(slug: str) -> str:
    slug_map = {
        "liuyi-that-day": "六一那天",
        "liuyi-that-day-v2": "六一那天_v2",
        "六一那天": "六一那天",
    }
    return slug_map.get(slug, slug)


def _slugify_fixture_dirname(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_\-]+", "-", value or "").strip("-").lower()
    return slug or value


def _normalize_fixture_upload_type(upload_type: Any) -> str:
    normalized = str(upload_type or "").strip().lower().replace("-", "_")
    if normalized in {"three_view", "three_views"}:
        return "three_views"
    if normalized in {"headshot", "head_shot", "avatar"}:
        return "head_shot"
    if normalized in {"expression", "expression_sheet", "expressionsheet"}:
        return "expression_sheet"
    if normalized in {"fullbody", "full_body"}:
        return "full_body"
    if normalized in {"image", "scene", "prop"}:
        return "image"
    return normalized


def _flatten_fixture_reference_assets(manifest: Dict[str, Any]) -> List[Dict[str, Any]]:
    flattened: List[Dict[str, Any]] = []
    seen_keys: set[tuple[str, str, str, str]] = set()

    def add_entry(entry: Dict[str, Any], *, default_locked: bool = True) -> None:
        asset_type = str(entry.get("asset_type") or "").strip().lower()
        asset_id = str(entry.get("asset_id") or "").strip()
        upload_type = _normalize_fixture_upload_type(entry.get("upload_type") or entry.get("role"))
        source_path = str(entry.get("path") or "").strip()
        if not asset_type or not asset_id or not source_path:
            return
        key = (asset_type, asset_id, upload_type, source_path)
        if key in seen_keys:
            return
        seen_keys.add(key)

        normalized_entry = dict(entry)
        normalized_entry["asset_type"] = asset_type
        normalized_entry["asset_id"] = asset_id
        normalized_entry["upload_type"] = upload_type
        normalized_entry["path"] = source_path
        normalized_entry["locked"] = bool(entry.get("locked", entry.get("lock", default_locked)))
        flattened.append(normalized_entry)

    for entry in [item for item in manifest.get("reference_assets", []) if isinstance(item, dict)]:
        add_entry(entry)

    for package in [item for item in manifest.get("asset_packages", []) if isinstance(item, dict)]:
        package_asset_type = str(package.get("asset_type") or "").strip().lower()
        package_asset_id = str(package.get("asset_id") or "").strip()
        package_name = str(package.get("name") or package_asset_id or "asset").strip()
        package_locked = bool(package.get("locked", package.get("lock", True)))
        if not package_asset_type or not package_asset_id:
            continue

        board = package.get("board")
        if isinstance(board, dict) and board.get("runtime_binding", True):
            add_entry(
                {
                    "asset_type": package_asset_type,
                    "asset_id": package_asset_id,
                    "upload_type": _normalize_fixture_upload_type(
                        board.get("upload_type") or board.get("role")
                    ),
                    "path": board.get("path"),
                    "label": board.get("label") or f"{package_name} 主板",
                    "prompt_used": board.get("prompt_used") or package_name,
                    "locked": bool(board.get("locked", package_locked)),
                },
                default_locked=package_locked,
            )

        for derivative in [
            item for item in package.get("derivatives", []) if isinstance(item, dict)
        ]:
            if derivative.get("runtime_binding", True) is False:
                continue
            add_entry(
                {
                    "asset_type": package_asset_type,
                    "asset_id": package_asset_id,
                    "upload_type": _normalize_fixture_upload_type(
                        derivative.get("upload_type") or derivative.get("role")
                    ),
                    "path": derivative.get("path"),
                    "label": derivative.get("label")
                    or f"{package_name} {derivative.get('role') or 'derivative'}",
                    "prompt_used": derivative.get("prompt_used") or package_name,
                    "locked": bool(derivative.get("locked", package_locked)),
                },
                default_locked=package_locked,
            )

    return flattened


def _get_fixture_root_dir() -> str:
    return os.path.join("tests", "fixtures", "story_projects")


def _get_fixture_manifest_path(fixture_dir: str) -> str:
    return os.path.join(fixture_dir, "project_manifest.json")


def _build_fixture_reference_bindings(
    *,
    frame: StoryboardFrame,
    scene: Scene,
    characters: List[Character],
    props: List[Prop],
    order: int,
    story_function: str,
    notes: str,
) -> Dict[str, Any]:
    characters_by_id = {character.id: character for character in characters}
    props_by_id = {prop.id: prop for prop in props}

    return {
        "reference_binding_version": 1,
        "fixture_role": "golden_storyboard_frame",
        "scene": {
            "id": scene.id,
            "name": scene.name,
            "required": True,
            "lock": True,
        },
        "characters": [
            {
                "id": character.id,
                "name": character.name,
                "required": True,
                "lock_identity": True,
                "preferred_reference": "full_body_asset",
            }
            for character_id in frame.character_ids
            if (character := characters_by_id.get(character_id))
        ],
        "props": [
            {
                "id": prop.id,
                "name": prop.name,
                "required": True,
                "lock_shape": True,
                "preferred_reference": "image_asset",
            }
            for prop_id in frame.prop_ids
            if (prop := props_by_id.get(prop_id))
        ],
        "continuity": {
            "same_scene_lock": order > 1,
            "prefer_previous_frame": order > 1,
            "guardrail": "保持同一时空内的人物造型、场景布局、光线方向和关键道具位置连续。",
        },
        "style": {
            "lock": True,
            "guardrail": "写实电影感，真实中国面孔，禁止卡通化、动漫化、廉价短剧感。",
        },
        "quality_targets": [
            "真人写实，不要卡通脸",
            "角色身份、发型、年龄感和服装保持一致",
            "场景空间结构、时间段和光线逻辑保持一致",
            "关键道具不能丢失、变形或改色",
            "画面中不要出现可读错误文字、水印或 logo",
        ],
        "story_function": story_function,
        "director_notes": notes,
    }


def _image_asset_has_selected_or_any_variant(asset: Any) -> bool:
    return bool(asset and getattr(asset, "variants", None))


def _asset_has_static_reference(asset: Any) -> bool:
    if not asset:
        return False

    reference_fields = (
        "image_url",
        "avatar_url",
        "full_body_image_url",
        "three_view_image_url",
        "headshot_image_url",
        "expression_sheet_image_url",
    )
    if any(bool(getattr(asset, field, None)) for field in reference_fields):
        return True

    image_asset_fields = (
        "image_asset",
        "full_body_asset",
        "three_view_asset",
        "headshot_asset",
        "expression_sheet_asset",
    )
    return any(
        _image_asset_has_selected_or_any_variant(getattr(asset, field, None))
        for field in image_asset_fields
    )


class ComicGenPipeline:
    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {}
        self.output_root = _get_output_root_path()
        self.script_processor = ScriptProcessor()
        asset_config = dict(self.config.get("assets") or {})
        storyboard_config = dict(self.config.get("storyboard") or {})
        video_config = dict(self.config.get("video") or {})
        audio_config = dict(self.config.get("audio") or {})
        export_config = dict(self.config.get("export") or {})
        asset_config.setdefault("output_dir", _output_path("assets"))
        storyboard_config.setdefault("output_dir", _output_path("storyboard"))
        video_config.setdefault("output_dir", _output_path("video"))
        audio_config.setdefault("output_dir", _output_path("audio"))
        export_config.setdefault("output_dir", _output_path("export"))
        self.asset_generator = AssetGenerator(asset_config)
        self.storyboard_generator = StoryboardGenerator(storyboard_config)
        self.video_generator = VideoGenerator(video_config)
        self.audio_generator = AudioGenerator(audio_config)
        self.export_manager = ExportManager(export_config)

        self.data_file = _output_path("projects.json")
        self.series_data_file = _output_path("series.json")
        self._save_lock = threading.RLock()  # Reentrant lock to prevent concurrent file writes
        self.scripts: Dict[str, Script] = self._load_data()
        self.series_store: Dict[str, Series] = self._load_series_data()

        # Task management for async asset generation
        # Format: { task_id: { status: str, progress: int, error: str, script_id: str, asset_id: str, created_at: float } }
        self.asset_generation_tasks: Dict[str, Dict[str, Any]] = {}
        self.video_generation_tasks: Dict[str, Dict[str, Any]] = {}
        # Temporary cache for file import previews (import_id -> text)
        self._import_cache: Dict[str, str] = {}
        # Cached model instances for vendor video providers (lazily initialized)
        self._kling_model = None
        self._seedance_model = None
        self._vidu_model = None

    def _resolve_video_backend(self, model_name: str) -> str:
        try:
            return resolve_provider_backend(model_name)
        except (KeyError, ValueError):
            logger.debug(
                "Provider backend not registered for video model %s, defaulting to dashscope.",
                model_name,
            )
            return "dashscope"
        except Exception as e:
            logger.warning(
                "Unexpected error resolving provider backend for video model %s: %s. "
                "Falling back to dashscope.",
                model_name,
                e,
            )
            return "dashscope"

    def _collect_frame_asset_reference_paths(
        self,
        script: Script,
        frame: StoryboardFrame,
        scene: Optional[Scene],
    ) -> List[str]:
        """Resolve selected scene, character, and prop references for a frame."""
        ref_image_paths: List[str] = []

        def append_resolved(url: Optional[str]) -> None:
            resolved = _resolve_reference_path(url)
            if resolved and resolved not in ref_image_paths:
                ref_image_paths.append(resolved)

        target_scene = scene or next(
            (item for item in script.scenes if item.id == frame.scene_id),
            None,
        )
        if target_scene:
            append_resolved(_get_asset_image_reference_url(target_scene))

        characters_by_id = {character.id: character for character in script.characters}
        for character_id in frame.character_ids or []:
            character = characters_by_id.get(character_id)
            if character:
                append_resolved(_get_character_reference_url(character))

        props_by_id = {prop.id: prop for prop in script.props}
        for prop_id in frame.prop_ids or []:
            prop = props_by_id.get(prop_id)
            if prop:
                append_resolved(_get_asset_image_reference_url(prop))

        return ref_image_paths

    # ... (existing methods)

    def export_project(self, script_id: str, options: Dict[str, Any]) -> Dict[str, Optional[str]]:
        """Step 7: Export project to final video."""
        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")

        export_result = self.export_manager.render_project(script, options)
        return export_result

    def get_script(self, script_id: str) -> Optional[Script]:
        return self.scripts.get(script_id)

    def _load_data(self) -> Dict[str, Script]:
        try:
            data = load_json_object_with_backup(self.data_file)
            return {k: Script(**v) for k, v in data.items()}
        except Exception as e:
            logger.error(f"Failed to load data: {e}")
            return {}

    def _save_data(self):
        """Save data with thread lock to prevent concurrent write issues."""
        with self._save_lock:
            try:
                save_json_object_atomic(
                    self.data_file,
                    {k: _model_dump_compat(v) for k, v in self.scripts.items()},
                )
            except Exception as e:
                logger.error(f"Failed to save data: {e}")

    def create_project(self, title: str, text: str, skip_analysis: bool = False) -> Script:
        """Step 1: Parse novel and create project."""
        if skip_analysis:
            script = self.script_processor.create_draft_script(title, text)
        else:
            script = self.script_processor.parse_novel(title, text)

        self.scripts[script.id] = script
        self._save_data()
        return script

    def list_fixture_story_projects(self) -> List[Dict[str, Any]]:
        """Return bundled fixture story projects that can be imported from the UI."""
        fixture_root = _get_fixture_root_dir()
        if not os.path.isdir(fixture_root):
            return []

        fixtures: List[Dict[str, Any]] = []
        for entry in sorted(os.listdir(fixture_root)):
            fixture_dir = os.path.join(fixture_root, entry)
            if not os.path.isdir(fixture_dir):
                continue

            manifest_path = _get_fixture_manifest_path(fixture_dir)
            if not os.path.exists(manifest_path):
                continue

            try:
                metadata = self._load_fixture_metadata(entry, fixture_dir)
                fixtures.append(metadata)
            except Exception as e:
                logger.warning("Skipping invalid fixture story project %s: %s", entry, e)

        return fixtures

    def _load_fixture_metadata(self, dirname: str, fixture_dir: str) -> Dict[str, Any]:
        manifest_path = _get_fixture_manifest_path(fixture_dir)
        with open(manifest_path, "r", encoding="utf-8") as handle:
            manifest = json.load(handle)

        fixture_name = str(manifest.get("project_name") or dirname)
        slug = str(manifest.get("slug") or _slugify_fixture_dirname(dirname))
        notes = manifest.get("notes") if isinstance(manifest.get("notes"), list) else []
        description = str(
            manifest.get("description")
            or next((note for note in notes if isinstance(note, str) and note.strip()), "")
            or "本地样板项目，可一键导入为真实项目。"
        )

        source_files = [item for item in manifest.get("source_files", []) if isinstance(item, dict)]
        reference_images = [
            item for item in manifest.get("reference_images", []) if isinstance(item, dict)
        ]
        reference_assets = _flatten_fixture_reference_assets(manifest)
        asset_packages = [
            item for item in manifest.get("asset_packages", []) if isinstance(item, dict)
        ]
        imported_project = self._find_imported_fixture_project(slug, fixture_name)
        model_settings = (
            manifest.get("model_settings", {})
            if isinstance(manifest.get("model_settings"), dict)
            else {}
        )
        parser = str(manifest.get("parser") or "seedance_storyboard_markdown")

        return {
            "slug": slug,
            "name": fixture_name,
            "project_type": manifest.get("project_type") or "storyboard_fixture",
            "description": description,
            "parser": parser,
            "dirname": dirname,
            "source_count": len(source_files),
            "reference_count": len(reference_images) + len(reference_assets),
            "asset_package_count": len(asset_packages),
            "source_files": source_files,
            "reference_images": reference_images,
            "reference_assets": reference_assets,
            "asset_packages": asset_packages,
            "model_settings": model_settings,
            "notes": notes,
            "is_imported": imported_project is not None,
            "project_id": imported_project.id if imported_project else None,
            "frame_count": self._count_fixture_storyboard_frames(fixture_dir, source_files, parser),
        }

    def _count_fixture_storyboard_frames(
        self,
        fixture_dir: str,
        source_files: List[Dict[str, Any]],
        parser: str,
    ) -> int:
        if parser != "seedance_storyboard_markdown":
            return 0

        prompt_doc_rel = next(
            (
                item.get("path")
                for item in source_files
                if item.get("role") == "storyboard_prompt_doc"
            ),
            None,
        )
        if not prompt_doc_rel:
            return 0

        prompt_doc_path = _safe_resolve_path(fixture_dir, str(prompt_doc_rel))
        if not os.path.exists(prompt_doc_path):
            return 0

        with open(prompt_doc_path, "r", encoding="utf-8") as handle:
            return len(list(_SHOT_BLOCK_RE.finditer(handle.read())))

    def _find_imported_fixture_project(self, slug: str, fixture_name: str) -> Optional[Script]:
        return next(
            (
                script
                for script in self.scripts.values()
                if (
                    script.fixture_slug == slug
                    or (
                        script.title == fixture_name
                        and (
                            script.style_prompt == f"fixture:{fixture_name}"
                            or script.fixture_name == fixture_name
                        )
                    )
                )
            ),
            None,
        )

    def _resolve_fixture_story_project(self, slug: str) -> Tuple[str, str, Dict[str, Any]]:
        fixture_root = _get_fixture_root_dir()
        fixture_name = _fixture_slug_to_dirname(slug)
        candidates = []
        if fixture_name:
            candidates.append(os.path.join(fixture_root, fixture_name))

        if os.path.isdir(fixture_root):
            for entry in sorted(os.listdir(fixture_root)):
                fixture_dir = os.path.join(fixture_root, entry)
                manifest_path = _get_fixture_manifest_path(fixture_dir)
                if not os.path.isdir(fixture_dir) or not os.path.exists(manifest_path):
                    continue
                with open(manifest_path, "r", encoding="utf-8") as handle:
                    manifest = json.load(handle)
                manifest_slug = str(manifest.get("slug") or _slugify_fixture_dirname(entry))
                manifest_name = str(manifest.get("project_name") or entry)
                if slug in {manifest_slug, manifest_name, entry}:
                    return entry, fixture_dir, manifest

        for fixture_dir in candidates:
            manifest_path = _get_fixture_manifest_path(fixture_dir)
            if os.path.exists(manifest_path):
                with open(manifest_path, "r", encoding="utf-8") as handle:
                    return os.path.basename(fixture_dir), fixture_dir, json.load(handle)

        raise ValueError(f"Fixture story project not found: {slug}")

    def import_fixture_story_project(self, slug: str) -> Script:
        """Create or reuse a local fixture story project for UI smoke testing."""
        fixture_dirname, fixture_dir, manifest = self._resolve_fixture_story_project(slug)
        fixture_name = str(manifest.get("project_name") or fixture_dirname)
        fixture_slug = str(manifest.get("slug") or _slugify_fixture_dirname(fixture_dirname))
        existing = self._find_imported_fixture_project(fixture_slug, fixture_name)

        parser = str(manifest.get("parser") or "seedance_storyboard_markdown")
        if parser != "seedance_storyboard_markdown":
            raise ValueError(f"Unsupported fixture parser for {slug}: {parser}")

        prompt_doc_rel = next(
            (
                item.get("path")
                for item in manifest.get("source_files", [])
                if item.get("role") == "storyboard_prompt_doc"
            ),
            None,
        )
        if not prompt_doc_rel:
            raise ValueError(f"Fixture story project {slug} is missing storyboard prompt doc")

        prompt_doc_path = _safe_resolve_path(fixture_dir, prompt_doc_rel)
        with open(prompt_doc_path, "r", encoding="utf-8") as handle:
            markdown_text = handle.read()

        reference_rel = next(
            (
                item.get("path")
                for item in manifest.get("reference_images", [])
                if item.get("role") == "storyboard_reference_collage"
            ),
            "",
        )
        reference_media_ref = self._copy_fixture_reference_image(
            fixture_slug, fixture_dir, reference_rel
        )
        script = self._build_fixture_script_from_markdown(
            title=fixture_name,
            fixture_slug=fixture_slug,
            fixture_project_type=str(manifest.get("project_type") or ""),
            markdown_text=markdown_text,
            manifest=manifest,
            reference_media_ref=reference_media_ref,
        )
        self._apply_fixture_reference_assets(script, fixture_dir, manifest)

        if existing:
            script = self._refresh_existing_fixture_project(existing, script)

        self.scripts[script.id] = script
        self._save_data()
        return script

    def _refresh_existing_fixture_project(self, existing: Script, fresh: Script) -> Script:
        """Refresh a reused fixture with the latest template while preserving stable identity."""
        fresh.id = existing.id
        fresh.created_at = existing.created_at
        fresh.updated_at = time.time()

        existing_frames = {frame.id: frame for frame in existing.frames}
        for frame in fresh.frames:
            existing_frame = existing_frames.get(frame.id)
            if not existing_frame:
                continue

            same_binding = (
                frame.scene_id == existing_frame.scene_id
                and frame.character_ids == existing_frame.character_ids
                and frame.prop_ids == existing_frame.prop_ids
            )
            if not same_binding:
                continue

            frame.image_url = existing_frame.image_url
            frame.image_asset = existing_frame.image_asset
            frame.rendered_image_url = existing_frame.rendered_image_url
            frame.rendered_image_asset = existing_frame.rendered_image_asset
            frame.status = existing_frame.status
            frame.updated_at = existing_frame.updated_at

        return fresh

    def _copy_fixture_reference_image(
        self, fixture_name: str, fixture_dir: str, reference_rel: str
    ) -> Optional[str]:
        if not reference_rel:
            return None
        source_path = _safe_resolve_path(fixture_dir, reference_rel)
        if not os.path.exists(source_path):
            return None
        _, ext = os.path.splitext(source_path)
        safe_name = re.sub(r"[^a-zA-Z0-9_\-]+", "-", fixture_name).strip("-") or "fixture"
        output_dir = _output_path("uploads", "fixtures")
        os.makedirs(output_dir, exist_ok=True)
        dest_path = os.path.join(output_dir, f"{safe_name}-storyboard-reference{ext or '.png'}")
        shutil.copyfile(source_path, dest_path)
        return output_media_ref(dest_path)

    def _copy_fixture_reference_asset(self, fixture_dir: str, reference_rel: str) -> Optional[str]:
        if not reference_rel:
            return None
        source_path = _safe_resolve_path(fixture_dir, reference_rel)
        if not os.path.exists(source_path):
            return None
        output_dir = _output_path("uploads", "fixtures")
        os.makedirs(output_dir, exist_ok=True)
        dest_path = os.path.join(output_dir, os.path.basename(source_path))
        shutil.copyfile(source_path, dest_path)
        return output_media_ref(dest_path)

    def _apply_fixture_reference_assets(
        self, script: Script, fixture_dir: str, manifest: Dict[str, Any]
    ) -> None:
        reference_assets = _flatten_fixture_reference_assets(manifest)
        if not reference_assets:
            return

        for item in reference_assets:
            asset_type = str(item.get("asset_type") or "").strip().lower()
            asset_id = str(item.get("asset_id") or "").strip()
            upload_type = str(item.get("upload_type") or "image").strip()
            source_rel = str(item.get("path") or "").strip()
            if not asset_type or not asset_id or not source_rel:
                continue

            copied_ref = self._copy_fixture_reference_asset(fixture_dir, source_rel)
            if not copied_ref:
                logger.warning(
                    "Skipping missing fixture reference asset %s/%s", asset_type, asset_id
                )
                continue

            prompt_used = str(
                item.get("prompt_used") or item.get("label") or item.get("description") or ""
            ).strip()
            locked = bool(item.get("locked", item.get("lock", True)))

            self._bind_fixture_reference_asset(
                script,
                asset_type=asset_type,
                asset_id=asset_id,
                upload_type=upload_type,
                image_url=copied_ref,
                prompt_used=prompt_used,
                locked=locked,
            )

    def _bind_fixture_reference_asset(
        self,
        script: Script,
        *,
        asset_type: str,
        asset_id: str,
        upload_type: str,
        image_url: str,
        prompt_used: str,
        locked: bool,
    ) -> None:
        now = time.time()
        variant_id = f"{asset_id}_{upload_type}_fixture"

        def make_variant() -> ImageVariant:
            return ImageVariant(
                id=variant_id,
                url=image_url,
                prompt_used=prompt_used or None,
                is_uploaded_source=True,
                upload_type=upload_type,
            )

        if asset_type == "character":
            target = next((item for item in script.characters if item.id == asset_id), None)
            if not target:
                logger.warning("Fixture character not found: %s", asset_id)
                return

            legacy_variant = make_variant()
            unit_variant = make_variant()

            if upload_type == "full_body":
                target.full_body = AssetUnit(
                    selected_image_id=variant_id,
                    image_variants=[unit_variant],
                    image_prompt=prompt_used or target.description,
                    image_updated_at=now,
                )
                if target.full_body_asset is None:
                    target.full_body_asset = ImageAsset()
                target.full_body_asset.variants = [legacy_variant]
                target.full_body_asset.selected_id = variant_id
                target.full_body_image_url = image_url
                target.image_url = image_url
            elif upload_type == "head_shot":
                target.head_shot = AssetUnit(
                    selected_image_id=variant_id,
                    image_variants=[unit_variant],
                    image_prompt=prompt_used or target.description,
                    image_updated_at=now,
                )
                if target.headshot_asset is None:
                    target.headshot_asset = ImageAsset()
                target.headshot_asset.variants = [legacy_variant]
                target.headshot_asset.selected_id = variant_id
                target.headshot_image_url = image_url
                target.avatar_url = image_url
            elif upload_type == "three_views":
                target.three_views = AssetUnit(
                    selected_image_id=variant_id,
                    image_variants=[unit_variant],
                    image_prompt=prompt_used or target.description,
                    image_updated_at=now,
                )
                if target.three_view_asset is None:
                    target.three_view_asset = ImageAsset()
                target.three_view_asset.variants = [legacy_variant]
                target.three_view_asset.selected_id = variant_id
                target.three_view_image_url = image_url
            elif upload_type == "expression_sheet":
                target.expression_sheet = AssetUnit(
                    selected_image_id=variant_id,
                    image_variants=[unit_variant],
                    image_prompt=prompt_used or target.description,
                    image_updated_at=now,
                )
                if target.expression_sheet_asset is None:
                    target.expression_sheet_asset = ImageAsset()
                target.expression_sheet_asset.variants = [legacy_variant]
                target.expression_sheet_asset.selected_id = variant_id
                target.expression_sheet_image_url = image_url
            else:
                logger.warning("Unsupported fixture character upload type: %s", upload_type)
                return

            target.locked = locked
            target.status = GenerationStatus.COMPLETED
            target.is_consistent = True
            target.full_body_updated_at = now
            if upload_type == "head_shot":
                target.headshot_updated_at = now
            elif upload_type == "three_views":
                target.three_view_updated_at = now
            elif upload_type == "expression_sheet":
                target.expression_sheet_updated_at = now
            return

        if asset_type == "scene":
            target = next((item for item in script.scenes if item.id == asset_id), None)
            if not target:
                logger.warning("Fixture scene not found: %s", asset_id)
                return

            target.image_asset = ImageAsset(
                selected_id=variant_id,
                variants=[make_variant()],
            )
            target.image_url = image_url
            target.image_prompt = prompt_used or target.description
            target.locked = locked
            target.status = GenerationStatus.COMPLETED
            return

        if asset_type == "prop":
            target = next((item for item in script.props if item.id == asset_id), None)
            if not target:
                logger.warning("Fixture prop not found: %s", asset_id)
                return

            target.image_asset = ImageAsset(
                selected_id=variant_id,
                variants=[make_variant()],
            )
            target.image_url = image_url
            target.image_prompt = prompt_used or target.description
            target.locked = locked
            target.status = GenerationStatus.COMPLETED
            return

        logger.warning("Unsupported fixture asset type: %s", asset_type)

    def _build_fixture_script_from_markdown(
        self,
        *,
        title: str,
        fixture_slug: str,
        fixture_project_type: str,
        markdown_text: str,
        manifest: Dict[str, Any],
        reference_media_ref: Optional[str],
    ) -> Script:
        script_id = str(uuid.uuid4())
        now = time.time()
        characters = self._build_default_liuyi_characters()
        scenes = self._build_default_liuyi_scenes()
        props = self._build_default_liuyi_props(reference_media_ref)
        scene_lookup = {scene.name: scene for scene in scenes}
        scene_by_id = {scene.id: scene for scene in scenes}
        character_by_id = {char.id: char for char in characters}
        prop_by_id = {prop.id: prop for prop in props}
        scene_keywords = {
            "医院": scene_lookup["中国医院普通病房"],
            "病房": scene_lookup["中国医院普通病房"],
            "床头": scene_lookup["中国医院普通病房"],
            "操场": scene_lookup["2008 年六一小学操场"],
            "舞台": scene_lookup["2008 年六一小学操场"],
            "校门": scene_lookup["2008 年小学校门口"],
            "家中": scene_lookup["家中夜晚书桌"],
            "台灯": scene_lookup["家中夜晚书桌"],
            "2026": scene_lookup["2026 年肝病科病房"],
        }
        default_scene = scene_lookup["2008 年六一小学操场"]
        fixture_scene_by_order = {
            1: "liuyi_scene_school_playground",
            2: "liuyi_scene_school_playground",
            3: "liuyi_scene_hospital_room",
            4: "liuyi_scene_hospital_room",
            5: "liuyi_scene_hospital_room",
            6: "liuyi_scene_school_playground",
            7: "liuyi_scene_hospital_room",
            8: "liuyi_scene_school_gate",
            9: "liuyi_scene_funeral_hall",
            10: "liuyi_scene_home_desk",
            11: "liuyi_scene_home_desk",
            12: "liuyi_scene_exam_admission",
            13: "liuyi_scene_medical_school",
            14: "liuyi_scene_doctor_office",
            15: "liuyi_scene_2026_ward",
            16: "liuyi_scene_2026_ward",
            17: "liuyi_scene_2026_ward",
            18: "liuyi_scene_hospital_corridor",
        }
        fixture_character_ids_by_order = {
            2: ["liuyi_char_xiaoqi_child"],
            3: ["liuyi_char_father"],
            4: ["liuyi_char_father", "liuyi_char_mother"],
            5: ["liuyi_char_father", "liuyi_char_mother"],
            6: ["liuyi_char_xiaoqi_child"],
            7: ["liuyi_char_mother"],
            8: ["liuyi_char_xiaoqi_child", "liuyi_char_mother"],
            9: ["liuyi_char_xiaoqi_child", "liuyi_char_father"],
            10: ["liuyi_char_xiaoqi_child", "liuyi_char_father"],
            11: ["liuyi_char_xiaoqi_young"],
            12: ["liuyi_char_xiaoqi_young"],
            13: ["liuyi_char_xiaoqi_young"],
            14: ["liuyi_char_xiaoqi_adult"],
            15: ["liuyi_char_xiaoqi_adult", "liuyi_char_boy", "liuyi_char_boy_father"],
            16: ["liuyi_char_xiaoqi_adult", "liuyi_char_boy", "liuyi_char_boy_father"],
            17: ["liuyi_char_xiaoqi_adult", "liuyi_char_boy", "liuyi_char_boy_father"],
            18: ["liuyi_char_xiaoqi_adult"],
        }
        fixture_prop_ids_by_order = {
            2: ["liuyi_prop_child_drawing"],
            3: ["liuyi_prop_white_bear", "liuyi_prop_paper_bag"],
            4: ["liuyi_prop_white_bear", "liuyi_prop_paper_bag"],
            5: ["liuyi_prop_white_bear", "liuyi_prop_paper_bag"],
            7: ["liuyi_prop_paper_bag"],
            8: ["liuyi_prop_white_bear", "liuyi_prop_paper_bag"],
            9: ["liuyi_prop_white_bear", "liuyi_prop_father_memorial_portrait"],
            10: ["liuyi_prop_white_bear", "liuyi_prop_family_photo", "liuyi_prop_notebook_pencil"],
            11: ["liuyi_prop_white_bear", "liuyi_prop_notebook_pencil"],
            12: ["liuyi_prop_white_bear", "liuyi_prop_admission_notice"],
            13: ["liuyi_prop_medical_textbooks"],
            14: ["liuyi_prop_white_bear", "liuyi_prop_medical_textbooks"],
            15: ["liuyi_prop_childrens_day_balloons"],
            16: ["liuyi_prop_childrens_day_balloons"],
            17: ["liuyi_prop_childrens_day_balloons"],
            18: ["liuyi_prop_white_bear", "liuyi_prop_medical_textbooks"],
        }

        frames: List[StoryboardFrame] = []
        story_beats: List[StoryBeat] = []
        for match in _SHOT_BLOCK_RE.finditer(markdown_text or ""):
            order = int(match.group("number"))
            time_range = match.group("time").strip()
            shot_title = match.group("title").strip()
            body = match.group("body")
            story_function = _strip_markdown_lines(_extract_markdown_section(body, "剧情功能"))
            visual_content = _strip_markdown_lines(_extract_markdown_section(body, "画面内容"))
            still_prompt = _strip_markdown_lines(_extract_markdown_section(body, "静帧画面提示词"))
            video_prompt = _strip_markdown_lines(
                _extract_markdown_section(body, "Seedance 2.0 图生视频提示词")
            )
            dialogue = _strip_markdown_lines(_extract_markdown_section(body, "台词 / 旁白"))
            notes = _strip_markdown_lines(_extract_markdown_section(body, "注意事项"))
            sound_design = _strip_markdown_lines(_extract_markdown_section(body, "声音设计"))
            combined_text = "\n".join([shot_title, visual_content, still_prompt, dialogue, notes])

            scene = scene_by_id.get(fixture_scene_by_order.get(order)) or next(
                (
                    candidate
                    for keyword, candidate in scene_keywords.items()
                    if keyword in combined_text
                ),
                default_scene,
            )
            character_ids = [
                char_id
                for char_id in fixture_character_ids_by_order.get(order, [])
                if char_id in character_by_id
            ]
            prop_ids = [
                prop_id
                for prop_id in fixture_prop_ids_by_order.get(order, [])
                if prop_id in prop_by_id
            ]
            beat_id = f"liuyi_beat_{order:02d}"
            beat_title = f"镜头 {order:02d}｜{shot_title}"
            composition_data = _build_fixture_reference_bindings(
                frame=StoryboardFrame(
                    id=f"liuyi_frame_{order:02d}",
                    scene_id=scene.id,
                    character_ids=character_ids,
                    prop_ids=prop_ids,
                ),
                scene=scene,
                characters=characters,
                props=props,
                order=order,
                story_function=story_function,
                notes=notes,
            )

            story_beats.append(
                StoryBeat(
                    id=beat_id,
                    order=order,
                    title=beat_title,
                    chapter_order=1,
                    chapter_title="18 镜头分镜提示词",
                    summary=story_function or visual_content,
                    action_summary=visual_content,
                    dialogue_excerpt=dialogue,
                    storyboard_goal=still_prompt,
                    scene_id=scene.id,
                    scene_name=scene.name,
                    character_ids=character_ids,
                    character_names=[char.name for char in characters if char.id in character_ids],
                    prop_ids=prop_ids,
                    prop_names=[prop.name for prop in props if prop.id in prop_ids],
                    source_excerpt=combined_text[:500],
                    storyboard_focus=(
                        f"{time_range}｜{story_function}" if story_function else time_range
                    ),
                )
            )
            frames.append(
                StoryboardFrame(
                    id=f"liuyi_frame_{order:02d}",
                    scene_id=scene.id,
                    story_beat_id=beat_id,
                    story_beat_title=beat_title,
                    story_beat_order=order,
                    chapter_order=1,
                    chapter_title="18 镜头分镜提示词",
                    character_ids=character_ids,
                    prop_ids=prop_ids,
                    action_description=visual_content,
                    dialogue=dialogue or None,
                    camera_angle="Medium Shot",
                    camera_movement=video_prompt,
                    composition=notes,
                    atmosphere=sound_design,
                    image_prompt=still_prompt,
                    image_prompt_cn=still_prompt,
                    video_prompt=video_prompt,
                    composition_data=composition_data,
                    status=GenerationStatus.PENDING,
                )
            )

        model_settings = manifest.get("model_settings", {}) if isinstance(manifest, dict) else {}
        codex_imagegen_policy = _normalize_codex_imagegen_policy(
            manifest.get("codex_imagegen_policy", {}) if isinstance(manifest, dict) else {}
        )
        story_analysis = self.script_processor.build_story_analysis(
            markdown_text, characters, scenes, props
        )
        story_analysis.scene_beats = story_beats
        story_analysis.plot_points = [beat.summary for beat in story_beats[:5] if beat.summary]
        story_analysis.summary = "《六一那天》18 镜头写实情绪短片分镜测试项目，围绕六一校园、医院离别、白色毛绒小熊和成年后的回望展开。"

        return Script(
            id=script_id,
            title=title,
            fixture_slug=fixture_slug,
            fixture_name=title,
            fixture_project_type=fixture_project_type or None,
            original_text=markdown_text,
            characters=characters,
            scenes=scenes,
            props=props,
            frames=frames,
            style_preset="realistic",
            style_prompt=f"fixture:{title}",
            art_direction=self._build_liuyi_art_direction(reference_media_ref),
            model_settings=ModelSettings(
                t2i_model=model_settings.get("t2i_model", DEFAULT_T2I_MODEL),
                i2i_model=model_settings.get("i2i_model", DEFAULT_I2I_MODEL),
                i2v_model=DEFAULT_I2V_MODEL,
                character_aspect_ratio=model_settings.get("character_aspect_ratio", "9:16"),
                scene_aspect_ratio=model_settings.get("scene_aspect_ratio", "16:9"),
                prop_aspect_ratio=model_settings.get("prop_aspect_ratio", "1:1"),
                storyboard_aspect_ratio=model_settings.get("storyboard_aspect_ratio", "16:9"),
            ),
            codex_imagegen_policy=codex_imagegen_policy,
            story_analysis=story_analysis,
            created_at=now,
            updated_at=now,
        )

    def _build_default_liuyi_characters(self) -> List[Character]:
        return [
            Character(
                id="liuyi_char_xiaoqi_child",
                name="小琪",
                aliases=["10 岁小琪", "中国小女孩"],
                description="10 岁中国小女孩，小学四年级，黑色齐肩短发，清秀偏瘦，眼神敏感克制。",
                age="10 岁",
                gender="女",
                clothing="浅蓝白色六一演出服",
                visual_weight=5,
                locked=True,
                status=GenerationStatus.PENDING,
            ),
            Character(
                id="liuyi_char_xiaoqi_young",
                name="青年小琪",
                aliases=["18 岁小琪", "年轻小琪", "医学院小琪"],
                description="18-20 岁中国女孩小琪，黑发扎起，眼神疲惫但专注，从高中毕业到医学院白大褂阶段，保留童年小琪的清秀轮廓。",
                age="18-20 岁",
                gender="女",
                clothing="白色短袖校服或干净白大褂，按镜头阶段变化",
                visual_weight=4,
                locked=False,
                status=GenerationStatus.PENDING,
            ),
            Character(
                id="liuyi_char_xiaoqi_adult",
                name="成年小琪",
                aliases=["28 岁小琪", "女医生小琪", "年轻女医生"],
                description="28 岁中国年轻女医生，黑色低马尾，面容温柔但坚定，气质克制专业，会蹲下与孩子平视。",
                age="28 岁",
                gender="女",
                clothing="干净长款白大褂，简洁胸牌不可读",
                visual_weight=5,
                locked=False,
                status=GenerationStatus.PENDING,
            ),
            Character(
                id="liuyi_char_mother",
                name="母亲",
                aliases=["妈妈"],
                description="30 多岁中国母亲，面容疲惫温柔，眼睛常因压抑哭意而发红，表演克制。",
                age="30 多岁",
                gender="女",
                clothing="朴素日常外套，医院和校门段保持现实质感",
                visual_weight=5,
                locked=True,
                status=GenerationStatus.PENDING,
            ),
            Character(
                id="liuyi_char_father",
                name="父亲",
                aliases=["爸爸"],
                description="40 岁左右中国父亲，病中消瘦苍白，眼神虚弱但温柔，是小熊礼物的托付者。",
                age="40 岁左右",
                gender="男",
                clothing="蓝白病号服",
                visual_weight=4,
                locked=True,
                status=GenerationStatus.PENDING,
            ),
            Character(
                id="liuyi_char_boy",
                name="小男孩",
                aliases=["2026 年小男孩", "男孩"],
                description="7-9 岁中国男孩，短发，穿浅色儿童节演出服，眼神天真又不安，期待父亲能去看自己的表演。",
                age="7-9 岁",
                gender="男",
                clothing="浅色儿童节演出服",
                visual_weight=4,
                locked=False,
                status=GenerationStatus.PENDING,
            ),
            Character(
                id="liuyi_char_boy_father",
                name="小男孩的父亲",
                aliases=["2026 年父亲", "病床上的父亲"],
                description="30 多岁到 40 岁中国男性肝病患者，躺在普通医院病床上，脸色苍白但清醒，看到孩子时努力微笑。",
                age="30 多岁到 40 岁",
                gender="男",
                clothing="普通医院病号服",
                visual_weight=4,
                locked=False,
                status=GenerationStatus.PENDING,
            ),
        ]

    def _build_default_liuyi_scenes(self) -> List[Scene]:
        return [
            Scene(
                id="liuyi_scene_school_playground",
                name="2008 年六一小学操场",
                description="中国小学操场，红色塑胶跑道、教学楼、彩旗和临时舞台，上午暖金色阳光，普通校园活动质感。",
                time_of_day="上午",
                lighting_mood="温暖金色自然光",
                visual_weight=5,
                locked=True,
                status=GenerationStatus.PENDING,
            ),
            Scene(
                id="liuyi_scene_hospital_room",
                name="中国医院普通病房",
                description="中国三甲医院普通病房，蓝白床单、冷白灯、床头柜和浅蓝绿色医用隐私帘，氛围安静沉重。",
                time_of_day="白天",
                lighting_mood="冷白蓝色医院灯光",
                visual_weight=5,
                locked=True,
                status=GenerationStatus.PENDING,
            ),
            Scene(
                id="liuyi_scene_school_gate",
                name="2008 年小学校门口",
                description="演出后的小学校门口，铁艺校门、绿树和散去的人群，温暖日光中带悲伤。",
                time_of_day="白天",
                lighting_mood="柔和自然光",
                visual_weight=4,
                locked=False,
                status=GenerationStatus.PENDING,
            ),
            Scene(
                id="liuyi_scene_funeral_hall",
                name="2008 年葬礼灵堂",
                description="中国家庭葬礼灵堂，中央父亲黑白遗照、白色花束和素色布置，安静克制，不阴森恐怖。",
                time_of_day="白天",
                lighting_mood="柔和低饱和光线",
                visual_weight=4,
                locked=False,
                status=GenerationStatus.PENDING,
            ),
            Scene(
                id="liuyi_scene_home_desk",
                name="家中夜晚书桌",
                description="家中夜晚，暖黄台灯、书桌、纸张和旧物，适合表现翻书、写字、回忆与克制情绪。",
                time_of_day="夜晚",
                lighting_mood="暖黄台灯光",
                visual_weight=3,
                locked=False,
                status=GenerationStatus.PENDING,
            ),
            Scene(
                id="liuyi_scene_exam_admission",
                name="2016 年高考录取书桌",
                description="中国高中毕业季书桌，堆满高考资料、试卷、闹钟和录取通知书，夏天自然光，旧白色小熊在桌角。",
                time_of_day="白天",
                lighting_mood="夏天暖色自然光",
                visual_weight=4,
                locked=False,
                status=GenerationStatus.PENDING,
            ),
            Scene(
                id="liuyi_scene_medical_school",
                name="中国医学院教室或实验楼走廊",
                description="中国医学院教室、实验室或实验楼走廊，白色工作台、显微镜、试剂瓶、厚重医学教材，冷白灯光。",
                time_of_day="白天",
                lighting_mood="冷白光与自然光混合",
                visual_weight=4,
                locked=False,
                status=GenerationStatus.PENDING,
            ),
            Scene(
                id="liuyi_scene_doctor_office",
                name="2026 年医生办公室",
                description="中国医院医生办公室，桌上有病历夹、医学书和听诊器，桌角坐着旧白色毛绒小熊，清晨冷白光中带暖阳。",
                time_of_day="清晨",
                lighting_mood="冷白光中带一点暖阳",
                visual_weight=4,
                locked=False,
                status=GenerationStatus.PENDING,
            ),
            Scene(
                id="liuyi_scene_2026_ward",
                name="2026 年肝病科病房",
                description="中国医院普通肝病科病房，蓝白床单、输液架、床头柜和窗边柔和晨光，父亲躺在病床上，小男孩拿彩色气球。",
                time_of_day="清晨",
                lighting_mood="柔和晨光，冷白转温暖",
                visual_weight=5,
                locked=False,
                status=GenerationStatus.PENDING,
            ),
            Scene(
                id="liuyi_scene_hospital_corridor",
                name="2026 年医院走廊",
                description="宽敞中国医院走廊，白墙和地砖，窗外晨光洒入，安静通透，适合成年小琪背影前行。",
                time_of_day="清晨",
                lighting_mood="温暖希望感晨光",
                visual_weight=4,
                locked=False,
                status=GenerationStatus.PENDING,
            ),
        ]

    def _build_default_liuyi_props(self, reference_media_ref: Optional[str]) -> List[Prop]:
        reference_asset = (
            ImageAsset(
                selected_id="liuyi_reference_collage",
                variants=[
                    ImageVariant(
                        id="liuyi_reference_collage",
                        url=reference_media_ref,
                        prompt_used="用户提供的分镜参考图拼图",
                        is_uploaded_source=True,
                        upload_type="image",
                    )
                ],
            )
            if reference_media_ref
            else None
        )
        return [
            Prop(
                id="liuyi_prop_white_bear",
                name="白色毛绒小熊",
                description="白色毛绒小熊，脖子系蓝色丝带，是父亲托付给小琪的核心礼物，所有镜头必须保持一致。",
                image_asset=reference_asset or ImageAsset(),
                image_url=reference_media_ref,
                locked=True,
                status=GenerationStatus.PENDING,
            ),
            Prop(
                id="liuyi_prop_paper_bag",
                name="浅色纸袋",
                description="浅色礼物纸袋，袋口露出白色毛绒小熊和蓝色丝带，纸袋有真实纸质褶皱。",
                locked=False,
                status=GenerationStatus.PENDING,
            ),
            Prop(
                id="liuyi_prop_child_drawing",
                name="儿童画",
                description="小琪手里攥着的微皱儿童画，画面可有手写痕迹但不要生成清晰可读文字。",
                locked=False,
                status=GenerationStatus.PENDING,
            ),
            Prop(
                id="liuyi_prop_family_photo",
                name="父女合照",
                description="普通深色相框里的父女生活照，父亲蹲在女儿身边整理红领巾，温暖生活感，不是遗照。",
                locked=False,
                status=GenerationStatus.PENDING,
            ),
            Prop(
                id="liuyi_prop_father_memorial_portrait",
                name="父亲遗照",
                description="普通深色相框中的父亲黑白遗照，照片里中年父亲笑得温和，只用于葬礼灵堂镜头。",
                locked=False,
                status=GenerationStatus.PENDING,
            ),
            Prop(
                id="liuyi_prop_notebook_pencil",
                name="作文本和铅笔",
                description="朴素小学生作文本与黄色木质铅笔，纸面文字不可读，承载小琪写下理想的镜头。",
                locked=False,
                status=GenerationStatus.PENDING,
            ),
            Prop(
                id="liuyi_prop_admission_notice",
                name="录取通知书",
                description="正式白色通知信封或录取通知书，干净整齐，文字不可读，和高考资料、旧小熊一起出现。",
                locked=False,
                status=GenerationStatus.PENDING,
            ),
            Prop(
                id="liuyi_prop_medical_textbooks",
                name="医学教材和病历夹",
                description="厚重医学教材、白色或浅蓝色病历夹，胸牌和文件文字都不可读，用于医学院和医生办公室阶段。",
                locked=False,
                status=GenerationStatus.PENDING,
            ),
            Prop(
                id="liuyi_prop_childrens_day_balloons",
                name="儿童节气球",
                description="红黄蓝彩色圆形气球，细绳自然下垂，小男孩攥着气球线，儿童节氛围轻轻点到。",
                locked=False,
                status=GenerationStatus.PENDING,
            ),
        ]

    def _build_liuyi_art_direction(self, reference_media_ref: Optional[str]) -> ArtDirection:
        style_config = {
            "id": "liuyi_realistic_cinema",
            "name": "六一那天写实电影感",
            "description": "中国现实题材，克制表演，儿童节暖金色与医院冷白蓝色形成情绪对比。",
            "positive_prompt": "写实电影感，中国现实题材，真实中国面孔，自然表演，克制情绪，浅景深，柔和自然光，轻微胶片颗粒，画面干净",
            "negative_prompt": "卡通，动画感，夸张表情，恐怖，血腥，手指畸形，多余手指，脸部变形，欧美面孔，科幻医院，过度磨皮，过曝，低清晰度，水印，logo，乱码文字，可读错误文字，廉价短剧感",
            "reference_images": [reference_media_ref] if reference_media_ref else [],
            "moodboard_notes": "童年校园段偏暖金色，医院临终段偏冷白蓝色，家中夜晚段为暖黄台灯光，2026 年医院段由冷白逐渐转为温暖晨光。",
            "is_custom": False,
        }
        return ArtDirection(
            selected_style_id="liuyi_realistic_cinema",
            style_config=style_config,
            custom_styles=[],
            ai_recommendations=[],
        )

    def reparse_project(self, script_id: str, text: str) -> Script:
        """Re-parse the text for an existing project, replacing all entities."""
        existing_script = self.scripts.get(script_id)
        if not existing_script:
            raise ValueError("Script not found")

        # Parse the new text (this generates new entities with new IDs)
        new_script = self.script_processor.parse_novel(existing_script.title, text)

        # Preserve the original script ID and timestamps
        new_script.id = existing_script.id
        new_script.created_at = existing_script.created_at
        new_script.updated_at = time.time()

        # Preserve project-level settings
        new_script.art_direction = existing_script.art_direction
        new_script.model_settings = existing_script.model_settings
        new_script.style_preset = existing_script.style_preset
        new_script.style_prompt = existing_script.style_prompt
        new_script.merged_video_url = existing_script.merged_video_url

        # Replace the script in memory
        self.scripts[script_id] = new_script
        self._save_data()
        return new_script

    def generate_assets(self, script_id: str) -> Script:
        """Step 2: Generate character and scene assets (Batch)."""
        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")

        logger.info(f"Generating assets for script {script.id}")

        # Sort characters: Base characters first (those without base_character_id)
        sorted_chars = sorted(script.characters, key=lambda c: 0 if not c.base_character_id else 1)

        for char in sorted_chars:
            if char.locked and _asset_has_static_reference(char):
                logger.info("Skipping locked character master reference: %s", char.id)
                continue
            self.generate_asset(script_id, char.id, "character")

        for scene in script.scenes:
            if scene.locked and _asset_has_static_reference(scene):
                logger.info("Skipping locked scene master reference: %s", scene.id)
                continue
            self.generate_asset(script_id, scene.id, "scene")

        for prop in script.props:
            if prop.locked and _asset_has_static_reference(prop):
                logger.info("Skipping locked prop master reference: %s", prop.id)
                continue
            self.generate_asset(script_id, prop.id, "prop")

        self._save_data()
        return script

    def generate_asset(
        self,
        script_id: str,
        asset_id: str,
        asset_type: str,
        style_preset: str = None,
        reference_image_url: str = None,
        style_prompt: str = None,
        generation_type: str = "all",
        prompt: str = None,
        apply_style: bool = True,
        negative_prompt: str = None,
        batch_size: int = 1,
        model_name: str = None,
    ) -> Script:
        """Step 2: Generate a specific asset (character/scene/prop).
        If style_preset is None, uses the project's global style."""
        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")

        # Get effective model names from project settings if not overridden
        t2i_model = model_name or script.model_settings.t2i_model
        i2i_model = script.model_settings.i2i_model

        # Get effective size based on asset type
        from .assets import ASPECT_RATIO_TO_SIZE

        if asset_type == "character":
            aspect_ratio = script.model_settings.character_aspect_ratio
            default_size = "576*1024"  # Portrait
        elif asset_type == "scene":
            aspect_ratio = script.model_settings.scene_aspect_ratio
            default_size = "1024*576"  # Landscape
        elif asset_type == "prop":
            aspect_ratio = script.model_settings.prop_aspect_ratio
            default_size = "1024*1024"  # Square
        else:
            aspect_ratio = "9:16"
            default_size = "576*1024"

        effective_size = ASPECT_RATIO_TO_SIZE.get(aspect_ratio, default_size)

        # Determine effective style: Art Direction > passed style > legacy style
        effective_positive_prompt = ""
        effective_negative_prompt = negative_prompt or ""  # Use passed negative prompt if available

        # Only calculate style prompt if apply_style is True
        if apply_style:
            if script.art_direction and script.art_direction.style_config:
                # Use Art Direction (highest priority)
                effective_positive_prompt = _build_art_direction_style_prompt(
                    script.art_direction.style_config
                )
                # Append global negative prompt if not overridden or append to it?
                # Let's append global negative prompt to the specific one for better results
                global_neg = script.art_direction.style_config.get("negative_prompt", "")
                if global_neg:
                    effective_negative_prompt = (
                        f"{effective_negative_prompt}, {global_neg}"
                        if effective_negative_prompt
                        else global_neg
                    )
            elif style_prompt:
                # Use passed style_prompt (for manual override)
                effective_positive_prompt = style_prompt
            elif style_preset:
                # Use passed style_preset (legacy)
                effective_positive_prompt = f"{style_preset} style"
            elif script.style_preset:
                # Fallback to script's legacy style_preset
                effective_positive_prompt = f"{script.style_preset} style"
                if script.style_prompt:
                    effective_positive_prompt += f", {script.style_prompt}"

        asset_list = []
        target_asset = None

        if asset_type == "character":
            asset_list = script.characters
        elif asset_type == "scene":
            asset_list = script.scenes
        elif asset_type == "prop":
            asset_list = script.props
        else:
            raise ValueError(f"Invalid asset_type: {asset_type}")

        target_asset = next((a for a in asset_list if a.id == asset_id), None)
        if not target_asset:
            raise ValueError(f"{asset_type.capitalize()} {asset_id} not found")
        if target_asset.locked and _asset_has_static_reference(target_asset):
            raise ValueError(
                f"{asset_type.capitalize()} {asset_id} is locked. Unlock it before regenerating the master reference."
            )

        target_asset.status = GenerationStatus.PROCESSING
        self._save_data()

        try:
            style_reference_paths = _get_art_direction_reference_paths(
                script.art_direction.style_config if script.art_direction else None
            )

            # Generate with Art Direction style injected
            if asset_type == "character":
                # Pass generation_type and specific prompt if available
                # If prompt is provided (from Workbench), use it directly.
                # Otherwise, asset_generator will construct it using effective_positive_prompt.
                # Note: If prompt is provided, we might still want to append style if it's not included?
                # For now, let's assume the Workbench passes the FULL prompt or we pass style separately.
                # The asset_generator.generate_character expects 'prompt' as the specific prompt.
                # If 'prompt' is None, it constructs one.
                # We should pass effective_positive_prompt as 'positive_prompt' (style suffix) to be appended if needed.
                self.asset_generator.generate_character(
                    target_asset,
                    generation_type=generation_type,
                    prompt=prompt,
                    positive_prompt=effective_positive_prompt,  # Used as style suffix if prompt is auto-generated
                    negative_prompt=effective_negative_prompt,
                    batch_size=batch_size,
                    model_name=t2i_model,
                    i2i_model_name=i2i_model,
                    size=effective_size,
                    style_reference_paths=style_reference_paths,
                )
            elif asset_type == "scene":
                self.asset_generator.generate_scene(
                    target_asset,
                    effective_positive_prompt,
                    effective_negative_prompt,
                    batch_size=batch_size,
                    model_name=t2i_model,
                    size=effective_size,
                    prompt=prompt or "",
                    style_reference_paths=style_reference_paths,
                )
            elif asset_type == "prop":
                self.asset_generator.generate_prop(
                    target_asset,
                    effective_positive_prompt,
                    effective_negative_prompt,
                    batch_size=batch_size,
                    model_name=t2i_model,
                    size=effective_size,
                    prompt=prompt or "",
                    style_reference_paths=style_reference_paths,
                )

            target_asset.status = GenerationStatus.COMPLETED
        except Exception as e:
            target_asset.status = GenerationStatus.FAILED
            raise e
        finally:
            self._save_data()

        return script

    def create_asset_generation_task(
        self,
        script_id: str,
        asset_id: str,
        asset_type: str,
        style_preset: str = None,
        reference_image_url: str = None,
        style_prompt: str = None,
        generation_type: str = "all",
        prompt: str = None,
        apply_style: bool = True,
        negative_prompt: str = None,
        batch_size: int = 1,
        model_name: str = None,
    ) -> Tuple[Script, str]:
        """Creates an async asset generation task and returns (script, task_id) immediately."""
        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")

        # Find the asset and set to PROCESSING
        asset_list = []
        if asset_type == "character":
            asset_list = script.characters
        elif asset_type == "scene":
            asset_list = script.scenes
        elif asset_type == "prop":
            asset_list = script.props
        else:
            raise ValueError(f"Invalid asset_type: {asset_type}")

        target_asset = next((a for a in asset_list if a.id == asset_id), None)
        if not target_asset:
            raise ValueError(f"{asset_type.capitalize()} {asset_id} not found")
        if target_asset.locked and _asset_has_static_reference(target_asset):
            raise ValueError(
                f"{asset_type.capitalize()} {asset_id} is locked. Unlock it before regenerating the master reference."
            )

        target_asset.status = GenerationStatus.PROCESSING

        # Create task
        task_id = str(uuid.uuid4())
        self.asset_generation_tasks[task_id] = {
            "status": "pending",  # pending -> processing -> completed/failed
            "progress": 0,
            "error": None,
            "script_id": script_id,
            "asset_id": asset_id,
            "asset_type": asset_type,
            "created_at": time.time(),
            # Store all params for later processing
            "params": {
                "style_preset": style_preset,
                "reference_image_url": reference_image_url,
                "style_prompt": style_prompt,
                "generation_type": generation_type,
                "prompt": prompt,
                "apply_style": apply_style,
                "negative_prompt": negative_prompt,
                "batch_size": batch_size,
                "model_name": model_name,
            },
        }

        self._save_data()
        return script, task_id

    def process_asset_generation_task(self, task_id: str):
        """Processes an asset generation task in the background."""
        task = self.asset_generation_tasks.get(task_id)
        if not task:
            logger.error(f"Task {task_id} not found")
            return

        task["status"] = "processing"

        try:
            params = task["params"]
            if task.get("is_series"):
                # Series asset generation — operate on series_store
                self._process_series_asset_task(task, params)
            else:
                # Project asset generation — existing logic
                self.generate_asset(
                    task["script_id"],
                    task["asset_id"],
                    task["asset_type"],
                    params["style_preset"],
                    params["reference_image_url"],
                    params["style_prompt"],
                    params["generation_type"],
                    params["prompt"],
                    params["apply_style"],
                    params["negative_prompt"],
                    params["batch_size"],
                    params["model_name"],
                )
            task["status"] = "completed"
            task["progress"] = 100
            logger.info(f"Task {task_id} completed successfully")
        except Exception as e:
            task["status"] = "failed"
            task["error"] = str(e)
            logger.error(f"Task {task_id} failed: {e}")

    def _process_series_asset_task(self, task: Dict, params: Dict):
        """Process a Series asset generation task."""
        series_id = task["script_id"]  # stored as script_id for compatibility
        series = self.series_store.get(series_id)
        if not series:
            raise ValueError("Series not found")

        asset_id = task["asset_id"]
        asset_type = task["asset_type"]
        positive_prompt = params.get("effective_positive_prompt", "")
        negative_prompt = params.get("effective_negative_prompt", "")
        t2i_model = params.get("t2i_model", DEFAULT_T2I_MODEL)
        effective_size = params.get("effective_size", "576*1024")
        batch_size = params.get("batch_size", 1)
        generation_type = params.get("generation_type", "all")
        prompt = params.get("prompt")
        reference_image_url = params.get("reference_image_url")

        if asset_type == "character":
            target = next((c for c in series.characters if c.id == asset_id), None)
            if not target:
                raise ValueError(f"Character {asset_id} not found in series")
            self.asset_generator.generate_character(
                target,
                generation_type=generation_type,
                prompt=prompt or "",
                positive_prompt=positive_prompt,
                negative_prompt=negative_prompt,
                batch_size=batch_size,
                model_name=t2i_model,
                size=effective_size,
            )
        elif asset_type == "scene":
            target = next((s for s in series.scenes if s.id == asset_id), None)
            if not target:
                raise ValueError(f"Scene {asset_id} not found in series")
            self.asset_generator.generate_scene(
                target,
                positive_prompt=positive_prompt,
                negative_prompt=negative_prompt,
                batch_size=batch_size,
                model_name=t2i_model,
                size=effective_size,
                prompt=prompt or "",
            )
        elif asset_type == "prop":
            target = next((p for p in series.props if p.id == asset_id), None)
            if not target:
                raise ValueError(f"Prop {asset_id} not found in series")
            self.asset_generator.generate_prop(
                target,
                positive_prompt=positive_prompt,
                negative_prompt=negative_prompt,
                batch_size=batch_size,
                model_name=t2i_model,
                size=effective_size,
                prompt=prompt or "",
            )
        else:
            raise ValueError(f"Unknown asset type: {asset_type}")

        self._save_series_data()

    def get_asset_generation_task_status(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Returns the status of an asset generation task."""
        # Check image tasks first
        task = self.asset_generation_tasks.get(task_id)
        if not task:
            # Then check video tasks
            task = self.video_generation_tasks.get(task_id)

        if not task:
            return None

        return {
            "task_id": task_id,
            "status": task["status"],
            "progress": task.get("progress", 0),
            "error": task.get("error"),
            "asset_id": task.get("asset_id"),
            "asset_type": task.get("asset_type"),
            "script_id": task.get("script_id"),
            "created_at": task.get("created_at"),
        }

    def create_motion_ref_task(
        self,
        script_id: str,
        asset_id: str,
        asset_type: str,
        prompt: Optional[str] = None,
        audio_url: Optional[str] = None,
        duration: int = 5,
        batch_size: int = 1,
    ) -> Tuple[Script, str]:
        """Creates an async motion reference generation task."""
        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")

        task_id = str(uuid.uuid4())
        self.video_generation_tasks[task_id] = {
            "status": "pending",
            "progress": 0,
            "error": None,
            "script_id": script_id,
            "asset_id": asset_id,
            "asset_type": asset_type,
            "created_at": time.time(),
            "params": {
                "prompt": prompt,
                "audio_url": audio_url,
                "duration": duration,
                "batch_size": batch_size,
            },
        }

        self._save_data()
        return script, task_id

    def process_motion_ref_task(self, script_id: str, task_id: str):
        """Processes a video generation task in the background."""
        task = self.video_generation_tasks.get(task_id)
        if not task:
            logger.error(f"Video task {task_id} not found")
            return

        task["status"] = "processing"

        try:
            params = task["params"]
            # Call the synchronous generate_motion_ref method
            self.generate_motion_ref(
                script_id=script_id,
                asset_id=task["asset_id"],
                asset_type=task["asset_type"],
                prompt=params["prompt"],
                audio_url=params["audio_url"],
                duration=params["duration"],
                batch_size=params["batch_size"],
            )
            task["status"] = "completed"
            task["progress"] = 100
            logger.info(f"Video task {task_id} completed successfully")
        except Exception as e:
            task["status"] = "failed"
            task["error"] = str(e)
            logger.error(f"Video task {task_id} failed: {e}")

    def sync_descriptions_from_script_entities(self, script_id: str) -> Script:
        """
        Syncs entity descriptions from ScriptProcessor parsed entities.
        This clears saved prompts so the UI will regenerate them from the current description.

        Note: This only updates prompts, not generated images/videos.
        """
        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")

        # Clear saved prompts for all characters so UI will regenerate from description
        for character in script.characters:
            character.full_body_prompt = None
            character.three_view_prompt = None
            character.headshot_prompt = None
            character.video_prompt = None

        # Scenes and props might also have prompts to clear (if applicable)
        for scene in script.scenes:
            if hasattr(scene, "prompt"):
                scene.prompt = None

        for prop in script.props:
            if hasattr(prop, "prompt"):
                prop.prompt = None

        self._save_data()
        logger.info(
            f"Descriptions synced for script {script_id}: cleared prompts for {len(script.characters)} characters, {len(script.scenes)} scenes, {len(script.props)} props"
        )
        return script

    def add_character(self, script_id: str, name: str, description: str) -> Script:
        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")

        new_char = Character(id=f"char_{uuid.uuid4().hex[:8]}", name=name, description=description)
        script.characters.append(new_char)
        self._save_data()
        return script

    def delete_character(self, script_id: str, char_id: str) -> Script:
        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")

        script.characters = [c for c in script.characters if c.id != char_id]
        self._save_data()
        return script

    def add_scene(self, script_id: str, name: str, description: str) -> Script:
        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")

        new_scene = Scene(id=f"scene_{uuid.uuid4().hex[:8]}", name=name, description=description)
        script.scenes.append(new_scene)
        self._save_data()
        return script

    def delete_scene(self, script_id: str, scene_id: str) -> Script:
        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")

        script.scenes = [s for s in script.scenes if s.id != scene_id]
        self._save_data()
        return script

    def toggle_asset_lock(self, script_id: str, asset_id: str, asset_type: str) -> Script:
        """Toggle the locked status of an asset."""
        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")

        target_asset = None
        if asset_type == "character":
            target_asset = next((c for c in script.characters if c.id == asset_id), None)
        elif asset_type == "scene":
            target_asset = next((s for s in script.scenes if s.id == asset_id), None)
        elif asset_type == "prop":
            target_asset = next((p for p in script.props if p.id == asset_id), None)

        if not target_asset:
            raise ValueError(f"Asset {asset_id} of type {asset_type} not found")

        # Toggle the locked status
        target_asset.locked = not target_asset.locked
        self._save_data()
        return script

    def toggle_frame_lock(self, script_id: str, frame_id: str) -> Script:
        """Toggle the locked status of a frame."""
        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")

        target_frame = next((f for f in script.frames if f.id == frame_id), None)
        if not target_frame:
            raise ValueError(f"Frame {frame_id} not found")

        # Toggle the locked status
        target_frame.locked = not target_frame.locked
        self._save_data()
        return script

    def update_asset_image(
        self, script_id: str, asset_id: str, asset_type: str, image_url: str
    ) -> Script:
        """Updates the image URL of an asset manually."""
        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")

        target_asset = None
        if asset_type == "character":
            target_asset = next((c for c in script.characters if c.id == asset_id), None)
        elif asset_type == "scene":
            target_asset = next((s for s in script.scenes if s.id == asset_id), None)
        elif asset_type == "prop":
            target_asset = next((p for p in script.props if p.id == asset_id), None)

        if not target_asset:
            raise ValueError(f"Asset {asset_id} of type {asset_type} not found")

        target_asset.image_url = image_url
        # For characters, also update avatar if it's not set or if we want to sync them
        # For now, let's assume the uploaded image is the main reference.
        # If it's a character, we might want to set avatar_url to the same image for simplicity
        if asset_type == "character":
            target_asset.avatar_url = image_url

        self._save_data()
        return script

    def update_asset_description(
        self, script_id: str, asset_id: str, asset_type: str, description: str
    ) -> Script:
        """Updates the description of an asset."""
        return self.update_asset_attributes(
            script_id, asset_id, asset_type, {"description": description}
        )

    def update_asset_attributes(
        self, script_id: str, asset_id: str, asset_type: str, attributes: Dict[str, Any]
    ) -> Script:
        """Updates arbitrary attributes of an asset."""
        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")

        target_asset = None
        if asset_type == "character":
            target_asset = next((c for c in script.characters if c.id == asset_id), None)
        elif asset_type == "scene":
            target_asset = next((s for s in script.scenes if s.id == asset_id), None)
        elif asset_type == "prop":
            target_asset = next((p for p in script.props if p.id == asset_id), None)

        if not target_asset:
            raise ValueError(f"Asset {asset_id} of type {asset_type} not found")

        # Update attributes
        for key, value in attributes.items():
            if hasattr(target_asset, key):
                setattr(target_asset, key, value)
            else:
                logger.warning(f"Attribute {key} not found in {asset_type} model")

        self._save_data()
        return script

    def add_uploaded_asset_variant(
        self,
        script_id: str,
        asset_type: str,
        asset_id: str,
        upload_type: str,
        image_url: str,
        description: Optional[str] = None,
    ) -> Script:
        """
        Adds an uploaded image as a new variant to an asset.
        The uploaded image is marked with is_uploaded_source=True.

        Args:
            script_id: The project ID
            asset_type: "character", "scene", or "prop"
            asset_id: The asset ID
            upload_type: "full_body", "head_shot", "three_views", "expression_sheet", or "image"
            image_url: URL of the uploaded image (OSS Object Key)
            description: Optional modified description for reverse generation
        """
        from .models import ImageVariant, AssetUnit

        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")

        # Find target asset
        target_asset = None
        if asset_type == "character":
            target_asset = next((c for c in script.characters if c.id == asset_id), None)
        elif asset_type == "scene":
            target_asset = next((s for s in script.scenes if s.id == asset_id), None)
        elif asset_type == "prop":
            target_asset = next((p for p in script.props if p.id == asset_id), None)

        if not target_asset:
            raise ValueError(f"Asset {asset_id} of type {asset_type} not found")

        # Create new variant with upload source flag
        new_variant = ImageVariant(
            id=str(uuid.uuid4()),
            url=image_url,
            prompt_used=description or target_asset.description,
            is_uploaded_source=True,
            upload_type=upload_type,
        )

        # Update description if provided
        if description:
            target_asset.description = description

        # Add variant to the appropriate asset unit
        if asset_type == "character":
            # Map upload_type to the correct asset unit
            if upload_type == "full_body":
                target_unit = target_asset.full_body
            elif upload_type == "head_shot":
                target_unit = target_asset.head_shot
            elif upload_type == "three_views":
                target_unit = target_asset.three_views
            elif upload_type == "expression_sheet":
                target_unit = target_asset.expression_sheet
            else:
                raise ValueError(f"Invalid upload_type for character: {upload_type}")

            # Ensure AssetUnit exists
            if target_unit is None:
                target_unit = AssetUnit()
                if upload_type == "full_body":
                    target_asset.full_body = target_unit
                elif upload_type == "head_shot":
                    target_asset.head_shot = target_unit
                elif upload_type == "three_views":
                    target_asset.three_views = target_unit
                elif upload_type == "expression_sheet":
                    target_asset.expression_sheet = target_unit

            # Add variant and select it
            target_unit.image_variants.append(new_variant)
            target_unit.selected_image_id = new_variant.id
            target_unit.image_updated_at = time.time()

            # === ALSO UPDATE LEGACY FIELDS for frontend compatibility ===
            # Create variant for legacy ImageAsset structure
            legacy_variant = ImageVariant(
                id=new_variant.id,
                url=image_url,
                prompt_used=description or target_asset.description,
                is_uploaded_source=True,
                upload_type=upload_type,
            )

            if upload_type == "full_body":
                # Ensure full_body_asset exists
                if target_asset.full_body_asset is None:
                    from .models import ImageAsset

                    target_asset.full_body_asset = ImageAsset()
                target_asset.full_body_asset.variants.append(legacy_variant)
                target_asset.full_body_asset.selected_id = new_variant.id
                target_asset.full_body_image_url = image_url
            elif upload_type == "head_shot":
                # Ensure headshot_asset exists
                if target_asset.headshot_asset is None:
                    from .models import ImageAsset

                    target_asset.headshot_asset = ImageAsset()
                target_asset.headshot_asset.variants.append(legacy_variant)
                target_asset.headshot_asset.selected_id = new_variant.id
                target_asset.headshot_image_url = image_url
            elif upload_type == "three_views":
                # Ensure three_view_asset exists
                if target_asset.three_view_asset is None:
                    from .models import ImageAsset

                    target_asset.three_view_asset = ImageAsset()
                target_asset.three_view_asset.variants.append(legacy_variant)
                target_asset.three_view_asset.selected_id = new_variant.id
                target_asset.three_view_image_url = image_url
            elif upload_type == "expression_sheet":
                # Ensure expression_sheet_asset exists
                if target_asset.expression_sheet_asset is None:
                    from .models import ImageAsset

                    target_asset.expression_sheet_asset = ImageAsset()
                target_asset.expression_sheet_asset.variants.append(legacy_variant)
                target_asset.expression_sheet_asset.selected_id = new_variant.id
                target_asset.expression_sheet_image_url = image_url

            logger.info(
                f"Added uploaded variant {new_variant.id} to character {asset_id} {upload_type}"
            )

        elif asset_type in ["scene", "prop"]:
            # Scene and Prop have a single 'image' asset unit
            if not hasattr(target_asset, "image") or target_asset.image is None:
                target_asset.image = AssetUnit()

            target_asset.image.image_variants.append(new_variant)
            target_asset.image.selected_image_id = new_variant.id
            target_asset.image.image_updated_at = time.time()

            # Also update legacy image_url field
            target_asset.image_url = image_url

            logger.info(f"Added uploaded variant {new_variant.id} to {asset_type} {asset_id}")

        self._save_data()
        return script

    def update_project_style(
        self, script_id: str, style_preset: str, style_prompt: Optional[str] = None
    ) -> Script:
        """Updates the global style settings for a project."""
        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")

        script.style_preset = style_preset
        script.style_prompt = style_prompt
        script.updated_at = time.time()
        self._save_data()
        return script

    def save_art_direction(
        self,
        script_id: str,
        selected_style_id: str,
        style_config: Dict[str, Any],
        custom_styles: List[Dict[str, Any]] = None,
        ai_recommendations: List[Dict[str, Any]] = None,
    ) -> Script:
        """Saves the Art Direction configuration."""
        from .models import ArtDirection

        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")

        normalized_style_config = _normalize_art_direction_style_config(style_config)

        # Create Art Direction object
        art_direction = ArtDirection(
            selected_style_id=selected_style_id,
            style_config=normalized_style_config,
            custom_styles=custom_styles or [],
            ai_recommendations=ai_recommendations or [],
        )

        script.art_direction = art_direction
        script.updated_at = time.time()
        self._save_data()
        return script

    # === STORYBOARD DRAMATIZATION v2 ===

    def _build_storyboard_entities_json(
        self,
        all_characters: List[Character],
        all_scenes: List[Scene],
        all_props: List[Any],
    ) -> Dict[str, Any]:
        return {
            "characters": [
                {"id": c.id, "name": c.name, "description": c.description} for c in all_characters
            ],
            "scenes": [
                {"id": s.id, "name": s.name, "description": s.description} for s in all_scenes
            ],
            "props": [
                {"id": p.id, "name": p.name, "description": p.description} for p in all_props
            ],
        }

    def _find_story_beat(
        self, story_analysis: Optional[StoryAnalysis], beat_id: str
    ) -> Optional[StoryBeat]:
        if not story_analysis or not beat_id:
            return None
        return next((beat for beat in story_analysis.scene_beats if beat.id == beat_id), None)

    def _resolve_story_beat_for_frame(
        self,
        frame_data: Dict[str, Any],
        story_analysis: Optional[StoryAnalysis],
        *,
        sequence_cursor: int = 0,
        default_beat: Optional[StoryBeat] = None,
    ) -> Optional[StoryBeat]:
        explicit_id = str(frame_data.get("story_beat_id", "") or "").strip()
        explicit_title = str(frame_data.get("story_beat_title", "") or "").strip()
        scene_ref_name = str(frame_data.get("scene_ref_name", "") or "").strip()
        character_names = {
            str(name or "").strip()
            for name in frame_data.get("character_ref_names", []) or []
            if str(name or "").strip()
        }
        prop_names = {
            str(name or "").strip()
            for name in frame_data.get("prop_ref_names", []) or []
            if str(name or "").strip()
        }

        logger.debug(
            "Story beat resolve start: explicit_id=%s explicit_title=%s scene_ref=%s character_refs=%s prop_refs=%s sequence_cursor=%s candidate_count=%s",
            explicit_id or "-",
            explicit_title or "-",
            scene_ref_name or "-",
            sorted(character_names) or [],
            sorted(prop_names) or [],
            sequence_cursor,
            len(story_analysis.scene_beats) if story_analysis and story_analysis.scene_beats else 0,
        )

        if default_beat:
            logger.debug(
                "Story beat resolve: using default beat beat_id=%s order=%s title=%s chapter_order=%s chapter_title=%s",
                default_beat.id,
                default_beat.order,
                default_beat.title,
                default_beat.chapter_order,
                default_beat.chapter_title or "-",
            )
            return default_beat
        if not story_analysis or not story_analysis.scene_beats:
            logger.debug("Story beat resolve: no structured beats available, returning None")
            return None

        if explicit_id:
            matched = self._find_story_beat(story_analysis, explicit_id)
            logger.debug(
                "Story beat resolve: explicit id lookup beat_id=%s matched=%s",
                explicit_id,
                matched.id if matched else "none",
            )
            if matched:
                return matched

        if explicit_title:
            for beat in story_analysis.scene_beats:
                if (
                    beat.title == explicit_title
                    or explicit_title in beat.title
                    or beat.title in explicit_title
                ):
                    logger.debug(
                        "Story beat resolve: explicit title lookup matched beat_id=%s order=%s title=%s",
                        beat.id,
                        beat.order,
                        beat.title,
                    )
                    return beat
            logger.debug(
                "Story beat resolve: explicit title lookup found no direct match for title=%s",
                explicit_title,
            )

        best_match: Optional[StoryBeat] = None
        best_score = 0.0
        for beat in story_analysis.scene_beats:
            beat_scene_name = str(beat.scene_name or beat.location_hint or "").strip()
            scene_score = 0.0
            scene_match_detail = "none"
            if scene_ref_name and beat_scene_name:
                if beat_scene_name == scene_ref_name:
                    scene_score += 5
                    scene_match_detail = "exact(+5)"
                elif scene_ref_name in beat_scene_name or beat_scene_name in scene_ref_name:
                    scene_score += 3
                    scene_match_detail = "partial(+3)"

            beat_character_names = {
                str(name or "").strip() for name in beat.character_names if str(name or "").strip()
            }
            beat_prop_names = {
                str(name or "").strip() for name in beat.prop_names if str(name or "").strip()
            }
            matched_characters = sorted(character_names.intersection(beat_character_names))
            matched_props = sorted(prop_names.intersection(beat_prop_names))
            character_score = 2 * len(matched_characters)
            prop_score = 1 * len(matched_props)
            sequence_bonus = 0.25 if beat.order >= sequence_cursor else 0.0
            score = scene_score + character_score + prop_score + sequence_bonus

            logger.debug(
                "Story beat resolve candidate: beat_id=%s order=%s title=%s scene_match=%s scene_score=%.2f matched_characters=%s character_score=%.2f matched_props=%s prop_score=%.2f sequence_bonus=%.2f total=%.2f",
                beat.id,
                beat.order,
                beat.title,
                scene_match_detail,
                scene_score,
                matched_characters or [],
                character_score,
                matched_props or [],
                prop_score,
                sequence_bonus,
                score,
            )

            if score > best_score:
                best_score = score
                best_match = beat

        logger.debug(
            "Story beat resolve result: best_match=%s best_order=%s best_title=%s best_score=%.2f sequence_cursor=%s",
            best_match.id if best_match else "none",
            best_match.order if best_match else "none",
            best_match.title if best_match else "none",
            best_score,
            sequence_cursor,
        )
        if best_match and best_score > 0:
            return best_match

        if len(story_analysis.scene_beats) == 1:
            logger.debug(
                "Story beat resolve: falling back to the only structured beat beat_id=%s order=%s title=%s",
                story_analysis.scene_beats[0].id,
                story_analysis.scene_beats[0].order,
                story_analysis.scene_beats[0].title,
            )
            return story_analysis.scene_beats[0]

        return None

    def _convert_raw_frames_to_storyboard_frames(
        self,
        raw_frames: List[Dict[str, Any]],
        all_characters: List[Character],
        all_scenes: List[Scene],
        all_props: List[Any],
        story_analysis: Optional[StoryAnalysis],
        *,
        default_beat: Optional[StoryBeat] = None,
    ) -> List[StoryboardFrame]:
        new_frames: List[StoryboardFrame] = []
        current_beat_order = default_beat.order if default_beat else 0

        for frame_data in raw_frames:
            scene_ref_name = str(frame_data.get("scene_ref_name", "") or "").strip()
            scene_id = None
            for scene in all_scenes:
                if scene.name == scene_ref_name or (
                    scene_ref_name and scene_ref_name in scene.name
                ):
                    scene_id = scene.id
                    break
            if not scene_id and all_scenes:
                scene_id = all_scenes[0].id
            elif not scene_id:
                scene_id = str(uuid.uuid4())

            character_ids: List[str] = []
            for char_name in frame_data.get("character_ref_names", []) or []:
                for char in all_characters:
                    if char.name == char_name or (char_name and char_name in char.name):
                        character_ids.append(char.id)
                        break

            prop_ids: List[str] = []
            for prop_name in frame_data.get("prop_ref_names", []) or []:
                for prop in all_props:
                    if prop.name == prop_name or (prop_name and prop_name in prop.name):
                        prop_ids.append(prop.id)
                        break

            story_beat = self._resolve_story_beat_for_frame(
                frame_data,
                story_analysis,
                sequence_cursor=current_beat_order,
                default_beat=default_beat,
            )
            if story_beat:
                current_beat_order = max(current_beat_order, story_beat.order)

            dialogue = frame_data.get("dialogue")
            speaker = frame_data.get("speaker")
            if isinstance(dialogue, dict):
                speaker = dialogue.get("speaker") or speaker
                dialogue = dialogue.get("text")

            generation_source = str(frame_data.get("generation_source") or "llm")
            generation_degraded = bool(frame_data.get("generation_degraded", False))
            generation_reason = str(frame_data.get("generation_reason", "") or "").strip() or None

            new_frames.append(
                StoryboardFrame(
                    id=str(uuid.uuid4()),
                    scene_id=scene_id,
                    story_beat_id=(
                        story_beat.id
                        if story_beat
                        else str(frame_data.get("story_beat_id", "") or "").strip() or None
                    ),
                    story_beat_title=(
                        story_beat.title
                        if story_beat
                        else str(frame_data.get("story_beat_title", "") or "").strip() or None
                    ),
                    story_beat_order=story_beat.order if story_beat else None,
                    chapter_order=(
                        story_beat.chapter_order if story_beat else frame_data.get("chapter_order")
                    ),
                    chapter_title=(
                        story_beat.chapter_title
                        if story_beat
                        else str(frame_data.get("chapter_title", "") or "").strip() or None
                    ),
                    character_ids=character_ids,
                    prop_ids=prop_ids,
                    action_description=frame_data.get("action_description", ""),
                    visual_atmosphere=frame_data.get("visual_atmosphere"),
                    shot_size=frame_data.get("shot_size"),
                    camera_angle=frame_data.get("camera_angle", "平视"),
                    camera_movement=frame_data.get("camera_movement"),
                    dialogue=dialogue,
                    speaker=speaker,
                    generation_source=generation_source,
                    generation_degraded=generation_degraded,
                    generation_reason=generation_reason,
                    status=GenerationStatus.PENDING,
                )
            )

        return new_frames

    def _replace_story_beat_frames(
        self,
        script: Script,
        beat_id: str,
        new_frames: List[StoryboardFrame],
        story_analysis: Optional[StoryAnalysis],
    ) -> None:
        existing_indices = [
            index for index, frame in enumerate(script.frames) if frame.story_beat_id == beat_id
        ]
        remaining_frames = [frame for frame in script.frames if frame.story_beat_id != beat_id]

        if existing_indices:
            insert_at = sum(
                1
                for frame in script.frames[: min(existing_indices)]
                if frame.story_beat_id != beat_id
            )
        else:
            target_beat = self._find_story_beat(story_analysis, beat_id)
            target_order = target_beat.order if target_beat else None
            insert_at = len(remaining_frames)
            if target_order is not None:
                for index, frame in enumerate(remaining_frames):
                    frame_order = (
                        frame.story_beat_order
                        if frame.story_beat_order is not None
                        else float("inf")
                    )
                    if frame_order > target_order:
                        insert_at = index
                        break

        script.frames = remaining_frames[:insert_at] + new_frames + remaining_frames[insert_at:]

    def get_story_analysis(self, script_id: str) -> StoryAnalysis:
        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")

        if script.story_analysis and script.story_analysis.scene_beats:
            return script.story_analysis

        resolved = self.resolve_episode_assets(script)
        story_analysis = self.script_processor.build_story_analysis(
            script.original_text,
            resolved["characters"],
            resolved["scenes"],
            resolved["props"],
        )
        script.story_analysis = story_analysis
        script.updated_at = time.time()
        self._save_data()
        return story_analysis

    def update_story_beat(self, script_id: str, beat_id: str, **updates) -> Script:
        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")

        story_analysis = self.get_story_analysis(script_id)
        beat = self._find_story_beat(story_analysis, beat_id)
        if not beat:
            raise ValueError("Story beat not found")

        allowed_fields = ("action_summary", "dialogue_excerpt", "storyboard_goal")
        for field in allowed_fields:
            if field in updates and updates[field] is not None:
                setattr(beat, field, str(updates[field]).strip())

        script.story_analysis = story_analysis
        script.updated_at = time.time()
        self._save_data()
        return script

    def analyze_story_beat_to_frames(self, script_id: str, beat_id: str) -> Script:
        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")

        logger.info(f"Analyzing story beat %s to frames for project %s", beat_id, script_id)
        resolved = self.resolve_episode_assets(script)
        all_characters = resolved["characters"]
        all_scenes = resolved["scenes"]
        all_props = resolved["props"]
        entities_json = self._build_storyboard_entities_json(all_characters, all_scenes, all_props)
        story_analysis = self.get_story_analysis(script_id)
        target_beat = self._find_story_beat(story_analysis, beat_id)
        if not target_beat:
            raise ValueError("Story beat not found")

        raw_frames = self.script_processor.analyze_to_storyboard(
            text=script.original_text,
            entities_json=entities_json,
            story_analysis=story_analysis,
            target_beat_id=beat_id,
        )
        if not raw_frames:
            raise RuntimeError("AI 场次分镜重算未返回任何帧数据，请重试。")

        new_frames = self._convert_raw_frames_to_storyboard_frames(
            raw_frames,
            all_characters,
            all_scenes,
            all_props,
            story_analysis,
            default_beat=target_beat,
        )
        self._replace_story_beat_frames(script, beat_id, new_frames, story_analysis)
        script.story_analysis = story_analysis
        _set_script_generation_metadata(
            script,
            "storyboard_analysis",
            new_frames[0].generation_source if new_frames else "llm",
            any(frame.generation_degraded for frame in new_frames),
            details={
                "frame_count": len(new_frames),
                "target_beat_id": beat_id,
            },
        )
        script.updated_at = time.time()
        self._save_data()
        return script

    def analyze_text_to_frames(self, script_id: str, text: str) -> Script:
        """
        Analyzes script text and generates storyboard frames using LLM.
        Replaces existing frames with newly generated ones.
        """
        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")

        logger.info(f"Analyzing text to frames for project {script_id}")

        # Resolve assets (merge Series + Episode if applicable)
        resolved = self.resolve_episode_assets(script)
        all_characters = resolved["characters"]
        all_scenes = resolved["scenes"]
        all_props = resolved["props"]
        entities_json = self._build_storyboard_entities_json(all_characters, all_scenes, all_props)

        story_analysis = (
            script.story_analysis
            if script.original_text == text and getattr(script, "story_analysis", None)
            else self.script_processor.build_story_analysis(
                text, all_characters, all_scenes, all_props
            )
        )

        # Call LLM to analyze text (may raise RuntimeError on parse failure)
        raw_frames = self.script_processor.analyze_to_storyboard(
            text, entities_json, story_analysis
        )

        if not raw_frames:
            raise RuntimeError("AI 分镜分析未返回任何帧数据，请重试。")

        new_frames = self._convert_raw_frames_to_storyboard_frames(
            raw_frames,
            all_characters,
            all_scenes,
            all_props,
            story_analysis,
        )

        script.frames = new_frames
        script.story_analysis = story_analysis
        _set_script_generation_metadata(
            script,
            "storyboard_analysis",
            new_frames[0].generation_source if new_frames else "llm",
            any(frame.generation_degraded for frame in new_frames),
            details={
                "frame_count": len(new_frames),
                "source_text_length": len(text),
            },
        )
        script.updated_at = time.time()

        logger.info(f"Generated {len(new_frames)} frames from text analysis")
        self._save_data()
        return script

    def refine_frame_prompt(
        self,
        script_id: str,
        frame_id: str,
        raw_prompt: str,
        assets: List[Dict[str, Any]],
        feedback: str = "",
    ) -> Dict[str, Any]:
        """
        Refines a raw prompt into bilingual (CN/EN) prompts using LLM.
        Also updates the frame with the refined prompts.
        """
        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")

        logger.debug(f"Refining prompt for frame {frame_id}")

        # Read custom prompt config with 3-level fallback (Episode → Series → default)
        series = self.series_store.get(script.series_id) if script.series_id else None
        custom_prompt = self.get_effective_prompt("storyboard_polish", script, series)
        # If it's the system default, pass empty so the LLM method uses its built-in default
        from .llm import DEFAULT_STORYBOARD_POLISH_PROMPT

        if custom_prompt == DEFAULT_STORYBOARD_POLISH_PROMPT:
            custom_prompt = ""

        # Call LLM to refine prompt
        result = self.script_processor.polish_storyboard_prompt(
            raw_prompt, assets, feedback, custom_prompt
        )

        # Find and update the frame
        frame_found = False
        for frame in script.frames:
            if frame.id == frame_id:
                frame.image_prompt_cn = result.get("prompt_cn")
                frame.image_prompt_en = result.get("prompt_en")
                frame.image_prompt = result.get("prompt_en")  # Also update legacy field
                frame.updated_at = time.time()
                frame_found = True
                break

        if frame_found:
            self._save_data()

        return {
            "prompt_cn": result.get("prompt_cn"),
            "prompt_en": result.get("prompt_en"),
            "frame_updated": frame_found,
            "generation_source": result.get("generation_source", "fallback"),
            "generation_degraded": bool(result.get("generation_degraded", False)),
            "generation_reason": result.get("generation_reason"),
        }

    def generate_storyboard(self, script_id: str) -> Script:
        """Step 3: Generate storyboard images (Initial/Batch)."""
        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")

        script = self.storyboard_generator.generate_storyboard(script)
        self._save_data()
        return script

    def update_frame(self, script_id: str, frame_id: str, **kwargs) -> Script:
        """Update frame data (prompt, scene_id, character_ids, etc.)."""
        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")

        frame = next((f for f in script.frames if f.id == frame_id), None)
        if not frame:
            raise ValueError(f"Frame {frame_id} not found")

        # Update only provided fields
        if kwargs.get("image_prompt") is not None:
            frame.image_prompt = kwargs["image_prompt"]
        if kwargs.get("action_description") is not None:
            frame.action_description = kwargs["action_description"]
        if kwargs.get("dialogue") is not None:
            frame.dialogue = kwargs["dialogue"]
        if kwargs.get("camera_angle") is not None:
            frame.camera_angle = kwargs["camera_angle"]
        if kwargs.get("scene_id") is not None:
            frame.scene_id = kwargs["scene_id"]
        if kwargs.get("character_ids") is not None:
            frame.character_ids = kwargs["character_ids"]

        self._save_data()
        return script

    def add_frame(
        self,
        script_id: str,
        scene_id: str = None,
        action_description: str = "",
        camera_angle: str = "medium_shot",
        insert_at: int = None,
    ) -> Script:
        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")

        new_frame = StoryboardFrame(
            id=f"frame_{uuid.uuid4().hex[:8]}",
            scene_id=scene_id or (script.scenes[0].id if script.scenes else ""),
            character_ids=[],
            action_description=action_description,
            camera_angle=camera_angle,
        )

        if insert_at is not None and 0 <= insert_at <= len(script.frames):
            script.frames.insert(insert_at, new_frame)
        else:
            script.frames.append(new_frame)

        self._save_data()
        return script

    def copy_frame(self, script_id: str, frame_id: str, insert_at: int = None) -> Script:
        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")

        original_frame = next((f for f in script.frames if f.id == frame_id), None)
        if not original_frame:
            raise ValueError(f"Frame {frame_id} not found")

        # Create a deep copy with new ID
        new_frame = original_frame.copy()
        new_frame.id = f"frame_{uuid.uuid4().hex[:8]}"
        new_frame.updated_at = time.time()
        # Reset generation status and URLs for the copy?
        # Usually copy implies copying content, but maybe we want to keep the image?
        # Let's keep the image/content but reset status if it was processing?
        # Actually, if we copy, we probably want the same image reference initially.
        # But we should reset the "locked" status maybe?
        new_frame.locked = False

        if insert_at is not None and 0 <= insert_at <= len(script.frames):
            script.frames.insert(insert_at, new_frame)
        else:
            # Insert after the original frame by default
            try:
                original_index = script.frames.index(original_frame)
                script.frames.insert(original_index + 1, new_frame)
            except ValueError:
                script.frames.append(new_frame)

        self._save_data()
        return script

    def delete_frame(self, script_id: str, frame_id: str) -> Script:
        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")

        script.frames = [f for f in script.frames if f.id != frame_id]
        self._save_data()
        return script

    def reorder_frames(self, script_id: str, frame_ids: List[str]) -> Script:
        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")

        frame_map = {f.id: f for f in script.frames}
        new_frames = []
        for fid in frame_ids:
            if fid in frame_map:
                new_frames.append(frame_map[fid])

        script.frames = new_frames
        self._save_data()
        return script

    def generate_motion_ref(
        self,
        script_id: str,
        asset_id: str,
        asset_type: str,  # 'full_body' | 'head_shot' for characters; 'scene' | 'prop' for scenes and props
        prompt: Optional[str] = None,
        audio_url: Optional[str] = None,
        duration: int = 5,
        batch_size: int = 1,
    ) -> Script:
        """Generate Motion Reference video for an asset (Character Full Body/Headshot, Scene, or Prop).

        Args:
            script_id: ID of the project/script
            asset_id: ID of the asset (character, scene, or prop)
            asset_type: 'full_body' | 'head_shot' for characters; 'scene' or 'prop' for scenes and props
            prompt: Custom prompt for motion generation
            audio_url: URL of driving audio for lip-sync
            duration: Video duration in seconds (5 or 10)
            batch_size: Number of videos to generate
        """
        from .models import VideoVariant, AssetUnit, VideoTask

        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")

        # Find the target asset based on type
        target_asset = None
        asset_display_name = ""

        if asset_type in ["full_body", "head_shot"]:
            # Find the character
            target_asset = next((c for c in script.characters if c.id == asset_id), None)
            asset_display_name = "Character"
        elif asset_type == "scene":
            # Find the scene
            target_asset = next((s for s in script.scenes if s.id == asset_id), None)
            asset_display_name = "Scene"
        elif asset_type == "prop":
            # Find the prop
            target_asset = next((p for p in script.props if p.id == asset_id), None)
            asset_display_name = "Prop"
        else:
            raise ValueError(
                f"Invalid asset_type: {asset_type}. Must be 'full_body', 'head_shot', 'scene', or 'prop'"
            )

        if not target_asset:
            raise ValueError(f"{asset_display_name} {asset_id} not found")

        # Get the appropriate AssetUnit or image URL based on the asset type
        asset_unit = None  # For characters with AssetUnit
        generated_videos = []  # Store generated videos

        if asset_type in ["full_body", "head_shot"]:
            # Handle character asset
            asset_unit = getattr(target_asset, asset_type, None)
            # Get source image from the AssetUnit or legacy field
            if asset_unit and asset_unit.selected_image_id:
                source_img = next(
                    (v for v in asset_unit.image_variants if v.id == asset_unit.selected_image_id),
                    None,
                )
                source_image_url = (
                    source_img.url
                    if source_img
                    else (
                        target_asset.full_body_image_url
                        if asset_type == "full_body"
                        else target_asset.headshot_image_url
                    )
                )
            else:
                source_image_url = (
                    target_asset.full_body_image_url
                    if asset_type == "full_body"
                    else target_asset.headshot_image_url
                )

            # Default prompt for character
            if not prompt:
                if audio_url:
                    prompt = f"{asset_type.replace('_', ' ').title()} character reference video. {target_asset.description}. The character is speaking naturally matching the audio, with accurate lip-sync and facial expressions. Stable camera, high quality, 4k."
                else:
                    prompt = f"{asset_type.replace('_', ' ').title()} character reference video. {target_asset.description}. Looking around, breathing, slight movement, subtle gestures. Stable camera, high quality, 4k."
        else:
            # Handle scene or prop assets
            source_image_url = target_asset.image_url
            # Default prompt for scene and prop
            if not prompt:
                if asset_type == "scene":
                    if audio_url:
                        prompt = f"Cinematic scene video reference of {target_asset.name}. {target_asset.description}. Ambient motion, lighting changes, natural elements moving, birds, clouds. Soundscape matching the audio. High quality, 4k."
                    else:
                        prompt = f"Cinematic scene video reference of {target_asset.name}. {target_asset.description}. Ambient motion, lighting changes, natural elements moving, birds, clouds. Slow pan across the scene. High quality, 4k."
                else:  # prop
                    if audio_url:
                        prompt = f"Cinematic prop video reference of {target_asset.name}. {target_asset.description}. Rotating object, detailed textures visible, ambient motion, subtle movements matching audio. High quality, 4k."
                    else:
                        prompt = f"Cinematic prop video reference of {target_asset.name}. {target_asset.description}. Rotating object, detailed textures visible, ambient motion, subtle movements. High quality, 4k."

        # Check if source image exists
        if not source_image_url:
            raise ValueError(
                f"No source image available for {asset_type}. Please generate a static image first."
            )

        # Generate videos based on the asset type
        for i in range(batch_size):
            try:
                # Call video generator (I2V)
                video_result = self.video_generator.generate_i2v(
                    image_url=source_image_url,
                    prompt=prompt,
                    duration=duration,
                    audio_url=audio_url,
                )

                if video_result and video_result.get("video_url"):
                    if asset_type in ["full_body", "head_shot"]:
                        # For characters, create VideoVariant in AssetUnit
                        video_variant = VideoVariant(
                            id=f"video_{uuid.uuid4().hex[:8]}",
                            url=video_result["video_url"],
                            prompt_used=prompt,
                            audio_url=audio_url,
                            source_image_id=None,  # Don't set this to avoid complications
                        )
                        asset_unit.video_variants.append(video_variant)

                        # Auto-select the first generated video
                        if not asset_unit.selected_video_id:
                            asset_unit.selected_video_id = video_variant.id

                        generated_videos.append(video_variant)
                        logger.info(f"Generated motion ref video: {video_variant.id}")
                    else:
                        # For scenes and props, create VideoTask and add to asset's video_assets
                        video_task = VideoTask(
                            id=f"video_{uuid.uuid4().hex[:8]}",
                            project_id=script_id,
                            asset_id=asset_id,
                            image_url=source_image_url,
                            prompt=prompt,
                            status="completed",  # Since generation is done in this step
                            video_url=video_result["video_url"],
                            duration=duration,
                            created_at=time.time(),
                            generate_audio=bool(audio_url),
                            model="wan2.6-i2v",
                            generation_mode="i2v",  # Image to video (motion reference)
                        )

                        # Add to the asset's video_assets
                        target_asset.video_assets.append(video_task)
                        generated_videos.append(video_task)
                        logger.info(f"Generated motion ref video for {asset_type}: {video_task.id}")
            except Exception as e:
                logger.error(f"Failed to generate motion ref video for {asset_type}: {e}")

        # For character assets, update the AssetUnit
        if asset_type in ["full_body", "head_shot"]:
            # Ensure AssetUnit exists
            if asset_unit is None:
                asset_unit = AssetUnit()
                setattr(target_asset, asset_type, asset_unit)

            asset_unit.video_prompt = prompt
            asset_unit.video_updated_at = time.time()
        # For scene and prop assets, the video tasks are already added in the generation loop above

        if batch_size > 0 and not generated_videos:
            raise RuntimeError(f"Failed to generate any motion reference videos for {asset_type}")

        self._save_data()
        return script

    def generate_storyboard_render(
        self,
        script_id: str,
        frame_id: str,
        composition_data: Optional[Dict[str, Any]],
        prompt: str,
        batch_size: int = 1,
    ) -> Script:
        """Step 3b: Render a specific frame from composition data."""
        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")

        frame = next((f for f in script.frames if f.id == frame_id), None)
        if not frame:
            raise ValueError(f"Frame {frame_id} not found")

        frame.status = GenerationStatus.PROCESSING
        if composition_data:
            frame.composition_data = composition_data
        frame.image_prompt = prompt
        self._save_data()

        try:
            # Extract reference image URL from composition data if available
            ref_image_url = None
            ref_image_urls = []

            if composition_data:
                ref_image_url = composition_data.get("reference_image_url")
                ref_image_urls = composition_data.get("reference_image_urls", [])

            ref_image_paths = []

            # Resolve multiple paths
            for url in ref_image_urls:
                resolved_url = _resolve_reference_path(url)
                if resolved_url:
                    ref_image_paths.append(resolved_url)

            # Also handle single path if provided (legacy support)
            if ref_image_url and ref_image_url not in ref_image_urls:
                resolved_single = _resolve_reference_path(ref_image_url)
                if resolved_single and resolved_single not in ref_image_paths:
                    ref_image_paths.append(resolved_single)

            continuity_lock = True
            if composition_data and "continuity_lock" in composition_data:
                continuity_lock = bool(composition_data.get("continuity_lock"))

            # Find scene for this frame
            scene = next((s for s in script.scenes if s.id == frame.scene_id), None)
            frame_index = script.frames.index(frame)
            previous_frame = script.frames[frame_index - 1] if frame_index > 0 else None
            next_frame = (
                script.frames[frame_index + 1] if frame_index < len(script.frames) - 1 else None
            )

            if continuity_lock and previous_frame and previous_frame.scene_id == frame.scene_id:
                previous_ref = _resolve_reference_path(
                    _get_selected_frame_reference(previous_frame)
                )
                if previous_ref and previous_ref not in ref_image_paths:
                    ref_image_paths.insert(0, previous_ref)

            if continuity_lock and next_frame and next_frame.scene_id == frame.scene_id:
                next_ref = _resolve_reference_path(_get_selected_frame_reference(next_frame))
                if next_ref and next_ref not in ref_image_paths:
                    ref_image_paths.append(next_ref)

            for asset_ref in self._collect_frame_asset_reference_paths(script, frame, scene):
                if asset_ref not in ref_image_paths:
                    ref_image_paths.append(asset_ref)

            style_config = script.art_direction.style_config if script.art_direction else None
            for style_ref in _get_art_direction_reference_paths(style_config):
                if style_ref not in ref_image_paths:
                    ref_image_paths.append(style_ref)

            continuity_hint = ""
            if continuity_lock and (
                (previous_frame and previous_frame.scene_id == frame.scene_id)
                or (next_frame and next_frame.scene_id == frame.scene_id)
            ):
                continuity_hint = build_storyboard_continuity_hint(
                    scene_name=scene.name if scene else None,
                    previous_action=(
                        previous_frame.action_description
                        if previous_frame and previous_frame.scene_id == frame.scene_id
                        else None
                    ),
                    next_action=(
                        next_frame.action_description
                        if next_frame and next_frame.scene_id == frame.scene_id
                        else None
                    ),
                )

            # Use the prompt as-is from frontend, but append continuity guardrails when this is the same scene.
            core_prompt = prompt or ""
            final_prompt = core_prompt
            style_prompt = _build_art_direction_style_prompt(style_config)
            if style_prompt and style_prompt not in final_prompt:
                final_prompt = f"{style_prompt}. {final_prompt}".strip(". ").strip()
            if continuity_hint and continuity_hint not in final_prompt:
                final_prompt = f"{final_prompt.rstrip()} {continuity_hint}".strip()

            # Remove duplicates and expose first path for legacy support
            ref_image_paths = list(dict.fromkeys(ref_image_paths))
            original_ref_image_paths = list(ref_image_paths)
            frame.image_prompt = final_prompt

            # Get effective size from storyboard_aspect_ratio
            from .assets import ASPECT_RATIO_TO_SIZE

            storyboard_aspect_ratio = script.model_settings.storyboard_aspect_ratio
            effective_size = ASPECT_RATIO_TO_SIZE.get(
                storyboard_aspect_ratio, "1024*576"
            )  # Default to landscape

            # Use model from settings
            i2i_model = script.model_settings.i2i_model
            render_model = i2i_model
            suppress_auto_references = False
            safe_render_strategy = _build_safe_storyboard_render_strategy(
                frame=frame,
                scene=scene,
                characters=script.characters,
                props=script.props,
                prompt=core_prompt,
                ref_image_paths=ref_image_paths,
                model_name=i2i_model,
            )
            if safe_render_strategy:
                frame.composition_data = dict(frame.composition_data or {})
                frame.composition_data["render_strategy"] = safe_render_strategy
                _, reference_payload_preflight = _build_storyboard_reference_payload_preflight(
                    script=script,
                    frame=frame,
                    ref_image_paths=original_ref_image_paths,
                    send_references=False,
                    prepare_references=False,
                )
                frame.composition_data["reference_payload_preflight"] = reference_payload_preflight
                suppress_auto_references = True
                ref_image_paths = []
                render_model = script.model_settings.t2i_model
                staged_prompt_note = (
                    "分阶段基础构图要求：本次先生成完整基础构图，不使用参考图直接图编；"
                    "人物保持日常、清醒、克制，不出现手术、血液、插管、急救、伤口或危重画面；"
                    "后续只用单参考图局部编辑分别校准人物身份、服装和道具。"
                )
                if staged_prompt_note not in final_prompt:
                    final_prompt = f"{final_prompt.rstrip()}\n\n{staged_prompt_note}".strip()
                frame.image_prompt = final_prompt
                logger.info(
                    "Using staged safe storyboard render for frame %s; omitted %s references.",
                    frame_id,
                    safe_render_strategy["omitted_reference_count"],
                )
            else:
                ref_image_paths, reference_payload_preflight = (
                    _build_storyboard_reference_payload_preflight(
                        script=script,
                        frame=frame,
                        ref_image_paths=ref_image_paths,
                        send_references=True,
                        prepare_references=True,
                    )
                )
                frame.composition_data = dict(frame.composition_data or {})
                frame.composition_data["reference_payload_preflight"] = reference_payload_preflight

            frame_codex_insights = build_codex_reference_recommendation_for_frame(
                _model_dump_compat(script),
                _model_dump_compat(frame),
                script.codex_imagegen_policy,
                continuity_lock=continuity_lock,
                include_style_references=True,
            )
            frame.composition_data = dict(frame.composition_data or {})
            frame.composition_data["codex_imagegen_reference_preview"] = frame_codex_insights["preview"]
            frame.composition_data["codex_imagegen_recommendation"] = frame_codex_insights["recommendation"]
            frame.composition_data["codex_imagegen_handoff_plan"] = frame_codex_insights["handoff_plan"]

            ref_image_path = ref_image_paths[0] if ref_image_paths else None
            logger.info(
                f"Rendering frame {frame_id} using model {render_model} with {len(ref_image_paths)} reference images"
            )
            if len(ref_image_urls) > 0:
                logger.debug(f"Original reference URLs from frontend: {ref_image_urls}")

            # Call generator
            self.storyboard_generator.generate_frame(
                frame,
                script.characters,
                scene,
                ref_image_path=ref_image_path,
                ref_image_paths=ref_image_paths,
                prompt=final_prompt,
                batch_size=batch_size,
                size=effective_size,
                model_name=render_model,
                raise_on_error=True,
                suppress_auto_references=suppress_auto_references,
            )

            self._save_data()
            return script
        except Exception as e:
            logger.error(f"Frame rendering failed: {e}")
            frame.status = GenerationStatus.FAILED
            self._save_data()
            raise

    def compose_frame_crops(
        self,
        script_id: str,
        frame_id: str,
        manifest_path: Optional[str] = None,
        output_path: Optional[str] = None,
        verify: bool = True,
    ) -> Script:
        """Compose edited crop outputs into a selected rendered frame variant."""
        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")

        frame = next((f for f in script.frames if f.id == frame_id), None)
        if not frame:
            raise ValueError(f"Frame {frame_id} not found")

        frame.status = GenerationStatus.PROCESSING
        self._save_data()

        try:
            if manifest_path:
                resolved_manifest_path = _ensure_project_path(
                    resolve_manifest_path(manifest_path),
                    "manifest_path",
                )
            else:
                resolved_manifest_path = _resolve_fixture_crop_manifest_path(script, frame)
                if not resolved_manifest_path:
                    raise ValueError(
                        f"No default crop composition manifest found for frame {frame_id}."
                    )

            resolved_output_override = None
            if output_path:
                resolved_output_override = _ensure_output_path(resolve_manifest_path(output_path))

            compose_result = compose_frame_crops_from_manifest(
                resolved_manifest_path,
                out_override=resolved_output_override,
                verify=verify,
            )
            composed_path = _ensure_output_path(compose_result["output_image"])

            from ...utils.oss_utils import OSSImageUploader

            uploader = OSSImageUploader()
            oss_url = uploader.upload_image(str(composed_path))
            image_url = oss_url if oss_url else output_media_ref(str(composed_path))

            variant = ImageVariant(
                id=str(uuid.uuid4()),
                url=image_url,
                prompt_used=(
                    "Composed frame crops from " f"{_project_relative_path(resolved_manifest_path)}"
                ),
                is_uploaded_source=False,
                upload_type="image",
            )

            if not frame.rendered_image_asset:
                frame.rendered_image_asset = ImageAsset()

            frame.rendered_image_asset.variants.append(variant)
            frame.rendered_image_asset.selected_id = variant.id
            frame.rendered_image_url = image_url
            frame.image_url = image_url
            frame.status = GenerationStatus.COMPLETED
            frame.updated_at = time.time()

            crop_composition = {
                "source": "compose_frame_crops",
                "manifest_path": _project_relative_path(resolved_manifest_path),
                "base_image": _project_relative_path(compose_result["base_image"]),
                "output_image": output_media_ref(str(composed_path)),
                "image_url": image_url,
                "frame_id": compose_result.get("frame_id") or frame_id,
                "project_slug": compose_result.get("project_slug") or script.fixture_slug,
                "schema_version": compose_result.get("schema_version"),
                "verified": verify,
                "composed_at": time.time(),
                "crops": [
                    {
                        "id": crop["id"],
                        "role": crop.get("role"),
                        "bbox": crop["bbox"],
                        "base_crop": (
                            _project_relative_path(crop["base_crop"])
                            if crop.get("base_crop")
                            else None
                        ),
                        "edited_crop": _project_relative_path(crop["edited_crop"]),
                        "prompt": crop.get("prompt"),
                        "reference_images": list(crop.get("reference_images") or []),
                    }
                    for crop in compose_result["crops"]
                ],
            }
            frame.composition_data = dict(frame.composition_data or {})
            frame.composition_data["crop_composition"] = crop_composition

            script.updated_at = time.time()
            self._save_data()
            return script
        except Exception:
            frame.status = GenerationStatus.FAILED
            frame.updated_at = time.time()
            self._save_data()
            raise

    def generate_video(self, script_id: str) -> Script:
        """Step 4: Generate video clips."""
        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")

        script = self.video_generator.generate_video(script)
        self._save_data()
        return script

    def create_video_task(
        self,
        script_id: str,
        image_url: str,
        prompt: str,
        duration: int = 5,
        seed: int = None,
        resolution: str = "720p",
        generate_audio: bool = False,
        audio_url: str = None,
        prompt_extend: bool = True,
        negative_prompt: str = None,
        model: str = DEFAULT_I2V_MODEL,
        frame_id: str = None,
        aspect_ratio: str = None,
        watermark: bool = False,
        camera_fixed: bool = None,
        reference_audio_url: str = None,
        seedance_reference_mode: str = None,
        seedance_workflow: str = None,
        seedance_extend_mode: str = None,
        seedance_edit_mode: str = None,
        shot_type: str = "single",
        generation_mode: str = "i2v",
        reference_video_urls: list = None,
        mode: str = None,
        sound: str = None,
        cfg_scale: float = None,
        vidu_audio: bool = None,
        movement_amplitude: str = None,
    ) -> Tuple[Script, str]:
        """Creates a new video generation task."""
        script = self.get_script(script_id)
        if not script:
            raise ValueError("Script not found")

        task_id = str(uuid.uuid4())

        # If R2V mode is selected, use the R2V model
        if generation_mode == "r2v":
            model = "wan2.6-r2v"

        # Snapshot the input image to ensure consistency
        snapshot_url = image_url
        try:
            # Resolve source path
            if image_url and not image_url.startswith("http"):
                # Assume relative to output dir
                src_path = _safe_resolve_output_ref(image_url)
                if os.path.exists(src_path) and os.path.isfile(src_path):
                    # Create snapshot dir
                    snapshot_dir = _output_path("video_inputs")
                    os.makedirs(snapshot_dir, exist_ok=True)

                    # Define snapshot path
                    ext = os.path.splitext(os.path.basename(image_url))[1] or ".png"
                    _validate_safe_id(task_id, "task_id")
                    snapshot_filename = f"{task_id}{ext}"
                    snapshot_path = _safe_resolve_path(snapshot_dir, snapshot_filename)

                    # Copy file
                    import shutil

                    shutil.copy2(src_path, snapshot_path)

                    # Update URL to relative path
                    snapshot_url = f"video_inputs/{snapshot_filename}"
        except Exception as e:
            logger.error(f"Failed to snapshot input image: {e}")
            # Fallback to original URL

        task = VideoTask(
            id=task_id,
            project_id=script_id,
            frame_id=frame_id,
            image_url=snapshot_url,
            prompt=prompt,
            status="pending",
            duration=duration,
            seed=seed,
            resolution=resolution,
            generate_audio=generate_audio,
            audio_url=audio_url,
            prompt_extend=prompt_extend,
            negative_prompt=negative_prompt,
            model=model,
            aspect_ratio=aspect_ratio,
            watermark=watermark,
            camera_fixed=camera_fixed,
            reference_audio_url=reference_audio_url,
            seedance_reference_mode=seedance_reference_mode,
            seedance_workflow=seedance_workflow,
            seedance_extend_mode=seedance_extend_mode,
            seedance_edit_mode=seedance_edit_mode,
            shot_type=shot_type,
            generation_mode=generation_mode,
            reference_video_urls=reference_video_urls or [],
            mode=mode,
            sound=sound,
            cfg_scale=cfg_scale,
            vidu_audio=vidu_audio,
            movement_amplitude=movement_amplitude,
            created_at=time.time(),
        )

        if not script.video_tasks:
            script.video_tasks = []
        script.video_tasks.append(task)

        self._save_data()
        return script, task_id

    def extract_last_frame(self, script_id: str, frame_id: str, video_task_id: str) -> Script:
        """Extract the last frame from a video task and add it as a variant of the frame's rendered_image_asset."""
        from .models import ImageVariant, ImageAsset

        script = self.get_script(script_id)
        if not script:
            raise ValueError("Script not found")

        frame = next((f for f in script.frames if f.id == frame_id), None)
        if not frame:
            raise ValueError("Frame not found")

        # Find the video task
        video_task = next((t for t in script.video_tasks if t.id == video_task_id), None)
        if not video_task or video_task.status != "completed" or not video_task.video_url:
            raise ValueError("Video task not found or not completed")

        # Resolve video path
        video_path = video_task.video_url
        if not video_path.startswith("/") and not video_path.startswith("http"):
            video_path = _safe_resolve_output_ref(video_path)

        if video_path.startswith("http"):
            # Download to temp file first
            video_path = self._download_temp_image(video_path)

        if not os.path.exists(video_path):
            raise ValueError(f"Video file not found: {video_path}")

        # Extract last frame using FFmpeg
        ffmpeg_path = get_ffmpeg_path()
        if not ffmpeg_path:
            raise RuntimeError("FFmpeg is required for frame extraction but was not found.")

        output_dir = _output_path("storyboard")
        os.makedirs(output_dir, exist_ok=True)
        _validate_safe_id(frame_id, "frame_id")
        output_filename = f"frame_{frame_id}_lastframe_{uuid.uuid4().hex[:8]}.jpg"
        output_path = _safe_resolve_path(output_dir, output_filename)

        cmd = [
            ffmpeg_path,
            "-sseof",
            "-0.1",
            "-i",
            video_path,
            "-frames:v",
            "1",
            "-q:v",
            "2",
            "-y",
            output_path,
        ]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode != 0:
                raise RuntimeError(f"FFmpeg error: {result.stderr}")
        except subprocess.TimeoutExpired:
            raise RuntimeError("FFmpeg frame extraction timed out")

        if not os.path.exists(output_path):
            raise RuntimeError("Failed to extract last frame from video")

        # Upload to OSS if configured
        from ...utils.oss_utils import OSSImageUploader

        uploader = OSSImageUploader()
        oss_url = uploader.upload_image(output_path)
        image_url = oss_url if oss_url else output_media_ref(output_path)

        # Create new variant
        variant = ImageVariant(
            id=str(uuid.uuid4()),
            url=image_url,
            prompt_used="Extracted last frame from video",
            is_uploaded_source=True,
            upload_type="image",
        )

        # Initialize rendered_image_asset if needed
        if not frame.rendered_image_asset:
            frame.rendered_image_asset = ImageAsset()

        frame.rendered_image_asset.variants.append(variant)
        frame.rendered_image_asset.selected_id = variant.id
        # Also update rendered_image_url so VideoCreator can pick it up
        frame.rendered_image_url = image_url

        script.updated_at = time.time()
        self._save_data()
        return script

    def upload_frame_image(self, script_id: str, frame_id: str, image_path: str) -> Script:
        """Upload an image as a variant of the frame's rendered_image_asset."""
        from .models import ImageVariant, ImageAsset

        # Validate that image_path is inside the output directory
        safe_rel_path = (
            output_media_ref(image_path)
            if os.path.isabs(image_path)
            else normalize_project_media_ref(image_path)
        )
        safe_path = _safe_resolve_output_ref(safe_rel_path)

        script = self.get_script(script_id)
        if not script:
            raise ValueError("Script not found")

        frame = next((f for f in script.frames if f.id == frame_id), None)
        if not frame:
            raise ValueError("Frame not found")

        # Upload to OSS if configured
        from ...utils.oss_utils import OSSImageUploader

        uploader = OSSImageUploader()
        oss_url = uploader.upload_image(safe_path)
        image_url = oss_url if oss_url else output_media_ref(safe_path)

        # Create new variant
        variant = ImageVariant(
            id=str(uuid.uuid4()),
            url=image_url,
            prompt_used="User uploaded image",
            is_uploaded_source=True,
            upload_type="image",
        )

        if not frame.rendered_image_asset:
            frame.rendered_image_asset = ImageAsset()

        frame.rendered_image_asset.variants.append(variant)
        frame.rendered_image_asset.selected_id = variant.id
        # Also update rendered_image_url so VideoCreator can pick it up
        frame.rendered_image_url = image_url

        script.updated_at = time.time()
        self._save_data()
        return script

    def _download_temp_image(self, url: str) -> str:
        """Downloads an image to a temporary file."""
        import tempfile

        # If it's a local file path (relative to output)
        if not url.startswith("http"):
            local_path = _safe_resolve_output_ref(url)
            if os.path.exists(local_path):
                return local_path

        # Download from URL
        try:
            # Create temp file
            fd, path = tempfile.mkstemp(suffix=".png")
            os.close(fd)
            os.unlink(path)
            download_url_to_file(url, path, timeout=(30, 120))
            return path
        except Exception as e:
            logger.error(f"Failed to download image: {e}")
            raise

    def select_video_for_frame(self, script_id: str, frame_id: str, video_id: str) -> Script:
        """Step 5a: Select a video variant for a frame."""
        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")

        frame = next((f for f in script.frames if f.id == frame_id), None)
        if not frame:
            raise ValueError("Frame not found")

        # Verify video exists and belongs to project
        video = next((v for v in script.video_tasks if v.id == video_id), None)
        if not video:
            raise ValueError("Video task not found")

        frame.selected_video_id = video_id

        # Also update the frame's video_url to point to this video for easy access
        frame.video_url = video.video_url

        self._save_data()
        return script

    def merge_videos(self, script_id: str) -> Script:
        """Step 5b: Merge selected videos into a single file."""
        _validate_safe_id(script_id, "script_id")
        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")

        logger.info(f"[MERGE] Starting video merge for script {script_id}")

        # Check if ffmpeg is available (prioritize bundled version)
        ffmpeg_path = get_ffmpeg_path()
        if not ffmpeg_path:
            install_instructions = get_ffmpeg_install_instructions()
            error_msg = (
                "FFmpeg is required for video merging but was not found.\n\n"
                f"{install_instructions}\n\n"
                "After installation, restart the application."
            )
            logger.error(f"[MERGE] FFmpeg not found. {error_msg}")
            raise RuntimeError(error_msg)

        # Log ffmpeg version for debugging
        try:
            version_result = subprocess.run(
                [ffmpeg_path, "-version"], capture_output=True, text=True, timeout=5
            )
            if version_result.returncode == 0:
                version_line = (
                    version_result.stdout.split("\n")[0] if version_result.stdout else "Unknown"
                )
                logger.debug(f"[MERGE] Using FFmpeg: {version_line}")
                logger.debug(f"[MERGE] FFmpeg path: {ffmpeg_path}")
            else:
                logger.warning(
                    f"[MERGE] Could not get FFmpeg version (exit code {version_result.returncode})"
                )
        except Exception as e:
            logger.warning(f"[MERGE] Could not get FFmpeg version: {e}")

        # Collect video paths
        video_paths = []
        for i, frame in enumerate(script.frames):
            logger.info(f"[MERGE] Processing frame {i+1}/{len(script.frames)}: {frame.id}")

            if not frame.selected_video_id:
                # Try to find a default completed video
                default_video = next(
                    (
                        v
                        for v in script.video_tasks
                        if v.frame_id == frame.id and v.status == "completed"
                    ),
                    None,
                )
                if default_video and default_video.video_url:
                    logger.debug(f"[MERGE]   -> Using default video: {default_video.video_url}")
                    video_paths.append(default_video.video_url)
                else:
                    logger.warning(f"[MERGE]   -> No video selected or available, skipping")
                continue

            video = next((v for v in script.video_tasks if v.id == frame.selected_video_id), None)
            if video and video.video_url:
                logger.debug(f"[MERGE]   -> Selected video: {video.video_url}")
                video_paths.append(video.video_url)
            else:
                logger.warning(
                    f"[MERGE]   -> Selected video {frame.selected_video_id} not found or has no URL"
                )

        if not video_paths:
            logger.error("[MERGE] No videos found to merge!")
            raise ValueError(
                "No videos selected to merge. Please select videos for each frame first."
            )

        logger.info(f"[MERGE] Found {len(video_paths)} videos to merge")

        # Create file list for ffmpeg
        list_path = _safe_resolve_output_ref(f"merge_list_{script_id}.txt")
        abs_video_paths = []

        with open(list_path, "w") as f:
            for path in video_paths:
                # Resolve to absolute path
                if not path.startswith("http"):
                    abs_path = _safe_resolve_output_ref(path)
                    if os.path.exists(abs_path):
                        f.write(f"file '{abs_path}'\n")
                        abs_video_paths.append(abs_path)
                        logger.debug(f"[MERGE] Added to list: {abs_path}")
                    else:
                        logger.warning(f"[MERGE] Video file not found: {abs_path}")

        if not abs_video_paths:
            logger.error("[MERGE] No valid video files found on disk!")
            raise ValueError(
                "No valid video files found. The video files may have been deleted or moved."
            )

        logger.info(f"[MERGE] Merge list created with {len(abs_video_paths)} videos")

        # Output path
        output_filename = f"merged_{script_id}_{int(time.time())}.mp4"
        output_path = _safe_resolve_path(_output_path("video"), output_filename)
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        logger.debug(f"[MERGE] Output path: {output_path}")

        # Log video file details for debugging
        for i, path in enumerate(abs_video_paths):
            try:
                size_mb = os.path.getsize(path) / (1024 * 1024)
                logger.debug(
                    f"[MERGE] Input video {i+1}: {os.path.basename(path)} ({size_mb:.2f} MB)"
                )
            except Exception as e:
                logger.warning(f"[MERGE] Could not get size for video {i+1}: {e}")

        # Run ffmpeg
        # Use re-encoding for better compatibility (slower but more reliable)
        # -c:v libx264 -c:a aac ensures consistent output format
        cmd = [
            ffmpeg_path,
            "-y",  # Use the detected ffmpeg path
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            list_path,
            "-c:v",
            "libx264",  # Re-encode video with H.264
            "-crf",
            "23",  # Quality (lower = better, 23 is default)
            "-preset",
            "fast",  # Encoding speed
            "-c:a",
            "aac",  # Re-encode audio with AAC
            "-b:a",
            "128k",  # Audio bitrate
            "-movflags",
            "+faststart",  # Web optimization
            output_path,
        ]

        logger.debug(f"[MERGE] Running FFmpeg command: {' '.join(cmd)}")
        logger.debug(f"[MERGE] Platform: {platform.system()} {platform.release()}")

        try:
            result = subprocess.run(
                cmd, check=True, capture_output=True, timeout=600
            )  # 10 min timeout for re-encoding
            logger.debug(
                f"[MERGE] FFmpeg stdout: {result.stdout.decode()[:500] if result.stdout else 'empty'}"
            )
            logger.info(f"[MERGE] FFmpeg completed successfully")

            # Update script with merged video path
            # Use 'videos/' (plural) to match the /files/videos route
            script.merged_video_url = f"videos/{output_filename}"

            # Verify file was created and log details
            if os.path.exists(output_path):
                file_size_mb = os.path.getsize(output_path) / (1024 * 1024)
                logger.info(
                    f"[MERGE] ✅ Merged video created successfully: {output_filename} ({file_size_mb:.2f} MB)"
                )
                logger.info(f"[MERGE] ✅ Video accessible at: /files/videos/{output_filename}")
            else:
                logger.error(f"[MERGE] ❌ Merged video file NOT found at: {output_path}")
                raise RuntimeError(
                    f"Video merge completed but output file not found: {output_path}"
                )

            self._save_data()

            # Cleanup list file
            if os.path.exists(list_path):
                os.remove(list_path)

            return script
        except subprocess.TimeoutExpired:
            logger.error("[MERGE] FFmpeg timed out after 600 seconds")
            raise RuntimeError("FFmpeg timed out. The videos may be too large.")
        except subprocess.CalledProcessError as e:
            stderr_msg = e.stderr.decode() if e.stderr else "No error output"
            stdout_msg = e.stdout.decode() if e.stdout else "No output"

            # Log full details for debugging
            logger.error(f"[MERGE] FFmpeg failed with exit code {e.returncode}")
            logger.error(f"[MERGE] FFmpeg command: {' '.join(cmd)}")
            logger.error(f"[MERGE] FFmpeg stderr: {stderr_msg}")
            logger.error(f"[MERGE] FFmpeg stdout: {stdout_msg}")
            logger.error(
                f"[MERGE] Video files attempted: {[os.path.basename(p) for p in abs_video_paths]}"
            )

            # Extract user-friendly error message
            user_msg = self._extract_ffmpeg_error_message(stderr_msg, abs_video_paths)
            raise RuntimeError(user_msg)

    def _extract_ffmpeg_error_message(self, stderr: str, video_paths: List[str]) -> str:
        """
        Extract a user-friendly error message from ffmpeg stderr output.

        Args:
            stderr: The stderr output from ffmpeg
            video_paths: List of video file paths that were being processed

        Returns:
            A user-friendly error message
        """
        if not stderr:
            return "FFmpeg merge failed with no error output. Please check the log files."

        stderr_lower = stderr.lower()

        # Common error patterns with user-friendly messages
        if "no such file or directory" in stderr_lower:
            return (
                "One or more video files could not be found.\n"
                "The videos may have been deleted or moved.\n"
                "Please try regenerating the missing videos."
            )

        if (
            "invalid data found" in stderr_lower
            or "invalid file" in stderr_lower
            or "moov atom not found" in stderr_lower
        ):
            return (
                "One or more video files are corrupted or incomplete.\n"
                "This can happen if video generation was interrupted.\n"
                "Please try regenerating the affected videos."
            )

        if "codec" in stderr_lower and (
            "not supported" in stderr_lower or "unknown" in stderr_lower
        ):
            return (
                "Video codec compatibility issue detected.\n"
                "The video format may not be supported by your FFmpeg installation.\n"
                "Try updating FFmpeg to the latest version."
            )

        if "permission denied" in stderr_lower or "access is denied" in stderr_lower:
            return (
                "Permission denied when accessing video files.\n"
                "Please check that the application has read/write permissions\n"
                "for the output directory."
            )

        if "disk full" in stderr_lower or "no space" in stderr_lower:
            return (
                "Insufficient disk space to create the merged video.\n"
                "Please free up some space and try again."
            )

        if "height not divisible" in stderr_lower or "width not divisible" in stderr_lower:
            return (
                "Video resolution compatibility issue.\n"
                "The videos have incompatible dimensions.\n"
                "This should not happen - please report this issue."
            )

        if "invalid argument" in stderr_lower:
            # Check if it's related to file list
            if any(
                "filelist" in line.lower() or "concat" in line.lower()
                for line in stderr.split("\n")
            ):
                return (
                    "FFmpeg could not read the video file list.\n"
                    "This might be a file path encoding issue.\n"
                    "Please ensure video filenames don't contain special characters."
                )

        # Fallback: extract the most relevant error line
        # Usually the last non-empty line before the final summary
        error_lines = [line.strip() for line in stderr.split("\n") if line.strip()]
        if error_lines:
            # Look for lines that seem like actual errors (contain "error", "failed", etc.)
            for line in reversed(error_lines):
                line_lower = line.lower()
                if any(
                    keyword in line_lower
                    for keyword in ["error", "failed", "invalid", "cannot", "unable"]
                ):
                    # Truncate if too long
                    if len(line) > 200:
                        line = line[:200] + "..."
                    return f"FFmpeg error: {line}\n\nPlease check the application logs for more details."

            # If no error keyword found, use last line
            last_line = error_lines[-1]
            if len(last_line) > 200:
                last_line = last_line[:200] + "..."
            return f"FFmpeg merge failed: {last_line}\n\nPlease check the application logs for more details."

        return (
            "FFmpeg merge failed with unknown error. Please check the application logs for details."
        )

    def create_asset_video_task(
        self,
        script_id: str,
        asset_id: str,
        asset_type: str,
        prompt: str,
        duration: int = 5,
        aspect_ratio: str = None,
    ) -> Tuple[Script, str]:
        """Creates a new video generation task for an asset (R2V)."""
        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")

        # Find asset
        target_asset = None
        if asset_type == "character":
            target_asset = next((c for c in script.characters if c.id == asset_id), None)
        elif asset_type == "scene":
            target_asset = next((s for s in script.scenes if s.id == asset_id), None)
        elif asset_type == "prop":
            target_asset = next((p for p in script.props if p.id == asset_id), None)

        if not target_asset:
            raise ValueError(f"Asset {asset_id} of type {asset_type} not found")

        # Use main image as reference
        image_url = target_asset.image_url
        if not image_url:
            # Try fallback for character
            if asset_type == "character":
                image_url = target_asset.full_body_image_url or target_asset.avatar_url

        if not image_url:
            raise ValueError("Asset has no reference image")

        # Save prompt to asset
        if prompt:
            target_asset.video_prompt = prompt

        task_id = str(uuid.uuid4())

        # Create VideoTask
        task = VideoTask(
            id=task_id,
            project_id=script_id,
            asset_id=asset_id,  # Link to asset
            image_url=image_url,
            prompt=prompt or f"Cinematic shot of {target_asset.name}",
            status="pending",
            duration=duration,
            model="wan2.6-r2v",  # Force R2V model
            created_at=time.time(),
        )

        # Add to script.video_tasks for global tracking
        if not script.video_tasks:
            script.video_tasks = []
        script.video_tasks.append(task)

        # Add to asset's video_assets list
        if not target_asset.video_assets:
            target_asset.video_assets = []
        target_asset.video_assets.append(task)

        self._save_data()
        return script, task_id

    def process_video_task(self, script_id: str, task_id: str):
        """Processes a video task."""
        script = self.get_script(script_id)
        if not script:
            logger.error(f"Script {script_id} not found for task {task_id}")
            return

        task = next((t for t in script.video_tasks if t.id == task_id), None)

        if not task:
            logger.error(f"Task {task_id} not found in script {script_id}")
            return

        try:
            # Update status to processing
            task.status = "processing"
            self._save_data()

            output_filename = f"video_{task_id}.mp4"
            output_path = _output_path("video", output_filename)
            os.makedirs(os.path.dirname(output_path), exist_ok=True)

            if os.getenv(LOCAL_VIDEO_SMOKE_ENV) == "1":
                _write_local_video_smoke_placeholder(output_path)
                task.video_url = output_media_ref(output_path)
                task.status = "completed"
                if task.asset_id:
                    self._sync_asset_video_task(script, task)
                self._save_data()
                return

            # Download image to temp file
            img_path = None
            if task.image_url:
                img_path = self._download_temp_image(task.image_url)

            # Generate video

            # Handle Audio Logic
            # 1. Silent: audio_url=None, audio=False
            # 2. AI Sound: audio_url=None, audio=True
            # 3. Sound Driven: audio_url=URL (audio param ignored)

            final_audio_url = None
            final_generate_audio = False

            if task.audio_url:
                # Sound Driven Mode
                final_audio_url = task.audio_url
                final_generate_audio = (
                    False  # API says audio param ignored if url present, but let's be explicit
                )
            elif task.generate_audio:
                # AI Sound Mode
                final_audio_url = None
                final_generate_audio = True
            else:
                # Silent Mode
                final_audio_url = None
                final_generate_audio = False

            # Ensure img_url is passed correctly for OSS
            img_url = task.image_url

            # Route to the appropriate model based on task.model
            model_name = task.model or ""
            model_name_lower = model_name.lower()
            backend = self._resolve_video_backend(model_name)
            use_vendor_kling = backend == "vendor" and model_name_lower.startswith("kling-")
            use_vendor_seedance = backend == "vendor" and model_name_lower.startswith(
                "doubao-seedance-"
            )
            use_vendor_vidu = backend == "vendor" and (
                model_name_lower.startswith("vidu")
                or model_name_lower.startswith("viduq2")
                or model_name_lower.startswith("viduq3")
            )

            if use_vendor_kling:
                # Use Kling model (cached)
                if self._kling_model is None:
                    from ...models.kling import KlingModel

                    self._kling_model = KlingModel({})
                video_path, _ = self._kling_model.generate(
                    prompt=task.prompt,
                    output_path=output_path,
                    img_url=img_url,
                    img_path=img_path,
                    duration=task.duration,
                    model=task.model,
                    negative_prompt=task.negative_prompt,
                    aspect_ratio="16:9",
                    mode=task.mode or "std",
                    sound=task.sound or "off",
                    cfg_scale=task.cfg_scale,
                )
            elif use_vendor_seedance:
                if self._seedance_model is None:
                    from ...models.seedance import SeedanceModel

                    self._seedance_model = SeedanceModel({})
                if final_audio_url:
                    logger.warning(
                        "Seedance currently does not support audio_url-driven video generation. "
                        "Ignoring audio_url for task %s.",
                        task.id,
                    )
                reference_mode = task.seedance_reference_mode or (
                    "combo"
                    if task.image_url and task.reference_video_urls
                    else "video" if task.reference_video_urls else "image"
                )
                workflow = task.seedance_workflow or "standard"
                workflow_mode = None
                if workflow == "extend":
                    workflow_mode = task.seedance_extend_mode or "continue"
                elif workflow == "edit":
                    workflow_mode = task.seedance_edit_mode or "subject_replace"
                video_path, _ = self._seedance_model.generate(
                    prompt=task.prompt,
                    output_path=output_path,
                    img_url=img_url,
                    img_path=img_path,
                    duration=task.duration,
                    model=task.model,
                    resolution=task.resolution,
                    aspect_ratio=task.aspect_ratio or "adaptive",
                    seed=task.seed,
                    generate_audio=final_generate_audio,
                    audio_url=final_audio_url,
                    reference_audio_url=task.reference_audio_url,
                    reference_video_urls=task.reference_video_urls,
                    reference_mode=reference_mode,
                    workflow=workflow,
                    workflow_mode=workflow_mode,
                    watermark=bool(task.watermark),
                    camera_fixed=task.camera_fixed,
                )
            elif use_vendor_vidu:
                # Use Vidu model (cached)
                if self._vidu_model is None:
                    from ...models.vidu import ViduModel

                    self._vidu_model = ViduModel({})
                video_path, _ = self._vidu_model.generate(
                    prompt=task.prompt,
                    output_path=output_path,
                    img_url=img_url,
                    img_path=img_path,
                    duration=task.duration,
                    model=task.model,
                    resolution=task.resolution,
                    aspect_ratio="16:9",
                    seed=task.seed or 0,
                    audio=task.vidu_audio if task.vidu_audio is not None else True,
                    movement_amplitude=task.movement_amplitude or "auto",
                )
            else:
                # Default: Wanx model
                video_path, _ = self.video_generator.model.generate(
                    prompt=task.prompt,
                    output_path=output_path,
                    img_path=img_path,
                    img_url=img_url,
                    duration=task.duration,
                    seed=task.seed,
                    resolution=task.resolution,
                    # Pass new params
                    audio_url=final_audio_url,
                    audio=final_generate_audio,
                    prompt_extend=task.prompt_extend,
                    negative_prompt=task.negative_prompt,
                    model=task.model,
                    shot_type=task.shot_type,
                    ref_video_urls=(
                        task.reference_video_urls if task.generation_mode == "r2v" else None
                    ),
                    camera_motion=None,
                    subject_motion=None,
                )

            task.video_url = output_media_ref(output_path)
            task.status = "completed"

            # Sync with asset if this is an asset video
            if task.asset_id:
                self._sync_asset_video_task(script, task)

        except Exception as e:
            import traceback

            logger.exception("Failed to process video task")
            logger.error(f"Video generation failed: {e}")
            task.status = "failed"
            if task.asset_id:
                self._sync_asset_video_task(script, task)

        self._save_data()

    def _sync_asset_video_task(self, script: Script, task: VideoTask):
        """Syncs the updated task status/url back to the asset's video_assets list."""
        target_asset = None
        # Search in all asset types
        for char in script.characters:
            if char.id == task.asset_id:
                target_asset = char
                break
        if not target_asset:
            for scene in script.scenes:
                if scene.id == task.asset_id:
                    target_asset = scene
                    break
        if not target_asset:
            for prop in script.props:
                if prop.id == task.asset_id:
                    target_asset = prop
                    break

        if target_asset:
            # Find and update the task in the asset's list
            for i, t in enumerate(target_asset.video_assets):
                if t.id == task.id:
                    target_asset.video_assets[i] = task
                    break
            else:
                # Not found, append it (shouldn't happen if created correctly, but good fallback)
                target_asset.video_assets.append(task)

    def create_asset_video_task(
        self,
        script_id: str,
        asset_id: str,
        asset_type: str,
        prompt: str = None,
        duration: int = 5,
        aspect_ratio: str = None,
    ) -> Tuple[Script, str]:
        """Creates a video generation task for an asset (I2V)."""
        script = self.get_script(script_id)
        if not script:
            raise ValueError("Script not found")

        target_asset = None
        if asset_type == "character":
            target_asset = next((c for c in script.characters if c.id == asset_id), None)
            # Use full body image for character video
            image_url = target_asset.full_body_image_url or target_asset.image_url
            if not prompt:
                prompt = f"A cinematic shot of {target_asset.name}, {target_asset.description}, looking around, breathing, slight movement, high quality, 4k"
        elif asset_type == "scene":
            target_asset = next((s for s in script.scenes if s.id == asset_id), None)
            image_url = target_asset.image_url
            if not prompt:
                prompt = f"A cinematic shot of {target_asset.name}, {target_asset.description}, ambient motion, lighting change, high quality, 4k"
        elif asset_type == "prop":
            target_asset = next((p for p in script.props if p.id == asset_id), None)
            image_url = target_asset.image_url
            if not prompt:
                prompt = f"A cinematic shot of {target_asset.name}, {target_asset.description}, rotating slowly, high quality, 4k"
        else:
            raise ValueError(f"Invalid asset_type: {asset_type}")

        if not target_asset:
            raise ValueError(f"Asset {asset_id} not found")

        if not image_url:
            raise ValueError(f"Asset {asset_id} has no image to generate video from")

        # Create task using existing method logic but with asset_id
        task_id = str(uuid.uuid4())

        # Snapshot logic (duplicated from create_video_task for now, or could refactor)
        snapshot_url = image_url
        try:
            if not image_url.startswith("http"):
                src_path = _safe_resolve_output_ref(image_url)
                if os.path.exists(src_path):
                    snapshot_dir = _output_path("video_inputs")
                    os.makedirs(snapshot_dir, exist_ok=True)
                    ext = os.path.splitext(image_url)[1] or ".png"
                    snapshot_filename = f"{task_id}{ext}"
                    snapshot_path = _safe_resolve_path(snapshot_dir, snapshot_filename)
                    import shutil

                    shutil.copy2(src_path, snapshot_path)
                    snapshot_url = f"video_inputs/{snapshot_filename}"
        except Exception:
            pass

        # Determine resolution from aspect ratio or default
        resolution = "720p"  # Default
        # TODO: Map aspect_ratio to resolution if needed

        task = VideoTask(
            id=task_id,
            project_id=script_id,
            asset_id=asset_id,
            image_url=snapshot_url,
            prompt=prompt,
            status="pending",
            duration=duration,
            resolution=resolution,
            model=script.model_settings.i2v_model
            or DEFAULT_I2V_MODEL,  # Asset video uses project's I2V model
            created_at=time.time(),
        )

        # Add to global list
        if not script.video_tasks:
            script.video_tasks = []
        script.video_tasks.append(task)

        # Add to asset list
        target_asset.video_assets.append(task)

        self._save_data()
        return script, task_id

    def delete_asset_video(
        self, script_id: str, asset_id: str, asset_type: str, video_id: str
    ) -> Script:
        """Deletes a video from an asset."""
        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")

        # Find asset
        target_asset = None
        if asset_type == "character":
            target_asset = next((c for c in script.characters if c.id == asset_id), None)
        elif asset_type == "scene":
            target_asset = next((s for s in script.scenes if s.id == asset_id), None)
        elif asset_type == "prop":
            target_asset = next((p for p in script.props if p.id == asset_id), None)

        if not target_asset:
            raise ValueError(f"Asset {asset_id} of type {asset_type} not found")

        # Find the task first to get video_url for file deletion
        video_task_to_delete = None
        if script.video_tasks:
            video_task_to_delete = next((v for v in script.video_tasks if v.id == video_id), None)

        # Remove from asset's video_assets
        if target_asset.video_assets:
            original_len = len(target_asset.video_assets)
            target_asset.video_assets = [v for v in target_asset.video_assets if v.id != video_id]
            if len(target_asset.video_assets) == original_len and not video_task_to_delete:
                # Only raise if not found in either place, or just log warning?
                # If found in global list but not asset list, it's weird but we should proceed.
                pass

        # Also remove from script.video_tasks
        if script.video_tasks:
            script.video_tasks = [v for v in script.video_tasks if v.id != video_id]

        # Try to delete the video file
        try:
            if video_task_to_delete and video_task_to_delete.video_url:
                video_path = _safe_resolve_output_ref(video_task_to_delete.video_url)
                if os.path.exists(video_path):
                    os.remove(video_path)
                    logger.info(f"Deleted video file: {video_path}")
        except Exception as e:
            logger.warning(f"Failed to delete video file: {e}")

        self._save_data()
        return script

    def _get_frame_audio_duration(self, script: Script, frame: StoryboardFrame) -> float:
        """Resolve a frame duration from its selected video task or fall back to a safe default."""
        selected_task = None
        if frame.selected_video_id:
            selected_task = next(
                (task for task in script.video_tasks if task.id == frame.selected_video_id), None
            )
        if not selected_task and frame.video_url:
            selected_task = next(
                (task for task in script.video_tasks if task.video_url == frame.video_url), None
            )

        if selected_task and selected_task.duration:
            try:
                return max(1.0, float(selected_task.duration))
            except (TypeError, ValueError):
                pass

        return 5.0

    def generate_audio(self, script_id: str) -> Script:
        """Step 5: Generate audio (Dialogue & SFX)."""
        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")

        logger.info(f"Generating audio for script {script.id}")

        for frame in script.frames:
            frame.audio_error = None

            # Generate Dialogue
            if frame.dialogue:
                speaker = None
                if frame.character_ids:
                    speaker = next(
                        (c for c in script.characters if c.id == frame.character_ids[0]), None
                    )

                if speaker:
                    self.audio_generator.generate_dialogue(
                        frame,
                        speaker,
                        speed=speaker.voice_speed,
                        pitch=speaker.voice_pitch,
                        volume=speaker.voice_volume,
                    )

            frame_duration = self._get_frame_audio_duration(script, frame)

            # Generate SFX (Text-to-Audio)
            if frame.action_description:
                self.audio_generator.generate_sfx(frame, duration=min(frame_duration, 3.0))

            # Generate SFX (Video-to-Audio) - if video exists
            if frame.video_url:
                self.audio_generator.generate_sfx_from_video(frame)

            # Generate BGM
            self.audio_generator.generate_bgm(
                frame,
                duration=frame_duration,
                context=" ".join(
                    value
                    for value in [
                        frame.action_description or "",
                        frame.dialogue or "",
                        frame.visual_atmosphere or "",
                    ]
                    if value
                ),
            )

        self._save_data()
        return script

    def generate_sfx(self, script_id: str) -> Script:
        """Step 5a: Generate only SFX tracks for all frames."""
        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")

        logger.info(f"Generating SFX for script {script.id}")

        for frame in script.frames:
            frame.audio_error = None
            if frame.action_description:
                frame_duration = self._get_frame_audio_duration(script, frame)
                self.audio_generator.generate_sfx(frame, duration=min(frame_duration, 3.0))
            if frame.video_url:
                self.audio_generator.generate_sfx_from_video(frame)

        self._save_data()
        return script

    def generate_bgm(self, script_id: str) -> Script:
        """Step 5b: Generate only BGM tracks for all frames."""
        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")

        logger.info(f"Generating BGM for script {script.id}")

        for frame in script.frames:
            frame.audio_error = None
            frame_duration = self._get_frame_audio_duration(script, frame)
            self.audio_generator.generate_bgm(
                frame,
                duration=frame_duration,
                context=" ".join(
                    value
                    for value in [
                        frame.action_description or "",
                        frame.dialogue or "",
                        frame.visual_atmosphere or "",
                    ]
                    if value
                ),
            )

        self._save_data()
        return script

    def generate_dialogue_line(
        self,
        script_id: str,
        frame_id: str,
        speed: float = 1.0,
        pitch: float = 1.0,
        volume: int = 50,
    ) -> Script:
        """Generates audio for a specific line with parameters."""
        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")

        frame = next((f for f in script.frames if f.id == frame_id), None)
        if not frame:
            raise ValueError("Frame not found")

        frame.audio_error = None
        if frame.dialogue:
            speaker = None
            if frame.character_ids:
                speaker = next(
                    (c for c in script.characters if c.id == frame.character_ids[0]), None
                )

            if speaker:
                self.audio_generator.generate_dialogue(frame, speaker, speed, pitch, volume)

        self._save_data()
        return script

    def bind_voice(self, script_id: str, char_id: str, voice_id: str, voice_name: str) -> Script:
        """Binds a voice to a character."""
        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")

        char = next((c for c in script.characters if c.id == char_id), None)
        if not char:
            raise ValueError("Character not found")

        char.voice_id = voice_id
        char.voice_name = voice_name
        self._save_data()
        return script

    def get_script(self, script_id: str) -> Optional[Script]:
        return self.scripts.get(script_id)

    def _select_variant_in_asset(self, image_asset: Any, variant_id: str) -> Any:
        """Helper to select a variant in an ImageAsset. Returns the selected variant if found."""
        if not image_asset or not image_asset.variants:
            return None

        for variant in image_asset.variants:
            if variant.id == variant_id:
                image_asset.selected_id = variant_id
                return variant
        return None

    def _delete_variant_in_asset(self, image_asset: Any, variant_id: str) -> bool:
        """Helper to delete a variant in an ImageAsset. Returns True if found and deleted."""
        if not image_asset or not image_asset.variants:
            return False

        initial_len = len(image_asset.variants)
        image_asset.variants = [v for v in image_asset.variants if v.id != variant_id]

        if len(image_asset.variants) < initial_len:
            # If we deleted the selected one, select the last one or None
            if image_asset.selected_id == variant_id:
                if image_asset.variants:
                    image_asset.selected_id = image_asset.variants[-1].id
                else:
                    image_asset.selected_id = None
            return True
        return False

    def select_asset_variant(
        self,
        script_id: str,
        asset_id: str,
        asset_type: str,
        variant_id: str,
        generation_type: str = None,
    ) -> Script:
        """Selects a specific variant for an asset."""
        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")

        target_asset = None
        if asset_type == "character":
            target_asset = next((c for c in script.characters if c.id == asset_id), None)
            if target_asset:
                # If generation_type is specified, only select from that specific asset
                if generation_type == "full_body":
                    variant = self._select_variant_in_asset(
                        target_asset.full_body_asset, variant_id
                    )
                    if variant:
                        target_asset.full_body_image_url = variant.url
                        target_asset.image_url = variant.url  # Legacy sync
                elif generation_type == "three_view":
                    variant = self._select_variant_in_asset(
                        target_asset.three_view_asset, variant_id
                    )
                    if variant:
                        target_asset.three_view_image_url = variant.url
                elif generation_type == "headshot":
                    variant = self._select_variant_in_asset(target_asset.headshot_asset, variant_id)
                    if variant:
                        target_asset.headshot_image_url = variant.url
                        target_asset.avatar_url = variant.url  # Sync avatar
                elif generation_type == "expression_sheet":
                    variant = self._select_variant_in_asset(
                        target_asset.expression_sheet_asset, variant_id
                    )
                    if variant:
                        target_asset.expression_sheet_image_url = variant.url
                else:
                    # Legacy fallback: search all assets (for backward compatibility)
                    variant = self._select_variant_in_asset(
                        target_asset.full_body_asset, variant_id
                    )
                    if variant:
                        target_asset.full_body_image_url = variant.url
                        target_asset.image_url = variant.url

                    if not variant:
                        variant = self._select_variant_in_asset(
                            target_asset.three_view_asset, variant_id
                        )
                        if variant:
                            target_asset.three_view_image_url = variant.url

                    if not variant:
                        variant = self._select_variant_in_asset(
                            target_asset.headshot_asset, variant_id
                        )
                        if variant:
                            target_asset.headshot_image_url = variant.url
                            target_asset.avatar_url = variant.url

                    if not variant:
                        variant = self._select_variant_in_asset(
                            target_asset.expression_sheet_asset, variant_id
                        )
                        if variant:
                            target_asset.expression_sheet_image_url = variant.url

        elif asset_type == "scene":
            target_asset = next((s for s in script.scenes if s.id == asset_id), None)
            if target_asset:
                variant = self._select_variant_in_asset(target_asset.image_asset, variant_id)
                if variant:
                    target_asset.image_url = variant.url

        elif asset_type == "prop":
            target_asset = next((p for p in script.props if p.id == asset_id), None)
            if target_asset:
                variant = self._select_variant_in_asset(target_asset.image_asset, variant_id)
                if variant:
                    target_asset.image_url = variant.url

        elif asset_type == "storyboard_frame":
            target_asset = next((f for f in script.frames if f.id == asset_id), None)
            if target_asset:
                # Check rendered_image_asset
                variant = self._select_variant_in_asset(
                    target_asset.rendered_image_asset, variant_id
                )
                if variant:
                    target_asset.rendered_image_url = variant.url
                    target_asset.image_url = variant.url  # Main image is rendered one

                # Also check image_asset (sketch)?
                if not variant:
                    variant = self._select_variant_in_asset(target_asset.image_asset, variant_id)
                    # If sketch, maybe don't update main image_url if rendered exists?
                    # For now, let's assume we only select rendered variants for frames usually.

        self._save_data()
        return script

    def delete_asset_variant(
        self, script_id: str, asset_id: str, asset_type: str, variant_id: str
    ) -> Script:
        """Deletes a specific variant from an asset."""
        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")

        target_asset = None
        if asset_type == "character":
            target_asset = next((c for c in script.characters if c.id == asset_id), None)
            if target_asset:
                if self._delete_variant_in_asset(target_asset.full_body_asset, variant_id):
                    # Sync legacy if needed
                    if target_asset.full_body_asset.selected_id:
                        selected = next(
                            (
                                v
                                for v in target_asset.full_body_asset.variants
                                if v.id == target_asset.full_body_asset.selected_id
                            ),
                            None,
                        )
                        target_asset.image_url = selected.url if selected else None
                    else:
                        target_asset.image_url = None

                elif self._delete_variant_in_asset(target_asset.three_view_asset, variant_id):
                    if target_asset.three_view_asset.selected_id:
                        selected = next(
                            (
                                v
                                for v in target_asset.three_view_asset.variants
                                if v.id == target_asset.three_view_asset.selected_id
                            ),
                            None,
                        )
                        target_asset.three_view_image_url = selected.url if selected else None
                    else:
                        target_asset.three_view_image_url = None

                elif self._delete_variant_in_asset(target_asset.headshot_asset, variant_id):
                    if target_asset.headshot_asset.selected_id:
                        selected = next(
                            (
                                v
                                for v in target_asset.headshot_asset.variants
                                if v.id == target_asset.headshot_asset.selected_id
                            ),
                            None,
                        )
                        target_asset.headshot_image_url = selected.url if selected else None
                    else:
                        target_asset.headshot_image_url = None

                elif self._delete_variant_in_asset(target_asset.expression_sheet_asset, variant_id):
                    if target_asset.expression_sheet_asset.selected_id:
                        selected = next(
                            (
                                v
                                for v in target_asset.expression_sheet_asset.variants
                                if v.id == target_asset.expression_sheet_asset.selected_id
                            ),
                            None,
                        )
                        target_asset.expression_sheet_image_url = selected.url if selected else None
                    else:
                        target_asset.expression_sheet_image_url = None

        elif asset_type == "scene":
            target_asset = next((s for s in script.scenes if s.id == asset_id), None)
            if target_asset and self._delete_variant_in_asset(target_asset.image_asset, variant_id):
                if target_asset.image_asset.selected_id:
                    selected = next(
                        (
                            v
                            for v in target_asset.image_asset.variants
                            if v.id == target_asset.image_asset.selected_id
                        ),
                        None,
                    )
                    target_asset.image_url = selected.url if selected else None
                else:
                    target_asset.image_url = None

        elif asset_type == "prop":
            target_asset = next((p for p in script.props if p.id == asset_id), None)
            if target_asset and self._delete_variant_in_asset(target_asset.image_asset, variant_id):
                if target_asset.image_asset.selected_id:
                    selected = next(
                        (
                            v
                            for v in target_asset.image_asset.variants
                            if v.id == target_asset.image_asset.selected_id
                        ),
                        None,
                    )
                    target_asset.image_url = selected.url if selected else None
                else:
                    target_asset.image_url = None

        elif asset_type == "storyboard_frame":
            target_asset = next((f for f in script.frames if f.id == asset_id), None)
            if target_asset:
                if self._delete_variant_in_asset(target_asset.rendered_image_asset, variant_id):
                    if target_asset.rendered_image_asset.selected_id:
                        selected = next(
                            (
                                v
                                for v in target_asset.rendered_image_asset.variants
                                if v.id == target_asset.rendered_image_asset.selected_id
                            ),
                            None,
                        )
                        target_asset.rendered_image_url = selected.url if selected else None
                        target_asset.image_url = selected.url if selected else None
                    else:
                        target_asset.rendered_image_url = None
                        # Don't clear image_url if it might fall back to sketch?
                        # For now, clear it if rendered is cleared.
                        target_asset.image_url = None

        self._save_data()
        return script

    def update_model_settings(
        self,
        script_id: str,
        t2i_model: str = None,
        i2i_model: str = None,
        i2v_model: str = None,
        character_aspect_ratio: str = None,
        scene_aspect_ratio: str = None,
        prop_aspect_ratio: str = None,
        storyboard_aspect_ratio: str = None,
    ) -> Script:
        """Updates the model settings for a script."""
        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")

        if t2i_model:
            script.model_settings.t2i_model = t2i_model
        if i2i_model:
            script.model_settings.i2i_model = i2i_model
        if i2v_model:
            script.model_settings.i2v_model = i2v_model
        if character_aspect_ratio:
            script.model_settings.character_aspect_ratio = character_aspect_ratio
        if scene_aspect_ratio:
            script.model_settings.scene_aspect_ratio = scene_aspect_ratio
        if prop_aspect_ratio:
            script.model_settings.prop_aspect_ratio = prop_aspect_ratio
        if storyboard_aspect_ratio:
            script.model_settings.storyboard_aspect_ratio = storyboard_aspect_ratio

        self._save_data()
        return script

    def update_codex_imagegen_policy(
        self,
        script_id: str,
        *,
        enabled: Optional[bool] = None,
        mode: Optional[str] = None,
        max_total_bytes: Optional[int] = None,
        max_side: Optional[int] = None,
        min_side: Optional[int] = None,
        jpeg_quality: Optional[int] = None,
        min_jpeg_quality: Optional[int] = None,
        never_attach_raw_references: Optional[bool] = None,
        recommendation: Optional[Dict[str, Any]] = None,
    ) -> Script:
        """Updates the Codex built-in imagegen safety policy for a script."""
        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")

        policy = script.codex_imagegen_policy or CodexImagegenPolicy()
        updates: Dict[str, Any] = {}
        if enabled is not None:
            updates["enabled"] = enabled
        if mode is not None:
            updates["mode"] = mode
        if max_total_bytes is not None:
            if max_total_bytes <= 0:
                raise ValueError("Codex imagegen max_total_bytes must be positive")
            if max_total_bytes > DEFAULT_CODEX_IMAGEGEN_HANDOFF_MAX_REFERENCE_BYTES:
                raise ValueError(
                    "Codex imagegen max_total_bytes cannot exceed "
                    f"{DEFAULT_CODEX_IMAGEGEN_HANDOFF_MAX_REFERENCE_BYTES}; "
                    "split the frame into staged handoffs instead"
                )
            updates["max_total_bytes"] = max_total_bytes
        if max_side is not None:
            updates["max_side"] = max_side
        if min_side is not None:
            updates["min_side"] = min_side
        if jpeg_quality is not None:
            updates["jpeg_quality"] = jpeg_quality
        if min_jpeg_quality is not None:
            updates["min_jpeg_quality"] = min_jpeg_quality
        if never_attach_raw_references is not None:
            updates["never_attach_raw_references"] = never_attach_raw_references
        if recommendation is not None:
            current_recommendation = policy.recommendation.model_dump()
            current_recommendation.update(recommendation)
            updates["recommendation"] = current_recommendation

        if updates:
            merged = policy.model_dump()
            merged.update(updates)
            script.codex_imagegen_policy = CodexImagegenPolicy.model_validate(merged)

        self._save_data()
        return script

    def _set_variant_favorite(self, image_asset: Any, variant_id: str, is_favorited: bool) -> bool:
        """Helper to set favorite status of a variant. Returns True if found."""
        if not image_asset or not image_asset.variants:
            return False
        for v in image_asset.variants:
            if v.id == variant_id:
                v.is_favorited = is_favorited
                return True
        return False

    def toggle_variant_favorite(
        self,
        script_id: str,
        asset_id: str,
        asset_type: str,
        variant_id: str,
        is_favorited: bool,
        generation_type: str = None,
    ) -> Script:
        """Toggles the favorite status of a variant."""
        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")

        found = False
        if asset_type == "character":
            target_asset = next((c for c in script.characters if c.id == asset_id), None)
            if target_asset:
                if generation_type == "full_body":
                    found = self._set_variant_favorite(
                        target_asset.full_body_asset, variant_id, is_favorited
                    )
                elif generation_type == "three_view":
                    found = self._set_variant_favorite(
                        target_asset.three_view_asset, variant_id, is_favorited
                    )
                elif generation_type == "headshot":
                    found = self._set_variant_favorite(
                        target_asset.headshot_asset, variant_id, is_favorited
                    )
                elif generation_type == "expression_sheet":
                    found = self._set_variant_favorite(
                        target_asset.expression_sheet_asset, variant_id, is_favorited
                    )
                else:
                    # Try all character assets
                    found = (
                        self._set_variant_favorite(
                            target_asset.full_body_asset, variant_id, is_favorited
                        )
                        or self._set_variant_favorite(
                            target_asset.three_view_asset, variant_id, is_favorited
                        )
                        or self._set_variant_favorite(
                            target_asset.headshot_asset, variant_id, is_favorited
                        )
                        or self._set_variant_favorite(
                            target_asset.expression_sheet_asset, variant_id, is_favorited
                        )
                    )

        elif asset_type == "scene":
            target_asset = next((s for s in script.scenes if s.id == asset_id), None)
            if target_asset:
                found = self._set_variant_favorite(
                    target_asset.image_asset, variant_id, is_favorited
                )

        elif asset_type == "prop":
            target_asset = next((p for p in script.props if p.id == asset_id), None)
            if target_asset:
                found = self._set_variant_favorite(
                    target_asset.image_asset, variant_id, is_favorited
                )

        elif asset_type == "storyboard_frame":
            target_asset = next((f for f in script.frames if f.id == asset_id), None)
            if target_asset:
                found = self._set_variant_favorite(
                    target_asset.rendered_image_asset, variant_id, is_favorited
                ) or self._set_variant_favorite(target_asset.image_asset, variant_id, is_favorited)

        if not found:
            raise ValueError(f"Variant {variant_id} not found")

        self._save_data()
        return script

    # ============================================================
    # Series Storage & CRUD
    # ============================================================

    def _load_series_data(self) -> Dict[str, Series]:
        try:
            data = load_json_object_with_backup(self.series_data_file)
            return {k: Series(**v) for k, v in data.items()}
        except Exception as e:
            logger.error(f"Failed to load series data: {e}")
            return {}

    def _save_series_data_unlocked(self):
        """Save series data without acquiring the lock (caller must hold self._save_lock)."""
        try:
            save_json_object_atomic(
                self.series_data_file,
                {k: _model_dump_compat(v) for k, v in self.series_store.items()},
            )
        except Exception as e:
            logger.error(f"Failed to save series data: {e}")

    def _save_series_data(self):
        """Save series data with thread lock."""
        with self._save_lock:
            self._save_series_data_unlocked()

    def create_series(self, title: str, description: str = "") -> Series:
        """Create a new Series."""
        with self._save_lock:
            series = Series(
                id=str(uuid.uuid4()),
                title=title,
                description=description,
                created_at=time.time(),
                updated_at=time.time(),
            )
            self.series_store[series.id] = series
            self._save_series_data_unlocked()
            return series

    def get_series(self, series_id: str) -> Optional[Series]:
        return self.series_store.get(series_id)

    def list_series(self) -> List[Series]:
        return list(self.series_store.values())

    def update_series(self, series_id: str, updates: Dict[str, Any]) -> Series:
        """Update Series fields (title, description, etc.)."""
        with self._save_lock:
            series = self.series_store.get(series_id)
            if not series:
                raise ValueError("Series not found")
            for key, value in updates.items():
                if hasattr(series, key) and key not in ("id", "created_at", "episode_ids"):
                    setattr(series, key, value)
            series.updated_at = time.time()
            self.series_store[series_id] = series
            self._save_series_data_unlocked()
            return series

    def delete_series(self, series_id: str) -> None:
        """Delete a Series and disassociate its episodes."""
        with self._save_lock:
            series = self.series_store.get(series_id)
            if not series:
                raise ValueError("Series not found")
            # Disassociate episodes
            for ep_id in series.episode_ids:
                script = self.scripts.get(ep_id)
                if script:
                    script.series_id = None
                    script.episode_number = None
            self._save_data()
            del self.series_store[series_id]
            self._save_series_data_unlocked()

    def add_episode_to_series(
        self, series_id: str, script_id: str, episode_number: Optional[int] = None
    ) -> Series:
        """Add an existing Script/Project as an Episode to a Series."""
        with self._save_lock:
            series = self.series_store.get(series_id)
            if not series:
                raise ValueError("Series not found")
            script = self.scripts.get(script_id)
            if not script:
                raise ValueError("Script not found")
            # If script already belongs to another series, remove it from the old one
            if script.series_id and script.series_id != series_id:
                old_series = self.series_store.get(script.series_id)
                if old_series and script_id in old_series.episode_ids:
                    old_series.episode_ids.remove(script_id)
            if script_id not in series.episode_ids:
                series.episode_ids.append(script_id)
            script.series_id = series_id
            script.episode_number = episode_number or len(series.episode_ids)
            series.updated_at = time.time()
            self._save_data()
            self._save_series_data_unlocked()
            return series

    def remove_episode_from_series(self, series_id: str, script_id: str) -> Series:
        """Remove an Episode from a Series (does not delete the project)."""
        with self._save_lock:
            series = self.series_store.get(series_id)
            if not series:
                raise ValueError("Series not found")
            if script_id in series.episode_ids:
                series.episode_ids.remove(script_id)
            script = self.scripts.get(script_id)
            if script:
                script.series_id = None
                script.episode_number = None
            series.updated_at = time.time()
            self._save_data()
            self._save_series_data_unlocked()
            return series

    def get_series_episodes(self, series_id: str) -> List[Script]:
        """Get all Episodes belonging to a Series, in order."""
        series = self.series_store.get(series_id)
        if not series:
            raise ValueError("Series not found")
        episodes = []
        for ep_id in series.episode_ids:
            script = self.scripts.get(ep_id)
            if script:
                episodes.append(script)
        return episodes

    def resolve_episode_assets(
        self, episode: Script, series: Optional[Series] = None
    ) -> Dict[str, List]:
        """Merge Episode-local assets with Series shared assets.
        Episode-local assets take priority (by ID) over Series assets."""
        if not series:
            # Auto-lookup series if episode has series_id
            if episode.series_id:
                series = self.series_store.get(episode.series_id)
        if not series:
            return {
                "characters": episode.characters,
                "scenes": episode.scenes,
                "props": episode.props,
            }
        # Build lookup by ID for episode-local assets
        ep_char_ids = {c.id for c in episode.characters}
        ep_scene_ids = {s.id for s in episode.scenes}
        ep_prop_ids = {p.id for p in episode.props}

        merged_characters = list(episode.characters) + [
            c for c in series.characters if c.id not in ep_char_ids
        ]
        merged_scenes = list(episode.scenes) + [
            s for s in series.scenes if s.id not in ep_scene_ids
        ]
        merged_props = list(episode.props) + [p for p in series.props if p.id not in ep_prop_ids]

        return {
            "characters": merged_characters,
            "scenes": merged_scenes,
            "props": merged_props,
        }

    # ============================================================
    # File Import & Episode Splitting
    # ============================================================

    def import_file_and_split(self, text: str, suggested_episodes: int = 3) -> List[Dict]:
        """Split text into episodes using LLM. Returns episode preview data."""
        return self.script_processor.split_into_episodes(text, suggested_episodes)

    def create_series_from_import(
        self, title: str, text: str, episodes_data: List[Dict], description: str = ""
    ) -> Dict:
        """Create a Series with Episodes from import data.
        episodes_data: list of dicts with episode_number, title, start_marker, end_marker."""
        # Create the Series (already acquires lock internally)
        series = self.create_series(title, description)

        # Split text into episode chunks based on markers
        episode_texts = self._split_text_by_markers(text, episodes_data)

        with self._save_lock:
            # Create Episode (Script) for each chunk
            created_episodes = []
            for idx, ep_data in enumerate(episodes_data):
                ep_text = episode_texts[idx] if idx < len(episode_texts) else ""
                ep_title = ep_data.get("title", f"第{idx+1}集")
                episode_number = ep_data.get("episode_number", idx + 1)

                # Create draft script (no LLM analysis yet — user can trigger later)
                script = self.script_processor.create_draft_script(ep_title, ep_text)
                script.series_id = series.id
                script.episode_number = episode_number
                self.scripts[script.id] = script

                series.episode_ids.append(script.id)
                created_episodes.append(
                    {
                        "id": script.id,
                        "title": ep_title,
                        "episode_number": episode_number,
                        "text_length": len(ep_text),
                    }
                )

            self._save_data()
            self._save_series_data_unlocked()

        return {
            "series": series.model_dump(),
            "episodes": created_episodes,
        }

    def _split_text_by_markers(self, text: str, episodes_data: List[Dict]) -> List[str]:
        """Split text into chunks using start/end markers from LLM.
        Searches sequentially to avoid overlapping chunks."""
        chunks = []
        search_from = 0  # Track position to avoid overlap

        for ep in episodes_data:
            start_marker = ep.get("start_marker", "")
            end_marker = ep.get("end_marker", "")

            start_idx = search_from
            end_idx = len(text)

            if start_marker:
                found = text.find(start_marker, search_from)
                if found >= 0:
                    start_idx = found

            if end_marker:
                found = text.find(end_marker, start_idx)
                if found >= 0:
                    end_idx = found + len(end_marker)

            chunks.append(text[start_idx:end_idx])
            search_from = end_idx  # Next episode starts after this one

        # Fallback: if markers produced empty/overlapping chunks, do equal split
        if not chunks or all(len(c.strip()) == 0 for c in chunks):
            chunk_size = max(1, len(text) // len(episodes_data))
            chunks = []
            for i in range(len(episodes_data)):
                start = i * chunk_size
                end = start + chunk_size if i < len(episodes_data) - 1 else len(text)
                chunks.append(text[start:end])

        return chunks

    # ============================================================
    # Series Asset Operations
    # ============================================================

    def _find_series_asset(self, series_id: str, asset_id: str, asset_type: str):
        """Find an asset in a Series. Returns (series, asset) tuple."""
        if asset_type not in ("character", "scene", "prop"):
            raise ValueError(f"Invalid asset type: {asset_type}")
        series = self.series_store.get(series_id)
        if not series:
            raise ValueError("Series not found")
        target_asset = None
        if asset_type == "character":
            target_asset = next((c for c in series.characters if c.id == asset_id), None)
        elif asset_type == "scene":
            target_asset = next((s for s in series.scenes if s.id == asset_id), None)
        elif asset_type == "prop":
            target_asset = next((p for p in series.props if p.id == asset_id), None)
        if not target_asset:
            raise ValueError(f"Asset {asset_id} of type {asset_type} not found in series")
        return series, target_asset

    def toggle_series_asset_lock(self, series_id: str, asset_id: str, asset_type: str) -> Series:
        """Toggle the locked status of a Series asset."""
        with self._save_lock:
            series, target_asset = self._find_series_asset(series_id, asset_id, asset_type)
            target_asset.locked = not target_asset.locked
            self._save_series_data_unlocked()
            return series

    def update_series_asset_image(
        self, series_id: str, asset_id: str, asset_type: str, image_url: str
    ) -> Series:
        """Updates the image URL of a Series asset."""
        with self._save_lock:
            series, target_asset = self._find_series_asset(series_id, asset_id, asset_type)
            target_asset.image_url = image_url
            if asset_type == "character":
                target_asset.avatar_url = image_url
            self._save_series_data_unlocked()
            return series

    def update_series_asset_attributes(
        self, series_id: str, asset_id: str, asset_type: str, attributes: Dict[str, Any]
    ) -> Series:
        """Updates arbitrary attributes of a Series asset."""
        with self._save_lock:
            series, target_asset = self._find_series_asset(series_id, asset_id, asset_type)
            for key, value in attributes.items():
                if hasattr(target_asset, key) and key not in ("id", "status", "locked"):
                    setattr(target_asset, key, value)
            series.updated_at = time.time()
            self._save_series_data_unlocked()
            return series

    def generate_series_asset(
        self,
        series_id: str,
        asset_id: str,
        asset_type: str,
        style_preset: str = None,
        reference_image_url: str = None,
        style_prompt: str = None,
        generation_type: str = "all",
        prompt: str = None,
        apply_style: bool = True,
        negative_prompt: str = None,
        batch_size: int = 1,
        model_name: str = None,
    ) -> tuple:
        """Generate a Series asset. Creates an async task like project asset generation.
        Returns (series, task_id)."""
        series = self.series_store.get(series_id)
        if not series:
            raise ValueError("Series not found")

        t2i_model = model_name or series.model_settings.t2i_model

        from .assets import ASPECT_RATIO_TO_SIZE

        if asset_type == "character":
            aspect_ratio = series.model_settings.character_aspect_ratio
            default_size = "576*1024"
        elif asset_type == "scene":
            aspect_ratio = series.model_settings.scene_aspect_ratio
            default_size = "1024*576"
        elif asset_type == "prop":
            aspect_ratio = series.model_settings.prop_aspect_ratio
            default_size = "1024*1024"
        else:
            aspect_ratio = "9:16"
            default_size = "576*1024"
        effective_size = ASPECT_RATIO_TO_SIZE.get(aspect_ratio, default_size)

        effective_positive_prompt = ""
        effective_negative_prompt = negative_prompt or ""
        if apply_style:
            if series.art_direction and series.art_direction.style_config:
                effective_positive_prompt = series.art_direction.style_config.get(
                    "positive_prompt", ""
                )
                global_neg = series.art_direction.style_config.get("negative_prompt", "")
                if global_neg:
                    effective_negative_prompt = (
                        f"{effective_negative_prompt}, {global_neg}"
                        if effective_negative_prompt
                        else global_neg
                    )
            elif style_prompt:
                effective_positive_prompt = style_prompt
            elif style_preset:
                effective_positive_prompt = f"{style_preset} style"

        task_id = str(uuid.uuid4())
        self.asset_generation_tasks[task_id] = {
            "status": "pending",
            "progress": 0,
            "error": None,
            "script_id": series_id,  # reuse field name for task lookup
            "asset_id": asset_id,
            "asset_type": asset_type,
            "created_at": time.time(),
            "is_series": True,
            "params": {
                "style_preset": style_preset,
                "reference_image_url": reference_image_url,
                "effective_positive_prompt": effective_positive_prompt,
                "effective_negative_prompt": effective_negative_prompt,
                "generation_type": generation_type,
                "prompt": prompt,
                "apply_style": apply_style,
                "batch_size": batch_size,
                "t2i_model": t2i_model,
                "effective_size": effective_size,
            },
        }
        return series, task_id

    def import_assets_from_series(
        self, target_series_id: str, source_series_id: str, asset_ids: List[str]
    ) -> Tuple[Series, List[str], List[str]]:
        """Deep-copy selected assets from source Series to target Series.
        Returns (target_series, imported_ids, skipped_ids)."""
        with self._save_lock:
            target = self.series_store.get(target_series_id)
            if not target:
                raise ValueError("Target series not found")
            source = self.series_store.get(source_series_id)
            if not source:
                raise ValueError("Source series not found")

            # Build lookup of all source assets
            source_assets = {}
            for c in source.characters:
                source_assets[c.id] = ("character", c)
            for s in source.scenes:
                source_assets[s.id] = ("scene", s)
            for p in source.props:
                source_assets[p.id] = ("prop", p)

            imported_ids = []
            skipped_ids = []
            for aid in asset_ids:
                if aid not in source_assets:
                    skipped_ids.append(aid)
                    continue
                asset_type, asset = source_assets[aid]
                # Deep copy with new ID
                import copy

                new_asset = copy.deepcopy(asset)
                new_asset.id = str(uuid.uuid4())
                if asset_type == "character":
                    target.characters.append(new_asset)
                elif asset_type == "scene":
                    target.scenes.append(new_asset)
                elif asset_type == "prop":
                    target.props.append(new_asset)
                imported_ids.append(aid)

            target.updated_at = time.time()
            self._save_series_data_unlocked()
            return target, imported_ids, skipped_ids

    def get_effective_prompt(
        self, prompt_type: str, episode: Script, series: Optional[Series] = None
    ) -> str:
        """Three-level fallback: Episode -> Series -> system default."""
        valid_prompt_types = ("storyboard_polish", "video_polish", "r2v_polish")
        if prompt_type not in valid_prompt_types:
            raise ValueError(
                f"Invalid prompt_type: {prompt_type}. Must be one of {valid_prompt_types}"
            )
        from .llm import (
            DEFAULT_STORYBOARD_POLISH_PROMPT,
            DEFAULT_VIDEO_POLISH_PROMPT,
            DEFAULT_R2V_POLISH_PROMPT,
        )

        defaults = {
            "storyboard_polish": DEFAULT_STORYBOARD_POLISH_PROMPT,
            "video_polish": DEFAULT_VIDEO_POLISH_PROMPT,
            "r2v_polish": DEFAULT_R2V_POLISH_PROMPT,
        }
        episode_value = getattr(episode.prompt_config, prompt_type, "")
        if episode_value.strip():
            return episode_value
        if series:
            series_value = getattr(series.prompt_config, prompt_type, "")
            if series_value.strip():
                return series_value
        return defaults.get(prompt_type, "")
