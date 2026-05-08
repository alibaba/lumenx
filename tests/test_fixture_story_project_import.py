import json
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image, ImageChops, ImageStat
from fastapi.responses import JSONResponse
import pytest
from fastapi.testclient import TestClient

from src.apps.comic_gen import api as api_module
from src.apps.comic_gen.pipeline import ComicGenPipeline


def _normalize_ref_paths(ref_paths):
    return [str(ref).replace("\\", "/") for ref in ref_paths]


def _bbox_text(bbox):
    return (
        f'{{ "x": {bbox["x"]}, "y": {bbox["y"]}, '
        f'"width": {bbox["width"]}, "height": {bbox["height"]} }}'
    )


def _masked_rgb_mean_delta(image_a, image_b, mask):
    a = np.asarray(image_a.convert("RGB"), dtype=np.int16)
    b = np.asarray(image_b.convert("RGB"), dtype=np.int16)
    mask_array = np.asarray(mask, dtype=np.uint8) > 180
    assert mask_array.any()
    return np.abs(a - b)[mask_array].mean()


def _mean_rgb_delta(image_a, image_b):
    diff = ImageChops.difference(image_a.convert("RGB"), image_b.convert("RGB"))
    return sum(ImageStat.Stat(diff).mean) / 3.0


def _assert_edited_crop_visual_gate(manifest, crop):
    bbox = crop["bbox"]
    base_crop_path = Path(crop["base_crop"])
    edited_crop_path = Path(crop["edited_crop"])
    output_path = Path(manifest["output_image"])

    assert crop["visual_gate"]["gate_id"] == "formal_crop_identity_vs_base_and_compose"
    assert base_crop_path.exists(), crop["base_crop"]
    assert edited_crop_path.exists(), crop["edited_crop"]
    assert output_path.exists(), manifest["output_image"]

    with Image.open(base_crop_path).convert("RGB") as base_crop, Image.open(edited_crop_path).convert("RGB") as edited_crop:
        assert base_crop.size == edited_crop.size == (bbox["width"], bbox["height"])
        assert ImageChops.difference(base_crop, edited_crop).getbbox() is not None
        assert _mean_rgb_delta(base_crop, edited_crop) > crop["visual_gate"]["min_changed_mean_delta"]

    with Image.open(output_path).convert("RGB") as output_image, Image.open(edited_crop_path).convert("RGB") as edited_crop:
        output_crop = output_image.crop(
            (
                bbox["x"],
                bbox["y"],
                bbox["x"] + bbox["width"],
                bbox["y"] + bbox["height"],
            )
        )
        assert ImageChops.difference(output_crop, edited_crop).getbbox() is None


def _make_pipeline(state_root: Path | None = None):
    with patch("src.apps.comic_gen.pipeline.AssetGenerator"), \
         patch("src.apps.comic_gen.pipeline.StoryboardGenerator"), \
         patch("src.apps.comic_gen.pipeline.VideoGenerator"), \
         patch("src.apps.comic_gen.pipeline.AudioGenerator"), \
         patch("src.apps.comic_gen.pipeline.ExportManager"):
        pipeline = ComicGenPipeline()

    if state_root is None:
        state_root = LIUYI_TEST_STATE_ROOT
    state_root.mkdir(parents=True, exist_ok=True)
    pipeline.data_file = str(state_root / "projects.json")
    pipeline.series_data_file = str(state_root / "series.json")
    pipeline.scripts = {}
    pipeline.series_store = {}
    return pipeline


LIUYI_TEST_STATE_ROOT = Path("output/codex_image_audit/liuyi-that-day/_test_state/fixture_import")
LIUYI_STATIC_FRAME_EXPORT_VISUAL_GATE = "test_liuyi_static_frame_exports_are_complete_and_openable"


LIUYI_READY_FRAME_CROP_EXPECTATIONS = {
    "frame_16": {
        "frame_id": "liuyi_frame_16",
        "base_image": (
            "output/codex_image_audit/liuyi-that-day/generated/"
            "liuyi_frame_16_stage1_base.png"
        ),
        "output_image": (
            "output/codex_image_audit/liuyi-that-day/generated/"
            "liuyi_frame_16_stage3_full_formal_v1.png"
        ),
        "base_crop": (
            "output/codex_image_audit/liuyi-that-day/generated/"
            "liuyi_frame_16_stage1_base_crop_adult_xiaoqi.png"
        ),
        "edited_crop": (
            "output/codex_image_audit/liuyi-that-day/generated/"
            "liuyi_frame_16_stage2_adult_xiaoqi_formal_v1.png"
        ),
        "bbox": {"x": 1280, "y": 256, "width": 768, "height": 896},
        "size": "768x896",
    },
    "frame_17": {
        "frame_id": "liuyi_frame_17",
        "base_image": (
            "output/codex_image_audit/liuyi-that-day/generated/"
            "liuyi_frame_17_stage1_base.png"
        ),
        "output_image": (
            "output/codex_image_audit/liuyi-that-day/generated/"
            "liuyi_frame_17_stage3_full_formal_v1.png"
        ),
        "base_crop": (
            "output/codex_image_audit/liuyi-that-day/generated/"
            "liuyi_frame_17_stage1_base_crop_adult_xiaoqi.png"
        ),
        "edited_crop": (
            "output/codex_image_audit/liuyi-that-day/generated/"
            "liuyi_frame_17_stage2_adult_xiaoqi_formal_v1.png"
        ),
        "bbox": {"x": 1280, "y": 256, "width": 768, "height": 896},
        "size": "768x896",
    },
    "frame_18": {
        "frame_id": "liuyi_frame_18",
        "base_image": (
            "output/codex_image_audit/liuyi-that-day/generated/"
            "liuyi_frame_18_stage1_base.png"
        ),
        "output_image": (
            "output/codex_image_audit/liuyi-that-day/generated/"
            "liuyi_frame_18_stage3_full_formal_v1.png"
        ),
        "base_crop": (
            "output/codex_image_audit/liuyi-that-day/generated/"
            "liuyi_frame_18_stage1_base_crop_adult_xiaoqi.png"
        ),
        "edited_crop": (
            "output/codex_image_audit/liuyi-that-day/generated/"
            "liuyi_frame_18_stage2_adult_xiaoqi_formal_v1.png"
        ),
        "bbox": {"x": 1024, "y": 128, "width": 1024, "height": 1024},
        "size": "1024x1024",
    },
}


