"""JSON API (/api/*). 외부 클라이언트·테스트·Swagger(/api/docs)용."""

import hmac

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, File, Form, Header, Path, Query, Request, UploadFile

from backend import __version__
from backend.core.auth import CurrentUser
from backend.core.config import get_settings
from backend.core.errors import AppError, ConfigError, UnauthorizedError
from backend.core.supabase_client import DbDep, SettingsDep
from backend.integrations.lms_client import LmsAssignment, LmsDep, LmsMaterialSummary, LmsSubmission, LmsTranscript
from backend.routers.deps import ApiAgentDep
from backend.schemas.career import CareerReport, CareerTargetRequest, CareerTrackItem
from backend.schemas.study_tools import (
    FlashcardDeck,
    FlashcardRequest,
    FlashcardResult,
    FlashcardResultRequest,
    MindMap,
    MindMapRequest,
)
from backend.schemas.models import (
    ChatRequest,
    ChatResponse,
    DashboardResponse,
    DocumentIngestResponse,
    DocumentListResponse,
    ErrorResponse,
    CronSummary,
    HealthResponse,
    LmsLinkStatus,
    PushResult,
    PushSubscriptionIn,
    PushUnsubscribeIn,
    QuizGenerateRequest,
    QuizResponse,
    QuizSubmitRequest,
    QuizSubmitResponse,
    ReviewListResponse,
    StreakResponse,
    SummaryRequest,
    SummaryResponse,
)
from backend.services import (
    career_service,
    lms_link_service,
    notification_service,
    pet_service,
    push_service,
    review_service,
    streak_service,
    study_service,
    study_tools_service,
)

ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    400: {"model": ErrorResponse},
    401: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
    502: {"model": ErrorResponse},
}

router = APIRouter(prefix="/api", responses=ERROR_RESPONSES)


@router.get("/health", response_model=HealthResponse, tags=["system"])
async def health() -> HealthResponse:
    try:
        get_settings()
    except ConfigError as exc:
        return HealthResponse(status="degraded", config_ok=False, missing_env=exc.missing, version=__version__)
    return HealthResponse(status="ok", config_ok=True, version=__version__)


# ---------------- 교안 ----------------
@router.get("/documents", response_model=DocumentListResponse, tags=["documents"])
async def list_documents(
    user: CurrentUser, db: DbDep, limit: Annotated[int, Query(ge=1, le=100)] = 30
) -> DocumentListResponse:
    """내 교안만 (교안은 계정별로 나뉜다)."""
    return DocumentListResponse(documents=await study_service.list_documents(db, user.id, limit))


@router.post(
    "/documents",
    response_model=DocumentIngestResponse,
    status_code=201,
    responses={413: {"model": ErrorResponse}},
    tags=["documents"],
)
async def upload_document(
    user: CurrentUser,
    db: DbDep,
    settings: SettingsDep,
    title: Annotated[str, Form(min_length=1, max_length=200)],
    course_name: Annotated[str | None, Form(max_length=100)] = None,
    week_no: Annotated[int | None, Form(ge=1, le=52)] = None,
    keywords: Annotated[str | None, Form(max_length=500, description="쉼표로 구분")] = None,
    text: Annotated[str | None, Form(max_length=200_000)] = None,
    file: Annotated[UploadFile | None, File()] = None,
) -> DocumentIngestResponse:
    """교안 업로드: 파일(PDF/MD/HTML/TXT) 또는 텍스트 중 하나. 파싱 → 청크 → 임베딩 → 저장."""
    return await study_service.upload_document(
        db,
        settings,
        user.id,
        title=title,
        course_name=course_name,
        week_no=week_no,
        keywords=keywords,
        text=text,
        file=file,
    )


# ---------------- 챗봇 · 요약 (MCP 에이전트) ----------------
@router.post("/chat", response_model=ChatResponse, tags=["agent"])
async def chat(body: ChatRequest, _user: CurrentUser, agent: ApiAgentDep) -> ChatResponse:
    return await agent.chat(body)


@router.post("/summary", response_model=SummaryResponse, tags=["agent"])
async def summary(body: SummaryRequest, _user: CurrentUser, agent: ApiAgentDep) -> SummaryResponse:
    return await agent.summarize(body.document_id)


# ---------------- 퀴즈 ----------------
@router.post("/quizzes", response_model=QuizResponse, status_code=201, tags=["quiz"])
async def create_quiz(body: QuizGenerateRequest, user: CurrentUser, db: DbDep, agent: ApiAgentDep) -> QuizResponse:
    return await study_service.create_quiz(agent, db, user.id, body.document_id, body.difficulty)


