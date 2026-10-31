import hashlib
import json

import pytest

from src.apps.atelier.product_skills import resolve_product_skill_snapshot
from src.apps.atelier.skill_runtime import (
    bind_skill_to_video_request,
    convert_manifest_to_snapshot,
    validate_skill_manifest_sandbox,
)


def platform_manifest():
    payload = {
        "name": "story-scout", "version": "1.0.0", "title": "Story Scout",
        "description": "Read the canvas.", "category": "story",
        "prompt_template": "Read the canvas and propose one next move.",
        "expected_tools": ["canvas.readProject"],
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {**payload, "content_hash": hashlib.sha256(canonical.encode()).hexdigest()}


def test_platform_manifest_converts_to_immutable_atelier_snapshot():
    snapshot = convert_manifest_to_snapshot(platform_manifest())
    assert snapshot.name == "story-scout"
    assert snapshot.expected_tools == ("canvas.readProject",)
    assert snapshot.content_hash != platform_manifest()["content_hash"]


def test_sandbox_rejects_scripts_and_unsupported_tools():
    manifest = platform_manifest() | {"script": "python unsafe.py", "expected_tools": ["shell.exec"]}
    report = validate_skill_manifest_sandbox(manifest)
    assert not report.allowed
    assert "script" in report.rejected_fields
    assert report.unsupported_tools == ("shell.exec",)
    with pytest.raises(ValueError, match="sandbox_rejected"):
        convert_manifest_to_snapshot(manifest)


def test_video_request_binding_changes_when_skill_snapshot_changes():
    snapshot = resolve_product_skill_snapshot("story-scout")
    request = {"client_request_id": "request-1", "model_id": "wan/wan2.7-video#i2v", "prompt": "sunrise"}
    binding = bind_skill_to_video_request(snapshot, request)
    assert binding.skill_name == snapshot.name
    assert binding.skill_content_hash == snapshot.content_hash
    assert len(binding.request_fingerprint) == 64