LIUYI_STATIC_FRAME_EXPORT_EXPECTATIONS = {
    "frame_01": {
        "output": "liuyi_frame_01_stage3_full_formal_v1.png",
        "bbox": '{ "x": 0, "y": 0, "width": 448, "height": 252 }',
        "references": ["liuyi_scene_school_playground.png"],
    },
    "frame_03": {
        "output": "liuyi_frame_03_stage3_full_formal_v1.png",
        "bbox": '{ "x": 896, "y": 0, "width": 448, "height": 252 }',
        "references": [
            "liuyi_scene_hospital_room.png",
            "liuyi_char_father_full_body.png",
            "liuyi_prop_white_bear.png",
            "liuyi_prop_paper_bag.png",
        ],
    },
    "frame_04": {
        "output": "liuyi_frame_04_stage3_full_formal_v1.png",
        "bbox": '{ "x": 0, "y": 286, "width": 448, "height": 252 }',
        "references": [
            "liuyi_scene_hospital_room.png",
            "liuyi_char_father_full_body.png",
            "liuyi_char_mother_full_body.png",
            "liuyi_prop_white_bear.png",
            "liuyi_prop_paper_bag.png",
        ],
    },
    "frame_05": {
        "output": "liuyi_frame_05_stage3_full_formal_v1.png",
        "bbox": '{ "x": 448, "y": 286, "width": 448, "height": 252 }',
        "references": [
            "liuyi_scene_hospital_room.png",
            "liuyi_char_father_full_body.png",
            "liuyi_char_mother_full_body.png",
            "liuyi_prop_white_bear.png",
            "liuyi_prop_paper_bag.png",
        ],
    },
    "frame_07": {
        "output": "liuyi_frame_07_stage3_full_formal_v1.png",
        "bbox": '{ "x": 0, "y": 572, "width": 448, "height": 252 }',
        "references": [
            "liuyi_scene_hospital_room.png",
            "liuyi_char_mother_full_body.png",
            "liuyi_prop_paper_bag.png",
        ],
    },
    "frame_11": {
        "output": "liuyi_frame_11_stage3_full_formal_v1.png",
        "bbox": '{ "x": 448, "y": 858, "width": 448, "height": 252 }',
        "references": [
            "liuyi_scene_home_desk.png",
            "liuyi_char_xiaoqi_young_full_body.png",
            "liuyi_prop_white_bear.png",
            "liuyi_prop_notebook_pencil.png",
        ],
    },
    "frame_12": {
        "output": "liuyi_frame_12_stage3_full_formal_v1.png",
        "bbox": '{ "x": 896, "y": 858, "width": 448, "height": 252 }',
        "references": [
            "liuyi_scene_exam_admission.png",
            "liuyi_char_xiaoqi_young_full_body.png",
            "liuyi_prop_white_bear.png",
            "liuyi_prop_admission_notice.png",
        ],
    },
    "frame_13": {
        "output": "liuyi_frame_13_stage3_full_formal_v1.png",
        "bbox": '{ "x": 0, "y": 1144, "width": 448, "height": 252 }',
        "references": [
            "liuyi_scene_medical_school.png",
            "liuyi_char_xiaoqi_young_full_body.png",
            "liuyi_prop_medical_textbooks.png",
        ],
    },
    "frame_14": {
        "output": "liuyi_frame_14_stage3_full_formal_v1.png",
        "bbox": '{ "x": 448, "y": 1144, "width": 448, "height": 252 }',
        "references": [
            "liuyi_scene_doctor_office.png",
            "liuyi_char_xiaoqi_adult_full_body.png",
            "liuyi_prop_white_bear.png",
            "liuyi_prop_medical_textbooks.png",
        ],
    },
}


