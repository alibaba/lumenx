import json
from pathlib import Path
from unittest.mock import patch

from fastapi.responses import JSONResponse
import pytest
from fastapi.testclient import TestClient

from src.apps.comic_gen import api as api_module
from src.apps.comic_gen.pipeline import ComicGenPipeline


def _normalize_ref_paths(ref_paths):
    return [str(ref).replace("\\", "/") for ref in ref_paths]


def _make_pipeline(tmp_path):
    with patch("src.apps.comic_gen.pipeline.AssetGenerator"), \
         patch("src.apps.comic_gen.pipeline.StoryboardGenerator"), \
         patch("src.apps.comic_gen.pipeline.VideoGenerator"), \
         patch("src.apps.comic_gen.pipeline.AudioGenerator"), \
         patch("src.apps.comic_gen.pipeline.ExportManager"):
        pipeline = ComicGenPipeline()

    pipeline.data_file = str(tmp_path / "projects.json")
    pipeline.series_data_file = str(tmp_path / "series.json")
    pipeline.scripts = {}
    pipeline.series_store = {}
    return pipeline


def test_liuyi_frame_15_two_stage_prompt_pack_is_reproducible():
    prompt_dir = Path("tests/fixtures/story_projects/六一那天/generation_prompts/frame_15")
    expected_files = [
        "01_base_room_t2i_prompt.txt",
        "02a_adult_xiaoqi_local_edit_prompt.txt",
        "02b_boy_local_edit_prompt.txt",
        "02c_father_local_edit_prompt.txt",
        "99_fallback_multiref_low_semantic_risk_prompt.txt",
        "README.md",
        "crop_composition_manifest.json",
        "run_formal_crop_edits.ps1",
    ]

    for filename in expected_files:
        assert (prompt_dir / filename).exists()

    base_prompt = (prompt_dir / "01_base_room_t2i_prompt.txt").read_text(encoding="utf-8")
    assert "text-to-image only" in base_prompt
    assert "do not attach reference images" in base_prompt
    assert "later local edits" in base_prompt

    for filename in [
        "02a_adult_xiaoqi_local_edit_prompt.txt",
        "02b_boy_local_edit_prompt.txt",
        "02c_father_local_edit_prompt.txt",
    ]:
        prompt = (prompt_dir / filename).read_text(encoding="utf-8")
        assert "exactly two input images" in prompt
        assert "Keep the edit localized" in prompt

    fallback_prompt = (
        prompt_dir / "99_fallback_multiref_low_semantic_risk_prompt.txt"
    ).read_text(encoding="utf-8")
    assert "semantically gentler" in fallback_prompt
    assert "not the default production path" in fallback_prompt
    assert "If the request is blocked or unstable" in fallback_prompt


def test_liuyi_frame_15_crop_composition_manifest_locks_bbox_and_output():
    manifest_path = Path(
        "tests/fixtures/story_projects/六一那天/generation_prompts/frame_15/"
        "crop_composition_manifest.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest["base_image"] == (
        "output/codex_image_audit/liuyi-that-day/generated/"
        "liuyi_frame_15_stage1_base_v3.png"
    )
    assert manifest["output_image"] == (
        "output/codex_image_audit/liuyi-that-day/generated/"
        "liuyi_frame_15_stage3_full_formal_v1.png"
    )
    assert [crop["id"] for crop in manifest["crops"]] == ["adult_xiaoqi", "boy_father"]
    assert [crop["bbox"] for crop in manifest["crops"]] == [
        {"x": 1360, "y": 32, "width": 640, "height": 1088},
        {"x": 0, "y": 256, "width": 1024, "height": 768},
    ]
    assert any("compose_fixture_frame_crops.ps1" in note for note in manifest["notes"])
    assert (
        manifest_path.parent / "run_formal_crop_edits.ps1"
    ).exists()
    ascii_wrapper = Path("scripts/run_liuyi_frame15_formal_crop_edits.ps1")
    generic_wrapper = Path("scripts/run_fixture_frame_script.ps1")
    compose_wrapper = Path("scripts/compose_fixture_frame_crops.ps1")
    assert ascii_wrapper.exists()
    wrapper_text = ascii_wrapper.read_text(encoding="utf-8")
    assert "六一那天" not in wrapper_text
    assert "run_fixture_frame_script.ps1" in wrapper_text
    assert "ProjectSlug" in wrapper_text
    assert "FrameId" in wrapper_text
    assert "ScriptName" in wrapper_text

    assert generic_wrapper.exists()
    generic_wrapper_text = generic_wrapper.read_text(encoding="utf-8")
    assert "六一那天" not in generic_wrapper_text
    assert "project_manifest.json" in generic_wrapper_text
    assert "ProjectSlug" in generic_wrapper_text
    assert "FrameId" in generic_wrapper_text
    assert "ScriptName" in generic_wrapper_text
    assert "PSCommandPath" in generic_wrapper_text

    assert compose_wrapper.exists()
    compose_wrapper_text = compose_wrapper.read_text(encoding="utf-8")
    assert "六一那天" not in compose_wrapper_text
    assert "compose_frame_crops.py" in compose_wrapper_text
    assert "crop_composition_manifest.json" in compose_wrapper_text
    assert "ProjectSlug" in compose_wrapper_text
    assert "FrameId" in compose_wrapper_text
    assert "DetectOnly" in compose_wrapper_text
    assert "WriteDetectedManifest" in compose_wrapper_text


