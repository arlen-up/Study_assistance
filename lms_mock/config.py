"""가상 LMS 서버 설정 (도우미 설정과 별개로 읽는다)."""

from functools import lru_cache

from pydantic import SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class LmsConfigError(Exception):
    def __init__(self, missing: list[str]) -> None:
        self.missing = missing
        super().__init__(f"가상 LMS 환경변수가 설정되지 않았습니다: {', '.join(missing)}")


class LmsSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", ".env.local"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    supabase_url: str
    supabase_secret_key: SecretStr
    lms_api_key: SecretStr | None = None  # 없으면 LMS API 전체가 503 (연동 미설정)

    @field_validator("supabase_url")
    @classmethod
    def _strip_url(cls, value: str) -> str:
        return value.strip().rstrip("/")

    @field_validator("lms_api_key", mode="before")
    @classmethod
    def _blank_to_none(cls, value: object) -> object:
        return None if isinstance(value, str) and not value.strip() else value


@lru_cache(maxsize=1)
def get_lms_settings() -> LmsSettings:
    try:
        return LmsSettings()  # type: ignore[call-arg]
    except ValidationError as exc:
        names = sorted({str(err["loc"][0]).upper() for err in exc.errors() if err.get("loc")})
        raise LmsConfigError(names) from None
