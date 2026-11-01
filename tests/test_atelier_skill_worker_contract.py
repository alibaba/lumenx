import pytest

from src.apps.atelier.product_skills import resolve_product_skill_snapshot
from src.apps.atelier.skill_worker_contract import build_skill_worker_job


def test_worker_job_isolated_contract_is_networkless_and_skill_bound():
    snapshot = resolve_product_skill_snapshot("story-scout")
    job = build_skill_worker_job(
        job_id="job-1", user_id="user-1", project_id="project-1", snapshot=snapshot,
        request_payload={"prompt": "scout", "client_request_id": "r1"},
    )
    assert job.network_access == "disabled"
    assert job.credential_ref is None
    assert job.skill_binding.skill_content_hash == snapshot.content_hash


def test_worker_job_rejects_forbidden_request_fields():
    snapshot = resolve_product_skill_snapshot("story-scout")
    with pytest.raises(ValueError, match="forbidden_fields"):
        build_skill_worker_job(
            job_id="job-1", user_id="user-1", project_id="project-1", snapshot=snapshot,
            request_payload={"script": "python unsafe.py"},
        )
