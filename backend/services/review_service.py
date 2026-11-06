"""에빙하우스 망각 곡선 복습 일정 — 퀴즈 성적에 맞춰 바뀐다 (적응형).

교안마다 최근 학습 기록을 보고 다음 복습 시점을 정한다.
  - 잘 푼 학습(3문제 중 2개 이상, 점수 없는 학습 포함)이 연속으로 쌓이면 1일 → 3일 → 7일 → 복습 완료
  - 마지막 퀴즈를 못 풀었으면(3문제 중 1개 이하) 연속 기록이 끊기고 **1일 뒤 다시** 복습
DB 뷰 `review_schedule`은 교안별 학습 횟수·마지막 학습 시각을 주고, 간격 계산은 여기서 한다.
대시보드를 열 때 is_due=True 인 항목을 '오늘 복습할 교안'으로 보여준다.
"""

import datetime as dt
import logging
from typing import Any
from uuid import UUID

from supabase import AsyncClient

from backend.core.errors import UpstreamError
from backend.schemas.models import ReviewItem, ReviewListResponse

logger = logging.getLogger(__name__)

_STAGE_LABELS = {1: "1일 후 복습", 2: "3일 후 복습", 3: "7일 후 복습"}
_INTERVAL_DAYS = {1: 1, 2: 3, 3: 7}   # 잘 푼 학습 연속 횟수 → 다음 복습까지 날짜 (4번 이상이면 완료)
GOOD_RATIO = 2 / 3                    # 이 비율 이상 맞히면 '잘 풂'


def _good(score: int | None, total: int | None) -> bool:
    return score is None or not total or score / total >= GOOD_RATIO - 1e-9


def adaptive_next(sessions: list[tuple[dt.datetime, int | None, int | None]]) -> tuple[dt.datetime | None, str]:
    """(학습 시각, 점수, 문항 수) 목록(최신 먼저) → (다음 복습 시각 또는 None=완료, 단계 이름)."""
    last_at, score, total = sessions[0]
    streak = 0
    for _, s, t in sessions:
        if not _good(s, t):
            break
        streak += 1
    if streak == 0:
        return last_at + dt.timedelta(days=1), f"다시 복습 (지난 퀴즈 {score}/{total})"
    if streak > len(_INTERVAL_DAYS):
        return None, "복습 완료"
    return last_at + dt.timedelta(days=_INTERVAL_DAYS[streak]), _STAGE_LABELS[streak]
_FAR_FUTURE = dt.datetime.max.replace(tzinfo=dt.UTC)


def stage_label(review_count: int) -> str:
    return _STAGE_LABELS.get(review_count, "복습 완료")


def _to_item(row: dict[str, Any]) -> ReviewItem:
    return ReviewItem(
        document_id=row["document_id"],
        document_title=row["document_title"],
        last_studied_at=row["last_studied_at"],
        review_count=row["review_count"],
        review_1d_at=row["review_1d_at"],
        review_3d_at=row["review_3d_at"],
        review_7d_at=row["review_7d_at"],
        next_review_at=row.get("next_review_at"),
        is_due=bool(row["is_due"]),
        stage_label=stage_label(int(row["review_count"])),
    )


async def _fetch(db: AsyncClient, user_id: UUID, document_id: UUID | None = None) -> list[ReviewItem]:
    query = db.table("review_schedule").select("*").eq("user_id", str(user_id))
    if document_id is not None:
        query = query.eq("document_id", str(document_id))
    try:
        res = await query.execute()
    except Exception:
        logger.exception("review_schedule 조회 실패")
        raise UpstreamError("복습 일정을 불러오지 못했습니다.") from None
    items = await _only_own_documents(db, user_id, [_to_item(row) for row in res.data or []])
    return await _apply_adaptive(db, user_id, items)


async def _apply_adaptive(db: AsyncClient, user_id: UUID, items: list[ReviewItem]) -> list[ReviewItem]:
    """최근 퀴즈 성적으로 다음 복습 시점·단계를 다시 정한다. 조회가 실패하면 횟수 기준 일정을 그대로 쓴다."""
    ids = list({str(item.document_id) for item in items})
    if not ids:
        return items
    try:
        res = (
            await db.table("study_logs")
            .select("document_id,score,total_questions,studied_at")
            .eq("user_id", str(user_id))
            .eq("completed", True)
            .in_("document_id", ids)
            .order("studied_at", desc=True)
            .limit(2000)
            .execute()
        )
    except Exception:
        logger.warning("복습 간격 조정용 학습 기록 조회 실패 — 횟수 기준 일정 사용", exc_info=True)
        return items
    sessions: dict[str, list[tuple[dt.datetime, int | None, int | None]]] = {}
    for row in res.data or []:
        sessions.setdefault(row["document_id"], []).append(
            (dt.datetime.fromisoformat(row["studied_at"]), row.get("score"), row.get("total_questions"))
        )
    now = dt.datetime.now(dt.UTC)
    for item in items:
        history = sessions.get(str(item.document_id))
        if not history:
            continue
        item.next_review_at, item.stage_label = adaptive_next(history)
        item.is_due = item.next_review_at is not None and item.next_review_at <= now
    return items


async def _only_own_documents(db: AsyncClient, user_id: UUID, items: list[ReviewItem]) -> list[ReviewItem]:
    """교안은 계정별로 나뉜다: 예전에 남의 교안으로 공부한 기록은 복습 목록에 띄우지 않는다."""
    ids = list({str(item.document_id) for item in items})
    if not ids:
        return items
    try:
        res = (
            await db.table("documents").select("id").in_("id", ids).eq("created_by", str(user_id)).execute()
        )
    except Exception:
        logger.exception("복습 교안 소유자 확인 실패")
        raise UpstreamError("복습 일정을 불러오지 못했습니다.") from None
    own = {row["id"] for row in res.data or []}
    return [item for item in items if str(item.document_id) in own]


def split_reviews(items: list[ReviewItem]) -> ReviewListResponse:
    due = sorted((i for i in items if i.is_due), key=lambda i: i.next_review_at or _FAR_FUTURE)
    upcoming = sorted(
        (i for i in items if not i.is_due and i.next_review_at is not None),
        key=lambda i: i.next_review_at or _FAR_FUTURE,
    )
    mastered = sorted(
        (i for i in items if i.next_review_at is None), key=lambda i: i.last_studied_at, reverse=True
    )
    return ReviewListResponse(due=due, upcoming=upcoming, mastered=mastered)


async def _attach_course_names(db: AsyncClient, items: list[ReviewItem]) -> None:
    """과목명을 붙인다. 과목명 조회가 실패해도 복습 목록은 그대로 보여 준다."""
    ids = list({str(item.document_id) for item in items})
    if not ids:
        return
    try:
        res = await db.table("documents").select("id,course_name").in_("id", ids).execute()
    except Exception:
        logger.exception("과목명 조회 실패 (복습 목록은 과목명 없이 표시)")
        return
    names = {row["id"]: row.get("course_name") for row in res.data or []}
    for item in items:
        item.course_name = names.get(str(item.document_id))


async def list_reviews(db: AsyncClient, user_id: UUID) -> ReviewListResponse:
    items = await _fetch(db, user_id)
    await _attach_course_names(db, items)
    return split_reviews(items)


async def get_review(db: AsyncClient, user_id: UUID, document_id: UUID) -> ReviewItem | None:
    items = await _fetch(db, user_id, document_id)
    return items[0] if items else None
