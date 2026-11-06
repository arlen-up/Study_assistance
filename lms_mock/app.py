"""가상 LMS 모의 서버 (FastAPI). 도우미 앱의 /lms 경로에 마운트된다.

두 개의 '가상 학교 시스템'을 제공한다.
  🏫 가상 코스모스 (Moodle 4.5 Web Services 호환)  → /lms/webservice/rest/server.php
       수강 과목 · 과제/마감일 · 과제 제출 · 강의 자료 · 과목 성적   (학생 전용 토큰)
     + 학교 계정 연결 (OAuth 2.0 + PKCE)             → /lms/oauth/authorize · token · revoke
  🏛 가상 학사정보시스템 (자체 REST API)            → /lms/api/*
       내 정보 · 내 성적표                            (학생 전용 토큰, Bearer)
       과목 카탈로그 · 졸업 요건 (학교 공통 정보)     (서버 간 키 X-LMS-API-Key)

학생 개인 정보는 학생이 학교에 로그인해 동의한 토큰으로만 읽을 수 있다.
서버 간 키로 아무 학생의 정보·토큰을 꺼내는 기능은 없다 (예전 /api/students/{id}/ws-token 삭제).

실제 학교에서는 코스모스(LMS)와 학사정보시스템(포털)이 별개 시스템이므로 여기서도 나눈다.
Swagger 문서: /lms/api/docs
"""

import logging
from typing import Annotated

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from lms_mock import __version__, moodle, oauth, repository
from lms_mock.auth import ApiKeyDep
from lms_mock.config import LmsConfigError, get_lms_settings
from lms_mock.errors import LmsError
from lms_mock.repository import LmsDbDep
from lms_mock.schemas import (
    CourseCategory,
    CourseList,
    GraduationRequirement,
    LmsHealth,
    Student,
    Transcript,
)
from lms_mock.student_auth import StudentToken, require_student_token

logger = logging.getLogger("lms_mock")


def create_lms_app() -> FastAPI:
    app = FastAPI(
        title="가상 학교 시스템 (코스모스 + 학사정보)",
        version=__version__,
        description=(
            "해커톤 시연용 가상 시스템. 코스모스는 Moodle 4.5 Web Services 규격(/lms/webservice/rest/server.php), "
            "학사정보는 자체 REST API(/lms/api)로 제공한다."
        ),
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
    )

    @app.exception_handler(LmsError)
    async def handle_lms_error(_: Request, exc: LmsError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"error": {"code": exc.code, "message": exc.message}})

    @app.exception_handler(LmsConfigError)
    async def handle_config_error(_: Request, exc: LmsConfigError) -> JSONResponse:
        return JSONResponse(status_code=503, content={"error": {"code": "lms_misconfigured", "message": str(exc)}})

    @app.exception_handler(RequestValidationError)
    async def handle_validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {}
        message = f"요청 형식이 올바르지 않습니다: {first.get('msg', '')}"
        return JSONResponse(status_code=422, content={"error": {"code": "validation_error", "message": message}})

    @app.exception_handler(Exception)
    async def handle_unexpected(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("가상 LMS 처리되지 않은 오류", exc_info=exc)
        return JSONResponse(status_code=500, content={"error": {"code": "internal_error", "message": "LMS 서버 오류"}})

    # 🏫 가상 코스모스 (Moodle Web Services REST) + 학교 계정 연결 (OAuth 2.0)
    app.include_router(moodle.router)
    app.include_router(oauth.router)

    # ---------------- 🏛 가상 학사정보시스템 ----------------
    @app.get("/api/health", response_model=LmsHealth, tags=["system"])
    async def health() -> LmsHealth:
        try:
            configured = get_lms_settings().lms_api_key is not None
        except LmsConfigError:
            configured = False
        return LmsHealth(status="ok" if configured else "degraded", configured=configured, version=__version__)

    @app.get("/api/me", response_model=Student, tags=["학사정보"])
    async def get_me(db: LmsDbDep, me: StudentToken = require_student_token("academic.read")) -> Student:
        """토큰 주인 학생의 정보 (학생 ID를 주소로 받지 않으므로 남의 정보를 요청할 방법이 없다)."""
        return await repository.get_student(db, me.student_id)

    @app.get("/api/me/transcript", response_model=Transcript, tags=["학사정보"])
    async def get_my_transcript(db: LmsDbDep, me: StudentToken = require_student_token("academic.read")) -> Transcript:
        return await repository.get_transcript(db, me.student_id)

    @app.get("/api/courses", response_model=CourseList, dependencies=[ApiKeyDep], tags=["학사정보"])
    async def list_courses(db: LmsDbDep, category: Annotated[CourseCategory | None, Query()] = None) -> CourseList:
        return CourseList(items=await repository.list_courses(db, category))

    @app.get(
        "/api/graduation-requirements/{department}",
        response_model=GraduationRequirement,
        dependencies=[ApiKeyDep],
        tags=["학사정보"],
    )
    async def get_graduation_requirement(department: str, db: LmsDbDep) -> GraduationRequirement:
        return await repository.get_graduation_requirement(db, department)

    return app


app = create_lms_app()
