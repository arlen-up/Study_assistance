"""Supabase Auth 액세스 토큰 검증.

프론트엔드는 `Authorization: Bearer <access_token>` 헤더를 보낸다.
백엔드는 Supabase Auth 서버에 토큰을 확인해 user_id를 얻는다.
"""

import logging
from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from supabase_auth.errors import AuthError

from backend.core.errors import UnauthorizedError
from backend.core.supabase_client import DbDep
from backend.schemas.models import AuthUser

logger = logging.getLogger(__name__)

_bearer = HTTPBearer(auto_error=False)


async def get_current_user(
    db: DbDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> AuthUser:
    if credentials is None or credentials.scheme.lower() != "bearer" or not credentials.credentials:
        raise UnauthorizedError("로그인이 필요합니다.")

    try:
        response = await db.auth.get_user(credentials.credentials)
    except AuthError:
        raise UnauthorizedError("로그인 정보가 만료되었거나 올바르지 않습니다.") from None
    except Exception:  # 네트워크 오류 등
        logger.exception("Supabase Auth 검증 실패")
        raise UnauthorizedError("로그인 정보를 확인할 수 없습니다.") from None

    if response is None or response.user is None:
        raise UnauthorizedError("로그인 정보가 올바르지 않습니다.")

    return AuthUser(id=response.user.id, email=response.user.email)


CurrentUser = Annotated[AuthUser, Depends(get_current_user)]
