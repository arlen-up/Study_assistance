"""도우미 사용자 ↔ 가상 LMS 학생 연결, 성적 동기화, 강의 자료 가져오기.

도우미는 LMS 데이터를 직접 읽지 않고 항상 LmsClient(HTTP API)를 거친다.
users 테이블에는 LMS 학생 ID와 동기화 캐시(이수 학점, 동기화 시각)만 저장한다.
"""

import datetime as dt
import hashlib
import logging
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field
from supabase import AsyncClient

from backend.core.config import Settings
from backend.core.errors import AppError, BadRequestError, UpstreamError
from backend.integrations.lms_client import LmsAssignment, LmsClient, LmsMaterialSummary, LmsSubmission, LmsTranscript
from backend.schemas.models import AuthUser, DocumentIngestResponse, LmsLinkStatus
from backend.services.document_service import copy_document, ingest_document, parse_plain_text
from backend.services.embedding_service import EmbeddingService

logger = logging.getLogger(__name__)

MAX_LINK_ATTEMPTS = 5
LMS_SOURCE_PREFIX = "lms://materials/"


class LmsNotLinkedError(BadRequestError):
    code = "lms_not_linked"

    def __init__(self) -> None:
        super().__init__("가상 LMS 계정을 먼저 연결해 주세요.")


# ---------------------------------------------------------------------
# 연결 정보
# ---------------------------------------------------------------------
async def _get_user_row(db: AsyncClient, user_id: UUID) -> dict[str, Any]:
    try:
        res = (
            await db.table("users")
            .select("id,lms_student_id,lms_synced_at,completed_credits")
            .eq("id", str(user_id))
            .limit(1)
            .execute()
        )
    except Exception:
        logger.exception("users 조회 실패")
        raise UpstreamError("사용자 정보를 불러오지 못했습니다.") from None
    if not res.data:
        raise UpstreamError("사용자 프로필이 없습니다. 다시 로그인해 주세요.", code="profile_missing")
    return res.data[0]


async def get_student_id(db: AsyncClient, user_id: UUID) -> UUID | None:
    row = await _get_user_row(db, user_id)
    return UUID(row["lms_student_id"]) if row.get("lms_student_id") else None


async def require_student_id(db: AsyncClient, user_id: UUID) -> UUID:
    student_id = await get_student_id(db, user_id)
    if student_id is None:
        raise LmsNotLinkedError()
    return student_id


def demo_student_no(user_id: UUID, attempt: int = 0) -> str:
    """사용자마다 고정된 시연용 학번 (2025 + 6자리). 충돌 시 attempt를 바꿔 재시도."""
    digest = hashlib.sha256(f"{user_id}:{attempt}".encode()).hexdigest()
    return f"2025{int(digest, 16) % 1_000_000:06d}"


async def _student_taken_by_other(db: AsyncClient, student_id: UUID, user_id: UUID) -> bool:
    res = (
        await db.table("users")
        .select("id")
        .eq("lms_student_id", str(student_id))
        .neq("id", str(user_id))
        .limit(1)
        .execute()
    )
    return bool(res.data)


# ---------------------------------------------------------------------
# 동기화
# ---------------------------------------------------------------------
async def sync_transcript(db: AsyncClient, lms: LmsClient, user_id: UUID, student_id: UUID) -> LmsTranscript:
    """LMS 성적표를 받아 users.completed_credits(캐시)와 동기화 시각을 갱신한다."""
    transcript = await lms.get_transcript(student_id)
    try:
        await db.table("users").update(
            {
                "completed_credits": transcript.completed_credits,
                "lms_synced_at": dt.datetime.now(dt.UTC).isoformat(),
            }
        ).eq("id", str(user_id)).execute()
    except Exception:
        logger.exception("users 동기화 저장 실패")
        raise UpstreamError("LMS 동기화 결과를 저장하지 못했습니다.") from None
    return transcript


