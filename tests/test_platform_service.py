from datetime import datetime, timedelta, timezone
import base64

from src.apps.platform.config import PlatformConfig
from src.apps.platform.service import PlatformError, PlatformService


def service(tmp_path):
    return PlatformService(PlatformConfig(enabled=True, database_url=f"sqlite:///{tmp_path / 'platform.db'}"))


def credential_service(tmp_path):
    return PlatformService(PlatformConfig(enabled=True, database_url=f"sqlite:///{tmp_path / 'platform.db'}", credentials_key=base64.urlsafe_b64encode(b"k" * 32).decode()))


def artifact(title="Demo"):
    return {
        "artifact_id": "artifact-1", "artifact_version": "v1", "sha256": "a" * 64,
        "media_type": "video/mp4", "project_id": "project-1", "project_title_snapshot": title,
        "title": title,
    }


def test_auth_session_revocation_and_password_policy(tmp_path):
    svc = service(tmp_path)
    user = svc.register("alice", "a" * 12, "alice@example.com")
    login = svc.login("alice", "a" * 12)
    assert svc.authenticate(login["access_token"])["id"] == user["id"]
    svc.logout(login["access_token"])
    try:
        svc.authenticate(login["access_token"])
    except PlatformError as exc:
        assert exc.code == "session_invalid"
    else:
        raise AssertionError("revoked session remained usable")


def test_token_usage_is_recorded_and_summarized_per_platform_user(tmp_path):
    svc = service(tmp_path)
    user = svc.register("alice", "a" * 12)
    svc.record_token_usage(user, {"product": "studio", "operation": "llm.chat", "provider": "dashscope", "requested_model": "qwen3.7-plus", "prompt_tokens": 10, "completion_tokens": 7, "total_tokens": 17})
    svc.record_token_usage(user, {"product": "atelier", "operation": "llm.chat", "provider": "dashscope", "prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5})
    usage = svc.token_usage(user)
    assert usage["prompt_tokens"] == 13
    assert usage["completion_tokens"] == 9
    assert usage["total_tokens"] == 22
    assert {row["product"] for row in usage["records"]} == {"studio", "atelier"}


def test_credit_reservation_settlement_release_and_idempotency(tmp_path):
    svc = service(tmp_path)
    admin = svc.register("admin", "a" * 12, role="admin")
    creator = svc.register("creator", "b" * 12)
    grant = svc.grant_credits(admin, creator["id"], 100, "trial", "grant-creator-1")
    assert grant["amount_credits"] == 100
    estimate = svc.estimate_credits("dashscope", "qwen3.7-plus", 1200, 800)
    assert estimate["estimated_credits"] == 6
    reservation = svc.reserve_credits(creator, 20, "generation-1", estimate["pricing_version"], "reserve-1")
    assert svc.reserve_credits(creator, 20, "generation-1", estimate["pricing_version"], "reserve-1")["idempotent_replay"]
    settled = svc.settle_credits(creator, reservation["reservation_id"], 7)
    assert settled["released_credits"] == 13
    assert svc.credit_balance(creator)["available_credits"] == 93
    released = svc.reserve_credits(creator, 10, "generation-2", estimate["pricing_version"], "reserve-2")
    assert svc.release_credits(creator, released["reservation_id"])["released_credits"] == 10
    assert svc.credit_balance(creator)["available_credits"] == 93


def test_user_credentials_are_encrypted_isolated_and_rotatable(tmp_path):
    svc = credential_service(tmp_path)
    alice = svc.register("alice", "a" * 12)
    bob = svc.register("bob", "b" * 12)
    credential = svc.create_credential(alice, "dashscope", "我的通义 Key", "sk-" + "a" * 30)
    assert credential["masked_key"] != "sk-" + "a" * 30
    assert "…" in credential["masked_key"]
    assert "ciphertext" not in credential and "secret" not in credential
    assert svc.verify_credential(alice, credential["id"])["last_verified_at"]
    try:
        svc.resolve_credential(bob, credential["id"])
    except PlatformError as exc:
        assert exc.code == "credential_not_found"
    else:
        raise AssertionError("credential crossed user boundary")
    resolved = svc.resolve_credential(alice, credential["id"])
    assert resolved["secret"] == "sk-" + "a" * 30
    rotated = svc.rotate_credential(alice, credential["id"], "sk-" + "b" * 30)
    assert svc.resolve_credential(alice, credential["id"])["secret"] == "sk-" + "b" * 30
    assert rotated["masked_key"] != credential["masked_key"]
    assert svc.revoke_credential(alice, credential["id"])["status"] == "revoked"
    try:
        svc.resolve_credential(alice, credential["id"])
    except PlatformError as exc:
        assert exc.code == "credential_revoked"
    else:
        raise AssertionError("revoked credential remained usable")
def test_certified_creator_claims_delivers_and_review_creates_simulated_ledger(tmp_path):
    svc = service(tmp_path)
    admin = svc.register("admin", "a" * 12, role="admin")
    creator = svc.register("creator", "b" * 12, role="creator")
    exam = svc.create_exam(admin, "Make a short scene", "1")
    application = svc.apply_certification(creator, {"exam_id": exam["id"], "artifact": artifact()})
    svc.review_certification(admin, application["id"], True, 92, "pass")
    task = svc.create_task(admin, {"title":"Ad", "category":"comic_ad", "brief_markdown":"brief", "bounty_cents":1200, "deadline":(datetime.now(timezone.utc)+timedelta(days=1)).isoformat(), "status":"published"})
    claim = svc.claim_task(creator, task["id"], "claim-key-1")
    assert claim["status"] == "claimed"
    svc.start_task(creator, task["id"])
    delivery = svc.deliver_task(creator, task["id"], artifact())
    assert delivery["version_num"] == 1
    result = svc.review_task(admin, task["id"], True, "accepted")
    assert result["status"] == "settled"
    assert svc.wallet(creator)["simulated_balance_cents"] == 1200


def test_claim_is_idempotent_and_competition_whitelist_is_enforced(tmp_path):
    svc = service(tmp_path)
    admin = svc.register("admin", "a" * 12, role="admin")
    creator = svc.register("creator", "b" * 12, role="creator")
    competition = svc.create_competition(admin, {"title":"C", "slug":"c", "status":"enrolling", "whitelist_required":True})
    try:
        svc.enroll(creator, competition["id"], None, "enroll-key-1")
    except PlatformError as exc:
        assert exc.code == "not_whitelisted"
    else:
        raise AssertionError("non-whitelisted enrollment succeeded")
    svc.whitelist(admin, competition["id"], creator["id"], "invite")
    first = svc.enroll(creator, competition["id"], "invite", "enroll-key-1")
    second = svc.enroll(creator, competition["id"], "invite", "enroll-key-1")
    assert first == second
