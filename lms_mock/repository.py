"""lms_* 테이블 접근 계층 (secret 키 사용, 가상 LMS 서버 내부 전용)."""

import datetime as dt
import logging
from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends
from supabase import AsyncClient, AsyncClientOptions, acreate_client

from lms_mock.auth import LmsSettingsDep
from lms_mock.errors import LmsError, not_found
from lms_mock.schemas import (
    Course,
    CourseRecord,
    CreditSummary,
    GraduationRequirement,
    Student,
    Transcript,
)

logger = logging.getLogger(__name__)


async def get_lms_db(settings: LmsSettingsDep) -> AsyncClient:
    return await acreate_client(
        settings.supabase_url,
        settings.supabase_secret_key.get_secret_value(),
        options=AsyncClientOptions(auto_refresh_token=False, persist_session=False),
    )


LmsDbDep = Annotated[AsyncClient, Depends(get_lms_db)]


def _db_error(action: str) -> LmsError:
    logger.exception("가상 LMS DB 오류: %s", action)
    return LmsError(502, "lms_db_error", f"가상 LMS 데이터베이스 {action}에 실패했습니다.")


async def get_student(db: AsyncClient, student_id: UUID) -> Student:
    try:
        res = (
            await db.table("lms_students")
            .select("id,student_no,name,department,admission_year")
            .eq("id", str(student_id))
            .limit(1)
            .execute()
        )
    except Exception:
        raise _db_error("학생 조회") from None
    if not res.data:
        raise not_found("학생")
    return Student.model_validate(res.data[0])


async def get_transcript(db: AsyncClient, student_id: UUID) -> Transcript:
    student = await get_student(db, student_id)
    try:
        history = (
            await db.table("lms_course_history")
            .select("course_code,semester,status,grade,credits,lms_courses(name,category,year_level,skills)")
            .eq("student_id", str(student_id))
            .order("semester")
            .execute()
        )
        summary = await db.rpc("lms_get_credit_summary", {"p_student_id": str(student_id)}).execute()
    except Exception:
        raise _db_error("성적표 조회") from None

    records: list[CourseRecord] = []
    for row in history.data or []:
        course: dict[str, Any] = row.get("lms_courses") or {}
        records.append(
            CourseRecord(
                course_code=row["course_code"],
                course_name=course.get("name", row["course_code"]),
                category=course.get("category", "major_elective"),
                year_level=course.get("year_level", 1),
                skills=course.get("skills") or [],
                semester=row["semester"],
                status=row["status"],
                grade=row.get("grade"),
                credits=row["credits"],
            )
        )

    return Transcript(
        student=student,
        records=records,
        credit_summary=[CreditSummary.model_validate(r) for r in summary.data or []],
        completed_credits=sum(r.credits for r in records if r.status == "completed"),
        in_progress_credits=sum(r.credits for r in records if r.status == "in_progress"),
        generated_at=dt.datetime.now(dt.UTC),
    )


async def list_courses(db: AsyncClient, category: str | None = None) -> list[Course]:
    try:
        query = db.table("lms_courses").select("code,name,credits,category,year_level,skills,description").order("code")
        if category:
            query = query.eq("category", category)
        res = await query.execute()
    except Exception:
        raise _db_error("과목 조회") from None
    return [Course.model_validate(r) for r in res.data or []]


async def get_graduation_requirement(db: AsyncClient, department: str) -> GraduationRequirement:
    try:
        res = (
            await db.table("lms_graduation_requirements")
            .select("department,total_credits,major_required_credits,major_elective_credits,"
                    "general_credits,required_course_codes,note")
            .eq("department", department)
            .limit(1)
            .execute()
        )
    except Exception:
        raise _db_error("졸업 요건 조회") from None
    if not res.data:
        raise not_found(f"'{department}' 졸업 요건")
    return GraduationRequirement.model_validate(res.data[0])


async def seed_demo_student(db: AsyncClient, student_no: str, name: str) -> UUID:
    try:
        res = await db.rpc("lms_seed_demo_student", {"p_student_no": student_no, "p_name": name}).execute()
    except Exception:
        raise _db_error("시연 학생 생성") from None
    if not res.data:
        raise LmsError(502, "lms_db_error", "시연 학생 ID를 받지 못했습니다.")
    return UUID(str(res.data))


