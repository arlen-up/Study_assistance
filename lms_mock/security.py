"""가상 학교 시스템의 보안 도구 (OAuth 2.0 + PKCE, 비밀번호, 토큰).

실제 학교라면 학교 인증 서버가 하는 일을 시연용으로 흉내 낸다. 표준을 그대로 따른다.
  - RFC 6749  OAuth 2.0 Authorization Code
  - RFC 7636  PKCE (S256)
  - RFC 7009  토큰 폐기
"""

import base64
import hashlib
import hmac
import secrets
from urllib.parse import urlsplit

from lms_mock.config import LmsSettings
from lms_mock.errors import LmsError

# 등록된 앱(클라이언트). 실제 학교에서는 전산팀이 앱을 등록하고 client_id/secret 을 발급한다.
CLIENT_ID = "lssa-study-overflow"
CALLBACK_PATH = "/ui/lms/oauth/callback"

# 권한 범위: 앱은 필요한 것만 요청하고, 학생은 동의 화면에서 내용을 보고 허용한다.
SCOPES: dict[str, str] = {
    "lms.read": "수강 과목 · 과제와 마감일 · 강의 자료 · 과목 성적 읽기",
    "academic.read": "학사정보 성적표 읽기 (졸업까지 남은 학점 계산용)",
    "lms.submit": "과제를 '제출 완료'로 표시하기",
}
DEFAULT_SCOPE = "lms.read academic.read lms.submit"

CODE_TTL_SECONDS = 300            # 일회용 코드: 5분
TOKEN_TTL_DAYS = 180              # 학생 전용 토큰: 180일 (학생이 연결 해제하면 바로 폐기)
_SCRYPT = {"n": 2**14, "r": 8, "p": 1}


def sha256_hex(value: str) -> str:
    """코드·토큰은 원문 대신 해시로만 저장한다 (DB가 새도 바로 쓸 수 없게)."""
    return hashlib.sha256(value.encode()).hexdigest()


def new_code() -> str:
    return secrets.token_urlsafe(32)


def new_access_token() -> str:
    """Moodle Web Service 토큰과 같은 32자리 16진수 형식 (기존 Moodle 연동 코드를 그대로 쓰기 위해)."""
    return secrets.token_hex(16)


def pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def pkce_matches(verifier: str, challenge: str) -> bool:
    if not 43 <= len(verifier) <= 128:
        return False
    return hmac.compare_digest(pkce_challenge(verifier), challenge)


def client_secret(settings: LmsSettings) -> str:
    """시연용 클라이언트 비밀값. 실제 학교에서는 학교가 따로 발급한다."""
    if settings.lms_api_key is None:
        raise LmsError(503, "lms_not_configured", "가상 LMS 연동 키(LMS_API_KEY)가 설정되지 않았습니다.")
    key = settings.lms_api_key.get_secret_value().encode()
    return hmac.new(key, f"oauth-client:{CLIENT_ID}".encode(), hashlib.sha256).hexdigest()


def check_client(settings: LmsSettings, client_id: str | None, secret: str | None) -> None:
    if client_id != CLIENT_ID or not secret or not hmac.compare_digest(secret, client_secret(settings)):
        raise LmsError(401, "invalid_client", "등록되지 않은 앱이거나 비밀값이 틀렸습니다.")


def valid_redirect_uri(redirect_uri: str | None, request_host: str) -> bool:
    """돌아갈 주소는 '같은 사이트의 정해진 경로'만 허용한다 (다른 사이트로 코드를 빼돌리는 공격 방지)."""
    if not redirect_uri:
        return False
    parts = urlsplit(redirect_uri)
    return (
        parts.scheme in ("https", "http")
        and parts.netloc == request_host
        and parts.path == CALLBACK_PATH
        and not parts.query
        and not parts.fragment
    )


def normalize_scope(raw: str | None) -> str | None:
    """요청한 권한 범위를 정리한다. 모르는 범위가 있으면 None."""
    names = (raw or DEFAULT_SCOPE).split()
    if not names or any(name not in SCOPES for name in names):
        return None
    return " ".join(name for name in SCOPES if name in names)  # 순서 고정 · 중복 제거


# ---------------------------------------------------------------------
# 가상 학교 로그인 비밀번호 (scrypt)
# ---------------------------------------------------------------------
def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, dklen=32, **_SCRYPT)
    b64 = base64.b64encode
    return f"scrypt${_SCRYPT['n']}${_SCRYPT['r']}${_SCRYPT['p']}${b64(salt).decode()}${b64(digest).decode()}"


def verify_password(password: str, stored: str | None) -> bool:
    if not stored:
        # 계정이 없거나 비밀번호가 없어도 같은 시간을 쓰게 해서, 학번이 있는지 알아내기 어렵게 한다
        hashlib.scrypt(password.encode(), salt=b"0" * 16, dklen=32, **_SCRYPT)
        return False
    try:
        _, n, r, p, salt, digest = stored.split("$")
        expected = base64.b64decode(digest)
        actual = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), dklen=len(expected),
                                n=int(n), r=int(r), p=int(p))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)
