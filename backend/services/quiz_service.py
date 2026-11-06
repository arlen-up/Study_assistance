"""퀴즈 저장·채점·학습 기록.

정답은 서버(DB)에만 보관하고 클라이언트에는 문항만 보낸다.
채점도 서버에서 해서 점수 조작을 막는다.
"""

import logging
import re
from typing import Any
from uuid import UUID

from pydantic import ValidationError
from supabase import AsyncClient

from backend.core.errors import BadRequestError, NotFoundError, UpstreamError
from backend.schemas.models import (
    ExamQuestionDraft,
    Difficulty,
    PublicQuizQuestion,
    QuestionResult,
    QuizDraft,
    QuizQuestionDraft,
)

logger = logging.getLogger(__name__)

_WS = re.compile(r"\s+")


def _normalize_code(value: str) -> str:
    return _WS.sub(" ", value.strip().strip(";").strip())


def is_correct(question: QuizQuestionDraft, answer: str) -> bool:
    given = answer.strip()
    if question.type == "ox":
        return given.upper() == question.answer
    if question.type == "multiple_choice":
        return given == question.answer
    # code_blank: 공백 차이·끝 세미콜론은 무시, 대소문자는 구분(코드이므로)
    return _normalize_code(given) == _normalize_code(question.answer)


def to_public(questions: list[QuizQuestionDraft]) -> list[PublicQuizQuestion]:
    return [
        PublicQuizQuestion(
            index=i, type=q.type, question=q.question, options=q.options, code=q.code, language=q.language
        )
        for i, q in enumerate(questions)
    ]


async def save_quiz(
    db: AsyncClient,
    *,
    user_id: UUID,
    document_id: UUID,
    draft: QuizDraft,
    difficulty: Difficulty,
    model_name: str,
) -> UUID:
    try:
        res = (
            await db.table("quizzes")
            .insert(
                {
                    "user_id": str(user_id),
                    "document_id": str(document_id),
                    "keywords": draft.keywords,
                    "questions": [q.model_dump(mode="json") for q in draft.questions],
                    "difficulty": difficulty,
                    "model_name": model_name,
                }
            )
            .execute()
        )
    except Exception:
        logger.exception("quizzes insert 실패")
        raise UpstreamError("퀴즈 저장에 실패했습니다.") from None
    return UUID(res.data[0]["id"])


async def save_exam(
    db: AsyncClient,
    *,
    user_id: UUID,
    document_id: UUID,
    questions: list[ExamQuestionDraft],
    model_name: str,
) -> UUID:
    """기말 예상 문제를 서술형 문항으로 저장한다. 정답·모범 답안은 서버에만 둔다."""
    items = [
        {
            "type": "short_answer",
            "question": q.question,
            "options": [],
            "code": None,
            "language": None,
            "answer": q.answer,
            "explanation": q.explanation,
            "source_chunk_id": None,
        }
        for q in questions
    ]
    try:
        res = (
            await db.table("quizzes")
            .insert(
                {
                    "user_id": str(user_id),
                    "document_id": str(document_id),
                    "keywords": [],
                    "questions": items,
                    "difficulty": "medium",
                    "model_name": model_name,
                }
            )
            .execute()
        )
    except Exception:
        logger.exception("기말 예상 문제 저장 실패")
        raise UpstreamError("예상 문제 저장에 실패했습니다.") from None
    return UUID(res.data[0]["id"])


async def load_quiz(db: AsyncClient, *, quiz_id: UUID, user_id: UUID) -> tuple[UUID, list[QuizQuestionDraft]]:
    res = (
        await db.table("quizzes")
        .select("id,document_id,questions")
        .eq("id", str(quiz_id))
        .eq("user_id", str(user_id))  # secret 키는 RLS를 우회하므로 소유자 필터 필수
        .limit(1)
        .execute()
    )
    if not res.data:
        raise NotFoundError("퀴즈를 찾을 수 없습니다.")
    row: dict[str, Any] = res.data[0]
    try:
        questions = [QuizQuestionDraft.model_validate(q) for q in row["questions"]]
    except ValidationError:
        logger.exception("저장된 퀴즈 형식 오류: %s", quiz_id)
        raise UpstreamError("저장된 퀴즈 데이터가 손상되었습니다.") from None
    return UUID(row["document_id"]), questions


def grade(questions: list[QuizQuestionDraft], answers: list[str]) -> tuple[int, list[QuestionResult]]:
    if len(answers) != len(questions):
        raise BadRequestError(f"답안 개수({len(answers)})가 문항 수({len(questions)})와 다릅니다.")
    results = [
        QuestionResult(
            index=i,
            correct=is_correct(q, a),
            your_answer=a,
            correct_answer=q.answer,
            explanation=q.explanation,
        )
        for i, (q, a) in enumerate(zip(questions, answers, strict=True))
    ]
    return sum(r.correct for r in results), results


async def record_study_log(
    db: AsyncClient,
    *,
    user_id: UUID,
    document_id: UUID,
    quiz_id: UUID | None,
    score: int,
    total: int,
    duration_seconds: int | None,
    missed: list[dict[str, str]] | None = None,
) -> UUID:
    """학습 완료 기록. DB 트리거가 users.last_studied_at도 함께 갱신한다.

    missed: 틀린 문제(질문·정답·해설) — 같은 교안의 다음 퀴즈에서 틀린 개념을 다시 묻는 데 쓴다 (적응형 학습).
    """
    row = {
        "user_id": str(user_id),
        "document_id": str(document_id),
        "quiz_id": str(quiz_id) if quiz_id else None,
        "score": score,
        "total_questions": total,
        "completed": True,
        "duration_seconds": duration_seconds,
    }
    try:
        res = await db.table("study_logs").insert({**row, "missed": missed} if missed else row).execute()
    except Exception:
        if not missed:
            logger.exception("study_logs insert 실패")
            raise UpstreamError("학습 기록 저장에 실패했습니다.") from None
        # 틀린 문제 칸이 아직 없는 DB(마이그레이션 전)여도 학습 기록은 남긴다
        logger.warning("틀린 문제 기록 실패 → 학습 기록만 저장", exc_info=True)
        try:
            res = await db.table("study_logs").insert(row).execute()
        except Exception:
            logger.exception("study_logs insert 실패")
            raise UpstreamError("학습 기록 저장에 실패했습니다.") from None
    return UUID(res.data[0]["id"])


def missed_items(questions: list[QuizQuestionDraft], results: list[QuestionResult]) -> list[dict[str, str]]:
    """틀린 문제만 골라 다음 퀴즈에 쓸 형태로 (질문·정답·해설, 너무 길면 자름)."""
    return [
        {"question": q.question[:300], "answer": q.answer[:200], "explanation": (q.explanation or "")[:300]}
        for q, r in zip(questions, results, strict=True)
        if not r.correct
    ]
