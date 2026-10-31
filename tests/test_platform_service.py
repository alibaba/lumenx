from datetime import datetime, timedelta, timezone

from src.apps.platform.config import PlatformConfig
from src.apps.platform.service import PlatformError, PlatformService


def service(tmp_path):
    return PlatformService(PlatformConfig(enabled=True, database_url=f"sqlite:///{tmp_path / 'platform.db'}"))


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
