"""JSON API (/api/*). 외부 클라이언트·테스트·Swagger(/api/docs)용."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, File, Form, Query, UploadFile

from backend import __version__
from backend.core.auth import CurrentUser
from backend.core.config import get_settings
from backend.core.errors import ConfigError
from backend.core.supabase_client import DbDep, SettingsDep
from backend.routers.deps import AgentDep
from backend.schemas.models import (
    ChatRequest,
    ChatResponse,
    DashboardResponse,
    DocumentIngestResponse,
    DocumentListResponse,
    ErrorResponse,
    HealthResponse,
    QuizGenerateRequest,
    QuizResponse,
    QuizSubmitRequest,
    QuizSubmitResponse,
    ReviewListResponse,
    StreakResponse,
    SummaryRequest,
    SummaryResponse,
)
from backend.services import review_service, streak_service, study_service

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
    _user: CurrentUser, db: DbDep, limit: Annotated[int, Query(ge=1, le=100)] = 30
) -> DocumentListResponse:
    return DocumentListResponse(documents=await study_service.list_documents(db, limit))


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
async def chat(body: ChatRequest, _user: CurrentUser, agent: AgentDep) -> ChatResponse:
    return await agent.chat(body)


@router.post("/summary", response_model=SummaryResponse, tags=["agent"])
async def summary(body: SummaryRequest, _user: CurrentUser, agent: AgentDep) -> SummaryResponse:
    return await agent.summarize(body.document_id)


# ---------------- 퀴즈 ----------------
@router.post("/quizzes", response_model=QuizResponse, status_code=201, tags=["quiz"])
async def create_quiz(body: QuizGenerateRequest, user: CurrentUser, db: DbDep, agent: AgentDep) -> QuizResponse:
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
