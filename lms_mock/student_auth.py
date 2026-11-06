"""학생 전용 토큰(Authorization: Bearer) 확인 — 학생 개인 정보는 이 토큰으로만 읽을 수 있다."""

from dataclasses import dataclass
from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, Header

from lms_mock import repository
from lms_mock.errors import LmsError
from lms_mock.repository import LmsDbDep
from lms_mock.security import sha256_hex


@dataclass(frozen=True)
class StudentToken:
    student_id: UUID
    scopes: frozenset[str]


def require_student_token(scope: str) -> Any:
    """이 권한 범위(scope)가 있는 학생 전용 토큰이 필요하다. 응답은 토큰 주인 학생의 정보로만 만든다."""

    async def dependency(db: LmsDbDep, authorization: Annotated[str | None, Header()] = None) -> StudentToken:
        token = (authorization or "").removeprefix("Bearer ").strip()
        row = await repository.find_token(db, sha256_hex(token)) if token else None
        if row is None:
            raise LmsError(401, "invalid_token", "학생 토큰이 없거나 만료·폐기됐습니다. 학교 계정을 다시 연결해 주세요.")
        scopes = frozenset(str(row["scope"]).split())
        if scope not in scopes:
            raise LmsError(403, "insufficient_scope", f"학생이 '{scope}' 권한을 허용하지 않았습니다.")
        return StudentToken(UUID(row["student_id"]), scopes)

    return Depends(dependency)
