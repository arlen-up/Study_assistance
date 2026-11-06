"""가상 코스모스 — 학교 계정 연결 (OAuth 2.0 Authorization Code + PKCE).

흐름
  1. 도우미(LMMS)가 학생을 GET /lms/oauth/authorize 로 보낸다 (client_id, redirect_uri, state, code_challenge, scope)
  2. 학생이 '학교' 화면에서 학번·비밀번호로 로그인하고, 요청한 권한을 보고 허용한다
     → 학생의 학교 비밀번호는 도우미에게 전달되지 않는다
  3. 학교는 일회용 코드(5분, 한 번만)를 붙여 redirect_uri 로 돌려보낸다
  4. 도우미 서버가 POST /lms/oauth/token 에 코드 + client_secret + code_verifier 를 보내면
     그 학생 전용 토큰(권한 범위·만료 포함)을 발급한다
  5. 학생이 연결을 해제하면 도우미가 POST /lms/oauth/revoke 로 토큰을 폐기한다

시연용 추가 기능: 가상 학교에 계정이 없는 사람은 '시연용 학생 계정 만들기'로 학번을 발급받는다.
(실제 학교에서는 학교가 이미 학생 계정을 갖고 있으므로 이 기능은 없다)
"""

import datetime as dt
import html
import logging
import secrets
from typing import Annotated
from urllib.parse import urlencode
from uuid import UUID

from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from lms_mock import repository
from lms_mock.auth import LmsSettingsDep
from lms_mock.errors import LmsError
from lms_mock.repository import LmsDbDep
from lms_mock.security import (
    CLIENT_ID,
    CODE_TTL_SECONDS,
    SCOPES,
    TOKEN_TTL_DAYS,
    check_client,
    hash_password,
    new_access_token,
    new_code,
    normalize_scope,
    pkce_matches,
    sha256_hex,
    valid_redirect_uri,
    verify_password,
)

logger = logging.getLogger("lms_mock.oauth")
router = APIRouter(prefix="/oauth", tags=["학교 계정 연결 (OAuth 2.0)"])

APP_NAME = "LMMS (Study Overflow)"
_CHALLENGE_LEN = (43, 128)


# ---------------------------------------------------------------------
# 화면 (가상 학교 사이트 — 도우미 앱과 다르게 보이도록 따로 꾸민다)
# ---------------------------------------------------------------------
_PAGE = """<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} · 가상 코스모스</title>
<style>
  :root {{ --school: #0f766e; --ink: #1f2937; --muted: #6b7280; --line: #e5e7eb; }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; font-family: -apple-system, "Apple SD Gothic Neo", "Malgun Gothic", sans-serif; color: var(--ink);
         background: #f1f5f4; min-height: 100vh; display: grid; place-items: center; padding: 16px; }}
  .box {{ width: 100%; max-width: 420px; background: #fff; border: 1px solid var(--line); border-radius: 14px;
          overflow: hidden; box-shadow: 0 10px 30px rgba(15,118,110,.12); }}
  .bar {{ background: var(--school); color: #fff; padding: 14px 20px; font-weight: 800; letter-spacing: -.01em; }}
  .bar small {{ display: block; font-weight: 500; opacity: .85; font-size: .78rem; }}
  .body {{ padding: 20px; display: grid; gap: 14px; }}
  h1 {{ font-size: 1.05rem; margin: 0; word-break: keep-all; }}
  p {{ margin: 0; line-height: 1.5; word-break: keep-all; }}
  .muted {{ color: var(--muted); font-size: .85rem; }}
  ul {{ margin: 0; padding-left: 20px; display: grid; gap: 4px; font-size: .9rem; }}
  label {{ display: grid; gap: 6px; font-size: .88rem; font-weight: 600; }}
  input {{ font: inherit; padding: 10px 12px; border: 1px solid #cbd5e1; border-radius: 8px; }}
  input:focus {{ outline: 3px solid #99f6e4; border-color: var(--school); }}
  .row {{ display: flex; gap: 8px; }}
  button {{ font: inherit; font-weight: 700; padding: 11px 14px; border-radius: 8px; border: 1px solid var(--school);
            cursor: pointer; flex: 1; }}
  .primary {{ background: var(--school); color: #fff; }}
  .ghost {{ background: #fff; color: var(--school); }}
  .error {{ background: #fef2f2; color: #b91c1c; border: 1px solid #fecaca; border-radius: 8px; padding: 10px 12px;
            font-size: .88rem; }}
  .ok {{ background: #f0fdfa; color: #115e59; border: 1px solid #99f6e4; border-radius: 8px; padding: 10px 12px; }}
  .app {{ background: #f8fafc; border: 1px solid var(--line); border-radius: 10px; padding: 12px; display: grid; gap: 8px; }}
  a {{ color: var(--school); }}
</style></head>
<body><main class="box">
<div class="bar">🏫 가상 코스모스<small>한양여대 LMS를 흉내 낸 시연용 학교 사이트</small></div>
<div class="body">{body}</div>
</main></body></html>"""