LIUYI_CHILD_IDENTITY_FRAME_EXPECTATIONS = {
    "frame_02": {
        "frame_id": "liuyi_frame_02",
        "size": "768x1152",
        "bbox": {"x": 640, "y": 0, "width": 768, "height": 1152},
        "identity_patch_bbox": {"x": 0, "y": 70, "width": 360, "height": 360},
        "source_collage_bbox": {"x": 448, "y": 0, "width": 448, "height": 252},
        "base_image": (
            "output/codex_image_audit/liuyi-that-day/generated/"
            "liuyi_frame_02_stage1_collage_base.png"
        ),
        "base_crop": (
            "output/codex_image_audit/liuyi-that-day/generated/"
            "liuyi_frame_02_stage1_collage_base_crop_child_xiaoqi.png"
        ),
        "edited_crop": (
            "output/codex_image_audit/liuyi-that-day/generated/"
            "liuyi_frame_02_stage2_child_xiaoqi_formal_v1.png"
        ),
        "output_image": (
            "output/codex_image_audit/liuyi-that-day/generated/"
            "liuyi_frame_02_stage3_full_formal_v1.png"
        ),
    },
    "frame_06": {
        "frame_id": "liuyi_frame_06",
        "size": "768x1152",
        "bbox": {"x": 512, "y": 0, "width": 768, "height": 1152},
        "identity_patch_bbox": {"x": 240, "y": 150, "width": 360, "height": 360},
        "source_collage_bbox": {"x": 896, "y": 286, "width": 448, "height": 252},
        "base_image": (
            "output/codex_image_audit/liuyi-that-day/generated/"
            "liuyi_frame_06_stage1_collage_base.png"
        ),
        "base_crop": (
            "output/codex_image_audit/liuyi-that-day/generated/"
            "liuyi_frame_06_stage1_collage_base_crop_child_xiaoqi.png"
        ),
        "edited_crop": (
            "output/codex_image_audit/liuyi-that-day/generated/"
            "liuyi_frame_06_stage2_child_xiaoqi_formal_v1.png"
        ),
        "output_image": (
            "output/codex_image_audit/liuyi-that-day/generated/"
            "liuyi_frame_06_stage3_full_formal_v1.png"
        ),
    },
    "frame_08": {
        "frame_id": "liuyi_frame_08",
        "size": "832x1152",
        "bbox": {"x": 208, "y": 0, "width": 832, "height": 1152},
        "identity_patch_bbox": {"x": 0, "y": 110, "width": 360, "height": 360},
        "source_collage_bbox": {"x": 448, "y": 572, "width": 448, "height": 252},
        "base_image": (
            "output/codex_image_audit/liuyi-that-day/generated/"
            "liuyi_frame_08_stage1_collage_base.png"
        ),
        "base_crop": (
            "output/codex_image_audit/liuyi-that-day/generated/"
            "liuyi_frame_08_stage1_collage_base_crop_child_xiaoqi.png"
        ),
        "edited_crop": (
            "output/codex_image_audit/liuyi-that-day/generated/"
            "liuyi_frame_08_stage2_child_xiaoqi_formal_v1.png"
        ),
        "output_image": (
            "output/codex_image_audit/liuyi-that-day/generated/"
            "liuyi_frame_08_stage3_full_formal_v1.png"
        ),
    },
    "frame_09": {
        "frame_id": "liuyi_frame_09",
        "size": "832x1152",
        "bbox": {"x": 160, "y": 0, "width": 832, "height": 1152},
        "identity_patch_bbox": {"x": 250, "y": 100, "width": 360, "height": 360},
        "source_collage_bbox": {"x": 896, "y": 572, "width": 448, "height": 252},
        "base_image": (
            "output/codex_image_audit/liuyi-that-day/generated/"
            "liuyi_frame_09_stage1_collage_base.png"
        ),
        "base_crop": (
            "output/codex_image_audit/liuyi-that-day/generated/"
            "liuyi_frame_09_stage1_collage_base_crop_child_xiaoqi.png"
        ),
        "edited_crop": (
            "output/codex_image_audit/liuyi-that-day/generated/"
            "liuyi_frame_09_stage2_child_xiaoqi_formal_v1.png"
        ),
        "output_image": (
            "output/codex_image_audit/liuyi-that-day/generated/"
            "liuyi_frame_09_stage3_full_formal_v1.png"
        ),
    },
    "frame_10": {
        "frame_id": "liuyi_frame_10",
        "size": "896x1152",
        "bbox": {"x": 0, "y": 0, "width": 896, "height": 1152},
        "identity_patch_bbox": {"x": 550, "y": 60, "width": 330, "height": 330},
        "source_collage_bbox": {"x": 0, "y": 858, "width": 448, "height": 252},
        "base_image": (
            "output/codex_image_audit/liuyi-that-day/generated/"
            "liuyi_frame_10_stage1_collage_base.png"
        ),
        "base_crop": (
            "output/codex_image_audit/liuyi-that-day/generated/"
            "liuyi_frame_10_stage1_collage_base_crop_child_xiaoqi.png"
        ),
        "edited_crop": (
            "output/codex_image_audit/liuyi-that-day/generated/"
            "liuyi_frame_10_stage2_child_xiaoqi_formal_v1.png"
        ),
        "output_image": (
            "output/codex_image_audit/liuyi-that-day/generated/"
            "liuyi_frame_10_stage3_full_formal_v1.png"
        ),
    },
}


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
    assert all(
        crop["visual_gate"]["gate_id"] == "formal_crop_identity_vs_base_and_compose"
        for crop in manifest["crops"]
    )
    assert any("compose_fixture_frame_crops.ps1" in note for note in manifest["notes"])
    assert any("Stage2 crops must differ" in note for note in manifest["notes"])
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


def test_liuyi_ready_frame_crop_manifests_lock_bbox_and_outputs():
    prompts_root = Path("tests/fixtures/story_projects/六一那天/generation_prompts")

    for frame_dirname, expected in LIUYI_READY_FRAME_CROP_EXPECTATIONS.items():
        manifest_path = prompts_root / frame_dirname / "crop_composition_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        crop = manifest["crops"][0]

        assert manifest["project_slug"] == "liuyi-that-day"
        assert manifest["frame_id"] == expected["frame_id"]
        assert manifest["base_image"] == expected["base_image"]
        assert manifest["output_image"] == expected["output_image"]
        assert crop["id"] == "adult_xiaoqi"
        assert crop["role"] == "adult Xiaoqi identity edit"
        assert crop["prompt"] == (
            "tests/fixtures/story_projects/六一那天/generation_prompts/"
            f"{frame_dirname}/02a_adult_xiaoqi_crop_edit_prompt.txt"
        )
        assert crop["reference_images"] == [
            "output/uploads/fixtures/liuyi_char_xiaoqi_adult_full_body.png"
        ]
        assert crop["base_crop"] == expected["base_crop"]
        assert crop["edited_crop"] == expected["edited_crop"]
        assert crop["bbox"] == expected["bbox"]
        assert crop["visual_gate"]["gate_id"] == "formal_crop_identity_vs_base_and_compose"


