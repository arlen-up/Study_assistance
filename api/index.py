"""FastAPI 엔트리 포인트 (Vercel Python 서버리스 함수).

vercel.json 이 /api/* 요청을 모두 이 파일로 보낸다. 요청 경로는 그대로 유지되므로
라우트는 /api 접두사를 포함해 정의한다.

로컬 실행: uvicorn api.index:app --reload --port 8000
"""

import asyncio
import logging
import sys
from pathlib import Path
from typing import Annotated
from uuid import UUID

# Vercel/로컬 어디서 실행해도 프로젝트 루트의 backend 패키지를 import 할 수 있게 함
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from fastapi import Depends, FastAPI, File, Form, Query, Request, UploadFile  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402

from backend import __version__  # noqa: E402
from backend.core.auth import CurrentUser  # noqa: E402
from backend.core.config import get_settings  # noqa: E402
from backend.core.errors import (  # noqa: E402
    AppError,
    BadRequestError,
    ConfigError,
    PayloadTooLargeError,
    UpstreamError,
)
from backend.core.supabase_client import DbDep, SettingsDep  # noqa: E402
from backend.schemas.models import (  # noqa: E402
    ChatRequest,
    ChatResponse,
    DashboardResponse,
    DocumentIngestResponse,
    DocumentItem,
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
from backend.services import quiz_service, review_service, streak_service  # noqa: E402
from backend.services.agent_service import AgentService  # noqa: E402
from backend.services.document_service import (  # noqa: E402
    detect_source_type,
    ingest_document,
    parse_bytes,
    parse_plain_text,
)
from backend.services.embedding_service import EmbeddingService  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("lms.api")

ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    400: {"model": ErrorResponse},
    401: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
    502: {"model": ErrorResponse},
}

app = FastAPI(
    title="LMS 3분 학습 도우미 API",
    version=__version__,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
    redoc_url=None,
)


# ---------------------------------------------------------------------
# 예외 처리: 모든 오류를 {"error": {"code", "message"}} 형태로 통일
# ---------------------------------------------------------------------
@app.exception_handler(AppError)
async def handle_app_error(_: Request, exc: AppError) -> JSONResponse:
    if exc.status_code >= 500:
        logger.warning("AppError %s: %s", exc.code, exc.message)
    return JSONResponse(status_code=exc.status_code, content={"error": {"code": exc.code, "message": exc.message}})


@app.exception_handler(Exception)
async def handle_unexpected(_: Request, exc: Exception) -> JSONResponse:
    logger.exception("처리되지 않은 오류", exc_info=exc)
    return JSONResponse(
        status_code=500,
        content={"error": {"code": "internal_error", "message": "서버 오류가 발생했습니다."}},
    )


# ---------------------------------------------------------------------
# 의존성
# ---------------------------------------------------------------------
def get_agent(settings: SettingsDep, db: DbDep) -> AgentService:
    return AgentService(settings, db)


AgentDep = Annotated[AgentService, Depends(get_agent)]


# ---------------------------------------------------------------------
# 상태 확인
# ---------------------------------------------------------------------
@app.get("/api/health", response_model=HealthResponse, tags=["system"])
async def health() -> HealthResponse:
    try:
        get_settings()
    except ConfigError as exc:
        return HealthResponse(status="degraded", config_ok=False, missing_env=exc.missing, version=__version__)
    return HealthResponse(status="ok", config_ok=True, version=__version__)


