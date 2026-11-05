"""웹 화면 (서버 렌더링 + HTMX 조각).

- 페이지: GET /, /login
- 폼 처리: POST /login, /signup, /logout
- HTMX 조각(/ui/*): 화면 일부만 HTML로 돌려주어 교체한다. JS 프레임워크 없이 동적인 화면을 만든다.
"""

import json
import logging
import time
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, File, Form, Path, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import TypeAdapter, ValidationError

from backend.core.errors import AppError, BadRequestError
from backend.core.supabase_client import DbDep, SettingsDep
from backend.integrations.lms_client import LmsClient, LmsDep
from backend.routers.deps import WebAgentDep
from backend.schemas.models import ChatRequest, ChatTurn, Difficulty, DocumentItem
from backend.services import career_service, lms_link_service, study_service
from backend.web.session import (
    OptionalWebUser,
    WebUser,
    clear_session_cookies,
    set_session_cookies,
    sign_in,
    sign_up,
)
from backend.web.templating import templates
from supabase import AsyncClient

logger = logging.getLogger(__name__)

router = APIRouter(include_in_schema=False)

MAX_HISTORY = 10
MAX_TURN_CHARS = 4000
_history_adapter = TypeAdapter(list[ChatTurn])
TABS = ("chat", "summary", "quiz", "career", "upload")


def _render(request: Request, name: str, context: dict[str, Any], status_code: int = 200) -> HTMLResponse:
    return templates.TemplateResponse(request, name, context, status_code=status_code)


def _safe_int(value: object) -> int:
    try:
        return max(0, int(str(value)))
    except (TypeError, ValueError):
        return 0


def _parse_uuid(value: str | None) -> UUID | None:
    if not value:
        return None
    try:
        return UUID(value)
    except ValueError:
        raise BadRequestError("잘못된 교안 ID입니다.") from None


# ---------------------------------------------------------------------
# 로그인
# ---------------------------------------------------------------------
@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, user: OptionalWebUser, mode: str = "signin") -> Response:
    if user is not None:
        return RedirectResponse("/", status_code=303)
    return _render(request, "login.html", {"mode": "signup" if mode == "signup" else "signin"})


@router.post("/login")
async def login(
    request: Request,
    settings: SettingsDep,
    email: Annotated[str, Form(max_length=254)],
    password: Annotated[str, Form(max_length=128)],
) -> Response:
    try:
        session = await sign_in(settings, email.strip(), password)
    except AppError as exc:
        return _render(request, "login.html", {"mode": "signin", "error": exc.message, "email": email}, 400)
    response = RedirectResponse("/", status_code=303)
    set_session_cookies(response, request, session)
    return response


@router.post("/signup")
async def signup(
    request: Request,
    settings: SettingsDep,
    email: Annotated[str, Form(max_length=254)],
    password: Annotated[str, Form(min_length=6, max_length=128)],
) -> Response:
    try:
        session = await sign_up(settings, email.strip(), password, redirect_to=str(request.url_for("login_page")))
    except AppError as exc:
        return _render(request, "login.html", {"mode": "signup", "error": exc.message, "email": email}, 400)
    if session is None:
        notice = "가입 확인 메일을 보냈어요. 메일의 링크를 누른 뒤 로그인해 주세요."
        return _render(request, "login.html", {"mode": "signin", "notice": notice, "email": email})
    response = RedirectResponse("/", status_code=303)
    set_session_cookies(response, request, session)
    return response


@router.post("/logout")
async def logout() -> Response:
    response = RedirectResponse("/login", status_code=303)
    clear_session_cookies(response)
    return response


# ---------------------------------------------------------------------
# 대시보드
# ---------------------------------------------------------------------
@router.get("/", response_class=HTMLResponse)
async def dashboard_page(
    request: Request,
    user: WebUser,
    db: DbDep,
    lms: LmsDep,
    settings: SettingsDep,
    tab: Annotated[str, Query()] = "chat",
    quiz_doc: Annotated[str | None, Query()] = None,
) -> HTMLResponse:
    error: str | None = None
    dashboard = None
    documents: list[DocumentItem] = []
    try:
        dashboard = await study_service.get_dashboard(db, user.id)
        documents = await study_service.list_documents(db)
    except AppError as exc:
        error = exc.message

    return _render(
        request,
        "dashboard.html",
        {
            "user": user,
            "dashboard": dashboard,
            "documents": documents,
            "error": error,
            "active_tab": tab if tab in TABS else "chat",
            "quiz_doc": quiz_doc or "",
            **await _lms_context(db, lms, user.id),
            **await _career_context(db, user.id),
            "vapid_public_key": settings.vapid_public_key if settings.push_enabled else None,
        },
    )


