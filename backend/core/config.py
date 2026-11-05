"""환경변수 설정 (pydantic-settings).

로컬: 프로젝트 루트의 .env / .env.local 을 읽는다.
Vercel: 프로젝트 Environment Variables 를 읽는다.
"""

import datetime as dt
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

    # --- Supabase ---
    supabase_url: str
    supabase_secret_key: SecretStr  # 서버 전용 (RLS 우회) — 절대 브라우저로 보내지 않음
    supabase_publishable_key: SecretStr | None = None  # 로그인/토큰 갱신용 (없으면 secret 키 사용)

    # --- LLM (OpenAI 호환 API: OpenAI, Azure OpenAI 호환 게이트웨이, 로컬 vLLM 등) ---
    llm_api_key: SecretStr
    llm_base_url: str = "https://api.openai.com/v1"
    llm_chat_model: str = "gpt-6-luna"
    llm_embedding_model: str = "text-embedding-3-small"  # 1536차원
    # 추론 모델 설정. gpt-6-luna는 Chat Completions에서 함수 호출을 쓰려면 "none"이어야 한다.
    # 추론 기능이 없는 다른 OpenAI 호환 모델을 쓸 때는 빈 값으로 두면 요청에서 빠진다.
    llm_reasoning_effort: str | None = "none"

    # --- 임베딩 ---
    embedding_dimensions: int = Field(default=1536, ge=1, le=4096)
    embedding_send_dimensions: bool = False

    # --- LLM / 에이전트 ---
    llm_temperature: float | None = Field(default=None, ge=0.0, le=2.0)  # None이면 모델 기본값 (추론 모델 호환)
    llm_timeout_seconds: float = Field(default=40.0, gt=0, le=55)
    agent_max_iterations: int = Field(default=5, ge=1, le=10)

    # --- 문서 ---
    lms_allowed_hosts: str = ""
    max_upload_bytes: int = Field(default=4_000_000, ge=1, le=4_400_000)  # Vercel 요청 본문 한도 4.5MB

    # --- 가상 LMS 연동 ---
    lms_api_key: SecretStr | None = None
    lms_api_base_url: str | None = None  # 학사정보 API. 없으면 같은 앱 안의 가상 시스템을 직접 호출
    moodle_base_url: str | None = None   # 코스모스(Moodle) 주소. 없으면 가상 코스모스
    lms_timeout_seconds: float = Field(default=10.0, gt=0, le=30)

    # --- Web Push 알림 ---
    vapid_public_key: str | None = None
    vapid_private_key: SecretStr | None = None
    vapid_subject: str = "mailto:you@example.com"
    # 구독 endpoint로 서버가 요청을 보내므로(SSRF 위험) 알려진 푸시 서비스 호스트만 허용 (접미사 일치)
    push_allowed_hosts: str = (
        "fcm.googleapis.com,updates.push.services.mozilla.com,push.services.mozilla.com,"
        "web.push.apple.com,notify.windows.com"
    )

    # --- 매일 알림 (Vercel Cron) ---
    cron_secret: SecretStr | None = None

    app_timezone: str = "Asia/Seoul"

    # --- 학기 달력 ('저번주 자바 내용' 같은 질문의 주차 계산) ---
    semester_label: str = "2026-2학기"
    semester_start: dt.date = dt.date(2026, 8, 31)  # 1주차가 시작하는 월요일

    @field_validator(
        "lms_api_key", "lms_api_base_url", "moodle_base_url", "vapid_public_key", "vapid_private_key", "cron_secret",
        mode="before",
    )
    @classmethod
    def _blank_optional(cls, value: object) -> object:
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator("lms_api_base_url", "moodle_base_url")
    @classmethod
    def _validate_lms_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip().rstrip("/")
        if not value.startswith(("https://", "http://")):
            raise ValueError("주소는 http:// 또는 https:// 로 시작해야 합니다")
        return value

    @field_validator("supabase_url", "llm_base_url")
    @classmethod
    def _validate_url(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if not value.startswith(("https://", "http://")):
            raise ValueError("URL은 http:// 또는 https:// 로 시작해야 합니다")
        return value

    @field_validator("llm_chat_model", "llm_embedding_model")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("빈 값일 수 없습니다")
        return value

    @field_validator("llm_reasoning_effort", mode="before")
    @classmethod
    def _validate_reasoning_effort(cls, value: object) -> str | None:
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        effort = str(value).strip().lower()
        if effort not in {"none", "low", "medium", "high", "xhigh", "max"}:
            raise ValueError("none, low, medium, high, xhigh, max 중 하나여야 합니다")
        return effort

    @field_validator("llm_temperature", mode="before")
    @classmethod
    def _blank_temperature(cls, value: object) -> object:
        return None if isinstance(value, str) and not value.strip() else value

    @property
    def allowed_hosts(self) -> frozenset[str]:
        return frozenset(h.strip().lower() for h in self.lms_allowed_hosts.split(",") if h.strip())

    @property
    def push_enabled(self) -> bool:
        return bool(self.vapid_public_key and self.vapid_private_key)

    @property
    def push_hosts(self) -> tuple[str, ...]:
        return tuple(h.strip().lower() for h in self.push_allowed_hosts.split(",") if h.strip())

    @property
    def auth_key(self) -> str:
        key = self.supabase_publishable_key or self.supabase_secret_key
        return key.get_secret_value()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """설정을 한 번만 읽어 캐시한다. 누락 시 값이 아닌 '변수 이름'만 담은 ConfigError를 던진다."""
    try:
        return Settings()  # type: ignore[call-arg]
    except ValidationError as exc:
        names = sorted({str(err["loc"][0]).upper() for err in exc.errors() if err.get("loc")})
        raise ConfigError(names) from None