def _status_from(transcript: LmsTranscript, synced_at: dt.datetime | None) -> LmsLinkStatus:
    s = transcript.student
    return LmsLinkStatus(
        linked=True,
        student_id=s.id,
        student_no=s.student_no,
        name=s.name,
        department=s.department,
        completed_credits=transcript.completed_credits,
        in_progress_credits=transcript.in_progress_credits,
        synced_at=synced_at,
    )


async def link_demo_account(db: AsyncClient, lms: LmsClient, user: AuthUser) -> LmsLinkStatus:
    """시연용 LMS 학생을 만들고(이미 있으면 재사용) 현재 사용자와 연결한다."""
    existing = await get_student_id(db, user.id)
    if existing is not None:
        transcript = await sync_transcript(db, lms, user.id, existing)
        return _status_from(transcript, dt.datetime.now(dt.UTC))

    display_name = (user.email or "학생").split("@")[0][:50] or "학생"
    for attempt in range(MAX_LINK_ATTEMPTS):
        student_id = await lms.create_demo_student(demo_student_no(user.id, attempt), display_name)
        if await _student_taken_by_other(db, student_id, user.id):
            continue  # 드물게 학번이 겹치면 다른 학번으로 재시도
        try:
            await db.table("users").update({"lms_student_id": str(student_id)}).eq("id", str(user.id)).execute()
        except Exception:
            logger.exception("LMS 연결 저장 실패")
            raise UpstreamError("LMS 연결 정보를 저장하지 못했습니다.") from None
        transcript = await sync_transcript(db, lms, user.id, student_id)
        return _status_from(transcript, dt.datetime.now(dt.UTC))

    raise AppError("시연용 학번을 배정하지 못했습니다. 다시 시도해 주세요.", code="lms_link_failed", status_code=409)


async def get_status(db: AsyncClient, lms: LmsClient, user_id: UUID) -> LmsLinkStatus:
    row = await _get_user_row(db, user_id)
    if not row.get("lms_student_id"):
        return LmsLinkStatus(linked=False)
    transcript = await lms.get_transcript(UUID(row["lms_student_id"]))
    synced_at = dt.datetime.fromisoformat(row["lms_synced_at"]) if row.get("lms_synced_at") else None
    return _status_from(transcript, synced_at)


# ---------------------------------------------------------------------
# 과제 · 강의 자료
# ---------------------------------------------------------------------
async def list_assignments(db: AsyncClient, lms: LmsClient, user_id: UUID) -> list[LmsAssignment]:
    return await lms.list_open_assignments(await require_student_id(db, user_id))


async def submit_assignment(db: AsyncClient, lms: LmsClient, user_id: UUID, assignment_id: str) -> LmsSubmission:
    return await lms.submit_assignment(await require_student_id(db, user_id), assignment_id)


async def list_materials(db: AsyncClient, lms: LmsClient, user_id: UUID) -> list[LmsMaterialSummary]:
    return await lms.list_student_materials(await require_student_id(db, user_id))


async def imported_material_ids(db: AsyncClient, user_id: UUID) -> set[str]:
    """이 사용자가 이미 RAG 인덱스로 가져온 LMS 자료 ID 집합 (교안은 계정별로 나뉜다)."""
    try:
        res = (
            await db.table("documents")
            .select("source_url")
            .eq("created_by", str(user_id))
            .like("source_url", f"{LMS_SOURCE_PREFIX}%")
            .execute()
        )
    except Exception:
        logger.exception("가져온 자료 조회 실패")
        return set()
    return {str(r["source_url"]).removeprefix(LMS_SOURCE_PREFIX) for r in res.data or [] if r.get("source_url")}