async def _career_context(db: AsyncClient, user_id: UUID) -> dict[str, Any]:
    try:
        return {
            "career_tracks": await career_service.list_tracks(db),
            "target_job": await career_service.get_target_job(db, user_id),
        }
    except AppError:
        return {"career_tracks": [], "target_job": None}


async def _lms_context(db: AsyncClient, lms: LmsClient, user_id: UUID) -> dict[str, Any]:
    """LMS 패널용 데이터. LMS 오류가 나도 대시보드 전체는 열리도록 메시지로만 전달한다."""
    context: dict[str, Any] = {
        "lms_status": None,
        "lms_assignments": [],
        "lms_materials": [],
        "imported_ids": set(),
        "lms_error": None,
    }
    try:
        status = await lms_link_service.get_status(db, lms, user_id)
        context["lms_status"] = status
        if status.linked and status.student_id is not None:
            context["lms_assignments"] = await lms.list_open_assignments(status.student_id)
            context["lms_materials"] = await lms.list_student_materials(status.student_id)
            context["imported_ids"] = await lms_link_service.imported_material_ids(db)
    except AppError as exc:
        context["lms_error"] = exc.message
    return context


def _render_lms_panel(request: Request, context: dict[str, Any]) -> HTMLResponse:
    return _render(request, "partials/lms_panel.html", {**context, "oob_materials": True})


# ---------------------------------------------------------------------
# HTMX 조각
# ---------------------------------------------------------------------
@router.post("/ui/chat", response_class=HTMLResponse)
async def ui_chat(
    request: Request,
    _user: WebUser,
    agent: WebAgentDep,
    message: Annotated[str, Form(max_length=2000)],
    document_id: Annotated[str, Form()] = "",
    include_summary: Annotated[str | None, Form()] = None,
    history: Annotated[str, Form()] = "[]",
) -> HTMLResponse:
    try:
        turns = _history_adapter.validate_json(history or "[]")[-MAX_HISTORY:]
    except ValidationError:
        turns = []

    try:
        chat_request = ChatRequest(
            message=message,
            document_id=_parse_uuid(document_id),
            history=turns,
            include_summary=include_summary is not None,
        )
    except ValidationError:
        raise BadRequestError("질문을 1~2000자로 입력해 주세요.") from None

    try:
        result = await agent.chat(chat_request)
    except AppError as exc:
        # 실패하면 입력한 질문을 입력창에 되돌려 다시 보낼 수 있게 한다
        return _render(request, "partials/chat_error.html", {"message": exc.message, "draft": message})

    new_history = [
        *turns,
        ChatTurn(role="user", content=chat_request.message),
        ChatTurn(role="assistant", content=result.answer[:MAX_TURN_CHARS]),
    ][-MAX_HISTORY:]
    return _render(
        request,
        "partials/chat_turn.html",
        {
            "question": chat_request.message,
            "result": result,
            "history_json": json.dumps([t.model_dump() for t in new_history], ensure_ascii=False),
        },
    )


@router.post("/ui/summary", response_class=HTMLResponse)
async def ui_summary(
    request: Request, _user: WebUser, agent: WebAgentDep, document_id: Annotated[str, Form()] = ""
) -> HTMLResponse:
    doc_id = _parse_uuid(document_id)
    if doc_id is None:
        raise BadRequestError("요약할 교안을 선택해 주세요.")
    summary = await agent.summarize(doc_id)
    return _render(request, "partials/summary.html", {"summary": summary})


@router.post("/ui/quiz", response_class=HTMLResponse)
async def ui_quiz(
    request: Request,
    user: WebUser,
    db: DbDep,
    agent: WebAgentDep,
    document_id: Annotated[str, Form()] = "",
    difficulty: Annotated[Difficulty, Form()] = "medium",
) -> HTMLResponse:
    quiz = await study_service.create_quiz(agent, db, user.id, _parse_uuid(document_id), difficulty)
    return _render(request, "partials/quiz_form.html", {"quiz": quiz, "started_at": int(time.time())})


@router.post("/ui/quiz/{quiz_id}/submit", response_class=HTMLResponse)
async def ui_quiz_submit(request: Request, quiz_id: UUID, user: WebUser, db: DbDep) -> HTMLResponse:
    form = await request.form()
    count = min(_safe_int(form.get("question_count")), 10)
    answers = [str(form.get(f"answer_{i}", "")).strip()[:500] for i in range(count)]
    if count == 0 or any(not a for a in answers):
        raise BadRequestError("모든 문제에 답을 입력해 주세요.")

    started_at = _safe_int(form.get("started_at"))
    duration = max(0, min(int(time.time()) - started_at, 86_400)) if started_at else None

    result, questions = await study_service.submit_quiz(db, user.id, quiz_id, answers, duration)
    dashboard = await study_service.get_dashboard(db, user.id)
    return _render(
        request,
        "partials/quiz_result.html",
        {"result": result, "questions": questions, "dashboard": dashboard, "oob": True},
    )


