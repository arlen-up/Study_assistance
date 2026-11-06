"""학교 계정 연결(OAuth 2.0 + PKCE)의 보안 규칙 — DB 없이 확인한다.

실제 DB로 하는 끝까지 흐름 확인(남의 토큰으로 못 읽음, 재사용·폐기 등)은 docs/연동_설계.md 의 확인 기록 참고.
"""

import base64
import hashlib
import secrets
import uuid

import pytest
from pydantic import SecretStr

from backend.core import crypto
from backend.core.config import Settings
from lms_mock.moodle import required_scope
from lms_mock.security import (
    CALLBACK_PATH,
    DEFAULT_SCOPE,
    hash_password,
    new_access_token,
    normalize_scope,
    pkce_challenge,
    pkce_matches,
    sha256_hex,
    valid_redirect_uri,
    verify_password,
)

HOST = "lms-study-helper.vercel.app"


def _settings(key: str | None = "test-key-0123456789-abcdefghij") -> Settings:
    return Settings.model_construct(lms_token_key=SecretStr(key) if key else None)


# ---------------- PKCE (RFC 7636) ----------------
def test_pkce_s256_definition() -> None:
    # RFC 7636 S256: BASE64URL(SHA256(ASCII(code_verifier))), 끝의 '=' 없음
    verifier = secrets.token_urlsafe(64)
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).decode().rstrip("=")
    assert pkce_challenge(verifier) == expected and "=" not in expected and len(expected) == 43


def test_pkce_rejects_wrong_or_short_verifier() -> None:
    verifier = secrets.token_urlsafe(64)
    challenge = pkce_challenge(verifier)
    assert pkce_matches(verifier, challenge)
    assert not pkce_matches(secrets.token_urlsafe(64), challenge)   # 코드를 훔쳐도 verifier 없이는 토큰 못 받음
    assert not pkce_matches("short", pkce_challenge("short"))       # 43자 미만은 거절


# ---------------- 돌아갈 주소 (코드 빼돌리기 방지) ----------------
@pytest.mark.parametrize(
    "uri",
    [f"https://{HOST}{CALLBACK_PATH}", f"http://{HOST}{CALLBACK_PATH}"],
)
def test_redirect_uri_allowed(uri: str) -> None:
    assert valid_redirect_uri(uri, HOST)


@pytest.mark.parametrize(
    "uri",
    [
        f"https://evil.example{CALLBACK_PATH}",                 # 다른 사이트
        f"https://{HOST}.evil.example{CALLBACK_PATH}",          # 비슷한 이름의 다른 사이트
        f"https://{HOST}/somewhere-else",                       # 다른 경로
        f"https://{HOST}{CALLBACK_PATH}?next=https://evil",     # 덧붙인 주소
        f"javascript://{HOST}{CALLBACK_PATH}",                  # 이상한 형식
        "",
        None,
    ],
)
def test_redirect_uri_rejected(uri: str | None) -> None:
    assert not valid_redirect_uri(uri, HOST)


# ---------------- 권한 범위 ----------------
def test_scope_only_known_values() -> None:
    assert normalize_scope(None) == DEFAULT_SCOPE
    assert normalize_scope("lms.submit lms.read lms.read") == "lms.read lms.submit"  # 순서 고정·중복 제거
    assert normalize_scope("lms.read admin.all") is None                             # 모르는 권한은 거절


def test_moodle_function_scopes() -> None:
    assert required_scope("core_enrol_get_users_courses") == "lms.read"
    assert required_scope("mod_assign_submit_for_grading") == "lms.submit"  # 쓰기는 따로 허용받아야 함


# ---------------- 비밀번호 · 토큰 저장 ----------------
def test_password_hash_roundtrip() -> None:
    stored = hash_password("school-pass-1")
    assert stored.startswith("scrypt$") and "school-pass-1" not in stored
    assert verify_password("school-pass-1", stored)
    assert not verify_password("wrong", stored)
    assert not verify_password("anything", None)  # 계정이 없어도 같은 방식으로 실패


def test_token_stored_as_hash_only() -> None:
    token = new_access_token()
    assert len(token) == 32 and int(token, 16) >= 0  # Moodle 토큰 형식 (32자리 16진수)
    assert sha256_hex(token) == hashlib.sha256(token.encode()).hexdigest() != token


# ---------------- 도우미 쪽 암호화 (토큰 저장 · state 쿠키) ----------------
def test_token_encryption_roundtrip_and_wrong_key() -> None:
    token = new_access_token()
    sealed = crypto.encrypt(_settings(), token)
    assert token not in sealed
    assert crypto.decrypt(_settings(), sealed) == token
    assert crypto.decrypt(_settings("another-key-0123456789-abcdefg"), sealed) is None


def test_state_cookie_tamper_and_expiry() -> None:
    uid = str(uuid.uuid4())
    sealed = crypto.seal(_settings(), {"uid": uid, "state": "s1", "verifier": "v" * 50})
    assert crypto.unseal(_settings(), sealed, 600)["uid"] == uid
    tampered = sealed[:-4] + ("AAAA" if not sealed.endswith("AAAA") else "BBBB")
    assert crypto.unseal(_settings(), tampered, 600) is None    # 조작하면 거절
    assert crypto.unseal(_settings(), None, 600) is None


def test_encryption_requires_server_key() -> None:
    with pytest.raises(crypto.CryptoNotConfiguredError):
        crypto.encrypt(_settings(None), "x")