def test_liuyi_formal_crop_workflows_change_pixels_and_compose_outputs():
    prompts_root = Path("tests/fixtures/story_projects/六一那天/generation_prompts")
    frame_dirs = ["frame_15", *LIUYI_READY_FRAME_CROP_EXPECTATIONS.keys()]

    for frame_dirname in frame_dirs:
        manifest_path = prompts_root / frame_dirname / "crop_composition_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        with Image.open(manifest["output_image"]).convert("RGB") as output_image:
            assert output_image.size == (2048, 1152)
            assert output_image.getbbox() is not None

        for crop in manifest["crops"]:
            _assert_edited_crop_visual_gate(manifest, crop)


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
    assert (
        template_manifest["crops"][0]["visual_gate"]["gate_id"]
        == "formal_crop_identity_vs_base_and_compose"
    )
    assert any("compose_fixture_frame_crops.ps1" in note for note in template_manifest["notes"])
    assert any("Stage2 crops must differ" in note for note in template_manifest["notes"])

    template_readme = (template_dir / "README.md").read_text(encoding="utf-8")
    assert "visual_gate" in template_readme
    assert "文件存在" in template_readme

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


def test_liuyi_formal_frame_template_dirs_are_scaffolded():
    prompts_root = Path("tests/fixtures/story_projects/六一那天/generation_prompts")
    assert (prompts_root / "README.md").exists()
    assert (prompts_root / "static_frame_exports.json").exists()

    for frame_dirname, expected in LIUYI_STATIC_FRAME_EXPORT_EXPECTATIONS.items():
        frame_dir = prompts_root / frame_dirname
        readme_path = frame_dir / "README.md"
        assert readme_path.exists()
        readme_text = readme_path.read_text(encoding="utf-8")
        assert "Formal Static Workflow" in readme_text
        assert "占位骨架目录" not in readme_text
        assert expected["bbox"] in readme_text
        assert expected["output"] in readme_text
        assert "2048x1152" in readme_text
        assert "static_frame_exports.json" in readme_text
        for reference_name in expected["references"]:
            assert reference_name in readme_text

    for frame_dirname, expected in LIUYI_CHILD_IDENTITY_FRAME_EXPECTATIONS.items():
        frame_dir = prompts_root / frame_dirname
        readme_text = (frame_dir / "README.md").read_text(encoding="utf-8")
        manifest_path = frame_dir / "crop_composition_manifest.json"
        script_path = frame_dir / "run_formal_crop_edits.ps1"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        assert "Child Identity Workflow" in readme_text
        assert "identity-preserve" in readme_text
        assert _bbox_text(expected["bbox"]) in readme_text
        assert _bbox_text(expected["source_collage_bbox"]) in readme_text
        assert "Reference source bbox" in readme_text
        assert 'Identity patch bbox:' in readme_text
        assert expected["base_crop"] in readme_text
        assert expected["edited_crop"] in readme_text
        assert expected["output_image"] in readme_text
        assert "liuyi_char_xiaoqi_child_full_body.png" in readme_text
        assert "scripts/compose_liuyi_child_identity_crop.py" in readme_text

        assert manifest["project_slug"] == "liuyi-that-day"
        assert manifest["frame_id"] == expected["frame_id"]
        assert manifest["base_image"] == expected["base_image"]
        assert manifest["output_image"] == expected["output_image"]
        assert manifest["crops"][0]["id"] == "child_xiaoqi"
        assert manifest["crops"][0]["role"] == "child Xiaoqi identity edit"
        assert manifest["crops"][0]["bbox"] == expected["bbox"]
        assert manifest["crops"][0]["reference_images"] == [
            "output/uploads/fixtures/liuyi_char_xiaoqi_child_full_body.png"
        ]
        assert manifest["crops"][0]["reference_source_bbox"] == {
            "x": 300,
            "y": 80,
            "width": 420,
            "height": 420,
        }
        assert manifest["crops"][0]["identity_patch_bbox"] == expected.get("identity_patch_bbox")
        assert manifest["crops"][0]["visual_gate"]["gate_id"] == "child_xiaoqi_reference_patch_similarity"
        assert manifest["crops"][0]["edited_crop"] == expected["edited_crop"]
        assert manifest["crops"][0]["base_crop"] == expected["base_crop"]
        assert script_path.exists()
        script_text = script_path.read_text(encoding="utf-8")
        assert "compose_liuyi_child_identity_crop.py" in script_text
        assert "crop_composition_manifest.json" in script_text
        assert "liuyi_char_xiaoqi_child_full_body.png" not in script_text
        assert "gpt-image2" not in script_text
        assert "quality medium" not in script_text
        assert "Force" in script_text

    assert (prompts_root / "frame_15" / "README.md").exists()

    for frame_dirname in LIUYI_READY_FRAME_CROP_EXPECTATIONS:
        frame_dir = prompts_root / frame_dirname
        readme_text = (frame_dir / "README.md").read_text(encoding="utf-8")
        assert "Crop Workflow" in readme_text
        assert "占位骨架目录" not in readme_text
        expected = LIUYI_READY_FRAME_CROP_EXPECTATIONS[frame_dirname]
        assert expected["output_image"] in readme_text
        assert expected["base_image"] in readme_text
        assert expected["base_crop"] in readme_text
        assert expected["edited_crop"] in readme_text
        manifest_path = frame_dir / "crop_composition_manifest.json"
        script_path = frame_dir / "run_formal_crop_edits.ps1"
        assert manifest_path.exists()
        assert script_path.exists()
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest["project_slug"] == "liuyi-that-day"
        assert manifest["frame_id"] == expected["frame_id"]
        assert manifest["crops"][0]["id"] == "adult_xiaoqi"
        assert manifest["crops"][0]["bbox"] == expected["bbox"]
        assert manifest["output_image"] == expected["output_image"]
        assert "TODO" not in json.dumps(manifest, ensure_ascii=False)
        script_text = script_path.read_text(encoding="utf-8")
        assert "gpt-image2" in script_text
        assert expected["size"] in script_text
        assert "TODO" not in script_text

    assert (prompts_root / "frame_15" / "README.md").exists()