def test_fixture_frame_powershell_scripts_use_ascii_entrypoints():
    template_dir = Path("tests/fixtures/story_projects/_templates/frame_crop_workflow")
    assert (template_dir / "README.md").exists()
    assert (template_dir / "crop_composition_manifest.template.json").exists()
    assert (template_dir / "run_formal_crop_edits.template.ps1").exists()

    template_manifest = json.loads(
        (template_dir / "crop_composition_manifest.template.json").read_text(encoding="utf-8")
    )
    assert template_manifest["project_slug"] == "PROJECT_SLUG"
    assert template_manifest["frame_id"] == "FRAME_ID"
    assert any("compose_fixture_frame_crops.ps1" in note for note in template_manifest["notes"])

    template_script = (template_dir / "run_formal_crop_edits.template.ps1").read_text(encoding="utf-8")
    assert "六一那天" not in template_script
    assert "$ProjectSlug" in template_script
    assert "$FrameId" in template_script
    assert "compose_fixture_frame_crops.ps1" in template_script

    scripts_root = Path("scripts")
    root_fixture_wrappers = [
        path
        for path in scripts_root.glob("*.ps1")
        if path.name not in {
            "run_fixture_frame_script.ps1",
            "compose_fixture_frame_crops.ps1",
        }
    ]
    assert root_fixture_wrappers
    for wrapper in root_fixture_wrappers:
        text = wrapper.read_text(encoding="utf-8")
        assert "六一那天" not in text
        if wrapper.name.startswith("run_"):
            assert "run_fixture_frame_script.ps1" in text

    fixture_scripts = list(Path("tests/fixtures/story_projects").glob("*/generation_prompts/frame_*/*.ps1"))
    assert fixture_scripts
    for script in fixture_scripts:
        text = script.read_text(encoding="utf-8")
        assert "PSCommandPath" in text
        if script.name == "run_formal_crop_edits.ps1":
            assert "compose_fixture_frame_crops.ps1" in text


