"""사용자 이름(닉네임).

- 가입 후 처음 들어오면 화면에서 정한다. 한 번 정하면 이 기능으로는 다시 바꾸지 않는다.
- 규칙: 2~16자, 한글·영어·숫자·밑줄(_)만, 밑줄만으로는 안 됨
- 다른 사람과 겹치면 안 된다 (영어 대소문자는 같은 글자로 본다: Kim = kim)
- 같은 규칙을 DB(마이그레이션 20261106030000_username.sql)에서도 강제한다.
  마이그레이션 전이면 이름 설정을 건너뛰어서 앱의 다른 기능은 그대로 쓸 수 있다.
"""

import logging
import re
import unicodedata
from dataclasses import dataclass
from uuid import UUID

from postgrest.exceptions import APIError
from supabase import AsyncClient

from backend.core.errors import BadRequestError, UpstreamError

logger = logging.getLogger(__name__)

MIN_LEN, MAX_LEN = 2, 16
_ALLOWED = re.compile(r"[0-9A-Za-z가-힣_]+")
_HAS_LETTER = re.compile(r"[0-9A-Za-z가-힣]")
_UNIQUE_VIOLATION = "23505"
_CHECK_VIOLATION = "23514"
TAKEN_MESSAGE = "이미 다른 사람이 쓰고 있는 이름이에요. 다른 이름을 골라 주세요."


@dataclass(frozen=True)
class UsernameState:
    supported: bool        # DB에 username 칸이 있는지 (마이그레이션 전이면 False)
    username: str | None

    @property
    def needs_setup(self) -> bool:
        return self.supported and not self.username


def normalize(raw: str) -> str:
    """앞뒤 공백을 지우고 한글을 완성형(NFC)으로 맞춘다 (맥에서 입력한 '풀어쓴 한글'도 같은 글자로)."""
    return unicodedata.normalize("NFC", raw or "").strip()


def validation_error(name: str) -> str | None:
    """규칙에 맞으면 None, 아니면 학생에게 보여 줄 이유."""
    if not name:
        return "사용할 이름을 입력해 주세요."
    if len(name) < MIN_LEN or len(name) > MAX_LEN:
        return f"{MIN_LEN}~{MAX_LEN}자로 정해 주세요. (지금 {len(name)}자)"
    if not _ALLOWED.fullmatch(name):
        bad = sorted({ch for ch in name if not _ALLOWED.fullmatch(ch)})
        shown = " ".join("공백" if ch.isspace() else ch for ch in bad[:5])
        return f"한글, 영어, 숫자, 밑줄(_)만 쓸 수 있어요. 쓸 수 없는 글자: {shown}"
    if not _HAS_LETTER.search(name):
        return "밑줄(_)만으로는 만들 수 없어요. 글자나 숫자를 넣어 주세요."
    return None


async def get_state(db: AsyncClient, user_id: UUID) -> UsernameState:
    try:
        res = await db.table("users").select("username").eq("id", str(user_id)).limit(1).execute()
    except Exception:
        # 마이그레이션 전(칸 없음) 등: 이름 설정만 건너뛰고 나머지 화면은 그대로 연다
        logger.warning("username 조회 실패 → 이름 설정 건너뜀", exc_info=True)
        return UsernameState(supported=False, username=None)
    username = res.data[0].get("username") if res.data else None
    return UsernameState(supported=True, username=username)


async def is_taken(db: AsyncClient, user_id: UUID, name: str) -> bool:
    try:
        res = await db.rpc("is_username_taken", {"p_username": name, "p_user_id": str(user_id)}).execute()
    except Exception:
        logger.exception("username 중복 확인 실패")
        raise UpstreamError("이름이 겹치는지 확인하지 못했어요. 잠시 뒤 다시 시도해 주세요.") from None
    return bool(res.data)


async def check(db: AsyncClient, user_id: UUID, raw: str) -> tuple[str, str | None]:
    """(정리된 이름, 문제) — 문제가 None이면 쓸 수 있는 이름."""
    name = normalize(raw)
    problem = validation_error(name)
    if problem is None and await is_taken(db, user_id, name):
        problem = TAKEN_MESSAGE
    return name, problem


async def set_username(db: AsyncClient, user_id: UUID, raw: str) -> str:
    """처음 한 번만 정한다. 이미 이름이 있으면 바꾸지 않는다."""
    name, problem = await check(db, user_id, raw)
    if problem:
        raise BadRequestError(problem, code="invalid_username")
    try:
        res = await (
            db.table("users")
            .update({"username": name})
            .eq("id", str(user_id))
            .is_("username", "null")
            .execute()
        )
    except APIError as exc:
        # 확인과 저장 사이에 다른 사람이 먼저 같은 이름을 가져간 경우 등은 DB가 막는다
        if exc.code == _UNIQUE_VIOLATION:
            raise BadRequestError(TAKEN_MESSAGE, code="username_taken") from None
        if exc.code == _CHECK_VIOLATION:
            raise BadRequestError("한글, 영어, 숫자, 밑줄(_)만 2~16자로 정해 주세요.", code="invalid_username") from None
        logger.exception("username 저장 실패")
        raise UpstreamError("이름을 저장하지 못했어요. 잠시 뒤 다시 시도해 주세요.") from None
    except Exception:
        logger.exception("username 저장 실패")
        raise UpstreamError("이름을 저장하지 못했어요. 잠시 뒤 다시 시도해 주세요.") from None
    if not res.data:
        state = await get_state(db, user_id)
        if state.username:
            return state.username  # 이미 정해져 있음 (다른 탭에서 먼저 정한 경우)
        raise UpstreamError("이름을 저장하지 못했어요. 잠시 뒤 다시 시도해 주세요.")
    return name