def test_liuyi_static_frame_exports_are_complete_and_openable():
    export_manifest = json.loads(
        Path("tests/fixtures/story_projects/六一那天/generation_prompts/static_frame_exports.json").read_text(
            encoding="utf-8"
        )
    )
    exports = export_manifest["frame_exports"]
    frame_ids = {f"liuyi_frame_{index:02d}" for index in range(1, 19)}
    collage_frame_ids = {f"liuyi_{frame_dirname}" for frame_dirname in LIUYI_STATIC_FRAME_EXPORT_EXPECTATIONS}
    child_frame_ids = {f"liuyi_{frame_dirname}" for frame_dirname in LIUYI_CHILD_IDENTITY_FRAME_EXPECTATIONS}

    assert export_manifest["project_slug"] == "liuyi-that-day"
    assert export_manifest["status"] == "formal_static_frame_files_complete_visual_gate_required"
    assert export_manifest["completion_scope"]["file_exports"] == (
        "18/18 stage3 output files are declared and openable; file completion only, not visual consistency"
    )
    assert "base-vs-edited pixel checks" in export_manifest["completion_scope"]["formal_crop_workflows"]
    assert export_manifest["output_size"] == {"width": 2048, "height": 1152}
    assert len(exports) == 18
    assert {entry["frame_id"] for entry in exports} == frame_ids

    for entry in exports:
        output_path = Path(entry["output_image"])
        assert output_path.exists(), entry["output_image"]
        with Image.open(output_path) as image:
            assert image.size == (2048, 1152)
            assert image.getbbox() is not None
            assert max(ImageStat.Stat(image.convert("RGB")).var) > 0

        if entry["frame_id"] in collage_frame_ids:
            frame_dirname = entry["frame_id"].replace("liuyi_", "")
            expected = LIUYI_STATIC_FRAME_EXPORT_EXPECTATIONS[frame_dirname]
            assert entry["source_type"] == "collage_static_export"
            assert Path(entry["source_collage"]).exists()
            assert entry["collage_bbox"] == json.loads(expected["bbox"])
            assert entry["output_image"].endswith(expected["output"])
            assert entry["visual_gate"] == LIUYI_STATIC_FRAME_EXPORT_VISUAL_GATE
        elif entry["frame_id"] in child_frame_ids:
            frame_dirname = entry["frame_id"].replace("liuyi_", "")
            expected = LIUYI_CHILD_IDENTITY_FRAME_EXPECTATIONS[frame_dirname]
            assert entry["source_type"] == "child_identity_preserve_workflow"
            assert Path(entry["source_collage"]).exists()
            assert entry["source_collage_bbox"] == expected["source_collage_bbox"]
            assert entry["workflow_manifest"].endswith(
                f"generation_prompts/{frame_dirname}/crop_composition_manifest.json"
            )
            assert entry["identity_reference_asset"] == "liuyi_char_xiaoqi_child"
            assert entry["identity_reference_image"] == (
                "output/uploads/fixtures/liuyi_char_xiaoqi_child_full_body.png"
            )
            assert entry["identity_reference_source_bbox"] == {
                "x": 300,
                "y": 80,
                "width": 420,
                "height": 420,
            }
            assert entry["identity_patch_bbox"] == expected["identity_patch_bbox"]
            assert entry["visual_gate"] == "test_liuyi_child_identity_visual_gate_embeds_locked_reference"
            assert entry["identity_crop_bbox"] == expected["bbox"]
            assert entry["output_image"] == expected["output_image"]
        else:
            assert entry["source_type"] == "formal_crop_workflow"
            assert Path(entry["workflow_manifest"]).exists()
            assert entry["visual_gate"] == "test_liuyi_formal_crop_workflows_change_pixels_and_compose_outputs"


def test_story_fixture_projects_use_explicit_visual_gate_contracts():
    story_projects_root = Path("tests/fixtures/story_projects")
    fixture_dirs = [
        path
        for path in story_projects_root.iterdir()
        if path.is_dir() and path.name != "_templates"
    ]
    assert fixture_dirs

    for fixture_dir in fixture_dirs:
        prompts_root = fixture_dir / "generation_prompts"
        static_exports_path = prompts_root / "static_frame_exports.json"
        if static_exports_path.exists():
            static_exports = json.loads(static_exports_path.read_text(encoding="utf-8"))
            frame_exports = static_exports.get("frame_exports") or []
            assert frame_exports, static_exports_path
            for entry in frame_exports:
                assert entry.get("frame_id"), static_exports_path
                assert isinstance(entry.get("visual_gate"), str) and entry["visual_gate"].strip()

        crop_manifest_paths = sorted(prompts_root.glob("frame_*/crop_composition_manifest.json"))
        for manifest_path in crop_manifest_paths:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            crops = manifest.get("crops") or []
            assert crops, manifest_path
            for crop in crops:
                visual_gate = crop.get("visual_gate") or {}
                assert isinstance(visual_gate.get("gate_id"), str) and visual_gate["gate_id"].strip()