def test_compose_frame_crops_backend_entry_updates_rendered_variant(tmp_path):
    pipeline = _make_pipeline(tmp_path)
    script = pipeline.import_fixture_story_project("liuyi-that-day")
    manifest_path = Path(
        "tests/fixtures/story_projects/六一那天/generation_prompts/frame_15/"
        "crop_composition_manifest.json"
    ).resolve()
    output_path = Path("output/storyboard/liuyi_frame_15_compose_test.png").resolve()
    result = {
        "manifest_path": str(manifest_path),
        "base_image": str(
            Path("output/codex_image_audit/liuyi-that-day/generated/"
                 "liuyi_frame_15_stage1_base_v3.png").resolve()
        ),
        "output_image": str(output_path),
        "frame_id": "liuyi_frame_15",
        "project_slug": "liuyi-that-day",
        "schema_version": 1,
        "crops": [
            {
                "id": "adult_xiaoqi",
                "role": "adult Xiaoqi identity edit",
                "prompt": (
                    "tests/fixtures/story_projects/六一那天/"
                    "generation_prompts/frame_15/"
                    "02a_adult_xiaoqi_crop_edit_prompt.txt"
                ),
                "reference_images": [
                    "output/uploads/fixtures/liuyi_char_xiaoqi_adult_full_body.png"
                ],
                "base_crop": str(
                    Path("output/codex_image_audit/liuyi-that-day/generated/"
                         "liuyi_frame_15_stage1_base_v3_crop_xiaoqi.png").resolve()
                ),
                "edited_crop": str(
                    Path("output/codex_image_audit/liuyi-that-day/generated/"
                         "liuyi_frame_15_stage2a_xiaoqi_crop_formal_v1.png").resolve()
                ),
                "bbox": {"x": 1360, "y": 32, "width": 640, "height": 1088},
            },
            {
                "id": "boy_father",
                "role": "boy father identity edit",
                "prompt": (
                    "tests/fixtures/story_projects/六一那天/"
                    "generation_prompts/frame_15/"
                    "02c_father_crop_edit_prompt.txt"
                ),
                "reference_images": [
                    "output/uploads/fixtures/liuyi_char_boy_father_full_body.png"
                ],
                "base_crop": str(
                    Path("output/codex_image_audit/liuyi-that-day/generated/"
                         "liuyi_frame_15_stage1_base_v3_crop_father.png").resolve()
                ),
                "edited_crop": str(
                    Path("output/codex_image_audit/liuyi-that-day/generated/"
                         "liuyi_frame_15_stage2c_father_crop_formal_v1.png").resolve()
                ),
                "bbox": {"x": 0, "y": 256, "width": 1024, "height": 768},
            },
        ],
    }

    with patch(
        "src.apps.comic_gen.pipeline.compose_frame_crops_from_manifest",
        return_value=result,
    ) as mock_compose:
        updated = pipeline.compose_frame_crops(script.id, "liuyi_frame_15")

    frame_15 = next(frame for frame in updated.frames if frame.id == "liuyi_frame_15")
    crop_meta = frame_15.composition_data["crop_composition"]

    assert mock_compose.call_count == 1
    assert frame_15.status.value == "completed"
    assert frame_15.rendered_image_url == "storyboard/liuyi_frame_15_compose_test.png"
    assert frame_15.image_url == "storyboard/liuyi_frame_15_compose_test.png"
    assert frame_15.rendered_image_asset.selected_id is not None
    assert crop_meta["manifest_path"].endswith("crop_composition_manifest.json")
    assert crop_meta["output_image"] == "storyboard/liuyi_frame_15_compose_test.png"
    assert crop_meta["crops"][0]["id"] == "adult_xiaoqi"


def test_import_liuyi_fixture_project_builds_openable_storyboard_project(tmp_path):
    pipeline = _make_pipeline(tmp_path)

    script = pipeline.import_fixture_story_project("liuyi-that-day")

    assert script.title == "六一那天"
    assert script.fixture_slug == "liuyi-that-day"
    assert script.fixture_project_type == "seedance_storyboard_test"
    assert script.model_settings.t2i_model == "openai-image"
    assert script.model_settings.i2i_model == "openai-image-edit"
    assert len(script.characters) == 7
    assert len(script.scenes) == 10
    assert len(script.props) == 9
    assert len(script.frames) == 18
    assert len(script.story_analysis.scene_beats) == 18
    assert script.frames[0].story_beat_title == "镜头 01｜2008 年六一校园建立"
    assert script.frames[0].image_prompt_cn
    assert script.frames[0].video_prompt
    assert script.frames[0].composition_data["reference_binding_version"] == 1
    assert script.frames[0].composition_data["scene"]["name"] == "2008 年六一小学操场"
    assert script.frames[0].composition_data["style"]["lock"] is True
    assert any(prop.name == "白色毛绒小熊" for prop in script.props)
    assert all(frame.composition_data["fixture_role"] == "golden_storyboard_frame" for frame in script.frames)


