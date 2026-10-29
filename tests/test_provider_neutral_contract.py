"""Neutral provider execution contracts shared by Studio and Atelier."""

import importlib
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from src.models.provider_tasks import (
    ProviderArtifact,
    ProviderHandle,
    ProviderObservation,
    ProviderScope,
    ProviderTaskStatus,
    QueryCapability,
)


HASH_A = "a" * 64
HASH_B = "b" * 64


def _scope() -> ProviderScope:
    return ProviderScope(
        provider="dashscope",
        backend="dashscope",
        gateway="dashscope_workspace",
        base_url="https://dashscope.aliyuncs.com",
        region="cn-beijing",
    )


def _record(**overrides):
    from src.models.provider_execution import ProviderExecutionRecord

    data = {
        "schema_version": 1,
        "attempt_id": "attempt-1",
        "phase": "submitted",
        "provider_model_id": "wan2.7-i2v",
        "intent": {
            "rules_version": 1,
            "model_id": "wan2.7-i2v",
            "catalog_mode": "i2v",
            "mode": "i2v",
            "prompt": "make a quiet cinematic shot",
            "params": {},
            "sources": {},
            "request_fields": ["prompt"],
            "preset_id": None,
            "preset_version": None,
        },
        "media": [],
        "scope": _scope().to_dict(),
        "credential_ref": "credential-ref",
        "fingerprint": HASH_A,
    }
    data.update(overrides)
    return ProviderExecutionRecord(**data)


def _handle(task_id: str = "remote-task-1") -> ProviderHandle:
    return ProviderHandle(
        provider_model_id="wan2.7-i2v",
        task_id=task_id,
        scope=_scope(),
        credential_ref="credential-ref",
        request_id="remote-request-1",
        query_capability=QueryCapability.BY_TASK_ID,
        submitted_at=3.0,
    )


def test_neutral_provider_modules_do_not_import_product_applications(tmp_path):
    probe = tmp_path / "neutral_import_probe.py"
    probe.write_text("""
import importlib
import sys

importlib.import_module("src.models.provider_execution")
importlib.import_module("src.models.provider_recovery")
violations = [name for name in sys.modules if name == "src.apps" or name.startswith("src.apps.")]
if violations:
    raise SystemExit("\\n".join(violations))
""")
    helper = Path(os.environ["LUMENX_REPO_ROOT"]) / "scripts/test_support/isolated_child.py"
    result = subprocess.run([sys.executable, "-S", str(helper), str(probe)], check=False, capture_output=True, text=True)

    assert result.returncode == 0, result.stderr or result.stdout


def test_studio_imports_reexport_the_neutral_provider_contract_objects():
    neutral_execution = importlib.import_module("src.models.provider_execution")
    neutral_recovery = importlib.import_module("src.models.provider_recovery")
    studio_models = importlib.import_module("src.apps.comic_gen.models")
    studio_recovery = importlib.import_module("src.apps.comic_gen.provider_recovery")

    assert studio_models.ProviderExecutionRecord is neutral_execution.ProviderExecutionRecord
    assert studio_recovery.ProviderExecutionRecord is neutral_execution.ProviderExecutionRecord
    assert studio_recovery.StudioProviderRecoveryError is neutral_recovery.ProviderRecoveryError
    assert studio_recovery.PreparedMedia is neutral_recovery.PreparedMedia
    assert studio_recovery.reconstruct_submission_from_record is neutral_recovery.reconstruct_submission_from_record
    assert studio_recovery.artifact_proof_from_record is neutral_recovery.artifact_proof_from_record


def test_neutral_provider_record_schema_matches_studio_baseline_file():
    from src.models.provider_execution import ProviderExecutionRecord

    schema = json.dumps(ProviderExecutionRecord.model_json_schema(), sort_keys=True, ensure_ascii=True)

    assert hashlib.sha256(schema.encode("utf-8")).hexdigest() == "bd0ff486fb4538cee36decdee8888d26454f4db0984c5c762a8c70113116a854"


def test_neutral_record_rejects_tampered_fingerprint():
    from src.models import provider_recovery

    with pytest.raises(provider_recovery.ProviderRecoveryError) as exc:
        provider_recovery.reconstruct_submission_from_record(_record(fingerprint=HASH_B), output_root=Path("/tmp"))

    assert exc.value.reason == "provider_execution_fingerprint_mismatch"


def test_neutral_handle_observation_and_artifact_binding_checks():
    from src.models import provider_recovery

    handle = _handle()
    record = provider_recovery.record_with_changes(_record(handle=handle.to_dict()), handle=handle.to_dict())
    assert provider_recovery.handle_from_record(record) == handle

    wrong_handle = _handle("remote-task-2")
    observation = ProviderObservation(
        task_id="remote-task-1",
        status=ProviderTaskStatus.SUCCEEDED,
        raw_status="SUCCEEDED",
        request_id="remote-request-1",
        video_url="https://media.example.invalid/result.mp4",
        error_code=None,
        source_handle=wrong_handle,
    )
    with pytest.raises(provider_recovery.ProviderRecoveryError) as exc:
        provider_recovery.validate_observation_for_handle(observation, handle)
    assert exc.value.reason == "provider_observation_invalid"

    artifact = ProviderArtifact(
        wrong_handle,
        "video/provider-attempt-1.mp4",
        "c" * 64,
        12,
    )
    with pytest.raises(provider_recovery.ProviderRecoveryError) as exc:
        provider_recovery.artifact_proof_from_record(record, artifact)
    assert exc.value.reason == "provider_artifact_handle_mismatch"
