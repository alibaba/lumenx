from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

from dotenv import load_dotenv
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.apps.comic_gen.models import ModelSettings, Scene, Script, StoryboardFrame
from src.apps.comic_gen.pipeline import ComicGenPipeline
from src.models.image import OPENAI_EDIT_REQUEST_MAX_BYTES, OPENAI_EDIT_REQUEST_MAX_BYTES_ENV
from src.utils.image_payload_budget import DEFAULT_CODEX_IMAGEGEN_MAX_REFERENCE_BYTES
from src.utils.runtime_config import get_output_root


LIUYI_V2_FRAME_17_REFERENCE_GLOBS = [
    "*/references/scenes/liuyi_scene_2026_ward_v2/"
    "liuyi_scene_2026_ward_v2_childrens_day_daylight.png",
    "*/references/characters/liuyi_char_xiaoqi_adult_v2/"
    "liuyi_char_xiaoqi_adult_v2_full_body.png",
    "*/references/characters/liuyi_char_boy_v2/liuyi_char_boy_v2_full_body.png",
    "*/references/characters/liuyi_char_boy_father_v2/"
    "liuyi_char_boy_father_v2_full_body.png",
    "*/references/props/liuyi_prop_white_bear_v2/liuyi_prop_white_bear_v2_usage_view.png",
    "*/references/props/liuyi_prop_childrens_day_balloons_v2/"
    "liuyi_prop_childrens_day_balloons_v2_usage_view.png",
]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run a real storyboard render through the configured OpenAI-compatible image "
            "edit provider, using the large liuyi v2 frame 17 reference set."
        )
    )
    parser.add_argument(
        "--max-reference-bytes",
        type=int,
        default=DEFAULT_CODEX_IMAGEGEN_MAX_REFERENCE_BYTES,
        help="Storyboard aggregate reference budget before safe refs are prepared.",
    )
    parser.add_argument(
        "--openai-edit-request-bytes",
        type=int,
        default=OPENAI_EDIT_REQUEST_MAX_BYTES,
        help="Aggregate bytes allowed for the final OpenAI-compatible edit upload.",
    )
    parser.add_argument(
        "--size",
        default="1024*576",
        help="Storyboard output size passed through the existing pipeline aspect ratio mapping.",
    )
    parser.add_argument(
        "--summary-path",
        default="",
        help="Optional JSON summary output path. Defaults under output/codex_image_audit.",
    )
    return parser.parse_args()


def _load_runtime_env() -> Dict[str, str]:
    load_dotenv(REPO_ROOT / ".env", override=False)

    os.environ.setdefault("IMAGE_PROVIDER", "openai")
    os.environ.setdefault("IMAGE_EDIT_PROVIDER", "openai")
    os.environ["OBJECT_STORAGE_PROVIDER"] = "none"

    return {
        "IMAGE_PROVIDER": os.getenv("IMAGE_PROVIDER", ""),
        "IMAGE_EDIT_PROVIDER": os.getenv("IMAGE_EDIT_PROVIDER", ""),
        "OPENAI_IMAGE_BASE_URL": os.getenv("OPENAI_IMAGE_BASE_URL", ""),
        "OPENAI_IMAGE_EDIT_BASE_URL": os.getenv("OPENAI_IMAGE_EDIT_BASE_URL", ""),
        "OPENAI_IMAGE_MODEL": os.getenv("OPENAI_IMAGE_MODEL", ""),
        "OPENAI_IMAGE_EDIT_MODEL": os.getenv("OPENAI_IMAGE_EDIT_MODEL", ""),
        "OPENAI_IMAGE_API_KEY": "<set>"
        if os.getenv("OPENAI_IMAGE_API_KEY")
        else "<unset>",
        "OPENAI_IMAGE_EDIT_API_KEY": "<set>"
        if os.getenv("OPENAI_IMAGE_EDIT_API_KEY")
        else "<unset>",
    }


def _resolve_fixture_references() -> List[Path]:
    fixture_root = REPO_ROOT / "tests" / "fixtures" / "story_projects"
    references: List[Path] = []
    for pattern in LIUYI_V2_FRAME_17_REFERENCE_GLOBS:
        matches = sorted(path for path in fixture_root.glob(pattern) if path.is_file())
        if len(matches) != 1:
            raise RuntimeError(
                f"Expected exactly one fixture reference for {pattern!r}, found {len(matches)}."
            )
        references.append(matches[0].resolve())
    return references


def _image_meta(path: Path) -> Dict[str, Any]:
    with Image.open(path) as image:
        return {
            "path": str(path),
            "bytes": path.stat().st_size,
            "width": image.width,
            "height": image.height,
        }


def _build_script() -> Script:
    now = time.time()
    return Script(
        id="real_provider_payload_smoke",
        title="Real Provider Payload Smoke",
        original_text="",
        scenes=[
            Scene(
                id="neutral_reference_room",
                name="Reference planning room",
                description=(
                    "A quiet bright room for checking visual continuity across people and props."
                ),
            )
        ],
        frames=[
            StoryboardFrame(
                id="liuyi_frame_17_payload_smoke",
                scene_id="neutral_reference_room",
                action_description=(
                    "Create a clean continuity-check storyboard frame from the provided references."
                ),
                camera_angle="Wide storyboard frame",
            )
        ],
        model_settings=ModelSettings(
            t2i_model="openai-image",
            i2i_model="openai-image-edit",
            storyboard_aspect_ratio="16:9",
        ),
        created_at=now,
        updated_at=now,
    )


