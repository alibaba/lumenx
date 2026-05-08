import json
from pathlib import Path
import subprocess
import sys

from PIL import Image

from src.apps.comic_gen.models import CodexImagegenPolicy
from src.utils.codex_imagegen_handoff import (
    recommend_codex_imagegen_mode_from_reference_items,
    select_codex_handoff_mode,
)
from src.utils.image_payload_budget import (
    estimate_base64_payload_bytes,
    prepare_image_references_for_payload,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


def _liuyi_v2_frame_spec(frame_dir: str) -> Path:
    return (
        REPO_ROOT
        / "tests"
        / "fixtures"
        / "story_projects"
        / "六一那天_v2"
        / "generation_prompts"
        / frame_dir
        / "codex_imagegen_handoff_manifest.json"
    )


def _load_liuyi_v2_frame_spec(frame_dir: str) -> dict:
    return json.loads(_liuyi_v2_frame_spec(frame_dir).read_text(encoding="utf-8"))


def test_prepare_image_references_for_payload_fits_aggregate_budget(tmp_path):
    references = []
    for index in range(6):
        path = tmp_path / f"reference-{index}.png"
        Image.effect_noise((900, 900), 96).convert("RGB").save(path, format="PNG")
        references.append(path)

    source_total = sum(path.stat().st_size for path in references)
    output_dir = tmp_path / "prepared"

    manifest = prepare_image_references_for_payload(
        references,
        output_dir,
        max_total_bytes=1_200_000,
        max_side=900,
        min_side=480,
        jpeg_quality=82,
        min_jpeg_quality=45,
    )

    assert manifest["fits_budget"] is True
    assert manifest["reference_count"] == 6
    assert manifest["total_source_bytes"] == source_total
    assert manifest["total_prepared_bytes"] <= 1_200_000
    assert manifest["estimated_base64_bytes"] == estimate_base64_payload_bytes(
        manifest["total_prepared_bytes"]
    )
    assert manifest["total_prepared_bytes"] < source_total
    for entry in manifest["references"]:
        prepared_path = Path(entry["prepared_path"])
        assert prepared_path.exists()
        assert prepared_path.suffix == ".jpg"
        assert entry["content_type"] == "image/jpeg"


def test_prepare_codex_imagegen_refs_builds_liuyi_frame17_handoff(tmp_path):
    spec_path = _liuyi_v2_frame_spec("frame_17")
    output_dir = tmp_path / "codex-handoff"

    result = subprocess.run(
        [
            sys.executable,
            "scripts/prepare_codex_imagegen_refs.py",
            "--frame-spec",
            str(spec_path),
            "--output-dir",
            str(output_dir),
        ],
        cwd=REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=120,
    )

    assert result.returncode == 0, result.stderr

    stdout_manifest = json.loads(result.stdout)
    manifest_path = output_dir / "codex_safe_reference_manifest.json"
    policy_path = output_dir / "handoff_policy.json"
    prompt_path = output_dir / "codex_imagegen_prompt.md"
    assert manifest_path.exists()
    assert policy_path.exists()
    assert prompt_path.exists()

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    prompt_text = prompt_path.read_text(encoding="utf-8")

    assert manifest["reference_count"] == 6
    assert stdout_manifest["codex_imagegen_recommendation"]["mode"] == "two_stage_high_consistency"
    assert stdout_manifest["codex_imagegen_handoff_plan"]["mode"] == "safe_refs_only"
    assert stdout_manifest["codex_imagegen_handoff_plan"]["selection_source"] == "policy_mode"
    assert manifest["total_source_bytes"] > manifest["max_total_bytes"]
    assert 900_000 <= manifest["total_prepared_bytes"] <= 1_048_576
    assert manifest["max_side_used"] >= 1600
    assert manifest["jpeg_quality_used"] >= 88
    assert manifest["request_policy"]["use_prepared_paths_only"] is True
    assert manifest["source_paths_redacted"] is True
    assert all("source_path" not in entry for entry in manifest["references"])
    assert policy["never_attach_raw_references"] is True
    assert policy["use_prepared_paths_only"] is True
    assert policy["raw_source_paths_redacted"] is True

    for entry in manifest["references"]:
        prepared_path = Path(entry["prepared_path"])
        assert prepared_path.exists()
        assert prepared_path.name in prompt_text
        assert entry["source_name"] not in prompt_text


def test_prepare_codex_imagegen_refs_rejects_budget_above_codex_handoff_cap(tmp_path):
    spec_path = _liuyi_v2_frame_spec("frame_17")

    result = subprocess.run(
        [
            sys.executable,
            "scripts/prepare_codex_imagegen_refs.py",
            "--frame-spec",
            str(spec_path),
            "--output-dir",
            str(tmp_path / "codex-handoff"),
            "--max-total-bytes",
            "1048577",
        ],
        cwd=REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=120,
    )

    assert result.returncode != 0
    assert "cannot exceed" in result.stderr


def test_codex_imagegen_policy_model_clamps_hard_budget_and_modes():
    policy = CodexImagegenPolicy(
        enabled="true",
        mode="mystery",
        max_total_bytes=9_999_999,
        never_attach_raw_references="false",
    )

    assert policy.enabled is True
    assert policy.mode == "safe_refs_only"
    assert policy.max_total_bytes == 1_048_576
    assert policy.never_attach_raw_references is False


def test_codex_imagegen_policy_accepts_two_stage_mode_aliases():
    policy = CodexImagegenPolicy(mode="two_stage", max_total_bytes=900_000)

    assert policy.mode == "two_stage_high_consistency"
    assert policy.max_total_bytes == 900_000


def test_codex_handoff_recommendation_and_selection_are_policy_driven():
    frame_17_spec = _load_liuyi_v2_frame_spec("frame_17")
    frame_18_spec = _load_liuyi_v2_frame_spec("frame_18")

    frame_17_recommendation = recommend_codex_imagegen_mode_from_reference_items(
        frame_17_spec["references"],
        frame_17_spec["policy"],
    )
    assert frame_17_recommendation["mode"] == "two_stage_high_consistency"
    assert frame_17_recommendation["metrics"]["ready_count"] == 6

    frame_17_manual_selection = select_codex_handoff_mode(
        frame_17_spec["policy"],
        recommendation=frame_17_recommendation,
    )
    assert frame_17_manual_selection["mode"] == "safe_refs_only"
    assert frame_17_manual_selection["selection_source"] == "policy_mode"

    frame_17_auto_selection = select_codex_handoff_mode(
        {
            **frame_17_spec["policy"],
            "recommendation": {
                "auto_apply": True,
            },
        },
        recommendation=frame_17_recommendation,
    )
    assert frame_17_auto_selection["mode"] == "two_stage_high_consistency"
    assert frame_17_auto_selection["selection_source"] == "recommendation_auto_apply"

    frame_18_recommendation = recommend_codex_imagegen_mode_from_reference_items(
        frame_18_spec["references"],
        frame_18_spec["policy"],
    )
    assert frame_18_recommendation["mode"] == "two_stage_high_consistency"
    assert frame_18_recommendation["metrics"]["ready_count"] == 8
    assert frame_18_recommendation["metrics"]["scene_count"] == 3


def test_prepare_codex_imagegen_refs_builds_two_stage_extreme_fixtures(tmp_path):
    fixtures = {
        "frame_03": (2, 1),
        "frame_18": (5, 3),
    }
    for frame_dir, expected_counts in fixtures.items():
        output_dir = tmp_path / frame_dir
        result = subprocess.run(
            [
                sys.executable,
                "scripts/prepare_codex_imagegen_refs.py",
                "--frame-spec",
                str(_liuyi_v2_frame_spec(frame_dir)),
                "--output-dir",
                str(output_dir),
            ],
            cwd=REPO_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=120,
        )

        assert result.returncode == 0, result.stderr

        pack_path = output_dir / "codex_two_stage_handoff_manifest.json"
        assert pack_path.exists()
        pack = json.loads(pack_path.read_text(encoding="utf-8"))
        assert pack["mode"] == "two_stage_high_consistency"
        assert pack["raw_source_paths_redacted"] is True
        assert len(pack["stages"]) == 2

        stage_1, stage_2 = pack["stages"]
        assert stage_1["stage_id"] == "stage_1_identity"
        assert stage_2["stage_id"] == "stage_2_scene_light"
        assert stage_1["reference_count"] == expected_counts[0]
        assert stage_2["reference_count"] == expected_counts[1]
        assert stage_2["requires_stage_result"] is True
        assert stage_2["stage_result_expected_name"] == "stage_1_identity_result.png"

        for stage in pack["stages"]:
            manifest = json.loads(
                Path(stage["safe_reference_manifest"]).read_text(encoding="utf-8")
            )
            assert manifest["total_prepared_bytes"] <= 1_048_576
            assert manifest["source_paths_redacted"] is True
            assert all("source_path" not in entry for entry in manifest["references"])

        stage_2_prompt = Path(stage_2["prompt_file"]).read_text(encoding="utf-8")
        assert "stage_1_identity_result.png" in stage_2_prompt
        assert "references/characters" not in stage_2_prompt
        assert "references/scenes" not in stage_2_prompt
