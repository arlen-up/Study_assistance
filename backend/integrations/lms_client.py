"""LMS 게이트웨이 — 도우미가 학교 시스템과 이야기하는 유일한 창구.

  🏛 학사정보시스템 (AcademicClient)  : 학생 · 전체 성적표 · 졸업 요건 · 과목 카탈로그
  🏫 코스모스 LMS (CoursemosAdapter)  : 과제/마감일 · 과제 제출 · 강의 자료   ← Moodle Web Services 규격

연결 대상
- 기본: 같은 앱 안의 가상 시스템(lms_mock)을 ASGI로 직접 호출 (HTTP 형식은 그대로, 네트워크 없음)
- LMS_API_BASE_URL  : 외부 학사정보 API 주소
- MOODLE_BASE_URL   : 외부 코스모스(Moodle) 주소  ← 진짜 코스모스로 바꿀 때 여기만 교체

토큰: 학생이 학교 계정으로 로그인해 동의하면(OAuth 2.0 + PKCE) 학교가 그 학생 전용 토큰을 발급한다.
      도우미는 토큰을 암호화해 저장하고(users.lms_token_enc), 요청마다 register_token 으로 등록해 쓴다.
      학생 개인 정보(성적표·과제·자료)는 그 학생의 토큰으로만 읽는다. 서버 키로 남의 토큰을 받는 기능은 없다.
      서버 간 키(LMS_API_KEY)로는 과목 목록·졸업 요건 같은 학교 공통 정보만 읽는다.
"""

import hashlib
import hmac
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any, TypeVar
from urllib.parse import quote, urlencode
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


class LmsReconnectError(AppError):
    """토큰이 없거나 만료·폐기됨 → 학생이 학교 계정을 다시 연결해야 한다."""

    status_code = 400
    code = "lms_reconnect"


class SchoolToken(BaseModel):
    """학교가 발급한 학생 전용 토큰 (OAuth 2.0 토큰 응답)."""

    access_token: str
    token_type: str
    expires_in: int
    scope: str
    student_id: UUID
    student_no: str
    name: str


# ---------------------------------------------------------------------
# 🏛 학사정보시스템 클라이언트
# ---------------------------------------------------------------------
class AcademicClient:
    def __init__(self, http: httpx.AsyncClient, api_key: str | None) -> None:
        self._http = http
        self._api_key = api_key

    async def _request(self, method: str, path: str, *, json: dict[str, Any] | None = None,
                       params: dict[str, Any] | None = None, student_token: str | None = None) -> Any:
        if student_token is not None:
            headers = {"Authorization": f"Bearer {student_token}"}  # 학생 개인 정보: 그 학생의 토큰으로만
        elif self._api_key:
            headers = {API_KEY_HEADER: self._api_key}               # 학교 공통 정보
        else:
            raise LmsNotConfiguredError("가상 LMS 연동 키(LMS_API_KEY)가 설정되지 않았습니다.")
        try:
            response = await self._http.request(method, path, json=json, params=params, headers=headers)
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
        if student_token is not None and response.status_code in (401, 403):
            raise LmsReconnectError("학교 계정 연결이 만료됐거나 권한이 없어요. 학교 계정을 다시 연결해 주세요.")
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

    async def get_me(self, student_token: str) -> LmsStudent:
        return self._parse(LmsStudent, await self._request("GET", "/me", student_token=student_token))

    async def get_my_transcript(self, student_token: str) -> LmsTranscript:
        return self._parse(LmsTranscript, await self._request("GET", "/me/transcript", student_token=student_token))

    async def list_courses(self, category: CourseCategory | None = None) -> list[LmsCourse]:
        data = await self._request("GET", "/courses", params={"category": category} if category else None)
        return [self._parse(LmsCourse, item) for item in self._parse(_Items, data).items]

    async def get_graduation_requirement(self, department: str) -> LmsGraduationRequirement:
        data = await self._request("GET", f"/graduation-requirements/{quote(department, safe='')}")
        return self._parse(LmsGraduationRequirement, data)