def _selected_output_path(frame: StoryboardFrame) -> Path | None:
    asset = frame.rendered_image_asset
    if not asset or not asset.selected_id:
        return None
    selected = next(
        (variant for variant in asset.variants if variant.id == asset.selected_id),
        None,
    )
    if not selected or not selected.url:
        return None
    return (get_output_root(project_root=REPO_ROOT) / selected.url).resolve()


def _redact(value: str) -> str:
    return re.sub(r"sk-[A-Za-z0-9_\-]{8,}", "sk-***", value)


def main() -> int:
    args = _parse_args()
    env_summary = _load_runtime_env()
    if env_summary["IMAGE_EDIT_PROVIDER"] != "openai":
        raise RuntimeError("IMAGE_EDIT_PROVIDER must be openai for this smoke test.")
    if (
        env_summary["OPENAI_IMAGE_EDIT_API_KEY"] == "<unset>"
        and env_summary["OPENAI_IMAGE_API_KEY"] == "<unset>"
        and not os.getenv("OPENAI_API_KEY")
    ):
        raise RuntimeError("No OpenAI-compatible image edit API key is configured.")

    os.environ["LUMENX_STORYBOARD_REFERENCE_MAX_BYTES"] = str(args.max_reference_bytes)
    os.environ[OPENAI_EDIT_REQUEST_MAX_BYTES_ENV] = str(args.openai_edit_request_bytes)

    references = _resolve_fixture_references()
    output_root = get_output_root(project_root=REPO_ROOT)
    state_root = output_root / "codex_image_audit" / "liuyi-that-day-v2" / "_test_state"
    render_output_dir = output_root / "codex_image_audit" / "liuyi-that-day-v2" / "real_provider_smoke"
    summary_path = (
        Path(args.summary_path)
        if args.summary_path
        else render_output_dir / "real_provider_smoke_summary.json"
    )
    state_root.mkdir(parents=True, exist_ok=True)
    render_output_dir.mkdir(parents=True, exist_ok=True)

    pipeline = ComicGenPipeline(
        {
            "storyboard": {
                "output_dir": str(render_output_dir),
                "model": {"params": {"size": args.size}},
            }
        }
    )
    pipeline.data_file = str(state_root / "projects.json")
    pipeline.series_data_file = str(state_root / "series.json")
    script = _build_script()
    pipeline.scripts = {script.id: script}
    pipeline.series_store = {}

    prompt = (
        "Use the six reference images only as visual continuity guides. "
        "Create one polished cinematic 16:9 storyboard frame in a bright neutral room, "
        "keeping the visible identities, the white plush toy, and the colorful balloons "
        "consistent. Fully clothed, ordinary family-safe composition, no text, no logos, "
        "no injury, no clinical treatment scene."
    )

    updated_script = pipeline.generate_storyboard_render(
        script.id,
        "liuyi_frame_17_payload_smoke",
        {
            "reference_image_urls": [str(path) for path in references],
            "continuity_lock": False,
        },
        prompt,
        batch_size=1,
    )
    frame = updated_script.frames[0]
    preflight = frame.composition_data.get("reference_payload_preflight", {})
    request_paths = [Path(path) for path in preflight.get("request_ref_image_paths", [])]
    output_path = _selected_output_path(frame)

    summary: Dict[str, Any] = {
        "ok": True,
        "env": env_summary,
        "policy": {
            "storyboard_reference_max_bytes": args.max_reference_bytes,
            "openai_edit_request_max_bytes": args.openai_edit_request_bytes,
            "size": args.size,
        },
        "source_references": {
            "count": len(references),
            "total_bytes": sum(path.stat().st_size for path in references),
            "items": [_image_meta(path) for path in references],
        },
        "preflight": {
            "status": preflight.get("status"),
            "prepared": preflight.get("prepared"),
            "fits_budget": preflight.get("fits_budget"),
            "max_total_bytes": preflight.get("max_total_bytes"),
            "total_source_bytes": preflight.get("total_source_bytes"),
            "estimated_source_base64_bytes": preflight.get("estimated_source_base64_bytes"),
            "total_prepared_bytes": preflight.get("total_prepared_bytes"),
            "estimated_prepared_base64_bytes": preflight.get(
                "estimated_prepared_base64_bytes"
            ),
            "prepared_manifest": preflight.get("prepared_manifest"),
            "request_ref_count": len(request_paths),
            "request_ref_total_bytes": sum(
                path.stat().st_size for path in request_paths if path.exists()
            ),
        },
        "output": {
            "frame_status": frame.status,
            "image_path": str(output_path) if output_path else None,
            "image_exists": bool(output_path and output_path.exists()),
            "image_bytes": output_path.stat().st_size
            if output_path and output_path.exists()
            else None,
        },
    }
    if output_path and output_path.exists():
        with Image.open(output_path) as image:
            summary["output"]["width"] = image.width
            summary["output"]["height"] = image.height

    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"summary_path": str(summary_path.resolve()), **summary}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(_redact(f"real provider smoke failed: {exc}"), file=sys.stderr)
        raise SystemExit(1)
