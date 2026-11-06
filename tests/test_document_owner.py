"""교안은 계정별로 나뉜다: 로그인한 사용자 없이 챗봇 도구가 교안을 보지 못하는지 (DB 없이 확인)."""

import uuid

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from backend.mcp_server import ToolDeps, _owner


def _deps(user_id: uuid.UUID | None) -> ToolDeps:
    return ToolDeps(settings=None, db=None, embeddings=None, user_id=user_id)  # type: ignore[arg-type]


def test_owner_required() -> None:
    with pytest.raises(ToolError):
        _owner(_deps(None))


def test_owner_is_current_user() -> None:
    uid = uuid.uuid4()
    assert _owner(_deps(uid)) == str(uid)
