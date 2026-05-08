from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List


REPO_ROOT = Path(__file__).resolve().parents[2]
STORY_PROJECTS_ROOT = REPO_ROOT / "tests" / "fixtures" / "story_projects"
UPLOADS_FIXTURES_ROOT = REPO_ROOT / "output" / "uploads" / "fixtures"
TEXT_EXTENSIONS = {".md", ".json", ".txt", ".ps1"}
BANNED_PHRASES = ("文件齐了", "文件齐全")
FILE_COMPLETION_PHRASES = (
    "文件完成",
    "file complete",
    "file completion",
    "files_complete",
    "complete_visual_gate_required",
    "openable",
)


@dataclass
class AuditReport:
    fixture_count: int
    static_export_count: int
    crop_manifest_count: int
    text_file_count: int
    issues: List[str]


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_\-]+", "-", value or "").strip("-")
    return slug or "fixture"


def _load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _mentions_visual_gate(text: str) -> bool:
    normalized = text.lower()
    return "visual_gate" in normalized or "visual gate" in normalized or "视觉门禁" in text


def _mentions_file_completion(text: str) -> bool:
    normalized = text.lower()
    return any(phrase in normalized for phrase in FILE_COMPLETION_PHRASES) or "文件完成" in text


def _iter_story_fixture_dirs(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return [
        path
        for path in sorted(root.iterdir())
        if path.is_dir()
        and path.name != "_templates"
        and (path / "project_manifest.json").exists()
    ]


def _iter_text_files(root: Path) -> Iterable[Path]:
    for path in root.rglob("*"):
        if path.is_file() and path.suffix.lower() in TEXT_EXTENSIONS:
            yield path


def _iter_relative_files(root: Path, base: Path) -> set[str]:
    if not root.exists():
        return set()
    return {
        path.relative_to(base).as_posix()
        for path in root.rglob("*")
        if path.is_file()
    }


def _add_issue(issues: list[str], path: Path, message: str) -> None:
    issues.append(f"{path}: {message}")


def _reference_entries(manifest: dict) -> list[dict]:
    def normalize_upload_type(upload_type: str | None) -> str:
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

    def add_entry(
        entries: list[dict],
        entry: dict,
        *,
        seen: set[tuple[str, str, str, str]],
        default_locked: bool = True,
    ) -> None:
        asset_type = str(entry.get("asset_type") or "").strip().lower()
        asset_id = str(entry.get("asset_id") or "").strip()
        upload_type = normalize_upload_type(entry.get("upload_type") or entry.get("role"))
        source_path = str(entry.get("path") or "").strip()
        if not asset_type or not asset_id or not source_path:
            return
        key = (asset_type, asset_id, upload_type, source_path)
        if key in seen:
            return
        seen.add(key)
        normalized = dict(entry)
        normalized["asset_type"] = asset_type
        normalized["asset_id"] = asset_id
        normalized["upload_type"] = upload_type
        normalized["path"] = source_path
        normalized["locked"] = bool(entry.get("locked", entry.get("lock", default_locked)))
        entries.append(normalized)

    entries = [item for item in manifest.get("reference_images", []) if isinstance(item, dict)]
    seen_keys: set[tuple[str, str, str, str]] = set()

    for item in [item for item in manifest.get("reference_assets", []) if isinstance(item, dict)]:
        add_entry(entries, item, seen=seen_keys)

    for package in [item for item in manifest.get("asset_packages", []) if isinstance(item, dict)]:
        package_type = str(package.get("asset_type") or "").strip().lower()
        package_id = str(package.get("asset_id") or "").strip()
        package_name = str(package.get("name") or package_id or "asset").strip()
        package_locked = bool(package.get("locked", package.get("lock", True)))
        board = package.get("board")
        if isinstance(board, dict) and board.get("runtime_binding", True):
            add_entry(
                entries,
                {
                    "asset_type": package_type,
                    "asset_id": package_id,
                    "upload_type": normalize_upload_type(board.get("upload_type") or board.get("role")),
                    "path": board.get("path"),
                    "label": board.get("label") or f"{package_name} 主板",
                    "locked": bool(board.get("locked", package_locked)),
                },
                seen=seen_keys,
                default_locked=package_locked,
            )
        for derivative in [item for item in package.get("derivatives", []) if isinstance(item, dict)]:
            if derivative.get("runtime_binding", True) is False:
                continue
            add_entry(
                entries,
                {
                    "asset_type": package_type,
                    "asset_id": package_id,
                    "upload_type": normalize_upload_type(derivative.get("upload_type") or derivative.get("role")),
                    "path": derivative.get("path"),
                    "label": derivative.get("label") or f"{package_name} {derivative.get('role') or 'derivative'}",
                    "locked": bool(derivative.get("locked", package_locked)),
                },
                seen=seen_keys,
                default_locked=package_locked,
            )

    return entries


def _expected_upload_name(manifest_slug: str, source_path: Path, entry: dict) -> str:
    if entry.get("role") == "storyboard_reference_collage":
        return f"{_slugify(manifest_slug)}-storyboard-reference{source_path.suffix or '.png'}"
    return source_path.name


def _audit_reference_copy(
    *,
    issues: list[str],
    fixture_dir: Path,
    manifest_slug: str,
    entry: dict,
) -> None:
    relative_path = str(entry.get("path") or "").strip()
    if not relative_path:
        _add_issue(issues, fixture_dir, "reference entry is missing a path")
        return

    source_path = fixture_dir / relative_path
    if not source_path.exists():
        _add_issue(issues, source_path, "reference source file is missing")
        return

    expected_name = _expected_upload_name(manifest_slug, source_path, entry)

    uploaded_path = UPLOADS_FIXTURES_ROOT / expected_name
    if not uploaded_path.exists():
        _add_issue(issues, uploaded_path, "uploaded fixture copy is missing")
        return

    if uploaded_path.read_bytes() != source_path.read_bytes():
        _add_issue(issues, uploaded_path, "uploaded fixture copy does not match the source bytes")


def _audit_project_manifest(fixture_dir: Path, issues: list[str]) -> None:
    manifest_path = fixture_dir / "project_manifest.json"
    if not manifest_path.exists():
        draft_manifest_path = fixture_dir / "project_manifest.draft.json"
        if draft_manifest_path.exists():
            return
        _add_issue(issues, manifest_path, "project_manifest.json is missing")
        return

    manifest = _load_json(manifest_path)
    manifest_slug = str(manifest.get("slug") or fixture_dir.name)
    references = _reference_entries(manifest)
    expected_reference_paths: set[str] = set()

    for entry in references:
        relative_path = str(entry.get("path") or "").strip()
        if relative_path:
            expected_reference_paths.add(relative_path)

    for entry in references:
        _audit_reference_copy(
            issues=issues,
            fixture_dir=fixture_dir,
            manifest_slug=manifest_slug,
            entry=entry,
        )

    actual_reference_paths = _iter_relative_files(fixture_dir / "references", fixture_dir)
    if actual_reference_paths != expected_reference_paths:
        missing_paths = sorted(expected_reference_paths - actual_reference_paths)
        extra_paths = sorted(actual_reference_paths - expected_reference_paths)
        if missing_paths:
            _add_issue(
                issues,
                fixture_dir / "references",
                "missing reference files: " + ", ".join(missing_paths),
            )
        if extra_paths:
            _add_issue(
                issues,
                fixture_dir / "references",
                "unexpected reference files: " + ", ".join(extra_paths),
            )


def _audit_static_frame_exports(fixture_dir: Path, issues: list[str]) -> int:
    path = fixture_dir / "generation_prompts" / "static_frame_exports.json"
    if not path.exists():
        return 0

    data = _load_json(path)
    exports = data.get("frame_exports")
    if not isinstance(exports, list) or not exports:
        _add_issue(issues, path, "frame_exports is missing or empty")
        return 0

    for index, entry in enumerate(exports):
        if not isinstance(entry, dict):
            _add_issue(issues, path, f"frame_exports[{index}] is not an object")
            continue

        frame_id = str(entry.get("frame_id") or "").strip()
        if not frame_id:
            _add_issue(issues, path, f"frame_exports[{index}] is missing frame_id")
        if not isinstance(entry.get("visual_gate"), str) or not entry["visual_gate"].strip():
            _add_issue(issues, path, f"{frame_id or f'frame_exports[{index}]'} is missing visual_gate")

        for key in ("output_image", "source_collage", "workflow_manifest", "identity_reference_image"):
            value = entry.get(key)
            if isinstance(value, str) and value.strip():
                target = REPO_ROOT / value
                if not target.exists():
                    _add_issue(issues, target, f"{frame_id or value} references a missing {key}")

    return len(exports)


def _audit_crop_manifests(fixture_dir: Path, issues: list[str]) -> int:
    prompts_root = fixture_dir / "generation_prompts"
    if not prompts_root.exists():
        return 0

    count = 0
    for path in sorted(prompts_root.glob("frame_*/crop_composition_manifest.json")):
        count += 1
        data = _load_json(path)
        crops = data.get("crops")
        if not isinstance(crops, list) or not crops:
            _add_issue(issues, path, "crops is missing or empty")
            continue

        output_image = data.get("output_image")
        if isinstance(output_image, str) and output_image.strip():
            output_path = REPO_ROOT / output_image
            if not output_path.exists():
                _add_issue(issues, output_path, "crop composition output image is missing")

        for index, crop in enumerate(crops):
            if not isinstance(crop, dict):
                _add_issue(issues, path, f"crops[{index}] is not an object")
                continue

            gate = crop.get("visual_gate")
            gate_id = gate.get("gate_id") if isinstance(gate, dict) else ""
            if not isinstance(gate_id, str) or not gate_id.strip():
                _add_issue(issues, path, f"crops[{index}] is missing visual_gate.gate_id")

            for key in ("base_crop", "edited_crop", "prompt"):
                value = str(crop.get(key) or "").strip()
                if not value:
                    _add_issue(issues, path, f"crops[{index}] is missing {key}")
                    continue
                target = REPO_ROOT / value
                if key != "prompt" and not target.exists():
                    _add_issue(issues, target, f"crops[{index}] references a missing {key}")

            for ref_image in crop.get("reference_images") or []:
                if not isinstance(ref_image, str) or not ref_image.strip():
                    _add_issue(issues, path, f"crops[{index}] has an invalid reference_images entry")
                    continue
                target = REPO_ROOT / ref_image
                if not target.exists():
                    _add_issue(issues, target, f"crops[{index}] references a missing reference image")

    return count


def _audit_text_footing(fixture_dir: Path, issues: list[str]) -> int:
    count = 0
    for path in _iter_text_files(fixture_dir):
        count += 1
        text = path.read_text(encoding="utf-8")
        for phrase in BANNED_PHRASES:
            if phrase in text:
                _add_issue(
                    issues,
                    path,
                    f"contains banned phrase {phrase!r}; replace it with file-complete + visual-gate wording",
                )

        mentions_static_exports = "static_frame_exports.json" in text
        mentions_crop_manifest = "crop_composition_manifest.json" in text
        if mentions_static_exports:
            if not (_mentions_file_completion(text) and _mentions_visual_gate(text)):
                _add_issue(
                    issues,
                    path,
                    "mentions static_frame_exports.json but does not mention both file completion and visual_gate or 视觉门禁",
                )
        elif mentions_crop_manifest and not _mentions_visual_gate(text):
            _add_issue(
                issues,
                path,
                "mentions crop_composition_manifest.json but does not mention visual_gate or 视觉门禁",
            )

    return count


def audit_story_fixture_visual_gates(root: Path | str = STORY_PROJECTS_ROOT) -> AuditReport:
    root = Path(root)
    issues: list[str] = []
    fixture_dirs = _iter_story_fixture_dirs(root)

    if not fixture_dirs:
        _add_issue(issues, root, "no story fixture projects found")
        return AuditReport(0, 0, 0, 0, issues)

    static_export_count = 0
    crop_manifest_count = 0
    text_file_count = 0

    for fixture_dir in fixture_dirs:
        _audit_project_manifest(fixture_dir, issues)
        static_export_count += _audit_static_frame_exports(fixture_dir, issues)
        crop_manifest_count += _audit_crop_manifests(fixture_dir, issues)
        text_file_count += _audit_text_footing(fixture_dir, issues)

    return AuditReport(
        fixture_count=len(fixture_dirs),
        static_export_count=static_export_count,
        crop_manifest_count=crop_manifest_count,
        text_file_count=text_file_count,
        issues=issues,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Audit story fixtures for manifest/reference/upload consistency, explicit visual_gate "
            "contracts, and file-complete wording."
        )
    )
    parser.add_argument(
        "--root",
        default=str(STORY_PROJECTS_ROOT),
        help="Fixture root to scan (default: tests/fixtures/story_projects).",
    )
    args = parser.parse_args(argv)

    report = audit_story_fixture_visual_gates(Path(args.root))
    if report.issues:
        print(f"[story-fixture-audit] Found {len(report.issues)} issue(s).")
        for issue in report.issues:
            print(f"- {issue}")
        return 1

    print(
        "[story-fixture-audit] OK "
        f"({report.fixture_count} fixtures, {report.static_export_count} static exports, "
        f"{report.crop_manifest_count} crop manifests, {report.text_file_count} text files scanned)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