# ---------------------------------------------------------------------
# 학교 계정 로그인 · OAuth 2.0 (코드·토큰은 해시만 저장)
# ---------------------------------------------------------------------
async def find_login(db: AsyncClient, student_no: str) -> dict[str, Any] | None:
    try:
        res = (
            await db.table("lms_students")
            .select("id,student_no,name,password_hash")
            .eq("student_no", student_no)
            .limit(1)
            .execute()
        )
    except Exception:
        raise _db_error("학생 로그인 조회") from None
    return res.data[0] if res.data else None


async def student_no_exists(db: AsyncClient, student_no: str) -> bool:
    return await find_login(db, student_no) is not None


async def set_password(db: AsyncClient, student_id: UUID, password_hash: str) -> None:
    try:
        await db.table("lms_students").update({"password_hash": password_hash}).eq("id", str(student_id)).execute()
    except Exception:
        raise _db_error("학교 비밀번호 저장") from None


async def save_code(db: AsyncClient, row: dict[str, Any]) -> None:
    try:
        await db.table("lms_oauth_codes").insert(row).execute()
    except Exception:
        raise _db_error("일회용 코드 저장") from None


async def consume_code(db: AsyncClient, code_hash: str) -> dict[str, Any] | None:
    """일회용 코드를 '사용됨'으로 바꾸면서 내용을 돌려준다. 이미 썼거나 만료됐으면 None."""
    try:
        res = await db.rpc("lms_consume_oauth_code", {"p_code_hash": code_hash}).execute()
    except Exception:
        raise _db_error("일회용 코드 확인") from None
    rows = res.data or []
    return rows[0] if rows else None


async def save_token(db: AsyncClient, row: dict[str, Any]) -> None:
    try:
        await db.table("lms_access_tokens").insert(row).execute()
    except Exception:
        raise _db_error("토큰 저장") from None


async def find_token(db: AsyncClient, token_hash: str) -> dict[str, Any] | None:
    """사용할 수 있는 토큰 (폐기·만료되지 않은 것)만."""
    try:
        res = (
            await db.table("lms_access_tokens")
            .select("student_id,client_id,scope,expires_at")
            .eq("token_hash", token_hash)
            .is_("revoked_at", "null")
            .gt("expires_at", dt.datetime.now(dt.UTC).isoformat())
            .limit(1)
            .execute()
        )
    except Exception:
        raise _db_error("토큰 확인") from None
    return res.data[0] if res.data else None


async def revoke_token(db: AsyncClient, token_hash: str, client_id: str) -> None:
    try:
        await (
            db.table("lms_access_tokens")
            .update({"revoked_at": dt.datetime.now(dt.UTC).isoformat()})
            .eq("token_hash", token_hash)
            .eq("client_id", client_id)
            .is_("revoked_at", "null")
            .execute()
        )
    except Exception:
        raise _db_error("토큰 폐기") from None


async def list_assignments_for_courses(db: AsyncClient, course_codes: list[str]) -> list[dict[str, Any]]:
    if not course_codes:
        return []
    try:
        res = (
            await db.table("lms_assignments")
            .select("id,course_code,material_id,title,description,due_at,created_at")
            .in_("course_code", course_codes)
            .order("due_at")
            .execute()
        )
    except Exception:
        raise _db_error("과제 조회") from None
    return list(res.data or [])


async def submissions_of(db: AsyncClient, student_id: UUID) -> dict[str, str]:
    """{assignment_id: submitted_at}"""
    try:
        res = (
            await db.table("lms_assignment_submissions")
            .select("assignment_id,submitted_at")
            .eq("student_id", str(student_id))
            .execute()
        )
    except Exception:
        raise _db_error("제출 기록 조회") from None
    return {str(r["assignment_id"]): str(r["submitted_at"]) for r in res.data or []}


async def mark_submitted(db: AsyncClient, student_id: UUID, assignment_id: str) -> dt.datetime:
    submitted_at = dt.datetime.now(dt.UTC)
    try:
        await db.table("lms_assignment_submissions").upsert(
            {
                "student_id": str(student_id),
                "assignment_id": assignment_id,
                "submitted_at": submitted_at.isoformat(),
            },
            on_conflict="student_id,assignment_id",
        ).execute()
    except Exception:
        raise _db_error("과제 제출") from None
    return submitted_at


async def list_materials_for_courses(db: AsyncClient, course_codes: list[str]) -> list[dict[str, Any]]:
    if not course_codes:
        return []
    try:
        res = (
            await db.table("lms_materials")
            .select("id,course_code,week_no,title,content,published_at")
            .in_("course_code", course_codes)
            .order("course_code")
            .order("week_no")
            .execute()
        )
    except Exception:
        raise _db_error("강의 자료 조회") from None
    return list(res.data or [])
