"""FastAPI router for the creator platform.

The router is only mounted when ``LUMENX_PLATFORM_ENABLED=true``. Payment
endpoints are intentionally absent; accepted tasks write simulated ledger
entries for business-flow testing.
"""

from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from .service import PlatformError, PlatformService
from .contracts import TaskStatus

router = APIRouter()
bearer = HTTPBearer(auto_error=False)


def service(request: Request) -> PlatformService:
    current = getattr(request.app.state, "platform_service", None)
    if current is None:
        current = PlatformService()
        request.app.state.platform_service = current
    return current


def current_user(credentials: HTTPAuthorizationCredentials | None = Depends(bearer), svc: PlatformService = Depends(service)) -> dict[str, Any]:
    if not credentials: raise HTTPException(401, "authentication required")
    try: return svc.authenticate(credentials.credentials)
    except PlatformError as exc: raise HTTPException(exc.status, detail={"code": exc.code, "message": exc.message}) from exc


def handle(fn):
    try: return fn()
    except PlatformError as exc: raise HTTPException(exc.status, detail={"code": exc.code, "message": exc.message}) from exc


class RegisterIn(BaseModel):
    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=12, max_length=200)
    email: str | None = None


class LoginIn(BaseModel): username: str; password: str
class CompetitionIn(BaseModel):
    title: str; slug: str; description: str = ""; rules_markdown: str = ""; whitelist_required: bool = True; allocated_credits: int = Field(default=0, ge=0); status: str = "draft"; enroll_deadline: str | None = None; submit_deadline: str | None = None
class WhitelistIn(BaseModel): user_id: str; invite_code: str | None = None
class ArtifactIn(BaseModel):
    artifact_id: str; artifact_version: str; sha256: str = Field(pattern=r"^[0-9a-f]{64}$"); media_type: str; project_id: str; project_title_snapshot: str; title: str; description: str = ""
class TaskIn(BaseModel): title: str; category: str; brief_markdown: str; bounty_cents: int = Field(ge=0); deadline: str; required_level: str = "standard"; status: str = "draft"
class DeliveryIn(ArtifactIn): delivery_note: str = ""
class ReviewIn(BaseModel): approved: bool; feedback: str = ""
class ExamIn(BaseModel): prompt: str; version: str
class CourseIn(BaseModel): title: str; description: str = ""; video_url: str = ""
class TemplateIn(BaseModel): title: str; source_project_id: str; version: str = "1"; metadata: dict[str, Any] = {}
class ForkIn(BaseModel): fork_project_id: str
class ApplyIn(BaseModel): exam_id: str; artifact: ArtifactIn
class CertReviewIn(BaseModel): approved: bool; score: float | None = None; feedback: str = ""
class SubmissionReviewIn(BaseModel): approved: bool; score: float | None = None; award_title: str | None = None
class CreditEstimateIn(BaseModel): provider: str; model: str; prompt_tokens: int = Field(ge=0); completion_tokens: int = Field(ge=0)
class MediaCreditEstimateIn(BaseModel): provider: str; model: str; media_kind: str; units: int = Field(gt=0)
class CreditGrantIn(BaseModel): user_id: str; amount_credits: int = Field(gt=0); reason: str = Field(min_length=1, max_length=200); idempotency_key: str = Field(min_length=8, max_length=200)
class CreditReservationIn(BaseModel): estimated_credits: int = Field(gt=0); generation_id: str = Field(min_length=1, max_length=200); pricing_version: str = Field(min_length=1, max_length=100); idempotency_key: str = Field(min_length=8, max_length=200)
class CreditSettleIn(BaseModel): actual_credits: int = Field(ge=0)
class CredentialIn(BaseModel): provider: str = Field(min_length=1, max_length=50); label: str = Field(min_length=1, max_length=80); api_key: str = Field(min_length=16, max_length=500)
class CredentialRotateIn(BaseModel): api_key: str = Field(min_length=16, max_length=500)


@router.post("/auth/register")
def register(body: RegisterIn, svc: PlatformService = Depends(service)): return handle(lambda: svc.register(body.username, body.password, body.email))

@router.post("/auth/login")
def login(body: LoginIn, svc: PlatformService = Depends(service)): return handle(lambda: svc.login(body.username, body.password))

@router.get("/auth/me")
def me(user=Depends(current_user), svc: PlatformService = Depends(service)): return handle(lambda: {**user, "wallet": svc.wallet(user)})

