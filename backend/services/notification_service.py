"""매일 저녁 메일 알림 (듀오링고 스타일) + 자동 동기화.

알림은 📧 메일로만 보낸다 (웹 푸시·카카오톡은 쓰지 않는다). 메일 알림을 직접 켠 학생에게만 보낸다.

하루에 '가장 중요한 알림 하나'만 보낸다. 우선순위:
  1. assignment_due : 24시간 안에 마감되는 미제출 과제 (코스모스)
  2. review_due     : 오늘 복습할 교안 (망각 곡선) — 오늘 이미 공부했으면 보내지 않음
  3. streak_risk    : 연속 학습이 끊길 위기 — 오늘 이미 공부했으면 보내지 않음

알림 고르기는 두 단계로 나눈다: gather_reminder_inputs(DB·LMS 조회) → choose_reminder(계산만).
매일 Cron 발송, 테스트 메일, 화면의 '오늘 메일 미리보기'가 모두 같은 재료와 같은 규칙을 쓴다 (email_service).

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
from backend.integrations.lms_client import LmsAssignment, LmsClient
from backend.schemas.models import CronSummary, PetStatus, ReviewListResponse, StreakResponse
from backend.services import (
    lms_link_service,
    pet_service,
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


@dataclass(frozen=True)
class ReminderInputs:
    """알림 문구를 고르는 데 필요한 재료. 푸시(Cron)와 메일 미리보기가 같은 재료를 쓴다."""

    pet_name: str | None
    pet: PetStatus
    streak: StreakResponse
    studied_today: bool
    linked: bool                          # 학교(LMS) 계정 연결 여부
    assignments: list[LmsAssignment]      # 미제출 과제 (마감 빠른 순)
    reviews: ReviewListResponse

    @property
    def title(self) -> str:
        return pet_service.notify_title(self.pet_name)


def today_kst() -> dt.date:
    return dt.datetime.now(KST).date()


async def gather_reminder_inputs(db: AsyncClient, lms: LmsClient, user_id: UUID) -> ReminderInputs:
    streak, pet_name, student_id, reviews = await asyncio.gather(
        streak_service.get_streak(db, user_id, 30),
        study_service.get_pet_name(db, user_id),
        lms_link_service.get_student_id(db, user_id, lms),
        review_service.list_reviews(db, user_id),
    )
    assignments: list[LmsAssignment] = []
    if student_id is not None:
        try:
            assignments = await lms.list_open_assignments(student_id)
        except AppError as exc:
            logger.warning("알림용 과제 조회 실패: %s", exc.code)
    return ReminderInputs(
        pet_name=pet_name,
        pet=pet_service.pet_status(streak, pet_name),
        streak=streak,
        studied_today=bool(streak.days) and streak.days[-1].completed,
        linked=student_id is not None,
        assignments=assignments,
        reviews=reviews,
    )


def urgent_assignments(inputs: ReminderInputs, now: dt.datetime) -> list[LmsAssignment]:
    return [a for a in inputs.assignments if a.due_at and dt.timedelta(0) < a.due_at - now <= URGENT_WINDOW]


def choose_reminder(inputs: ReminderInputs, now: dt.datetime | None = None) -> Reminder | None:
    """오늘 보낼 '가장 중요한 알림 하나'를 고른다 (DB·네트워크 없이 계산만)."""
    now = now or dt.datetime.now(dt.UTC)
    title = inputs.title

    # 1) 마감 임박 과제 (LMS 연결된 학생만)
    urgent = urgent_assignments(inputs, now)
    if urgent:
        a = urgent[0]
        hours = max(1, math.ceil((a.due_at - now).total_seconds() / 3600))  # type: ignore[operator]
        tail = "" if inputs.studied_today else " 오늘 3분 복습도 잊지 마세요!"
        return Reminder(
            kind="assignment_due",
            ref_key=a.id,
            title=title,
            body=f"‘{a.title}’ 마감까지 {hours}시간 남았어요.{tail}",
            url="/?tab=grades",  # 학점·진로 화면의 미제출 과제 목록
        )

    if inputs.studied_today:
        return None  # 오늘 이미 공부했으면 더 재촉하지 않는다

    # 2) 복습할 교안
    due = inputs.reviews.due
    if due:
        first = due[0]
        more = f" 외 {len(due) - 1}개" if len(due) > 1 else ""
        return Reminder(
            kind="review_due",
            ref_key=str(first.document_id),
            title=title,
            body=f"오늘은 ‘{first.document_title}’{more} 복습하는 날이에요. 3분이면 끝나요!",
            url=f"/?tab=chat&quiz_doc={first.document_id}",  # 챗봇에 퀴즈·카드 시작 단추가 뜬다
        )

    # 3) 잔디 끊김 위기
    if inputs.pet.mood == "waiting":
        return Reminder(kind="streak_risk", ref_key="", title=title, body=inputs.pet.message, url="/?tab=chat")
    return None


async def build_reminder(db: AsyncClient, lms: LmsClient, user_id: UUID) -> Reminder | None:
    return choose_reminder(await gather_reminder_inputs(db, lms, user_id))


TEST_KIND = "email_test"


async def _already_notified_today(db: AsyncClient, user_id: UUID) -> bool:
    res = (
        await db.table("notification_logs")
        .select("id")
        .eq("user_id", str(user_id))
        .eq("local_date", today_kst().isoformat())
        .neq("kind", TEST_KIND)  # 테스트 메일은 오늘 저녁 알림과 상관없다
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


# ---------------------------------------------------------------------
# 📧 메일 알림 설정 · 받는 사람
# ---------------------------------------------------------------------
@dataclass(frozen=True)
class EmailSettings:
    enabled: bool             # 학생이 메일 알림을 켰는지
    alert_email: str | None   # 따로 정한 받을 주소 (없으면 가입 메일)
    account_email: str | None

    @property
    def recipient(self) -> str | None:
        return self.alert_email or self.account_email


async def get_email_settings(db: AsyncClient, user_id: UUID) -> EmailSettings:
    try:
        res = (
            await db.table("users").select("email,email_alerts,alert_email").eq("id", str(user_id)).limit(1).execute()
        )
    except Exception:
        logger.warning("메일 알림 설정 조회 실패 (마이그레이션 전?)", exc_info=True)
        return EmailSettings(enabled=False, alert_email=None, account_email=None)
    row = res.data[0] if res.data else {}
    return EmailSettings(bool(row.get("email_alerts")), row.get("alert_email"), row.get("email"))


async def save_email_settings(db: AsyncClient, user_id: UUID, *, enabled: bool, alert_email: str | None) -> None:
    try:
        await db.table("users").update({"email_alerts": enabled, "alert_email": alert_email}).eq(
            "id", str(user_id)
        ).execute()
    except Exception:
        logger.exception("메일 알림 설정 저장 실패")
        raise AppError("메일 알림 설정을 저장하지 못했어요.", code="email_settings_failed", status_code=502) from None


async def email_recipients(db: AsyncClient) -> dict[UUID, str]:
    """메일 알림을 켠 학생 → 받을 주소."""
    res = await db.table("users").select("id,email,email_alerts,alert_email").eq("email_alerts", True).execute()
    out: dict[UUID, str] = {}
    for row in res.data or []:
        address = row.get("alert_email") or row.get("email")
        if address:
            out[UUID(row["id"])] = address
    return out


async def tests_sent_today(db: AsyncClient, user_id: UUID) -> int:
    res = (
        await db.table("notification_logs")
        .select("id", count="exact")
        .eq("user_id", str(user_id))
        .eq("kind", TEST_KIND)
        .eq("local_date", today_kst().isoformat())
        .execute()
    )
    return res.count or 0


async def send_test_email(
    db: AsyncClient, settings: Settings, lms: LmsClient, user_id: UUID, *, base_url: str
) -> str:
    """오늘 저녁에 갈 메일을 지금 한 번 보내 본다 (하루 횟수 제한). 받은 주소를 돌려준다."""
    from backend.services import email_service  # email_service가 이 모듈을 쓰므로 순환을 피해 여기서 가져온다

    prefs = await get_email_settings(db, user_id)
    if not prefs.recipient:
        raise AppError("받을 메일 주소가 없어요. 주소를 먼저 저장해 주세요.", code="email_no_recipient", status_code=400)
    if await tests_sent_today(db, user_id) >= settings.email_test_daily_limit:
        raise AppError(f"테스트 메일은 하루 {settings.email_test_daily_limit}번까지 보낼 수 있어요.",
                       code="email_test_limit", status_code=429)
    inputs = await gather_reminder_inputs(db, lms, user_id)
    message = email_service.build_daily_email(inputs, base_url=base_url, to=prefs.recipient)
    await email_service.send(settings, message, prefs.recipient, test=True)
    await db.table("notification_logs").insert({
        "user_id": str(user_id), "kind": TEST_KIND, "ref_key": dt.datetime.now(dt.UTC).isoformat(),
        "title": message.subject[:200], "body": "테스트 메일", "delivered": 1,
    }).execute()
    return prefs.recipient


async def notify_user(
    db: AsyncClient, settings: Settings, lms: LmsClient, user_id: UUID, *, to: str, base_url: str
) -> str:
    """오늘 보낼 알림이 있으면 메일 한 통을 보낸다. 결과: 'sent' | 'skipped' | 'failed'"""
    from backend.services import email_service

    if await _already_notified_today(db, user_id):
        return "skipped"
    inputs = await gather_reminder_inputs(db, lms, user_id)
    reminder = choose_reminder(inputs)
    if reminder is None:
        return "skipped"
    log_id = await _claim(db, user_id, reminder)  # 먼저 기록해 두 번 보내지 않게 한다
    if log_id is None:
        return "skipped"
    try:
        await email_service.send(settings, email_service.build_daily_email(inputs, base_url=base_url, to=to), to)
    except AppError as exc:
        logger.warning("메일 알림 실패: %s", exc.code)
        return "failed"
    await db.table("notification_logs").update({"delivered": 1}).eq("id", log_id).execute()
    return "sent"


async def run_daily(db: AsyncClient, settings: Settings, lms: LmsClient, *, base_url: str) -> CronSummary:
    """매일 Cron: LMS 연결 학생 자동 동기화 + 메일 알림을 켠 학생에게 메일."""
    started = time.monotonic()
    linked_res = await db.table("users").select("id,lms_student_id,lms_token_enc").execute()
    # 학교 계정으로 연결해 학생 전용 토큰이 있는 사용자만 (각자 자기 토큰으로 동기화)
    linked = {UUID(r["id"]) for r in linked_res.data or [] if r.get("lms_student_id") and r.get("lms_token_enc")}
    recipients = await email_recipients(db) if settings.email_enabled else {}
    users = sorted(linked | set(recipients), key=str)

    counts = {"synced": 0, "imported": 0, "sent": 0, "skipped": 0, "failed": 0}
    semaphore = asyncio.Semaphore(CONCURRENCY)

    async def process(user_id: UUID) -> None:
        async with semaphore:
            remaining = TIME_BUDGET_SECONDS - (time.monotonic() - started)
            if remaining <= 0:
                counts["skipped"] += 1
                return
            try:
                # 한 사람이 오래 걸려도 전체가 Vercel 제한 시간(60초)을 넘기지 않게 남은 시간만큼만 기다린다
                async with asyncio.timeout(remaining):
                    # 알림이 더 중요하므로 먼저 보내고, 자료 동기화는 그다음에 한다
                    if user_id in recipients:
                        counts[await notify_user(db, settings, lms, user_id, to=recipients[user_id],
                                                 base_url=base_url)] += 1
                    if user_id in linked:
                        result = await lms_link_service.sync_user(db, settings, lms, user_id, max_imports=3)
                        counts["synced"] += int(result.linked)
                        counts["imported"] += len(result.imported_titles)
            except TimeoutError:
                logger.warning("일일 처리 시간 초과 (남은 시간 %.1f초)", remaining)
                counts["skipped"] += 1
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
