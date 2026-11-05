"""매일 저녁 알림 (듀오링고 스타일) + 자동 동기화.

하루에 '가장 중요한 알림 하나'만 보낸다. 우선순위:
  1. assignment_due : 24시간 안에 마감되는 미제출 과제 (코스모스)
  2. review_due     : 오늘 복습할 교안 (망각 곡선) — 오늘 이미 공부했으면 보내지 않음
  3. streak_risk    : 연속 학습이 끊길 위기 — 오늘 이미 공부했으면 보내지 않음

Vercel Cron은 같은 실행을 두 번 보낼 수 있으므로(idempotency),
notification_logs에 (user, kind, ref_key, 날짜)를 먼저 '선점 기록'한 뒤에만 발송한다.
"""

import asyncio
import datetime as dt
import logging
import math
import time
from dataclasses import dataclass
from uuid import UUID

from supabase import AsyncClient

from backend.core.config import Settings
from backend.core.errors import AppError
from backend.integrations.lms_client import LmsClient
from backend.schemas.models import CronSummary
from backend.services import (
    lms_link_service,
    pet_service,
    push_service,
    review_service,
    streak_service,
    study_service,
)

logger = logging.getLogger(__name__)

KST = dt.timezone(dt.timedelta(hours=9))
URGENT_WINDOW = dt.timedelta(hours=24)
CONCURRENCY = 4
TIME_BUDGET_SECONDS = 50  # Vercel maxDuration 60초 안에 끝내기


@dataclass(frozen=True)
class Reminder:
    kind: str
    ref_key: str
    title: str
    body: str
    url: str


def today_kst() -> dt.date:
    return dt.datetime.now(KST).date()


async def build_reminder(db: AsyncClient, lms: LmsClient, user_id: UUID) -> Reminder | None:
    streak = await streak_service.get_streak(db, user_id, 30)
    pet_name = await study_service.get_pet_name(db, user_id)
    pet = pet_service.pet_status(streak, pet_name)
    title = pet_service.notify_title(pet_name)
    studied_today = bool(streak.days) and streak.days[-1].completed

    # 1) 마감 임박 과제 (LMS 연결된 학생만)
    student_id = await lms_link_service.get_student_id(db, user_id)
    if student_id is not None:
        now = dt.datetime.now(dt.UTC)
        try:
            assignments = await lms.list_open_assignments(student_id)
        except AppError as exc:
            logger.warning("알림용 과제 조회 실패: %s", exc.code)
            assignments = []
        urgent = [a for a in assignments if a.due_at and dt.timedelta(0) < a.due_at - now <= URGENT_WINDOW]
        if urgent:
            a = urgent[0]
            hours = max(1, math.ceil((a.due_at - now).total_seconds() / 3600))  # type: ignore[operator]
            tail = "" if studied_today else " 오늘 3분 복습도 잊지 마세요!"
            return Reminder(
                kind="assignment_due",
                ref_key=a.id,
                title=title,
                body=f"‘{a.title}’ 마감까지 {hours}시간 남았어요.{tail}",
                url="/?tab=chat",
            )

    if studied_today:
        return None  # 오늘 이미 공부했으면 더 재촉하지 않는다

    # 2) 복습할 교안
    reviews = await review_service.list_reviews(db, user_id)
    if reviews.due:
        first = reviews.due[0]
        more = f" 외 {len(reviews.due) - 1}개" if len(reviews.due) > 1 else ""
        return Reminder(
            kind="review_due",
            ref_key=str(first.document_id),
            title=title,
            body=f"오늘은 ‘{first.document_title}’{more} 복습하는 날이에요. 3분이면 끝나요!",
            url=f"/?tab=quiz&quiz_doc={first.document_id}",
        )

    # 3) 잔디 끊김 위기
    if pet.mood == "waiting":
        return Reminder(kind="streak_risk", ref_key="", title=title, body=pet.message, url="/?tab=quiz")
    return None


async def _already_notified_today(db: AsyncClient, user_id: UUID) -> bool:
    res = (
        await db.table("notification_logs")
        .select("id")
        .eq("user_id", str(user_id))
        .eq("local_date", today_kst().isoformat())
        .limit(1)
        .execute()
    )
    return bool(res.data)


async def _claim(db: AsyncClient, user_id: UUID, reminder: Reminder) -> str | None:
    """발송 권한 선점. 이미 같은 기록이 있으면(중복 실행) None."""
    try:
        res = await db.table("notification_logs").insert(
            {
                "user_id": str(user_id),
                "kind": reminder.kind,
                "ref_key": reminder.ref_key,
                "title": reminder.title,
                "body": reminder.body,
                "local_date": today_kst().isoformat(),
            }
        ).execute()
    except Exception as exc:  # 유니크 제약 위반(23505) = 이미 보냄
        if "23505" in str(exc) or "duplicate" in str(exc).lower():
            return None
        raise
    return str(res.data[0]["id"]) if res.data else None


async def notify_user(db: AsyncClient, settings: Settings, lms: LmsClient, user_id: UUID) -> str:
    """결과: 'sent' | 'skipped' | 'failed'"""
    if await _already_notified_today(db, user_id):
        return "skipped"
    reminder = await build_reminder(db, lms, user_id)
    if reminder is None:
        return "skipped"
    log_id = await _claim(db, user_id, reminder)
    if log_id is None:
        return "skipped"
    payload = push_service.PushPayload(title=reminder.title, body=reminder.body, url=reminder.url, tag=reminder.kind)
    delivered = await push_service.send_to_user(db, settings, user_id, payload)
    await db.table("notification_logs").update({"delivered": delivered}).eq("id", log_id).execute()
    return "sent" if delivered else "failed"


async def run_daily(db: AsyncClient, settings: Settings, lms: LmsClient) -> CronSummary:
    """매일 Cron: LMS 연결 학생 자동 동기화 + 구독자에게 알림."""
    started = time.monotonic()
    linked_res = await db.table("users").select("id,lms_student_id").execute()
    linked = {UUID(r["id"]) for r in linked_res.data or [] if r.get("lms_student_id")}
    subscribed = await push_service.subscribed_user_ids(db) if settings.push_enabled else set()
    users = sorted(linked | subscribed, key=str)

    counts = {"synced": 0, "imported": 0, "sent": 0, "skipped": 0, "failed": 0}
    semaphore = asyncio.Semaphore(CONCURRENCY)

    async def process(user_id: UUID) -> None:
        async with semaphore:
            if time.monotonic() - started > TIME_BUDGET_SECONDS:
                counts["skipped"] += 1
                return
            try:
                if user_id in linked:
                    result = await lms_link_service.sync_user(db, settings, lms, user_id, max_imports=3)
                    counts["synced"] += int(result.linked)
                    counts["imported"] += len(result.imported_titles)
                if user_id in subscribed:
                    counts[await notify_user(db, settings, lms, user_id)] += 1
            except Exception:
                logger.exception("일일 처리 실패")
                counts["failed"] += 1

    await asyncio.gather(*(process(u) for u in users))
    return CronSummary(
        users=len(users),
        synced=counts["synced"],
        imported_materials=counts["imported"],
        sent=counts["sent"],
        skipped=counts["skipped"],
        failed=counts["failed"],
        elapsed_seconds=round(time.monotonic() - started, 2),
    )
