"""에빙하우스 망각 곡선 복습 일정 (앱 내 알림).

DB 뷰 `review_schedule`이 교안별 학습 횟수로 다음 복습 시점을 계산한다.
  1회 완료 → +1일, 2회 → +3일, 3회 → +7일, 4회 이상 → 완료
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
    return await _only_own_documents(db, user_id, [_to_item(row) for row in res.data or []])


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