@router.post("/auth/logout")
def logout(credentials: HTTPAuthorizationCredentials | None = Depends(bearer), svc: PlatformService = Depends(service)):
    if credentials: svc.logout(credentials.credentials)
    return {"status": "logged_out"}

@router.get("/usage/tokens")
def token_usage(limit: int = 100, user=Depends(current_user), svc: PlatformService = Depends(service)):
    return svc.token_usage(user, limit)

@router.get("/credentials")
def credentials(user=Depends(current_user), svc: PlatformService = Depends(service)): return svc.list_credentials(user)

@router.post("/credentials")
def create_credential(body: CredentialIn, user=Depends(current_user), svc: PlatformService = Depends(service)): return handle(lambda: svc.create_credential(user, body.provider, body.label, body.api_key))

@router.post("/credentials/{credential_id}/verify")
def verify_credential(credential_id: str, user=Depends(current_user), svc: PlatformService = Depends(service)): return handle(lambda: svc.verify_credential(user, credential_id))

@router.post("/credentials/{credential_id}/rotate")
def rotate_credential(credential_id: str, body: CredentialRotateIn, user=Depends(current_user), svc: PlatformService = Depends(service)): return handle(lambda: svc.rotate_credential(user, credential_id, body.api_key))

@router.post("/credentials/{credential_id}/revoke")
def revoke_credential(credential_id: str, user=Depends(current_user), svc: PlatformService = Depends(service)): return handle(lambda: svc.revoke_credential(user, credential_id))

@router.get("/credits/pricing")
def credit_pricing(svc: PlatformService = Depends(service)): return svc.credit_pricing()

@router.get("/credits/media-pricing")
def media_credit_pricing(svc: PlatformService = Depends(service)): return svc.media_pricing()

@router.post("/credits/estimate")
def credit_estimate(body: CreditEstimateIn, svc: PlatformService = Depends(service)): return handle(lambda: svc.estimate_credits(body.provider, body.model, body.prompt_tokens, body.completion_tokens))

@router.post("/credits/media-estimate")
def media_credit_estimate(body: MediaCreditEstimateIn, svc: PlatformService = Depends(service)): return handle(lambda: svc.estimate_media_credits(body.provider, body.model, body.media_kind, body.units))

@router.get("/credits/balance")
def credit_balance(user=Depends(current_user), svc: PlatformService = Depends(service)): return svc.credit_balance(user)

@router.get("/credits/ledger")
def credit_ledger(limit: int = 100, user=Depends(current_user), svc: PlatformService = Depends(service)): return svc.credit_ledger(user, limit)

@router.post("/credits/grants")
def credit_grant(body: CreditGrantIn, user=Depends(current_user), svc: PlatformService = Depends(service)): return handle(lambda: svc.grant_credits(user, body.user_id, body.amount_credits, body.reason, body.idempotency_key))

@router.post("/credits/reservations")
def credit_reserve(body: CreditReservationIn, user=Depends(current_user), svc: PlatformService = Depends(service)): return handle(lambda: svc.reserve_credits(user, body.estimated_credits, body.generation_id, body.pricing_version, body.idempotency_key))

@router.post("/credits/reservations/{reservation_id}/settle")
def credit_settle(reservation_id: str, body: CreditSettleIn, user=Depends(current_user), svc: PlatformService = Depends(service)): return handle(lambda: svc.settle_credits(user, reservation_id, body.actual_credits))

@router.post("/credits/reservations/{reservation_id}/release")
def credit_release(reservation_id: str, user=Depends(current_user), svc: PlatformService = Depends(service)): return handle(lambda: svc.release_credits(user, reservation_id))

@router.get("/competitions")
def competitions(svc: PlatformService = Depends(service)): return svc.list_competitions()

@router.get("/competitions/{competition_id}")
def competition(competition_id: str, svc: PlatformService = Depends(service)): return handle(lambda: svc.competition(competition_id))

@router.get("/competitions/{competition_id}/leaderboard")
def leaderboard(competition_id: str, svc: PlatformService = Depends(service)): return svc.leaderboard(competition_id)

@router.post("/competitions")
def create_competition(body: CompetitionIn, user=Depends(current_user), svc: PlatformService = Depends(service)): return handle(lambda: svc.create_competition(user, body.model_dump()))

@router.post("/competitions/{competition_id}/whitelist")
def whitelist(competition_id: str, body: WhitelistIn, user=Depends(current_user), svc: PlatformService = Depends(service)): return handle(lambda: svc.whitelist(user, competition_id, body.user_id, body.invite_code))

