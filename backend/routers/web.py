"""웹 화면 (서버 렌더링 + HTMX 조각).

- 페이지: GET /, /login
- 폼 처리: POST /login, /signup, /logout
- 사용자 이름: 처음 들어오면 이름 설정 창 → POST /ui/username (입력 중 확인: GET /ui/username/check)
- HTMX 조각(/ui/*): 화면 일부만 HTML로 돌려주어 교체한다. JS 프레임워크 없이 동적인 화면을 만든다.
  · 챗봇: /ui/chat (질문 분류 → 답변), /ui/chat/summary·quiz (답변 밑 '이어서 하기' 단추)
  · 교안 뷰어: /ui/viewer/<교안 ID> (출처 위치 표시), /ui/viewer/<교안 ID>/file (PDF 원본)
"""

import json
import logging
import time
from typing import Annotated, Any, get_args
from uuid import UUID, uuid4

from fastapi import APIRouter, File, Form, Path, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import TypeAdapter, ValidationError

from backend.core.errors import AppError, BadRequestError, NotFoundError
from backend.core.supabase_client import DbDep, SettingsDep
from backend.integrations.lms_client import LmsClient, LmsDep
from backend.routers.deps import WebAgentDep
from backend.schemas.models import ChatRequest, ChatTurn, DashboardResponse, Difficulty, DocumentItem, RouteType
from backend.services import (
    career_service,
    file_store,
    insights_service,
    lms_link_service,
    pdf_highlight,
    profile_service,
    quiz_service,
    study_service,
    viewer_service,
)
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
ROUTES: tuple[str, ...] = get_args(RouteType)
# ?tab= 값 → 위쪽 메뉴 화면. 예전 주소(?tab=quiz 등)는 모두 챗봇으로 (퀴즈·요약은 챗봇 안 단추로 옮김)
VIEW_OF_TAB = {
    "chat": "chat", "summary": "chat", "quiz": "chat", "flashcards": "chat", "mindmap": "chat",
    "dashboard": "dashboard", "grades": "grades", "career": "grades", "upload": "upload",
}


def _render(request: Request, name: str, context: dict[str, Any], status_code: int = 200) -> HTMLResponse:
    return templates.TemplateResponse(request, name, context, status_code=status_code)


def _safe_int(value: object) -> int:
    try:
        return max(0, int(str(value)))
    except (TypeError, ValueError):
        return 0


def _turn_id() -> str:
    """한 번의 답변을 구별하는 짧은 이름 (출처 [1] 링크가 같은 답변의 출처 목록을 가리키게)."""
    return uuid4().hex[:10]


async def _insights_context(db: AsyncClient, user_id: UUID, dashboard: DashboardResponse | None) -> dict[str, Any]:
    if dashboard is None:
        return {"insights": None}
    return {"insights": await insights_service.get_insights(db, user_id, dashboard)}


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
    username_state = await profile_service.get_state(db, user.id)
    try:
        dashboard = await study_service.get_dashboard(db, user.id)
        documents = await study_service.list_documents(db, user.id)
    except AppError as exc:
        error = exc.message
    # 복습 알림(푸시)에서 들어오면 챗봇에 '퀴즈·카드 시작' 말풍선을 띄운다
    quiz_doc_item = next((d for d in documents if quiz_doc and str(d.id) == quiz_doc), None)

    return _render(
        request,
        "dashboard.html",
        {
            "user": user,
            "username_state": username_state,
            "dashboard": dashboard,
            "documents": documents,
            "error": error,
            "active_view": VIEW_OF_TAB.get(tab, "chat"),
            "quiz_doc_item": quiz_doc_item,
            **await _insights_context(db, user.id, dashboard),
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
            context["imported_ids"] = await lms_link_service.imported_material_ids(db, user_id)
    except AppError as exc:
        context["lms_error"] = exc.message
    return context


def _render_lms_panel(request: Request, context: dict[str, Any]) -> HTMLResponse:
    return _render(request, "partials/lms_panel.html", {**context, "oob_materials": True})


# ---------------------------------------------------------------------
# 사용자 이름 (처음 한 번)
# ---------------------------------------------------------------------
@router.post("/ui/username", response_class=HTMLResponse)
async def ui_set_username(
    request: Request,
    user: WebUser,
    db: DbDep,
    username: Annotated[str, Form(max_length=64)] = "",
) -> Response:
    htmx = request.headers.get("HX-Request") == "true"
    try:
        await profile_service.set_username(db, user.id, username)
    except AppError as exc:
        if not htmx:
            return RedirectResponse("/", status_code=303)
        return _render(request, "partials/username_form.html", {"value": username, "error": exc.message})
    if not htmx:
        return RedirectResponse("/", status_code=303)
    return Response(status_code=200, headers={"HX-Redirect": "/"})


@router.get("/ui/username/check", response_class=HTMLResponse)
async def ui_check_username(
    request: Request,
    user: WebUser,
    db: DbDep,
    username: Annotated[str, Query(max_length=64)] = "",
) -> HTMLResponse:
    """입력하는 동안 바로 알려 주기: 규칙에 맞는지 + 다른 사람이 쓰고 있는지."""
    if not username.strip():
        return HTMLResponse("")
    try:
        _, problem = await profile_service.check(db, user.id, username)
    except AppError as exc:
        problem = exc.message
    return _render(request, "partials/username_hint.html", {"problem": problem})


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
    route: Annotated[str, Form(max_length=10)] = "",
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
            route=route if route in ROUTES else None,  # 입력창에서는 보내지 않음 → AI가 질문 종류를 고른다
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
            "turn_id": _turn_id(),
            "history_json": json.dumps([t.model_dump() for t in new_history], ensure_ascii=False),
        },
    )


