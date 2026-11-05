"""웹 화면용 로그인 세션 (HttpOnly 쿠키).

- 토큰은 HttpOnly 쿠키에만 저장 → 브라우저 JS에서 읽을 수 없어 XSS로 탈취하기 어렵다.
- SameSite=Lax + Origin 검사(app.py) → 다른 사이트에서 보낸 POST(CSRF)를 막는다.
- 액세스 토큰이 만료되면 리프레시 토큰으로 자동 갱신하고, 새 쿠키는 미들웨어가 응답에 붙인다.
"""

import logging
from typing import Annotated

from fastapi import Depends, Request, Response
from supabase_auth.errors import AuthApiError, AuthError
from supabase_auth.types import Session

from backend.core.auth import ACCESS_COOKIE, REFRESH_COOKIE, verify_access_token
from backend.core.config import Settings
from backend.core.errors import BadRequestError, UnauthorizedError, UpstreamError
from backend.core.supabase_client import DbDep, SettingsDep, create_auth_client
from backend.schemas.models import AuthUser

logger = logging.getLogger(__name__)

REFRESH_MAX_AGE = 60 * 60 * 24 * 30  # 30일


class LoginRequiredError(UnauthorizedError):
    code = "login_required"


def is_https(request: Request) -> bool:
    return request.headers.get("x-forwarded-proto", request.url.scheme) == "https"


def set_session_cookies(response: Response, request: Request, session: Session) -> None:
    secure = is_https(request)
    response.set_cookie(
        ACCESS_COOKIE,
        session.access_token,
        max_age=int(session.expires_in or 3600),
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )
    response.set_cookie(
        REFRESH_COOKIE,
        session.refresh_token,
        max_age=REFRESH_MAX_AGE,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )


def clear_session_cookies(response: Response) -> None:
    response.delete_cookie(ACCESS_COOKIE, path="/")
    response.delete_cookie(REFRESH_COOKIE, path="/")


def _friendly_auth_error(exc: AuthError) -> str:
    message = str(exc).lower()
    if "invalid login credentials" in message:
        return "이메일 또는 비밀번호가 올바르지 않습니다."
    if "email not confirmed" in message:
        return "이메일 인증을 먼저 완료해 주세요. 받은 메일의 링크를 눌러 주세요."
    if "already registered" in message:
        return "이미 가입된 이메일입니다. 로그인해 주세요."
    if "password" in message:
        return "비밀번호는 6자 이상이어야 합니다."
    return "로그인 처리 중 문제가 발생했습니다. 잠시 후 다시 시도해 주세요."


async def sign_in(settings: Settings, email: str, password: str) -> Session:
    client = await create_auth_client(settings)
    try:
        result = await client.auth.sign_in_with_password({"email": email, "password": password})
    except AuthApiError as exc:
        raise BadRequestError(_friendly_auth_error(exc), code="auth_failed") from None
    except Exception:  # AuthError(비 API), 네트워크 오류 등
        logger.exception("로그인 실패")
        raise UpstreamError("로그인 서버에 연결할 수 없습니다.") from None
    if result.session is None:
        raise BadRequestError("로그인 세션을 만들지 못했습니다.", code="auth_failed")
    return result.session


async def sign_up(settings: Settings, email: str, password: str, redirect_to: str) -> Session | None:
    """가입. 이메일 인증이 켜져 있으면 세션 없이 None을 돌려준다."""
    client = await create_auth_client(settings)
    try:
        result = await client.auth.sign_up(
            {"email": email, "password": password, "options": {"email_redirect_to": redirect_to}}
        )
    except AuthApiError as exc:
        raise BadRequestError(_friendly_auth_error(exc), code="auth_failed") from None
    except Exception:  # AuthError(비 API), 네트워크 오류 등
        logger.exception("회원가입 실패")
        raise UpstreamError("로그인 서버에 연결할 수 없습니다.") from None
    return result.session


async def _refresh(settings: Settings, refresh_token: str) -> Session | None:
    client = await create_auth_client(settings)
    try:
        result = await client.auth.refresh_session(refresh_token)
    except AuthError:
        return None  # 만료·폐기된 리프레시 토큰 → 다시 로그인
    except Exception:
        logger.exception("토큰 갱신 중 네트워크 오류")
        raise UpstreamError("로그인 서버에 연결할 수 없습니다.") from None
    return result.session


async def get_web_user(request: Request, db: DbDep, settings: SettingsDep) -> AuthUser:
    access = request.cookies.get(ACCESS_COOKIE)
    if access:
        user = await verify_access_token(db, access)
        if user is not None:
            return user

    refresh = request.cookies.get(REFRESH_COOKIE)
    if refresh:
        session = await _refresh(settings, refresh)
        if session is not None and session.user is not None:
            request.state.new_session = session  # 미들웨어가 새 쿠키를 응답에 기록
            return AuthUser(id=session.user.id, email=session.user.email)

    request.state.clear_session = True
    raise LoginRequiredError("로그인이 필요합니다.")


async def get_optional_web_user(request: Request, db: DbDep, settings: SettingsDep) -> AuthUser | None:
    if not request.cookies.get(ACCESS_COOKIE) and not request.cookies.get(REFRESH_COOKIE):
        return None
    try:
        return await get_web_user(request, db, settings)
    except LoginRequiredError:
        return None


WebUser = Annotated[AuthUser, Depends(get_web_user)]
OptionalWebUser = Annotated[AuthUser | None, Depends(get_optional_web_user)]
