from src.apps.atelier.product_skills import resolve_product_skill_snapshot
from src.apps.atelier.skill_resolution import (
    SkillBinding,
    SkillScope,
    bind_system_skill,
    resolve_effective_skills,
)


def test_more_specific_binding_replaces_system_baseline_without_changing_snapshot():
    system = resolve_product_skill_snapshot("story-scout")
    user = system
    resolved = resolve_effective_skills(
        [
            bind_system_skill(system),
            SkillBinding(name="story-scout", scope=SkillScope.USER, owner_id="u1", snapshot=user),
        ]
    )
    assert resolved["story-scout"].scope is SkillScope.USER
    assert resolved["story-scout"].owner_id == "u1"
    assert resolved["story-scout"].snapshot.title == "Story Scout"
    assert system.title == "Story Scout"


def test_skill_tools_are_intersected_with_current_entry_capabilities():
    snapshot = resolve_product_skill_snapshot("story-scout")
    resolved = resolve_effective_skills(
        [bind_system_skill(snapshot)], available_tools={"canvas.readProject", "shell.exec"}
    )
    assert resolved["story-scout"].allowed_tools == ("canvas.readProject",)


def test_same_scope_different_owner_is_rejected():
    snapshot = resolve_product_skill_snapshot("story-scout")
    bindings = [
        SkillBinding(name="story-scout", scope=SkillScope.USER, owner_id="u1", snapshot=snapshot),
        SkillBinding(name="story-scout", scope=SkillScope.USER, owner_id="u2", snapshot=snapshot),
    ]
    try:
        resolve_effective_skills(bindings)
    except ValueError as exc:
        assert str(exc) == "skill_binding_scope_conflict"
    else:
        raise AssertionError("same-scope bindings must conflict")