# 📝 기말 예상 문제: 문항 수·요청을 정해 만들고, 답을 적으면 AI가 의미로 채점한다
@router.get("/ui/exam/new", response_class=HTMLResponse)
async def ui_exam_new(
    request: Request, user: WebUser, db: DbDep, document_id: Annotated[str, Query()] = ""
) -> HTMLResponse:
    doc_id = _parse_uuid(document_id)
    if doc_id is None:
        raise BadRequestError("예상 문제를 만들 교안을 골라 주세요.")
    await study_service.require_document(db, user.id, doc_id)
    return _render(request, "partials/exam_new.html", {"document_id": doc_id})


@router.post("/ui/exam", response_class=HTMLResponse)
async def ui_exam_create(
    request: Request,
    user: WebUser,
    db: DbDep,
    agent: WebAgentDep,
    document_id: Annotated[str, Form()],
    count: Annotated[int, Form(ge=1, le=10)] = 5,
    ask: Annotated[str, Form(max_length=300)] = "",
) -> HTMLResponse:
    doc_id = _parse_uuid(document_id)
    if doc_id is None:
        raise BadRequestError("예상 문제를 만들 교안을 골라 주세요.")
    # 정답은 요청 문장에 '정답'이라고 적었을 때만 채점 결과에 함께 보여 준다
    show_answer = "정답" in ask
    draft, model_name = await agent.generate_exam(doc_id, count)
    quiz_id = await quiz_service.save_exam(
        db, user_id=user.id, document_id=doc_id, questions=draft.questions, model_name=model_name
    )
    return _render(
        request,
        "partials/exam_form.html",
        {"quiz_id": quiz_id, "questions": draft.questions, "show_answer": show_answer},
    )


@router.post("/ui/exam/{quiz_id}/grade", response_class=HTMLResponse)
async def ui_exam_grade(
    request: Request,
    quiz_id: UUID,
    user: WebUser,
    db: DbDep,
    agent: WebAgentDep,
    show_answer: Annotated[str, Form()] = "0",
) -> HTMLResponse:
    form = await request.form()
    _, questions = await quiz_service.load_quiz(db, quiz_id=quiz_id, user_id=user.id)
    answers = [str(form.get(f"a_{i}", "")).strip()[:500] for i in range(len(questions))]
    if any(not a for a in answers):
        raise BadRequestError("모든 문제에 답을 적은 뒤 채점해 주세요.")
    graded = await agent.grade_exam([(q.question, q.answer, q.explanation) for q in questions], answers)
    by_index = {r.index: r for r in graded.results}
    rows = []
    for i, q in enumerate(questions):
        item = by_index.get(i)
        rows.append(
            {
                "index": i,
                "question": q.question,
                "your_answer": answers[i],
                "correct": bool(item and item.correct),
                "feedback": item.feedback if item else "채점 결과를 받지 못했어요.",
                "expected": q.answer,
                "rubric": q.explanation,
            }
        )
    return _render(
        request,
        "partials/exam_result.html",
        {
            "quiz_id": quiz_id,
            "rows": rows,
            "score": sum(1 for r in rows if r["correct"]),
            "total": len(rows),
            "show_answer": show_answer == "1",
        },
    )


# 답변 밑 '그림으로 정리' 단추: 결과를 채팅이 아니라 오른쪽 '교안 보기' 칸에 크게 띄운다
@router.post("/ui/diagram", response_class=HTMLResponse)
async def ui_diagram(
    request: Request,
    _user: WebUser,
    agent: WebAgentDep,
    document_id: Annotated[str, Form()],
) -> HTMLResponse:
    doc_id = _parse_uuid(document_id)
    try:
        chat_request = ChatRequest(
            message="이 교안의 핵심 개념을 마인드맵 그림으로 정리해줘",
            document_id=doc_id,
            route="study",
        )
    except ValidationError:
        raise BadRequestError("그림으로 정리할 교안을 골라 주세요.") from None

    try:
        result = await agent.chat(chat_request)
    except AppError as exc:
        return _render(request, "partials/chat_error.html", {"message": exc.message, "draft": ""})
    return _render(request, "partials/diagram_view.html", {"result": result, "document_id": doc_id})


