"""가상 LMS 모의 서버 (FastAPI). 도우미 앱의 /lms 경로에 마운트된다.

두 개의 '가상 학교 시스템'을 제공한다.
  🏫 가상 코스모스 (Moodle 4.5 Web Services 호환)  → /lms/webservice/rest/server.php
       수강 과목 · 과제/마감일 · 과제 제출 · 강의 자료 · 과목 성적   (wstoken 인증)
  🏛 가상 학사정보시스템 (자체 REST API)            → /lms/api/*
       학생 · 전체 성적표 · 졸업 요건 · 과목 카탈로그 · 시연 학생 · WS 토큰 발급   (X-LMS-API-Key 인증)

실제 학교에서는 코스모스(LMS)와 학사정보시스템(포털)이 별개 시스템이므로 여기서도 나눈다.
Swagger 문서: /lms/api/docs
"""

import logging
from typing import Annotated
from uuid import UUID

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from lms_mock import __version__, moodle, repository
from lms_mock.auth import ApiKeyDep, LmsSettingsDep
from lms_mock.config import LmsConfigError, get_lms_settings
from lms_mock.errors import LmsError
from lms_mock.repository import LmsDbDep
from lms_mock.schemas import (
    CourseCategory,
    CourseList,
    DemoStudentRequest,
    DemoStudentResponse,
    GraduationRequirement,
    LmsHealth,
    Student,
    Transcript,
    WsToken,
)

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

    # 🏫 가상 코스모스 (Moodle Web Services REST)
    app.include_router(moodle.router)

    # ---------------- 🏛 가상 학사정보시스템 ----------------
    @app.get("/api/health", response_model=LmsHealth, tags=["system"])
    async def health() -> LmsHealth:
        try:
            configured = get_lms_settings().lms_api_key is not None
        except LmsConfigError:
            configured = False
        return LmsHealth(status="ok" if configured else "degraded", configured=configured, version=__version__)

    @app.get("/api/students/{student_id}", response_model=Student, dependencies=[ApiKeyDep], tags=["학사정보"])
    async def get_student(student_id: UUID, db: LmsDbDep) -> Student:
        return await repository.get_student(db, student_id)

    @app.get(
        "/api/students/{student_id}/transcript", response_model=Transcript, dependencies=[ApiKeyDep], tags=["학사정보"]
    )
    async def get_transcript(student_id: UUID, db: LmsDbDep) -> Transcript:
        return await repository.get_transcript(db, student_id)

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

    @app.get(
        "/api/students/{student_id}/ws-token", response_model=WsToken, dependencies=[ApiKeyDep], tags=["시연용"]
    )
    async def issue_ws_token(student_id: UUID, db: LmsDbDep, settings: LmsSettingsDep) -> WsToken:
        """가상 코스모스(Moodle) Web Service 토큰 발급. 진짜 학교에서는 학교가 학생별로 발급한다."""
        await repository.get_student(db, student_id)
        return WsToken(token=moodle.ws_token(settings, student_id))

    @app.post(
        "/api/demo/students",
        response_model=DemoStudentResponse,
        status_code=201,
        dependencies=[ApiKeyDep],
        tags=["시연용"],
    )
    async def create_demo_student(body: DemoStudentRequest, db: LmsDbDep) -> DemoStudentResponse:
        """시연용 학생과 수강 이력·과제를 만든다 (같은 학번이면 갱신)."""
        return DemoStudentResponse(student_id=await repository.seed_demo_student(db, body.student_no, body.name))

    return app


app = create_lms_app()
