"""JSON API와 웹 화면이 함께 쓰는 학습 흐름(유스케이스) 모음."""

import asyncio
import logging
from uuid import UUID

from fastapi import UploadFile
from supabase import AsyncClient

from backend.core.config import Settings
from backend.core.errors import BadRequestError, PayloadTooLargeError, UpstreamError
from backend.schemas.models import (
    DashboardResponse,
    Difficulty,
    DocumentIngestResponse,
    DocumentItem,
    QuizQuestionDraft,
    QuizResponse,
    QuizSubmitResponse,
)
from backend.services import quiz_service, review_service, streak_service
from backend.services.agent_service import AgentService
from backend.services.document_service import detect_source_type, ingest_document, parse_bytes, parse_plain_text
from backend.services.embedding_service import EmbeddingService

logger = logging.getLogger(__name__)


async def list_documents(db: AsyncClient, limit: int = 30) -> list[DocumentItem]:
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
    return [DocumentItem.model_validate(row) for row in res.data]


async def get_dashboard(db: AsyncClient, user_id: UUID) -> DashboardResponse:
    streak, reviews = await asyncio.gather(
        streak_service.get_streak(db, user_id, 365),
        review_service.list_reviews(db, user_id),
    )
    return DashboardResponse(streak=streak, reviews=reviews)


async def upload_document(
    db: AsyncClient,
    settings: Settings,
    user_id: UUID,
    *,
    title: str,
    course_name: str | None,
    week_no: int | None,
    keywords: str | None,
    text: str | None,
    file: UploadFile | None,
) -> DocumentIngestResponse:
    has_file = file is not None and bool(file.filename)
    has_text = bool(text and text.strip())
    if has_file == has_text:
        raise BadRequestError("파일 또는 텍스트 중 하나만 보내 주세요.")

    if has_file and file is not None:
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
        created_by=user_id,
        course_name=course_name.strip() if course_name and course_name.strip() else None,
        week_no=week_no,
        keywords=keyword_list,
    )


async def create_quiz(
    agent: AgentService, db: AsyncClient, user_id: UUID, document_id: UUID | None, difficulty: Difficulty
) -> QuizResponse:
    resolved_id, title, draft, traces = await agent.generate_quiz(document_id, difficulty)
    quiz_id = await quiz_service.save_quiz(
        db, user_id=user_id, document_id=resolved_id, draft=draft, difficulty=difficulty, model_name=agent.model_name
    )
    return QuizResponse(
        quiz_id=quiz_id,
        document_id=resolved_id,
        document_title=title,
        difficulty=difficulty,
        keywords=draft.keywords,
        questions=quiz_service.to_public(draft.questions),
        tool_calls=traces,
    )


async def submit_quiz(
    db: AsyncClient, user_id: UUID, quiz_id: UUID, answers: list[str], duration_seconds: int | None
) -> tuple[QuizSubmitResponse, list[QuizQuestionDraft]]:
    document_id, questions = await quiz_service.load_quiz(db, quiz_id=quiz_id, user_id=user_id)
    score, results = quiz_service.grade(questions, answers)
    log_id = await quiz_service.record_study_log(
        db,
        user_id=user_id,
        document_id=document_id,
        quiz_id=quiz_id,
        score=score,
        total=len(questions),
        duration_seconds=duration_seconds,
    )
    review = await review_service.get_review(db, user_id, document_id)
    response = QuizSubmitResponse(
        quiz_id=quiz_id, score=score, total=len(questions), results=results, study_log_id=log_id, review=review
    )
    return response, questions
