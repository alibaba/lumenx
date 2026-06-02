"""v1.4 BATCH 3 — Skills layer tests.

Four cases:
  1. test_registry_discovers_all_8_builtins
  2. test_registry_precedence_overrides_by_name
  3. test_agent_update_plan_creates_then_updates_plan_node
  4. test_skill_prompt_template_prepends_to_system_prefix
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.apps.comic_gen import atelier_skill_registry as reg
from src.apps.comic_gen.atelier_agent import (
    AtelierAgentHarness,
    _build_atelier_llm_prefix,
    _execute_update_plan,
    build_default_atelier_tool_registry,
)
from src.apps.comic_gen.pipeline import ComicGenPipeline


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    """Fresh pipeline with tmp persistence + isolated skill stages.

    The personal/managed/workspace/extra dirs are pinned to tmp_path
    subdirs that don't exist by default so tests don't see ~/.atelier
    / repo workspace skills.
    """
    monkeypatch.setenv(reg.ATELIER_SKILL_PERSONAL_DIR_ENV, str(tmp_path / "personal_skills"))
    monkeypatch.setenv(reg.ATELIER_SKILL_MANAGED_DIR_ENV, str(tmp_path / "managed_skills"))
    monkeypatch.setenv(reg.ATELIER_SKILL_WORKSPACE_DIR_ENV, str(tmp_path / "workspace_skills"))
    monkeypatch.delenv(reg.ATELIER_SKILL_EXTRA_DIRS_ENV, raising=False)
    monkeypatch.setenv(reg.ATELIER_SKILL_REGISTRY_CACHE_ENV, "0")
    reg.reset_registry_cache()

    with patch("src.apps.comic_gen.pipeline.ScriptProcessor"), \
         patch("src.apps.comic_gen.pipeline.AssetGenerator"), \
         patch("src.apps.comic_gen.pipeline.StoryboardGenerator"), \
         patch("src.apps.comic_gen.pipeline.VideoGenerator"), \
         patch("src.apps.comic_gen.pipeline.AudioGenerator"), \
         patch("src.apps.comic_gen.pipeline.ExportManager"):
        p = ComicGenPipeline()
    p.data_file = str(tmp_path / "projects.json")
    p.series_data_file = str(tmp_path / "series.json")
    p.atelier_data_file = str(tmp_path / "atelier_projects.json")
    p.scripts = {}
    p.series_store = {}
    p.atelier_projects = {}
    return p


# ---------------------------------------------------------------------------
# 1. registry discovers all 8 builtins
# ---------------------------------------------------------------------------


def test_registry_discovers_all_8_builtins():
    reg.reset_registry_cache()
    specs = reg.discover_skills(use_cache=False)
    names = [s.name for s in specs]

    expected = {
        "compose-short-film",
        "storyboard-script",
        "generate-hero-shot",
        "animate-still",
        "stylize-footage",
        "replace-voice",
        "mix-soundtrack",
        "try-workflow",
    }
    assert expected.issubset(set(names)), f"missing: {expected - set(names)}"
    # The 8 bundled skills must all parse with non-empty description /
    # prompt_template. Every skill must come from the bundled stage when
    # nothing else is on the precedence chain.
    bundled_names = {s.name for s in specs if s.source_stage == "bundled"}
    assert expected.issubset(bundled_names)
    by_name = {s.name: s for s in specs}
    for name in expected:
        spec = by_name[name]
        assert spec.description.strip(), f"{name} description empty"
        assert spec.prompt_template.strip(), f"{name} prompt_template empty"
        # `default_iteration_cap` is optional but every bundled skill we
        # ship sets one — assert it's a valid int when present.
        if spec.default_iteration_cap is not None:
            assert 1 <= spec.default_iteration_cap <= 10


# ---------------------------------------------------------------------------
# 2. precedence overrides by name (personal > bundled)
# ---------------------------------------------------------------------------


def test_registry_precedence_overrides_by_name(tmp_path, monkeypatch):
    personal_root = tmp_path / "personal_skills"
    skill_dir = personal_root / "compose-short-film"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        """---
name: compose-short-film
description: Personal override description for compose-short-film.
prompt_template: |
  Personal override prompt for the user's local library.