def test_liuyi_static_frame_reference_readmes_lock_exports_and_bboxes():
    prompts_root = Path("tests/fixtures/story_projects/六一那天/generation_prompts")

    for frame_dirname, expected in LIUYI_STATIC_FRAME_EXPORT_EXPECTATIONS.items():
        readme_text = (prompts_root / frame_dirname / "README.md").read_text(encoding="utf-8")

        assert "storyboard_reference_collage.png" in readme_text
        assert expected["bbox"] in readme_text
        assert expected["output"] in readme_text
        assert "2048x1152" in readme_text
        for reference_name in expected["references"]:
            assert reference_name in readme_text


def test_liuyi_child_identity_visual_gate_embeds_locked_reference():
    prompts_root = Path("tests/fixtures/story_projects/六一那天/generation_prompts")

    for frame_dirname, expected in LIUYI_CHILD_IDENTITY_FRAME_EXPECTATIONS.items():
        frame_dir = prompts_root / frame_dirname
        manifest = json.loads((frame_dir / "crop_composition_manifest.json").read_text(encoding="utf-8"))
        base_crop = Path(expected["base_crop"])
        edited_crop = Path(expected["edited_crop"])
        reference_path = Path("output/uploads/fixtures/liuyi_char_xiaoqi_child_full_body.png")
        patch_bbox = manifest["crops"][0]["identity_patch_bbox"]
        source_bbox = manifest["crops"][0]["reference_source_bbox"]

        assert manifest["crops"][0]["reference_images"] == [
            "output/uploads/fixtures/liuyi_char_xiaoqi_child_full_body.png"
        ]
        assert manifest["crops"][0]["prompt"].endswith("02a_child_xiaoqi_crop_edit_prompt.txt")
        assert base_crop.exists()
        assert edited_crop.exists()

        with Image.open(base_crop).convert("RGB") as base_image, Image.open(edited_crop).convert("RGB") as edited_image:
            assert base_image.size == edited_image.size == (expected["bbox"]["width"], expected["bbox"]["height"])
            diff = ImageChops.difference(base_image, edited_image)
            assert diff.getbbox() is not None
            assert sum(ImageStat.Stat(diff).mean) / 3.0 > 1.0

        with Image.open(reference_path).convert("RGBA") as reference_image:
            source_crop = reference_image.crop(
                (
                    source_bbox["x"],
                    source_bbox["y"],
                    source_bbox["x"] + source_bbox["width"],
                    source_bbox["y"] + source_bbox["height"],
                )
            )

        from scripts.compose_liuyi_child_identity_crop import _multiply_masks, _reference_alpha_mask, _soft_ellipse_mask

        mask = _multiply_masks(_reference_alpha_mask(source_crop), _soft_ellipse_mask(source_crop.size))
        resized_mask = mask.resize((patch_bbox["width"], patch_bbox["height"]), Image.Resampling.LANCZOS)
        resized_source = source_crop.resize((patch_bbox["width"], patch_bbox["height"]), Image.Resampling.LANCZOS)

        with Image.open(edited_crop).convert("RGB") as edited_image, Image.open(base_crop).convert("RGB") as base_image:
            patch = edited_image.crop(
                (
                    patch_bbox["x"],
                    patch_bbox["y"],
                    patch_bbox["x"] + patch_bbox["width"],
                    patch_bbox["y"] + patch_bbox["height"],
                )
            )
            base_patch = base_image.crop(
                (
                    patch_bbox["x"],
                    patch_bbox["y"],
                    patch_bbox["x"] + patch_bbox["width"],
                    patch_bbox["y"] + patch_bbox["height"],
                )
            )
            assert _masked_rgb_mean_delta(patch, resized_source, resized_mask) < 6.0
            assert _masked_rgb_mean_delta(patch, base_patch, resized_mask) > 1.0

        with Image.open(expected["output_image"]).convert("RGB") as full_output, Image.open(edited_crop).convert("RGB") as edited_image:
            full_crop = full_output.crop(
                (
                    expected["bbox"]["x"],
                    expected["bbox"]["y"],
                    expected["bbox"]["x"] + expected["bbox"]["width"],
                    expected["bbox"]["y"] + expected["bbox"]["height"],
                )
            )
            assert ImageChops.difference(full_crop, edited_image).getbbox() is None


def test_liuyi_manifest_references_match_fixture_and_uploaded_copies():
    fixture_dir = Path("tests/fixtures/story_projects/六一那天")
    manifest = json.loads((fixture_dir / "project_manifest.json").read_text(encoding="utf-8"))
    export_manifest = json.loads(
        (fixture_dir / "generation_prompts" / "static_frame_exports.json").read_text(encoding="utf-8")
    )
    entries = manifest["reference_images"] + manifest["reference_assets"]

    state_root = LIUYI_TEST_STATE_ROOT
    state_root.mkdir(parents=True, exist_ok=True)
    pipeline = _make_pipeline(state_root)
    script = pipeline.import_fixture_story_project("liuyi-that-day")

    uploads_dir = Path("output/uploads/fixtures")
    expected_upload_names = {}
    manifest_paths = [entry["path"] for entry in entries]
    manifest_asset_ids = {entry["asset_id"] for entry in manifest["reference_assets"]}
    export_asset_ids = {
        asset_id
        for frame_export in export_manifest["frame_exports"]
        for asset_id in frame_export.get("reference_assets", [])
    }
    used_asset_ids = {
        asset_id
        for frame in script.frames
        for asset_id in ([frame.scene_id] if frame.scene_id else [])
    }
    for frame in script.frames:
        used_asset_ids.update(frame.character_ids)
        used_asset_ids.update(frame.prop_ids)

    actual_reference_paths = {
        f"references/{path.name}"
        for path in (fixture_dir / "references").glob("*.png")
    }
    expected_reference_paths = set(manifest_paths)

    assert len(manifest_paths) == len(expected_reference_paths)
    assert actual_reference_paths == expected_reference_paths
    assert used_asset_ids == manifest_asset_ids
    assert export_asset_ids == manifest_asset_ids

    for entry in entries:
        source_path = fixture_dir / entry["path"]
        assert source_path.exists(), entry["path"]

        if entry.get("role") == "storyboard_reference_collage":
            upload_name = "liuyi-that-day-storyboard-reference.png"
        else:
            upload_name = source_path.name
        expected_upload_names[entry["path"]] = upload_name

        uploaded_path = uploads_dir / upload_name
        assert uploaded_path.exists(), upload_name
        assert uploaded_path.read_bytes() == source_path.read_bytes()

    assert len(set(expected_upload_names.values())) == len(expected_upload_names)
    actual_project_upload_names = {
        path.name
        for path in uploads_dir.glob("liuyi*.png")
    }
    assert actual_project_upload_names == set(expected_upload_names.values())


