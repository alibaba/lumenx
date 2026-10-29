import pytest
from pydantic import ValidationError

from src.apps.atelier.product_skills import (
    AtelierProductSkillSpec,
    build_product_skill_registry,
    list_product_skill_catalog,
    resolve_product_skill_snapshot,
)


def test_product_skill_catalog_is_sanitized_and_snapshot_hash_is_self_validating():
    catalog = list_product_skill_catalog()
    enabled = next(item for item in catalog if item["enabled"])
    assert enabled["name"] == "story-scout"
    assert enabled["content_hash"] == resolve_product_skill_snapshot("story-scout").content_hash
    forbidden = {"path", "source", "credential_ref", "approval", "provider_handle", "request_fingerprint", "prompt_invocation"}
    assert forbidden.isdisjoint(enabled)

    snapshot = resolve_product_skill_snapshot("story-scout")
    with pytest.raises(ValidationError, match="content_hash"):
        type(snapshot)(**{**snapshot.model_dump(mode="json"), "expected_tools": snapshot.expected_tools, "title": "Drifted"})


def test_product_skill_registry_rejects_duplicate_and_unsupported_tools():
    spec = AtelierProductSkillSpec(
        schema_version=1,
        name="test-skill",
        version="1.0.0",
        title="Test",
        description="Test recipe",
        category="story",
        prompt_template="Inspect the story.",
        expected_tools=("canvas.readProject",),
        default_iteration_cap=3,
    )
    with pytest.raises(ValueError, match="duplicate"):
        build_product_skill_registry((spec, spec))
    with pytest.raises(ValueError, match="unsupported"):
        build_product_skill_registry((spec.model_copy(update={"name": "bad-tool", "expected_tools": ("shell.exec",)}),))