def _page(title: str, body: str, status_code: int = 200) -> HTMLResponse:
    headers = {"Cache-Control": "no-store", "X-Frame-Options": "DENY"}  # 로그인 화면을 다른 사이트가 감싸지 못하게
    return HTMLResponse(_PAGE.format(title=html.escape(title), body=body), status_code=status_code, headers=headers)


def _hidden(params: dict[str, str]) -> str:
    return "".join(
        f'<input type="hidden" name="{html.escape(k)}" value="{html.escape(v)}">' for k, v in params.items()
    )


def _error_page(message: str) -> HTMLResponse:
    body = f'<h1>연결할 수 없어요</h1><div class="error">{html.escape(message)}</div>' \
           '<p class="muted">앱으로 돌아가 처음부터 다시 시도해 주세요.</p>'
    return _page("오류", body, 400)


def _consent_form(params: dict[str, str], *, error: str = "", notice: str = "", student_no: str = "") -> HTMLResponse:
    scope_items = "".join(f"<li>{html.escape(SCOPES[s])}</li>" for s in params["scope"].split())
    register_link = "/lms/oauth/register?" + urlencode(params)
    body = f"""
<h1>학교 계정으로 로그인</h1>
{f'<div class="error" role="alert">{html.escape(error)}</div>' if error else ''}
{f'<div class="ok" role="status">{notice}</div>' if notice else ''}
<div class="app">
  <p><strong>{html.escape(APP_NAME)}</strong> 앱이 내 학교 정보에 접근하려고 해요.</p>
  <ul>{scope_items}</ul>
  <p class="muted">허용하면 이 앱은 <strong>나의</strong> 정보만, 위 범위 안에서만 읽어요.
     학교 비밀번호는 앱에 전달되지 않고, 언제든 앱에서 연결을 해제할 수 있어요.</p>
</div>
<form method="post" action="/lms/oauth/authorize" class="body" style="padding:0">
  {_hidden(params)}
  <label>학번 <input name="student_no" inputmode="numeric" autocomplete="username" required maxlength="20"
         value="{html.escape(student_no)}" autofocus></label>
  <label>학교 비밀번호 <input type="password" name="password" autocomplete="current-password" required maxlength="128"></label>
  <div class="row">
    <button class="ghost" type="submit" name="decision" value="deny" formnovalidate>거부</button>
    <button class="primary" type="submit" name="decision" value="allow">로그인하고 허용</button>
  </div>
</form>
<p class="muted">가상 학교 계정이 없나요? <a href="{html.escape(register_link)}">시연용 학생 계정 만들기</a></p>
"""
    return _page("학교 계정 연결", body, 400 if error else 200)


def _redirect_back(redirect_uri: str, **params: str) -> RedirectResponse:
    return RedirectResponse(f"{redirect_uri}?{urlencode(params)}", status_code=303)


def _check_request(request: Request, client_id: str | None, redirect_uri: str | None, state: str | None,
                   code_challenge: str | None, code_challenge_method: str | None, scope: str | None,
                   response_type: str | None = "code") -> dict[str, str]:
    """요청을 검사한다. 앱·돌아갈 주소가 이상하면 돌려보내지 않고 오류 화면을 보여 준다 (주소 조작 방지)."""
    if client_id != CLIENT_ID:
        raise LmsError(400, "invalid_client", "등록되지 않은 앱이에요.")
    if not valid_redirect_uri(redirect_uri, request.url.netloc):
        raise LmsError(400, "invalid_redirect_uri", "돌아갈 주소가 등록된 주소와 달라요.")
    normalized = normalize_scope(scope)
    if (
        response_type != "code"
        or not state
        or len(state) > 200
        or code_challenge_method != "S256"
        or not code_challenge
        or not _CHALLENGE_LEN[0] <= len(code_challenge) <= _CHALLENGE_LEN[1]
        or normalized is None
    ):
        raise LmsError(400, "invalid_request", "연결 요청 형식이 올바르지 않아요 (state·PKCE·권한 범위).")
    return {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": str(redirect_uri),
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "scope": normalized,
    }