# ---------------------------------------------------------------------
# 게이트웨이
# ---------------------------------------------------------------------
class LmsClient:
    """학사정보 + 코스모스를 하나의 창구로 묶는다 (서비스·MCP 도구는 이 클래스만 사용)."""

    def __init__(self, academic: AcademicClient, moodle_http: httpx.AsyncClient, settings: Settings) -> None:
        self.academic = academic
        self._moodle_http = moodle_http
        self._settings = settings
        self._tokens: dict[UUID, str] = {}  # 이번 요청에서 쓸 학생 전용 토큰 (register_token 으로만 채움)

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
            yield cls(AcademicClient(academic_http, api_key), moodle_http, settings)
        finally:
            await academic_http.aclose()
            await moodle_http.aclose()

    @property
    def settings(self) -> Settings:
        return self._settings

    # ---------------- 학생 전용 토큰 ----------------
    def register_token(self, student_id: UUID, token: str) -> None:
        self._tokens[student_id] = token

    def _token(self, student_id: UUID) -> str:
        token = self._tokens.get(student_id)
        if token is None:
            raise LmsReconnectError("학교 계정이 연결되지 않았어요. 학교 계정으로 다시 연결해 주세요.")
        return token

    # ---------------- 🔑 학교 계정 연결 (OAuth 2.0 + PKCE) ----------------
    def authorize_url(self, *, redirect_uri: str, state: str, code_challenge: str, scope: str) -> str:
        """학생 브라우저를 보낼 학교 로그인·동의 화면 주소."""
        base = self._settings.moodle_base_url or "/lms"  # 가상 코스모스는 같은 사이트의 /lms 에 있다
        query = urlencode({
            "response_type": "code",
            "client_id": self._settings.lms_oauth_client_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "scope": scope,
        })
        return f"{base}/oauth/authorize?{query}"

    def _client_secret(self) -> str:
        if self._settings.lms_oauth_client_secret is not None:
            return self._settings.lms_oauth_client_secret.get_secret_value()
        if self._settings.lms_api_key is None:
            raise LmsNotConfiguredError("학교 계정 연결 설정(LMS_OAUTH_CLIENT_SECRET 또는 LMS_API_KEY)이 없습니다.")
        # 가상 학교: 학교 쪽(lms_mock.security.client_secret)과 같은 방법으로 만든 시연용 값
        key = self._settings.lms_api_key.get_secret_value().encode()
        return hmac.new(key, f"oauth-client:{self._settings.lms_oauth_client_id}".encode(), hashlib.sha256).hexdigest()

    async def exchange_code(self, *, code: str, redirect_uri: str, code_verifier: str) -> SchoolToken:
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": self._settings.lms_oauth_client_id,
            "client_secret": self._client_secret(),
            "code_verifier": code_verifier,
        }
        try:
            response = await self._moodle_http.post("/oauth/token", data=data)
        except httpx.HTTPError:
            raise UpstreamError("학교 시스템에 연결할 수 없습니다.", code="lms_unreachable") from None
        if not response.is_success:
            logger.warning("학교 토큰 발급 실패: HTTP %s", response.status_code)
            raise LmsReconnectError("학교 계정 연결에 실패했어요. 처음부터 다시 시도해 주세요.")
        try:
            return SchoolToken.model_validate(response.json())
        except (ValueError, ValidationError):
            raise UpstreamError("학교 토큰 응답 형식이 올바르지 않습니다.", code="lms_bad_response") from None

    async def revoke(self, token: str) -> None:
        """학교 쪽 토큰 폐기 (RFC 7009). 실패해도 도우미 쪽 연결은 끊는다."""
        data = {"token": token, "client_id": self._settings.lms_oauth_client_id, "client_secret": self._client_secret()}
        try:
            response = await self._moodle_http.post("/oauth/revoke", data=data)
            if not response.is_success:
                logger.warning("학교 토큰 폐기 실패: HTTP %s", response.status_code)
        except httpx.HTTPError:
            logger.warning("학교 토큰 폐기 요청 실패 (네트워크)")

    # ---------------- 🏛 학사정보 ----------------
    async def get_student(self, student_id: UUID) -> LmsStudent:
        return await self.academic.get_me(self._token(student_id))

    async def get_transcript(self, student_id: UUID) -> LmsTranscript:
        return await self.academic.get_my_transcript(self._token(student_id))

    async def list_courses(self, category: CourseCategory | None = None) -> list[LmsCourse]:
        return await self.academic.list_courses(category)

    async def get_graduation_requirement(self, department: str) -> LmsGraduationRequirement:
        return await self.academic.get_graduation_requirement(department)

    # ---------------- 🏫 코스모스 ----------------
    async def coursemos(self, student_id: UUID) -> CoursemosAdapter:
        return CoursemosAdapter(MoodleClient(self._moodle_http, self._token(student_id)))

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
