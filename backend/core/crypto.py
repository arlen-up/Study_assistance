"""서버 비밀 키(LMS_TOKEN_KEY)로 하는 암호화.

- 학교가 발급한 학생 전용 토큰을 DB에 저장할 때 암호화한다 (DB가 새도 토큰을 바로 쓸 수 없게).
- 학교 계정 연결 중 잠깐 쓰는 state·PKCE 값을 쿠키에 담을 때도 암호화한다 (내용 보호 + 위조 방지).
Fernet = AES-128-CBC + HMAC-SHA256 (암호화와 위조 검사를 함께 한다).
"""

import base64
import hashlib
import json
import time
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from backend.core.config import Settings
from backend.core.errors import AppError


class CryptoNotConfiguredError(AppError):
    status_code = 503
    code = "crypto_not_configured"


def fernet_from_key(key: str) -> Fernet:
    """LMS_TOKEN_KEY(아무 길이의 비밀 문자열) → Fernet 키 (시연 계정 스크립트도 같은 방법을 쓴다)."""
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(key.encode()).digest()))


def _fernet(settings: Settings) -> Fernet:
    if settings.lms_token_key is None:
        raise CryptoNotConfiguredError("학교 계정 연결용 서버 키(LMS_TOKEN_KEY)가 설정되지 않았습니다.")
    return fernet_from_key(settings.lms_token_key.get_secret_value())


def encrypt(settings: Settings, plain: str) -> str:
    return _fernet(settings).encrypt(plain.encode()).decode()


def decrypt(settings: Settings, token: str, max_age_seconds: int | None = None) -> str | None:
    """복호화. 키가 다르거나 위조·만료됐으면 None."""
    try:
        return _fernet(settings).decrypt(token.encode(), ttl=max_age_seconds).decode()
    except (InvalidToken, ValueError):
        return None


def seal(settings: Settings, data: dict[str, Any]) -> str:
    return encrypt(settings, json.dumps({**data, "iat": int(time.time())}, separators=(",", ":")))


def unseal(settings: Settings, sealed: str | None, max_age_seconds: int) -> dict[str, Any] | None:
    if not sealed:
        return None
    plain = decrypt(settings, sealed, max_age_seconds)
    if plain is None:
        return None
    try:
        data = json.loads(plain)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None