@router.post("/competitions/{competition_id}/enroll")
def enroll(competition_id: str, invite_code: str | None = None, idempotency_key: str = Header(..., alias="Idempotency-Key"), user=Depends(current_user), svc: PlatformService = Depends(service)): return handle(lambda: svc.enroll(user, competition_id, invite_code, idempotency_key))

@router.post("/competitions/{competition_id}/submit")
def competition_submit(competition_id: str, body: ArtifactIn, user=Depends(current_user), svc: PlatformService = Depends(service)): return handle(lambda: svc.submit_artifact(user, "competition", competition_id, body.model_dump()))

@router.post("/competitions/submissions/{submission_id}/review")
def review_submission(submission_id: str, body: SubmissionReviewIn, user=Depends(current_user), svc: PlatformService = Depends(service)): return handle(lambda: svc.review_submission(user, submission_id, body.approved, body.score, body.award_title))

@router.get("/academy/courses")
def courses(svc: PlatformService = Depends(service)): return svc.list_courses()

@router.post("/academy/courses")
def add_course(body: CourseIn, user=Depends(current_user), svc: PlatformService = Depends(service)): return handle(lambda: svc.add_course(user, body.model_dump()))

@router.get("/academy/templates")
def templates(svc: PlatformService = Depends(service)): return svc.list_templates()

@router.post("/academy/templates")
def add_template(body: TemplateIn, user=Depends(current_user), svc: PlatformService = Depends(service)): return handle(lambda: svc.add_template(user, body.model_dump()))

@router.post("/academy/templates/{template_id}/fork")
def fork_template(template_id: str, body: ForkIn, user=Depends(current_user), svc: PlatformService = Depends(service)): return handle(lambda: svc.fork_template(user, template_id, body.fork_project_id))

@router.post("/certifications/exams")
def create_exam(body: ExamIn, user=Depends(current_user), svc: PlatformService = Depends(service)): return handle(lambda: svc.create_exam(user, body.prompt, body.version))

@router.get("/certifications/exam/prompt")
def exam_prompt(svc: PlatformService = Depends(service)): return svc.active_exam()

@router.post("/certifications/apply")
def apply_cert(body: ApplyIn, user=Depends(current_user), svc: PlatformService = Depends(service)): return handle(lambda: svc.apply_certification(user, body.model_dump()))

@router.get("/certifications/status")
def cert_status(user=Depends(current_user), svc: PlatformService = Depends(service)): return svc.certification_status(user)

@router.post("/certifications/{application_id}/review")
def review_cert(application_id: str, body: CertReviewIn, user=Depends(current_user), svc: PlatformService = Depends(service)): return handle(lambda: svc.review_certification(user, application_id, body.approved, body.score, body.feedback))

@router.get("/tasks/square")
def square(user=Depends(current_user), svc: PlatformService = Depends(service)): return svc.task_square()

@router.post("/tasks")
def create_task(body: TaskIn, user=Depends(current_user), svc: PlatformService = Depends(service)): return handle(lambda: svc.create_task(user, body.model_dump()))

@router.post("/tasks/{task_id}/claim")
def claim(task_id: str, idempotency_key: str = Header(..., alias="Idempotency-Key"), user=Depends(current_user), svc: PlatformService = Depends(service)): return handle(lambda: svc.claim_task(user, task_id, idempotency_key))

@router.post("/tasks/{task_id}/deliver")
def deliver(task_id: str, body: DeliveryIn, user=Depends(current_user), svc: PlatformService = Depends(service)): return handle(lambda: svc.deliver_task(user, task_id, body.model_dump()))

@router.post("/tasks/{task_id}/start")
def start(task_id: str, user=Depends(current_user), svc: PlatformService = Depends(service)): return handle(lambda: svc.start_task(user, task_id))

@router.post("/tasks/{task_id}/review")
def review(task_id: str, body: ReviewIn, user=Depends(current_user), svc: PlatformService = Depends(service)): return handle(lambda: svc.review_task(user, task_id, body.approved, body.feedback))

@router.get("/settlement/wallet")
def wallet(user=Depends(current_user), svc: PlatformService = Depends(service)): return svc.wallet(user)

@router.get("/settlement/transactions")
def transactions(user=Depends(current_user), svc: PlatformService = Depends(service)): return svc.transactions(user)