default_iteration_cap: 4
---

Personal-stage body text used in the override test.
""",
        encoding="utf-8",
    )
    monkeypatch.setenv(reg.ATELIER_SKILL_PERSONAL_DIR_ENV, str(personal_root))
    monkeypatch.setenv(reg.ATELIER_SKILL_MANAGED_DIR_ENV, str(tmp_path / "managed_skills"))
    monkeypatch.setenv(reg.ATELIER_SKILL_WORKSPACE_DIR_ENV, str(tmp_path / "workspace_skills"))
    monkeypatch.delenv(reg.ATELIER_SKILL_EXTRA_DIRS_ENV, raising=False)
    monkeypatch.setenv(reg.ATELIER_SKILL_REGISTRY_CACHE_ENV, "0")
    reg.reset_registry_cache()

    spec = reg.get_skill("compose-short-film")
    assert spec is not None
    assert spec.source_stage == "personal", spec.source_stage
    assert spec.description == "Personal override description for compose-short-film."
    assert "Personal override prompt" in spec.prompt_template
    assert "Personal-stage body" in spec.body
    assert spec.default_iteration_cap == 4

    # Without the override, bundled wins.
    monkeypatch.delenv(reg.ATELIER_SKILL_PERSONAL_DIR_ENV, raising=False)
    monkeypatch.setenv(reg.ATELIER_SKILL_PERSONAL_DIR_ENV, str(tmp_path / "no_personal"))
    reg.reset_registry_cache()
    spec_after = reg.get_skill("compose-short-film")
    assert spec_after is not None
    assert spec_after.source_stage == "bundled"
    assert spec_after.description != "Personal override description for compose-short-film."


# ---------------------------------------------------------------------------
# 3. agent.updatePlan executor: create then update
# ---------------------------------------------------------------------------


def test_agent_update_plan_creates_then_updates_plan_node(pipeline):
    project = pipeline.create_atelier_project("Plan board")
    # First call — no node_id, must create.
    result = _execute_update_plan(
        project.id,
        {
            "title": "First plan",
            "steps": [
                {"id": "s1", "title": "Setup", "status": "pending"},
                {"id": "s2", "title": "Turn", "status": "pending"},
                {"id": "s3", "title": "Payoff", "status": "pending"},
            ],
        },
        pipeline,
    )
    assert result["created"] is True
    assert result["step_count"] == 3
    node_payload = result["node"]
    assert node_payload["type"] == "plan"
    assert node_payload["title"] == "First plan"
    project_state = pipeline.get_atelier_project(project.id)
    assert len(project_state.nodes) == 1
    plan_node = project_state.nodes[0]
    assert plan_node.type == "plan"
    assert plan_node.created_by == "agent"
    assert plan_node.data["steps"] == [
        {"id": "s1", "title": "Setup", "status": "pending"},
        {"id": "s2", "title": "Turn", "status": "pending"},
        {"id": "s3", "title": "Payoff", "status": "pending"},
    ]
    bullets = plan_node.data["bullets"]
    assert len(bullets) == 3
    # Pending status renders the open glyph.
    assert all(b.startswith("○ ") for b in bullets)

    # Second call — update existing.
    result2 = _execute_update_plan(
        project.id,
        {
            "node_id": plan_node.id,
            "title": "Updated plan",
            "steps": [
                {"id": "s1", "title": "Setup", "status": "completed"},
                {"id": "s2", "title": "Turn", "status": "in_progress", "notes": "shooting now"},
                {"id": "s3", "title": "Payoff", "status": "pending"},
                {"id": "s4", "title": "Polish", "status": "pending"},
            ],
        },
        pipeline,
    )
    assert result2["created"] is False
    assert result2["step_count"] == 4
    project_state = pipeline.get_atelier_project(project.id)
    # Still ONE node — update in place.
    assert len(project_state.nodes) == 1
    updated_plan = project_state.nodes[0]
    assert updated_plan.id == plan_node.id
    assert updated_plan.title == "Updated plan"
    assert len(updated_plan.data["steps"]) == 4
    assert updated_plan.data["steps"][1]["status"] == "in_progress"
    assert updated_plan.data["steps"][1]["notes"] == "shooting now"
    bullets2 = updated_plan.data["bullets"]
    # Status glyphs reflect the new statuses.
    assert bullets2[0].startswith("● ")  # completed
    assert bullets2[1].startswith("◐ ")  # in_progress
    assert "shooting now" in bullets2[1]
    assert bullets2[2].startswith("○ ")  # pending

    # Validation: oversized list rejected.
    with pytest.raises(ValueError, match="exceed 12"):
        _execute_update_plan(
            project.id,
            {"steps": [{"id": f"s{i}", "title": f"step{i}", "status": "pending"} for i in range(13)]},
            pipeline,
        )

    # Validation: duplicate step ids rejected.
    with pytest.raises(ValueError, match="duplicate step id"):
        _execute_update_plan(
            project.id,
            {"steps": [
                {"id": "x", "title": "a", "status": "pending"},
                {"id": "x", "title": "b", "status": "pending"},
            ]},
            pipeline,
        )

    # Validation: targeting a non-plan node fails cleanly.
    video = pipeline.create_atelier_node(project.id, {"type": "video", "title": "Shot"})
    with pytest.raises(ValueError, match="not a plan node"):
        _execute_update_plan(
            project.id,
            {"node_id": video.id, "steps": [{"id": "s", "title": "x", "status": "pending"}]},
            pipeline,
        )

    # And: agent.updatePlan is registered in the default registry.
    registry = build_default_atelier_tool_registry()
    spec_pair = registry.get("agent.updatePlan")
    assert spec_pair is not None
    spec, _ = spec_pair
    assert spec.required_permission == "canvas_write"
    assert spec.mutates_canvas is True
    assert spec.requires_approval is False


# ---------------------------------------------------------------------------
# 4. skill prompt_template prepends to system prefix
# ---------------------------------------------------------------------------


def test_skill_prompt_template_prepends_to_system_prefix(pipeline):
    project = pipeline.create_atelier_project("Prefix board")

    package = pipeline.build_atelier_agent_planner_package(
        project.id,
        user_message="Compose a short film about rain",
        skill_name="compose-short-film",
    )
    assert package.skill_name == "compose-short-film"
    # build_planner_package resolved the SkillSpec onto the package.
    assert isinstance(package.skill_spec, dict)
    assert package.skill_spec["name"] == "compose-short-film"

    prefix = _build_atelier_llm_prefix(package)
    # Skill splice landed AFTER directive, BEFORE OUTPUT FORMAT.
    assert "You are the Atelier canvas agent" in prefix
    assert "Active skill: compose-short-film" in prefix
    assert "Skill brief:" in prefix
    # The skill's prompt_template content is embedded.
    assert "agent.updatePlan" in prefix or "Compose a complete short film" in prefix
    # Skill body (after frontmatter) also in.
    assert "director skill" in prefix or "Recommended flow" in prefix

    # Order invariant: directive < skill block < OUTPUT FORMAT < Registered tools < Project policy.
    idx_directive = prefix.find("You are the Atelier canvas agent")
    idx_active = prefix.find("Active skill: compose-short-film")
    idx_brief = prefix.find("Skill brief:")
    idx_output = prefix.find("OUTPUT FORMAT")
    idx_tools = prefix.find("Registered tools:")
    idx_policy = prefix.find("Project policy:")
    assert -1 < idx_directive < idx_active < idx_brief < idx_output < idx_tools < idx_policy

    # No skill_name supplied → no skill block emitted.
    package_blank = pipeline.build_atelier_agent_planner_package(
        project.id,
        user_message="Just a regular turn",
    )
    assert package_blank.skill_spec is None
    prefix_blank = _build_atelier_llm_prefix(package_blank)
    assert "Active skill:" not in prefix_blank
    assert "Skill brief:" not in prefix_blank

    # Unknown skill name should not crash, but should leave skill_spec=None.
    package_unknown = pipeline.build_atelier_agent_planner_package(
        project.id,
        user_message="ghost skill",
        skill_name="not-a-real-skill",
    )
    assert package_unknown.skill_spec is None
    assert package_unknown.skill_name == "not-a-real-skill"
    prefix_unknown = _build_atelier_llm_prefix(package_unknown)
    assert "Active skill:" not in prefix_unknown