@router.post("/quizzes/{quiz_id}/submit", response_model=QuizSubmitResponse, tags=["quiz"])
async def submit_quiz(quiz_id: UUID, body: QuizSubmitRequest, user: CurrentUser, db: DbDep) -> QuizSubmitResponse:
    response, _ = await study_service.submit_quiz(db, user.id, quiz_id, body.answers, body.duration_seconds)
    return response


# ---------------- 복습 · 잔디 · 대시보드 ----------------
@router.get("/reviews", response_model=ReviewListResponse, tags=["learning"])
async def reviews(user: CurrentUser, db: DbDep) -> ReviewListResponse:
    return await review_service.list_reviews(db, user.id)


@router.get("/streak", response_model=StreakResponse, tags=["learning"])
async def streak(user: CurrentUser, db: DbDep, days: Annotated[int, Query(ge=7, le=366)] = 365) -> StreakResponse:
    return await streak_service.get_streak(db, user.id, days)


@router.get("/dashboard", response_model=DashboardResponse, tags=["learning"])
async def dashboard(user: CurrentUser, db: DbDep) -> DashboardResponse:
    return await study_service.get_dashboard(db, user.id)


# ---------------- 가상 LMS 연동 (도우미 → LMS API 경유) ----------------
@router.get("/lms/status", response_model=LmsLinkStatus, tags=["lms"])
async def lms_status(user: CurrentUser, db: DbDep, lms: LmsDep) -> LmsLinkStatus:
    return await lms_link_service.get_status(db, lms, user.id)


@router.post("/lms/unlink", response_model=LmsLinkStatus, tags=["lms"])
async def lms_unlink(user: CurrentUser, db: DbDep, lms: LmsDep) -> LmsLinkStatus:
    """학교 계정 연결 해제: 학교 쪽 토큰을 폐기하고 연결 정보를 지운다.
    (연결은 학생이 학교 로그인·동의 화면을 거쳐야 하므로 웹 화면의 '학교 계정으로 연결'에서만 한다)"""
    await lms_link_service.disconnect_school_account(db, lms, user.id)
    return LmsLinkStatus(linked=False)


@router.get("/lms/transcript", response_model=LmsTranscript, tags=["lms"])
async def lms_transcript(user: CurrentUser, db: DbDep, lms: LmsDep) -> LmsTranscript:
    student_id = await lms_link_service.require_student_id(db, user.id, lms)
    return await lms_link_service.sync_transcript(db, lms, user.id, student_id)


@router.get("/lms/assignments", response_model=list[LmsAssignment], tags=["lms"])
async def lms_assignments(user: CurrentUser, db: DbDep, lms: LmsDep) -> list[LmsAssignment]:
    return await lms_link_service.list_assignments(db, lms, user.id)


@router.post("/lms/assignments/{assignment_id}/submit", response_model=LmsSubmission, tags=["lms"])
async def lms_submit_assignment(
    assignment_id: Annotated[str, Path(pattern=r"^[0-9A-Za-z_-]{1,64}$")], user: CurrentUser, db: DbDep, lms: LmsDep
) -> LmsSubmission:
    return await lms_link_service.submit_assignment(db, lms, user.id, assignment_id)


@router.get("/lms/materials", response_model=list[LmsMaterialSummary], tags=["lms"])
async def lms_materials(user: CurrentUser, db: DbDep, lms: LmsDep) -> list[LmsMaterialSummary]:
    return await lms_link_service.list_materials(db, lms, user.id)


@router.post("/lms/materials/{material_id}/import", response_model=DocumentIngestResponse, tags=["lms"])
async def lms_import_material(
    material_id: Annotated[str, Path(pattern=r"^[0-9A-Za-z_-]{1,64}$")],
    user: CurrentUser,
    db: DbDep,
    settings: SettingsDep,
    lms: LmsDep,
) -> DocumentIngestResponse:
    """LMS 강의 자료를 도우미의 RAG 인덱스로 가져온다 (이미 가져왔으면 재사용)."""
    return await lms_link_service.import_material(db, settings, lms, user.id, material_id)


# ---------------- 커리어 나비게이터 ----------------
@router.get("/career/tracks", response_model=list[CareerTrackItem], tags=["career"])
async def career_tracks(_user: CurrentUser, db: DbDep) -> list[CareerTrackItem]:
    tracks = await career_service.list_tracks(db)
    return [CareerTrackItem(job_role=t.job_role, title=t.title, description=t.description) for t in tracks]


@router.post("/career/target", response_model=CareerTrackItem, tags=["career"])
async def career_set_target(body: CareerTargetRequest, user: CurrentUser, db: DbDep) -> CareerTrackItem:
    track = await career_service.set_target_job(db, user.id, body.job_role)
    return CareerTrackItem(job_role=track.job_role, title=track.title, description=track.description)