def test_import_liuyi_fixture_project_maps_final_storyboard_assets_per_frame(tmp_path):
    pipeline = _make_pipeline(tmp_path)

    script = pipeline.import_fixture_story_project("liuyi-that-day")
    expected = {
        "liuyi_frame_01": ("liuyi_scene_school_playground", [], []),
        "liuyi_frame_02": (
            "liuyi_scene_school_playground",
            ["liuyi_char_xiaoqi_child"],
            ["liuyi_prop_child_drawing"],
        ),
        "liuyi_frame_03": (
            "liuyi_scene_hospital_room",
            ["liuyi_char_father"],
            ["liuyi_prop_white_bear", "liuyi_prop_paper_bag"],
        ),
        "liuyi_frame_04": (
            "liuyi_scene_hospital_room",
            ["liuyi_char_father", "liuyi_char_mother"],
            ["liuyi_prop_white_bear", "liuyi_prop_paper_bag"],
        ),
        "liuyi_frame_05": (
            "liuyi_scene_hospital_room",
            ["liuyi_char_father", "liuyi_char_mother"],
            ["liuyi_prop_white_bear", "liuyi_prop_paper_bag"],
        ),
        "liuyi_frame_06": (
            "liuyi_scene_school_playground",
            ["liuyi_char_xiaoqi_child"],
            [],
        ),
        "liuyi_frame_07": (
            "liuyi_scene_hospital_room",
            ["liuyi_char_mother"],
            ["liuyi_prop_paper_bag"],
        ),
        "liuyi_frame_08": (
            "liuyi_scene_school_gate",
            ["liuyi_char_xiaoqi_child", "liuyi_char_mother"],
            ["liuyi_prop_white_bear", "liuyi_prop_paper_bag"],
        ),
        "liuyi_frame_09": (
            "liuyi_scene_funeral_hall",
            ["liuyi_char_xiaoqi_child", "liuyi_char_father"],
            ["liuyi_prop_white_bear", "liuyi_prop_father_memorial_portrait"],
        ),
        "liuyi_frame_10": (
            "liuyi_scene_home_desk",
            ["liuyi_char_xiaoqi_child", "liuyi_char_father"],
            ["liuyi_prop_white_bear", "liuyi_prop_family_photo", "liuyi_prop_notebook_pencil"],
        ),
        "liuyi_frame_11": (
            "liuyi_scene_home_desk",
            ["liuyi_char_xiaoqi_young"],
            ["liuyi_prop_white_bear", "liuyi_prop_notebook_pencil"],
        ),
        "liuyi_frame_12": (
            "liuyi_scene_exam_admission",
            ["liuyi_char_xiaoqi_young"],
            ["liuyi_prop_white_bear", "liuyi_prop_admission_notice"],
        ),
        "liuyi_frame_13": (
            "liuyi_scene_medical_school",
            ["liuyi_char_xiaoqi_young"],
            ["liuyi_prop_medical_textbooks"],
        ),
        "liuyi_frame_14": (
            "liuyi_scene_doctor_office",
            ["liuyi_char_xiaoqi_adult"],
            ["liuyi_prop_white_bear", "liuyi_prop_medical_textbooks"],
        ),
        "liuyi_frame_15": (
            "liuyi_scene_2026_ward",
            ["liuyi_char_xiaoqi_adult", "liuyi_char_boy", "liuyi_char_boy_father"],
            ["liuyi_prop_childrens_day_balloons"],
        ),
        "liuyi_frame_16": (
            "liuyi_scene_2026_ward",
            ["liuyi_char_xiaoqi_adult", "liuyi_char_boy", "liuyi_char_boy_father"],
            ["liuyi_prop_childrens_day_balloons"],
        ),
        "liuyi_frame_17": (
            "liuyi_scene_2026_ward",
            ["liuyi_char_xiaoqi_adult", "liuyi_char_boy", "liuyi_char_boy_father"],
            ["liuyi_prop_childrens_day_balloons"],
        ),
        "liuyi_frame_18": (
            "liuyi_scene_hospital_corridor",
            ["liuyi_char_xiaoqi_adult"],
            ["liuyi_prop_white_bear", "liuyi_prop_medical_textbooks"],
        ),
    }

    for frame in script.frames:
        expected_scene_id, expected_character_ids, expected_prop_ids = expected[frame.id]
        composition = frame.composition_data

        assert frame.scene_id == expected_scene_id
        assert frame.character_ids == expected_character_ids
        assert frame.prop_ids == expected_prop_ids
        assert composition["scene"]["id"] == expected_scene_id
        assert [item["id"] for item in composition["characters"]] == expected_character_ids
        assert [item["id"] for item in composition["props"]] == expected_prop_ids


