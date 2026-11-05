"""LMS 게이트웨이 — 도우미가 학교 시스템과 이야기하는 유일한 창구.

  🏛 학사정보시스템 (AcademicClient)  : 학생 · 전체 성적표 · 졸업 요건 · 과목 카탈로그
  🏫 코스모스 LMS (CoursemosAdapter)  : 과제/마감일 · 과제 제출 · 강의 자료   ← Moodle Web Services 규격

연결 대상
- 기본: 같은 앱 안의 가상 시스템(lms_mock)을 ASGI로 직접 호출 (HTTP 형식은 그대로, 네트워크 없음)
- LMS_API_BASE_URL  : 외부 학사정보 API 주소
- MOODLE_BASE_URL   : 외부 코스모스(Moodle) 주소  ← 진짜 코스모스로 바꿀 때 여기만 교체

토큰: 지금은 가상 학사정보 API가 학생별 Moodle 토큰을 발급한다.
      진짜 코스모스에서는 학교가 발급한 학생별 토큰을 (암호화해) 저장해 쓰는 방식으로 _ws_token만 교체한다.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any, TypeVar
from urllib.parse import quote
from uuid import UUID

import httpx
from fastapi import Depends
from pydantic import BaseModel, ValidationError

from backend.core.config import Settings
from backend.core.errors import AppError, NotFoundError, UpstreamError
from backend.core.supabase_client import SettingsDep
from backend.integrations.coursemos import CoursemosAdapter
from backend.integrations.lms_models import (  # noqa: F401  (다른 모듈이 여기서 import)
    CATEGORY_LABELS,
    CourseCategory,
    LmsAssignment,
    LmsCourse,
    LmsCourseRecord,
    LmsCreditSummary,
    LmsGraduationRequirement,
    LmsMaterial,
    LmsMaterialSummary,
    LmsStudent,
    LmsSubmission,
    LmsTranscript,
)
from backend.integrations.moodle_client import MoodleClient

logger = logging.getLogger(__name__)

API_KEY_HEADER = "X-LMS-API-Key"
INTERNAL_HOST = "http://lms.internal"

M = TypeVar("M", bound=BaseModel)


class LmsNotConfiguredError(AppError):
    status_code = 503
    code = "lms_not_configured"


class _Items(BaseModel):
    items: list[dict[str, Any]]


class _DemoStudent(BaseModel):
    student_id: UUID


class _WsToken(BaseModel):
    token: str


# ---------------------------------------------------------------------
# 🏛 학사정보시스템 클라이언트
# ---------------------------------------------------------------------
class AcademicClient:
    def __init__(self, http: httpx.AsyncClient, api_key: str | None) -> None:
        self._http = http
        self._api_key = api_key

    async def _request(self, method: str, path: str, *, json: dict[str, Any] | None = None,
                       params: dict[str, Any] | None = None) -> Any:
        if not self._api_key:
            raise LmsNotConfiguredError("가상 LMS 연동 키(LMS_API_KEY)가 설정되지 않았습니다.")
        try:
            response = await self._http.request(
                method, path, json=json, params=params, headers={API_KEY_HEADER: self._api_key}
            )
        except httpx.HTTPError as exc:
            logger.warning("학사정보 연결 실패: %s %s (%s)", method, path, type(exc).__name__)
            raise UpstreamError("학사정보시스템에 연결할 수 없습니다.", code="lms_unreachable") from None

        if response.is_success:
            try:
                return response.json()
            except ValueError:
                raise UpstreamError("학사정보 응답 형식이 올바르지 않습니다.", code="lms_bad_response") from None

        try:
            error = response.json().get("error", {})
        except ValueError:
            error = {}
        message = str(error.get("message") or f"학사정보 요청 실패 (HTTP {response.status_code})")
        if response.status_code == 404:
            raise NotFoundError(message, code="lms_not_found")
        if response.status_code in (401, 403):
            logger.error("학사정보 인증 실패 — LMS_API_KEY 확인 필요")
            raise UpstreamError("학사정보 인증에 실패했습니다. 서버 설정을 확인해 주세요.", code="lms_auth_failed")
        if response.status_code == 503:
            raise LmsNotConfiguredError(message)
        raise UpstreamError(message, code="lms_error")

    @staticmethod
    def _parse(model: type[M], data: Any) -> M:
        try:
            return model.model_validate(data)
        except ValidationError:
            logger.exception("학사정보 응답 검증 실패: %s", model.__name__)
            raise UpstreamError("학사정보 응답 형식이 예상과 다릅니다.", code="lms_bad_response") from None

    async def get_student(self, student_id: UUID) -> LmsStudent:
        return self._parse(LmsStudent, await self._request("GET", f"/students/{student_id}"))

    async def get_transcript(self, student_id: UUID) -> LmsTranscript:
        return self._parse(LmsTranscript, await self._request("GET", f"/students/{student_id}/transcript"))

    async def list_courses(self, category: CourseCategory | None = None) -> list[LmsCourse]:
        data = await self._request("GET", "/courses", params={"category": category} if category else None)
        return [self._parse(LmsCourse, item) for item in self._parse(_Items, data).items]

    async def get_graduation_requirement(self, department: str) -> LmsGraduationRequirement:
        data = await self._request("GET", f"/graduation-requirements/{quote(department, safe='')}")
        return self._parse(LmsGraduationRequirement, data)

    async def create_demo_student(self, student_no: str, name: str) -> UUID:
        data = await self._request("POST", "/demo/students", json={"student_no": student_no, "name": name})
        return self._parse(_DemoStudent, data).student_id

    async def issue_ws_token(self, student_id: UUID) -> str:
        return self._parse(_WsToken, await self._request("GET", f"/students/{student_id}/ws-token")).token


# ---------------------------------------------------------------------
# 게이트웨이
# ---------------------------------------------------------------------
class LmsClient:
    """학사정보 + 코스모스를 하나의 창구로 묶는다 (서비스·MCP 도구는 이 클래스만 사용)."""

    def __init__(self, academic: AcademicClient, moodle_http: httpx.AsyncClient) -> None:
        self.academic = academic
        self._moodle_http = moodle_http
        self._tokens: dict[UUID, str] = {}

    @classmethod
    @asynccontextmanager
    async def connect(cls, settings: Settings) -> AsyncIterator["LmsClient"]:
        api_key = settings.lms_api_key.get_secret_value() if settings.lms_api_key else None
        timeout = httpx.Timeout(settings.lms_timeout_seconds)
        transport: httpx.AsyncBaseTransport | None = None
        if not settings.lms_api_base_url or not settings.moodle_base_url:
            from lms_mock.app import app as lms_app  # 같은 배포 안의 가상 시스템

            transport = httpx.ASGITransport(app=lms_app)

        def client(base_url: str | None, internal_base: str) -> httpx.AsyncClient:
            if base_url:
                return httpx.AsyncClient(base_url=base_url, timeout=timeout, follow_redirects=False)
            return httpx.AsyncClient(transport=transport, base_url=internal_base, timeout=timeout)

        academic_http = client(settings.lms_api_base_url, f"{INTERNAL_HOST}/api")
        moodle_http = client(settings.moodle_base_url, INTERNAL_HOST)
        try:
            yield cls(AcademicClient(academic_http, api_key), moodle_http)
        finally:
            await academic_http.aclose()
            await moodle_http.aclose()

    # ---------------- 🏛 학사정보 ----------------
    async def get_student(self, student_id: UUID) -> LmsStudent:
        return await self.academic.get_student(student_id)

    async def get_transcript(self, student_id: UUID) -> LmsTranscript:
        return await self.academic.get_transcript(student_id)

    async def list_courses(self, category: CourseCategory | None = None) -> list[LmsCourse]:
        return await self.academic.list_courses(category)

    async def get_graduation_requirement(self, department: str) -> LmsGraduationRequirement:
        return await self.academic.get_graduation_requirement(department)

    async def create_demo_student(self, student_no: str, name: str) -> UUID:
        return await self.academic.create_demo_student(student_no, name)

    # ---------------- 🏫 코스모스 ----------------
    async def _ws_token(self, student_id: UUID) -> str:
        if student_id not in self._tokens:
            self._tokens[student_id] = await self.academic.issue_ws_token(student_id)
        return self._tokens[student_id]

    async def coursemos(self, student_id: UUID) -> CoursemosAdapter:
        return CoursemosAdapter(MoodleClient(self._moodle_http, await self._ws_token(student_id)))

    async def list_open_assignments(self, student_id: UUID) -> list[LmsAssignment]:
        return await (await self.coursemos(student_id)).list_open_assignments()

    async def submit_assignment(self, student_id: UUID, assignment_id: str) -> LmsSubmission:
        return await (await self.coursemos(student_id)).submit_assignment(assignment_id)

    async def list_student_materials(self, student_id: UUID) -> list[LmsMaterialSummary]:
        return await (await self.coursemos(student_id)).list_materials()

    async def get_material(self, student_id: UUID, material_id: str) -> LmsMaterial:
        return await (await self.coursemos(student_id)).get_material(material_id)


async def get_lms_client(settings: SettingsDep) -> AsyncIterator[LmsClient]:
    async with LmsClient.connect(settings) as client:
        yield client


LmsDep = Annotated[LmsClient, Depends(get_lms_client)]
