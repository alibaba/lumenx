from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image

from src.utils import story_fixture_audit as audit_module


REPO_ROOT = Path(__file__).resolve().parents[1]


def _write_png(path: Path, color: tuple[int, int, int, int] = (255, 255, 255, 255)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGBA", (8, 8), color)
    image.save(path)


def test_story_fixture_audit_cli_passes_on_liuyi_fixture():
    result = subprocess.run(
        [sys.executable, "-m", "src.utils.story_fixture_audit"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "[story-fixture-audit] OK" in result.stdout
    assert "1 fixtures" in result.stdout


def test_story_fixture_audit_flags_unexpected_reference_files(tmp_path, monkeypatch):
    story_projects_root = tmp_path / "tests" / "fixtures" / "story_projects"
    fixture_dir = story_projects_root / "demo-fixture"
    references_dir = fixture_dir / "references"
    uploads_dir = tmp_path / "output" / "uploads" / "fixtures"

    references_dir.mkdir(parents=True)
    uploads_dir.mkdir(parents=True)

    source_path = references_dir / "storyboard_reference_collage.png"
    _write_png(source_path)
    shutil.copyfile(source_path, uploads_dir / "demo-fixture-storyboard-reference.png")
    _write_png(references_dir / "unused_reference.png", color=(0, 255, 0, 255))

    manifest = {
        "slug": "demo-fixture",
        "reference_images": [
            {
                "role": "storyboard_reference_collage",
                "path": "references/storyboard_reference_collage.png",
            }
        ],
        "reference_assets": [],
    }
    (fixture_dir / "project_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    monkeypatch.setattr(audit_module, "UPLOADS_FIXTURES_ROOT", uploads_dir)

    report = audit_module.audit_story_fixture_visual_gates(story_projects_root)

    assert report.issues
    assert any("unexpected reference files" in issue for issue in report.issues)
    assert any("unused_reference.png" in issue for issue in report.issues)


def test_story_fixture_audit_flattens_asset_packages(tmp_path, monkeypatch):
    story_projects_root = tmp_path / "tests" / "fixtures" / "story_projects"
    fixture_dir = story_projects_root / "package-fixture"
    references_dir = fixture_dir / "references" / "characters" / "char_mina"
    uploads_dir = tmp_path / "output" / "uploads" / "fixtures"

    references_dir.mkdir(parents=True)
    uploads_dir.mkdir(parents=True)

    full_body_source = references_dir / "char_mina_full_body.png"
    expression_source = references_dir / "char_mina_expression_sheet.png"
    _write_png(full_body_source)
    _write_png(expression_source, color=(0, 0, 255, 255))
    shutil.copyfile(full_body_source, uploads_dir / full_body_source.name)
    shutil.copyfile(expression_source, uploads_dir / expression_source.name)

    manifest = {
        "slug": "package-fixture",
        "asset_packages": [
            {
                "asset_type": "character",
                "asset_id": "char_mina",
                "name": "Mina",
                "locked": True,
                "board": {
                    "role": "board_4k",
                    "path": "references/characters/char_mina/char_mina_board_4k.png",
                    "runtime_binding": False,
                },
                "derivatives": [
                    {
                        "role": "full_body",
                        "upload_type": "full_body",
                        "path": "references/characters/char_mina/char_mina_full_body.png",
                    },
                    {
                        "role": "expression_sheet",
                        "upload_type": "expression-sheet",
                        "path": "references/characters/char_mina/char_mina_expression_sheet.png",
                    },
                ],
            }
        ],
        "reference_images": [],
        "reference_assets": [],
    }
    (fixture_dir / "project_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    monkeypatch.setattr(audit_module, "UPLOADS_FIXTURES_ROOT", uploads_dir)

    report = audit_module.audit_story_fixture_visual_gates(story_projects_root)

    assert report.issues == []