def test_import_liuyi_fixture_project_reuses_existing_project(tmp_path):
    pipeline = _make_pipeline(tmp_path)

    first = pipeline.import_fixture_story_project("liuyi-that-day")
    second = pipeline.import_fixture_story_project("liuyi-that-day")

    assert second.id == first.id
    assert len(pipeline.scripts) == 1


def test_reimport_liuyi_fixture_project_refreshes_storyboard_asset_mapping(tmp_path):
    pipeline = _make_pipeline(tmp_path)

    first = pipeline.import_fixture_story_project("liuyi-that-day")
    first.frames[14].scene_id = "liuyi_scene_hospital_room"
    first.frames[14].character_ids = ["liuyi_char_father"]
    first.frames[14].prop_ids = []

    refreshed = pipeline.import_fixture_story_project("liuyi-that-day")

    frame_15 = next(frame for frame in refreshed.frames if frame.id == "liuyi_frame_15")
    assert refreshed.id == first.id
    assert len(pipeline.scripts) == 1
    assert frame_15.scene_id == "liuyi_scene_2026_ward"
    assert frame_15.character_ids == [
        "liuyi_char_xiaoqi_adult",
        "liuyi_char_boy",
        "liuyi_char_boy_father",
    ]
    assert frame_15.prop_ids == ["liuyi_prop_childrens_day_balloons"]


def test_list_fixture_story_projects_discovers_liuyi_template(tmp_path):
    pipeline = _make_pipeline(tmp_path)

    fixtures = pipeline.list_fixture_story_projects()

    liuyi = next(item for item in fixtures if item["slug"] == "liuyi-that-day")
    assert liuyi["name"] == "六一那天"
    assert liuyi["project_type"] == "seedance_storyboard_test"
    assert liuyi["parser"] == "seedance_storyboard_markdown"
    assert liuyi["frame_count"] == 18
    assert liuyi["source_count"] == 1
    assert liuyi["reference_count"] == 14
    assert liuyi["model_settings"]["openai_image_model"] == "gpt-image2"
    assert liuyi["is_imported"] is False
    assert liuyi["project_id"] is None


def test_list_fixture_story_projects_marks_imported_project(tmp_path):
    pipeline = _make_pipeline(tmp_path)
    script = pipeline.import_fixture_story_project("liuyi-that-day")

    fixtures = pipeline.list_fixture_story_projects()

    liuyi = next(item for item in fixtures if item["slug"] == "liuyi-that-day")
    assert liuyi["is_imported"] is True
    assert liuyi["project_id"] == script.id


def test_locked_fixture_asset_with_master_reference_requires_unlock_before_regeneration(tmp_path):
    pipeline = _make_pipeline(tmp_path)
    script = pipeline.import_fixture_story_project("liuyi-that-day")

    with pytest.raises(ValueError, match="locked"):
        pipeline.generate_asset(script.id, "liuyi_prop_white_bear", "prop")


def test_import_fixture_endpoint_returns_normal_project_payload(tmp_path, monkeypatch):
    pipeline = _make_pipeline(tmp_path)
    monkeypatch.setattr(api_module, "pipeline", pipeline)
    client = TestClient(api_module.app)

    response = client.post("/projects/fixtures/liuyi-that-day/import")

    assert response.status_code == 200
    payload = response.json()
    assert payload["title"] == "六一那天"
    assert len(payload["frames"]) == 18