@router.post("/ui/documents", response_class=HTMLResponse)
async def ui_upload(
    request: Request,
    user: WebUser,
    db: DbDep,
    settings: SettingsDep,
    title: Annotated[str, Form(min_length=1, max_length=200)],
    course_name: Annotated[str, Form(max_length=100)] = "",
    week_no: Annotated[str, Form()] = "",
    keywords: Annotated[str, Form(max_length=500)] = "",
    text: Annotated[str, Form(max_length=200_000)] = "",
    file: Annotated[UploadFile | None, File()] = None,
) -> HTMLResponse:
    week: int | None = None
    if week_no.strip():
        if not week_no.strip().isdigit() or not 1 <= int(week_no) <= 52:
            raise BadRequestError("주차는 1~52 사이 숫자로 입력해 주세요.")
        week = int(week_no)

    result = await study_service.upload_document(
        db,
        settings,
        user.id,
        title=title,
        course_name=course_name,
        week_no=week,
        keywords=keywords,
        text=text,
        file=file if file is not None and file.filename else None,
    )
    documents = await study_service.list_documents(db)
    return _render(request, "partials/upload_result.html", {"result": result, "documents": documents, "oob": True})


# ---------------------------------------------------------------------
# 가상 LMS 연동 조각
# ---------------------------------------------------------------------
@router.post("/ui/lms/link", response_class=HTMLResponse)
async def ui_lms_link(request: Request, user: WebUser, db: DbDep, lms: LmsDep) -> HTMLResponse:
    await lms_link_service.link_demo_account(db, lms, user)
    return _render_lms_panel(request, {**await _lms_context(db, lms, user.id), **await _career_context(db, user.id)})


@router.post("/ui/lms/assignments/{assignment_id}/submit", response_class=HTMLResponse)
async def ui_lms_submit(
    request: Request,
    assignment_id: Annotated[str, Path(pattern=r"^[0-9A-Za-z_-]{1,64}$")],
    user: WebUser,
    db: DbDep,
    lms: LmsDep,
) -> HTMLResponse:
    await lms_link_service.submit_assignment(db, lms, user.id, assignment_id)
    return _render_lms_panel(request, {**await _lms_context(db, lms, user.id), **await _career_context(db, user.id)})


@router.post("/ui/lms/materials/{material_id}/import", response_class=HTMLResponse)
async def ui_lms_import(
    request: Request,
    material_id: Annotated[str, Path(pattern=r"^[0-9A-Za-z_-]{1,64}$")],
    user: WebUser,
    db: DbDep,
    settings: SettingsDep,
    lms: LmsDep,
) -> HTMLResponse:
    result = await lms_link_service.import_material(db, settings, lms, user.id, material_id)
    documents = await study_service.list_documents(db)
    return _render(
        request,
        "partials/lms_import_result.html",
        {"result": result, "material_id": str(material_id), "documents": documents},
    )


# ---------------------------------------------------------------------
# 커리어 나비게이터 조각
# ---------------------------------------------------------------------
@router.post("/ui/career", response_class=HTMLResponse)
async def ui_career(
    request: Request,
    user: WebUser,
    db: DbDep,
    lms: LmsDep,
    agent: WebAgentDep,
    job_role: Annotated[str, Form(pattern=r"^[a-z_]{0,30}$")] = "",
) -> HTMLResponse:
    """선택한 직무를 관심 직무로 저장하고, 졸업 요건 분석 + AI 조언을 보여준다."""
    if job_role:
        await career_service.set_target_job(db, user.id, job_role)
    report = await career_service.build_report(db, lms, user.id, agent, job_role or None)
    return _render(request, "partials/career_report.html", {"report": report})


@router.post("/ui/lms/sync", response_class=HTMLResponse)
async def ui_lms_sync(request: Request, user: WebUser, db: DbDep, settings: SettingsDep, lms: LmsDep) -> HTMLResponse:
    """코스모스·학사정보 즉시 동기화: 성적 갱신 + 새 강의 자료 자동 가져오기."""
    result = await lms_link_service.sync_user(db, settings, lms, user.id)
    if result.imported_titles:
        message = f"새 강의 자료 {len(result.imported_titles)}개를 가져왔어요: " + ", ".join(result.imported_titles[:3])
    else:
        message = f"최신 상태예요. 미제출 과제 {result.open_assignments}개, 이수 {result.completed_credits}학점."
    context = {**await _lms_context(db, lms, user.id), **await _career_context(db, user.id), "sync_message": message}
    return _render_lms_panel(request, context)
