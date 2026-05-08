import json
from pathlib import Path
from unittest.mock import patch

from src.apps.comic_gen.models import Character, Script
from src.apps.comic_gen.pipeline import (
    ComicGenPipeline,
    _SHOT_BLOCK_RE,
    _flatten_fixture_reference_assets,
)


V2_ROOT = Path("tests/fixtures/story_projects/六一那天_v2")
V1_PROMPT = Path("tests/fixtures/story_projects/六一那天/source/08_seedance2_storyboard_prompts.md")


def _make_pipeline(state_root: Path):
    with patch("src.apps.comic_gen.pipeline.AssetGenerator"), \
         patch("src.apps.comic_gen.pipeline.StoryboardGenerator"), \
         patch("src.apps.comic_gen.pipeline.VideoGenerator"), \
         patch("src.apps.comic_gen.pipeline.AudioGenerator"), \
         patch("src.apps.comic_gen.pipeline.ExportManager"):
        pipeline = ComicGenPipeline()

    state_root.mkdir(parents=True, exist_ok=True)
    pipeline.data_file = str(state_root / "projects.json")
    pipeline.series_data_file = str(state_root / "series.json")
    pipeline.scripts = {}
    pipeline.series_store = {}
    return pipeline


def test_liuyi_v2_has_draft_manifest_and_parseable_rewritten_storyboard():
    assert not (V2_ROOT / "project_manifest.json").exists()

    manifest = json.loads((V2_ROOT / "project_manifest.draft.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 2
    assert manifest["slug"] == "liuyi-that-day-v2"
    assert manifest["project_stage"] == "draft"
    assert "expression_sheet" in manifest["asset_policy"]["character_board"]["required_parts"]

    prompt_doc = next(item for item in manifest["source_files"] if item["role"] == "storyboard_prompt_doc")
    assert prompt_doc["path"] == "source/05_storyboard_script.md"

    storyboard_text = (V2_ROOT / prompt_doc["path"]).read_text(encoding="utf-8")
    assert len(list(_SHOT_BLOCK_RE.finditer(storyboard_text))) == 18

    old_lines = {
        line.strip()
        for line in V1_PROMPT.read_text(encoding="utf-8").splitlines()
        if len(line.strip()) >= 32 and not line.strip().startswith("**")
    }
    reused_lines = sorted(line for line in old_lines if line in storyboard_text)
    assert reused_lines == []


def test_liuyi_v2_manifest_asset_packages_cover_complete_inventory():
    manifest = json.loads((V2_ROOT / "project_manifest.draft.json").read_text(encoding="utf-8"))
    asset_packages = manifest["asset_packages"]

    assert len(asset_packages) == 26

    expected_ids = {
        "liuyi_char_xiaoqi_child_v2",
        "liuyi_char_mother_v2",
        "liuyi_char_father_v2",
        "liuyi_char_xiaoqi_young_v2",
        "liuyi_char_xiaoqi_adult_v2",
        "liuyi_char_boy_v2",
        "liuyi_char_boy_father_v2",
        "liuyi_scene_school_playground_v2",
        "liuyi_scene_school_gate_v2",
        "liuyi_scene_hospital_room_v2",
        "liuyi_scene_2026_ward_v2",
        "liuyi_scene_hospital_corridor_v2",
        "liuyi_scene_funeral_hall_v2",
        "liuyi_scene_home_desk_v2",
        "liuyi_scene_exam_admission_v2",
        "liuyi_scene_medical_school_v2",
        "liuyi_scene_doctor_office_v2",
        "liuyi_prop_white_bear_v2",
        "liuyi_prop_paper_bag_v2",
        "liuyi_prop_child_drawing_v2",
        "liuyi_prop_childrens_day_balloons_v2",
        "liuyi_prop_medical_textbooks_v2",
        "liuyi_prop_father_memorial_portrait_v2",
        "liuyi_prop_family_photo_v2",
        "liuyi_prop_notebook_pencil_v2",
        "liuyi_prop_admission_notice_v2",
    }

    assert {item["asset_id"] for item in asset_packages} == expected_ids


def test_liuyi_v2_draft_is_not_discovered_as_formal_fixture(tmp_path):
    pipeline = _make_pipeline(tmp_path)

    fixture_slugs = {item["slug"] for item in pipeline.list_fixture_story_projects()}

    assert "liuyi-that-day-v2" not in fixture_slugs


def test_fixture_asset_packages_flatten_to_runtime_reference_assets():
    manifest = {
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
        ]
    }

    reference_assets = _flatten_fixture_reference_assets(manifest)

    assert [item["path"] for item in reference_assets] == [
        "references/characters/char_mina/char_mina_full_body.png",
        "references/characters/char_mina/char_mina_expression_sheet.png",
    ]
    assert [item["upload_type"] for item in reference_assets] == [
        "full_body",
        "expression_sheet",
    ]
    assert all(item["asset_id"] == "char_mina" for item in reference_assets)


def test_fixture_reference_binding_supports_expression_sheet(tmp_path):
    pipeline = _make_pipeline(tmp_path)
    script = Script(
        id="script-1",
        title="Demo",
        original_text="",
        characters=[
            Character(
                id="char_mina",
                name="Mina",
                description="A calm young doctor",
            )
        ],
        created_at=1.0,
        updated_at=1.0,
    )

    pipeline._bind_fixture_reference_asset(
        script,
        asset_type="character",
        asset_id="char_mina",
        upload_type="expression_sheet",
        image_url="uploads/fixtures/char_mina_expression_sheet.png",
        prompt_used="Mina expression board",
        locked=True,
    )

    character = script.characters[0]
    assert character.locked is True
    assert character.expression_sheet_image_url == "uploads/fixtures/char_mina_expression_sheet.png"
    assert character.expression_sheet_asset.selected_id == "char_mina_expression_sheet_fixture"
    assert character.expression_sheet_asset.variants[0].is_uploaded_source is True
    assert character.expression_sheet_asset.variants[0].upload_type == "expression_sheet"