# ---------------------------------------------------------------------
# 1~3. 로그인 · 동의 → 일회용 코드
# ---------------------------------------------------------------------
@router.get("/authorize", response_class=HTMLResponse)
async def authorize_page(
    request: Request,
    response_type: Annotated[str | None, Query()] = None,
    client_id: Annotated[str | None, Query()] = None,
    redirect_uri: Annotated[str | None, Query()] = None,
    state: Annotated[str | None, Query()] = None,
    code_challenge: Annotated[str | None, Query()] = None,
    code_challenge_method: Annotated[str | None, Query()] = None,
    scope: Annotated[str | None, Query()] = None,
) -> HTMLResponse:
    try:
        params = _check_request(request, client_id, redirect_uri, state, code_challenge, code_challenge_method,
                                scope, response_type)
    except LmsError as exc:
        return _error_page(exc.message)
    return _consent_form(params)


@router.post("/authorize", response_model=None)
async def authorize_submit(
    request: Request,
    db: LmsDbDep,
    client_id: Annotated[str | None, Form()] = None,
    redirect_uri: Annotated[str | None, Form()] = None,
    state: Annotated[str | None, Form()] = None,
    code_challenge: Annotated[str | None, Form()] = None,
    code_challenge_method: Annotated[str | None, Form()] = None,
    scope: Annotated[str | None, Form()] = None,
    decision: Annotated[str, Form()] = "allow",
    student_no: Annotated[str, Form(max_length=20)] = "",
    password: Annotated[str, Form(max_length=128)] = "",
) -> Response:
    try:
        params = _check_request(request, client_id, redirect_uri, state, code_challenge, code_challenge_method, scope)
    except LmsError as exc:
        return _error_page(exc.message)
    if decision != "allow":
        return _redirect_back(params["redirect_uri"], error="access_denied", state=params["state"])

    login = await repository.find_login(db, student_no.strip()) if student_no.strip() else None
    if not verify_password(password, login.get("password_hash") if login else None) or login is None:
        # 학번이 없는지 비밀번호가 틀렸는지 알려 주지 않는다 (계정 추측 방지)
        return _consent_form(params, error="학번 또는 비밀번호가 맞지 않아요.", student_no=student_no.strip())

    code = new_code()
    await repository.save_code(db, {
        "code_hash": sha256_hex(code),
        "student_id": login["id"],
        "client_id": params["client_id"],
        "redirect_uri": params["redirect_uri"],
        "code_challenge": params["code_challenge"],
        "scope": params["scope"],
        "expires_at": (dt.datetime.now(dt.UTC) + dt.timedelta(seconds=CODE_TTL_SECONDS)).isoformat(),
    })
    return _redirect_back(params["redirect_uri"], code=code, state=params["state"])


# ---------------------------------------------------------------------
# 시연용: 가상 학교 학생 계정 만들기 (실제 학교에는 없는 기능)
# ---------------------------------------------------------------------
@router.get("/register", response_class=HTMLResponse)
async def register_page(request: Request) -> HTMLResponse:
    q = request.query_params
    try:
        params = _check_request(request, q.get("client_id"), q.get("redirect_uri"), q.get("state"),
                                q.get("code_challenge"), q.get("code_challenge_method"), q.get("scope"))
    except LmsError as exc:
        return _error_page(exc.message)
    return _register_form(params)


def _register_form(params: dict[str, str], *, name: str = "", error: str = "") -> HTMLResponse:
    body = f"""
<h1>시연용 학생 계정 만들기</h1>
<p class="muted">실제 학교에서는 학교가 이미 학생 계정을 갖고 있어요. 이 화면은 해커톤 시연용이에요.
   만들면 가상 학번과 시연용 수강 과목·성적이 생겨요.</p>
{f'<div class="error" role="alert">{html.escape(error)}</div>' if error else ''}
<form method="post" action="/lms/oauth/register" class="body" style="padding:0">
  {_hidden(params)}
  <label>이름 <input name="name" required maxlength="30" value="{html.escape(name)}" autocomplete="name" autofocus></label>
  <label>학교 비밀번호 (6자 이상) <input type="password" name="password" required minlength="6" maxlength="128"
         autocomplete="new-password"></label>
  <button class="primary" type="submit">계정 만들기</button>
</form>
"""
    return _page("시연용 학생 계정", body, 400 if error else 200)


