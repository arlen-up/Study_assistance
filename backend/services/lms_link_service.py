"""도우미 사용자 ↔ 학교 계정 연결 (OAuth 2.0 + PKCE), 성적 동기화, 강의 자료 가져오기.

연결: 학생이 학교(가상 코스모스)에 직접 로그인해 동의 → 학교가 그 학생 전용 토큰 발급
      → users.lms_token_enc 에 암호화해 저장 (학교 비밀번호는 도우미가 받지 않는다)
사용: 요청마다 토큰을 복호화해 LmsClient 에 등록하고, 그 학생의 정보만 읽는다.
해제: 학교 쪽 토큰을 폐기하고 도우미 쪽 연결 정보를 지운다.
도우미는 LMS 데이터를 직접 읽지 않고 항상 LmsClient(HTTP API)를 거친다.
"""

import datetime as dt
import logging
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field
from supabase import AsyncClient

from backend.core.config import Settings
from backend.core.errors import AppError, BadRequestError, UpstreamError
from backend.core import crypto
from backend.integrations.lms_client import (
    LmsAssignment,
    LmsClient,
    LmsMaterialSummary,
    LmsReconnectError,
    LmsSubmission,
    LmsTranscript,
)
from backend.schemas.models import DocumentIngestResponse, LmsLinkStatus
from backend.services.document_service import copy_document, ingest_document, parse_plain_text
from backend.services.embedding_service import EmbeddingService

logger = logging.getLogger(__name__)

LMS_SOURCE_PREFIX = "lms://materials/"
REQUESTED_SCOPE = "lms.read academic.read lms.submit"  # 필요한 것만: 읽기 + 과제 '제출 완료' 표시


class LmsNotLinkedError(BadRequestError):
    code = "lms_not_linked"

    def __init__(self) -> None:
        super().__init__("학교 계정을 먼저 연결해 주세요.")


# ---------------------------------------------------------------------
# 연결 정보
# ---------------------------------------------------------------------
async def _get_user_row(db: AsyncClient, user_id: UUID) -> dict[str, Any]:
    try:
        res = (
            await db.table("users")
            .select("id,lms_student_id,lms_token_enc,lms_synced_at,completed_credits")
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


def _activate(lms: LmsClient, row: dict[str, Any]) -> UUID | None:
    """저장된 토큰을 복호화해 이번 요청의 LmsClient 에 등록한다. 토큰이 없거나 풀 수 없으면 None."""
    if not row.get("lms_student_id") or not row.get("lms_token_enc"):
        return None  # 연결 안 됨, 또는 예전 방식(토큰 없음)으로 연결된 계정 → 다시 연결 필요
    token = crypto.decrypt(lms.settings, row["lms_token_enc"])
    if token is None:
        logger.warning("저장된 학교 토큰을 복호화하지 못함 (서버 키 변경?) → 다시 연결 필요")
        return None
    student_id = UUID(row["lms_student_id"])
    lms.register_token(student_id, token)
    return student_id


async def get_student_id(db: AsyncClient, user_id: UUID, lms: LmsClient) -> UUID | None:
    """연결된 학생 ID. 이 사용자의 학생 전용 토큰도 lms 에 등록해, 이후 호출이 그 학생 정보만 읽게 한다."""
    return _activate(lms, await _get_user_row(db, user_id))


async def require_student_id(db: AsyncClient, user_id: UUID, lms: LmsClient) -> UUID:
    student_id = await get_student_id(db, user_id, lms)
    if student_id is None:
        raise LmsNotLinkedError()
    return student_id


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


async def connect_school_account(
    db: AsyncClient, lms: LmsClient, user_id: UUID, *, code: str, redirect_uri: str, code_verifier: str
) -> LmsLinkStatus:
    """학교가 돌려준 일회용 코드를 학생 전용 토큰으로 바꾸고, 이 사용자와 연결한다."""
    token = await lms.exchange_code(code=code, redirect_uri=redirect_uri, code_verifier=code_verifier)
    student_id = token.student_id
    if await _student_taken_by_other(db, student_id, user_id):
        await lms.revoke(token.access_token)
        raise BadRequestError("이 학교 계정은 이미 다른 LSSA 계정에 연결돼 있어요.", code="lms_already_linked")

    previous = await _get_user_row(db, user_id)
    if previous.get("lms_token_enc"):
        old = crypto.decrypt(lms.settings, previous["lms_token_enc"])
        if old:
            await lms.revoke(old)  # 다시 연결하면 예전 토큰은 폐기

    try:
        await db.table("users").update({
            "lms_student_id": str(student_id),
            "lms_token_enc": crypto.encrypt(lms.settings, token.access_token),
        }).eq("id", str(user_id)).execute()
    except Exception:
        logger.exception("학교 계정 연결 저장 실패")
        await lms.revoke(token.access_token)
        raise UpstreamError("학교 계정 연결 정보를 저장하지 못했습니다.") from None
    lms.register_token(student_id, token.access_token)
    transcript = await sync_transcript(db, lms, user_id, student_id)
    return _status_from(transcript, dt.datetime.now(dt.UTC))


async def disconnect_school_account(db: AsyncClient, lms: LmsClient, user_id: UUID) -> None:
    """학교 쪽 토큰을 폐기하고, 도우미 쪽 연결 정보(학생 ID·토큰·동기화 캐시)를 지운다."""
    row = await _get_user_row(db, user_id)
    if row.get("lms_token_enc"):
        token = crypto.decrypt(lms.settings, row["lms_token_enc"])
        if token:
            await lms.revoke(token)
    try:
        await db.table("users").update({
            "lms_student_id": None, "lms_token_enc": None, "lms_synced_at": None, "completed_credits": 0,
        }).eq("id", str(user_id)).execute()
    except Exception:
        logger.exception("학교 계정 연결 해제 저장 실패")
        raise UpstreamError("연결을 해제하지 못했습니다. 잠시 뒤 다시 시도해 주세요.") from None


async def get_status(db: AsyncClient, lms: LmsClient, user_id: UUID) -> LmsLinkStatus:
    row = await _get_user_row(db, user_id)
    student_id = _activate(lms, row)
    if student_id is None:
        return LmsLinkStatus(linked=False, reconnect=bool(row.get("lms_student_id")))
    try:
        transcript = await lms.get_transcript(student_id)
    except LmsReconnectError:
        return LmsLinkStatus(linked=False, reconnect=True)
    synced_at = dt.datetime.fromisoformat(row["lms_synced_at"]) if row.get("lms_synced_at") else None
    return _status_from(transcript, synced_at)


# ---------------------------------------------------------------------
# 과제 · 강의 자료
# ---------------------------------------------------------------------
async def list_assignments(db: AsyncClient, lms: LmsClient, user_id: UUID) -> list[LmsAssignment]:
    return await lms.list_open_assignments(await require_student_id(db, user_id, lms))


async def submit_assignment(db: AsyncClient, lms: LmsClient, user_id: UUID, assignment_id: str) -> LmsSubmission:
    return await lms.submit_assignment(await require_student_id(db, user_id, lms), assignment_id)


async def list_materials(db: AsyncClient, lms: LmsClient, user_id: UUID) -> list[LmsMaterialSummary]:
    return await lms.list_student_materials(await require_student_id(db, user_id, lms))


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
    student_id = await require_student_id(db, user_id, lms)  # 연결된 학생만 가져오기 허용
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
    student_id = await get_student_id(db, user_id, lms)
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