# 답변 밑 '이어서 하기' 단추: 교안을 코드가 바로 읽고 AI는 한 번만 불러서 빠르다
@router.post("/ui/chat/summary", response_class=HTMLResponse)
async def ui_chat_summary(
    request: Request, _user: WebUser, agent: WebAgentDep, document_id: Annotated[str, Form()] = ""
) -> HTMLResponse:
    doc_id = _parse_uuid(document_id)
    if doc_id is None:
        raise BadRequestError("요약할 교안을 골라 주세요.")
    started = time.perf_counter()
    summary = await agent.summarize(doc_id)
    elapsed = round((time.perf_counter() - started) * 1000)
    return _render(request, "partials/chat_summary.html", {"summary": summary, "answer_ms": elapsed})


@router.post("/ui/chat/quiz", response_class=HTMLResponse)
async def ui_chat_quiz(
    request: Request,
    user: WebUser,
    db: DbDep,
    agent: WebAgentDep,
    document_id: Annotated[str, Form()] = "",
    difficulty: Annotated[Difficulty, Form()] = "medium",
) -> HTMLResponse:
    """퀴즈·카드 (예전 '퀴즈'와 '플래시카드'를 하나로): 카드로 풀고, 채점 뒤 카드를 뒤집어 해설을 본다."""
    started = time.perf_counter()
    quiz = await study_service.create_quiz(agent, db, user.id, _parse_uuid(document_id), difficulty)
    elapsed = round((time.perf_counter() - started) * 1000)
    return _render(
        request, "partials/chat_quiz.html", {"quiz": quiz, "started_at": int(time.time()), "answer_ms": elapsed}
    )


MAX_QUIZ_QUESTIONS = 10


def _parse_answers(raw: str) -> list[str]:
    """화면이 들고 다니는 지금까지의 답안 목록(JSON)을 검사한다."""
    try:
        data = json.loads(raw or "[]")
    except json.JSONDecodeError:
        raise BadRequestError("답안 정보를 다시 불러와 주세요.") from None
    if not isinstance(data, list) or len(data) > MAX_QUIZ_QUESTIONS or not all(isinstance(a, str) for a in data):
        raise BadRequestError("답안 정보를 다시 불러와 주세요.")
    return [a.strip()[:500] for a in data]


def _quiz_step_context(quiz_id: UUID, index: int, total: int, answers: list[str], started_at: str) -> dict[str, Any]:
    return {
        "quiz_id": quiz_id,
        "index": index,
        "total": total,
        "answers_json": json.dumps(answers, ensure_ascii=False),
        "started_at": _safe_int(started_at),
    }


@router.post("/ui/quiz/{quiz_id}/slide", response_class=HTMLResponse)
async def ui_quiz_slide(
    request: Request,
    quiz_id: UUID,
    user: WebUser,
    db: DbDep,
    index: Annotated[int, Form(ge=0, le=MAX_QUIZ_QUESTIONS - 1)],
    answers_json: Annotated[str, Form()] = "[]",
    started_at: Annotated[str, Form()] = "0",
) -> HTMLResponse:
    """슬라이드 한 장(문제 한 개)을 보여 준다. 앞 문제를 모두 채점했을 때만 다음 문제를 연다."""
    _, questions = await quiz_service.load_quiz(db, quiz_id=quiz_id, user_id=user.id)
    answers = _parse_answers(answers_json)
    if index >= len(questions) or len(answers) != index:
        raise BadRequestError("이전 문제부터 차례로 풀어 주세요.")
    return _render(
        request,
        "partials/quiz_slide.html",
        {"q": questions[index], **_quiz_step_context(quiz_id, index, len(questions), answers, started_at)},
    )


