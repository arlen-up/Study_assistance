"""Supabase Auth 액세스 토큰 검증.

- JSON API(/api/*): `Authorization: Bearer <token>` 헤더 또는 로그인 쿠키
- 웹 화면: backend/web/session.py 가 쿠키 + 자동 토큰 갱신을 처리
"""

import logging
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from supabase import AsyncClient
from supabase_auth.errors import AuthError

from backend.core.errors import UnauthorizedError
from backend.core.supabase_client import DbDep
from backend.schemas.models import AuthUser

logger = logging.getLogger(__name__)

ACCESS_COOKIE = "lms_access"
REFRESH_COOKIE = "lms_refresh"

_bearer = HTTPBearer(auto_error=False)


async def verify_access_token(db: AsyncClient, token: str) -> AuthUser | None:
    """유효하면 사용자, 만료·위조면 None. Auth 서버 장애는 예외."""
    try:
        response = await db.auth.get_user(token)
    except AuthError:
        return None
    except Exception:
        logger.exception("Supabase Auth 검증 실패")
        raise UnauthorizedError("로그인 정보를 확인할 수 없습니다.") from None
    if response is None or response.user is None:
        return None
    return AuthUser(id=response.user.id, email=response.user.email)


async def get_current_user(
    request: Request,
    db: DbDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> AuthUser:
    token: str | None = None
    if credentials is not None and credentials.scheme.lower() == "bearer":
        token = credentials.credentials
    if not token:
        token = request.cookies.get(ACCESS_COOKIE)
    if not token:
        raise UnauthorizedError("로그인이 필요합니다.")

    user = await verify_access_token(db, token)
    if user is None:
        raise UnauthorizedError("로그인 정보가 만료되었거나 올바르지 않습니다.")
    return user


CurrentUser = Annotated[AuthUser, Depends(get_current_user)]
