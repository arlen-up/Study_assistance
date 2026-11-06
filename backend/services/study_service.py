"""JSON API와 웹 화면이 함께 쓰는 학습 흐름(유스케이스) 모음."""

import asyncio
import logging
from uuid import UUID

from fastapi import UploadFile
from supabase import AsyncClient

from backend.core.config import Settings
from backend.core.errors import BadRequestError, NotFoundError, PayloadTooLargeError, UpstreamError
from backend.schemas.models import (
    DashboardResponse,
    Difficulty,
    DocumentIngestResponse,
    DocumentItem,
    QuizQuestionDraft,
    QuizResponse,
    QuizSubmitResponse,
)
from backend.services import file_store, pet_service, quiz_service, review_service, streak_service
from backend.services.agent_service import AgentService
from backend.services.document_service import detect_source_type, ingest_document, parse_bytes, parse_plain_text
from backend.services.embedding_service import EmbeddingService

logger = logging.getLogger(__name__)


# 교안은 계정별로 나뉜다: 본인이 올리거나 가져온 교안(documents.created_by)만 보이고 쓸 수 있다.
# db 는 secret 키 클라이언트(RLS 우회)라서 모든 조회에 소유자 조건을 직접 붙인다.
async def list_documents(db: AsyncClient, user_id: UUID, limit: int = 30) -> list[DocumentItem]:
    try:
        res = (
            await db.table("documents")
            .select("id,title,course_name,week_no,keywords,created_at")
            .eq("created_by", str(user_id))
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
    except Exception:
        logger.exception("documents 조회 실패")
        raise UpstreamError("교안 목록을 불러오지 못했습니다.") from None
    return [DocumentItem.model_validate(row) for row in res.data]


async def require_document(db: AsyncClient, user_id: UUID, document_id: UUID) -> None:
    """본인 교안이 아니면 '없음'으로 답한다 (남의 교안이 있는지도 알 수 없게)."""
    try:
        res = (
            await db.table("documents")
            .select("id")
            .eq("id", str(document_id))
            .eq("created_by", str(user_id))
            .limit(1)
            .execute()
        )
    except Exception:
        logger.exception("documents 확인 실패")
        raise UpstreamError("교안 정보를 확인하지 못했습니다.") from None
    if not res.data:
        raise NotFoundError("교안을 찾을 수 없습니다.")


async def get_pet_name(db: AsyncClient, user_id: UUID) -> str | None:
    try:
        res = await db.table("users").select("pet_name").eq("id", str(user_id)).limit(1).execute()
    except Exception:
        logger.warning("pet_name 조회 실패 — 기본 이름 사용")
        return None
    return res.data[0].get("pet_name") if res.data else None


async def get_dashboard(db: AsyncClient, user_id: UUID) -> DashboardResponse:
    streak, reviews, pet_name = await asyncio.gather(
        streak_service.get_streak(db, user_id, 365),
        review_service.list_reviews(db, user_id),
        get_pet_name(db, user_id),
    )
    return DashboardResponse(streak=streak, reviews=reviews, pet=pet_service.pet_status(streak, pet_name))


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

    data = b""
    if has_file and file is not None:
        data = await file.read(settings.max_upload_bytes + 1)
        if len(data) > settings.max_upload_bytes:
            raise PayloadTooLargeError(f"파일은 {settings.max_upload_bytes // 1_000_000}MB 이하만 가능합니다.")
        parsed = parse_bytes(data, detect_source_type(file.filename, file.content_type))
    else:
        parsed = parse_plain_text(text or "")

    keyword_list = [k.strip() for k in (keywords or "").split(",") if k.strip()][:20]
    result = await ingest_document(
        db,
        EmbeddingService(settings),
        title=title.strip(),
        parsed=parsed,
        created_by=user_id,
        course_name=course_name.strip() if course_name and course_name.strip() else None,
        week_no=week_no,
        keywords=keyword_list,
    )
    if parsed.source_type == "pdf" and data:
        # 원본 PDF를 보관해 두면 뷰어에서 출처 쪽(p.3 등)을 바로 띄울 수 있다
        result.original_saved = await file_store.save_pdf(db, result.document_id, data)
    return result


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