@router.post("/ui/quiz/{quiz_id}/check", response_class=HTMLResponse)
async def ui_quiz_check(
    request: Request,
    quiz_id: UUID,
    user: WebUser,
    db: DbDep,
    index: Annotated[int, Form(ge=0, le=MAX_QUIZ_QUESTIONS - 1)],
    answer: Annotated[str, Form(max_length=500)],
    answers_json: Annotated[str, Form()] = "[]",
    started_at: Annotated[str, Form()] = "0",
) -> HTMLResponse:
    """이 문제 하나만 바로 채점해서 정답과 해설을 보여 준다. 결과는 아직 저장하지 않는다."""
    _, questions = await quiz_service.load_quiz(db, quiz_id=quiz_id, user_id=user.id)
    answers = _parse_answers(answers_json)
    if index >= len(questions) or len(answers) != index:
        raise BadRequestError("이전 문제부터 차례로 풀어 주세요.")
    value = answer.strip()
    if not value:
        raise BadRequestError("답을 입력한 뒤 확인해 주세요.")
    question = questions[index]
    answers.append(value)
    total = len(questions)
    return _render(
        request,
        "partials/quiz_checked.html",
        {
            "q": question,
            "correct": quiz_service.is_correct(question, value),
            "your_answer": value,
            "is_last": index + 1 >= total,
            **_quiz_step_context(quiz_id, index, total, answers, started_at),
        },
    )


@router.post("/ui/quiz/{quiz_id}/finish", response_class=HTMLResponse)
async def ui_quiz_finish(
    request: Request,
    quiz_id: UUID,
    user: WebUser,
    db: DbDep,
    answers_json: Annotated[str, Form()] = "[]",
    started_at: Annotated[str, Form()] = "0",
) -> HTMLResponse:
    """모든 문제를 채점해 학습 기록을 남기고, 카드 결과(한 장씩)를 보여 준다."""
    answers = _parse_answers(answers_json)
    started = _safe_int(started_at)
    duration = max(0, min(int(time.time()) - started, 86_400)) if started else None
    result, questions = await study_service.submit_quiz(db, user.id, quiz_id, answers, duration)
    dashboard = await study_service.get_dashboard(db, user.id)
    return _render(
        request,
        "partials/quiz_result.html",
        {"result": result, "questions": questions, "dashboard": dashboard, "oob": True,
         **await _insights_context(db, user.id, dashboard)},
    )


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
        {
            "result": result,
            "questions": questions,
            "dashboard": dashboard,
            "oob": True,
            **await _insights_context(db, user.id, dashboard),
        },
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
    documents = await study_service.list_documents(db, user.id)
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
    documents = await study_service.list_documents(db, user.id)
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


# ---------------------------------------------------------------------
# 📄 교안 뷰어 (챗봇 옆 빈 공간)
# ---------------------------------------------------------------------
@router.get("/ui/viewer", response_class=HTMLResponse)
async def ui_viewer_empty(request: Request, user: WebUser, db: DbDep) -> HTMLResponse:
    return _render(request, "partials/viewer_empty.html", {"documents": await study_service.list_documents(db, user.id)})


@router.get("/ui/viewer/{document_id}", response_class=HTMLResponse)
async def ui_viewer(
    request: Request,
    document_id: UUID,
    user: WebUser,
    db: DbDep,
    chunk: Annotated[UUID | None, Query()] = None,
    page: Annotated[int | None, Query(ge=1, le=5000)] = None,
) -> HTMLResponse:
    doc = await viewer_service.load(db, user.id, document_id, chunk, page)
    return _render(request, "partials/viewer.html", {"doc": doc})


@router.get("/ui/viewer/{document_id}/page")
async def ui_viewer_page(
    document_id: UUID,
    user: WebUser,
    db: DbDep,
    page: Annotated[int, Query(ge=1, le=5000)],
    q: Annotated[str, Query(max_length=400)] = "",
) -> Response:
    """PDF 원본의 한 쪽을 그림으로 그리고, 답변에 쓴 문장 부분을 노란 상자로 표시한다."""
    await study_service.require_document(db, user.id, document_id)  # 본인 교안만
    data = await file_store.load_pdf(db, document_id)
    if data is None:
        raise NotFoundError("이 교안은 PDF 원본이 보관되어 있지 않아요.")
    png, boxes = pdf_highlight.render_page(data, page, q)
    return Response(
        content=png,
        media_type="image/png",
        headers={"Cache-Control": "private, max-age=300", "X-Highlight-Count": str(boxes)},
    )


@router.get("/ui/viewer/{document_id}/file")
async def ui_viewer_file(document_id: UUID, user: WebUser, db: DbDep) -> Response:
    """보관된 PDF 원본. 같은 사이트의 뷰어(iframe) 안에서만 열리게 한다."""
    await study_service.require_document(db, user.id, document_id)  # 본인 교안만
    data = await file_store.load_pdf(db, document_id)
    if data is None:
        raise NotFoundError("이 교안은 PDF 원본이 보관되어 있지 않아요.")
    return Response(
        content=data,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="{document_id}.pdf"',
            "Cache-Control": "private, max-age=300",
            "X-Frame-Options": "SAMEORIGIN",
            "Content-Security-Policy": "frame-ancestors 'self'",
        },
    )