# ---------------------------------------------------------------------
# 교안
# ---------------------------------------------------------------------
@app.get("/api/documents", response_model=DocumentListResponse, responses=ERROR_RESPONSES, tags=["documents"])
async def list_documents(
    _user: CurrentUser,
    db: DbDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> DocumentListResponse:
    try:
        res = (
            await db.table("documents")
            .select("id,title,course_name,week_no,keywords,created_at")
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
    except Exception:
        logger.exception("documents 조회 실패")
        raise UpstreamError("교안 목록을 불러오지 못했습니다.") from None
    return DocumentListResponse(documents=[DocumentItem.model_validate(row) for row in res.data])


@app.post(
    "/api/documents",
    response_model=DocumentIngestResponse,
    status_code=201,
    responses={**ERROR_RESPONSES, 413: {"model": ErrorResponse}},
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
    if (file is None) == (not text):
        raise BadRequestError("파일 또는 텍스트 중 하나만 보내 주세요.")

    if file is not None:
        data = await file.read(settings.max_upload_bytes + 1)
        if len(data) > settings.max_upload_bytes:
            raise PayloadTooLargeError(f"파일은 {settings.max_upload_bytes // 1_000_000}MB 이하만 가능합니다.")
        parsed = parse_bytes(data, detect_source_type(file.filename, file.content_type))
    else:
        parsed = parse_plain_text(text or "")

    keyword_list = [k.strip() for k in (keywords or "").split(",") if k.strip()][:20]
    return await ingest_document(
        db,
        EmbeddingService(settings),
        title=title.strip(),
        parsed=parsed,
        created_by=user.id,
        course_name=course_name.strip() if course_name else None,
        week_no=week_no,
        keywords=keyword_list,
    )


# ---------------------------------------------------------------------
# 챗봇 · 3분 요약 (MCP 에이전트)
# ---------------------------------------------------------------------
@app.post("/api/chat", response_model=ChatResponse, responses=ERROR_RESPONSES, tags=["agent"])
async def chat(body: ChatRequest, _user: CurrentUser, agent: AgentDep) -> ChatResponse:
    return await agent.chat(body)


@app.post("/api/summary", response_model=SummaryResponse, responses=ERROR_RESPONSES, tags=["agent"])
async def summary(body: SummaryRequest, _user: CurrentUser, agent: AgentDep) -> SummaryResponse:
    return await agent.summarize(body.document_id)


# ---------------------------------------------------------------------
# 적응형 마이크로 퀴즈
# ---------------------------------------------------------------------
@app.post("/api/quizzes", response_model=QuizResponse, status_code=201, responses=ERROR_RESPONSES, tags=["quiz"])
async def create_quiz(body: QuizGenerateRequest, user: CurrentUser, db: DbDep, agent: AgentDep) -> QuizResponse:
    document_id, title, draft, traces = await agent.generate_quiz(body.document_id, body.difficulty)
    quiz_id = await quiz_service.save_quiz(
        db,
        user_id=user.id,
        document_id=document_id,
        draft=draft,
        difficulty=body.difficulty,
        model_name=agent.model_name,
    )
    return QuizResponse(
        quiz_id=quiz_id,
        document_id=document_id,
        document_title=title,
        difficulty=body.difficulty,
        keywords=draft.keywords,
        questions=quiz_service.to_public(draft.questions),
        tool_calls=traces,
    )


@app.post(
    "/api/quizzes/{quiz_id}/submit",
    response_model=QuizSubmitResponse,
    responses=ERROR_RESPONSES,
    tags=["quiz"],
)
async def submit_quiz(quiz_id: UUID, body: QuizSubmitRequest, user: CurrentUser, db: DbDep) -> QuizSubmitResponse:
    document_id, questions = await quiz_service.load_quiz(db, quiz_id=quiz_id, user_id=user.id)
    score, results = quiz_service.grade(questions, body.answers)
    log_id = await quiz_service.record_study_log(
        db,
        user_id=user.id,
        document_id=document_id,
        quiz_id=quiz_id,
        score=score,
        total=len(questions),
        duration_seconds=body.duration_seconds,
    )
    review = await review_service.get_review(db, user.id, document_id)
    return QuizSubmitResponse(
        quiz_id=quiz_id, score=score, total=len(questions), results=results, study_log_id=log_id, review=review
    )


# ---------------------------------------------------------------------
# 복습 일정 · 잔디 · 대시보드
# ---------------------------------------------------------------------
@app.get("/api/reviews", response_model=ReviewListResponse, responses=ERROR_RESPONSES, tags=["learning"])
async def reviews(user: CurrentUser, db: DbDep) -> ReviewListResponse:
    return await review_service.list_reviews(db, user.id)


@app.get("/api/streak", response_model=StreakResponse, responses=ERROR_RESPONSES, tags=["learning"])
async def streak(
    user: CurrentUser,
    db: DbDep,
    days: Annotated[int, Query(ge=7, le=366)] = 365,
) -> StreakResponse:
    return await streak_service.get_streak(db, user.id, days)


@app.get("/api/dashboard", response_model=DashboardResponse, responses=ERROR_RESPONSES, tags=["learning"])
async def dashboard(user: CurrentUser, db: DbDep) -> DashboardResponse:
    streak_data, review_data = await asyncio.gather(
        streak_service.get_streak(db, user.id, 365),
        review_service.list_reviews(db, user.id),
    )
    return DashboardResponse(streak=streak_data, reviews=review_data)
