"""서버 간 인증: 도우미가 보낸 X-LMS-API-Key 헤더를 확인한다."""

import hmac
from typing import Annotated

from fastapi import Depends, Header

from lms_mock.config import LmsSettings, get_lms_settings
from lms_mock.errors import LmsError

API_KEY_HEADER = "X-LMS-API-Key"


def lms_settings_dependency() -> LmsSettings:
    return get_lms_settings()


LmsSettingsDep = Annotated[LmsSettings, Depends(lms_settings_dependency)]


def require_api_key(
    settings: LmsSettingsDep,
    x_lms_api_key: Annotated[str | None, Header(alias=API_KEY_HEADER)] = None,
) -> None:
    if settings.lms_api_key is None:
        raise LmsError(503, "lms_not_configured", "가상 LMS 연동 키(LMS_API_KEY)가 설정되지 않았습니다.")
    expected = settings.lms_api_key.get_secret_value().encode()
    # 길이·내용 비교에 걸리는 시간이 같도록 compare_digest 사용 (타이밍 공격 방지)
    if not x_lms_api_key or not hmac.compare_digest(x_lms_api_key.encode(), expected):
        raise LmsError(401, "invalid_api_key", "LMS API 키가 올바르지 않습니다.")


ApiKeyDep = Depends(require_api_key)
