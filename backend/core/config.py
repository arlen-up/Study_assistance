"""환경변수 설정 (pydantic-settings).

로컬: 프로젝트 루트의 .env / .env.local 을 읽는다.
Vercel: 프로젝트 Environment Variables 를 읽는다.
"""

from functools import lru_cache

from pydantic import Field, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from backend.core.errors import ConfigError


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", ".env.local"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Supabase (서버 전용) ---
    supabase_url: str
    supabase_secret_key: SecretStr

    # --- Tilon (OpenAI 호환) ---
    tilon_api_key: SecretStr
    tilon_base_url: str
    tilon_chat_model: str
    tilon_embedding_model: str

    # --- 임베딩 ---
    embedding_dimensions: int = Field(default=1536, ge=1, le=4096)
    embedding_send_dimensions: bool = False

    # --- LLM / 에이전트 ---
    llm_temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    llm_timeout_seconds: float = Field(default=40.0, gt=0, le=55)
    agent_max_iterations: int = Field(default=5, ge=1, le=10)

    # --- 문서 ---
    lms_allowed_hosts: str = ""
    max_upload_bytes: int = Field(default=4_000_000, ge=1, le=4_400_000)  # Vercel 요청 본문 한도 4.5MB

    app_timezone: str = "Asia/Seoul"

    @field_validator("supabase_url", "tilon_base_url")
    @classmethod
    def _validate_url(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if not value.startswith(("https://", "http://")):
            raise ValueError("URL은 http:// 또는 https:// 로 시작해야 합니다")
        return value

    @field_validator("tilon_chat_model", "tilon_embedding_model")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("빈 값일 수 없습니다")
        return value

    @property
    def allowed_hosts(self) -> frozenset[str]:
        return frozenset(h.strip().lower() for h in self.lms_allowed_hosts.split(",") if h.strip())


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """설정을 한 번만 읽어 캐시한다. 누락 시 값이 아닌 '변수 이름'만 담은 ConfigError를 던진다."""
    try:
        return Settings()  # type: ignore[call-arg]
    except ValidationError as exc:
        names = sorted({str(err["loc"][0]).upper() for err in exc.errors() if err.get("loc")})
        raise ConfigError(names) from None