async def import_material(
    db: AsyncClient, settings: Settings, lms: LmsClient, user_id: UUID, material_id: str
) -> DocumentIngestResponse:
    """LMS 강의 자료를 가져와 이 사용자의 RAG 인덱스(documents/document_chunks)에 저장한다.

    교안은 계정별로 나뉜다. 같은 자료를 다른 학생이 이미 가져왔고 내용이 지금 LMS 자료와 같으면,
    AI 임베딩을 다시 만들지 않고 복사한다 (학교 강의 자료라 개인 정보가 아니다).
    """
    student_id = await require_student_id(db, user_id)  # 연결된 학생만 가져오기 허용
    source_url = f"{LMS_SOURCE_PREFIX}{material_id}"

    existing = await _own_material_document(db, user_id, source_url)
    if existing:
        row = existing
        count = (
            await db.table("document_chunks").select("id", count="exact").eq("document_id", row["id"]).execute()
        ).count or 0
        return DocumentIngestResponse(
            document_id=row["id"],
            title=row["title"],
            chunk_count=count,
            char_count=len(row.get("raw_text") or ""),
            already_imported=True,
        )

    material = await lms.get_material(student_id, material_id)  # 수강 중인 과목 자료만 받을 수 있다
    parsed = parse_plain_text(material.content)
    template = await _same_material_elsewhere(db, source_url, parsed.full_text)
    if template is not None:
        try:
            return await copy_document(db, template, created_by=user_id)
        except AppError:
            logger.warning("교안 복사 실패 → 새로 임베딩해서 가져옴 (%s)", material_id)
    week = f"{material.week_no}주차 " if material.week_no else ""
    return await ingest_document(
        db,
        EmbeddingService(settings),
        title=f"[{material.course_name}] {week}{material.title}",
        parsed=parsed,
        created_by=user_id,
        course_name=material.course_name,
        week_no=material.week_no,
        keywords=[],
        source_url=source_url,
    )


async def _own_material_document(db: AsyncClient, user_id: UUID, source_url: str) -> dict[str, Any] | None:
    try:
        res = (
            await db.table("documents")
            .select("id,title,raw_text")
            .eq("created_by", str(user_id))
            .eq("source_url", source_url)
            .limit(1)
            .execute()
        )
    except Exception:
        logger.exception("documents 중복 확인 실패")
        raise UpstreamError("교안 정보를 확인하지 못했습니다.") from None
    return res.data[0] if res.data else None


async def _same_material_elsewhere(db: AsyncClient, source_url: str, full_text: str) -> str | None:
    """다른 계정이 이미 가져온 같은 LMS 자료 중 내용이 똑같은 것 (복사 원본). 없으면 None."""
    try:
        res = (
            await db.table("documents")
            .select("id,raw_text")
            .eq("source_url", source_url)
            .order("created_at", desc=True)
            .limit(5)
            .execute()
        )
    except Exception:
        logger.warning("복사할 LMS 자료 찾기 실패 → 새로 임베딩", exc_info=True)
        return None
    return next((row["id"] for row in res.data or [] if (row.get("raw_text") or "") == full_text), None)


# ---------------------------------------------------------------------
# 자동 동기화 (매일 Cron · '지금 동기화' 버튼)
# ---------------------------------------------------------------------
class SyncResult(BaseModel):
    linked: bool
    completed_credits: int = 0
    imported_titles: list[str] = Field(default_factory=list)
    open_assignments: int = 0


async def sync_user(
    db: AsyncClient, settings: Settings, lms: LmsClient, user_id: UUID, max_imports: int = 5
) -> SyncResult:
    """성적 동기화 + 코스모스에 새로 올라온 강의 자료를 RAG 인덱스로 자동 가져오기."""
    student_id = await get_student_id(db, user_id)
    if student_id is None:
        return SyncResult(linked=False)
    transcript = await sync_transcript(db, lms, user_id, student_id)
    materials = await lms.list_student_materials(student_id)
    done = await imported_material_ids(db, user_id)
    imported: list[str] = []
    for material in [m for m in materials if m.id not in done][:max_imports]:
        try:
            result = await import_material(db, settings, lms, user_id, material.id)
        except AppError as exc:
            logger.warning("자료 자동 가져오기 실패 (%s): %s", material.id, exc.code)
            continue
        if not result.already_imported:
            imported.append(result.title)
    assignments = await lms.list_open_assignments(student_id)
    return SyncResult(
        linked=True,
        completed_credits=transcript.completed_credits,
        imported_titles=imported,
        open_assignments=len(assignments),
    )