def test_compose_frame_crops_endpoint_forwards_request_payload(tmp_path, monkeypatch):
    pipeline = _make_pipeline(tmp_path)
    script = pipeline.import_fixture_story_project("liuyi-that-day")
    captured = {}

    def fake_compose_frame_crops(
        script_id,
        frame_id,
        manifest_path=None,
        output_path=None,
        verify=True,
    ):
        captured["script_id"] = script_id
        captured["frame_id"] = frame_id
        captured["manifest_path"] = manifest_path
        captured["output_path"] = output_path
        captured["verify"] = verify
        return script

    pipeline.compose_frame_crops = fake_compose_frame_crops
    monkeypatch.setattr(api_module, "pipeline", pipeline)
    client = TestClient(api_module.app)

    response = client.post(
        f"/projects/{script.id}/frames/liuyi_frame_15/compose_crops",
        json={
            "manifest_path": (
                "tests/fixtures/story_projects/六一那天/"
                "generation_prompts/frame_15/crop_composition_manifest.json"
            ),
            "output_path": "output/storyboard/liuyi_frame_15_compose_test.png",
            "verify": True,
        },
    )

    assert response.status_code == 200
    assert captured["script_id"] == script.id
    assert captured["frame_id"] == "liuyi_frame_15"
    assert captured["manifest_path"].endswith("crop_composition_manifest.json")
    assert captured["output_path"] == "output/storyboard/liuyi_frame_15_compose_test.png"
    assert captured["verify"] is True