@router.post("/register", response_class=HTMLResponse)
async def register_submit(
    request: Request,
    db: LmsDbDep,
    name: Annotated[str, Form(max_length=30)] = "",
    password: Annotated[str, Form(max_length=128)] = "",
    client_id: Annotated[str | None, Form()] = None,
    redirect_uri: Annotated[str | None, Form()] = None,
    state: Annotated[str | None, Form()] = None,
    code_challenge: Annotated[str | None, Form()] = None,
    code_challenge_method: Annotated[str | None, Form()] = None,
    scope: Annotated[str | None, Form()] = None,
) -> HTMLResponse:
    try:
        params = _check_request(request, client_id, redirect_uri, state, code_challenge, code_challenge_method, scope)
    except LmsError as exc:
        return _error_page(exc.message)
    name = name.strip()
    if not name or len(password) < 6:
        return _register_form(params, name=name, error="이름과 6자 이상의 비밀번호를 입력해 주세요.")

    for _ in range(5):
        student_no = f"2025{secrets.randbelow(1_000_000):06d}"
        if not await repository.student_no_exists(db, student_no):
            break
    else:
        return _register_form(params, name=name, error="학번을 만들지 못했어요. 다시 시도해 주세요.")
    student_id = await repository.seed_demo_student(db, student_no, name)
    await repository.set_password(db, student_id, hash_password(password))
    notice = (f"가상 학교 계정을 만들었어요. 학번은 <strong>{html.escape(student_no)}</strong> 이에요. "
              "아래에서 이 학번과 방금 정한 비밀번호로 로그인해 주세요.")
    return _consent_form(params, notice=notice, student_no=student_no)


# ---------------------------------------------------------------------
# 4. 코드 → 학생 전용 토큰 (서버끼리)
# ---------------------------------------------------------------------
def _oauth_error(error: str, description: str, status_code: int = 400) -> JSONResponse:
    return JSONResponse({"error": error, "error_description": description}, status_code=status_code,
                        headers={"Cache-Control": "no-store"})


@router.post("/token")
async def token(
    db: LmsDbDep,
    settings: LmsSettingsDep,
    grant_type: Annotated[str, Form()] = "",
    code: Annotated[str, Form()] = "",
    redirect_uri: Annotated[str, Form()] = "",
    client_id: Annotated[str, Form()] = "",
    client_secret: Annotated[str, Form()] = "",
    code_verifier: Annotated[str, Form()] = "",
) -> JSONResponse:
    try:
        check_client(settings, client_id, client_secret)
    except LmsError:
        return _oauth_error("invalid_client", "앱 인증에 실패했습니다.", 401)
    if grant_type != "authorization_code" or not code:
        return _oauth_error("invalid_request", "grant_type=authorization_code 와 code 가 필요합니다.")

    row = await repository.consume_code(db, sha256_hex(code))  # 한 번만 쓸 수 있음 (동시에 와도 하나만 성공)
    if (
        row is None
        or row["client_id"] != client_id
        or row["redirect_uri"] != redirect_uri
        or not pkce_matches(code_verifier, row["code_challenge"])
    ):
        return _oauth_error("invalid_grant", "코드가 없거나, 이미 썼거나, 만료됐거나, PKCE 확인에 실패했습니다.")

    access_token = new_access_token()
    expires_at = dt.datetime.now(dt.UTC) + dt.timedelta(days=TOKEN_TTL_DAYS)
    await repository.save_token(db, {
        "token_hash": sha256_hex(access_token),
        "student_id": row["student_id"],
        "client_id": client_id,
        "scope": row["scope"],
        "expires_at": expires_at.isoformat(),
    })
    student = await repository.get_student(db, UUID(row["student_id"]))
    logger.info("학생 전용 토큰 발급 (scope=%s)", row["scope"])
    return JSONResponse(
        {
            "access_token": access_token,
            "token_type": "Bearer",
            "expires_in": TOKEN_TTL_DAYS * 86400,
            "scope": row["scope"],
            # 확장 필드: 어떤 학생의 토큰인지 (Moodle의 core_webservice_get_site_info 와 같은 정보)
            "student_id": str(student.id),
            "student_no": student.student_no,
            "name": student.name,
        },
        headers={"Cache-Control": "no-store"},
    )


# ---------------------------------------------------------------------
# 5. 토큰 폐기 (RFC 7009: 없는 토큰이어도 200)
# ---------------------------------------------------------------------
@router.post("/revoke")
async def revoke(
    db: LmsDbDep,
    settings: LmsSettingsDep,
    token: Annotated[str, Form()] = "",
    client_id: Annotated[str, Form()] = "",
    client_secret: Annotated[str, Form()] = "",
) -> Response:
    try:
        check_client(settings, client_id, client_secret)
    except LmsError:
        return _oauth_error("invalid_client", "앱 인증에 실패했습니다.", 401)
    if token:
        await repository.revoke_token(db, sha256_hex(token), client_id)
    return Response(status_code=200, headers={"Cache-Control": "no-store"})