def test_compose_frame_crops_backend_entry_updates_rendered_variant():
    pipeline = _make_pipeline(None)
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


def test_import_liuyi_fixture_project_builds_openable_storyboard_project():
    pipeline = _make_pipeline(None)

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


def test_import_liuyi_fixture_project_maps_final_storyboard_assets_per_frame():
    pipeline = _make_pipeline(None)

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


def test_import_liuyi_fixture_project_reuses_existing_project():
    pipeline = _make_pipeline(None)

    first = pipeline.import_fixture_story_project("liuyi-that-day")
    second = pipeline.import_fixture_story_project("liuyi-that-day")

    assert second.id == first.id
    assert len(pipeline.scripts) == 1


def test_reimport_liuyi_fixture_project_refreshes_storyboard_asset_mapping():
    pipeline = _make_pipeline(None)

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


def test_list_fixture_story_projects_discovers_liuyi_template():
    pipeline = _make_pipeline(None)

    fixtures = pipeline.list_fixture_story_projects()

    liuyi = next(item for item in fixtures if item["slug"] == "liuyi-that-day")
    assert liuyi["name"] == "六一那天"
    assert liuyi["project_type"] == "seedance_storyboard_test"
    assert liuyi["parser"] == "seedance_storyboard_markdown"
    assert liuyi["frame_count"] == 18
    assert liuyi["source_count"] == 1
    assert liuyi["reference_count"] == 27
    assert liuyi["model_settings"]["openai_image_model"] == "gpt-image2"
    assert liuyi["is_imported"] is False
    assert liuyi["project_id"] is None


def test_list_fixture_story_projects_marks_imported_project():
    pipeline = _make_pipeline(None)
    script = pipeline.import_fixture_story_project("liuyi-that-day")

    fixtures = pipeline.list_fixture_story_projects()

    liuyi = next(item for item in fixtures if item["slug"] == "liuyi-that-day")
    assert liuyi["is_imported"] is True
    assert liuyi["project_id"] == script.id


def test_locked_fixture_asset_with_master_reference_requires_unlock_before_regeneration():
    pipeline = _make_pipeline(None)
    script = pipeline.import_fixture_story_project("liuyi-that-day")

    with pytest.raises(ValueError, match="locked"):
        pipeline.generate_asset(script.id, "liuyi_prop_white_bear", "prop")


def test_import_fixture_endpoint_returns_normal_project_payload(monkeypatch):
    pipeline = _make_pipeline(None)
    monkeypatch.setattr(api_module, "pipeline", pipeline)
    client = TestClient(api_module.app)

    response = client.post("/projects/fixtures/liuyi-that-day/import")

    assert response.status_code == 200
    payload = response.json()
    assert payload["title"] == "六一那天"
    assert len(payload["frames"]) == 18


def test_compose_frame_crops_endpoint_forwards_request_payload(monkeypatch):
    pipeline = _make_pipeline(None)
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