def test_compose_frame_crops_endpoint_smoke_forwards_request_body(monkeypatch):
    captured = {}

    class StubPipeline:
        def compose_frame_crops(
            self,
            script_id,
            frame_id,
            manifest_path=None,
            output_path=None,
            verify=True,
        ):
            captured["script_id"] = script_id
            captured["frame_id"] = frame_id
            captured["manifest_path"] = manifest_path
            captured["output_path"] = output_path
            captured["verify"] = verify
            return {"status": "ok"}

    monkeypatch.setattr(api_module, "pipeline", StubPipeline())
    monkeypatch.setattr(
        api_module,
        "signed_response",
        lambda result: JSONResponse(content={"status": "ok"}),
    )
    client = TestClient(api_module.app)

    response = client.post(
        "/projects/script-123/frames/liuyi_frame_15/compose_crops",
        json={
            "manifest_path": (
                "tests/fixtures/story_projects/六一那天/"
                "generation_prompts/frame_15/crop_composition_manifest.json"
            ),
            "output_path": "output/storyboard/liuyi_frame_15_compose_smoke.png",
            "verify": False,
        },
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert captured == {
        "script_id": "script-123",
        "frame_id": "liuyi_frame_15",
        "manifest_path": (
            "tests/fixtures/story_projects/六一那天/"
            "generation_prompts/frame_15/crop_composition_manifest.json"
        ),
        "output_path": "output/storyboard/liuyi_frame_15_compose_smoke.png",
        "verify": False,
    }


def test_import_liuyi_fixture_project_binds_locked_master_references(tmp_path):
    pipeline = _make_pipeline(tmp_path)

    script = pipeline.import_fixture_story_project("liuyi-that-day")

    xiaoqi = next(item for item in script.characters if item.id == "liuyi_char_xiaoqi_child")
    adult_xiaoqi = next(item for item in script.characters if item.id == "liuyi_char_xiaoqi_adult")
    mother = next(item for item in script.characters if item.id == "liuyi_char_mother")
    father = next(item for item in script.characters if item.id == "liuyi_char_father")
    boy = next(item for item in script.characters if item.id == "liuyi_char_boy")
    boy_father = next(item for item in script.characters if item.id == "liuyi_char_boy_father")
    playground = next(item for item in script.scenes if item.id == "liuyi_scene_school_playground")
    hospital = next(item for item in script.scenes if item.id == "liuyi_scene_hospital_room")
    ward_2026 = next(item for item in script.scenes if item.id == "liuyi_scene_2026_ward")
    corridor = next(item for item in script.scenes if item.id == "liuyi_scene_hospital_corridor")
    bear = next(item for item in script.props if item.id == "liuyi_prop_white_bear")
    balloons = next(item for item in script.props if item.id == "liuyi_prop_childrens_day_balloons")
    medical_textbooks = next(item for item in script.props if item.id == "liuyi_prop_medical_textbooks")

    assert xiaoqi.locked is True
    assert adult_xiaoqi.locked is True
    assert mother.locked is True
    assert father.locked is True
    assert boy.locked is True
    assert boy_father.locked is True
    assert xiaoqi.full_body_asset.selected_id == "liuyi_char_xiaoqi_child_full_body_fixture"
    assert adult_xiaoqi.full_body_asset.selected_id == "liuyi_char_xiaoqi_adult_full_body_fixture"
    assert mother.full_body_asset.selected_id == "liuyi_char_mother_full_body_fixture"
    assert father.full_body_asset.selected_id == "liuyi_char_father_full_body_fixture"
    assert boy.full_body_asset.selected_id == "liuyi_char_boy_full_body_fixture"
    assert boy_father.full_body_asset.selected_id == "liuyi_char_boy_father_full_body_fixture"
    assert xiaoqi.full_body_asset.variants[0].is_uploaded_source is True
    assert playground.locked is True
    assert hospital.locked is True
    assert ward_2026.locked is True
    assert corridor.locked is True
    assert bear.locked is True
    assert balloons.locked is True
    assert medical_textbooks.locked is True
    assert playground.image_asset.selected_id == "liuyi_scene_school_playground_image_fixture"
    assert hospital.image_asset.selected_id == "liuyi_scene_hospital_room_image_fixture"
    assert ward_2026.image_asset.selected_id == "liuyi_scene_2026_ward_image_fixture"
    assert corridor.image_asset.selected_id == "liuyi_scene_hospital_corridor_image_fixture"
    assert bear.image_asset.selected_id == "liuyi_prop_white_bear_image_fixture"
    assert balloons.image_asset.selected_id == "liuyi_prop_childrens_day_balloons_image_fixture"
    assert medical_textbooks.image_asset.selected_id == "liuyi_prop_medical_textbooks_image_fixture"
    assert playground.image_asset.variants[0].is_uploaded_source is True
    assert bear.image_asset.variants[0].is_uploaded_source is True


def test_storyboard_render_includes_frame_locked_asset_references(tmp_path):
    pipeline = _make_pipeline(tmp_path)
    script = pipeline.import_fixture_story_project("liuyi-that-day")
    captured = {}

    def fake_generate_frame(frame, characters, scene, **kwargs):
        captured["ref_image_paths"] = kwargs.get("ref_image_paths") or []
        return frame

    pipeline.storyboard_generator.generate_frame = fake_generate_frame

    pipeline.generate_storyboard_render(
        script.id,
        "liuyi_frame_03",
        {
            "reference_image_urls": ["uploads/fixtures/liuyi-that-day-storyboard-reference.png"],
            "continuity_lock": False,
        },
        "测试静帧提示词",
    )

    ref_paths = captured["ref_image_paths"]
    normalized_paths = _normalize_ref_paths(ref_paths)

    assert ref_paths == list(dict.fromkeys(ref_paths))
    assert any(
        path.endswith("output/uploads/fixtures/liuyi-that-day-storyboard-reference.png")
        for path in normalized_paths
    )
    assert any(
        path.endswith("output/uploads/fixtures/liuyi_scene_hospital_room.png")
        for path in normalized_paths
    )
    assert any(
        path.endswith("output/uploads/fixtures/liuyi_char_father_full_body.png")
        for path in normalized_paths
    )
    assert any(
        path.endswith("output/uploads/fixtures/liuyi_prop_white_bear.png")
        for path in normalized_paths
    )


def test_storyboard_render_uses_only_the_current_frame_asset_references(tmp_path):
    pipeline = _make_pipeline(tmp_path)
    script = pipeline.import_fixture_story_project("liuyi-that-day")
    captured = {}

    def fake_generate_frame(frame, characters, scene, **kwargs):
        captured["ref_image_paths"] = kwargs.get("ref_image_paths") or []
        return frame

    pipeline.storyboard_generator.generate_frame = fake_generate_frame

    pipeline.generate_storyboard_render(
        script.id,
        "liuyi_frame_02",
        {"continuity_lock": False},
        "测试小琪等待爸爸静帧提示词",
    )

    normalized_paths = _normalize_ref_paths(captured["ref_image_paths"])

    assert any(
        path.endswith("output/uploads/fixtures/liuyi_scene_school_playground.png")
        for path in normalized_paths
    )
    assert any(
        path.endswith("output/uploads/fixtures/liuyi_char_xiaoqi_child_full_body.png")
        for path in normalized_paths
    )
    assert not any(
        path.endswith("output/uploads/fixtures/liuyi_char_father_full_body.png")
        for path in normalized_paths
    )


def test_storyboard_render_uses_safe_staged_strategy_for_frame_15(tmp_path):
    pipeline = _make_pipeline(tmp_path)
    script = pipeline.import_fixture_story_project("liuyi-that-day")
    captured = {}

    def fake_generate_frame(frame, characters, scene, **kwargs):
        captured["ref_image_paths"] = kwargs.get("ref_image_paths") or []
        captured["model_name"] = kwargs.get("model_name")
        captured["prompt"] = kwargs.get("prompt")
        captured["suppress_auto_references"] = kwargs.get("suppress_auto_references")
        return frame

    pipeline.storyboard_generator.generate_frame = fake_generate_frame

    pipeline.generate_storyboard_render(
        script.id,
        "liuyi_frame_15",
        {"continuity_lock": False},
        "测试 2026 病房静帧提示词",
    )

    frame_15 = next(frame for frame in script.frames if frame.id == "liuyi_frame_15")
    strategy = frame_15.composition_data["render_strategy"]

    assert captured["ref_image_paths"] == []
    assert captured["model_name"] == script.model_settings.t2i_model
    assert captured["suppress_auto_references"] is True
    assert "分阶段基础构图要求" in captured["prompt"]
    assert strategy["mode"] == "staged_safe_storyboard"
    assert "medical_context" in strategy["reason_codes"]
    assert "minor_context" in strategy["reason_codes"]
    assert "patient_context" in strategy["reason_codes"]
    assert strategy["direct_multi_reference_edit_allowed"] is False
    assert strategy["detected_context"]["minor_character_ids"] == [
        "liuyi_char_boy",
    ]
    assert strategy["detected_context"]["vulnerable_patient_character_ids"] == [
        "liuyi_char_boy_father",
    ]
    assert strategy["detected_context"]["has_medical_context"] is True


def test_storyboard_render_includes_frame_18_generated_reference_assets(tmp_path):
    pipeline = _make_pipeline(tmp_path)
    script = pipeline.import_fixture_story_project("liuyi-that-day")
    captured = {}

    def fake_generate_frame(frame, characters, scene, **kwargs):
        captured["ref_image_paths"] = kwargs.get("ref_image_paths") or []
        return frame

    pipeline.storyboard_generator.generate_frame = fake_generate_frame

    pipeline.generate_storyboard_render(
        script.id,
        "liuyi_frame_18",
        {"continuity_lock": False},
        "测试 2026 医院走廊收束静帧提示词",
    )

    normalized_paths = _normalize_ref_paths(captured["ref_image_paths"])

    expected_suffixes = [
        "output/uploads/fixtures/liuyi_scene_hospital_corridor.png",
        "output/uploads/fixtures/liuyi_char_xiaoqi_adult_full_body.png",
        "output/uploads/fixtures/liuyi_prop_white_bear.png",
        "output/uploads/fixtures/liuyi_prop_medical_textbooks.png",
    ]
    for suffix in expected_suffixes:
        assert any(path.endswith(suffix) for path in normalized_paths)


def test_list_fixture_endpoint_returns_template_library(tmp_path, monkeypatch):
    pipeline = _make_pipeline(tmp_path)
    monkeypatch.setattr(api_module, "pipeline", pipeline)
    client = TestClient(api_module.app)

    response = client.get("/projects/fixtures")

    assert response.status_code == 200
    payload = response.json()
    liuyi = next(item for item in payload if item["slug"] == "liuyi-that-day")
    assert liuyi["name"] == "六一那天"
    assert liuyi["frame_count"] == 18
    assert liuyi["is_imported"] is False