@router.get("/career", response_model=CareerReport, tags=["career"])
async def career_report(
    user: CurrentUser,
    db: DbDep,
    lms: LmsDep,
    agent: ApiAgentDep,
    job_role: Annotated[str | None, Query(pattern=r"^[a-z_]{2,30}$", description="비우면 관심 직무")] = None,
    with_ai: Annotated[bool, Query(description="AI 조언 포함 여부 (false면 계산 결과만)")] = True,
) -> CareerReport:
    """졸업 요건 분석 + 직무 트랙 추천. 계산은 파이썬, 조언은 AI (후보 과목 안에서만)."""
    return await career_service.build_report(db, lms, user.id, agent if with_ai else None, job_role)


# ---------------- 푸시 알림 ----------------
@router.get("/push/public-key", tags=["push"])
async def push_public_key(settings: SettingsDep) -> dict[str, object]:
    """브라우저 구독용 VAPID 공개 키 (공개해도 되는 값)."""
    return {"enabled": settings.push_enabled, "publicKey": settings.vapid_public_key}


@router.post("/push/subscribe", response_model=PushResult, tags=["push"])
async def push_subscribe(
    body: PushSubscriptionIn, request: Request, user: CurrentUser, db: DbDep, settings: SettingsDep
) -> PushResult:
    await push_service.save_subscription(db, settings, user.id, body, request.headers.get("user-agent"))
    return PushResult(ok=True, message="알림을 켰어요.")


@router.post("/push/unsubscribe", response_model=PushResult, tags=["push"])
async def push_unsubscribe(body: PushUnsubscribeIn, user: CurrentUser, db: DbDep) -> PushResult:
    await push_service.delete_subscription(db, user.id, body.endpoint)
    return PushResult(ok=True, message="알림을 껐어요.")


@router.post("/push/test", response_model=PushResult, tags=["push"])
async def push_test(user: CurrentUser, db: DbDep, settings: SettingsDep) -> PushResult:
    pet_name = await study_service.get_pet_name(db, user.id)
    payload = push_service.PushPayload(
        title=pet_service.notify_title(pet_name),
        body="알림이 잘 도착했어요! 매일 저녁 8시쯤 공부할 거리를 알려드릴게요.",
        url="/",
        tag="test",
    )
    delivered = await push_service.send_to_user(db, settings, user.id, payload)
    if delivered == 0:
        return PushResult(ok=False, message="알림을 받을 기기가 없어요. 먼저 '알림 켜기'를 눌러 주세요.")
    return PushResult(ok=True, delivered=delivered, message=f"{delivered}개 기기로 보냈어요.")


# ---------------- 매일 알림 · 자동 동기화 (Vercel Cron) ----------------
@router.get("/cron/daily-reminders", response_model=CronSummary, tags=["cron"])
async def cron_daily_reminders(
    db: DbDep,
    settings: SettingsDep,
    lms: LmsDep,
    authorization: Annotated[str | None, Header()] = None,
) -> CronSummary:
    """Vercel Cron이 매일 호출한다. Authorization: Bearer <CRON_SECRET> 가 맞아야 실행된다."""
    if settings.cron_secret is None:
        raise AppError("CRON_SECRET이 설정되지 않았습니다.", code="cron_not_configured", status_code=503)
    expected = f"Bearer {settings.cron_secret.get_secret_value()}"
    if not authorization or not hmac.compare_digest(authorization.encode(), expected.encode()):
        raise UnauthorizedError("Cron 인증에 실패했습니다.")
    return await notification_service.run_daily(db, settings, lms)


# ---------------- 노트북LM 참고 학습 도구 ----------------
@router.post("/flashcards", response_model=FlashcardDeck, tags=["study-tools"])
async def flashcards(body: FlashcardRequest, _user: CurrentUser, agent: ApiAgentDep) -> FlashcardDeck:
    return await agent.generate_flashcards(body.document_id)


@router.post("/flashcards/result", response_model=FlashcardResult, tags=["study-tools"])
async def flashcards_result(body: FlashcardResultRequest, user: CurrentUser, db: DbDep) -> FlashcardResult:
    await study_service.require_document(db, user.id, body.document_id)
    log_id = await study_tools_service.record_flashcard_session(
        db, user.id, body.document_id, body.known, body.total, body.duration_seconds
    )
    return FlashcardResult(known=body.known, total=body.total, study_log_id=log_id)


@router.post("/mindmap", response_model=MindMap, tags=["study-tools"])
async def mindmap(body: MindMapRequest, _user: CurrentUser, agent: ApiAgentDep) -> MindMap:
    return await agent.generate_mindmap(body.document_id)