def test_import_liuyi_fixture_project_binds_locked_master_references():
    pipeline = _make_pipeline(None)

    script = pipeline.import_fixture_story_project("liuyi-that-day")

    xiaoqi = next(item for item in script.characters if item.id == "liuyi_char_xiaoqi_child")
    adult_xiaoqi = next(item for item in script.characters if item.id == "liuyi_char_xiaoqi_adult")
    mother = next(item for item in script.characters if item.id == "liuyi_char_mother")
    father = next(item for item in script.characters if item.id == "liuyi_char_father")
    boy = next(item for item in script.characters if item.id == "liuyi_char_boy")
    boy_father = next(item for item in script.characters if item.id == "liuyi_char_boy_father")
    playground = next(item for item in script.scenes if item.id == "liuyi_scene_school_playground")
    school_gate = next(item for item in script.scenes if item.id == "liuyi_scene_school_gate")
    hospital = next(item for item in script.scenes if item.id == "liuyi_scene_hospital_room")
    ward_2026 = next(item for item in script.scenes if item.id == "liuyi_scene_2026_ward")
    corridor = next(item for item in script.scenes if item.id == "liuyi_scene_hospital_corridor")
    funeral_hall = next(item for item in script.scenes if item.id == "liuyi_scene_funeral_hall")
    home_desk = next(item for item in script.scenes if item.id == "liuyi_scene_home_desk")
    exam_admission = next(item for item in script.scenes if item.id == "liuyi_scene_exam_admission")
    medical_school = next(item for item in script.scenes if item.id == "liuyi_scene_medical_school")
    doctor_office = next(item for item in script.scenes if item.id == "liuyi_scene_doctor_office")
    bear = next(item for item in script.props if item.id == "liuyi_prop_white_bear")
    paper_bag = next(item for item in script.props if item.id == "liuyi_prop_paper_bag")
    child_drawing = next(item for item in script.props if item.id == "liuyi_prop_child_drawing")
    balloons = next(item for item in script.props if item.id == "liuyi_prop_childrens_day_balloons")
    medical_textbooks = next(item for item in script.props if item.id == "liuyi_prop_medical_textbooks")
    father_memorial_portrait = next(
        item for item in script.props if item.id == "liuyi_prop_father_memorial_portrait"
    )
    family_photo = next(item for item in script.props if item.id == "liuyi_prop_family_photo")
    notebook_pencil = next(item for item in script.props if item.id == "liuyi_prop_notebook_pencil")
    admission_notice = next(item for item in script.props if item.id == "liuyi_prop_admission_notice")
    young_xiaoqi = next(item for item in script.characters if item.id == "liuyi_char_xiaoqi_young")

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
    assert young_xiaoqi.full_body_asset.selected_id == "liuyi_char_xiaoqi_young_full_body_fixture"
    assert young_xiaoqi.locked is True
    assert xiaoqi.full_body_asset.variants[0].is_uploaded_source is True
    assert young_xiaoqi.full_body_asset.variants[0].is_uploaded_source is True
    assert playground.locked is True
    assert school_gate.locked is True
    assert hospital.locked is True
    assert ward_2026.locked is True
    assert corridor.locked is True
    assert funeral_hall.locked is True
    assert home_desk.locked is True
    assert exam_admission.locked is True
    assert medical_school.locked is True
    assert doctor_office.locked is True
    assert bear.locked is True
    assert paper_bag.locked is True
    assert child_drawing.locked is True
    assert balloons.locked is True
    assert medical_textbooks.locked is True
    assert father_memorial_portrait.locked is True
    assert family_photo.locked is True
    assert notebook_pencil.locked is True
    assert admission_notice.locked is True
    assert playground.image_asset.selected_id == "liuyi_scene_school_playground_image_fixture"
    assert school_gate.image_asset.selected_id == "liuyi_scene_school_gate_image_fixture"
    assert hospital.image_asset.selected_id == "liuyi_scene_hospital_room_image_fixture"
    assert ward_2026.image_asset.selected_id == "liuyi_scene_2026_ward_image_fixture"
    assert corridor.image_asset.selected_id == "liuyi_scene_hospital_corridor_image_fixture"
    assert funeral_hall.image_asset.selected_id == "liuyi_scene_funeral_hall_image_fixture"
    assert home_desk.image_asset.selected_id == "liuyi_scene_home_desk_image_fixture"
    assert exam_admission.image_asset.selected_id == "liuyi_scene_exam_admission_image_fixture"
    assert medical_school.image_asset.selected_id == "liuyi_scene_medical_school_image_fixture"
    assert doctor_office.image_asset.selected_id == "liuyi_scene_doctor_office_image_fixture"
    assert bear.image_asset.selected_id == "liuyi_prop_white_bear_image_fixture"
    assert paper_bag.image_asset.selected_id == "liuyi_prop_paper_bag_image_fixture"
    assert child_drawing.image_asset.selected_id == "liuyi_prop_child_drawing_image_fixture"
    assert balloons.image_asset.selected_id == "liuyi_prop_childrens_day_balloons_image_fixture"
    assert medical_textbooks.image_asset.selected_id == "liuyi_prop_medical_textbooks_image_fixture"
    assert father_memorial_portrait.image_asset.selected_id == "liuyi_prop_father_memorial_portrait_image_fixture"
    assert family_photo.image_asset.selected_id == "liuyi_prop_family_photo_image_fixture"
    assert notebook_pencil.image_asset.selected_id == "liuyi_prop_notebook_pencil_image_fixture"
    assert admission_notice.image_asset.selected_id == "liuyi_prop_admission_notice_image_fixture"
    assert playground.image_asset.variants[0].is_uploaded_source is True
    assert bear.image_asset.variants[0].is_uploaded_source is True


def test_storyboard_render_includes_frame_locked_asset_references():
    pipeline = _make_pipeline(None)
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
    assert any(
        path.endswith("output/uploads/fixtures/liuyi_prop_paper_bag.png")
        for path in normalized_paths
    )


def test_storyboard_render_uses_only_the_current_frame_asset_references():
    pipeline = _make_pipeline(None)
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
    assert any(
        path.endswith("output/uploads/fixtures/liuyi_prop_child_drawing.png")
        for path in normalized_paths
    )
    assert not any(
        path.endswith("output/uploads/fixtures/liuyi_char_father_full_body.png")
        for path in normalized_paths
    )


def test_storyboard_render_uses_safe_staged_strategy_for_frame_15():
    pipeline = _make_pipeline(None)
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


def test_storyboard_render_includes_frame_18_generated_reference_assets():
    pipeline = _make_pipeline(None)
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


def test_list_fixture_endpoint_returns_template_library(monkeypatch):
    pipeline = _make_pipeline(None)
    monkeypatch.setattr(api_module, "pipeline", pipeline)
    client = TestClient(api_module.app)

    response = client.get("/projects/fixtures")

    assert response.status_code == 200
    payload = response.json()
    liuyi = next(item for item in payload if item["slug"] == "liuyi-that-day")
    assert liuyi["name"] == "六一那天"
    assert liuyi["frame_count"] == 18
    assert liuyi["is_imported"] is False
