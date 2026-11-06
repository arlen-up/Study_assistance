"""적응형 복습 간격: 퀴즈 성적에 따라 다음 복습 시점이 바뀐다 (DB 없이 확인)."""

import datetime as dt

from backend.services.quiz_service import missed_items
from backend.services.review_service import adaptive_next
from backend.schemas.models import QuestionResult, QuizQuestionDraft

NOW = dt.datetime(2026, 11, 6, 12, 0, tzinfo=dt.UTC)


def at(days_ago: int) -> dt.datetime:
    return NOW - dt.timedelta(days=days_ago)


def test_good_streak_follows_1_3_7_then_done() -> None:
    assert adaptive_next([(at(0), 3, 3)]) == (at(0) + dt.timedelta(days=1), "1일 후 복습")
    assert adaptive_next([(at(0), 2, 3), (at(2), 3, 3)])[1] == "3일 후 복습"
    assert adaptive_next([(at(0), 3, 3), (at(3), 2, 3), (at(5), 3, 3)])[1] == "7일 후 복습"
    assert adaptive_next([(at(0), 3, 3), (at(7), 3, 3), (at(9), 3, 3), (at(10), 3, 3)]) == (None, "복습 완료")


def test_poor_score_resets_to_one_day() -> None:
    nxt, label = adaptive_next([(at(0), 1, 3), (at(3), 3, 3), (at(5), 3, 3)])
    assert nxt == at(0) + dt.timedelta(days=1) and label == "다시 복습 (지난 퀴즈 1/3)"
    # 완료된 교안도 다시 틀리면 복습으로 돌아온다
    assert adaptive_next([(at(0), 0, 3)] + [(at(i), 3, 3) for i in range(5, 9)])[0] is not None


def test_sessions_without_score_count_as_good() -> None:
    assert adaptive_next([(at(0), None, None), (at(1), 3, 3)])[1] == "3일 후 복습"


def test_missed_items_only_wrong_questions() -> None:
    qs = [
        QuizQuestionDraft(type="ox", question="List는 순서가 있다", answer="O", explanation="순서 유지"),
        QuizQuestionDraft(type="ox", question="Set은 중복을 허용한다", answer="X", explanation="중복 불가"),
    ]
    rs = [QuestionResult(index=0, correct=True, your_answer="O", correct_answer="O", explanation=""),
          QuestionResult(index=1, correct=False, your_answer="O", correct_answer="X", explanation="")]
    assert missed_items(qs, rs) == [{"question": "Set은 중복을 허용한다", "answer": "X", "explanation": "중복 불가"}]
